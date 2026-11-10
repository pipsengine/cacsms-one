import sys
import tempfile
import unittest
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

import db
from history_browser import (
    LAGOS,
    _advance,
    _blank_state,
    _store,
    archived_settlement,
    collect_match_history,
    parse_betexplorer_results,
    parse_soccerway_results,
    plan_visits,
    repair_imported_settlements,
    ticket_result_visits,
)

NOW = datetime(2026, 11, 7, 8, 0, tzinfo=timezone.utc)
OBSERVED = NOW.isoformat()

BETEXPLORER_HTML = """
<table>
<tr class="js-tournament"><th><a class="table-main__tournament" href="/football/africa/caf-champions-league-women/">Africa: CAF Champions League Women</a></th></tr>
<tr data-dt="5,11,2026,13,00" data-def="0">
<td class="table-main__tt"><a href="/football/africa/caf-champions-league-women/far-rabat-usfas-bamako/KObfSiXq/"><strong>FAR Rabat W</strong> - USFAS Bamako W</a></td>
<td class="table-main__result"><a href="/football/africa/caf-champions-league-women/far-rabat-usfas-bamako/KObfSiXq/"><strong>6:0</strong></a></td>
</tr>
<tr data-dt="5,11,2026,17,00">
<td class="table-main__tt"><a href="/football/africa/caf-champions-league-women/open-side/ABC12345/"><strong>Open Side</strong> - Other Side</a></td>
<td class="table-main__result"><strong>POSTP.</strong></td>
</tr>
</table>
<table>
<tr class="js-tournament"><th><a class="table-main__tournament" href="/basketball/argentina/liga-a/">Argentina: Liga A</a></th></tr>
<tr data-dt="5,11,2026,2,05">
<td class="table-main__tt"><a href="/basketball/argentina/liga-a/olimpico-lanus/OhZRuWR6/" class="table-main__teamsLink"><span class="table-main__teamLine table-main__teamLine--home"><strong>Olimpico</strong></span><span class="table-main__teamLine table-main__teamLine--away">Lanus</span></a></td>
<td class="table-main__result"><a href="/basketball/argentina/liga-a/olimpico-lanus/OhZRuWR6/"><strong>95:94</strong></a></td>
</tr>
</table>
"""

SOCCERWAY_HTML = """
<span class="headerLeague__category-text">ENGLAND</span>
<a class="headerLeague__title" href="/england/premier-league/"><span class="headerLeague__title-text">Premier League</span></a>
<div id="g_1_OUYQUF9b" class="event__match event__match--withRowLink">
  <a href="https://ng.soccerway.com/match/coventry-GOvB22xg/everton-KluSTr9s/?mid=OUYQUF9b"></a>
  <span data-testid="wcl-stageTime"><span>06.11. 21:00</span></span>
  <div class="event__homeParticipant"><img alt="Everton" src="https://ng.soccerway.com/badge.png"><span data-testid="wcl-simple-text-01">Everton</span></div>
  <div class="event__awayParticipant"><span data-testid="wcl-simple-text-01">Coventry</span></div>
  <span data-testid="wcl-tableScore">3</span>
  <span data-testid="wcl-tableScore">1</span>
</div>
<div class="event__match">
  <span data-testid="wcl-stageTime">08.11. 15:00</span>
  <div class="event__homeParticipant"><span>Brighton</span></div>
  <div class="event__awayParticipant"><span>Brentford</span></div>
</div>
"""


