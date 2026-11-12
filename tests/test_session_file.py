"""The saved SportyBet session survives concurrent saves and loads (API and worker save after
every browser visit). A half-written file once read as "no session saved" and cancelled a real bet."""
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

import sportybet_account as acct


class SessionFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'SPORTS_SECRETS_DIR': self.temp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_round_trip(self):
        acct.save_session({'cookies': [{'name': 'a', 'value': '1'}]})
        self.assertEqual(acct.load_session()['cookies'][0]['value'], '1')

    def test_concurrent_saves_never_make_a_load_see_no_session(self):
        big = {'cookies': [{'name': f'c{i}', 'value': 'x' * 400} for i in range(200)]}
        acct.save_session(big)
        stop, misses, errors = threading.Event(), [], []

        def writer():
            while not stop.is_set():
                try:
                    acct.save_session(big)
                except Exception as error:  # pragma: no cover - reported below
                    errors.append(error)

        def reader():
            for _ in range(150):
                if acct.load_session() is None:
                    misses.append(1)
        writers = [threading.Thread(target=writer) for _ in range(2)]
        readers = [threading.Thread(target=reader) for _ in range(3)]
        for t in writers + readers:
            t.start()
        for t in readers:
            t.join()
        stop.set()
        for t in writers:
            t.join()
        self.assertEqual((len(misses), errors), (0, []))
        self.assertEqual(list(Path(self.temp.name).glob('*.tmp')), [])

    def test_missing_file_is_no_session(self):
        self.assertIsNone(acct.load_session())


if __name__ == '__main__':
    unittest.main()
