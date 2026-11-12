"""Handicap labels exactly as SportyBet shows them (12 Nov 2026). The market carries the home side's
line and each selection its own; settlement reads AWAY_SPREAD_x as "away gets x points"."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

from sportybet_research import map_market
from accumulator_engine import settle_result


def canon(sport, market, selection, line):
    return map_market(sport, market, selection, line)


class SpreadMappingTests(unittest.TestCase):
    def test_each_side_keeps_its_own_sign(self):
        self.assertEqual(canon('basketball', 'Handicap (incl. overtime) 2.5', 'Home (+2.5)', 2.5)['canonical_market'], 'HOME_SPREAD_2.5')
        self.assertEqual(canon('basketball', 'Handicap (incl. overtime) 2.5', 'Away (-2.5)', 2.5)['canonical_market'], 'AWAY_SPREAD_-2.5')
        self.assertEqual(canon('basketball', 'Handicap (incl. overtime) -1.5', 'Away (+1.5)', -1.5)['canonical_market'], 'AWAY_SPREAD_1.5')
        self.assertEqual(canon('basketball', 'Handicap (incl. overtime) -1.5', 'Home (-1.5)', -1.5)['canonical_market'], 'HOME_SPREAD_-1.5')

    def test_without_a_number_on_the_selection_the_away_side_takes_the_opposite_of_the_market_line(self):
        self.assertEqual(canon('basketball', 'Point Spread', 'Away', 26.5)['canonical_market'], 'AWAY_SPREAD_-26.5')
        self.assertEqual(canon('basketball', 'Point Spread', 'Away', 0)['canonical_market'], 'AWAY_SPREAD_0')

    def test_spurs_case_now_settles_as_the_price_meant(self):
        # Kings 109 - 124 Spurs. The 4.60 price was for "Away (-26.5)": Spurs had to win by 27.
        market = canon('basketball', 'Handicap (incl. overtime) 26.5', 'Away (-26.5)', 26.5)['canonical_market']
        self.assertEqual(market, 'AWAY_SPREAD_-26.5')
        self.assertEqual(settle_result('basketball', market, 109, 124), 'lost')

    def test_three_way_score_handicap_is_not_a_point_spread(self):
        for pick in ('Away (0:2)', 'Home (0:2)', 'Draw (0:2)'):
            self.assertFalse(canon('basketball', 'Handicap 0:2', pick, 0)['supported'], pick)

    def test_football_asian_handicap_away_side(self):
        self.assertEqual(canon('football', 'Asian Handicap -0.5', 'Away (+0.5)', -0.5)['canonical_market'], 'AWAY_HANDICAP_0.5')
        self.assertEqual(canon('football', 'Asian Handicap', 'Home', -0.5)['canonical_market'], 'HOME_HANDICAP_-0.5')


if __name__ == '__main__':
    unittest.main()
