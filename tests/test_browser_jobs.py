import os
import sys
import tempfile
import threading
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

import db
from browser_jobs import (
    browser_summary,
    create_job,
    enqueue_authorized_jobs,
    inspect_job,
    list_observations,
    record_quote,
    recover_browser_jobs,
)
from match_analysis import analyse_upcoming_batch


class BrowserJobTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.previous = db.DB
        db.DB = Path(cls.temp.name) / 'sports.sqlite3'
        db.initialize()
        cls.previous_domains = os.environ.get('SPORTS_AI_AUTHORIZED_DOMAINS')
        cls.previous_urls = os.environ.get('SPORTS_AI_AUTHORIZED_URLS')
        cls.previous_local = os.environ.get('SPORTS_BROWSER_ALLOW_LOCAL_TESTS')

    @classmethod
    def tearDownClass(cls):
        db.DB = cls.previous
        cls.temp.cleanup()
        _restore('SPORTS_AI_AUTHORIZED_DOMAINS', cls.previous_domains)
        _restore('SPORTS_AI_AUTHORIZED_URLS', cls.previous_urls)
        _restore('SPORTS_BROWSER_ALLOW_LOCAL_TESTS', cls.previous_local)

    def setUp(self):
        os.environ['SPORTS_BROWSER_ALLOW_LOCAL_TESTS'] = '1'
        os.environ['SPORTS_AI_AUTHORIZED_DOMAINS'] = ''
        os.environ['SPORTS_AI_AUTHORIZED_URLS'] = ''
        with db.connect() as connection:
            connection.execute('DELETE FROM sports_ai_browser_observations')
            connection.execute('DELETE FROM sports_ai_browser_jobs')
            connection.execute('DELETE FROM sports_ai_collection_authorizations')
            connection.execute('DELETE FROM sports_ai_value_opportunities')
            connection.execute('DELETE FROM sports_ai_match_analyses')
            connection.execute('DELETE FROM sports_ai_results')
            connection.execute('DELETE FROM sports_ai_fixtures')

    def test_sportybet_is_refused_until_authorized(self):
        with self.assertRaises(ValueError):
            create_job('https://www.sportybet.com/ng/sport/football')

    def test_empty_allowlist_queues_nothing(self):
        os.environ['SPORTS_AI_AUTHORIZED_URLS'] = 'https://www.sportybet.com/'
        with db.connect() as connection:
            result = enqueue_authorized_jobs(connection)
            stored = connection.execute('SELECT COUNT(*) FROM sports_ai_browser_jobs').fetchone()[0]
        self.assertEqual(result['queued'], 0)
        self.assertEqual(stored, 0)
        self.assertIn('not visited', result['reason'].lower())

    def test_quote_stays_unconfirmed_and_does_not_qualify(self):
        job_id = create_job('http://127.0.0.1/fixture')
        with self.assertRaises(ValueError):
            record_quote(job_id, 'A v B', '1X2', 'Home', 1.0, 'http://127.0.0.1/fixture')
        record_quote(job_id, 'A v B', '1X2', 'Home', 2.1, 'http://127.0.0.1/fixture')
        rows = list_observations(job_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['confirmed'], 0)
        with db.connect() as connection:
            opportunities = connection.execute('SELECT COUNT(*) FROM sports_ai_value_opportunities').fetchone()[0]
            odds = connection.execute('SELECT COUNT(*) FROM sports_ai_odds_snapshots').fetchone()[0]
        self.assertEqual(opportunities, 0)
        self.assertEqual(odds, 0)

    def test_duplicate_page_is_not_queued_twice(self):
        create_job('http://127.0.0.1/fixture')
        with self.assertRaises(ValueError):
            create_job('http://127.0.0.1/fixture')

    def test_missed_heartbeat_returns_the_job_to_the_queue(self):
        job_id = str(uuid.uuid4())
        stale = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        with db.connect() as connection:
            connection.execute(
                """INSERT INTO sports_ai_browser_jobs
                   (id,url,status,created_at,started_at,heartbeat_at,attempt,max_attempts)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (job_id, 'http://127.0.0.1/fixture', 'running', stale, stale, stale, 1, 3),
            )
            recovered = recover_browser_jobs(connection)
            status = connection.execute(
                'SELECT status, error FROM sports_ai_browser_jobs WHERE id=?', (job_id,),
            ).fetchone()
        self.assertEqual(recovered, 1)
        self.assertEqual(status['status'], 'queued')
        self.assertIn('heartbeat', status['error'].lower())

    def test_summary_reports_no_authorized_sites(self):
        with db.connect() as connection:
            summary = browser_summary(connection)
        self.assertEqual(summary['authorized_urls'], 0)
        self.assertFalse(summary['sportybet_unattended']['operational'])
        self.assertFalse(summary['extracts_odds_from_text'])
        self.assertFalse(summary['places_wager'])

    def test_unanalysed_fixtures_are_chosen_before_a_refresh(self):
        now = datetime.now(timezone.utc)
        soon = (now + timedelta(days=1)).isoformat()
        later = (now + timedelta(days=2)).isoformat()
        latest = (now + timedelta(days=3)).isoformat()
        with db.connect() as connection:
            analysed = _fixture(connection, 'Analysed Home', 'Analysed Away', soon)
            fresh = _fixture(connection, 'Fresh Home', 'Fresh Away', later)
            refresh = _fixture(connection, 'Refresh Home', 'Refresh Away', latest)
            past = _fixture(connection, 'Refresh Home', 'Other Away', (now - timedelta(days=1)).isoformat(), 'finished')
            connection.execute(
                """INSERT INTO sports_ai_match_analyses (id,fixture_id,status,report_json,created_at)
                   VALUES(?,?,?,?,?)""",
                (str(uuid.uuid4()), analysed, 'complete', '{}', (now - timedelta(hours=13)).isoformat()),
            )
            connection.execute(
                """INSERT INTO sports_ai_match_analyses (id,fixture_id,status,report_json,created_at)
                   VALUES(?,?,?,?,?)""",
                (str(uuid.uuid4()), refresh, 'complete', '{}', (now - timedelta(days=2)).isoformat()),
            )
            connection.execute(
                """INSERT INTO sports_ai_results (id,fixture_id,home_score,away_score,status,source,settled_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (str(uuid.uuid4()), past, 1, 0, 'final', 'test', now.isoformat()),
            )
            with patch('match_analysis.analyze_fixture', return_value={'forecast_level': 'secondary'}):
                result = analyse_upcoming_batch(connection, 'test', lambda *args: None, limit=8)
        chosen = [item['fixture_id'] for item in result['results']]
        self.assertEqual(chosen, [fresh, refresh])
        self.assertNotIn(analysed, chosen)


class BrowserVisitTests(unittest.TestCase):
    def setUp(self):
        self.previous = db.DB
        self.temp = tempfile.TemporaryDirectory()
        db.DB = Path(self.temp.name) / 'sports.sqlite3'
        db.initialize()
        self.previous_local = os.environ.get('SPORTS_BROWSER_ALLOW_LOCAL_TESTS')
        self.previous_headless = os.environ.get('SPORTS_BROWSER_HEADLESS')
        os.environ['SPORTS_BROWSER_ALLOW_LOCAL_TESTS'] = '1'
        os.environ['SPORTS_BROWSER_HEADLESS'] = '1'

    def tearDown(self):
        db.DB = self.previous
        self.temp.cleanup()
        _restore('SPORTS_BROWSER_ALLOW_LOCAL_TESTS', self.previous_local)
        _restore('SPORTS_BROWSER_HEADLESS', self.previous_headless)

    def test_local_page_is_read_without_turning_numbers_into_odds(self):
        if not _chromium_installed():
            self.skipTest('Chromium is not installed')
        page = '<html><head><title>Local board</title></head><body>Team A vs Team B 2.10</body></html>'
        server = _server(page)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        job_id = create_job(f'http://127.0.0.1:{server.server_address[1]}/board')
        inspect_job(job_id)
        with db.connect() as connection:
            job = connection.execute('SELECT status, title, observations FROM sports_ai_browser_jobs WHERE id=?', (job_id,)).fetchone()
            quotes = connection.execute('SELECT COUNT(*) FROM sports_ai_browser_observations').fetchone()[0]
            odds = connection.execute('SELECT COUNT(*) FROM sports_ai_odds_snapshots').fetchone()[0]
        self.assertEqual(job['status'], 'inspected')
        self.assertEqual(job['title'], 'Local board')
        self.assertEqual(job['observations'], 0)
        self.assertEqual(quotes, 0)
        self.assertEqual(odds, 0)

    def test_challenge_page_stores_no_odds(self):
        if not _chromium_installed():
            self.skipTest('Chromium is not installed')
        server = _server('<html><body>verify you are human</body></html>')
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        job_id = create_job(f'http://127.0.0.1:{server.server_address[1]}/challenge')
        with self.assertRaises(RuntimeError):
            inspect_job(job_id)
        with db.connect() as connection:
            job = connection.execute('SELECT status, error FROM sports_ai_browser_jobs WHERE id=?', (job_id,)).fetchone()
            quotes = connection.execute('SELECT COUNT(*) FROM sports_ai_browser_observations').fetchone()[0]
        self.assertIn(job['status'], ('queued', 'failed'))
        self.assertIn('challenge', job['error'].lower())
        self.assertEqual(quotes, 0)


def _restore(name, value):
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value


def _fixture(connection, home, away, kickoff, status='scheduled'):
    fixture_id = str(uuid.uuid4())
    connection.execute(
        """INSERT INTO sports_ai_fixtures
           (id,external_id,source,sport,competition,home,away,starts_at,status,statistics_json,odds_json,observed_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (fixture_id, fixture_id, 'test', 'football', 'Test', home, away, kickoff, status, '{}', '{}', kickoff),
    )
    return fixture_id


def _server(page):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = page.encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            return

    return ThreadingHTTPServer(('127.0.0.1', 0), Handler)


def _chromium_installed():
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            return bool(playwright.chromium.executable_path)
    except Exception:
        return False


if __name__ == '__main__':
    unittest.main()
