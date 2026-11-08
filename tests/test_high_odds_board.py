import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

from datetime import datetime, timezone

from high_odds_board import COMBINED_TARGET, SELECTION_FLOOR, _in_band, build_day, build_slip, choose_one_per_fixture, pack_sure_tickets


def leg(fixture, odds, probability, confidence=0.9, market='OVER_1.5', sport='football', competition='League'):
    return {
        'fixture_id': fixture,
        'home': fixture,
        'away': 'Opponent',
        'sport': sport,
        'competition': competition,
        'market': market,
        'starts_at': '2026-11-07T15:00:00+00:00',
        'decimal_odds': odds,
        'probability': probability,
        'confidence': confidence,
        'preferred': probability >= 0.85 and confidence is not None and confidence >= 0.80,
    }


class HighOddsBoardTests(unittest.TestCase):
    def test_price_bands_do_not_overlap(self):
        bands = ('to-1.95', '2-3', '3-4', '4-5', 'above-5')
        self.assertEqual([band for band in bands if _in_band(1.95, band)], ['to-1.95'])
        self.assertEqual([band for band in bands if _in_band(1.96, band)], [])
        self.assertEqual([band for band in bands if _in_band(2, band)], ['2-3'])
        self.assertEqual([band for band in bands if _in_band(2.99, band)], ['2-3'])
        self.assertEqual([band for band in bands if _in_band(3, band)], ['3-4'])
        self.assertEqual([band for band in bands if _in_band(3.99, band)], ['3-4'])
        self.assertEqual([band for band in bands if _in_band(4, band)], ['4-5'])
        self.assertEqual([band for band in bands if _in_band(5, band)], ['4-5'])
        self.assertEqual([band for band in bands if _in_band(5.01, band)], ['above-5'])

    def test_a_selection_under_80_percent_is_left_out(self):
        rows = choose_one_per_fixture([leg('a', 3.4, SELECTION_FLOOR - 0.01), leg('b', 2.4, 0.86)])
        self.assertEqual([row['fixture_id'] for row in rows], ['b'])
        short = [leg(str(index), 2.4, 0.90) for index in range(11)]
        short.append(leg('city', 2.8, 0.43))
        self.assertIsNone(build_slip(short, 12))

    def test_sure_tickets_land_between_2_and_2_95(self):
        day = {'name': 'Saturday', 'date': '2026-11-07'}
        rows = [leg(str(index), 1.2, 0.90, competition=f'League {index}') for index in range(10)]
        rows.append(leg('long', 4.0, 0.95))
        rows.append(leg('weak', 1.2, 0.70))
        tickets = pack_sure_tickets(rows, day)
        self.assertEqual(len(tickets), 2)
        used = []
        for ticket in tickets:
            self.assertGreaterEqual(ticket['selection_count'], 2)
            self.assertGreaterEqual(ticket['combined_odds'], 2)
            self.assertLessEqual(ticket['combined_odds'], 2.95)
            self.assertTrue(all(item['probability'] >= 80 for item in ticket['selections']))
            used.extend(item['fixture'] for item in ticket['selections'])
        self.assertEqual(len(used), len(set(used)))
        self.assertNotIn('long vs Opponent', used)
        self.assertEqual(pack_sure_tickets([leg('only', 1.4, 0.91)], day), [])
        blocked = [leg('solo', 2.95, 0.99)]
        blocked.extend(leg(f'small-{index}', 1.2, 0.90, competition=f'League {index}') for index in range(5))
        opened = pack_sure_tickets(blocked, day)
        self.assertEqual(len(opened), 1)
        self.assertNotIn('solo vs Opponent', [item['fixture'] for item in opened[0]['selections']])

    def test_twelve_selections_when_every_price_is_from_2_to_3(self):
        rows = [leg(str(index), 2.4, 0.90, competition=f'League {index}') for index in range(12)]
        chosen, _product, _joint = build_slip(rows, 12)
        self.assertEqual(len(chosen), 12)
        self.assertTrue(all(2 <= item['decimal_odds'] < 3 for item in chosen))

    def test_ten_selections_when_every_price_is_from_3_to_4(self):
        rows = [leg(str(index), 3.4, 0.90) for index in range(10)]
        chosen, _product, _joint = build_slip(rows, 10)
        self.assertEqual(len(chosen), 10)

    def test_eight_selections_when_every_price_is_from_4_to_5(self):
        rows = [leg(str(index), 4.4, 0.90) for index in range(8)]
        rows.append(leg('edge', 5, 0.91))
        chosen, _product, _joint = build_slip(rows, 8)
        self.assertEqual(len(chosen), 8)
        self.assertTrue(all(4 <= item['decimal_odds'] <= 5 for item in chosen))

    def test_prices_above_5_stay_at_eight_or_fewer_and_clear_10000(self):
        six = [leg(str(index), 5.4, 0.90) for index in range(6)]
        chosen, product, _joint = build_slip(six, 8, at_most=True, minimum_product=COMBINED_TARGET)
        self.assertEqual(len(chosen), 6)
        self.assertGreater(product, COMBINED_TARGET)
        five = [leg(str(index), 5.4, 0.90) for index in range(5)]
        self.assertIsNone(build_slip(five, 8, at_most=True, minimum_product=COMBINED_TARGET))
        nine = [leg(str(index), 5.5, 0.91, competition=f'League {index}') for index in range(9)]
        chosen, product, _joint = build_slip(nine, 8, at_most=True, minimum_product=COMBINED_TARGET)
        self.assertEqual(len(chosen), 8)
        self.assertGreater(product, COMBINED_TARGET)
        three = [leg(str(index), 25, 0.91) for index in range(3)]
        chosen, product, _joint = build_slip(three, 8, at_most=True, minimum_product=COMBINED_TARGET)
        self.assertEqual(len(chosen), 3)
        self.assertGreater(product, COMBINED_TARGET)

    def test_prices_at_1_95_or_below_use_15_to_30_selections(self):
        twenty = [leg(str(index), 1.8, 0.90, competition=f'League {index}') for index in range(20)]
        chosen, _product, _joint = build_slip(twenty, 30, at_most=True, minimum=15)
        self.assertEqual(len(chosen), 20)
        self.assertTrue(all(item['decimal_odds'] <= 1.95 for item in chosen))
        forty = [leg(str(index), 1.7, 0.90, competition=f'League {index}') for index in range(40)]
        chosen, _product, _joint = build_slip(forty, 30, at_most=True, minimum=15)
        self.assertEqual(len(chosen), 30)
        self.assertIsNone(build_slip([leg(str(index), 1.6, 0.90) for index in range(14)], 30, at_most=True, minimum=15))

    def test_a_selection_is_not_reused_even_if_its_probability_is_100(self):
        day = {'name': 'Saturday', 'start': datetime(2026, 11, 7, tzinfo=timezone.utc)}
        rows = [leg(str(index), 5.5, 0.90, competition=f'League {index}') for index in range(13)]
        rows.append(leg('certain', 6.0, 1, market='HOME'))
        tickets = [ticket for ticket in build_day(rows, day)['tickets'] if ticket['band'] == 'above-5' and ticket['status'] == 'ready']
        self.assertGreaterEqual(len(tickets), 2)
        first = {item['fixture'] for item in tickets[0]['selections']}
        second = {item['fixture'] for item in tickets[1]['selections']}
        self.assertIn('certain vs Opponent', first)
        self.assertNotIn('certain vs Opponent', second)
        self.assertEqual(first & second, set())

    def test_a_ticket_mixes_markets_before_repeating_one(self):
        rows = [leg(f'u{index}', 2.8, 0.90, market='UNDER_4.5', competition='League') for index in range(4)]
        rows.extend(leg(f'o{index}', 2.2, 0.90, market='OVER_1.5', competition='League') for index in range(4))
        chosen, _product, _joint = build_slip(rows, 4)
        families = {item['market'].split('_')[0] for item in chosen}
        self.assertEqual(families, {'UNDER', 'OVER'})

    def test_a_short_pool_is_not_filled(self):
        self.assertIsNone(build_slip([leg(str(index), 2.2, 0.90) for index in range(11)], 12))


if __name__ == '__main__':
    unittest.main()
