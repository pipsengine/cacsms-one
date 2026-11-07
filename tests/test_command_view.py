import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

from booking_schedule import WAT, opportunity_week
from command_view import _opportunities, _sure_odds, readable_league, ticket_confidence, ticket_probability
import db


class TicketProbabilityTests(unittest.TestCase):
    def test_independent_joint_probability_is_reported(self):
        self.assertEqual(ticket_probability('independent', 0.425), 42.5)

    def test_correlated_outcomes_do_not_receive_a_probability(self):
        self.assertIsNone(ticket_probability('unverified', 0.425))
        self.assertIsNone(ticket_probability('unverified', None))
        self.assertIsNone(ticket_probability('independent', None))

    def test_confidence_uses_only_a_complete_stored_set(self):
        self.assertEqual(ticket_confidence([0.91, 0.74], 2), 74.0)
        self.assertIsNone(ticket_confidence([0.91, None], 2))
        self.assertIsNone(ticket_confidence([0.91], 2))


class OpportunityWeekTests(unittest.TestCase):
    def test_friday_week_ends_at_monday_after_sunday(self):
        friday = datetime(2026, 11, 6, 10, 8, tzinfo=WAT)
        monday, end = opportunity_week(friday)
        self.assertEqual(monday.isoformat(), '2026-11-02T00:00:00+01:00')
        self.assertEqual(end.isoformat(), '2026-11-09T00:00:00+01:00')

    def test_live_board_keeps_sunday_and_drops_the_next_week(self):
        previous = db.DB
        temp = tempfile.TemporaryDirectory()
        db.DB = Path(temp.name) / 'sports.sqlite3'
        try:
            db.initialize()
            now = datetime(2026, 11, 6, 9, 8, tzinfo=timezone.utc)
            sunday = '2026-11-08T21:00:00+00:00'
            monday = '2026-11-08T23:00:00+00:00'
            with db.connect() as connection:
                connection.execute(
                    """INSERT INTO sports_ai_analysis_runs(id,fixture_count,selection_count,ticket_count,status,details_json)
                       VALUES('run',0,0,0,'complete','{}')""",
                )
                connection.execute(
                    """INSERT INTO sports_ai_training_runs
                       (id,sport,model_name,version,status,training_window_json,parameters_json,metrics_json,sample_size)
                       VALUES('train','football','ensemble','test','validated','{}','{}','{}',40)""",
                )
                connection.execute(
                    """INSERT INTO sports_ai_model_registry
                       (id,training_run_id,sport,model_name,version,status,active,parameters_json,metrics_json,calibration_status)
                       VALUES('model','train','football','ensemble','test','active',1,'{}','{}','calibrated')""",
                )
                self._row(connection, 'sun', 'Sunday Side', sunday, 0.91)
                self._row(connection, 'mon', 'Monday Side', monday, 0.97)
                names = [row['fixture'] for row in _opportunities(connection, now, opportunity_week(now)[1])]
        finally:
            db.DB = previous
            temp.cleanup()
        self.assertEqual(names, ['Home vs Sunday Side'])

    def _row(self, connection, fixture_id, away, kickoff, probability):
        prediction_id = str(uuid.uuid4())
        feature_id = f'feature-{fixture_id}'
        connection.execute(
            """INSERT INTO sports_ai_fixtures
               (id,external_id,source,sport,competition,home,away,starts_at,status,statistics_json,odds_json,observed_at)
               VALUES(?,?,?,?,?,?,?,?, 'scheduled','{}','{}',?)""",
            (fixture_id, fixture_id, 'test', 'football', 'League', 'Home', away, kickoff, kickoff),
        )
        connection.execute(
            """INSERT INTO sports_ai_feature_snapshots
               (id,fixture_id,sport,cutoff_at,features_json,source_refs_json,generated_at)
               VALUES(?,?,?,?, '{}','[]',?)""",
            (feature_id, fixture_id, 'football', kickoff, kickoff),
        )
        connection.execute(
            """INSERT INTO sports_ai_engine_predictions
               (id,run_id,fixture_id,model_id,feature_id,market,outcome,probability,settlement_json,explanation_json)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (prediction_id, 'run', fixture_id, 'model', feature_id, 'HOME', 'HOME', probability, '{}', '{}'),
        )
        connection.execute(
            """INSERT INTO sports_ai_qualification_decisions
               (id,prediction_id,fixture_id,sport,market,state,reason_code,reason,evidence_json,products_json,
                policy_version,decision_version,fingerprint,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (str(uuid.uuid4()), prediction_id, fixture_id, 'football', 'HOME', 'QUALIFIED', 'QUALIFIED_OK',
             'stored', '{}', '[]', 1, 1, fixture_id, kickoff, kickoff),
        )


class SureOddsTests(unittest.TestCase):
    def test_today_keeps_prices_from_1_50_through_3_00(self):
        previous = db.DB
        temp = tempfile.TemporaryDirectory()
        db.DB = Path(temp.name) / 'sports.sqlite3'
        try:
            db.initialize()
            start = datetime(2026, 11, 6, 23, 0, tzinfo=timezone.utc)
            end = start + timedelta(days=1)
            with db.connect() as connection:
                connection.execute(
                    """INSERT INTO sports_ai_provider_registry
                       (id,provider_key,display_name,sport,adapter,enabled,terms_reviewed,status)
                       VALUES('provider','sure-test','SportyBet','football','authorized_json',1,1,'operational')"""
                )
                connection.execute(
                    """INSERT INTO sports_ai_analysis_runs(id,fixture_count,selection_count,ticket_count,status,details_json)
                       VALUES('run',0,0,0,'complete','{}')"""
                )
                connection.execute(
                    """INSERT INTO sports_ai_training_runs
                       (id,sport,model_name,version,status,training_window_json,parameters_json,metrics_json,sample_size)
                       VALUES('train','football','ensemble','test','validated','{}','{}','{"calibration_error":0.08}',40)"""
                )
                connection.execute(
                    """INSERT INTO sports_ai_model_registry
                       (id,training_run_id,sport,model_name,version,status,active,parameters_json,metrics_json,calibration_status)
                       VALUES('model','train','football','ensemble','test','active',1,'{}','{"calibration_error":0.08}','calibrated')"""
                )
                self._fixture(connection, 'in-band', 'Side', '2026-11-07T15:00:00+00:00', 2.1)
                self._fixture(connection, 'played', 'Early', '2026-11-07T08:00:00+00:00', 1.9)
                self._fixture(connection, 'dear', 'Other', '2026-11-07T16:00:00+00:00', 4.2)
                self._fixture(connection, 'tomorrow', 'Later', '2026-11-08T15:00:00+00:00', 1.8)
                connection.execute(
                    """INSERT INTO sports_ai_feature_snapshots
                       (id,fixture_id,sport,cutoff_at,features_json,source_refs_json,generated_at)
                       VALUES('feature-in-band','in-band','football','2026-11-07T15:00:00+00:00','{}','[]','2026-11-07T09:00:00+00:00')"""
                )
                connection.execute(
                    """INSERT INTO sports_ai_feature_snapshots
                       (id,fixture_id,sport,cutoff_at,features_json,source_refs_json,generated_at)
                       VALUES('feature-played','played','football','2026-11-07T08:00:00+00:00','{}','[]','2026-11-07T06:00:00+00:00')"""
                )
                connection.execute(
                    """INSERT INTO sports_ai_engine_predictions
                       (id,run_id,fixture_id,model_id,feature_id,market,outcome,probability,settlement_json,explanation_json)
                       VALUES('pred','run','in-band','model','feature-in-band','HOME','HOME',0.81,'{}','{}')"""
                )
                connection.execute(
                    """INSERT INTO sports_ai_engine_predictions
                       (id,run_id,fixture_id,model_id,feature_id,market,outcome,probability,settlement_json,explanation_json)
                       VALUES('pred-played','run','played','model','feature-played','HOME','HOME',0.84,'{}','{}')"""
                )
                connection.execute(
                    """INSERT INTO sports_ai_accumulator_runs
                       (id,config_id,sport_mode,status,trigger,idempotency_key,parameters_json,actor,started_at)
                       VALUES('run-ticket','accumulator-default','football','complete','test','run-ticket','{}','test','2026-11-07T09:00:00+00:00')"""
                )
                connection.execute(
                    """INSERT INTO sports_ai_accumulator_tickets
                       (id,run_id,family_id,version,signature,tier,sport_mode,status,leg_count,probability_basis,explanation,parameters_json,created_at)
                       VALUES('ticket','run-ticket','ticket',1,'sig',1,'football','qualified',1,'independent','test','{}','2026-11-07T09:00:00+00:00')"""
                )
                connection.execute(
                    """INSERT INTO sports_ai_accumulator_legs
                       (id,ticket_id,opportunity_id,fixture_id,sport,competition,home,away,starts_at,market,selection,
                        decimal_odds,bookmaker,odds_observed_at,quote_market,quote_outcome,model_id,model_version,settlement_kind,qualification_json)
                       VALUES('leg','ticket','opp','in-band','football','League','Home','Side','2026-11-07T15:00:00+00:00','HOME','Home',
                               2.1,'SportyBet','2026-11-07T09:00:00+00:00','HOME','Home','model','test','binary','{}')"""
                )
                connection.execute(
                    """INSERT INTO sports_ai_booking_codes
                       (id,accumulator_ticket_id,code,verification_method,evidence,status,verified_at)
                       VALUES('code','ticket','SURE1','displayed','shot.png','verified','2026-11-07T09:10:00+00:00')"""
                )
                rows = _sure_odds(connection, start, end, datetime(2026, 11, 7, 10, 0, tzinfo=timezone.utc))
        finally:
            db.DB = previous
            temp.cleanup()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['fixture'], 'Home vs Side')
        self.assertEqual(rows[0]['competition'], 'League')
        self.assertEqual(rows[0]['selection'], 'Home Win')
        self.assertEqual(rows[0]['odds'], 2.1)
        self.assertEqual(rows[0]['code'], 'SURE1')
        self.assertEqual(rows[0]['confidence'], 92.0)
        self.assertEqual(rows[0]['starts_at'], '2026-11-07T15:00:00+00:00')

    def test_league_names_drop_the_season_and_keep_the_division(self):
        self.assertEqual(readable_league('2. Fußball-Bundesliga 2026/2027'), '2. Bundesliga')
        self.assertEqual(readable_league('1. Fußball-Bundesliga 2026/2027'), 'Bundesliga')
        self.assertEqual(readable_league('Germany Amateur Women Bundesliga'), "Women's Amateur Bundesliga")
        self.assertEqual(readable_league('Portugal U19 Campeonato Nacional'), 'Portugal U19')

    def _fixture(self, connection, fixture_id, away, kickoff, odds):
        connection.execute(
            """INSERT INTO sports_ai_fixtures
               (id,external_id,source,sport,competition,home,away,starts_at,status,statistics_json,odds_json,observed_at)
               VALUES(?,?,?,?,?,?,?,?, 'scheduled','{}','{}',?)""",
            (fixture_id, fixture_id, 'test', 'football', 'League', 'Home', away, kickoff, kickoff),
        )
        connection.execute(
            """INSERT INTO sports_ai_odds_snapshots
               (id,fixture_id,provider_id,bookmaker,market,outcome,decimal_odds,source_url,observed_at)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                f'odds-{fixture_id}', fixture_id, 'provider', 'SportyBet', 'HOME', 'Home', odds,
                'https://www.sportybet.com/ng/sport/football', '2026-11-07T09:00:00+00:00',
            ),
        )


if __name__ == '__main__':
    unittest.main()
