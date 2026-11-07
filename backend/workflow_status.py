"""Live status for the 9-stage workflow. Every figure is a stored count.

Discover through Tickets & Booking Codes read upcoming fixtures. Settle,
Learn and Optimize read completed matches. Rollover is a parallel engine
and does not hold either lane. An unfinished kickoff does not hold the next
cycle, and no stage places a wager.
"""

from datetime import datetime, timedelta, timezone

from booking_schedule import last_publication_at, phase_at, publication_due


def workflow_board(connection, now=None, ready_slips=0, qualified_visible=0, win_rate=None):
    current = now or datetime.now(timezone.utc)
    heartbeat = connection.execute(
        "SELECT heartbeat_at FROM sports_ai_worker_leases WHERE id='scheduler-leader'",
    ).fetchone()
    seen = _parse(heartbeat['heartbeat_at']) if heartbeat else None
    fresh = bool(seen) and (current - seen).total_seconds() < 120
    cycles = _latest_cycles(connection)
    catalogue = connection.execute(
        "SELECT COUNT(*) FROM sports_ai_leagues WHERE season='catalogue'",
    ).fetchone()[0]
    football = connection.execute(
        "SELECT COUNT(*) FROM sports_ai_leagues WHERE season='catalogue' AND sport_code='football'",
    ).fetchone()[0]
    upcoming = connection.execute(
        "SELECT COUNT(*) FROM sports_ai_fixtures WHERE status='scheduled' AND starts_at>?",
        (current.isoformat(),),
    ).fetchone()[0]
    fixtures = connection.execute('SELECT COUNT(*) FROM sports_ai_fixtures').fetchone()[0]
    fixtures_24h = connection.execute(
        'SELECT COUNT(*) FROM sports_ai_fixtures WHERE observed_at>=?',
        ((current - timedelta(hours=24)).isoformat(),),
    ).fetchone()[0]
    predictions = connection.execute('SELECT COUNT(*) FROM sports_ai_engine_predictions').fetchone()[0]
    predictions_24h = connection.execute(
        'SELECT COUNT(*) FROM sports_ai_engine_predictions WHERE created_at>=?',
        ((current - timedelta(hours=24)).isoformat(),),
    ).fetchone()[0]
    analysed = connection.execute(
        'SELECT COUNT(DISTINCT fixture_id) FROM sports_ai_engine_predictions',
    ).fetchone()[0]
    issues = connection.execute(
        """SELECT COUNT(*) FROM sports_ai_data_quality_issues
           WHERE resolved_at IS NULL AND severity IN ('high','critical') AND fixture_id IS NOT NULL""",
    ).fetchone()[0]
    odds_row = connection.execute(
        "SELECT COUNT(*) AS total, MAX(observed_at) AS latest FROM sports_ai_odds_snapshots WHERE bookmaker='SportyBet'",
    ).fetchone()
    odds_age = _age_seconds(current, odds_row['latest'])
    codes = connection.execute(
        "SELECT COUNT(*) FROM sports_ai_booking_codes WHERE verification_method='displayed'",
    ).fetchone()[0]
    settled = connection.execute(
        "SELECT COUNT(*) FROM sports_ai_results WHERE status='final'",
    ).fetchone()[0]
    rollover = connection.execute(
        "SELECT COUNT(*) FROM sports_ai_rollover_campaigns WHERE status='active'",
    ).fetchone()[0]
    coded = connection.execute(
        """SELECT COUNT(DISTINCT t.id)
           FROM sports_ai_accumulator_tickets t
           JOIN sports_ai_booking_codes b ON b.accumulator_ticket_id=t.id
           WHERE t.status='qualified' AND b.verification_method='displayed'
             AND TRIM(COALESCE(b.code, ''))<>''""",
    ).fetchone()[0]
    rejection = connection.execute(
        """SELECT reason_code, COUNT(*) AS total
           FROM sports_ai_qualification_decisions
           WHERE state!='QUALIFIED'
           GROUP BY reason_code ORDER BY total DESC LIMIT 1""",
    ).fetchone()
    active_models = connection.execute(
        "SELECT COUNT(*) FROM sports_ai_model_registry WHERE active=1",
    ).fetchone()[0]
    sources = [
        row['display_name'] for row in connection.execute(
            """SELECT display_name FROM sports_ai_provider_registry
               WHERE enabled=1 AND status='operational' ORDER BY display_name""",
        )
    ]
    if odds_row['total']:
        sources.append('SportyBet displayed prices')
    booking_open = publication_due(current, last_publication_at(connection))
    learn = cycles.get('learning') or {}
    learn_age = _age_seconds(current, learn.get('created_at'))
    stages = [
        _stage('Discover', 'Catalog leagues and fixtures', 'sources', 'upcoming',
               f'{_fmt(catalogue)} competitions',
               'Completed' if catalogue else 'Waiting',
               f'{_fmt(football)} football competitions are registered. {_fmt(upcoming)} fixtures are still to play.'),
        _stage('Collect Odds', 'Retrieve prices from SportyBet', 'sportybet-browser', 'upcoming',
               f'{_fmt(odds_row["total"])} prices',
               'Running' if fresh and odds_age is not None and odds_age < 20 * 60 else 'Completed' if odds_age is not None and odds_age < 6 * 3600 else 'Waiting',
               'Only a price SportyBet displayed is stored.'),
        _stage('Validate', 'Check data integrity', 'diagnostics', 'upcoming',
               'Clear' if not issues else f'{_fmt(issues)} open issues',
               'Completed' if not issues else 'Attention',
               'A fixture with an open high-severity issue stays out of qualification.'),
        _stage('Analyse', 'Sport models on stored scores', 'football-analysis', 'upcoming',
               f'{_fmt(analysed)} fixtures',
               'Running' if fresh and analysed else 'Waiting' if not analysed else 'Completed',
               'Each cycle prices up to 24 fixtures that still have no prediction. An unfinished match is not required.'),
        _stage('Qualify', 'Probability, edge and market scan', 'qualification', 'upcoming',
               f'{_fmt(qualified_visible)} visible',
               'Running' if fresh else 'Completed' if qualified_visible else 'Waiting',
               _qualify_note(qualified_visible, rejection)),
        _stage('Tickets & Booking Codes', 'Finalize the ticket, then register its code', 'tickets', 'upcoming',
               f'{_fmt(ready_slips)} ready · {_fmt(coded)} coded',
               'Running' if fresh and booking_open else 'Completed' if coded else 'Waiting',
               _ticket_note(ready_slips, coded)),
        _stage('Settle', 'Official results for completed matches', 'results', 'completed',
               f'{_fmt(settled)} finals',
               'Running' if fresh else 'Waiting',
               'Completed matches settle on this pass. Upcoming fixtures are not held.'),
        _stage('Learn', 'Calibration on settled predictions', 'models', 'completed',
               'Held' if learn.get('status') == 'failed' else 'Recorded' if learn_age is not None else 'Scheduled',
               'Attention' if learn.get('status') == 'failed' else 'Completed' if learn_age is not None and learn_age < 6 * 3600 else 'Scheduled',
               'Ceilings stay at 30 samples, 0.20 calibration error, and 30 quoted fixtures.'),
        _stage('Optimize', 'Holdout gate and ticket constraints', 'optimizer', 'completed',
               f'{_fmt(active_models)} active',
               'Completed' if active_models else 'Scheduled',
               'The holdout gate stays in place. Optimization does not lower a probability floor or invent a price.'),
    ]
    completed = sum(1 for item in stages if item['status'] == 'Completed')
    return {
        'places_wager': False,
        'asynchronous': True,
        'note': 'Stage 6 finalizes a qualified ticket and then requests its SportyBet code. Settlement does not wait for rollover.',
        'parallel': {
            'name': 'Rollover',
            'detail': 'Daily and weekly simulation',
            'route': 'daily-rollover',
            'count': f'{_fmt(rollover)} active',
            'status': 'Running' if rollover else 'Waiting',
            'explanation': 'Rollover runs beside the nine stages. It does not delay a ticket, a booking code, or settlement. Simulation only. No wager is placed.',
            'places_wager': False,
        },
        'worker': 'responding' if fresh else 'heartbeat stale',
        'frequency': 'Every 30 seconds',
        'next_cycle': _next_cycle(current, seen),
        'last_cycle': None if not seen else seen.isoformat(),
        'progress': round(100 * completed / len(stages)),
        'completed': completed,
        'sources': ', '.join(sources) if sources else 'No operational source is stored',
        'metrics': [
            {'label': 'Fixtures stored', 'value': _fmt(fixtures), 'note': f'+{_fmt(fixtures_24h)} in 24h' if fixtures_24h else 'Stored'},
            {'label': 'Predictions stored', 'value': _fmt(predictions), 'note': f'+{_fmt(predictions_24h)} in 24h' if predictions_24h else 'Stored'},
            {'label': 'Qualified opportunities', 'value': _fmt(qualified_visible), 'note': 'Above 80%'},
            {'label': 'Ready slips', 'value': _fmt(ready_slips), 'note': 'Above 70%'},
            {'label': 'Booking codes', 'value': _fmt(codes), 'note': 'Displayed'},
            {'label': 'Settled win rate', 'value': 'Awaiting results' if win_rate is None else f'{win_rate}%', 'note': 'Prospective selections'},
        ],
        'stages': stages,
    }


