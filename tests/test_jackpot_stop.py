import asyncio
import json
import sqlite3
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import jackpot_low
import prize_ladder
import high_odds_service
import sportybet_booking
from jackpot_stop import is_stopped, require_enabled
from single_betting import _recheck_ticket


class JackpotStopTests(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(':memory:')
        self.c.row_factory = sqlite3.Row
        self.c.executescript('''
          CREATE TABLE sports_ai_accumulator_tickets(id TEXT PRIMARY KEY,parameters_json TEXT);
          CREATE TABLE sports_ai_high_odds_tickets(ticket_id TEXT,category_id TEXT);
          CREATE TABLE sports_ai_booking_jobs(id TEXT,selections_json TEXT,status TEXT,updated_at TEXT,error TEXT,reason_code TEXT);
        ''')
        for product in ('jackpot_low', 'prize_ladder', 'sure_odds', 'autonomous_high_odds'):
            self.c.execute('INSERT INTO sports_ai_accumulator_tickets VALUES(?,?)', (product, json.dumps({'product': product})))
        self.c.execute("INSERT INTO sports_ai_high_odds_tickets VALUES('autonomous_high_odds','jackpot')")
        self.c.commit()

    def tearDown(self):
        self.c.close()

    def test_retired_entry_points_stay_off_even_when_forced(self):
        # The weekly jackpot ladder (prize_ladder) was re-enabled by the owner on 12 Nov 2026;
        # the low-odds jackpot and the single-tier jackpot category stay off.
        self.assertEqual(jackpot_low.build(None, force=True)['status'], 'disabled')
        self.assertIsNone(high_odds_service.persist_ticket(None, None, {'id': 'jackpot'}, None, None, None))

    def test_retired_jackpot_products_and_optimizer_category_are_stopped(self):
        self.assertFalse(is_stopped(self.c, 'prize_ladder'))   # the weekly jackpot ladder is enabled
        require_enabled(self.c, 'prize_ladder')
        for ticket in ('jackpot_low', 'autonomous_high_odds'):
            self.assertTrue(is_stopped(self.c, ticket))
            with self.assertRaisesRegex(ValueError, 'disabled by the operator'):
                require_enabled(self.c, ticket)
        require_enabled(self.c, 'sure_odds')

    def test_new_jobs_and_late_code_results_are_rejected(self):
        for ticket in ('jackpot_low', 'autonomous_high_odds'):
            with self.assertRaisesRegex(ValueError, 'disabled by the operator'):
                sportybet_booking.create_job(self.c, [{'ticket_id': ticket}])
            with self.assertRaisesRegex(ValueError, 'disabled by the operator'):
                sportybet_booking._store_displayed_code(self.c, ticket, 'TESTONLY', 'test')

    def test_already_queued_job_is_cancelled_before_opening_browser(self):
        self.c.execute('INSERT INTO sports_ai_booking_jobs(id,selections_json,status) VALUES(?,?,?)',
                       ('job', '[{"ticket_id":"jackpot_low"}]', 'QUEUED'))
        self.c.commit()
        @contextmanager
        def connected(*args, **kwargs):
            yield self.c
            self.c.commit()
        with patch.object(sportybet_booking, 'connect', connected), patch('playwright.async_api.async_playwright') as browser:
            result = asyncio.run(sportybet_booking._execute_job('job'))
            self.assertEqual(result['status'], 'SUPERSEDED')
            browser.assert_not_called()
        self.assertEqual(self.c.execute('SELECT reason_code FROM sports_ai_booking_jobs').fetchone()[0], 'OPERATOR_DISABLED')

    def test_existing_followed_ticket_cannot_enter_betting(self):
        fresh, reason = _recheck_ticket(self.c, {'id': 'ticket:jackpot_low'}, None)
        self.assertIsNone(fresh)
        self.assertIn('disabled by the operator', reason)

    def test_jackpot_booking_gate_is_closed(self):
        with self.assertRaisesRegex(ValueError, 'disabled by the operator'):
            jackpot_low.require_bookable(None, [])
