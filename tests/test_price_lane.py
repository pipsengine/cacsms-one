import sys
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import worker
import sportybet_feed


class PriceLaneTests(unittest.TestCase):
    def test_pages_are_published_before_the_next_page_is_requested(self):
        page=Mock()
        bodies=[{'data':{'totalNum':200,'tournaments':[{'name':'first'}]}},
                {'data':{'totalNum':200,'tournaments':[{'name':'second'}]}}]
        responses=[]
        for body in bodies:
            response=Mock(status=200)
            response.json.return_value=body
            responses.append(response)
        page.context.request.get.side_effect=responses
        stream=sportybet_feed._pages(page,'https://www.sportybet.com/api/pcUpcomingEvents?pageNum=1',None)
        self.assertEqual(next(stream),bodies[0])
        self.assertEqual(page.context.request.get.call_count,1)
        self.assertEqual(next(stream),bodies[1])
        with self.assertRaises(StopIteration):next(stream)

    def test_section_delivers_each_quote_batch_without_buffering_the_sport(self):
        page=Mock()
        response=Mock(status=200,url='https://www.sportybet.com/api/pcUpcomingEvents')
        response.json.return_value={'data':{'tournaments':[]}}
        page.goto.side_effect=lambda *a,**kw:page.on.call_args.args[1](response)
        page.locator.return_value.inner_text.return_value='Upcoming basketball'
        received=[]
        def pages(*args):
            yield {'page':1}
            self.assertEqual(received,[['first']])
            yield {'page':2}
        with patch('source_permissions.require'), patch.object(sportybet_feed,'_pages',side_effect=pages), \
             patch.object(sportybet_feed,'quotes_from_payload',side_effect=[['first'],['second']]):
            result=sportybet_feed._read_section(page,'basketball','https://www.sportybet.com/ng/sport/basketball',on_rows=received.append)
        self.assertEqual(received,[['first'],['second']])
        self.assertEqual(result['rows'],[])

    def connection(self, owner, expires):
        c=Mock()
        c.execute.return_value.fetchone.return_value={'owner':owner,'expires_at':expires}
        @contextmanager
        def connected():
            yield c
        return connected

    def test_non_leader_never_collects(self):
        future=(datetime.now(timezone.utc)+timedelta(minutes=1)).isoformat()
        with patch.object(worker,'connect',self.connection('other-worker',future)), patch.object(sportybet_feed,'collect_displayed_odds') as collect:
            self.assertEqual(worker._price_tick()['status'],'standby')
            collect.assert_not_called()

    def test_new_prices_refresh_decisions_without_booking_or_placing(self):
        future=(datetime.now(timezone.utc)+timedelta(minutes=1)).isoformat()
        with patch.object(worker,'connect',self.connection(worker._leader_name(),future)), \
             patch.object(sportybet_feed,'collect_displayed_odds',return_value={'status':'collected','imported':2}), \
             patch.object(worker,'_odds_refresh_tick') as refresh, patch.object(worker,'_observed'), \
             patch.object(worker,'_booking_tick') as book, patch.object(worker,'_single_betting_fast') as bets:
            worker._price_tick()
            refresh.assert_called_once()
            book.assert_not_called()
            bets.assert_not_called()

    def test_overlapping_collectors_yield_and_release_the_guard(self):
        sportybet_feed._COLLECT_LOCK.acquire()
        try:
            with patch.object(sportybet_feed,'_collect_if_due') as collect:
                self.assertEqual(sportybet_feed.collect_displayed_odds()['status'],'waiting')
                collect.assert_not_called()
        finally:
            sportybet_feed._COLLECT_LOCK.release()
        with patch.object(sportybet_feed,'_collect_if_due',side_effect=RuntimeError('test')):
            with self.assertRaises(RuntimeError):
                sportybet_feed.collect_displayed_odds()
        self.assertFalse(sportybet_feed._COLLECT_LOCK.locked())
