"""Accumulator tickets built only from qualified Phase 2 opportunities.

Combined probability is the product of model probabilities only when every leg is
an independent binary market. Shared-team, same-fixture and partial-settlement
combinations keep the quoted price and leave probability unverified.
"""

import json
import math
import re
import time
import uuid
from datetime import datetime, timezone

from prediction_engine import _line_settlement

TARGETS = (2, 5, 10, 20, 25, 50, 100, 500, 800, 1000, 5000, 10000, 20000, 25000, 50000)
PREVIOUS_TARGETS = (2, 5, 10, 25, 100, 1000, 5000, 25000)
RANGE_TIERS = (
    {'id': '1.50-1.99', 'label': '1.50–1.99', 'low': 1.50, 'high': 1.99, 'tier': 150, 'extreme': False},
    {'id': '2.00-2.99', 'label': '2.00–2.99', 'low': 2.00, 'high': 2.99, 'tier': 200, 'extreme': False},
    {'id': '3.00-4.99', 'label': '3.00–4.99', 'low': 3.00, 'high': 4.99, 'tier': 300, 'extreme': False},
    {'id': '5.00-9.99', 'label': '5.00–9.99', 'low': 5.00, 'high': 9.99, 'tier': 500, 'extreme': False},
    {'id': '10.00-19.99', 'label': '10.00–19.99', 'low': 10.00, 'high': 19.99, 'tier': 1001, 'extreme': False},
    {'id': '20.00-49.99', 'label': '20.00–49.99', 'low': 20.00, 'high': 49.99, 'tier': 2000, 'extreme': True},
    {'id': '50.00-99.99', 'label': '50.00–99.99', 'low': 50.00, 'high': 99.99, 'tier': 5001, 'extreme': True},
    {'id': '100.00-249.99', 'label': '100.00–249.99', 'low': 100.00, 'high': 249.99, 'tier': 10001, 'extreme': True},
    {'id': '250.00-499.99', 'label': '250.00–499.99', 'low': 250.00, 'high': 499.99, 'tier': 25001, 'extreme': True},
    {'id': '500.00-1000.00', 'label': '500.00–1,000.00', 'low': 500.00, 'high': 1000.00, 'tier': 50000, 'extreme': True},
)
TIER_POLICY_KEY = 'ticket_tiers'
CONFIG_ID = 'accumulator-default'
STAKE_ASSUMPTION = 1.0
HYPOTHETICAL_LABEL = (
    'Hypothetical unit stake applied to stored decimal odds. '
    'This is not an actual betting return and no wager was placed.'
)

_BINARY_FOOTBALL = {
    'HOME', 'DRAW', 'AWAY', 'DOUBLE_CHANCE_1X', 'DOUBLE_CHANCE_12',
    'DOUBLE_CHANCE_X2', 'DNB_HOME', 'DNB_AWAY', 'BTTS_YES', 'BTTS_NO',
}
_OPPOSITES = {
    'HOME': {'AWAY', 'DRAW'},
    'AWAY': {'HOME', 'DRAW'},
    'DRAW': {'HOME', 'AWAY'},
    'BTTS_YES': {'BTTS_NO'},
    'BTTS_NO': {'BTTS_YES'},
    'MONEYLINE_HOME': {'MONEYLINE_AWAY'},
    'MONEYLINE_AWAY': {'MONEYLINE_HOME'},
    'DNB_HOME': {'DNB_AWAY', 'AWAY'},
    'DNB_AWAY': {'DNB_HOME', 'HOME'},
}


def _now(value=None):
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _parse_time(value):
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _loads(value, fallback):
    try:
        return json.loads(value) if value else fallback
    except (TypeError, json.JSONDecodeError):
        return fallback


def supported_market(sport, market):
    market = (market or '').upper()
    if not market or market.startswith('FIRST_HALF_'):
        return False
    if sport in ('football', 'frauenfussball', 'ice_hockey'):
        if market in _BINARY_FOOTBALL:
            return True
        return bool(
            re.fullmatch(r'(OVER|UNDER)_\d+(?:\.\d+)?', market)
            or re.fullmatch(r'(HOME|AWAY)_(OVER|UNDER)_\d+(?:\.\d+)?', market)
            or re.fullmatch(r'(HOME|AWAY)_(HANDICAP|SPREAD)_-?\d+(?:\.\d+)?', market)
        )
    if sport == 'basketball':
        if market in ('MONEYLINE_HOME', 'MONEYLINE_AWAY'):
            return True
        return bool(
            re.fullmatch(r'(OVER|UNDER)_\d+(?:\.\d+)?', market)
            or re.fullmatch(r'(HOME|AWAY)_(OVER|UNDER)_\d+(?:\.\d+)?', market)
            or re.fullmatch(r'(HOME|AWAY)_SPREAD_-?\d+(?:\.\d+)?', market)
        )
    return False


def _settlement_kind(raw):
    data = _loads(raw, None)
    if not isinstance(data, dict) or 'p_win' not in data or 'p_loss' not in data:
        return 'unknown'
    partial = any(float(data.get(key) or 0) > 1e-9 for key in ('p_half_win', 'p_push', 'p_half_loss'))
    return 'partial' if partial else 'binary'


def _reliability(metrics_json):
    metrics = _loads(metrics_json, {})
    error = metrics.get('calibration_error') if isinstance(metrics, dict) else None
    if error is None:
        return None
    try:
        return max(0.0, min(1.0, 1 - float(error)))
    except (TypeError, ValueError):
        return None


def default_config():
    return {
        'id': CONFIG_ID,
        'targets': list(TARGETS),
        'tolerance': 0.15,
        'min_legs': 2,
        'max_legs': 25,
        'max_per_competition': 3,
        'max_per_team': 1,
        'min_probability': 0.02,
        'min_edge': 0.05,
        'min_model_reliability': 0.0,
        'min_completeness': 0.5,
        'odds_max_age_minutes': 15,
        'max_tickets_per_tier': 3,
        'beam_width': 24,
        'search_limit': 8000,
        'timeout_seconds': 8,
        'allow_same_fixture': 0,
    }


def config_from_row(row):
    if not row:
        return default_config()
    data = dict(row)
    data['targets'] = [int(item) for item in _loads(data.pop('targets_json', None), list(TARGETS))]
    if tuple(data['targets']) == PREVIOUS_TARGETS:
        data['targets'] = list(TARGETS)
    data['allow_same_fixture'] = int(data.get('allow_same_fixture') or 0)
    return data


def load_config(connection):
    row = connection.execute(
        'SELECT * FROM sports_ai_accumulator_configs WHERE id=?', (CONFIG_ID,),
    ).fetchone()
    return config_from_row(row)


def save_config(connection, updates):
    current = load_config(connection)
    merged = {**current, **updates}
    targets = [int(item) for item in merged['targets']]
    if not targets or any(item not in TARGETS for item in targets):
        raise ValueError('Targets must be chosen from the configured odds categories')
    if not 2 <= int(merged['min_legs']) <= int(merged['max_legs']) <= 25:
        raise ValueError('Leg limits must run from 2 through 25, with the maximum at least the minimum')
    if not 0 < float(merged['tolerance']) < 1:
        raise ValueError('Odds tolerance must be between 0 and 1')
    merged['targets'] = targets
    connection.execute(
        """UPDATE sports_ai_accumulator_configs SET
           targets_json=?,tolerance=?,min_legs=?,max_legs=?,max_per_competition=?,max_per_team=?,
           min_probability=?,min_edge=?,min_model_reliability=?,min_completeness=?,
           odds_max_age_minutes=?,max_tickets_per_tier=?,beam_width=?,search_limit=?,
           timeout_seconds=?,allow_same_fixture=?,updated_at=?
           WHERE id=?""",
        (
            json.dumps(targets), float(merged['tolerance']), int(merged['min_legs']), int(merged['max_legs']),
            int(merged['max_per_competition']), int(merged['max_per_team']), float(merged['min_probability']),
            float(merged['min_edge']), float(merged['min_model_reliability']), float(merged['min_completeness']),
            int(merged['odds_max_age_minutes']), int(merged['max_tickets_per_tier']), int(merged['beam_width']),
            int(merged['search_limit']), int(merged['timeout_seconds']), int(merged['allow_same_fixture']),
            _now().isoformat(), CONFIG_ID,
        ),
    )
    return load_config(connection)


def _quality(candidate):
    reliability = 0.5 if candidate.get('reliability') is None else candidate['reliability']
    edge = max(0.0, min(1.0, float(candidate.get('edge') or 0)))
    return (
        0.34 * edge
        + 0.24 * float(candidate['probability'])
        + 0.20 * reliability
        + 0.14 * float(candidate.get('completeness') or 0)
        + 0.08 * float(candidate.get('freshness') or 0)
    )


def qualify_candidates(rows, config, now=None, blocked_fixtures=None):
    """Return (accepted, decisions). Decisions include every rejection and acceptance."""
    current = _now(now)
    blocked = set(blocked_fixtures or ())
    markets = {item.upper() for item in config.get('markets') or []}
    competitions = {item.casefold() for item in config.get('competitions') or []}
    bookmakers = {item.casefold() for item in config.get('bookmakers') or []}
    mode = config.get('sport_mode') or 'mixed'
    decisions = []
    pool = []
    seen_prices = {}

    for row in rows:
        reason = _reject_reason(row, config, current, blocked, markets, competitions, bookmakers, mode)
        if reason:
            decisions.append({'opportunity_id': row.get('opportunity_id'), 'decision': 'rejected', 'reason': reason})
            continue
        candidate = _candidate(row, current, config)
        price_key = (
            candidate['fixture_id'], candidate['market'], candidate['bookmaker'], candidate['odds_observed_at'],
        )
        seen_prices.setdefault(price_key, set()).add(round(candidate['decimal_odds'], 6))
        pool.append(candidate)

    conflicted = {key for key, prices in seen_prices.items() if len(prices) > 1}
    accepted = []
    for candidate in pool:
        price_key = (
            candidate['fixture_id'], candidate['market'], candidate['bookmaker'], candidate['odds_observed_at'],
        )
        if price_key in conflicted:
            decisions.append({
                'opportunity_id': candidate['opportunity_id'], 'decision': 'rejected',
                'reason': 'Conflicting decimal odds were stored for the same bookmaker, market and timestamp',
            })
            continue
        accepted.append(candidate)

    accepted.sort(key=lambda item: (-_quality(item), item['fixture_id'], item['market'], item['opportunity_id']))
    kept = []
    seen_fixtures = set()
    for candidate in accepted:
        if candidate['fixture_id'] in seen_fixtures:
            decisions.append({
                'opportunity_id': candidate['opportunity_id'], 'decision': 'rejected',
                'reason': 'Another selection from this fixture ranked higher. Same-fixture combinations are not used without a validated joint model',
            })
            continue
        seen_fixtures.add(candidate['fixture_id'])
        kept.append(candidate)
        decisions.append({
            'opportunity_id': candidate['opportunity_id'], 'decision': 'accepted',
            'reason': 'Fixture, kickoff, market, quote, model, completeness and edge passed the accumulator checks',
        })
    return kept, decisions


