"""Phase 4 workflow, scheduler lease, learning checks and notification delivery.

The web process does not loop this module. backend/worker.py is the only scheduler.
Production model promotion is never implied by a training run.
"""

import json
import os
import smtplib
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from accumulator_engine import monitor_accumulators, settle_result
from db import connect
from prediction_engine import recover_timed_out_jobs, train_model

STATES = (
    'DISCOVERED', 'DATA_COLLECTION_PENDING', 'COLLECTING', 'DATA_VALIDATION', 'DATA_INCOMPLETE',
    'READY_FOR_ANALYSIS', 'ANALYSING', 'PREDICTION_READY', 'AWAITING_ODDS', 'VALUE_EVALUATION',
    'QUALIFIED', 'NOT_QUALIFIED', 'ACCUMULATOR_ELIGIBLE', 'TICKET_GENERATED', 'MONITORING',
    'RESULT_PENDING', 'SETTLED', 'COMPLETED', 'FAILED', 'RETRY_PENDING', 'MANUAL_REVIEW',
)
TRANSITIONS = {
    'DISCOVERED': {'DATA_COLLECTION_PENDING', 'DATA_VALIDATION', 'FAILED', 'MANUAL_REVIEW'},
    'DATA_COLLECTION_PENDING': {'COLLECTING', 'DATA_VALIDATION', 'DATA_INCOMPLETE', 'FAILED', 'RETRY_PENDING', 'MANUAL_REVIEW'},
    'COLLECTING': {'DATA_VALIDATION', 'DATA_INCOMPLETE', 'FAILED', 'RETRY_PENDING'},
    'DATA_VALIDATION': {'DATA_INCOMPLETE', 'READY_FOR_ANALYSIS', 'FAILED', 'MANUAL_REVIEW'},
    'DATA_INCOMPLETE': {'DATA_COLLECTION_PENDING', 'MANUAL_REVIEW', 'FAILED'},
    'READY_FOR_ANALYSIS': {'ANALYSING', 'DATA_INCOMPLETE', 'FAILED'},
    'ANALYSING': {'PREDICTION_READY', 'FAILED', 'RETRY_PENDING', 'MANUAL_REVIEW'},
    'PREDICTION_READY': {'AWAITING_ODDS', 'VALUE_EVALUATION', 'FAILED'},
    'AWAITING_ODDS': {'VALUE_EVALUATION', 'NOT_QUALIFIED', 'FAILED', 'MANUAL_REVIEW'},
    'VALUE_EVALUATION': {'QUALIFIED', 'NOT_QUALIFIED', 'AWAITING_ODDS', 'FAILED'},
    'QUALIFIED': {'ACCUMULATOR_ELIGIBLE', 'NOT_QUALIFIED', 'MONITORING', 'MANUAL_REVIEW'},
    'NOT_QUALIFIED': {'VALUE_EVALUATION', 'COMPLETED', 'MANUAL_REVIEW'},
    'ACCUMULATOR_ELIGIBLE': {'TICKET_GENERATED', 'QUALIFIED', 'NOT_QUALIFIED', 'MANUAL_REVIEW'},
    'TICKET_GENERATED': {'MONITORING', 'MANUAL_REVIEW'},
    'MONITORING': {'RESULT_PENDING', 'MANUAL_REVIEW', 'FAILED'},
    'RESULT_PENDING': {'SETTLED', 'FAILED', 'MANUAL_REVIEW'},
    'SETTLED': {'COMPLETED'},
    'COMPLETED': set(),
    'FAILED': {'RETRY_PENDING', 'MANUAL_REVIEW'},
    'RETRY_PENDING': {'DATA_COLLECTION_PENDING', 'ANALYSING', 'VALUE_EVALUATION', 'FAILED', 'MANUAL_REVIEW'},
    'MANUAL_REVIEW': {'DATA_COLLECTION_PENDING', 'READY_FOR_ANALYSIS', 'VALUE_EVALUATION', 'FAILED', 'COMPLETED'},
}
LEADER_ID = 'scheduler-leader'
SECRET_KEYS = ('password', 'token', 'secret', 'authorization', 'api_key', 'smtp')


def _now(value=None):
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _parse(value):
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


def redact(payload):
    if not isinstance(payload, dict):
        return payload
    clean = {}
    for key, value in payload.items():
        if any(part in key.lower() for part in SECRET_KEYS):
            clean[key] = '[redacted]'
        elif isinstance(value, dict):
            clean[key] = redact(value)
        else:
            clean[key] = value
    return clean


def can_transition(source, target):
    return target in TRANSITIONS.get(source, ())


def shortest_path(source, target):
    if source == target:
        return []
    queue = [(source, [])]
    seen = {source}
    while queue:
        state, path = queue.pop(0)
        for nxt in sorted(TRANSITIONS.get(state, ())):
            if nxt in seen:
                continue
            route = path + [nxt]
            if nxt == target:
                return route
            seen.add(nxt)
            queue.append((nxt, route))
    return None


