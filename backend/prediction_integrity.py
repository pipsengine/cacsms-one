"""Prediction integrity, conservative EV, capability levels and closing-line value.

This module is called by ``qualification_engine.revalidate`` for every decision
and by ``sportybet_booking.create_job`` for every booking request. It adds the
gates a positive point-estimate edge alone cannot satisfy:

1. Integrity: kickoff time zone, prediction/feature timing (leakage), quote
   timing, quote-to-market mapping, settlement period and duplicated fixtures.
   Any unresolved issue quarantines the selection.
2. Conservative EV: point EV minus (uncertainty margin x odds) minus
   transaction cost must exceed a safety margin. The margin is the larger of
   the measured calibration error and a one-sided 95% sampling bound, both
   taken from settled forward predictions for that sport and market.
3. Capability level: DISCOVERY, RESEARCH (paper trading) or ELIGIBLE per
   sport-market and sport-league-market. Only ELIGIBLE combinations can
   produce a QUALIFIED booking selection. Promotion requires enough settled
   forward predictions, low calibration error, a Brier improvement over the
   bookmaker's margin-free price that survives a Bonferroni correction for
   every combination tested, no longshot overconfidence and, by default,
   positive closing-line value with a confidence bound above zero.
4. An immutable probability record per distinct (prediction, price,
   probability, policy) that decisions and booking jobs reference by id.
5. Closing-line value for paper, qualified and booked selections, against the
   last price observed inside a pre-kickoff window. Settled paper results are
   tracked separately from CLV.

Nothing here fetches a page, invents a price or places a wager. A day with zero
qualified selections is a valid outcome.
"""
import hashlib
import json
import math
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import NormalDist

POLICY_KEY = 'integrity_policy'
REFRESH_KEY = 'capability_refresh'
LEVELS = ('DISCOVERY', 'RESEARCH', 'ELIGIBLE')
KIND_RANK = {'paper': 0, 'qualified': 1, 'booked': 2}
Z_SELECTION = 1.645

DEFAULT_POLICY = {
    'version': 1,
    # Capability promotion (sport-market).
    'min_observations': 500,
    'max_calibration_error': 0.05,
    'family_alpha': 0.05,
    'longshot_odds': 3.0,
    'longshot_tolerance': 0.02,
    'min_longshot_observations': 50,
    'require_clv': True,
    'min_clv_observations': 100,
    # League inheritance.
    'min_league_observations': 30,
    # Selection-level conservative EV.
    'safety_margin': 0.01,
    'transaction_cost': 0.0,
    # Market consensus.
    'consensus_max_age_minutes': 30,
    'divergence_review': 0.05,
    'source_weights': {},
    # Closing-line value.
    'close_window_minutes': 90,
    'close_grace_minutes': 30,
    'closing_refresh_minutes': 75,
    'refresh_hours': 6,
}

BOUNDS = {
    'min_observations': (50, 100000), 'max_calibration_error': (0.005, 0.5), 'family_alpha': (0.001, 0.2),
    'longshot_odds': (1.5, 50), 'longshot_tolerance': (0.0, 0.2), 'min_longshot_observations': (10, 100000),
    'min_clv_observations': (10, 100000), 'min_league_observations': (5, 100000), 'safety_margin': (0.0, 1.0),
    'transaction_cost': (0.0, 0.5), 'consensus_max_age_minutes': (1, 1440), 'divergence_review': (0.01, 0.5),
    'close_window_minutes': (5, 720), 'close_grace_minutes': (5, 720), 'closing_refresh_minutes': (5, 720),
    'refresh_hours': (1, 168),
}


# --------------------------------------------------------------------------- helpers

def _now(now=None):
    return now or datetime.now(timezone.utc)


def _loads(value, fallback):
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value) if value else fallback
    except (TypeError, ValueError):
        return fallback


