"""Objective 3 jackpots (owner, 12 Nov 2026): ₦1M, ₦10M, ₦20M, ₦50M, ₦100M and ₦200M on a ₦100 stake,
every selection priced 1.60-2.95, matches Monday to Sunday, one set per week, right sports and leagues."""
import json
import math
import sqlite3
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

import prize_ladder as ladder
import jackpot_stop
import league_quality as lq

NOW = datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc)   # a Monday


SPORTS = ('football',) * 5 + ('basketball',) * 2 + ('ice_hockey', 'tennis', 'tennis')   # roughly the real mix


def pool(n=400):
    legs = []
    for i in range(n):
        odds = 1.60 + (i % 27) * 0.05                       # 1.60 .. 2.90
        legs.append({'fixture_id': f'f{i}', 'sport': SPORTS[i % len(SPORTS)], 'competition': f'League {i % 60}',
                     'home': f'Home {i}', 'away': f'Away {i}', 'market': 'HOME', 'decimal_odds': round(odds, 2),
                     'probability': 0.95 / odds, 'starts_at': (NOW + timedelta(hours=5 + i % 120)).isoformat()})
    return legs


class LadderTests(unittest.TestCase):
    def test_six_prizes_on_a_100_stake(self):
        self.assertEqual(ladder.PRIZES, (1_000_000, 10_000_000, 20_000_000, 50_000_000, 100_000_000, 200_000_000))
        self.assertEqual(ladder.STAKE, 100)
        self.assertEqual((ladder.LEG_MIN, ladder.LEG_MAX), (1.60, 2.95))

    def test_every_prize_is_built_from_legs_priced_160_to_295(self):
        uses = {}
        for prize in sorted(ladder.PRIZES, reverse=True):
            legs = ladder.pick(pool(), prize, uses, NOW)
            self.assertIsNotNone(legs, prize)
            for leg in legs:
                uses[leg['fixture_id']] = uses.get(leg['fixture_id'], 0) + 1
                self.assertTrue(1.60 <= leg['decimal_odds'] <= 2.95)
            odds = math.prod(l['decimal_odds'] for l in legs)
            self.assertGreaterEqual(odds, ladder.prize_odds(prize) * ladder.BUILD_LOW - 1e-6, prize)
            self.assertLessEqual(len(legs), ladder.MAX_LEGS)
            self.assertEqual(len({l['fixture_id'] for l in legs}), len(legs))

    def test_matches_run_to_the_end_of_sunday(self):
        hours = ladder._hours_to_sunday(NOW)                  # Monday 10:00 WAT -> Sunday 23:59 WAT
        self.assertGreater(hours, 6 * 24)

    def test_one_set_per_week(self):
        c = sqlite3.connect(':memory:')
        c.execute('CREATE TABLE sports_ai_accumulator_tickets(id, run_id, parameters_json, created_at)')
        c.execute("INSERT INTO sports_ai_accumulator_tickets VALUES('t','run-1',?,?)",
                  (json.dumps({'product': 'prize_ladder', 'ladder_version': ladder.LADDER_VERSION}), (NOW + timedelta(hours=1)).isoformat()))
        # A ladder of the old 17-prize design built earlier this week does not use up the week's set.
        c.execute("INSERT INTO sports_ai_accumulator_tickets VALUES('old','run-0',?,?)",
                  (json.dumps({'product': 'prize_ladder'}), NOW.isoformat()))
        self.assertEqual(ladder._built_this_week(c, NOW + timedelta(days=3)), 1)   # Thursday: same week
        self.assertEqual(ladder._built_this_week(c, NOW + timedelta(days=7)), 0)   # next Monday: new week


class StopTests(unittest.TestCase):
    def test_ladder_is_enabled_and_retired_jackpots_stay_stopped(self):
        c = sqlite3.connect(':memory:')
        c.execute('CREATE TABLE sports_ai_accumulator_tickets(id, parameters_json)')
        c.execute('CREATE TABLE sports_ai_high_odds_tickets(ticket_id, category_id)')
        for tid, product in (('ladder', 'prize_ladder'), ('old', 'jackpot'), ('low', 'jackpot_low')):
            c.execute('INSERT INTO sports_ai_accumulator_tickets VALUES(?,?)', (tid, json.dumps({'product': product})))
        self.assertFalse(jackpot_stop.is_stopped(c, 'ladder'))
        self.assertTrue(jackpot_stop.is_stopped(c, 'old'))
        self.assertTrue(jackpot_stop.is_stopped(c, 'low'))


class LeagueQualityTests(unittest.TestCase):
    def tearDown(self):
        lq.clear()

    def test_simulated_and_esports_never(self):
        self.assertTrue(lq.simulated('football', 'Simulated Reality League UEFA Europa League SRL'))
        self.assertTrue(lq.simulated('efootball', 'Champions League'))
        self.assertTrue(lq.simulated('football', 'Premier League', 'Arsenal SRL', 'Chelsea SRL'))
        self.assertFalse(lq.simulated('football', 'Italy Serie A'))

    def test_losing_leagues_and_sports_are_left_out(self):
        lq._CACHE.update(at=float('inf'), record={'sports': {}, 'picks': 0, 'blocked_sports': {'frauenfussball'},
                                                   'blocked_leagues': {('football', 'Brazil Pernambucano')}})
        legs = [{'sport': 'football', 'competition': 'Italy Serie A'},
                {'sport': 'football', 'competition': 'Brazil Pernambucano'},
                {'sport': 'frauenfussball', 'competition': 'Germany Frauen-Bundesliga'},
                {'sport': 'football', 'competition': 'Simulated Reality League SRL'}]
        self.assertEqual([l['competition'] for l in lq.filter_legs(None, legs)], ['Italy Serie A'])

    def test_record_thresholds(self):
        c = sqlite3.connect(':memory:')
        c.row_factory = sqlite3.Row
        c.executescript("""
            CREATE TABLE sports_ai_fixtures(id, sport, competition, starts_at);
            CREATE TABLE sports_ai_engine_predictions(id, fixture_id, market, settled_outcome, created_at);
            CREATE TABLE sports_ai_value_opportunities(prediction_id, odds_id);
            CREATE TABLE sports_ai_odds_snapshots(id, bookmaker, decimal_odds, observed_at);""")
        def add(i, league, won, odds=1.8):
            c.execute('INSERT INTO sports_ai_fixtures VALUES(?,?,?,?)', (f'f{i}', 'football', league, '2026-11-10T15:00'))
            c.execute('INSERT INTO sports_ai_engine_predictions VALUES(?,?,?,?,?)', (f'p{i}', f'f{i}', 'HOME', 'won' if won else 'lost', '2026-11-10T09:00'))
            c.execute('INSERT INTO sports_ai_value_opportunities VALUES(?,?)', (f'p{i}', f'q{i}'))
            c.execute('INSERT INTO sports_ai_odds_snapshots VALUES(?,?,?,?)', (f'q{i}', 'SportyBet', odds, '2026-11-10T09:00'))
        for i in range(10):
            add(i, 'Losing League', won=i < 3)          # 3/10 at 1.80: -46%
        for i in range(10, 30):
            add(i, 'Good League', won=(i % 5) != 0)     # 16/20 at 1.80: +44%
        rec = lq._compute(c)
        self.assertIn(('football', 'Losing League'), rec['blocked_leagues'])
        self.assertNotIn(('football', 'Good League'), rec['blocked_leagues'])
        self.assertNotIn('football', rec['blocked_sports'])


if __name__ == '__main__':
    unittest.main()