def _qualify_note(visible, rejection):
    if visible:
        return 'Visible opportunities are strictly above 80 percent, one market per fixture.'
    if rejection:
        return (
            'No fixture this week is strictly above 80 percent. '
            f'The most common stored decision is {rejection["reason_code"]} ({_fmt(rejection["total"])}).'
        )
    return 'No qualification decision is stored yet.'


def _ticket_note(ready, coded):
    waiting = max(0, int(ready or 0) - int(coded or 0))
    return (
        f'{_fmt(coded)} qualified ticket(s) carry a code SportyBet displayed. '
        f'{_fmt(waiting)} ready ticket(s) have none yet. '
        'The ticket is finalized first. Registration then runs in the same stage. '
        'A code is stored only when SportyBet displays it.'
    )


def _stage(name, detail, route, lane, count, status, explanation):
    return {
        'name': name,
        'detail': detail,
        'route': route,
        'lane': lane,
        'count': count,
        'status': status,
        'explanation': explanation,
        'places_wager': False,
    }


def _latest_cycles(connection):
    rows = connection.execute(
        """SELECT c.stage, c.status, c.created_at
           FROM sports_ai_autonomous_cycles c
           JOIN (
             SELECT stage, MAX(created_at) AS created_at FROM sports_ai_autonomous_cycles GROUP BY stage
           ) latest ON latest.stage=c.stage AND latest.created_at=c.created_at""",
    ).fetchall()
    return {row['stage']: {'status': row['status'], 'created_at': row['created_at']} for row in rows}


def _next_cycle(current, seen):
    if not seen:
        return 'When the worker is running'
    due = seen + timedelta(seconds=30)
    remaining = int((due - current).total_seconds())
    if remaining <= 0:
        return 'Due now'
    minutes, seconds = divmod(remaining, 60)
    return f'In {minutes:02d}:{seconds:02d}'


def _age_seconds(current, value):
    parsed = _parse(value)
    if not parsed:
        return None
    return (current - parsed).total_seconds()


def _parse(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _fmt(value):
    return f'{int(value or 0):,}'
