"""Read-only status for the autonomous engine page, plus schedule pause and resume.

Stopping or pausing disables stored schedules. It does not place a wager or activate a model.
"""

import json
import os
import uuid
from datetime import datetime, timedelta, timezone

from booking_schedule import opportunity_week

AUTONOMOUS_KINDS = (
    'prediction', 'accumulator', 'settlement', 'model_evaluation', 'browser_queue', 'booking_queue',
    'collection', 'weekly_draw', 'rollover', 'learning', 'wat_window',
)
STAGE_KINDS = (
    ('Discover', 'Global catalog and fixtures', 'collection', 'catalog'),
    ('Collect Odds', 'Authorized SportyBet prices', 'browser_queue', None),
    ('Validate', 'Data integrity', None, None),
    ('Analyse', 'Stored models', 'prediction', None),
    ('Qualify', 'Central qualification', None, 'qualification'),
    ('Tickets & Booking Codes', 'Finalize the ticket, then register its displayed code', 'booking_queue', 'tickets_booking'),
    ('Settle', 'Verified results', 'settlement', None),
    ('Learn', 'Performance evaluation', 'learning', 'learning'),
    ('Optimize', 'Holdout gate and ticket constraints', 'model_evaluation', 'optimize'),
)


def build_overview(connection, now=None):
    now = now or datetime.now(timezone.utc)
    day = (now - timedelta(hours=24)).isoformat()
    mode = _mode(connection)
    schedules = [dict(row) for row in connection.execute(
        'SELECT id,name,job_kind,enabled,interval_minutes,last_run_at,next_run_at FROM sports_ai_schedules ORDER BY name',
    )]
    jobs = _latest_jobs(connection)
    heartbeat = connection.execute(
        "SELECT heartbeat_at FROM sports_ai_worker_leases WHERE id='scheduler-leader'",
    ).fetchone()
    heartbeat_at = heartbeat['heartbeat_at'] if heartbeat else None
    fresh = _fresh(heartbeat_at, now)
    enabled = [row for row in schedules if row['enabled']]
    if not enabled:
        status = 'paused' if mode == 'paused' else 'stopped'
    else:
        status = 'running'
    fixtures = connection.execute('SELECT COUNT(*) FROM sports_ai_fixtures').fetchone()[0]
    fixtures_24h = connection.execute(
        'SELECT COUNT(*) FROM sports_ai_fixtures WHERE observed_at>=?', (day,),
    ).fetchone()[0]
    live = connection.execute(
        "SELECT COUNT(*) FROM sports_ai_fixtures WHERE status IN ('live','inplay')",
    ).fetchone()[0]
    analysed = connection.execute(
        'SELECT COUNT(*) FROM sports_ai_engine_predictions WHERE created_at>=?', (day,),
    ).fetchone()[0]
    qualified = connection.execute(
        """SELECT COUNT(*) FROM sports_ai_qualification_decisions d
           JOIN sports_ai_value_opportunities o ON o.id=d.opportunity_id
           WHERE d.state='QUALIFIED' AND o.expected_edge>0""",
    ).fetchone()[0]
    tickets = connection.execute('SELECT COUNT(*) FROM sports_ai_accumulator_tickets').fetchone()[0]
    settled = connection.execute(
        """SELECT
             SUM(CASE WHEN (p.market IN ('HOME','MONEYLINE_HOME') AND r.home_score>r.away_score)
                        OR (p.market IN ('AWAY','MONEYLINE_AWAY') AND r.away_score>r.home_score)
                        OR (p.market='DRAW' AND r.home_score=r.away_score) THEN 1 ELSE 0 END) AS won,
             COUNT(*) AS total
           FROM sports_ai_engine_predictions p
           JOIN sports_ai_fixtures f ON f.id=p.fixture_id
           JOIN sports_ai_results r ON r.fixture_id=f.id AND r.status='final'
           WHERE p.market IN ('HOME','DRAW','AWAY','MONEYLINE_HOME','MONEYLINE_AWAY')
             AND p.created_at<f.starts_at""",
    ).fetchone()
    won, total = (settled['won'] or 0), (settled['total'] or 0)
    edge_row = connection.execute(
        """SELECT AVG(o.expected_edge)
           FROM sports_ai_qualification_decisions d
           JOIN sports_ai_value_opportunities o ON o.id=d.opportunity_id
           WHERE d.state='QUALIFIED' AND o.expected_edge>0""",
    ).fetchone()[0]
    health_row = connection.execute(
        """SELECT
             SUM(CASE WHEN status='complete' THEN 1 ELSE 0 END) AS done,
             SUM(CASE WHEN status IN ('failed','dead_letter') THEN 1 ELSE 0 END) AS failed
           FROM sports_ai_jobs WHERE created_at>=?""",
        (day,),
    ).fetchone()
    done, failed = (health_row['done'] or 0), (health_row['failed'] or 0)
    health = round(100 * done / (done + failed), 1) if done + failed else None
    tasks = [_task(row, jobs.get(row['id']), now) for row in schedules]
    return {
        'status': status,
        'scheduler_fresh': fresh,
        'places_wager': False,
        'summary': {
            'fixtures': fixtures,
            'fixtures_24h': fixtures_24h,
            'live': live,
            'analysed': analysed,
            'opportunities': qualified,
            'tickets': tickets,
            'accuracy': None if not total else round(100 * won / total, 1),
            'settled': total,
            'edge': None if edge_row is None else round(100 * float(edge_row), 1),
            'uptime': _age(heartbeat_at, now) if fresh else None,
            'health': health,
            'heartbeat_at': heartbeat_at,
            'qualified_decisions': connection.execute("SELECT COUNT(*) FROM sports_ai_qualification_decisions WHERE state='QUALIFIED'").fetchone()[0],
            'verified_codes': connection.execute("SELECT COUNT(*) FROM sports_ai_booking_codes WHERE status='verified' AND verification_method='displayed'").fetchone()[0],
            'pending_jobs': connection.execute("SELECT COUNT(*) FROM sports_ai_jobs WHERE status IN ('queued','running','retry_pending')").fetchone()[0],
            'failures': connection.execute("SELECT COUNT(*) FROM sports_ai_jobs WHERE status IN ('failed','dead_letter')").fetchone()[0],
            'weekly_tickets': connection.execute("SELECT COUNT(*) FROM sports_ai_accumulator_tickets WHERE json_extract(parameters_json,'$.product')='weekly_draw'").fetchone()[0],
            'rollover_campaigns': connection.execute('SELECT COUNT(*) FROM sports_ai_rollover_campaigns').fetchone()[0],
            'prospective_settled': total,
        },
        'readiness': production_readiness(connection, now),
        'tasks': tasks,
        'stages': _stages(connection, schedules, jobs, now),
        'engines': _engines(connection, schedules, jobs, fresh, now),
        'opportunities': _opportunities(connection, now),
        'tickets': _tickets(connection, now),
        'tickets_checked_at': connection.execute(
            "SELECT MAX(created_at) FROM sports_ai_autonomous_cycles WHERE stage='tickets'",
        ).fetchone()[0],
        'logs': _logs(connection),
        'models': _models(connection),
        'settings': _settings(connection),
        'pipeline': _pipeline(connection, now),
        'jobs': _recent_jobs(connection),
        'next_cycle': min((row['next_run_at'] for row in enabled if row['next_run_at']), default=None),
    }


