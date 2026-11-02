import os
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))

import db
from prediction_engine import (
    build_features,
    expected_value,
    market_probability,
    run_analysis,
    train_model,
)


class PredictionEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_directory=tempfile.TemporaryDirectory()
        cls.database_path=Path(cls.temp_directory.name)/'sports-ai.db'
        cls.previous_database=db.DB
        db.DB=cls.database_path
        db.initialize()

    @classmethod
    def tearDownClass(cls):
        db.DB=cls.previous_database
        cls.temp_directory.cleanup()

    def setUp(self):
        with db.connect() as connection:
            for table in (
                'sports_ai_value_opportunities','sports_ai_engine_predictions',
                'sports_ai_feature_snapshots','sports_ai_model_registry',
                'sports_ai_calibration_results','sports_ai_training_runs',
                'sports_ai_analysis_runs','sports_ai_results','sports_ai_fixtures',
            ):
                connection.execute(f'DELETE FROM {table}')
            connection.execute("DELETE FROM sports_ai_settings WHERE key='engine'")
        self.now=datetime.now(timezone.utc)
        self.target=(self.now+timedelta(days=10)).isoformat()
        self.fixture={
            'id':'target-fixture','sport':'football','competition':'Test League',
            'home':'Home FC','away':'Away FC','starts_at':self.target,
            'status':'scheduled','source':'test-source',
        }
        with db.connect() as connection:
            self._history(connection)

    def _insert_match(self,connection,external_id,home,away,when,home_score,away_score):
        fixture_id=str(uuid.uuid4())
        kickoff=when.isoformat()
        connection.execute(
            """INSERT INTO sports_ai_fixtures
               (id,external_id,source,sport,competition,home,away,starts_at,status,
                statistics_json,odds_json,observed_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (fixture_id,external_id,'history-source','football','Test League',
             home,away,kickoff,'finished','{}','{}',self.now.isoformat()),
        )
        connection.execute(
            """INSERT INTO sports_ai_results
               (id,fixture_id,home_score,away_score,source,status,settled_at)
               VALUES(?,?,?,?,?,'final',?)""",
            (str(uuid.uuid4()),fixture_id,home_score,away_score,'verified-results',
             (when+timedelta(hours=2)).isoformat()),
        )
        return fixture_id

    def _history(self,connection):
        history_ids=[]
        for index in range(1,4):
            kickoff=self.now-timedelta(days=index*3)
            history_ids.append(self._insert_match(
                connection,f'home-{index}','Home FC',f'Opponent H{index}',
                kickoff,2,1,
            ))
            history_ids.append(self._insert_match(
                connection,f'away-{index}',f'Opponent A{index}','Away FC',
                kickoff-timedelta(hours=12),0,1,
            ))
        future_id=self._insert_match(
            connection,'future-match','Home FC','Away FC',
            self.now+timedelta(days=11),0,5,
        )
        target_id=str(uuid.uuid4())
        connection.execute(
            """INSERT INTO sports_ai_fixtures
               (id,external_id,source,sport,competition,home,away,starts_at,status,
                statistics_json,odds_json,observed_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (target_id,'target-fixture','fixture-source','football','Test League',
             'Home FC','Away FC',self.target,'scheduled','{}','{}',self.now.isoformat()),
        )
        self.fixture_id=target_id
        self.history_ids=history_ids
        self.future_id=future_id

    def test_features_use_only_settled_pre_kickoff_records(self):
        with db.connect() as connection:
            built=build_features(connection,self.fixture)
        self.assertIsNotNone(built)
        self.assertEqual(built['features']['home_games'],3)
        self.assertEqual(built['features']['away_games'],3)
        self.assertNotIn(self.future_id,built['fixture_ids'])
        self.assertEqual(built['source_refs'],['verified-results'])

    def test_football_outcomes_and_asian_settlement_are_consistent(self):
        with db.connect() as connection:
            features=build_features(connection,self.fixture)['features']
        probabilities=[
            market_probability('football',market,features,.08)['p_win']
            for market in ('HOME','DRAW','AWAY')
        ]
        self.assertAlmostEqual(sum(probabilities),1.0,places=10)
        asian=market_probability('football','OVER_2.25',features)
        self.assertAlmostEqual(sum(asian.values()),1.0,places=10)
        self.assertGreater(asian['p_half_loss'],0)
        quarter_win=market_probability('football','OVER_2.75',features)
        self.assertGreater(quarter_win['p_half_win'],0)

    def test_basketball_model_uses_scoring_distribution(self):
        features={
            'expected_home_points':112.0,'expected_away_points':106.0,
            'home_score_sd':11.0,'away_score_sd':12.0,'total_sd':(11**2+12**2)**.5,
        }
        home=market_probability('basketball','MONEYLINE_HOME',features)
        away=market_probability('basketball','MONEYLINE_AWAY',features)
        self.assertAlmostEqual(home['p_win']+away['p_win'],1.0,places=10)
        self.assertGreater(home['p_win'],.5)
        total=market_probability('basketball','OVER_218.5',features)
        self.assertAlmostEqual(total['p_win']+total['p_loss'],1.0,places=10)

    def test_expected_value_accounts_for_pushes(self):
        settlement={'p_win':.4,'p_half_win':0,'p_push':.2,'p_half_loss':0,'p_loss':.4}
        self.assertAlmostEqual(expected_value(settlement,2.0),0.0,places=10)

    def test_training_does_not_activate_insufficient_data(self):
        with db.connect() as connection:
            result=train_model(connection,'football')
            model=connection.execute(
                'SELECT active,status FROM sports_ai_model_registry WHERE id=?',
                (result['model_id'],),
            ).fetchone()
        self.assertEqual(result['status'],'insufficient_data')
        self.assertEqual(model['active'],0)

    def test_analysis_persists_unqualified_predictions_until_validation(self):
        with db.connect() as connection:
            result=run_analysis(connection,'test-user',lambda *args:None)
            statuses=connection.execute(
                'SELECT DISTINCT status FROM sports_ai_value_opportunities',
            ).fetchall()
            prediction_count=connection.execute(
                'SELECT COUNT(*) FROM sports_ai_engine_predictions',
            ).fetchone()[0]
        self.assertGreater(prediction_count,0)
        self.assertEqual(result['qualified'],0)
        self.assertTrue(result['model_activation_required'])
        self.assertIn('awaiting_calibration',{row['status'] for row in statuses})


if __name__=='__main__':
    unittest.main()
