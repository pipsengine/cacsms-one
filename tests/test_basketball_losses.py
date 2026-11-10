import copy
import json
import math
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import db
from basketball_losses import import_evidence, dashboard, metrics, eligibility_reason, conflicting_groups
from basketball_validation import split_rows, validate, promote, probability
from qualification_engine import _apply_limits, default_policy
import test_accumulator_engine as accumulator_fixtures


def record(identifier='actual-1', **changes):
    value={'external_ticket_id':identifier,'source_kind':'user_screenshot','source_reference':'test screenshot',
           'currency':'NGN','stake':100,'total_return':0,'outcome':'lost',
           'selections':[{'home':'Home','away':'Away','sport':'basketball','starts_at':'2026-11-09T18:30:00+00:00',
                          'market':'MONEYLINE_HOME','period':'including_overtime','decimal_odds':4.4,
                          'result':'lost','home_score':81,'away_score':92}]}
    value.update(changes);return value


class BasketballLossTests(unittest.TestCase):
    _analysis=accumulator_fixtures.AccumulatorPersistenceTests._analysis
    _model=accumulator_fixtures.AccumulatorPersistenceTests._model
    _opportunity=accumulator_fixtures.AccumulatorPersistenceTests._opportunity

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.previous=db.DB
        db.DB=Path(self.temp.name)/'losses.sqlite3';db.initialize()
        self.now=datetime(2026,11,3,12,tzinfo=timezone.utc)

    def tearDown(self):
        db.DB=self.previous;self.temp.cleanup()

    def test_actual_cash_deduplicates_bookmaker_ids_and_excludes_unconfirmed_probabilities(self):
        with db.connect() as c:
            self.assertEqual(import_evidence(c,[record()])['inserted'],1)
            self.assertEqual(import_evidence(c,[record()])['duplicates'],1)
            report=dashboard(c)
            self.assertEqual(report['actual_cash'][0]['profit'],-100)
            self.assertEqual(report['actual_cash'][0]['roi'],-1)
            self.assertEqual(report['actual_selections']['scored'],0)
            self.assertIsNone(report['actual_selections']['brier_score'])
            self.assertEqual(c.execute('SELECT COUNT(*) FROM sports_ai_results').fetchone()[0],0)

    def test_import_rejects_conflicting_duplicate_and_immutable_sql_updates(self):
        with db.connect() as c: import_evidence(c,[record()])
        with self.assertRaisesRegex(ValueError,'Conflicting duplicate'):
            with db.connect() as c: import_evidence(c,[record(stake=200)])
        with self.assertRaises(sqlite3.IntegrityError):
            with db.connect() as c: c.execute('UPDATE sports_ai_basketball_bet_evidence SET total_return=440')
        with db.connect() as c: self.assertEqual(c.execute('SELECT stake FROM sports_ai_basketball_bet_evidence').fetchone()[0],100)

    def test_accumulator_return_is_not_allocated_to_legs(self):
        bet=record();bet['selections'].append({**bet['selections'][0],'home':'Another home','away':'Another away'})
        with db.connect() as c:
            import_evidence(c,[bet]);report=dashboard(c)
            self.assertEqual(report['actual_cash'][0]['roi'],-1)
            self.assertIsNone(report['actual_by_market'][0]['roi'])

    def test_score_market_conflict_and_naive_time_are_rejected(self):
        wrong=record();wrong['selections'][0]['result']='won'
        for bet in (wrong,record(placed_at='2026-11-09T12:00:00')):
            with self.assertRaises(ValueError):
                with db.connect() as c: import_evidence(c,[bet])

    def test_binary_metrics_and_weighted_ece_use_original_probabilities(self):
        score=metrics([{'probability':.9,'result':'lost'},{'probability':.6,'result':'won'},
                       {'probability':None,'result':'won'},{'probability':.8,'result':'push'}])
        self.assertAlmostEqual(score['brier_score'],(.81+.16)/2)
        self.assertAlmostEqual(score['calibration_error'],.65)
        self.assertAlmostEqual(score['log_loss'],(-math.log(.1)-math.log(.6))/2)
        self.assertEqual(score['scored'],2)
        self.assertAlmostEqual(score['win_rate'],2/3)

    def test_both_conflicting_recommendations_are_suspended_in_one_snapshot(self):
        def item(market):
            return {'row':{'sport':'basketball','fixture_id':'same','market':market,'home':'H','away':'A'},
                    'result':{'state':'QUALIFIED','rank_score':.8,'products':['booking']}}
        for markets in [('MONEYLINE_HOME','MONEYLINE_AWAY'),('OVER_210.5','UNDER_210.5'),('HOME_SPREAD_-3.5','AWAY_SPREAD_3.5')]:
            batch=[item(m) for m in markets];_apply_limits(batch,default_policy())
            self.assertTrue(all(r['result']['state']=='SUSPENDED' for r in batch))
            self.assertTrue(all(r['result']['reason_code']=='CONTRADICTORY_SELECTION' and not r['result']['products'] for r in batch))
        self.assertEqual(conflicting_groups([item('OVER_210.5'),item('UNDER_220.5')]),[])
        aliases=[item('MONEYLINE_HOME'),item('MONEYLINE_AWAY')]
        aliases[0]['row'].update(fixture_id='source-1',canonical_fixture_id='confirmed-game')
        aliases[1]['row'].update(fixture_id='source-2',canonical_fixture_id='confirmed-game')
        self.assertEqual(len(conflicting_groups(aliases)),1)

    def test_old_models_cannot_acquire_release_from_a_global_calibration_score(self):
        with db.connect() as c:
            self.assertIn('loss-audited',eligibility_reason(c,'missing','Germany Pro A','MONEYLINE_HOME'))
            candidate=validate(c)
            self.assertEqual(candidate['status'],'research')
            with self.assertRaisesRegex(ValueError,'no passing'): promote(c,candidate['candidate_id'])
            self.assertTrue(validate(c)['reused'])

    def test_chronology_keeps_simultaneous_games_together_and_embargoes_late_results(self):
        start=datetime(2026,1,1,tzinfo=timezone.utc)
        rows=[{'fixture_id':str(i),'kickoff_at':(start+timedelta(days=i//2)).isoformat(),
               'settled_at':(start+timedelta(days=i//2,hours=3)).isoformat()} for i in range(100)]
        rows[0]['settled_at']=(start+timedelta(days=100)).isoformat()
        train,cal,hold=split_rows(rows)
        self.assertNotIn('0',[r['fixture_id'] for r in train])
        self.assertLess(max(r['kickoff_at'] for r in train),min(r['kickoff_at'] for r in cal))
        self.assertLess(max(r['kickoff_at'] for r in cal),min(r['kickoff_at'] for r in hold))

    def test_holdout_labels_do_not_change_fitted_artifacts_or_calibration(self):
        import random
        rng=random.Random(47);start=datetime(2024,1,1,tzinfo=timezone.utc);rows=[]
        for i in range(320):
            home=100+rng.random()*15;away=100+rng.random()*15
            rows.append({'fixture_id':str(i),'competition':'Test League','home':'H','away':'A',
                         'kickoff_at':(start+timedelta(days=i)).isoformat(),
                         'settled_at':(start+timedelta(days=i,hours=3)).isoformat(),
                         'home_score':round(home+rng.gauss(0,12)),'away_score':round(away+rng.gauss(0,12)),
                         'features':{'expected_home_points':home,'expected_away_points':away,'home_rest_days':2,
                                     'away_rest_days':2,'home_games_3d':1,'away_games_3d':1,'home_form':.6,'away_form':.5,
                                     'total_sd':17,'home_score_sd':12,'away_score_sd':12}})
        changed=copy.deepcopy(rows)
        for r in changed[224:]: r['home_score'],r['away_score']=r['away_score'],r['home_score']
        with db.connect() as c:
            with patch('basketball_validation._observations',return_value=rows): a=validate(c)
            with patch('basketball_validation._observations',return_value=changed): b=validate(c)
            artifacts=[json.loads(c.execute('SELECT artifact_json FROM sports_ai_basketball_candidates WHERE id=?',(x['candidate_id'],)).fetchone()[0]) for x in (a,b)]
            self.assertEqual(artifacts[0],artifacts[1])
            self.assertNotEqual(a['metrics']['leagues'][0]['markets'][0]['candidate']['brier_score'],b['metrics']['leagues'][0]['markets'][0]['candidate']['brier_score'])
            self.assertEqual(a['status'],'research')  # no invented quote period or historical lines

    def test_candidate_spread_signs_and_integer_line_exclusion(self):
        model={'columns':['intercept'],'coefficients':[5]}
        artifact={'margin_model':model,'total_model':{**model,'coefficients':[220]},'margin_sd':12,'total_sd':18,'temperature':{}}
        self.assertAlmostEqual(probability(artifact,{},'HOME_SPREAD_-3.5')+probability(artifact,{},'AWAY_SPREAD_3.5'),1)
        self.assertAlmostEqual(probability(artifact,{},'OVER_220.5')+probability(artifact,{},'UNDER_220.5'),1)
        self.assertIsNone(probability(artifact,{},'OVER_220'))

    def test_explicit_placement_evidence_reconciles_without_rewriting_original_forecast(self):
        from basketball_losses import reconcile_selection
        with db.connect() as c:
            self.run_id=self._analysis(c);self.basketball_model=self._model(c,'basketball')
            item=self._opportunity(c,sport='basketball',market='MONEYLINE_HOME',odds=4.4,probability=.4)
            fixture=c.execute('SELECT * FROM sports_ai_fixtures WHERE id=?',(item['fixture_id'],)).fetchone()
            prediction=c.execute('SELECT id,probability FROM sports_ai_engine_predictions WHERE fixture_id=?',(item['fixture_id'],)).fetchone()
            bet=record();bet['selections'][0].update(home=fixture['home'],away=fixture['away'],starts_at=fixture['starts_at'])
            import_evidence(c,[bet]);selection=c.execute('SELECT id,snapshot_json FROM sports_ai_basketball_bet_selections').fetchone()
            original=selection['snapshot_json']
            reconcile_selection(c,selection['id'],prediction['id'],(self.now+timedelta(minutes=1)).isoformat(),'Original placed-bet export with prediction reference','test')
            self.assertEqual(dashboard(c)['actual_selections']['scored'],1)
            self.assertEqual(c.execute('SELECT snapshot_json FROM sports_ai_basketball_bet_selections').fetchone()[0],original)
            self.assertEqual(c.execute('SELECT probability FROM sports_ai_engine_predictions WHERE id=?',(prediction['id'],)).fetchone()[0],.4)

    def test_candidate_market_quarantine_removes_eligibility_and_rolls_back_without_deleting_forecasts(self):
        from basketball_losses import quarantine
        with db.connect() as c:
            self.run_id=self._analysis(c);self.basketball_model=self._model(c,'basketball')
            item=self._opportunity(c,sport='basketball',market='MONEYLINE_HOME')
            league=c.execute('SELECT competition FROM sports_ai_fixtures WHERE id=?',(item['fixture_id'],)).fetchone()[0]
            c.execute("INSERT INTO sports_ai_basketball_candidates VALUES('approved-fixture','dataset','recipe','{}','{}','validated',?)",(self.now.isoformat(),))
            c.execute('UPDATE sports_ai_model_registry SET parameters_json=? WHERE id=?',(json.dumps({'basketball_loss_candidate':'approved-fixture'}),self.basketball_model))
            c.execute("INSERT INTO sports_ai_basketball_market_approvals VALUES('approved-fixture',?,'moneyline','including_overtime','validated','{}',?)",(league,self.now.isoformat()))
            self.assertIsNone(eligibility_reason(c,self.basketball_model,league,'MONEYLINE_HOME'))
            self.assertIsNotNone(eligibility_reason(c,self.basketball_model,'Different League','MONEYLINE_HOME'))
            before=c.execute('SELECT COUNT(*) FROM sports_ai_engine_predictions').fetchone()[0]
            quarantine(c,self.basketball_model,league,'moneyline','Reliability breach',{'calibration_error':.3})
            self.assertIsNotNone(eligibility_reason(c,self.basketball_model,league,'MONEYLINE_HOME'))
            self.assertEqual(c.execute('SELECT active FROM sports_ai_model_registry WHERE id=?',(self.basketball_model,)).fetchone()[0],0)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM sports_ai_engine_predictions').fetchone()[0],before)

    def test_unapproved_basketball_cannot_enter_mixed_or_football_proposals(self):
        from accumulator_engine import qualify_candidates,default_config
        candidate=accumulator_fixtures._row(sport='basketball',market='MONEYLINE_HOME',basketball_loss_approved=False)
        for mode in ('mixed','football','basketball'):
            accepted,_=qualify_candidates([candidate],{**default_config(),'sport_mode':mode},self.now)
            self.assertEqual(accepted,[])

    def test_legacy_illustrative_basketball_cannot_supply_ticket_picks(self):
        from engine import evaluate
        self.assertEqual(evaluate({'sport':'basketball','stats':{'home_points':110,'away_points':90},'odds':{'HOME':4.4}}),[])

    def test_selected_simulation_family_has_identical_training_and_serving_winner_distribution(self):
        from prediction_engine import _outcome_probabilities,settle_market
        features={'expected_home_points':110,'expected_away_points':105,'home_score_sd':12,
                  'away_score_sd':10,'total_sd':math.sqrt(244)}
        trained,_=_outcome_probabilities('basketball',features,115,108,0,'monte_carlo',None,None)
        _,home=settle_market('basketball','MONEYLINE_HOME',features,{'ensemble_weights':{'monte_carlo':1}})
        _,away=settle_market('basketball','MONEYLINE_AWAY',features,{'ensemble_weights':{'monte_carlo':1}})
        self.assertAlmostEqual(trained[0],home['p_win']/(1-home['p_push']))
        self.assertAlmostEqual(trained[1],away['p_win']/(1-away['p_push']))
        self.assertAlmostEqual(sum(trained),1)

    def test_promotion_registers_an_audited_version_only_after_persisted_passing_approval(self):
        from basketball_losses import dumps
        score={'scored':60,'brier_score':.15,'log_loss':.4,'calibration_error':.04}
        detail={'candidate':score,'legacy_baseline':{'log_loss':.5},'distinct_holdout_fixtures':60}
        with db.connect() as c:
            c.execute("INSERT INTO sports_ai_basketball_candidates VALUES('passing','data','recipe','{}','{}','validated',?)",(self.now.isoformat(),))
            with self.assertRaises(ValueError):promote(c,'passing')
            c.execute("INSERT INTO sports_ai_basketball_market_approvals VALUES('passing','Fixture League','moneyline','including_overtime','validated',?,?)",(dumps(detail),self.now.isoformat()))
            result=promote(c,'passing')
            self.assertTrue(result['promoted'])
            self.assertEqual(c.execute('SELECT COUNT(*) FROM sports_ai_model_versions WHERE model_id=?',(result['model_id'],)).fetchone()[0],1)
            self.assertFalse(promote(c,'passing')['promoted'])


if __name__=='__main__': unittest.main()
