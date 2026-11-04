import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

import db
from suitability import (
    assess_market, compare_models, evaluation_report, qualification_block, sync_lifecycles,
)


class SuitabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.previous = db.DB
        db.DB = Path(cls.temp.name) / 'suitability.db'
        db.initialize()

    @classmethod
    def tearDownClass(cls):
        db.DB = cls.previous
        cls.temp.cleanup()

    def test_research_model_cannot_qualify_even_when_rates_exist(self):
        assessment = assess_market(
            'football', 'HOME',
            {'expected_home_goals': 1.4, 'expected_away_goals': 1.1},
            'research', False, 0.8,
        )
        self.assertEqual(assessment['suitable'], 1)
        self.assertEqual(assessment['booking_ready'], 0)
        self.assertIn('research', qualification_block(assessment))

    def test_active_calibrated_market_is_booking_ready(self):
        assessment = assess_market(
            'football', 'DRAW',
            {'expected_home_goals': 1.2, 'expected_away_goals': 0.9, 'home_rest_days': 6, 'away_rest_days': 4},
            'active', True, 0.7,
        )
        self.assertEqual(assessment['booking_ready'], 1)
        self.assertIsNone(qualification_block(assessment))
        self.assertIn('injuries', assessment['missing'])
        self.assertGreater(assessment['rank_score'], 70)

    def test_missing_rates_are_not_suitable(self):
        assessment = assess_market('basketball', 'MONEYLINE_HOME', {}, 'active', True, 0.2)
        self.assertEqual(assessment['suitable'], 0)
        self.assertEqual(assessment['booking_ready'], 0)
        self.assertIn('scoring_rates', assessment['missing'])

    def test_unmodelled_sport_produces_no_probability_claim(self):
        assessment = assess_market('tennis', 'HOME', {}, 'research', False, None)
        self.assertEqual(assessment['rank_score'], 0)
        self.assertIn('No trained model', assessment['reasons'][0])
        with db.connect() as connection:
            report = evaluation_report(connection, 'tennis')
            compared = compare_models(connection, 'volleyball')
        self.assertEqual(report['models'], [])
        self.assertEqual(compared['models'], [])

    def test_lifecycle_sync_maps_registry_status(self):
        with db.connect() as connection:
            training = connection.execute('SELECT id FROM sports_ai_training_runs LIMIT 1').fetchone()
            if not training:
                connection.execute(
                    """INSERT INTO sports_ai_training_runs
                       (id,sport,model_name,version,status,training_window_json,parameters_json,metrics_json,sample_size)
                       VALUES('train-1','football','test','1','validated','{}','{}','{}',0)""",
                )
                training_id = 'train-1'
            else:
                training_id = training['id']
            connection.execute(
                """INSERT INTO sports_ai_model_registry
                   (id,training_run_id,sport,model_name,version,status,active,parameters_json,metrics_json)
                   VALUES('model-research',?,'football','test','9','unvalidated',0,'{}','{}')""",
                (training_id,),
            )
            connection.execute(
                """INSERT INTO sports_ai_model_registry
                   (id,training_run_id,sport,model_name,version,status,active,parameters_json,metrics_json)
                   VALUES('model-live',?,'basketball','test','9','active',1,'{}','{}')""",
                (training_id,),
            )
            sync_lifecycles(connection)
            research = connection.execute(
                "SELECT lifecycle FROM sports_ai_model_registry WHERE id='model-research'",
            ).fetchone()['lifecycle']
            active = connection.execute(
                "SELECT lifecycle FROM sports_ai_model_registry WHERE id='model-live'",
            ).fetchone()['lifecycle']
        self.assertEqual(research, 'research')
        self.assertEqual(active, 'active')
