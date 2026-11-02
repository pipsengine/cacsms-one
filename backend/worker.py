"""Sports-only scheduler. It never places or submits wagers."""

import argparse
import json
import time
import uuid
from datetime import datetime, timedelta, timezone

from acquisition import CollectionError, collect_fixtures
from db import connect, initialize
from prediction_engine import run_analysis


def _record_failure(job_id, error):
    with connect() as connection:
        connection.execute(
            """UPDATE sports_ai_job_history SET status='failed',finished_at=?,
               details_json=? WHERE id=?""",
            (datetime.now(timezone.utc).isoformat(),
             json.dumps({'error':str(error)[:500]}),job_id),
        )


def _audit(connection, actor, action, details):
    connection.execute(
        """INSERT INTO sports_ai_audit_events(id,actor,action,metadata_json)
           VALUES(?,?,?,?)""",
        (str(uuid.uuid4()),actor,action,json.dumps(details)),
    )


def _collect_due_providers():
    now=datetime.now(timezone.utc)
    with connect() as connection:
        providers=[dict(row) for row in connection.execute(
            """SELECT * FROM sports_ai_provider_registry WHERE enabled=1
               AND terms_reviewed=1 AND adapter IN ('openligadb','openfootball','the_odds_api')""",
        )]
    for provider in providers:
        last=provider.get('last_success_at')
        try:
            if last and now-datetime.fromisoformat(last.replace('Z','+00:00'))<timedelta(hours=6):
                continue
        except ValueError:
            pass
        job_id=str(uuid.uuid4())
        with connect() as connection:
            connection.execute(
                """INSERT INTO sports_ai_job_history
                   (id,schedule_id,status,details_json) VALUES(?,NULL,'running',?)""",
                (job_id,json.dumps({'provider_id':provider['id'],'kind':'collection'})),
            )
        try:
            with connect() as connection:
                result=collect_fixtures(provider,connection)
                completed=datetime.now(timezone.utc).isoformat()
                connection.execute(
                    """UPDATE sports_ai_provider_registry SET status='operational',
                       last_success_at=?,last_error=NULL WHERE id=?""",
                    (completed,provider['id']),
                )
                connection.execute(
                    """UPDATE sports_ai_job_history SET status='complete',finished_at=?,
                       details_json=? WHERE id=?""",
                    (completed,json.dumps(result),job_id),
                )
        except (CollectionError,ValueError,TypeError) as error:
            with connect() as connection:
                connection.execute(
                    "UPDATE sports_ai_provider_registry SET status='error',last_error=? WHERE id=?",
                    (str(error)[:500],provider['id']),
                )
            _record_failure(job_id,error)
            print('provider collection failed',provider['provider_key'],str(error))


def _run_schedule(schedule):
    job_id=str(uuid.uuid4())
    with connect() as connection:
        connection.execute(
            """INSERT INTO sports_ai_job_history
               (id,schedule_id,status) VALUES(?,?,'running')""",
            (job_id,schedule['id']),
        )
    try:
        with connect() as connection:
            result=run_analysis(connection,'sports-ai-worker',_audit)
            completed=datetime.now(timezone.utc).isoformat()
            connection.execute(
                'UPDATE sports_ai_schedules SET last_run_at=? WHERE id=?',
                (completed,schedule['id']),
            )
            connection.execute(
                """UPDATE sports_ai_job_history SET status='complete',finished_at=?,
                   details_json=? WHERE id=?""",
                (completed,json.dumps(result),job_id),
            )
        print('completed scheduled analysis',schedule['name'],
              'predictions',result['predictions'],'qualified',result['qualified'])
    except Exception as error:
        _record_failure(job_id,error)
        print('scheduled analysis failed',schedule['name'],str(error))


def tick():
    initialize()
    _collect_due_providers()
    now=datetime.now(timezone.utc)
    with connect() as connection:
        schedules=[dict(row) for row in connection.execute(
            'SELECT * FROM sports_ai_schedules WHERE enabled=1',
        )]
    for schedule in schedules:
        last=schedule.get('last_run_at')
        try:
            if last:
                parsed=datetime.fromisoformat(last.replace('Z','+00:00'))
                if parsed.tzinfo is None:
                    parsed=parsed.replace(tzinfo=timezone.utc)
                if now-parsed<timedelta(minutes=schedule['interval_minutes']):
                    continue
        except ValueError:
            pass
        _run_schedule(schedule)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--once',action='store_true')
    group.add_argument('--loop',action='store_true')
    args=parser.parse_args()
    if args.once:
        tick()
    else:
        while True:
            tick()
            time.sleep(30)
