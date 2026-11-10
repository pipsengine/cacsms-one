import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

import db
from workflow_status import workflow_board


class WorkflowBoardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = db.DB
        db.DB = Path(self.temp.name) / 'sports.sqlite3'
        db.initialize()

    def tearDown(self):
        db.DB = self.previous
        self.temp.cleanup()

    def test_nine_stages_keep_rollover_beside_settlement(self):
        now = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
        with db.connect() as connection:
            board = workflow_board(connection, now, ready_slips=2, qualified_visible=0, win_rate=None)
        names = [item['name'] for item in board['stages']]
        self.assertEqual(names, [
            'Discover', 'Collect Odds', 'Validate', 'Analyse', 'Qualify',
            'Tickets & Booking Codes', 'Settle', 'Learn', 'Optimize',
        ])
        lanes = {item['name']: item['lane'] for item in board['stages']}
        self.assertEqual(lanes['Discover'], 'upcoming')
        self.assertEqual(lanes['Settle'], 'completed')
        self.assertEqual(lanes['Learn'], 'completed')
        self.assertEqual(board['parallel']['name'], 'Rollover')
        self.assertNotIn('Rollover', names)
        stage_six = board['stages'][5]
        self.assertIn('displayed', stage_six['explanation'])
        self.assertIn('2 ready', stage_six['count'])
        self.assertFalse(board['places_wager'])
        self.assertFalse(board['parallel']['places_wager'])
        self.assertTrue(board['asynchronous'])
        self.assertEqual(board['metrics'][5]['value'], 'Awaiting results')
        self.assertEqual(board['frequency'], 'Every 30 seconds')
        discover = board['stages'][0]
        self.assertIn('competitions', discover['count'])
        self.assertNotIn('8,462', board['metrics'][0]['value'])
        with db.connect() as connection:
            connection.execute(
                """INSERT INTO sports_ai_data_quality_issues
                   (id,provider_id,severity,issue_type,details_json,created_at)
                   VALUES('dns-1',NULL,'high','provider_collection_failed','{}',?)""",
                (now.isoformat(),),
            )
            again = workflow_board(connection, now)
        self.assertEqual(again['stages'][2]['status'], 'Completed')
        self.assertEqual(again['stages'][2]['count'], 'Clear')
        with db.connect() as connection:
            connection.execute(
                """INSERT INTO sports_ai_fixtures
                   (id,external_id,source,sport,competition,home,away,starts_at,status,statistics_json,odds_json,observed_at)
                   VALUES('fx-1','ext-1','betexplorer','volleyball','Europe: Challenge Cup Women','Roeselare W','Charleroi W',?,'finished','{}','{}',?)""",
                (now.isoformat(), now.isoformat()),
            )
            connection.execute(
                """INSERT INTO sports_ai_data_quality_issues
                   (id,fixture_id,severity,issue_type,details_json,created_at)
                   VALUES('fx-issue','fx-1','high','result_conflict','{}',?)""",
                (now.isoformat(),),
            )
            single = workflow_board(connection, now)
        # A score dispute on a finished match cannot hold a qualification; it belongs to Settle.
        self.assertEqual(single['stages'][2]['status'], 'Completed')
        self.assertEqual(single['stages'][2]['count'], 'Clear')
        self.assertEqual(single['stages'][6]['status'], 'Attention')
        self.assertIn('1 result conflict', single['stages'][6]['count'])
        with db.connect() as connection:
            connection.execute(
                """INSERT INTO sports_ai_fixtures
                   (id,external_id,source,sport,competition,home,away,starts_at,status,statistics_json,odds_json,observed_at)
                   VALUES('fx-2','ext-2','test','football','League','Home','Away','2026-11-07T15:00:00+00:00','scheduled','{}','{}',?)""",
                (now.isoformat(),),
            )
            connection.execute(
                """INSERT INTO sports_ai_data_quality_issues
                   (id,fixture_id,severity,issue_type,details_json,created_at)
                   VALUES('fx-issue-2','fx-2','high','odds_conflict','{}',?)""",
                (now.isoformat(),),
            )
            upcoming = workflow_board(connection, now)
        self.assertEqual(upcoming['stages'][2]['status'], 'Attention')
        self.assertEqual(upcoming['stages'][2]['count'], '1 open issue')


if __name__ == '__main__':
    unittest.main()
