import json
import math
import sys
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from integrity_support import eligible_everywhere
import db
import high_odds_service as service
from accumulator_engine import _candidate,default_config,monitor_accumulators
from qualification_engine import revalidate,save_policy
import test_accumulator_engine as fixtures
from test_accumulator_engine import _row


class HighOddsServiceTests(unittest.TestCase):
    _analysis=fixtures.AccumulatorPersistenceTests._analysis
    _model=fixtures.AccumulatorPersistenceTests._model
    _opportunity=fixtures.AccumulatorPersistenceTests._opportunity

    def setUp(self):
        eligible_everywhere(self)
        self.temp=tempfile.TemporaryDirectory();self.previous=db.DB
        db.DB=Path(self.temp.name)/'high-odds.sqlite3';db.initialize()
        with db.connect() as _c:
            __import__('market_anchor').set_model_only(_c)  # these tests cover other behaviour than anchoring
        self.now=datetime(2026,11,3,12,tzinfo=timezone.utc)
        with db.connect() as c:
            self.run_id=self._analysis(c)
            self.football_model=self._model(c,'football')
            self.basketball_model=self._model(c,'basketball')
            c.execute("UPDATE sports_ai_model_registry SET lifecycle='active',calibration_status='validated'")
            save_policy(c,{'max_open_qualified':200},'test')

    def tearDown(self):
        db.DB=self.previous;self.temp.cleanup()

    def seed(self,c,count,odds=2.1,probability=.62,sport='football'):
        values=[]
        for index in range(count):
            item=self._opportunity(c,home=f'{sport} home {index}',away=f'{sport} away {index}',
                sport=sport,market='HOME' if sport=='football' else 'MONEYLINE_HOME',
                odds=odds,probability=probability,edge=probability*odds-1)
            c.execute('UPDATE sports_ai_fixtures SET competition=? WHERE id=?',(f'{sport} league {index}',item['fixture_id']))
            if sport=='basketball':
                c.execute("""INSERT OR IGNORE INTO sports_ai_basketball_candidates VALUES('test-audited','fixture-dataset','fixture-recipe','{}','{}','validated',?)""",(self.now.isoformat(),))
                c.execute("UPDATE sports_ai_model_registry SET parameters_json=? WHERE id=?",
                          (json.dumps({'basketball_loss_candidate':'test-audited'}),self.basketball_model))
                c.execute("""INSERT OR IGNORE INTO sports_ai_basketball_market_approvals VALUES('test-audited',?,'moneyline','including_overtime','validated','{}',?)""",
                          (f'{sport} league {index}',self.now.isoformat()))
                c.execute("UPDATE sports_ai_odds_snapshots SET settlement_period='including_overtime' WHERE fixture_id=?",(item['fixture_id'],))
            values.append(item)
        c.execute("UPDATE sports_ai_odds_snapshots SET source_url='https://www.sportybet.com/ng/sport/football'")
        revalidate(c,'test',self.now,limit=1000)
        return values

    def event(self,c):
        return service.enqueue(c,'test.opportunities',{},'test-event',self.now)

    def test_board_excludes_research_models_even_with_a_qualified_decision(self):
        from high_odds_board import _rows
        with db.connect() as c:
            self.seed(c, 1, odds=1.3, probability=.94, sport='basketball')
            with patch('market_registry.holdout_ready', return_value=True):
                self.assertEqual(len(_rows(c, self.now, self.now+timedelta(days=3), self.now)), 1)
                c.execute("UPDATE sports_ai_model_registry SET active=0,lifecycle='research' WHERE sport='basketball'")
                self.assertEqual(_rows(c, self.now, self.now+timedelta(days=3), self.now), [])

    def test_board_excludes_expired_or_suspended_decisions(self):
        from high_odds_board import _rows
        with db.connect() as c:
            self.seed(c, 1, odds=1.3, probability=.94, sport='basketball')
            with patch('market_registry.holdout_ready', return_value=True):
                c.execute("UPDATE sports_ai_qualification_decisions SET state='SUSPENDED'")
                self.assertEqual(_rows(c, self.now, self.now+timedelta(days=3), self.now), [])
                c.execute("UPDATE sports_ai_qualification_decisions SET state='QUALIFIED',expires_at=?",
                          ((self.now-timedelta(minutes=1)).isoformat(),))
                self.assertEqual(_rows(c, self.now, self.now+timedelta(days=3), self.now), [])

    def test_basketball_booking_checks_model_and_price_even_with_relaxed_policy(self):
        from qualification_engine import require_current_qualification
        from sportybet_booking import create_job
        with db.connect() as c:
            self.seed(c, 1, odds=1.3, probability=.94, sport='basketball')
            d = c.execute("SELECT fixture_id,market FROM sports_ai_qualification_decisions WHERE state='QUALIFIED'").fetchone()
            selection = {**dict(d), 'sport':'basketball', 'decimal_odds':1.3}
            require_current_qualification(c, [selection], self.now)
            with self.assertRaises(ValueError):
                require_current_qualification(c, [{**selection, 'decimal_odds':1.2}], self.now)
            c.execute("UPDATE sports_ai_model_registry SET active=0,lifecycle='research' WHERE sport='basketball'")
            with patch('sportybet_booking._now', return_value=self.now):
                for high_odds in (False, True):
                    with self.assertRaisesRegex(ValueError, 'active validated model'):
                        create_job(c, [selection], high_odds=high_odds)

    def test_all_eight_categories_end_to_end_and_no_shared_matches(self):
        with db.connect() as c:
            self.seed(c,150)
            c.execute('UPDATE sports_ai_high_odds_categories SET max_active=2')
            settings=service.config(c)
            event=self.event(c)
            reports=service.generate_event(c,event,self.now)
            self.assertEqual(service.PRIMARY,'2-4.99')
            self.assertEqual(reports[0]['category_id'],'2-4.99')
            self.assertEqual(len(reports),9)
            board=service.dashboard(c,self.now)
            self.assertEqual(board['primary_category'],'2-4.99')
            self.assertTrue(next(item['primary'] for item in board['categories'] if item['id']=='2-4.99'))
            self.assertFalse(next(item['primary'] for item in board['categories'] if item['id']=='10-24.99'))
            self.assertTrue(all(r['created_ids'] for r in reports if r['category_id']!=service.JACKPOT),[(r['category_id'],r['status'],r['candidate_count']) for r in reports])
            self.assertTrue(all(r['candidate_count']>1 for r in reports if r['category_id']!=service.JACKPOT))
            tickets=service.dashboard(c,self.now)['tickets']
            self.assertEqual(len({t['category_id'] for t in tickets}),8)
            fixtures=[l['fixture_id'] for t in tickets for l in t['legs']]
            self.assertEqual(len(fixtures),len(set(fixtures)))
            for ticket in tickets:
                self.assertLessEqual(ticket['combined_odds'],1000)
                self.assertGreater(ticket['estimated_edge'],0)
                self.assertEqual(service.validate_snapshot(c,ticket['ticket_id'],self.now),[])
            self.assertEqual(service.generate_event(c,event,self.now),[])
            self.assertEqual(len(service.dashboard(c,self.now)['tickets']),len(tickets))

    def test_jackpot_generation_remains_disabled_when_database_flag_is_enabled(self):
        with db.connect() as c:
            c.execute("UPDATE sports_ai_high_odds_categories SET enabled=(id='jackpot')")
            report=next(r for r in service.generate_event(c,self.event(c),self.now) if r['category_id']=='jackpot')
            self.assertEqual(report['status'],'disabled')
            self.assertEqual(report['created_ids'],[])
            self.assertEqual(service.queue_registration(c,self.now),0)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM sports_ai_high_odds_tickets').fetchone()[0],0)

    def test_jackpot_stop_survives_daily_rollover(self):
        with db.connect() as c:
            c.execute("UPDATE sports_ai_high_odds_categories SET enabled=(id='jackpot')")
            for offset in (0,1):
                moment=self.now+timedelta(days=offset)
                event=service.enqueue(c,'test.day',{},f'disabled-day-{offset}',moment)
                report=next(r for r in service.generate_event(c,event,moment) if r['category_id']=='jackpot')
                self.assertEqual(report['status'],'disabled')
                self.assertEqual(report['created_ids'],[])

    def test_jackpot_cannot_be_reenabled_in_settings(self):
        with db.connect() as c:
            with self.assertRaisesRegex(ValueError, 'disabled by the operator'):
                service.save_settings(c, {}, [{'id':'jackpot','enabled':True}], 'operator')
            self.assertFalse(next(row for row in service.categories(c) if row['id']=='jackpot')['enabled'])

    def test_jackpot_migration_preserves_existing_ticket_references(self):
        with db.connect() as c:
            # Emulate an existing database before migration 042, including a stored ticket.
            c.execute('DROP TABLE sports_ai_high_odds_categories')
            original=(Path(__file__).resolve().parents[1]/'migrations/040_high_odds_optimizer.sql').read_text(encoding='utf-8')
            for statement in original.split(';')[:2]:c.execute(statement)
            c.execute("DELETE FROM sports_ai_schema_migrations WHERE name='042_high_odds_jackpot.sql'")
            self.seed(c,4)
            self.assertTrue(service.generate_event(c,self.event(c),self.now))
            before=[tuple(r) for r in c.execute('SELECT ticket_id,category_id,snapshot_hash FROM sports_ai_high_odds_tickets')]
        db.initialize()
        with db.connect() as c:
            self.assertEqual(before,[tuple(r) for r in c.execute('SELECT ticket_id,category_id,snapshot_hash FROM sports_ai_high_odds_tickets')])
            self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[])
            self.assertEqual(len(service.categories(c)),9)
            with self.assertRaises(Exception):
                c.execute("UPDATE sports_ai_high_odds_categories SET max_odds=2000 WHERE id='500-1000'")

    def test_registration_is_idempotent_and_has_snapshot_audit(self):
        with db.connect() as c:
            self.seed(c,4)
            reports=service.generate_event(c,self.event(c),self.now)
            identifier=next(i for r in reports for i in r['created_ids'])
            with patch('sportybet_booking._now', return_value=self.now):  # the booking gate checks kickoff against this clock
                service.queue_registration(c,self.now)
                jobs=c.execute('SELECT COUNT(*) FROM sports_ai_booking_jobs').fetchone()[0]
                service.queue_registration(c,self.now)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM sports_ai_booking_jobs').fetchone()[0],jobs)
            row=c.execute('SELECT * FROM sports_ai_high_odds_tickets WHERE ticket_id=?',(identifier,)).fetchone()
            self.assertEqual(service.digest(json.loads(row['snapshot_json'])),row['snapshot_hash'])
            self.assertEqual(row['registration_state'],'queued')
            self.assertGreater(c.execute('SELECT COUNT(*) FROM sports_ai_high_odds_audit WHERE ticket_id=?',(identifier,)).fetchone()[0],1)

    def test_displayed_code_deduplication_and_in_play_match_reservation(self):
        from sportybet_booking import _store_displayed_code
        from ticket_diversity import unallocated
        with db.connect() as c:
            self.seed(c,10)
            ids=[i for r in service.generate_event(c,self.event(c),self.now) for i in r['created_ids']]
            self.assertGreaterEqual(len(ids),2)
            with patch.object(service,'now_utc',side_effect=lambda now=None:now or self.now):
                _store_displayed_code(c,ids[0],'TESTONLY1','isolated test-page evidence')
                _store_displayed_code(c,ids[0],'TESTONLY1','isolated test-page evidence')
                with self.assertRaises(ValueError):_store_displayed_code(c,ids[1],'TESTONLY1','isolated test-page evidence')
            self.assertEqual(c.execute('SELECT COUNT(*) FROM sports_ai_booking_codes').fetchone()[0],1)
            service.registration_result(c,ids[0],{'status':'CODE_GENERATED'},self.now)
            self.assertEqual(c.execute('SELECT registration_state FROM sports_ai_high_odds_tickets WHERE ticket_id=?',(ids[0],)).fetchone()[0],'registered')
            legs=json.loads(c.execute('SELECT snapshot_json FROM sports_ai_high_odds_tickets WHERE ticket_id=?',(ids[0],)).fetchone()[0])['legs']
            c.execute("UPDATE sports_ai_accumulator_tickets SET status='expired' WHERE id=?",(ids[0],))
            self.assertEqual(unallocated(c,legs,self.now),[])

    def test_final_displayed_price_change_is_blocked_before_code_request(self):
        from sportybet_booking import ticket_selections
        with db.connect() as c:
            self.seed(c,2)
            identifier=next(i for r in service.generate_event(c,self.event(c),self.now) for i in r['created_ids'])
            selections=ticket_selections(c,identifier)
        shown=[{'fixture_id':l['fixture_id'],'market':l['market'],'page_odds':l['decimal_odds']} for l in selections]
        with patch.object(service,'now_utc',side_effect=lambda now=None:now or self.now):
            service.check_displayed_prices(identifier,selections,shown)
            shown[0]['page_odds']=1000
            with self.assertRaises(ValueError):service.check_displayed_prices(identifier,selections,shown)

    def test_displayed_price_drift_creates_requalified_immutable_replacement(self):
        from sportybet_booking import ticket_selections
        with db.connect() as c:
            self.seed(c,2)
            identifier=next(i for r in service.generate_event(c,self.event(c),self.now) for i in r['created_ids'])
            old_snapshot=c.execute('SELECT snapshot_json FROM sports_ai_high_odds_tickets WHERE ticket_id=?',(identifier,)).fetchone()[0]
            selections=ticket_selections(c,identifier)
            shown=[{**leg,'page_odds':2.15} for leg in selections]
            replacement=service.requalify_displayed_ticket(c,identifier,shown,self.now)
            self.assertNotEqual(replacement,identifier)
            self.assertEqual(c.execute('SELECT snapshot_json FROM sports_ai_high_odds_tickets WHERE ticket_id=?',(identifier,)).fetchone()[0],old_snapshot)
            self.assertEqual(c.execute('SELECT status FROM sports_ai_accumulator_tickets WHERE id=?',(identifier,)).fetchone()[0],'expired')
            self.assertEqual(service.validate_snapshot(c,replacement,self.now),[])
            self.assertAlmostEqual(c.execute('SELECT combined_odds FROM sports_ai_accumulator_tickets WHERE id=?',(replacement,)).fetchone()[0],2.15**2)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM sports_ai_booking_codes').fetchone()[0],0)

    def test_repricing_rejects_changed_selection_and_out_of_band_prices(self):
        from sportybet_booking import ticket_selections
        with db.connect() as c:
            self.seed(c,2)
            identifier=next(i for r in service.generate_event(c,self.event(c),self.now) for i in r['created_ids'])
            shown=[{**leg,'page_odds':1000} for leg in ticket_selections(c,identifier)]
            with self.assertRaisesRegex(ValueError,'category limits'):
                service.requalify_displayed_ticket(c,identifier,shown,self.now)
            shown=[{**leg,'page_odds':2.15,'selection':'DIFFERENT'} for leg in ticket_selections(c,identifier)]
            with self.assertRaisesRegex(ValueError,'selection changed'):
                service.requalify_displayed_ticket(c,identifier,shown,self.now)
            self.assertEqual(c.execute('SELECT status FROM sports_ai_accumulator_tickets WHERE id=?',(identifier,)).fetchone()[0],'qualified')

    def test_quote_linker_includes_calibrated_totals(self):
        from market_registry import link_stored_quotes
        with db.connect() as c:
            self._opportunity(c,market='OVER_1.5',probability=.8,odds=1.4)
            with patch('market_registry.active_keys',return_value={('football','OVER_1.5')}), patch('sportybet_research._assess_value',return_value=('qualified',.12)) as assess:
                self.assertEqual(link_stored_quotes(c,self.now)['qualified'],1)
                self.assertEqual(assess.call_args.args[1]['canonical_market'],'OVER_1.5')

    def test_retries_stop_at_limit_and_returned_strings_are_not_verified_codes(self):
        with db.connect() as c:
            self.seed(c,2)
            identifier=next(i for r in service.generate_event(c,self.event(c),self.now) for i in r['created_ids'])
            for attempt in range(3):
                service.registration_result(c,identifier,{'status':'CODE_GENERATED','code':'TEST-ONLY'},self.now)
            row=c.execute('SELECT registration_state,attempts FROM sports_ai_high_odds_tickets WHERE ticket_id=?',(identifier,)).fetchone()
            self.assertEqual(tuple(row),('failed',3))
            self.assertEqual(c.execute('SELECT COUNT(*) FROM sports_ai_booking_codes').fetchone()[0],0)

    def test_price_change_invalidates_snapshot_without_changing_its_legs(self):
        with db.connect() as _c:
            __import__('booking_policy').save(_c, __import__('booking_policy').STRICT)  # these tests cover the strict gates
        with db.connect() as c:
            self.seed(c,2)
            identifier=next(i for r in service.generate_event(c,self.event(c),self.now) for i in r['created_ids'])
            original=c.execute('SELECT snapshot_json FROM sports_ai_high_odds_tickets WHERE ticket_id=?',(identifier,)).fetchone()[0]
            c.execute('UPDATE sports_ai_odds_snapshots SET decimal_odds=2.2')
            self.assertTrue(service.validate_snapshot(c,identifier,self.now))
            service.monitor(c,self.now)
            self.assertEqual(c.execute('SELECT status FROM sports_ai_accumulator_tickets WHERE id=?',(identifier,)).fetchone()[0],'expired')
            self.assertEqual(c.execute('SELECT snapshot_json FROM sports_ai_high_odds_tickets WHERE ticket_id=?',(identifier,)).fetchone()[0],original)

    def test_inactive_uncalibrated_stale_and_partial_candidates_are_rejected(self):
        with db.connect() as c:
            self.seed(c,4)
            self.assertEqual(len(service.eligible(c,self.now)[0]),4)
            c.execute("UPDATE sports_ai_model_registry SET active=0")
            self.assertEqual(service.eligible(c,self.now)[0],[])
            c.execute("UPDATE sports_ai_model_registry SET active=1,calibration_status='uncalibrated'")
            self.assertEqual(service.eligible(c,self.now)[0],[])
            c.execute("UPDATE sports_ai_model_registry SET calibration_status='validated'")
            self.assertEqual(service.eligible(c,self.now+timedelta(minutes=20))[0],[])
            c.execute("UPDATE sports_ai_engine_predictions SET settlement_json=?",(json.dumps({'p_win':.62,'p_loss':.28,'p_push':.1}),))
            self.assertEqual(service.eligible(c,self.now)[0],[])

    def test_lower_probability_qualified_opportunities_remain_live(self):
        with db.connect() as c:
            self.seed(c,3)
            live=service.live_opportunities(c,self.now)
            self.assertEqual(len(live['items']),3)
            self.assertTrue(all(item['probability']==62 for item in live['items']))
            self.assertEqual(service.live_opportunities(c,self.now,sport='tennis')['items'],[])

    def test_no_data_reports_each_category_independently(self):
        with db.connect() as c:
            reports=service.generate_event(c,self.event(c),self.now)
            self.assertEqual(len(reports),9)
            self.assertTrue(all(r['status']==('disabled' if r['category_id']=='jackpot' else 'insufficient_eligible_data') for r in reports))
            self.assertEqual(c.execute('SELECT COUNT(*) FROM sports_ai_accumulator_tickets').fetchone()[0],0)

    def test_category_failure_does_not_stop_the_other_seven(self):
        with db.connect() as c:
            self.seed(c,4)
            optimizer=service.optimize
            def search(pool,category,settings):
                if category['id']==service.PRIMARY:raise RuntimeError('Isolated category failure')
                return optimizer(pool,category,settings)
            with patch.object(service,'optimize',side_effect=search):
                reports=service.generate_event(c,self.event(c),self.now)
            self.assertEqual(len(reports),9)
            self.assertEqual(reports[0]['status'],'failed')
            self.assertTrue(any(r['status']=='created' for r in reports[1:]))

    def test_another_enabled_sport_uses_its_own_activated_model(self):
        with db.connect() as c:
            model=self._model(c,'tennis')
            self.seed(c,3,sport='tennis')
            c.execute("UPDATE sports_ai_sports SET prediction_status='modelled' WHERE code='tennis'")
            c.execute("UPDATE sports_ai_model_registry SET lifecycle='active',calibration_status='validated' WHERE id=?",(model,))
            c.execute('UPDATE sports_ai_engine_predictions SET model_id=?',(model,))
            revalidate(c,'test',self.now)
            pool,_,_=service.eligible(c,self.now)
            self.assertEqual(len(pool),3)
            self.assertTrue(all(l['sport']=='tennis' and l['model_id']==model for l in pool))

    def test_http_api_categories_filters_and_idempotent_commands(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from high_odds_api import build_high_odds_router
        app=FastAPI();app.include_router(build_high_odds_router(lambda:'test-operator',lambda _key:None))
        with TestClient(app) as client:
            self.assertEqual(len(client.get('/api/sports-ai/high-odds').json()['categories']),9)
            self.assertEqual(client.get('/api/sports-ai/high-odds?date_from=2026-11-09&date_to=2026-11-08').status_code,422)
            first=client.post('/api/sports-ai/high-odds/generate',json={'idempotency_key':'same-command'})
            second=client.post('/api/sports-ai/high-odds/generate',json={'idempotency_key':'same-command'})
            self.assertEqual(first.status_code,202)
            self.assertEqual(first.json()['event_id'],second.json()['event_id'])
            self.assertEqual(client.put('/api/sports-ai/high-odds/settings',json={'settings':{'max_legs':100}}).status_code,422)

    def test_event_recovery_deduplication_and_failure_audit(self):
        with db.connect() as c:
            event=self.event(c);self.assertEqual(event,self.event(c))
            with patch.object(service,'generate_event',side_effect=RuntimeError('test database recovery')):
                result=service.process_events(c,self.now)
            self.assertEqual(result['status'],'failed')
            self.assertEqual(c.execute("SELECT COUNT(*) FROM sports_ai_high_odds_audit WHERE action='event.failed'").fetchone()[0],1)
            later=self.now+timedelta(minutes=2)
            self.assertEqual(service.process_events(c,later)['status'],'complete')
            self.assertEqual(c.execute("SELECT COUNT(*) FROM sports_ai_high_odds_events WHERE status='completed'").fetchone()[0],3)

    def test_configuration_rejects_unsafe_limits_atomically(self):
        with db.connect() as c:
            with self.assertRaises(ValueError):service.save_settings(c,{'max_legs':40},[],'test')
            with self.assertRaises(ValueError):service.save_settings(c,{},[{'id':'500-1000','max_odds':2000}],'test')
            result=service.save_settings(c,{'max_legs':12},[{'id':'500-1000','enabled':False}],'test')
            self.assertEqual(result['settings']['max_legs'],12)
            self.assertFalse(next(c for c in result['categories'] if c['id']=='500-1000')['enabled'])

    def test_official_settlement_feeds_category_performance(self):
        with db.connect() as c:
            fixtures=self.seed(c,2)
            identifier=next(i for r in service.generate_event(c,self.event(c),self.now) for i in r['created_ids'])
            for i,item in enumerate(fixtures):
                c.execute("INSERT INTO sports_ai_results(id,fixture_id,home_score,away_score,source,status,settled_at) VALUES(?,?,2,0,'official','final',?)",
                    (f'result-{i}',item['fixture_id'],(self.now+timedelta(days=2,hours=2)).isoformat()))
            monitor_accumulators(c,'test',None,'high-odds-settle',now=self.now+timedelta(days=3))
            board=service.dashboard(c,self.now+timedelta(days=3))
            self.assertEqual(board['kpis']['settled'],1)
            self.assertEqual(board['tickets'][0]['outcome'],'won')
            self.assertEqual(sum(category['wins'] or 0 for category in board['categories']),1)


class OptimizationMathTests(unittest.TestCase):
    def setUp(self):eligible_everywhere(self);self.now=datetime(2026,11,3,12,tzinfo=timezone.utc)

    def leg(self,index,odds=2.1,probability=.62,**overrides):
        raw=_row(opportunity_id=f'opp-{index}',fixture_id=f'fixture-{index}',home=f'Home {index}',away=f'Away {index}',
            competition=f'League {index}',decimal_odds=odds,probability=probability,**overrides)
        leg=_candidate(raw,self.now,default_config());leg['calibration_error']=.04
        return leg

    def test_cap_boundaries_and_flexible_selection_counts(self):
        category={'min_odds':500,'max_odds':1000}
        pool=[self.leg(i,10,.15) for i in range(4)]
        candidates,_=service.optimize(pool,category,{**default_config(),**service.DEFAULTS})
        self.assertTrue(candidates)
        self.assertTrue(all(t['combined_odds']==1000 and len(t['legs'])==3 for t in candidates))
        self.assertFalse(service.inside(1000.001,category))

    def test_only_jackpot_can_exceed_regular_odds_cap(self):
        jackpot={'id':'jackpot','min_odds':2_000_000,'max_odds':2_200_000}
        self.assertTrue(service.inside(2**21,jackpot))
        self.assertFalse(service.inside(2_200_000.01,jackpot))
        self.assertFalse(service.inside(1_000_000,jackpot))
        self.assertFalse(service.inside(2**21,{**jackpot,'id':'500-1000'}))
        self.assertFalse(service.inside(float('inf'),jackpot))

    def test_unsupported_dependence_is_not_multiplied(self):
        category={'min_odds':2,'max_odds':24.99}
        settings={**default_config(),**service.DEFAULTS}
        for conflict in ('fixture_id','home','competition'):
            left=self.leg(1);right=self.leg(2);right[conflict]=left[conflict]
            self.assertEqual(service.optimize([left,right],category,settings)[0],[])

    def test_probability_value_and_calibration_sensitivity_math(self):
        candidates,_=service.optimize([self.leg(1),self.leg(2)],{'min_odds':2,'max_odds':4.99},{**default_config(),**service.DEFAULTS})
        first=candidates[0]
        self.assertAlmostEqual(first['joint_probability'],.62**2)
        self.assertAlmostEqual(first['estimated_edge'],(.62**2)*(2.1**2)-1)
        self.assertAlmostEqual(first['lower_joint_probability'],.58**2)
        self.assertAlmostEqual(first['risk'],1-.58**2)


if __name__=='__main__':unittest.main()