def transition(connection, workflow_id, target, reason, now=None, job_id=None, source_ref='', model_id=None):
    current = connection.execute('SELECT * FROM sports_ai_workflows WHERE id=?', (workflow_id,)).fetchone()
    if not current:
        raise ValueError('Workflow not found')
    source = current['state']
    if not can_transition(source, target):
        raise ValueError(f'Invalid workflow transition {source} -> {target}')
    moment = _now(now).isoformat()
    connection.execute(
        """UPDATE sports_ai_workflows
           SET state=?, reason=?, job_id=?, source_ref=?, model_id=?, updated_at=? WHERE id=?""",
        (target, reason, job_id, source_ref or current['source_ref'], model_id or current['model_id'], moment, workflow_id),
    )
    connection.execute(
        """INSERT INTO sports_ai_workflow_events
           (id,workflow_id,fixture_id,from_state,to_state,reason,job_id,source_ref,model_id,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (str(uuid.uuid4()), workflow_id, current['fixture_id'], source, target, reason, job_id,
         source_ref or current['source_ref'], model_id or current['model_id'], moment),
    )
    return target


def ensure_workflow(connection, fixture_id, source_ref, now=None):
    existing = connection.execute(
        'SELECT * FROM sports_ai_workflows WHERE fixture_id=?', (fixture_id,),
    ).fetchone()
    if existing:
        return dict(existing)
    moment = _now(now).isoformat()
    workflow_id = str(uuid.uuid4())
    connection.execute(
        """INSERT INTO sports_ai_workflows
           (id,fixture_id,state,source_ref,reason,updated_at,created_at)
           VALUES(?,?,'DISCOVERED',?,?,?,?)""",
        (workflow_id, fixture_id, source_ref or '', 'Fixture recorded', moment, moment),
    )
    connection.execute(
        """INSERT INTO sports_ai_workflow_events
           (id,workflow_id,fixture_id,from_state,to_state,reason,source_ref,created_at)
           VALUES(?,?,?,NULL,'DISCOVERED','Fixture entered the workflow',?,?)""",
        (str(uuid.uuid4()), workflow_id, fixture_id, source_ref or '', moment),
    )
    return dict(connection.execute('SELECT * FROM sports_ai_workflows WHERE id=?', (workflow_id,)).fetchone())


def observe_state(connection, fixture, now=None):
    """Choose the state supported by stored records. Missing data stays incomplete."""
    current = _now(now)
    kickoff = _parse(fixture['starts_at'])
    started = bool(kickoff and kickoff <= current)
    issues = connection.execute(
        """SELECT COUNT(*) FROM sports_ai_data_quality_issues
           WHERE fixture_id=? AND resolved_at IS NULL AND severity IN ('high','critical')""",
        (fixture['id'],),
    ).fetchone()[0]
    predictions = connection.execute(
        'SELECT COUNT(*), MAX(model_id) FROM sports_ai_engine_predictions WHERE fixture_id=?',
        (fixture['id'],),
    ).fetchone()
    prediction_count = predictions[0] or 0
    odds = connection.execute(
        'SELECT COUNT(*) FROM sports_ai_odds_snapshots WHERE fixture_id=?', (fixture['id'],),
    ).fetchone()[0]
    opportunity = connection.execute(
        """SELECT o.status FROM sports_ai_value_opportunities o
           JOIN sports_ai_engine_predictions p ON p.id=o.prediction_id
           WHERE p.fixture_id=? ORDER BY o.evaluated_at DESC LIMIT 1""",
        (fixture['id'],),
    ).fetchone()
    ticket = connection.execute(
        """SELECT t.status FROM sports_ai_accumulator_legs l
           JOIN sports_ai_accumulator_tickets t ON t.id=l.ticket_id
           WHERE l.fixture_id=? ORDER BY t.created_at DESC LIMIT 1""",
        (fixture['id'],),
    ).fetchone()
    result = connection.execute(
        "SELECT status FROM sports_ai_results WHERE fixture_id=? AND status='final'", (fixture['id'],),
    ).fetchone()
    if started and result and ticket and ticket['status'] in ('settled', 'void'):
        return 'SETTLED'
    if started and result:
        return 'COMPLETED' if not ticket else 'RESULT_PENDING'
    if started:
        return 'RESULT_PENDING' if ticket else 'MONITORING'
    if issues:
        return 'DATA_INCOMPLETE'
    if ticket:
        return 'TICKET_GENERATED'
    if opportunity and opportunity['status'] == 'qualified':
        return 'ACCUMULATOR_ELIGIBLE'
    if opportunity and opportunity['status'] in ('rejected', 'expired', 'suspended'):
        return 'NOT_QUALIFIED'
    if opportunity and opportunity['status'] == 'awaiting_odds':
        return 'AWAITING_ODDS'
    if prediction_count and not odds:
        return 'AWAITING_ODDS'
    if prediction_count:
        return 'PREDICTION_READY'
    features = connection.execute(
        'SELECT COUNT(*) FROM sports_ai_feature_snapshots WHERE fixture_id=?', (fixture['id'],),
    ).fetchone()[0]
    if features:
        return 'READY_FOR_ANALYSIS'
    return 'DATA_COLLECTION_PENDING'


def sync_workflows(connection, now=None, limit=200):
    current = _now(now)
    fixtures = connection.execute(
        'SELECT id,source,starts_at FROM sports_ai_fixtures ORDER BY starts_at DESC LIMIT ?', (limit,),
    ).fetchall()
    moved = 0
    for fixture in fixtures:
        workflow = ensure_workflow(connection, fixture['id'], fixture['source'], current)
        target = observe_state(connection, fixture, current)
        if workflow['state'] == target:
            continue
        path = shortest_path(workflow['state'], target)
        if not path:
            if can_transition(workflow['state'], 'MANUAL_REVIEW'):
                transition(connection, workflow['id'], 'MANUAL_REVIEW',
                           f'Stored records support {target}, which is not a legal step from {workflow["state"]}', current)
                moved += 1
            continue
        for step in path:
            transition(connection, workflow['id'], step, f'Stored records support {target}', current, source_ref=fixture['source'])
            moved += 1
    return {'fixtures': len(fixtures), 'transitions': moved}


def renew_leader(connection, owner, now=None, ttl_seconds=90):
    """Keep the scheduler lease fresh while a long stage is still this worker's."""
    current = _now(now)
    cursor = connection.execute(
        """UPDATE sports_ai_worker_leases
           SET heartbeat_at=?, expires_at=?
           WHERE id=? AND owner=?""",
        (current.isoformat(), (current + timedelta(seconds=ttl_seconds)).isoformat(), LEADER_ID, owner),
    )
    return cursor.rowcount == 1


def keep_leader(connection, owner, now=None, ttl_seconds=90):
    """Renew this worker's lease, or take it once the previous lease has expired."""
    current = _now(now)
    if renew_leader(connection, owner, current, ttl_seconds):
        return True
    return acquire_leader(connection, owner, current, ttl_seconds)


def acquire_leader(connection, owner, now=None, ttl_seconds=40):
    current = _now(now)
    until = (current + timedelta(seconds=ttl_seconds)).isoformat()
    heartbeat = current.isoformat()
    cursor = connection.execute(
        """UPDATE sports_ai_worker_leases
           SET owner=?, expires_at=?, heartbeat_at=?
           WHERE id=? AND (owner=? OR expires_at<=?)""",
        (owner, until, heartbeat, LEADER_ID, owner, heartbeat),
    )
    return cursor.rowcount == 1


def recover_stuck_jobs(connection, now=None):
    current = _now(now).isoformat()
    expired = connection.execute(
        """SELECT id,attempt,max_attempts,job_kind,error FROM sports_ai_jobs
           WHERE status='running' AND lease_expires_at IS NOT NULL AND lease_expires_at<?""",
        (current,),
    ).fetchall()
    recovered = 0
    for job in expired:
        if int(job['attempt']) >= int(job['max_attempts']):
            _dead_letter(connection, job, 'LEASE_EXPIRED', 'Worker lease expired after the final attempt', current)
        else:
            delay = min(60, 2 ** max(1, int(job['attempt'])))
            available = (_parse(current) + timedelta(minutes=delay)).isoformat()
            connection.execute(
                """UPDATE sports_ai_jobs
                   SET status='retry_pending', lease_owner=NULL, error_code='LEASE_EXPIRED',
                       error='Worker lease expired before the job finished', available_at=?
                   WHERE id=?""",
                (available, job['id']),
            )
        recovered += 1
    abandoned = connection.execute(
        """SELECT id,attempt,max_attempts,job_kind,error FROM sports_ai_jobs
           WHERE status='running' AND lease_expires_at IS NULL AND started_at<?""",
        ((_now(now) - timedelta(minutes=10)).isoformat(),),
    ).fetchall()
    for job in abandoned:
        connection.execute(
            """UPDATE sports_ai_jobs
               SET status='retry_pending', lease_owner=NULL, error_code='WORKER_STOPPED',
                   error='The worker stopped before this job finished', available_at=?
               WHERE id=?""",
            (current, job['id']),
        )
        recovered += 1
    return recovered


def enqueue_job(connection, job_kind, idempotency_key, payload=None, schedule_id=None, now=None, max_attempts=3):
    existing = connection.execute(
        'SELECT * FROM sports_ai_jobs WHERE idempotency_key=?', (idempotency_key,),
    ).fetchone()
    if existing:
        return {'id': existing['id'], 'status': existing['status'], 'created': False}
    moment = _now(now).isoformat()
    job_id = str(uuid.uuid4())
    connection.execute(
        """INSERT INTO sports_ai_jobs
           (id,idempotency_key,job_kind,status,max_attempts,available_at,payload_json,schedule_id,created_at)
           VALUES(?,?,?,'queued',?,?,?,?,?)""",
        (job_id, idempotency_key, job_kind, max_attempts, moment, json.dumps(redact(payload or {})), schedule_id, moment),
    )
    return {'id': job_id, 'status': 'queued', 'created': True}


def fail_job(connection, job_id, error_code, message, now=None):
    job = connection.execute('SELECT * FROM sports_ai_jobs WHERE id=?', (job_id,)).fetchone()
    if not job:
        return None
    current = _now(now)
    attempt = int(job['attempt'])
    if attempt >= int(job['max_attempts']):
        _dead_letter(connection, job, error_code, message, current.isoformat())
        return 'dead_letter'
    delay = min(60, 2 ** max(1, attempt))
    available = (current + timedelta(minutes=delay)).isoformat()
    connection.execute(
        """UPDATE sports_ai_jobs
           SET status='retry_pending', error_code=?, error=?, available_at=?, lease_owner=NULL, finished_at=?
           WHERE id=?""",
        (error_code, str(message)[:500], available, current.isoformat(), job_id),
    )
    _attempt_row(connection, job_id, attempt, 'failed', error_code, message, current.isoformat())
    return 'retry_pending'


def _dead_letter(connection, job, error_code, message, moment):
    connection.execute(
        """UPDATE sports_ai_jobs
           SET status='dead_letter', error_code=?, error=?, lease_owner=NULL, finished_at=? WHERE id=?""",
        (error_code, str(message)[:500], moment, job['id']),
    )
    connection.execute(
        """INSERT INTO sports_ai_recovery_cases
           (id,job_id,error_code,status,attempt_count,details_json,created_at,updated_at)
           VALUES(?,?,?,'open',?,?,?,?)""",
        (str(uuid.uuid4()), job['id'], error_code, int(job['attempt']),
         json.dumps({'job_kind': job['job_kind'], 'message': str(message)[:500]}), moment, moment),
    )


def _attempt_row(connection, job_id, attempt, status, error_code, error, moment):
    connection.execute(
        """INSERT INTO sports_ai_job_attempts
           (id,job_id,attempt,status,error_code,error,started_at,finished_at)
           VALUES(?,?,?,?,?,?,?,?)""",
        (str(uuid.uuid4()), job_id, attempt, status, error_code, None if error is None else str(error)[:500], moment, moment),
    )


def complete_job(connection, job_id, result, status='complete', now=None):
    moment = _now(now).isoformat()
    job = connection.execute('SELECT attempt FROM sports_ai_jobs WHERE id=?', (job_id,)).fetchone()
    connection.execute(
        """UPDATE sports_ai_jobs
           SET status=?, result_json=?, finished_at=?, lease_owner=NULL, error_code=NULL WHERE id=?""",
        (status, json.dumps(redact(result)), moment, job_id),
    )
    if job:
        _attempt_row(connection, job_id, int(job['attempt']), status, None, None, moment)


def claim_job(connection, owner, now=None):
    current = _now(now)
    moment = current.isoformat()
    row = connection.execute(
        """SELECT id FROM sports_ai_jobs
           WHERE status IN ('queued','retry_pending') AND available_at<=? AND attempt<max_attempts
           ORDER BY available_at,created_at LIMIT 1""",
        (moment,),
    ).fetchone()
    if not row:
        return None
    lease = (current + timedelta(seconds=120)).isoformat()
    cursor = connection.execute(
        """UPDATE sports_ai_jobs
           SET status='running', attempt=attempt+1, lease_owner=?, lease_expires_at=?, started_at=?
           WHERE id=? AND status IN ('queued','retry_pending')""",
        (owner, lease, moment, row['id']),
    )
    if cursor.rowcount != 1:
        return None
    return dict(connection.execute('SELECT * FROM sports_ai_jobs WHERE id=?', (row['id'],)).fetchone())


def cron_is_due(expression, last_run, now):
    """Support minute/hour UTC cron. Other fields must be '*'."""
    if not expression:
        return False
    fields = expression.split()
    if len(fields) != 5 or fields[2:] != ['*', '*', '*']:
        raise ValueError('Only minute and hour cron fields are supported; day, month and weekday must be *')
    current = _now(now)
    if not (_cron_match(fields[0], current.minute) and _cron_match(fields[1], current.hour)):
        return False
    slot = current.replace(second=0, microsecond=0)
    previous = _parse(last_run)
    return previous is None or previous < slot


def _cron_match(field, value):
    if field == '*':
        return True
    if field.startswith('*/'):
        step = int(field[2:])
        return step > 0 and value % step == 0
    return value in {int(part) for part in field.split(',') if part != ''}


def run_due_schedules(connection, audit, now=None):
    current = _now(now)
    ran = []
    schedules = connection.execute('SELECT * FROM sports_ai_schedules WHERE enabled=1').fetchall()
    for schedule in schedules:
        due = False
        if schedule['cron_expression']:
            try:
                due = cron_is_due(schedule['cron_expression'], schedule['last_run_at'], current)
            except ValueError as error:
                ran.append({'schedule_id': schedule['id'], 'status': 'skipped', 'reason': str(error)})
                continue
        else:
            last = _parse(schedule['last_run_at'])
            due = last is None or current - last >= timedelta(minutes=int(schedule['interval_minutes']))
        if not due:
            continue
        bucket = int(current.timestamp() // (max(5, int(schedule['interval_minutes'])) * 60))
        key = f"schedule:{schedule['id']}:{bucket}"
        job = enqueue_job(connection, schedule['job_kind'] or 'prediction', key, {'schedule': schedule['name']}, schedule['id'], current)
        if not job['created'] and job['status'] in ('complete', 'skipped', 'running'):
            ran.append({'schedule_id': schedule['id'], 'status': 'duplicate', 'job_id': job['id']})
            continue
        if job['status'] == 'queued':
            connection.execute(
                """UPDATE sports_ai_jobs SET status='running', attempt=attempt+1, started_at=?, lease_owner='scheduler'
                   WHERE id=? AND status='queued'""",
                (current.isoformat(), job['id']),
            )
        try:
            result = _execute_kind(connection, schedule['job_kind'] or 'prediction', audit, key, current)
            final = 'skipped' if result.get('status') == 'skipped' else 'complete'
            complete_job(connection, job['id'], result, final, current)
            connection.execute(
                'UPDATE sports_ai_schedules SET last_run_at=?, next_run_at=? WHERE id=?',
                (current.isoformat(), (current + timedelta(minutes=int(schedule['interval_minutes']))).isoformat(), schedule['id']),
            )
            connection.commit()
            connection.execute(
                """INSERT INTO sports_ai_job_history (id,schedule_id,status,finished_at,details_json)
                   VALUES(?,?,'complete',?,?)""",
                (str(uuid.uuid4()), schedule['id'], current.isoformat(), json.dumps(redact(result))),
            )
            ran.append({'schedule_id': schedule['id'], 'status': final, 'job_id': job['id']})
        except Exception as error:
            outcome = fail_job(connection, job['id'], _error_code(error), error, current)
            ran.append({'schedule_id': schedule['id'], 'status': outcome, 'error': str(error)[:300]})
    return ran


def _error_code(error):
    name = type(error).__name__
    if 'Timeout' in name:
        return 'TIMEOUT'
    if 'Collection' in name:
        return 'PROVIDER_REJECTED'
    if isinstance(error, ValueError):
        return 'INVALID_INPUT'
    return 'UNHANDLED'


def _execute_kind(connection, kind, audit, idempotency_key, now):
    if kind == 'prediction':
        from match_analysis import analyse_upcoming_batch
        return analyse_upcoming_batch(connection, 'sports-ai-worker', audit, limit=40)
    if kind in ('accumulator', 'settlement'):
        return monitor_accumulators(connection, 'sports-ai-worker', audit, idempotency_key, now)
    if kind == 'model_evaluation':
        return evaluate_models(connection, now)
    if kind == 'retrain_evaluation':
        return {'status': 'skipped', 'reason': 'Retraining runs only from an explicit evaluation request and does not promote a model'}
    if kind == 'browser_research':
        return browser_research_status(connection)
    if kind == 'browser_queue':
        from browser_jobs import enqueue_authorized_jobs
        return enqueue_authorized_jobs(connection)
    if kind == 'booking_queue':
        from booking_schedule import describe
        return {'status': 'deferred', 'places_wager': False, 'schedule': describe(now), 'reason': 'Booking runs in the worker after this transaction, every 15 minutes during testing'}
    if kind == 'wat_window':
        from booking_schedule import describe
        return {'status': 'observed', 'places_wager': False, **describe(now)}
    if kind == 'weekly_draw':
        from weekly_draw_engine import describe_schedule
        return {'status': 'observed', 'places_wager': False, **describe_schedule(now)}
    if kind == 'rollover':
        from rollover_engine import describe_schedule
        return {'status': 'observed', 'places_wager': False, **describe_schedule(now)}
    if kind == 'learning':
        from learning_engine import describe_schedule
        return {'status': 'observed', **describe_schedule()}
    if kind == 'collection':
        return {
            'status': 'complete',
            'reason': 'OpenFootball, OpenLigaDB and SportsDataverse stay on their provider schedules. The worker runs each one when it is due and skips a source that is not due.',
        }
    if kind == 'notification_dispatch':
        return dispatch_notifications(connection, now)
    raise ValueError(f'Unknown job kind {kind}')


def browser_research_status(connection):
    approved = connection.execute(
        """SELECT COUNT(*) FROM sports_ai_source_permissions
           WHERE approved=1 AND hostname NOT LIKE '%betexplorer%'""",
    ).fetchone()
    count = approved[0] if approved else 0
    return {
        'status': 'skipped',
        'approved_sources': count,
        'reason': (
            'Browser research runs only for an administrator-approved host from the acquisition centre. '
            'BetExplorer and Soccerway match history is collected on its own pass, and a score is stored only when the page displays it. '
            'This scheduler does not bypass access controls.'
        ),
    }


def refresh_provider_health(connection, now=None):
    current = _now(now)
    providers = connection.execute('SELECT * FROM sports_ai_provider_registry').fetchall()
    snapshots = []
    for provider in providers:
        failures = connection.execute(
            """SELECT status FROM sports_ai_acquisition_jobs
               WHERE provider_id=? ORDER BY started_at DESC LIMIT 3""",
            (provider['id'],),
        ).fetchall()
        consecutive = 0
        for row in failures:
            if row['status'] == 'failed':
                consecutive += 1
            else:
                break
        paused = 1 if consecutive >= 3 else 0
        if paused:
            connection.execute(
                'UPDATE sports_ai_acquisition_schedules SET paused=1, updated_at=? WHERE provider_id=? AND paused=0',
                (current.isoformat(), provider['id']),
            )
        last_success = _parse(provider['last_success_at'])
        freshness = None if not last_success else (current - last_success).total_seconds() / 60
        status = 'paused' if paused else (provider['status'] or 'untested')
        connection.execute(
            """INSERT INTO sports_ai_provider_health
               (id,provider_id,status,last_success_at,last_error,freshness_minutes,consecutive_failures,paused,details_json,observed_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (str(uuid.uuid4()), provider['id'], status, provider['last_success_at'], provider['last_error'],
             freshness, consecutive, paused, json.dumps({'adapter': provider['adapter']}), current.isoformat()),
        )
        snapshots.append({'provider_id': provider['id'], 'name': provider['display_name'], 'status': status, 'paused': paused,
                          'freshness_minutes': freshness, 'consecutive_failures': consecutive})
    return snapshots


def evaluate_models(connection, now=None):
    """Score stored predictions only when the result was recorded after kickoff."""
    current = _now(now)
    connection.commit()
    rows = connection.execute(
        """SELECT p.model_id,p.market,p.probability,p.created_at,f.sport,f.starts_at,
                  r.home_score,r.away_score,r.settled_at,m.version
           FROM sports_ai_engine_predictions p
           JOIN sports_ai_fixtures f ON f.id=p.fixture_id
           JOIN sports_ai_results r ON r.fixture_id=f.id AND r.status='final'
           LEFT JOIN sports_ai_model_registry m ON m.id=p.model_id""",
    ).fetchall()
    groups = {}
    excluded = 0
    for row in rows:
        created, kickoff, settled = _parse(row['created_at']), _parse(row['starts_at']), _parse(row['settled_at'])
        if not created or not kickoff or not settled or created >= kickoff or settled < kickoff:
            excluded += 1
            continue
        outcome = settle_result(row['sport'], row['market'], row['home_score'], row['away_score'])
        if outcome not in ('won', 'lost'):
            excluded += 1
            continue
        key = (row['model_id'], row['sport'], row['market'])
        groups.setdefault(key, []).append((float(row['probability']), 1 if outcome == 'won' else 0))
    written = []
    for index, ((model_id, sport, market), pairs) in enumerate(groups.items(), start=1):
        metrics = _score(pairs)
        connection.execute(
            """INSERT INTO sports_ai_model_evaluations
               (id,model_id,sport,market,sample_size,brier_score,log_loss,calibration_error,hit_rate,coverage,completeness,metrics_json,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (str(uuid.uuid4()), model_id, sport, market, metrics['sample_size'], metrics['brier_score'], metrics['log_loss'],
             metrics['calibration_error'], metrics['hit_rate'], metrics['coverage'], None, json.dumps(metrics), current.isoformat()),
        )
        if metrics['sample_size'] >= 20 and abs(metrics['probability_mean'] - metrics['hit_rate']) > 0.15:
            connection.execute(
                """INSERT INTO sports_ai_model_drift_events
                   (id,model_id,sport,market,drift_type,metric_value,threshold,details_json,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (str(uuid.uuid4()), model_id, sport, market, 'probability_shift',
                 metrics['probability_mean'] - metrics['hit_rate'], 0.15,
                 json.dumps({'note': 'Drift is a monitoring signal. The active model was not changed.'}), current.isoformat()),
            )
        written.append({'model_id': model_id, 'sport': sport, 'market': market, **metrics, 'hit_rate_is_not_expected_return': True})
        if index % 20 == 0:
            connection.commit()
    return {'status': 'complete', 'evaluations': written, 'excluded': excluded, 'promoted': False}


def _score(pairs):
    import math
    total = len(pairs)
    brier = sum((p - y) ** 2 for p, y in pairs) / total
    log_loss = sum(
        -(y * math.log(min(1 - 1e-12, max(1e-12, p))) + (1 - y) * math.log(min(1 - 1e-12, max(1e-12, 1 - p))))
        for p, y in pairs
    ) / total
    bins = []
    error = 0
    used = 0
    for index in range(10):
        low, high = index / 10, (index + 1) / 10
        chosen = [(p, y) for p, y in pairs if (p >= low and p < high) or (index == 9 and p == 1)]
        if not chosen:
            continue
        confidence = sum(p for p, _ in chosen) / len(chosen)
        frequency = sum(y for _, y in chosen) / len(chosen)
        error += abs(confidence - frequency)
        used += 1
        bins.append({'lower': low, 'upper': high, 'count': len(chosen), 'confidence': confidence, 'frequency': frequency})
    hit = sum(y for _, y in pairs) / total
    return {
        'sample_size': total,
        'brier_score': brier,
        'log_loss': log_loss,
        'calibration_error': error / used if used else None,
        'hit_rate': hit,
        'coverage': 1,
        'probability_mean': sum(p for p, _ in pairs) / total,
        'bins': bins,
        'note': 'Hit rate counts binary wins and is not an expected-return or staking result.',
    }


def evaluate_retraining(connection, sport, actor, audit):
    result = train_model(connection, sport)
    connection.execute(
        """INSERT INTO sports_ai_model_training_runs
           (id,training_run_id,sport,status,promoted,dataset_version,details_json,created_at)
           VALUES(?,?,?,?,0,?,?,?)""",
        (str(uuid.uuid4()), result.get('training_run_id') or result.get('model_id'), sport, result.get('status', 'failed'),
         f"{sport}:{result.get('version', 'candidate')}", json.dumps(redact(result)), _now().isoformat()),
    )
    if audit:
        audit(connection, actor, 'models.retrain_evaluation', {'sport': sport, 'status': result.get('status'), 'promoted': False})
    result['promoted'] = False
    return result


def record_promotion(connection, model_id, action, status, reason, actor):
    connection.execute(
        """INSERT INTO sports_ai_model_promotions (id,model_id,action,approved_by,status,reason,created_at)
           VALUES(?,?,?,?,?,?,?)""",
        (str(uuid.uuid4()), model_id, action, actor, status, reason, _now().isoformat()),
    )


def dispatch_notifications(connection, now=None):
    current = _now(now)
    sent = 0
    rules = connection.execute('SELECT * FROM sports_ai_notification_rules WHERE enabled=1').fetchall()
    events = []
    if any(rule['event_type'] == 'qualified_opportunity' for rule in rules):
        rows = connection.execute(
            "SELECT id FROM sports_ai_value_opportunities WHERE status='qualified' ORDER BY evaluated_at DESC LIMIT 20",
        ).fetchall()
        events.extend(('qualified_opportunity', row['id'], {'opportunity_id': row['id']}) for row in rows)
    for rule in rules:
        for event_type, entity_key, payload in events:
            if rule['event_type'] != event_type:
                continue
            outcome = deliver_notification(connection, rule, entity_key, payload, current)
            if outcome == 'sent':
                sent += 1
    return {'status': 'complete', 'sent': sent}


def deliver_notification(connection, rule, entity_key, payload, now=None):
    current = _now(now).isoformat()
    existing = connection.execute(
        """SELECT id,status FROM sports_ai_notification_deliveries
           WHERE event_type=? AND channel=? AND entity_key=?""",
        (rule['event_type'], rule['channel'], entity_key),
    ).fetchone()
    if existing:
        return 'duplicate'
    status, response = _send(rule['channel'], rule['template'].format(entity_key=entity_key), payload)
    connection.execute(
        """INSERT INTO sports_ai_notification_deliveries
           (id,rule_id,event_type,channel,entity_key,status,provider_response,payload_json,created_at)
           VALUES(?,?,?,?,?,?,?,?,?)""",
        (str(uuid.uuid4()), rule['id'], rule['event_type'], rule['channel'], entity_key, status,
         response[:500], json.dumps(redact(payload)), current),
    )
    if status == 'sent' and rule['channel'] == 'dashboard':
        connection.execute(
            """INSERT INTO sports_ai_notifications (id,channel,event_type,payload_json,status)
               VALUES(?,?,?,?, 'sent')""",
            (str(uuid.uuid4()), 'dashboard', rule['event_type'], json.dumps(redact(payload))),
        )
    return status


def _send(channel, text, payload):
    if channel == 'dashboard':
        return 'sent', 'in-app notification accepted'
    if channel == 'email':
        host = os.environ.get('SPORTS_SMTP_HOST')
        sender = os.environ.get('SPORTS_SMTP_FROM')
        recipient = os.environ.get('SPORTS_ALERT_EMAIL')
        if not host or not sender or not recipient:
            return 'not_configured', 'SMTP host, sender or recipient is not configured'
        message = EmailMessage()
        message['From'] = sender
        message['To'] = recipient
        message['Subject'] = 'CACSMS Sports AI'
        message.set_content(text)
        try:
            with smtplib.SMTP(host, int(os.environ.get('SPORTS_SMTP_PORT', '25')), timeout=10) as client:
                if os.environ.get('SPORTS_SMTP_USER'):
                    client.starttls()
                    client.login(os.environ['SPORTS_SMTP_USER'], os.environ.get('SPORTS_SMTP_PASSWORD', ''))
                client.send_message(message)
            return 'sent', 'smtp accepted the message'
        except (OSError, smtplib.SMTPException) as error:
            return 'failed', str(error)[:300]
    if channel == 'telegram':
        token = os.environ.get('SPORTS_TELEGRAM_BOT_TOKEN')
        chat = os.environ.get('SPORTS_TELEGRAM_CHAT_ID')
        if not token or not chat:
            return 'not_configured', 'Telegram bot token or chat id is not configured'
        body = urllib.parse.urlencode({'chat_id': chat, 'text': text[:3500]}).encode()
        request = urllib.request.Request(
            f'https://api.telegram.org/bot{token}/sendMessage', data=body, method='POST',
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                parsed = json.loads(response.read().decode('utf-8'))
            if response.status == 200 and parsed.get('ok') is True:
                return 'sent', 'telegram accepted the message'
            return 'failed', 'telegram did not accept the message'
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
            return 'failed', str(error)[:300]
    return 'failed', 'unknown channel'


def retry_case(connection, case_id, actor, audit, now=None):
    case = connection.execute('SELECT * FROM sports_ai_recovery_cases WHERE id=?', (case_id,)).fetchone()
    if not case or not case['job_id']:
        return None
    current = _now(now).isoformat()
    connection.execute(
        """UPDATE sports_ai_jobs
           SET status='queued', available_at=?, error_code=NULL, lease_owner=NULL
           WHERE id=? AND status IN ('dead_letter','failed','retry_pending')""",
        (current, case['job_id']),
    )
    connection.execute(
        """UPDATE sports_ai_recovery_cases SET status='retried', updated_at=? WHERE id=?""",
        (current, case_id),
    )
    if audit:
        audit(connection, actor, 'recovery.retry', {'case_id': case_id, 'job_id': case['job_id']})
    return {'case_id': case_id, 'status': 'retried'}


def command_centre(connection):
    def count(query, args=()):
        return connection.execute(query, args).fetchone()[0]
    states = {
        row['state']: row['total']
        for row in connection.execute('SELECT state, COUNT(*) AS total FROM sports_ai_workflows GROUP BY state')
    }
    health = connection.execute(
        """SELECT h.* FROM sports_ai_provider_health h
           JOIN (
             SELECT provider_id, MAX(observed_at) AS observed_at FROM sports_ai_provider_health GROUP BY provider_id
           ) latest ON latest.provider_id=h.provider_id AND latest.observed_at=h.observed_at
           ORDER BY h.status""",
    ).fetchall()
    evaluations = connection.execute(
        """SELECT sport,market,sample_size,brier_score,log_loss,calibration_error,hit_rate,created_at
           FROM sports_ai_model_evaluations ORDER BY created_at DESC LIMIT 12""",
    ).fetchall()
    return {
        'operational': True,
        'fixtures': count('SELECT COUNT(*) FROM sports_ai_fixtures'),
        'fixtures_analysed': count('SELECT COUNT(DISTINCT fixture_id) FROM sports_ai_engine_predictions'),
        'predictions': count('SELECT COUNT(*) FROM sports_ai_engine_predictions'),
        'qualified_opportunities': count("SELECT COUNT(*) FROM sports_ai_value_opportunities WHERE status='qualified'"),
        'accumulator_tickets': count('SELECT COUNT(*) FROM sports_ai_accumulator_tickets'),
        'pending_settlements': count(
            """SELECT COUNT(*) FROM sports_ai_accumulator_tickets t
               WHERE t.status IN ('qualified','expired','review_required','unverified_probability','awaiting_odds')
                 AND NOT EXISTS (SELECT 1 FROM sports_ai_ticket_settlements s WHERE s.ticket_id=t.id)""",
        ),
        'active_jobs': count("SELECT COUNT(*) FROM sports_ai_jobs WHERE status IN ('queued','running','retry_pending')"),
        'failed_jobs': count("SELECT COUNT(*) FROM sports_ai_jobs WHERE status IN ('failed','dead_letter')"),
        'open_incidents': count("SELECT COUNT(*) FROM sports_ai_recovery_cases WHERE status='open'")
            + count("SELECT COUNT(*) FROM sports_ai_data_quality_incidents WHERE status='open'"),
        'workflows_by_state': states,
        'providers': [dict(row) for row in health],
        'model_evaluations': [dict(row) for row in evaluations],
        'recent_events': [dict(row) for row in connection.execute(
            'SELECT fixture_id,from_state,to_state,reason,created_at FROM sports_ai_workflow_events ORDER BY created_at DESC LIMIT 20',
        )],
        'leader': dict(connection.execute('SELECT owner,expires_at,heartbeat_at FROM sports_ai_worker_leases WHERE id=?', (LEADER_ID,)).fetchone() or {}),
        'hit_rate_note': 'Hit rate is a classification count, not evidence of positive expected return.',
    }


def orchestrate_once(audit, collect=None, predict=None, monitor=None, owner=None, now=None):
    """One scheduler pass. A second live worker does not enter while the lease is valid."""
    current = _now(now)
    owner = owner or f"worker:{os.getpid()}"
    with connect() as connection:
        if not acquire_leader(connection, owner, current):
            return {'status': 'skipped', 'reason': 'another worker holds the scheduler lease'}
        recovered = recover_stuck_jobs(connection, current)
        workflows = sync_workflows(connection, current)
        schedules = run_due_schedules(connection, audit, current)
        health = refresh_provider_health(connection, current)
        notes = dispatch_notifications(connection, current)
        evaluation = None
        marker = current.strftime('%Y-%m-%d')
        if enqueue_job(connection, 'model_evaluation', f'model-evaluation:{marker}', now=current)['created']:
            job = connection.execute(
                'SELECT id FROM sports_ai_jobs WHERE idempotency_key=?', (f'model-evaluation:{marker}',),
            ).fetchone()
            try:
                evaluation = evaluate_models(connection, current)
                if job:
                    complete_job(connection, job['id'], evaluation, 'complete', current)
            except Exception as error:
                evaluation = {'status': 'failed', 'error': str(error)[:300]}
                if job:
                    fail_job(connection, job['id'], 'UNHANDLED', error, current)
    if collect:
        try:
            collect()
        except Exception as error:
            print('collection pass failed', str(error))
    if predict:
        try:
            predict()
        except Exception as error:
            print('prediction pass failed', str(error))
    if monitor:
        try:
            monitor()
        except Exception as error:
            print('accumulator pass failed', str(error))
    return {
        'status': 'complete',
        'owner': owner,
        'recovered_jobs': recovered,
        'workflows': workflows,
        'schedules': schedules,
        'providers': len(health),
        'notifications': notes,
        'model_evaluation': evaluation,
    }
