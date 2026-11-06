"""Central qualification. Tickets and booking codes read these decisions and do not invent a second pass."""

import hashlib
import json
import math
import uuid
from datetime import datetime, timedelta, timezone

from sport_support import GOAL_SPORTS

STATES = ('QUALIFIED', 'MONITORING', 'REJECTED', 'AWAITING_DATA', 'EXPIRED', 'SUSPENDED')
CALIBRATED = {
    'football': {'HOME', 'DRAW', 'AWAY'},
    'frauenfussball': {'HOME', 'DRAW', 'AWAY'},
    'ice_hockey': {'HOME', 'DRAW', 'AWAY'},
    'efootball': {'HOME', 'DRAW', 'AWAY'},
    'vfootball': {'HOME', 'DRAW', 'AWAY'},
    'basketball': {'MONEYLINE_HOME', 'MONEYLINE_AWAY'},
}
OPEN_STATES = ('QUALIFIED', 'MONITORING', 'AWAITING_DATA')


def utc_now():
    return datetime.now(timezone.utc)


def default_policy(engine=None):
    """Thresholds already used by the prediction engine. They are not a confidence score."""
    engine = engine or {}
    edge = float(engine.get('min_edge', 0.05))
    markets = {}
    for sport, names in CALIBRATED.items():
        for name in names:
            markets[f'{sport}:{name}'] = {'min_edge': edge, 'min_expected_value': edge}
    return {
        'version': 1,
        'odds_max_age_minutes': int(engine.get('odds_max_age_minutes', 15)),
        'min_completeness': 0.5,
        'max_calibration_error': 0.20,
        'min_holdout_fixtures': 10,
        'max_uncertainty': 0.97,
        'max_same_team': 2,
        'max_open_qualified': 40,
        'require_lineups': False,
        'risk_review_enabled': True,
        'risk_review_edge': 0.50,
        'risk_review_draw_gap': 0.10,
        'markets': markets,
        'note': 'Edge and calibration limits match the stored engine and model activation gates.',
    }


def _loads(value, fallback):
    try:
        return json.loads(value or '')
    except (TypeError, json.JSONDecodeError):
        return fallback