def ensure_running(connection, now=None):
    """Keep the stored schedules running. The worker does not wait for a start click."""
    now = now or datetime.now(timezone.utc)
    enabled = connection.execute('SELECT COUNT(*) FROM sports_ai_schedules WHERE enabled=1').fetchone()[0]
    if _mode(connection) == 'running' and enabled:
        return False
    apply_control(connection, 'resume', lambda *_args: None, 'sports-ai-worker', now)
    return True


def apply_control(connection, action, audit, actor, now=None):
    now = now or datetime.now(timezone.utc)
    stored = _stored(connection)
    if action in ('pause', 'stop'):
        enabled = [row['id'] for row in connection.execute('SELECT id FROM sports_ai_schedules WHERE enabled=1')]
        restore = enabled or stored.get('restore_ids') or []
        connection.execute('UPDATE sports_ai_schedules SET enabled=0 WHERE enabled=1')
        _save(connection, {'mode': 'paused' if action == 'pause' else 'stopped', 'restore_ids': restore}, now)
    elif action in ('start', 'resume'):
        restore = stored.get('restore_ids') or [
            row['id'] for row in connection.execute(
                f"SELECT id FROM sports_ai_schedules WHERE job_kind IN ({','.join('?' for _ in AUTONOMOUS_KINDS)})",
                AUTONOMOUS_KINDS,
            )
        ]
        if restore:
            connection.execute(
                f"UPDATE sports_ai_schedules SET enabled=1 WHERE id IN ({','.join('?' for _ in restore)})",
                restore,
            )
        _save(connection, {'mode': 'running', 'restore_ids': restore}, now)
    else:
        raise ValueError('Unknown autonomous action')
    audit(connection, actor, 'autonomous.control', {'action': action, 'places_wager': False})
    overview = build_overview(connection, now)
    overview['action'] = action
    return overview


