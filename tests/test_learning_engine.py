import json
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

from learning_engine import build_report, describe_schedule, export_csv, refresh
import db

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)


class LearningTests(unittest.TestCase):
    def setUp(self):
        self.previous = db.DB
        self.temp = tempfile.TemporaryDirectory()
        db.DB = Path(self.temp.name) / 'sports.sqlite3'
        db.initialize()

    def tearDown(self):
        db.DB = self.previous
        self.temp.cleanup()

    def _model(self, connection, version, active=0, status='validated'):
        run_id, model_id = str(uuid.uuid4()), str(uuid.uuid4())
        connection.execute(
            """INSERT INTO sports_ai_training_runs
               (id,sport,model_name,version,status,training_window_json,parameters_json,metrics_json,sample_size)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (run_id, 'football', 'sports-ai-football-ensemble', version, 'validated' if status == 'validated' else 'insufficient_data', '{}', '{}', '{}', 10),
        )
        connection.execute(
            """INSERT INTO sports_ai_model_registry
               (id,training_run_id,sport,model_name,version,status,active,parameters_json,metrics_json,lifecycle)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (model_id, run_id, 'football', 'sports-ai-football-ensemble', version, status, active, '{}', '{}', 'active' if active else status),
        )
        return model_id

    def _analysis(self, connection):
        analysis_id = str(uuid.uuid4())
        connection.execute(
            "INSERT INTO sports_ai_analysis_runs (id,fixture_count,selection_count,ticket_count,status) VALUES(?,0,0,0,'complete')",
            (analysis_id,),
        )
        return analysis_id

    def _case(self, connection, model_id, analysis_id, kickoff, created, settled, probability, home, away, odds_at=None, odds=None):
        fixture_id, feature_id = str(uuid.uuid4()), str(uuid.uuid4())
        connection.execute(
            """INSERT INTO sports_ai_fixtures
               (id,external_id,source,sport,competition,home,away,starts_at,status,statistics_json,odds_json,observed_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (fixture_id, fixture_id, 'test', 'football', 'League', 'Home', 'Away', kickoff.isoformat(), 'final', '{}', '{}', created.isoformat()),
        )
        connection.execute(
            """INSERT INTO sports_ai_results(id,fixture_id,home_score,away_score,status,source,settled_at)
               VALUES(?,?,?,?,?,?,?)""",
            (str(uuid.uuid4()), fixture_id, home, away, 'final', 'verified', settled.isoformat()),
        )
        connection.execute(
            """INSERT INTO sports_ai_feature_snapshots
               (id,fixture_id,sport,cutoff_at,features_json,source_refs_json,generated_at)
               VALUES(?,?,?,?,?,?,?)""",
            (feature_id, fixture_id, 'football', (kickoff - timedelta(hours=2)).isoformat(), '{}', '[]', created.isoformat()),
        )
        prediction_id = str(uuid.uuid4())
        connection.execute(
            """INSERT INTO sports_ai_engine_predictions
               (id,run_id,fixture_id,model_id,feature_id,market,outcome,probability,settlement_json,explanation_json,created_at)
               VALUES(?,?,?,?,?,'HOME','HOME',?,?,?,?)""",
            (prediction_id, analysis_id, fixture_id, model_id, feature_id, probability,
             '{"p_win": 1}', '{}', created.isoformat()),
        )
        if odds_at and odds:
            provider = connection.execute('SELECT id FROM sports_ai_provider_registry').fetchone()
            if not provider:
                provider_id = str(uuid.uuid4())
                connection.execute(
                    """INSERT INTO sports_ai_provider_registry
                       (id,provider_key,display_name,sport,adapter,enabled,terms_reviewed,status,config_json)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    (provider_id, 'learning-test', 'Learning', 'football', 'manual', 0, 1, 'untested', '{}'),
                )
            else:
                provider_id = provider['id']
            connection.execute(
                """INSERT INTO sports_ai_odds_snapshots
                   (id,fixture_id,provider_id,bookmaker,market,outcome,decimal_odds,source_url,observed_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (str(uuid.uuid4()), fixture_id, provider_id, 'SportyBet', 'HOME', 'HOME', odds, 'https://example.test/odds', odds_at.isoformat()),
            )
        return fixture_id, prediction_id

    def test_empty_history_stays_empty(self):
        with db.connect() as connection:
            report = build_report(connection, now=NOW)
        self.assertEqual(report['selections']['sample_size'], 0)
        self.assertIsNone(report['selections']['accuracy'])
        self.assertIsNone(report['selections']['brier_score'])
        self.assertFalse(report['promoted'])
        self.assertFalse(describe_schedule()['promoted'])

    def test_leakage_and_post_kickoff_price_are_excluded(self):
        with db.connect() as connection:
            model_id = self._model(connection, 'v1', active=1, status='active')
            analysis_id = self._analysis(connection)
            kickoff = datetime(2026, 10, 1, 15, 0, tzinfo=timezone.utc)
            self._case(connection, model_id, analysis_id, kickoff, kickoff + timedelta(hours=1), kickoff + timedelta(hours=3), 0.9, 2, 0)
            kept, prediction_id = self._case(
                connection, model_id, analysis_id, kickoff + timedelta(days=1), kickoff,
                kickoff + timedelta(days=1, hours=3), 1, 2, 0,
                odds_at=kickoff + timedelta(days=1, hours=2), odds=1.5,
            )
            report = build_report(connection, now=NOW)
        self.assertEqual(report['exceptions']['leakage'], 1)
        self.assertEqual(report['selections']['sample_size'], 1)
        self.assertEqual(report['selections']['brier_score'], 0)
        self.assertIsNone(report['selections']['expected_return'])
        self.assertNotEqual(kept, prediction_id)

    def test_pre_kickoff_price_sets_expected_and_realized_return(self):
        with db.connect() as connection:
            model_id = self._model(connection, 'v1', active=1, status='active')
            analysis_id = self._analysis(connection)
            kickoff = datetime(2026, 10, 2, 15, 0, tzinfo=timezone.utc)
            self._case(connection, model_id, analysis_id, kickoff, kickoff - timedelta(hours=2), kickoff + timedelta(hours=2), 0.5, 2, 0, odds_at=kickoff - timedelta(hours=1), odds=2)
            self._case(connection, model_id, analysis_id, kickoff + timedelta(days=1), kickoff, kickoff + timedelta(days=1, hours=2), 0.5, 0, 1, odds_at=kickoff + timedelta(days=1, hours=-1), odds=2)
            report = build_report(connection, now=NOW)
        self.assertEqual(report['selections']['priced_sample'], 2)
        self.assertAlmostEqual(report['selections']['expected_return'], 0)
        self.assertAlmostEqual(report['selections']['realized_return'], 0)
        self.assertEqual(report['selections']['accuracy'], 0.5)
        self.assertAlmostEqual(report['selections']['brier_score'], 0.25)

    def test_walk_forward_folds_move_forward_in_time(self):
        with db.connect() as connection:
            model_id = self._model(connection, 'v1', active=1, status='active')
            analysis_id = self._analysis(connection)
            for offset in range(4):
                kickoff = datetime(2026, 8, 1 + offset, 15, 0, tzinfo=timezone.utc)
                self._case(connection, model_id, analysis_id, kickoff, kickoff - timedelta(hours=1), kickoff + timedelta(hours=2), 0.6, 1, 0)
            report = build_report(connection, now=NOW)
        folds = report['walk_forward']['folds']
        self.assertEqual([fold['sample_size'] for fold in folds], [1, 1, 1, 1])
        self.assertLessEqual(folds[0]['to'], folds[1]['from'])

    def test_latest_pre_kickoff_prediction_is_the_one_scored(self):
        with db.connect() as connection:
            model_id = self._model(connection, 'v1', active=1, status='active')
            analysis_id = self._analysis(connection)
            kickoff = datetime(2026, 10, 3, 15, 0, tzinfo=timezone.utc)
            fixture_id, _ = self._case(connection, model_id, analysis_id, kickoff, kickoff - timedelta(hours=5), kickoff + timedelta(hours=2), 0.1, 2, 0)
            analysis_id = self._analysis(connection)
            feature_id = str(uuid.uuid4())
            connection.execute(
                """INSERT INTO sports_ai_feature_snapshots
                   (id,fixture_id,sport,cutoff_at,features_json,source_refs_json,generated_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (feature_id, fixture_id, 'football', (kickoff - timedelta(hours=1)).isoformat(), '{}', '[]', kickoff.isoformat()),
            )
            connection.execute(
                """INSERT INTO sports_ai_engine_predictions
                   (id,run_id,fixture_id,model_id,feature_id,market,outcome,probability,settlement_json,explanation_json,created_at)
                   VALUES(?,?,?,?,?,'HOME','HOME',?,?,?,?)""",
                (str(uuid.uuid4()), analysis_id, fixture_id, model_id, feature_id, 1,
                 '{"p_win": 1}', '{}', (kickoff - timedelta(hours=1)).isoformat()),
            )
            report = build_report(connection, now=NOW)
        self.assertEqual(report['selections']['sample_size'], 1)
        self.assertEqual(report['selections']['brier_score'], 0)

    def test_ticket_result_is_separate_from_selection_accuracy(self):
        with db.connect() as connection:
            model_id = self._model(connection, 'v1', active=1, status='active')
            analysis_id = self._analysis(connection)
            kickoff = datetime(2026, 10, 4, 15, 0, tzinfo=timezone.utc)
            self._case(connection, model_id, analysis_id, kickoff, kickoff - timedelta(hours=1), kickoff + timedelta(hours=2), 1, 1, 0)
            self._ticket(connection, 'lost', 'odds_tier', tier=150)
            self._ticket(connection, 'won', 'weekly_draw', mode='WEEKLY', picks=2)
            self._ticket(connection, 'lost', 'weekly_draw', mode='SATURDAY_ONLY', picks=8)
            report = build_report(connection, now=NOW)
        self.assertEqual(report['selections']['accuracy'], 1)
        self.assertEqual(report['tickets']['win_rate'], 1 / 3)
        weekly = next(row for row in report['draws']['by_size'] if row['mode'] == 'WEEKLY' and row['picks'] == 2)
        saturday = next(row for row in report['draws']['by_size'] if row['mode'] == 'SATURDAY_ONLY' and row['picks'] == 8)
        self.assertEqual(weekly['win_rate'], 1)
        self.assertEqual(saturday['sample_size'], 1)
        self.assertEqual(saturday['win_rate'], 0)
        self.assertEqual(report['tiers'][0]['label'], '1.50–1.99')

    def test_rollover_path_is_not_invented(self):
        with db.connect() as connection:
            connection.execute(
                """INSERT INTO sports_ai_rollover_campaigns
                   (id,name,mode,status,start_date,length_days,opening_balance,currency,odds_target,odds_low,odds_high,
                    stake_mode,fixed_stake,stake_fraction,max_stake_fraction,protected_profit_fraction,profit_lock_multiple,
                    stop_loss_fraction,bankroll,protected_capital,peak_equity,places_wager,created_at,updated_at)
                   VALUES('camp','30-day','CAPITAL_CONTROLLED_SIMULATION','active','2026-11-04',30,1000,'NGN',1.5,1.4,1.6,
                          'fixed',100,0.05,0.1,0.5,1.5,0.2,1000,0,1000,0,?,?)""",
                (NOW.isoformat(), NOW.isoformat()),
            )
            report = build_report(connection, now=NOW)
        self.assertIsNone(report['rollover'][0]['path_probability'])
        self.assertEqual(report['rollover'][0]['realized_return'], 0)
        self.assertFalse(report['rollover'][0]['places_wager'])

    def test_deterioration_suspends_without_activating_a_challenger(self):
        with db.connect() as connection:
            champion = self._model(connection, 'champion', active=1, status='active')
            challenger = self._model(connection, 'challenger', active=0, status='validated')
            analysis_id = self._analysis(connection)
            self._window(connection, champion, analysis_id, datetime(2026, 7, 1, tzinfo=timezone.utc), 0.9, won=True)
            self._window(connection, champion, analysis_id, datetime(2026, 10, 1, tzinfo=timezone.utc), 0.9, won=False)
            self._window(connection, challenger, analysis_id, datetime(2026, 10, 15, tzinfo=timezone.utc), 0.8, won=True, count=10)
            before = build_report(connection, now=NOW)
            first = refresh(connection, 'tester', None, NOW)
            active = connection.execute('SELECT active FROM sports_ai_model_registry WHERE id=?', (champion,)).fetchone()['active']
            challenger_active = connection.execute('SELECT active FROM sports_ai_model_registry WHERE id=?', (challenger,)).fetchone()['active']
            controls = connection.execute("SELECT COUNT(*) AS n FROM sports_ai_learning_controls WHERE action='suspend'").fetchone()['n']
            second = refresh(connection, 'tester', None, NOW)
            controls_after = connection.execute("SELECT COUNT(*) AS n FROM sports_ai_learning_controls WHERE action='suspend'").fetchone()['n']
            report = build_report(connection, now=NOW)
        self.assertEqual(first['suspended'], [champion])
        self.assertFalse(first['promoted'])
        self.assertEqual(active, 0)
        self.assertEqual(challenger_active, 0)
        self.assertEqual(controls, 1)
        self.assertEqual(second['status'], 'stored')
        self.assertEqual(controls_after, 1)
        comparison = before['models']['comparisons'][0]
        self.assertTrue(comparison['challenger_has_lower_brier'])
        self.assertFalse(comparison['activated'])
        self.assertFalse(report['models']['comparisons'][0]['activated'])

    def test_a_short_sample_does_not_suspend(self):
        with db.connect() as connection:
            model_id = self._model(connection, 'short', active=1, status='active')
            analysis_id = self._analysis(connection)
            self._window(connection, model_id, analysis_id, datetime(2026, 10, 1, tzinfo=timezone.utc), 0.9, won=False, count=5)
            result = refresh(connection, 'tester', None, NOW)
            active = connection.execute('SELECT active FROM sports_ai_model_registry WHERE id=?', (model_id,)).fetchone()['active']
            body = export_csv(connection, 'selections', now=NOW)
        self.assertEqual(result['suspended'], [])
        self.assertEqual(active, 1)
        self.assertIn('fixture_id', body.splitlines()[0])
        self.assertEqual(len(body.splitlines()), 6)

    def _window(self, connection, model_id, analysis_id, start, probability, won, count=30):
        for index in range(count):
            kickoff = start + timedelta(days=index % 20, hours=index)
            created = kickoff - timedelta(hours=2)
            settled = kickoff + timedelta(hours=3)
            self._case(connection, model_id, analysis_id, kickoff, created, settled, probability, 2 if won else 0, 0 if won else 1)

    def _ticket(self, connection, outcome, product, tier=20002, mode=None, picks=None):
        ticket_id = str(uuid.uuid4())
        parameters = {'product': product}
        if mode:
            parameters.update({'mode': mode, 'pick_count': picks})
        connection.execute(
            """INSERT INTO sports_ai_accumulator_tickets
               (id,family_id,version,signature,tier,sport_mode,status,combined_odds,joint_probability,leg_count,
                probability_basis,explanation,parameters_json,created_at)
               VALUES(?,?,1,?,?,'football','settled',2,0.4,1,'independent','stored',?,?)""",
            (ticket_id, str(uuid.uuid4()), ticket_id, tier, json.dumps(parameters), NOW.isoformat()),
        )
        connection.execute(
            """INSERT INTO sports_ai_ticket_settlements
               (id,ticket_id,outcome,stake_assumption,hypothetical_profit,settled_at,details_json)
               VALUES(?,?,?,1,?,?,?)""",
            (str(uuid.uuid4()), ticket_id, outcome, 1 if outcome == 'won' else -1, NOW.isoformat(), '{}'),
        )
        connection.execute(
            """INSERT INTO sports_ai_accumulator_legs
               (id,ticket_id,opportunity_id,fixture_id,sport,competition,home,away,starts_at,market,selection,
                decimal_odds,bookmaker,odds_observed_at,quote_market,quote_outcome,model_id,model_version,
                settlement_kind,qualification_json,result)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (str(uuid.uuid4()), ticket_id, str(uuid.uuid4()), str(uuid.uuid4()), 'football', 'League', 'Home', 'Away',
             NOW.isoformat(), 'DRAW', 'Draw', 3.2, 'SportyBet', NOW.isoformat(), 'DRAW', 'DRAW', 'model', 'v1',
             'binary', '{}', 'won' if outcome == 'won' else 'lost'),
        )
