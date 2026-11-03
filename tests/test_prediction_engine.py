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
    execute_prediction_job,
    expected_value,
    league_key,
    market_probability,
    recover_timed_out_jobs,
    run_analysis,
    train_model,
)
from match_analysis import analyze_fixture
from prediction_math import simulate_scores


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
                'sports_ai_value_opportunities','sports_ai_prediction_explanations',
                'sports_ai_engine_predictions','sports_ai_feature_snapshots',
                'sports_ai_calibration_results','sports_ai_validation_results',
                'sports_ai_training_datasets','sports_ai_model_versions',
                'sports_ai_model_registry','sports_ai_training_runs',
                'sports_ai_prediction_jobs','sports_ai_market_mappings',
                'sports_ai_odds_snapshots','sports_ai_analysis_runs',
                'sports_ai_results','sports_ai_fixtures',
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

    def _insert_match(self,connection,external_id,home,away,when,home_score,away_score,competition='Test League'):
        fixture_id=str(uuid.uuid4())
        kickoff=when.isoformat()
        connection.execute(
            """INSERT INTO sports_ai_fixtures
               (id,external_id,source,sport,competition,home,away,starts_at,status,
                statistics_json,odds_json,observed_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (fixture_id,external_id,'history-source','football',competition,
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

    def test_earlier_season_of_the_same_league_is_used(self):
        with db.connect() as connection:
            for index in range(1, 4):
                kickoff = self.now - timedelta(days=index * 20)
                self._insert_match(connection, f'serie-h-{index}', 'Napoli', f'Opponent H{index}', kickoff, 2, 0, 'Italian Serie A 2025/26')
                self._insert_match(connection, f'serie-a-{index}', f'Opponent A{index}', 'Lecce', kickoff - timedelta(hours=5), 0, 1, 'Italian Serie A 2024/25')
            target = {
                'id': 'serie-target', 'sport': 'football', 'competition': 'Italian Serie A 2026/27',
                'home': 'Napoli', 'away': 'Lecce', 'starts_at': self.target,
            }
            built = build_features(connection, target)
        self.assertIsNotNone(built)
        self.assertGreaterEqual(built['features']['home_games'], 3)
        self.assertGreaterEqual(built['features']['away_games'], 3)
        self.assertEqual(league_key('England FA Cup'), 'fa-cup')
        self.assertNotEqual(league_key('England FA Cup'), league_key('English Premier League 2026/27'))

    def test_one_short_club_name_uses_that_league_history(self):
        with db.connect() as connection:
            for index in range(1, 4):
                kickoff = self.now - timedelta(days=40 + index)
                self._insert_match(connection, f'bayern-h-{index}', 'FC Bayern München', f'Opponent H{index}', kickoff, 3, 0, '1. Fußball-Bundesliga 2025/2026')
                self._insert_match(connection, f'mainz-a-{index}', f'Opponent A{index}', '1. FSV Mainz 05', kickoff - timedelta(hours=4), 0, 2, '1. Fußball-Bundesliga 2025/2026')
            built = build_features(connection, {
                'id': 'bayern-short', 'sport': 'football', 'competition': 'Germany Bundesliga',
                'home': 'Bayern Munich', 'away': 'Mainz', 'starts_at': self.target,
            })
        self.assertIsNotNone(built)
        self.assertGreaterEqual(built['features']['home_games'], 3)
        self.assertGreaterEqual(built['features']['away_games'], 3)

    def test_european_fixture_uses_domestic_league_when_competition_has_no_finals(self):
        with db.connect() as connection:
            for index in range(1, 4):
                kickoff = self.now - timedelta(days=30 + index)
                self._insert_match(connection, f'united-{index}', 'Manchester United FC', f'Host {index}', kickoff, 2, 1, 'English Premier League 2025/26')
                self._insert_match(connection, f'roma-{index}', f'Visitor {index}', 'AS Roma', kickoff - timedelta(hours=3), 1, 1, 'Italian Serie A 2025/26')
            built = build_features(connection, {
                'id': 'ucl-short', 'sport': 'football',
                'competition': 'International Clubs UEFA Champions League',
                'home': 'Man Utd', 'away': 'Roma', 'starts_at': self.target,
            })
        self.assertIsNotNone(built)
        self.assertIn('stored league', built['features']['history_basis'])

    def test_match_analysis_uses_stored_results_and_no_invented_odds(self):
        with db.connect() as connection:
            report=analyze_fixture(connection,self.fixture_id,'test-user',lambda *args:None)
        self.assertEqual(report['forecast_level'],'secondary')
        self.assertAlmostEqual(report['forecast']['outcome_probability_sum'],1.0,places=6)
        self.assertGreater(report['home_profile']['home_played'],0)
        self.assertGreater(report['away_profile']['away_played'],0)
        self.assertTrue(report['markets'])
        self.assertTrue(all(market['decimal_odds'] is None and market['odds_status']=='Odds Unavailable' for market in report['markets']))
        self.assertTrue(any('bookmaker' in item.lower() for item in report['missing']))

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

    def test_binary_expected_value_matches_edge_identity(self):
        settlement={'p_win':.55,'p_half_win':0,'p_push':0,'p_half_loss':0,'p_loss':.45}
        self.assertAlmostEqual(expected_value(settlement,2.0),.55*2-1,places=10)

    def test_missing_expected_goals_are_not_invented(self):
        with db.connect() as connection:
            built=build_features(connection,self.fixture)
        self.assertIsNone(built['features']['home_xg_for'])
        self.assertIsNone(built['features']['away_xg_for'])
        self.assertIsNone(built['features']['h2h_avg_goals'])
        self.assertIn('home_xg_for',built['missing'])

    def test_first_half_market_requires_source_data(self):
        features={
            'expected_home_points':112.0,'expected_away_points':106.0,
            'home_score_sd':11.0,'away_score_sd':12.0,'total_sd':(11**2+12**2)**.5,
            'expected_home_h1':None,'expected_away_h1':None,'h1_total_sd':None,
        }
        self.assertIsNone(market_probability('basketball','FIRST_HALF_OVER_110.5',features))
        features.update({'expected_home_h1':56.0,'expected_away_h1':52.0,'h1_total_sd':8.0})
        priced=market_probability('basketball','FIRST_HALF_OVER_110.5',features)
        self.assertAlmostEqual(priced['p_win']+priced['p_loss']+priced['p_push'],1.0,places=10)

    def test_monte_carlo_scores_are_reproducible(self):
        first=simulate_scores(110,104,11,12,draws=200,seed=17)
        second=simulate_scores(110,104,11,12,draws=200,seed=17)
        self.assertEqual(first,second)
        self.assertEqual(len(first),200)

    def test_stale_quotes_are_rejected_and_snapshots_are_reused(self):
        observed=(self.now-timedelta(hours=3)).isoformat()
        with db.connect() as connection:
            connection.execute(
                """UPDATE sports_ai_provider_registry
                   SET enabled=1,terms_reviewed=1,status='operational' WHERE id=?""",
                ('provider-manual-odds',),
            )
            connection.execute(
                """INSERT INTO sports_ai_odds_snapshots
                   (id,fixture_id,provider_id,bookmaker,market,outcome,line,decimal_odds,source_url,observed_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                ('quote-stale',self.fixture_id,'provider-manual-odds','Example Book',
                 'h2h','home',None,2.4,'https://example.test/quote',observed),
            )
            first=run_analysis(connection,'test-user',lambda *args:None)
            second=run_analysis(connection,'test-user',lambda *args:None)
            statuses={row['status'] for row in connection.execute(
                'SELECT status FROM sports_ai_value_opportunities',
            )}
            snapshots=connection.execute(
                'SELECT COUNT(*) FROM sports_ai_feature_snapshots',
            ).fetchone()[0]
            summary=connection.execute(
                'SELECT summary FROM sports_ai_prediction_explanations LIMIT 1',
            ).fetchone()['summary']
        self.assertIn('expired',statuses)
        self.assertNotIn('qualified',statuses)
        self.assertGreater(second['snapshots_reused'],0)
        self.assertEqual(snapshots,1)
        self.assertGreater(first['predictions'],0)
        self.assertIn('expected goals',summary)

    def test_repeated_job_is_idempotent(self):
        with db.connect() as connection:
            first=execute_prediction_job(connection,'test-user',lambda *args:None,'job-1','manual')
            second=execute_prediction_job(connection,'test-user',lambda *args:None,'job-1','manual')
            runs=connection.execute('SELECT COUNT(*) FROM sports_ai_prediction_jobs').fetchone()[0]
        self.assertEqual(first['status'],'complete')
        self.assertEqual(second['status'],'skipped')
        self.assertEqual(second['reason'],'idempotent_replay')
        self.assertEqual(runs,1)

    def test_running_job_past_timeout_is_recovered(self):
        started=(self.now-timedelta(hours=2)).isoformat()
        with db.connect() as connection:
            connection.execute(
                """INSERT INTO sports_ai_prediction_jobs
                   (id,idempotency_key,trigger,status,attempt,started_at,timeout_seconds)
                   VALUES('job-old','old','manual','running',1,?,60)""",
                (started,),
            )
            recovered=recover_timed_out_jobs(connection,self.now)
            status=connection.execute(
                "SELECT status FROM sports_ai_prediction_jobs WHERE id='job-old'",
            ).fetchone()['status']
        self.assertEqual(recovered,1)
        self.assertEqual(status,'timed_out')


    def test_inactive_basketball_model_stays_near_even_teams(self):
        kickoff=self.now+timedelta(days=3)
        with db.connect() as connection:
            home_scores=((100,98),(124,110),(108,121),(116,104))
            away_scores=((118,102),(99,114),(125,111),(107,120))
            for index,(home_score,away_score) in enumerate(home_scores):
                when=self.now-timedelta(days=20-index)
                self._insert_basketball(connection,f'bh-{index}','Home Hoops','Other East',when,home_score,away_score)
            for index,(home_score,away_score) in enumerate(away_scores):
                when=self.now-timedelta(days=16-index)
                self._insert_basketball(connection,f'ba-{index}','Other West','Away Hoops',when,home_score,away_score)
            connection.execute(
                """INSERT INTO sports_ai_fixtures
                   (id,external_id,source,sport,competition,home,away,starts_at,status,statistics_json,odds_json,observed_at)
                   VALUES('bball-target','bball-target','history-source','basketball','NBA Test','Home Hoops','Away Hoops',?,'scheduled','{}','{}',?)""",
                (kickoff.isoformat(), self.now.isoformat()),
            )
            result=run_analysis(connection,'test-user',lambda *args:None,fixture_id='bball-target')
            probability=connection.execute(
                """SELECT probability FROM sports_ai_engine_predictions
                   WHERE fixture_id='bball-target' AND market='MONEYLINE_HOME'
                   ORDER BY created_at DESC LIMIT 1""",
            ).fetchone()['probability']
            edge=connection.execute(
                """SELECT expected_edge FROM sports_ai_value_opportunities
                   WHERE prediction_id IN (
                     SELECT id FROM sports_ai_engine_predictions WHERE fixture_id='bball-target'
                   ) LIMIT 1""",
            ).fetchone()['expected_edge']
        self.assertGreater(result['predictions'],0)
        self.assertGreater(probability,0.15)
        self.assertLess(probability,0.85)
        self.assertIsNone(edge)

    def _insert_basketball(self,connection,external_id,home,away,when,home_score,away_score):
        fixture_id=str(uuid.uuid4())
        connection.execute(
            """INSERT INTO sports_ai_fixtures
               (id,external_id,source,sport,competition,home,away,starts_at,status,statistics_json,odds_json,observed_at)
               VALUES(?,?,?,'basketball','NBA Test',?,?,?,'finished','{}','{}',?)""",
            (fixture_id,external_id,'history-source',home,away,when.isoformat(),self.now.isoformat()),
        )
        connection.execute(
            """INSERT INTO sports_ai_results
               (id,fixture_id,home_score,away_score,source,status,settled_at)
               VALUES(?,?,?,?,?,'final',?)""",
            (str(uuid.uuid4()),fixture_id,home_score,away_score,'verified-results',(when+timedelta(hours=3)).isoformat()),
        )


if __name__=='__main__':
    unittest.main()