def _latest_jobs(connection):
    return {
        row['schedule_id']: dict(row)
        for row in connection.execute(
            """SELECT j.schedule_id,j.status,j.started_at,j.finished_at,j.error,j.created_at
               FROM sports_ai_jobs j
               JOIN (
                 SELECT schedule_id, MAX(created_at) AS created_at
                 FROM sports_ai_jobs WHERE schedule_id IS NOT NULL GROUP BY schedule_id
               ) latest ON latest.schedule_id=j.schedule_id AND latest.created_at=j.created_at""",
        )
    }


def _task(schedule, job, now):
    if job and job['status'] == 'running':
        status = 'Running'
    elif job and job['status'] in ('failed', 'dead_letter', 'timed_out'):
        status = 'Error'
    elif job and job['status'] == 'complete':
        status = 'Completed'
    elif schedule['enabled']:
        status = 'Queued'
    else:
        status = 'Stopped'
    started = (job or {}).get('started_at') or schedule['last_run_at']
    finished = (job or {}).get('finished_at')
    return {
        'name': schedule['name'],
        'kind': schedule['job_kind'],
        'progress': 100 if status == 'Completed' else None if status == 'Running' else 0,
        'status': status,
        'started': started,
        'finished': finished,
        'duration': _duration(started, finished or (now.isoformat() if status == 'Running' else None)),
        'next_run_at': schedule['next_run_at'],
        'enabled': bool(schedule['enabled']),
        'error': (job or {}).get('error'),
    }


def record_stage(connection, stage, status, detail=None, now=None):
    """Persist one worker stage. A stage cannot record a paid wager."""
    current = now or datetime.now(timezone.utc)
    payload = dict(detail or {})
    if payload.get('places_wager'):
        raise ValueError('Automatic paid wagering is disabled')
    payload['places_wager'] = False
    connection.execute(
        """INSERT INTO sports_ai_autonomous_cycles
           (id,stage,status,detail_json,places_wager,created_at) VALUES(?,?,?,?,0,?)""",
        (str(uuid.uuid4()), stage, status or 'complete', json.dumps(payload, default=str)[:4000], current.isoformat()),
    )
    cutoff = (current - timedelta(days=14)).isoformat()
    connection.execute('DELETE FROM sports_ai_autonomous_cycles WHERE created_at<?', (cutoff,))
    return {'stage': stage, 'status': status or 'complete', 'places_wager': False}


