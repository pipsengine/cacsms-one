import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

import db
from sportybet_feed import publish_quotes, quotes_from_payload
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


if __name__ == '__main__':
    unittest.main()
