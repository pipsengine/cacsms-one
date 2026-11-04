"""Market suitability and model lifecycle. Probabilities stay in the existing sport models."""

import json
import uuid
from datetime import datetime, timezone

CORE_RATES = {
    'football': ('expected_home_goals', 'expected_away_goals'),
    'basketball': ('expected_home_points', 'expected_away_points'),
}
CALIBRATED_MARKETS = {
    'football': {'HOME', 'DRAW', 'AWAY'},
    'basketball': {'MONEYLINE_HOME', 'MONEYLINE_AWAY'},
}
CONTEXT_FIELDS = {
    'rest': ('home_rest_days', 'away_rest_days'),
    'injuries': ('injuries_out_home', 'injuries_out_away'),
    'lineups': ('lineup_known_home', 'lineup_known_away'),
}
MODELLED_SPORTS = ('football', 'basketball')


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def sync_lifecycles(connection):
    """Derive Research, Validated, Active and Suspended from the registry row. Does not promote a model."""
    connection.execute(
        """UPDATE sports_ai_model_registry SET lifecycle=CASE
             WHEN active=1 AND status='active' THEN 'active'
             WHEN status='validated' THEN 'validated'
             WHEN status IN ('inactive','rejected') THEN 'suspended'
             ELSE 'research' END""",
    )


def assess_market(sport, market, features, lifecycle, calibrated, completeness=None):
    """Rank a stored prediction. Missing injuries, lineups or rest stay missing."""
    features = features or {}
    reasons = []
    missing = []
    if sport not in CORE_RATES:
        return {
            'suitable': 0, 'booking_ready': 0, 'rank_score': 0.0,
            'reasons': [f'No trained model exists for {sport}. No probability was produced.'],
            'missing': ['model'],
        }
    score = 0.0
    if all(features.get(key) is not None for key in CORE_RATES[sport]):
        score += 40
        reasons.append('Stored scoring rates are present.')
    else:
        missing.append('scoring_rates')
        reasons.append('Scoring rates are missing, so the market is not suitable.')
    if calibrated or market in CALIBRATED_MARKETS.get(sport, ()):
        if calibrated and market in CALIBRATED_MARKETS.get(sport, ()):
            score += 25
            reasons.append('This market uses the holdout temperature calibration.')
        elif calibrated:
            score += 25
            reasons.append('This market passed its own holdout calibration in the market registry.')
        else:
            reasons.append('This market has no market-specific holdout calibration.')
    else:
        reasons.append('This market has no market-specific holdout calibration.')
    if lifecycle == 'active':
        score += 20
    elif lifecycle == 'validated':
        score += 10
        reasons.append('The model is validated and is not the active version.')
    elif lifecycle == 'suspended':
        reasons.append('The model is suspended and cannot qualify a booking selection.')
    else:
        reasons.append('The model is in research and cannot qualify a booking selection.')
    if completeness is not None and completeness >= 0.5:
        score += 10
        reasons.append('Stored feature completeness is at least 0.50.')
    elif completeness is not None:
        reasons.append('Stored feature completeness is below 0.50.')
    for label, keys in CONTEXT_FIELDS.items():
        present = [key for key in keys if features.get(key) is not None]
        if len(present) == len(keys):
            score += 5 / len(CONTEXT_FIELDS) * 3
            reasons.append(f'Stored {label} values are present.')
        else:
            missing.append(label)
            reasons.append(f'{label.capitalize()} values were not supplied by a source.')
    score = round(min(100.0, score), 1)
    suitable = int('scoring_rates' not in missing)
    booking_ready = int(suitable and lifecycle == 'active' and calibrated)
    return {
        'suitable': suitable,
        'booking_ready': booking_ready,
        'rank_score': score,
        'reasons': reasons,
        'missing': missing,
    }


def record_suitability(connection, prediction_id, sport, market, assessment):
    connection.execute(
        """INSERT INTO sports_ai_market_suitability
           (id,prediction_id,sport,market,suitable,booking_ready,rank_score,reasons_json,missing_json,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(prediction_id) DO UPDATE SET
             suitable=excluded.suitable, booking_ready=excluded.booking_ready,
             rank_score=excluded.rank_score, reasons_json=excluded.reasons_json,
             missing_json=excluded.missing_json, created_at=excluded.created_at""",
        (str(uuid.uuid4()), prediction_id, sport, market, assessment['suitable'],
         assessment['booking_ready'], assessment['rank_score'],
         json.dumps(assessment['reasons']), json.dumps(assessment['missing']), utc_now()),
    )


