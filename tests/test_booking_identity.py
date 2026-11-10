"""Phase 1: SportyBet page identity and booking lead time."""
import asyncio
import sys
import tempfile
import unittest
import uuid
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from integrity_support import eligible_everywhere

import db
import high_odds_service as service
from sportybet_booking import page_identity, prepare_slip
from sportybet_feed import quotes_from_payload
from sportybet_research import store_observations
import test_high_odds_service as high_odds_tests


def _leg(**overrides):
    leg = {
        'fixture_id': 'fx-1', 'sport': 'football', 'competition': '2. Fussball-Bundesliga 2026/2027',
        'home': 'Wolverhampton Wanderers', 'away': 'VfL Wolfsburg', 'market': 'HOME',
        'selection': 'Wolverhampton Wanderers', 'decimal_odds': 1.8, 'ticket_id': 't-1',
    }
    leg.update(overrides)
    return leg


def _observe(connection, fixture_id, home, away, competition, event_id='sr:match:1'):
    stored = store_observations(connection, None, [{
        'sport': 'football', 'competition': competition, 'home': home, 'away': away,
        'starts_at': '2026-11-10T15:00:00+00:00', 'market_label': '1X2', 'selection_label': 'Home',
        'decimal_odds': 1.8, 'line': None, 'event_id': event_id,
    }], 'https://www.sportybet.com/ng/sport/football', '2026-11-09T10:00:00+00:00')
    connection.execute('UPDATE sports_ai_sportybet_observations SET fixture_id=? WHERE id=?', (fixture_id, stored['saved'][0]))


class PageIdentityTests(unittest.TestCase):
    def setUp(self):
        eligible_everywhere(self)
        self.temp = tempfile.TemporaryDirectory()
        self.previous = db.DB
        db.DB = Path(self.temp.name) / 'identity.sqlite3'
        db.initialize()

    def tearDown(self):
        db.DB = self.previous
        self.temp.cleanup()

    def test_feed_keeps_sportybet_event_id(self):
        payload = {'data': {'tournaments': [{'categoryName': 'Germany', 'name': '2. Bundesliga', 'events': [{
            'eventId': 'sr:match:42', 'homeTeamName': 'Hertha', 'awayTeamName': 'Wolfsburg',
            'estimateStartTime': 1793000000000, 'markets': [{'status': 0, 'desc': '1X2', 'outcomes': [
                {'isActive': 1, 'odds': '1.80', 'desc': 'Home'}]}]}]}]}}
        rows = quotes_from_payload(payload, 'football', 'https://www.sportybet.com/ng/sport/football')
        self.assertEqual(rows[0]['event_id'], 'sr:match:42')
        with db.connect() as c:
            store_observations(c, None, rows, rows[0]['source_url'], '2026-11-09T10:00:00+00:00')
            self.assertEqual(c.execute('SELECT event_id FROM sports_ai_sportybet_observations').fetchone()[0], 'sr:match:42')

    def test_uses_names_and_league_sportybet_displays(self):
        with db.connect() as c:
            _observe(c, 'fx-1', 'Wolves', 'Wolfsburg', 'Germany 2. Bundesliga')
            item, = page_identity(c, [_leg()])
        self.assertEqual((item['home'], item['away'], item['competition']), ('Wolves', 'Wolfsburg', 'Germany 2. Bundesliga'))
        self.assertEqual(item['selection'], 'Wolves')
        self.assertEqual(item['fixture_home'], 'Wolverhampton Wanderers')
        self.assertEqual(item['sportybet_event_id'], 'sr:match:1')
        self.assertEqual(item['page_identity'], 'observation')

    def test_learned_alias_covers_a_fixture_without_an_observation(self):
        with db.connect() as c:
            _observe(c, 'fx-1', 'Wolves', 'Wolfsburg', 'Germany 2. Bundesliga')
            page_identity(c, [_leg()])
            item, = page_identity(c, [_leg(fixture_id='fx-2', away='Everton', selection='Everton', market='AWAY')])
        self.assertEqual((item['home'], item['away'], item['page_identity']), ('Wolves', 'Everton', 'alias'))
        self.assertEqual(item['selection'], 'Everton')

    def test_unknown_fixture_keeps_its_own_names(self):
        with db.connect() as c:
            item, = page_identity(c, [_leg()])
        self.assertEqual((item['home'], item['page_identity']), ('Wolverhampton Wanderers', 'fixture'))

    def test_identity_is_applied_once(self):
        with db.connect() as c:
            _observe(c, 'fx-1', 'Wolves', 'Wolfsburg', 'Germany 2. Bundesliga')
            once = page_identity(c, [_leg()])
            twice = page_identity(c, once)
        self.assertEqual(twice[0]['fixture_home'], 'Wolverhampton Wanderers')