def _reject_reason(row, config, current, blocked, markets, competitions, bookmakers, mode):
    status = row.get('opportunity_status') or row.get('status')
    if status != 'qualified':
        return f'Opportunity status is {status or "missing"}; only qualified Phase 2 records can enter a ticket'
    sport = row.get('sport')
    if mode == 'football' and sport != 'football':
        return 'Selection sport is outside the football-only request'
    if mode == 'basketball' and sport != 'basketball':
        return 'Selection sport is outside the basketball-only request'
    if mode not in ('mixed', 'football', 'basketball', '', None) and sport != mode:
        return 'Selection sport is outside the requested sport'
    if not row.get('fixture_id') or not row.get('home') or not row.get('away'):
        return 'Fixture identity is incomplete'
    if row.get('fixture_id') in blocked:
        return 'Fixture has an unresolved high or critical source-quality issue'
    kickoff = _parse_time(row.get('starts_at'))
    if not kickoff:
        return 'Fixture kickoff time is missing'
    if kickoff <= current:
        return 'Fixture has already started'
    market = (row.get('market') or '').upper()
    if not supported_market(sport, market):
        return 'Selected market has no validated settlement rule for stored final scores'
    if markets and market not in markets:
        return 'Market is outside the requested filter'
    if competitions and str(row.get('competition') or '').casefold() not in competitions:
        return 'Competition is outside the requested filter'
    bookmaker = str(row.get('bookmaker') or '')
    if not bookmaker:
        return 'Bookmaker name is missing from the stored quote'
    if bookmakers and bookmaker.casefold() not in bookmakers:
        return 'Bookmaker is outside the requested filter'
    try:
        odds = float(row.get('decimal_odds'))
    except (TypeError, ValueError):
        return 'Stored decimal odds are missing'
    if not math.isfinite(odds) or odds <= 1:
        return 'Stored decimal odds are not a genuine price above 1'
    observed = _parse_time(row.get('observed_at') or row.get('odds_observed_at'))
    if not observed:
        return 'Odds collection timestamp is missing'
    age = (current - observed).total_seconds() / 60
    if age < -5:
        return 'Odds timestamp is in the future'
    if age > float(config['odds_max_age_minutes']):
        return 'Bookmaker odds are older than the configured freshness window'
    if int(row.get('model_active') or 0) != 1 or row.get('model_status') not in ('active', 'validated'):
        return 'Prediction model is not an active validated model'
    if (row.get('calibration_status') or '') == 'uncalibrated':
        return 'Prediction model has not passed calibration'
    reliability = row.get('reliability')
    if reliability is None and 'metrics_json' in row:
        reliability = _reliability(row.get('metrics_json'))
    if reliability is None and float(config['min_model_reliability']) > 0:
        return 'Model reliability was not recorded'
    if reliability is not None and reliability < float(config['min_model_reliability']):
        return 'Model reliability is below the configured threshold'
    completeness = row.get('data_completeness')
    if completeness is None:
        return 'Source-data completeness was not recorded'
    if float(completeness) < float(config['min_completeness']):
        return 'Source-data completeness is below the configured threshold'
    try:
        probability = float(row.get('probability'))
        edge = float(row.get('edge') if row.get('edge') is not None else row.get('expected_edge'))
    except (TypeError, ValueError):
        return 'Model probability or estimated edge is missing'
    if not math.isfinite(probability) or probability <= 0 or probability >= 1:
        return 'Model probability is outside the open unit interval'
    if probability < float(config['min_probability']):
        return 'Model probability is below the configured threshold'
    if not math.isfinite(edge) or edge < float(config['min_edge']):
        return 'Estimated edge is below the configured threshold'
    kind = row.get('settlement_kind') or _settlement_kind(row.get('settlement_json'))
    if kind == 'unknown':
        return 'Settlement distribution was not recorded, so the selection cannot be validated'
    return None


def _candidate(row, current, config):
    observed = _parse_time(row.get('observed_at') or row.get('odds_observed_at'))
    age = max(0.0, (current - observed).total_seconds() / 60)
    window = max(1.0, float(config['odds_max_age_minutes']))
    reliability = row.get('reliability')
    if reliability is None:
        reliability = _reliability(row.get('metrics_json'))
    market = (row.get('market') or '').upper()
    return {
        'opportunity_id': row['opportunity_id'],
        'fixture_id': row['fixture_id'],
        'sport': row['sport'],
        'competition': row.get('competition') or '',
        'home': row['home'],
        'away': row['away'],
        'starts_at': _parse_time(row['starts_at']).isoformat(),
        'market': market,
        'selection': row.get('selection') or row.get('outcome') or market,
        'decimal_odds': float(row['decimal_odds']),
        'probability': float(row['probability']),
        'edge': float(row.get('edge') if row.get('edge') is not None else row.get('expected_edge')),
        'bookmaker': row['bookmaker'],
        'odds_observed_at': observed.isoformat(),
        'quote_market': row.get('quote_market') or market,
        'quote_outcome': row.get('quote_outcome') or row.get('outcome') or market,
        'quote_line': row.get('quote_line') if row.get('quote_line') is not None else row.get('line'),
        'model_id': row.get('model_id') or '',
        'model_version': row.get('model_version') or '',
        'model_name': row.get('model_name') or '',
        'completeness': float(row['data_completeness']),
        'reliability': reliability,
        'freshness': max(0.0, 1 - age / window),
        'settlement_kind': row.get('settlement_kind') or _settlement_kind(row.get('settlement_json')),
        'teams': (row['home'], row['away']),
    }


def combination_issues(legs, config):
    issues = []
    by_fixture = {}
    for leg in legs:
        by_fixture.setdefault(leg['fixture_id'], []).append(leg)
    if any(len(group) > 1 for group in by_fixture.values()):
        if int(config.get('allow_same_fixture') or 0):
            issues.append(('same_fixture', 'unverified', 'More than one selection uses this fixture and no joint model is available'))
        else:
            issues.append(('same_fixture', 'fail', 'Multiple selections from the same fixture are prohibited'))
        for group in by_fixture.values():
            if len(group) > 1 and _contradictory(group):
                issues.append(('contradictory', 'fail', 'Selections contain contradictory outcomes'))
            elif len(group) > 1 and _same_score_family(group):
                issues.append(('related_markets', 'fail', 'Score-distribution markets from one fixture are not combined.'))
            elif len(group) > 1:
                issues.append(('related_markets', 'unverified', 'Related markets from one fixture have no validated joint distribution'))
    else:
        issues.append(('same_fixture', 'pass', 'Each selection is from a different fixture'))
        issues.append(('contradictory', 'pass', 'No contradictory pair is present'))
        issues.append(('related_markets', 'pass', 'No same-fixture related markets are present'))
    teams = {}
    for leg in legs:
        for team in (leg['home'], leg['away']):
            key = (leg['sport'], team.casefold())
            teams[key] = teams.get(key, 0) + 1
    shared = [key for key, count in teams.items() if count > 1]
    if any(count > int(config['max_per_team']) for count in teams.values()):
        issues.append(('team_exposure', 'fail', 'A team appears more often than the configured maximum'))
    elif shared:
        issues.append(('shared_team', 'unverified', 'Selections share a team and no joint-probability model supports that dependence'))
    else:
        issues.append(('shared_team', 'pass', 'No team is shared across fixtures'))
    competitions = {}
    for leg in legs:
        competitions[leg['competition']] = competitions.get(leg['competition'], 0) + 1
    if any(count > int(config['max_per_competition']) for count in competitions.values()):
        issues.append(('competition_exposure', 'fail', 'A competition contributes more selections than allowed'))
    else:
        issues.append(('competition_exposure', 'pass', 'Competition exposure is within the configured limit'))
    kinds = {leg.get('settlement_kind') or 'unknown' for leg in legs}
    if kinds - {'binary'}:
        issues.append(('partial_settlement', 'unverified', 'A leg can push or settle in part, and no joint payout model is stored'))
    else:
        issues.append(('partial_settlement', 'pass', 'Every leg is a binary win-or-loss market'))
    return issues


def _same_score_family(legs):
    """Markets priced from one full-time score or point distribution."""
    def family(market):
        market = (market or '').upper()
        if market in _BINARY_FOOTBALL or market in ('MONEYLINE_HOME', 'MONEYLINE_AWAY'):
            return 'score'
        if re.fullmatch(r'(OVER|UNDER)_\d+(?:\.\d+)?', market):
            return 'score'
        if re.fullmatch(r'(HOME|AWAY)_(OVER|UNDER)_\d+(?:\.\d+)?', market):
            return 'score'
        if re.fullmatch(r'(HOME|AWAY)_(HANDICAP|SPREAD)_-?\d+(?:\.\d+)?', market):
            return 'score'
        return None
    families = {family(leg.get('market')) for leg in legs}
    return families == {'score'}


def _contradictory(legs):
    markets = {(leg['market'] or '').upper() for leg in legs}
    for market in markets:
        if markets & _OPPOSITES.get(market, set()):
            return True
    totals = {}
    for market in markets:
        match = re.fullmatch(r'(OVER|UNDER)_(.+)', market)
        if match:
            totals.setdefault(match.group(2), set()).add(match.group(1))
    return any(pair >= {'OVER', 'UNDER'} for pair in totals.values())


def _hard_failure(issues):
    return any(result == 'fail' for _, result, _ in issues)


def _unverified(issues):
    return any(result == 'unverified' for _, result, _ in issues)


