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

    def test_ten_stages_use_stored_counts_and_keep_settlement_independent(self):
        now = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
        with db.connect() as connection:
            board = workflow_board(connection, now, ready_slips=0, qualified_visible=0, win_rate=None)
        names = [item['name'] for item in board['stages']]
        self.assertEqual(names, [
            'Discover', 'Collect Odds', 'Validate', 'Analyse', 'Qualify',
            'Tickets', 'Rollover', 'Booking Codes', 'Settle', 'Learn',
        ])
        lanes = {item['name']: item['lane'] for item in board['stages']}
        self.assertEqual(lanes['Discover'], 'upcoming')
        self.assertEqual(lanes['Settle'], 'completed')
        self.assertEqual(lanes['Learn'], 'completed')
        self.assertFalse(board['places_wager'])
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


if __name__ == '__main__':
    unittest.main()
