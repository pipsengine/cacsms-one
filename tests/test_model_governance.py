import json
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

import db
from prediction_engine import govern_models, reprice_active_batch, review_degradation


class ModelGovernanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_directory = tempfile.TemporaryDirectory()
        cls.previous_database = db.DB
        db.DB = Path(cls.temp_directory.name) / 'sports-ai.db'
        db.initialize()

    @classmethod
    def tearDownClass(cls):
        db.DB = cls.previous_database
        cls.temp_directory.cleanup()

    def setUp(self):
        self.now = datetime.now(timezone.utc)
        with db.connect() as connection:
            for table in (
                'sports_ai_value_opportunities', 'sports_ai_engine_predictions', 'sports_ai_analysis_runs',
                'sports_ai_feature_snapshots', 'sports_ai_odds_snapshots', 'sports_ai_calibration_results',
                'sports_ai_model_evaluations', 'sports_ai_model_registry', 'sports_ai_training_runs',
                'sports_ai_fixtures',
            ):
                connection.execute(f'DELETE FROM {table}')
            connection.execute("DELETE FROM sports_ai_settings WHERE key IN ('model_governance','value_cycle','engine')")

    def _model(self, connection, sport, version, error, holdout, baseline, selected, selected_loss):
        run_id, model_id = str(uuid.uuid4()), str(uuid.uuid4())
        metrics = {
            'fixtures_scored': 120, 'holdout_fixtures': holdout, 'calibration_error': error,
            'selected_family': selected,
            'family_log_loss': {'baseline': baseline, selected: selected_loss},
        }
        name = 'sports-ai-football-ensemble' if sport == 'football' else 'sports-ai-basketball-ensemble'
        connection.execute(
            """INSERT INTO sports_ai_training_runs
               (id,sport,model_name,version,status,training_window_json,parameters_json,metrics_json,sample_size,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (run_id, sport, name, version, 'validated', '{}', '{}', json.dumps(metrics), 120, self.now.isoformat()),
        )
        connection.execute(
            """INSERT INTO sports_ai_model_registry
               (id,training_run_id,sport,model_name,version,market,status,active,parameters_json,metrics_json,
                feature_schema_json,artifact_ref,calibration_status,created_at)
               VALUES(?,?,?,?,?,'match_outcome','validated',0,?,?,?,?,?,?)""",
            (model_id, run_id, sport, name, version, json.dumps({
                'ensemble_weights': {'baseline': 1}, 'temperature': 1, 'dixon_coles_rho': 0,
            }), json.dumps(metrics), '[]', f'sqlite:{model_id}', 'validated', self.now.isoformat()),
        )
        connection.execute(
            """INSERT INTO sports_ai_calibration_results
               (id,model_id,market,method,sample_size,metrics_json,held_out_start,held_out_end)
               VALUES(?,?,?,?,?,?,?,?)""",
            (str(uuid.uuid4()), model_id, 'match_outcome', 'temperature_scaling', holdout, json.dumps(metrics), None, None),
        )
        return model_id

    def test_holdout_failure_stays_inactive_and_records_the_reason(self):
        with db.connect() as connection:
            model_id = self._model(connection, 'football', '2', 0.41, 4, 1.1, 'baseline', 1.2)
            result = govern_models(connection, 'test', None, self.now)
            active = connection.execute('SELECT active FROM sports_ai_model_registry WHERE id=?', (model_id,)).fetchone()[0]
        decision = next(item for item in result['decisions'] if item['sport'] == 'football')
        self.assertEqual(active, 0)
        self.assertEqual(decision['action'], 'inactive')
        self.assertTrue(any('calibration error' in reason for reason in decision['reasons']))
        self.assertTrue(any('holdout' in reason for reason in decision['reasons']))

    def test_passing_holdout_activates_and_a_later_failure_rolls_back(self):
        with db.connect() as connection:
            model_id = self._model(connection, 'football', '2', 0.04, 101, 1.10, 'gradient_boosting', 1.01)
            activated = govern_models(connection, 'test', None, self.now)
            self.assertEqual(activated['activated'], [model_id])
            connection.execute(
                """INSERT INTO sports_ai_model_evaluations
                   (id,model_id,sport,market,sample_size,brier_score,log_loss,calibration_error,hit_rate,coverage,completeness,metrics_json,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (str(uuid.uuid4()), model_id, 'football', 'HOME', 30, 0.3, 0.8, 0.41, 0.4, None, None, '{}',
                 (self.now + timedelta(minutes=5)).isoformat()),
            )
            review = review_degradation(connection, 'test', None, self.now + timedelta(minutes=5))
            active = connection.execute('SELECT active,status FROM sports_ai_model_registry WHERE id=?', (model_id,)).fetchone()
            again = govern_models(connection, 'test', None, self.now + timedelta(minutes=6))
        self.assertEqual(review['rolled_back'][0]['model_id'], model_id)
        self.assertEqual(active['active'], 0)
        self.assertEqual(active['status'], 'inactive')
        self.assertEqual(next(item for item in again['decisions'] if item['model_id'] == model_id)['action'], 'blocked')

    def test_reprice_qualifies_a_fresh_price_and_ignores_a_stale_one(self):
        with db.connect() as connection:
            model_id = self._model(connection, 'football', '2', 0.04, 40, 1.1, 'gradient_boosting', 0.9)
            connection.execute(
                "UPDATE sports_ai_model_registry SET active=1,status='active',activated_at=? WHERE id=?",
                (self.now.isoformat(), model_id),
            )
            provider = connection.execute('SELECT id FROM sports_ai_provider_registry LIMIT 1').fetchone()[0]
            fresh_id, stale_id = 'fresh-match', 'stale-match'
            for fixture_id, kickoff in ((fresh_id, self.now + timedelta(days=2)), (stale_id, self.now + timedelta(days=3))):
                connection.execute(
                    """INSERT INTO sports_ai_fixtures
                       (id,external_id,source,sport,competition,home,away,starts_at,status,statistics_json,odds_json,observed_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (fixture_id, fixture_id, 'test', 'football', 'League', 'Home FC', 'Away FC', kickoff.isoformat(),
                     'scheduled', '{}', '{}', self.now.isoformat()),
                )
                connection.execute(
                    """INSERT INTO sports_ai_feature_snapshots
                       (id,fixture_id,sport,cutoff_at,features_json,source_refs_json,generated_at,completeness)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (str(uuid.uuid4()), fixture_id, 'football', self.now.isoformat(), json.dumps({
                        'expected_home_goals': 2.4, 'expected_away_goals': 0.4, 'home_score_sd': 1, 'away_score_sd': 1,
                    }), '{}', self.now.isoformat(), 0.8),
                )
            connection.execute(
                """INSERT INTO sports_ai_odds_snapshots
                   (id,fixture_id,provider_id,bookmaker,market,outcome,line,decimal_odds,source_url,observed_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (str(uuid.uuid4()), fresh_id, provider, 'SportyBet', 'h2h', 'home', None, 2.2,
                 'https://www.sportybet.com/ng/sport/football', self.now.isoformat()),
            )
            connection.execute(
                """INSERT INTO sports_ai_odds_snapshots
                   (id,fixture_id,provider_id,bookmaker,market,outcome,line,decimal_odds,source_url,observed_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (str(uuid.uuid4()), stale_id, provider, 'SportyBet', 'h2h', 'home', None, 2.2,
                 'https://www.sportybet.com/ng/sport/football', (self.now - timedelta(hours=3)).isoformat()),
            )
            result = reprice_active_batch(connection, 'test', None, limit=10, now=self.now)
            qualified = connection.execute(
                "SELECT f.id FROM sports_ai_value_opportunities o JOIN sports_ai_engine_predictions p ON p.id=o.prediction_id JOIN sports_ai_fixtures f ON f.id=p.fixture_id WHERE o.status='qualified'",
            ).fetchall()
        self.assertGreaterEqual(result['qualified'], 1)
        self.assertEqual([row['id'] for row in qualified], [fresh_id])
        self.assertNotIn('stale-match', [row['id'] for row in qualified])