def combine_ticket(legs, config):
    issues = combination_issues(legs, config)
    odds = math.exp(sum(math.log(leg['decimal_odds']) for leg in legs))
    basis = 'unverified' if _unverified(issues) or _hard_failure(issues) else 'independent'
    if basis == 'independent':
        probability = math.exp(sum(math.log(leg['probability']) for leg in legs))
        edge = probability * odds - 1
        uncertainty = _binary_entropy(probability)
        explanation = (
            'Estimated joint probability is the product of the individual model probabilities because every leg '
            'is a binary market on a different fixture with no shared team. Combined decimal odds are the product '
            'of the stored bookmaker prices. Estimated edge is joint probability times combined odds, minus 1. '
            'These figures are model estimates, not historical performance.'
        )
    else:
        probability = None
        edge = None
        uncertainty = None
        explanation = (
            'Combined decimal odds are the product of the stored bookmaker prices. Joint probability was not '
            'calculated because the selections are dependent, contradictory, or not purely binary, and no validated '
            'joint model is available. The ticket is not a validated opportunity.'
        )
    return {
        'combined_odds': odds,
        'joint_probability': probability,
        'estimated_edge': edge,
        'probability_basis': basis,
        'uncertainty': uncertainty,
        'issues': issues,
        'explanation': explanation,
        'sport_mode': _sport_mode(legs),
    }


def _binary_entropy(probability):
    p = min(1 - 1e-12, max(1e-12, probability))
    return -(p * math.log(p) + (1 - p) * math.log(1 - p)) / math.log(2)


def _sport_mode(legs):
    sports = {leg['sport'] for leg in legs}
    if sports == {'football'}:
        return 'football'
    if sports == {'basketball'}:
        return 'basketball'
    return 'mixed'


def _ticket_score(target, tolerance, legs, combined):
    center = math.log(target)
    span = abs(math.log(1 + tolerance))
    proximity = max(0.0, 1 - abs(math.log(combined['combined_odds']) - center) / span) if span else 0
    mean_probability = sum(leg['probability'] for leg in legs) / len(legs)
    reliability = sum((0.5 if leg.get('reliability') is None else leg['reliability']) for leg in legs) / len(legs)
    completeness = sum(leg.get('completeness') or 0 for leg in legs) / len(legs)
    freshness = sum(leg.get('freshness') or 0 for leg in legs) / len(legs)
    edges = [max(0.0, min(1.0, leg['edge'])) for leg in legs]
    value = sum(edges) / len(edges)
    dependency = 0.0 if combined['probability_basis'] == 'independent' else 1.0
    components = {
        'proximity': proximity,
        'mean_probability': mean_probability,
        'reliability': reliability,
        'completeness': completeness,
        'value': value,
        'freshness': freshness,
        'dependency_risk': dependency,
    }
    score = (
        0.30 * proximity
        + 0.16 * mean_probability
        + 0.16 * reliability
        + 0.12 * completeness
        + 0.14 * value
        + 0.08 * freshness
        + 0.04 * (1 - dependency)
    )
    return score, components


def default_tier_policy():
    return {
        'version': 1,
        'min_settled_before_activation': 20,
        'tiers': [
            {
                'id': item['id'], 'label': item['label'], 'low': item['low'], 'high': item['high'],
                'tier': item['tier'], 'extreme': item['extreme'], 'enabled': True,
                'mode': 'simulation' if item['extreme'] else 'standard',
            }
            for item in RANGE_TIERS
        ],
    }


def load_tier_policy(connection):
    policy = default_tier_policy()
    row = connection.execute('SELECT value_json FROM sports_ai_settings WHERE key=?', (TIER_POLICY_KEY,)).fetchone()
    stored = _loads(row['value_json'], {}) if row else {}
    saved = {item.get('id'): item for item in stored.get('tiers') or []}
    minimum = stored.get('min_settled_before_activation')
    if isinstance(minimum, int) and 1 <= minimum <= 1000:
        policy['min_settled_before_activation'] = minimum
    for item in policy['tiers']:
        choice = saved.get(item['id']) or {}
        if 'enabled' in choice:
            item['enabled'] = bool(choice['enabled'])
        mode = choice.get('mode')
        if mode in ('standard', 'simulation'):
            item['mode'] = mode
        item['settled'] = _tier_settled_count(connection, item['tier'])
        item['activation_ready'] = (not item['extreme']) or item['settled'] >= policy['min_settled_before_activation']
    return policy


def save_tier_policy(connection, updates):
    policy = load_tier_policy(connection)
    minimum = int(updates.get('min_settled_before_activation') or policy['min_settled_before_activation'])
    if not 1 <= minimum <= 1000:
        raise ValueError('Activation sample must be between 1 and 1000 stored settled tickets')
    by_id = {item['id']: item for item in policy['tiers']}
    for change in updates.get('tiers') or []:
        item = by_id.get(change.get('id'))
        if item is None:
            raise ValueError('Unknown odds tier')
        if 'enabled' in change:
            item['enabled'] = bool(change['enabled'])
        if 'mode' in change:
            mode = change['mode']
            if mode not in ('standard', 'simulation'):
                raise ValueError('Tier mode must be standard or simulation')
            if mode == 'standard' and item['extreme'] and item['settled'] < minimum:
                raise ValueError(
                    f"{item['label']} has {item['settled']} settled tickets. "
                    f"It stays in simulation until {minimum} stored results exist."
                )
            item['mode'] = mode
    policy['min_settled_before_activation'] = minimum
    stored = {
        'min_settled_before_activation': minimum,
        'tiers': [{'id': item['id'], 'enabled': item['enabled'], 'mode': item['mode']} for item in policy['tiers']],
    }
    connection.execute(
        """INSERT INTO sports_ai_settings(key,value_json,updated_at) VALUES(?,?,?)
           ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at""",
        (TIER_POLICY_KEY, json.dumps(stored), _now().isoformat()),
    )
    return load_tier_policy(connection)


def _tier_settled_count(connection, tier):
    return connection.execute(
        "SELECT COUNT(*) FROM sports_ai_accumulator_tickets WHERE tier=? AND status='settled'",
        (int(tier),),
    ).fetchone()[0]


def optimize_candidates(candidates, config, now=None):
    """Search each requested target. Never invent a leg to fill a tier."""
    if config.get('ranges'):
        return _optimize_ranges(candidates, config, now)
    current = _now(now)
    targets = [int(item) for item in (config.get('targets') or TARGETS)]
    ordered = sorted(candidates, key=lambda item: (-_quality(item), item['fixture_id'], item['market'], item['opportunity_id']))
    started = time.monotonic()
    deadline = started + float(config['timeout_seconds'])
    evaluations = 0
    truncated = False
    results = []
    for target in targets:
        if time.monotonic() >= deadline:
            truncated = True
            results.append(_gap(target, 'The configured search timeout elapsed before this tier was searched', True))
            continue
        found, reached, evaluations, tier_truncated = _search_target(
            ordered, target, config, deadline, evaluations,
        )
        truncated = truncated or tier_truncated
        if not found and reached:
            reached.sort(key=lambda indexes: sum(math.log(ordered[index]['decimal_odds']) for index in indexes))
            found = reached[:int(config['max_tickets_per_tier'])]
        if not found:
            if len(ordered) < int(config['min_legs']):
                reason = 'Fewer qualified selections than the minimum leg count'
            elif tier_truncated:
                reason = 'The search stopped at its configured limit before a combination inside the target band was found. This is not proof that none exists'
            else:
                low, high = target * (1 - float(config['tolerance'])), target * (1 + float(config['tolerance']))
                reason = (
                    f'No combination of the qualified selections has combined odds between {low:.4g} and {high:.4g} '
                    'under the leg, fixture, team and competition limits'
                )
            results.append(_gap(target, reason, tier_truncated))
            continue
        ranked = []
        for indexes in found:
            legs = [ordered[index] for index in indexes]
            combined = combine_ticket(legs, config)
            if _hard_failure(combined['issues']):
                continue
            score, components = _ticket_score(target, float(config['tolerance']), legs, combined)
            signature = '|'.join(sorted(leg['opportunity_id'] for leg in legs))
            mode = 'standard'
            status = 'qualified' if combined['probability_basis'] == 'independent' else 'unverified_probability'
            explanation = combined['explanation']
            if int(target) >= 20:
                explanation = (
                    f'{int(target)}× uses only selections that already qualified on their own. '
                    'A booking code can be requested for this ticket. No wager is placed. ' + explanation
                )
            ranked.append({
                'tier': target,
                'tier_label': f'{int(target)}×',
                'tier_mode': mode,
                'legs': legs,
                'signature': signature,
                'score': score,
                'components': components,
                **combined,
                'explanation': explanation,
                'status': status,
            })
        ranked.sort(key=lambda item: (
            item['probability_basis'] != 'independent',
            -item['score'],
            abs(math.log(item['combined_odds']) - math.log(target)),
            item['signature'],
        ))
        kept = []
        seen = set()
        for item in ranked:
            if item['signature'] in seen:
                continue
            seen.add(item['signature'])
            kept.append(item)
            if len(kept) >= int(config['max_tickets_per_tier']):
                break
        if kept:
            results.append({'target': target, 'status': 'generated', 'tickets': kept, 'truncated': tier_truncated})
        else:
            results.append(_gap(target, 'Every combination inside the band broke a hard fixture, team or contradiction rule', tier_truncated))
    return {
        'generated_at': current.isoformat(),
        'evaluations': evaluations,
        'truncated': truncated,
        'candidate_count': len(ordered),
        'tiers': results,
    }