def _float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _parse(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _has_zone(value):
    text = str(value or '')
    if 'T' not in text and ' ' not in text:
        return False
    tail = text.split('T', 1)[-1] if 'T' in text else text.split(' ', 1)[-1]
    return tail.endswith('Z') or '+' in tail or '-' in tail


def load_policy(connection):
    row = connection.execute('SELECT value_json FROM sports_ai_settings WHERE key=?', (POLICY_KEY,)).fetchone()
    stored = _loads(row[0], {}) if row else {}
    return {**DEFAULT_POLICY, **{k: v for k, v in stored.items() if k in DEFAULT_POLICY}}


def save_policy(connection, updates, actor):
    current = load_policy(connection)
    merged = dict(current)
    for key, value in (updates or {}).items():
        if key in ('version',) or key not in DEFAULT_POLICY:
            continue
        if key == 'require_clv':
            merged[key] = bool(value)
        elif key == 'source_weights':
            if not isinstance(value, dict) or any(_float(v) is None or _float(v) < 0 for v in value.values()):
                raise ValueError('Source weights must be non-negative numbers')
            merged[key] = {str(k): float(v) for k, v in value.items()}
        else:
            number = _float(value)
            low, high = BOUNDS[key]
            if number is None or not low <= number <= high:
                raise ValueError(f'{key} must be between {low} and {high}')
            merged[key] = number
    merged['version'] = int(current.get('version') or 1) + 1
    moment = _now().isoformat()
    connection.execute(
        """INSERT INTO sports_ai_settings(key,value_json,updated_at) VALUES(?,?,?)
           ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at""",
        (POLICY_KEY, json.dumps(merged), moment),
    )
    _audit(connection, actor, 'integrity.policy', {'version': merged['version'], 'changes': updates})
    return merged


def _audit(connection, actor, action, details):
    connection.execute(
        'INSERT INTO sports_ai_audit_events(id,actor,action,metadata_json) VALUES(?,?,?,?)',
        (str(uuid.uuid4()), actor, action, json.dumps(details, default=str)),
    )


# --------------------------------------------------------------------------- probability math

def break_even(odds):
    o = _float(odds)
    return None if o is None or o <= 1 else 1 / o


def point_ev(probability, odds):
    p, o = _float(probability), _float(odds)
    if p is None or o is None or o <= 1:
        return None
    return p * o - 1


def settlement_ev(distribution, odds):
    """EV from a full settlement distribution: won, half_win, push, half_loss, lost (and void)."""
    o = _float(odds)
    if o is None or o <= 1 or not distribution:
        return None
    returns = {'won': o - 1, 'half_win': (o - 1) / 2, 'push': 0.0, 'void': 0.0, 'half_loss': -0.5, 'lost': -1.0}
    total = sum(float(v) for v in distribution.values())
    if total <= 0:
        return None
    return sum(returns.get(k, 0.0) * float(v) for k, v in distribution.items()) / total


def uncertainty_margin(probability, calibration_error=None, observations=None):
    """Larger of calibration error and a one-sided 95% sampling bound. No evidence: the whole probability."""
    p = _float(probability)
    if p is None:
        return None
    n = int(_float(observations) or 0)
    if n <= 0:
        return p
    sampling = Z_SELECTION * math.sqrt(max(p * (1 - p), 0.0) / n)
    return max(_float(calibration_error) or 0.0, sampling)


def conservative(probability, odds, ev=None, margin=None, cost=0.0):
    p, o = _float(probability), _float(odds)
    if p is None or o is None or o <= 1 or margin is None:
        return {'conservative_probability': None, 'conservative_ev': None}
    base = _float(ev)
    if base is None:
        base = p * o - 1
    return {
        'conservative_probability': max(0.0, p - margin),
        'conservative_ev': base - margin * o - float(cost or 0.0),
    }


def fair_from_quotes(odds_by_outcome):
    """Margin-free probabilities from one bookmaker's complete outcome set."""
    inverse = {k: 1 / float(v) for k, v in odds_by_outcome.items() if _float(v) and float(v) > 1}
    total = sum(inverse.values())
    if len(inverse) < 2 or total <= 0:
        return None, None
    return {k: v / total for k, v in inverse.items()}, total


def odds_band(odds):
    o = _float(odds)
    if o is None:
        return 'unknown'
    for low, high in ((1.0, 1.5), (1.5, 1.7), (1.7, 2.5), (2.5, 4.0), (4.0, 10.0)):
        if low <= o < high:
            return f'{low:.2f}-{high:.2f}'
    return '10.00+'


def lead_bucket(minutes):
    m = _float(minutes)
    if m is None:
        return 'unknown'
    if m < 60:
        return '<1h'
    if m < 360:
        return '1-6h'
    if m < 1440:
        return '6-24h'
    return '>24h'


def mean_ci(values, z=1.96):
    n = len(values)
    if not n:
        return {'n': 0, 'mean': None, 'se': None, 'ci_low': None, 'ci_high': None}
    mean = sum(values) / n
    if n < 2:
        return {'n': n, 'mean': mean, 'se': None, 'ci_low': None, 'ci_high': None}
    variance = sum((v - mean) ** 2 for v in values) / (n - 1)
    se = math.sqrt(variance / n)
    return {'n': n, 'mean': mean, 'se': se, 'ci_low': mean - z * se, 'ci_high': mean + z * se}


def max_drawdown(profits):
    peak = running = worst = 0.0
    for value in profits:
        running += value
        peak = max(peak, running)
        worst = min(worst, running - peak)
    return worst


# --------------------------------------------------------------------------- market consensus

def market_consensus(connection, quote, policy, now=None, target_bookmaker='SportyBet'):
    """Weighted margin-free probability across bookmakers quoting the same market, line and settlement.

    Returns the SportyBet fair probability and, separately, the independent consensus.
    Quotes on a different line or settlement period, stale quotes and incomplete
    outcome sets are excluded and listed with the reason.
    """
    current = _now(now)
    since = (current - timedelta(minutes=float(policy['consensus_max_age_minutes']))).isoformat()
    rows = connection.execute(
        """SELECT bookmaker,outcome,decimal_odds,observed_at,settlement_period FROM sports_ai_odds_snapshots
           WHERE fixture_id=? AND market=? AND line IS ? AND observed_at>=? AND observed_at<=?
           ORDER BY observed_at DESC LIMIT 400""",
        (quote['fixture_id'], quote['market'], quote['line'], since, current.isoformat()),
    ).fetchall()
    by_book = defaultdict(list)
    for row in rows:
        by_book[row['bookmaker']].append(row)
    weights = policy.get('source_weights') or {}
    sources, rejected = [], []
    for book, items in by_book.items():
        latest = _parse(items[0]['observed_at'])
        if items[0]['settlement_period'] != quote.get('settlement_period', 'unknown'):
            rejected.append({'bookmaker': book, 'reason': 'different settlement period'})
            continue
        window = [r for r in items if latest and _parse(r['observed_at']) and latest - _parse(r['observed_at']) <= timedelta(minutes=10)]
        outcomes = {}
        for row in window:
            outcomes.setdefault(row['outcome'], row['decimal_odds'])
        fair, overround = fair_from_quotes(outcomes)
        if fair is None or quote['outcome'] not in fair:
            rejected.append({'bookmaker': book, 'reason': 'incomplete outcome set'})
            continue
        sources.append({'bookmaker': book, 'fair_probability': fair[quote['outcome']], 'overround': overround,
                        'observed_at': items[0]['observed_at'], 'weight': float(weights.get(book, 1.0))})
    target = next((s for s in sources if s['bookmaker'] == target_bookmaker), None)
    independent = [s for s in sources if s['bookmaker'] != target_bookmaker and s['weight'] > 0]
    consensus = None
    if independent:
        total = sum(s['weight'] for s in independent)
        consensus = sum(s['fair_probability'] * s['weight'] for s in independent) / total
    return {
        'target_fair_probability': target['fair_probability'] if target else None,
        'target_overround': target['overround'] if target else None,
        'consensus_probability': consensus,
        'independent_sources': len(independent),
        'sources': sources,
        'rejected': rejected,
        'divergence': None if consensus is None or target is None else target['fair_probability'] - consensus,
        'single_source': not independent,
    }


# --------------------------------------------------------------------------- integrity

def _quote_for(connection, opportunity_id):
    if not opportunity_id:
        return None
    row = connection.execute(
        """SELECT q.id,q.fixture_id,q.bookmaker,q.market,q.outcome,q.line,q.decimal_odds,q.observed_at,
                  q.settlement_period,q.confirmation_status
           FROM sports_ai_value_opportunities o JOIN sports_ai_odds_snapshots q ON q.id=o.odds_id WHERE o.id=?""",
        (opportunity_id,),
    ).fetchone()
    return dict(row) if row else None


def integrity_issues(connection, row, quote, now=None):
    """Return a list of {code, detail}. Empty means no unresolved discrepancy was found."""
    from sport_support import GOAL_SPORTS
    issues = []
    kickoff = _parse(row.get('starts_at'))
    if row.get('starts_at') and not _has_zone(row.get('starts_at')):
        issues.append({'code': 'KICKOFF_TIMEZONE_MISSING', 'detail': 'Kickoff has no time zone, so its instant is ambiguous.'})
    prediction = connection.execute(
        """SELECT p.created_at,p.feature_id,s.cutoff_at,s.generated_at FROM sports_ai_engine_predictions p
           LEFT JOIN sports_ai_feature_snapshots s ON s.id=p.feature_id WHERE p.id=?""",
        (row.get('prediction_id'),),
    ).fetchone()
    if prediction is None:
        issues.append({'code': 'PREDICTION_RECORD_MISSING', 'detail': 'The stored prediction row was not found.'})
    else:
        made = _parse(prediction['created_at'])
        cutoff = _parse(prediction['cutoff_at'])  # the latest data time the features may use (kickoff)
        generated = _parse(prediction['generated_at'])  # when the features were built
        if kickoff and made and made >= kickoff:
            issues.append({'code': 'PREDICTION_AFTER_KICKOFF', 'detail': 'The prediction was stored after kickoff.'})
        if kickoff and cutoff and cutoff > kickoff + timedelta(minutes=1):
            issues.append({'code': 'FEATURE_LEAKAGE', 'detail': 'The feature snapshot may use data from after kickoff.'})
        if made and generated and generated > made + timedelta(minutes=1):
            issues.append({'code': 'FEATURE_SNAPSHOT_CHANGED',
                           'detail': 'The feature snapshot was rebuilt after this prediction, so it no longer matches the inputs used.'})
    if quote:
        observed = _parse(quote.get('observed_at'))
        if kickoff and observed and observed > kickoff:
            issues.append({'code': 'ODDS_AFTER_KICKOFF', 'detail': 'The price was observed after kickoff.'})
        from market_registry import _canonical
        try:
            mapped = _canonical(row.get('sport'), quote.get('market'), quote.get('outcome'), quote.get('line'))
        except Exception:  # mapping helpers must never block integrity reporting
            mapped = None
        if mapped is not None and str(mapped).upper() != str(row.get('market') or '').upper():
            issues.append({'code': 'MARKET_MAPPING_MISMATCH',
                           'detail': f"The quote maps to {mapped}, not {row.get('market')}."})
        if row.get('sport') in GOAL_SPORTS and quote.get('settlement_period') == 'including_overtime':
            issues.append({'code': 'SETTLEMENT_MISMATCH',
                           'detail': 'A regular-time market is priced on an including-overtime quote.'})
    if row.get('fixture_id'):
        duplicate = connection.execute(
            """SELECT f2.id FROM sports_ai_fixtures f1
               JOIN sports_ai_fixtures f2 ON f2.canonical_key=f1.canonical_key AND f2.id<>f1.id AND f2.sport=f1.sport
               JOIN sports_ai_qualification_decisions d ON d.fixture_id=f2.id AND d.market=?
                 AND d.state IN ('QUALIFIED','MONITORING')
               WHERE f1.id=? AND f1.canonical_key<>'' AND f2.status IN ('scheduled','not_started')
                 AND abs(julianday(f2.starts_at)-julianday(f1.starts_at))<0.5
                 AND NOT EXISTS (SELECT 1 FROM sports_ai_fixture_identity_links l WHERE l.status='confirmed'
                   AND ((l.source_fixture_id=f1.id AND l.canonical_fixture_id=f2.id)
                     OR (l.source_fixture_id=f2.id AND l.canonical_fixture_id=f1.id)))
               LIMIT 1""",
            (row.get('market'), row['fixture_id']),
        ).fetchone()
        if duplicate:
            issues.append({'code': 'DUPLICATE_FIXTURE',
                           'detail': f"An unreconciled duplicate fixture {duplicate['id']} carries the same market."})
    return issues


# --------------------------------------------------------------------------- capability levels

def load_capabilities(connection):
    rows = connection.execute('SELECT * FROM sports_ai_capability_levels').fetchall()
    return {(r['sport'], r['competition'], r['market']): dict(r) for r in rows}


def capability_for(capabilities, sport, competition, market, modelled=True):
    """Resolve the level in force. A league needs its own history before it inherits ELIGIBLE."""
    league = capabilities.get((sport, (competition or '').strip(), market))
    base = capabilities.get((sport, '', market))
    if league:
        return {'level': league['level'], 'scope': 'league', 'reason': league['reason'],
                'evidence': _loads(league['evidence_json'], {}), 'sport_evidence': _loads(base['evidence_json'], {}) if base else {}}
    if base:
        evidence = _loads(base['evidence_json'], {})
        if base['level'] == 'ELIGIBLE':
            return {'level': 'RESEARCH', 'scope': 'league', 'evidence': {}, 'sport_evidence': evidence,
                    'reason': 'The sport-market is eligible but this league has no settled forward history yet.'}
        return {'level': base['level'], 'scope': 'sport', 'reason': base['reason'], 'evidence': evidence,
                'sport_evidence': evidence}
    return {'level': 'RESEARCH' if modelled else 'DISCOVERY', 'scope': 'none', 'evidence': {}, 'sport_evidence': {},
            'reason': 'No settled forward predictions are stored for this sport and market yet.' if modelled
            else 'Discovered only; no validated model exists.'}


def _settled_pairs(connection):
    """Forward predictions (stored before kickoff) with a SportyBet price and a final result."""
    from accumulator_engine import settle_result
    rows = connection.execute(
        """SELECT p.id,p.fixture_id,p.market,p.probability,p.created_at,m.version AS model_version,
                  f.sport,f.competition,f.starts_at,q.decimal_odds,q.observed_at,r.home_score,r.away_score
           FROM sports_ai_value_opportunities v
           JOIN sports_ai_engine_predictions p ON p.id=v.prediction_id
           JOIN sports_ai_model_registry m ON m.id=p.model_id
           JOIN sports_ai_odds_snapshots q ON q.id=v.odds_id
           JOIN sports_ai_fixtures f ON f.id=p.fixture_id
           JOIN sports_ai_results r ON r.fixture_id=f.id AND r.status='final'
           WHERE q.bookmaker='SportyBet' AND p.created_at<f.starts_at AND q.observed_at<f.starts_at AND q.decimal_odds>1""",
    ).fetchall()
    latest = {}
    for row in rows:
        key = (row['fixture_id'], row['market'])
        held = latest.get(key)
        if held and (str(held['created_at']), str(held['observed_at'])) >= (str(row['created_at']), str(row['observed_at'])):
            continue
        latest[key] = row
    pairs = []
    for row in latest.values():
        try:
            outcome = settle_result(row['sport'], row['market'], row['home_score'], row['away_score'])
        except (TypeError, ValueError):
            continue
        p, o = _float(row['probability']), _float(row['decimal_odds'])
        if outcome not in ('won', 'lost') or p is None or o is None or not 0 < p < 1:
            continue
        pairs.append({'sport': row['sport'], 'competition': (row['competition'] or '').strip(), 'market': row['market'],
                      'p': p, 'odds': o, 'y': 1 if outcome == 'won' else 0, 'kickoff': str(row['starts_at'])})
    return pairs


def _group_stats(items, margin, policy):
    n = len(items)
    model_sq = [(i['p'] - i['y']) ** 2 for i in items]
    market_p = [min(0.999, max(0.001, (1 / i['odds']) / margin)) for i in items]
    market_sq = [(m - i['y']) ** 2 for m, i in zip(market_p, items)]
    diffs = [b - a for a, b in zip(model_sq, market_sq)]  # positive = model better than market
    improvement = mean_ci(diffs)
    bins = []
    ece = 0.0
    for b in range(10):
        members = [i for i in items if b / 10 <= i['p'] < (b + 1) / 10 or (b == 9 and i['p'] == 1)]
        if not members:
            continue
        mean_p = sum(i['p'] for i in members) / len(members)
        rate = sum(i['y'] for i in members) / len(members)
        ece += abs(mean_p - rate) * len(members) / n
        bins.append({'bin': f'{b / 10:.1f}-{(b + 1) / 10:.1f}', 'n': len(members), 'mean_probability': round(mean_p, 4),
                     'observed_rate': round(rate, 4)})
    longshots = [i for i in items if i['odds'] >= float(policy['longshot_odds'])]
    ls = mean_ci([i['p'] - i['y'] for i in longshots])
    favourites = [i for i in items if i['odds'] < 2.0]
    fav = mean_ci([i['p'] - i['y'] for i in favourites])
    log_loss = sum(-(i['y'] * math.log(max(i['p'], 1e-9)) + (1 - i['y']) * math.log(max(1 - i['p'], 1e-9))) for i in items) / n
    z = None if not improvement['se'] else improvement['mean'] / improvement['se']
    return {
        'observations': n,
        'model_brier': round(sum(model_sq) / n, 5), 'market_brier': round(sum(market_sq) / n, 5),
        'model_log_loss': round(log_loss, 5),
        'brier_improvement': None if improvement['mean'] is None else round(improvement['mean'], 6),
        'improvement_z': None if z is None else round(z, 3),
        'calibration_error': round(ece, 5), 'reliability_bins': bins,
        'longshot_observations': ls['n'], 'longshot_bias': None if ls['mean'] is None else round(ls['mean'], 5),
        'longshot_bias_se': None if ls['se'] is None else round(ls['se'], 5),
        'favourite_observations': fav['n'], 'favourite_bias': None if fav['mean'] is None else round(fav['mean'], 5),
        'market_margin': round(margin, 4),
    }


def _clv_groups(connection):
    stats = defaultdict(list)
    for row in connection.execute("SELECT sport,competition,market,clv_price FROM sports_ai_clv_tracking WHERE status='closed' AND clv_price IS NOT NULL"):
        stats[(row['sport'], '', row['market'])].append(row['clv_price'])
        stats[(row['sport'], (row['competition'] or '').strip(), row['market'])].append(row['clv_price'])
    return {k: mean_ci(v) for k, v in stats.items()}


def _longshot_overconfident(stats, policy):
    if stats['longshot_observations'] < int(policy['min_longshot_observations']) or stats['longshot_bias'] is None:
        return False
    se = stats['longshot_bias_se'] or 0.0
    return stats['longshot_bias'] - Z_SELECTION * se > float(policy['longshot_tolerance'])


def refresh_capabilities(connection, actor='sports-ai-worker', now=None, pairs=None):
    """Recompute DISCOVERY/RESEARCH/ELIGIBLE for every sport-market and sport-league-market with settled history."""
    from market_anchor import load as load_anchors, parameters
    current = _now(now)
    policy = load_policy(connection)
    anchors = load_anchors(connection)
    pairs = _settled_pairs(connection) if pairs is None else pairs
    clv = _clv_groups(connection)
    by_sport_market, by_league = defaultdict(list), defaultdict(list)
    for item in pairs:
        by_sport_market[(item['sport'], item['market'])].append(item)
        by_league[(item['sport'], item['competition'], item['market'])].append(item)
    tested = [k for k, v in by_sport_market.items() if len(v) >= int(policy['min_observations'])]
    family = max(1, len(tested))
    z_required = NormalDist().inv_cdf(1 - float(policy['family_alpha']) / family)
    decided = {}
    for (sport, market), items in by_sport_market.items():
        margin = float(parameters(anchors or {}, sport, '').get('margin') or 1.06)
        stats = _group_stats(items, margin, policy)
        clv_stats = clv.get((sport, '', market), mean_ci([]))
        failures = []
        if stats['observations'] < int(policy['min_observations']):
            failures.append(f"{stats['observations']} settled forward predictions; {int(policy['min_observations'])} required")
        if stats['calibration_error'] > float(policy['max_calibration_error']):
            failures.append(f"calibration error {stats['calibration_error']:.3f} above {float(policy['max_calibration_error']):.3f}")
        if stats['improvement_z'] is None or stats['improvement_z'] < z_required:
            failures.append(f"Brier improvement over the bookmaker is not significant after testing {family} combinations "
                            f"(z {stats['improvement_z']} < {z_required:.2f})")
        if _longshot_overconfident(stats, policy):
            failures.append(f"longshot overconfidence {stats['longshot_bias']:+.3f} on {stats['longshot_observations']} selections")
        if policy['require_clv']:
            if clv_stats['n'] < int(policy['min_clv_observations']):
                failures.append(f"{clv_stats['n']} closing-line observations; {int(policy['min_clv_observations'])} required")
            elif clv_stats['ci_low'] is None or clv_stats['ci_low'] <= 0:
                failures.append('closing-line value is not positive at 95% confidence')
        level = 'RESEARCH' if failures else 'ELIGIBLE'
        reason = '; '.join(failures) if failures else 'All promotion checks passed.'
        evidence = {**stats, 'z_required': round(z_required, 3), 'combinations_tested': family,
                    'clv': {k: (None if v is None else round(v, 6)) for k, v in clv_stats.items()}, 'failures': failures}
        decided[(sport, '', market)] = (level, reason, evidence)
    for (sport, competition, market), items in by_league.items():
        if not competition:
            continue
        base = decided.get((sport, '', market))
        margin = float(parameters(anchors or {}, sport, competition).get('margin') or 1.06)
        stats = _group_stats(items, margin, policy)
        failures = []
        if not base or base[0] != 'ELIGIBLE':
            failures.append('the sport-market is not eligible')
        if stats['observations'] < int(policy['min_league_observations']):
            failures.append(f"{stats['observations']} settled league predictions; {int(policy['min_league_observations'])} required")
        if stats['improvement_z'] is not None and stats['improvement_z'] <= -Z_SELECTION:
            failures.append('the model is significantly worse than the bookmaker in this league')
        if stats['calibration_error'] > 1.5 * float(policy['max_calibration_error']) and stats['observations'] >= int(policy['min_league_observations']):
            failures.append(f"league calibration error {stats['calibration_error']:.3f}")
        if _longshot_overconfident(stats, policy):
            failures.append('longshot overconfidence in this league')
        level = 'RESEARCH' if failures else 'ELIGIBLE'
        decided[(sport, competition, market)] = (level, '; '.join(failures) if failures else 'League evidence supports the eligible sport-market.',
                                                 {**stats, 'failures': failures})
    existing = load_capabilities(connection)
    changed = 0
    for (sport, competition, market), (level, reason, evidence) in decided.items():
        prior = existing.get((sport, competition, market))
        moment = current.isoformat()
        if prior is None:
            connection.execute(
                """INSERT INTO sports_ai_capability_levels(id,sport,competition,market,level,reason,evidence_json,changed_at,evaluated_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (str(uuid.uuid4()), sport, competition, market, level, reason, json.dumps(evidence), moment, moment),
            )
        else:
            connection.execute(
                """UPDATE sports_ai_capability_levels SET level=?,reason=?,evidence_json=?,evaluated_at=?,
                     changed_at=CASE WHEN level<>? THEN ? ELSE changed_at END WHERE id=?""",
                (level, reason, json.dumps(evidence), moment, level, moment, prior['id']),
            )
        if prior is None or prior['level'] != level:
            changed += 1
            _audit(connection, actor, 'capability.level', {'sport': sport, 'competition': competition, 'market': market,
                                                          'from': prior['level'] if prior else None, 'to': level, 'reason': reason})
    summary = {'evaluated_at': current.isoformat(), 'pairs': len(pairs), 'combinations': len(decided), 'changed': changed,
               'eligible': sum(1 for v in decided.values() if v[0] == 'ELIGIBLE'), 'z_required': round(z_required, 3),
               'combinations_tested': family, 'policy_version': policy['version']}
    connection.execute(
        """INSERT INTO sports_ai_settings(key,value_json,updated_at) VALUES(?,?,?)
           ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at""",
        (REFRESH_KEY, json.dumps(summary), current.isoformat()),
    )
    return summary


def capability_stamp(connection):
    row = connection.execute('SELECT value_json FROM sports_ai_settings WHERE key=?', (REFRESH_KEY,)).fetchone()
    return (_loads(row[0], {}) if row else {}).get('evaluated_at') or 'never'


def capabilities_due(connection, now=None):
    policy = load_policy(connection)
    stamp = _parse(capability_stamp(connection))
    return stamp is None or _now(now) - stamp >= timedelta(hours=float(policy['refresh_hours']))


# --------------------------------------------------------------------------- probability records

def record_probability(connection, row, quote, evidence, status, issues, capability, policy, now=None):
    payload = {
        'prediction': row.get('prediction_id'), 'market': row.get('market'), 'quote': (quote or {}).get('id'),
        'odds': (quote or {}).get('decimal_odds'), 'model_probability': evidence.get('model_probability'),
        'probability': evidence.get('probability'), 'conservative_ev': evidence.get('conservative_ev'),
        'consensus': evidence.get('consensus_probability'), 'level': capability.get('level'), 'status': status,
        'policy': policy.get('version'), 'model': row.get('model_version'),
    }
    record_id = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:32]
    prediction = connection.execute('SELECT feature_id,created_at FROM sports_ai_engine_predictions WHERE id=?',
                                    (row.get('prediction_id'),)).fetchone()
    connection.execute(
        """INSERT OR IGNORE INTO sports_ai_probability_records
           (id,prediction_id,fixture_id,sport,competition,market,model_id,model_version,feature_id,prediction_created_at,
            odds_snapshot_id,bookmaker,decimal_odds,odds_observed_at,model_probability,consensus_probability,consensus_sources,
            blended_probability,uncertainty_margin,conservative_probability,break_even,point_ev,conservative_ev,capability_level,
            integrity_status,integrity_issues_json,evidence_json,policy_version,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (record_id, row.get('prediction_id'), row.get('fixture_id'), row.get('sport'), row.get('competition') or '',
         row.get('market'), row.get('model_id'), row.get('model_version'),
         prediction['feature_id'] if prediction else None, prediction['created_at'] if prediction else None,
         (quote or {}).get('id'), (quote or {}).get('bookmaker'), (quote or {}).get('decimal_odds'),
         (quote or {}).get('observed_at'), evidence.get('model_probability'), evidence.get('consensus_probability'),
         int(evidence.get('consensus_independent_sources') or 0), evidence.get('probability'),
         evidence.get('uncertainty_margin'), evidence.get('conservative_probability'), evidence.get('break_even'),
         evidence.get('expected_value'), evidence.get('conservative_ev'), capability.get('level'), status,
         json.dumps(issues), json.dumps({k: evidence.get(k) for k in (
             'market_fair_probability', 'anchor_weight', 'anchor_source', 'calibration_error', 'holdout_fixtures',
             'margin_source', 'margin_observations', 'odds_age_minutes', 'completeness')}, default=str),
         str(policy.get('version')), _now(now).isoformat()),
    )
    return record_id


# --------------------------------------------------------------------------- qualification hook

def prepare(connection):
    """Per-revalidation context: policy, capability table and a stamp for decision fingerprints."""
    policy = load_policy(connection)
    modelled = {r['code'] for r in connection.execute("SELECT code FROM sports_ai_sports WHERE prediction_status='modelled'")}
    return {'policy': policy, 'capabilities': load_capabilities(connection), 'modelled': modelled,
            'stamp': f"integrity:{policy['version']}:{capability_stamp(connection)}"}


def evaluate(connection, row, result, context, now=None):
    """Apply integrity, conservative EV and capability gates to one decide() result."""
    policy = context['policy']
    evidence = result.setdefault('evidence', {})
    quote = _quote_for(connection, row.get('opportunity_id'))
    issues = integrity_issues(connection, row, quote, now)
    evidence['integrity_issues'] = issues
    capability = capability_for(context['capabilities'], row.get('sport'), row.get('competition'), row.get('market'),
                                row.get('sport') in context['modelled'])
    evidence['capability_level'] = capability['level']
    evidence['capability_scope'] = capability['scope']
    evidence['capability_reason'] = capability['reason']
    probability = _float(evidence.get('probability'))
    odds = _float(quote.get('decimal_odds')) if quote else None
    if probability is not None and odds is not None:
        sport_evidence = capability.get('sport_evidence') or {}
        if sport_evidence.get('observations'):
            margin = uncertainty_margin(probability, sport_evidence.get('calibration_error'), sport_evidence.get('observations'))
            evidence['margin_source'] = 'settled_forward_predictions'
            evidence['margin_observations'] = sport_evidence.get('observations')
        else:
            margin = uncertainty_margin(probability, evidence.get('calibration_error'), evidence.get('holdout_fixtures'))
            evidence['margin_source'] = 'model_holdout'
            evidence['margin_observations'] = evidence.get('holdout_fixtures')
        ev = _float(evidence.get('expected_value'))
        cons = conservative(probability, odds, ev, margin, policy['transaction_cost'])
        consensus = market_consensus(connection, quote, policy, now)
        evidence.update(
            break_even=round(1 / odds, 6), uncertainty_margin=None if margin is None else round(margin, 6),
            conservative_probability=None if cons['conservative_probability'] is None else round(cons['conservative_probability'], 6),
            conservative_ev=None if cons['conservative_ev'] is None else round(cons['conservative_ev'], 6),
            safety_margin=policy['safety_margin'], transaction_cost=policy['transaction_cost'],
            consensus_probability=consensus['consensus_probability'],
            consensus_independent_sources=consensus['independent_sources'],
            consensus_single_source=consensus['single_source'],
            sportybet_fair_probability=consensus['target_fair_probability'],
            market_divergence=consensus['divergence'],
            consensus_rejected=consensus['rejected'][:10],
            market_disagreement=None if probability is None or consensus['target_fair_probability'] is None
            else round(probability - consensus['target_fair_probability'], 6),
        )
    state = result.get('state')
    if issues and state in ('QUALIFIED', 'MONITORING', 'AWAITING_DATA'):
        result.update(state='SUSPENDED', reason_code='INTEGRITY_QUARANTINE', products=[], rank_score=None,
                      reason='Quarantined: ' + '; '.join(i['detail'] for i in issues))
    elif state == 'QUALIFIED':
        cons_ev = _float(evidence.get('conservative_ev'))
        if cons_ev is None:
            result.update(state='AWAITING_DATA', reason_code='CONSERVATIVE_EV_UNVERIFIED', products=[], rank_score=None,
                          reason='Conservative EV could not be verified, so the selection cannot qualify.')
        elif cons_ev <= float(policy['safety_margin']):
            result.update(state='MONITORING', reason_code='CONSERVATIVE_EV_NOT_POSITIVE', products=[], rank_score=cons_ev,
                          reason=f"Point EV {float(evidence.get('expected_value') or 0):+.1%} does not survive the uncertainty margin: "
                                 f"conservative EV {cons_ev:+.1%} is not above the {float(policy['safety_margin']):.1%} safety margin.")
        elif evidence.get('market_divergence') is not None and abs(evidence['market_divergence']) > float(policy['divergence_review']):
            result.update(state='MONITORING', reason_code='PRICE_DIVERGENCE_REVIEW', products=[], rank_score=cons_ev,
                          reason='SportyBet differs from the independent consensus; stale price or mapping must be ruled out first.')
        elif capability['level'] != 'ELIGIBLE':
            evidence['paper_trade'] = True
            result.update(state='MONITORING', reason_code='PAPER_TRADE', products=[], rank_score=cons_ev,
                          reason=f"Paper trade: passes every selection gate, but {row.get('sport')} {row.get('market')} is at "
                                 f"{capability['level']} ({capability['reason']}).")
        else:
            result['rank_score'] = cons_ev
    if probability is not None and odds is not None:
        status = 'quarantined' if issues else 'ok'
        evidence['probability_record_id'] = record_probability(connection, row, quote, evidence, status, issues, capability, policy, now)
    result['_quote'] = quote
    return result


# --------------------------------------------------------------------------- closing-line value

def track_selections(connection, prepared, now=None):
    """Record paper and qualified selections after correlation limits are applied."""
    current = _now(now).isoformat()
    tracked = 0
    for item in prepared:
        result, row = item['result'], item['row']
        quote = result.get('_quote')
        if not quote:
            continue
        if result['state'] == 'QUALIFIED':
            kind = 'qualified'
        elif result.get('reason_code') == 'PAPER_TRADE':
            kind = 'paper'
        else:
            continue
        evidence = result.get('evidence') or {}
        odds = float(quote['decimal_odds'])
        existing = connection.execute('SELECT id,kind FROM sports_ai_clv_tracking WHERE prediction_id=? AND market=?',
                                      (row['prediction_id'], row['market'])).fetchone()
        if existing is None:
            connection.execute(
                """INSERT INTO sports_ai_clv_tracking
                   (id,prediction_id,market,fixture_id,sport,competition,starts_at,kind,model_version,probability_record_id,
                    probability,conservative_ev,bookmaker,quote_market,quote_outcome,quote_line,settlement_period,first_odds,
                    first_seen_at,qualification_odds,qualified_at,taken_odds,taken_at,status,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'open',?,?)""",
                (str(uuid.uuid4()), row['prediction_id'], row['market'], row['fixture_id'], row['sport'],
                 row.get('competition') or '', row['starts_at'], kind, row.get('model_version'),
                 evidence.get('probability_record_id'), evidence.get('probability'), evidence.get('conservative_ev'),
                 quote['bookmaker'], quote['market'], quote['outcome'], quote['line'], quote.get('settlement_period') or 'unknown',
                 odds, quote['observed_at'], odds if kind == 'qualified' else None, current if kind == 'qualified' else None,
                 odds, quote['observed_at'], current, current),
            )
            tracked += 1
        elif kind == 'qualified' and KIND_RANK[existing['kind']] < KIND_RANK['qualified']:
            connection.execute(
                """UPDATE sports_ai_clv_tracking SET kind='qualified',qualification_odds=?,qualified_at=?,taken_odds=?,taken_at=?,
                     probability_record_id=?,probability=?,conservative_ev=?,updated_at=? WHERE id=?""",
                (odds, current, odds, quote['observed_at'], evidence.get('probability_record_id'), evidence.get('probability'),
                 evidence.get('conservative_ev'), current, existing['id']),
            )
            tracked += 1
    return tracked


def enqueue_closing_refresh(connection, now=None):
    """Ask the existing odds refresh queue for a late price on every open tracked selection."""
    current = _now(now)
    policy = load_policy(connection)
    horizon = current + timedelta(minutes=float(policy['closing_refresh_minutes']))
    rows = connection.execute(
        """SELECT DISTINCT fixture_id,starts_at FROM sports_ai_clv_tracking
           WHERE status='open' AND starts_at>? AND starts_at<=?""", (current.isoformat(), horizon.isoformat()),
    ).fetchall()
    queued = 0
    for row in rows:
        recent = connection.execute(
            "SELECT 1 FROM sports_ai_odds_snapshots WHERE fixture_id=? AND observed_at>=? LIMIT 1",
            (row['fixture_id'], (current - timedelta(minutes=15)).isoformat()),
        ).fetchone()
        if recent:
            continue
        connection.execute(
            """INSERT INTO sports_ai_odds_refresh_queue(fixture_id,priority,status,attempt,next_attempt_at,last_error,reason,updated_at)
               VALUES(?,500,'queued',0,NULL,NULL,'clv_closing',?)
               ON CONFLICT(fixture_id) DO UPDATE SET priority=MAX(sports_ai_odds_refresh_queue.priority,500),
                 status='queued',reason='clv_closing',updated_at=excluded.updated_at
               WHERE sports_ai_odds_refresh_queue.status!='deferred'
                  OR sports_ai_odds_refresh_queue.next_attempt_at IS NULL
                  OR sports_ai_odds_refresh_queue.next_attempt_at<=excluded.updated_at""",
            (row['fixture_id'], current.isoformat()),
        )
        queued += 1
    return queued


def capture_closing(connection, now=None):
    """Close every tracked selection whose kickoff has passed, against the last in-window pre-kickoff price."""
    current = _now(now)
    policy = load_policy(connection)
    window = timedelta(minutes=float(policy['close_window_minutes']))
    grace = timedelta(minutes=float(policy['close_grace_minutes']))
    rows = connection.execute(
        "SELECT * FROM sports_ai_clv_tracking WHERE status='open' AND starts_at<=? LIMIT 2000", (current.isoformat(),),
    ).fetchall()
    closed = missing = 0
    for row in rows:
        kickoff = _parse(row['starts_at'])
        if kickoff is None:
            continue
        status = connection.execute('SELECT starts_at FROM sports_ai_fixtures WHERE id=?', (row['fixture_id'],)).fetchone()
        if status and _parse(status['starts_at']) and _parse(status['starts_at']) > current:
            # Kickoff moved later: keep the row open with the new time.
            connection.execute('UPDATE sports_ai_clv_tracking SET starts_at=?,updated_at=? WHERE id=?',
                               (status['starts_at'], current.isoformat(), row['id']))
            continue
        close = connection.execute(
            """SELECT decimal_odds,observed_at FROM sports_ai_odds_snapshots
               WHERE fixture_id=? AND bookmaker=? AND market=? AND outcome=? AND line IS ? AND settlement_period=?
                 AND observed_at<=? AND observed_at>=? ORDER BY observed_at DESC LIMIT 1""",
            (row['fixture_id'], row['bookmaker'], row['quote_market'], row['quote_outcome'], row['quote_line'],
             row['settlement_period'], kickoff.isoformat(), (kickoff - window).isoformat()),
        ).fetchone()
        if close is None:
            if current >= kickoff + grace:
                connection.execute("UPDATE sports_ai_clv_tracking SET status='no_valid_close',updated_at=? WHERE id=?",
                                   (current.isoformat(), row['id']))
                missing += 1
            continue
        closing = float(close['decimal_odds'])
        siblings = connection.execute(
            """SELECT outcome,decimal_odds,observed_at FROM sports_ai_odds_snapshots
               WHERE fixture_id=? AND bookmaker=? AND market=? AND line IS ? AND settlement_period=?
                 AND observed_at<=? AND observed_at>=? ORDER BY observed_at DESC""",
            (row['fixture_id'], row['bookmaker'], row['quote_market'], row['quote_line'], row['settlement_period'],
             kickoff.isoformat(), (_parse(close['observed_at']) - timedelta(minutes=10)).isoformat()),
        ).fetchall()
        outcomes = {}
        for sibling in siblings:
            outcomes.setdefault(sibling['outcome'], sibling['decimal_odds'])
        fair, overround = fair_from_quotes(outcomes)
        fair_p = fair.get(row['quote_outcome']) if fair else None
        connection.execute(
            """UPDATE sports_ai_clv_tracking SET status='closed',closing_odds=?,closing_observed_at=?,closing_fair_probability=?,
                 closing_overround=?,clv_price=?,clv_fair_ev=?,updated_at=? WHERE id=?""",
            (closing, close['observed_at'], fair_p, overround, row['taken_odds'] / closing - 1,
             None if fair_p is None else fair_p * row['taken_odds'] - 1, current.isoformat(), row['id']),
        )
        closed += 1
    return {'closed': closed, 'no_valid_close': missing}


def settle_tracked(connection, now=None, limit=2000):
    """Paper/realized outcome per unit stake from the stored final score. Separate from CLV."""
    from accumulator_engine import settle_result
    current = _now(now).isoformat()
    rows = connection.execute(
        """SELECT t.id,t.sport,t.market,t.taken_odds,r.home_score,r.away_score FROM sports_ai_clv_tracking t
           JOIN sports_ai_results r ON r.fixture_id=t.fixture_id AND r.status='final'
           WHERE t.settled_outcome IS NULL AND t.status<>'open' LIMIT ?""", (limit,),
    ).fetchall()
    settled = 0
    for row in rows:
        try:
            outcome = settle_result(row['sport'], row['market'], row['home_score'], row['away_score'])
        except (TypeError, ValueError):
            outcome = None
        if outcome is None:
            continue
        o = float(row['taken_odds'])
        profit = {'won': o - 1, 'half_win': (o - 1) / 2, 'push': 0.0, 'half_loss': -0.5, 'lost': -1.0}[outcome]
        connection.execute('UPDATE sports_ai_clv_tracking SET settled_outcome=?,profit=?,settled_at=?,updated_at=? WHERE id=?',
                           (outcome, profit, current, current, row['id']))
        settled += 1
    return settled


def run_cycle(connection, actor='sports-ai-worker', now=None):
    """Worker entry point: capability refresh when due, closing refreshes, closing capture, settlement."""
    current = _now(now)
    summary = {'status': 'complete', 'places_wager': False}
    if capabilities_due(connection, current):
        summary['capabilities'] = refresh_capabilities(connection, actor, current)
    summary['closing_refresh_queued'] = enqueue_closing_refresh(connection, current)
    summary.update(capture_closing(connection, current))
    summary['settled'] = settle_tracked(connection, current)
    return summary


# --------------------------------------------------------------------------- booking gate

def require_bookable(connection, selections, now=None, record_booking=True):
    """Hard gate for every booking request. Not affected by booking_policy relaxations.

    Each leg needs a current QUALIFIED booking decision whose immutable probability
    record is integrity-clean, at an ELIGIBLE capability, with conservative EV above
    the safety margin at the requested price. Legs may not share a fixture or a team.
    Returns joint figures and the probability record per leg.
    """
    current = _now(now)
    policy = load_policy(connection)
    if not selections:
        raise ValueError('Booking requires at least one qualified selection')
    fixtures, teams, trace = set(), set(), {}
    joint_p = joint_odds = 1.0
    for item in selections:
        fixture_id, market = item.get('fixture_id'), str(item.get('market') or '')
        if fixture_id in fixtures:
            raise ValueError('Two legs share a fixture; correlated legs cannot be booked together')
        fixtures.add(fixture_id)
        fixture = connection.execute('SELECT home,away,starts_at,status FROM sports_ai_fixtures WHERE id=?', (fixture_id,)).fetchone()
        if fixture is None:
            raise ValueError('A leg refers to an unknown fixture')
        for team in (fixture['home'], fixture['away']):
            key = str(team or '').strip().casefold()
            if key and key in teams:
                raise ValueError(f'{team} appears in two legs; correlated legs cannot be booked together')
            teams.add(key)
        kickoff = _parse(fixture['starts_at'])
        if kickoff is None or kickoff <= current or fixture['status'] not in ('scheduled', 'not_started'):
            raise ValueError('A leg has started or is not scheduled')
        decision = connection.execute(
            """SELECT d.* FROM sports_ai_qualification_decisions d WHERE d.fixture_id=? AND upper(d.market)=upper(?)
                 AND d.state='QUALIFIED' AND EXISTS (SELECT 1 FROM json_each(d.products_json) WHERE value='booking')
               ORDER BY d.updated_at DESC LIMIT 1""", (fixture_id, market),
        ).fetchone()
        if decision is None:
            raise ValueError(f'{market} has no current QUALIFIED booking decision')
        if decision['expires_at'] and (_parse(decision['expires_at']) or current) <= current:
            raise ValueError(f'{market} qualification has expired (stale price or kickoff)')
        evidence = _loads(decision['evidence_json'], {})
        record_id = evidence.get('probability_record_id')
        record = connection.execute('SELECT * FROM sports_ai_probability_records WHERE id=?', (record_id,)).fetchone() if record_id else None
        if record is None:
            raise ValueError(f'{market} has no immutable probability record; revalidation is required')
        if record['integrity_status'] != 'ok':
            raise ValueError(f'{market} is quarantined by an integrity check')
        if record['capability_level'] != 'ELIGIBLE':
            raise ValueError(f'{market} is not from an ELIGIBLE capability')
        modelled = {r['code'] for r in connection.execute("SELECT code FROM sports_ai_sports WHERE prediction_status='modelled'")}
        live_level = capability_for(load_capabilities(connection), record['sport'], record['competition'], record['market'],
                                    record['sport'] in modelled)['level']
        if live_level != 'ELIGIBLE':
            raise ValueError(f'{market} capability has been demoted to {live_level}')
        odds = _float(item.get('decimal_odds')) or _float(record['decimal_odds'])
        p, margin = _float(record['blended_probability']), _float(record['uncertainty_margin'])
        if odds is None or p is None or margin is None:
            raise ValueError(f'{market} conservative EV cannot be verified')
        ev = record['point_ev'] if odds == _float(record['decimal_odds']) else None
        cons = conservative(p, odds, ev, margin, policy['transaction_cost'])['conservative_ev']
        if cons is None or cons <= float(policy['safety_margin']):
            raise ValueError(f'{market} conservative EV {cons if cons is None else round(cons, 4)} is not above the safety margin at odds {odds}')
        joint_p *= max(0.0, p - margin)
        joint_odds *= odds
        trace[(fixture_id, market.upper())] = {'probability_record_id': record['id'], 'conservative_ev': round(cons, 6),
                                               'conservative_probability': round(max(0.0, p - margin), 6)}
        if record_booking:
            connection.execute(
                """UPDATE sports_ai_clv_tracking SET kind='booked',booking_odds=?,booked_at=?,taken_odds=?,taken_at=?,updated_at=?
                   WHERE prediction_id=? AND market=? AND booked_at IS NULL""",
                (odds, current.isoformat(), odds, current.isoformat(), current.isoformat(), record['prediction_id'], record['market']),
            )
    return {'legs': trace, 'joint_conservative_probability': joint_p, 'combined_odds': joint_odds,
            'joint_conservative_ev': joint_p * joint_odds - 1,
            'dependence': 'Legs are on distinct fixtures with no shared team; residual dependence is treated as independent.'}


# --------------------------------------------------------------------------- reports

def clv_report(connection, filters=None, now=None):
    filters = {k: v for k, v in (filters or {}).items() if v not in (None, '')}
    policy = load_policy(connection)
    where, params = ['1=1'], []
    for key in ('sport', 'market', 'kind', 'bookmaker'):
        if filters.get(key):
            where.append(f'{key}=?')
            params.append(filters[key])
    if filters.get('competition'):
        where.append('competition LIKE ?')
        params.append(f"%{filters['competition']}%")
    if filters.get('date_from'):
        where.append('starts_at>=?')
        params.append(filters['date_from'])
    if filters.get('date_to'):
        where.append('starts_at<=?')
        params.append(filters['date_to'] + ('T23:59:59' if len(filters['date_to']) <= 10 else ''))
    rows = [dict(r) for r in connection.execute(
        f"SELECT * FROM sports_ai_clv_tracking WHERE {' AND '.join(where)} ORDER BY starts_at", params)]
    closed = [r for r in rows if r['status'] == 'closed' and r['clv_price'] is not None]
    minimum = int(policy['min_clv_observations'])

    def summarise(items):
        clv = mean_ci([r['clv_price'] for r in items])
        fair = mean_ci([r['clv_fair_ev'] for r in items if r['clv_fair_ev'] is not None])
        settled = [r for r in items if r['profit'] is not None]
        profits = [r['profit'] for r in settled]
        return {
            'observations': clv['n'], 'mean_clv': clv['mean'], 'ci_low': clv['ci_low'], 'ci_high': clv['ci_high'],
            'positive_share': (sum(1 for r in items if r['clv_price'] > 0) / len(items)) if items else None,
            'mean_fair_clv_ev': fair['mean'],
            'persistent_advantage': clv['n'] >= minimum and clv['ci_low'] is not None and clv['ci_low'] > 0,
            'settled': len(settled), 'profit_units': sum(profits) if profits else 0.0,
            'yield': (sum(profits) / len(profits)) if profits else None,
            'max_drawdown_units': max_drawdown(profits) if profits else 0.0,
        }

    def grouped(key_fn):
        groups = defaultdict(list)
        for r in closed:
            groups[key_fn(r)].append(r)
        out = [{'key': k, **summarise(v)} for k, v in groups.items()]
        return sorted(out, key=lambda g: -g['observations'])

    def lead(r):
        taken, kickoff = _parse(r['taken_at']), _parse(r['starts_at'])
        return lead_bucket((kickoff - taken).total_seconds() / 60 if taken and kickoff else None)

    realised = {kind: summarise([r for r in rows if r['kind'] == kind and r['status'] == 'closed' and r['clv_price'] is not None])
                for kind in ('paper', 'qualified', 'booked')}
    return {
        'overall': summarise(closed),
        'pipeline': {s: sum(1 for r in rows if r['status'] == s) for s in ('open', 'closed', 'no_valid_close')},
        'by_kind': realised,
        'by_sport': grouped(lambda r: r['sport']),
        'by_league': grouped(lambda r: f"{r['sport']} · {r['competition'] or '—'}")[:50],
        'by_market': grouped(lambda r: f"{r['sport']} · {r['market']}")[:50],
        'by_odds_band': grouped(lambda r: odds_band(r['taken_odds'])),
        'by_bookmaker': grouped(lambda r: r['bookmaker']),
        'by_model_version': grouped(lambda r: r['model_version'] or 'unknown'),
        'by_time_before_kickoff': grouped(lead),
        'recent': [{k: r[k] for k in ('sport', 'competition', 'market', 'kind', 'starts_at', 'taken_odds', 'closing_odds',
                                      'clv_price', 'clv_fair_ev', 'status', 'settled_outcome', 'profit')} for r in rows[-100:][::-1]],
        'minimum_observations': minimum,
        'note': 'CLV measures price quality against the last pre-kickoff price; it is not profit. '
                'An advantage is reported as persistent only when the 95% lower bound is above zero with enough observations.',
        'generated_at': _now(now).isoformat(),
    }


def integrity_report(connection, now=None):
    current = _now(now)
    policy = load_policy(connection)
    capabilities = []
    for row in connection.execute('SELECT * FROM sports_ai_capability_levels ORDER BY level DESC, sport, market, competition'):
        evidence = _loads(row['evidence_json'], {})
        capabilities.append({
            'sport': row['sport'], 'competition': row['competition'], 'market': row['market'], 'level': row['level'],
            'reason': row['reason'], 'changed_at': row['changed_at'], 'evaluated_at': row['evaluated_at'],
            **{k: evidence.get(k) for k in ('observations', 'calibration_error', 'model_brier', 'market_brier',
                                            'brier_improvement', 'improvement_z', 'z_required', 'longshot_bias',
                                            'longshot_observations', 'favourite_bias', 'model_log_loss', 'reliability_bins', 'clv')},
        })
    reasons = [dict(r) for r in connection.execute(
        """SELECT state,reason_code,COUNT(*) total FROM sports_ai_qualification_decisions
           WHERE starts_at>? GROUP BY state,reason_code ORDER BY total DESC""", (current.isoformat(),))]
    quarantined = []
    for row in connection.execute(
        """SELECT d.fixture_id,d.sport,d.competition,d.market,d.reason,d.evidence_json,d.updated_at,f.home,f.away,f.starts_at
           FROM sports_ai_qualification_decisions d JOIN sports_ai_fixtures f ON f.id=d.fixture_id
           WHERE d.reason_code='INTEGRITY_QUARANTINE' AND d.starts_at>? ORDER BY d.updated_at DESC LIMIT 200""",
        (current.isoformat(),),
    ):
        quarantined.append({**{k: row[k] for k in ('fixture_id', 'sport', 'competition', 'market', 'reason', 'updated_at', 'home', 'away', 'starts_at')},
                            'issues': _loads(row['evidence_json'], {}).get('integrity_issues') or []})
    issue_counts = defaultdict(int)
    for item in quarantined:
        for issue in item['issues']:
            issue_counts[issue.get('code')] += 1
    refresh = connection.execute('SELECT value_json FROM sports_ai_settings WHERE key=?', (REFRESH_KEY,)).fetchone()
    return {
        'policy': policy,
        'capability_refresh': _loads(refresh[0], {}) if refresh else None,
        'capabilities': capabilities,
        'levels': {level: sum(1 for c in capabilities if c['level'] == level) for level in LEVELS},
        'decision_reasons': reasons,
        'quarantined': quarantined,
        'quarantine_by_issue': dict(issue_counts),
        'probability_records': connection.execute('SELECT COUNT(*) FROM sports_ai_probability_records').fetchone()[0],
        'paper_trades_open': connection.execute("SELECT COUNT(*) FROM sports_ai_clv_tracking WHERE kind='paper' AND status='open'").fetchone()[0],
        'generated_at': current.isoformat(),
    }


def probability_record(connection, record_id):
    row = connection.execute('SELECT * FROM sports_ai_probability_records WHERE id=?', (record_id,)).fetchone()
    if row is None:
        raise LookupError('Probability record not found')
    item = dict(row)
    item['integrity_issues'] = _loads(item.pop('integrity_issues_json'), [])
    item['evidence'] = _loads(item.pop('evidence_json'), {})
    item['lineage'] = [dict(r) for r in connection.execute(
        """SELECT id,created_at,decimal_odds,blended_probability,conservative_ev,capability_level,integrity_status
           FROM sports_ai_probability_records WHERE prediction_id=? AND market=? ORDER BY created_at""",
        (item['prediction_id'], item['market']))]
    return item


def build_integrity_router(auth, require_admin_key):
    from fastapi import APIRouter, Depends, Header, HTTPException
    from db import connect

    router = APIRouter(prefix='/api/sports-ai/integrity', tags=['Prediction integrity'])

    @router.get('/report')
    def report(user=Depends(auth)):
        with connect() as connection:
            return integrity_report(connection)

    @router.get('/clv')
    def clv(sport: str | None = None, market: str | None = None, competition: str | None = None, kind: str | None = None,
            bookmaker: str | None = None, date_from: str | None = None, date_to: str | None = None, user=Depends(auth)):
        if kind and kind not in KIND_RANK:
            raise HTTPException(422, 'kind must be paper, qualified or booked')
        with connect() as connection:
            return clv_report(connection, dict(sport=sport, market=market, competition=competition, kind=kind,
                                               bookmaker=bookmaker, date_from=date_from, date_to=date_to))

    @router.get('/records/{record_id}')
    def record(record_id: str, user=Depends(auth)):
        try:
            with connect() as connection:
                return probability_record(connection, record_id)
        except LookupError as error:
            raise HTTPException(404, str(error)) from error

    @router.get('/policy')
    def policy(user=Depends(auth)):
        with connect() as connection:
            return load_policy(connection)

    @router.put('/policy')
    def save(body: dict, x_sports_admin_key: str | None = Header(default=None), user=Depends(auth)):
        require_admin_key(x_sports_admin_key)
        try:
            with connect() as connection:
                return save_policy(connection, body, str(user))
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @router.post('/capabilities/refresh')
    def refresh(x_sports_admin_key: str | None = Header(default=None), user=Depends(auth)):
        require_admin_key(x_sports_admin_key)
        with connect() as connection:
            return refresh_capabilities(connection, str(user))

    return router
