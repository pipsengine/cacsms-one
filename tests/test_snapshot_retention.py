import json
import sqlite3
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / 'backend'))
from snapshot_retention import cleanup_settled_odds


class SnapshotRetentionTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
        self.c = sqlite3.connect(':memory:')
        self.c.row_factory = sqlite3.Row
        self.c.executescript('''
            PRAGMA foreign_keys=ON;
            CREATE TABLE sports_ai_fixtures(id TEXT PRIMARY KEY,status TEXT);
            CREATE TABLE sports_ai_results(fixture_id TEXT,status TEXT,home_score INTEGER,
                away_score INTEGER,settled_at TEXT);
            CREATE TABLE sports_ai_accumulator_legs(id TEXT,ticket_id TEXT,fixture_id TEXT,
                starts_at TEXT,sport TEXT,market TEXT,result TEXT,decimal_odds REAL);
            CREATE TABLE sports_ai_ticket_settlements(ticket_id TEXT,outcome TEXT);
            CREATE TABLE sports_ai_odds_snapshots(id TEXT PRIMARY KEY,fixture_id TEXT);
            CREATE TABLE sports_ai_value_opportunities(id TEXT,odds_id TEXT
                REFERENCES sports_ai_odds_snapshots(id) ON DELETE SET NULL);
            CREATE TABLE sports_ai_sportybet_observations(odds_id TEXT);
            CREATE TABLE sports_ai_ticket_legs(ticket_id TEXT,fixture_id TEXT);
            CREATE TABLE sports_ai_audit_events(id TEXT,actor TEXT,action TEXT,
                metadata_json TEXT,created_at TEXT);
            CREATE TABLE sports_ai_settings(key TEXT PRIMARY KEY,value_json TEXT,updated_at TEXT);
            CREATE TABLE sports_ai_data_quality_issues(fixture_id TEXT,resolved_at TEXT,severity TEXT);
            INSERT INTO sports_ai_fixtures VALUES('f','finished');
            INSERT INTO sports_ai_results VALUES('f','final',2,1,'2026-11-09T11:00:00+00:00');
            INSERT INTO sports_ai_accumulator_legs VALUES(
                'l','t','f','2026-11-09T09:00:00+00:00','football','HOME','won',2);
            INSERT INTO sports_ai_ticket_settlements VALUES('t','won');
            INSERT INTO sports_ai_odds_snapshots VALUES('q1','f'),('q2','f');
            INSERT INTO sports_ai_value_opportunities VALUES('o','q1');
            INSERT INTO sports_ai_sportybet_observations VALUES('q1');
        ''')

    def tearDown(self):
        self.c.close()

    def cleanup(self, **kwargs):
        return cleanup_settled_odds(self.c, self.now, **kwargs)

    def test_deletes_raw_odds_and_retains_final_ticket_evidence(self):
        self.assertEqual(self.cleanup()['deleted_odds_snapshots'], 2)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM sports_ai_accumulator_legs').fetchone()[0], 1)
        self.assertEqual(self.c.execute('SELECT outcome FROM sports_ai_ticket_settlements').fetchone()[0], 'won')
        self.assertIsNone(self.c.execute('SELECT odds_id FROM sports_ai_value_opportunities').fetchone()[0])
        self.assertIsNone(self.c.execute('SELECT odds_id FROM sports_ai_sportybet_observations').fetchone()[0])
        metadata = json.loads(self.c.execute('SELECT metadata_json FROM sports_ai_audit_events').fetchone()[0])
        self.assertEqual(metadata['deleted_odds_snapshots'], 2)
        self.assertEqual(self.cleanup()['deleted_odds_snapshots'], 0)
        self.assertEqual(self.c.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_expired_or_losing_ticket_is_not_enough_without_all_results(self):
        self.c.execute('DELETE FROM sports_ai_ticket_settlements')
        self.assertEqual(self.cleanup()['deleted_odds_snapshots'], 0)

    def test_shared_fixture_waits_for_other_entire_ticket(self):
        self.c.execute("INSERT INTO sports_ai_accumulator_legs SELECT 'other', 'other', fixture_id, starts_at, sport, market, result, decimal_odds FROM sports_ai_accumulator_legs")
        self.assertEqual(self.cleanup()['deleted_odds_snapshots'], 0)

    def test_pending_leg_on_same_ticket_blocks_cleanup(self):
        self.c.execute("INSERT INTO sports_ai_accumulator_legs VALUES('l2','t','pending','2026-11-09T09:00:00+00:00','football','HOME','pending',2)")
        self.assertEqual(self.cleanup()['deleted_odds_snapshots'], 0)

    def test_postponed_or_unconfirmed_result_blocks_cleanup(self):
        for status in ('postponed', 'cancelled', 'abandoned'):
            self.c.execute('UPDATE sports_ai_fixtures SET status=?', (status,))
            self.assertEqual(self.cleanup()['deleted_odds_snapshots'], 0)

    def test_corrected_result_blocks_cleanup_until_resettled(self):
        self.c.execute('UPDATE sports_ai_results SET home_score=0')
        self.assertEqual(self.cleanup()['deleted_odds_snapshots'], 0)

    def test_result_timestamp_must_follow_kickoff_and_not_be_future(self):
        for stamp in ('2026-11-09T08:00:00+00:00', '2026-11-09T13:00:00+00:00'):
            self.c.execute('UPDATE sports_ai_results SET settled_at=?', (stamp,))
            self.assertEqual(self.cleanup()['deleted_odds_snapshots'], 0)

    def test_batch_limit_leaves_remaining_snapshots_for_next_pass(self):
        self.assertEqual(self.cleanup(batch_size=1)['deleted_odds_snapshots'], 1)
        self.assertEqual(self.cleanup()['deleted_odds_snapshots'], 1)

    def test_legacy_ticket_reference_is_preserved(self):
        self.c.execute("INSERT INTO sports_ai_ticket_legs VALUES('legacy','f')")
        self.assertEqual(self.cleanup()['deleted_odds_snapshots'], 0)

    def test_zero_deletion_run_records_its_blockers(self):
        self.c.execute('DELETE FROM sports_ai_ticket_settlements WHERE ticket_id=?', ('t',))
        # Keep a settled ticket referring to the same fixture as the open ticket.
        self.c.execute("INSERT INTO sports_ai_accumulator_legs SELECT 'l2','t2',fixture_id,starts_at,sport,market,result,decimal_odds FROM sports_ai_accumulator_legs")
        self.c.execute("INSERT INTO sports_ai_ticket_settlements VALUES('t2','won')")
        result = self.cleanup()
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['ticket_blocker_counts'], {'ticket_not_settled': 1})
        stored = json.loads(self.c.execute("SELECT value_json FROM sports_ai_settings WHERE key='snapshot_cleanup'").fetchone()[0])
        self.assertEqual(stored, result)

    def test_result_conflict_blocks_cleanup(self):
        self.c.execute("INSERT INTO sports_ai_data_quality_issues VALUES('f',NULL,'high')")
        self.assertEqual(self.cleanup()['ticket_blocker_counts'], {'unresolved_data_issue': 1})


if __name__ == '__main__':
    unittest.main()
