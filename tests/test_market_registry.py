import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

import db
from accumulator_engine import combine_ticket
from discovery_catalogue import (
    COMPETITIONS, MARKET_GROUPS, catalogue_summary, ensure_reference_catalogue, load_reference_catalogue, match_competition,
)
from market_registry import _canonical, assign_status, choose_best, holdout_passes, refresh_market_registry
from prediction_engine import football_market
from qualification_engine import decide, default_policy
from accumulator_engine import settle_result
from sport_adapters import probability
from sport_support import coverage_status
from sportybet_research import map_market
from prediction_engine import market_probability


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
        self.assertEqual(assign_status(0, None, 0, blocked=True), 'data_pending')
        self.assertEqual(assign_status(0, None, 0, unsupported=True), 'unsupported')
        self.assertEqual(assign_status(101, 0.04, 40, parent_active=False), 'suspended')

    def test_unsupported_adapter_returns_no_probability(self):
        self.assertIsNone(probability('tennis', 'MATCH_WINNER', {'anything': 1}))
        self.assertIsNone(probability('ice_hockey', 'TOTAL_GOALS'))
        self.assertIsNone(probability('table_tennis', 'MATCH_WINNER'))
        self.assertIsNone(probability('tischtennis', 'TOTAL_POINTS'))

    def test_best_market_follows_expected_edge(self):
        chosen = choose_best([
            {'fixture_id': 'a', 'market': 'HOME', 'expected_edge': 0.06, 'probability': 0.72},
            {'fixture_id': 'a', 'market': 'OVER_2.5', 'expected_edge': 0.11, 'probability': 0.41},
            {'fixture_id': 'b', 'market': 'DRAW', 'expected_edge': 0.08, 'probability': 0.3},
        ])
        by_fixture = {item['fixture_id']: item['market'] for item in chosen}
        self.assertEqual(by_fixture['a'], 'HOME')
        self.assertEqual(by_fixture['b'], 'DRAW')
        tied = choose_best([
            {'fixture_id': 'c', 'market': 'HOME', 'probability': 0.81, 'confidence': 0.70},
            {'fixture_id': 'c', 'market': 'OVER_2.5', 'probability': 0.81, 'confidence': 0.91},
        ])
        self.assertEqual(tied[0]['market'], 'OVER_2.5')

    def test_goal_sports_use_the_result_market_names(self):
        self.assertEqual(_canonical('ice_hockey', 'h2h', 'home', None), 'HOME')
        self.assertEqual(_canonical('ice_hockey', 'h2h', 'draw', None), 'DRAW')
        self.assertEqual(_canonical('frauenfussball', 'h2h', 'away', None), 'AWAY')
        self.assertEqual(_canonical('basketball', 'h2h', 'home', None), 'MONEYLINE_HOME')
        self.assertIsNone(_canonical('basketball', 'h2h', 'draw', None))
        self.assertEqual(_canonical('tennis', 'h2h', 'home', None), 'MONEYLINE_HOME')

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
        self.assertEqual(by_key[('football', 'CORNERS')], 'data_pending')
        self.assertEqual(by_key[('football', 'CARDS')], 'data_pending')
        self.assertEqual(by_key[('football', 'CORRECT_SCORE')], 'research')
        self.assertEqual(by_key[('tennis', 'MATCH_WINNER')], 'research')
        self.assertEqual(by_key[('tennis', 'MONEYLINE_HOME')], 'research')
        self.assertEqual(by_key[('cricket', 'MATCH_WINNER')], 'unsupported')
        self.assertEqual(by_key[('table_tennis', 'MONEYLINE_HOME')], 'research')
        self.assertEqual(by_key[('volleyball', 'SET_HANDICAP')], 'unsupported')
        self.assertEqual(by_key[('mma', 'MONEYLINE_AWAY')], 'research')
        self.assertNotIn('active', set(by_key.values()))
        self.assertFalse(report['places_wager'])
        self.assertFalse(report['thresholds_changed'])
        self.assertTrue(any(item['sport'] == 'tennis' and item.get('unsupported', 0) for item in report['coverage']))

    def test_a_stored_quote_does_not_activate_an_unmodelled_sport(self):
        with db.connect() as connection:
            connection.execute(
                """INSERT INTO sports_ai_fixtures
                   (id,external_id,source,sport,competition,home,away,starts_at,status,statistics_json,odds_json,observed_at)
                   VALUES('fx-tennis','ext-tennis','test','tennis','ATP','Player A','Player B',?, 'scheduled','{}','{}',?)""",
                (NOW.isoformat(), NOW.isoformat()),
            )
            connection.execute(
                """INSERT INTO sports_ai_provider_registry
                   (id,provider_key,display_name,sport,adapter,enabled,terms_reviewed,status)
                   VALUES('provider-test','sportybet-test','SportyBet','both','authorized_json',1,1,'operational')""",
            )
            connection.execute(
                """INSERT INTO sports_ai_odds_snapshots
                   (id,fixture_id,provider_id,bookmaker,market,outcome,decimal_odds,source_url,observed_at)
                   VALUES('odds-tennis','fx-tennis','provider-test','SportyBet','h2h','home',1.8,'https://www.sportybet.com/ng/',?)""",
                (NOW.isoformat(),),
            )
            report = refresh_market_registry(connection)
        tennis = [item for item in report['items'] if item['sport'] == 'tennis' and item['market_key'] == 'MONEYLINE_HOME']
        self.assertEqual(len(tennis), 1)
        self.assertEqual(tennis[0]['status'], 'research')
        self.assertIsNone(tennis[0]['evidence'].get('probability'))
        self.assertNotIn('active', {item['status'] for item in report['items'] if item['sport'] == 'tennis'})

    def test_the_starting_catalogue_registers_every_group_without_activating_it(self):
        summary = catalogue_summary()
        self.assertEqual(summary['sports'], 4)
        self.assertEqual(summary['competitions'], 23)
        self.assertEqual(summary['market_groups'], 61)
        self.assertEqual(summary['by_sport']['football'], {'competitions': 10, 'markets': 17})
        self.assertEqual(summary['by_sport']['basketball'], {'competitions': 5, 'markets': 15})
        self.assertEqual(summary['by_sport']['tennis'], {'competitions': 4, 'markets': 14})
        self.assertEqual(summary['by_sport']['ice_hockey'], {'competitions': 4, 'markets': 15})
        self.assertEqual(len(COMPETITIONS), 23)
        self.assertEqual(len(MARKET_GROUPS), 61)
        self.assertEqual(match_competition('football', 'English Premier League 2026/27'), 'england-premier-league')
        self.assertEqual(match_competition('football', 'UEFA Champions League'), 'uefa-champions-league')
        self.assertEqual(match_competition('football', 'EFL Championship'), 'england-championship')
        self.assertEqual(match_competition('basketball', 'NBA'), 'usa-nba')
        with db.connect() as connection:
            report = refresh_market_registry(connection)
        by_key = {(item['sport'], item['market_key']): item['status'] for item in report['items']}
        for group in MARKET_GROUPS:
            self.assertIn((group['sport'], group['key']), by_key)
            if group['kind'] == 'unsupported':
                self.assertEqual(by_key[(group['sport'], group['key'])], 'unsupported')
            if group['kind'] == 'data_pending':
                self.assertIn(by_key[(group['sport'], group['key'])], {'data_pending', 'research'})
        self.assertEqual(by_key[('ice_hockey', 'MATCH_WINNER_INCL_OT')], 'unsupported')
        self.assertEqual(by_key[('ice_hockey', 'REGULATION_RESULT')], 'research')
        self.assertEqual(by_key[('football', 'CORNERS')], 'data_pending')
        self.assertNotIn('active', set(by_key.values()))
        self.assertEqual(report['catalogue']['registered'], 23)
        self.assertEqual(report['catalogue']['fixtures_invented'], 0)
        self.assertEqual(report['reference_catalogue']['competitions'], 932)
        self.assertEqual(report['reference_catalogue']['football'], 603)
        self.assertEqual(report['reference_catalogue']['fixtures_invented'], 0)
        self.assertFalse(report['reference_catalogue']['places_wager'])
        reference = [item for item in report['items'] if item['period'] == 'reference']
        self.assertTrue(reference)
        self.assertTrue(all(item['status'] == 'data_pending' for item in reference))
        self.assertTrue(all(item['evidence'].get('probability') is None for item in reference))
        self.assertTrue(all(item['evidence'].get('activated') is False for item in reference))
        priced = football_market({'expected_home_goals': 1.4, 'expected_away_goals': 1.1}, 'ODD_GOALS')
        other = football_market({'expected_home_goals': 1.4, 'expected_away_goals': 1.1}, 'EVEN_GOALS')
        self.assertAlmostEqual(priced['p_win'] + other['p_win'], 1, places=6)
        self.assertEqual(settle_result('football', 'MARGIN_HOME_1', 2, 1), 'won')
        self.assertEqual(settle_result('football', 'EUROPEAN_HOME_-1', 2, 1), 'push')

    def test_every_workbook_league_is_registered_and_no_fixture_is_invented(self):
        catalogue = load_reference_catalogue()
        expected = {
            f"{item['country']} — {item['name']}"
            for item in catalogue['competitions']
            if item['sport'] == 'football'
        }
        with db.connect() as connection:
            before = connection.execute('SELECT COUNT(*) FROM sports_ai_fixtures').fetchone()[0]
            report = ensure_reference_catalogue(connection)
            stored = {
                row['name'] for row in connection.execute(
                    "SELECT name FROM sports_ai_leagues WHERE sport_code='football' AND season='catalogue'",
                )
            }
            divisions = {
                row['division'] for row in connection.execute(
                    "SELECT DISTINCT division FROM sports_ai_leagues WHERE sport_code='football' AND season='catalogue'",
                )
            }
            snooker = connection.execute(
                "SELECT prediction_status FROM sports_ai_sports WHERE code='snooker'",
            ).fetchone()
            football = connection.execute(
                "SELECT prediction_status FROM sports_ai_sports WHERE code='football'",
            ).fetchone()
            after = connection.execute('SELECT COUNT(*) FROM sports_ai_fixtures').fetchone()[0]
            again = ensure_reference_catalogue(connection)
        self.assertEqual(len(expected), 603)
        self.assertEqual(stored, expected)
        self.assertEqual(report['competitions'], 932)
        self.assertEqual(again['added'], 0)
        self.assertEqual(before, after)
        self.assertEqual(football['prediction_status'], 'modelled')
        self.assertEqual(snooker['prediction_status'], 'not_modelled')
        for division in (
            'First Division', 'Second Division', 'Lower / Regional', 'Domestic Cup',
            "Women's top divisions", 'Youth U17', 'Friendlies',
        ):
            self.assertIn(division, divisions)

    def test_collected_sports_wait_for_their_own_model(self):
        self.assertEqual(coverage_status('tennis', set(), 0), 'Research')
        self.assertEqual(coverage_status('efootball', set(), 0), 'Research')
        self.assertEqual(coverage_status('vfootball', set(), 0), 'Research')
        self.assertEqual(coverage_status('volleyball', set(), 0), 'Research')
        self.assertEqual(coverage_status('mma', set(), 0), 'Research')
        self.assertEqual(coverage_status('table_tennis', set(), 0), 'Research')
        self.assertEqual(coverage_status('football', {'football'}, 2), 'Active')
        self.assertEqual(coverage_status('basketball', set(), 1), 'Research')
        self.assertEqual(coverage_status('darts', set(), 0), 'Unsupported')
        self.assertEqual(settle_result('tennis', 'MONEYLINE_HOME', 2, 1), 'won')
        self.assertEqual(settle_result('mma', 'MONEYLINE_AWAY', 0, 1), 'won')
        self.assertEqual(settle_result('volleyball', 'MONEYLINE_HOME', 1, 3), 'lost')
        self.assertIsNone(settle_result('tennis', 'TOTAL_GAMES', 2, 1))
        self.assertEqual(map_market('efootball', '1X2', 'Home')['canonical_market'], 'HOME')
        self.assertEqual(map_market('tennis', 'Winner', 'Home')['canonical_market'], 'MONEYLINE_HOME')
        self.assertEqual(map_market('table_tennis', 'Winner', 'Player B', home='Player A', away='Player B')['canonical_market'], 'MONEYLINE_AWAY')
        self.assertFalse(map_market('tennis', 'Total Games', 'Over 22.5')['supported'])
        self.assertIsNone(market_probability('tennis', 'MONEYLINE_HOME', {'anything': 1}))
        self.assertIsNone(probability('tennis', 'MATCH_WINNER', {'anything': 1}))


