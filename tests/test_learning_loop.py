"""Phase 3: waiting prices reach late predictions; drift triggers a governed retrain."""
import json
import sys
import unittest
import uuid
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

import db
import learning_loop
import test_high_odds_service as high_odds_tests

def _base(name):
    return getattr(high_odds_tests.HighOddsServiceTests, name)


class LearningLoopTests(unittest.TestCase):
    setUp = _base('setUp')
    tearDown = _base('tearDown')
    _analysis = _base('_analysis')
    _model = _base('_model')
    _opportunity = _base('_opportunity')

    def _waiting_price(self, c, fixture_id, market='HOME', odds=2.1):
        quote = c.execute('SELECT id FROM sports_ai_odds_snapshots WHERE fixture_id=?', (fixture_id,)).fetchone()
        observation = str(uuid.uuid4())
        c.execute(
            """INSERT INTO sports_ai_sportybet_observations
               (id,sport,competition,home,away,market_label,selection_label,decimal_odds,source_url,observed_at,
                confirmation_status,fixture_id,canonical_market,odds_id,import_status,value_status,evidence_json,created_at)
               VALUES(?,'football','League A',?,'A','1X2','Home',?,'https://www.sportybet.com/ng/sport/football',?,
                      'confirmed',?,?,?,'imported','awaiting_model','{}',?)""",
            (observation, fixture_id + market, odds, self.now.isoformat(), fixture_id, market, quote['id'] if quote else None, self.now.isoformat()),
        )
        return observation

    def _status(self, c, observation):
        return c.execute('SELECT value_status FROM sports_ai_sportybet_observations WHERE id=?', (observation,)).fetchone()[0]

    def test_waiting_price_is_assessed_once_its_prediction_exists(self):
        with db.connect() as c:
            item = self._opportunity(c, odds=2.1, probability=.62, edge=.30)
            waiting = self._waiting_price(c, item['fixture_id'])
            result = learning_loop.reassess_awaiting_model(c, self.now)
            self.assertEqual(result['reassessed'], 1)
            self.assertNotEqual(self._status(c, waiting), 'awaiting_model')
            self.assertEqual(learning_loop.backlog(c, self.now)['awaiting_model'], 0)

    def test_price_without_a_prediction_keeps_waiting(self):
        with db.connect() as c:
            item = self._opportunity(c)
            waiting = self._waiting_price(c, item['fixture_id'], market='AWAY')
            learning_loop.reassess_awaiting_model(c, self.now)
            self.assertEqual(self._status(c, waiting), 'awaiting_model')
            self.assertEqual(learning_loop.backlog(c, self.now)['upcoming'], 1)

    def test_started_fixtures_are_closed_as_expired(self):
        with db.connect() as c:
            item = self._opportunity(c)
            waiting = self._waiting_price(c, item['fixture_id'], market='AWAY')
            later = item['kickoff'] + timedelta(minutes=5)
            result = learning_loop.reassess_awaiting_model(c, later)
            self.assertEqual(self._status(c, waiting), 'expired')
            self.assertEqual(result['counts'], {'expired': 1})

    def test_pass_size_is_bounded(self):
        with db.connect() as c:
            for index in range(3):
                item = self._opportunity(c, home=f'h{index}', away=f'a{index}', odds=2.1, probability=.62, edge=.3)
                self._waiting_price(c, item['fixture_id'])
            self.assertEqual(learning_loop.reassess_awaiting_model(c, self.now, limit=2)['reassessed'], 2)
            self.assertEqual(learning_loop.reassess_awaiting_model(c, self.now, limit=2)['reassessed'], 1)

    def _drift(self, c, sport='football', when=None):
        c.execute(
            """INSERT INTO sports_ai_model_drift_events(id,model_id,sport,market,drift_type,metric_value,threshold,details_json,created_at)
               VALUES(?,?,?,'HOME','probability_shift',0.2,0.15,'{}',?)""",
            (str(uuid.uuid4()), self.football_model, sport, (when or self.now).isoformat()),
        )

    def test_drift_trains_one_inactive_candidate_then_cools_down(self):
        calls = []

        def fake_train(connection, sport):
            calls.append(sport)
            connection.execute(
                """INSERT INTO sports_ai_training_runs
                   (id,sport,model_name,version,status,training_window_json,parameters_json,metrics_json,sample_size,created_at)
                   VALUES(?,?,'m','99','insufficient_data','{}','{}','{}',0,?)""",
                (str(uuid.uuid4()), sport, (self.now + timedelta(minutes=1)).isoformat()),
            )
            return {'status': 'insufficient_data', 'model_id': 'candidate'}
        from datetime import datetime
        with db.connect() as c:
            latest = c.execute('SELECT MAX(created_at) FROM sports_ai_training_runs').fetchone()[0]
            self.now = datetime.fromisoformat(latest) + timedelta(hours=25)
            self._drift(c)
            audits = []
            with patch('prediction_engine.train_model', fake_train):
                first = learning_loop.drift_retraining(c, 'test', lambda *a: audits.append(a), self.now + timedelta(minutes=1))
                second = learning_loop.drift_retraining(c, 'test', None, self.now + timedelta(minutes=2))
        self.assertEqual(calls, ['football'])
        self.assertFalse(first['trained'][0]['activated'])
        self.assertEqual(audits[0][2], 'models.drift_retrain')
        self.assertEqual(second['status'], 'idle')

    def test_no_drift_no_training(self):
        with db.connect() as c, patch('prediction_engine.train_model') as train:
            self.assertEqual(learning_loop.drift_retraining(c, now=self.now)['status'], 'idle')
        train.assert_not_called()

    def test_learning_status_read_model(self):
        with db.connect() as c:
            c.execute(
                """INSERT INTO sports_ai_model_evaluations
                   (id,model_id,sport,market,sample_size,brier_score,log_loss,calibration_error,hit_rate,coverage,completeness,metrics_json,created_at)
                   VALUES(?,?,'football','HOME',40,0.21,0.6,0.05,0.5,1,NULL,'{}',?)""",
                (str(uuid.uuid4()), self.football_model, self.now.isoformat()),
            )
            status = learning_loop.learning_status(c, self.now)
        self.assertEqual(status['evaluations'][0]['brier_score'], 0.21)
        self.assertTrue(status['models'])
        self.assertFalse(status['places_wager'])


if __name__ == '__main__':
    unittest.main()