def record_from_evidence(connection, prediction_id, sport, market, explanation_json, lifecycle):
    """Rank one stored prediction. Does not create a probability."""
    try:
        evidence = json.loads(explanation_json or '{}')
    except json.JSONDecodeError:
        evidence = {}
    assessment = assess_market(
        sport, market,
        evidence.get('raw_features') or evidence.get('features') or {},
        lifecycle or 'research',
        evidence.get('calibration_status') == 'validated',
        evidence.get('data_completeness'),
    )
    record_suitability(connection, prediction_id, sport, market, assessment)
    return assessment


def qualification_block(assessment):
    if assessment['booking_ready']:
        return None
    if 'scoring_rates' in assessment['missing']:
        return 'The market is not suitable because stored scoring rates are missing.'
    if any('suspended' in reason for reason in assessment['reasons']):
        return 'A suspended model cannot qualify a booking selection.'
    if any('research' in reason for reason in assessment['reasons']):
        return 'A research model cannot qualify a booking selection.'
    if any('not the active' in reason for reason in assessment['reasons']):
        return 'A validated model must be activated before it can qualify a booking selection.'
    return 'This market has no holdout calibration, so it cannot qualify a booking selection.'


def list_lifecycle(connection):
    sync_lifecycles(connection)
    rows = []
    for row in connection.execute(
        """SELECT id, sport, model_name, version, status, active, lifecycle, metrics_json, created_at
           FROM sports_ai_model_registry ORDER BY sport, created_at DESC""",
    ):
        metrics = _metrics(row['metrics_json'])
        rows.append({
            'id': row['id'],
            'sport': row['sport'],
            'model_name': row['model_name'],
            'version': row['version'],
            'registry_status': row['status'],
            'lifecycle': row['lifecycle'],
            'active': bool(row['active']),
            'brier_score': metrics.get('brier_score'),
            'log_loss': metrics.get('log_loss'),
            'calibration_error': metrics.get('calibration_error'),
            'holdout_fixtures': metrics.get('holdout_fixtures'),
            'created_at': row['created_at'],
        })
    unsupported = [
        {'sport': sport, 'lifecycle': 'research', 'models': 0,
         'note': 'No trained module. No probabilities are produced.'}
        for sport in ('tennis', 'volleyball', 'baseball', 'ice_hockey')
    ]
    return {'models': rows, 'unsupported_sports': unsupported}


def compare_models(connection, sport):
    sync_lifecycles(connection)
    if sport not in MODELLED_SPORTS:
        return {
            'sport': sport, 'models': [],
            'note': 'No trained module exists for this sport. Nothing was compared.',
        }
    models = list_lifecycle(connection)['models']
    chosen = [row for row in models if row['sport'] == sport]
    return {
        'sport': sport,
        'models': chosen,
        'note': 'Comparison uses stored training metrics only.',
    }


def evaluation_report(connection, sport):
    sync_lifecycles(connection)
    if sport not in MODELLED_SPORTS:
        return {
            'sport': sport,
            'models': [],
            'markets': [],
            'note': 'No evaluation exists because this sport has no trained model.',
        }
    models = [row for row in list_lifecycle(connection)['models'] if row['sport'] == sport]
    markets = []
    for row in connection.execute(
        """SELECT market, COUNT(*) AS predictions,
                  SUM(booking_ready) AS booking_ready,
                  AVG(rank_score) AS average_rank
           FROM sports_ai_market_suitability
           WHERE sport=? GROUP BY market ORDER BY average_rank DESC""",
        (sport,),
    ):
        markets.append({
            'market': row['market'],
            'predictions': row['predictions'],
            'booking_ready': row['booking_ready'] or 0,
            'average_rank': None if row['average_rank'] is None else round(row['average_rank'], 1),
        })
    return {
        'sport': sport,
        'models': models,
        'markets': markets,
        'calibrated_markets': sorted(CALIBRATED_MARKETS[sport]),
        'note': 'Metrics are copied from stored training rows and suitability records. No result was simulated.',
    }


def _metrics(value):
    try:
        parsed = json.loads(value or '{}')
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
