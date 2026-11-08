import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

import db
from accumulator_engine import pack_diverse_tickets, pack_league_tickets
from prediction_engine import quoted_coverage_fixtures, rotate_by_league


class QuotedCoverageTests(unittest.TestCase):
    def setUp(self):
        self.previous = db.DB
        self.temp = tempfile.TemporaryDirectory()
        db.DB = Path(self.temp.name) / 'sports.sqlite3'
        db.initialize()

    def tearDown(self):
        db.DB = self.previous
        self.temp.cleanup()

    def test_a_league_with_a_final_is_selected_without_a_fresh_price(self):
        now = datetime.now(timezone.utc)
        kickoff = (now + timedelta(hours=4)).isoformat()
        played = (now - timedelta(days=3)).isoformat()

        def fixture(fixture_id, sport, competition, status, starts_at):
            connection.execute(
                """INSERT INTO sports_ai_fixtures
                   (id,external_id,source,sport,competition,home,away,starts_at,status,
                    statistics_json,odds_json,observed_at)
                   VALUES(?,?,?,?,?,?,?,?,?,'{}','{}',?)""",
                (fixture_id, fixture_id, 'history', sport, competition, 'Home FC', 'Away FC',
                 starts_at, status, now.isoformat()),
            )

        with db.connect() as connection:
            fixture('past', 'football', 'Saturday League', 'finished', played)
            connection.execute(
                """INSERT INTO sports_ai_results
                   (id,fixture_id,home_score,away_score,source,status,settled_at)
                   VALUES('result-1','past',1,0,'verified','final',?)""",
                (now.isoformat(),),
            )
            fixture('today', 'football', 'Saturday League', 'scheduled', kickoff)
            fixture('empty', 'football', 'Empty Cup', 'scheduled', kickoff)
            fixture('tennis', 'tennis', 'Saturday League', 'scheduled', kickoff)
            chosen = quoted_coverage_fixtures(connection, now, limit=8)
        self.assertEqual({row['id'] for row in chosen}, {'today', 'empty', 'tennis'})


class CoverageSelectionTests(unittest.TestCase):
    def test_each_league_is_visited_before_a_league_is_repeated(self):
        rows = [
            {'sport': 'football', 'competition': 'Alpha', 'id': 1},
            {'sport': 'football', 'competition': 'Alpha', 'id': 2},
            {'sport': 'basketball', 'competition': 'Beta', 'id': 3},
            {'sport': 'ice_hockey', 'competition': 'Gamma', 'id': 4},
        ]
        chosen = rotate_by_league(rows, 4)
        self.assertEqual([row['id'] for row in chosen], [3, 1, 4, 2])

    def test_high_probability_pairs_come_from_different_leagues_first(self):
        legs = [
            {'competition': 'League A', 'probability': 0.91, 'fixture_id': 'a1'},
            {'competition': 'League A', 'probability': 0.80, 'fixture_id': 'a2'},
            {'competition': 'League B', 'probability': 0.88, 'fixture_id': 'b1'},
            {'competition': 'League C', 'probability': 0.84, 'fixture_id': 'c1'},
        ]
        tickets = pack_league_tickets(legs, 2)
        self.assertEqual([leg['fixture_id'] for leg in tickets[0]], ['a1', 'b1'])
        self.assertEqual([leg['fixture_id'] for leg in tickets[1]], ['c1', 'a2'])

    def test_a_ticket_spreads_across_sports_leagues_and_markets(self):
        legs = [
            {'fixture_id': 'f1', 'sport': 'football', 'competition': 'Premier League', 'market': 'UNDER_3.5', 'probability': 0.91},
            {'fixture_id': 'f2', 'sport': 'football', 'competition': 'Premier League', 'market': 'UNDER_4.5', 'probability': 0.90},
            {'fixture_id': 'b1', 'sport': 'basketball', 'competition': 'NBA', 'market': 'HOME_SPREAD_-4.5', 'probability': 0.82},
            {'fixture_id': 'h1', 'sport': 'ice_hockey', 'competition': 'DEL', 'market': 'HOME', 'probability': 0.80},
            {'fixture_id': 'f3', 'sport': 'football', 'competition': 'Bundesliga', 'market': 'OVER_2.5', 'probability': 0.78},
        ]
        tickets = pack_diverse_tickets(legs, size=4, minimum=3, limit=2)
        self.assertEqual(len(tickets), 1)
        ticket = tickets[0]
        self.assertGreaterEqual(len({leg['sport'] for leg in ticket}), 2)
        self.assertGreaterEqual(len({leg['competition'] for leg in ticket}), 2)
        self.assertGreaterEqual(len({leg['market'].split('_')[0] for leg in ticket}), 2)
        self.assertEqual(len({leg['fixture_id'] for leg in ticket}), len(ticket))

    def test_coverage_tickets_hold_more_than_two_selections(self):
        legs = [
            {'competition': f'League {index}', 'probability': 0.90 - index / 100, 'fixture_id': f'f{index}'}
            for index in range(10)
        ]
        tickets = pack_league_tickets(legs, 8, minimum=3)
        self.assertEqual(len(tickets), 1)
        self.assertEqual(len(tickets[0]), 8)
        self.assertGreater(len(tickets[0]), 2)
        short = pack_league_tickets(legs[:2], 8, minimum=3)
        self.assertEqual(short, [])


if __name__ == '__main__':
    unittest.main()
