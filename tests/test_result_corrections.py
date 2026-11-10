import json
import tempfile
import unittest
from pathlib import Path

import db
from result_corrections import apply_self_corrections


class SelfCorrectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = db.DB
        db.DB = Path(self.temp.name) / 'sports.sqlite3'
        db.initialize()

    def tearDown(self):
        db.DB = self.previous
        self.temp.cleanup()

    def _conflict(self, c, fixture='fx', result_source='betexplorer', provider='betexplorer', stored=(127, 126), incoming=(129, 126)):
        c.execute("""INSERT INTO sports_ai_fixtures(id,external_id,source,sport,competition,home,away,starts_at,status,statistics_json,odds_json,observed_at)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (fixture, fixture, 'betexplorer', 'basketball', 'G League', 'Clippers', 'Suns',
                   '2026-11-07T03:00:00+00:00', 'finished', '{}', '{}', '2026-11-07T03:00:00+00:00'))
        c.execute("""INSERT INTO sports_ai_results(id,fixture_id,home_score,away_score,status,source,settled_at)
                     VALUES(?,?,?,?,'final',?,?)""", ('r-' + fixture, fixture, *stored, result_source, '2026-11-07T11:00:00+00:00'))
        c.execute("""INSERT INTO sports_ai_data_quality_issues(id,fixture_id,provider_id,severity,issue_type,details_json)
                     VALUES(?,?,NULL,'high','result_conflict',?)""",
                  ('i-' + fixture, fixture, json.dumps({'stored': list(stored), 'incoming': list(incoming), 'provider_key': provider})))

    def _state(self, c, fixture='fx'):
        score = tuple(c.execute('SELECT home_score, away_score FROM sports_ai_results WHERE fixture_id=?', (fixture,)).fetchone())
        open_issue = c.execute('SELECT resolved_at FROM sports_ai_data_quality_issues WHERE fixture_id=?', (fixture,)).fetchone()[0] is None
        return score, open_issue

    def test_same_provider_correction_is_applied_and_closed(self):
        with db.connect() as c:
            self._conflict(c)
            result = apply_self_corrections(c)
            self.assertEqual(len(result['applied']), 1)
            self.assertEqual(self._state(c), ((129, 126), False))
            audit = c.execute("SELECT metadata_json FROM sports_ai_audit_events WHERE action='result.self_correction'").fetchone()
            self.assertEqual(json.loads(audit[0])['from'], [127, 126])

    def test_different_providers_wait_for_an_operator(self):
        with db.connect() as c:
            self._conflict(c, result_source='openligadb', provider='betexplorer')
            self.assertEqual(apply_self_corrections(c)['applied'], [])
            self.assertEqual(self._state(c), ((127, 126), True))

    def test_graded_fixture_is_held(self):
        with db.connect() as c:
            c.execute('PRAGMA foreign_keys=OFF')
            self._conflict(c)
            c.execute("""INSERT INTO sports_ai_single_bets(id,fixture_id,sport,home,away,market,selection,decimal_odds,probability,
                         expected_value,stake,mode,status,local_date,starts_at,created_at)
                         VALUES('b','fx','basketball','Clippers','Suns','MONEYLINE_HOME','Home',1.8,0.6,0.08,100,'PAPER','WON','2026-11-07','2026-11-07T03:00:00+00:00','2026-11-06')""")
            result = apply_self_corrections(c)
            self.assertEqual(result['applied'], [])
            self.assertEqual(result['held'][0]['graded_in'], 'sports_ai_single_bets')
            self.assertEqual(self._state(c), ((127, 126), True))


if __name__ == '__main__':
    unittest.main()
