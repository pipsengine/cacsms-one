import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

import prize_ladder as pl


def pool(n=600):
    legs = []
    for i in range(n):
        odds = 1.15 + (i % 30) * 0.03            # 1.15 .. 2.02
        legs.append({'fixture_id': f'f{i}', 'home': f'H{i}', 'away': f'A{i}',
                     'sport': ('football', 'basketball', 'tennis', 'ice_hockey', 'handball')[i % 5],
                     'competition': f'L{i % 120}', 'decimal_odds': round(odds, 2), 'probability': 0.95 / odds,
                     'starts_at': f'2026-11-12T{10 + i % 12:02d}:00:00+00:00', 'market': 'HOME'})
    return legs


class PrizeLadderTests(unittest.TestCase):
    def test_every_prize_lands_in_band_with_modest_legs(self):
        legs, uses = pool(), {}
        for prize in sorted(pl.PRIZES, reverse=True):
            chosen = pl.pick(legs, prize, uses)
            self.assertIsNotNone(chosen, prize)
            for leg in chosen:
                uses[leg['fixture_id']] = uses.get(leg['fixture_id'], 0) + 1
            odds = math.prod(l['decimal_odds'] for l in chosen)
            target = pl.prize_odds(prize)
            self.assertGreaterEqual(odds, target * pl.BUILD_LOW, prize)
            self.assertLessEqual(odds, target * pl.BUILD_HIGH, prize)
            self.assertLessEqual(len(chosen), pl.MAX_LEGS)
            self.assertLess(odds ** (1 / len(chosen)), 1.6)   # legs stay modest
            self.assertEqual(len({l['fixture_id'] for l in chosen}), len(chosen))
            share = max(sum(1 for l in chosen if l['sport'] == s) for s in {l['sport'] for l in chosen})
            self.assertLessEqual(share, math.ceil(pl.MAX_SPORT_SHARE * pl.MAX_LEGS))   # no sport dominates
        self.assertLessEqual(max(uses.values()), pl.MAX_SHARE)

    def test_prize_floor_blocks_a_short_booking(self):
        import sportybet_booking as sb
        original = pl.minimum_odds
        pl.minimum_odds = lambda connection, ticket_id: 1000.0
        try:
            booked = [{'ticket_id': 't', 'fixture_id': f'f{i}', 'decimal_odds': 1.4} for i in range(20)]
            seen = [{'fixture_id': f'f{i}', 'page_odds': 1.4} for i in range(20)]   # 1.4^20 = 836
            self.assertIsNotNone(sb._prize_short(booked, seen))
            seen = [{'fixture_id': f'f{i}', 'page_odds': 1.45} for i in range(20)]  # 1.45^20 = 1688
            self.assertIsNone(sb._prize_short(booked, seen))
        finally:
            pl.minimum_odds = original


if __name__ == '__main__':
    unittest.main()


class ReserveTests(unittest.TestCase):
    def test_reserves_exclude_ticket_matches_and_teams(self):
        legs = pool()
        chosen = pl.pick(legs, 1_000_000, {})
        spare = pl.reserves(legs, chosen)
        self.assertTrue(0 < len(spare) <= pl.RESERVES)
        used = {l['fixture_id'] for l in chosen}
        teams = {t for l in chosen for t in (l['home'].casefold(), l['away'].casefold())}
        for leg in spare:
            self.assertNotIn(leg['fixture_id'], used)
            self.assertFalse({leg['home'].casefold(), leg['away'].casefold()} & teams)

    def test_top_up_adds_only_shown_legs_until_prize_is_reached(self):
        import asyncio
        import sportybet_booking as sb
        booked = [{'ticket_id': 't', 'fixture_id': f'b{i}', 'home': f'BH{i}', 'away': f'BA{i}', 'decimal_odds': 1.4} for i in range(18)]
        seen = [{'fixture_id': f'b{i}', 'page_odds': 1.4} for i in range(18)]          # 1.4^18 = 427x
        reserve = [{'fixture_id': f'r{i}', 'home': f'RH{i}', 'away': f'RA{i}', 'sport': 'football', 'market': 'HOME',
                    'decimal_odds': 1.5} for i in range(10)]
        hidden = {'r0', 'r3'}                                                          # SportyBet does not show these

        async def fake_prepare(page, items, **kwargs):
            return {'status': 'SELECTED', 'seen': [{'fixture_id': i['fixture_id'], 'page_odds': i['decimal_odds']}
                                                   for i in items if i['fixture_id'] not in hidden]}

        async def fake_open(*args, **kwargs):
            return None

        saved = (sb.prepare_slip, sb._open_sport, pl.reserve_legs, pl.minimum_odds)
        sb.prepare_slip, sb._open_sport = fake_prepare, fake_open
        pl.reserve_legs = lambda connection, ticket_id: reserve
        pl.minimum_odds = lambda connection, ticket_id: 1000.0
        try:
            added = asyncio.run(sb._top_up_from_reserves(None, booked, seen, {'max_price_move': 0.3, 'accept_displayed_odds': True}))
        finally:
            sb.prepare_slip, sb._open_sport, pl.reserve_legs, pl.minimum_odds = saved
        self.assertTrue(added)
        self.assertFalse({a['fixture_id'] for a in added} & hidden)
        self.assertIsNone(sb._prize_short(booked + added, seen))                       # prize reached
        self.assertLessEqual(len(booked) + len(added), 50)


class ReliabilityTests(unittest.TestCase):
    def test_only_reliable_markets_and_sports(self):
        legs = [{'sport': 'football', 'market': 'OVER_1.5'}, {'sport': 'football', 'market': 'BTTS_YES'},
                {'sport': 'football', 'market': 'DOUBLE_CHANCE_1X'}, {'sport': 'football', 'market': 'HOME'},
                {'sport': 'tennis', 'market': 'MONEYLINE_HOME'}, {'sport': 'basketball', 'market': 'MONEYLINE_AWAY'}]
        rates = {('tennis', 'winner'): (5, 20), ('basketball', 'winner'): (13, 14)}   # 25% dropped, 93% kept
        kept = [(l['sport'], l['market']) for l in pl.reliable(legs, rates)]
        self.assertEqual(kept, [('football', 'DOUBLE_CHANCE_1X'), ('football', 'HOME'), ('basketball', 'MONEYLINE_AWAY')])


class ReserveRankingTests(unittest.TestCase):
    def test_reserves_spread_across_sports_and_prefer_reliable_pages(self):
        legs = pool()
        chosen = pl.pick(legs, 100_000, {})
        rates = {('tennis', 'result'): (2, 10), ('basketball', 'result'): (10, 10)}
        spare = pl.reserves(legs, chosen, rates)
        sports = [leg['sport'] for leg in spare]
        self.assertLessEqual(max(sports.count(s) for s in set(sports)), pl.RESERVE_PER_SPORT)
        self.assertEqual(spare[0]['sport'], 'basketball')            # 100% page hit rate ranks first
        self.assertGreaterEqual(len(set(sports)), 3)
