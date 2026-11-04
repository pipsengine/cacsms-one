import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

from draw_engine import candidate_sort_key, refresh_draw_intelligence, rolling, shrink
import db

NOW = datetime(2026, 11, 4, 9, 0, tzinfo=timezone.utc)


def insert_result(connection, fixture_id, competition, home, away, kickoff, home_score, away_score, status='final'):
    connection.execute(
        """INSERT INTO sports_ai_fixtures
           (id,external_id,source,sport,competition,home,away,starts_at,status,statistics_json,odds_json,observed_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (fixture_id, fixture_id, 'test', 'football', competition, home, away, kickoff, status, '{}', '{}', kickoff),
    )
    if status == 'final':
        connection.execute(
            """INSERT INTO sports_ai_results(id,fixture_id,home_score,away_score,status,source,settled_at)
               VALUES(?,?,?,?,?,?,?)""",
            (f'result-{fixture_id}', fixture_id, home_score, away_score, 'final', 'test', kickoff),
        )


class DrawIntelligenceTests(unittest.TestCase):
    def setUp(self):
        self.previous = db.DB
        self.temp = tempfile.TemporaryDirectory()
        db.DB = Path(self.temp.name) / 'sports.sqlite3'
        db.initialize()

    def tearDown(self):
        db.DB = self.previous
        self.temp.cleanup()

    def test_empty_results_leave_scores_and_half_time_empty(self):
        with db.connect() as connection:
            summary = refresh_draw_intelligence(connection, NOW)
        self.assertEqual(summary['matches'], 0)
        self.assertIsNone(summary['global_draw_rate'])
        self.assertIsNone(summary['halftime'])
        self.assertIn('half-time', summary['halftime_reason'])
        self.assertEqual(summary['validation']['sample_size'], 0)
        self.assertIsNone(summary['validation']['brier_score'])
        self.assertFalse(summary['model']['promoted'])

    def test_stored_full_time_scores_keep_short_windows_and_half_time_empty(self):
        with db.connect() as connection:
            insert_result(connection, 'm1', 'County Cup', 'A', 'B', '2024-08-01T15:00:00+00:00', 0, 0)
            insert_result(connection, 'm2', 'County Cup', 'B', 'A', '2024-09-01T15:00:00+00:00', 1, 1)
            insert_result(connection, 'm3', 'County Cup', 'A', 'C', '2025-08-01T15:00:00+00:00', 2, 2)
            insert_result(connection, 'm4', 'County Cup', 'C', 'A', '2025-09-01T15:00:00+00:00', 2, 1)
            for index in range(8):
                insert_result(connection, f'o{index}', 'Other League', 'D', 'E', f'2025-03-{index + 1:02d}T15:00:00+00:00', 1, 0)
            insert_result(connection, 'next', 'County Cup', 'A', 'B', '2026-11-06T15:00:00+00:00', 0, 0, status='scheduled')
            summary = refresh_draw_intelligence(connection, NOW)
            league = connection.execute("SELECT * FROM sports_ai_draw_leagues WHERE competition='County Cup'").fetchone()
            team = connection.execute("SELECT * FROM sports_ai_draw_teams WHERE team='A'").fetchone()
            fixture = connection.execute("SELECT * FROM sports_ai_draw_fixtures").fetchone()
            decisions = connection.execute('SELECT COUNT(*) AS n FROM sports_ai_qualification_decisions').fetchone()['n']
        self.assertEqual(summary['matches'], 12)
        self.assertEqual(league['draws'], 3)
        self.assertAlmostEqual(league['score_00'], 0.25)
        self.assertAlmostEqual(league['score_11'], 0.25)
        self.assertAlmostEqual(league['score_22'], 0.25)
        self.assertIsNotNone(league['reliability'])
        self.assertLess(league['reliability'], league['draw_rate'])
        self.assertIn('No half-time score is stored.', league['detail_json'])
        self.assertIn('"expected_home_goals": null', league['detail_json'])
        self.assertEqual(team['roll5_matches'], 4)
        self.assertIsNone(team['roll5_rate'])
        self.assertIsNone(team['roll10_rate'])
        self.assertIsNone(team['roll20_rate'])
        self.assertIsNone(team['schedule_strength'])
        self.assertIsNone(team['adjusted_balance'])
        self.assertEqual(team['home_matches'], 2)
        self.assertEqual(team['away_matches'], 2)
        self.assertIsNone(fixture['model_probability'])
        self.assertIsNone(fixture['rank_score'])
        self.assertEqual(fixture['quality'], 'missing_model')
        self.assertIsNone(fixture['qualification_state'])
        self.assertEqual(decisions, 0)
        self.assertEqual(summary['ranked'], 0)

    def test_missing_sample_does_not_invent_a_reliability_score(self):
        self.assertIsNone(shrink(None, 0, 0.27, 30))
        self.assertIsNone(shrink(1, 0, 0.27, 30))
        self.assertIsNone(rolling([], 5)['rate'])
        self.assertFalse(rolling([{'draw': 1}] * 4, 5)['complete'])
        self.assertIsNone(rolling([{'draw': 1}] * 4, 5)['rate'])
        self.assertEqual(rolling([{'draw': 1}] * 5, 5)['rate'], 1)

    def test_rank_uses_value_rather_than_a_historical_draw_rate(self):
        rows = [
            {'fixture_id': 'history', 'rank_score': None, 'model_probability': None, 'calibrated': 0, 'starts_at': '2026-11-05', 'historical_draw_rate': 0.91},
            {'fixture_id': 'priced', 'rank_score': 0.04, 'model_probability': 0.27, 'calibrated': 1, 'starts_at': '2026-11-06', 'historical_draw_rate': 0.11},
            {'fixture_id': 'no-price', 'rank_score': None, 'model_probability': 0.46, 'calibrated': 1, 'starts_at': '2026-11-05', 'historical_draw_rate': 0.80},
        ]
        ordered = [row['fixture_id'] for row in sorted(rows, key=candidate_sort_key)]
        self.assertEqual(ordered[0], 'priced')
        self.assertEqual(ordered[1], 'no-price')
        self.assertEqual(ordered[-1], 'history')
