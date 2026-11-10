"""Connection & Diagnostics: honest provider states and safe actions."""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

import db
import source_diagnostics as sd

NOW = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)


def row(**overrides):
    base = {'adapter': 'openligadb', 'credential_env': '', 'enabled': 1, 'terms_reviewed': 1,
            'last_success_at': (NOW - timedelta(hours=1)).isoformat(), 'last_attempt_at': (NOW - timedelta(hours=1)).isoformat(),
            'last_error': None, 'interval_minutes': 360}
    base.update(overrides)
    return base


class StateTests(unittest.TestCase):
    def test_states(self):
        self.assertEqual(sd.provider_state(row(), None, NOW)[0], 'healthy')
        self.assertEqual(sd.provider_state(row(last_success_at=(NOW - timedelta(days=2)).isoformat()), None, NOW)[0], 'stale')
        failing = row(last_error='DNS failure', last_attempt_at=NOW.isoformat())
        self.assertEqual(sd.provider_state(failing, None, NOW)[0], 'failing')
        paused = sd.provider_state(failing, {'paused': 1, 'updated_by': 'ops', 'updated_at': NOW.isoformat()}, NOW)
        self.assertEqual(paused[0], 'paused')
        self.assertIn('DNS failure', paused[2])
        self.assertEqual(sd.provider_state(row(enabled=0), None, NOW)[0], 'disabled')
        self.assertEqual(sd.provider_state(row(last_success_at=None, last_attempt_at=None), None, NOW)[0], 'awaiting')
        with unittest.mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('SPORTS_TEST_MISSING_KEY', None)
            self.assertEqual(sd.provider_state(row(credential_env='SPORTS_TEST_MISSING_KEY'), None, NOW)[0], 'not_configured')

    def test_old_error_after_newer_success_is_not_failing(self):
        recovered = row(last_error='old', last_attempt_at=(NOW - timedelta(hours=2)).isoformat())
        self.assertEqual(sd.provider_state(recovered, None, NOW)[0], 'healthy')

    def test_browser_source_without_terms_review_is_not_enabled(self):
        self.assertEqual(sd.provider_state(row(adapter='browser'), None, NOW, permission=(False, None))[0], 'disabled')


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = db.DB
        db.DB = Path(self.temp.name) / 'diag.sqlite3'
        db.initialize()
        sd._FIXTURE_CACHE.update(at=0.0, value=None)
        sd._BROWSER_CACHE.update(at=10**12, value={'installed': True, 'chromium': True, 'status': 'ok', 'detail': ''})

    def tearDown(self):
        sd._BROWSER_CACHE.update(at=0.0, value=None)
        db.DB = self.previous
        self.temp.cleanup()

    def test_each_provider_once_with_actions_only_where_supported(self):
        with db.connect() as c:
            report = sd.diagnostics(c, NOW)
        ids = [p['id'] for p in report['providers']]
        self.assertEqual(len(ids), len(set(ids)))
        for p in report['providers']:
            if p['adapter'] not in sd.API_ADAPTERS:
                self.assertFalse(any(p['actions'].values()), p['name'])
        self.assertIn('scheduler', report)
        self.assertIn(report['sportybet']['state'], ('awaiting', 'healthy', 'stale', 'failing'))

    def test_paused_schedule_is_reported_and_resumable(self):
        with db.connect() as c:
            provider = c.execute("SELECT id FROM sports_ai_provider_registry WHERE adapter='openligadb'").fetchone()[0]
            c.execute("UPDATE sports_ai_provider_registry SET enabled=1,terms_reviewed=1,last_error='DNS',last_attempt_at=? WHERE id=?",
                      (NOW.isoformat(), provider))
            c.execute("""INSERT INTO sports_ai_acquisition_schedules(provider_id,enabled,interval_minutes,paused,updated_by,updated_at)
                         VALUES(?,1,360,1,'ops',?) ON CONFLICT(provider_id) DO UPDATE SET paused=1""", (provider, NOW.isoformat()))
            report = sd.diagnostics(c, NOW)
        item = next(p for p in report['providers'] if p['id'] == provider)
        self.assertEqual(item['state'], 'paused')
        self.assertTrue(item['actions']['resume'])
        self.assertFalse(item['actions']['collect'])
        self.assertTrue(any(i.get('action') == 'resume' for i in report['issues']))

    def test_api_refuses_browser_test_without_recording_a_failure_and_resumes_api_schedule(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from acquisition_api import build_acquisition_router
        app = FastAPI()
        app.include_router(build_acquisition_router(lambda: 'tester'))
        client = TestClient(app)
        with db.connect() as c:
            browser = c.execute("SELECT id FROM sports_ai_provider_registry WHERE adapter='browser'").fetchone()[0]
            api = c.execute("SELECT id FROM sports_ai_provider_registry WHERE adapter='openligadb'").fetchone()[0]
            c.execute("UPDATE sports_ai_provider_registry SET terms_reviewed=1,status='operational',last_error=NULL WHERE id=?", (browser,))
            c.execute("UPDATE sports_ai_provider_registry SET terms_reviewed=1 WHERE id=?", (api,))
            c.execute("""INSERT INTO sports_ai_acquisition_schedules(provider_id,enabled,interval_minutes,paused,updated_by,updated_at)
                         VALUES(?,1,360,1,'ops',?) ON CONFLICT(provider_id) DO UPDATE SET paused=1""", (api, NOW.isoformat()))
        self.assertEqual(client.post(f'/api/sports-ai/acquisition/sources/{browser}/test').status_code, 422)
        self.assertEqual(client.post(f'/api/sports-ai/acquisition/sources/{browser}/collect').status_code, 422)
        with db.connect() as c:
            status, error = c.execute('SELECT status,last_error FROM sports_ai_provider_registry WHERE id=?', (browser,)).fetchone()
        self.assertEqual((status, error), ('operational', None))
        self.assertEqual(client.post(f'/api/sports-ai/acquisition/sources/{browser}/resume').status_code, 422)
        response = client.post(f'/api/sports-ai/acquisition/sources/{api}/resume')
        self.assertEqual(response.status_code, 200, response.text)
        with db.connect() as c:
            paused = c.execute('SELECT paused,enabled FROM sports_ai_acquisition_schedules WHERE provider_id=?', (api,)).fetchone()
            enabled = c.execute('SELECT enabled FROM sports_ai_provider_registry WHERE id=?', (api,)).fetchone()[0]
            audited = c.execute("SELECT 1 FROM sports_ai_audit_events WHERE action='acquisition.source.resumed'").fetchone()
        self.assertEqual(tuple(paused), (0, 1))
        self.assertEqual(enabled, 1)
        self.assertTrue(audited)


import unittest.mock  # noqa: E402

if __name__ == '__main__':
    unittest.main()
