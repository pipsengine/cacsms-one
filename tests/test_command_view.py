import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

from booking_schedule import WAT, opportunity_week
from command_view import _opportunities, ticket_confidence, ticket_probability
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


if __name__ == '__main__':
    unittest.main()