class SlipMatchingTests(unittest.TestCase):
    """A page row named the SportyBet way is found only with SportyBet's names."""
    HTML = ('<article><span>Wolves</span><span>Wolfsburg</span>'
            '<button>1.80</button><button>3.40</button><button>4.20</button></article>')

    def _run(self, leg):
        from playwright.async_api import async_playwright

        async def go():
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                try:
                    page = await browser.new_page()
                    await page.set_content(self.HTML)
                    return await prepare_slip(page, [leg], book=False)
                finally:
                    await browser.close()
        return asyncio.run(go())

    def test_fixture_store_names_miss_the_row(self):
        with self.assertRaisesRegex(RuntimeError, 'Selection control missing'):
            self._run(_leg())

    def test_sportybet_names_select_the_row(self):
        result = self._run(_leg(home='Wolves', away='Wolfsburg', selection='Wolves'))
        self.assertEqual(result['status'], 'SELECTED')
        self.assertEqual(result['seen'][0]['page_odds'], 1.8)


class LeadTimeTests(unittest.TestCase):
    setUp = high_odds_tests.HighOddsServiceTests.setUp
    tearDown = high_odds_tests.HighOddsServiceTests.tearDown
    seed = high_odds_tests.HighOddsServiceTests.seed
    _analysis = high_odds_tests.HighOddsServiceTests._analysis
    _model = high_odds_tests.HighOddsServiceTests._model
    _opportunity = high_odds_tests.HighOddsServiceTests._opportunity

    def test_matches_kicking_off_inside_the_lead_time_are_not_eligible(self):
        with db.connect() as c:
            items = self.seed(c, 3)
            self.assertEqual(len(service.eligible(c, self.now)[0]), 3)
            soon = (self.now + timedelta(minutes=10)).isoformat()
            c.execute('UPDATE sports_ai_fixtures SET starts_at=? WHERE id=?', (soon, items[0]['fixture_id']))
            pool, rejected, _ = service.eligible(c, self.now)
        self.assertEqual(len(pool), 2)
        self.assertIn('Kickoff is too close to register and use a booking code', rejected)

    def test_lead_time_is_a_validated_setting(self):
        with db.connect() as c:
            with self.assertRaises(ValueError):
                service.save_settings(c, {'min_lead_minutes': 1}, {}, 'test')
            service.save_settings(c, {'min_lead_minutes': 60}, {}, 'test')
            self.assertEqual(service.config(c)['min_lead_minutes'], 60)


if __name__ == '__main__':
    unittest.main()


class TicketListTests(unittest.TestCase):
    """Current tickets must not be pushed out of the 200-row list by newer expired ones."""

    def setUp(self):
        eligible_everywhere(self)
        self.temp = tempfile.TemporaryDirectory()
        self.previous = db.DB
        db.DB = Path(self.temp.name) / 'tickets.sqlite3'
        db.initialize()

    def tearDown(self):
        db.DB = self.previous
        self.temp.cleanup()

    def test_current_ticket_survives_many_newer_expired_tickets(self):
        from accumulator_engine import list_tickets
        with db.connect() as c:
            def add(ticket_id, status, created):
                c.execute(
                    """INSERT INTO sports_ai_accumulator_tickets
                       (id,family_id,version,signature,tier,sport_mode,status,combined_odds,leg_count,
                        probability_basis,explanation,parameters_json,created_at)
                       VALUES(?,?,1,?,2,'football',?,3.0,0,'unverified','test','{}',?)""",
                    (ticket_id, ticket_id, ticket_id, status, created),
                )
            add('current', 'unverified_probability', '2026-11-05T13:00:00+00:00')
            for index in range(210):
                add(f'old-{index}', 'expired', f'2026-11-06T{index // 60:02d}:{index % 60:02d}:00+00:00')
            ids = [ticket['id'] for ticket in list_tickets(c)]
        self.assertIn('current', ids)
        self.assertEqual(len(ids), 200)
