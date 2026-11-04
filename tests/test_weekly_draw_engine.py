import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

from weekly_draw_engine import (
    INSUFFICIENT,
    best_combination,
    build_tickets,
    performance,
    phase_at,
    prepare_watchlist,
    process_weekly_draw,
    save_policy,
    settle_weekly_tickets,
    week_bounds,
)
import db

NOW = datetime(2026, 11, 4, 10, 0, tzinfo=timezone.utc)


def candidate(name, kickoff, home, away, fixture_id=None):
    return {
        'opportunity_id': f'opp-{name}',
        'fixture_id': fixture_id or f'fx-{name}',
        'sport': 'football',
        'competition': 'County Cup',
        'home': home,
        'away': away,
        'starts_at': kickoff,
        'market': 'DRAW',
        'selection': 'Draw',
        'decimal_odds': 3.2,
        'probability': 0.31,
        'edge': 0.05,
        'bookmaker': 'SportyBet',
        'odds_observed_at': NOW.isoformat(),
        'quote_market': 'h2h',
        'quote_outcome': 'draw',
        'quote_line': None,
        'model_id': 'model',
        'model_version': '2',
        'completeness': 0.8,
        'reliability': None,
        'settlement_kind': 'binary',
        'rank_score': 0.05,
        'uncertainty': None,
    }


