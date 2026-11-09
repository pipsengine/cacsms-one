"""Phase 2: shared browser slots, transient-failure recovery and stage recovery cases."""
import asyncio
import json
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

import db
import browser_slots
from browser_slots import BrowserBusy, browser_slot, classify_browser_error, slot_status, try_acquire, release


class SlotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = db.DB
        db.DB = Path(self.temp.name) / 'slots.sqlite3'
        db.initialize()

    def tearDown(self):
        db.DB = self.previous
        self.temp.cleanup()

    def test_two_slots_then_busy(self):
        first, second = try_acquire('a'), try_acquire('b')
        self.assertTrue(first and second)
        self.assertIsNone(try_acquire('c'))
        release(first)
        self.assertIsNotNone(try_acquire('c'))

    def test_expired_lease_is_reclaimed(self):
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        try_acquire('crashed-1', ttl=60, now=past)
        try_acquire('crashed-2', ttl=60, now=past)
        self.assertIsNotNone(try_acquire('fresh'))

    def test_context_manager_waits_then_raises_busy(self):
        held = [try_acquire('x'), try_acquire('y')]
        with self.assertRaises(BrowserBusy):
            with browser_slot('z', wait=0.3, poll=0.1):
                pass
        for owner in held:
            release(owner)
        with browser_slot('z', wait=0.3, poll=0.1):
            with db.connect() as c:
                self.assertEqual(sum(s['busy'] for s in slot_status(c)), 1)
        with db.connect() as c:
            self.assertEqual(sum(s['busy'] for s in slot_status(c)), 0)

    def test_slot_released_when_work_raises(self):
        with self.assertRaises(ValueError):
            with browser_slot('boom', wait=1):
                raise ValueError('page broke')
        with db.connect() as c:
            self.assertFalse(any(s['busy'] for s in slot_status(c)))

    def test_concurrent_threads_never_exceed_two(self):
        active, peak, lock = [0], [0], threading.Lock()

        def work():
            with browser_slot('t', wait=10, poll=0.05):
                with lock:
                    active[0] += 1
                    peak[0] = max(peak[0], active[0])
                threading.Event().wait(0.15)
                with lock:
                    active[0] -= 1
        threads = [threading.Thread(target=work) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(peak[0], 2)

    def test_async_decorator(self):
        @browser_slots.holds_browser_slot('async-test', wait=1)
        async def job():
            with db.connect() as c:
                return sum(s['busy'] for s in slot_status(c))
        self.assertEqual(asyncio.run(job()), 1)


class ClassificationTests(unittest.TestCase):
    def test_classes(self):
        self.assertEqual(classify_browser_error('Page.goto: net::ERR_NAME_NOT_RESOLVED at https://x'), 'transient')
        self.assertEqual(classify_browser_error(RuntimeError('Page.goto: Timeout 45000ms exceeded.')), 'transient')
        self.assertEqual(classify_browser_error(BrowserBusy('all busy')), 'transient')
        self.assertEqual(classify_browser_error("BrowserType.launch: Executable doesn't exist at C:/x"), 'install')
        self.assertEqual(classify_browser_error('Selection control missing or ambiguous: A vs B'), 'permanent')


class BookingRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = db.DB
        db.DB = Path(self.temp.name) / 'booking.sqlite3'
        db.initialize()
        self.job = 'job-1'
        legs = [{'fixture_id': 'fx', 'sport': 'football', 'competition': 'L', 'home': 'A', 'away': 'B',
                 'market': 'HOME', 'selection': 'A', 'decimal_odds': 1.8}]
        moment = datetime.now(timezone.utc).isoformat()
        with db.connect() as c:
            c.execute(
                """INSERT INTO sports_ai_booking_jobs(id,created_at,updated_at,status,sport,signature,selections_json,attempt,max_attempts)
                   VALUES(?,?,?,'QUEUED','football','fx|HOME|A',?,0,3)""", (self.job, moment, moment, json.dumps(legs)),
            )

    def tearDown(self):
        db.DB = self.previous
        self.temp.cleanup()

    def _job(self):
        with db.connect() as c:
            return dict(c.execute('SELECT status,attempt,reason_code FROM sports_ai_booking_jobs WHERE id=?', (self.job,)).fetchone())

    def test_busy_browser_leaves_job_queued_without_an_attempt(self):
        from sportybet_booking import execute_job
        held = [try_acquire('x'), try_acquire('y')]
        result = asyncio.run(execute_job(self.job, slot_wait=0.2))
        for owner in held:
            release(owner)
        self.assertEqual((result['status'], result['reason_code']), ('QUEUED', 'BROWSER_BUSY'))
        self.assertEqual(self._job()['status'], 'QUEUED')
        self.assertEqual(self._job()['attempt'], 0)

    def test_network_outage_requeues_and_refunds_the_attempt(self):
        import sportybet_booking

        async def offline(*args, **kwargs):
            raise RuntimeError('Page.goto: net::ERR_NAME_NOT_RESOLVED at https://www.sportybet.com/ng/sport/football')
        with patch.object(sportybet_booking, '_open_sport', offline):
            result = asyncio.run(sportybet_booking.execute_job(self.job))
        self.assertEqual(result['status'], 'QUEUED')
        job = self._job()
        self.assertEqual((job['status'], job['attempt'], job['reason_code']), ('QUEUED', 0, 'NETWORK_UNAVAILABLE'))

    def test_page_errors_still_fail(self):
        import sportybet_booking

        async def missing(*args, **kwargs):
            raise RuntimeError('Selection control missing or ambiguous: A vs B (league control: none)')
        with patch.object(sportybet_booking, '_open_sport', missing):
            result = asyncio.run(sportybet_booking.execute_job(self.job))
        self.assertEqual(result['status'], 'FAILED')
        self.assertEqual(self._job()['reason_code'], 'FIXTURE_NOT_FOUND')


class StageRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = db.DB
        db.DB = Path(self.temp.name) / 'stages.sqlite3'
        db.initialize()

    def tearDown(self):
        db.DB = self.previous
        self.temp.cleanup()

    def _cases(self):
        with db.connect() as c:
            return [dict(r) for r in c.execute("SELECT * FROM sports_ai_recovery_cases WHERE error_code='STAGE_FAILED:tickets'")]

    def test_worker_records_the_error_and_the_case_lifecycle(self):
        import worker
        try:
            raise KeyError('combined_odds')
        except KeyError as error:
            for _ in range(5):
                worker._observed('tickets', {'status': 'failed'}, failed=True, error=error)
        with db.connect() as c:
            detail = json.loads(c.execute("SELECT detail_json FROM sports_ai_autonomous_cycles WHERE stage='tickets'").fetchone()[0])
        self.assertEqual(detail['error_type'], 'KeyError')
        self.assertIn('combined_odds', detail['error'])
        self.assertTrue(detail['where'])
        case, = self._cases()
        self.assertEqual((case['status'], case['attempt_count']), ('escalated', 5))
        self.assertIn('combined_odds', json.loads(case['details_json'])['error'])
        worker._observed('tickets', {'status': 'complete'})
        case, = self._cases()
        self.assertEqual(case['status'], 'resolved')


if __name__ == '__main__':
    unittest.main()
