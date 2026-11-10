"""Universal intelligence: research sources, capability catalogue, conservative ranking, analyzer."""
import json
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

import db
import universal_intelligence as ui

NOW = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)
LATER = (NOW + timedelta(hours=6)).isoformat()


def evidence(probability, odds, holdout=400, calibration_error=0.03, completeness=0.9, age=5):
    return json.dumps({
        'probability': probability, 'implied_probability': 1 / odds, 'expected_value': probability * odds - 1,
        'calibration_error': calibration_error, 'holdout_fixtures': holdout, 'completeness': completeness,
        'uncertainty': 0.9, 'odds_age_minutes': age, 'model_version': 'v1',
    })


class UniversalIntelligenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = db.DB
        db.DB = Path(self.temp.name) / 'universal.sqlite3'
        db.initialize()

    def tearDown(self):
        db.DB = self.previous
        self.temp.cleanup()

    def fixture(self, c, sport='football', home='A', away='B', starts=LATER):
        fid = str(uuid.uuid4())
        c.execute(
            """INSERT INTO sports_ai_fixtures(id,external_id,source,sport,competition,home,away,starts_at,status,
                 statistics_json,odds_json,observed_at) VALUES(?,?,?,?,?,?,?,?,'scheduled','{}','{}',?)""",
            (fid, fid, 'test', sport, 'Test League', home, away, starts, NOW.isoformat()),
        )
        return fid

    def decision(self, c, fid, market, state, ev_json, sport='football'):
        c.execute(
            """INSERT INTO sports_ai_qualification_decisions(id,prediction_id,fixture_id,sport,competition,market,state,
                 reason_code,reason,evidence_json,products_json,policy_version,decision_version,fingerprint,
                 starts_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,'[]',1,1,'f',?,?,?)""",
            (str(uuid.uuid4()), str(uuid.uuid4()), fid, sport, 'Test League', market, state,
             'QUALIFIED_OK' if state == 'QUALIFIED' else 'EDGE_BELOW_MARKET', f'{state} reason', ev_json,
             LATER, NOW.isoformat(), NOW.isoformat()),
        )

    # ---- scoring

    def test_conservative_ev_subtracts_margin_times_odds(self):
        scored = ui.score_market(0.60, 2.0, calibration_error=0.03, holdout=400)
        margin = ui.conservative_margin(0.60, 0.03, 400)
        self.assertAlmostEqual(scored['expected_value'], 0.20, places=6)
        self.assertAlmostEqual(scored['conservative_ev'], 0.20 - margin * 2.0, places=6)
        self.assertAlmostEqual(scored['break_even'], 0.5)
        self.assertTrue(scored['preferred_band'])
        self.assertTrue(scored['robust'])

    def test_no_holdout_is_never_conservatively_positive(self):
        scored = ui.score_market(0.9, 3.0, holdout=0)
        self.assertLess(scored['conservative_ev'], 0)
        self.assertFalse(scored['robust'])

    def test_missing_price_scores_nothing(self):
        scored = ui.score_market(0.6, None)
        self.assertIsNone(scored['expected_value'])
        self.assertFalse(scored['robust'])

    def test_stored_settlement_ev_is_preferred_over_binary_formula(self):
        # A push-capable market: stored EV already accounts for the push.
        scored = ui.score_market(0.55, 2.0, expected_value=0.15, calibration_error=0.0, holdout=10_000)
        self.assertAlmostEqual(scored['expected_value'], 0.15)

    # ---- board

    def test_board_ranks_by_conservative_ev_and_returns_fixture_verdicts(self):
        with db.connect() as c:
            strong = self.fixture(c, home='Strong')
            thin = self.fixture(c, home='Thin')
            none = self.fixture(c, home='None')
            # Higher raw EV but tiny holdout: conservative EV must rank it below.
            self.decision(c, thin, 'HOME', 'QUALIFIED', evidence(0.70, 2.0, holdout=30, calibration_error=0.05))
            self.decision(c, strong, 'HOME', 'QUALIFIED', evidence(0.62, 2.0, holdout=2000, calibration_error=0.02))
            self.decision(c, none, 'AWAY', 'MONITORING', evidence(0.5, 2.05))
            board = ui.opportunity_board(c, {}, now=NOW)
            self.assertEqual(board['items'][0]['home'], 'Strong')
            self.assertEqual(board['summary']['fixtures_without_opportunity'], 1)
            fixtures = ui.opportunity_board(c, {'view': 'fixtures'}, now=NOW)['items']
            verdicts = {f['home']: f['verdict'] for f in fixtures}
            self.assertEqual(verdicts['None'], ui.NO_OPPORTUNITY)
            self.assertEqual(verdicts['Strong'], 'QUALIFIED')
            self.assertEqual(fixtures[-1]['home'], 'None')

    def test_board_filters_odds_probability_sport_and_state(self):
        with db.connect() as c:
            f1 = self.fixture(c)
            f2 = self.fixture(c, sport='basketball')
            self.decision(c, f1, 'HOME', 'QUALIFIED', evidence(0.6, 1.9))
            self.decision(c, f1, 'OVER_2.5', 'QUALIFIED', evidence(0.3, 4.0))
            self.decision(c, f2, 'MONEYLINE_HOME', 'REJECTED', evidence(0.6, 1.5), sport='basketball')
            banded = ui.opportunity_board(c, {'odds_min': 1.7, 'odds_max': 2.5}, now=NOW)
            self.assertEqual([i['market'] for i in banded['items']], ['HOME'])
            self.assertEqual(ui.opportunity_board(c, {'min_probability': 0.5, 'sport': 'football'}, now=NOW)['total'], 1)
            self.assertEqual(ui.opportunity_board(c, {'state': 'REJECTED'}, now=NOW)['total'], 1)
            with self.assertRaises(ValueError):
                ui.opportunity_board(c, {'state': 'BOGUS'}, now=NOW)

    def test_started_fixtures_are_excluded_by_default(self):
        with db.connect() as c:
            past = self.fixture(c, starts=(NOW - timedelta(hours=1)).isoformat())
            c.execute('UPDATE sports_ai_fixtures SET starts_at=? WHERE id=?', ((NOW - timedelta(hours=1)).isoformat(), past))
            self.decision(c, past, 'HOME', 'QUALIFIED', evidence(0.6, 2.0))
            c.execute('UPDATE sports_ai_qualification_decisions SET starts_at=?', ((NOW - timedelta(hours=1)).isoformat(),))
            self.assertEqual(ui.opportunity_board(c, {}, now=NOW)['total'], 0)
            self.assertEqual(ui.opportunity_board(c, {'include_started': True}, now=NOW)['total'], 1)

    # ---- capability catalogue

    def test_new_sport_is_registered_as_unsupported_not_predicted(self):
        with db.connect() as c:
            self.fixture(c, sport='kabaddi')
            added = ui.register_discovered_sports(c)
            self.assertIn('kabaddi', added['registered'])
            self.assertEqual(ui.register_discovered_sports(c)['registered'], [])
            catalogue = {s['code']: s for s in ui.capability_catalogue(c)['sports']}
            self.assertEqual(catalogue['kabaddi']['prediction_status'], 'not_modelled')
            self.assertEqual(catalogue['kabaddi']['capability'], 'unsupported')
            self.assertIsNone(catalogue['kabaddi']['model_family'])

    def test_model_families_are_sport_specific(self):
        from sport_support import family_of
        self.assertEqual(family_of('football'), 'goal_distribution')
        self.assertEqual(family_of('basketball'), 'basketball_points')
        self.assertEqual(family_of('tennis'), 'match_winner')
        self.assertIsNone(family_of('kabaddi'))

    # ---- sources

    def test_named_sources_await_review_and_never_self_approve(self):
        with db.connect() as c:
            report = ui.research_sources(c, now=NOW)
            keys = {s['key']: s for s in report['sources']}
            for key in ('flashscore', 'sofascore', 'betexplorer', 'oddsportal', 'forebet', 'asia_basket', 'sportybet'):
                self.assertIn(key, keys)
            self.assertEqual(keys['oddsportal']['availability'], 'awaiting_terms_review')
            self.assertFalse(keys['flashscore']['approved'])
            self.assertEqual(keys['flashscore']['freshness'], 'never')

    def test_operator_review_drives_availability_and_extends_registry(self):
        from source_permissions import record_review
        with db.connect() as c:
            record_review(c, 'www.forebet.com', False, 'ops', 'https://www.forebet.com/terms', now=NOW)
            record_review(c, 'stats.example.org', True, 'ops', 'https://stats.example.org/terms', now=NOW)
            c.execute("""INSERT INTO sports_ai_source_robots(host,robots_status,robots_txt,checked_at)
                         VALUES('stats.example.org','absent','',?)""", (NOW.isoformat(),))
            c.execute("""INSERT INTO sports_ai_source_permission_checks(id,host,url,purpose,allowed,reason,created_at)
                         VALUES('x','stats.example.org','https://stats.example.org/a','collection',1,'ok',?)""",
                      ((NOW - timedelta(hours=1)).isoformat(),))
            keys = {s['key']: s for s in ui.research_sources(c, now=NOW)['sources']}
            self.assertEqual(keys['forebet']['availability'], 'declined_by_review')
            extension = keys['stats.example.org']
            self.assertEqual(extension['registered'], 'operator_approval')
            self.assertEqual(extension['availability'], 'available')
            self.assertEqual(extension['freshness'], 'fresh')
            self.assertEqual(extension['reliability'], 1.0)

    # ---- analyzer

    def test_analyzer_lists_modelled_and_unmodelled_markets(self):
        with db.connect() as c:
            c.execute('PRAGMA foreign_keys=OFF')
            fid = self.fixture(c)
            c.execute("""INSERT INTO sports_ai_model_registry(id,training_run_id,sport,model_name,version,status,active,
                           parameters_json,metrics_json,lifecycle) VALUES('m','t','football','poisson','v1','active',1,'{}',?,'active')""",
                      (json.dumps({'calibration_error': 0.03, 'holdout_fixtures': 500}),))
            c.execute("""INSERT INTO sports_ai_engine_predictions(id,run_id,fixture_id,model_id,feature_id,market,outcome,
                           probability,settlement_json,explanation_json,created_at) VALUES('p','r',?,'m','fs','HOME','HOME',0.6,'{}','{}',?)""",
                      (fid, NOW.isoformat()))
            c.execute("""INSERT OR IGNORE INTO sports_ai_provider_registry(id,provider_key,display_name,sport,adapter)
                         VALUES('prov','sportybet','SportyBet','both','browser')""")
            prov = c.execute("SELECT id FROM sports_ai_provider_registry WHERE provider_key='sportybet'").fetchone()[0]
            for oid, market, outcome, odds in (('q1', '1X2', 'HOME', 2.0), ('q2', 'CORNERS', 'OVER', 1.8)):
                c.execute("""INSERT INTO sports_ai_odds_snapshots(id,fixture_id,provider_id,bookmaker,market,outcome,line,
                               decimal_odds,source_url,observed_at) VALUES(?,?,?,'SportyBet',?,?,NULL,?,'https://www.sportybet.com/x',?)""",
                          (oid, fid, prov, market, outcome, odds, (NOW - timedelta(minutes=10)).isoformat()))
            c.execute("""INSERT INTO sports_ai_value_opportunities(id,prediction_id,odds_id,implied_probability,expected_edge,
                           status,reason,evaluated_at,data_completeness) VALUES('v','p','q1',0.5,0.2,'qualified','ok',?,0.9)""",
                      (NOW.isoformat(),))
            result = ui.fixture_markets(c, fid, now=NOW)
            self.assertEqual(result['counts']['modelled'], 1)
            home = result['markets'][0]
            self.assertEqual(home['odds'], 2.0)
            self.assertAlmostEqual(home['break_even'], 0.5)
            self.assertAlmostEqual(home['expected_value'], 0.2)
            self.assertEqual(result['verdict'], ui.NO_OPPORTUNITY)  # no qualification decision stored
            self.assertEqual([q['market'] for q in result['unmodelled_quotes']], ['CORNERS'])
            with self.assertRaises(LookupError):
                ui.fixture_markets(c, 'missing', now=NOW)


if __name__ == '__main__':
    unittest.main()
