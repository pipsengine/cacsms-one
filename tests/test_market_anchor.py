"""Market anchoring for every sport and league, and linking results across duplicate fixtures."""
import random
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

import db
import market_anchor as ma
from qualification_engine import decide, default_policy
from result_linker import link_results, same_team

NOW = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)


def _row(**overrides):
    base = {
        'fixture_id': 'fx', 'home': 'Home', 'away': 'Away', 'sport': 'football', 'competition': 'Test League',
        'fixture_status': 'scheduled', 'starts_at': (NOW + timedelta(days=1)).isoformat(),
        'market': 'BTTS_YES', 'probability': 0.62, 'decimal_odds': 1.9,
        'odds_observed_at': (NOW - timedelta(minutes=2)).isoformat(),
        'expected_edge': 0.18, 'completeness': 0.8,
        'lifecycle': 'active', 'model_active': 1, 'calibration_status': 'validated',
        'metrics': {'calibration_error': 0.05, 'holdout_fixtures': 40},
        'features': {}, 'quality_hold': False, 'market_registry_active': True,
    }
    base.update(overrides)
    return base


def _pairs(n, sport, league, model_skill, seed=1):
    """Synthetic settled picks. model_skill 1 = model knows the truth; 0 = model is noise around 0.5+."""
    rng = random.Random(seed)
    pairs = []
    for index in range(n):
        truth = rng.uniform(0.2, 0.8)
        odds = 1 / min(0.95, truth * 1.05)  # an efficient market with a 5% margin
        model = min(0.97, max(0.03, model_skill * truth + (1 - model_skill) * rng.uniform(0.6, 0.95)))
        pairs.append({'sport': sport, 'league': league, 'kickoff': f'2026-10-{1 + index % 28:02d}T{index % 24:02d}:00',
                      'p': model, 'odds': odds, 'y': 1 if rng.random() < truth else 0})
    return pairs


class AnchorTests(unittest.TestCase):
    def test_default_blend_is_half_model_half_fair_price(self):
        result = ma.anchor({}, 'tennis', 'ATP', 0.80, 2.0)
        fair = 0.5 / ma.DEFAULT_MARGIN
        self.assertAlmostEqual(result['fair'], fair, places=6)
        self.assertAlmostEqual(result['probability'], 0.5 * 0.8 + 0.5 * fair, places=6)
        self.assertEqual(result['source'], 'default')

    def test_league_overrides_sport_which_overrides_default(self):
        anchors = {'sports': {'football': {'weight': 0.3, 'margin': 1.05, 'cap': 0.15}},
                   'leagues': {'football|premier league': {'weight': 0.8, 'margin': 1.04, 'cap': 0.25}}}
        self.assertEqual(ma.anchor(anchors, 'football', 'Premier League', 0.6, 2.0)['source'], 'league')
        self.assertEqual(ma.anchor(anchors, 'football', 'La Liga', 0.6, 2.0)['weight'], 0.3)
        self.assertEqual(ma.anchor(anchors, 'basketball', 'NBA', 0.6, 2.0)['source'], 'default')

    def test_overconfident_model_loses_weight_and_a_skilled_one_keeps_it(self):
        noisy = ma._fit(_pairs(400, 'football', 'L', model_skill=0.0))
        skilled = ma._fit(_pairs(400, 'football', 'L', model_skill=1.0, seed=2))
        self.assertLessEqual(noisy['weight'], 0.1)
        self.assertGreaterEqual(skilled['weight'], 0.5)

    def test_small_samples_never_switch_the_model_off(self):
        self.assertGreaterEqual(ma._fit(_pairs(60, 'football', 'L', model_skill=0.0))['weight'], ma.WEIGHT_FLOOR)

    def test_learn_covers_every_sport_and_league_with_enough_data(self):
        pairs = (_pairs(120, 'football', 'Premier League', 1.0) + _pairs(25, 'tennis', 'ATP', 1.0, 3)
                 + _pairs(10, 'darts', 'PDC', 1.0, 4))
        with tempfile.TemporaryDirectory() as temp:
            previous = db.DB
            db.DB = Path(temp) / 'anchor.sqlite3'
            try:
                db.initialize()
                with db.connect() as c, patch.object(ma, '_pairs', lambda connection: pairs):
                    learned = ma.learn(c, NOW)
                    self.assertEqual(ma.load(c)['pairs'], 155)
                    self.assertFalse(ma.due(c, NOW + timedelta(hours=1)))
            finally:
                db.DB = previous
        self.assertEqual(set(learned['sports']), {'football', 'tennis', 'darts'})
        self.assertEqual(learned['sports']['darts']['status'], 'default')
        self.assertIn('football|premier league', learned['leagues'])
        self.assertIn('tennis|atp', learned['leagues'])


