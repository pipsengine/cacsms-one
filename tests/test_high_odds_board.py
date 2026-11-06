import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

from high_odds_board import SELECTION_FLOOR, build_band, choose_one_per_fixture


def leg(fixture, odds, probability, confidence=0.9, market='OVER_1.5'):
    return {
        'fixture_id': fixture,
        'home': fixture,
        'away': 'Opponent',
        'sport': 'football',
        'competition': 'League',
        'market': market,
        'starts_at': '2026-11-07T15:00:00+00:00',
        'decimal_odds': odds,
        'probability': probability,
        'confidence': confidence,
    }


class HighOddsBoardTests(unittest.TestCase):
    def test_a_selection_under_30_percent_is_left_out(self):
        rows = choose_one_per_fixture([leg('a', 3, SELECTION_FLOOR - 0.01), leg('b', 1.8, 0.86)])
        self.assertEqual([row['fixture_id'] for row in rows], ['b'])

    def test_one_market_per_fixture_keeps_the_stronger_price(self):
        rows = choose_one_per_fixture([
            leg('a', 1.4, 0.91, market='OVER_1.5'),
            leg('a', 4.5, 0.40, market='HOME'),
        ])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['market'], 'OVER_1.5')

    def test_preferred_selections_are_used_before_a_weaker_price(self):
        candidates = choose_one_per_fixture([
            leg('a', 1.9, 0.90),
            leg('b', 1.8, 0.88),
            leg('c', 1.7, 0.86),
            leg('d', 6.0, 0.35, confidence=0.5),
        ])
        chosen, product, _joint = build_band(candidates, 5)
        self.assertGreaterEqual(product, 5)
        self.assertNotIn('d', [leg['fixture_id'] for leg in chosen])
        self.assertGreaterEqual(len(chosen), 2)

    def test_a_high_band_can_add_a_weaker_selection_after_the_preferred_ones(self):
        candidates = choose_one_per_fixture([
            leg('a', 1.5, 0.90),
            leg('b', 1.4, 0.88),
            leg('c', 8.0, 0.32, confidence=0.7),
        ])
        chosen, product, _joint = build_band(candidates, 10)
        self.assertGreaterEqual(product, 10)
        self.assertIn('c', [item['fixture_id'] for item in chosen])

    def test_two_selections_are_required(self):
        candidates = choose_one_per_fixture([leg('a', 12, 0.91)])
        self.assertIsNone(build_band(candidates, 5))


if __name__ == '__main__':
    unittest.main()