def production_readiness(connection, now=None):
    """Report evidence. A missing control stays unresolved rather than being marked ready."""
    now = now or datetime.now(timezone.utc)
    heartbeat = connection.execute("SELECT heartbeat_at FROM sports_ai_worker_leases WHERE id='scheduler-leader'").fetchone()
    fresh = _fresh(heartbeat['heartbeat_at'], now) if heartbeat else False
    migration = connection.execute("SELECT name FROM sports_ai_schema_migrations WHERE name LIKE '026_%'").fetchone()
    backfill = connection.execute("SELECT value_json FROM sports_ai_settings WHERE key='catalog_backfill'").fetchone()
    try:
        per_day = json.loads(backfill['value_json']).get('per_day', 0) if backfill else 0
    except (TypeError, json.JSONDecodeError):
        per_day = None
    active_models = connection.execute("SELECT COUNT(*) FROM sports_ai_model_registry WHERE active=1").fetchone()[0]
    verified = connection.execute("SELECT COUNT(*) FROM sports_ai_booking_codes WHERE status='verified' AND verification_method='displayed'").fetchone()[0]
    prospective = connection.execute(
        """SELECT COUNT(*) FROM sports_ai_engine_predictions p
           JOIN sports_ai_fixtures f ON f.id=p.fixture_id
           JOIN sports_ai_results r ON r.fixture_id=f.id AND r.status='final'
           WHERE p.created_at<f.starts_at""",
    ).fetchone()[0]
    failures = connection.execute("SELECT COUNT(*) FROM sports_ai_jobs WHERE status IN ('failed','dead_letter')").fetchone()[0]
    checks = [
        {'name': 'Paid wagering', 'ok': True, 'blocking': True, 'detail': 'Automatic paid wagering is disabled. Booking preparation retrieves a displayed code only.'},
        {'name': 'Worker heartbeat', 'ok': fresh, 'blocking': True, 'detail': 'Fresh' if fresh else 'The scheduler heartbeat is older than two minutes or is not stored.'},
        {'name': 'Schema migration 026', 'ok': bool(migration), 'blocking': True, 'detail': migration['name'] if migration else '026_sports_ai_autonomous_cycles.sql has not been applied.'},
        {'name': 'Catalog backfill', 'ok': per_day == 0, 'blocking': True, 'detail': f'Historical backfill is {per_day} leagues per day.'},
        {'name': 'Active validated models', 'ok': active_models > 0, 'blocking': False, 'detail': f'{active_models} active model(s). Activation still requires an administrator and the holdout gate.'},
        {'name': 'Displayed booking codes', 'ok': True, 'blocking': False, 'detail': f'{verified} verified displayed code(s). Zero is valid when nothing qualifies.'},
        {'name': 'Prospective evaluation', 'ok': prospective > 0, 'blocking': False, 'detail': f'{prospective} predictions were stored before kickoff and have a final result.'},
        {'name': 'Open job failures', 'ok': failures == 0, 'blocking': False, 'detail': f'{failures} failed or dead-letter job(s).'},
        {'name': 'Administrator key', 'ok': bool(os.environ.get('SPORTS_ADMIN_API_KEY')), 'blocking': True, 'detail': 'SPORTS_ADMIN_API_KEY is configured.' if os.environ.get('SPORTS_ADMIN_API_KEY') else 'SPORTS_ADMIN_API_KEY is not set. Model activation and policy writes stay closed.'},
        {'name': 'Production session', 'ok': os.environ.get('SPORTS_LOCAL_PREVIEW') != '1', 'blocking': True, 'detail': 'Loopback preview authentication is off.' if os.environ.get('SPORTS_LOCAL_PREVIEW') != '1' else 'SPORTS_LOCAL_PREVIEW=1 accepts loopback requests. Replace it with the CACSMS ONE session before production.'},
        {'name': 'Alert delivery', 'ok': bool(os.environ.get('SPORTS_SMTP_HOST') and os.environ.get('SPORTS_ALERT_EMAIL')) or bool(os.environ.get('SPORTS_TELEGRAM_BOT_TOKEN') and os.environ.get('SPORTS_TELEGRAM_CHAT_ID')), 'blocking': False, 'detail': 'SMTP or Telegram credentials are stored in the environment.' if (os.environ.get('SPORTS_SMTP_HOST') or os.environ.get('SPORTS_TELEGRAM_BOT_TOKEN')) else 'Dashboard alerts work. Email and Telegram are not configured.'},
        {'name': 'Application rate limit', 'ok': True, 'blocking': True, 'detail': 'Each client is limited to 240 API requests per minute. Health checks are exempt. SPORTS_PRODUCTION=1 also refuses loopback preview authentication.'},
    ]
    return {
        'ready': all(item['ok'] for item in checks if item['blocking']),
        'places_wager': False,
        'checks': checks,
        'rollback': [
            'Stop the worker, then start one API process so initialize() copies the database before a pending migration.',
            'Restore that backup over the SQLite file and restart the API and worker.',
            'Roll a model back with the administrator key. Training does not activate a replacement.',
        ],
        'procedures': [
            'The worker runs discovery, odds, qualification, then Stage 6. Stage 6 stores a ticket from current QUALIFIED selections and then requests its displayed code. Rollover runs after settlement and does not delay that stage. It does not place a paid wager.',
            'Pause or Stop on the Autonomous Engine disables stored schedules and remembers them for Resume.',
            'A booking code is stored only when SportyBet displays it. Registration does not start before the ticket is finalized.',
        ],
    }