def _optimize_ranges(candidates, config, now=None):
    """Keep a combination only when its product already sits inside a range. Do not add another leg after that."""
    current = _now(now)
    ranges = list(config.get('ranges') or [])
    ordered = sorted(candidates, key=lambda item: (-_quality(item), item['fixture_id'], item['market'], item['opportunity_id']))
    started = time.monotonic()
    deadline = started + float(config['timeout_seconds'])
    evaluations = 0
    truncated = False
    results = []
    for band in ranges:
        if time.monotonic() >= deadline:
            truncated = True
            results.append(_gap(band['tier'], 'The configured search timeout elapsed before this tier was searched', True, band))
            continue
        found, evaluations, tier_truncated = _search_band(ordered, band, config, deadline, evaluations)
        truncated = truncated or tier_truncated
        if not found:
            if not ordered:
                reason = 'No independently qualified selection is available'
            elif tier_truncated:
                reason = 'The search stopped at its configured limit before a combination inside this range was found. This is not proof that none exists'
            else:
                reason = (
                    f"No qualified single or accumulator has combined odds between {band['low']:.2f} and {band['high']:.2f} "
                    'under the fixture, team and competition limits. No selection was added to reach the range.'
                )
            results.append(_gap(band['tier'], reason, tier_truncated, band))
            continue
        ranked = []
        for indexes in found:
            legs = [ordered[index] for index in indexes]
            combined = combine_ticket(legs, config)
            if _hard_failure(combined['issues']):
                continue
            signature = '|'.join(sorted(leg['opportunity_id'] for leg in legs))
            status = 'qualified' if combined['probability_basis'] == 'independent' else 'unverified_probability'
            explanation = combined['explanation']
            if band['mode'] == 'simulation':
                status = 'review_required'
                explanation = (
                    f"{band['label']} stays in simulation until stored settled tickets support activation. "
                    'This candidate is not available for booking. ' + explanation
                )
            ranked.append({
                'tier': band['tier'],
                'tier_id': band['id'],
                'tier_label': band['label'],
                'tier_mode': band['mode'],
                'legs': legs,
                'signature': signature,
                'score': float(combined['estimated_edge'] or 0),
                'components': {},
                **combined,
                'explanation': explanation,
                'correlation_exposure': sum(1 for _check, result, _detail in combined['issues'] if result != 'pass'),
                'status': status,
            })
        ranked.sort(key=lambda item: (
            item['status'] != 'qualified',
            len(item['legs']),
            item['combined_odds'],
            -(item['estimated_edge'] or -1),
            item['signature'],
        ))
        kept = []
        seen = set()
        for item in ranked:
            if item['signature'] in seen:
                continue
            seen.add(item['signature'])
            kept.append(item)
            if len(kept) >= int(config['max_tickets_per_tier']):
                break
        if kept:
            results.append({
                'target': band['tier'], 'tier_id': band['id'], 'label': band['label'], 'mode': band['mode'],
                'status': 'generated', 'tickets': kept, 'truncated': tier_truncated,
            })
        else:
            results.append(_gap(
                band['tier'], 'Every combination inside the range broke a hard fixture, team or contradiction rule', tier_truncated, band,
            ))
    return {
        'generated_at': current.isoformat(),
        'evaluations': evaluations,
        'truncated': truncated,
        'candidate_count': len(ordered),
        'tiers': results,
    }


def _gap(target, reason, truncated, band=None):
    payload = {'target': target, 'status': 'no_qualifying_ticket', 'reason': reason, 'tickets': [], 'truncated': truncated}
    if band:
        payload.update({'tier_id': band['id'], 'label': band['label'], 'mode': band['mode']})
    return payload


def _search_band(candidates, band, config, deadline, evaluations):
    low = math.log(float(band['low']))
    high = math.log(float(band['high']))
    min_legs = max(1, int(config.get('min_legs') or 1))
    max_legs = int(config['max_legs'])
    beam_width = int(config['beam_width'])
    limit = int(config['search_limit'])
    beam = [()]
    found = []
    truncated = False
    for _depth in range(max_legs):
        if time.monotonic() >= deadline or evaluations >= limit:
            truncated = True
            break
        nxt = []
        for prefix in beam:
            start = prefix[-1] + 1 if prefix else 0
            for index in range(start, len(candidates)):
                evaluations += 1
                if evaluations > limit or time.monotonic() >= deadline:
                    truncated = True
                    break
                trial = prefix + (index,)
                legs = [candidates[position] for position in trial]
                if _hard_failure(combination_issues(legs, config)):
                    continue
                log_odds = sum(math.log(leg['decimal_odds']) for leg in legs)
                if log_odds > high:
                    continue
                if len(trial) >= min_legs and log_odds >= low:
                    found.append(trial)
                    continue
                if len(trial) < max_legs and log_odds < low:
                    nxt.append(trial)
            if truncated:
                break
        if not nxt:
            break
        nxt.sort(key=lambda item: _partial_key(item, candidates, float(band['low'])))
        if len(nxt) > beam_width:
            truncated = True
            nxt = nxt[:beam_width]
        beam = nxt
    unique = []
    seen = set()
    for indexes in found:
        key = tuple(sorted(indexes))
        if key not in seen:
            seen.add(key)
            unique.append(indexes)
    return unique, evaluations, truncated


def _search_target(candidates, target, config, deadline, evaluations):
    tolerance = float(config['tolerance'])
    low = math.log(target * (1 - tolerance))
    high = math.log(target * (1 + tolerance))
    min_legs = int(config['min_legs'])
    max_legs = int(config['max_legs'])
    beam_width = int(config['beam_width'])
    limit = int(config['search_limit'])
    beam = [()]
    found = []
    reached = []
    truncated = False
    target_log = math.log(target)
    for _depth in range(max_legs):
        if time.monotonic() >= deadline or evaluations >= limit:
            truncated = True
            break
        nxt = []
        for prefix in beam:
            start = prefix[-1] + 1 if prefix else 0
            for index in range(start, len(candidates)):
                evaluations += 1
                if evaluations > limit or time.monotonic() >= deadline:
                    truncated = True
                    break
                trial = prefix + (index,)
                legs = [candidates[position] for position in trial]
                if _hard_failure(combination_issues(legs, config)):
                    continue
                log_odds = sum(math.log(leg['decimal_odds']) for leg in legs)
                if log_odds > high:
                    if len(trial) >= min_legs and log_odds >= target_log and len(reached) < 48:
                        reached.append(trial)
                    continue
                if len(trial) >= min_legs and log_odds >= low:
                    found.append(trial)
                if len(trial) < max_legs and log_odds < high:
                    nxt.append(trial)
            if truncated:
                break
        if not nxt:
            break
        nxt.sort(key=lambda item: _partial_key(item, candidates, target))
        if len(nxt) > beam_width:
            truncated = True
            nxt = nxt[:beam_width]
        beam = nxt
    unique = []
    seen = set()
    for indexes in found:
        key = tuple(sorted(indexes))
        if key not in seen:
            seen.add(key)
            unique.append(indexes)
    reached_unique = []
    reached_seen = set()
    for indexes in reached:
        key = tuple(sorted(indexes))
        if key not in reached_seen:
            reached_seen.add(key)
            reached_unique.append(indexes)
    return unique, reached_unique, evaluations, truncated


def _partial_key(indexes, candidates, target):
    legs = [candidates[index] for index in indexes]
    distance = abs(sum(math.log(leg['decimal_odds']) for leg in legs) - math.log(target))
    quality = sum(_quality(leg) for leg in legs) / len(legs)
    return (distance - 0.15 * quality, indexes)


def settle_result(sport, market, home_score, away_score):
    """Map a verified final score to a leg result. None means the market cannot be settled."""
    market = (market or '').upper()
    if market.startswith('FIRST_HALF_') or not supported_market(sport, market):
        return None
    home, away = int(home_score), int(away_score)
    if sport in ('football', 'frauenfussball', 'ice_hockey'):
        value = _football_result(market, home, away)
    elif sport == 'basketball':
        value = _basketball_result(market, home, away)
    else:
        return None
    return {1: 'won', 0.5: 'half_win', 0: 'push', -0.5: 'half_loss', -1: 'lost'}.get(value)


def _football_result(market, home, away):
    if market == 'HOME':
        return 1 if home > away else -1
    if market == 'DRAW':
        return 1 if home == away else -1
    if market == 'AWAY':
        return 1 if home < away else -1
    if market == 'DOUBLE_CHANCE_1X':
        return 1 if home >= away else -1
    if market == 'DOUBLE_CHANCE_12':
        return 1 if home != away else -1
    if market == 'DOUBLE_CHANCE_X2':
        return 1 if home <= away else -1
    if market == 'DNB_HOME':
        return 0 if home == away else (1 if home > away else -1)
    if market == 'DNB_AWAY':
        return 0 if home == away else (1 if home < away else -1)
    if market == 'BTTS_YES':
        return 1 if home > 0 and away > 0 else -1
    if market == 'BTTS_NO':
        return 1 if home == 0 or away == 0 else -1
    matcher = re.fullmatch(r'(OVER|UNDER)_(\d+(?:\.\d+)?)', market)
    team_total = re.fullmatch(r'(HOME|AWAY)_(OVER|UNDER)_(\d+(?:\.\d+)?)', market)
    handicap = re.fullmatch(r'(HOME|AWAY)_(?:HANDICAP|SPREAD)_(-?\d+(?:\.\d+)?)', market)
    if matcher:
        return _line_settlement(home + away, float(matcher.group(2)), matcher.group(1) == 'OVER')
    if team_total:
        score = home if team_total.group(1) == 'HOME' else away
        return _line_settlement(score, float(team_total.group(3)), team_total.group(2) == 'OVER')
    if handicap:
        margin = home - away if handicap.group(1) == 'HOME' else away - home
        return _line_settlement(margin, -float(handicap.group(2)), True)
    return None


def _basketball_result(market, home, away):
    if market == 'MONEYLINE_HOME':
        return 0 if home == away else (1 if home > away else -1)
    if market == 'MONEYLINE_AWAY':
        return 0 if home == away else (1 if home < away else -1)
    total = re.fullmatch(r'(OVER|UNDER)_(\d+(?:\.\d+)?)', market)
    team = re.fullmatch(r'(HOME|AWAY)_(OVER|UNDER)_(\d+(?:\.\d+)?)', market)
    spread = re.fullmatch(r'(HOME|AWAY)_SPREAD_(-?\d+(?:\.\d+)?)', market)
    if total:
        return _line_settlement(home + away, float(total.group(2)), total.group(1) == 'OVER')
    if team:
        score = home if team.group(1) == 'HOME' else away
        return _line_settlement(score, float(team.group(3)), team.group(2) == 'OVER')
    if spread:
        margin = home - away if spread.group(1) == 'HOME' else away - home
        return _line_settlement(margin, -float(spread.group(2)), True)
    return None


def payout_multiple(legs):
    multiple = 1.0
    for leg in legs:
        result = leg['result']
        odds = float(leg['decimal_odds'])
        if result == 'won':
            multiple *= odds
        elif result == 'half_win':
            multiple *= 1 + (odds - 1) / 2
        elif result in ('push', 'void'):
            multiple *= 1
        elif result == 'half_loss':
            multiple *= 0.5
        elif result == 'lost':
            return 0.0
        else:
            return None
    return multiple


def ticket_outcome(legs):
    if any(leg['result'] == 'pending' for leg in legs):
        return 'pending', None
    active = [leg for leg in legs if leg['result'] != 'void']
    if not active:
        return 'void', 1.0
    multiple = payout_multiple(active)
    if multiple is None:
        return 'pending', None
    if multiple == 0:
        return 'lost', 0.0
    if all(leg['result'] in ('push', 'void') for leg in active):
        return 'void', 1.0
    if any(leg['result'] in ('half_win', 'half_loss', 'push') for leg in active):
        return 'partial', multiple
    return 'won', multiple


