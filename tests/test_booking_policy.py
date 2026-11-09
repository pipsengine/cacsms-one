"""Booking policy: lapsed qualification, moved prices and missing legs no longer stop a code."""
import asyncio
import json
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

import db
import booking_policy
from sportybet_booking import examine_ready_tickets, execute_job, next_ticket_id, ticket_selections, create_job

# Pistons are shown at 1.95 (stored 1.80); Lakers at 2.40 (stored 2.40); Heat v Knicks is not on the page.
PAGE = """<!doctype html><html><body>
<article><span>Pistons</span><span>76ers</span><button class="price">1.95</button><button class="price">2.07</button></article>
<article><span>Lakers</span><span>Celtics</span><button class="price">2.40</button><button class="price">1.60</button></article>
<button id="place">Place bet</button><button id="book">Book bet</button><div data-booking-code></div>
<script>
document.body.dataset.placed='no';
document.getElementById('place').addEventListener('click', () => { document.body.dataset.placed='yes'; });
document.getElementById('book').addEventListener('click', () => { document.querySelector('[data-booking-code]').textContent='PLCY42'; });
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = PAGE.encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        return


LEGS = [
    ('pistons', 'Pistons', '76ers', 'MONEYLINE_HOME', 1.80, 2),
    ('lakers', 'Lakers', 'Celtics', 'MONEYLINE_HOME', 2.40, 3),
    ('heat', 'Heat', 'Knicks', 'MONEYLINE_HOME', 1.90, 4),
]


class BookingPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f'http://127.0.0.1:{cls.server.server_address[1]}/'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = db.DB
        db.DB = Path(self.temp.name) / 'policy.sqlite3'
        db.initialize()
        self.now = datetime.now(timezone.utc)

    def tearDown(self):
        db.DB = self.previous
        self.temp.cleanup()

    def _ticket(self, c, status='review_required', legs=LEGS, ticket_id='t1'):
        observed = (self.now - timedelta(hours=3)).isoformat()  # stale price, no qualification decisions
        c.execute(
            """INSERT INTO sports_ai_accumulator_tickets
               (id,family_id,version,signature,tier,sport_mode,status,combined_odds,joint_probability,leg_count,
                probability_basis,explanation,parameters_json,created_at)
               VALUES(?,?,1,?,5,'basketball',?,8.21,0.2,?,'independent','test','{"tier_mode":"standard"}',?)""",
            (ticket_id, ticket_id, ticket_id, status, len(legs), self.now.isoformat()),
        )
        for fixture, home, away, market, odds, hours in legs:
            kickoff = (self.now + timedelta(hours=hours)).isoformat()
            c.execute(
                """INSERT OR IGNORE INTO sports_ai_fixtures
                   (id,external_id,source,sport,competition,home,away,starts_at,status,statistics_json,odds_json,observed_at)
                   VALUES(?,?,'test','basketball','NBA',?,?,?,'scheduled','{}','{}',?)""",
                (fixture, fixture, home, away, kickoff, observed),
            )
            c.execute(
                """INSERT INTO sports_ai_accumulator_legs
                   (id,ticket_id,opportunity_id,fixture_id,sport,competition,home,away,starts_at,market,selection,decimal_odds,
                    probability,bookmaker,odds_observed_at,quote_market,quote_outcome,model_id,model_version,settlement_kind,qualification_json)
                   VALUES(?,?,?,?,'basketball','NBA',?,?,?,?,?,?,0.6,'SportyBet',?,?,?,'m','1','binary','{}')""",
                (f'{ticket_id}-{fixture}', ticket_id, f'o-{fixture}', fixture, home, away, kickoff, market, home, odds, observed, market, market),
            )

    def test_defaults_and_validation(self):
        with db.connect() as c:
            policy = booking_policy.load(c)
            self.assertFalse(policy['require_live_qualification'])
            self.assertTrue(policy['accept_displayed_odds'])
            with self.assertRaises(ValueError):
                booking_policy.save(c, {'min_legs': 0})
            with self.assertRaises(ValueError):
                booking_policy.save(c, {'surprise': 1})
            self.assertEqual(booking_policy.save(c, {'request_gap_minutes': 5})['request_gap_minutes'], 5)

    def test_lapsed_and_stale_ticket_is_restored_and_chosen(self):
        with db.connect() as c:
            self._ticket(c)
            summary = examine_ready_tickets(c, self.now, window_open=True)
            self.assertEqual(summary['restored'], 1)
            self.assertEqual(c.execute("SELECT status FROM sports_ai_accumulator_tickets").fetchone()[0], 'qualified')
            self.assertEqual(next_ticket_id(c, self.now), 't1')
            job = create_job(c, ticket_selections(c, 't1'))
            self.assertFalse(job['duplicate'])

    def test_strict_policy_keeps_the_old_gates(self):
        with db.connect() as c:
            booking_policy.save(c, {'require_live_qualification': True, 'accept_displayed_odds': False})
            self._ticket(c)
            examine_ready_tickets(c, self.now, window_open=True)
            self.assertEqual(c.execute("SELECT status FROM sports_ai_accumulator_tickets").fetchone()[0], 'review_required')
            self.assertIsNone(next_ticket_id(c, self.now))
            with self.assertRaises(ValueError):
                create_job(c, ticket_selections(c, 't1'))

    def test_too_few_live_legs_still_holds_the_ticket(self):
        with db.connect() as c:
            self._ticket(c, legs=[LEGS[0], ('gone', 'Old', 'Match', 'MONEYLINE_HOME', 1.5, -1)])
            examine_ready_tickets(c, self.now, window_open=True)
            self.assertEqual(c.execute("SELECT status FROM sports_ai_accumulator_tickets").fetchone()[0], 'expired')

    def test_monitor_drops_a_started_leg_instead_of_expiring(self):
        from accumulator_engine import monitor_accumulators
        with db.connect() as c:
            self._ticket(c, status='qualified', legs=LEGS + [('early', 'Early', 'Bird', 'MONEYLINE_HOME', 1.5, -1)])
            monitor_accumulators(c, 'test', None, 'monitor-1', self.now)
            status, legs = c.execute('SELECT status,leg_count FROM sports_ai_accumulator_tickets').fetchone()
        self.assertEqual((status, legs), ('qualified', 3))

    def test_books_at_displayed_prices_and_drops_the_missing_leg(self):
        with db.connect() as c:
            self._ticket(c, status='qualified')
            job = create_job(c, ticket_selections(c, 't1'))['id']
        result = asyncio.run(execute_job(job, page_url=self.url))
        self.assertEqual((result['status'], result['code']), ('CODE_GENERATED', 'PLCY42'))
        self.assertFalse(result['places_wager'])
        with db.connect() as c:
            legs = {r['fixture_id']: r['decimal_odds'] for r in c.execute('SELECT fixture_id,decimal_odds FROM sports_ai_accumulator_legs')}
            ticket = c.execute('SELECT combined_odds,leg_count FROM sports_ai_accumulator_tickets').fetchone()
            code = c.execute('SELECT code FROM sports_ai_booking_codes').fetchone()
            logs = [json.loads(r[0]) for r in c.execute("SELECT detail_json FROM sports_ai_booking_logs WHERE phase='policy'")]
        self.assertEqual(legs, {'pistons': 1.95, 'lakers': 2.40})
        self.assertEqual((ticket['combined_odds'], ticket['leg_count']), (round(1.95 * 2.40, 2), 2))
        self.assertEqual(code['code'], 'PLCY42')
        self.assertTrue(any('dropped' in entry for entry in logs))
        self.assertTrue(any(entry.get('changes', [{}])[0].get('displayed') == 1.95 for entry in logs if 'changes' in entry))

    def test_too_few_offered_legs_does_not_request_a_code(self):
        with db.connect() as c:
            booking_policy.save(c, {'min_legs': 3})
            self._ticket(c, status='qualified')
            job = create_job(c, ticket_selections(c, 't1'))['id']
        result = asyncio.run(execute_job(job, page_url=self.url))
        self.assertNotEqual(result['status'], 'CODE_GENERATED')
        with db.connect() as c:
            self.assertIsNone(c.execute('SELECT code FROM sports_ai_booking_codes').fetchone())


if __name__ == '__main__':
    unittest.main()
