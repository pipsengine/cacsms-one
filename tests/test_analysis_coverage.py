import json
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import db
from analysis_coverage import coverage_report, dataset_signature, record_attempt, refresh_models, repair_identities
from history_browser import _advance, _blank_state, plan_visits, repair_imported_settlements
from prediction_engine import build_features, league_key, quoted_coverage_fixtures, run_analysis, train_model, upcoming_fixture_sql
from sport_support import WINNER_SPORTS


class AnalysisCoverageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = db.DB
        db.DB = Path(self.temp.name) / 'coverage.sqlite3'
        db.initialize()
        self.now = datetime.now(timezone.utc)

    def tearDown(self):
        db.DB = self.previous
        self.temp.cleanup()

    def fixture(self, c, sport='tennis', league='ATP Paris', when=None, score=None, home='Player A', away='Player B', settled=None):
        identifier = str(uuid.uuid4())
        kickoff = when or self.now + timedelta(hours=4)
        c.execute('''INSERT INTO sports_ai_fixtures
            (id,external_id,source,sport,competition,home,away,starts_at,status,statistics_json,odds_json,observed_at)
            VALUES(?,?,'test',?,?,?,?,?,?,'{}','{}',?)''',
            (identifier, identifier, sport, league, home, away, kickoff.isoformat(),
             'finished' if score else 'scheduled', self.now.isoformat()))
        if score:
            c.execute('''INSERT INTO sports_ai_results(id,fixture_id,home_score,away_score,status,source,settled_at)
                VALUES(?,?,?,?,'final','observed',?)''',
                (str(uuid.uuid4()), identifier, *score, (settled or kickoff + timedelta(hours=1)).isoformat()))
        return dict(c.execute('SELECT * FROM sports_ai_fixtures WHERE id=?', (identifier,)).fetchone())

    def history(self, c, sport='tennis', count=65):
        for index in range(count):
            self.fixture(c, sport=sport, when=self.now - timedelta(days=count-index), score=(2, 0) if index % 3 else (0, 2))

    def test_league_identity_preserves_country_gender_division_and_age(self):
        self.assertNotEqual(league_key('Ghana Premier League'), league_key('English Premier League'))
        self.assertNotEqual(league_key('Italy Serie A, Women'), league_key('Italy Serie A'))
        self.assertNotEqual(league_key('Portugal Liga Portugal 2'), league_key('Portugal Liga Portugal'))
        self.assertNotEqual(league_key('England Premier League U21'), league_key('England Premier League'))
        self.assertEqual(league_key('Germany Bundesliga'), league_key('1. Fußball-Bundesliga 2025/26'))
        self.assertEqual(league_key('Italy Serie A'), league_key('Italian Serie A 2026/27'))

    def test_general_history_advances_even_with_permanent_priority_gaps(self):
        state = _blank_state()
        visited = set()
        for _ in range(10):
            for visit in plan_visits(state, {('football', 'unfilled')}, self.now):
                if visit.get('cursor') == 'general':
                    visited.add(visit['sport'])
                    _advance(state, visit, self.now)
        self.assertIn('tennis', visited)
        self.assertIn('volleyball', visited)
        self.assertIn('table_tennis', visited)

    def test_tennis_tournament_modifier_and_player_slug_are_parsed(self):
        from history_browser import parse_betexplorer_results
        html = '''<table><tr class="js-tournament"><th><a class="table-main__tournament table-main__tournament--tennis">ATP - Singles: Paris, hard</a></th></tr>
          <tr data-dt="6,11,2026,14,10"><td class="table-main__tt">
          <a href="/tennis/atp-singles/paris/shelton-ben-paul-tommy/dnsgwGt1/">
          <span class="table-main__teamLine--home"><strong>Shelton B.</strong></span>
          <span class="table-main__teamLine--away">Paul T.</span></a></td>
          <td class="table-main__result"><a><strong>2:1</strong></a></td></tr></table>'''
        rows = parse_betexplorer_results(html, 'tennis', 'https://www.betexplorer.com/tennis/results/', self.now.isoformat(), self.now)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['home'], rows[0]['away']), ('Shelton Ben', 'Paul Tommy'))

    def test_esports_results_keep_their_game_identity(self):
        from history_browser import _sport_for
        self.assertEqual(_sport_for('esports', 'Dota 2: International'), 'dota2')
        self.assertEqual(_sport_for('esports', 'Counter-Strike: Major'), 'counterstrike')
        self.assertEqual(_sport_for('esports', 'League of Legends: Worlds'), 'lol')
        self.assertIsNone(_sport_for('esports', 'Unidentified game'))

    def test_elapsed_instant_and_old_cooldown_are_compared_chronologically(self):
        with db.connect() as c:
            elapsed = self.fixture(c, when=self.now - timedelta(seconds=10))
            target = self.fixture(c)
            record_attempt(c, target, 'insufficient_history', 'test', now=self.now - timedelta(hours=1))
            upcoming = [r['id'] for r in c.execute('SELECT f.id FROM sports_ai_fixtures f WHERE '+upcoming_fixture_sql('f'))]
            self.assertNotIn(elapsed['id'], upcoming)
            self.assertIn(target['id'], [r['id'] for r in quoted_coverage_fixtures(c, self.now)])

    def test_fair_queue_reaches_other_sports_before_repeating_a_sport(self):
        with db.connect() as c:
            for index in range(20):
                self.fixture(c, sport='football', league=f'League {index}')
            self.fixture(c, sport='tennis')
            self.fixture(c, sport='volleyball')
            chosen = quoted_coverage_fixtures(c, self.now, limit=3)
            self.assertEqual({row['sport'] for row in chosen}, {'football', 'tennis', 'volleyball'})

    def test_fixture_predicted_only_by_a_retired_model_is_predicted_again(self):
        """2026-11-10: after a model switch the new model covered 31 of 1,190 fixtures,
        because predictions from retired versions counted as coverage."""
        from prediction_engine import PIPELINE_VERSION
        with db.connect() as c:
            c.execute('PRAGMA foreign_keys=OFF')
            target = self.fixture(c, sport='football', league='Liga Portugal')
            params = json.dumps({'pipeline_version': PIPELINE_VERSION})
            for model_id, active in (('old', 0), ('new', 1)):
                c.execute("""INSERT INTO sports_ai_model_registry
                    (id,training_run_id,sport,model_name,version,status,active,parameters_json,metrics_json,lifecycle)
                    VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (model_id, 'run', 'football', 'ens', model_id, 'active' if active else 'inactive', active, params, '{}',
                     'active' if active else 'suspended'))
            c.execute("INSERT INTO sports_ai_feature_snapshots(id,fixture_id,sport,cutoff_at,features_json,source_refs_json,generated_at) VALUES('feat',?,?,?,?,?,?)",
                      (target['id'], 'football', self.now.isoformat(), json.dumps({'pipeline_version': PIPELINE_VERSION}), '[]', self.now.isoformat()))
            c.execute("""INSERT INTO sports_ai_engine_predictions(id,run_id,fixture_id,model_id,feature_id,market,outcome,probability,settlement_json,explanation_json)
                         VALUES('p-old','r',?,'old','feat','HOME','HOME',0.5,'{}','{}')""", (target['id'],))
            self.assertIn(target['id'], [r['id'] for r in quoted_coverage_fixtures(c, self.now, limit=50)])
            c.execute("""INSERT INTO sports_ai_engine_predictions(id,run_id,fixture_id,model_id,feature_id,market,outcome,probability,settlement_json,explanation_json)
                         VALUES('p-new','r2',?,'new','feat','HOME','HOME',0.5,'{}','{}')""", (target['id'],))
            self.assertNotIn(target['id'], [r['id'] for r in quoted_coverage_fixtures(c, self.now, limit=50)])

    def test_history_follows_the_club_across_spellings_but_not_across_countries(self):
        """Bookmaker league names differ from the results source; the club's own results are used,
        but only within the fixture's country so a same-named foreign club is never borrowed."""
        import prediction_engine
        prediction_engine._TEAM_INDEX.clear()
        from prediction_engine import history_competitions
        with db.connect() as c:
            for i in range(4):
                self.fixture(c, sport='football', league='Brazil: Serie B', home='America MG', away=f'Opp {i}',
                             when=self.now - timedelta(days=10 + i), score=(1, 0))
                self.fixture(c, sport='football', league='Mexico: Liga MX', home='America', away=f'Rival {i}',
                             when=self.now - timedelta(days=10 + i), score=(2, 0))
            chosen = history_competitions(c, 'football', 'Brazil Brasileiro Serie B', ('America FC MG', 'Unknown FC'))
        self.assertIn('Brazil: Serie B', chosen)
        self.assertNotIn('Mexico: Liga MX', chosen)

    def test_tennis_players_match_across_bookmaker_and_result_spellings(self):
        from winner_model import build_winner_features
        with db.connect() as c:
            for i in range(4):
                self.fixture(c, sport='tennis', league='ITF Men', home='Verdes E. D.', away=f'Rival {i}',
                             when=self.now - timedelta(days=5 + i), score=(2, 0))
                self.fixture(c, sport='tennis', league='ITF Men', home='Bothma John', away=f'Other {i}',
                             when=self.now - timedelta(days=5 + i), score=(2, 1))
            upcoming = self.fixture(c, sport='tennis', league='ITF Men Sharm El Sheikh',
                                    home='Eric David Verdes', away='John Bothma')
            built = build_winner_features(c, upcoming)
        self.assertIsNotNone(built)
        self.assertEqual((built['features']['home_games'], built['features']['away_games']), (4, 4))

    def test_models_are_not_retrained_on_every_new_result(self):
        """2026-11-10: retraining on each new final replaced the football model three times
        in a day and voided the board each time. Retraining now follows a cadence."""
        from analysis_coverage import retrain_due
        now = datetime(2026, 11, 10, 16, tzinfo=timezone.utc)
        def model(hours_ago, finals, validated=True):
            return {'created_at': (now - timedelta(hours=hours_ago)).isoformat(),
                    'metrics_json': json.dumps({'available_finals': finals}),
                    'calibration_status': 'validated' if validated else 'uncalibrated'}
        self.assertFalse(retrain_due(model(8, 28000), 29000, now))          # too soon
        self.assertFalse(retrain_due(model(30, 28000), 29000, now))         # < 10% more results
        self.assertTrue(retrain_due(model(30, 28000), 31000, now))          # a day later, +10%
        self.assertFalse(retrain_due(model(3, 500, False), 900, now))       # bootstrap: too soon
        self.assertTrue(retrain_due(model(7, 500, False), 900, now))        # bootstrap: 6h, +20%
        self.assertTrue(retrain_due(None, 0, now))                          # no model yet

    def test_first_attempt_is_not_starved_by_a_retry_in_an_earlier_league(self):
        with db.connect() as c:
            retry = self.fixture(c, sport='football', league='AAA League')
            first = self.fixture(c, sport='football', league='ZZZ League', when=self.now+timedelta(days=2))
            record_attempt(c, retry, 'insufficient_history', 'test', now=self.now-timedelta(hours=2))
            self.assertEqual(quoted_coverage_fixtures(c, self.now, limit=1)[0]['id'], first['id'])

    def test_this_weeks_fixtures_are_predicted_before_distant_ones(self):
        """2026-11-10: after a model switch, December and May fixtures shared each batch
        with the current week, so qualification and the jackpot had nothing to use."""
        with db.connect() as c:
            distant = [self.fixture(c, sport='football', league=f'AAA League {i}', when=self.now + timedelta(days=30 + i))
                       for i in range(3)]
            near = [self.fixture(c, sport='football', league=f'ZZZ League {i}', when=self.now + timedelta(days=3))
                    for i in range(3)]
            chosen = [r['id'] for r in quoted_coverage_fixtures(c, self.now, limit=3)]
            self.assertEqual(set(chosen), {row['id'] for row in near})
            # Distant fixtures still follow once the week is covered.
            later = [r['id'] for r in quoted_coverage_fixtures(c, self.now, limit=6)]
            self.assertEqual(set(later[3:]), {row['id'] for row in distant})

    def test_each_winner_sport_has_own_features_and_never_generates_totals(self):
        from prediction_engine import settle_market
        with db.connect() as c:
            for sport in WINNER_SPORTS:
                if sport == 'cricket':
                    continue
                self.history(c, sport, count=6)
                target = self.fixture(c, sport=sport)
                built = build_features(c, target)
                self.assertIsNotNone(built, sport)
                self.assertEqual(built['features']['historical_games'], 6)
                _, result = settle_market(sport, 'MONEYLINE_HOME', built['features'], {})
                self.assertAlmostEqual(sum(result.values()), 1)
                self.assertIsNone(settle_market(sport, 'OVER_2.5', built['features'], {})[1])

    def test_cricket_runs_do_not_establish_a_winner(self):
        with db.connect() as c:
            self.history(c, 'cricket', count=6)
            target = self.fixture(c, sport='cricket')
            self.assertIsNone(build_features(c, target))

    def test_basketball_training_labels_agree_with_home_probability(self):
        from prediction_engine import _outcome_probabilities
        features = {'expected_home_points': 110, 'expected_away_points': 90,
                    'home_score_sd': 10, 'away_score_sd': 10, 'total_sd': 14.142}
        for family in ('baseline', 'monte_carlo'):
            probabilities, actual = _outcome_probabilities('basketball', features, 110, 90, 0, family, None, None)
            self.assertEqual(actual, 0)
            self.assertGreater(probabilities[0], probabilities[1])

    def test_winner_training_and_inference_are_persisted_without_automatic_activation(self):
        with db.connect() as c:
            self.history(c)
            target = self.fixture(c)
            trained = train_model(c, 'tennis')
            self.assertGreater(trained['metrics']['fixtures_scored'], 30)
            self.assertGreaterEqual(trained['metrics']['holdout_fixtures'], 10)
            self.assertFalse(trained['active'])
            result = run_analysis(c, 'test', lambda *args: None, fixture_id=target['id'])
            self.assertEqual(result['predictions'], 2)
            self.assertEqual(result['qualified'], 0)
            reasons = coverage_report(c)['leagues'][0]['reasons']
            self.assertEqual(reasons['research_prediction'], 1)

    def test_late_observed_results_cannot_leak_into_winner_training(self):
        with db.connect() as c:
            for index in range(12):
                self.fixture(c, when=self.now-timedelta(days=index+1), score=(2, 1), settled=self.now)
            self.assertEqual(train_model(c, 'tennis')['metrics']['fixtures_scored'], 0)

    def test_a_repaired_catalogue_import_can_train(self):
        with db.connect() as c:
            for index in range(40):
                self.fixture(
                    c, when=self.now-timedelta(days=40-index), score=(2, 1),
                    home='Player A', away='Player B', settled=self.now,
                )
            self.assertEqual(train_model(c, 'tennis')['metrics']['fixtures_scored'], 0)
            self.assertGreater(repair_imported_settlements(c, ('tennis',))['updated'], 0)
            trained = train_model(c, 'tennis')
        self.assertGreater(trained['metrics']['fixtures_scored'], 30)
        self.assertFalse(trained['active'])

    def test_model_refresh_uses_new_results_and_skips_unchanged_data(self):
        with db.connect() as c:
            self.fixture(c)
            first = refresh_models(c, limit=10)
            self.assertEqual(len(first), 1)
            self.assertEqual(refresh_models(c, limit=10), [])
            original = dataset_signature(c, 'tennis')
            self.history(c, count=6)
            self.assertNotEqual(dataset_signature(c, 'tennis'), original)
            # New results alone do not retrain inside the cadence window...
            self.assertEqual(refresh_models(c, limit=10), [])
            # ...but do once the window has passed and the results have grown.
            second = refresh_models(c, limit=10, now=datetime.now(timezone.utc) + timedelta(hours=7))
            self.assertEqual(len(second), 1)
            self.assertNotEqual(first[0]['model_id'], second[0]['model_id'])

    def test_womens_fixtures_and_aliases_are_repaired_idempotently(self):
        with db.connect() as c:
            female = self.fixture(c, sport='football', league='Italy Serie A, Women')
            alias = self.fixture(c, sport='tischtennis')
            self.assertEqual(repair_identities(c)['fixtures_repaired'], 2)
            self.assertEqual(c.execute('SELECT sport FROM sports_ai_fixtures WHERE id=?', (female['id'],)).fetchone()[0], 'frauenfussball')
            self.assertEqual(c.execute('SELECT sport FROM sports_ai_fixtures WHERE id=?', (alias['id'],)).fetchone()[0], 'table_tennis')
            self.assertEqual(repair_identities(c)['fixtures_repaired'], 0)

    def test_worker_does_not_start_a_cycle_without_the_leader_lease(self):
        import worker
        with patch.object(worker, 'initialize'), patch.object(worker, '_touch_heartbeat', return_value=False), patch.object(worker, '_run_cycle') as cycle:
            self.assertEqual(worker.tick()['status'], 'standby')
            cycle.assert_not_called()

    def test_all_sports_prediction_api_handles_empty_and_status_filtered_results(self):
        from fastapi.testclient import TestClient
        from main import app
        with patch.dict('os.environ', {'SPORTS_LOCAL_PREVIEW': '1', 'SPORTS_PRODUCTION': '0'}):
            client = TestClient(app, client=('127.0.0.1', 55000))
            for sport in ('tennis', 'ice_hockey', 'volleyball', 'mma'):
                response = client.get('/api/sports-ai/predictions', params={'group': 'fixture', 'sport': sport, 'status': 'qualified'})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()['items'], [])

    def test_ice_hockey_ot_winner_is_distinct_from_legacy_regulation_quote(self):
        from prediction_engine import normalize_quote_market
        from sportybet_research import map_market
        fixture = {'home': 'Home', 'away': 'Away'}
        self.assertIsNone(normalize_quote_market('ice_hockey', fixture, {'market': 'h2h', 'outcome': 'home'}))
        mapped = map_market('ice_hockey', 'Winner (incl. overtime)', 'Home')
        self.assertEqual(mapped['provider_market'], 'full_match_winner')
        self.assertEqual(normalize_quote_market('ice_hockey', fixture, {'market': mapped['provider_market'], 'outcome': 'home'}), 'MONEYLINE_HOME')


if __name__ == '__main__':
    unittest.main()
