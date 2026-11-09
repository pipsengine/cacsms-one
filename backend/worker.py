"""Sports-only scheduler. It never places or submits wagers."""

import argparse
import json
import os
import threading
import time
import uuid
from pathlib import Path
from datetime import datetime, timedelta, timezone

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


def _observed(stage, result, failed=False, error=None):
    detail = {}
    if error is not None:
        import traceback
        from browser_slots import classify_browser_error
        detail['error_type'] = type(error).__name__
        detail['error'] = str(error).encode('ascii', 'backslashreplace').decode('ascii')[:600]
        detail['error_class'] = classify_browser_error(error)
        frames = traceback.extract_tb(error.__traceback__)[-3:]
        detail['where'] = [f'{Path(f.filename).name}:{f.lineno} {f.name}' for f in frames]
    if isinstance(result, dict):
        for key in ('status', 'phase', 'settled', 'selected', 'qualified', 'places_wager', 'promoted', 'saved', 'reassessed', 'counts'):
            if key in result:
                detail[key] = result[key]
    detail['places_wager'] = False
    try:
        from autonomous_engine import record_stage
        with connect() as connection:
            record_stage(connection, stage, 'failed' if failed else (detail.get('status') or 'complete'), detail)
    except Exception as error:
        _report('cycle record failed', error)


def _leader_name():
    return f'worker:{os.getpid()}'


def _touch_heartbeat():
    """Refresh the scheduler lease while this process is inside a long stage.

    A restarted worker takes the lease when the previous one has expired.
    The update is one short write, so a price import can commit and let it through.
    """
    try:
        from operations_engine import keep_leader
        with connect(busy_timeout_ms=60000) as connection:
            return keep_leader(connection, _leader_name())
    except Exception as error:
        _report('heartbeat failed', error)
        return False


def _beat_until(stop):
    while not stop.wait(20):
        _touch_heartbeat()


def tick():
    """One cycle. Upcoming fixtures and completed matches move on separate lanes.

    Stage 6 finalizes qualified tickets and then requests a displayed code in
    the same pass. Rollover runs after settlement and does not sit between a
    ticket and its code. An unfinished kickoff does not hold the next cycle.
    """
    initialize()
    stop = threading.Event()
    beater = threading.Thread(target=_beat_until, args=(stop,), daemon=True)
    if not _touch_heartbeat():
        return {'status': 'standby', 'reason': 'Another scheduler or maintenance process holds the leader lease.'}
    beater.start()
    try:
        _run_cycle()
    finally:
        stop.set()
        _touch_heartbeat()


def _qualify_priced_board():
    """Qualify selections that already have a fresh SportyBet price, then store the tickets."""
    try:
        from market_registry import link_stored_quotes
        from qualification_engine import revalidate
        with connect() as connection:
            linked = link_stored_quotes(connection, limit=600)
            result = revalidate(connection, 'sports-ai-worker', limit=600)
        print(
            'priced qualification', result.get('counts', {}).get('QUALIFIED', 0),
            'linked', linked.get('qualified'),
            'checked', linked.get('checked'),
        )
        _high_odds_booking_tick()
        _ticket_tick()
    except Exception as error:
        _report('priced qualification failed', error)


def _release_stopped_jobs():
    try:
        from operations_engine import recover_stuck_jobs
        with connect() as connection:
            recovered = recover_stuck_jobs(connection)
        if recovered:
            print('recovered stopped jobs', recovered)
    except Exception as error:
        _report('job recovery failed', error)


def _run_cycle():
    from ticket_diversity import reconcile
    with connect() as connection:
        reconcile(connection)
    _release_stopped_jobs()
    _monitor_accumulators()
    _settled_snapshot_cleanup()
    _high_odds_booking_tick()
    _qualify_priced_board()
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
    _touch_heartbeat()
    _catalog_tick()
    _qualification_tick()
    _stage_six()
    _process_browser_queue()
    _odds_refresh_tick()
    _history_tick()
    _learning_loop_tick()
    _govern_cycle()
    _coverage_tick()
    _match_report_tick()
    _draw_tick()
    _weekly_draw_tick()
    _reliability_tick()
    _learning_tick()
    _rollover_tick()
    _publication_tick()
    _pipeline_snapshot_tick()


