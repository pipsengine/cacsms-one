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
                 'balldontlie','sportsdataverse_csv','the_odds_api','authorized_json')""",
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


def _observed(stage, result, failed=False):
    detail = {}
    if isinstance(result, dict):
        for key in ('status', 'phase', 'settled', 'selected', 'qualified', 'places_wager', 'promoted', 'saved'):
            if key in result:
                detail[key] = result[key]
    detail['places_wager'] = False
    try:
        from autonomous_engine import record_stage
        with connect() as connection:
            record_stage(connection, stage, 'failed' if failed else (detail.get('status') or 'complete'), detail)
    except Exception as error:
        _report('cycle record failed', error)


def tick():
    initialize()
    _operator_booking()
    try:
        from autonomous_engine import ensure_running
        with connect() as connection:
            ensure_running(connection)
    except Exception as error:
        _report('autonomous resume failed', error)
    from operations_engine import orchestrate_once
    result=orchestrate_once(
        _audit, collect=_collect_due_providers, predict=_process_prediction_queue,
        monitor=_monitor_accumulators,
    )
    print('orchestrator', result.get('status'), result.get('reason', ''))
    _catalog_tick()
    _qualification_tick()
    _process_browser_queue()
    _odds_refresh_tick()
    _coverage_tick()
    _ticket_tick()
    _draw_tick()
    _weekly_draw_tick()
    _rollover_tick()
    _learning_tick()
    _booking_tick()
    _reliability_tick()
    _publication_tick()
    _pipeline_snapshot_tick()
    _govern_cycle()


def _qualification_tick():
    try:
        from market_registry import link_stored_quotes, refresh_market_registry, store_recommendations
        from qualification_engine import revalidate
        with connect() as connection:
            registry = refresh_market_registry(connection)
            linked = link_stored_quotes(connection)
            result = revalidate(connection, 'sports-ai-worker')
            recommended = store_recommendations(connection)
        print(
            'qualification', result.get('counts', {}).get('QUALIFIED', 0),
            'changed', result.get('changed', 0),
            'markets', registry.get('counts'),
            'linked', linked.get('qualified'),
            'recommended', recommended.get('fixtures'),
        )
        _observed('qualification', result)
    except Exception as error:
        _observed('qualification', {'status': 'failed'}, failed=True)
        _report('qualification failed', error)


def _draw_tick():
    try:
        from draw_engine import refresh_if_due
        with connect() as connection:
            result = refresh_if_due(connection)
        print(
            'draw intelligence', result.get('status', 'refreshed'),
            'matches', result.get('matches', ''),
            'ranked', result.get('ranked', ''),
        )
        _observed('draw', result)
    except Exception as error:
        _observed('draw', {'status': 'failed'}, failed=True)
        _report('draw intelligence failed', error)


def _weekly_draw_tick():
    try:
        from weekly_draw_engine import process_weekly_draw
        with connect() as connection:
            result = process_weekly_draw(connection)
        print(
            'weekly draw', result.get('phase'),
            'settled', result.get('settled'),
            'monitored', result.get('monitored'),
            'places_wager', result.get('places_wager'),
        )
        _observed('weekly_draw', result)
    except Exception as error:
        _observed('weekly_draw', {'status': 'failed'}, failed=True)
        _report('weekly draw failed', error)


def _rollover_tick():
    try:
        from rollover_engine import process_rollover
        with connect() as connection:
            result = process_rollover(connection)
        print('rollover', 'settled', result.get('settled'), 'selected', result.get('selected'), 'places_wager', result.get('places_wager'))
        _observed('rollover', result)
    except Exception as error:
        _observed('rollover', {'status': 'failed'}, failed=True)
        _report('rollover failed', error)


def _learning_tick():
    try:
        from learning_engine import refresh_if_due
        with connect() as connection:
            result = refresh_if_due(connection, 'sports-ai-worker', _audit)
        print('learning', result.get('status'), 'selections', result.get('selection_sample', ''), 'suspended', result.get('suspended', ''), 'promoted', result.get('promoted'))
        _observed('learning', result)
    except Exception as error:
        _observed('learning', {'status': 'failed'}, failed=True)
        _report('learning failed', error)


def _catalog_tick():
    try:
        from catalog_engine import repair_canonical_keys, run_due_catalog
        with connect() as connection:
            repair_canonical_keys(connection)
        from prediction_engine import classify_cohorts
        from pipeline_engine import classify_observations
        with connect() as connection:
            classify_cohorts(connection)
            classify_observations(connection)
        result = run_due_catalog()
        backfill = result.get('backfill') or {}
        print(
            'catalog', result.get('status'),
            'saved', result.get('saved', 0),
            'backfill', backfill.get('status'), backfill.get('records_saved', ''),
        )
        _observed('catalog', result)
    except Exception as error:
        _observed('catalog', {'status': 'failed'}, failed=True)
        _report('catalog failed', error)


def _process_browser_queue():
    try:
        from browser_jobs import process_browser_queue
        result = process_browser_queue()
        print('browser queue', result.get('status'), result.get('job_id', ''), result.get('error', ''))
        from sportybet_feed import collect_displayed_odds
        collected = collect_displayed_odds()
        print('sportybet feed', collected.get('status'), 'imported', collected.get('imported', 0), collected.get('error', ''))
        _observed('browser', collected)
        if collected.get('imported'):
            _operator_booking()
    except Exception as error:
        _observed('browser', {'status': 'failed'}, failed=True)
        _report('browser queue failed', error)


def _odds_refresh_tick():
    try:
        from pipeline_engine import enqueue_odds_refresh, process_odds_refresh
        from qualification_engine import revalidate
        with connect() as connection:
            queued = enqueue_odds_refresh(connection)
            refreshed = process_odds_refresh(connection)
            if refreshed.get('fixture_ids'):
                qualification = revalidate(connection, 'sports-ai-worker', fixture_ids=refreshed['fixture_ids'])
            else:
                qualification = None
        print(
            'odds refresh', 'queued', queued.get('queued'),
            'refreshed', refreshed.get('refreshed'),
            'blocked', refreshed.get('feed_blocked'),
            'invented', refreshed.get('odds_invented'),
        )
        if qualification is not None:
            print('qualification after price', qualification.get('counts', {}).get('QUALIFIED', 0))
        _observed('odds_refresh', refreshed)
    except Exception as error:
        _observed('odds_refresh', {'status': 'failed'}, failed=True)
        _report('odds refresh failed', error)


def _ticket_tick():
    try:
        from accumulator_engine import build_high_probability_tickets
        from pipeline_engine import build_autonomous_tickets
        with connect() as connection:
            result = build_autonomous_tickets(connection, 'sports-ai-worker', _audit)
            coverage = build_high_probability_tickets(connection, 'sports-ai-worker', _audit)
        print(
            'tickets', result.get('status'),
            'created', result.get('tickets_created', result.get('ticket_count', 0)),
            'high_probability', coverage.get('created', 0),
            'leagues', coverage.get('leagues', 0),
            'places_wager', False,
        )
        _observed('tickets', result)
    except Exception as error:
        _observed('tickets', {'status': 'failed'}, failed=True)
        _report('autonomous tickets failed', error)


def _coverage_tick():
    try:
        from prediction_engine import run_analysis
        with connect() as connection:
            result = run_analysis(connection, 'sports-ai-worker', _audit, coverage=True, limit=8)
        print(
            'coverage analysis',
            'fixtures', result.get('fixtures'),
            'predictions', result.get('predictions'),
            'skipped', result.get('skipped'),
        )
    except Exception as error:
        _report('coverage analysis failed', error)


def _operator_booking():
    """One operator-named ticket. A closed window does not open the browser until its prices are fresh."""
    try:
        import json
        with connect() as connection:
            row = connection.execute(
                "SELECT value_json FROM sports_ai_settings WHERE key='operator_booking_ticket'",
            ).fetchone()
        if not row:
            return
        stored = json.loads(row['value_json'] or '{}')
        ticket_id = stored.get('ticket_id')
        attempts = int(stored.get('attempts') or 0)
        if not ticket_id or attempts >= 4:
            with connect() as connection:
                connection.execute("DELETE FROM sports_ai_settings WHERE key='operator_booking_ticket'")
            return
        from sportybet_booking import confirm_ticket
        result = confirm_ticket(ticket_id)
        print(
            'operator booking', result.get('status'),
            result.get('code') or result.get('reason_code') or '',
            'places_wager', False,
        )
        if result.get('status') == 'waiting':
            stored['attempts'] = attempts + 1
            with connect() as connection:
                connection.execute(
                    """INSERT INTO sports_ai_settings(key,value_json,updated_at) VALUES('operator_booking_ticket',?,?)
                       ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at""",
                    (json.dumps(stored), datetime.now(timezone.utc).isoformat()),
                )
            return
        with connect() as connection:
            connection.execute("DELETE FROM sports_ai_settings WHERE key='operator_booking_ticket'")
    except Exception as error:
        _report('operator booking failed', error)


def _booking_tick():
    _operator_booking()
    try:
        from sportybet_booking import process_booking_queue
        booked = process_booking_queue()
        print('sportybet booking', booked.get('status'), 'qualified', booked.get('qualified', ''), 'restored', (booked.get('examined') or {}).get('restored', 0), 'places_wager', booked.get('places_wager'))
        _observed('booking', booked)
    except Exception as error:
        _observed('booking', {'status': 'failed'}, failed=True)
        _report('booking failed', error)


def _reliability_tick():
    try:
        from fixture_reconciliation import reconcile_past_live_fixtures
        from integrity_engine import audit_prematch_features
        from learning_engine import settle_live_forecasts
        from pipeline_engine import review_qualified_predictions
        with connect() as connection:
            reconciled = reconcile_past_live_fixtures(connection)
            audited = audit_prematch_features(connection)
            settled = settle_live_forecasts(connection)
            reviews = review_qualified_predictions(connection)
        print(
            'settlement', 'linked', settled.get('live_linked'),
            'awaiting_result', settled.get('awaiting_official_result'),
            'reconciled', reconciled.get('confirmed'),
            'audit_leaks', audited.get('leaked'),
            'reviews', reviews.get('inserted'),
        )
    except Exception as error:
        _report('settlement failed', error)


def _publication_tick():
    try:
        from pipeline_engine import sync_ticket_lifecycle
        with connect() as connection:
            result = sync_ticket_lifecycle(connection)
        print('publication', result.get('phase'), 'demoted', result.get('demoted'), 'places_wager', False)
    except Exception as error:
        _report('publication sync failed', error)


def _pipeline_snapshot_tick():
    try:
        from pipeline_engine import capture_snapshot
        with connect() as connection:
            result = capture_snapshot(connection)
        counts = result.get('counts') or {}
        print(
            'pipeline',
            'predicted', counts.get('PREDICTED'),
            'fresh', counts.get('ODDS_FRESH'),
            'qualified', counts.get('QUALIFIED'),
            'tickets', counts.get('TICKET_READY'),
        )
        _observed('pipeline', {'status': 'complete', 'qualified': counts.get('QUALIFIED')})
    except Exception as error:
        _observed('pipeline', {'status': 'failed'}, failed=True)
        _report('pipeline snapshot failed', error)


def _govern_cycle():
    try:
        from prediction_engine import ensure_goal_sport_models, govern_models, reprice_active_batch, review_degradation
        with connect() as connection:
            connection.execute("UPDATE sports_ai_schedules SET enabled=1 WHERE id='schedule-discovery' AND enabled=0")
            ensure_goal_sport_models(connection)
            review = review_degradation(connection, 'sports-ai-worker', _audit)
            governed = govern_models(connection, 'sports-ai-worker', _audit)
            priced = reprice_active_batch(connection, 'sports-ai-worker', _audit, limit=20)
            from qualification_engine import revalidate
            qualification = revalidate(connection, 'sports-ai-worker')
        print(
            'governance',
            'activated', len(governed.get('activated') or []),
            'rolled_back', len(review.get('rolled_back') or []),
            'repriced', priced.get('repriced'),
            'qualified', priced.get('qualified'),
            'qualification', qualification.get('counts', {}).get('QUALIFIED', 0),
            priced.get('reason', ''),
        )
    except Exception as error:
        _report('governance failed', error)


def _monitor_accumulators():
    try:
        hour=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H')
        with connect() as connection:
            result=monitor_accumulators(
                connection,'sports-ai-worker',_audit,f'accumulator-monitor:v2:{hour}',
            )
        print('accumulator monitor',result.get('status'),
              'settled',result.get('settled',0),'expired',result.get('expired',0),
              'review',result.get('review_required',0))
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
