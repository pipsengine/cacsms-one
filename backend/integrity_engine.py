"""Record whether a stored pre-match forecast used only earlier results.

This does not change probabilities, snapshots, or qualification thresholds.
"""

import json
import uuid
from datetime import datetime, timezone


def _parse(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _store(connection, subject_id, finding, detail, now):
    connection.execute(
        """INSERT INTO sports_ai_feature_audits(id,subject_id,finding,detail,created_at)
           VALUES(?,?,?,?,?)
           ON CONFLICT(subject_id, finding) DO UPDATE SET detail=excluded.detail, created_at=excluded.created_at""",
        (str(uuid.uuid4()), subject_id, finding, detail[:500], now.isoformat()),
    )


def audit_prematch_features(connection, now=None):
    """Check active models and current qualified forecasts for feature timing."""
    current = now or datetime.now(timezone.utc)
    models = connection.execute(
        """SELECT id, model_name, version, sport, metrics_json
           FROM sports_ai_model_registry WHERE active=1""",
    ).fetchall()
    for model in models:
        metrics = {}
        try:
            metrics = json.loads(model['metrics_json'] or '{}')
        except json.JSONDecodeError:
            metrics = {}
        _store(
            connection, model['id'], 'HOLDOUT_RECORDED',
            (
                f"{model['sport']} {model['model_name']} {model['version']} holdout "
                f"brier {metrics.get('brier_score')} log_loss {metrics.get('log_loss')} "
                f"calibration_error {metrics.get('calibration_error')} sample {metrics.get('holdout_fixtures')}. "
                'These stored figures were not recalculated and the model was not activated.'
            ),
            current,
        )
        _store(
            connection, model['id'], 'CUTOFF_IS_KICKOFF',
            'Feature construction keeps matches whose kickoff and settlement are both before the predicted fixture kickoff. It does not read that fixture result.',
            current,
        )
    forecasts = connection.execute(
        """SELECT p.id, p.created_at, s.generated_at, s.cutoff_at, s.source_refs_json
           FROM sports_ai_engine_predictions p
           LEFT JOIN sports_ai_feature_snapshots s ON s.id=p.feature_id
           WHERE p.id IN (
             SELECT prediction_id FROM sports_ai_qualification_decisions
             WHERE state='QUALIFIED' OR reason_code='RISK_REVIEW'
             UNION
             SELECT prediction_id FROM sports_ai_prediction_reviews
           )""",
    ).fetchall()
    leaked = 0
    unavailable = 0
    checked = 0
    for forecast in forecasts:
        checked += 1
        generated = _parse(forecast['generated_at'])
        cutoff = _parse(forecast['cutoff_at'])
        try:
            refs = json.loads(forecast['source_refs_json'] or '{}')
        except json.JSONDecodeError:
            refs = {}
        source_ids = refs.get('fixture_ids') if isinstance(refs, dict) else None
        if not generated or not cutoff or not source_ids:
            unavailable += 1
            _store(connection, forecast['id'], 'FEATURE_SOURCES_NOT_STORED', 'The snapshot does not list the source fixtures and their timestamps.', current)
            continue
        if generated > cutoff:
            leaked += 1
            _store(connection, forecast['id'], 'GENERATED_AFTER_CUTOFF', 'The snapshot was generated after its cutoff.', current)
        placeholders = ','.join('?' for _ in source_ids)
        sources = connection.execute(
            f"""SELECT f.id, f.starts_at, r.settled_at
                FROM sports_ai_fixtures f
                LEFT JOIN sports_ai_results r ON r.fixture_id=f.id
                WHERE f.id IN ({placeholders})""",
            tuple(source_ids),
        ).fetchall()
        late = [
            row['id'] for row in sources
            if (_parse(row['starts_at']) and _parse(row['starts_at']) >= cutoff)
            or (_parse(row['settled_at']) and generated and _parse(row['settled_at']) >= generated)
        ]
        if late:
            leaked += 1
            _store(
                connection, forecast['id'], 'POST_PREDICTION_FEATURE',
                f"{len(late)} source fixtures were not finished before this forecast was generated.",
                current,
            )
        else:
            _store(
                connection, forecast['id'], 'FEATURES_BEFORE_FORECAST',
                f"{len(sources)} source fixtures have kickoff before the cutoff and no settlement after the forecast timestamp.",
                current,
            )
    return {'checked': checked, 'leaked': leaked, 'sources_not_stored': unavailable, 'thresholds_changed': False, 'places_wager': False}
