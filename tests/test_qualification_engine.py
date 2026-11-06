import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

from qualification_engine import _apply_limits, decide, default_policy, require_bookable


NOW = datetime(2026, 11, 4, 9, 0, tzinfo=timezone.utc)


def policy():
    return default_policy({'min_edge': 0.05, 'odds_max_age_minutes': 15})


def row(**overrides):
    base = {
        'fixture_id': 'fx', 'home': 'Home', 'away': 'Away', 'sport': 'football',
        'fixture_status': 'scheduled', 'starts_at': (NOW + timedelta(days=1)).isoformat(),
        'market': 'HOME', 'probability': 0.62, 'decimal_odds': 1.9,
        'odds_observed_at': (NOW - timedelta(minutes=2)).isoformat(),
        'expected_edge': 0.08, 'completeness': 0.8,
        'lifecycle': 'active', 'model_active': 1, 'calibration_status': 'validated',
        'metrics': {'calibration_error': 0.05, 'holdout_fixtures': 40, 'brier_score': 0.21},
        'features': {}, 'quality_hold': False,
    }
    base.update(overrides)
    return base


class QualificationDecisionTests(unittest.TestCase):
    def test_calibrated_fresh_edge_qualifies_for_booking(self):
        result = decide(row(), policy(), NOW)
        self.assertEqual(result['state'], 'QUALIFIED')
        self.assertEqual(result['reason_code'], 'QUALIFIED_OK')
        self.assertIn('booking', result['products'])
        self.assertNotIn('weekly_draw', result['products'])

    def test_draw_inside_seven_days_is_shared_with_weekly_draw(self):
        result = decide(row(market='DRAW', probability=0.30, expected_edge=0.07), policy(), NOW)
        self.assertEqual(result['state'], 'QUALIFIED')
        self.assertIn('weekly_draw', result['products'])

    def test_a_regulation_draw_in_any_goal_sport_is_a_weekly_draw(self):
        hockey = decide(row(sport='ice_hockey', market='DRAW', probability=0.28, expected_edge=0.07), policy(), NOW)
        virtual = decide(row(sport='vfootball', market='DRAW', probability=0.31, expected_edge=0.07), policy(), NOW)
        tennis = decide(row(sport='tennis', market='MONEYLINE_HOME'), policy(), NOW)
        self.assertIn('weekly_draw', hockey['products'])
        self.assertIn('weekly_draw', virtual['products'])
        self.assertNotIn('weekly_draw', tennis['products'])

    def test_same_day_kickoff_is_shared_with_daily_rollover(self):
        result = decide(row(starts_at=(NOW + timedelta(hours=5)).isoformat()), policy(), NOW)
        self.assertIn('daily_rollover', result['products'])

    def test_missing_price_waits_for_data(self):
        result = decide(row(decimal_odds=None, odds_observed_at=None), policy(), NOW)
        self.assertEqual(result['state'], 'AWAITING_DATA')
        self.assertEqual(result['reason_code'], 'MARKET_UNAVAILABLE')

    def test_uncalibrated_market_cannot_qualify(self):
        result = decide(row(market='BTTS_YES'), policy(), NOW)
        self.assertEqual(result['state'], 'AWAITING_DATA')
        self.assertEqual(result['reason_code'], 'MARKET_NOT_CALIBRATED')
        self.assertEqual(result['products'], [])

    def test_inactive_model_is_suspended(self):
        result = decide(row(lifecycle='research', model_active=0), policy(), NOW)
        self.assertEqual(result['state'], 'SUSPENDED')
        self.assertEqual(result['reason_code'], 'MODEL_INACTIVE')

    def test_deteriorating_model_is_suspended(self):
        result = decide(row(metrics={'calibration_error': 0.41, 'holdout_fixtures': 40}), policy(), NOW)
        self.assertEqual(result['reason_code'], 'MODEL_DETERIORATING')

    def test_started_fixture_expires(self):
        result = decide(row(starts_at=(NOW - timedelta(hours=1)).isoformat()), policy(), NOW)
        self.assertEqual(result['state'], 'EXPIRED')
        self.assertEqual(result['reason_code'], 'FIXTURE_STARTED')

    def test_stale_price_expires(self):
        result = decide(row(odds_observed_at=(NOW - timedelta(minutes=40)).isoformat()), policy(), NOW)
        self.assertEqual(result['reason_code'], 'ODDS_STALE')

    def test_positive_edge_below_threshold_is_monitored(self):
        result = decide(row(expected_edge=0.02), policy(), NOW)
        self.assertEqual(result['state'], 'MONITORING')
        self.assertEqual(result['reason_code'], 'EDGE_BELOW_MARKET')

    def test_second_market_on_the_same_fixture_is_not_bookable(self):
        home = row(expected_edge=0.12)
        away = row(market='AWAY', probability=0.24, expected_edge=0.06)
        first = {'row': home, 'result': decide(home, policy(), NOW)}
        second = {'row': away, 'result': decide(away, policy(), NOW)}
        _apply_limits([first, second], policy())
        states = {item['row']['market']: item['result']['state'] for item in (first, second)}
        self.assertEqual(states['HOME'], 'QUALIFIED')
        self.assertEqual(states['AWAY'], 'MONITORING')
        self.assertEqual(second['result']['reason_code'], 'CORRELATION_LIMIT')

    def test_booking_without_a_decision_is_rejected(self):
        class Empty:
            def execute(self, *args):
                return self
            def fetchone(self):
                return None
        with self.assertRaises(ValueError):
            require_bookable(Empty(), [{'fixture_id': 'fx', 'market': 'HOME'}])

    def test_extreme_edge_stays_out_of_qualification_until_reviewed(self):
        held = decide(row(expected_edge=0.71), policy(), NOW)
        self.assertEqual(held['state'], 'MONITORING')
        self.assertEqual(held['reason_code'], 'RISK_REVIEW')
        self.assertEqual(held['products'], [])
        draw = decide(row(market='DRAW', probability=0.36, decimal_odds=4.0, expected_edge=0.20), policy(), NOW)
        self.assertEqual(draw['reason_code'], 'RISK_REVIEW')
        released = decide(row(expected_edge=0.71, risk_review_approved=True), policy(), NOW)
        self.assertEqual(released['state'], 'QUALIFIED')
        disabled = policy()
        disabled['risk_review_enabled'] = False
        self.assertEqual(decide(row(expected_edge=0.71), disabled, NOW)['state'], 'QUALIFIED')

    def test_unmodelled_sport_produces_no_probability_claim(self):
        result = decide(row(sport='tennis', market='HOME'), policy(), NOW)
        self.assertEqual(result['state'], 'REJECTED')
        self.assertEqual(result['reason_code'], 'SPORT_NOT_MODELLED')
        self.assertIsNone(result['rank_score'])

    def test_ice_hockey_result_uses_the_stored_model(self):
        result = decide(row(sport='ice_hockey', market='HOME'), policy(), NOW)
        self.assertEqual(result['state'], 'QUALIFIED')
        self.assertEqual(result['reason_code'], 'QUALIFIED_OK')
