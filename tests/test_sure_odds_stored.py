import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import db
import high_odds_board as board
from command_view import _sure_tickets
from sportybet_booking import _sure_price_problem

NOW=datetime(2026,11,12,10,tzinfo=timezone.utc)


class StoredSureOddsTests(unittest.TestCase):
    def setUp(self):
        self.previous=db.DB
        self.temp=tempfile.TemporaryDirectory()
        db.DB=Path(self.temp.name)/'sports.sqlite3'
        db.initialize()
        self.day=board._today_window(NOW)[0]

    def tearDown(self):
        db.DB=self.previous
        self.temp.cleanup()

    def store(self,c,prefix='t',source='market',odds=1.37):
        legs=[]
        for index in range(3):
            fid=f'{prefix}-{index}'
            kickoff=(NOW+timedelta(hours=4)).isoformat()
            c.execute("""INSERT INTO sports_ai_fixtures
                 (id,external_id,source,sport,competition,home,away,starts_at,status,statistics_json,odds_json,observed_at)
                 VALUES(?,?,'test','tennis','League',?,?,?,'scheduled','{}','{}',?)""",
                      (fid,fid,f'Home {fid}',f'Away {fid}',kickoff,NOW.isoformat()))
            legs.append({'fixture_id':fid,'opportunity_id':fid,'sport':'tennis','competition':'League',
                         'home':f'Home {fid}','away':f'Away {fid}','starts_at':kickoff,'market':'MONEYLINE_HOME',
                         'selection':f'Home {fid}','decimal_odds':odds,'probability':.74,'confidence':None,
                         'preferred':False,'bookmaker':'SportyBet','odds_observed_at':NOW.isoformat(),
                         'model_id':'market-price'})
        payload=board._ready_ticket(self.day,{'id':'2-2.95','odds_label':'2.00–2.95'},legs,odds**3,.74**3)
        payload.update(leg_source=source)
        identifier=board._upsert_ticket(c,self.day,payload,NOW,product='sure_odds')
        return identifier,legs

    def test_market_ticket_displays_without_becoming_model_qualified(self):
        with db.connect() as c:
            identifier,legs=self.store(c)
            with patch.object(board,'_qualified_slip',side_effect=AssertionError('Model gate used for market ticket')):
                stored=board._stored_slips(c,self.day,'sure_odds',now=NOW)
            shown=_sure_tickets({'sure_tickets':stored},NOW)
            self.assertEqual(len(shown),1)
            self.assertEqual(shown[0]['probability_source'],'market')
            self.assertEqual(shown[0]['id'],identifier)
            self.assertIsNone(shown[0]['confidence'])
            self.assertIsNone(shown[0]['code'])
            self.assertEqual(c.execute("SELECT COUNT(*) FROM sports_ai_qualification_decisions WHERE state='QUALIFIED'").fetchone()[0],0)

    def test_model_ticket_still_requires_model_qualification(self):
        with db.connect() as c:
            self.store(c,source='model')
            with patch.object(board,'_qualified_slip',return_value=False) as gate:
                self.assertEqual(board._stored_slips(c,self.day,'sure_odds',now=NOW),[])
                gate.assert_called_once()

    def test_no_new_sure_ticket_is_built_from_market_prices_without_predictions(self):
        with db.connect() as c:
            with patch.object(board, '_candidates', return_value=[]), \
                 patch.object(board, '_sure_pool', return_value=[]), \
                 patch.object(board, '_market_sure_pool', side_effect=AssertionError('Price-only fallback used')):
                self.assertEqual(board._sure_today(c, self.day, NOW), [])

    def test_model_candidates_from_all_four_sports_can_form_sure_tickets(self):
        for sport in ('football', 'basketball', 'ice_hockey', 'tennis'):
            with self.subTest(sport=sport):
                legs = [
                    {'fixture_id': f'{sport}-{i}', 'sport': sport, 'home': f'Home {i}',
                     'away': f'Away {i}', 'competition': f'League {i}', 'market': 'MONEYLINE_HOME',
                     'decimal_odds': 1.65, 'probability': .9, 'confidence': .9,
                     'starts_at': (NOW + timedelta(hours=4)).isoformat()}
                    for i in range(2)
                ]
                self.assertEqual(len(board.pack_sure_tickets(legs, self.day)), 1)

    def test_booking_gate_accepts_more_than_four_model_selections(self):
        import sqlite3
        from sportybet_booking import _selection_limit, _sure_odds_ticket
        c = sqlite3.connect(':memory:')
        c.row_factory = sqlite3.Row
        c.execute('CREATE TABLE sports_ai_accumulator_tickets(id, parameters_json)')
        c.execute('CREATE TABLE sports_ai_accumulator_legs(ticket_id, sport, market, decimal_odds, probability, bookmaker)')
        c.execute('INSERT INTO sports_ai_accumulator_tickets VALUES(?, ?)', ('t', json.dumps({'product': 'sure_odds'})))
        c.executemany('INSERT INTO sports_ai_accumulator_legs VALUES(?,?,?,?,?,?)',
                      [('t', 'football', 'HOME', 1.17, .85, 'SportyBet')] * 6)
        with patch('market_registry.holdout_ready', return_value=True):
            self.assertTrue(_sure_odds_ticket(c, 't'))
        self.assertEqual(_selection_limit(c, 't'), 50)
        c.close()

    def test_model_sure_booking_job_limit_does_not_change_other_products(self):
        from sportybet_booking import _selection_limit, create_job
        with db.connect() as c:
            ticket_id, _ = self.store(c, source='model')
            selections = [
                {'ticket_id': ticket_id, 'fixture_id': f'fixture-{i}', 'sport': 'football',
                 'market': 'HOME', 'selection': 'Home'}
                for i in range(51)
            ]
            with patch('jackpot_stop.require_enabled'), \
                 patch('booking_policy.load', return_value={'require_live_qualification': False,
                                                            'enforce_match_exposure': False}), \
                 patch('qualification_engine.require_current_qualification'), \
                 patch('prediction_integrity.require_bookable', return_value={'legs': {}}):
                result = create_job(c, selections[:50], high_odds=True)
                self.assertEqual(result['status'], 'QUEUED')
                with self.assertRaisesRegex(ValueError, '1 to 50'):
                    create_job(c, selections, high_odds=True)
                c.execute('UPDATE sports_ai_accumulator_tickets SET parameters_json=? WHERE id=?',
                          (json.dumps({'product': 'high_odds'}), ticket_id))
                self.assertEqual(_selection_limit(c, ticket_id), 25)
                with self.assertRaisesRegex(ValueError, '1 to 30'):
                    create_job(c, selections[:31], high_odds=True)

    def test_legacy_price_only_ticket_cannot_be_queued(self):
        from sportybet_booking import create_job, ticket_selections
        with db.connect() as c:
            ticket_id, _ = self.store(c)
            with patch('jackpot_stop.require_enabled'):
                with self.assertRaisesRegex(ValueError, 'bookmaker-only probabilities'):
                    create_job(c, ticket_selections(c, ticket_id), high_odds=True)

    def test_started_or_out_of_band_market_ticket_is_not_displayed(self):
        with db.connect() as c:
            self.store(c,'too-short',odds=1.10)
            identifier,legs=self.store(c,'started')
            c.execute("UPDATE sports_ai_fixtures SET status='finished' WHERE id=?",(legs[0]['fixture_id'],))
            self.assertEqual(board._stored_slips(c,self.day,'sure_odds',now=NOW),[])

    def test_daily_cap_keeps_legacy_tickets_visible_but_does_not_book_them(self):
        with db.connect() as c:
            for i in range(board.SURE_DAILY_MAX):self.store(c,f'ticket-{i}')
            c.commit()
            with patch.object(board,'_market_sure_pool',side_effect=AssertionError('New ticket generated at cap')):
                self.assertEqual(len(board._sure_today(c,self.day,NOW)),board.SURE_DAILY_MAX)
        with patch('sportybet_booking._now', return_value=NOW):
            job=board.prepare_sure_odds_booking(now=NOW)
        self.assertIsNone(job)
        with db.connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM sports_ai_accumulator_tickets').fetchone()[0],board.SURE_DAILY_MAX)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM sports_ai_booking_jobs').fetchone()[0],0)

    def test_dropped_leg_cannot_create_an_out_of_band_code(self):
        booked=[{'fixture_id':'a','market':'HOME'},{'fixture_id':'b','market':'HOME'}]
        shown=[{**leg,'page_odds':odds} for leg,odds in zip(booked,[1.25,1.22])]
        self.assertIn('outside 2.50',_sure_price_problem(booked,shown))
        shown=[{**leg,'page_odds':1.6} for leg in booked]
        self.assertIsNone(_sure_price_problem(booked,shown))
        self.assertIsNotNone(_sure_price_problem(booked,[]))

    def test_a_short_slip_is_filled_with_another_selection_inside_the_range(self):
        from sportybet_booking import _sure_next_supplement
        # The stored four-leg ticket lost one leg. 1.27 x 1.27 x 1.26 = 2.03.
        product = 1.27 * 1.27 * 1.26
        self.assertLess(product, 2.50)
        spares = [
            {'fixture_id': 'too-long', 'sport': 'football', 'market': 'HOME', 'decimal_odds': 1.80},
            {'fixture_id': 'fits', 'sport': 'football', 'market': 'HOME', 'decimal_odds': 1.30},
            {'fixture_id': 'used', 'sport': 'football', 'market': 'HOME', 'decimal_odds': 1.25},
        ]
        choice = _sure_next_supplement(product, spares, {'used'}, 3)
        self.assertEqual(choice['fixture_id'], 'fits')
        self.assertTrue(2.50 <= product * choice['decimal_odds'] <= 2.95)
        self.assertIsNone(_sure_next_supplement(product, [spares[0]], set(), 3))
        self.assertIsNone(_sure_next_supplement(2.70, spares, set(), 4))
