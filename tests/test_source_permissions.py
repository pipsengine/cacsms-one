"""Phase 4: a recorded terms decision plus robots.txt gate every collector."""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

import db
import source_permissions as sp

ROBOTS = 'User-agent: *\nDisallow: /ng/search\n'
NOW = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)


def fetcher(status='present', text=ROBOTS, calls=None):
    def fetch(host):
        if calls is not None:
            calls.append(host)
        return status, text
    return fetch


class SourcePermissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = db.DB
        db.DB = Path(self.temp.name) / 'perm.sqlite3'
        db.initialize()

    def tearDown(self):
        db.DB = self.previous
        self.temp.cleanup()

    def test_unreviewed_host_is_pending_and_denied(self):
        with db.connect() as c:
            allowed, reason = sp.evaluate(c, 'https://stats.example/fixtures', now=NOW, fetcher=fetcher())
            check = c.execute('SELECT allowed,reason FROM sports_ai_source_permission_checks').fetchone()
        self.assertFalse(allowed)
        self.assertEqual((check[0], check[1]), (0, reason))

    def test_env_allowlisted_host_is_recorded_as_approved(self):
        with patch.dict(os.environ, {'SPORTS_AI_AUTHORIZED_DOMAINS': 'stats.example'}), db.connect() as c:
            allowed, _ = sp.evaluate(c, 'https://stats.example/fixtures', now=NOW, fetcher=fetcher())
            self.assertEqual(sp.terms_decision(c, 'stats.example'), (True, 'env:SPORTS_AI_AUTHORIZED_DOMAINS'))
            sp.record_review(c, 'stats.example', False, 'op', 'https://stats.example/terms', now=NOW)
            self.assertFalse(sp.evaluate(c, 'https://stats.example/fixtures', now=NOW, fetcher=fetcher())[0])
        self.assertTrue(allowed)

    def test_sportybet_uses_its_collection_authorization(self):
        with db.connect() as c:
            c.execute("""INSERT INTO sports_ai_collection_authorizations(id,bookmaker,scope,status,evidence,actor,created_at)
                         VALUES('a','SportyBet','unattended','authorized','{}','owner','2026-11-01T00:00:00+00:00')""")
            self.assertTrue(sp.evaluate(c, 'https://www.sportybet.com/ng/sport/football', now=NOW, fetcher=fetcher())[0])
            allowed, reason = sp.evaluate(c, 'https://www.sportybet.com/ng/search?q=x', now=NOW, fetcher=fetcher())
        self.assertFalse(allowed)
        self.assertIn('robots.txt disallows', reason)

    def test_review_approves_and_blocks(self):
        with db.connect() as c:
            sp.record_review(c, 'stats.example', True, 'operator', 'https://stats.example/terms', now=NOW)
            self.assertTrue(sp.evaluate(c, 'https://stats.example/a', now=NOW, fetcher=fetcher())[0])
            sp.record_review(c, 'stats.example', False, 'operator', 'https://stats.example/terms', now=NOW)
            self.assertFalse(sp.evaluate(c, 'https://stats.example/a', now=NOW, fetcher=fetcher())[0])
            with self.assertRaises(ValueError):
                sp.record_review(c, 'stats.example', True, '', 'https://stats.example/terms')
            with self.assertRaises(ValueError):
                sp.record_review(c, 'stats.example', True, 'op', 'http://insecure')

    def test_missing_robots_allows_and_unreachable_denies(self):
        with db.connect() as c:
            sp.record_review(c, 'a.example', True, 'op', 'https://x.example/terms', now=NOW)
            sp.record_review(c, 'b.example', True, 'op', 'https://x.example/terms', now=NOW)
            self.assertTrue(sp.evaluate(c, 'https://a.example/x', now=NOW, fetcher=fetcher('absent', ''))[0])
            allowed, reason = sp.evaluate(c, 'https://b.example/x', now=NOW, fetcher=fetcher('unreachable', None))
        self.assertFalse(allowed)
        self.assertIn('could not be read', reason)

    def test_robots_is_cached_for_a_day_and_kept_through_a_short_outage(self):
        calls = []
        with db.connect() as c:
            sp.record_review(c, 'a.example', True, 'op', 'https://x.example/terms', now=NOW)
            sp.evaluate(c, 'https://a.example/x', now=NOW, fetcher=fetcher(calls=calls))
            sp.evaluate(c, 'https://a.example/x', now=NOW + timedelta(hours=2), fetcher=fetcher(calls=calls))
            self.assertEqual(len(calls), 1)
            allowed, _ = sp.evaluate(c, 'https://a.example/x', now=NOW + timedelta(days=2),
                                     fetcher=fetcher('unreachable', None, calls))
        self.assertEqual(len(calls), 2)
        self.assertTrue(allowed)

    def test_require_raises_and_unreachable_is_transient_for_bookings(self):
        from browser_slots import classify_browser_error
        with db.connect() as c:
            sp.record_review(c, 'b.example', True, 'op', 'https://x.example/terms', now=NOW)
            with self.assertRaises(sp.SourceNotPermitted) as caught:
                sp.require('https://b.example/x', connection=c, now=NOW, fetcher=fetcher('unreachable', None))
        self.assertEqual(classify_browser_error(caught.exception), 'transient')

    def test_browser_job_url_check_enforces_permissions(self):
        import browser_jobs
        with patch.dict(os.environ, {'SPORTS_AI_AUTHORIZED_DOMAINS': 'stats.example'}), \
                patch('browser_operator._host_addresses', return_value={__import__('ipaddress').ip_address('93.184.216.34')}), \
                patch.object(sp, '_fetch_robots', fetcher()):
            with db.connect() as c:
                self.assertEqual(browser_jobs.check_url('https://stats.example/fixtures', c), 'stats.example')
                sp.record_review(c, 'stats.example', False, 'op', 'https://x.example/terms')
                with self.assertRaisesRegex(ValueError, 'no approved terms review'):
                    browser_jobs.check_url('https://stats.example/fixtures', c)

    def test_source_health(self):
        with db.connect() as c:
            sp.evaluate(c, 'https://stats.example/fixtures', now=NOW, fetcher=fetcher())
            health = sp.source_health(c, NOW)
        host, = [h for h in health['hosts'] if h['host'] == 'stats.example']
        self.assertEqual((host['checks_denied'], host['approved'], host['robots_status']), (1, False, 'unchecked'))
        self.assertEqual(len(health['recent_denials']), 1)


if __name__ == '__main__':
    unittest.main()