class ScoreDistributionTests(unittest.TestCase):
    def test_identity_settings_leave_the_grid_unchanged(self):
        features = {'expected_home_goals': 1.4, 'expected_away_goals': 1.1}
        plain = football_market(features, 'BTTS_YES')
        same = football_market(features, 'BTTS_YES', 0.0, 1.0, 1.0)
        self.assertAlmostEqual(plain['p_win'], same['p_win'], places=8)

    def test_both_score_weight_keeps_the_match_result(self):
        features = {'expected_home_goals': 1.4, 'expected_away_goals': 1.1}
        home = football_market(features, 'HOME', 0.0, 1.0, 1.0)
        moved = football_market(features, 'HOME', 0.0, 1.0, 2.0)
        draw = football_market(features, 'DRAW', 0.0, 1.0, 1.0)
        draw_moved = football_market(features, 'DRAW', 0.0, 1.0, 2.0)
        btts = football_market(features, 'BTTS_YES', 0.0, 1.0, 1.0)
        btts_moved = football_market(features, 'BTTS_YES', 0.0, 1.0, 2.0)
        self.assertAlmostEqual(home['p_win'], moved['p_win'], places=6)
        self.assertAlmostEqual(draw['p_win'], draw_moved['p_win'], places=6)
        self.assertGreater(btts_moved['p_win'], btts['p_win'])
        self.assertAlmostEqual(btts['p_win'] + btts['p_loss'], 1.0, places=6)

    def test_a_line_without_its_own_holdout_stays_off(self):
        self.assertFalse(holdout_passes({}))
        self.assertFalse(holdout_passes({'calibration_error': 0.05, 'sample_size': 12}))
        self.assertFalse(holdout_passes({'calibration_error': 0.21, 'sample_size': 40}))
        self.assertTrue(holdout_passes({'calibration_error': 0.09, 'sample_size': 30}))

    def test_team_totals_map_separately_from_the_match_total(self):
        team = map_market('football', 'Home Team Goals', 'Over 1.5', 1.5)
        match = map_market('football', 'Over/Under', 'Over 1.5', 1.5)
        asian = map_market('football', 'Asian Handicap', 'Home', -0.5)
        self.assertEqual(team['canonical_market'], 'HOME_OVER_1.5')
        self.assertEqual(match['canonical_market'], 'OVER_1.5')
        self.assertEqual(asian['canonical_market'], 'HOME_HANDICAP_-0.5')
