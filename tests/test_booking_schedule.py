import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

from booking_schedule import phase_at, publication_due, publication_open, next_publication
from sportybet_booking import _align_selection_to_offered, _same_line, can_retry, expire_displayed_codes
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

    def test_testing_period_requests_a_code_every_fifteen_minutes(self):
        wat = timezone(timedelta(hours=1))
        moment = datetime(2026, 11, 7, 10, 41, tzinfo=wat)
        self.assertEqual(phase_at(moment), 'event')
        self.assertTrue(publication_due(moment, None))
        self.assertFalse(publication_due(moment, moment - timedelta(minutes=10)))
        self.assertTrue(publication_due(moment, moment - timedelta(minutes=15)))
        due = next_publication(moment, moment - timedelta(minutes=10))
        self.assertEqual(due, '2026-11-07T10:46+01:00')

    def test_a_page_line_matches_the_stored_line_by_number(self):
        self.assertTrue(_same_line('4.50', '4.5'))
        self.assertTrue(_same_line('Over 4.5', '4.5'))
        self.assertFalse(_same_line('3', '4.5'))

    def test_a_stored_price_for_a_line_the_page_shows_replaces_the_missing_line(self):
        previous = db.DB
        temp = tempfile.TemporaryDirectory()
        db.DB = Path(temp.name) / 'sports.sqlite3'
        try:
            db.initialize()
            with db.connect() as connection:
                connection.execute(
                    """INSERT INTO sports_ai_fixtures
                       (id,external_id,source,sport,competition,home,away,starts_at,status,statistics_json,odds_json,observed_at)
                       VALUES('fix','fix','test','football','League','Osnabruck','Kaiserslautern','2026-11-07T15:00:00+00:00','scheduled','{}','{}','2026-11-07T09:00:00+00:00')"""
                )
                connection.execute(
                    """INSERT INTO sports_ai_provider_registry
                       (id,provider_key,display_name,sport,adapter,enabled,terms_reviewed,status)
                       VALUES('provider','line-test','SportyBet','football','authorized_json',1,1,'operational')"""
                )
                connection.execute(
                    """INSERT INTO sports_ai_odds_snapshots
                       (id,fixture_id,provider_id,bookmaker,market,outcome,line,decimal_odds,source_url,observed_at)
                       VALUES('odds','fix','provider','SportyBet','UNDER_3.5','Under 3.5',3.5,1.72,'https://www.sportybet.com/ng/sport/football','2026-11-07T09:16:00+00:00')"""
                )
            aligned = _align_selection_to_offered(
                {'fixture_id': 'fix', 'market': 'UNDER_4.5', 'selection': 'Under 4.5', 'decimal_odds': 1.9},
                [3, 3.5],
            )
        finally:
            db.DB = previous
            temp.cleanup()
        self.assertEqual(aligned['market'], 'UNDER_3.5')
        self.assertEqual(aligned['decimal_odds'], 1.72)

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