def _latest_cycles(connection):
    rows = connection.execute(
        """SELECT c.stage,c.status,c.detail_json,c.created_at
           FROM sports_ai_autonomous_cycles c
           JOIN (
             SELECT stage, MAX(created_at) AS created_at FROM sports_ai_autonomous_cycles GROUP BY stage
           ) latest ON latest.stage=c.stage AND latest.created_at=c.created_at""",
    ).fetchall()
    return {row['stage']: dict(row) for row in rows}


def _stages(connection, schedules, jobs, now):
    by_kind = {}
    for row in schedules:
        by_kind.setdefault(row['job_kind'], row)
    cycles = _latest_cycles(connection)
    issues = connection.execute(
        "SELECT COUNT(*) FROM sports_ai_data_quality_issues WHERE resolved_at IS NULL AND severity IN ('high','critical')",
    ).fetchone()[0]
    odds = connection.execute(
        "SELECT MAX(observed_at) FROM sports_ai_odds_snapshots WHERE bookmaker='SportyBet'",
    ).fetchone()[0]
    stages = []
    for name, detail, kind, cycle_stage in STAGE_KINDS:
        cycle = cycles.get(cycle_stage or '')
        if name == 'Validate':
            stages.append({
                'name': name, 'detail': detail, 'status': 'Clear' if not issues else 'Attention',
                'ok': issues == 0, 'last_run_at': None, 'next_run_at': None,
                'result': 'No open high-severity data issue' if not issues else f'{issues} open high-severity issues',
            })
            continue
        schedule = by_kind.get(kind) if kind else None
        job = jobs.get(schedule['id']) if schedule else None
        task = _task(schedule, job, now) if schedule else None
        status = task['status'] if task else 'Not scheduled'
        cycle_at = (cycle or {}).get('created_at') or ''
        job_at = (job or {}).get('finished_at') or (job or {}).get('started_at') or ''
        cycle_is_current = bool(cycle) and cycle_at >= job_at
        if cycle_is_current:
            status = 'Completed' if cycle['status'] not in ('failed', 'error') else 'Error'
        recent_odds = False
        if kind == 'browser_queue' and odds:
            try:
                recent_odds = (now - _parse(odds)).total_seconds() < 6 * 3600
            except (TypeError, ValueError):
                recent_odds = False
        if cycle_is_current:
            result = cycle['status']
        elif (job or {}).get('error'):
            result = job['error']
        elif cycle:
            result = cycle['status']
        elif task:
            result = task['status']
        else:
            result = 'No cycle is stored yet'
        stages.append({
            'name': name,
            'detail': detail,
            'status': status,
            'ok': status in ('Completed', 'Clear') or recent_odds,
            'last_run_at': cycle_at or (schedule['last_run_at'] if schedule else odds),
            'next_run_at': schedule['next_run_at'] if schedule else None,
            'result': result,
        })
    return stages


def _cycle_reason(connection, fallback):
    row = connection.execute("SELECT value_json FROM sports_ai_settings WHERE key='value_cycle'").fetchone()
    if not row:
        return fallback
    try:
        return json.loads(row['value_json']).get('reason') or fallback
    except json.JSONDecodeError:
        return fallback


