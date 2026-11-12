import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

import view_cache as vc


class ViewCacheTests(unittest.TestCase):
    def setUp(self):
        vc.clear()

    def test_fresh_copy_is_served_without_rebuilding(self):
        calls = []
        build = lambda: calls.append(1) or {'n': len(calls)}
        self.assertEqual(vc.cached_view('k', build, ttl=60)['n'], 1)
        self.assertEqual(vc.cached_view('k', build, ttl=60)['n'], 1)
        self.assertEqual(len(calls), 1)

    def test_stale_copy_is_served_at_once_and_rebuilt_in_the_background(self):
        release = threading.Event()
        calls = []

        def build():
            calls.append(1)
            if len(calls) > 1:
                release.wait(2)
            return {'n': len(calls)}
        vc.cached_view('k', build, ttl=0.05)
        time.sleep(0.1)
        started = time.monotonic()
        self.assertEqual(vc.cached_view('k', build, ttl=0.05)['n'], 1)  # stale copy, no waiting
        self.assertEqual(vc.cached_view('k', build, ttl=0.05)['n'], 1)  # one refresh at a time
        self.assertLess(time.monotonic() - started, 0.5)
        release.set()
        for _ in range(50):
            if vc.cached_view('k', build, ttl=60)['n'] == 2:
                break
            time.sleep(0.02)
        self.assertEqual(vc.cached_view('k', build, ttl=60)['n'], 2)
        self.assertEqual(len(calls), 2)

    def test_failed_refresh_keeps_the_last_good_copy(self):
        state = {'fail': False}

        def build():
            if state['fail']:
                raise RuntimeError('database is locked')
            return {'ok': True}
        vc.cached_view('k', build, ttl=0.01)
        state['fail'] = True
        time.sleep(0.05)
        self.assertTrue(vc.cached_view('k', build, ttl=0.01)['ok'])
        time.sleep(0.1)
        self.assertTrue(vc.cached_view('k', build, ttl=0.01)['ok'])

    def test_payload_reports_its_age(self):
        self.assertIn('cache_age_seconds', vc.cached_view('k', lambda: {'a': 1}))


if __name__ == '__main__':
    unittest.main()