def _settled_snapshot_cleanup():
    try:
        from snapshot_retention import cleanup_settled_odds
        with connect() as connection:
            result = cleanup_settled_odds(connection)
        print('settled snapshot cleanup', result)
    except Exception as error:
        _report('settled snapshot cleanup failed', error)


def _qualification_tick():
    try:
        from market_registry import link_stored_quotes, refresh_market_registry, store_recommendations
        from prediction_engine import refresh_score_dependence
        from qualification_engine import revalidate
        with connect() as connection:
            dependence = refresh_score_dependence(connection)
        with connect() as connection:
            registry = refresh_market_registry(connection)
        with connect() as connection:
            linked = link_stored_quotes(connection)
        with connect() as connection:
            result = revalidate(connection, 'sports-ai-worker')
        with connect() as connection:
            recommended = store_recommendations(connection)
        print(
            'qualification', result.get('counts', {}).get('QUALIFIED', 0),
            'changed', result.get('changed', 0),
            'markets', registry.get('counts'),
            'linked', linked.get('qualified'),
            'recommended', recommended.get('fixtures'),
            'score_dependence', dependence.get('score_dependence', ''),
            dependence.get('status', ''),
        )
        _observed('qualification', result)
    except Exception as error:
        _observed('qualification', {'status': 'failed'}, failed=True, error=error)
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
        _observed('draw', {'status': 'failed'}, failed=True, error=error)
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
        _observed('weekly_draw', {'status': 'failed'}, failed=True, error=error)
        _report('weekly draw failed', error)


def _rollover_tick():
    try:
        from rollover_engine import process_rollover
        with connect() as connection:
            result = process_rollover(connection)
        print('rollover', 'settled', result.get('settled'), 'selected', result.get('selected'), 'places_wager', result.get('places_wager'))
        _observed('rollover', result)
    except Exception as error:
        _observed('rollover', {'status': 'failed'}, failed=True, error=error)
        _report('rollover failed', error)


def _learning_tick():
    try:
        from learning_engine import refresh_if_due
        with connect() as connection:
            result = refresh_if_due(connection, 'sports-ai-worker', _audit)
        print('learning', result.get('status'), 'selections', result.get('selection_sample', ''), 'suspended', result.get('suspended', ''), 'promoted', result.get('promoted'))
        _observed('learning', result)
    except Exception as error:
        _observed('learning', {'status': 'failed'}, failed=True, error=error)
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
        _observed('catalog', {'status': 'failed'}, failed=True, error=error)
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
            _qualify_priced_board()
            _booking_tick()
            _high_odds_booking_tick()
    except Exception as error:
        _observed('browser', {'status': 'failed'}, failed=True, error=error)
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
        _observed('odds_refresh', {'status': 'failed'}, failed=True, error=error)
        _report('odds refresh failed', error)


def _stage_six():
    """Store finalized tickets, then register one displayed code for a finalized ticket.

    The code request does not start until the ticket write has committed. It
    does not invent a code, and it does not place a wager. Rollover is not called.
    """
    _ticket_tick()
    _booking_tick()


def _ticket_tick():
    try:
        from accumulator_engine import build_bookable_cross_tickets, build_high_probability_tickets
        from pipeline_engine import build_autonomous_tickets
        from prediction_engine import price_quoted_board
        from datetime import datetime, timezone
        current = datetime.now(timezone.utc)
        with connect() as connection:
            priced = price_quoted_board(connection, now=current, limit=60)
            result = build_autonomous_tickets(connection, 'sports-ai-worker', _audit, now=current)
            cross = build_bookable_cross_tickets(connection, 'sports-ai-worker', _audit, now=current)
            coverage = build_high_probability_tickets(connection, 'sports-ai-worker', _audit, now=current)
        print(
            'tickets', result.get('status'),
            'created', result.get('tickets_created', result.get('ticket_count', 0)),
            'cross_market', cross.get('created', 0),
            'high_probability', coverage.get('created', 0),
            'leagues', coverage.get('leagues', 0),
            'sports', coverage.get('sports', 0),
            'quoted_board', priced.get('fixtures', 0),
            'places_wager', False,
        )
        _observed('tickets', result)
        return result
    except Exception as error:
        _observed('tickets', {'status': 'failed'}, failed=True, error=error)
        _report('autonomous tickets failed', error)
        return None


