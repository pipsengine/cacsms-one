"""Prediction integrity, conservative EV, capability levels, CLV and the booking gate."""
import json
import random
import sqlite3
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

import db
import prediction_integrity as pi

NOW = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)


def pairs(n, sport='football', market='HOME', competition='League A', model_edge=0.08, seed=1, odds=2.0):
    """Synthetic settled forward predictions. The true rate is the model probability.

    The market is the truth shrunk towards 50% by ``model_edge * 5`` (0 = the market knows as much as the model).
    """
    rng = random.Random(seed)
    shrink = min(1.0, model_edge * 5)
    out = []
    for i in range(n):
        p = 0.25 + 0.5 * rng.random()
        market_p = (1 - shrink) * p + shrink * 0.5
        out.append({'sport': sport, 'competition': competition, 'market': market, 'p': p,
                    'odds': 1 / (market_p * 1.06), 'y': 1 if rng.random() < p else 0,
                    'kickoff': (NOW - timedelta(days=n - i)).isoformat()})
    return out


class MathTests(unittest.TestCase):
    def test_fair_probabilities_remove_the_margin(self):
        fair, overround = pi.fair_from_quotes({'home': 1.9, 'draw': 3.4, 'away': 4.2})
        self.assertAlmostEqual(sum(fair.values()), 1.0)
        self.assertGreater(overround, 1.0)
        self.assertEqual(pi.fair_from_quotes({'home': 1.9}), (None, None))

    def test_settlement_distribution_ev_handles_push_and_halves(self):
        self.assertAlmostEqual(pi.settlement_ev({'won': 0.5, 'push': 0.2, 'lost': 0.3}, 2.0), 0.5 - 0.3)
        self.assertAlmostEqual(pi.settlement_ev({'half_win': 1.0}, 3.0), 1.0)
        self.assertAlmostEqual(pi.settlement_ev({'half_loss': 1.0}, 3.0), -0.5)
        self.assertIsNone(pi.settlement_ev({}, 2.0))

    def test_positive_point_ev_alone_is_not_conservatively_positive(self):
        margin = pi.uncertainty_margin(0.30, calibration_error=0.10, observations=180)
        result = pi.conservative(0.30, 4.0, None, margin)
        self.assertGreater(pi.point_ev(0.30, 4.0), 0)
        self.assertLess(result['conservative_ev'], 0)

    def test_no_evidence_means_the_whole_probability_is_uncertain(self):
        self.assertEqual(pi.uncertainty_margin(0.6, None, 0), 0.6)

    def test_transaction_cost_reduces_conservative_ev(self):
        a = pi.conservative(0.6, 2.0, None, 0.01)['conservative_ev']
        b = pi.conservative(0.6, 2.0, None, 0.01, cost=0.02)['conservative_ev']
        self.assertAlmostEqual(a - b, 0.02)

    def test_drawdown_and_ci(self):
        self.assertEqual(pi.max_drawdown([1, -1, -1, 2, -3]), -3)
        stats = pi.mean_ci([0.1, 0.2, 0.3])
        self.assertAlmostEqual(stats['mean'], 0.2)
        self.assertLess(stats['ci_low'], 0.2)


class DbTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = db.DB
        db.DB = Path(self.temp.name) / 'integrity.sqlite3'
        db.initialize()
        with db.connect() as c:
            __import__('market_anchor').set_model_only(c)
            self.provider = c.execute("SELECT id FROM sports_ai_provider_registry WHERE provider_key='sportybet'").fetchone()
            if self.provider is None:
                c.execute("""INSERT INTO sports_ai_provider_registry(id,provider_key,display_name,sport,adapter)
                             VALUES('prov','sportybet','SportyBet','both','browser')""")
                self.provider = 'prov'
            else:
                self.provider = self.provider[0]

    def tearDown(self):
        db.DB = self.previous
        self.temp.cleanup()

    # ------------------------------------------------------------------ builders

    def fixture(self, c, home='Alpha', away='Beta', kickoff=None, competition='League A', key=''):
        c.execute('PRAGMA foreign_keys=OFF')  # model, run and feature rows are minimal test stand-ins
        fid = str(uuid.uuid4())
        kickoff = kickoff or (NOW + timedelta(hours=5)).isoformat()
        c.execute(
            """INSERT INTO sports_ai_fixtures(id,external_id,source,sport,competition,home,away,starts_at,status,statistics_json,
                 odds_json,observed_at,canonical_key) VALUES(?,?,?,?,?,?,?,?,'scheduled','{}','{}',?,?)""",
            (fid, fid, 'test', 'football', competition, home, away, kickoff, NOW.isoformat(), key),
        )
        return fid

    def model(self, c):
        existing = c.execute("SELECT id FROM sports_ai_model_registry WHERE sport='football' AND version='v-test'").fetchone()
        if existing:
            return existing[0]
        mid = str(uuid.uuid4())
        c.execute(
            """INSERT INTO sports_ai_model_registry(id,training_run_id,sport,model_name,version,status,active,parameters_json,
                 metrics_json,lifecycle,calibration_status) VALUES(?,?,?,?,?,'active',1,'{}',?,'active','validated')""",
            (mid, 'run', 'football', 'poisson', 'v-test', json.dumps({'calibration_error': 0.02, 'holdout_fixtures': 400})),
        )
        return mid

    def quote(self, c, fid, outcome, odds, observed=None, line=None, market='h2h', bookmaker='SportyBet', period='unknown'):
        qid = str(uuid.uuid4())
        c.execute(
            """INSERT INTO sports_ai_odds_snapshots(id,fixture_id,provider_id,bookmaker,market,outcome,line,decimal_odds,source_url,
                 observed_at,settlement_period,confirmation_status) VALUES(?,?,?,?,?,?,?,?,?,?,?,'confirmed')""",
            (qid, fid, self.provider, bookmaker, market, outcome, line, odds, 'https://www.sportybet.com/x',
             (observed or NOW - timedelta(minutes=5)).isoformat(), period),
        )
        return qid

    def priced_prediction(self, c, fid, mid, probability=0.62, odds=2.0, market='HOME'):
        c.execute('PRAGMA foreign_keys=OFF')
        feature = str(uuid.uuid4())
        c.execute(
            """INSERT INTO sports_ai_feature_snapshots(id,fixture_id,sport,cutoff_at,features_json,source_refs_json,generated_at,completeness)
               VALUES(?,?,'football',?,'{}','[]',?,0.9)""",
            (feature, fid, c.execute('SELECT starts_at FROM sports_ai_fixtures WHERE id=?', (fid,)).fetchone()[0],
             (NOW - timedelta(hours=2)).isoformat()),
        )
        pid = str(uuid.uuid4())
        c.execute(
            """INSERT INTO sports_ai_engine_predictions(id,run_id,fixture_id,model_id,feature_id,market,outcome,probability,
                 settlement_json,explanation_json,created_at) VALUES(?,?,?,?,?,?,?,?,'{}','{}',?)""",
            (pid, 'run', fid, mid, feature, market, market, probability, (NOW - timedelta(hours=1)).isoformat()),
        )
        home = self.quote(c, fid, 'home', odds)
        self.quote(c, fid, 'draw', 3.6)
        self.quote(c, fid, 'away', 4.5)
        c.execute(
            """INSERT INTO sports_ai_value_opportunities(id,prediction_id,odds_id,implied_probability,expected_edge,status,reason,
                 evaluated_at,data_completeness) VALUES(?,?,?,?,?,'qualified','test',?,0.9)""",
            (str(uuid.uuid4()), pid, home, 1 / odds, probability * odds - 1, NOW.isoformat()),
        )
        return pid

    def eligible(self, c, competition='League A', market='HOME'):
        evidence = json.dumps({'observations': 5000, 'calibration_error': 0.01})
        for comp in ('', competition):
            c.execute(
                """INSERT OR REPLACE INTO sports_ai_capability_levels(id,sport,competition,market,level,reason,evidence_json,changed_at,evaluated_at)
                   VALUES(?,?,?,?,'ELIGIBLE','test',?,?,?)""",
                (str(uuid.uuid4()), 'football', comp, market, evidence, NOW.isoformat(), NOW.isoformat()),
            )

    def revalidate(self, c, fid):
        from qualification_engine import revalidate
        c.execute("UPDATE sports_ai_sports SET prediction_status='modelled' WHERE code='football'")
        return revalidate(c, 'test', NOW, limit=0, fixture_ids=[fid])

    def decision(self, c, fid):
        row = c.execute('SELECT * FROM sports_ai_qualification_decisions WHERE fixture_id=?', (fid,)).fetchone()
        return dict(row), json.loads(row['evidence_json'])

    # ------------------------------------------------------------------ consensus

    def test_consensus_matches_only_same_line_and_settlement_with_complete_outcomes(self):
        with db.connect() as c:
            fid = self.fixture(c)
            for book, (h, d, a) in (('SportyBet', (2.0, 3.5, 4.0)), ('BookB', (2.1, 3.4, 3.9))):
                for outcome, odds in (('home', h), ('draw', d), ('away', a)):
                    self.quote(c, fid, outcome, odds, bookmaker=book)
            self.quote(c, fid, 'home', 1.5, bookmaker='BookC')  # incomplete outcome set
            for outcome, odds in (('home', 2.3), ('draw', 3.2), ('away', 3.5)):
                self.quote(c, fid, outcome, odds, bookmaker='BookD', period='including_overtime')
            self.quote(c, fid, 'home', 2.0, bookmaker='BookE', observed=NOW - timedelta(hours=3))  # stale
            target = {'fixture_id': fid, 'market': 'h2h', 'outcome': 'home', 'line': None, 'settlement_period': 'unknown'}
            result = pi.market_consensus(c, target, pi.DEFAULT_POLICY, NOW)
            self.assertEqual(result['independent_sources'], 1)
            self.assertFalse(result['single_source'])
            reasons = {r['bookmaker']: r['reason'] for r in result['rejected']}
            self.assertEqual(reasons['BookC'], 'incomplete outcome set')
            self.assertEqual(reasons['BookD'], 'different settlement period')
            self.assertNotIn('BookE', reasons)  # outside the freshness window entirely
            fair, _ = pi.fair_from_quotes({'home': 2.1, 'draw': 3.4, 'away': 3.9})
            self.assertAlmostEqual(result['consensus_probability'], fair['home'])
            self.assertIsNotNone(result['divergence'])

    def test_single_source_is_labelled_and_never_a_disagreement(self):
        with db.connect() as c:
            fid = self.fixture(c)
            for outcome, odds in (('home', 2.0), ('draw', 3.5), ('away', 4.0)):
                self.quote(c, fid, outcome, odds)
            result = pi.market_consensus(c, {'fixture_id': fid, 'market': 'h2h', 'outcome': 'home', 'line': None,
                                             'settlement_period': 'unknown'}, pi.DEFAULT_POLICY, NOW)
            self.assertTrue(result['single_source'])
            self.assertIsNone(result['consensus_probability'])
            self.assertIsNone(result['divergence'])
            self.assertIsNotNone(result['target_fair_probability'])

    # ------------------------------------------------------------------ capability levels

    def test_strong_model_is_eligible_only_with_clv_evidence(self):
        with db.connect() as c:
            data = pairs(1500, model_edge=0.12)
            c.execute("INSERT INTO sports_ai_settings(key,value_json,updated_at) VALUES(?,?,?)",
                      (pi.POLICY_KEY, json.dumps({'require_clv': False}), NOW.isoformat()))
            summary = pi.refresh_capabilities(c, 'test', NOW, pairs=data)
            levels = pi.load_capabilities(c)
            self.assertEqual(levels[('football', '', 'HOME')]['level'], 'ELIGIBLE', levels[('football', '', 'HOME')]['reason'])
            self.assertEqual(summary['eligible'], 2)
            c.execute('UPDATE sports_ai_settings SET value_json=? WHERE key=?', (json.dumps({'require_clv': True}), pi.POLICY_KEY))
            pi.refresh_capabilities(c, 'test', NOW, pairs=data)
            row = pi.load_capabilities(c)[('football', '', 'HOME')]
            self.assertEqual(row['level'], 'RESEARCH')
            self.assertIn('closing-line observations', row['reason'])
            self.assertTrue(c.execute("SELECT 1 FROM sports_ai_audit_events WHERE action='capability.level'").fetchone())

    def test_model_no_better_than_market_stays_research(self):
        with db.connect() as c:
            c.execute("INSERT INTO sports_ai_settings(key,value_json,updated_at) VALUES(?,?,?)",
                      (pi.POLICY_KEY, json.dumps({'require_clv': False}), NOW.isoformat()))
            pi.refresh_capabilities(c, 'test', NOW, pairs=pairs(800, model_edge=0.0))
            row = pi.load_capabilities(c)[('football', '', 'HOME')]
            self.assertEqual(row['level'], 'RESEARCH')
            self.assertIn('not significant', row['reason'])

    def test_multiple_testing_raises_the_bar_with_more_combinations(self):
        with db.connect() as c:
            c.execute("INSERT INTO sports_ai_settings(key,value_json,updated_at) VALUES(?,?,?)",
                      (pi.POLICY_KEY, json.dumps({'require_clv': False}), NOW.isoformat()))
            one = pi.refresh_capabilities(c, 'test', NOW, pairs=pairs(600))
            many = pi.refresh_capabilities(c, 'test', NOW, pairs=[p for k in range(20) for p in pairs(600, market=f'M{k}', seed=k)])
            self.assertGreater(many['z_required'], one['z_required'])
            self.assertEqual(many['combinations_tested'], 20)

    def test_longshot_overconfidence_blocks_promotion(self):
        with db.connect() as c:
            c.execute("INSERT INTO sports_ai_settings(key,value_json,updated_at) VALUES(?,?,?)",
                      (pi.POLICY_KEY, json.dumps({'require_clv': False}), NOW.isoformat()))
            rng = random.Random(3)
            data = pairs(1500, model_edge=0.12)
            for i in range(300):  # model says 30%, longshots win 10%
                data.append({'sport': 'football', 'competition': 'League A', 'market': 'HOME', 'p': 0.30, 'odds': 5.0,
                             'y': 1 if rng.random() < 0.10 else 0, 'kickoff': NOW.isoformat()})
            pi.refresh_capabilities(c, 'test', NOW, pairs=data)
            row = pi.load_capabilities(c)[('football', '', 'HOME')]
            self.assertEqual(row['level'], 'RESEARCH')
            self.assertIn('longshot overconfidence', row['reason'])

    def test_league_without_history_does_not_inherit_eligibility(self):
        caps = {('football', '', 'HOME'): {'level': 'ELIGIBLE', 'reason': 'ok', 'evidence_json': '{}'}}
        self.assertEqual(pi.capability_for(caps, 'football', 'New League', 'HOME')['level'], 'RESEARCH')
        self.assertEqual(pi.capability_for({}, 'kabaddi', '', 'WIN', modelled=False)['level'], 'DISCOVERY')

    # ------------------------------------------------------------------ records and integrity

    def test_probability_records_are_immutable(self):
        with db.connect() as c:
            fid = self.fixture(c)
            mid = self.model(c)
            self.priced_prediction(c, fid, mid)
            self.revalidate(c, fid)
            record = c.execute('SELECT id FROM sports_ai_probability_records').fetchone()[0]
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute('UPDATE sports_ai_probability_records SET blended_probability=0.9 WHERE id=?', (record,))

    def test_integrity_issues_quarantine_the_selection(self):
        with db.connect() as c:
            fid = self.fixture(c, kickoff=(NOW + timedelta(hours=5)).strftime('%Y-%m-%dT%H:%M:%S'))
            mid = self.model(c)
            self.priced_prediction(c, fid, mid)
            self.eligible(c)
            self.revalidate(c, fid)
            decision, evidence = self.decision(c, fid)
            self.assertEqual(decision['state'], 'SUSPENDED')
            self.assertEqual(decision['reason_code'], 'INTEGRITY_QUARANTINE')
            self.assertIn('KICKOFF_TIMEZONE_MISSING', {i['code'] for i in evidence['integrity_issues']})
            record = c.execute('SELECT integrity_status FROM sports_ai_probability_records').fetchone()[0]
            self.assertEqual(record, 'quarantined')

    def test_rebuilt_feature_snapshot_and_late_odds_are_detected(self):
        with db.connect() as c:
            fid = self.fixture(c)
            mid = self.model(c)
            pid = self.priced_prediction(c, fid, mid)
            c.execute('UPDATE sports_ai_feature_snapshots SET generated_at=?', ((NOW - timedelta(minutes=5)).isoformat(),))
            row = {'prediction_id': pid, 'fixture_id': fid, 'sport': 'football', 'market': 'HOME',
                   'starts_at': (NOW + timedelta(hours=5)).isoformat()}
            late = {'market': 'h2h', 'outcome': 'home', 'line': None, 'observed_at': (NOW + timedelta(hours=6)).isoformat(),
                    'settlement_period': 'unknown'}
            codes = {i['code'] for i in pi.integrity_issues(c, row, late, NOW)}
            self.assertIn('FEATURE_SNAPSHOT_CHANGED', codes)
            self.assertIn('ODDS_AFTER_KICKOFF', codes)
            wrong = {**late, 'observed_at': NOW.isoformat(), 'outcome': 'away'}
            self.assertIn('MARKET_MAPPING_MISMATCH', {i['code'] for i in pi.integrity_issues(c, row, wrong, NOW)})
            overtime = {**late, 'observed_at': NOW.isoformat(), 'settlement_period': 'including_overtime'}
            self.assertIn('SETTLEMENT_MISMATCH', {i['code'] for i in pi.integrity_issues(c, row, overtime, NOW)})

    # ------------------------------------------------------------------ qualification flow

    def test_research_capability_paper_trades_and_eligible_qualifies(self):
        with db.connect() as c:
            fid = self.fixture(c)
            mid = self.model(c)
            self.priced_prediction(c, fid, mid)
            summary = self.revalidate(c, fid)
            decision, evidence = self.decision(c, fid)
            self.assertEqual((decision['state'], decision['reason_code']), ('MONITORING', 'PAPER_TRADE'))
            self.assertEqual(summary['paper_trades'], 1)
            self.assertTrue(evidence['paper_trade'])
            clv = c.execute('SELECT kind,taken_odds,status FROM sports_ai_clv_tracking').fetchone()
            self.assertEqual(tuple(clv), ('paper', 2.0, 'open'))
            self.eligible(c)
            self.revalidate(c, fid)
            decision, evidence = self.decision(c, fid)
            self.assertEqual(decision['state'], 'QUALIFIED', decision['reason'])
            self.assertGreater(evidence['conservative_ev'], 0)
            self.assertAlmostEqual(decision['rank_score'], evidence['conservative_ev'])
            self.assertEqual(evidence['capability_level'], 'ELIGIBLE')
            self.assertTrue(evidence['consensus_single_source'])
            self.assertEqual(c.execute('SELECT kind FROM sports_ai_clv_tracking').fetchone()[0], 'qualified')
            record = c.execute('SELECT * FROM sports_ai_probability_records WHERE id=?', (evidence['probability_record_id'],)).fetchone()
            self.assertEqual(record['capability_level'], 'ELIGIBLE')
            self.assertAlmostEqual(record['blended_probability'], evidence['probability'])

    def test_negative_conservative_ev_never_qualifies(self):
        with db.connect() as c:
            fid = self.fixture(c)
            mid = self.model(c)
            # Thin evidence: margin swamps a positive point EV.
            c.execute('UPDATE sports_ai_model_registry SET metrics_json=? WHERE id=?',
                      (json.dumps({'calibration_error': 0.15, 'holdout_fixtures': 30}), mid))
            self.priced_prediction(c, fid, mid, probability=0.70, odds=1.6)
            c.execute("""INSERT INTO sports_ai_capability_levels(id,sport,competition,market,level,reason,evidence_json,changed_at,evaluated_at)
                         VALUES('a','football','','HOME','ELIGIBLE','t','{}',?,?),('b','football','League A','HOME','ELIGIBLE','t','{}',?,?)""",
                      (NOW.isoformat(),) * 4)
            self.revalidate(c, fid)
            decision, evidence = self.decision(c, fid)
            self.assertGreater(evidence['expected_value'], 0)
            self.assertLess(evidence['conservative_ev'], 0)
            self.assertEqual((decision['state'], decision['reason_code']), ('MONITORING', 'CONSERVATIVE_EV_NOT_POSITIVE'))

    # ------------------------------------------------------------------ booking gate

    def qualified_leg(self, c, home='Alpha', away='Beta'):
        fid = self.fixture(c, home=home, away=away)
        mid = self.model(c)
        self.priced_prediction(c, fid, mid)
        self.eligible(c)
        self.revalidate(c, fid)
        return {'fixture_id': fid, 'market': 'HOME', 'decimal_odds': 2.0}

    def test_gate_passes_eligible_leg_and_records_booking_odds(self):
        with db.connect() as c:
            leg = self.qualified_leg(c)
            result = pi.require_bookable(c, [leg], NOW)
            self.assertIn((leg['fixture_id'], 'HOME'), result['legs'])
            self.assertGreater(result['joint_conservative_ev'], 0)
            self.assertEqual(c.execute('SELECT kind,booking_odds FROM sports_ai_clv_tracking').fetchone()[:], ('booked', 2.0))

    def test_gate_rejects_demoted_capability_low_price_and_correlated_legs(self):
        with db.connect() as c:
            leg = self.qualified_leg(c)
            with self.assertRaisesRegex(ValueError, 'conservative EV'):
                pi.require_bookable(c, [{**leg, 'decimal_odds': 1.5}], NOW, record_booking=False)
            with self.assertRaisesRegex(ValueError, 'share a fixture'):
                pi.require_bookable(c, [leg, dict(leg)], NOW, record_booking=False)
            c.execute("UPDATE sports_ai_capability_levels SET level='RESEARCH' WHERE competition=''")
            c.execute("DELETE FROM sports_ai_capability_levels WHERE competition<>''")
            with self.assertRaisesRegex(ValueError, 'demoted'):
                pi.require_bookable(c, [leg], NOW, record_booking=False)

    def test_gate_rejects_shared_team_and_unqualified_leg(self):
        with db.connect() as c:
            first = self.qualified_leg(c, 'Alpha', 'Beta')
            second = self.qualified_leg(c, 'Alpha', 'Gamma')
            with self.assertRaisesRegex(ValueError, 'appears in two legs'):
                pi.require_bookable(c, [first, second], NOW, record_booking=False)
            with self.assertRaisesRegex(ValueError, 'no current QUALIFIED'):
                pi.require_bookable(c, [{**first, 'market': 'AWAY'}], NOW, record_booking=False)

    def test_relaxed_booking_policy_cannot_bypass_the_gate(self):
        import booking_policy
        import sportybet_booking
        with db.connect() as c:
            fid = self.fixture(c)
            mid = self.model(c)
            self.priced_prediction(c, fid, mid)
            self.revalidate(c, fid)  # research capability: paper trade only
            self.assertFalse(booking_policy.load(c)['require_live_qualification'])
            selection = {'fixture_id': fid, 'market': 'HOME', 'decimal_odds': 2.0, 'sport': 'football',
                         'selection': 'Alpha', 'home': 'Alpha', 'away': 'Beta'}
            with self.assertRaises(ValueError):
                sportybet_booking.create_job(c, [selection])
            self.assertEqual(c.execute('SELECT COUNT(*) FROM sports_ai_booking_jobs').fetchone()[0], 0)

    # ------------------------------------------------------------------ CLV

    def test_clv_closes_against_last_in_window_price_and_settles_separately(self):
        with db.connect() as c:
            fid = self.fixture(c)
            mid = self.model(c)
            self.priced_prediction(c, fid, mid)
            self.revalidate(c, fid)
            kickoff = NOW + timedelta(hours=5)
            for outcome, odds in (('home', 1.8), ('draw', 3.8), ('away', 5.0)):
                self.quote(c, fid, outcome, odds, observed=kickoff - timedelta(minutes=10))
            self.quote(c, fid, 'home', 1.5, observed=kickoff + timedelta(minutes=5))  # in-play: ignored
            result = pi.capture_closing(c, kickoff + timedelta(minutes=1))
            self.assertEqual(result['closed'], 1)
            row = c.execute('SELECT * FROM sports_ai_clv_tracking').fetchone()
            self.assertAlmostEqual(row['closing_odds'], 1.8)
            self.assertAlmostEqual(row['clv_price'], 2.0 / 1.8 - 1)
            fair, _ = pi.fair_from_quotes({'home': 1.8, 'draw': 3.8, 'away': 5.0})
            self.assertAlmostEqual(row['clv_fair_ev'], fair['home'] * 2.0 - 1)
            self.assertIsNone(row['profit'])
            c.execute("""INSERT INTO sports_ai_results(id,fixture_id,home_score,away_score,status,source,settled_at)
                         VALUES('r1',?,0,1,'final','test',?)""", (fid, NOW.isoformat()))
            self.assertEqual(pi.settle_tracked(c, kickoff + timedelta(hours=3)), 1)
            row = c.execute('SELECT settled_outcome,profit FROM sports_ai_clv_tracking').fetchone()
            self.assertEqual(tuple(row), ('lost', -1.0))
            report = pi.clv_report(c)
            self.assertEqual(report['overall']['observations'], 1)
            self.assertGreater(report['overall']['mean_clv'], 0)
            self.assertLess(report['overall']['profit_units'], 0)  # positive CLV is not profit
            self.assertFalse(report['overall']['persistent_advantage'])

    def test_missing_close_is_recorded_and_closing_refresh_is_requested(self):
        with db.connect() as c:
            fid = self.fixture(c)
            mid = self.model(c)
            self.priced_prediction(c, fid, mid)
            self.revalidate(c, fid)
            kickoff = NOW + timedelta(hours=5)
            self.assertEqual(pi.enqueue_closing_refresh(c, kickoff - timedelta(minutes=30)), 1)
            queued = c.execute('SELECT reason,priority FROM sports_ai_odds_refresh_queue WHERE fixture_id=?', (fid,)).fetchone()
            self.assertEqual(queued['reason'], 'clv_closing')
            self.assertEqual(pi.capture_closing(c, kickoff + timedelta(minutes=5))['no_valid_close'], 0)  # still in grace
            self.assertEqual(pi.capture_closing(c, kickoff + timedelta(hours=1))['no_valid_close'], 1)
            self.assertEqual(c.execute('SELECT status FROM sports_ai_clv_tracking').fetchone()[0], 'no_valid_close')

    def test_postponed_kickoff_keeps_the_row_open(self):
        with db.connect() as c:
            fid = self.fixture(c)
            mid = self.model(c)
            self.priced_prediction(c, fid, mid)
            self.revalidate(c, fid)
            later = NOW + timedelta(days=2)
            c.execute('UPDATE sports_ai_fixtures SET starts_at=? WHERE id=?', (later.isoformat(), fid))
            pi.capture_closing(c, NOW + timedelta(hours=7))
            row = c.execute('SELECT status,starts_at FROM sports_ai_clv_tracking').fetchone()
            self.assertEqual(tuple(row), ('open', later.isoformat()))

    def test_policy_validation(self):
        with db.connect() as c:
            saved = pi.save_policy(c, {'safety_margin': 0.02, 'require_clv': False}, 'test')
            self.assertEqual(saved['version'], 2)
            with self.assertRaises(ValueError):
                pi.save_policy(c, {'safety_margin': 5}, 'test')
            report = pi.integrity_report(c, NOW)
            self.assertEqual(report['policy']['safety_margin'], 0.02)


if __name__ == '__main__':
    unittest.main()
