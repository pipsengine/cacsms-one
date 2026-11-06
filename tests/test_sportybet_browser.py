import os
import sys
import tempfile
import threading
import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

import db
from sportybet_api import _operator_permitted
from sportybet_browser import SportyBetOperator
from sportybet_research import (
    confirm_observation,
    import_observation,
    map_market,
    match_observation,
    parse_visible_text,
    rank_fixtures,
    store_observations,
    unattended_status,
    validate_decimal,
    validate_observation_market,
)


def _html(page):
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
    return Handler


class SportyBetResearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.previous = db.DB
        db.DB = Path(cls.temp.name) / 'sports.sqlite3'
        db.initialize()

    @classmethod
    def tearDownClass(cls):
        db.DB = cls.previous
        cls.temp.cleanup()

    def setUp(self):
        with db.connect() as connection:
            for table in (
                'sports_ai_value_opportunities', 'sports_ai_engine_predictions',
                'sports_ai_feature_snapshots', 'sports_ai_odds_history', 'sports_ai_odds_snapshots',
                'sports_ai_sportybet_observations', 'sports_ai_sportybet_events', 'sports_ai_sportybet_sessions',
                'sports_ai_model_registry', 'sports_ai_training_runs', 'sports_ai_analysis_runs',
                'sports_ai_fixtures',
            ):
                connection.execute(f'DELETE FROM {table}')
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        self.kickoff = (self.now + timedelta(days=3)).isoformat()

    def _fixture(self, connection, home, away, competition='Premier League', kickoff=None, sport='football'):
        fixture_id = str(uuid.uuid4())
        connection.execute(
            """INSERT INTO sports_ai_fixtures
               (id,external_id,source,sport,competition,home,away,starts_at,status,statistics_json,odds_json,observed_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (fixture_id, fixture_id, 'test', sport, competition, home, away, kickoff or self.kickoff,
             'scheduled', '{}', '{}', self.now.isoformat()),
        )
        return fixture_id

    def _prediction(self, connection, fixture_id, market, active, status, probability=0.6):
        run_id = str(uuid.uuid4())
        feature_id = str(uuid.uuid4())
        training_id = str(uuid.uuid4())
        model_id = str(uuid.uuid4())
        connection.execute(
            "INSERT INTO sports_ai_analysis_runs(id,status) VALUES(?, 'complete')", (run_id,),
        )
        connection.execute(
            """INSERT INTO sports_ai_feature_snapshots
               (id,fixture_id,sport,cutoff_at,features_json,source_refs_json,generated_at)
               VALUES(?,?,?,?,?,?,?)""",
            (feature_id, fixture_id, 'football', self.now.isoformat(), '{}', '{}', self.now.isoformat()),
        )
        connection.execute(
            """INSERT INTO sports_ai_training_runs
               (id,sport,model_name,version,status,training_window_json,parameters_json,metrics_json,sample_size)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (training_id, 'football', 'sports-ai-football-ensemble', str(uuid.uuid4())[:8], 'validated', '{}', '{}', '{}', 40),
        )
        connection.execute(
            """INSERT INTO sports_ai_model_registry
               (id,training_run_id,sport,model_name,version,status,active,parameters_json,metrics_json)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (model_id, training_id, 'football', 'sports-ai-football-ensemble', str(uuid.uuid4())[:8],
             status, int(active), '{}', '{}'),
        )
        settlement = {'p_win': probability, 'p_half_win': 0, 'p_push': 0, 'p_half_loss': 0, 'p_loss': 1 - probability}
        connection.execute(
            """INSERT INTO sports_ai_engine_predictions
               (id,run_id,fixture_id,model_id,feature_id,market,outcome,probability,settlement_json,explanation_json,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (str(uuid.uuid4()), run_id, fixture_id, model_id, feature_id, market, market, probability,
             __import__('json').dumps(settlement), '{}', (self.now - timedelta(days=1)).isoformat()),
        )

    def test_market_mapping_keeps_lines_and_periods_distinct(self):
        self.assertEqual(map_market('football', '1X2', 'Home')['canonical_market'], 'HOME')
        self.assertEqual(map_market('football', 'Double Chance', '1X')['canonical_market'], 'DOUBLE_CHANCE_1X')
        self.assertEqual(map_market('football', 'double chance', 'Home or Draw')['canonical_market'], 'DOUBLE_CHANCE_1X')
        self.assertEqual(map_market('football', 'double chance', 'Home or Away')['canonical_market'], 'DOUBLE_CHANCE_12')
        self.assertEqual(map_market('football', 'double chance', 'Draw or Away')['canonical_market'], 'DOUBLE_CHANCE_X2')
        self.assertEqual(map_market('football', 'draw no bet', 'Home')['canonical_market'], 'DNB_HOME')
        self.assertEqual(map_market('football', 'gg/ng', 'Yes')['canonical_market'], 'BTTS_YES')
        self.assertEqual(map_market('football', 'gg/ng', 'No')['canonical_market'], 'BTTS_NO')
        self.assertEqual(map_market('football', 'handicap 0:1', 'Home (0:1)')['canonical_market'], 'EUROPEAN_HOME_-1')
        self.assertEqual(map_market('football', 'handicap 1:0', 'Away (1:0)')['canonical_market'], 'EUROPEAN_AWAY_1')
        self.assertEqual(map_market('football', 'Over/Under', 'Over 2.5')['canonical_market'], 'OVER_2.5')
        self.assertEqual(map_market('football', 'Over/Under', 'Under 3.5')['canonical_market'], 'UNDER_3.5')
        self.assertNotEqual(
            map_market('football', 'Over/Under', 'Over 2.5')['canonical_market'],
            map_market('football', 'Over/Under', 'Over 3.5')['canonical_market'],
        )
        self.assertFalse(map_market('football', 'First Half Over/Under', 'Over 0.5')['supported'])
        self.assertEqual(map_market('football', 'Both Teams to Score', 'Yes')['canonical_market'], 'BTTS_YES')
        handicap = map_market('football', 'Asian Handicap', 'Home -0.5')
        self.assertEqual(handicap['canonical_market'], 'HOME_HANDICAP_-0.5')
        self.assertEqual(map_market('football', 'European Handicap', 'Home -1')['canonical_market'], 'EUROPEAN_HOME_-1')
        self.assertEqual(map_market('basketball', 'Moneyline', 'Away')['canonical_market'], 'MONEYLINE_AWAY')
        self.assertEqual(map_market('basketball', 'Total Points', 'Over 219.5')['canonical_market'], 'OVER_219.5')
        self.assertEqual(map_market('basketball', 'Point Spread', 'Home -5.5')['canonical_market'], 'HOME_SPREAD_-5.5')
        self.assertFalse(map_market('basketball', 'Moneyline', 'Draw')['supported'])

    def test_matching_requires_a_clear_fixture(self):
        with db.connect() as connection:
            first = self._fixture(connection, 'Arsenal', 'Chelsea')
            second = self._fixture(connection, 'Arsenal', 'Chelsea', competition='Premier Cup')
            observation = {
                'sport': 'football', 'competition': 'Premier League',
                'home': 'Arsenal FC', 'away': 'Chelsea', 'starts_at': self.kickoff,
            }
            ranked = rank_fixtures(connection, observation)
            self.assertTrue(ranked['ambiguous'])
            weak = {'sport': 'football', 'home': 'Arsenal', 'away': 'Tottenham', 'starts_at': self.kickoff, 'competition': 'Premier League'}
            self.assertEqual(rank_fixtures(connection, weak)['candidates'], [])
            stored = store_observations(connection, None, [{
                **observation, 'market_label': '1X2', 'selection_label': 'Home', 'decimal_odds': 2.1,
            }], 'https://www.sportybet.com/ng/sport/football', self.now.isoformat())
            with self.assertRaises(ValueError):
                match_observation(connection, stored['saved'][0], first)
            matched = match_observation(connection, stored['saved'][0], first, acknowledge_ambiguity=True)
            self.assertEqual(matched['fixture_id'], first)
            self.assertGreaterEqual(matched['match_confidence'], 0.5)
            self.assertNotEqual(matched['fixture_id'], second)

    def test_duplicate_invalid_and_stale_quotes_are_rejected(self):
        self.assertIsNone(validate_decimal(1))
        self.assertIsNone(validate_decimal('nope'))
        with db.connect() as connection:
            fixture_id = self._fixture(connection, 'Arsenal', 'Chelsea')
            row = {
                'sport': 'football', 'competition': 'Premier League', 'home': 'Arsenal', 'away': 'Chelsea',
                'starts_at': self.kickoff, 'market_label': '1X2', 'selection_label': 'Home', 'decimal_odds': 2.1,
            }
            source = 'https://www.sportybet.com/ng/sport/football'
            first = store_observations(connection, None, [row], source, self.now.isoformat())
            second = store_observations(connection, None, [row], source, self.now.isoformat())
            self.assertEqual(len(first['saved']), 1)
            self.assertEqual(second['duplicates'], 1)
            saved = connection.execute('SELECT * FROM sports_ai_sportybet_observations').fetchone()
            self.assertEqual(saved['decimal_odds'], 2.1)
            confirmed = confirm_observation(connection, first['saved'][0], 'tester', self.now)
            self.assertEqual(confirmed['confirmation_status'], 'confirmed')
            connection.execute(
                "UPDATE sports_ai_sportybet_observations SET decimal_odds=NULL WHERE id=?", (first['saved'][0],),
            )
            with self.assertRaises(ValueError):
                confirm_observation(connection, first['saved'][0], 'tester', self.now)
            connection.execute(
                """UPDATE sports_ai_sportybet_observations
                   SET decimal_odds=2.1, confirmation_status='confirmed', match_status='confirmed',
                       fixture_id=?, market_status='confirmed', canonical_market='HOME', observed_at=?
                   WHERE id=?""",
                (fixture_id, (self.now - timedelta(hours=2)).isoformat(), first['saved'][0]),
            )
            with self.assertRaises(ValueError):
                import_observation(connection, first['saved'][0], 'tester', self.now)
            stale = connection.execute('SELECT expiry_status, odds_id FROM sports_ai_sportybet_observations').fetchone()
            self.assertEqual(stale['expiry_status'], 'stale')
            self.assertIsNone(stale['odds_id'])

    def test_supervised_import_does_not_invent_edge_for_an_inactive_model(self):
        with db.connect() as connection:
            fixture_id = self._fixture(connection, 'Arsenal', 'Chelsea')
            self._prediction(connection, fixture_id, 'HOME', active=False, status='validated')
            observation_id = self._ready(connection, fixture_id)
            result = import_observation(connection, observation_id, 'tester', self.now)
            self.assertEqual(result['value_status'], 'awaiting_calibration')
            self.assertIsNone(result['expected_edge'])
            opportunity = connection.execute('SELECT expected_edge, status FROM sports_ai_value_opportunities').fetchone()
            self.assertIsNone(opportunity['expected_edge'])
            self.assertEqual(opportunity['status'], 'awaiting_calibration')
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM sports_ai_booking_codes').fetchone()[0], 0)

    def test_active_validated_model_can_calculate_edge(self):
        with db.connect() as connection:
            fixture_id = self._fixture(connection, 'Arsenal', 'Chelsea')
            self._prediction(connection, fixture_id, 'HOME', active=True, status='validated', probability=0.6)
            observation_id = self._ready(connection, fixture_id)
            result = import_observation(connection, observation_id, 'tester', self.now)
            self.assertEqual(result['value_status'], 'qualified')
            self.assertAlmostEqual(result['expected_edge'], 0.26, places=2)
            again = dict(connection.execute('SELECT * FROM sports_ai_sportybet_observations').fetchone())
            again_id = str(uuid.uuid4())
            connection.execute(
                """INSERT INTO sports_ai_sportybet_observations
                   (id,sport,competition,home,away,starts_at,market_label,selection_label,decimal_odds,source_url,
                    observed_at,confirmation_status,match_status,fixture_id,match_confidence,market_status,
                    canonical_market,import_status,evidence_json,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'not_imported','{}',?)""",
                (again_id, 'football', 'Premier League', 'Arsenal', 'Chelsea', self.kickoff, '1X2', 'Home', 2.1,
                 'https://www.sportybet.com/other', self.now.isoformat(), 'confirmed', 'confirmed', fixture_id, 0.9,
                 'confirmed', 'HOME', self.now.isoformat()),
            )
            with self.assertRaises(ValueError):
                import_observation(connection, again_id, 'tester', self.now)

    def test_permissions_and_unattended_gate(self):
        self.assertTrue(_operator_permitted('local-preview-user', None))
        self.assertFalse(_operator_permitted('local-dev-user', None))
        previous = os.environ.get('SPORTS_ADMIN_API_KEY')
        os.environ['SPORTS_ADMIN_API_KEY'] = 'test-admin-key'
        try:
            self.assertTrue(_operator_permitted('local-dev-user', 'test-admin-key'))
            self.assertFalse(_operator_permitted('local-dev-user', 'wrong'))
        finally:
            if previous is None:
                os.environ.pop('SPORTS_ADMIN_API_KEY', None)
            else:
                os.environ['SPORTS_ADMIN_API_KEY'] = previous
        with db.connect() as connection:
            self.assertFalse(unattended_status(connection)['operational'])
            self.assertFalse(unattended_status(connection)['authorized'])

    def test_visible_text_parser_reads_local_rows_only(self):
        rows = parse_visible_text('FIXTURE|football|Premier League|Arsenal|Chelsea|2026-11-08T15:00:00+00:00|1X2|Home|2.10\nnoise')
        self.assertEqual(rows[0]['home'], 'Arsenal')
        self.assertEqual(rows[0]['decimal_odds'], 2.1)
        self.assertEqual(parse_visible_text('verify you are human'), [])

    def _ready(self, connection, fixture_id):
        stored = store_observations(connection, None, [{
            'sport': 'football', 'competition': 'Premier League', 'home': 'Arsenal', 'away': 'Chelsea',
            'starts_at': self.kickoff, 'market_label': '1X2', 'selection_label': 'Home', 'decimal_odds': 2.1,
        }], 'https://www.sportybet.com/fixture', self.now.isoformat())
        observation_id = stored['saved'][0]
        confirm_observation(connection, observation_id, 'tester', self.now)
        match_observation(connection, observation_id, fixture_id)
        validate_observation_market(connection, observation_id)
        return observation_id


class SportyBetBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as playwright:
                cls.chromium = os.path.exists(playwright.chromium.executable_path)
        except Exception:
            cls.chromium = False
        cls.temp = tempfile.TemporaryDirectory()
        cls.previous = db.DB
        cls.env = {
            'SPORTS_BROWSER_ALLOW_LOCAL_TESTS': os.environ.get('SPORTS_BROWSER_ALLOW_LOCAL_TESTS'),
            'SPORTS_BROWSER_HEADLESS': os.environ.get('SPORTS_BROWSER_HEADLESS'),
            'SPORTS_BROWSER_CAPTURE_DIR': os.environ.get('SPORTS_BROWSER_CAPTURE_DIR'),
        }
        db.DB = Path(cls.temp.name) / 'browser.sqlite3'
        os.environ['SPORTS_BROWSER_ALLOW_LOCAL_TESTS'] = '1'
        os.environ['SPORTS_BROWSER_HEADLESS'] = '1'
        os.environ['SPORTS_BROWSER_CAPTURE_DIR'] = str(Path(cls.temp.name) / 'captures')
        db.initialize()

    @classmethod
    def tearDownClass(cls):
        db.DB = cls.previous
        for key, value in cls.env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        cls.temp.cleanup()

    def setUp(self):
        if not self.chromium:
            self.skipTest('Chromium is not installed for the supervised browser test')
        with db.connect() as connection:
            connection.execute('DELETE FROM sports_ai_sportybet_observations')
            connection.execute('DELETE FROM sports_ai_sportybet_events')
            connection.execute('DELETE FROM sports_ai_sportybet_sessions')

    def _serve(self, html):
        server = ThreadingHTTPServer(('127.0.0.1', 0), _html(html))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        return f'http://127.0.0.1:{server.server_port}/'

    def _wait(self, browser, states):
        last = None
        deadline = time.time() + 30
        while time.time() < deadline:
            last = browser.status()
            if last['state'] in states:
                return last
            time.sleep(0.25)
        return last

    def test_browser_reads_a_local_page_and_stops(self):
        url = self._serve('<html><body><pre>FIXTURE|football|Premier League|Arsenal|Chelsea|2026-11-08T15:00:00+00:00|1X2|Home|2.10</pre></body></html>')
        browser = SportyBetOperator()
        self.addCleanup(browser.stop)
        browser.start('tester', target_url=url)
        status = self._wait(browser, {'AWAITING_CONFIRMATION', 'FAILED', 'BLOCKED'})
        self.assertEqual(status['state'], 'AWAITING_CONFIRMATION', status.get('error'))
        self.assertGreaterEqual(status['observation_count'], 1)
        with db.connect() as connection:
            row = connection.execute('SELECT confirmation_status, decimal_odds FROM sports_ai_sportybet_observations').fetchone()
            self.assertEqual(row['confirmation_status'], 'proposed')
            self.assertEqual(row['decimal_odds'], 2.1)
            self.assertTrue(connection.execute('SELECT COUNT(*) FROM sports_ai_sportybet_events WHERE screenshot_path IS NOT NULL').fetchone()[0] >= 1)
        browser.stop()
        stopped = self._wait(browser, {'COMPLETED', 'FAILED'})
        self.assertEqual(stopped['state'], 'COMPLETED')

    def test_challenge_page_blocks_extraction(self):
        url = self._serve('<html><body><h1>Verify you are human</h1><p>2.10</p></body></html>')
        browser = SportyBetOperator()
        self.addCleanup(browser.stop)
        browser.start('tester', target_url=url)
        status = self._wait(browser, {'BLOCKED', 'FAILED'})
        self.assertEqual(status['state'], 'BLOCKED', status.get('error'))
        with db.connect() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM sports_ai_sportybet_observations').fetchone()[0], 0)
        browser.stop()

    def test_abandoned_session_is_recovered_without_import(self):
        browser = SportyBetOperator()
        with db.connect() as connection:
            stamp = datetime.now(timezone.utc).isoformat()
            connection.execute(
                """INSERT INTO sports_ai_sportybet_sessions
                   (id,mode,state,last_operation,started_at,heartbeat_at,actor)
                   VALUES('stale','supervised','NAVIGATING','Opening page',?,?,'tester')""",
                (stamp, stamp),
            )
        browser.recover()
        with db.connect() as connection:
            row = connection.execute("SELECT state, error FROM sports_ai_sportybet_sessions WHERE id='stale'").fetchone()
        self.assertEqual(row['state'], 'FAILED')
        self.assertIn('No quotes were imported', row['error'])
