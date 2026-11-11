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


def ids(prefix, lane, n):
    """Fixture ids that favourites.lane_of sends to `lane` (for legs priced at 1.20 or more)."""
    out, i = [], 0
    while len(out) < n:
        fid = f'{prefix}{i}'
        if fv.lane_of({'fixture_id': fid, 'decimal_odds': 1.25}) == lane:
            out.append(fid)
        i += 1
    return out


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
                leg(c, f'f{i}', odds=1.16 + i * 0.01)
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
                leg(c, f'f{i}', odds=1.16 + i * 0.01)
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


class FavouriteSinglesTests(unittest.TestCase):
    """Single betting falls back to favourites as PAPER ONLY bets, in every execution mode."""

    def setUp(self):
        self.previous = db.DB
        self.temp = tempfile.TemporaryDirectory()
        db.DB = Path(self.temp.name) / 'sports.sqlite3'
        db.initialize()

    def tearDown(self):
        db.DB = self.previous
        self.temp.cleanup()

    def _live(self, c):
        import single_betting as sb
        state = sb.runtime(c)
        state['execution_mode'] = 'LIVE'
        sb._save_setting(c, sb.RUNTIME_KEY, state, NOW)

    def test_favourites_follow_the_execution_mode(self):
        import single_betting as sb
        with db.connect() as c:
            c.execute('PRAGMA foreign_keys=OFF')
            model(c)
            fav1 = ids('fav', 'single', 1)[0]
            leg(c, fav1, odds=1.25, probability=0.86)
            leg(c, 'cheap', odds=1.16, probability=0.90)   # below the 1.20 single minimum
            self._live(c)
            self.assertEqual(sb.run_cycle(c, NOW)['queued'], 1)
            row = c.execute('SELECT * FROM sports_ai_single_bets').fetchone()
            self.assertEqual((row['kind'], row['fixture_id']), ('FAVOURITE', fav1))
            # In a live mode the engine does not paper-place it: the account lane takes it.
            sb.run_cycle(c, NOW + timedelta(seconds=30))
            self.assertEqual(c.execute('SELECT status FROM sports_ai_single_bets').fetchone()[0], 'QUEUED')
            head = sb.next_for_execution(c, NOW + timedelta(seconds=30))
            self.assertEqual((head['kind'], head['fixture_id']), ('FAVOURITE', fav1))
            sb.apply_execution(c, head, 'LIVE_PLACED', 'PLACED', 'accepted', 'SUBMISSION', NOW + timedelta(seconds=40))
            c.execute("INSERT INTO sports_ai_results(id,fixture_id,home_score,away_score,status,source,settled_at) VALUES('r',?,2,0,'final','t',?)",
                      (fav1, NOW.isoformat()))
            sb.run_cycle(c, NOW + timedelta(hours=6))
            settled = c.execute('SELECT status, profit FROM sports_ai_single_bets').fetchone()
            self.assertEqual(settled['status'], 'WON')
            self.assertAlmostEqual(settled['profit'], 25.0)

    def test_real_money_cap_is_ten_a_day_including_combined(self):
        import sportybet_account as acct
        self.assertEqual((acct.DAILY_COUNT, acct.DAILY_STAKE), (10, 1000.0))
        self.assertIn('TICKET', acct.NO_EDGE_KINDS)
        legs = [{'home': 'A', 'away': 'B'}, {'home': 'C', 'away': 'D'}]
        self.assertEqual(len(acct._bet_legs({'kind': 'TICKET', 'legs': legs})), 2)

    def test_favourites_capped_and_rechecked(self):
        import single_betting as sb
        with db.connect() as c:
            c.execute('PRAGMA foreign_keys=OFF')
            model(c)
            singles = ids('f', 'single', sb.FAVOURITE_DAILY_LIMIT + 3)
            for fid in singles:
                leg(c, fid, odds=1.25, probability=0.86)
            self.assertEqual(sb.run_cycle(c, NOW)['queued'], sb.FAVOURITE_DAILY_LIMIT)
            # A favourite whose price went stale is cancelled before placement.
            first = c.execute("SELECT fixture_id FROM sports_ai_single_bets ORDER BY created_at, id LIMIT 1").fetchone()[0]
            c.execute("UPDATE sports_ai_odds_snapshots SET observed_at=? WHERE fixture_id=?",
                      ((NOW - timedelta(hours=2)).isoformat(), first))
            out = sb.run_cycle(c, NOW + timedelta(seconds=30))
            self.assertGreaterEqual(out['cancelled'], 1)
            self.assertEqual(c.execute("SELECT status FROM sports_ai_single_bets WHERE fixture_id=?", (first,)).fetchone()[0], 'CANCELLED')


