import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

from autonomous_engine import apply_control, build_overview, production_readiness, record_stage
import db

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)


class AutonomousIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.previous = db.DB
        self.temp = tempfile.TemporaryDirectory()
        db.DB = Path(self.temp.name) / 'sports.sqlite3'
        db.initialize()
        self.preview = os.environ.get('SPORTS_LOCAL_PREVIEW')
        os.environ['SPORTS_LOCAL_PREVIEW'] = '1'

    def tearDown(self):
        if self.preview is None:
            os.environ.pop('SPORTS_LOCAL_PREVIEW', None)
        else:
            os.environ['SPORTS_LOCAL_PREVIEW'] = self.preview
        db.DB = self.previous
        self.temp.cleanup()

    def test_overview_covers_the_pipeline_and_never_places_a_wager(self):
        with db.connect() as connection:
            overview = build_overview(connection, NOW)
        names = [stage['name'] for stage in overview['stages']]
        self.assertIn('Qualify', names)
        self.assertIn('Tickets & Booking Codes', names)
        self.assertIn('Optimize', names)
        self.assertNotIn('Rollover', names)
        self.assertIn('Learn', names)
        self.assertFalse(overview['places_wager'])
        self.assertFalse(overview['pipeline']['places_wager'])
        self.assertIn('QUALIFIED', [item['stage'] for item in overview['pipeline']['stages']])
        self.assertFalse(overview['readiness']['places_wager'])
        self.assertFalse(overview['readiness']['ready'])

    def test_a_stage_cannot_record_a_paid_wager(self):
        with db.connect() as connection:
            record_stage(connection, 'booking', 'complete', {'places_wager': False, 'status': 'idle'}, NOW)
            with self.assertRaises(ValueError):
                record_stage(connection, 'booking', 'complete', {'places_wager': True}, NOW)
            stored = connection.execute('SELECT places_wager FROM sports_ai_autonomous_cycles').fetchone()['places_wager']
        self.assertEqual(stored, 0)

    def test_pause_and_resume_do_not_enable_wagering(self):
        def audit(connection, actor, action, details):
            self.assertFalse(details['places_wager'])
            self.assertEqual(action, 'autonomous.control')
        with db.connect() as connection:
            paused = apply_control(connection, 'pause', audit, 'tester', NOW)
            resumed = apply_control(connection, 'resume', audit, 'tester', NOW)
        self.assertEqual(paused['status'], 'paused')
        self.assertFalse(paused['places_wager'])
        self.assertFalse(resumed['places_wager'])
        self.assertIn(resumed['status'], ('running', 'stopped'))

    def test_readiness_names_the_open_production_controls(self):
        with db.connect() as connection:
            report = production_readiness(connection, NOW)
        by_name = {item['name']: item for item in report['checks']}
        self.assertTrue(by_name['Paid wagering']['ok'])
        self.assertTrue(by_name['Application rate limit']['ok'])
        self.assertIn('240', by_name['Application rate limit']['detail'])
        self.assertFalse(by_name['Production session']['ok'])
        self.assertTrue(by_name['Schema migration 026']['ok'])
        self.assertIn('does not place a paid wager', report['procedures'][0])
