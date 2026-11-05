import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

from accumulator_engine import pack_league_tickets
from prediction_engine import rotate_by_league


class CoverageSelectionTests(unittest.TestCase):
    def test_each_league_is_visited_before_a_league_is_repeated(self):
        rows = [
            {'sport': 'football', 'competition': 'Alpha', 'id': 1},
            {'sport': 'football', 'competition': 'Alpha', 'id': 2},
            {'sport': 'basketball', 'competition': 'Beta', 'id': 3},
            {'sport': 'ice_hockey', 'competition': 'Gamma', 'id': 4},
        ]
        chosen = rotate_by_league(rows, 4)
        self.assertEqual([row['id'] for row in chosen], [3, 1, 4, 2])

    def test_high_probability_pairs_come_from_different_leagues_first(self):
        legs = [
            {'competition': 'League A', 'probability': 0.91, 'fixture_id': 'a1'},
            {'competition': 'League A', 'probability': 0.80, 'fixture_id': 'a2'},
            {'competition': 'League B', 'probability': 0.88, 'fixture_id': 'b1'},
            {'competition': 'League C', 'probability': 0.84, 'fixture_id': 'c1'},
        ]
        tickets = pack_league_tickets(legs, 2)
        self.assertEqual([leg['fixture_id'] for leg in tickets[0]], ['a1', 'b1'])
        self.assertEqual([leg['fixture_id'] for leg in tickets[1]], ['c1', 'a2'])


if __name__ == '__main__':
    unittest.main()
