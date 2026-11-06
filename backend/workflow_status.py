"""Live status for the 10-stage workflow. Every figure is a stored count.

Discover through booking keep reading upcoming fixtures. Settle and learn
read completed matches on the same cycle. An unfinished kickoff does not
hold either lane, and neither lane places a wager.
"""

from datetime import datetime, timedelta, timezone

from booking_schedule import phase_at, publication_open


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
    sources = [
        row['display_name'] for row in connection.execute(
            """SELECT display_name FROM sports_ai_provider_registry
               WHERE enabled=1 AND status='operational' ORDER BY display_name""",
        )
    ]
    if odds_row['total']:
        sources.append('SportyBet displayed prices')
    booking_open = publication_open(phase_at(current))
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
               'Visible opportunities are strictly above 80 percent, one market per fixture.'),
        _stage('Tickets', 'Singles and accumulators', 'tickets', 'upcoming',
               f'{_fmt(ready_slips)} ready',
               'Running' if fresh else 'Completed' if ready_slips else 'Waiting',
               'Ready slips are strictly above 70 percent and still inside this Monday–Sunday week.'),
        _stage('Rollover', 'Daily and weekly simulation', 'daily-rollover', 'upcoming',
               f'{_fmt(rollover)} active',
               'Running' if rollover else 'Waiting',
               'Simulation only. No wager is placed.'),
        _stage('Booking Codes', 'Retrieve a displayed code', 'codes', 'upcoming',
               f'{_fmt(codes)} displayed',
               'Running' if booking_open else 'Waiting',
               'A code is stored only when SportyBet displays it. One request per open window.'),
        _stage('Settle', 'Official results for completed matches', 'results', 'completed',
               f'{_fmt(settled)} finals',
               'Running' if fresh else 'Waiting',
               'Completed matches settle on this pass. Upcoming fixtures are not held.'),
        _stage('Learn', 'Calibration on settled predictions', 'models', 'completed',
               'Held' if learn.get('status') == 'failed' else 'Recorded' if learn_age is not None else 'Scheduled',
               'Attention' if learn.get('status') == 'failed' else 'Completed' if learn_age is not None and learn_age < 6 * 3600 else 'Scheduled',
               'Ceilings stay at 30 samples, 0.20 calibration error, and 30 quoted fixtures.'),
    ]
    completed = sum(1 for item in stages if item['status'] == 'Completed')
    return {
        'places_wager': False,
        'asynchronous': True,
        'note': 'Settlement and learning run on completed matches. Discovery through booking keep moving on upcoming fixtures.',
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
