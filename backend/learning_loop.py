"""Close the learning loop: late predictions reach waiting prices, drift triggers retraining.

Training, holdout governance, prospective scoring and rollback already exist in
prediction_engine and operations_engine. Two links were missing:

* A SportyBet price that arrived before its prediction was marked
  ``awaiting_model`` once and never assessed again, so the backlog only grew.
* A drift event was recorded as a monitoring signal and nothing acted on it.

Neither function activates a model. A retrained candidate still has to pass the
existing holdout governance gates before it can be used.
"""
import json
from datetime import datetime, timedelta, timezone

RETRAIN_COOLDOWN_HOURS = 24


def _now(now=None):
    return now or datetime.now(timezone.utc)


def reassess_awaiting_model(connection, now=None, limit=500, expire_fixtures=400):
    """Re-run value assessment for waiting prices whose prediction now exists.

    Work is driven by fixture through a partial index, so each pass stays fast
    even with millions of waiting rows. Prices for fixtures that have already
    started are closed as ``expired``. Returns counts per resulting status.
    """
    from sportybet_research import _assess_value
    current = _now(now)
    counts = {}
    waiting = [r[0] for r in connection.execute(
        "SELECT DISTINCT fixture_id FROM sports_ai_sportybet_observations WHERE value_status='awaiting_model'",
    ) if r[0]]
    kickoff = {}
    for chunk in range(0, len(waiting), 500):
        ids = waiting[chunk:chunk + 500]
        marks = ','.join('?' * len(ids))
        for row in connection.execute(f'SELECT id,starts_at FROM sports_ai_fixtures WHERE id IN ({marks})', ids):
            kickoff[row['id']] = row['starts_at']
    started, upcoming = [], []
    for fixture_id in waiting:
        moment = kickoff.get(fixture_id)
        try:
            began = moment is None or datetime.fromisoformat(str(moment).replace('Z', '+00:00')) <= current
        except ValueError:
            began = True
        (started if began else upcoming).append(fixture_id)
    for fixture_id in started[:expire_fixtures]:
        changed = connection.execute(
            """UPDATE sports_ai_sportybet_observations SET value_status='expired'
               WHERE fixture_id=? AND value_status='awaiting_model'""", (fixture_id,),
        ).rowcount
        if changed:
            counts['expired'] = counts.get('expired', 0) + changed
    reassessed = 0
    for fixture_id in upcoming:
        if reassessed >= limit:
            break
        markets = {r[0] for r in connection.execute(
            'SELECT DISTINCT market FROM sports_ai_engine_predictions WHERE fixture_id=?', (fixture_id,),
        )}
        if not markets:
            continue
        rows = connection.execute(
            """SELECT * FROM sports_ai_sportybet_observations
               WHERE fixture_id=? AND value_status='awaiting_model' AND odds_id IS NOT NULL
               ORDER BY observed_at DESC""", (fixture_id,),
        ).fetchall()
        for row in rows:
            if row['canonical_market'] not in markets or reassessed >= limit:
                continue
            status, _edge = _assess_value(connection, row, row['odds_id'], float(row['decimal_odds']), current)
            connection.execute('UPDATE sports_ai_sportybet_observations SET value_status=? WHERE id=?', (status, row['id']))
            counts[status] = counts.get(status, 0) + 1
            reassessed += 1
    return {
        'status': 'complete', 'reassessed': reassessed, 'counts': counts,
        'waiting_fixtures': len(waiting), 'started_fixtures_left': max(0, len(started) - expire_fixtures),
    }


def backlog(connection, now=None):
    current = _now(now)
    row = connection.execute(
        """SELECT COUNT(*) total,
                  SUM(CASE WHEN julianday(f.starts_at)>julianday(?) THEN 1 ELSE 0 END) upcoming,
                  SUM(CASE WHEN julianday(f.starts_at)>julianday(?) AND EXISTS (
                      SELECT 1 FROM sports_ai_engine_predictions p
                      WHERE p.fixture_id=o.fixture_id AND p.market=o.canonical_market) THEN 1 ELSE 0 END) ready
           FROM sports_ai_sportybet_observations o JOIN sports_ai_fixtures f ON f.id=o.fixture_id
           WHERE o.value_status='awaiting_model'""", (current.isoformat(), current.isoformat()),
    ).fetchone()
    return {'awaiting_model': row['total'] or 0, 'upcoming': row['upcoming'] or 0, 'prediction_ready': row['ready'] or 0}


