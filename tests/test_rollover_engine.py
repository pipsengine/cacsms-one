import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

from rollover_engine import (
    WARNING,
    create_campaign,
    next_stake,
    process_rollover,
    run_day,
    selection_open,
    settle_campaigns,
)
import db

NOW = datetime(2026, 11, 4, 10, 0, tzinfo=timezone.utc)
WAT = timezone(timedelta(hours=1))


def candidate(fixture_id='fx-a', market='HOME', odds=1.5):
    return {
        'eligible': [{
            'fixture_id': fixture_id, 'sport': 'football', 'market': market, 'selection': 'Home',
            'home': 'Home', 'away': 'Away', 'starts_at': '2026-11-04T18:00:00+00:00',
            'decimal_odds': odds, 'probability': 0.72, 'edge': 0.08, 'uncertainty': 0.2,
            'completeness': 0.8, 'bookmaker': 'SportyBet', 'score': 0.1,
        }],
        'excluded': [],
    }


def add_result(connection, fixture_id, home, away):
    kickoff = '2026-11-04T18:00:00+00:00'
    connection.execute(
        """INSERT INTO sports_ai_fixtures
           (id,external_id,source,sport,competition,home,away,starts_at,status,statistics_json,odds_json,observed_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (fixture_id, fixture_id, 'test', 'football', 'League', 'Home', 'Away', kickoff, 'final', '{}', '{}', kickoff),
    )
    connection.execute(
        """INSERT INTO sports_ai_results(id,fixture_id,home_score,away_score,status,source,settled_at)
           VALUES(?,?,?,?,?,?,?)""",
        (f'result-{fixture_id}', fixture_id, home, away, 'final', 'test', kickoff),
    )


class DailyRolloverTests(unittest.TestCase):
    def setUp(self):
        self.previous = db.DB
        self.temp = tempfile.TemporaryDirectory()
        db.DB = Path(self.temp.name) / 'sports.sqlite3'
        db.initialize()

    def tearDown(self):
        db.DB = self.previous
        self.temp.cleanup()

    def test_selection_window_and_warning(self):
        self.assertTrue(selection_open(datetime(2026, 11, 4, 6, 0, tzinfo=WAT)))
        self.assertTrue(selection_open(datetime(2026, 11, 4, 9, 0, tzinfo=WAT)))
        self.assertFalse(selection_open(datetime(2026, 11, 4, 9, 1, tzinfo=WAT)))
        self.assertIn('cannot guarantee', WARNING)

    def test_empty_day_is_skipped_and_no_wager_is_recorded(self):
        with db.connect() as connection:
            campaign = create_campaign(connection, {'name': 'Empty', 'opening_balance': 1000}, NOW)
            result = run_day(connection, campaign['id'], NOW, candidates={'eligible': []})
            stored = connection.execute('SELECT status, stake FROM sports_ai_rollover_days').fetchone()
            places = connection.execute('SELECT places_wager FROM sports_ai_rollover_campaigns').fetchone()['places_wager']
        self.assertEqual(result['status'], 'SKIPPED_DAY')
        self.assertEqual(stored['status'], 'SKIPPED_DAY')
        self.assertEqual(stored['stake'], 0)
        self.assertEqual(places, 0)
        self.assertFalse(result['places_wager'])

    def test_one_single_per_day_and_full_rollover_win_reinvests(self):
        with db.connect() as connection:
            campaign = create_campaign(connection, {
                'name': 'Full', 'mode': 'FULL_ROLLOVER_SIMULATION', 'opening_balance': 1000,
            }, NOW)
            first = run_day(connection, campaign['id'], NOW, candidates=candidate())
            second = run_day(connection, campaign['id'], NOW, candidates=candidate(fixture_id='fx-b'))
            add_result(connection, 'fx-a', 2, 0)
            settled = settle_campaigns(connection, NOW)
            updated = connection.execute('SELECT bankroll, consecutive_wins, status FROM sports_ai_rollover_campaigns').fetchone()
        self.assertEqual(first['status'], 'SELECTED')
        self.assertEqual(first['stake'], 1000)
        self.assertTrue(second['idempotent'])
        self.assertEqual(settled, 1)
        self.assertEqual(updated['bankroll'], 1500)
        self.assertEqual(updated['consecutive_wins'], 1)
        self.assertEqual(updated['status'], 'active')

    def test_full_rollover_loss_stops_the_campaign(self):
        with db.connect() as connection:
            campaign = create_campaign(connection, {'mode': 'FULL_ROLLOVER_SIMULATION', 'opening_balance': 1000}, NOW)
            run_day(connection, campaign['id'], NOW, candidates=candidate(market='AWAY'))
            add_result(connection, 'fx-a', 2, 0)
            settle_campaigns(connection, NOW)
            updated = connection.execute('SELECT bankroll, status, losses FROM sports_ai_rollover_campaigns').fetchone()
        self.assertEqual(updated['bankroll'], 0)
        self.assertEqual(updated['losses'], 1)
        self.assertEqual(updated['status'], 'stopped')

    def test_a_loss_does_not_increase_the_next_stake(self):
        campaign = {
            'mode': 'CAPITAL_CONTROLLED_SIMULATION', 'bankroll': 1000, 'stake_mode': 'fractional',
            'stake_fraction': 0.5, 'max_stake_fraction': 1, 'fixed_stake': 100,
        }
        self.assertEqual(next_stake(campaign, {'status': 'LOST', 'stake': 100}), 100)
        self.assertEqual(next_stake(campaign, {'status': 'WON', 'stake': 100}), 500)
        with db.connect() as connection:
            created = create_campaign(connection, {
                'mode': 'CAPITAL_CONTROLLED_SIMULATION', 'opening_balance': 1000, 'fixed_stake': 100,
                'stop_loss_fraction': 0.5,
            }, NOW)
            run_day(connection, created['id'], NOW, candidates=candidate(market='AWAY'))
            add_result(connection, 'fx-a', 2, 0)
            settle_campaigns(connection, NOW)
            later = NOW + timedelta(days=1)
            run_day(connection, created['id'], later, candidates=candidate(fixture_id='fx-b'))
            stakes = [row['stake'] for row in connection.execute('SELECT stake FROM sports_ai_rollover_days ORDER BY campaign_date')]
            bankroll = connection.execute('SELECT bankroll FROM sports_ai_rollover_campaigns').fetchone()['bankroll']
        self.assertEqual(stakes[0], 100)
        self.assertLessEqual(stakes[1], stakes[0])
        self.assertEqual(bankroll, 900)

    def test_stop_loss_ends_a_capital_campaign_without_another_stake(self):
        with db.connect() as connection:
            created = create_campaign(connection, {
                'mode': 'CAPITAL_CONTROLLED_SIMULATION', 'opening_balance': 1000,
                'fixed_stake': 100, 'stop_loss_fraction': 0.05,
            }, NOW)
            run_day(connection, created['id'], NOW, candidates=candidate(market='AWAY'))
            add_result(connection, 'fx-a', 2, 0)
            settle_campaigns(connection, NOW)
            follow = run_day(connection, created['id'], NOW + timedelta(days=1), candidates=candidate(fixture_id='fx-b'))
            count = connection.execute('SELECT COUNT(*) AS n FROM sports_ai_rollover_days').fetchone()['n']
        self.assertEqual(follow['status'], 'stopped')
        self.assertEqual(count, 1)

    def test_closed_window_does_not_create_a_selection(self):
        with db.connect() as connection:
            create_campaign(connection, {'name': 'Waiting'}, NOW)
            result = process_rollover(connection, datetime(2026, 11, 4, 12, 0, tzinfo=WAT))
            count = connection.execute('SELECT COUNT(*) AS n FROM sports_ai_rollover_days').fetchone()['n']
        self.assertFalse(result['selects_now'])
        self.assertEqual(result['selected'], 0)
        self.assertEqual(count, 0)
        self.assertFalse(result['places_wager'])

    def test_odds_bounds_reject_a_target_outside_the_range(self):
        with db.connect() as connection:
            with self.assertRaises(ValueError):
                create_campaign(connection, {'odds_low': 1.40, 'odds_target': 2, 'odds_high': 1.60}, NOW)