def _history_tick():
    """Read displayed finals from BetExplorer and Soccerway before the next analysis."""
    try:
        from history_browser import collect_match_history
        result = collect_match_history()
        print(
            'match history', result.get('status'),
            'saved', result.get('saved', 0),
            'pages', result.get('pages', 0),
            (result.get('error') or '')[:180],
        )
    except Exception as error:
        _report('match history failed', error)


def _coverage_tick():
    try:
        from prediction_engine import record_result_gaps, run_analysis
        with connect() as connection:
            gaps = record_result_gaps(connection)
        totals = {'fixtures': 0, 'predictions': 0, 'skipped': {}}
        for _ in range(32):
            _touch_heartbeat()
            with connect() as connection:
                result = run_analysis(connection, 'sports-ai-worker', _audit, coverage=True, limit=12)
            totals['fixtures'] += result.get('fixtures') or 0
            totals['predictions'] += result.get('predictions') or 0
            for key, value in (result.get('skipped') or {}).items():
                totals['skipped'][key] = totals['skipped'].get(key, 0) + value
            if not result.get('fixtures'):
                break
        print(
            'coverage analysis',
            'fixtures', totals['fixtures'],
            'predictions', totals['predictions'],
            'skipped', totals['skipped'],
            'leagues_waiting_for_results', gaps.get('waiting'),
        )
        _observed('analyse', totals)
    except Exception as error:
        _observed('analyse', {'status': 'failed'}, failed=True, error=error)
        _report('coverage analysis failed', error)


def _match_report_tick():
    """Store match reports for the next upcoming fixtures. The pages read these rows; they do not start a job."""
    try:
        from match_analysis import analyse_upcoming_batch
        with connect() as connection:
            result = analyse_upcoming_batch(connection, 'sports-ai-worker', _audit, limit=2)
        print('match reports', result.get('attempted'), 'stored', len(result.get('results') or []))
    except Exception as error:
        _report('match reports failed', error)


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


_BOOKING_LANE = None


def _booking_lane(stop):
    """Book codes on their own thread so the long worker cycle does not hold them.

    Every 30 seconds: when the request gap has passed, book up to batch_size codes.
    """
    from sportybet_booking import _booking_due, book_batch
    while not stop.wait(30):
        try:
            with connect() as connection:
                due = _booking_due(connection, datetime.now(timezone.utc))
            if not due:
                continue
            result = book_batch()
            if result['attempts']:
                print('booking lane', result['status'], 'codes', result['codes'], 'attempts', result['attempts'], 'places_wager', False)
            _observed('booking_lane', {'status': result['status']})
        except Exception as error:
            _observed('booking_lane', {'status': 'failed'}, failed=True, error=error)
            _report('booking lane failed', error)


def start_booking_lane():
    global _BOOKING_LANE
    if _BOOKING_LANE and _BOOKING_LANE.is_alive():
        return _BOOKING_LANE
    _BOOKING_LANE = threading.Thread(target=_booking_lane, args=(threading.Event(),), name='booking-lane', daemon=True)
    _BOOKING_LANE.start()
    return _BOOKING_LANE


def _booking_tick():
    _operator_booking()
    if _BOOKING_LANE and _BOOKING_LANE.is_alive():
        return  # the booking lane requests codes; the cycle does not compete with it
    try:
        from sportybet_booking import process_booking_queue
        booked = process_booking_queue()
        print('sportybet booking', booked.get('status'), 'qualified', booked.get('qualified', ''), 'restored', (booked.get('examined') or {}).get('restored', 0), 'places_wager', booked.get('places_wager'))
        _observed('tickets_booking', booked)
    except Exception as error:
        _observed('tickets_booking', {'status': 'failed'}, failed=True, error=error)
        _report('booking failed', error)


_HIGHER_ODDS_KEY = 'high_odds_booking'
_HIGHER_ODDS_INTERVAL = timedelta(minutes=30)


def _higher_odds_due(now):
    with connect() as connection:
        row = connection.execute(
            'SELECT value_json FROM sports_ai_settings WHERE key=?',
            (_HIGHER_ODDS_KEY,),
        ).fetchone()
    if not row:
        return True
    try:
        stored = json.loads(row['value_json'] or '{}')
        last = datetime.fromisoformat(stored.get('last_run_at') or '')
    except (TypeError, ValueError, json.JSONDecodeError):
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return now - last >= _HIGHER_ODDS_INTERVAL