def drift_retraining(connection, actor='sports-ai-worker', audit=None, now=None, limit=1):
    """Train a candidate for a sport whose live drift has no newer training run.

    At most ``limit`` sports per call, and at most once per sport per cooldown.
    The candidate stays inactive unless holdout governance later activates it.
    """
    from prediction_engine import train_model
    current = _now(now)
    since = (current - timedelta(hours=RETRAIN_COOLDOWN_HOURS)).isoformat()
    sports = connection.execute(
        """SELECT d.sport, MAX(d.created_at) drifted_at, COUNT(*) events
           FROM sports_ai_model_drift_events d
           WHERE d.created_at>=?
             AND NOT EXISTS (SELECT 1 FROM sports_ai_training_runs t
                             WHERE t.sport=d.sport AND t.created_at>=d.created_at)
             AND NOT EXISTS (SELECT 1 FROM sports_ai_training_runs t
                             WHERE t.sport=d.sport AND t.created_at>=?)
           GROUP BY d.sport ORDER BY drifted_at LIMIT ?""", (since, since, limit),
    ).fetchall()
    trained = []
    for row in sports:
        result = train_model(connection, row['sport'])
        record = {
            'sport': row['sport'], 'drift_events': row['events'], 'drifted_at': row['drifted_at'],
            'candidate_status': result.get('status'), 'model_id': result.get('model_id'), 'activated': False,
        }
        trained.append(record)
        if audit:
            audit(connection, actor, 'models.drift_retrain', record)
    return {'status': 'complete' if trained else 'idle', 'trained': trained}


def learning_status(connection, now=None):
    """One read model for the Model Accuracy page."""
    current = _now(now)
    week = (current - timedelta(days=7)).isoformat()
    evaluations = [dict(r) for r in connection.execute(
        """SELECT e.sport,e.market,e.sample_size,e.brier_score,e.log_loss,e.calibration_error,e.hit_rate,e.created_at,
                  m.version,m.active
           FROM sports_ai_model_evaluations e LEFT JOIN sports_ai_model_registry m ON m.id=e.model_id
           WHERE e.created_at=(SELECT MAX(x.created_at) FROM sports_ai_model_evaluations x
                               WHERE x.model_id IS e.model_id AND x.market=e.market)
           ORDER BY e.sport,e.market""",
    )]
    drift = [dict(r) for r in connection.execute(
        """SELECT sport,market,drift_type,metric_value,threshold,created_at FROM sports_ai_model_drift_events
           WHERE created_at>=? ORDER BY created_at DESC LIMIT 50""", (week,),
    )]
    runs = [dict(r) for r in connection.execute(
        """SELECT sport,version,status,sample_size,created_at,metrics_json FROM sports_ai_training_runs
           ORDER BY created_at DESC LIMIT 20""",
    )]
    for run in runs:
        metrics = json.loads(run.pop('metrics_json') or '{}')
        run.update({k: metrics.get(k) for k in ('calibration_error', 'brier_score', 'log_loss', 'holdout_fixtures')})
    models = [dict(r) for r in connection.execute(
        """SELECT id,sport,version,status,active,calibration_status,activated_at,created_at
           FROM sports_ai_model_registry WHERE active=1 OR status IN ('validated','active') ORDER BY sport,created_at DESC""",
    )]
    from market_anchor import load as load_anchors
    anchors = load_anchors(connection)
    anchor_rows = [{'scope': 'sport', 'name': sport, **values} for sport, values in (anchors.get('sports') or {}).items()]
    anchor_rows += [{'scope': 'league', 'name': key.split('|', 1)[1], 'sport': key.split('|', 1)[0], **values}
                    for key, values in (anchors.get('leagues') or {}).items()]
    return {
        'anchors': anchor_rows, 'anchors_learned_at': anchors.get('learned_at'), 'anchor_pairs': anchors.get('pairs'),
        'generated_at': current.isoformat(), 'models': models, 'evaluations': evaluations,
        'drift': drift, 'training_runs': runs, 'places_wager': False,
    }