def _audit(audit, connection, actor, action, details):
    if audit:
        audit(connection, actor, action, details)


def _notify(connection, event_type, payload):
    connection.execute(
        """INSERT INTO sports_ai_notifications(id,channel,event_type,payload_json,status)
           VALUES(?,?,?,?,?)""",
        (str(uuid.uuid4()), 'dashboard', event_type, json.dumps(payload), 'stored'),
    )


def _blocked_fixtures(connection):
    rows = connection.execute(
        """SELECT fixture_id FROM sports_ai_data_quality_issues
           WHERE resolved_at IS NULL AND severity IN ('high','critical') AND fixture_id IS NOT NULL""",
    ).fetchall()
    return {row['fixture_id'] for row in rows}


def load_opportunity_rows(connection):
    rows = connection.execute(
        """SELECT o.id AS opportunity_id, o.status AS opportunity_status, o.expected_edge,
                  COALESCE(o.data_completeness, s.completeness) AS data_completeness,
                  p.market, p.outcome, p.probability, p.settlement_json, p.model_id,
                  f.id AS fixture_id, f.sport, f.competition, f.home, f.away, f.starts_at, f.status AS fixture_status,
                  q.decimal_odds, q.bookmaker, q.observed_at, q.market AS quote_market, q.outcome AS quote_outcome, q.line AS quote_line,
                  m.version AS model_version, m.model_name, m.status AS model_status, m.active AS model_active,
                  m.metrics_json, m.calibration_status
           FROM sports_ai_value_opportunities o
           JOIN sports_ai_engine_predictions p ON p.id=o.prediction_id
           JOIN sports_ai_fixtures f ON f.id=p.fixture_id
           JOIN sports_ai_model_registry m ON m.id=p.model_id
           JOIN sports_ai_odds_snapshots q ON q.id=o.odds_id
           JOIN sports_ai_qualification_decisions d ON d.opportunity_id=o.id AND d.state='QUALIFIED'
           LEFT JOIN sports_ai_feature_snapshots s ON s.id=p.feature_id
           WHERE o.status='qualified'""",
    ).fetchall()
    from market_registry import choose_best
    return choose_best([dict(row) for row in rows])


def generate_accumulators(connection, actor, audit, sport_mode='mixed', overrides=None, idempotency_key=None, now=None, trigger='manual'):
    current = _now(now)
    key = idempotency_key or f"accumulator:{sport_mode}:{current.strftime('%Y%m%dT%H%M%S%f')}:{uuid.uuid4()}"
    existing = connection.execute(
        'SELECT id,status,summary_json FROM sports_ai_accumulator_runs WHERE idempotency_key=?', (key,),
    ).fetchone()
    if existing:
        return {'run_id': existing['id'], 'status': existing['status'], 'idempotent': True, **_loads(existing['summary_json'], {})}
    config = load_config(connection)
    config.update(overrides or {})
    config['sport_mode'] = sport_mode
    legacy = 'targets' in (overrides or {})
    if legacy:
        config['targets'] = [int(item) for item in config.get('targets') or [] if int(item) in TARGETS]
        if not config['targets']:
            raise ValueError('Choose at least one configured target odds')
    else:
        policy = load_tier_policy(connection)
        selected = set(config.pop('range_ids', None) or [])
        ranges = [item for item in policy['tiers'] if item['enabled'] and (not selected or item['id'] in selected)]
        if selected and len(ranges) != len(selected):
            raise ValueError('Each range must be one of the ten configured odds tiers')
        if not ranges:
            raise ValueError('Choose at least one odds range')
        if 'min_legs' not in (overrides or {}):
            config['min_legs'] = 1
        config['ranges'] = ranges
        config['targets'] = [item['tier'] for item in ranges]
    if int(config['min_legs']) > int(config['max_legs']):
        raise ValueError('Minimum legs cannot exceed maximum legs')
    run_id = str(uuid.uuid4())
    connection.execute(
        """INSERT INTO sports_ai_accumulator_runs
           (id,config_id,sport_mode,status,trigger,idempotency_key,parameters_json,actor,started_at)
           VALUES(?,?,?,'running',?,?,?,?,?)""",
        (run_id, CONFIG_ID, sport_mode, trigger, key, json.dumps(_public_config(config)), actor, current.isoformat()),
    )
    try:
        from qualification_engine import revalidate
        revalidate(connection, actor, current)
        rows = load_opportunity_rows(connection)
        accepted, decisions = qualify_candidates(rows, config, current, _blocked_fixtures(connection))
        search = optimize_candidates(accepted, config, current)
        open_signatures = {
            row['signature'] for row in connection.execute(
                """SELECT signature FROM sports_ai_accumulator_tickets
                   WHERE status IN ('qualified','unverified_probability','review_required','awaiting_odds')""",
            )
        }
        stored = []
        tier_summary = []
        for tier in search['tiers']:
            ids = []
            duplicates = 0
            for ticket in tier['tickets']:
                if ticket['signature'] in open_signatures:
                    duplicates += 1
                    continue
                ticket_id = _insert_ticket(connection, run_id, ticket, config, current, 'created', 'Generated from qualified Phase 2 opportunities')
                open_signatures.add(ticket['signature'])
                ids.append(ticket_id)
                for leg in ticket['legs']:
                    decision = next(item for item in decisions if item['opportunity_id'] == leg['opportunity_id'] and item['decision'] == 'accepted')
                    _log_decision(connection, run_id, ticket_id, decision, current)
                stored.append(_ticket_payload(connection, ticket_id))
            entry = {
                'target': tier['target'],
                'tier_id': tier.get('tier_id'),
                'label': tier.get('label'),
                'mode': tier.get('mode'),
                'status': 'generated' if ids else tier['status'],
                'reason': tier.get('reason') if not ids else None,
                'ticket_ids': ids,
                'duplicates': duplicates,
                'truncated': tier.get('truncated', False),
            }
            if not ids and duplicates and tier['status'] == 'generated':
                entry['status'] = 'duplicate'
                entry['reason'] = 'An open ticket already contains this combination'
            tier_summary.append(entry)
        used = {leg['opportunity_id'] for ticket in stored for leg in ticket['legs']}
        for decision in decisions:
            if decision['decision'] == 'rejected' or decision.get('opportunity_id') not in used:
                _log_decision(connection, run_id, None, decision, current)
        qualified = sum(1 for ticket in stored if ticket['status'] == 'qualified')
        status = 'complete' if stored else 'no_qualifying_ticket'
        summary = {
            'tiers': tier_summary,
            'candidate_count': search['candidate_count'],
            'qualified_inputs': len(rows),
            'accepted': len(accepted),
            'rejected': sum(1 for item in decisions if item['decision'] == 'rejected'),
            'evaluations': search['evaluations'],
            'truncated': search['truncated'],
            'ticket_count': len(stored),
            'qualified_tickets': qualified,
            'rejection_sample': [item for item in decisions if item['decision'] == 'rejected'][:20],
        }
        connection.execute(
            """UPDATE sports_ai_accumulator_runs
               SET status=?,summary_json=?,finished_at=? WHERE id=?""",
            (status, json.dumps(summary), _now().isoformat(), run_id),
        )
        _audit(audit, connection, actor, 'accumulator.generate', {'run_id': run_id, 'status': status, 'tickets': len(stored)})
        return {'run_id': run_id, 'status': status, 'idempotent': False, 'tickets': stored, **summary}
    except Exception as error:
        connection.execute(
            """UPDATE sports_ai_accumulator_runs SET status='failed',error=?,finished_at=? WHERE id=?""",
            (str(error)[:500], _now().isoformat(), run_id),
        )
        raise


def _public_config(config):
    keys = (
        'targets', 'tolerance', 'min_legs', 'max_legs', 'max_per_competition', 'max_per_team',
        'min_probability', 'min_edge', 'min_model_reliability', 'min_completeness', 'odds_max_age_minutes',
        'max_tickets_per_tier', 'beam_width', 'search_limit', 'timeout_seconds', 'allow_same_fixture',
        'sport_mode', 'markets', 'competitions', 'bookmakers',
    )
    payload = {key: config.get(key) for key in keys if key in config or key in default_config()}
    if config.get('ranges'):
        payload['ranges'] = [
            {'id': item['id'], 'label': item['label'], 'mode': item['mode']} for item in config['ranges']
        ]
    return payload


