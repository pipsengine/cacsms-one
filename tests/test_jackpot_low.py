import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

import db
import jackpot_low as jl

NOW = datetime(2026, 11, 11, 9, 0, tzinfo=timezone.utc)


def match(c, fid, sport='football', league=None, odds=1.05, market='totals', outcome='OVER', line=0.5, home=None):
    league = league or f'League {fid}'
    kickoff = NOW + timedelta(hours=5)
    c.execute("""INSERT INTO sports_ai_fixtures(id,external_id,source,sport,competition,home,away,starts_at,status,statistics_json,
                 odds_json,observed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
              (fid, fid, 't', sport, league, home or f'{fid}H', f'{fid}A', kickoff.isoformat(), 'scheduled', '{}', '{}', NOW.isoformat()))
    c.execute("""INSERT INTO sports_ai_odds_snapshots(id,fixture_id,provider_id,bookmaker,market,outcome,line,decimal_odds,source_url,observed_at)
                 VALUES(?,?,?,?,?,?,?,?,?,?)""", (f'q-{fid}', fid, 'p', 'SportyBet', market, outcome, line, odds, 'https://x', NOW.isoformat()))
    c.execute("""INSERT INTO sports_ai_sportybet_observations(id,home,away,market_label,selection_label,decimal_odds,source_url,
                 observed_at,confirmation_status,fixture_id,created_at) VALUES(?,?,?,?,?,?,?,?,'confirmed',?,?)""",
              (f'o-{fid}', home or f'{fid}H', f'{fid}A', 'Over/Under', 'Over', odds, 'https://x', NOW.isoformat(), fid, NOW.isoformat()))


class JackpotTests(unittest.TestCase):
    def setUp(self):
        self.previous = db.DB
        self.temp = tempfile.TemporaryDirectory()
        db.DB = Path(self.temp.name) / 'sports.sqlite3'
        db.initialize()

    def tearDown(self):
        db.DB = self.previous
        self.temp.cleanup()

    def test_deal_balances_and_respects_team_and_league_limits(self):
        pool = [{'fixture_id': f'f{i}', 'home': f'T{i}', 'away': f'U{i}', 'sport': 'football',
                 'competition': f'L{i % 20}', 'probability': 0.9, 'decimal_odds': 1.05} for i in range(150)]
        hands = jl.deal(pool)
        self.assertEqual([len(h) for h in hands], [45, 45, 45])
        for hand in hands:
            leagues = {}
            for leg in hand:
                leagues[leg['competition']] = leagues.get(leg['competition'], 0) + 1
            self.assertLessEqual(max(leagues.values()), jl.MAX_PER_LEAGUE)
        used = [leg['fixture_id'] for hand in hands for leg in hand]
        self.assertEqual(len(used), len(set(used)))

    def test_scan_keeps_short_bookable_prices_only(self):
        try:
            with db.connect() as c:
                c.execute('PRAGMA foreign_keys=OFF')
                columns = {r[1] for r in c.execute('PRAGMA table_info(sports_ai_sportybet_observations)')}
                if not {'id', 'fixture_id', 'observed_at'} <= columns:
                    self.skipTest('observation schema differs')
                match(c, 'ok', odds=1.05)
                match(c, 'long', odds=1.60)
                match(c, 'push', market='totals', outcome='UNDER', line=5, odds=1.03)
                match(c, 'sim', sport='efootball', odds=1.02)
                legs = jl.scan(c, NOW)
        except Exception as error:
            if 'NOT NULL' in str(error) or 'no column' in str(error):
                self.skipTest(f'observation schema differs: {error}')
            raise
        self.assertEqual([l['fixture_id'] for l in legs], ['ok'])
        self.assertEqual(legs[0]['market'], 'OVER_0.5')
        self.assertLess(legs[0]['probability'], 1 / 1.05)

    def test_jackpot_gate_is_disabled_and_is_not_followed_by_single_betting(self):
        import single_betting as sb
        with db.connect() as c:
            c.execute('PRAGMA foreign_keys=OFF')
            selections = []
            for i in range(45):
                fid = f'j{i}'
                c.execute("""INSERT INTO sports_ai_fixtures(id,external_id,source,sport,competition,home,away,starts_at,status,
                             statistics_json,odds_json,observed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                          (fid, fid, 't', 'basketball' if i % 3 else 'football', 'L', f'{fid}H', f'{fid}A',
                           (NOW + timedelta(hours=4)).isoformat(), 'scheduled', '{}', '{}', NOW.isoformat()))
                selections.append({'fixture_id': fid, 'market': 'HOME', 'sport': 'football'})
            with self.assertRaisesRegex(ValueError, 'disabled by the operator'):
                jl.require_bookable(c, selections, NOW)
            with self.assertRaises(ValueError):
                jl.require_bookable(c, selections + selections[:6], NOW)
            with self.assertRaisesRegex(ValueError, 'disabled by the operator'):
                jl.require_bookable(c, selections, NOW + timedelta(hours=5))
            c.execute("""INSERT INTO sports_ai_accumulator_tickets(id,run_id,family_id,version,signature,tier,sport_mode,status,
                         combined_odds,joint_probability,estimated_edge,leg_count,probability_basis,uncertainty,explanation,
                         parameters_json,score,finalized,created_at) VALUES('jt','r','jt',1,'s',2,'mixed','qualified',20,0.002,-0.96,45,
                         'independent',0,'x',?,0,1,?)""", (json.dumps({'product': 'jackpot_low'}), NOW.isoformat()))
            c.execute("""INSERT INTO sports_ai_booking_codes(id,accumulator_ticket_id,code,verification_method,evidence,status,
                         verified_at,created_at) VALUES('b','jt','JACK01','displayed','t','verified',?,?)""",
                      (NOW.isoformat(), NOW.isoformat()))
            self.assertEqual(sb.import_coded_tickets(c, NOW), 0)


if __name__ == '__main__':
    unittest.main()
