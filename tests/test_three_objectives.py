"""The owner's structure (12 Nov 2026): three objectives, no deviation.

1. Real-money betting: singles 1.60-1.99; combined bets are the Sure Odds tickets (2.00-2.95).
2. Sure Odds codes: 2-3 selections, combined 2.00-2.95, at most 5 per day.
3. Category-card codes: every selection priced 1.60-2.95.
"""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

import single_betting as sb
import high_odds_board as board
import high_odds_service as service

NOW = datetime(2026, 11, 12, 9, 0, tzinfo=timezone.utc)
DAY = {'name': 'Thursday', 'start': NOW.replace(hour=0), 'end': NOW.replace(hour=0) + timedelta(days=1), 'date': '2026-11-12'}


def leg(i, odds, p=0.85, sport='football'):
    return {'fixture_id': f'f{i}', 'sport': sport, 'competition': f'L{i}', 'home': f'H{i}', 'away': f'A{i}',
            'market': 'HOME', 'decimal_odds': odds, 'probability': p, 'confidence': 0.9,
            'starts_at': (NOW + timedelta(hours=5)).isoformat(), 'edge': 0.05, 'calibration_error': 0.02}


class ObjectiveOne(unittest.TestCase):
    def test_single_range_is_160_to_199(self):
        ok = lambda odds: sb.evaluate(odds, 0.9, NOW, NOW + timedelta(hours=2), NOW)
        self.assertIn('BELOW_MIN_ODDS', ok(1.59)['reasons'])
        self.assertTrue(ok(1.60)['eligible'])
        self.assertTrue(ok(1.99)['eligible'])
        self.assertIn('ABOVE_MAX_ODDS', ok(2.00)['reasons'])

    def test_combined_bets_are_sure_odds_tickets_only(self):
        self.assertEqual(sb.TICKET_PRODUCTS, ('sure_odds',))
        self.assertEqual((sb.COMBINED_MIN_ODDS, sb.COMBINED_MAX_ODDS), (2.00, 2.95))
        self.assertFalse(sb.ENGINE_COMBOS)


class DailyQuotas(unittest.TestCase):
    """15 a day: 10 singles + 5 Sure Odds combined bets, each product within its own quota."""

    def setUp(self):
        import tempfile
        import db
        self.temp = tempfile.TemporaryDirectory()
        self.previous = db.DB
        db.DB = Path(self.temp.name) / 'q.sqlite3'
        db.initialize()

    def tearDown(self):
        import db
        db.DB = self.previous
        self.temp.cleanup()

    def _bets(self, c, kind, n, status='PLACED'):
        for i in range(n):
            c.execute("""INSERT INTO sports_ai_single_bets(id,fixture_id,sport,competition,home,away,market,selection,
                         decimal_odds,probability,expected_value,stake,mode,status,local_date,starts_at,created_at,kind)
                         VALUES(?,?, 'football','L','H','A','HOME','Home',1.7,0.6,0,100,'PAPER',?,'2026-11-12',?,?,?)""",
                      (f'{kind}{i}', f'fx-{kind}{i}', status, NOW.isoformat(), NOW.isoformat(), kind))

    def test_totals_and_split(self):
        import db
        self.assertEqual((sb.DAILY_LIMIT, sb.DAILY_STAKE_LIMIT, sb.SINGLES_DAILY, sb.COMBINED_DAILY), (15, 1500.0, 10, 5))
        with db.connect() as c:
            self._bets(c, 'FAVOURITE', 10)
            self.assertEqual(sb.room_today(c, '2026-11-12', 'single'), 0)     # singles full ...
            self.assertEqual(sb.room_today(c, '2026-11-12', 'combined'), 5)   # ... combined keep their 5
            self._bets(c, 'TICKET', 2, status='QUEUED')
            self.assertEqual(sb.room_today(c, '2026-11-12', 'combined'), 3)
            self.assertEqual(sb.room_today(c, '2026-11-12'), 3)