class SplitTests(unittest.TestCase):
    def setUp(self):
        self.previous = db.DB
        self.temp = tempfile.TemporaryDirectory()
        db.DB = Path(self.temp.name) / 'sports.sqlite3'
        db.initialize()

    def tearDown(self):
        db.DB = self.previous
        self.temp.cleanup()

    def test_each_batch_is_split_and_never_shared(self):
        import single_betting as sb
        with db.connect() as c:
            c.execute('PRAGMA foreign_keys=OFF')
            model(c)
            single_ids, ticket_ids = ids('s', 'single', 3), ids('t', 'ticket', 4)
            for fid in single_ids + ticket_ids:
                leg(c, fid, odds=1.21, probability=0.88)
            self.assertEqual(fv.lane_of({'fixture_id': single_ids[0], 'decimal_odds': 1.10}), 'ticket')  # too short for a single
            built = fv.build_favourite_tickets(c, now=NOW)
            self.assertEqual(built['created'], 1)
            used = {r[0] for r in c.execute('SELECT fixture_id FROM sports_ai_accumulator_legs')}
            self.assertTrue(used <= set(ticket_ids))
            singles = {i['fixture_id'] for i in sb.favourite_candidates(c, NOW)}
            self.assertEqual(singles, set(single_ids))
            sb.run_cycle(c, NOW)
            backed = {r[0] for r in c.execute("SELECT fixture_id FROM sports_ai_single_bets WHERE kind='FAVOURITE'")}
            self.assertFalse(backed & used)
            # Matches already backed as singles are never put on a later ticket.
            self.assertFalse(backed & {l['fixture_id'] for l in fv.candidates(c, NOW) if l['fixture_id'] in
                                       {r[0] for r in c.execute('SELECT fixture_id FROM sports_ai_accumulator_legs')}})


