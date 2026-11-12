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

    def store(self,c,prefix='t',source='market',odds=1.27):
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

    def test_started_or_out_of_band_market_ticket_is_not_displayed(self):
        with db.connect() as c:
            self.store(c,'too-short',odds=1.10)
            identifier,legs=self.store(c,'started')
            c.execute("UPDATE sports_ai_fixtures SET status='finished' WHERE id=?",(legs[0]['fixture_id'],))
            self.assertEqual(board._stored_slips(c,self.day,'sure_odds',now=NOW),[])

    def test_daily_cap_does_not_hide_stored_tickets_or_prevent_booking_them(self):
        with db.connect() as c:
            for i in range(board.SURE_DAILY_MAX):self.store(c,f'ticket-{i}')
            c.commit()
            with patch.object(board,'_market_sure_pool',side_effect=AssertionError('New ticket generated at cap')):
                self.assertEqual(len(board._sure_today(c,self.day,NOW)),board.SURE_DAILY_MAX)
        job=board.prepare_sure_odds_booking(now=NOW)
        self.assertIsNotNone(job)
        with db.connect() as c:
            self.assertEqual(c.execute('SELECT status FROM sports_ai_booking_jobs WHERE id=?',(job,)).fetchone()[0],'QUEUED')
            self.assertEqual(c.execute('SELECT COUNT(*) FROM sports_ai_accumulator_tickets').fetchone()[0],board.SURE_DAILY_MAX)
            c.execute("UPDATE sports_ai_booking_jobs SET status='FAILED',reason_code='SURE_ODDS_OUT_OF_RANGE',error='Displayed total 1.98 is outside 2.00-2.95' WHERE id=?",(job,))
            stored=board._sure_today(c,self.day,NOW)
            board._attach_booking(c,[{'tickets':stored}])
            failed=next(ticket for ticket in stored if ticket.get('booking_status')=='FAILED')
            self.assertIn('1.98',failed['booking_note'])
            self.assertIsNone(failed['booking_code'])

    def test_dropped_leg_cannot_create_an_out_of_band_code(self):
        booked=[{'fixture_id':'a','market':'HOME'},{'fixture_id':'b','market':'HOME'}]
        shown=[{**leg,'page_odds':odds} for leg,odds in zip(booked,[1.25,1.22])]
        self.assertIn('outside 2.00',_sure_price_problem(booked,shown))
        shown=[{**leg,'page_odds':1.45} for leg in booked]
        self.assertIsNone(_sure_price_problem(booked,shown))
        self.assertIsNotNone(_sure_price_problem(booked,[]))
