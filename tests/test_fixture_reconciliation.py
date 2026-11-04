import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

import db
from fixture_reconciliation import match_fixture
from integrity_engine import audit_prematch_features
from learning_engine import settle_live_forecasts

NOW = datetime(2026, 11, 4, 18, 0, tzinfo=timezone.utc)


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.previous = db.DB
        self.temp = tempfile.TemporaryDirectory()
        db.DB = Path(self.temp.name) / 'sports.sqlite3'
        db.initialize()

    def tearDown(self):
        db.DB = self.previous
        self.temp.cleanup()

    def test_exact_clock_and_names_link_without_renaming_the_source(self):
        kickoff = NOW - timedelta(hours=2)
        with db.connect() as connection:
            source = self._fixture(connection, 'sportybet-displayed', 'Arsenal', 'Chelsea', kickoff)
            official = self._fixture(connection, 'openfootball', 'Arsenal', 'Chelsea', kickoff + timedelta(minutes=5), final=(1, 0))
            decision = match_fixture(connection, source, NOW)
            home = connection.execute('SELECT home FROM sports_ai_fixtures WHERE id=?', (source,)).fetchone()['home']
        self.assertEqual(decision['status'], 'confirmed')
        self.assertEqual(decision['canonical_fixture_id'], official)
        self.assertEqual(home, 'Arsenal')

    def test_similar_names_are_not_linked(self):
        kickoff = NOW - timedelta(hours=2)
        with db.connect() as connection:
            source = self._fixture(connection, 'sportybet-displayed', 'Man Utd', 'Roma', kickoff)
            self._fixture(connection, 'openfootball', 'Manchester United', 'AS Roma', kickoff)
            decision = match_fixture(connection, source, NOW)
        self.assertEqual(decision['status'], 'unresolved')
        self.assertIsNone(decision['canonical_fixture_id'])

    def test_two_exact_candidates_stay_ambiguous(self):
        kickoff = NOW - timedelta(hours=2)
        with db.connect() as connection:
            source = self._fixture(connection, 'sportybet-displayed', 'Arsenal', 'Chelsea', kickoff)
            self._fixture(connection, 'openfootball', 'Arsenal', 'Chelsea', kickoff + timedelta(minutes=4))
            self._fixture(connection, 'openligadb', 'Arsenal', 'Chelsea', kickoff + timedelta(minutes=6))
            decision = match_fixture(connection, source, NOW)
        self.assertEqual(decision['status'], 'ambiguous')
        self.assertIsNone(decision['canonical_fixture_id'])

    def test_a_confirmed_official_result_settles_the_forecast(self):
        kickoff = NOW - timedelta(hours=3)
        created = kickoff - timedelta(hours=5)
        with db.connect() as connection:
            source = self._fixture(connection, 'sportybet-displayed', 'Arsenal', 'Chelsea', kickoff)
            self._fixture(connection, 'openfootball', 'Arsenal', 'Chelsea', kickoff, final=(2, 0))
            prediction = self._prediction(connection, source, kickoff, created)
            match_fixture(connection, source, NOW)
            summary = settle_live_forecasts(connection, NOW)
            outcome = connection.execute('SELECT settled_outcome FROM sports_ai_engine_predictions WHERE id=?', (prediction,)).fetchone()['settled_outcome']
        self.assertEqual(summary['settled'], 1)
        self.assertEqual(outcome, 'won')

    def test_a_source_settled_after_the_forecast_is_flagged(self):
        kickoff = NOW + timedelta(days=1)
        created = NOW - timedelta(hours=2)
        with db.connect() as connection:
            target = self._fixture(connection, 'openfootball', 'Arsenal', 'Chelsea', kickoff)
            earlier = self._fixture(connection, 'openfootball', 'Everton', 'Leeds', created + timedelta(minutes=30), final=(1, 1))
            connection.execute(
                "UPDATE sports_ai_results SET settled_at=? WHERE fixture_id=?",
                ((created + timedelta(minutes=40)).isoformat(), earlier),
            )
            self._snapshot_prediction(connection, target, kickoff, created, [earlier])
            summary = audit_prematch_features(connection, NOW)
            finding = connection.execute("SELECT finding FROM sports_ai_feature_audits WHERE finding='POST_PREDICTION_FEATURE'").fetchone()
        self.assertEqual(summary['leaked'], 1)
        self.assertIsNotNone(finding)

    def _fixture(self, connection, source, home, away, kickoff, final=None):
        fixture_id = str(uuid.uuid4())
        connection.execute(
            """INSERT INTO sports_ai_fixtures
               (id,external_id,source,sport,competition,home,away,starts_at,status,statistics_json,odds_json,observed_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (fixture_id, fixture_id, source, 'football', 'League', home, away, kickoff.isoformat(), 'scheduled', '{}', '{}', NOW.isoformat()),
        )
        if final:
            connection.execute(
                """INSERT INTO sports_ai_results(id,fixture_id,home_score,away_score,source,status,settled_at)
                   VALUES(?,?,?,?,?,'final',?)""",
                (str(uuid.uuid4()), fixture_id, final[0], final[1], source, (kickoff + timedelta(hours=2)).isoformat()),
            )
        return fixture_id

    def _prediction(self, connection, fixture_id, kickoff, created):
        model_id = self._model(connection)
        prediction_id = str(uuid.uuid4())
        run_id = str(uuid.uuid4())
        connection.execute(
            "INSERT INTO sports_ai_analysis_runs (id,fixture_count,selection_count,ticket_count,status) VALUES(?,0,0,0,'complete')",
            (run_id,),
        )
        connection.execute(
            """INSERT INTO sports_ai_feature_snapshots
               (id,fixture_id,sport,cutoff_at,features_json,source_refs_json,generated_at,completeness,input_fingerprint)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (str(uuid.uuid4()), fixture_id, 'football', kickoff.isoformat(), '{}', '{"fixture_ids":[]}', created.isoformat(), 0.8, 'fp'),
        )
        feature_id = connection.execute('SELECT id FROM sports_ai_feature_snapshots WHERE fixture_id=?', (fixture_id,)).fetchone()['id']
        connection.execute(
            """INSERT INTO sports_ai_engine_predictions
               (id,run_id,fixture_id,feature_id,model_id,market,outcome,probability,settlement_json,explanation_json,created_at,cohort,observation_class)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (prediction_id, run_id, fixture_id, feature_id, model_id, 'HOME', 'Home', 0.6, '{}', '{}', created.isoformat(), 'prospective', 'live_observed'),
        )
        return prediction_id

    def _snapshot_prediction(self, connection, fixture_id, kickoff, created, source_ids):
        model_id = self._model(connection)
        feature_id = str(uuid.uuid4())
        run_id = str(uuid.uuid4())
        connection.execute(
            "INSERT INTO sports_ai_analysis_runs (id,fixture_count,selection_count,ticket_count,status) VALUES(?,0,0,0,'complete')",
            (run_id,),
        )
        connection.execute(
            """INSERT INTO sports_ai_feature_snapshots
               (id,fixture_id,sport,cutoff_at,features_json,source_refs_json,generated_at,completeness,input_fingerprint)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (feature_id, fixture_id, 'football', kickoff.isoformat(), '{}', '{"fixture_ids":' + __import__('json').dumps(source_ids) + '}', created.isoformat(), 0.8, feature_id),
        )
        prediction_id = str(uuid.uuid4())
        connection.execute(
            """INSERT INTO sports_ai_engine_predictions
               (id,run_id,fixture_id,feature_id,model_id,market,outcome,probability,settlement_json,explanation_json,created_at,observation_class)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (prediction_id, run_id, fixture_id, feature_id, model_id, 'HOME', 'Home', 0.6, '{}', '{}', created.isoformat(), 'live_observed'),
        )
        connection.execute(
            """INSERT INTO sports_ai_qualification_decisions
               (id,prediction_id,fixture_id,sport,market,state,reason_code,reason,evidence_json,products_json,
                policy_version,decision_version,fingerprint,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (str(uuid.uuid4()), prediction_id, fixture_id, 'football', 'HOME', 'QUALIFIED', 'QUALIFIED_OK', 'stored', '{}', '["booking"]', 1, 1, 'fp', created.isoformat(), created.isoformat()),
        )

    def _model(self, connection):
        existing = connection.execute('SELECT id FROM sports_ai_model_registry LIMIT 1').fetchone()
        if existing:
            return existing['id']
        run_id, model_id = str(uuid.uuid4()), str(uuid.uuid4())
        connection.execute(
            """INSERT INTO sports_ai_training_runs
               (id,sport,model_name,version,status,training_window_json,parameters_json,metrics_json,sample_size)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (run_id, 'football', 'ensemble', 'v2', 'validated', '{}', '{}', '{}', 10),
        )
        connection.execute(
            """INSERT INTO sports_ai_model_registry
               (id,training_run_id,sport,model_name,version,status,active,parameters_json,metrics_json,lifecycle,calibration_status)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (model_id, run_id, 'football', 'ensemble', 'v2', 'active', 1, '{}', '{"brier_score":0.21,"calibration_error":0.05,"holdout_fixtures":10}', 'active', 'validated'),
        )
        return model_id