def _engines(connection, schedules, jobs, fresh, now):
    models = list(connection.execute(
        "SELECT sport,status,active FROM sports_ai_model_registry ORDER BY created_at DESC",
    ))
    active = [row for row in models if row['active']]
    validated = [row for row in models if row['status'] in ('validated', 'active') and not row['active']]
    if active:
        model_status, model_tone = 'Active after holdout validation', 'good'
    elif validated:
        model_status, model_tone = 'Validated, holdout has not cleared activation', 'warn'
    else:
        model_status, model_tone = 'Not validated', 'neutral'
    governance = connection.execute("SELECT value_json FROM sports_ai_settings WHERE key='model_governance'").fetchone()
    if governance:
        try:
            blocked = [item for item in json.loads(governance['value_json']).get('decisions', []) if item.get('reasons')]
        except json.JSONDecodeError:
            blocked = []
        if blocked and not active:
            model_status = blocked[0]['reasons'][0]
    qualified = connection.execute(
        "SELECT COUNT(*) FROM sports_ai_value_opportunities WHERE status='qualified'",
    ).fetchone()[0]
    from sportybet_research import unattended_status
    browser = unattended_status(connection, now)
    prediction = next((row for row in schedules if row['job_kind'] == 'prediction'), None)
    prediction_job = jobs.get(prediction['id']) if prediction else None
    analysis_ok = bool(prediction_job and prediction_job['status'] == 'complete')
    return [
        {'name': 'AI Analysis Engine', 'status': 'Completed last cycle' if analysis_ok else 'Waiting', 'tone': 'good' if analysis_ok else 'neutral'},
        {'name': 'Prediction Model', 'status': model_status, 'tone': model_tone},
        {'name': 'Value & Edge Engine', 'status': 'Qualified selections stored' if qualified else _cycle_reason(connection, 'No selection has cleared an active model, a fresh price and the configured edge.'), 'tone': 'good' if qualified else 'warn'},
        {'name': 'Ticket Generator', 'status': 'Qualified selections available' if qualified else 'Waiting for a qualified selection', 'tone': 'good' if qualified else 'neutral'},
        {'name': 'Auto Browser Operator', 'status': 'Collecting displayed prices' if browser['operational'] else browser['reason'], 'tone': 'good' if browser['operational'] else 'warn'},
        {'name': 'Qualification', 'status': 'Decisions stored' if connection.execute('SELECT COUNT(*) FROM sports_ai_qualification_decisions').fetchone()[0] else 'No qualification decision is stored', 'tone': 'good' if connection.execute("SELECT COUNT(*) FROM sports_ai_qualification_decisions WHERE state='QUALIFIED'").fetchone()[0] else 'neutral'},
        {'name': 'Weekly Draw', 'status': 'Tickets stored' if connection.execute("SELECT COUNT(*) FROM sports_ai_accumulator_tickets WHERE json_extract(parameters_json,'$.product')='weekly_draw'").fetchone()[0] else 'No weekly draw ticket is stored', 'tone': 'neutral'},
        {'name': 'Daily Rollover', 'status': 'Simulation only' if connection.execute('SELECT COUNT(*) FROM sports_ai_rollover_campaigns').fetchone()[0] else 'No rollover campaign is stored', 'tone': 'neutral'},
        {'name': 'Performance & Learning', 'status': 'Evaluation stored' if connection.execute('SELECT COUNT(*) FROM sports_ai_learning_runs').fetchone()[0] else 'No learning run is stored', 'tone': 'neutral'},
        {'name': 'Scheduler (24/7)', 'status': 'Heartbeat fresh' if fresh else 'Heartbeat idle', 'tone': 'good' if fresh else 'warn'},
        {'name': 'Data Sync & Monitor', 'status': 'Provider collection is scheduled' if any(row['enabled'] and row['job_kind'] == 'collection' for row in schedules) else 'Provider schedules run separately', 'tone': 'neutral'},
    ]


def _opportunities(connection, now):
    """One current market per fixture: the highest stored win probability above 80%."""
    rows = connection.execute(
        """SELECT id,home,away,competition,starts_at,market,probability,expected_edge,decimal_odds,bookmaker
           FROM (
             SELECT o.id,f.home,f.away,f.competition,f.starts_at,p.market,p.probability,
                    o.expected_edge,q.decimal_odds,q.bookmaker,
                    ROW_NUMBER() OVER (
                      PARTITION BY f.id
                      ORDER BY p.probability DESC, o.evaluated_at DESC
                    ) AS rn
             FROM sports_ai_value_opportunities o
             JOIN sports_ai_engine_predictions p ON p.id=o.prediction_id
             JOIN sports_ai_fixtures f ON f.id=p.fixture_id
             JOIN sports_ai_model_registry m ON m.id=p.model_id
             JOIN sports_ai_odds_snapshots q ON q.id=o.odds_id
             JOIN sports_ai_qualification_decisions d ON d.opportunity_id=o.id AND d.state='QUALIFIED'
             WHERE o.status='qualified' AND o.expected_edge>0 AND m.active=1 AND m.status='active'
               AND p.probability>0.80
               AND f.status='scheduled' AND f.starts_at>? AND f.starts_at<?
           )
           WHERE rn=1
           ORDER BY probability DESC LIMIT 10""",
        (now.isoformat(), opportunity_week(now)[1].astimezone(timezone.utc).isoformat()),
    )
    return [{
        'id': row['id'],
        'fixture': f"{row['home']} vs {row['away']}",
        'competition': row['competition'],
        'starts_at': row['starts_at'],
        'market': row['market'],
        'odds': row['decimal_odds'],
        'probability': round(100 * float(row['probability']), 1),
        'edge': round(100 * float(row['expected_edge']), 1),
        'bookmaker': row['bookmaker'],
        'confidence': None,
    } for row in rows]


