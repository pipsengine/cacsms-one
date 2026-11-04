import json
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

import db
from accumulator_engine import (
    RANGE_TIERS,
    TARGETS,
    combine_ticket,
    default_config,
    default_tier_policy,
    generate_accumulators,
    monitor_accumulators,
    optimize_candidates,
    payout_multiple,
    qualify_candidates,
    run_backtest,
    save_tier_policy,
    settle_result,
    ticket_outcome,
)
from engine import build_tickets
from sportybet_adapter import automatic_booking_code, integration_status, register_manual_code


def _binary(probability):
    return json.dumps({
        'p_win': probability, 'p_half_win': 0, 'p_push': 0, 'p_half_loss': 0, 'p_loss': 1 - probability,
    })


def _row(**overrides):
    now = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)
    row = {
        'opportunity_id': 'opp-1',
        'opportunity_status': 'qualified',
        'fixture_id': 'fixture-1',
        'sport': 'football',
        'competition': 'League A',
        'home': 'Home A',
        'away': 'Away A',
        'starts_at': (now + timedelta(days=2)).isoformat(),
        'market': 'HOME',
        'outcome': 'HOME',
        'decimal_odds': 1.5,
        'probability': 0.70,
        'expected_edge': 0.08,
        'bookmaker': 'Book',
        'observed_at': now.isoformat(),
        'model_active': 1,
        'model_status': 'active',
        'calibration_status': 'calibrated',
        'metrics_json': '{"calibration_error":0.04}',
        'data_completeness': 0.8,
        'settlement_json': _binary(0.70),
        'model_id': 'model-1',
        'model_version': '2',
    }
    row.update(overrides)
    return row


def _config(**overrides):
    config = default_config()
    config.update({'targets': [2], 'min_legs': 2, 'max_legs': 2, 'max_tickets_per_tier': 3, 'timeout_seconds': 5})
    config.update(overrides)
    return config


class AccumulatorMathTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)

    def test_eight_targets_and_product_math(self):
        self.assertEqual(TARGETS, (2, 5, 10, 20, 25, 50, 100, 500, 800, 1000, 5000, 10000, 20000, 25000, 50000))
        self.assertEqual(default_config()['targets'], list(TARGETS))
        self.assertEqual(default_config()['tolerance'], 0.15)
        accepted, _decisions = qualify_candidates([
            _row(),
            _row(opportunity_id='opp-2', fixture_id='fixture-2', home='Home B', away='Away B',
                 decimal_odds=1.4, probability=0.75, settlement_json=_binary(0.75)),
        ], _config(), self.now)
        result = optimize_candidates(accepted, _config(), self.now)
        ticket = result['tiers'][0]['tickets'][0]
        self.assertAlmostEqual(ticket['combined_odds'], 2.1)
        self.assertAlmostEqual(ticket['joint_probability'], 0.525)
        self.assertAlmostEqual(ticket['estimated_edge'], 0.525 * 2.1 - 1)
        self.assertEqual(ticket['status'], 'qualified')
        self.assertEqual(ticket['probability_basis'], 'independent')

    def test_stale_started_and_unqualified_are_excluded(self):
        stale = _row(opportunity_id='stale', observed_at=(self.now - timedelta(hours=2)).isoformat())
        started = _row(opportunity_id='started', fixture_id='past', starts_at=(self.now - timedelta(hours=1)).isoformat())
        waiting = _row(opportunity_id='waiting', opportunity_status='awaiting_odds')
        accepted, decisions = qualify_candidates([stale, started, waiting], _config(), self.now)
        self.assertEqual(accepted, [])
        reasons = ' '.join(item['reason'] for item in decisions)
        self.assertIn('freshness', reasons)
        self.assertIn('already started', reasons)
        self.assertIn('awaiting_odds', reasons)

    def test_same_fixture_and_contradictions_do_not_combine(self):
        first = _row()
        second = _row(opportunity_id='opp-2', market='AWAY', decimal_odds=3.2, probability=0.4, settlement_json=_binary(0.4))
        accepted, decisions = qualify_candidates([first, second], _config(), self.now)
        self.assertEqual(len(accepted), 1)
        self.assertTrue(any('Same-fixture' in item['reason'] for item in decisions))
        search = optimize_candidates([
            {**accepted[0]},
            {**accepted[0], 'opportunity_id': 'opp-2', 'market': 'AWAY', 'decimal_odds': 3.2},
        ], _config(allow_same_fixture=0), self.now)
        self.assertEqual(search['tiers'][0]['status'], 'no_qualifying_ticket')
        over = {**accepted[0], 'market': 'OVER_2.5', 'decimal_odds': 1.9, 'probability': 0.55}
        under = {**accepted[0], 'opportunity_id': 'opp-2', 'market': 'UNDER_2.5', 'decimal_odds': 1.9, 'probability': 0.48}
        issues = combine_ticket([over, under], _config(allow_same_fixture=1))['issues']
        self.assertTrue(any(check == 'contradictory' and result == 'fail' for check, result, _detail in issues))

    def test_ranges_keep_a_single_and_do_not_pad_it(self):
        self.assertEqual(len(RANGE_TIERS), 10)
        self.assertEqual(RANGE_TIERS[0]['low'], 1.50)
        self.assertEqual(RANGE_TIERS[-1]['high'], 1000.00)
        short = _row(decimal_odds=1.80, probability=0.60, settlement_kind='binary')
        alone = optimize_candidates(
            [short], _config(ranges=default_tier_policy()['tiers'], min_legs=1, max_legs=4), self.now,
        )
        generated = [item for item in alone['tiers'] if item['tickets']]
        self.assertEqual(len(generated), 1)
        self.assertEqual(generated[0]['tier_id'], '1.50-1.99')
        self.assertEqual(generated[0]['tickets'][0]['status'], 'qualified')
        self.assertEqual(len(generated[0]['tickets'][0]['legs']), 1)
        self.assertTrue(all(item['status'] == 'no_qualifying_ticket' for item in alone['tiers'] if item['tier_id'] != '1.50-1.99'))
        below = optimize_candidates(
            [_row(decimal_odds=1.20, probability=0.80, settlement_kind='binary')],
            _config(ranges=default_tier_policy()['tiers'], min_legs=1, max_legs=4), self.now,
        )
        self.assertTrue(all(item['tickets'] == [] for item in below['tiers']))
        self.assertIn('No selection was added', below['tiers'][0]['reason'])

    def test_longer_combination_is_a_separate_range_and_extreme_tiers_stay_in_simulation(self):
        left = _row(decimal_odds=1.80, probability=0.60, settlement_kind='binary')
        right = _row(
            opportunity_id='opp-2', fixture_id='fixture-2', home='Home B', away='Away B',
            decimal_odds=1.80, probability=0.58, settlement_kind='binary',
        )
        result = optimize_candidates(
            [left, right], _config(ranges=default_tier_policy()['tiers'], min_legs=1, max_legs=4), self.now,
        )
        first = next(item for item in result['tiers'] if item['tier_id'] == '1.50-1.99')
        third = next(item for item in result['tiers'] if item['tier_id'] == '3.00-4.99')
        self.assertTrue(all(len(ticket['legs']) == 1 for ticket in first['tickets']))
        self.assertEqual(len(third['tickets'][0]['legs']), 2)
        self.assertAlmostEqual(third['tickets'][0]['combined_odds'], 3.24)
        self.assertEqual(third['tickets'][0]['status'], 'qualified')
        high_left = _row(decimal_odds=5.0, probability=0.22, settlement_kind='binary')
        high_right = _row(
            opportunity_id='opp-2', fixture_id='fixture-2', home='Home B', away='Away B',
            decimal_odds=5.0, probability=0.22, settlement_kind='binary',
        )
        extreme = optimize_candidates(
            [high_left, high_right], _config(ranges=default_tier_policy()['tiers'], min_legs=1, max_legs=2), self.now,
        )
        band = next(item for item in extreme['tiers'] if item['tier_id'] == '20.00-49.99')
        self.assertEqual(band['mode'], 'simulation')
        self.assertEqual(band['tickets'][0]['status'], 'review_required')
        self.assertEqual(band['tickets'][0]['tier_mode'], 'simulation')
        self.assertIsNotNone(band['tickets'][0]['joint_probability'])
        self.assertEqual(third['tickets'][0]['correlation_exposure'], 0)

    def test_no_combination_is_reported_instead_of_forced(self):
        accepted, _decisions = qualify_candidates([_row()], _config(targets=list(TARGETS)), self.now)
        result = optimize_candidates(accepted, _config(targets=list(TARGETS), max_legs=6), self.now)
        self.assertEqual(len(result['tiers']), len(TARGETS))
        self.assertTrue(all(item['status'] == 'no_qualifying_ticket' and item['tickets'] == [] for item in result['tiers']))
        self.assertIn('minimum leg', result['tiers'][0]['reason'])

    def test_a_ticket_accepts_25_legs_and_keeps_an_unsettled_sport_out(self):
        self.assertEqual(default_config()['max_legs'], 25)
        rows = [
            _row(opportunity_id=f'opp-{index}', fixture_id=f'fixture-{index}',
                 home=f'Home {index}', away=f'Away {index}', competition=f'League {index}',
                 decimal_odds=1.2, probability=0.9, expected_edge=0.08,
                 settlement_json=_binary(0.9))
            for index in range(25)
        ]
        rows.append(_row(opportunity_id='handball', fixture_id='handball-1', sport='handball',
                         market='MATCH_WINNER', competition='Handball League'))
        accepted, decisions = qualify_candidates(rows, _config(max_legs=25, max_per_competition=1), self.now)
        self.assertEqual(len(accepted), 25)
        self.assertTrue(all(row['sport'] == 'football' for row in accepted))
        handball = next(item for item in decisions if item['opportunity_id'] == 'handball')
        self.assertIn('settlement', handball['reason'])
        result = optimize_candidates(
            accepted, _config(targets=[2], max_legs=25, max_per_competition=1, timeout_seconds=5), self.now,
        )
        self.assertGreaterEqual(len(result['tiers'][0]['tickets'][0]['legs']), 2)
        self.assertLessEqual(len(result['tiers'][0]['tickets'][0]['legs']), 25)

    def test_a_product_that_jumps_over_the_band_still_reaches_the_target(self):
        left = _row(decimal_odds=8, probability=0.2, expected_edge=0.6, settlement_json=_binary(0.2))
        right = _row(
            opportunity_id='opp-2', fixture_id='fixture-2', home='Home B', away='Away B',
            decimal_odds=8, probability=0.2, expected_edge=0.6, settlement_json=_binary(0.2),
        )
        accepted, _decisions = qualify_candidates([left, right], _config(targets=[50], max_legs=2), self.now)
        result = optimize_candidates(accepted, _config(targets=[50], max_legs=2, tolerance=0.15), self.now)
        ticket = result['tiers'][0]['tickets'][0]
        self.assertGreaterEqual(ticket['combined_odds'], 50)
        self.assertEqual(ticket['tier_mode'], 'standard')
        self.assertLessEqual(len(ticket['legs']), 25)

    def test_shared_team_and_partial_settlement_are_not_multiplied(self):
        left = _row(home='Shared FC', away='Away A')
        right = _row(opportunity_id='opp-2', fixture_id='fixture-2', home='Shared FC', away='Away B',
                     decimal_odds=1.4, probability=0.75, settlement_json=_binary(0.75))
        accepted, _decisions = qualify_candidates([left, right], _config(max_per_team=2), self.now)
        result = optimize_candidates(accepted, _config(max_per_team=2), self.now)
        ticket = result['tiers'][0]['tickets'][0]
        self.assertEqual(ticket['status'], 'unverified_probability')
        self.assertIsNone(ticket['joint_probability'])
        self.assertIsNone(ticket['estimated_edge'])
        self.assertAlmostEqual(ticket['combined_odds'], 2.1)
        blocked = optimize_candidates(accepted, _config(max_per_team=1), self.now)
        self.assertEqual(blocked['tiers'][0]['status'], 'no_qualifying_ticket')
        partial = _row(settlement_json=json.dumps({
            'p_win': 0.5, 'p_half_win': 0, 'p_push': 0.1, 'p_half_loss': 0, 'p_loss': 0.4,
        }))
        other = _row(opportunity_id='opp-2', fixture_id='fixture-2', home='Home B', away='Away B', decimal_odds=1.4,
                     probability=0.75, settlement_json=_binary(0.75))
        partial_rows, _decisions = qualify_candidates([partial, other], _config(), self.now)
        partial_ticket = optimize_candidates(partial_rows, _config(), self.now)['tiers'][0]['tickets'][0]
        self.assertEqual(partial_ticket['probability_basis'], 'unverified')
        self.assertIsNone(partial_ticket['joint_probability'])

    def test_sport_modes_and_settlement_math(self):
        football = _row()
        basketball = _row(opportunity_id='opp-b', fixture_id='fixture-b', sport='basketball', market='MONEYLINE_HOME',
                          home='Ball A', away='Ball B', decimal_odds=1.8, probability=0.6, settlement_json=_binary(0.6))
        only_football, _decisions = qualify_candidates([football, basketball], _config(sport_mode='football'), self.now)
        self.assertEqual([item['sport'] for item in only_football], ['football'])
        only_basketball, _decisions = qualify_candidates([football, basketball], _config(sport_mode='basketball'), self.now)
        self.assertEqual([item['sport'] for item in only_basketball], ['basketball'])
        self.assertEqual(settle_result('football', 'HOME', 2, 0), 'won')
        self.assertEqual(settle_result('football', 'DRAW', 2, 0), 'lost')
        self.assertEqual(settle_result('football', 'OVER_2.5', 1, 1), 'lost')
        self.assertEqual(settle_result('football', 'OVER_2', 1, 1), 'push')
        self.assertEqual(settle_result('basketball', 'MONEYLINE_HOME', 90, 90), 'push')
        legs = [
            {'result': 'won', 'decimal_odds': 1.5},
            {'result': 'won', 'decimal_odds': 1.4},
        ]
        outcome, multiple = ticket_outcome(legs)
        self.assertEqual(outcome, 'won')
        self.assertAlmostEqual(multiple, 2.1)
        self.assertAlmostEqual(payout_multiple(legs), 2.1)
        self.assertEqual(ticket_outcome([
            {'result': 'won', 'decimal_odds': 1.5},
            {'result': 'lost', 'decimal_odds': 1.4},
        ])[0], 'lost')

    def test_legacy_builder_still_rejects_duplicate_fixtures(self):
        tickets = build_tickets([
            {'event_id': 'x', 'market': 'HOME', 'odds': 2, 'probability': 0.6, 'edge': 0.2},
            {'event_id': 'x', 'market': 'AWAY', 'odds': 3, 'probability': 0.3, 'edge': 0.1},
        ])
        self.assertTrue(all(len(ticket['legs']) == 1 for group in tickets.values() for ticket in group))


class AccumulatorPersistenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.previous = db.DB
        db.DB = Path(cls.temp.name) / 'accumulators.sqlite3'
        db.initialize()

    @classmethod
    def tearDownClass(cls):
        db.DB = cls.previous
        cls.temp.cleanup()

    def setUp(self):
        self.now = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)
        with db.connect() as connection:
            for table in (
                'sports_ai_booking_codes', 'sports_ai_ticket_settlements',
                'sports_ai_ticket_monitoring_events', 'sports_ai_ticket_correlation_checks',
                'sports_ai_ticket_qualification_logs', 'sports_ai_ticket_versions',
                'sports_ai_accumulator_legs', 'sports_ai_accumulator_tickets',
                'sports_ai_accumulator_runs', 'sports_ai_accumulator_backtests',
                'sports_ai_accumulator_performance', 'sports_ai_ticket_legs', 'sports_ai_tickets',
                'sports_ai_value_opportunities',
                'sports_ai_engine_predictions', 'sports_ai_feature_snapshots',
                'sports_ai_odds_snapshots', 'sports_ai_results', 'sports_ai_notifications',
                'sports_ai_audit_events', 'sports_ai_model_registry', 'sports_ai_training_runs',
                'sports_ai_analysis_runs', 'sports_ai_fixtures',
            ):
                connection.execute(f'DELETE FROM {table}')
            self.run_id = self._analysis(connection)
            self.football_model = self._model(connection, 'football')
            self.basketball_model = self._model(connection, 'basketball')

    def _analysis(self, connection):
        run_id = str(uuid.uuid4())
        connection.execute(
            """INSERT INTO sports_ai_analysis_runs(id,fixture_count,selection_count,ticket_count,status,details_json)
               VALUES(?,0,0,0,'complete','{}')""",
            (run_id,),
        )
        return run_id

    def _model(self, connection, sport):
        training_id = str(uuid.uuid4())
        model_id = str(uuid.uuid4())
        connection.execute(
            """INSERT INTO sports_ai_training_runs
               (id,sport,model_name,version,status,training_window_json,parameters_json,metrics_json,sample_size)
               VALUES(?,?,?,'test','validated','{}','{}','{"calibration_error":0.04,"holdout_fixtures":40}',40)""",
            (training_id, sport, f'sports-ai-{sport}-ensemble'),
        )
        connection.execute(
            """INSERT INTO sports_ai_model_registry
               (id,training_run_id,sport,model_name,version,status,active,parameters_json,metrics_json,calibration_status)
               VALUES(?,?,?,?,'test','active',1,'{}','{"calibration_error":0.04,"holdout_fixtures":40}','calibrated')""",
            (model_id, training_id, sport, f'sports-ai-{sport}-ensemble'),
        )
        return model_id

    def _opportunity(self, connection, *, sport='football', home='Home A', away='Away A', market='HOME',
                     odds=1.5, probability=0.70, edge=0.08, status='qualified', age_minutes=1, opportunity_id=None):
        fixture_id = str(uuid.uuid4())
        kickoff = self.now + timedelta(days=2)
        observed = self.now - timedelta(minutes=age_minutes)
        connection.execute(
            """INSERT INTO sports_ai_fixtures
               (id,external_id,source,sport,competition,home,away,starts_at,status,statistics_json,odds_json,observed_at)
               VALUES(?,?,?,?,?,?,?,?, 'scheduled','{}','{}',?)""",
            (fixture_id, fixture_id, 'test', sport, 'League A', home, away, kickoff.isoformat(), observed.isoformat()),
        )
        feature_id = str(uuid.uuid4())
        connection.execute(
            """INSERT INTO sports_ai_feature_snapshots
               (id,fixture_id,sport,cutoff_at,features_json,source_refs_json,generated_at)
               VALUES(?,?,?,?, '{}','[]',?)""",
            (feature_id, fixture_id, sport, kickoff.isoformat(), self.now.isoformat()),
        )
        prediction_id = str(uuid.uuid4())
        connection.execute(
            """INSERT INTO sports_ai_engine_predictions
               (id,run_id,fixture_id,model_id,feature_id,market,outcome,probability,settlement_json,explanation_json)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (prediction_id, self.run_id, fixture_id, self.football_model if sport == 'football' else self.basketball_model,
             feature_id, market, market, probability, _binary(probability), '{}'),
        )
        quote_id = str(uuid.uuid4())
        connection.execute(
            """INSERT INTO sports_ai_odds_snapshots
               (id,fixture_id,provider_id,bookmaker,market,outcome,decimal_odds,source_url,observed_at)
               VALUES(?,?,'provider-manual-odds','Book',?,?,?,'https://example.test/quote',?)""",
            (quote_id, fixture_id, market, market, odds, observed.isoformat()),
        )
        opportunity = opportunity_id or str(uuid.uuid4())
        connection.execute(
            """INSERT INTO sports_ai_value_opportunities
               (id,prediction_id,odds_id,implied_probability,expected_edge,status,reason,evaluated_at,data_completeness)
               VALUES(?,?,?,?,?,?,?,?,0.8)""",
            (opportunity, prediction_id, quote_id, 1 / odds, edge, status, status, self.now.isoformat()),
        )
        return {'fixture_id': fixture_id, 'opportunity_id': opportunity, 'kickoff': kickoff}

    def test_qualified_records_persist_and_unqualified_do_not(self):
        self._opportunity_pair()
        with db.connect() as connection:
            self._opportunity(connection, home='Late', away='Side', status='awaiting_calibration', age_minutes=1)
            self._opportunity(connection, home='Old', away='Quote', age_minutes=120)
            result = generate_accumulators(
                connection, 'tester', None, 'football', {'targets': [2], 'max_legs': 2}, 'persist-key', self.now,
            )
            again = generate_accumulators(
                connection, 'tester', None, 'football', {'targets': [2], 'max_legs': 2}, 'persist-key', self.now,
            )
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['ticket_count'], 1)
        self.assertEqual(again['run_id'], result['run_id'])
        self.assertTrue(again['idempotent'])
        ticket = result['tickets'][0]
        self.assertEqual(ticket['leg_count'], 2)
        self.assertEqual(ticket['sport_mode'], 'football')
        self.assertAlmostEqual(ticket['combined_odds'], 2.1)
        homes = {leg['home'] for leg in ticket['legs']}
        self.assertEqual(homes, {'Home A', 'Home B'})

    def _opportunity_pair(self):
        with db.connect() as connection:
            self._opportunity(connection, home='Home A', away='Away A', odds=1.5, probability=0.70)
            self._opportunity(connection, home='Home B', away='Away B', odds=1.4, probability=0.75)

    def test_modes_monitor_settlement_and_manual_code(self):
        with db.connect() as connection:
            self._opportunity(connection, sport='football', home='Home A', away='Away A', odds=1.5, probability=0.70)
            self._opportunity(connection, sport='basketball', home='Ball A', away='Ball B', market='MONEYLINE_HOME',
                              odds=1.4, probability=0.75)
            mixed = generate_accumulators(
                connection, 'tester', None, 'mixed', {'targets': [2], 'max_legs': 2}, 'mixed-key', self.now,
            )
            self.assertEqual(mixed['tickets'][0]['sport_mode'], 'mixed')
            only_ball = generate_accumulators(
                connection, 'tester', None, 'basketball', {'targets': [2], 'max_legs': 2}, 'ball-key', self.now,
            )
            self.assertEqual(only_ball['status'], 'no_qualifying_ticket')
            ticket_id = mixed['tickets'][0]['id']
            for leg in mixed['tickets'][0]['legs']:
                connection.execute(
                    """INSERT INTO sports_ai_results(id,fixture_id,home_score,away_score,source,status,settled_at)
                       VALUES(?,?,2,0,'official','final',?)""",
                    (str(uuid.uuid4()), leg['fixture_id'], (self.now + timedelta(days=2, hours=1)).isoformat()),
                )
            settled = monitor_accumulators(
                connection, 'tester', None, 'monitor-1', self.now + timedelta(days=2, hours=3),
            )
            repeat = monitor_accumulators(
                connection, 'tester', None, 'monitor-1', self.now + timedelta(days=2, hours=3),
            )
            row = connection.execute(
                'SELECT outcome,hypothetical_profit,stake_assumption,details_json FROM sports_ai_ticket_settlements WHERE ticket_id=?',
                (ticket_id,),
            ).fetchone()
            backtest = run_backtest(connection, 'tester', None, self.now + timedelta(days=3))
        self.assertEqual(settled['settled'], 1)
        self.assertTrue(repeat['idempotent'])
        self.assertEqual(row['outcome'], 'won')
        self.assertEqual(row['stake_assumption'], 1)
        self.assertAlmostEqual(row['hypothetical_profit'], 1.1)
        self.assertIn('not an actual betting return', row['details_json'].lower())
        self.assertEqual(backtest['sample_size'], 1)
        self.assertIn('not actual betting returns', backtest['label'])
        self.assertFalse(integration_status()['automatic_booking_code'])
        self.assertIsNone(automatic_booking_code(ticket_id)['booking_code'])
        with db.connect() as connection:
            stored = register_manual_code(
                connection, 'tester', None, ticket_id, 'USER-CODE-42', 'Copied from the SportyBet slip', self.now,
            )
            blocked = register_manual_code(
                connection, 'tester', None, ticket_id, 'LATER-CODE', 'After kickoff', self.now + timedelta(days=3),
            )
            saved = connection.execute(
                'SELECT code,status,integration_status FROM sports_ai_booking_codes WHERE accumulator_ticket_id=?',
                (ticket_id,),
            ).fetchone()
        self.assertEqual(stored['code'], 'USER-CODE-42')
        self.assertEqual(stored['status'], 'unverified')
        self.assertEqual(stored['automation'], 'unavailable')
        self.assertIn('not stored', blocked['error'])
        self.assertEqual(saved['code'], 'USER-CODE-42')
        self.assertEqual(saved['integration_status'], 'manual_only')

    def test_legacy_booking_conflict_still_updates(self):
        with db.connect() as connection:
            connection.execute(
                """INSERT INTO sports_ai_tickets
                   (id,run_id,tier,combined_odds,estimated_probability,estimated_ev,selection_count)
                   VALUES('legacy-ticket',?,2,2,0.4,0.1,1)""",
                (self.run_id,),
            )
            for code in ('FIRST', 'SECOND'):
                connection.execute(
                    """INSERT INTO sports_ai_booking_codes
                       (id,ticket_id,code,verification_method,evidence,status)
                       VALUES(?,?,?,'manual','user slip','unverified')
                       ON CONFLICT(ticket_id) DO UPDATE SET code=excluded.code""",
                    (str(uuid.uuid4()), 'legacy-ticket', code),
                )
            saved = connection.execute(
                "SELECT code FROM sports_ai_booking_codes WHERE ticket_id='legacy-ticket'",
            ).fetchone()[0]
        self.assertEqual(saved, 'SECOND')

    def test_remove_creates_a_review_version_without_editing_the_original(self):
        self._opportunity_pair()
        with db.connect() as connection:
            result = generate_accumulators(
                connection, 'tester', None, 'football', {'targets': [2], 'max_legs': 2}, 'version-key', self.now,
            )
            original = result['tickets'][0]
            from accumulator_engine import remove_leg
            revised = remove_leg(connection, 'tester', None, original['id'], original['legs'][0]['opportunity_id'], self.now)
            untouched = connection.execute(
                'SELECT leg_count,status FROM sports_ai_accumulator_tickets WHERE id=?', (original['id'],),
            ).fetchone()
        self.assertEqual(untouched['leg_count'], 2)
        self.assertEqual(untouched['status'], 'qualified')
        self.assertEqual(revised['status'], 'review_required')
        self.assertEqual(revised['version'], 2)
        self.assertEqual(revised['family_id'], original['family_id'])
        self.assertEqual(revised['leg_count'], 1)

    def test_range_generation_stores_a_single_and_blocks_extreme_activation(self):
        self._opportunity_pair()
        with db.connect() as connection:
            result = generate_accumulators(
                connection, 'tester', None, 'football', {'max_legs': 2}, 'range-key', self.now,
            )
            with self.assertRaises(ValueError):
                save_tier_policy(connection, {'tiers': [{'id': '500.00-1000.00', 'mode': 'standard'}]})
        self.assertEqual(result['status'], 'complete')
        stored = {ticket['tier']: ticket for ticket in result['tickets']}
        self.assertEqual(stored[150]['leg_count'], 1)
        self.assertEqual(stored[150]['status'], 'qualified')
        self.assertEqual(stored[200]['leg_count'], 2)
        self.assertAlmostEqual(stored[200]['combined_odds'], 2.1)
        self.assertTrue(all(ticket['parameters']['tier_mode'] == 'standard' for ticket in result['tickets']))
        self.assertNotIn(50000, stored)

    def test_monitor_demotes_a_ticket_when_qualification_lapses(self):
        self._opportunity_pair()
        with db.connect() as connection:
            connection.execute('DELETE FROM sports_ai_qualification_decisions')
            generated = generate_accumulators(
                connection, 'tester', None, 'football', {'targets': [2], 'max_legs': 2}, 'lapse-key', self.now,
            )
            ticket_id = generated['tickets'][0]['id']
            legs = connection.execute(
                'SELECT fixture_id, market FROM sports_ai_accumulator_legs WHERE ticket_id=?', (ticket_id,),
            ).fetchall()
            for index, leg in enumerate(legs):
                connection.execute(
                    """INSERT INTO sports_ai_qualification_decisions
                       (id,prediction_id,fixture_id,sport,market,state,reason_code,reason,evidence_json,products_json,
                        policy_version,decision_version,fingerprint,created_at,updated_at)
                       VALUES(?,?,?,'football',?,'QUALIFIED','QUALIFIED_OK','ok','{}','["booking"]',1,1,?,?,?)""",
                    (str(uuid.uuid4()), str(uuid.uuid4()), leg['fixture_id'], leg['market'], f'fp-{index}',
                     self.now.isoformat(), self.now.isoformat()),
                )
            held = monitor_accumulators(connection, 'tester', None, 'monitor-held', self.now)
            held_status = connection.execute(
                'SELECT status FROM sports_ai_accumulator_tickets WHERE id=?', (ticket_id,),
            ).fetchone()['status']
            connection.execute('DELETE FROM sports_ai_qualification_decisions')
            lapsed = monitor_accumulators(connection, 'tester', None, 'monitor-lapsed', self.now)
            lapsed_status = connection.execute(
                'SELECT status FROM sports_ai_accumulator_tickets WHERE id=?', (ticket_id,),
            ).fetchone()['status']
        self.assertEqual(held_status, 'qualified')
        self.assertEqual(held.get('review_required', 0), 0)
        self.assertEqual(lapsed_status, 'review_required')
        self.assertEqual(lapsed.get('review_required'), 1)
