import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

import db
from sportybet_feed import page_count, publish_quotes, quotes_from_payload, sections_from_links, tournaments_from_menu
from sportybet_research import blocked_page


class SportyBetFeedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.previous = db.DB
        db.DB = Path(cls.temp.name) / 'sports.sqlite3'
        db.initialize()

    @classmethod
    def tearDownClass(cls):
        db.DB = cls.previous
        cls.temp.cleanup()

    def test_payload_reads_displayed_prices_and_ignores_closed_markets(self):
        kickoff = int(datetime(2026, 11, 5, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
        payload = {'data': {'tournaments': [{'categoryName': 'USA', 'name': 'NBA', 'events': [{
            'homeTeamName': 'Detroit Pistons', 'awayTeamName': 'Philadelphia 76ers',
            'estimateStartTime': kickoff,
            'markets': [
                {'status': 0, 'desc': 'Winner (incl. overtime)', 'outcomes': [
                    {'isActive': 1, 'desc': 'Home', 'odds': '1.80'},
                    {'isActive': 1, 'desc': 'Away', 'odds': '2.07'},
                ]},
                {'status': 0, 'desc': 'Over/Under', 'specifier': 'total=215.5', 'outcomes': [
                    {'isActive': 1, 'desc': 'Over', 'odds': '1.76'},
                    {'isActive': 0, 'desc': 'Under', 'odds': '2.10'},
                ]},
                {'status': 2, 'desc': 'Handicap (incl. overtime)', 'specifier': 'hcp=-6.5', 'outcomes': [
                    {'isActive': 1, 'desc': 'Home', 'odds': '1.90'},
                ]},
            ],
        }]}]}}
        rows = quotes_from_payload(payload, 'basketball', 'https://www.sportybet.com/ng/sport/basketball')
        prices = sorted(row['decimal_odds'] for row in rows)
        self.assertEqual(prices, [1.76, 1.8, 2.07])
        self.assertNotIn(215.5, prices)
        self.assertTrue(blocked_page('verify you are human'))

    def test_matching_full_names_stores_the_displayed_price(self):
        kickoff = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=2)
        payload = {'data': {'tournaments': [{'categoryName': 'USA', 'name': 'NBA', 'events': [{
            'homeTeamName': 'Detroit Pistons', 'awayTeamName': 'Philadelphia 76ers',
            'estimateStartTime': int(kickoff.timestamp() * 1000),
            'markets': [{'status': 0, 'desc': 'Winner (incl. overtime)', 'outcomes': [
                {'isActive': 1, 'desc': 'Home', 'odds': '1.80'},
            ]}],
        }]}]}}
        rows = quotes_from_payload(payload, 'basketball', 'https://www.sportybet.com/ng/sport/basketball')
        with db.connect() as connection:
            connection.execute('DELETE FROM sports_ai_odds_snapshots')
            connection.execute('DELETE FROM sports_ai_sportybet_observations')
            connection.execute('DELETE FROM sports_ai_fixtures')
            fixture_id = str(uuid.uuid4())
            connection.execute(
                """INSERT INTO sports_ai_fixtures
                   (id,external_id,source,sport,competition,home,away,starts_at,status,statistics_json,odds_json,observed_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (fixture_id, fixture_id, 'test', 'basketball', 'NBA 2027', 'Pistons', '76ers',
                 kickoff.isoformat(), 'scheduled', '{}', '{}', kickoff.isoformat()),
            )
            result = publish_quotes(connection, rows, rows[0]['source_url'])
            stored = connection.execute(
                'SELECT decimal_odds, bookmaker FROM sports_ai_odds_snapshots WHERE fixture_id=?',
                (fixture_id,),
            ).fetchone()
        self.assertEqual(result['imported'], 1)
        self.assertEqual(stored['decimal_odds'], 1.8)
        self.assertEqual(stored['bookmaker'], 'SportyBet')

    def test_unmatched_league_becomes_a_fixture(self):
        kickoff = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=3)
        payload = {'data': {'tournaments': [{'categoryName': 'Spain', 'name': 'LaLiga', 'events': [{
            'homeTeamName': 'Real Madrid', 'awayTeamName': 'Barcelona',
            'estimateStartTime': int(kickoff.timestamp() * 1000),
            'markets': [{'status': 0, 'desc': '1X2', 'outcomes': [
                {'isActive': 1, 'desc': 'Home', 'odds': '1.70'},
                {'isActive': 1, 'desc': 'Draw', 'odds': '3.80'},
                {'isActive': 1, 'desc': 'Away', 'odds': '4.50'},
            ]}],
        }]}]}}
        rows = quotes_from_payload(payload, 'football', 'https://www.sportybet.com/ng/sport/football')
        with db.connect() as connection:
            result = publish_quotes(connection, rows, rows[0]['source_url'])
            fixture = connection.execute(
                "SELECT id, competition, home, away FROM sports_ai_fixtures WHERE source='sportybet-displayed' AND competition='Spain LaLiga'",
            ).fetchone()
            prices = connection.execute(
                'SELECT COUNT(*) FROM sports_ai_odds_snapshots WHERE fixture_id=?',
                (fixture['id'],),
            ).fetchone()[0]
        self.assertEqual(result['imported'], 3)
        self.assertEqual(fixture['home'], 'Real Madrid')
        self.assertEqual(fixture['away'], 'Barcelona')
        self.assertEqual(prices, 3)

    def test_ice_hockey_result_and_total_are_imported(self):
        kickoff = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=2)
        payload = {'data': {'tournaments': [{'categoryName': 'Germany', 'name': 'DEL', 'events': [{
            'homeTeamName': 'Eisbaren Berlin', 'awayTeamName': 'Adler Mannheim',
            'estimateStartTime': int(kickoff.timestamp() * 1000),
            'markets': [
                {'status': 0, 'desc': '1X2', 'outcomes': [
                    {'isActive': 1, 'desc': 'Home', 'odds': '2.40'},
                    {'isActive': 1, 'desc': 'Draw', 'odds': '4.10'},
                    {'isActive': 1, 'desc': 'Away', 'odds': '2.55'},
                ]},
                {'status': 0, 'desc': 'Over/Under', 'specifier': 'total=5.5', 'outcomes': [
                    {'isActive': 1, 'desc': 'Over 5.5', 'odds': '1.87'},
                    {'isActive': 1, 'desc': 'Under 5.5', 'odds': '1.95'},
                ]},
            ],
        }]}]}}
        rows = quotes_from_payload(payload, 'ice_hockey', 'https://www.sportybet.com/ng/sport/iceHockey')
        self.assertEqual(len(rows), 5)
        with db.connect() as connection:
            result = publish_quotes(connection, rows, rows[0]['source_url'])
            fixture = connection.execute(
                "SELECT id FROM sports_ai_fixtures WHERE source='sportybet-displayed' AND home='Eisbaren Berlin'",
            ).fetchone()
            prices = connection.execute(
                'SELECT market, outcome, decimal_odds FROM sports_ai_odds_snapshots WHERE fixture_id=? ORDER BY market, outcome',
                (fixture['id'],),
            ).fetchall()
            connection.execute('DELETE FROM sports_ai_odds_history')
            connection.execute('DELETE FROM sports_ai_odds_snapshots')
            connection.execute('DELETE FROM sports_ai_sportybet_observations')
            connection.execute('DELETE FROM sports_ai_fixtures')
        self.assertEqual(result['imported'], 5)
        self.assertEqual(
            [(row['market'], row['outcome'], row['decimal_odds']) for row in prices],
            [('h2h', 'away', 2.55), ('h2h', 'draw', 4.1), ('h2h', 'home', 2.4), ('totals', 'OVER', 1.87), ('totals', 'UNDER', 1.95)],
        )


    def test_the_menu_adds_tennis_and_an_empty_menu_uses_the_four_sports(self):
        discovered = sections_from_links([
            'https://www.sportybet.com/ng/sport/football',
            'https://www.sportybet.com/ng/sport/tennis',
            'https://www.sportybet.com/ng/sport/iceHockey',
            'https://www.sportybet.com/ng/sport/football',
        ])
        self.assertEqual(
            [code for code, _url in discovered],
            ['football', 'basketball', 'tennis', 'ice_hockey'],
        )
        fallback = sections_from_links(['https://www.sportybet.com/ng/sport/tableTennis'])
        self.assertEqual(
            [code for code, _url in fallback],
            ['football', 'basketball', 'tennis', 'ice_hockey', 'table_tennis'],
        )

    def test_a_displayed_market_without_a_model_is_stored_and_not_qualified(self):
        kickoff = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=2)
        payload = {'data': {'tournaments': [{'categoryName': 'England', 'name': 'Premier League', 'events': [{
            'homeTeamName': 'Arsenal', 'awayTeamName': 'Chelsea',
            'estimateStartTime': int(kickoff.timestamp() * 1000),
            'markets': [{'status': 0, 'desc': 'Corners Over/Under', 'specifier': 'total=9.5', 'outcomes': [
                {'isActive': 1, 'desc': 'Over 9.5', 'odds': '1.91'},
            ]}],
        }]}]}}
        rows = quotes_from_payload(payload, 'football', 'https://www.sportybet.com/ng/sport/football')
        self.assertEqual(len(rows), 1)
        with db.connect() as connection:
            result = publish_quotes(connection, rows, rows[0]['source_url'])
            fixture = connection.execute(
                "SELECT id FROM sports_ai_fixtures WHERE source='sportybet-displayed' AND home='Arsenal'",
            ).fetchone()
            stored = connection.execute(
                'SELECT market, decimal_odds FROM sports_ai_odds_snapshots WHERE fixture_id=?',
                (fixture['id'],),
            ).fetchone()
            qualified = connection.execute('SELECT COUNT(*) FROM sports_ai_value_opportunities').fetchone()[0]
            connection.execute('DELETE FROM sports_ai_odds_history')
            connection.execute('DELETE FROM sports_ai_odds_snapshots')
            connection.execute('DELETE FROM sports_ai_sportybet_observations')
            connection.execute('DELETE FROM sports_ai_fixtures')
        self.assertEqual(result['imported'], 1)
        self.assertEqual(stored['market'], 'Corners Over/Under')
        self.assertEqual(stored['decimal_odds'], 1.91)
        self.assertEqual(qualified, 0)

    def test_football_menu_keeps_every_division_and_cup(self):
        payload = {'data': {'categories': [
            {'name': 'England', 'tournaments': [
                {'id': 'sr:tournament:17', 'name': 'Premier League', 'eventSize': 10},
                {'id': 'sr:tournament:18', 'name': 'Championship', 'eventSize': 12},
                {'id': 'sr:tournament:19', 'name': 'League One', 'eventSize': 12},
                {'id': 'sr:tournament:20', 'name': 'League Two', 'eventSize': 12},
                {'id': 'sr:tournament:21', 'name': 'FA Cup', 'eventSize': 0},
            ]},
            {'name': 'Ghana', 'tournaments': [
                {'id': 'sr:tournament:99', 'name': 'Premier League', 'eventSize': 4},
            ]},
        ]}}
        found = tournaments_from_menu(payload)
        labels = {item['label'] for item in found}
        self.assertEqual(len(found), 6)
        self.assertEqual(labels, {
            'England Premier League', 'England Championship', 'England League One',
            'England League Two', 'England FA Cup', 'Ghana Premier League',
        })
        self.assertEqual(page_count(250, 100), 3)
        self.assertEqual(page_count(100, 100), 1)
        self.assertEqual(page_count(50000, 100), 500)


if __name__ == '__main__':
    unittest.main()