def _parse(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def load_policy(connection):
    row = connection.execute(
        "SELECT value_json FROM sports_ai_settings WHERE key='qualification_policy'",
    ).fetchone()
    if row:
        stored = _loads(row['value_json'], {})
        if stored.get('markets'):
            return stored
    engine_row = connection.execute(
        "SELECT value_json FROM sports_ai_settings WHERE key='engine'",
    ).fetchone()
    policy = default_policy(_loads(engine_row['value_json'], {}) if engine_row else {})
    _write_policy(connection, policy, 'system')
    return policy


def _write_policy(connection, policy, actor):
    moment = utc_now().isoformat()
    connection.execute(
        """INSERT INTO sports_ai_settings(key,value_json,updated_at) VALUES('qualification_policy',?,?)
           ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at""",
        (json.dumps(policy), moment),
    )
    connection.execute(
        """INSERT INTO sports_ai_qualification_policies(id,version,policy_json,actor,created_at)
           VALUES(?,?,?,?,?)""",
        (str(uuid.uuid4()), int(policy['version']), json.dumps(policy), actor, moment),
    )


def save_policy(connection, updates, actor):
    current = load_policy(connection)
    merged = dict(current)
    for key in (
        'odds_max_age_minutes', 'min_completeness', 'max_calibration_error',
        'min_holdout_fixtures', 'max_uncertainty', 'max_same_team', 'max_open_qualified',
        'risk_review_enabled', 'risk_review_edge', 'risk_review_draw_gap',
    ):
        if key in updates and updates[key] is not None:
            merged[key] = updates[key]
    if updates.get('markets'):
        markets = dict(merged.get('markets') or {})
        for name, limits in updates['markets'].items():
            if name not in markets:
                continue
            markets[name] = {
                'min_edge': float(limits.get('min_edge', markets[name]['min_edge'])),
                'min_expected_value': float(limits.get('min_expected_value', markets[name]['min_expected_value'])),
            }
        merged['markets'] = markets
    merged['version'] = int(current.get('version') or 1) + 1
    _validate_policy(merged)
    _write_policy(connection, merged, actor)
    return merged


def _validate_policy(policy):
    if not 1 <= int(policy['odds_max_age_minutes']) <= 180:
        raise ValueError('Odds freshness must be between 1 and 180 minutes')
    if not 0 <= float(policy['min_completeness']) <= 1:
        raise ValueError('Completeness threshold must be between 0 and 1')
    if not 0 < float(policy['max_calibration_error']) <= 1:
        raise ValueError('Calibration ceiling must be between 0 and 1')
    if not 1 <= int(policy['max_same_team']) <= 10:
        raise ValueError('Team exposure limit must be between 1 and 10')
    if not 1 <= int(policy['max_open_qualified']) <= 200:
        raise ValueError('Open qualification limit must be between 1 and 200')
    for limits in policy['markets'].values():
        if float(limits['min_edge']) < 0 or float(limits['min_expected_value']) < 0:
            raise ValueError('Market edge thresholds cannot be negative')
    if 'risk_review_edge' in policy and not 0.05 <= float(policy['risk_review_edge']) <= 5:
        raise ValueError('Risk-review edge must stay between 0.05 and 5')
    if 'risk_review_draw_gap' in policy and not 0.02 <= float(policy['risk_review_draw_gap']) <= 0.50:
        raise ValueError('Risk-review draw gap must stay between 0.02 and 0.50')


def binary_entropy(probability):
    bounded = min(1 - 1e-6, max(1e-6, float(probability)))
    return -(bounded * math.log(bounded) + (1 - bounded) * math.log(1 - bounded)) / math.log(2)


def _metrics(raw):
    return _loads(raw, {}) if not isinstance(raw, dict) else raw


def _lineup(features):
    if not isinstance(features, dict):
        return None
    home = features.get('lineup_known_home')
    away = features.get('lineup_known_away')
    if home is None and away is None:
        return None
    return [home, away]


def decide(row, policy, now=None):
    """One selection. Missing prices and model evidence stay missing."""
    current = now or utc_now()
    features = row.get('features') if isinstance(row.get('features'), dict) else {}
    metrics = _metrics(row.get('metrics'))
    evidence = {
        'probability': row.get('probability'),
        'implied_probability': None,
        'expected_edge': row.get('expected_edge'),
        'expected_value': None,
        'uncertainty': None,
        'calibration_error': metrics.get('calibration_error'),
        'holdout_fixtures': metrics.get('holdout_fixtures'),
        'brier_score': metrics.get('brier_score'),
        'log_loss': metrics.get('log_loss'),
        'model_lifecycle': row.get('lifecycle'),
        'model_version': row.get('model_version'),
        'completeness': row.get('completeness'),
        'odds_age_minutes': None,
        'lineups': _lineup(features),
        'missing': [],
    }
    market = str(row.get('market') or '').upper()
    sport = row.get('sport')
    kickoff = _parse(row.get('starts_at'))
    threshold = (policy.get('markets') or {}).get(f'{sport}:{market}') or {}

    def finish(state, code, reason, rank=None):
        products = []
        if state == 'QUALIFIED' and kickoff:
            products.append('booking')
            if sport in GOAL_SPORTS and market == 'DRAW' and kickoff <= current + timedelta(days=7):
                products.append('weekly_draw')
            if kickoff.date() == current.date():
                products.append('daily_rollover')
        expires = None
        if kickoff:
            expires = kickoff
        observed = _parse(row.get('odds_observed_at'))
        if observed and state in OPEN_STATES:
            stale_at = observed + timedelta(minutes=int(policy['odds_max_age_minutes']))
            expires = stale_at if expires is None or stale_at < expires else expires
        return {
            'state': state,
            'reason_code': code,
            'reason': reason,
            'evidence': evidence,
            'products': products,
            'rank_score': rank,
            'expires_at': None if expires is None else expires.isoformat(),
        }

    if not row.get('fixture_id') or not row.get('home') or not row.get('away'):
        evidence['missing'].append('fixture_identity')
        return finish('REJECTED', 'FIXTURE_IDENTITY_MISSING', 'Fixture identity is incomplete.')
    if sport not in CALIBRATED:
        return finish('REJECTED', 'SPORT_NOT_MODELLED', f'No trained model exists for {sport}.')
    if row.get('quality_hold'):
        return finish('SUSPENDED', 'QUALITY_HOLD', 'The fixture has an unresolved high or critical quality issue.')
    if not kickoff:
        evidence['missing'].append('kickoff')
        return finish('AWAITING_DATA', 'FIXTURE_IDENTITY_MISSING', 'Fixture kickoff is not stored.')
    if str(row.get('fixture_status') or '') not in ('scheduled', 'not_started'):
        if kickoff <= current:
            return finish('EXPIRED', 'FIXTURE_STARTED', 'The fixture is no longer scheduled.')
        return finish('REJECTED', 'FIXTURE_NOT_SCHEDULED', 'The fixture is not in a scheduled state.')
    if kickoff <= current:
        return finish('EXPIRED', 'FIXTURE_STARTED', 'The fixture has started.')
    lifecycle = row.get('lifecycle') or ''
    if lifecycle != 'active' or int(row.get('model_active') or 0) != 1:
        return finish('SUSPENDED', 'MODEL_INACTIVE', 'The model is not the active version.')
    calibration_error = metrics.get('calibration_error')
    holdout = int(metrics.get('holdout_fixtures') or 0)
    if calibration_error is None or holdout < int(policy['min_holdout_fixtures']) or row.get('calibration_status') == 'uncalibrated':
        evidence['missing'].append('model_calibration')
        return finish('SUSPENDED', 'MODEL_UNCALIBRATED', 'The active model has no usable holdout calibration.')
    if float(calibration_error) > float(policy['max_calibration_error']):
        return finish('SUSPENDED', 'MODEL_DETERIORATING', 'Stored holdout calibration error is above the activation ceiling.')
    if market not in CALIBRATED[sport] and not row.get('market_registry_active'):
        evidence['missing'].append('market_calibration')
        return finish('AWAITING_DATA', 'MARKET_NOT_CALIBRATED', 'This market has no holdout calibration, so it cannot qualify.')
    try:
        probability = float(row.get('probability'))
    except (TypeError, ValueError):
        evidence['missing'].append('probability')
        return finish('AWAITING_DATA', 'PROBABILITY_MISSING', 'No stored model probability exists for this market.')
    if not math.isfinite(probability) or probability <= 0 or probability >= 1:
        return finish('AWAITING_DATA', 'PROBABILITY_MISSING', 'The stored probability is outside the open unit interval.')
    evidence['uncertainty'] = round(binary_entropy(probability), 4)
    if policy.get('require_lineups') and evidence['lineups'] is None:
        evidence['missing'].append('lineups')
        return finish('AWAITING_DATA', 'LINEUP_UNAVAILABLE', 'Lineups were required by policy and no lineup is stored.')
    try:
        odds = float(row.get('decimal_odds'))
    except (TypeError, ValueError):
        evidence['missing'].append('odds')
        return finish('AWAITING_DATA', 'MARKET_UNAVAILABLE', 'No current bookmaker price is stored for this market.')
    if not math.isfinite(odds) or odds <= 1:
        return finish('AWAITING_DATA', 'MARKET_UNAVAILABLE', 'The stored price is not a decimal odds value above 1.')
    observed = _parse(row.get('odds_observed_at'))
    if not observed:
        evidence['missing'].append('odds_timestamp')
        return finish('AWAITING_DATA', 'MARKET_UNAVAILABLE', 'The bookmaker price has no observation time.')
    age = (current - observed).total_seconds() / 60
    evidence['odds_age_minutes'] = round(age, 2)
    if age < -5:
        return finish('REJECTED', 'ODDS_TIMESTAMP_INVALID', 'The bookmaker price timestamp is in the future.')
    if age > float(policy['odds_max_age_minutes']):
        return finish('EXPIRED', 'ODDS_STALE', 'The bookmaker price is older than the configured freshness window.')
    implied = 1 / odds
    evidence['implied_probability'] = round(implied, 6)
    completeness = row.get('completeness')
    if completeness is None:
        evidence['missing'].append('completeness')
        return finish('AWAITING_DATA', 'DATA_INCOMPLETE', 'Feature completeness was not stored.')
    if float(completeness) < float(policy['min_completeness']):
        return finish('REJECTED', 'DATA_INCOMPLETE', 'Stored feature completeness is below the engine threshold of 0.50.')
    edge = row.get('expected_edge')
    if edge is None:
        edge = probability * odds - 1
    try:
        edge = float(edge)
    except (TypeError, ValueError):
        evidence['missing'].append('expected_value')
        return finish('AWAITING_DATA', 'EXPECTED_VALUE_MISSING', 'Expected value cannot be calculated from the stored probability and price.')
    if not math.isfinite(edge):
        return finish('AWAITING_DATA', 'EXPECTED_VALUE_MISSING', 'Expected value is not a finite number.')
    evidence['expected_edge'] = round(edge, 6)
    evidence['expected_value'] = round(edge, 6)
    minimum = float(threshold.get('min_edge', policy['markets'].get(f'{sport}:{market}', {}).get('min_edge', 0.05)))
    evidence['market_min_edge'] = minimum
    if edge < 0:
        return finish('REJECTED', 'EDGE_BELOW_MARKET', 'Estimated edge is negative at the stored price.')
    if edge < minimum:
        return finish('MONITORING', 'EDGE_BELOW_MARKET', 'Estimated edge is positive and below the market threshold.', edge)
    if evidence['uncertainty'] > float(policy['max_uncertainty']):
        return finish('MONITORING', 'UNCERTAINTY_HIGH', 'The stored probability is near even, so the selection stays in monitoring.', edge)
    if _needs_risk_review(policy, market, probability, implied, edge) and not row.get('risk_review_approved'):
        evidence['risk_review'] = True
        return finish(
            'MONITORING', 'RISK_REVIEW',
            'The estimated edge or draw gap is large enough to require review before this selection can qualify.',
            edge,
        )
    return finish('QUALIFIED', 'QUALIFIED_OK', 'Fixture, calibrated probability, fresh price, edge and model evidence passed the market threshold.', edge)


def _needs_risk_review(policy, market, probability, implied, edge):
    if not policy.get('risk_review_enabled', True):
        return False
    if edge >= float(policy.get('risk_review_edge', 0.50)):
        return True
    return market == 'DRAW' and (probability - implied) >= float(policy.get('risk_review_draw_gap', 0.10))


def _fingerprint(row, policy_version):
    features = row.get('features') if isinstance(row.get('features'), dict) else {}
    payload = {
        'prediction': row.get('prediction_id'),
        'probability': row.get('probability'),
        'model': row.get('model_id'),
        'lifecycle': row.get('lifecycle'),
        'calibration_error': _metrics(row.get('metrics')).get('calibration_error'),
        'odds': row.get('decimal_odds'),
        'observed': row.get('odds_observed_at'),
        'status': row.get('fixture_status'),
        'starts': row.get('starts_at'),
        'completeness': row.get('completeness'),
        'lineups': _lineup(features),
        'quality': bool(row.get('quality_hold')),
        'risk_review': bool(row.get('risk_review_approved')),
        'market_registry': bool(row.get('market_registry_active')),
        'policy': policy_version,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _apply_limits(decisions, policy):
    qualified = [item for item in decisions if item['result']['state'] == 'QUALIFIED']
    qualified.sort(key=lambda item: (-(item['result']['rank_score'] or 0), item['row'].get('starts_at') or ''))
    kept = []
    fixtures = set()
    teams = {}
    for item in qualified:
        row = item['row']
        if row.get('fixture_id') in fixtures:
            _demote(item, 'CORRELATION_LIMIT', 'Another market from this fixture already qualified. No joint model is stored.')
            continue
        names = [row.get('home'), row.get('away')]
        if any(teams.get(name, 0) >= int(policy['max_same_team']) for name in names):
            _demote(item, 'CORRELATION_LIMIT', 'This team already has the configured number of open qualified selections.')
            continue
        if len(kept) >= int(policy['max_open_qualified']):
            _demote(item, 'RISK_LIMIT', 'The configured limit of open qualified selections is already reached.')
            continue
        fixtures.add(row.get('fixture_id'))
        for name in names:
            teams[name] = teams.get(name, 0) + 1
        kept.append(item)
    return decisions


def _demote(item, code, reason):
    item['result']['state'] = 'MONITORING'
    item['result']['reason_code'] = code
    item['result']['reason'] = reason
    item['result']['products'] = []


def qualified_decision_rows(connection, now):
    """Every current QUALIFIED selection, including those outside the kickoff batch."""
    moment = now.isoformat()
    return connection.execute(
        """SELECT p.id AS prediction_id, p.market, p.probability, p.explanation_json,
                  f.id AS fixture_id, f.sport, f.competition, f.home, f.away, f.starts_at, f.status AS fixture_status,
                  m.id AS model_id, m.lifecycle, m.active AS model_active, m.version AS model_version,
                  m.metrics_json, m.calibration_status,
                  o.id AS opportunity_id, o.expected_edge,
                  COALESCE(o.data_completeness, s.completeness) AS completeness,
                  q.decimal_odds, q.observed_at AS odds_observed_at,
                  EXISTS (
                    SELECT 1 FROM sports_ai_data_quality_issues i
                    WHERE i.fixture_id=f.id AND i.resolved_at IS NULL AND i.severity IN ('high','critical')
                  ) AS quality_hold,
                  EXISTS (
                    SELECT 1 FROM sports_ai_prediction_reviews rv
                    WHERE rv.prediction_id=p.id AND rv.reason_code='RISK_REVIEW_APPROVED' AND rv.disposition='approved'
                  ) AS risk_review_approved,
                  EXISTS (
                    SELECT 1 FROM sports_ai_market_registry r
                    WHERE r.sport=f.sport AND r.market_key=p.market AND r.status='active'
                  ) AS market_registry_active
           FROM sports_ai_qualification_decisions d
           JOIN sports_ai_engine_predictions p ON p.id=d.prediction_id AND p.market=d.market
           JOIN sports_ai_fixtures f ON f.id=d.fixture_id
           JOIN sports_ai_model_registry m ON m.id=p.model_id
           LEFT JOIN sports_ai_feature_snapshots s ON s.id=p.feature_id
           LEFT JOIN sports_ai_value_opportunities o ON o.prediction_id=p.id
             AND o.evaluated_at=(
               SELECT MAX(o2.evaluated_at) FROM sports_ai_value_opportunities o2 WHERE o2.prediction_id=p.id
             )
           LEFT JOIN sports_ai_odds_snapshots q ON q.id=o.odds_id
           WHERE d.state='QUALIFIED' AND f.starts_at>?""",
        (moment,),
    ).fetchall()


def candidate_rows(connection, now, limit=100):
    """Each modelled sport gets its own share, so football kickoffs do not crowd the others out."""
    moment = now.isoformat()
    per_sport = max(1, min(int(limit), 100))
    return connection.execute(
        """SELECT * FROM (
           SELECT p.id AS prediction_id, p.market, p.probability, p.explanation_json,
                  f.id AS fixture_id, f.sport, f.competition, f.home, f.away, f.starts_at, f.status AS fixture_status,
                  m.id AS model_id, m.lifecycle, m.active AS model_active, m.version AS model_version,
                  m.metrics_json, m.calibration_status,
                  o.id AS opportunity_id, o.expected_edge,
                  COALESCE(o.data_completeness, s.completeness) AS completeness,
                  q.decimal_odds, q.observed_at AS odds_observed_at,
                  EXISTS (
                    SELECT 1 FROM sports_ai_data_quality_issues i
                    WHERE i.fixture_id=f.id AND i.resolved_at IS NULL AND i.severity IN ('high','critical')
                  ) AS quality_hold,
                  EXISTS (
                    SELECT 1 FROM sports_ai_prediction_reviews rv
                    WHERE rv.prediction_id=p.id AND rv.reason_code='RISK_REVIEW_APPROVED' AND rv.disposition='approved'
                  ) AS risk_review_approved,
                  EXISTS (
                    SELECT 1 FROM sports_ai_market_registry r
                    WHERE r.sport=f.sport AND r.market_key=p.market AND r.status='active'
                  ) AS market_registry_active,
                  ROW_NUMBER() OVER (
                    PARTITION BY f.sport
                    ORDER BY CASE WHEN EXISTS (
                      SELECT 1 FROM sports_ai_qualification_decisions d
                      WHERE d.prediction_id=p.id AND d.market=p.market AND d.state='QUALIFIED'
                    ) THEN 0 ELSE 1 END, f.starts_at
                  ) AS sport_rank
           FROM sports_ai_engine_predictions p
           JOIN sports_ai_fixtures f ON f.id=p.fixture_id
           JOIN sports_ai_model_registry m ON m.id=p.model_id
           LEFT JOIN sports_ai_feature_snapshots s ON s.id=p.feature_id
           LEFT JOIN sports_ai_value_opportunities o ON o.prediction_id=p.id
             AND o.evaluated_at=(
               SELECT MAX(o2.evaluated_at) FROM sports_ai_value_opportunities o2 WHERE o2.prediction_id=p.id
             )
           LEFT JOIN sports_ai_odds_snapshots q ON q.id=o.odds_id
           WHERE f.status='scheduled' AND f.starts_at>?
             AND EXISTS (
               SELECT 1 FROM sports_ai_sports s
               WHERE s.code=f.sport AND s.prediction_status='modelled'
             )
             AND (
               (f.sport IN ('football','frauenfussball','ice_hockey') AND p.market IN ('HOME','DRAW','AWAY'))
               OR (f.sport='basketball' AND p.market IN ('MONEYLINE_HOME','MONEYLINE_AWAY'))
               OR EXISTS (
                 SELECT 1 FROM sports_ai_market_registry r
                 WHERE r.sport=f.sport AND r.market_key=p.market AND r.status='active'
               )
             )
             AND p.created_at=(
               SELECT MAX(p2.created_at) FROM sports_ai_engine_predictions p2
               WHERE p2.fixture_id=p.fixture_id AND p2.market=p.market
             )
           ) ranked
           WHERE ranked.sport_rank<=?
           ORDER BY ranked.sport_rank, ranked.starts_at""",
        (moment, per_sport),
    ).fetchall()


def _row_from_sql(row):
    item = dict(row)
    explanation = _loads(item.get('explanation_json'), {})
    item['features'] = explanation.get('raw_features') or explanation.get('features') or {}
    item['metrics'] = _loads(item.get('metrics_json'), {})
    item['quality_hold'] = bool(item.get('quality_hold'))
    item['risk_review_approved'] = bool(item.get('risk_review_approved'))
    item['market_registry_active'] = bool(item.get('market_registry_active'))
    return item


def _persist(connection, row, result, fingerprint, policy_version, actor, now):
    moment = now.isoformat()
    existing = connection.execute(
        """SELECT id, state, reason_code, fingerprint, decision_version
           FROM sports_ai_qualification_decisions
           WHERE prediction_id=? AND market=?""",
        (row['prediction_id'], row['market']),
    ).fetchone()
    if existing and existing['fingerprint'] == fingerprint and existing['state'] == result['state'] and existing['reason_code'] == result['reason_code']:
        if row.get('opportunity_id'):
            connection.execute(
                """UPDATE sports_ai_qualification_decisions
                   SET opportunity_id=?
                   WHERE id=? AND (opportunity_id IS NULL OR opportunity_id!=?)""",
                (row['opportunity_id'], existing['id'], row['opportunity_id']),
            )
        return 'unchanged', existing['id']
    version = 1 if not existing else int(existing['decision_version']) + 1
    decision_id = existing['id'] if existing else str(uuid.uuid4())
    payload = (
        result['state'], result['reason_code'], result['reason'], json.dumps(result['evidence']),
        json.dumps(result['products']), result['rank_score'], policy_version, version, fingerprint,
        row.get('starts_at'), result['expires_at'], moment, decision_id,
    )
    if existing:
        connection.execute(
            """UPDATE sports_ai_qualification_decisions
               SET state=?, reason_code=?, reason=?, evidence_json=?, products_json=?, rank_score=?,
                   policy_version=?, decision_version=?, fingerprint=?, starts_at=?, expires_at=?, updated_at=?,
                   opportunity_id=COALESCE(?, opportunity_id)
               WHERE id=?""",
            (*payload[:-1], row.get('opportunity_id'), payload[-1]),
        )
    else:
        connection.execute(
            """INSERT INTO sports_ai_qualification_decisions
               (state,reason_code,reason,evidence_json,products_json,rank_score,policy_version,decision_version,
                fingerprint,starts_at,expires_at,updated_at,id,prediction_id,opportunity_id,fixture_id,sport,
                competition,market,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (*payload, row['prediction_id'], row.get('opportunity_id'), row['fixture_id'], row['sport'],
             row.get('competition') or '', row['market'], moment),
        )
    connection.execute(
        """INSERT INTO sports_ai_qualification_history
           (id,decision_id,prediction_id,fixture_id,market,state,reason_code,reason,evidence_json,products_json,
            policy_version,decision_version,fingerprint,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (str(uuid.uuid4()), decision_id, row['prediction_id'], row['fixture_id'], row['market'],
         result['state'], result['reason_code'], result['reason'], json.dumps(result['evidence']),
         json.dumps(result['products']), policy_version, version, fingerprint, moment),
    )
    previous = existing['state'] if existing else None
    if previous != result['state'] and (previous == 'QUALIFIED' or result['state'] in ('SUSPENDED', 'EXPIRED', 'REJECTED')):
        connection.execute(
            """INSERT INTO sports_ai_notifications(id,channel,event_type,payload_json,status)
               VALUES(?,?,?,?,?)""",
            (str(uuid.uuid4()), 'dashboard', 'qualification.changed', json.dumps({
                'decision_id': decision_id, 'fixture_id': row['fixture_id'], 'market': row['market'],
                'from': previous, 'state': result['state'], 'reason_code': result['reason_code'],
            }), 'stored'),
        )
    connection.execute(
        """INSERT INTO sports_ai_audit_events(id,actor,action,metadata_json)
           VALUES(?,?,?,?)""",
        (str(uuid.uuid4()), actor, 'qualification.decide', json.dumps({
            'decision_id': decision_id, 'state': result['state'], 'reason_code': result['reason_code'],
            'prediction_id': row['prediction_id'], 'version': version,
        })),
    )
    return 'changed', decision_id


def expire_started(connection, actor, now=None):
    current = now or utc_now()
    rows = connection.execute(
        """SELECT * FROM sports_ai_qualification_decisions
           WHERE state IN ('QUALIFIED','MONITORING','AWAITING_DATA') AND starts_at<=?""",
        (current.isoformat(),),
    ).fetchall()
    expired = 0
    for row in rows:
        result = {
            'state': 'EXPIRED', 'reason_code': 'FIXTURE_STARTED',
            'reason': 'The fixture has started.',
            'evidence': _loads(row['evidence_json'], {}),
            'products': [], 'rank_score': row['rank_score'], 'expires_at': row['expires_at'],
        }
        _persist(connection, dict(row), result, row['fingerprint'] + ':expired', row['policy_version'], actor, current)
        expired += 1
    return expired


def rows_for_fixtures(connection, now, fixture_ids):
    """Latest forecasts for fixtures that just received a verified price."""
    if not fixture_ids:
        return []
    moment = now.isoformat()
    marks = ','.join('?' for _ in fixture_ids)
    return connection.execute(
        f"""SELECT p.id AS prediction_id, p.market, p.probability, p.explanation_json,
                   f.id AS fixture_id, f.sport, f.competition, f.home, f.away, f.starts_at, f.status AS fixture_status,
                   m.id AS model_id, m.lifecycle, m.active AS model_active, m.version AS model_version,
                   m.metrics_json, m.calibration_status,
                   o.id AS opportunity_id, o.expected_edge,
                   COALESCE(o.data_completeness, s.completeness) AS completeness,
                   q.decimal_odds, q.observed_at AS odds_observed_at,
                   EXISTS (
                     SELECT 1 FROM sports_ai_data_quality_issues i
                     WHERE i.fixture_id=f.id AND i.resolved_at IS NULL AND i.severity IN ('high','critical')
                   ) AS quality_hold,
                  EXISTS (
                    SELECT 1 FROM sports_ai_prediction_reviews rv
                    WHERE rv.prediction_id=p.id AND rv.reason_code='RISK_REVIEW_APPROVED' AND rv.disposition='approved'
                  ) AS risk_review_approved,
                  EXISTS (
                    SELECT 1 FROM sports_ai_market_registry r
                    WHERE r.sport=f.sport AND r.market_key=p.market AND r.status='active'
                  ) AS market_registry_active
            FROM sports_ai_engine_predictions p
            JOIN sports_ai_fixtures f ON f.id=p.fixture_id
            JOIN sports_ai_model_registry m ON m.id=p.model_id
            LEFT JOIN sports_ai_feature_snapshots s ON s.id=p.feature_id
            LEFT JOIN sports_ai_value_opportunities o ON o.prediction_id=p.id
              AND o.evaluated_at=(
                SELECT MAX(o2.evaluated_at) FROM sports_ai_value_opportunities o2 WHERE o2.prediction_id=p.id
              )
            LEFT JOIN sports_ai_odds_snapshots q ON q.id=o.odds_id
            WHERE f.id IN ({marks}) AND f.starts_at>?
              AND p.created_at=(
                SELECT MAX(p2.created_at) FROM sports_ai_engine_predictions p2
                WHERE p2.fixture_id=p.fixture_id AND p2.market=p.market
              )""",
        (*fixture_ids, moment),
    ).fetchall()


def revalidate(connection, actor='sports-ai-worker', now=None, limit=400, fixture_ids=None):
    current = now or utc_now()
    from suitability import sync_lifecycles
    sync_lifecycles(connection)
    policy = load_policy(connection)
    expired = expire_started(connection, actor, current)
    prepared = []
    seen = set()
    sources = list(qualified_decision_rows(connection, current))
    sources.extend(rows_for_fixtures(connection, current, fixture_ids or []))
    if limit:
        sources.extend(candidate_rows(connection, current, limit))
    for raw in sources:
        key = (raw['prediction_id'], raw['market'])
        if key in seen:
            continue
        seen.add(key)
        row = _row_from_sql(raw)
        prepared.append({
            'row': row,
            'result': decide(row, policy, current),
            'fingerprint': _fingerprint(row, policy['version']),
        })
    _apply_limits(prepared, policy)
    changed = 0
    counts = {state: 0 for state in STATES}
    for item in prepared:
        outcome, _decision_id = _persist(
            connection, item['row'], item['result'], item['fingerprint'], policy['version'], actor, current,
        )
        if outcome == 'changed':
            changed += 1
        counts[item['result']['state']] += 1
    summary = {
        'checked': len(prepared), 'changed': changed, 'expired': expired,
        'counts': counts, 'policy_version': policy['version'], 'evaluated_at': current.isoformat(),
    }
    connection.execute(
        """INSERT INTO sports_ai_settings(key,value_json,updated_at) VALUES('qualification_status',?,?)
           ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at""",
        (json.dumps(summary), current.isoformat()),
    )
    return summary


def selection_is_qualified(connection, fixture_id, market):
    row = connection.execute(
        """SELECT 1 FROM sports_ai_qualification_decisions
           WHERE fixture_id=? AND upper(market)=upper(?) AND state='QUALIFIED'
             AND EXISTS (SELECT 1 FROM json_each(products_json) WHERE value='booking')
           LIMIT 1""",
        (fixture_id, market),
    ).fetchone()
    return row is not None


def require_bookable(connection, selections):
    if not selections:
        raise ValueError('Booking requires a current QUALIFIED decision for every selection')
    for item in selections:
        if not selection_is_qualified(connection, item.get('fixture_id'), item.get('market')):
            raise ValueError('Booking requires a current QUALIFIED decision for every selection')


def ticket_is_bookable(connection, ticket_id, table='sports_ai_accumulator_legs'):
    if table not in ('sports_ai_accumulator_legs', 'sports_ai_ticket_legs'):
        raise ValueError('Unknown ticket leg table')
    legs = connection.execute(
        f'SELECT fixture_id, market, decimal_odds FROM {table} WHERE ticket_id=?',
        (ticket_id,),
    ).fetchall()
    if not legs:
        return False
    for leg in legs:
        try:
            odds = float(leg['decimal_odds'])
        except (TypeError, ValueError):
            return False
        if not math.isfinite(odds) or odds < 1.3:
            return False
    return all(selection_is_qualified(connection, leg['fixture_id'], leg['market']) for leg in legs)


def list_decisions(connection, state=None, sport=None, market=None, competition=None, product=None, page=1, page_size=25):
    filters, args = [], []
    if state:
        filters.append('d.state=?')
        args.append(state)
    if sport:
        filters.append('d.sport=?')
        args.append(sport)
    if market:
        filters.append('upper(d.market)=upper(?)')
        args.append(market)
    if competition:
        filters.append('d.competition=?')
        args.append(competition[:120])
    if product:
        filters.append("EXISTS (SELECT 1 FROM json_each(d.products_json) WHERE value=?)")
        args.append(product)
    if product == 'weekly_draw':
        filters.append("""EXISTS (
            SELECT 1 FROM sports_ai_engine_predictions p
            WHERE p.id=d.prediction_id AND p.probability>0.60
        )""")
    else:
        filters.append("""(
            d.state!='QUALIFIED'
            OR d.id IN (
                SELECT picked.id FROM (
                    SELECT d2.id, ROW_NUMBER() OVER (
                        PARTITION BY d2.fixture_id ORDER BY p.probability DESC
                    ) AS rn
                    FROM sports_ai_qualification_decisions d2
                    JOIN sports_ai_engine_predictions p ON p.id=d2.prediction_id
                    WHERE d2.state='QUALIFIED' AND p.probability>0.80
                ) picked WHERE picked.rn=1
            )
        )""")
    where = (' WHERE ' + ' AND '.join(filters)) if filters else ''
    total = connection.execute(
        f'SELECT COUNT(*) FROM sports_ai_qualification_decisions d{where}', args,
    ).fetchone()[0]
    rows = connection.execute(
        f"""SELECT d.*, f.home, f.away
            FROM sports_ai_qualification_decisions d
            LEFT JOIN sports_ai_fixtures f ON f.id=d.fixture_id
            {where} ORDER BY CASE d.state WHEN 'QUALIFIED' THEN 0 WHEN 'MONITORING' THEN 1 ELSE 2 END,
                    d.rank_score DESC, d.starts_at
            LIMIT ? OFFSET ?""",
        [*args, page_size, (page - 1) * page_size],
    ).fetchall()
    return {'items': [_public(row) for row in rows], 'total': total, 'page': page, 'page_size': page_size}


def _public(row):
    item = dict(row)
    item['evidence'] = _loads(item.pop('evidence_json', None), {})
    item['products'] = _loads(item.pop('products_json', None), [])
    item.pop('fingerprint', None)
    return item


def decision_detail(connection, decision_id):
    row = connection.execute(
        """SELECT d.*, f.home, f.away FROM sports_ai_qualification_decisions d
           LEFT JOIN sports_ai_fixtures f ON f.id=d.fixture_id WHERE d.id=?""",
        (decision_id,),
    ).fetchone()
    if not row:
        return None
    history = connection.execute(
        """SELECT state, reason_code, reason, decision_version, policy_version, created_at
           FROM sports_ai_qualification_history WHERE decision_id=? ORDER BY decision_version""",
        (decision_id,),
    ).fetchall()
    detail = _public(row)
    detail['history'] = [dict(item) for item in history]
    return detail


def summary(connection):
    counts = {state: 0 for state in STATES}
    for row in connection.execute(
        'SELECT state, COUNT(*) AS total FROM sports_ai_qualification_decisions GROUP BY state',
    ):
        counts[row['state']] = row['total']
    status = connection.execute(
        "SELECT value_json FROM sports_ai_settings WHERE key='qualification_status'",
    ).fetchone()
    alerts = connection.execute(
        """SELECT id, payload_json, created_at FROM sports_ai_notifications
           WHERE event_type='qualification.changed' ORDER BY created_at DESC LIMIT 8""",
    ).fetchall()
    return {
        'counts': counts,
        'last_run': _loads(status['value_json'], {}) if status else None,
        'alerts': [_loads(row['payload_json'], {}) | {'id': row['id'], 'created_at': row['created_at']} for row in alerts],
        'places_wager': False,
    }


def model_health(connection):
    from suitability import sync_lifecycles
    sync_lifecycles(connection)
    rows = []
    for row in connection.execute(
        """SELECT id, sport, model_name, version, lifecycle, status, active, metrics_json
           FROM sports_ai_model_registry ORDER BY sport, created_at DESC""",
    ):
        metrics = _metrics(row['metrics_json'])
        rows.append({
            'id': row['id'], 'sport': row['sport'], 'model_name': row['model_name'],
            'version': row['version'], 'lifecycle': row['lifecycle'], 'registry_status': row['status'],
            'active': bool(row['active']),
            'calibration_error': metrics.get('calibration_error'),
            'holdout_fixtures': metrics.get('holdout_fixtures'),
            'brier_score': metrics.get('brier_score'),
            'blocks_qualification': row['lifecycle'] != 'active' or (
                metrics.get('calibration_error') is not None and float(metrics['calibration_error']) > 0.20
            ),
        })
    return {'models': rows}