class HistoryParseTests(unittest.TestCase):
    def test_lagos_history_timezone_works_without_a_system_timezone_database(self):
        self.assertEqual(LAGOS.utcoffset(None).total_seconds(), 3600)
        local = datetime(2026, 11, 7, 8, 0, tzinfo=timezone.utc).astimezone(LAGOS)
        self.assertEqual(local.isoformat(), '2026-11-07T09:00:00+01:00')

    def test_displayed_finals_are_kept_and_a_postponement_is_skipped(self):
        women = parse_betexplorer_results(BETEXPLORER_HTML, 'football', 'https://www.betexplorer.com/football/results/', OBSERVED, NOW)
        basketball = parse_betexplorer_results(BETEXPLORER_HTML, 'basketball', 'https://www.betexplorer.com/basketball/results/', OBSERVED, NOW)
        self.assertEqual(len(women), 1)
        self.assertEqual(women[0]['sport'], 'frauenfussball')
        self.assertEqual(women[0]['home'], 'FAR Rabat W')
        self.assertEqual(women[0]['away'], 'USFAS Bamako W')
        self.assertEqual((women[0]['home_score'], women[0]['away_score']), (6, 0))
        self.assertEqual(women[0]['starts_at'], '2026-11-05T12:00:00+00:00')
        self.assertEqual(len(basketball), 1)
        self.assertEqual(basketball[0]['sport'], 'basketball')
        self.assertEqual((basketball[0]['home_score'], basketball[0]['away_score']), (95, 94))

    def test_soccerway_reads_the_finished_score_and_leaves_a_fixture_without_one(self):
        rows, leagues = parse_soccerway_results(SOCCERWAY_HTML, OBSERVED, NOW)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['competition'], 'England: Premier League')
        self.assertEqual(rows[0]['home'], 'Everton')
        self.assertEqual((rows[0]['home_score'], rows[0]['away_score']), (3, 1))
        self.assertEqual(rows[0]['starts_at'], '2026-11-06T20:00:00+00:00')
        self.assertIn('/england/premier-league/', leagues)

    def test_a_volleyball_golden_set_is_not_a_second_match_score(self):
        html = """
<table>
<tr class="js-tournament"><th><a class="table-main__tournament" href="/volleyball/europe/challenge-cup-women/">Europe: Challenge Cup Women</a></th></tr>
<tr data-dt="5,11,2026,20,00">
<td class="table-main__tt"><a href="/volleyball/europe/challenge-cup-women/roeselare-charleroi/SrergToD/"><strong>Roeselare W</strong> - Charleroi W</a></td>
<td class="table-main__result"><a href="/volleyball/europe/challenge-cup-women/roeselare-charleroi/SrergToD/"><strong>3:2</strong></a></td>
</tr>
<tr data-dt="5,11,2026,22,00">
<td class="table-main__tt"><a href="/volleyball/europe/challenge-cup-women/roeselare-charleroi/QRGxwBdn/"><strong>Roeselare W</strong> - Charleroi W</a></td>
<td class="table-main__result"><a href="/volleyball/europe/challenge-cup-women/roeselare-charleroi/QRGxwBdn/"><strong>15:11</strong></a></td>
</tr>
</table>
"""
        rows = parse_betexplorer_results(html, 'volleyball', 'https://www.betexplorer.com/volleyball/results/', OBSERVED, NOW)
        self.assertEqual([(row['external_id'], row['home_score'], row['away_score']) for row in rows], [('SrergToD', 3, 2)])

    def test_a_page_without_a_finished_score_stores_nothing(self):
        rows = parse_betexplorer_results(
            '<html><title>Verify you are human</title><body>captcha</body></html>',
            'football', 'https://www.betexplorer.com/football/results/', OBSERVED, NOW,
        )
        self.assertEqual(rows, [])


