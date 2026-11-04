import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

from booking_schedule import phase_at, publication_open
from sportybet_booking import can_retry, expire_displayed_codes
import db


class WatWindowTests(unittest.TestCase):
    def test_named_windows_and_the_gaps_between_them(self):
        wat = timezone(timedelta(hours=1))
        cases = {
            (0, 0): 'discovery',
            (4, 0): 'discovery',
            (4, 1): 'event',
            (5, 0): 'qualification',
            (6, 30): 'qualification',
            (6, 31): 'event',
            (7, 0): 'publication',
            (8, 0): 'publication',
            (8, 1): 'event',
            (12, 0): 'midday',
            (13, 0): 'midday',
            (16, 59): 'event',
            (17, 0): 'evening',
            (18, 0): 'evening',
            (18, 1): 'event',
        }
        for (hour, minute), expected in cases.items():
            phase = phase_at(datetime(2026, 11, 4, hour, minute, tzinfo=wat))
            self.assertEqual(phase, expected, f'{hour:02d}:{minute:02d}')
        self.assertTrue(publication_open('publication'))
        self.assertTrue(publication_open('midday'))
        self.assertTrue(publication_open('evening'))
        self.assertFalse(publication_open('qualification'))
        self.assertFalse(publication_open('discovery'))
        self.assertFalse(publication_open('event'))

    def test_retries_stop_for_access_challenges_and_the_attempt_limit(self):
        self.assertTrue(can_retry('FAILED', 1, 3, 'The page did not show one booking code'))
        self.assertFalse(can_retry('FAILED', 3, 3, 'The page did not show one booking code'))
        self.assertFalse(can_retry('FAILED', 1, 3, 'Page is blocked. captcha'))
        self.assertFalse(can_retry('CODE_GENERATED', 0, 3, ''))

    def test_verified_code_expires_at_the_stored_kickoff(self):
        previous = db.DB
        temp = tempfile.TemporaryDirectory()
        db.DB = Path(temp.name) / 'sports.sqlite3'
        try:
            db.initialize()
            with db.connect() as connection:
                connection.execute(
                    """INSERT INTO sports_ai_accumulator_tickets
                       (id,family_id,version,signature,tier,sport_mode,status,leg_count,probability_basis,explanation,parameters_json,created_at)
                       VALUES('ticket-1','ticket-1',1,'sig',150,'football','qualified',1,'independent','Stored test ticket','{}','2026-11-04T06:00:00+00:00')"""
                )
                connection.execute(
                    """INSERT INTO sports_ai_booking_codes
                       (id,accumulator_ticket_id,code,verification_method,evidence,status,expires_at,verified_at)
                       VALUES('code-1','ticket-1','BK7REAL','displayed','shot.png','verified','2026-11-04T08:00:00+00:00','2026-11-04T06:00:00+00:00')"""
                )
                changed = expire_displayed_codes(connection, datetime(2026, 11, 4, 8, 1, tzinfo=timezone.utc))
                status = connection.execute("SELECT status FROM sports_ai_booking_codes WHERE id='code-1'").fetchone()['status']
            self.assertEqual(changed, 1)
            self.assertEqual(status, 'expired')
        finally:
            db.DB = previous
            temp.cleanup()