def _remember_higher_odds_run(now, status):
    with connect() as connection:
        connection.execute(
            """INSERT INTO sports_ai_settings(key,value_json,updated_at) VALUES(?,?,?)
               ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at""",
            (_HIGHER_ODDS_KEY, json.dumps({'last_run_at': now.isoformat(), 'status': status}), now.isoformat()),
        )


def _book_sure_odds():
    """Build today's 2.00–2.95 tickets and request each displayed code. No wager is placed."""
    import asyncio
    from high_odds_board import prepare_sure_odds_booking
    from sportybet_booking import execute_job
    seen = set()
    status = 'waiting'
    while True:
        job_id = prepare_sure_odds_booking()
        if not job_id or job_id in seen:
            if not seen:
                print('sure odds booking', 'waiting', 'places_wager', False)
            return status
        seen.add(job_id)
        result = asyncio.run(execute_job(job_id))
        status = result.get('status') or status
        print(
            'sure odds booking', status,
            result.get('code') or result.get('error') or '',
            'places_wager', False,
        )
    return status


def _book_ready_tickets(bands):
    """Store each ready ticket and request its code before the worker does anything else."""
    import asyncio
    from high_odds_board import prepare_high_odds_booking
    from sportybet_booking import execute_job
    seen = set()
    status = 'waiting'
    while True:
        job_id = prepare_high_odds_booking(bands=bands)
        if not job_id or job_id in seen:
            if not seen:
                print('high odds booking', 'waiting', 'places_wager', False)
            return status
        seen.add(job_id)
        result = asyncio.run(execute_job(job_id))
        status = result.get('status') or status
        print(
            'high odds booking', status,
            result.get('code') or result.get('error') or '',
            'places_wager', False,
        )
    return status


def _high_odds_booking_tick():
    """Drain qualification events and register finalized capped-odds tickets."""
    try:
        from high_odds_service import worker_tick
        result = worker_tick()
        print('high odds optimizer', result.get('status'), result.get('booking_status',''))
        _book_sure_odds()
    except Exception as error:
        _report('high odds booking failed', error)


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
        _observed('settle', {'status': 'complete', 'settled': settled.get('live_linked')})
    except Exception as error:
        _observed('settle', {'status': 'failed'}, failed=True, error=error)
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
        _observed('pipeline', {'status': 'failed'}, failed=True, error=error)
        _report('pipeline snapshot failed', error)


def _learning_loop_tick():
    """Assess prices that waited for a prediction, then retrain sports with live drift."""
    try:
        from learning_loop import drift_retraining, reassess_awaiting_model
        with connect() as connection:
            waiting = reassess_awaiting_model(connection)
        with connect() as connection:
            drift = drift_retraining(connection, 'sports-ai-worker', _audit)
        print('learning loop', 'reassessed', waiting['reassessed'], waiting['counts'],
              'drift_retrained', [item['sport'] for item in drift['trained']], 'places_wager', False)
        _observed('learning_loop', {'status': 'complete', 'reassessed': waiting['reassessed'], 'counts': waiting['counts']})
    except Exception as error:
        _observed('learning_loop', {'status': 'failed'}, failed=True, error=error)
        _report('learning loop failed', error)


def _govern_cycle():
    try:
        from prediction_engine import ensure_goal_sport_models, govern_models, reprice_active_batch, review_degradation
        with connect() as connection:
            from analysis_coverage import repair_identities
            repair_identities(connection)
            connection.execute("UPDATE sports_ai_schedules SET enabled=1 WHERE id='schedule-discovery' AND enabled=0")
        with connect() as connection:
            ensure_goal_sport_models(connection)
        with connect() as connection:
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
        _observed('optimize', {'status': 'complete', 'qualified': qualification.get('counts', {}).get('QUALIFIED', 0)})
    except Exception as error:
        _observed('optimize', {'status': 'failed'}, failed=True, error=error)
        _report('governance failed', error)


def _monitor_accumulators():
    try:
        current=datetime.now(timezone.utc)
        window=current.replace(minute=(current.minute // 5) * 5,second=0,microsecond=0).isoformat()
        with connect() as connection:
            result=monitor_accumulators(
                connection,'sports-ai-worker',_audit,f'accumulator-monitor:v3:{window}',
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
    if args.loop:
        from high_odds_service import start_background
        start_background(_leader_name())
        start_booking_lane()
    if args.once:
        tick()
    else:
        while True:
            tick()
            time.sleep(30)
