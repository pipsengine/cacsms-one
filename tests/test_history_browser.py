import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

import db
from history_browser import (
    LAGOS,
    _advance,
    _blank_state,
    collect_match_history,
    parse_betexplorer_results,
    parse_soccerway_results,
    plan_visits,
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

    def test_a_page_without_a_finished_score_stores_nothing(self):
        rows = parse_betexplorer_results(
            '<html><title>Verify you are human</title><body>captcha</body></html>',
            'football', 'https://www.betexplorer.com/football/results/', OBSERVED, NOW,
        )
        self.assertEqual(rows, [])


class HistoryPlanTests(unittest.TestCase):
    def test_waiting_leagues_read_modelled_sports_before_the_general_walk(self):
        waiting = plan_visits(_blank_state(), {'premier-league'}, NOW)
        sports = [visit['sport'] for visit in waiting if visit['kind'] == 'betexplorer']
        self.assertEqual(sports[:3], ['football', 'basketball', 'ice_hockey'])
        self.assertNotIn('tennis', sports)
        self.assertEqual(len(sports), 6)
        self.assertEqual(sum(visit['kind'] == 'soccerway' for visit in waiting), 3)
        state = _blank_state()
        for visit in waiting:
            if visit['kind'] == 'betexplorer':
                state = _advance(state, visit, NOW)
        self.assertEqual(state['priority_sport'], 0)
        self.assertEqual(state['priority_day'], 3)

        idle = plan_visits(_blank_state(), set(), NOW)
        idle_sports = [visit['sport'] for visit in idle if visit['kind'] == 'betexplorer']
        self.assertEqual(idle_sports, ['football', 'basketball'])
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


if __name__ == '__main__':
    unittest.main()
