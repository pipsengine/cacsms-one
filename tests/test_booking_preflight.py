import os
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

import db
from main import _assert_production_configuration
from sportybet_booking import classify_booking_failure, examine_ready_tickets

OUTSIDE = datetime(2026, 11, 4, 12, 30, tzinfo=timezone.utc)


class BookingPreflightTests(unittest.TestCase):
    def setUp(self):
        self.previous = db.DB
        self.temp = tempfile.TemporaryDirectory()
        db.DB = Path(self.temp.name) / 'sports.sqlite3'
        db.initialize()

    def tearDown(self):
        db.DB = self.previous
        self.temp.cleanup()

    def test_a_closed_window_keeps_a_fresh_ticket_and_does_not_open_a_browser(self):
        with db.connect() as connection:
            self._ticket(connection, OUTSIDE - timedelta(minutes=2))
            result = examine_ready_tickets(connection, OUTSIDE)
            status = connection.execute('SELECT status, publication_state FROM sports_ai_accumulator_tickets').fetchone()
            reason = connection.execute('SELECT reason_code FROM sports_ai_booking_diagnostics').fetchone()['reason_code']
        self.assertEqual(result['examined'], 1)
        self.assertEqual(result['ready'], 1)
        self.assertEqual(result['demoted'], 0)
        self.assertFalse(result['opens_browser'])
        self.assertFalse(result['places_wager'])
        self.assertEqual(status['status'], 'qualified')
        self.assertEqual(status['publication_state'], 'READY_FOR_WINDOW')
        self.assertEqual(reason, 'WINDOW_CLOSED')

    def test_qualified_accumulator_is_not_rejected_for_joint_probability_below_70_percent(self):
        from sportybet_booking import next_ticket_id
        with db.connect() as connection:
            self._ticket(connection, OUTSIDE-timedelta(minutes=2))
            connection.execute('UPDATE sports_ai_accumulator_tickets SET joint_probability=0.23')
            self.assertIsNotNone(next_ticket_id(connection,OUTSIDE))
            connection.execute("UPDATE sports_ai_qualification_decisions SET state='SUSPENDED'")
            self.assertIsNone(next_ticket_id(connection,OUTSIDE))

    def test_old_high_probability_status_is_repaired_only_with_current_qualification(self):
        from accumulator_engine import reconcile_high_probability_tickets
        with db.connect() as connection:
            self._ticket(connection,OUTSIDE-timedelta(minutes=2))
            connection.execute('''UPDATE sports_ai_accumulator_tickets SET
                status='unverified_probability',joint_probability=0.23,
                parameters_json='{"coverage":"high_probability"}' ''')
            self.assertEqual(reconcile_high_probability_tickets(connection,OUTSIDE)['qualified'],1)
            self.assertEqual(connection.execute('SELECT status,finalized FROM sports_ai_accumulator_tickets').fetchone()['finalized'],1)
            connection.execute("UPDATE sports_ai_accumulator_tickets SET status='unverified_probability'")
            connection.execute("UPDATE sports_ai_qualification_decisions SET state='SUSPENDED'")
            self.assertEqual(reconcile_high_probability_tickets(connection,OUTSIDE)['held'],1)
            self.assertEqual(connection.execute('SELECT status FROM sports_ai_accumulator_tickets').fetchone()[0],'review_required')

    def test_stopped_preparing_job_is_released(self):
        from sportybet_booking import create_job,ticket_selections,recover_stopped_bookings
        with db.connect() as connection:
            self._ticket(connection,OUTSIDE-timedelta(minutes=2))
            identifier=connection.execute('SELECT id FROM sports_ai_accumulator_tickets').fetchone()[0]
            job=create_job(connection,ticket_selections(connection,identifier))
            connection.execute("UPDATE sports_ai_booking_jobs SET status='PREPARING',updated_at=?",((OUTSIDE-timedelta(days=2)).isoformat(),))
            self.assertEqual(recover_stopped_bookings(connection,OUTSIDE),1)
            self.assertEqual(connection.execute('SELECT status,reason_code FROM sports_ai_booking_jobs').fetchone()['reason_code'],'WORKER_STOPPED')
            self.assertEqual(recover_stopped_bookings(connection,OUTSIDE),0)

    def test_a_review_ticket_returns_when_the_price_and_decision_are_current(self):
        with db.connect() as connection:
            self._ticket(connection, OUTSIDE - timedelta(minutes=2))
            connection.execute("UPDATE sports_ai_accumulator_tickets SET status='review_required', publication_state=NULL")
            result = examine_ready_tickets(connection, OUTSIDE)
            status = connection.execute('SELECT status, publication_state FROM sports_ai_accumulator_tickets').fetchone()
        self.assertEqual(result['restored'], 1)
        self.assertEqual(result['demoted'], 0)
        self.assertFalse(result['opens_browser'])
        self.assertFalse(result['places_wager'])
        self.assertEqual(status['status'], 'qualified')
        self.assertEqual(status['publication_state'], 'READY_FOR_WINDOW')

    def test_a_fresh_price_change_does_not_block_the_same_ticket_selection(self):
        with db.connect() as connection:
            self._ticket(connection, OUTSIDE - timedelta(minutes=2))
            connection.execute("UPDATE sports_ai_odds_snapshots SET decimal_odds=4.25")
            result = examine_ready_tickets(connection, OUTSIDE)
            status = connection.execute('SELECT status, publication_state FROM sports_ai_accumulator_tickets').fetchone()
        self.assertEqual(result['ready'], 1)
        self.assertEqual(result['demoted'], 0)
        self.assertEqual(status['status'], 'qualified')
        self.assertEqual(status['publication_state'], 'READY_FOR_WINDOW')

    def test_a_review_ticket_with_a_stale_price_stays_out(self):
        with db.connect() as connection:
            self._ticket(connection, OUTSIDE - timedelta(minutes=40))
            connection.execute("UPDATE sports_ai_accumulator_tickets SET status='review_required', publication_state=NULL")
            result = examine_ready_tickets(connection, OUTSIDE)
            status = connection.execute('SELECT status FROM sports_ai_accumulator_tickets').fetchone()
        self.assertEqual(result['restored'], 0)
        self.assertEqual(status['status'], 'review_required')

    def test_a_stale_price_is_removed_before_the_window(self):
        with db.connect() as connection:
            self._ticket(connection, OUTSIDE - timedelta(minutes=40))
            result = examine_ready_tickets(connection, OUTSIDE)
            status = connection.execute('SELECT status, publication_state FROM sports_ai_accumulator_tickets').fetchone()
            reason = connection.execute('SELECT reason_code FROM sports_ai_booking_diagnostics').fetchone()['reason_code']
        self.assertEqual(result['demoted'], 1)
        self.assertEqual(status['status'], 'review_required')
        self.assertIsNone(status['publication_state'])
        self.assertEqual(reason, 'ODDS_STALE')
        self.assertEqual(classify_booking_failure('The page asked for a challenge (captcha).'), 'ACCESS_CHALLENGE')

    def test_production_startup_fails_closed(self):
        saved = {key: os.environ.get(key) for key in ('SPORTS_PRODUCTION', 'SPORTS_LOCAL_PREVIEW', 'SPORTS_ADMIN_API_KEY', 'SPORTS_DEV_API_KEY')}
        try:
            os.environ['SPORTS_PRODUCTION'] = '1'
            os.environ['SPORTS_LOCAL_PREVIEW'] = '1'
            with self.assertRaises(RuntimeError):
                _assert_production_configuration()
            os.environ['SPORTS_LOCAL_PREVIEW'] = '0'
            os.environ.pop('SPORTS_ADMIN_API_KEY', None)
            os.environ.pop('SPORTS_DEV_API_KEY', None)
            with self.assertRaises(RuntimeError):
                _assert_production_configuration()
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def _ticket(self, connection, observed):
        fixture_id, ticket_id = str(uuid.uuid4()), str(uuid.uuid4())
        kickoff = OUTSIDE + timedelta(days=1)
        connection.execute(
            """INSERT INTO sports_ai_fixtures
               (id,external_id,source,sport,competition,home,away,starts_at,status,statistics_json,odds_json,observed_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (fixture_id, fixture_id, 'test', 'football', 'League', 'Home', 'Away', kickoff.isoformat(), 'scheduled', '{}', '{}', observed.isoformat()),
        )
        provider_id = str(uuid.uuid4())
        connection.execute(
            """INSERT INTO sports_ai_provider_registry
               (id,provider_key,display_name,sport,adapter,enabled,terms_reviewed,status)
               VALUES(?,?,?,?,?,1,1,'operational')""",
            (provider_id, f'provider-{fixture_id[:8]}', 'SportyBet', 'football', 'authorized_json'),
        )
        connection.execute(
            """INSERT INTO sports_ai_odds_snapshots
               (id,fixture_id,provider_id,bookmaker,market,outcome,decimal_odds,source_url,observed_at)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (str(uuid.uuid4()), fixture_id, provider_id, 'SportyBet', 'h2h', 'draw', 3.67, 'https://www.sportybet.com/ng/sport/football', observed.isoformat()),
        )
        connection.execute(
            """INSERT INTO sports_ai_qualification_decisions
               (id,prediction_id,fixture_id,sport,market,state,reason_code,reason,evidence_json,products_json,
                policy_version,decision_version,fingerprint,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (str(uuid.uuid4()), str(uuid.uuid4()), fixture_id, 'football', 'DRAW', 'QUALIFIED', 'QUALIFIED_OK', 'stored', '{}', '["booking"]', 1, 1, 'fp', observed.isoformat(), observed.isoformat()),
        )
        run_id = str(uuid.uuid4())
        connection.execute(
            """INSERT INTO sports_ai_accumulator_runs
               (id,config_id,sport_mode,status,trigger,idempotency_key,parameters_json,actor,started_at)
               VALUES(?,'accumulator-default','football','complete','test',?,'{}','test',?)""",
            (run_id, run_id, observed.isoformat()),
        )
        connection.execute(
            """INSERT INTO sports_ai_accumulator_tickets
               (id,run_id,family_id,version,signature,tier,sport_mode,status,leg_count,probability_basis,
                explanation,parameters_json,publication_state,created_at)
               VALUES(?,?,?,1,?,300,'football','qualified',1,'independent','test','{}','READY_FOR_WINDOW',?)""",
            (ticket_id, run_id, ticket_id, ticket_id, observed.isoformat()),
        )
        connection.execute(
            """INSERT INTO sports_ai_accumulator_legs
               (id,ticket_id,opportunity_id,fixture_id,sport,competition,home,away,starts_at,market,selection,
                decimal_odds,bookmaker,odds_observed_at,quote_market,quote_outcome,model_id,model_version,settlement_kind,qualification_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                str(uuid.uuid4()), ticket_id, str(uuid.uuid4()), fixture_id, 'football', 'League', 'Home', 'Away',
                kickoff.isoformat(), 'DRAW', 'Draw', 3.67, 'SportyBet', observed.isoformat(), 'h2h', 'draw', 'model', 'v2', 'binary', '{}',
            ),
        )
