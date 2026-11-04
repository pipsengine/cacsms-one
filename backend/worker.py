"""Sports-only scheduler. It never places or submits wagers."""

import argparse
import json
import time
import uuid
from datetime import datetime, timezone

from acquisition import CollectionError
from acquisition_engine import run_collection
from db import connect, initialize
from accumulator_engine import monitor_accumulators
from prediction_engine import execute_prediction_job, recover_timed_out_jobs


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
            """SELECT p.* FROM sports_ai_provider_registry p
               JOIN sports_ai_acquisition_schedules s ON s.provider_id=p.id
               WHERE p.enabled=1 AND p.terms_reviewed=1 AND s.enabled=1 AND s.paused=0
               AND p.adapter IN ('openligadb','openfootball','football_data',
                 'balldontlie','sportsdataverse_csv','the_odds_api')""",
        )]
    for provider in providers:
        next_run=provider.get('next_run_at')
        try:
            if next_run and now<datetime.fromisoformat(next_run.replace('Z','+00:00')):
                continue
        except ValueError:
            pass
        try:
            result=run_collection(provider['id'])
            print('provider collection',provider['provider_key'],
                  'records',result['records_saved'])
        except Exception as error:
            print('provider collection failed',provider['provider_key'],str(error))


def _process_prediction_queue():
    with connect() as connection:
        recover_timed_out_jobs(connection)
        running=connection.execute(
            "SELECT COUNT(*) FROM sports_ai_prediction_jobs WHERE status='running'",
        ).fetchone()[0]
        if running:
            return
        job=connection.execute(
            """SELECT * FROM sports_ai_prediction_jobs
               WHERE status='queued'
                  OR (status IN ('failed','timed_out') AND attempt<3)
               ORDER BY created_at LIMIT 1""",
        ).fetchone()
        if not job:
            return
        result=execute_prediction_job(
            connection,'sports-ai-worker',_audit,job['idempotency_key'],job['trigger'],
        )
        print('prediction queue',job['trigger'],result.get('status'))


def _report(label, error):
    print(label, str(error).encode('ascii', 'backslashreplace').decode('ascii'))


def tick():
    initialize()
    from operations_engine import orchestrate_once
    result=orchestrate_once(
        _audit, collect=_collect_due_providers, predict=_process_prediction_queue,
        monitor=_monitor_accumulators,
    )
    print('orchestrator', result.get('status'), result.get('reason', ''))
    _process_browser_queue()
    _govern_cycle()


def _process_browser_queue():
    try:
        from browser_jobs import process_browser_queue
        result = process_browser_queue()
        print('browser queue', result.get('status'), result.get('job_id', ''), result.get('error', ''))
        from sportybet_feed import collect_displayed_odds
        collected = collect_displayed_odds()
        print('sportybet feed', collected.get('status'), 'imported', collected.get('imported', 0), collected.get('error', ''))
        from sportybet_booking import process_booking_queue
        booked = process_booking_queue()
        print('sportybet booking', booked.get('status'), 'qualified', booked.get('qualified', ''), 'places_wager', booked.get('places_wager'))
    except Exception as error:
        _report('browser queue failed', error)


def _govern_cycle():
    try:
        from accumulator_engine import generate_accumulators
        from prediction_engine import govern_models, reprice_active_batch, review_degradation
        with connect() as connection:
            connection.execute("UPDATE sports_ai_schedules SET enabled=1 WHERE id='schedule-discovery' AND enabled=0")
            review = review_degradation(connection, 'sports-ai-worker', _audit)
            governed = govern_models(connection, 'sports-ai-worker', _audit)
            priced = reprice_active_batch(connection, 'sports-ai-worker', _audit, limit=20)
            tickets = []
            if priced.get('qualified'):
                hour = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H')
                for mode in ('football', 'basketball', 'mixed'):
                    tickets.append(generate_accumulators(
                        connection, 'sports-ai-worker', _audit, mode, None, f'auto-ticket:{mode}:{hour}', trigger='scheduler',
                    ))
        print(
            'governance',
            'activated', len(governed.get('activated') or []),
            'rolled_back', len(review.get('rolled_back') or []),
            'repriced', priced.get('repriced'),
            'qualified', priced.get('qualified'),
            'tickets', len(tickets),
            priced.get('reason', ''),
        )
    except Exception as error:
        _report('governance failed', error)


def _monitor_accumulators():
    try:
        hour=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H')
        with connect() as connection:
            result=monitor_accumulators(
                connection,'sports-ai-worker',_audit,f'accumulator-monitor:{hour}',
            )
        print('accumulator monitor',result.get('status'),
              'settled',result.get('settled',0),'expired',result.get('expired',0))
    except Exception as error:
        _report('accumulator monitor failed', error)


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
