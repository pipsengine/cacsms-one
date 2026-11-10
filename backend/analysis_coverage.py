"""Progress, identity repair and data-driven model refresh for every sport."""

import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from sport_support import ANALYSED_SPORTS, PIPELINE_VERSION, canonical_sport


def dataset_signature(connection, sport):
    digest = hashlib.sha256(PIPELINE_VERSION.encode())
    for row in connection.execute(
        """SELECT f.id,f.competition,f.home,f.away,f.starts_at,r.home_score,r.away_score,r.settled_at,f.statistics_json
           FROM sports_ai_fixtures f JOIN sports_ai_results r ON r.fixture_id=f.id
           WHERE f.sport=? AND r.status='final'
             AND julianday(f.starts_at)<julianday('now') AND julianday(r.settled_at)<=julianday('now')
           ORDER BY f.id""", (sport,),
    ):
        digest.update(json.dumps(tuple(row), default=str).encode())
    return digest.hexdigest()


def record_attempt(connection, fixture, status, reason, model_id=None, now=None):
    current = now or datetime.now(timezone.utc)
    connection.execute(
        """INSERT INTO sports_ai_analysis_coverage
           (fixture_id,sport,competition,status,reason,model_id,attempted_at,retry_after,pipeline_version)
           VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(fixture_id) DO UPDATE SET
           sport=excluded.sport,competition=excluded.competition,status=excluded.status,reason=excluded.reason,
           model_id=excluded.model_id,attempted_at=excluded.attempted_at,retry_after=excluded.retry_after,
           pipeline_version=excluded.pipeline_version""",
        (fixture['id'], fixture['sport'], fixture['competition'], status, reason, model_id,
         current.isoformat(), (current + timedelta(minutes=45)).isoformat(), PIPELINE_VERSION),
    )


def repair_identities(connection):
    if not connection.in_transaction:
        connection.execute('BEGIN IMMEDIATE')
    changed = []
    for row in connection.execute('SELECT id,sport,competition FROM sports_ai_fixtures').fetchall():
        code = canonical_sport(row['sport'], row['competition'])
        if code == row['sport']:
            continue
        changed.append(row['id'])
        connection.execute('UPDATE sports_ai_fixtures SET sport=?,league_id=NULL WHERE id=?', (code, row['id']))
        connection.execute('UPDATE sports_ai_sportybet_observations SET sport=? WHERE fixture_id=?', (code, row['id']))
        connection.execute('UPDATE sports_ai_feature_snapshots SET sport=? WHERE fixture_id=?', (code, row['id']))
        connection.execute('UPDATE sports_ai_accumulator_legs SET sport=? WHERE fixture_id=?', (code, row['id']))
        connection.execute('DELETE FROM sports_ai_analysis_coverage WHERE fixture_id=?', (row['id'],))
    # Predictions from the old league identity must not remain bookable.
    legacy = [row['id'] for row in connection.execute(
        "SELECT id FROM sports_ai_model_registry WHERE COALESCE(json_extract(parameters_json,'$.pipeline_version'),'')<>?",
        (PIPELINE_VERSION,),
    )]
    if legacy:
        stored = connection.execute("SELECT value_json FROM sports_ai_settings WHERE key='model_governance'").fetchone()
        state = json.loads(stored['value_json']) if stored else {}
        state['blocked_ids'] = sorted(set(state.get('blocked_ids', [])) | set(legacy))
        connection.execute(
            """INSERT INTO sports_ai_settings(key,value_json,updated_at) VALUES('model_governance',?,CURRENT_TIMESTAMP)
               ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at""", (json.dumps(state),),
        )
    connection.execute(
        """UPDATE sports_ai_value_opportunities SET status='suspended',reason='Analysis identity changed; awaiting current-model inference.'
           WHERE status='qualified' AND prediction_id IN (
             SELECT p.id FROM sports_ai_engine_predictions p JOIN sports_ai_model_registry m ON m.id=p.model_id
             WHERE COALESCE(json_extract(m.parameters_json,'$.pipeline_version'),'')<>?
           )""", (PIPELINE_VERSION,),
    )
    connection.execute(
        """UPDATE sports_ai_model_registry SET active=0,status='inactive',lifecycle='suspended'
           WHERE active=1 AND COALESCE(json_extract(parameters_json,'$.pipeline_version'),'')<>?""",
        (PIPELINE_VERSION,),
    )
    return {'fixtures_repaired': len(changed)}


# Retraining cadence. Retraining on every new result replaced models several times a day:
# each new active version voided the board's predictions (MODEL_INACTIVE) and cleared the
# analysis coverage, so qualification never caught up (2026-11-09/10: football v3 -> v4 -> v5,
# each training run holding the worker for 20+ minutes).
VALIDATED_RETRAIN_HOURS = 24
VALIDATED_RETRAIN_GROWTH = 0.10
BOOTSTRAP_RETRAIN_HOURS = 6
BOOTSTRAP_RETRAIN_GROWTH = 0.20


def retrain_due(latest, finals, now=None):
    """Whether a sport's newest model is old enough and its results have grown enough."""
    if not latest:
        return True  # first (research) model for a newly seen sport
    current = now or datetime.now(timezone.utc)
    try:
        created = datetime.fromisoformat(str(latest['created_at']).replace('Z', '+00:00'))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    metrics = json.loads(latest['metrics_json'] or '{}')
    trained_on = int(metrics.get('available_finals') or 0)
    validated = latest['calibration_status'] == 'validated'
    hours, growth = ((VALIDATED_RETRAIN_HOURS, VALIDATED_RETRAIN_GROWTH) if validated
                     else (BOOTSTRAP_RETRAIN_HOURS, BOOTSTRAP_RETRAIN_GROWTH))
    if current - created < timedelta(hours=hours):
        return False
    return finals > trained_on * (1 + growth) if trained_on else finals > 0


