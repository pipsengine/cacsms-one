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


    def test_batch_books_every_ready_ticket_without_waiting_for_the_gap(self):
        from unittest.mock import patch
        import sportybet_booking
        booked = []

        async def fake_execute(job_id, page_url=None, slot_wait=60):
            with db.connect() as c:
                c.execute("UPDATE sports_ai_booking_jobs SET status='CODE_GENERATED',updated_at=? WHERE id=?",
                          (datetime.now(timezone.utc).isoformat(), job_id))
            booked.append(job_id)
            return {'id': job_id, 'status': 'CODE_GENERATED', 'code': f'CODE{len(booked)}X', 'places_wager': False}
        with db.connect() as c:
            for index in range(3):
                legs = [(f'{fx}{index}', f'{home}{index}', away, market, odds, hours) for fx, home, away, market, odds, hours in LEGS[:2]]
                self._ticket(c, status='qualified', legs=legs, ticket_id=f't{index}')
        with patch.object(sportybet_booking, 'execute_job', fake_execute):
            result = sportybet_booking.book_batch(max_codes=5)
            again = sportybet_booking.book_batch(max_codes=5)
        self.assertEqual(result['codes'], ['CODE1X', 'CODE2X', 'CODE3X'])
        self.assertEqual(result['attempts'], 4)  # the fourth finds nothing ready
        self.assertEqual((again['codes'], again['attempts']), ([], 1))  # inside the gap: no request

    def test_default_batch_meets_five_codes_per_fifteen_minutes(self):
        with db.connect() as c:
            policy = booking_policy.load(c)
        self.assertGreaterEqual(policy['batch_size'] * (15 // policy['request_gap_minutes']), 5)


    def test_codes_from_standard_tickets_show_on_the_category_dashboard(self):
        import high_odds_service
        with db.connect() as c:
            self._ticket(c, status='qualified')  # 1.80 x 2.40 x 1.90 = 8.21 -> Low Odds
            c.execute('UPDATE sports_ai_accumulator_tickets SET combined_odds=8.21')
            for index in range(2):  # the same code stored twice is shown once
                c.execute(
                    """INSERT INTO sports_ai_booking_codes
                       (id,accumulator_ticket_id,code,verification_method,evidence,status,bookmaker,verified_at,integration_status,created_at)
                       VALUES(?,'t1','DASH01','displayed','x','verified','SportyBet',?,'displayed',?)""",
                    (f'code-{index}', self.now.isoformat(), self.now.isoformat()),
                )
            view = high_odds_service.dashboard(c, self.now)
        shown = [t for t in view['tickets'] if t.get('booking_code') == 'DASH01']
        self.assertEqual(len(shown), 1)
        self.assertEqual((shown[0]['category_id'], shown[0]['registration_state'], shown[0]['leg_count']), ('5-9.99', 'registered', 3))
        low = next(c for c in view['categories'] if c['id'] == '5-9.99')
        self.assertEqual(low['daily_verified'], 1)


if __name__ == '__main__':
    unittest.main()


class SureOddsTodayTests(unittest.TestCase):
    """The daily sure-odds product works on weekdays and follows the booking policy."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = db.DB
        db.DB = Path(self.temp.name) / 'sure.sqlite3'
        db.initialize()
        self.monday = datetime(2026, 11, 9, 10, tzinfo=timezone.utc)

    def tearDown(self):
        db.DB = self.previous
        self.temp.cleanup()

    def _legs(self):
        def leg(fixture, odds):
            return {'fixture_id': fixture, 'home': fixture, 'away': 'Opp', 'sport': 'basketball', 'competition': 'L',
                    'market': 'MONEYLINE_HOME', 'starts_at': '2026-11-09T19:00:00+00:00', 'decimal_odds': odds,
                    'probability': 0.85, 'confidence': 0.9, 'preferred': True}
        return [leg('a', 1.30), leg('b', 1.70), leg('c', 1.09)]

    def test_today_window_is_today_on_a_weekday(self):
        import high_odds_board as hb
        day, = hb._today_window(self.monday)
        self.assertEqual((day['name'], day['date']), ('Monday', '2026-11-09'))
        self.assertNotIn('Monday', [d['name'] for d in hb._day_window(self.monday)])

    def test_weekday_board_builds_a_ticket_from_shared_matches_under_the_policy(self):
        from unittest.mock import patch
        import high_odds_board as hb
        with patch.object(hb, '_candidates', lambda c, day, now: self._legs()), \
                patch('ticket_diversity.unallocated', lambda c, rows, now, *a: rows[2:]):
            with db.connect() as c:
                board = hb.sure_odds_board(c, self.monday)
                tickets = board['sure_tickets']
                self.assertEqual(len(tickets), 1)
                self.assertTrue(2.0 <= tickets[0]['combined_odds'] <= 2.95)
                booking_policy.save(c, booking_policy.STRICT)  # strict keeps one match per ticket
                self.assertEqual(hb.sure_odds_board(c, self.monday)['sure_tickets'], [])


class SureOddsDuplicateTests(SureOddsTodayTests):
    def test_stored_ticket_is_not_repeated_as_a_new_preview(self):
        from unittest.mock import patch
        import high_odds_board as hb
        stored = {'signature': 'sure-odds|Monday|2-2.95|a|b|c', 'legs': [{'fixture_id': f} for f in 'abc'], 'status': 'ready'}
        with patch.object(hb, '_candidates', lambda c, day, now: self._legs()),                 patch.object(hb, '_stored_slips', lambda c, day, product: [dict(stored)] if product == 'sure_odds' else []):
            with db.connect() as c:
                tickets = hb.sure_odds_board(c, self.monday)['sure_tickets']
        self.assertEqual(len(tickets), 1)


class WrongSelectionGuardTests(unittest.TestCase):
    """A displayed price far from the stored one is not clicked."""
    HTML = ('<article><span>Pistons</span><span>76ers</span><button>1.15</button><button>2.07</button></article>'
            '<article><span>Lakers</span><span>Celtics</span><button>2.50</button><button>1.60</button></article>')

    def _run(self, **kwargs):
        from playwright.async_api import async_playwright
        from sportybet_booking import prepare_slip
        legs = [{'fixture_id': 'p', 'home': 'Pistons', 'away': '76ers', 'market': 'MONEYLINE_HOME', 'selection': 'Pistons', 'decimal_odds': 1.87},
                {'fixture_id': 'l', 'home': 'Lakers', 'away': 'Celtics', 'market': 'MONEYLINE_HOME', 'selection': 'Lakers', 'decimal_odds': 2.40}]

        async def go():
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                try:
                    page = await browser.new_page()
                    await page.set_content(self.HTML)
                    return await prepare_slip(page, legs, book=False, **kwargs)
                finally:
                    await browser.close()
        return asyncio.run(go())

    def test_large_gap_is_dropped_and_small_move_is_booked(self):
        result = self._run(skip_unpriced=True, max_move=0.30)
        self.assertEqual([s['fixture_id'] for s in result['seen']], ['l'])  # 2.40 -> 2.50 is a real move
        self.assertEqual(result['skipped'][0]['_rejected_price'], 1.15)  # 1.87 -> 1.15 is not clicked

    def test_without_dropping_the_gap_stops_the_booking(self):
        result = self._run(skip_unpriced=False, max_move=0.30)
        self.assertEqual(result['status'], 'ODDS_CHANGED')
        self.assertIn('probably a different selection', result['error'])