def _insert_ticket(connection, run_id, ticket, config, current, change_type, reason, family_id=None, version=1):
    ticket_id = str(uuid.uuid4())
    family = family_id or ticket_id
    parameters = {
        'targets': config.get('targets'),
        'tolerance': config.get('tolerance'),
        'min_legs': config.get('min_legs'),
        'max_legs': config.get('max_legs'),
        'max_per_competition': config.get('max_per_competition'),
        'max_per_team': config.get('max_per_team'),
        'score_components': ticket.get('components') or {},
        'search_limit': config.get('search_limit'),
        'beam_width': config.get('beam_width'),
        'tier_id': ticket.get('tier_id'),
        'tier_label': ticket.get('tier_label'),
        'tier_mode': ticket.get('tier_mode'),
        'correlation_exposure': ticket.get('correlation_exposure'),
    }
    connection.execute(
        """INSERT INTO sports_ai_accumulator_tickets
           (id,run_id,family_id,version,signature,tier,sport_mode,status,combined_odds,joint_probability,
            estimated_edge,leg_count,probability_basis,uncertainty,explanation,parameters_json,score,finalized,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?)""",
        (
            ticket_id, run_id, family, version, ticket['signature'], int(ticket['tier']), ticket['sport_mode'],
            ticket['status'], ticket.get('combined_odds'), ticket.get('joint_probability'), ticket.get('estimated_edge'),
            len(ticket['legs']), ticket['probability_basis'], ticket.get('uncertainty'), ticket['explanation'],
            json.dumps(parameters), ticket.get('score'), current.isoformat(),
        ),
    )
    for leg in ticket['legs']:
        connection.execute(
            """INSERT INTO sports_ai_accumulator_legs
               (id,ticket_id,opportunity_id,fixture_id,sport,competition,home,away,starts_at,market,selection,
                decimal_odds,probability,edge,bookmaker,odds_observed_at,quote_market,quote_outcome,quote_line,
                model_id,model_version,data_completeness,reliability,settlement_kind,qualification_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                str(uuid.uuid4()), ticket_id, leg['opportunity_id'], leg['fixture_id'], leg['sport'], leg['competition'],
                leg['home'], leg['away'], leg['starts_at'], leg['market'], leg['selection'], leg['decimal_odds'],
                leg.get('probability'), leg.get('edge'), leg['bookmaker'], leg['odds_observed_at'],
                leg.get('quote_market') or leg['market'], leg.get('quote_outcome') or leg['selection'], leg.get('quote_line'),
                leg.get('model_id') or '', leg.get('model_version') or '', leg.get('completeness'), leg.get('reliability'),
                leg.get('settlement_kind') or 'unknown',
                json.dumps({'decision': 'accepted', 'settlement_kind': leg.get('settlement_kind')}),
            ),
        )
    for check_type, result, detail in ticket.get('issues') or combination_issues(ticket['legs'], config):
        connection.execute(
            """INSERT INTO sports_ai_ticket_correlation_checks
               (id,ticket_id,check_type,result,details_json,created_at) VALUES(?,?,?,?,?,?)""",
            (str(uuid.uuid4()), ticket_id, check_type, result, json.dumps({'detail': detail}), current.isoformat()),
        )
    connection.execute(
        """INSERT INTO sports_ai_ticket_versions(id,family_id,ticket_id,version,change_type,reason,created_at)
           VALUES(?,?,?,?,?,?,?)""",
        (str(uuid.uuid4()), family, ticket_id, version, change_type, reason, current.isoformat()),
    )
    return ticket_id


def _log_decision(connection, run_id, ticket_id, decision, current):
    connection.execute(
        """INSERT INTO sports_ai_ticket_qualification_logs
           (id,run_id,ticket_id,opportunity_id,decision,reason,created_at) VALUES(?,?,?,?,?,?,?)""",
        (str(uuid.uuid4()), run_id, ticket_id, decision.get('opportunity_id'), decision['decision'], decision['reason'], current.isoformat()),
    )


def _ticket_payload(connection, ticket_id):
    ticket = dict(connection.execute('SELECT * FROM sports_ai_accumulator_tickets WHERE id=?', (ticket_id,)).fetchone())
    ticket['parameters'] = _loads(ticket.pop('parameters_json'), {})
    ticket['legs'] = [dict(row) for row in connection.execute(
        'SELECT * FROM sports_ai_accumulator_legs WHERE ticket_id=? ORDER BY starts_at,fixture_id', (ticket_id,),
    )]
    ticket['correlation'] = [dict(row) for row in connection.execute(
        'SELECT check_type,result,details_json FROM sports_ai_ticket_correlation_checks WHERE ticket_id=?', (ticket_id,),
    )]
    for leg in ticket['legs']:
        leg['qualification'] = _loads(leg.pop('qualification_json'), {})
    return ticket


def list_tickets(connection, tier=None, sport_mode=None, status=None, family_id=None):
    clauses = ['1=1']
    args = []
    if tier:
        clauses.append('tier=?')
        args.append(int(tier))
    if sport_mode:
        clauses.append('sport_mode=?')
        args.append(sport_mode)
    if status:
        clauses.append('status=?')
        args.append(status)
    if family_id:
        clauses.append('family_id=?')
        args.append(family_id)
    rows = connection.execute(
        f"""SELECT * FROM sports_ai_accumulator_tickets WHERE {' AND '.join(clauses)}
            ORDER BY created_at DESC, tier, signature LIMIT 200""",
        args,
    ).fetchall()
    return [_ticket_payload(connection, row['id']) for row in rows]


def revalidate_ticket(connection, ticket_id, now=None):
    current = _now(now)
    ticket = connection.execute('SELECT * FROM sports_ai_accumulator_tickets WHERE id=?', (ticket_id,)).fetchone()
    if not ticket:
        return None
    config = load_config(connection)
    problems = []
    for leg in connection.execute('SELECT * FROM sports_ai_accumulator_legs WHERE ticket_id=?', (ticket_id,)):
        kickoff = _parse_time(leg['starts_at'])
        if not kickoff or kickoff <= current:
            problems.append({'opportunity_id': leg['opportunity_id'], 'reason': 'Fixture has already started'})
            continue
        quote = connection.execute(
            """SELECT decimal_odds,observed_at FROM sports_ai_odds_snapshots
               WHERE fixture_id=? AND bookmaker=? AND market=? AND outcome=?
                 AND ((line IS NULL AND ? IS NULL) OR line=?)
               ORDER BY observed_at DESC LIMIT 1""",
            (leg['fixture_id'], leg['bookmaker'], leg['quote_market'], leg['quote_outcome'], leg['quote_line'], leg['quote_line']),
        ).fetchone()
        if not quote:
            problems.append({'opportunity_id': leg['opportunity_id'], 'reason': 'No stored quote remains for this selection'})
            continue
        observed = _parse_time(quote['observed_at'])
        age = (current - observed).total_seconds() / 60 if observed else 9999
        if age > config['odds_max_age_minutes']:
            problems.append({'opportunity_id': leg['opportunity_id'], 'reason': 'Latest stored quote is outside the freshness window'})
        elif abs(float(quote['decimal_odds']) - float(leg['decimal_odds'])) > 0.001:
            problems.append({
                'opportunity_id': leg['opportunity_id'],
                'reason': f"Latest stored odds are {float(quote['decimal_odds']):.4g}; the ticket still records {float(leg['decimal_odds']):.4g}",
                'decimal_odds': float(quote['decimal_odds']),
                'observed_at': quote['observed_at'],
            })
        opportunity = connection.execute(
            'SELECT status FROM sports_ai_value_opportunities WHERE id=?', (leg['opportunity_id'],),
        ).fetchone()
        if not opportunity or opportunity['status'] != 'qualified':
            problems.append({'opportunity_id': leg['opportunity_id'], 'reason': 'The Phase 2 opportunity is no longer qualified'})
    return {'ticket_id': ticket_id, 'ok': not problems, 'problems': problems, 'checked_at': current.isoformat()}


def _clone_from(connection, ticket_id, legs, status, change_type, reason, now, run_id=None):
    source = connection.execute('SELECT * FROM sports_ai_accumulator_tickets WHERE id=?', (ticket_id,)).fetchone()
    config = load_config(connection)
    material = [_material_leg(leg) for leg in legs]
    combined = combine_ticket(material, config) if material else {
        'combined_odds': None, 'joint_probability': None, 'estimated_edge': None,
        'probability_basis': 'unverified', 'uncertainty': None, 'issues': [],
        'explanation': reason, 'sport_mode': source['sport_mode'],
    }
    if len(material) < int(config['min_legs']):
        status = 'review_required'
        combined['explanation'] = reason + ' The ticket is below the minimum leg count and is not a validated accumulator.'
        combined['probability_basis'] = 'unverified'
        combined['joint_probability'] = None
        combined['estimated_edge'] = None
    ticket = {
        'tier': source['tier'],
        'signature': '|'.join(sorted(leg['opportunity_id'] for leg in material)) or f"empty:{source['family_id']}:{source['version']+1}",
        'legs': material,
        'score': source['score'],
        'components': _loads(source['parameters_json'], {}).get('score_components') or {},
        'status': status,
        **combined,
    }
    version = int(source['version']) + 1
    return _insert_ticket(
        connection, run_id or source['run_id'], ticket, config, _now(now), change_type, reason,
        family_id=source['family_id'], version=version,
    )


def _material_leg(row):
    if 'data_completeness' in row or 'qualification_json' in row:
        return _leg_dict(row)
    return row


def _leg_dict(row):
    data = dict(row)
    return {
        'opportunity_id': data['opportunity_id'],
        'fixture_id': data['fixture_id'],
        'sport': data['sport'],
        'competition': data['competition'],
        'home': data['home'],
        'away': data['away'],
        'starts_at': data['starts_at'],
        'market': data['market'],
        'selection': data['selection'],
        'decimal_odds': float(data['decimal_odds']),
        'probability': None if data.get('probability') is None else float(data['probability']),
        'edge': None if data.get('edge') is None else float(data['edge']),
        'bookmaker': data['bookmaker'],
        'odds_observed_at': data['odds_observed_at'],
        'quote_market': data['quote_market'],
        'quote_outcome': data['quote_outcome'],
        'quote_line': data['quote_line'],
        'model_id': data['model_id'],
        'model_version': data['model_version'],
        'completeness': data['data_completeness'],
        'reliability': data['reliability'],
        'freshness': 1,
        'settlement_kind': data['settlement_kind'],
    }


def remove_leg(connection, actor, audit, ticket_id, opportunity_id, now=None):
    current = _now(now)
    legs = [dict(row) for row in connection.execute(
        'SELECT * FROM sports_ai_accumulator_legs WHERE ticket_id=?', (ticket_id,),
    )]
    if not legs or not any(leg['opportunity_id'] == opportunity_id for leg in legs):
        return None
    remaining = [leg for leg in legs if leg['opportunity_id'] != opportunity_id]
    new_id = _clone_from(
        connection, ticket_id, remaining, 'review_required', 'leg_removed',
        'A selection was removed. The previous ticket version is unchanged and this version requires review.',
        current,
    )
    _audit(audit, connection, actor, 'accumulator.leg_remove', {'from': ticket_id, 'ticket_id': new_id, 'opportunity_id': opportunity_id})
    return _ticket_payload(connection, new_id)


def replace_leg(connection, actor, audit, ticket_id, remove_id, add_id, now=None):
    current = _now(now)
    source_legs = [dict(row) for row in connection.execute(
        'SELECT * FROM sports_ai_accumulator_legs WHERE ticket_id=?', (ticket_id,),
    )]
    if not any(leg['opportunity_id'] == remove_id for leg in source_legs):
        return {'error': 'The selection to replace is not on this ticket'}
    if any(leg['opportunity_id'] == add_id for leg in source_legs):
        return {'error': 'That opportunity is already on the ticket'}
    config = load_config(connection)
    rows = [row for row in load_opportunity_rows(connection) if row['opportunity_id'] == add_id]
    accepted, decisions = qualify_candidates(rows, config, current, _blocked_fixtures(connection))
    if not accepted:
        reason = decisions[0]['reason'] if decisions else 'The replacement did not qualify'
        return {'error': reason}
    remaining = [_leg_dict(leg) for leg in source_legs if leg['opportunity_id'] != remove_id]
    remaining.append(accepted[0])
    issues = combination_issues(remaining, config)
    if _hard_failure(issues):
        failed = next(detail for _, result, detail in issues if result == 'fail')
        return {'error': failed}
    new_id = _clone_from(
        connection, ticket_id, remaining, 'review_required', 'leg_replaced',
        'A selection was replaced. The previous version is unchanged and this version requires review.',
        current,
    )
    _audit(audit, connection, actor, 'accumulator.leg_replace', {'from': ticket_id, 'ticket_id': new_id, 'remove': remove_id, 'add': add_id})
    return _ticket_payload(connection, new_id)


def recalculate_ticket(connection, actor, audit, ticket_id, now=None):
    current = _now(now)
    check = revalidate_ticket(connection, ticket_id, current)
    if check is None:
        return None
    if check['ok']:
        connection.execute(
            """INSERT INTO sports_ai_ticket_monitoring_events
               (id,ticket_id,event_type,details_json,created_at) VALUES(?,?,?,?,?)""",
            (str(uuid.uuid4()), ticket_id, 'revalidated_unchanged', json.dumps(check), current.isoformat()),
        )
        _audit(audit, connection, actor, 'accumulator.recalculate', {'ticket_id': ticket_id, 'changed': False})
        payload = _ticket_payload(connection, ticket_id)
        payload['revalidation'] = check
        return payload
    legs = [_leg_dict(row) for row in connection.execute(
        'SELECT * FROM sports_ai_accumulator_legs WHERE ticket_id=?', (ticket_id,),
    )]
    moved = {item['opportunity_id']: item for item in check['problems'] if item.get('decimal_odds')}
    if not moved:
        connection.execute(
            """UPDATE sports_ai_accumulator_tickets SET status='review_required' WHERE id=?""",
            (ticket_id,),
        )
        connection.execute(
            """INSERT INTO sports_ai_ticket_monitoring_events
               (id,ticket_id,event_type,details_json,created_at) VALUES(?,?,?,?,?)""",
            (str(uuid.uuid4()), ticket_id, 'revalidation_failed', json.dumps(check), current.isoformat()),
        )
        payload = _ticket_payload(connection, ticket_id)
        payload['revalidation'] = check
        return payload
    for leg in legs:
        if leg['opportunity_id'] in moved:
            leg['decimal_odds'] = moved[leg['opportunity_id']]['decimal_odds']
            leg['odds_observed_at'] = moved[leg['opportunity_id']]['observed_at']
    new_id = _clone_from(
        connection, ticket_id, legs, 'review_required', 'recalculated',
        'Stored odds moved. A new version records the latest quote and requires review; the previous selection list was not overwritten.',
        current,
    )
    _audit(audit, connection, actor, 'accumulator.recalculate', {'from': ticket_id, 'ticket_id': new_id, 'changed': True})
    payload = _ticket_payload(connection, new_id)
    payload['revalidation'] = check
    return payload


def save_ticket(connection, actor, audit, ticket_id):
    ticket = connection.execute('SELECT id,status FROM sports_ai_accumulator_tickets WHERE id=?', (ticket_id,)).fetchone()
    if not ticket:
        return None
    connection.execute('UPDATE sports_ai_accumulator_tickets SET finalized=1 WHERE id=?', (ticket_id,))
    connection.execute(
        """INSERT INTO sports_ai_ticket_versions(id,family_id,ticket_id,version,change_type,reason,created_at)
           SELECT ?,family_id,id,version,'saved','Ticket finalized without changing its selections',?
           FROM sports_ai_accumulator_tickets WHERE id=?""",
        (str(uuid.uuid4()), _now().isoformat(), ticket_id),
    )
    _audit(audit, connection, actor, 'accumulator.save', {'ticket_id': ticket_id})
    return _ticket_payload(connection, ticket_id)


def accept_ticket(connection, actor, audit, ticket_id, now=None):
    check = revalidate_ticket(connection, ticket_id, now)
    if check is None:
        return None
    if not check['ok']:
        return {'error': 'Revalidation failed', 'revalidation': check}
    ticket = _ticket_payload(connection, ticket_id)
    config = load_config(connection)
    parameters = ticket.get('parameters') or {}
    if parameters.get('tier_mode') == 'simulation':
        return {'error': 'This odds tier remains in simulation until stored settled results support activation', 'revalidation': check}
    minimum = 1 if parameters.get('tier_id') else int(config['min_legs'])
    if ticket['probability_basis'] != 'independent' or ticket['leg_count'] < minimum:
        return {'error': 'The ticket remains unverified or below the minimum leg count', 'revalidation': check}
    band = next((item for item in RANGE_TIERS if item['tier'] == ticket['tier']), None)
    if band:
        low, high = band['low'], band['high']
    else:
        low = ticket['tier'] * (1 - config['tolerance'])
        high = ticket['tier'] * (1 + config['tolerance'])
    if ticket['combined_odds'] is None or not low <= ticket['combined_odds'] <= high:
        return {'error': 'Combined odds are outside the tier tolerance', 'revalidation': check}
    connection.execute(
        """UPDATE sports_ai_accumulator_tickets SET status='qualified' WHERE id=?""", (ticket_id,),
    )
    _audit(audit, connection, actor, 'accumulator.accept', {'ticket_id': ticket_id})
    payload = _ticket_payload(connection, ticket_id)
    payload['revalidation'] = check
    return payload


def export_ticket(connection, actor, audit, ticket_id, now=None):
    ticket = connection.execute(
        'SELECT status,probability_basis,finalized FROM sports_ai_accumulator_tickets WHERE id=?', (ticket_id,),
    ).fetchone()
    if not ticket:
        return None
    check = revalidate_ticket(connection, ticket_id, now)
    if ticket['status'] != 'qualified' or ticket['probability_basis'] != 'independent':
        return {'error': 'Only a qualified ticket with an independent probability can be exported', 'revalidation': check}
    if not check['ok']:
        connection.execute(
            """INSERT INTO sports_ai_ticket_monitoring_events
               (id,ticket_id,event_type,details_json,created_at) VALUES(?,?,?,?,?)""",
            (str(uuid.uuid4()), ticket_id, 'export_blocked', json.dumps(check), _now(now).isoformat()),
        )
        return {'error': 'Export blocked because revalidation failed', 'revalidation': check}
    payload = _ticket_payload(connection, ticket_id)
    payload['revalidation'] = check
    payload['disclaimer'] = (
        'Estimated probability and edge are model calculations from the quotes stored on this ticket. '
        'They are not historical returns and this export does not place a wager.'
    )
    connection.execute(
        """INSERT INTO sports_ai_ticket_monitoring_events
           (id,ticket_id,event_type,details_json,created_at) VALUES(?,?,?,?,?)""",
        (str(uuid.uuid4()), ticket_id, 'exported', json.dumps({'checked_at': check['checked_at']}), check['checked_at']),
    )
    _audit(audit, connection, actor, 'accumulator.export', {'ticket_id': ticket_id})
    return payload


def monitor_accumulators(connection, actor, audit, idempotency_key, now=None):
    current = _now(now)
    existing = connection.execute(
        'SELECT id,status,summary_json FROM sports_ai_accumulator_runs WHERE idempotency_key=?', (idempotency_key,),
    ).fetchone()
    if existing:
        return {'run_id': existing['id'], 'status': existing['status'], 'idempotent': True, **_loads(existing['summary_json'], {})}
    run_id = str(uuid.uuid4())
    connection.execute(
        """INSERT INTO sports_ai_accumulator_runs
           (id,config_id,sport_mode,status,trigger,idempotency_key,parameters_json,actor,started_at)
           VALUES(?,?,'mixed','running','monitor',?,?,?,?)""",
        (run_id, CONFIG_ID, idempotency_key, '{}', actor, current.isoformat()),
    )
    summary = {'expired': 0, 'settled': 0, 'awaiting_result': 0, 'unchanged': 0}
    try:
        tickets = connection.execute(
            """SELECT * FROM sports_ai_accumulator_tickets
               WHERE status IN ('qualified','unverified_probability','review_required','awaiting_odds','expired')""",
        ).fetchall()
        for ticket in tickets:
            if connection.execute('SELECT 1 FROM sports_ai_ticket_settlements WHERE ticket_id=?', (ticket['id'],)).fetchone():
                summary['unchanged'] += 1
                continue
            legs = [dict(row) for row in connection.execute(
                'SELECT * FROM sports_ai_accumulator_legs WHERE ticket_id=?', (ticket['id'],),
            )]
            started = any((_parse_time(leg['starts_at']) or current) <= current for leg in legs)
            outcome = _settle_if_ready(connection, ticket['id'], legs, current)
            if outcome == 'settled':
                summary['settled'] += 1
                _event(connection, ticket['id'], run_id, 'settled', {'detail': 'Settled from stored final scores'}, current)
            elif started and ticket['status'] != 'expired':
                connection.execute(
                    """UPDATE sports_ai_accumulator_tickets SET status='expired' WHERE id=?""", (ticket['id'],),
                )
                _event(connection, ticket['id'], run_id, 'kickoff', {'detail': 'A selection has reached kickoff. The ticket can no longer be placed.'}, current)
                _notify(connection, 'ticket_expired', {'ticket_id': ticket['id']})
                summary['expired'] += 1
            elif outcome == 'pending' and started:
                summary['awaiting_result'] += 1
                _event_once(connection, ticket['id'], run_id, 'awaiting_result', current)
            elif ticket['status'] == 'qualified':
                from qualification_engine import ticket_is_bookable
                if ticket_is_bookable(connection, ticket['id']):
                    summary['unchanged'] += 1
                else:
                    connection.execute(
                        "UPDATE sports_ai_accumulator_tickets SET status='review_required' WHERE id=?",
                        (ticket['id'],),
                    )
                    _event(connection, ticket['id'], run_id, 'qualification_lapsed', {
                        'detail': 'A selection no longer has a current QUALIFIED booking decision.',
                    }, current)
                    summary['review_required'] = summary.get('review_required', 0) + 1
            else:
                summary['unchanged'] += 1
        refresh_performance(connection, current)
        connection.execute(
            """UPDATE sports_ai_accumulator_runs SET status='complete',summary_json=?,finished_at=? WHERE id=?""",
            (json.dumps(summary), current.isoformat(), run_id),
        )
        _audit(audit, connection, actor, 'accumulator.monitor', summary)
        return {'run_id': run_id, 'status': 'complete', 'idempotent': False, **summary}
    except Exception as error:
        connection.execute(
            """UPDATE sports_ai_accumulator_runs SET status='failed',error=?,finished_at=? WHERE id=?""",
            (str(error)[:500], current.isoformat(), run_id),
        )
        raise


def _event(connection, ticket_id, run_id, event_type, details, current):
    connection.execute(
        """INSERT INTO sports_ai_ticket_monitoring_events
           (id,ticket_id,run_id,event_type,details_json,created_at) VALUES(?,?,?,?,?,?)""",
        (str(uuid.uuid4()), ticket_id, run_id, event_type, json.dumps(details), current.isoformat()),
    )


def _event_once(connection, ticket_id, run_id, event_type, current):
    existing = connection.execute(
        """SELECT 1 FROM sports_ai_ticket_monitoring_events
           WHERE ticket_id=? AND event_type=? LIMIT 1""",
        (ticket_id, event_type),
    ).fetchone()
    if not existing:
        _event(connection, ticket_id, run_id, event_type, {'detail': 'Kickoff has passed and no final score is stored'}, current)


def _settle_if_ready(connection, ticket_id, legs, current):
    scored = []
    for leg in legs:
        if (_parse_time(leg['starts_at']) or current) > current:
            return 'pending'
        result = connection.execute(
            """SELECT home_score,away_score,status,settled_at FROM sports_ai_results WHERE fixture_id=?""",
            (leg['fixture_id'],),
        ).fetchone()
        fixture = connection.execute('SELECT status FROM sports_ai_fixtures WHERE id=?', (leg['fixture_id'],)).fetchone()
        if fixture and fixture['status'] in ('cancelled', 'postponed', 'abandoned'):
            leg_result = 'void'
        elif not result or result['status'] != 'final':
            return 'pending'
        else:
            settled_at = _parse_time(result['settled_at'])
            if settled_at and settled_at < _parse_time(leg['starts_at']):
                return 'pending'
            leg_result = settle_result(leg['sport'], leg['market'], result['home_score'], result['away_score'])
            if leg_result is None:
                return 'pending'
        connection.execute(
            'UPDATE sports_ai_accumulator_legs SET result=? WHERE id=?', (leg_result, leg['id']),
        )
        scored.append({**leg, 'result': leg_result})
    outcome, multiple = ticket_outcome(scored)
    if outcome == 'pending':
        return 'pending'
    profit = None if multiple is None else STAKE_ASSUMPTION * (multiple - 1)
    connection.execute(
        """INSERT INTO sports_ai_ticket_settlements
           (id,ticket_id,outcome,payout_multiple,stake_assumption,hypothetical_profit,settled_at,details_json)
           VALUES(?,?,?,?,?,?,?,?)""",
        (
            str(uuid.uuid4()), ticket_id, outcome, multiple, STAKE_ASSUMPTION, profit, current.isoformat(),
            json.dumps({
                'label': HYPOTHETICAL_LABEL,
                'legs': [{'opportunity_id': leg['opportunity_id'], 'result': leg['result']} for leg in scored],
            }),
        ),
    )
    final_status = 'void' if outcome == 'void' else 'settled'
    connection.execute('UPDATE sports_ai_accumulator_tickets SET status=? WHERE id=?', (final_status, ticket_id))
    _notify(connection, 'ticket_settled', {'ticket_id': ticket_id, 'outcome': outcome, 'hypothetical': True})
    return 'settled'


def refresh_performance(connection, now=None):
    current = _now(now)
    rows = connection.execute(
        """SELECT t.tier,t.sport_mode,s.outcome,s.hypothetical_profit,s.stake_assumption
           FROM sports_ai_ticket_settlements s
           JOIN sports_ai_accumulator_tickets t ON t.id=s.ticket_id""",
    ).fetchall()
    grouped = {}
    for row in rows:
        bucket = grouped.setdefault((row['tier'], row['sport_mode']), {
            'settled_count': 0, 'wins': 0, 'losses': 0, 'voids': 0, 'partials': 0,
            'hypothetical_profit': 0.0, 'stake_assumption': 0.0,
        })
        bucket['settled_count'] += 1
        bucket['wins'] += int(row['outcome'] == 'won')
        bucket['losses'] += int(row['outcome'] == 'lost')
        bucket['voids'] += int(row['outcome'] == 'void')
        bucket['partials'] += int(row['outcome'] == 'partial')
        bucket['hypothetical_profit'] += float(row['hypothetical_profit'] or 0)
        bucket['stake_assumption'] += float(row['stake_assumption'] or 0)
    connection.execute('DELETE FROM sports_ai_accumulator_performance')
    for (tier, sport_mode), bucket in grouped.items():
        connection.execute(
            """INSERT INTO sports_ai_accumulator_performance
               (id,tier,sport_mode,settled_count,wins,losses,voids,partials,hypothetical_profit,stake_assumption,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                str(uuid.uuid4()), tier, sport_mode, bucket['settled_count'], bucket['wins'], bucket['losses'],
                bucket['voids'], bucket['partials'], bucket['hypothetical_profit'], bucket['stake_assumption'],
                current.isoformat(),
            ),
        )
    return grouped


