"""Match Results shows only bets the system placed; real money is settled from SportyBet."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

import placed_results as pr


def bet(status='PLACED', stake=100.0, odds=2.0, payout=None, profit=None):
    return {'status': status, 'stake': stake, 'decimal_odds': odds, 'payout': payout, 'profit': profit}


def row(result, stake=100.0, payout=None, profit=None, group='single', date='2026-11-11'):
    return {'result': result, 'stake': stake, 'payout': payout, 'profit': profit,
            'group': group, 'sport': 'football', 'date': date}


class ResultTests(unittest.TestCase):
    def test_open_real_bet_awaits_result(self):
        self.assertEqual(pr._result(bet('PLACED'), {'status': 'PLACED', 'payout': None})[0], 'Awaiting result')

    def test_sportybet_history_wins_over_our_settlement(self):
        result, payout, profit, source, mismatch = pr._result(
            bet('WON', payout=200, profit=100), {'status': 'SETTLED_LOST', 'payout': 0})
        self.assertEqual((result, payout, profit, source, mismatch), ('Lost', 0, -100, 'SportyBet Bet History', True))

    def test_sportybet_payout_is_used(self):
        self.assertEqual(pr._result(bet('PLACED'), {'status': 'SETTLED_WON', 'payout': 195.5})[:3], ('Won', 195.5, 95.5))

    def test_unclear_submission_is_never_won_or_lost(self):
        self.assertEqual(pr._result(bet('WON'), {'status': 'UNKNOWN', 'payout': None})[0], 'Unconfirmed')

    def test_placed_real_bet_shows_score_result_until_sportybet_settles(self):
        result, _, _, source, _ = pr._result(bet('LOST', payout=0, profit=-100), {'status': 'PLACED', 'payout': None})
        self.assertEqual((result, source), ('Lost', 'Verified final score (awaiting SportyBet)'))


class MetricTests(unittest.TestCase):
    def test_metrics(self):
        m = pr.metrics([row('Won', payout=250, profit=150), row('Lost', payout=0, profit=-100),
                        row('Void', payout=100, profit=0), row('Awaiting result')])
        self.assertEqual((m['placed'], m['settled'], m['won'], m['lost'], m['awaiting']), (4, 3, 1, 1, 1))
        self.assertAlmostEqual(m['win_rate'], 0.5)
        self.assertEqual((m['staked'], m['returned'], m['profit']), (400, 350, 50))
        self.assertAlmostEqual(m['roi'], 50 / 300)

    def test_nothing_settled_is_none_not_zero(self):
        m = pr.metrics([row('Awaiting result')])
        self.assertIsNone(m['profit'])
        self.assertIsNone(m['win_rate'])

    def test_filters(self):
        rows = [row('Won', group='ticket', date='2026-11-10'), row('Lost')]
        self.assertEqual(len(pr._filter(rows, kind='ticket')), 1)
        self.assertEqual(len(pr._filter(rows, date_from='2026-11-11')), 1)

    def test_local_day_is_lagos_date(self):
        self.assertEqual(pr.local_day('2026-11-10T23:30:00+00:00'), '2026-11-11')


class CollectTests(unittest.TestCase):
    """Only bets with a real-money submission are listed; rehearsed paper bets never are."""
    def test_paper_bets_are_not_listed(self):
        import sqlite3
        c = sqlite3.connect(':memory:')
        c.row_factory = sqlite3.Row
        c.executescript("""
            CREATE TABLE sports_ai_single_bets(id,fixture_id,sport,competition,home,away,market,selection,decimal_odds,
                stake,mode,status,starts_at,payout,profit,placed_at,settled_at,kind,created_at);
            CREATE TABLE sports_ai_single_bet_legs(bet_id,leg_no,fixture_id,sport,competition,home,away,market,selection,
                decimal_odds,starts_at,status);
            CREATE TABLE sports_ai_single_live_orders(id,bet_id,kind,status,payout,bookmaker_ref,submitted_at,created_at,reconciled_at);
            CREATE TABLE sports_ai_booking_codes(accumulator_ticket_id,code);
            INSERT INTO sports_ai_single_bets VALUES
             ('real','f1','football','L','A','B','HOME','Home',2.0,100,'PAPER','PLACED','2026-11-11T15:00:00+00:00',NULL,NULL,NULL,NULL,'SINGLE','2026-11-11'),
             ('paper','f2','football','L','C','D','HOME','Home',2.0,100,'PAPER','WON','2026-11-11T15:00:00+00:00',200,100,NULL,NULL,'SINGLE','2026-11-11');
            INSERT INTO sports_ai_single_live_orders VALUES
             ('o1','real','SUBMISSION','SETTLED_LOST',0,'SB1','2026-11-11T10:00:00+00:00','2026-11-11T10:00:00+00:00','2026-11-11T18:00:00+00:00'),
             ('o2','paper','REHEARSAL','REHEARSED',NULL,NULL,NULL,'2026-11-11T10:00:00+00:00',NULL);
        """)
        rows = pr.collect(c)
        self.assertEqual([r['id'] for r in rows], ['real'])
        self.assertEqual((rows[0]['result'], rows[0]['profit'], rows[0]['bookmaker_ref']), ('Lost', -100, 'SB1'))


if __name__ == '__main__':
    unittest.main()