class DecisionTests(unittest.TestCase):
    def setUp(self):
        self.policy = default_policy()
        self.policy['markets'] = {}
        self.policy['anchors'] = {}  # as revalidate() supplies them: defaults until learned

    def test_large_gap_over_the_market_is_held(self):
        # model 0.90 against a 1.67 price (fair about 0.565): a 33-point gap
        result = decide(_row(probability=0.90, decimal_odds=1.67, expected_edge=0.5), self.policy, NOW)
        self.assertEqual((result['state'], result['reason_code']), ('MONITORING', 'MARKET_DISAGREEMENT'))
        self.assertAlmostEqual(result['evidence']['market_gap'], 0.90 - (1 / 1.67) / ma.DEFAULT_MARGIN, places=4)

    def test_edge_uses_the_anchored_probability_not_the_stored_model_edge(self):
        result = decide(_row(probability=0.62, decimal_odds=1.9, expected_edge=0.9), self.policy, NOW)
        anchored = result['evidence']['anchored_probability']
        self.assertAlmostEqual(result['evidence']['expected_edge'], anchored * 1.9 - 1, places=5)
        self.assertEqual(result['evidence']['model_probability'], 0.62)

    def test_market_only_league_cannot_qualify(self):
        self.policy['anchors'] = {'leagues': {'football|test league': {'weight': 0.0, 'margin': 1.05, 'cap': 0.15}}}
        result = decide(_row(probability=0.62, decimal_odds=1.9), self.policy, NOW)
        self.assertNotEqual(result['state'], 'QUALIFIED')


class ResultLinkTests(unittest.TestCase):
    def test_team_names(self):
        self.assertTrue(same_team('Suns', 'Phoenix Suns'))
        self.assertTrue(same_team('Nottingham Forest FC', 'Nottingham'))
        self.assertTrue(same_team('VILNIUS RYTAS-2', 'Rytas 2'))
        self.assertFalse(same_team('Manchester City', 'Manchester United'))
        self.assertFalse(same_team('Real Madrid', 'Atletico Madrid'))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = db.DB
        db.DB = Path(self.temp.name) / 'link.sqlite3'
        db.initialize()

    def tearDown(self):
        db.DB = self.previous
        self.temp.cleanup()

    def _fixture(self, c, home, away, kickoff, source, result=None, predicted=False):
        fixture = str(uuid.uuid4())
        c.execute("""INSERT INTO sports_ai_fixtures(id,external_id,source,sport,competition,home,away,starts_at,status,statistics_json,odds_json,observed_at)
                     VALUES(?,?,?,'basketball','NBA',?,?,?,'scheduled','{}','{}',?)""",
                  (fixture, fixture, source, home, away, kickoff, kickoff))
        if result:
            c.execute("INSERT INTO sports_ai_results(id,fixture_id,home_score,away_score,status,source,settled_at) VALUES(?,?,?,?,'final','betexplorer',?)",
                      (str(uuid.uuid4()), fixture, result[0], result[1], (NOW - timedelta(hours=1)).isoformat()))
        if predicted:
            c.execute("""INSERT INTO sports_ai_engine_predictions(id,run_id,fixture_id,model_id,feature_id,market,outcome,probability,settlement_json,explanation_json,created_at)
                         VALUES(?,?,?,'m','f','MONEYLINE_HOME','MONEYLINE_HOME',0.6,'{}','{}',?)""",
                      (str(uuid.uuid4()), 'run', fixture, (NOW - timedelta(days=2)).isoformat()))
        return fixture

    def test_links_only_unambiguous_same_orientation_results(self):
        kickoff = (NOW - timedelta(hours=10)).isoformat()
        with db.connect() as c:
            c.execute('PRAGMA foreign_keys=OFF')
            suns = self._fixture(c, 'Suns', 'Trail Blazers', kickoff, 'sportsdataverse', predicted=True)
            self._fixture(c, 'Phoenix Suns', 'Portland Trail Blazers', kickoff, 'betexplorer', result=(119, 132))
            swapped = self._fixture(c, 'Heat', 'Kings', kickoff, 'sportsdataverse', predicted=True)
            self._fixture(c, 'Sacramento Kings', 'Miami Heat', kickoff, 'betexplorer', result=(108, 114))
            result = link_results(c, NOW)
            linked = c.execute('SELECT home_score,away_score,source FROM sports_ai_results WHERE fixture_id=?', (suns,)).fetchone()
            self.assertIsNone(c.execute('SELECT 1 FROM sports_ai_results WHERE fixture_id=?', (swapped,)).fetchone())
            self.assertEqual(c.execute('SELECT status FROM sports_ai_fixtures WHERE id=?', (suns,)).fetchone()[0], 'finished')
        self.assertEqual((linked[0], linked[1]), (119, 132))
        self.assertTrue(linked[2].startswith('linked:betexplorer:'))
        self.assertEqual((result['linked'], result['ambiguous']), (1, 1))


if __name__ == '__main__':
    unittest.main()
