"""A guard that refuses one ticket must not stall the booking lane (2026-11-10)."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import db
import sportybet_booking as sb

REASON = 'Basketball requires a loss-audited candidate with passing out-of-sample league and market evidence.'


class BookingRefusalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = db.DB
        db.DB = Path(self.temp.name) / 'sports.sqlite3'
        db.initialize()

    def tearDown(self):
        db.DB = self.previous
        self.temp.cleanup()

    def test_refused_ticket_is_recorded_and_lane_does_not_raise(self):
        selections = [{'ticket_id': 't-basket', 'fixture_id': 'fx', 'market': 'MONEYLINE_HOME', 'selection': 'Home',
                       'sport': 'basketball', 'home': 'A', 'away': 'B', 'decimal_odds': 1.8}]
        now = datetime.now(timezone.utc)
        with patch.object(sb, 'select_booking', return_value={'action': 'ticket', 'phase': 'open', 'open': True, 'ticket_id': 't-basket'}), \
             patch.object(sb, 'ticket_selections', return_value=selections), \
             patch.object(sb, 'create_job', side_effect=ValueError(REASON)), \
             patch.object(sb, 'examine_ready_tickets', return_value=0), \
             patch.object(sb, 'execute_job') as browser:
            result = sb.process_booking_queue(now=now, ignore_gap=True)
        self.assertEqual(result['status'], 'NOT_BOOKABLE')
        self.assertIn('loss-audited', result['error'])
        browser.assert_not_called()
        with db.connect() as c:
            job = c.execute("SELECT status, reason_code, error, ticket_id, updated_at FROM sports_ai_booking_jobs").fetchone()
        self.assertEqual((job['status'], job['reason_code'], job['ticket_id']), ('FAILED', 'NOT_BOOKABLE', 't-basket'))
        # The picker's "recent job" rule now skips this ticket, so the next ticket gets the lane.
        self.assertGreater(job['updated_at'], (now.replace(microsecond=0)).isoformat()[:16])


if __name__ == '__main__':
    unittest.main()