def run_backtest(connection, actor, audit, now=None):
    current = _now(now)
    considered = 0
    excluded = 0
    evaluations = []
    tickets = connection.execute('SELECT * FROM sports_ai_accumulator_tickets WHERE leg_count>=2').fetchall()
    for ticket in tickets:
        legs = [dict(row) for row in connection.execute(
            'SELECT * FROM sports_ai_accumulator_legs WHERE ticket_id=?', (ticket['id'],),
        )]
        created = _parse_time(ticket['created_at'])
        ready = []
        leaked = False
        for leg in legs:
            if created and _parse_time(leg['starts_at']) and created > _parse_time(leg['starts_at']):
                leaked = True
                break
            result = connection.execute(
                'SELECT home_score,away_score,status,settled_at FROM sports_ai_results WHERE fixture_id=?',
                (leg['fixture_id'],),
            ).fetchone()
            if not result or result['status'] != 'final':
                leaked = True
                break
            settled_at = _parse_time(result['settled_at'])
            if settled_at and created and settled_at <= created:
                leaked = True
                break
            leg_result = settle_result(leg['sport'], leg['market'], result['home_score'], result['away_score'])
            if leg_result is None:
                leaked = True
                break
            ready.append({**leg, 'result': leg_result})
        considered += 1
        if leaked or len(ready) != len(legs):
            excluded += 1
            continue
        outcome, multiple = ticket_outcome(ready)
        profit = None if multiple is None else STAKE_ASSUMPTION * (multiple - 1)
        evaluations.append({
            'ticket_id': ticket['id'], 'tier': ticket['tier'], 'sport_mode': ticket['sport_mode'],
            'outcome': outcome, 'hypothetical_profit': profit,
        })
    by_tier = {}
    for item in evaluations:
        bucket = by_tier.setdefault(str(item['tier']), {'sample_size': 0, 'wins': 0, 'losses': 0, 'hypothetical_profit': 0.0})
        bucket['sample_size'] += 1
        bucket['wins'] += int(item['outcome'] == 'won')
        bucket['losses'] += int(item['outcome'] == 'lost')
        bucket['hypothetical_profit'] += float(item['hypothetical_profit'] or 0)
    metrics = {
        'sample_size': len(evaluations),
        'excluded': excluded,
        'considered': considered,
        'by_tier': by_tier,
        'hypothetical_profit': sum(float(item['hypothetical_profit'] or 0) for item in evaluations),
        'stake_assumption': STAKE_ASSUMPTION,
        'label': (
            'Walk-forward score of stored tickets using only the odds saved when the ticket was generated '
            'and official results recorded after that decision. Simulated results are not actual betting returns.'
        ),
    }
    backtest_id = str(uuid.uuid4())
    connection.execute(
        """INSERT INTO sports_ai_accumulator_backtests
           (id,created_at,window_json,parameters_json,metrics_json,sample_size) VALUES(?,?,?,?,?,?)""",
        (backtest_id, current.isoformat(), json.dumps({'decision_time': 'ticket.created_at'}), '{}', json.dumps(metrics), len(evaluations)),
    )
    _audit(audit, connection, actor, 'accumulator.backtest', {'id': backtest_id, 'sample_size': len(evaluations)})
    return {'id': backtest_id, **metrics}


