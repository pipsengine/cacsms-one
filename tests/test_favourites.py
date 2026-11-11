import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

import db
import favourites as fv

NOW = datetime(2026, 11, 11, 9, 0, tzinfo=timezone.utc)


def model(c, sport='football'):
    c.execute("""INSERT INTO sports_ai_model_registry(id,training_run_id,sport,model_name,version,status,active,parameters_json,
                 metrics_json,calibration_status,lifecycle) VALUES(?,?,?,?,?,'active',1,'{}',?,'validated','active')""",
              (f'm-{sport}', 'run', sport, 'm', '1', json.dumps({'calibration_error': 0.05})))


def leg(c, fid, market='HOME', odds=1.20, probability=0.85, reason='EDGE_BELOW_MARKET', sport='football',
        league=None, kickoff=NOW + timedelta(hours=4), observed=NOW - timedelta(minutes=3), settlement=None):
    league = league or f'League {fid}'
    c.execute("""INSERT OR IGNORE INTO sports_ai_fixtures(id,external_id,source,sport,competition,home,away,starts_at,status,
                 statistics_json,odds_json,observed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
              (fid, fid, 'test', sport, league, f'{fid} Home', f'{fid} Away', kickoff.isoformat(), 'scheduled', '{}', '{}', NOW.isoformat()))
    pid, qid = f'p-{fid}-{market}', f'q-{fid}-{market}'
    c.execute("""INSERT INTO sports_ai_engine_predictions(id,run_id,fixture_id,model_id,feature_id,market,outcome,probability,
                 settlement_json,explanation_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
              (pid, 'run', fid, f'm-{sport}', 'feat', market, market, probability,
               json.dumps(settlement or {'p_win': probability, 'p_loss': 1 - probability}), '{}', NOW.isoformat()))
    c.execute("""INSERT INTO sports_ai_odds_snapshots(id,fixture_id,provider_id,bookmaker,market,outcome,line,decimal_odds,
                 source_url,observed_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
              (qid, fid, 'prov', 'SportyBet', market, market, None, odds, 'https://example.test', observed.isoformat()))
    c.execute("""INSERT INTO sports_ai_value_opportunities(id,prediction_id,odds_id,implied_probability,expected_edge,status,
                 reason,evaluated_at) VALUES(?,?,?,?,?,?,?,?)""",
              (f'o-{fid}-{market}', pid, qid, 1 / odds, -0.05, 'rejected', 'test', NOW.isoformat()))
    c.execute("""INSERT INTO sports_ai_qualification_decisions(id,prediction_id,fixture_id,sport,competition,market,state,
                 reason_code,reason,evidence_json,products_json,rank_score,policy_version,decision_version,fingerprint,
                 starts_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (f'd-{fid}-{market}', pid, fid, sport, league, market, 'REJECTED', reason, 'x', '{}', '[]', None, 1, 1, 'fp',
               kickoff.isoformat(), NOW.isoformat(), NOW.isoformat()))


class FavouritesTests(unittest.TestCase):
    def setUp(self):
        self.previous = db.DB
        self.temp = tempfile.TemporaryDirectory()
        db.DB = Path(self.temp.name) / 'sports.sqlite3'
        db.initialize()

    def tearDown(self):
        db.DB = self.previous
        self.temp.cleanup()

    def conn(self):
        return db.connect()

    def test_builds_labelled_no_edge_ticket_once(self):
        with self.conn() as c:
            c.execute('PRAGMA foreign_keys=OFF')
            model(c)
            for i in range(4):
                leg(c, f'f{i}', odds=1.20 + i * 0.02)
            out = fv.build_favourite_tickets(c, now=NOW)
            self.assertEqual(out['created'], 1)
            ticket = c.execute('SELECT * FROM sports_ai_accumulator_tickets').fetchone()
            params = json.loads(ticket['parameters_json'])
            self.assertEqual((ticket['status'], params['product'], params['positive_edge']), ('qualified', 'favourites', False))
            self.assertIn('NO positive edge', ticket['explanation'])
            self.assertEqual(ticket['probability_basis'], 'independent')
            self.assertTrue(3 <= ticket['leg_count'] <= 4)
            self.assertTrue(fv.TICKET_ODDS[0] <= ticket['combined_odds'] <= fv.TICKET_ODDS[1])
            # Its matches are now allocated: the next run cannot reuse them.
            self.assertEqual(fv.build_favourite_tickets(c, now=NOW)['created'], 0)

    def test_safety_blockers_and_unsuitable_legs_are_excluded(self):
        with self.conn() as c:
            c.execute('PRAGMA foreign_keys=OFF')
            model(c)
            model(c, 'basketball')
            leg(c, 'ok1')
            leg(c, 'quarantined', reason='BASKETBALL_QUARANTINE')
            leg(c, 'quality', reason='QUALITY_HOLD')
            leg(c, 'stale', observed=NOW - timedelta(hours=2))
            leg(c, 'dnb', market='DNB_HOME')
            leg(c, 'push', settlement={'p_win': 0.8, 'p_push': 0.1, 'p_loss': 0.1})
            leg(c, 'hoops', market='MONEYLINE_HOME', sport='basketball')
            leg(c, 'weak', probability=0.55, odds=1.60)
            leg(c, 'soon', kickoff=NOW + timedelta(minutes=5))
            self.assertEqual([l['fixture_id'] for l in fv.candidates(c, NOW)], ['ok1'])

    def test_booking_gate_exception_applies_to_favourites_only(self):
        import sportybet_booking as sb
        with self.conn() as c:
            c.execute('PRAGMA foreign_keys=OFF')
            model(c)
            for i in range(4):
                leg(c, f'f{i}', odds=1.20 + i * 0.02)
            ticket_id = fv.build_favourite_tickets(c, now=NOW)['ticket_ids'][0]
            selections = sb.ticket_selections(c, ticket_id)
            original = sb._now
            sb._now = lambda: NOW
            try:
                job = sb.create_job(c, selections)
                self.assertEqual(job['status'], 'QUEUED')
                # The same legs outside the favourites product still face the positive-EV gate.
                c.execute('DELETE FROM sports_ai_booking_jobs')
                plain = [{k: v for k, v in s.items() if k != 'ticket_id'} for s in selections]
                with self.assertRaises(ValueError):
                    sb.create_job(c, plain)
            finally:
                sb._now = original
            with self.assertRaisesRegex(ValueError, 'started'):
                fv.require_bookable(c, selections, NOW + timedelta(hours=5))
            with self.assertRaisesRegex(ValueError, 'share a fixture'):
                fv.require_bookable(c, selections + selections[:1], NOW)


if __name__ == '__main__':
    unittest.main()