class HistoryPlanTests(unittest.TestCase):
    def test_missing_ticket_results_are_prioritized_and_throttled(self):
        connection = sqlite3.connect(':memory:')
        connection.row_factory = sqlite3.Row
        self.addCleanup(connection.close)
        connection.executescript('''
            CREATE TABLE sports_ai_fixtures(id TEXT,sport TEXT,starts_at TEXT);
            CREATE TABLE sports_ai_accumulator_legs(ticket_id TEXT,fixture_id TEXT);
            CREATE TABLE sports_ai_ticket_settlements(ticket_id TEXT);
            CREATE TABLE sports_ai_results(fixture_id TEXT,status TEXT);
            INSERT INTO sports_ai_fixtures VALUES('missing','football','2026-11-05T17:45:00+00:00'),
                ('future','basketball','2026-11-09T12:00:00+00:00'),
                ('settled','basketball','2026-11-04T12:00:00+00:00');
            INSERT INTO sports_ai_accumulator_legs VALUES('t','missing'),('f','future'),('s','settled');
            INSERT INTO sports_ai_ticket_settlements VALUES('s');
        ''')
        state = _blank_state()
        visits = ticket_result_visits(connection, state, NOW)
        self.assertEqual(len(visits), 1)
        self.assertIn('year=2026&month=11&day=05', visits[0]['url'])
        previous = (state['sport_index'], state['day_offset'])
        _advance(state, visits[0], NOW)
        self.assertEqual(ticket_result_visits(connection, state, NOW), [])
        self.assertEqual((state['sport_index'], state['day_offset']), previous)

    def test_waiting_leagues_reserve_general_history_capacity(self):
        waiting = plan_visits(_blank_state(), {'premier-league'}, NOW)
        sports = [visit['sport'] for visit in waiting if visit['kind'] == 'betexplorer']
        self.assertEqual(sports[:3], ['football', 'basketball', 'football'])
        # The modelled-sport walk never covers tennis; tennis comes only from the bootstrap walk.
        self.assertNotIn('tennis', [v['sport'] for v in waiting if v.get('cursor') == 'priority'])
        self.assertEqual({v['sport'] for v in waiting if v.get('cursor') == 'bootstrap'}, {'tennis', 'volleyball'})
        self.assertEqual(len(sports), 12)
        self.assertEqual(sum(visit['kind'] == 'soccerway' for visit in waiting), 3)
        state = _blank_state()
        for visit in waiting:
            if visit['kind'] == 'betexplorer':
                state = _advance(state, visit, NOW)
        self.assertEqual(state['priority_sport'], 0)
        self.assertEqual(state['priority_day'], 3)

        # With no league waiting, history still deepens until each walk reaches its depth.
        idle = plan_visits(_blank_state(), set(), NOW)
        self.assertEqual([v['sport'] for v in idle if v.get('cursor') == 'general'], ['football', 'basketball'])
        self.assertEqual(len([v for v in idle if v.get('cursor') == 'priority']), 6)
        self.assertEqual(sum(visit['kind'] == 'soccerway' for visit in idle), 1)