class TicketFollowTests(unittest.TestCase):
    """Booked accumulator tickets are followed in single betting as ₦100 paper bets."""

    def setUp(self):
        self.previous = db.DB
        self.temp = tempfile.TemporaryDirectory()
        db.DB = Path(self.temp.name) / 'sports.sqlite3'
        db.initialize()

    def tearDown(self):
        db.DB = self.previous
        self.temp.cleanup()

    def _coded_ticket(self, c, code, coded_at):
        model(c)
        for i in range(4):
            leg(c, f'{code}{i}', odds=1.16 + i * 0.01)
        ticket_id = fv.build_favourite_tickets(c, now=NOW)['ticket_ids'][0]
        c.execute("""INSERT INTO sports_ai_booking_codes(id,accumulator_ticket_id,code,verification_method,evidence,status,
                     verified_at,created_at) VALUES(?,?,?,'displayed','test','verified',?,?)""",
                  (f'bc-{code}', ticket_id, code, coded_at.isoformat(), coded_at.isoformat()))
        return ticket_id

    def test_coded_ticket_is_followed_as_paper_bet_and_settled(self):
        import single_betting as sb
        with db.connect() as c:
            c.execute('PRAGMA foreign_keys=OFF')
            ticket_id = self._coded_ticket(c, 'AB12CD', NOW + timedelta(minutes=1))
            out = sb.run_cycle(c, NOW + timedelta(minutes=2))
            self.assertEqual(out['tickets_imported'], 1)
            bet = c.execute("SELECT * FROM sports_ai_single_bets WHERE kind='TICKET'").fetchone()
            self.assertEqual((bet['id'], bet['status'], bet['mode'], bet['stake']), (f'ticket:{ticket_id}', 'PLACED', 'PAPER', 100.0))
            self.assertEqual(bet['placed_at'], (NOW + timedelta(minutes=1)).isoformat())
            public = sb._bet_public(c, bet)
            self.assertEqual(public['booking_code'], 'AB12CD')
            legs = public['legs']
            # Imported once only; its matches are not offered as favourite singles.
            self.assertEqual(sb.run_cycle(c, NOW + timedelta(minutes=3))['tickets_imported'], 0)
            taken = {l['fixture_id'] for l in legs}
            self.assertFalse(taken & {i['fixture_id'] for i in sb.favourite_candidates(c, NOW + timedelta(minutes=3))})
            for l in legs:  # every leg is a HOME pick: a home win settles it
                c.execute("INSERT INTO sports_ai_results(id,fixture_id,home_score,away_score,status,source,settled_at) VALUES(?,?,2,0,'final','t',?)",
                          (f"r-{l['fixture_id']}", l['fixture_id'], NOW.isoformat()))
            sb.run_cycle(c, NOW + timedelta(hours=6))
            settled = c.execute("SELECT status, payout FROM sports_ai_single_bets WHERE kind='TICKET'").fetchone()
            self.assertEqual(settled['status'], 'WON')
            self.assertAlmostEqual(settled['payout'], round(100 * bet['decimal_odds'], 2), places=1)

    def test_live_mode_queues_tickets_for_the_account(self):
        import single_betting as sb
        with db.connect() as c:
            c.execute('PRAGMA foreign_keys=OFF')
            self._coded_ticket(c, 'LIVE01', NOW + timedelta(minutes=1))
            state = sb.runtime(c)
            state['execution_mode'] = 'LIVE_REHEARSAL'
            sb._save_setting(c, sb.RUNTIME_KEY, state, NOW)
            self.assertEqual(sb.import_coded_tickets(c, NOW + timedelta(minutes=2)), 1)
            bet = c.execute("SELECT * FROM sports_ai_single_bets WHERE kind='TICKET'").fetchone()
            self.assertEqual((bet['status'], bet['placed_at']), ('QUEUED', None))
            head = sb.next_for_execution(c, NOW + timedelta(minutes=3))
            self.assertEqual((head['kind'], len(head['legs'])), ('TICKET', bet['decimal_odds'] and len(head['legs'])))
            self.assertGreaterEqual(len(head['legs']), 3)

    def test_high_odds_and_untagged_tickets_are_never_followed(self):
        import single_betting as sb
        with db.connect() as c:
            c.execute('PRAGMA foreign_keys=OFF')
            self._coded_ticket(c, 'JACK01', NOW + timedelta(minutes=1))
            c.execute("UPDATE sports_ai_accumulator_tickets SET parameters_json=json_set(parameters_json,'$.product','high_odds')")
            self.assertEqual(sb.import_coded_tickets(c, NOW + timedelta(minutes=2)), 0)
            c.execute("UPDATE sports_ai_accumulator_tickets SET parameters_json=json_remove(parameters_json,'$.product')")
            self.assertEqual(sb.import_coded_tickets(c, NOW + timedelta(minutes=2)), 0)

    def test_code_shown_after_kickoff_is_never_followed(self):
        import single_betting as sb
        with db.connect() as c:
            c.execute('PRAGMA foreign_keys=OFF')
            self._coded_ticket(c, 'LATE01', NOW + timedelta(hours=5))   # legs kick off at NOW+4h
            self.assertEqual(sb.import_coded_tickets(c, NOW + timedelta(hours=6)), 0)


if __name__ == '__main__':
    unittest.main()