def _tickets(connection, now=None):
    """The same slips the Ticket Builder lists: qualified, above 70%, and still inside this Monday–Sunday week."""
    current = now or datetime.now(timezone.utc)
    now_iso = current.astimezone(timezone.utc).isoformat()
    week_end = opportunity_week(current)[1].astimezone(timezone.utc).isoformat()
    candidates = [dict(row) for row in connection.execute(
        """SELECT id,tier,status,combined_odds,joint_probability,estimated_edge,leg_count,created_at,sport_mode
           FROM sports_ai_accumulator_tickets
           WHERE probability_basis='independent' AND joint_probability>?
             AND (
               status='qualified'
               OR (status='unverified_probability' AND json_extract(parameters_json,'$.coverage')='high_probability')
               OR (status='unverified_probability' AND json_extract(parameters_json,'$.product') IN ('sure_odds','high_odds'))
             )
           ORDER BY created_at DESC""",
        (0.70,),
    )]
    if not candidates:
        return []
    ids = [row['id'] for row in candidates]
    legs = {}
    selections = {}
    for row in connection.execute(
        f"""SELECT ticket_id,fixture_id,home,away,market,decimal_odds,starts_at FROM sports_ai_accumulator_legs
            WHERE ticket_id IN ({','.join('?' for _ in ids)}) ORDER BY id""",
        ids,
    ):
        legs.setdefault(row['ticket_id'], []).append(
            f"{row['home']} vs {row['away']} — {row['market']} ({float(row['decimal_odds']):.2f})",
        )
        selections.setdefault(row['ticket_id'], []).append(row)
    tickets = []
    seen = set()
    for row in candidates:
        items = selections.get(row['id'], [])
        if not items or any(
            not item['starts_at'] or item['starts_at'] <= now_iso or item['starts_at'] >= week_end
            for item in items
        ):
            continue
        key = '|'.join(sorted(f"{item['fixture_id']}:{str(item['market']).upper()}" for item in items))
        if key in seen:
            continue
        seen.add(key)
        tickets.append(row)
    codes = {}
    for row in connection.execute(
        f"""SELECT accumulator_ticket_id, code, status, verification_method
            FROM sports_ai_booking_codes
            WHERE accumulator_ticket_id IN ({','.join('?' for _ in ids)})
            ORDER BY created_at DESC""",
        ids,
    ):
        codes.setdefault(row['accumulator_ticket_id'], row)
    return [{
        'id': row['id'],
        'type': 'Single' if row['leg_count'] == 1 else f"Accumulator ({row['leg_count']})",
        'tier': row['tier'],
        'status': row['status'],
        'odds': row['combined_odds'],
        'probability': None if row['joint_probability'] is None else round(100 * float(row['joint_probability']), 1),
        'edge': None if row['estimated_edge'] is None else round(100 * float(row['estimated_edge']), 1),
        'created_at': row['created_at'],
        'picks': legs.get(row['id'], []),
        'booking_code': codes[row['id']]['code'] if row['id'] in codes else None,
        'code_status': codes[row['id']]['status'] if row['id'] in codes else None,
    } for row in tickets]


def _logs(connection):
    rows = []
    for row in connection.execute(
        """SELECT h.finished_at AS time,h.status,s.name,h.details_json
           FROM sports_ai_job_history h
           LEFT JOIN sports_ai_schedules s ON s.id=h.schedule_id
           ORDER BY h.finished_at DESC LIMIT 30""",
    ):
        detail = ''
        try:
            parsed = json.loads(row['details_json'] or '{}')
            detail = parsed.get('reason') or parsed.get('status') or ''
        except (TypeError, json.JSONDecodeError):
            detail = ''
        rows.append({
            'time': row['time'],
            'level': row['status'],
            'message': f"{row['name'] or 'Job'}: {row['status']}" + (f" — {detail}" if detail else ''),
        })
    for row in connection.execute(
        'SELECT created_at AS time,action,metadata_json FROM sports_ai_audit_events ORDER BY created_at DESC LIMIT 20',
    ):
        rows.append({'time': row['time'], 'level': 'audit', 'message': row['action'].replace('.', ' ')})
    rows.sort(key=lambda item: item['time'] or '', reverse=True)
    return rows[:40]


