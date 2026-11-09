"""Match Results read model: real settled picks, metrics and labels."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

import results_view as rv


def row(result, probability, odds, **extra):
    base = {'id': extra.pop('id', f'{result}{probability}{odds}'), 'date': '2026-11-08', 'sport': 'football',
            'market': '1X2', 'competition': 'L', 'model': 'm v1', 'result': result, 'probability': probability, 'odds': odds}
    base.update(extra)
    return base


class ResultsViewTests(unittest.TestCase):
    def test_metrics(self):
        rows = [row('Won', 0.8, 1.5), row('Lost', 0.6, 2.0), row('Void', 0.5, 2.0)]
        m = rv.metrics(rows)
        self.assertEqual((m['settled'], m['decided'], m['voids']), (3, 2, 1))
        self.assertAlmostEqual(m['accuracy'], 0.5)
        self.assertAlmostEqual(m['brier'], ((0.8 - 1) ** 2 + 0.6 ** 2) / 2)
        self.assertAlmostEqual(m['realized_return'], ((1.5 - 1) - 1) / 2)
        self.assertAlmostEqual(m['expected_return'], ((0.8 * 1.5 - 1) + (0.6 * 2 - 1)) / 2)

    def test_picks_without_a_price_do_not_count_towards_returns(self):
        m = rv.metrics([row('Won', 0.8, None), row('Lost', 0.6, 2.0)])
        self.assertEqual((m['scored'], m['priced']), (2, 1))
        self.assertAlmostEqual(m['realized_return'], -1)

    def test_empty_is_none_not_zero(self):
        m = rv.metrics([])
        self.assertIsNone(m['accuracy'])
        self.assertIsNone(m['realized_return'])

    def test_labels(self):
        self.assertEqual(rv.market_group('HOME'), '1X2')
        self.assertEqual(rv.market_group('MONEYLINE_AWAY'), 'Match Winner')
        self.assertEqual(rv.market_group('OVER_2.5'), 'Totals')
        self.assertEqual(rv.market_group('HOME_SPREAD_4.5'), 'Handicap')
        self.assertEqual(rv.selection_label('HOME', 'Arsenal', 'Brighton'), 'Arsenal (Home)')
        self.assertEqual(rv.selection_label('MONEYLINE_AWAY', 'Lakers', 'Warriors'), 'Warriors')

    def test_filters(self):
        rows = [row('Won', 0.8, 1.5, id='a', sport='basketball', date='2026-11-01'), row('Lost', 0.6, 2.0, id='b')]
        self.assertEqual([r['id'] for r in rv._filter(rows, sport='basketball')], ['a'])
        self.assertEqual([r['id'] for r in rv._filter(rows, date_from='2026-11-05')], ['b'])


if __name__ == '__main__':
    unittest.main()
