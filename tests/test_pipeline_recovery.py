"""Regressions from 2026-11-10: no ticket or booking code was produced after a model switch."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import db
import high_odds_service
import sportybet_booking

NOW = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)


class EvidenceIsolationTests(unittest.TestCase):
    def test_tests_never_write_production_evidence(self):
        production = Path(sportybet_booking.__file__).resolve().parent / 'booking_evidence'
        self.assertNotEqual(Path(sportybet_booking.EVIDENCE).resolve(), production.resolve())


class QualificationCoverageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = db.DB
        db.DB = Path(self.temp.name) / 'sports.sqlite3'
        db.initialize()

    def tearDown(self):
        db.DB = self.previous
        self.temp.cleanup()

    def _model(self, c, model_id, active):
        c.execute(
            """INSERT INTO sports_ai_model_registry
               (id,training_run_id,sport,model_name,version,status,active,parameters_json,metrics_json,lifecycle)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (model_id, 'run', 'football', 'ens', model_id, 'active' if active else 'inactive', int(active), '{}', '{}',
             'active' if active else 'suspended'),
        )

    def _prediction(self, c, fixture, model_id, kickoff, created):
        c.execute(
            """INSERT INTO sports_ai_fixtures
               (id,external_id,source,sport,competition,home,away,starts_at,status,statistics_json,odds_json,observed_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (fixture, fixture, 'test', 'football', 'L', fixture + 'H', fixture + 'A', kickoff.isoformat(), 'scheduled', '{}', '{}', NOW.isoformat()),
        )
        c.execute(
            """INSERT INTO sports_ai_engine_predictions
               (id,run_id,fixture_id,model_id,feature_id,market,outcome,probability,settlement_json,explanation_json,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            ('p-' + fixture, 'r', fixture, model_id, 'feat', 'HOME', 'HOME', 0.6, '{}', '{}', created.isoformat()),
        )

    def test_active_model_predictions_rank_ahead_of_earlier_stale_ones(self):
        from qualification_engine import candidate_rows
        with db.connect() as c:
            c.execute('PRAGMA foreign_keys=OFF')
            c.execute("INSERT OR IGNORE INTO sports_ai_sports(code,name,prediction_status) VALUES('football','Football','modelled')")
            c.execute("UPDATE sports_ai_sports SET prediction_status='modelled' WHERE code='football'")
            self._model(c, 'old', False)
            self._model(c, 'new', True)
            # Three early kickoffs still carry the old model; a Saturday fixture has the active model.
            for i in range(3):
                self._prediction(c, f'early{i}', 'old', NOW + timedelta(hours=2 + i), NOW - timedelta(days=1))
            self._prediction(c, 'saturday', 'new', NOW + timedelta(days=4), NOW)
            rows = candidate_rows(c, NOW, limit=1)
        self.assertEqual([r['fixture_id'] for r in rows], ['saturday'])


class MultiDaySpreadTests(unittest.TestCase):
    def _leg(self, i, day, probability=0.75, odds=1.7):
        start = datetime(2026, 11, 10 + day, 15, 0, tzinfo=timezone.utc).isoformat()
        return {'fixture_id': f'f{i}', 'market': 'HOME', 'sport': 'football', 'competition': f'C{i}',
                'home': f'H{i}', 'away': f'A{i}', 'probability': probability, 'decimal_odds': odds,
                'edge': probability * odds - 1, 'calibration_error': 0.02, 'reliability': 0.9,
                'starts_at': start, 'settlement_kind': 'binary', 'team_keys': [('football', f'h{i}'), ('football', f'a{i}')]}

    def test_fifty_plus_tickets_record_days_and_may_span_the_week(self):
        settings = {**high_odds_service.DEFAULTS, 'max_legs': 10, 'search_limit': 20000, 'timeout_seconds': 5}
        # Identical legs on Tuesday..Sunday; the only difference between equal tickets is day spread.
        pool = [self._leg(i, i % 6) for i in range(12)]
        category = {'id': '50-99.99', 'min_odds': 50.0, 'max_odds': 99.99}
        results, _ = high_odds_service.optimize(pool, category, settings)
        self.assertTrue(results)
        best = results[0]
        self.assertTrue(50 <= best['combined_odds'] <= 99.99)
        self.assertEqual(best['kickoff_days'], sorted(best['kickoff_days']))
        self.assertGreater(len(best['kickoff_days']), 1)
        top = round(best['score'], 4)
        spreads = [len(r['kickoff_days']) for r in results if round(r['score'], 4) == top]
        self.assertEqual(len(best['kickoff_days']), max(spreads))


if __name__ == '__main__':
    unittest.main()