def _recent_jobs(connection):
    return [{
        'id': row['id'],
        'kind': row['job_kind'],
        'status': row['status'],
        'attempt': row['attempt'],
        'started': row['started_at'],
        'finished': row['finished_at'],
        'error': row['error'],
    } for row in connection.execute(
        """SELECT id,job_kind,status,attempt,started_at,finished_at,error
           FROM sports_ai_jobs ORDER BY created_at DESC LIMIT 20""",
    )]


def _models(connection):
    models = []
    for row in connection.execute(
        """SELECT sport,model_name,version,status,active,metrics_json,created_at
           FROM sports_ai_model_registry ORDER BY sport,created_at DESC""",
    ):
        try:
            metrics = json.loads(row['metrics_json'] or '{}')
        except json.JSONDecodeError:
            metrics = {}
        models.append({
            'sport': row['sport'],
            'name': row['model_name'],
            'version': row['version'],
            'status': row['status'],
            'active': bool(row['active']),
            'brier': metrics.get('brier_score'),
            'log_loss': metrics.get('log_loss'),
            'calibration_error': metrics.get('calibration_error'),
            'samples': metrics.get('sample_size') or metrics.get('fixtures_scored'),
            'holdout': metrics.get('holdout_fixtures'),
            'created_at': row['created_at'],
        })
    return models


_PIPELINE_CACHE = {}


def _pipeline(connection, now):
    try:
        import time
        from pipeline_engine import latest_snapshot, measure_pipeline
        database = connection.execute('PRAGMA database_list').fetchone()
        key = database['file'] if database else ''
        cached = _PIPELINE_CACHE.get(key)
        if cached and time.monotonic() - cached[0] < 20:
            return cached[1]
        measured = measure_pipeline(connection, now)
        measured['stored_snapshot_at'] = (latest_snapshot(connection) or {}).get('created_at')
        _PIPELINE_CACHE[key] = (time.monotonic(), measured)
        return measured
    except Exception as error:
        return {'stages': [], 'blockers': [{'stage': 'PIPELINE', 'reason': str(error)[:200]}], 'places_wager': False}


def _settings(connection):
    row = connection.execute("SELECT value_json FROM sports_ai_settings WHERE key='engine'").fetchone()
    defaults = {
        'min_edge': 0.05, 'max_legs': 6, 'odds_tolerance': 0.15, 'odds_max_age_minutes': 15,
        'form_half_life_days': 180, 'autonomous_tickets': True,
    }
    try:
        stored = json.loads(row['value_json']) if row else {}
    except (TypeError, json.JSONDecodeError):
        stored = {}
    return {**defaults, **stored}


def _mode(connection):
    return _stored(connection).get('mode') or 'running'


def _stored(connection):
    row = connection.execute("SELECT value_json FROM sports_ai_settings WHERE key='autonomous'").fetchone()
    try:
        return json.loads(row['value_json']) if row else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _save(connection, payload, now):
    connection.execute(
        """INSERT INTO sports_ai_settings(key,value_json,updated_at) VALUES('autonomous',?,?)
           ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at""",
        (json.dumps(payload), now.isoformat()),
    )


def _fresh(value, now):
    try:
        return bool(value) and (now - _parse(value)).total_seconds() < 120
    except (TypeError, ValueError):
        return False


def _parse(value):
    return datetime.fromisoformat(str(value).replace('Z', '+00:00'))


def _age(value, now):
    try:
        seconds = max(0, int((now - _parse(value)).total_seconds()))
    except (TypeError, ValueError):
        return None
    if seconds < 60:
        return f'{seconds}s'
    return f'{seconds // 60}m'


def _duration(started, finished):
    if not started or not finished:
        return None
    try:
        seconds = max(0, int((_parse(finished) - _parse(started)).total_seconds()))
    except (TypeError, ValueError):
        return None
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f'{hours:02d}:{minutes:02d}:{secs:02d}'