class HistoryCollectTests(unittest.TestCase):
    def setUp(self):
        self.previous = db.DB
        self.temp = tempfile.TemporaryDirectory()
        db.DB = Path(self.temp.name) / 'sports.sqlite3'
        db.initialize()

    def tearDown(self):
        db.DB = self.previous
        self.temp.cleanup()

    def test_a_displayed_page_is_stored_and_a_challenge_pauses_the_pass(self):
        def reader(visits):
            pages = []
            for visit in visits:
                if visit['kind'] == 'soccerway':
                    pages.append({'html': SOCCERWAY_HTML, 'title': 'Premier League results', 'error': ''})
                elif visit['sport'] == 'football':
                    pages.append({'html': BETEXPLORER_HTML, 'title': 'Latest Football Results', 'error': ''})
                else:
                    pages.append({'html': '<table></table>', 'title': 'Results', 'error': ''})
            return pages

        result = collect_match_history(NOW, reader)
        self.assertEqual(result['places_wager'], False)
        self.assertGreaterEqual(result['saved'], 2)
        with db.connect() as connection:
            stored = connection.execute(
                """SELECT f.sport, f.home, f.away, r.home_score, r.away_score, r.source
                   FROM sports_ai_results r JOIN sports_ai_fixtures f ON f.id=r.fixture_id
                   ORDER BY f.home""",
            ).fetchall()
        homes = {row['home'] for row in stored}
        self.assertIn('Everton', homes)
        self.assertIn('FAR Rabat W', homes)
        self.assertTrue(all(row['source'] in ('betexplorer', 'soccerway') for row in stored))

        def blocked(_visits):
            return [{'html': '', 'title': 'Verify you are human', 'error': 'page asked for a challenge or a subscription', 'blocked': True}]

        paused = collect_match_history(NOW.replace(hour=9), blocked)
        self.assertEqual(paused['status'], 'blocked')
        self.assertEqual(paused['saved'], 0)

    def test_a_golden_set_does_not_open_a_result_conflict(self):
        match = {
            'external_id': 'SrergToD', 'sport': 'volleyball', 'competition': 'Europe: Challenge Cup Women',
            'home': 'Roeselare W', 'away': 'Charleroi W', 'starts_at': '2026-11-05T19:00:00+00:00',
            'home_score': 3, 'away_score': 2, 'observed_at': OBSERVED,
            'source_url': 'https://www.betexplorer.com/volleyball/europe/challenge-cup-women/roeselare-charleroi/SrergToD/',
        }
        golden = {
            'external_id': 'QRGxwBdn', 'sport': 'volleyball', 'competition': 'Europe: Challenge Cup Women',
            'home': 'Roeselare W', 'away': 'Charleroi W', 'starts_at': '2026-11-05T21:00:00+00:00',
            'home_score': 15, 'away_score': 11, 'observed_at': OBSERVED,
            'source_url': 'https://www.betexplorer.com/volleyball/europe/challenge-cup-women/roeselare-charleroi/QRGxwBdn/',
        }
        with db.connect() as connection:
            provider = dict(connection.execute(
                "SELECT * FROM sports_ai_provider_registry WHERE provider_key='betexplorer'",
            ).fetchone())
            _store(connection, provider, [golden], OBSERVED)
            _store(connection, provider, [match, golden], OBSERVED)
            _store(connection, provider, [golden, match], OBSERVED)
            stored = connection.execute(
                """SELECT r.home_score, r.away_score FROM sports_ai_results r
                   JOIN sports_ai_fixtures f ON f.id=r.fixture_id
                   WHERE f.home='Roeselare W'""",
            ).fetchall()
            issues = connection.execute(
                "SELECT COUNT(*) FROM sports_ai_data_quality_issues WHERE issue_type='result_conflict' AND resolved_at IS NULL",
            ).fetchone()[0]
        self.assertEqual([(row['home_score'], row['away_score']) for row in stored], [(3, 2)])
        self.assertEqual(issues, 0)

    def test_reversed_home_at_the_same_kickoff_is_a_different_match(self):
        kickoff = '2026-09-26T23:00:00+00:00'
        montreal = {
            'external_id': 'tMFKm3y2', 'sport': 'ice_hockey', 'competition': 'USA: NHL',
            'home': 'Montreal Canadiens', 'away': 'Ottawa Senators', 'starts_at': kickoff,
            'home_score': 4, 'away_score': 2, 'observed_at': OBSERVED,
            'source_url': 'https://www.betexplorer.com/hockey/usa/nhl/montreal-canadiens-ottawa-senators/tMFKm3y2/',
        }
        ottawa = {
            'external_id': 'KWjPqOEk', 'sport': 'ice_hockey', 'competition': 'USA: NHL',
            'home': 'Ottawa Senators', 'away': 'Montreal Canadiens', 'starts_at': kickoff,
            'home_score': 4, 'away_score': 3, 'observed_at': OBSERVED,
            'source_url': 'https://www.betexplorer.com/hockey/usa/nhl/ottawa-senators-montreal-canadiens/KWjPqOEk/',
        }
        with db.connect() as connection:
            provider = dict(connection.execute(
                "SELECT * FROM sports_ai_provider_registry WHERE provider_key='betexplorer'",
            ).fetchone())
            _store(connection, provider, [montreal, ottawa], OBSERVED)
            rows = connection.execute(
                """SELECT f.home, r.home_score, r.away_score
                   FROM sports_ai_results r JOIN sports_ai_fixtures f ON f.id=r.fixture_id
                   WHERE f.sport='ice_hockey' ORDER BY f.home""",
            ).fetchall()
            issues = connection.execute(
                "SELECT COUNT(*) FROM sports_ai_data_quality_issues WHERE issue_type='result_conflict' AND resolved_at IS NULL",
            ).fetchone()[0]
        self.assertEqual(
            [(row['home'], row['home_score'], row['away_score']) for row in rows],
            [('Montreal Canadiens', 4, 2), ('Ottawa Senators', 4, 3)],
        )
        self.assertEqual(issues, 0)

    def test_doubleheaders_keep_distinct_provider_event_ids(self):
        first = {
            'external_id': 'game-one', 'sport': 'baseball', 'competition': 'Mexico: LMP',
            'home': 'Mayos de Navojoa', 'away': 'Mazatlan',
            'starts_at': '2026-11-05T22:00:00+00:00',
            'home_score': 0, 'away_score': 1, 'observed_at': OBSERVED,
            'source_url': 'https://www.betexplorer.com/baseball/game-one/',
        }
        second = {**first, 'external_id': 'game-two',
                  'starts_at': '2026-11-06T00:30:00+00:00',
                  'home_score': 5, 'away_score': 4,
                  'source_url': 'https://www.betexplorer.com/baseball/game-two/'}
        with db.connect() as connection:
            provider = dict(connection.execute(
                "SELECT * FROM sports_ai_provider_registry WHERE provider_key='betexplorer'",
            ).fetchone())
            _store(connection, provider, [first, second], OBSERVED)
            _store(connection, provider, [second, first], OBSERVED)
            rows = connection.execute('''SELECT f.external_id,r.home_score,r.away_score
                FROM sports_ai_fixtures f JOIN sports_ai_results r ON r.fixture_id=f.id
                WHERE f.sport='baseball' ORDER BY f.external_id''').fetchall()
            self.assertEqual([tuple(r) for r in rows], [('game-one', 0, 1), ('game-two', 5, 4)])
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sports_ai_data_quality_issues WHERE issue_type='result_conflict'").fetchone()[0], 0)
            # A changed score for the SAME event must still raise an alert.
            _store(connection, provider, [{**first, 'home_score': 9}], OBSERVED)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sports_ai_data_quality_issues WHERE issue_type='result_conflict'").fetchone()[0], 1)

    def test_an_archived_result_settles_when_the_match_closed(self):
        kickoff = '2026-10-01T18:00:00+00:00'
        self.assertEqual(archived_settlement(kickoff, OBSERVED), '2026-10-02T02:00:00+00:00')
        self.assertEqual(
            archived_settlement('2026-11-07T06:00:00+00:00', OBSERVED),
            OBSERVED,
        )
        page = {
            'external_id': 'archive-1', 'sport': 'basketball', 'competition': 'NBA',
            'home': 'Home', 'away': 'Away', 'starts_at': kickoff,
            'home_score': 110, 'away_score': 99, 'observed_at': OBSERVED,
            'source_url': 'https://www.betexplorer.com/basketball/usa/nba/home-away/archive01/',
        }
        with db.connect() as connection:
            provider = dict(connection.execute(
                "SELECT * FROM sports_ai_provider_registry WHERE provider_key='betexplorer'",
            ).fetchone())
            _store(connection, provider, [page], OBSERVED)
            settled = connection.execute(
                'SELECT settled_at FROM sports_ai_results',
            ).fetchone()['settled_at']
        self.assertEqual(settled, '2026-10-02T02:00:00+00:00')

    def test_a_shared_import_stamp_is_repaired_and_a_single_late_result_is_kept(self):
        with db.connect() as connection:
            for index, sport in enumerate(('basketball', 'football')):
                for kick in ('2026-10-01T18:00:00+00:00', '2026-10-20T18:00:00+00:00'):
                    fixture_id = f'{sport}-{kick[:10]}'
                    connection.execute(
                        """INSERT INTO sports_ai_fixtures
                           (id,external_id,source,sport,competition,home,away,starts_at,status,
                            statistics_json,odds_json,observed_at)
                           VALUES(?,?,'betexplorer',?,'League',?,?,?,'finished','{}','{}',?)""",
                        (fixture_id, fixture_id, sport, f'Home {index}', f'Away {index}', kick, OBSERVED),
                    )
                    connection.execute(
                        """INSERT INTO sports_ai_results
                           (id,fixture_id,home_score,away_score,source,status,settled_at)
                           VALUES(?,?,110,99,'betexplorer','final',?)""",
                        (f'result-{fixture_id}', fixture_id, OBSERVED),
                    )
                lone = f'{sport}-lone'
                connection.execute(
                    """INSERT INTO sports_ai_fixtures
                       (id,external_id,source,sport,competition,home,away,starts_at,status,
                        statistics_json,odds_json,observed_at)
                       VALUES(?,?,'betexplorer',?,'League','Lone Home','Lone Away',?,'finished','{}','{}',?)""",
                    (lone, lone, sport, '2026-10-05T18:00:00+00:00', OBSERVED),
                )
                connection.execute(
                    """INSERT INTO sports_ai_results
                       (id,fixture_id,home_score,away_score,source,status,settled_at)
                       VALUES(?,?,100,90,'betexplorer','final',?)""",
                    (f'result-{lone}', lone, '2026-11-01T12:00:00+00:00'),
                )
            repaired = repair_imported_settlements(connection)
            basketball = {
                row['starts_at']: row['settled_at']
                for row in connection.execute(
                    """SELECT f.starts_at, r.settled_at
                       FROM sports_ai_results r JOIN sports_ai_fixtures f ON f.id=r.fixture_id
                       WHERE f.sport='basketball' ORDER BY f.starts_at""",
                )
            }
            football = connection.execute(
                "SELECT settled_at FROM sports_ai_results r JOIN sports_ai_fixtures f ON f.id=r.fixture_id WHERE f.sport='football'",
            ).fetchall()
        self.assertEqual(repaired['updated'], 2)
        self.assertEqual(basketball['2026-10-01T18:00:00+00:00'], '2026-10-02T02:00:00+00:00')
        self.assertEqual(basketball['2026-10-20T18:00:00+00:00'], '2026-10-21T02:00:00+00:00')
        self.assertEqual(basketball['2026-10-05T18:00:00+00:00'], '2026-11-01T12:00:00+00:00')
        self.assertTrue(all(row['settled_at'] in (OBSERVED, '2026-11-01T12:00:00+00:00') for row in football))