def refresh_models(connection, limit=1, now=None):
    from prediction_engine import train_model
    pending = []
    for sport in ANALYSED_SPORTS:
        if not connection.execute('SELECT 1 FROM sports_ai_fixtures WHERE sport=? LIMIT 1', (sport,)).fetchone():
            continue
        latest = connection.execute(
            'SELECT * FROM sports_ai_model_registry WHERE sport=? ORDER BY julianday(created_at) DESC LIMIT 1', (sport,),
        ).fetchone()
        finals = connection.execute(
            """SELECT COUNT(*) FROM sports_ai_fixtures f JOIN sports_ai_results r ON r.fixture_id=f.id
               WHERE f.sport=? AND r.status='final'""", (sport,),
        ).fetchone()[0]
        # Cheap cadence check first; the full dataset fingerprint scans every final.
        if not retrain_due(latest, finals, now):
            continue
        signature = dataset_signature(connection, sport)
        params = json.loads(latest['parameters_json'] or '{}') if latest else {}
        if params.get('dataset_signature') == signature:
            continue
        pending.append((0 if finals else 1, latest['created_at'] if latest else '', sport))
    trained = []
    for _priority, _created, sport in sorted(pending)[:max(1, limit)]:
        trained.append(train_model(connection, sport))
        connection.execute('DELETE FROM sports_ai_analysis_coverage WHERE sport=?', (sport,))
    return trained


def coverage_report(connection):
    from prediction_engine import league_key, upcoming_fixture_sql
    history = defaultdict(int)
    for row in connection.execute(
        """SELECT f.sport,f.competition,COUNT(*) n FROM sports_ai_fixtures f
           JOIN sports_ai_results r ON r.fixture_id=f.id AND r.status='final'
           GROUP BY f.sport,f.competition""",
    ):
        history[(row['sport'], league_key(row['competition']))] += row['n']
    models = {}
    for row in connection.execute('SELECT * FROM sports_ai_model_registry ORDER BY active,julianday(created_at)'):
        models[row['sport']] = dict(row)
    state = connection.execute("SELECT value_json FROM sports_ai_settings WHERE key='model_governance'").fetchone()
    decisions = {item['sport']: item for item in (json.loads(state['value_json']).get('decisions', []) if state else [])}
    current_ids = {model['id'] for model in models.values()
                   if json.loads(model['parameters_json'] or '{}').get('pipeline_version') == PIPELINE_VERSION}
    marks = ','.join('?' for _ in current_ids) or 'NULL'
    predicted_ids = {row['fixture_id'] for row in connection.execute(
        f"""SELECT DISTINCT p.fixture_id FROM sports_ai_engine_predictions p
            JOIN sports_ai_feature_snapshots s ON s.id=p.feature_id
            WHERE p.model_id IN ({marks}) AND json_extract(s.features_json,'$.pipeline_version')=?""",
        (*current_ids, PIPELINE_VERSION),
    )}
    leagues = {}
    for row in connection.execute(
        f"""SELECT f.*,a.status AS attempt_status,a.reason AS attempt_reason
           FROM sports_ai_fixtures f LEFT JOIN sports_ai_analysis_coverage a ON a.fixture_id=f.id
           WHERE {upcoming_fixture_sql('f')}""",
    ):
        key = (row['sport'], league_key(row['competition']))
        cell = leagues.setdefault(key, {'sport': row['sport'], 'competition': row['competition'],
            'league_key': key[1], 'fixtures': 0, 'analyzed': 0, 'reasons': Counter(), 'final_results': history[key]})
        cell['fixtures'] += 1
        predicted = row['id'] in predicted_ids
        cell['analyzed'] += predicted
        model = models.get(row['sport'])
        if predicted:
            reason = 'analyzed' if model and model['active'] else 'research_prediction'
        elif row['sport'] not in ANALYSED_SPORTS:
            reason = 'outcome_definition_required'
        elif not history[key]:
            reason = 'awaiting_results'
        elif not model or model['id'] not in current_ids:
            reason = 'model_refresh_pending'
        else:
            reason = row['attempt_status'] or 'queued'
        cell['reasons'][reason] += 1
    items = sorted(leagues.values(), key=lambda item: (item['sport'], item['competition']))
    counts = Counter()
    for cell in items:
        counts.update(cell['reasons'])
    model_rows = []
    for sport in sorted({item['sport'] for item in items}):
        model = models.get(sport)
        metrics = json.loads(model['metrics_json'] or '{}') if model else {}
        model_rows.append({'sport': sport, 'status': model['status'] if model else 'awaiting_training',
            'active': bool(model and model['active']), 'scored_fixtures': metrics.get('fixtures_scored', 0),
            'holdout_fixtures': metrics.get('holdout_fixtures', 0), 'calibration_error': metrics.get('calibration_error'),
            'reasons': [] if model and model['active'] else decisions.get(sport, {}).get('reasons') or
                ['Waiting for sufficient cutoff-safe training and holdout data.' if not metrics.get('fixtures_scored') else 'Awaiting the next validation review.']})
    return {'leagues': items, 'models': model_rows, 'counts': dict(counts),
            'pipeline_version': PIPELINE_VERSION, 'places_wager': False}
