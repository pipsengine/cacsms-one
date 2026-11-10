import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

import db
import single_betting as sb

NOW = datetime(2026, 11, 10, 14, 0, tzinfo=timezone.utc)


def seed(connection, fixture_id, market='HOME', odds=1.72, probability=0.645, kickoff=NOW + timedelta(hours=3),
         observed=NOW - timedelta(minutes=2), qualified=True, sport='football'):
    """Minimal rows for one qualified selection with a SportyBet quote. FK checks are off for brevity."""
    connection.execute(
        """INSERT INTO sports_ai_fixtures
           (id,external_id,source,sport,competition,home,away,starts_at,status,statistics_json,odds_json,observed_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (fixture_id, fixture_id, 'test', sport, 'Primeira Liga', f'{fixture_id}-H', f'{fixture_id}-A',
         kickoff.isoformat(), 'scheduled', '{}', '{}', NOW.isoformat()),
    )
    prediction = f'p-{fixture_id}-{market}'
    connection.execute(
        """INSERT INTO sports_ai_engine_predictions
           (id,run_id,fixture_id,model_id,feature_id,market,outcome,probability,settlement_json,explanation_json)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (prediction, 'run', fixture_id, 'model', 'feature', market, market, probability, '{}', '{}'),
    )
    quote = f'q-{fixture_id}-{market}'
    connection.execute(
        """INSERT INTO sports_ai_odds_snapshots
           (id,fixture_id,provider_id,bookmaker,market,outcome,line,decimal_odds,source_url,observed_at)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (quote, fixture_id, 'provider', 'SportyBet', market, market, None, odds, 'https://example.test', observed.isoformat()),
    )
    connection.execute(
        """INSERT INTO sports_ai_value_opportunities
           (id,prediction_id,odds_id,implied_probability,expected_edge,status,reason,evaluated_at)
           VALUES(?,?,?,?,?,?,?,?)""",
        (f'o-{fixture_id}-{market}', prediction, quote, 1 / odds, odds * probability - 1, 'qualified', 'test', NOW.isoformat()),
    )
    connection.execute(
        """INSERT INTO sports_ai_qualification_decisions
           (id,prediction_id,fixture_id,sport,competition,market,state,reason_code,reason,evidence_json,products_json,
            rank_score,policy_version,decision_version,fingerprint,starts_at,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (f'd-{fixture_id}-{market}', prediction, fixture_id, sport, 'Primeira Liga', market,
         'QUALIFIED' if qualified else 'REJECTED', 'OK', 'ok', json.dumps({'probability': probability}),
         json.dumps(['booking'] if qualified else []), 1.0, 1, 1, 'fp', kickoff.isoformat(), NOW.isoformat(), NOW.isoformat()),
    )


def result(connection, fixture_id, home, away):
    connection.execute(
        """INSERT INTO sports_ai_results(id,fixture_id,home_score,away_score,status,source,settled_at)
           VALUES(?,?,?,?,?,?,?)""",
        (f'r-{fixture_id}', fixture_id, home, away, 'final', 'test', NOW.isoformat()),
    )


class SingleBettingTests(unittest.TestCase):
    def setUp(self):
        self.previous = db.DB
        self.temp = tempfile.TemporaryDirectory()
        db.DB = Path(self.temp.name) / 'sports.sqlite3'
        db.initialize()

    def tearDown(self):
        db.DB = self.previous
        self.temp.cleanup()

    def conn(self):
        return db.connect()

    def test_policy_rejects_stale_close_and_negative_edge(self):
        ok = sb.evaluate(1.72, 0.645, NOW - timedelta(minutes=1), NOW + timedelta(hours=1), NOW)
        self.assertTrue(ok['eligible'])
        self.assertAlmostEqual(ok['ev'], 0.1094, places=3)
        self.assertIn('STALE_ODDS', sb.evaluate(1.72, 0.645, NOW - timedelta(hours=1), NOW + timedelta(hours=1), NOW)['reasons'])
        self.assertIn('EVENT_TOO_CLOSE', sb.evaluate(1.72, 0.645, NOW, NOW + timedelta(seconds=30), NOW)['reasons'])
        self.assertIn('INSUFFICIENT_EDGE', sb.evaluate(1.50, 0.60, NOW, NOW + timedelta(hours=1), NOW)['reasons'])

    def test_capacity_respects_count_and_stake_caps(self):
        self.assertEqual(sb.capacity(0, 0), 25)
        self.assertEqual(sb.capacity(24, 2400), 1)
        self.assertEqual(sb.capacity(25, 2500), 0)
        self.assertEqual(sb.capacity(3, 2450), 0)

    def test_queue_then_paced_placement_then_settlement(self):
        with self.conn() as c:
            c.execute('PRAGMA foreign_keys=OFF')
            for i in range(3):
                seed(c, f'fx{i}')
            seed(c, 'bad-edge', odds=1.40, probability=0.6)
            seed(c, 'not-qualified', qualified=False)
            first = sb.run_cycle(c, NOW)
            self.assertEqual(first['queued'], 3)
            self.assertEqual(first['placed'], 0)
            self.assertFalse(first['places_wager'])
            second = sb.run_cycle(c, NOW + timedelta(seconds=30))
            self.assertEqual(second['placed'], sb.PLACE_PER_TICK)
            self.assertEqual(second['queued'], 0)  # no fixture is backed twice
            sb.run_cycle(c, NOW + timedelta(seconds=60))
            statuses = [r[0] for r in c.execute('SELECT status FROM sports_ai_single_bets')]
            self.assertEqual(statuses, ['PLACED'] * 3)
            result(c, 'fx0', 2, 0)
            result(c, 'fx1', 0, 1)
            sb.run_cycle(c, NOW + timedelta(hours=5))
            rows = {r['fixture_id']: r for r in c.execute('SELECT * FROM sports_ai_single_bets')}
            self.assertEqual(rows['fx0']['status'], 'WON')
            self.assertAlmostEqual(rows['fx0']['profit'], 72.0)
            self.assertEqual(rows['fx1']['status'], 'LOST')
            self.assertAlmostEqual(rows['fx1']['profit'], -100.0)
            view = sb.dashboard(c, NOW + timedelta(hours=5))
            self.assertEqual(view['today']['executed'], 3)
            self.assertEqual((view['today']['wins'], view['today']['losses']), (1, 1))
            self.assertAlmostEqual(view['today']['profit'], -28.0)
            self.assertIsNone(view['balance']['value'])
            engine = sb.engine_view(c, NOW + timedelta(hours=5))
            self.assertEqual(engine['cards']['executed'], 3)
            self.assertFalse(engine['real_money_enabled'])

    def test_lost_qualification_cancels_queue_entry(self):
        with self.conn() as c:
            c.execute('PRAGMA foreign_keys=OFF')
            seed(c, 'fx')
            sb.run_cycle(c, NOW)
            c.execute("UPDATE sports_ai_qualification_decisions SET state='MONITORING'")
            out = sb.run_cycle(c, NOW + timedelta(seconds=30))
            self.assertEqual(out['cancelled'], 1)
            self.assertEqual(c.execute('SELECT status FROM sports_ai_single_bets').fetchone()[0], 'CANCELLED')

    def test_daily_cap_is_hard(self):
        with self.conn() as c:
            c.execute('PRAGMA foreign_keys=OFF')
            for i in range(30):
                seed(c, f'fx{i}')
            out = sb.run_cycle(c, NOW)
            self.assertEqual(out['queued'], sb.DAILY_LIMIT)

    def test_kill_switch_stops_selection(self):
        with self.conn() as c:
            c.execute('PRAGMA foreign_keys=OFF')
            seed(c, 'fx')
            sb.set_paused(c, True, 'tester', NOW)
            out = sb.run_cycle(c, NOW)
            self.assertEqual(out['status'], 'paused')
            self.assertEqual(c.execute('SELECT COUNT(*) FROM sports_ai_single_bets').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