def command_centre(connection):
    latest = """
        SELECT t.* FROM sports_ai_accumulator_tickets t
        JOIN (
            SELECT family_id, MAX(version) AS version FROM sports_ai_accumulator_tickets GROUP BY family_id
        ) v ON v.family_id=t.family_id AND v.version=t.version
    """
    tickets = [dict(row) for row in connection.execute(latest)]
    policy = load_tier_policy(connection)
    by_tier = {
        str(item['tier']): {
            'target': item['tier'], 'label': item['label'], 'mode': item['mode'], 'tier_id': item['id'],
            'total': 0, 'qualified': 0, 'unverified_probability': 0, 'expired': 0, 'review_required': 0, 'settled': 0,
        }
        for item in policy['tiers']
    }
    by_mode = {'football': 0, 'basketball': 0, 'mixed': 0}
    counts = {'qualified': 0, 'awaiting_odds': 0, 'expired': 0, 'review_required': 0, 'unverified_probability': 0, 'settled': 0, 'void': 0}
    for ticket in tickets:
        bucket = by_tier.setdefault(str(ticket['tier']), {'target': ticket['tier'], 'total': 0, 'qualified': 0, 'unverified_probability': 0, 'expired': 0, 'review_required': 0, 'settled': 0})
        bucket['total'] += 1
        if ticket['status'] in bucket:
            bucket[ticket['status']] += 1
        by_mode[ticket['sport_mode']] = by_mode.get(ticket['sport_mode'], 0) + 1
        if ticket['status'] in counts:
            counts[ticket['status']] += 1
    run = connection.execute(
        """SELECT id,status,sport_mode,trigger,started_at,finished_at,summary_json
           FROM sports_ai_accumulator_runs ORDER BY started_at DESC LIMIT 1""",
    ).fetchone()
    performance = [dict(row) for row in connection.execute(
        'SELECT * FROM sports_ai_accumulator_performance ORDER BY tier,sport_mode',
    )]
    for row in performance:
        row['success_rate'] = (row['wins'] / row['settled_count']) if row['settled_count'] else None
        row['hypothetical'] = True
    qualified_opportunities = connection.execute(
        "SELECT COUNT(*) FROM sports_ai_value_opportunities WHERE status='qualified'",
    ).fetchone()[0]
    providers = connection.execute(
        "SELECT COUNT(*) FROM sports_ai_provider_registry WHERE enabled=1 AND status='operational'",
    ).fetchone()[0]
    return {
        'by_tier': list(by_tier.values()),
        'by_mode': by_mode,
        **counts,
        'latest_run': None if not run else {**dict(run), 'summary': _loads(run['summary_json'], {})},
        'performance': performance,
        'sources': {
            'qualified_opportunities': qualified_opportunities,
            'operational_providers': providers,
        },
    }
