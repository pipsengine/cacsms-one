"""Autonomy health: one read model for browser slots, stage recovery, bookings,
source permissions and the learning loop. Every figure comes from stored rows.
"""
import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from db import connect

STAGE_WINDOW_HOURS = 24


class ReviewIn(BaseModel):
    host: str = Field(min_length=3, max_length=253)
    approved: bool
    terms_url: str = Field(min_length=8, max_length=500)
    notes: str = Field(default='', max_length=1000)


def _stages(connection, now):
    since = (now - timedelta(hours=STAGE_WINDOW_HOURS)).isoformat()
    rows = connection.execute(
        """SELECT stage,status,detail_json,created_at FROM sports_ai_autonomous_cycles
           WHERE created_at>=? ORDER BY created_at DESC""", (since,),
    ).fetchall()
    stages = {}
    for row in rows:
        item = stages.setdefault(row['stage'], {
            'stage': row['stage'], 'last_status': row['status'], 'last_at': row['created_at'],
            'runs': 0, 'failed': 0, 'last_error': None,
        })
        item['runs'] += 1
        if row['status'] == 'failed':
            item['failed'] += 1
            if item['last_error'] is None:
                detail = json.loads(row['detail_json'] or '{}')
                item['last_error'] = detail.get('error') or 'No error message was recorded'
    return sorted(stages.values(), key=lambda s: (-s['failed'], s['stage']))


def _recovery(connection):
    rows = connection.execute(
        """SELECT id,error_code,status,attempt_count,details_json,created_at,updated_at FROM sports_ai_recovery_cases
           WHERE error_code LIKE 'STAGE_FAILED:%' ORDER BY CASE status WHEN 'escalated' THEN 0 WHEN 'retried' THEN 1
             WHEN 'open' THEN 2 ELSE 3 END, updated_at DESC LIMIT 30""",
    ).fetchall()
    cases = []
    for row in rows:
        detail = json.loads(row['details_json'] or '{}')
        cases.append({
            'id': row['id'], 'stage': row['error_code'].split(':', 1)[1], 'status': row['status'],
            'failures': row['attempt_count'], 'error': detail.get('error'), 'error_type': detail.get('error_type'),
            'error_class': detail.get('error_class'), 'where': detail.get('where') or [],
            'resolved_by': detail.get('resolved_by'), 'opened_at': row['created_at'], 'updated_at': row['updated_at'],
        })
    return cases


def _bookings(connection, now):
    since = (now - timedelta(days=7)).isoformat()
    outcomes = [dict(r) for r in connection.execute(
        """SELECT COALESCE(reason_code,status) reason,status,COUNT(*) n FROM sports_ai_booking_jobs
           WHERE updated_at>=? GROUP BY 1,2 ORDER BY 3 DESC""", (since,),
    )]
    finished = [o for o in outcomes if o['status'] not in ('QUEUED', 'PREPARING')]
    codes = sum(o['n'] for o in finished if o['status'] == 'CODE_GENERATED')
    total = sum(o['n'] for o in finished)
    recent = [dict(r) for r in connection.execute(
        """SELECT id,status,reason_code,code,error,attempt,updated_at FROM sports_ai_booking_jobs
           ORDER BY updated_at DESC LIMIT 12""",
    )]
    return {'outcomes': outcomes, 'codes': codes, 'finished': total,
            'success_rate': codes / total if total else None, 'recent': recent}


def autonomy_overview(connection, now=None):
    from browser_slots import slot_status
    from learning_loop import learning_status
    from source_permissions import source_health
    current = now or datetime.now(timezone.utc)
    waiting = connection.execute(
        "SELECT COUNT(*) FROM sports_ai_sportybet_observations WHERE value_status='awaiting_model'",
    ).fetchone()[0]
    learning = learning_status(connection, current)
    learning['awaiting_model'] = waiting
    return {
        'generated_at': current.isoformat(),
        'slots': slot_status(connection, current),
        'stages': _stages(connection, current),
        'recovery': _recovery(connection),
        'bookings': _bookings(connection, current),
        'sources': source_health(connection, current),
        'learning': learning,
        'booking_policy': __import__('booking_policy').load(connection),
        'places_wager': False,
    }


def build_autonomy_router(auth, require_admin_key):
    router = APIRouter(prefix='/api/sports-ai/autonomy', tags=['Autonomy health'])

    @router.get('/overview')
    def overview(user=Depends(auth)):
        with connect() as connection:
            return autonomy_overview(connection)

    @router.post('/sources/review')
    def review(body: ReviewIn, x_sports_admin_key: str | None = Header(default=None), user=Depends(auth)):
        require_admin_key(x_sports_admin_key)
        from source_permissions import record_review
        try:
            with connect() as connection:
                saved = record_review(connection, body.host, body.approved, str(user), body.terms_url, body.notes)
                connection.execute(
                    """INSERT INTO sports_ai_audit_events(id,actor,action,metadata_json) VALUES(lower(hex(randomblob(16))),?,?,?)""",
                    (str(user), 'sources.review', json.dumps({'host': saved['hostname'], 'approved': bool(saved['approved'])})),
                )
                return saved
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @router.get('/placed-results')
    def placed_results(kind: str | None = None, sport: str | None = None,
                       date_from: str | None = None, date_to: str | None = None,
                       page: int = 1, page_size: int = 25, user=Depends(auth)):
        from placed_results import placed_view
        try:
            with connect() as connection:
                return placed_view(connection, kind, sport, date_from, date_to, page, page_size)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @router.get('/booking-policy')
    def booking_policy_view(user=Depends(auth)):
        from booking_policy import load
        with connect() as connection:
            return load(connection)

    @router.put('/booking-policy')
    def booking_policy_save(body: dict, x_sports_admin_key: str | None = Header(default=None), user=Depends(auth)):
        require_admin_key(x_sports_admin_key)
        from booking_policy import save
        try:
            with connect() as connection:
                saved = save(connection, body)
                connection.execute(
                    """INSERT INTO sports_ai_audit_events(id,actor,action,metadata_json) VALUES(lower(hex(randomblob(16))),?,?,?)""",
                    (str(user), 'booking.policy', json.dumps(saved)),
                )
                return saved
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    return router