class BasketballReserve(unittest.TestCase):
    """3 of the 10 singles are held for basketball (evening games) until 21:00 WAT."""

    def test_reserved_slots_are_kept_for_basketball(self):
        items = [{'sport': 'football', 'id': i} for i in range(12)] + [{'sport': 'basketball', 'id': 'b'}]
        chosen = sb.with_sport_reserve(items, 10, {'basketball': 3})
        self.assertEqual(sum(i['sport'] == 'basketball' for i in chosen), 1)
        self.assertEqual(len(chosen), 8)          # 2 slots stay free for later basketball games
        self.assertEqual(len(sb.with_sport_reserve(items, 10, {})), 10)   # released: any sport

    def test_release_hour_and_usage(self):
        import tempfile, db
        temp = tempfile.TemporaryDirectory()
        previous = db.DB
        db.DB = Path(temp.name) / 'r.sqlite3'
        try:
            db.initialize()
            with db.connect() as c:
                morning = datetime(2026, 11, 13, 8, 0, tzinfo=timezone.utc)      # 09:00 WAT
                self.assertEqual(sb.sport_reserve_left(c, '2026-11-13', morning), {'basketball': 3})
                c.execute("""INSERT INTO sports_ai_single_bets(id,fixture_id,sport,competition,home,away,market,selection,
                             decimal_odds,probability,expected_value,stake,mode,status,local_date,starts_at,created_at,kind)
                             VALUES('b1','fb','basketball','NBA','H','A','MONEYLINE_HOME','Home',1.7,0.58,0,100,'PAPER',
                             'PLACED','2026-11-13',?,?,'FAVOURITE')""", (morning.isoformat(), morning.isoformat()))
                self.assertEqual(sb.sport_reserve_left(c, '2026-11-13', morning), {'basketball': 2})
                night = datetime(2026, 11, 13, 20, 30, tzinfo=timezone.utc)      # 21:30 WAT
                self.assertEqual(sb.sport_reserve_left(c, '2026-11-13', night), {})
        finally:
            db.DB = previous
            temp.cleanup()


class ObjectiveTwo(unittest.TestCase):
    def test_tickets_have_two_or_three_legs_in_range_and_at_most_five(self):
        pool = [leg(i, odds) for i, odds in enumerate([1.15, 1.18, 1.2, 1.25, 1.3, 1.35, 1.4, 1.45, 1.5, 1.55,
                                                         1.6, 1.62, 1.7, 1.25, 1.3, 1.22, 1.28, 1.33, 1.41, 1.44])]
        tickets = board.pack_sure_tickets(pool, DAY)
        self.assertLessEqual(len(tickets), board.SURE_DAILY_MAX)
        self.assertGreater(len(tickets), 0)
        used = set()
        for t in tickets:
            self.assertIn(len(t['legs']), (2, 3))
            self.assertTrue(2.0 - 1e-9 <= t['combined_odds'] <= 2.95 + 1e-9, t['combined_odds'])
            ids = {l['fixture_id'] for l in t['legs']}
            self.assertFalse(ids & used)  # a match is used once
            used |= ids

    def test_short_prices_alone_never_make_a_long_ticket(self):
        # Five 1.15s cannot reach 2.00 within 3 legs: no ticket rather than a 5-leg one.
        self.assertEqual(board.pack_sure_tickets([leg(i, 1.15) for i in range(5)], DAY), [])

    def test_limit_counts_tickets_already_made(self):
        pool = [leg(i, 1.45) for i in range(12)]
        self.assertEqual(len(board.pack_sure_tickets(pool, DAY, limit=2)), 2)
        self.assertEqual(board.pack_sure_tickets(pool, DAY, limit=0), [])


class ObjectiveThree(unittest.TestCase):
    """Category legs 1.60-2.95: see tests/test_high_odds_service.py::test_category_legs_are_priced_160_to_295."""

    def test_limits(self):
        self.assertEqual((service.CATEGORY_LEG_MIN_ODDS, service.CATEGORY_LEG_MAX_ODDS), (1.60, 2.95))


if __name__ == '__main__':
    unittest.main()