class WeeklyDrawTicketTests(unittest.TestCase):
    def setUp(self):
        self.previous = db.DB
        self.temp = tempfile.TemporaryDirectory()
        db.DB = Path(self.temp.name) / 'sports.sqlite3'
        db.initialize()

    def tearDown(self):
        db.DB = self.previous
        self.temp.cleanup()

    def test_windows_cover_monday_friday_and_saturday_only(self):
        wat = timezone(timedelta(hours=1))
        self.assertEqual(phase_at(datetime(2026, 11, 2, 7, 0, tzinfo=wat)), 'weekly_discovery')
        self.assertEqual(phase_at(datetime(2026, 11, 3, 7, 0, tzinfo=wat)), 'monitoring')
        self.assertEqual(phase_at(datetime(2026, 11, 6, 17, 0, tzinfo=wat)), 'saturday_watchlist')
        self.assertEqual(phase_at(datetime(2026, 11, 7, 9, 0, tzinfo=wat)), 'saturday_revalidation')
        monday, sunday = week_bounds(datetime(2026, 11, 4, 12, 0, tzinfo=wat))
        self.assertEqual(monday.weekday(), 0)
        self.assertEqual(sunday.weekday(), 6)

    def test_short_list_is_reported_and_no_ticket_is_forced(self):
        with db.connect() as connection:
            summary = build_tickets(connection, 'WEEKLY', NOW, candidates={'eligible': []}, revalidate_first=False)
            count = connection.execute(
                """SELECT COUNT(*) AS n FROM sports_ai_accumulator_tickets
                   WHERE json_extract(parameters_json,'$.product')='weekly_draw'""",
            ).fetchone()['n']
        self.assertTrue(all(item['status'] == INSUFFICIENT for item in summary['categories']))
        self.assertEqual(count, 0)
        self.assertFalse(summary['places_wager'])

    def test_two_qualified_draws_fill_only_the_two_pick_category(self):
        kickoff = (NOW + timedelta(days=1)).isoformat()
        eligible = [
            candidate('a', kickoff, 'Alpha', 'Beta'),
            candidate('b', kickoff, 'Gamma', 'Delta'),
        ]
        with db.connect() as connection:
            summary = build_tickets(connection, 'WEEKLY', NOW, candidates={'eligible': eligible}, revalidate_first=False)
            again = build_tickets(connection, 'WEEKLY', NOW, candidates={'eligible': eligible}, revalidate_first=False)
            ticket = connection.execute(
                """SELECT status, leg_count, parameters_json FROM sports_ai_accumulator_tickets
                   WHERE json_extract(parameters_json,'$.pick_count')=2""",
            ).fetchone()
        by_size = {item['pick_count']: item['status'] for item in summary['categories']}
        self.assertEqual(by_size[2], 'stored')
        self.assertEqual(by_size[5], INSUFFICIENT)
        self.assertEqual(by_size[8], INSUFFICIENT)
        self.assertEqual(by_size[10], INSUFFICIENT)
        self.assertEqual(ticket['status'], 'qualified')
        self.assertEqual(ticket['leg_count'], 2)
        self.assertEqual(again['categories'][0]['status'], 'open')

    def test_duplicate_fixtures_and_shared_teams_do_not_make_a_ticket(self):
        kickoff = (NOW + timedelta(days=1)).isoformat()
        duplicated = [candidate('a', kickoff, 'Alpha', 'Beta'), candidate('a2', kickoff, 'Alpha', 'Beta', fixture_id='fx-a')]
        shared = [candidate('a', kickoff, 'Alpha', 'Beta'), candidate('b', kickoff, 'Alpha', 'Gamma')]
        self.assertIsNone(best_combination(duplicated, 2))
        self.assertIsNone(best_combination(shared, 2))

    def test_eight_pick_ticket_stays_in_simulation(self):
        kickoff = (NOW + timedelta(days=1)).isoformat()
        eligible = [candidate(str(index), kickoff, f'Home {index}', f'Away {index}') for index in range(8)]
        with db.connect() as connection:
            summary = build_tickets(connection, 'SATURDAY_ONLY', datetime(2026, 11, 7, 8, 0, tzinfo=timezone.utc), candidates={'eligible': eligible}, revalidate_first=False)
            ticket = connection.execute(
                """SELECT status, parameters_json FROM sports_ai_accumulator_tickets
                   WHERE json_extract(parameters_json,'$.pick_count')=8""",
            ).fetchone()
            with self.assertRaises(ValueError):
                save_policy(connection, {'sizes': {'8': {'mode': 'standard'}}})
        self.assertEqual(summary['categories'][2]['status'], 'stored')
        self.assertEqual(ticket['status'], 'review_required')
        self.assertIn('simulation', ticket['parameters_json'])

    def test_settlement_keeps_selection_accuracy_separate_from_ticket_wins(self):
        kickoff = (NOW - timedelta(days=1)).isoformat()
        with db.connect() as connection:
            for name, scores in (('a', (1, 1)), ('b', (1, 1)), ('c', (2, 0)), ('d', (0, 0))):
                connection.execute(
                    """INSERT INTO sports_ai_fixtures
                       (id,external_id,source,sport,competition,home,away,starts_at,status,statistics_json,odds_json,observed_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (f'fx-{name}', f'fx-{name}', 'test', 'football', 'County Cup', f'Home {name}', f'Away {name}', kickoff, 'final', '{}', '{}', kickoff),
                )
                connection.execute(
                    """INSERT INTO sports_ai_results(id,fixture_id,home_score,away_score,status,source,settled_at)
                       VALUES(?,?,?,?,?,?,?)""",
                    (f'result-{name}', f'fx-{name}', scores[0], scores[1], 'final', 'test', kickoff),
                )
            build_tickets(connection, 'WEEKLY', NOW - timedelta(days=2), candidates={'eligible': [
                candidate('a', kickoff, 'Home a', 'Away a'),
                candidate('b', kickoff, 'Home b', 'Away b'),
            ]}, revalidate_first=False)
            build_tickets(connection, 'SATURDAY_ONLY', datetime(2026, 11, 7, 8, 0, tzinfo=timezone.utc) - timedelta(days=7), candidates={'eligible': [
                candidate('c', kickoff, 'Home c', 'Away c'),
                candidate('d', kickoff, 'Home d', 'Away d'),
            ]}, revalidate_first=False)
            settled = settle_weekly_tickets(connection, NOW)
            report = {(row['mode'], row['pick_count']): row for row in performance(connection)}
        self.assertEqual(settled, 2)
        weekly = report[('WEEKLY', 2)]
        saturday = report[('SATURDAY_ONLY', 2)]
        self.assertEqual(weekly['draw_accuracy'], 1)
        self.assertEqual(weekly['ticket_win_rate'], 1)
        self.assertEqual(saturday['leg_wins'], 1)
        self.assertEqual(saturday['leg_sample'], 2)
        self.assertEqual(saturday['ticket_win_rate'], 0)

    def test_saturday_watchlist_can_be_empty_and_tuesday_does_not_build(self):
        with db.connect() as connection:
            empty = prepare_watchlist(connection, NOW, candidates={'eligible': []})
            watched = prepare_watchlist(connection, NOW, candidates={'eligible': [candidate('a', '2026-11-07T15:00:00+00:00', 'Alpha', 'Beta')]})
            tuesday = process_weekly_draw(connection, datetime(2026, 11, 3, 10, 0, tzinfo=timezone.utc))
            count = connection.execute(
                "SELECT COUNT(*) AS n FROM sports_ai_accumulator_tickets WHERE json_extract(parameters_json,'$.product')='weekly_draw'",
            ).fetchone()['n']
        self.assertEqual(empty['status'], INSUFFICIENT)
        self.assertEqual(watched['count'], 1)
        self.assertEqual(tuesday['phase'], 'monitoring')
        self.assertIsNone(tuesday['built'])
        self.assertEqual(count, 0)
        self.assertFalse(tuesday['places_wager'])
