import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

import db
from accumulator_engine import combine_ticket
from market_registry import assign_status, choose_best, refresh_market_registry
from qualification_engine import decide, default_policy
from sport_adapters import probability


NOW = datetime(2026, 11, 4, 9, 0, tzinfo=timezone.utc)


def _row(**overrides):
    base = {
        'fixture_id': 'fx', 'home': 'Home', 'away': 'Away', 'sport': 'football',
        'fixture_status': 'scheduled', 'starts_at': (NOW + timedelta(days=1)).isoformat(),
        'market': 'BTTS_YES', 'probability': 0.62, 'decimal_odds': 1.9,
        'odds_observed_at': (NOW - timedelta(minutes=2)).isoformat(),
        'expected_edge': 0.18, 'completeness': 0.8,
        'lifecycle': 'active', 'model_active': 1, 'calibration_status': 'validated',
        'metrics': {'calibration_error': 0.05, 'holdout_fixtures': 40},
        'features': {}, 'quality_hold': False, 'market_registry_active': True,
    }
    base.update(overrides)
    return base


class MarketStatusTests(unittest.TestCase):
    def test_activation_keeps_the_existing_sample_and_calibration_ceilings(self):
        self.assertEqual(assign_status(101, 0.04, 40), 'active')
        self.assertEqual(assign_status(101, 0.04, 0), 'validated')
        self.assertEqual(assign_status(12, 0.04, 40), 'research')
        self.assertEqual(assign_status(101, 0.27, 40), 'research')
        self.assertEqual(assign_status(0, None, 0, blocked=True), 'blocked')
        self.assertEqual(assign_status(0, None, 0, unsupported=True), 'unsupported')
        self.assertEqual(assign_status(101, 0.04, 40, parent_active=False), 'suspended')

    def test_unsupported_adapter_returns_no_probability(self):
        self.assertIsNone(probability('tennis', 'MATCH_WINNER', {'anything': 1}))
        self.assertIsNone(probability('ice_hockey', 'TOTAL_GOALS'))

    def test_best_market_follows_expected_edge(self):
        chosen = choose_best([
            {'fixture_id': 'a', 'market': 'HOME', 'expected_edge': 0.06, 'probability': 0.72},
            {'fixture_id': 'a', 'market': 'OVER_2.5', 'expected_edge': 0.11, 'probability': 0.41},
            {'fixture_id': 'b', 'market': 'DRAW', 'expected_edge': 0.08, 'probability': 0.3},
        ])
        by_fixture = {item['fixture_id']: item['market'] for item in chosen}
        self.assertEqual(by_fixture['a'], 'OVER_2.5')
        self.assertEqual(by_fixture['b'], 'DRAW')

    def test_registry_active_market_can_qualify_without_reusing_the_result_set(self):
        result = decide(_row(), default_policy({'min_edge': 0.05}), NOW)
        self.assertEqual(result['state'], 'QUALIFIED')
        self.assertIn('booking', result['products'])
        self.assertNotIn('weekly_draw', result['products'])

    def test_same_fixture_score_markets_are_not_combined(self):
        leg = {
            'fixture_id': 'fx', 'sport': 'football', 'market': 'HOME', 'home': 'Home', 'away': 'Away',
            'competition': 'League', 'decimal_odds': 1.8, 'probability': 0.5, 'settlement_kind': 'binary',
        }
        other = {**leg, 'market': 'OVER_2.5', 'decimal_odds': 1.9, 'probability': 0.55}
        issues = combine_ticket([leg, other], {'allow_same_fixture': 1, 'max_per_team': 2, 'max_per_competition': 3})['issues']
        self.assertTrue(any(check == 'related_markets' and outcome == 'fail' for check, outcome, _detail in issues))


class RegistryRefreshTests(unittest.TestCase):
    def setUp(self):
        self.previous = db.DB
        self.temp = tempfile.TemporaryDirectory()
        db.DB = Path(self.temp.name) / 'sports.sqlite3'
        db.initialize()

    def tearDown(self):
        db.DB = self.previous
        self.temp.cleanup()

    def test_empty_store_does_not_activate_named_markets(self):
        with db.connect() as connection:
            report = refresh_market_registry(connection)
        by_key = {(item['sport'], item['market_key']): item['status'] for item in report['items']}
        self.assertEqual(by_key[('football', 'CORNERS')], 'blocked')
        self.assertEqual(by_key[('football', 'CARDS')], 'blocked')
        self.assertEqual(by_key[('football', 'CORRECT_SCORE')], 'research')
        self.assertEqual(by_key[('tennis', 'MATCH_WINNER')], 'unsupported')
        self.assertEqual(by_key[('cricket', 'MATCH_WINNER')], 'unsupported')
        self.assertNotIn('active', set(by_key.values()))
        self.assertFalse(report['places_wager'])
        self.assertFalse(report['thresholds_changed'])
