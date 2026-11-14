import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

import db
import single_betting as sb
from unittest.mock import patch

# The engine's own combined selections are retired (owner's structure, 12 Nov 2026: combined bets
# are the Sure Odds tickets). The code stays to settle combined bets already placed, so its tests
# run under the settings it was built for.
LEGACY_ENGINE_COMBOS = patch.multiple(sb, ENGINE_COMBOS=True, MIN_ODDS=1.2, MAX_ODDS=1000.0)

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
        # These tests seed every match in one league; the day plan's league limit is tested on its own.
        import day_plan
        self.league_cap = patch.object(day_plan, 'MAX_PER_LEAGUE', 99)
        self.league_cap.start()

    def tearDown(self):
        self.league_cap.stop()
        db.DB = self.previous
        self.temp.cleanup()

    def conn(self):
        return db.connect()

    def test_day_plan_ranks_the_whole_day_and_places_near_kickoff(self):
        import day_plan
        with self.conn() as c:
            c.execute('PRAGMA foreign_keys=OFF')
            seed(c, 'early', probability=0.60, kickoff=NOW + timedelta(hours=2))
            seed(c, 'late-better', probability=0.70, kickoff=NOW + timedelta(hours=9))
            pool = sb.load_candidates(c, NOW)
            with patch.object(sb, 'SPORT_RESERVE', {}):
                queued = day_plan.plan_singles(c, NOW, sb.local_date(NOW), pool)
            # Both are picked; only the one inside its placement window is queued now.
            self.assertEqual([i['fixture_id'] for i in queued], ['early'])
            plan = day_plan.plan_view(c)
            self.assertEqual([r['fixture_id'] for r in plan['rows']], ['late-better', 'early'])   # ranked by chance
            self.assertTrue(plan['rows'][0]['status'].startswith('picked · places from'))

    def test_day_plan_league_limit(self):
        import day_plan
        with self.conn() as c, patch.object(day_plan, 'MAX_PER_LEAGUE', 2), patch.object(sb, 'SPORT_RESERVE', {}):
            c.execute('PRAGMA foreign_keys=OFF')
            for i in range(4):
                seed(c, f'same-league-{i}', probability=0.60 + i / 100)
            queued = day_plan.plan_singles(c, NOW, sb.local_date(NOW), sb.load_candidates(c, NOW))
            self.assertEqual(len(queued), 2)
            skipped = [r for r in day_plan.plan_view(c)['rows'] if not r['picked']]
            self.assertTrue(all('league already has 2' in r['status'] for r in skipped))

    def test_engine_read_uses_relevant_prices_without_global_history_counts(self):
        with self.conn() as c:
            c.execute('PRAGMA foreign_keys=OFF')
            seed(c, 'fresh', observed=NOW-timedelta(minutes=2))
            seed(c, 'started', observed=NOW, kickoff=NOW-timedelta(hours=1))
            queries=[]
            c.set_trace_callback(queries.append)
            with patch('workflow_status.workflow_board', side_effect=AssertionError('Full history scan')):
                view=sb.engine_view(c,NOW)
            self.assertEqual(view['health']['price_age_minutes'],2.0)
            self.assertIsNotNone(view['health']['data_sync'])
            self.assertIsNone(view['focus'])
            self.assertFalse(any('COUNT(*)' in q.upper() and 'SPORTS_AI_ODDS_SNAPSHOTS' in q.upper() for q in queries))

    def test_price_health_marks_stale_price_even_when_engine_cycle_is_recent(self):
        with self.conn() as c:
            c.execute('PRAGMA foreign_keys=OFF')
            seed(c,'old',observed=NOW-timedelta(minutes=30))
            view=sb.engine_view(c,NOW)
            self.assertEqual(view['health']['price_age_minutes'],30.0)
            self.assertIsNone(view['health']['data_sync'])
            self.assertEqual(view['pipeline'][1]['status'],'Attention')

    def test_old_pipeline_snapshot_never_appears_completed(self):
        with self.conn() as c:
            c.execute('INSERT INTO sports_ai_pipeline_snapshots(id,created_at,stages_json,blockers_json) VALUES(?,?,?,?)',
                      ('old',(NOW-timedelta(hours=2)).isoformat(),json.dumps([{'stage':'PREDICTED','count':100}]),'[]'))
            stages,at=sb._execution_pipeline(c,NOW,{'fresh':False})
            self.assertEqual(stages[3]['status'],'Waiting')
            self.assertEqual(at,(NOW-timedelta(hours=2)).isoformat())

    def test_policy_rejects_stale_close_and_negative_edge(self):
        ok = sb.evaluate(1.72, 0.645, NOW - timedelta(minutes=1), NOW + timedelta(hours=1), NOW)
        self.assertTrue(ok['eligible'])
        self.assertAlmostEqual(ok['ev'], 0.1094, places=3)
        self.assertIn('STALE_ODDS', sb.evaluate(1.72, 0.645, NOW - timedelta(hours=1), NOW + timedelta(hours=1), NOW)['reasons'])
        self.assertIn('EVENT_TOO_CLOSE', sb.evaluate(1.72, 0.645, NOW, NOW + timedelta(seconds=30), NOW)['reasons'])
        self.assertIn('INSUFFICIENT_EDGE', sb.evaluate(1.50, 0.60, NOW, NOW + timedelta(hours=1), NOW)['reasons'])

    def test_capacity_respects_count_and_stake_caps(self):
        # 10 a day / ₦1,000 (5 singles + 5 Sure Odds combined bets, owner 13 Nov 2026)
        self.assertEqual(sb.capacity(0, 0), 10)
        self.assertEqual(sb.capacity(9, 900), 1)
        self.assertEqual(sb.capacity(10, 1000), 0)
        self.assertEqual(sb.capacity(3, 950), 0)

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
            # Football-only candidates: the basketball reserve (tested in test_three_objectives) is off here.
            with patch.object(sb, 'SPORT_RESERVE', {}):
                out = sb.run_cycle(c, NOW)
                self.assertEqual(out['queued'], sb.SINGLES_DAILY)  # singles quota; the other 5 are combined bets

    def test_rehearsed_bets_do_not_use_up_live_capacity(self):
        day = sb.local_date(NOW)
        with self.conn() as c:
            c.execute('PRAGMA foreign_keys=OFF')
            for i in range(3):
                c.execute(
                    """INSERT INTO sports_ai_single_bets(id,fixture_id,sport,competition,home,away,market,selection,decimal_odds,
                       probability,expected_value,stake,mode,status,local_date,starts_at,created_at,kind)
                       VALUES(?,?,'football','L','A','B','HOME','Home',1.3,0.8,0.04,100,'PAPER','PLACED',?,?,?,'FAVOURITE')""",
                    (f'b{i}', f'f{i}', day, (NOW + timedelta(hours=3)).isoformat(), NOW.isoformat()))
            c.execute("""INSERT INTO sports_ai_single_live_orders(id,bet_id,kind,status,local_date,stake,expected_odds,created_at,updated_at)
                         VALUES('o0','b0','SUBMISSION','PLACED',?,100,1.3,?,?)""", (day, NOW.isoformat(), NOW.isoformat()))
            self.assertEqual(sb._day_usage(c, day), (3, 300.0))  # paper and rehearsal modes count everything
            self.assertEqual(sb._favourites_today(c, day), 3)
            sb._save_setting(c, sb.RUNTIME_KEY, {'paused': False, 'execution_mode': 'LIVE'}, NOW)
            self.assertEqual(sb._day_usage(c, day), (1, 100.0))  # only the real submission
            self.assertEqual(sb._favourites_today(c, day), 1)

    def test_match_not_found_on_sportybet_is_not_requeued(self):
        with self.conn() as c:
            c.execute('PRAGMA foreign_keys=OFF')
            seed(c, 'fx-missing')
            self.assertEqual(sb.run_cycle(c, NOW)['queued'], 1)
            bet = c.execute('SELECT id FROM sports_ai_single_bets').fetchone()['id']
            c.execute("UPDATE sports_ai_single_bets SET status='CANCELLED'")
            moment = datetime.now(timezone.utc).isoformat()
            c.execute("""INSERT INTO sports_ai_single_live_orders(id,bet_id,kind,status,local_date,stake,expected_odds,error,created_at,updated_at)
                         VALUES('o1',?,'SUBMISSION','FAILED','2026-11-10',100,1.72,
                                'Selection control missing or ambiguous: fx-missing-H vs fx-missing-A (league control: none)',?,?)""",
                      (bet, moment, moment))
            self.assertIn('fx-missing', sb._active_fixtures(c))
            pool = sb.load_candidates(c, NOW)
            self.assertEqual(pool['eligible'], [])
            self.assertEqual(pool['rejected'].get('NOT_FOUND_ON_SPORTYBET'), 1)

    def test_single_below_min_odds_is_rejected(self):
        self.assertIn('BELOW_MIN_ODDS', sb.evaluate(1.59, 0.95, NOW, NOW + timedelta(hours=1), NOW)['reasons'])
        self.assertTrue(sb.evaluate(1.60, 0.90, NOW, NOW + timedelta(hours=1), NOW)['eligible'])
        self.assertIn('ABOVE_MAX_ODDS', sb.evaluate(2.00, 0.90, NOW, NOW + timedelta(hours=1), NOW)['reasons'])
        with self.conn() as c:
            c.execute('PRAGMA foreign_keys=OFF')
            seed(c, 'lonely', odds=1.12, probability=0.95)  # no partner leg: nothing is bet
            out = sb.run_cycle(c, NOW)
            self.assertEqual(out['queued'], 0)
            status = json.loads(c.execute("SELECT value_json FROM sports_ai_settings WHERE key=?", (sb.STATUS_KEY,)).fetchone()[0])
            self.assertEqual(status['rejected'].get('BELOW_MIN_ODDS'), 1)

    @LEGACY_ENGINE_COMBOS
    def test_low_odds_legs_combine_and_settle(self):
        with self.conn() as c:
            c.execute('PRAGMA foreign_keys=OFF')
            seed(c, 'a', odds=1.12, probability=0.95)
            seed(c, 'b', odds=1.14, probability=0.93)
            seed(c, 'single', odds=1.72, probability=0.645)
            out = sb.run_cycle(c, NOW)
            self.assertEqual(out['queued'], 2)
            combo = c.execute("SELECT * FROM sports_ai_single_bets WHERE kind='COMBO'").fetchone()
            self.assertAlmostEqual(combo['decimal_odds'], round(1.12 * 1.14, 4))
            self.assertGreaterEqual(combo['decimal_odds'], sb.MIN_ODDS)
            self.assertAlmostEqual(combo['probability'], 0.95 * 0.93)
            legs = c.execute('SELECT fixture_id FROM sports_ai_single_bet_legs WHERE bet_id=? ORDER BY leg_no', (combo['id'],)).fetchall()
            self.assertEqual(sorted(r[0] for r in legs), ['a', 'b'])
            # Neither leg fixture can be queued again.
            self.assertEqual(sb.run_cycle(c, NOW + timedelta(seconds=30))['queued'], 0)
            view = sb.engine_view(c, NOW + timedelta(seconds=30))
            shown = next(b for b in view['recent'] if b['kind'] == 'COMBO')
            self.assertEqual(shown['fixture'], 'a-H vs a-A + b-H vs b-A')
            self.assertEqual(c.execute('SELECT status FROM sports_ai_single_bets WHERE id=?', (combo['id'],)).fetchone()[0], 'PLACED')
            result(c, 'a', 1, 0)
            sb.run_cycle(c, NOW + timedelta(hours=5))
            self.assertEqual(c.execute('SELECT status FROM sports_ai_single_bets WHERE id=?', (combo['id'],)).fetchone()[0], 'PLACED')
            result(c, 'b', 3, 1)
            sb.run_cycle(c, NOW + timedelta(hours=6))
            row = c.execute('SELECT * FROM sports_ai_single_bets WHERE id=?', (combo['id'],)).fetchone()
            self.assertEqual(row['status'], 'WON')
            self.assertAlmostEqual(row['payout'], round(100 * 1.12 * 1.14, 2))

    @LEGACY_ENGINE_COMBOS
    def test_combo_loses_on_any_leg_and_needs_high_probability(self):
        with self.conn() as c:
            c.execute('PRAGMA foreign_keys=OFF')
            seed(c, 'a', odds=1.12, probability=0.95)
            seed(c, 'b', odds=1.14, probability=0.93)
            sb.run_cycle(c, NOW)
            sb.run_cycle(c, NOW + timedelta(seconds=30))
            result(c, 'b', 0, 2)  # leg b lost; leg a still pending
            sb.run_cycle(c, NOW + timedelta(hours=5))
            row = c.execute("SELECT * FROM sports_ai_single_bets WHERE kind='COMBO'").fetchone()
            self.assertEqual(row['status'], 'LOST')
            self.assertAlmostEqual(row['profit'], -100.0)
        legs = [{'fixture_id': f, 'sport': 'football', 'home': f, 'away': f, 'selection': 'x', 'starts_at': 'z',
                 'decimal_odds': 1.11, 'probability': 0.88} for f in ('x', 'y')]
        combos, unused = sb.build_combos(legs)  # 1.23 but p=0.77 < 0.80
        self.assertEqual((combos, len(unused)), ([], 2))

    def _seed_second_market(self, c, fixture_id, market, odds, probability):
        """A second qualified market on a fixture seed() already created."""
        prediction, quote = f'p-{fixture_id}-{market}', f'q-{fixture_id}-{market}'
        kickoff = (NOW + timedelta(hours=3)).isoformat()
        c.execute("""INSERT INTO sports_ai_engine_predictions(id,run_id,fixture_id,model_id,feature_id,market,outcome,probability,settlement_json,explanation_json)
                     VALUES(?,?,?,?,?,?,?,?,?,?)""", (prediction, 'run', fixture_id, 'model', 'feature', market, market, probability, '{}', '{}'))
        c.execute("""INSERT INTO sports_ai_odds_snapshots(id,fixture_id,provider_id,bookmaker,market,outcome,line,decimal_odds,source_url,observed_at)
                     VALUES(?,?,?,?,?,?,?,?,?,?)""", (quote, fixture_id, 'provider', 'SportyBet', market, market, None, odds, 'https://example.test', (NOW - timedelta(minutes=2)).isoformat()))
        c.execute("""INSERT INTO sports_ai_value_opportunities(id,prediction_id,odds_id,implied_probability,expected_edge,status,reason,evaluated_at)
                     VALUES(?,?,?,?,?,?,?,?)""", (f'o-{fixture_id}-{market}', prediction, quote, 1 / odds, odds * probability - 1, 'qualified', 'test', NOW.isoformat()))
        c.execute("""INSERT INTO sports_ai_qualification_decisions(id,prediction_id,fixture_id,sport,competition,market,state,reason_code,reason,evidence_json,products_json,
                     rank_score,policy_version,decision_version,fingerprint,starts_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (f'd-{fixture_id}-{market}', prediction, fixture_id, 'football', 'Primeira Liga', market, 'QUALIFIED', 'OK', 'ok',
                   json.dumps({'probability': probability}), json.dumps(['booking']), 1.0, 1, 1, 'fp', kickoff, NOW.isoformat(), NOW.isoformat()))

    @LEGACY_ENGINE_COMBOS
    def test_combined_replaces_single_when_more_likely_to_win(self):
        with self.conn() as c:
            c.execute('PRAGMA foreign_keys=OFF')
            seed(c, 'x', odds=1.72, probability=0.645)          # single at 64.5%
            self._seed_second_market(c, 'x', 'HOME_OR_DRAW', 1.12, 0.95)
            seed(c, 'y', odds=1.14, probability=0.93)
            pool = sb.load_candidates(c, NOW)
            self.assertEqual([i['kind'] for i in pool['eligible']], ['COMBO'])  # 88% beats 64.5%
            combo = pool['eligible'][0]
            self.assertEqual({(l['fixture_id'], l['market']) for l in combo['legs']}, {('x', 'HOME_OR_DRAW'), ('y', 'HOME')})
            self.assertEqual(pool['rejected'].get('SINGLE_REPLACED_BY_COMBINED'), 1)

    @LEGACY_ENGINE_COMBOS
    def test_single_kept_when_it_is_more_likely_than_the_combination(self):
        with self.conn() as c:
            c.execute('PRAGMA foreign_keys=OFF')
            seed(c, 'x', odds=1.25, probability=0.90)           # single at 90%
            self._seed_second_market(c, 'x', 'HOME_OR_DRAW', 1.12, 0.95)
            seed(c, 'y', odds=1.14, probability=0.93)           # combination would be 88%
            pool = sb.load_candidates(c, NOW)
            self.assertEqual([(i['kind'], i['fixture_id']) for i in pool['eligible']], [('SINGLE', 'x')])
            self.assertEqual(pool['rejected'].get('BELOW_MIN_ODDS'), 1)  # y alone cannot reach 1.20

    def test_candidates_ranked_by_chance_of_winning(self):
        with self.conn() as c:
            c.execute('PRAGMA foreign_keys=OFF')
            seed(c, 'value', odds=1.95, probability=0.58)       # EV +0.131, 58%
            seed(c, 'safe', odds=1.65, probability=0.70)        # EV +0.155, 70%
            ranked = [i['fixture_id'] for i in sb.load_candidates(c, NOW)['eligible']]
            self.assertEqual(ranked, ['safe', 'value'])

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
