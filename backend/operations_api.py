"""Operations HTTP API. Mutations require the admin key; views use the Sports AI session."""

import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from db import connect
from acquisition_engine import connection_diagnostics
from operations_engine import (
    STATES,
    browser_research_status,
    command_centre,
    cron_is_due,
    enqueue_job,
    evaluate_retraining,
    record_promotion,
    retry_case,
)


class ScheduleIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    interval_minutes: int = Field(ge=5, le=10080)
    job_kind: str = Field(min_length=2, max_length=40)
    cron_expression: str | None = None
    enabled: bool = False


class ScheduleUpdate(BaseModel):
    enabled: bool


class ManualJobIn(BaseModel):
    job_kind: str = Field(min_length=2, max_length=40)
    idempotency_key: str | None = Field(default=None, max_length=160)


class RuleIn(BaseModel):
    event_type: str = Field(min_length=2, max_length=80)
    channel: str
    enabled: bool = False
    template: str = Field(min_length=2, max_length=300)


def build_operations_router(auth, require_admin):
    router = APIRouter(prefix='/api/sports-ai/operations', tags=['Operations'])

    def audit(connection, actor, action, details):
        connection.execute(
            'INSERT INTO sports_ai_audit_events(id,actor,action,metadata_json) VALUES(?,?,?,?)',
            (str(uuid.uuid4()), actor, action, json.dumps(details)),
        )

    def admin(x_sports_admin_key: str | None = Header(default=None)):
        require_admin(x_sports_admin_key)

    @router.get('/diagnostics')
    def diagnostics(user=Depends(auth)):
        with connect() as connection:
            return connection_diagnostics(connection)

    @router.get('/command')
    def command(user=Depends(auth)):
        with connect() as connection:
            payload = command_centre(connection)
        payload['states'] = list(STATES)
        payload['browser_research'] = {'status': 'manual_until_approved'}
        return payload

    @router.get('/workflows')
    def workflows(state: str | None = None, user=Depends(auth)):
        query = 'SELECT * FROM sports_ai_workflows'
        args = ()
        if state:
            if state not in STATES:
                raise HTTPException(422, 'Unknown workflow state')
            query += ' WHERE state=?'
            args = (state,)
        query += ' ORDER BY updated_at DESC LIMIT 200'
        with connect() as connection:
            return [dict(row) for row in connection.execute(query, args)]

    @router.get('/workflows/{workflow_id}/events')
    def workflow_events(workflow_id: str, user=Depends(auth)):
        with connect() as connection:
            return [dict(row) for row in connection.execute(
                'SELECT * FROM sports_ai_workflow_events WHERE workflow_id=? ORDER BY created_at', (workflow_id,),
            )]

    @router.get('/schedules')
    def schedules(user=Depends(auth)):
        with connect() as connection:
            rows = [dict(row) for row in connection.execute('SELECT * FROM sports_ai_schedules ORDER BY name')]
        for row in rows:
            row['stored_timezone'] = 'UTC'
        return rows

    @router.post('/schedules')
    def add_schedule(payload: ScheduleIn, user=Depends(auth), _admin=Depends(admin)):
        if payload.cron_expression:
            try:
                cron_is_due(payload.cron_expression, None, datetime.now(timezone.utc))
            except ValueError as error:
                raise HTTPException(422, str(error)) from error
        schedule_id = str(uuid.uuid4())
        with connect() as connection:
            connection.execute(
                """INSERT INTO sports_ai_schedules
                   (id,name,interval_minutes,enabled,job_kind,cron_expression,timezone)
                   VALUES(?,?,?,?,?,?, 'UTC')""",
                (schedule_id, payload.name, payload.interval_minutes, int(payload.enabled), payload.job_kind, payload.cron_expression),
            )
            audit(connection, user, 'operations.schedule', {'id': schedule_id, 'permission': 'operations.schedules'})
        return {'id': schedule_id, 'saved': True, 'note': 'Stored in UTC. backend/worker.py is the only process that runs it.'}

    @router.put('/schedules/{schedule_id}')
    def update_schedule(schedule_id: str, payload: ScheduleUpdate, user=Depends(auth), _admin=Depends(admin)):
        with connect() as connection:
            cursor = connection.execute(
                'UPDATE sports_ai_schedules SET enabled=? WHERE id=?',
                (int(payload.enabled), schedule_id),
            )
            if cursor.rowcount != 1:
                raise HTTPException(404, 'Schedule not found')
            audit(connection, user, 'operations.schedule', {'id': schedule_id, 'enabled': payload.enabled})
        return {'id': schedule_id, 'enabled': payload.enabled}

    @router.post('/schedules/{schedule_id}/run')
    def run_schedule(schedule_id: str, user=Depends(auth), _admin=Depends(admin)):
        with connect() as connection:
            schedule = connection.execute('SELECT * FROM sports_ai_schedules WHERE id=?', (schedule_id,)).fetchone()
            if not schedule:
                raise HTTPException(404, 'Schedule not found')
            key = f"manual:{schedule_id}:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
            job = enqueue_job(connection, schedule['job_kind'], key, {'schedule': schedule['name']}, schedule_id)
            audit(connection, user, 'operations.job', {'job_id': job['id'], 'permission': 'operations.jobs'})
        return {**job, 'note': 'Queued. The worker executes it unless another worker already holds the scheduler lease.'}

    @router.get('/jobs')
    def jobs(user=Depends(auth)):
        with connect() as connection:
            return [dict(row) for row in connection.execute(
                'SELECT * FROM sports_ai_jobs ORDER BY created_at DESC LIMIT 200',
            )]

    @router.post('/jobs')
    def add_job(payload: ManualJobIn, user=Depends(auth), _admin=Depends(admin)):
        key = payload.idempotency_key or f"manual:{payload.job_kind}:{uuid.uuid4()}"
        with connect() as connection:
            job = enqueue_job(connection, payload.job_kind, key, {'requested_by': user})
            audit(connection, user, 'operations.job', {'job_id': job['id'], 'permission': 'operations.jobs'})
        return job

    @router.post('/jobs/{job_id}/cancel')
    def cancel_job(job_id: str, user=Depends(auth), _admin=Depends(admin)):
        with connect() as connection:
            cursor = connection.execute(
                """UPDATE sports_ai_jobs SET status='cancelled', finished_at=?
                   WHERE id=? AND status IN ('queued','retry_pending')""",
                (datetime.now(timezone.utc).isoformat(), job_id),
            )
            if cursor.rowcount != 1:
                raise HTTPException(409, 'Only a queued job can be cancelled')
            audit(connection, user, 'operations.job_cancel', {'job_id': job_id})
        return {'cancelled': True}

    @router.get('/providers/health')
    def health(user=Depends(auth)):
        with connect() as connection:
            return [dict(row) for row in connection.execute(
                """SELECT p.display_name,p.provider_key,p.enabled,p.status AS registry_status,h.*
                   FROM sports_ai_provider_registry p
                   LEFT JOIN sports_ai_provider_health h ON h.provider_id=p.id
                   AND h.observed_at=(SELECT MAX(observed_at) FROM sports_ai_provider_health WHERE provider_id=p.id)
                   ORDER BY p.display_name""",
            )]

    @router.post('/providers/{provider_id}/pause')
    def pause_provider(provider_id: str, user=Depends(auth), _admin=Depends(admin)):
        with connect() as connection:
            cursor = connection.execute(
                """UPDATE sports_ai_acquisition_schedules SET paused=1, updated_by=?, updated_at=?
                   WHERE provider_id=?""",
                (user, datetime.now(timezone.utc).isoformat(), provider_id),
            )
            if cursor.rowcount != 1:
                raise HTTPException(404, 'Provider schedule not found')
            audit(connection, user, 'operations.provider_pause', {'provider_id': provider_id, 'permission': 'operations.sources'})
        return {'paused': True}

    @router.post('/providers/{provider_id}/resume')
    def resume_provider(provider_id: str, user=Depends(auth), _admin=Depends(admin)):
        with connect() as connection:
            cursor = connection.execute(
                """UPDATE sports_ai_acquisition_schedules SET paused=0, updated_by=?, updated_at=?
                   WHERE provider_id=?""",
                (user, datetime.now(timezone.utc).isoformat(), provider_id),
            )
            if cursor.rowcount != 1:
                raise HTTPException(404, 'Provider schedule not found')
            audit(connection, user, 'operations.provider_resume', {'provider_id': provider_id, 'permission': 'operations.sources'})
        return {'paused': False}

    @router.get('/browser-research')
    def browser(user=Depends(auth)):
        with connect() as connection:
            return browser_research_status(connection)

    @router.get('/evaluations')
    def evaluations(user=Depends(auth)):
        with connect() as connection:
            rows = [dict(row) for row in connection.execute(
                'SELECT * FROM sports_ai_model_evaluations ORDER BY created_at DESC LIMIT 100',
            )]
            drift = [dict(row) for row in connection.execute(
                'SELECT * FROM sports_ai_model_drift_events ORDER BY created_at DESC LIMIT 50',
            )]
        return {'evaluations': rows, 'drift': drift, 'note': 'Hit rate is not an expected-return figure. Drift does not change the active model.'}

    @router.post('/training/evaluate')
    def retrain(sport: str, user=Depends(auth), _admin=Depends(admin)):
        if sport not in ('football', 'basketball'):
            raise HTTPException(422, 'Sport must be football or basketball')
        with connect() as connection:
            result = evaluate_retraining(connection, sport, user, audit)
        return result

    @router.post('/models/{model_id}/promotion')
    def promotion(model_id: str, action: str = 'promote', user=Depends(auth), _admin=Depends(admin)):
        if action not in ('promote', 'reject', 'rollback'):
            raise HTTPException(422, 'Action must be promote, reject or rollback')
        with connect() as connection:
            if action == 'reject':
                record_promotion(connection, model_id, 'reject', 'rejected', 'Administrator rejected promotion', user)
                return {'promoted': False}
            if action == 'rollback':
                from prediction_engine import rollback_model
                result = rollback_model(connection, model_id)
                if not result or not result.get('rolled_back'):
                    record_promotion(connection, model_id, 'rollback', 'rejected', (result or {}).get('reason', 'Rollback was not applied'), user)
                    raise HTTPException(409, (result or {}).get('reason', 'Rollback was not applied'))
                record_promotion(connection, model_id, 'rollback', 'applied', 'Administrator rolled back the active model', user)
                return result
            model = connection.execute(
                """SELECT m.*, t.status AS training_status, c.sample_size AS calibration_sample_size
                   FROM sports_ai_model_registry m
                   JOIN sports_ai_training_runs t ON t.id=m.training_run_id
                   LEFT JOIN sports_ai_calibration_results c ON c.model_id=m.id
                   WHERE m.id=?""",
                (model_id,),
            ).fetchone()
            if not model:
                raise HTTPException(404, 'Model not found')
            metrics = json.loads(model['metrics_json'] or '{}')
            if (model['status'] != 'validated' or model['training_status'] != 'validated'
                    or int(model['calibration_sample_size'] or 0) < 10
                    or metrics.get('calibration_error') is None or metrics['calibration_error'] > 0.20):
                record_promotion(connection, model_id, 'promote', 'rejected', 'Validation or calibration evidence is below the acceptance gate', user)
                raise HTTPException(409, 'Model has insufficient chronological validation or held-out calibration evidence')
            moment = datetime.now(timezone.utc).isoformat()
            connection.execute(
                "UPDATE sports_ai_model_registry SET active=0,status='inactive' WHERE sport=? AND active=1",
                (model['sport'],),
            )
            connection.execute(
                "UPDATE sports_ai_model_registry SET active=1,status='active',activated_at=? WHERE id=?",
                (moment, model_id),
            )
            record_promotion(connection, model_id, 'promote', 'applied', 'Acceptance gate passed and an administrator approved promotion', user)
            audit(connection, user, 'operations.promote', {'model_id': model_id, 'permission': 'operations.promote'})
        return {'promoted': True, 'model_id': model_id}

    @router.get('/notification-rules')
    def rules(user=Depends(auth)):
        with connect() as connection:
            return [dict(row) for row in connection.execute('SELECT * FROM sports_ai_notification_rules ORDER BY event_type,channel')]

    @router.post('/notification-rules')
    def save_rule(payload: RuleIn, user=Depends(auth), _admin=Depends(admin)):
        if payload.channel not in ('dashboard', 'email', 'telegram'):
            raise HTTPException(422, 'Channel must be dashboard, email or telegram')
        with connect() as connection:
            connection.execute(
                """INSERT INTO sports_ai_notification_rules (id,event_type,channel,enabled,template)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(event_type,channel) DO UPDATE SET enabled=excluded.enabled, template=excluded.template""",
                (str(uuid.uuid4()), payload.event_type, payload.channel, int(payload.enabled), payload.template),
            )
            audit(connection, user, 'operations.notification_rule', {'event_type': payload.event_type, 'channel': payload.channel})
        return {'saved': True}

    @router.get('/notification-deliveries')
    def deliveries(user=Depends(auth)):
        with connect() as connection:
            return [dict(row) for row in connection.execute(
                'SELECT * FROM sports_ai_notification_deliveries ORDER BY created_at DESC LIMIT 200',
            )]

    @router.post('/notifications/test')
    def test_notification(channel: str = 'dashboard', user=Depends(auth), _admin=Depends(admin)):
        from operations_engine import deliver_notification
        with connect() as connection:
            rule = connection.execute(
                """SELECT * FROM sports_ai_notification_rules
                   WHERE channel=? ORDER BY enabled DESC LIMIT 1""",
                (channel,),
            ).fetchone()
            if not rule:
                raise HTTPException(404, 'No notification rule exists for that channel')
            rule = dict(rule)
            rule['event_type'] = 'daily_summary'
            status = deliver_notification(connection, rule, f'test:{uuid.uuid4()}', {'test': True})
        return {'status': status, 'sent': status == 'sent'}

    @router.get('/recovery')
    def recovery(user=Depends(auth)):
        with connect() as connection:
            return [dict(row) for row in connection.execute(
                'SELECT * FROM sports_ai_recovery_cases ORDER BY created_at DESC LIMIT 200',
            )]

    @router.post('/recovery/{case_id}/retry')
    def retry(case_id: str, user=Depends(auth), _admin=Depends(admin)):
        with connect() as connection:
            result = retry_case(connection, case_id, user, audit)
        if result is None:
            raise HTTPException(404, 'Recovery case not found')
        return result

    return router
