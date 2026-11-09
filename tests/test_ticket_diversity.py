import json
import sys
import tempfile
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import db
from high_odds_board import _ready_ticket, _upsert_ticket
from ticket_diversity import TicketExposureConflict, assert_available, event_keys, reconcile, unallocated


class TicketDiversityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = db.DB
        db.DB = Path(self.temp.name) / 'diversity.sqlite3'
        db.initialize()
        with db.connect() as _c:
            __import__('booking_policy').save(_c, __import__('booking_policy').STRICT)  # these tests cover the strict gates
        self.now = datetime.now(timezone.utc)
        start = self.now.replace(hour=0, minute=0, second=0, microsecond=0)
        self.day = {'name': 'Today', 'date': start.date().isoformat(), 'start': start, 'end': start+timedelta(days=2)}

    def tearDown(self):
        db.DB = self.previous
        self.temp.cleanup()

    def leg(self, name, market='HOME'):
        return {'fixture_id': name, 'market': market, 'home': f'Home {name}', 'away': f'Away {name}',
                'starts_at': (self.now+timedelta(hours=4)).isoformat(), 'sport': 'football', 'competition': 'League',
                'decimal_odds': 1.5, 'probability': .9, 'confidence': .9, 'preferred': True,
                'opportunity_id': name, 'selection': 'home', 'odds_observed_at': self.now.isoformat()}

    def store(self, c, legs, product='sure_odds'):
        ticket = _ready_ticket(self.day, {'id': '2-2.95', 'odds_label': '2–2.95'}, legs, 2.25, .81)
        return _upsert_ticket(c, self.day, ticket, self.now, product)

    def legacy_duplicate(self, c, ticket_id):
        identifier = str(uuid.uuid4())
        row = dict(c.execute('SELECT * FROM sports_ai_accumulator_tickets WHERE id=?', (ticket_id,)).fetchone())
        row.update({'id': identifier, 'family_id': identifier, 'signature': identifier})
        cols = list(row)
        c.execute(f"INSERT INTO sports_ai_accumulator_tickets({','.join(cols)}) VALUES({','.join('?' for _ in cols)})", [row[col] for col in cols])
        for raw in c.execute('SELECT * FROM sports_ai_accumulator_legs WHERE ticket_id=?', (ticket_id,)).fetchall():
            leg = dict(raw); leg.update({'id': str(uuid.uuid4()), 'ticket_id': identifier})
            cols = list(leg)
            c.execute(f"INSERT INTO sports_ai_accumulator_legs({','.join(cols)}) VALUES({','.join('?' for _ in cols)})", [leg[col] for col in cols])
        return identifier

    def test_other_market_on_a_reserved_match_cannot_be_used(self):
        with db.connect() as c:
            ticket_id = self.store(c, [self.leg('a'), self.leg('b')])
            self.assertTrue(ticket_id)
            self.assertEqual(unallocated(c, [self.leg('a', 'UNDER_4.5'), self.leg('c')], self.now), [self.leg('c')])
            with self.assertRaises(TicketExposureConflict):
                assert_available(c, [self.leg('a', 'OVER_1.5')], self.now)

    def test_reservation_is_shared_across_products_and_refreshes(self):
        with db.connect() as c:
            first = self.store(c, [self.leg('a'), self.leg('b')])
            self.assertIsNone(self.store(c, [self.leg('a', 'UNDER_4.5'), self.leg('c')], 'high_odds'))
            self.assertEqual(self.store(c, [self.leg('a'), self.leg('b')]), first)
            self.assertTrue(self.store(c, [self.leg('c'), self.leg('d')]))

    def test_alias_fixture_ids_do_not_bypass_event_exposure(self):
        with db.connect() as c:
            original = self.leg('a')
            self.store(c, [original, self.leg('b')])
            alias = {**original, 'fixture_id': 'provider-alias', 'market': 'AWAY'}
            self.assertEqual(unallocated(c, [alias], self.now), [])

    def test_initials_and_team_numbers_are_distinct(self):
        a, b = self.leg('a'), self.leg('b')
        self.assertFalse(event_keys(a) & event_keys(b))
        first = {**a, 'fixture_id': '1', 'home': 'Team 1', 'away': 'Team 2'}
        second = {**a, 'fixture_id': '2', 'home': 'Team 3', 'away': 'Team 4'}
        self.assertFalse(event_keys(first) & event_keys(second))

    def test_reconcile_retires_overlap_and_preserves_codes_and_legs(self):
        with db.connect() as c:
            first = self.store(c, [self.leg('a'), self.leg('b')])
            duplicate = self.legacy_duplicate(c, first)
            c.execute("""INSERT INTO sports_ai_booking_codes(id,accumulator_ticket_id,code,verification_method,evidence,status,bookmaker,verified_at,integration_status)
                         VALUES('evidence',?,'KEEP123','displayed','screenshot','verified','SportyBet',?,'displayed')""", (first, self.now.isoformat()))
            count = c.execute('SELECT COUNT(*) FROM sports_ai_accumulator_legs').fetchone()[0]
            result = reconcile(c, self.now)
            self.assertEqual(result['retired_count'], 1)
            row = c.execute('SELECT status,parameters_json FROM sports_ai_accumulator_tickets WHERE id=?', (duplicate,)).fetchone()
            self.assertEqual(row['status'], 'expired')
            self.assertTrue(json.loads(row['parameters_json'])['diversity_retired'])
            self.assertEqual(c.execute('SELECT COUNT(*) FROM sports_ai_accumulator_legs').fetchone()[0], count)
            self.assertEqual(c.execute("SELECT code,status FROM sports_ai_booking_codes WHERE id='evidence'").fetchone()['status'], 'verified')
            self.assertEqual(reconcile(c, self.now)['retired_count'], 0)

    def test_competing_writers_cannot_allocate_the_same_match(self):
        barrier = threading.Barrier(2)
        def build(market):
            with db.connect() as c:
                barrier.wait(timeout=10)
                return self.store(c, [self.leg('a', market), self.leg('b', market)])
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(build, ['HOME', 'UNDER_4.5']))
        self.assertEqual(sum(bool(result) for result in results), 1)

    def test_released_ticket_does_not_permanently_lock_a_match(self):
        with db.connect() as c:
            first = self.store(c, [self.leg('a'), self.leg('b')])
            c.execute("UPDATE sports_ai_accumulator_tickets SET status='void' WHERE id=?", (first,))
            self.assertEqual(len(unallocated(c, [self.leg('a')], self.now)), 1)


if __name__ == '__main__':
    unittest.main()