class BootstrapHistoryTests(unittest.TestCase):
    """Tennis and volleyball need months of results before a model can validate (2026-11-10)."""

    def test_bootstrap_walk_reads_tennis_and_volleyball_days_and_advances(self):
        from history_browser import _advance, _blank_state, plan_visits, BOOTSTRAP_DEPTH_DAYS
        now = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)
        state = _blank_state()
        visits = [v for v in plan_visits(state, set(), now) if v.get('cursor') == 'bootstrap']
        self.assertEqual({v['sport'] for v in visits}, {'tennis', 'volleyball'})
        self.assertTrue(all('betexplorer.com/' in v['url'] and '/results/?year=' in v['url'] for v in visits))
        for v in visits:
            _advance(state, v, now)
        self.assertEqual(state['bootstrap_day'], 3)
        state['bootstrap_day'] = BOOTSTRAP_DEPTH_DAYS + 1
        self.assertFalse([v for v in plan_visits(state, set(), now) if v.get('cursor') == 'bootstrap'])

    def test_modelled_walk_deepens_even_when_no_league_is_waiting(self):
        from history_browser import _blank_state, plan_visits, PRIORITY_DEPTH_DAYS
        now = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)
        state = _blank_state()
        state['priority_day'] = 65
        self.assertTrue([v for v in plan_visits(state, set(), now) if v.get('cursor') == 'priority'])
        state['priority_day'] = PRIORITY_DEPTH_DAYS + 1
        self.assertFalse([v for v in plan_visits(state, set(), now) if v.get('cursor') == 'priority'])


if __name__ == '__main__':
    unittest.main()
