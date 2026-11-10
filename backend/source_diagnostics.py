"""Connection and diagnostics read model for the Source Management pages.

Every figure comes from stored rows or a cached local runtime check. A provider
is reported in one honest state:

* ``healthy``       collected within twice its interval (or 48 h for passes without one)
* ``stale``         enabled and collected before, but not recently
* ``failing``       the latest attempt failed after the latest success
* ``paused``        its schedule is paused; the worker will not run it
* ``disabled``      not enabled, or terms not reviewed
* ``not_configured`` a required credential is missing from the environment
* ``awaiting``      enabled but nothing collected yet

Actions are offered only where the backend can perform them: Test and Collect
for API adapters, Resume for a paused API schedule. Browser and feed sources run
on their own worker passes and are never tested through the API path.
"""
import os
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

API_ADAPTERS = {'openligadb', 'openfootball', 'football_data', 'balldontlie', 'sportsdataverse_csv',
                'the_odds_api', 'authorized_json'}
CHANNELS = {
    'browser': 'Browser history pass',
    'history_browser': 'Browser history pass',
    'manual': 'SportyBet feed and booking lane',
    'manual_odds': 'Manual import',
    'csv_import': 'Dataset upload',
}
SPORT_LABELS = {'football': 'Football', 'basketball': 'Basketball', 'both': 'Multi-sport'}
_BROWSER_CACHE = {'at': 0.0, 'value': None}
BROWSER_TTL_SECONDS = 600


def _now(now=None):
    return now or datetime.now(timezone.utc)


def _parse(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00').replace(' ', 'T'))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def browser_runtime(force=False):
    """Playwright/Chromium availability, cached so a page refresh does not launch a browser driver."""
    if not force and _BROWSER_CACHE['value'] is not None and time.monotonic() - _BROWSER_CACHE['at'] < BROWSER_TTL_SECONDS:
        return _BROWSER_CACHE['value']
    from acquisition_engine import browser_runtime as probe
    value = probe()
    value['checked_at'] = datetime.now(timezone.utc).isoformat()
    _BROWSER_CACHE.update(at=time.monotonic(), value=value)
    return value


_FIXTURE_CACHE = {'at': 0.0, 'value': None}
FIXTURE_TTL_SECONDS = 300


def _fixture_counts(connection, now):
    """Fixture totals change slowly and cost ~1 s on the live table, so they are cached for five minutes."""
    if _FIXTURE_CACHE['value'] is not None and time.monotonic() - _FIXTURE_CACHE['at'] < FIXTURE_TTL_SECONDS:
        return _FIXTURE_CACHE['value']
    fixtures = [dict(r) for r in connection.execute(
        'SELECT sport, source, COUNT(*) AS fixtures FROM sports_ai_fixtures GROUP BY sport, source ORDER BY fixtures DESC')]
    upcoming = connection.execute(
        "SELECT COUNT(*) FROM sports_ai_fixtures WHERE status='scheduled' AND starts_at>?", (now.isoformat(),)).fetchone()[0]
    _FIXTURE_CACHE.update(at=time.monotonic(), value=(fixtures, upcoming))
    return fixtures, upcoming


def provider_state(row, schedule, now, permission=None):
    """Return (state, label, detail) for one provider row."""
    adapter = row['adapter']
    credential = row.get('credential_env') or ''
    success = _parse(row.get('last_success_at'))
    attempt = _parse(row.get('last_attempt_at'))
    error = (row.get('last_error') or '').strip()
    interval = int(row.get('interval_minutes') or 360)
    fresh_window = timedelta(minutes=max(2 * interval, 60)) if adapter in API_ADAPTERS else timedelta(hours=48)
    if credential and not os.environ.get(credential):
        return 'not_configured', 'Not configured', f'Set {credential} in the backend environment to enable this provider.'
    if adapter in ('browser', 'history_browser') and permission is not None and not permission[0]:
        return 'disabled', 'Awaiting terms review', 'The host has no approved terms review, so no page is opened.'
    if schedule and schedule.get('paused'):
        when = _parse(schedule.get('updated_at'))
        by = schedule.get('updated_by') or 'an operator'
        detail = f"Schedule paused by {by}" + (f" on {when.strftime('%d %b %Y %H:%M UTC')}" if when else '') + '.'
        if error:
            detail += f' Last error: {error}'
        return 'paused', 'Paused', detail
    if not row.get('enabled') or not row.get('terms_reviewed'):
        reason = 'Terms have not been reviewed.' if not row.get('terms_reviewed') else 'Collection is not enabled.'
        return 'disabled', 'Not enabled', reason
    if error and (success is None or (attempt and attempt > success)):
        return 'failing', 'Failing', error
    if success is None:
        return 'awaiting', 'Awaiting first collection', 'Enabled; no record has been saved yet.'
    if now - success > fresh_window:
        return 'stale', 'Stale', f"Last saved data is older than {int(fresh_window.total_seconds() // 3600)} hours."
    return 'healthy', 'Healthy', 'Collected recently.'


def _sportybet(connection, now):
    latest = connection.execute(
        'SELECT observed_at FROM sports_ai_sportybet_observations ORDER BY rowid DESC LIMIT 1').fetchone()
    latest_quote = connection.execute(
        "SELECT observed_at FROM sports_ai_odds_snapshots ORDER BY rowid DESC LIMIT 1").fetchone()
    since = (now - timedelta(hours=24)).isoformat()
    codes = connection.execute(
        """SELECT COUNT(*) FROM sports_ai_booking_codes
           WHERE bookmaker='SportyBet' AND verification_method='displayed' AND created_at>=?""", (since,)).fetchone()[0]
    last_code = connection.execute(
        """SELECT created_at FROM sports_ai_booking_codes WHERE bookmaker='SportyBet' AND verification_method='displayed'
           ORDER BY created_at DESC LIMIT 1""").fetchone()
    observed = _parse(latest['observed_at']) if latest else None
    quote = _parse(latest_quote['observed_at']) if latest_quote else None
    newest = max((m for m in (observed, quote) if m), default=None)
    if newest is None:
        state, label = 'awaiting', 'No prices yet'
    elif now - newest <= timedelta(hours=2):
        state, label = 'healthy', 'Prices current'
    elif now - newest <= timedelta(hours=24):
        state, label = 'stale', 'Prices ageing'
    else:
        state, label = 'failing', 'No recent prices'
    return {
        'state': state, 'label': label,
        'last_price_at': newest.isoformat() if newest else None,
        'codes_24h': codes, 'last_code_at': last_code['created_at'] if last_code else None,
        'detail': 'Displayed SportyBet prices are read from the public page. Booking codes are requested only for qualified '
                  'tickets inside the WAT publication windows and stored only when the page shows one. No wager is placed.',
        'automatic_wager_placement': False,
    }


def _scheduler(connection, now):
    lease = connection.execute(
        "SELECT owner, expires_at, heartbeat_at FROM sports_ai_worker_leases WHERE id='scheduler-leader'").fetchone()
    beat = _parse(lease['heartbeat_at']) if lease else None
    age = (now - beat).total_seconds() if beat else None
    if age is None:
        state, label = 'failing', 'No scheduler heartbeat'
    elif age <= 180:
        state, label = 'healthy', 'Running'
    elif age <= 900:
        state, label = 'stale', 'Heartbeat delayed'
    else:
        state, label = 'failing', 'Scheduler stopped'
    return {
        'state': state, 'label': label, 'owner': lease['owner'] if lease else None,
        'heartbeat_at': lease['heartbeat_at'] if lease else None, 'heartbeat_age_seconds': None if age is None else round(age),
        'enabled_provider_schedules': connection.execute(
            'SELECT COUNT(*) FROM sports_ai_acquisition_schedules WHERE enabled=1 AND paused=0').fetchone()[0],
        'paused_provider_schedules': connection.execute(
            'SELECT COUNT(*) FROM sports_ai_acquisition_schedules WHERE paused=1').fetchone()[0],
        'detail': 'backend/worker.py --loop is the only collector; the web process never runs a second scheduler.',
    }


def diagnostics(connection, now=None):
    from source_permissions import terms_decision
    current = _now(now)
    schedules = {row['provider_id']: dict(row) for row in connection.execute('SELECT * FROM sports_ai_acquisition_schedules')}
    providers = []
    for raw in connection.execute(
        """SELECT id,provider_key,display_name,sport,adapter,enabled,terms_reviewed,status,credential_env,
                  last_success_at,last_attempt_at,last_error,next_run_at,interval_minutes,terms_url
           FROM sports_ai_provider_registry ORDER BY display_name""",
    ):
        row = dict(raw)
        schedule = schedules.get(row['id'])
        host = (urlsplit(row['terms_url'] or '').hostname or '').casefold()
        permission = terms_decision(connection, host) if host and row['adapter'] in ('browser', 'history_browser') else None
        state, label, detail = provider_state(row, schedule, current, permission)
        if row['provider_key'] == 'sportybet':
            feed = _sportybet(connection, current)
            state, label, detail = feed['state'], feed['label'], feed['detail']
            row['last_success_at'] = feed['last_price_at']
        api = row['adapter'] in API_ADAPTERS
        credential_ok = not row['credential_env'] or bool(os.environ.get(row['credential_env']))
        providers.append({
            'id': row['id'], 'key': row['provider_key'], 'name': row['display_name'],
            'sport': row['sport'], 'sport_label': SPORT_LABELS.get(row['sport'], row['sport']),
            'adapter': row['adapter'], 'channel': 'Scheduled API' if api else CHANNELS.get(row['adapter'], row['adapter']),
            'state': state, 'label': label, 'detail': detail,
            'enabled': bool(row['enabled']), 'terms_reviewed': bool(row['terms_reviewed']),
            'credential': 'Not required' if not row['credential_env'] else ('Configured' if credential_ok else 'Missing'),
            'credential_env': row['credential_env'] or None,
            'last_success_at': row['last_success_at'], 'last_attempt_at': row['last_attempt_at'],
            'last_error': (row['last_error'] or None),
            'next_run_at': None if not api or (schedule and schedule.get('paused')) else row['next_run_at'],
            'interval_minutes': (schedule or {}).get('interval_minutes') or row['interval_minutes'],
            'schedule': None if not schedule else {k: schedule.get(k) for k in ('enabled', 'paused', 'updated_by', 'updated_at')},
            'source_url': row['terms_url'] or None,
            'actions': {
                'test': api and credential_ok,
                'collect': api and credential_ok and bool(row['enabled']) and bool(row['terms_reviewed'])
                           and not (schedule and schedule.get('paused')),
                'resume': api and credential_ok and bool(row['terms_reviewed']) and bool(schedule and schedule.get('paused')),
            },
        })
    names = {p['id']: p['name'] for p in providers}
    jobs = []
    for raw in connection.execute(
        """SELECT id,provider_id,kind,status,started_at,finished_at,records_seen,records_saved,error
           FROM sports_ai_acquisition_jobs ORDER BY started_at DESC LIMIT 25""",
    ):
        job = dict(raw)
        start, end = _parse(job['started_at']), _parse(job['finished_at'])
        job['provider'] = names.get(job['provider_id'], job['provider_id'] or 'Unknown provider')
        job['duration_seconds'] = round((end - start).total_seconds(), 1) if start and end else None
        jobs.append(job)
    fixtures, upcoming = _fixture_counts(connection, current)
    sportybet = _sportybet(connection, current)
    scheduler = _scheduler(connection, current)
    browser = browser_runtime()
    journal = connection.execute('PRAGMA journal_mode').fetchone()[0]
    counts = {}
    for p in providers:
        counts[p['state']] = counts.get(p['state'], 0) + 1
    issues = []
    for p in providers:
        if p['state'] == 'paused' and p['last_error']:
            issues.append({'tone': 'warning', 'provider_id': p['id'], 'action': 'resume' if p['actions']['resume'] else None,
                           'title': f"{p['name']} is paused after a failure",
                           'detail': p['detail']})
        elif p['state'] == 'failing':
            issues.append({'tone': 'danger', 'provider_id': p['id'], 'action': 'test' if p['actions']['test'] else None,
                           'title': f"{p['name']} is failing", 'detail': p['last_error'] or p['detail']})
        elif p['state'] == 'stale':
            issues.append({'tone': 'warning', 'provider_id': p['id'], 'action': 'collect' if p['actions']['collect'] else None,
                           'title': f"{p['name']} has stale data", 'detail': p['detail']})
    if scheduler['state'] != 'healthy':
        issues.insert(0, {'tone': 'danger', 'title': scheduler['label'], 'detail': scheduler['detail'], 'action': None})
    if not browser.get('chromium'):
        issues.append({'tone': 'warning', 'title': 'Browser runtime unavailable', 'detail': browser.get('detail'), 'action': None})
    return {
        'generated_at': current.isoformat(),
        'backend': {'state': 'healthy', 'label': 'Connected'},
        'database': {'state': 'healthy' if journal == 'wal' else 'stale', 'label': f'SQLite · {journal.upper()}'},
        'data_mode': 'live',
        'fixtures': {
            'football': sum(r['fixtures'] for r in fixtures if r['sport'] == 'football'),
            'basketball': sum(r['fixtures'] for r in fixtures if r['sport'] == 'basketball'),
            'total': sum(r['fixtures'] for r in fixtures), 'upcoming': upcoming, 'by_source': fixtures,
        },
        'providers': providers,
        'provider_counts': counts,
        'issues': issues,
        'recent_collections': jobs,
        'sportybet': sportybet,
        'scheduler': scheduler,
        'browser': browser,
        'approved_browser_hosts': connection.execute(
            'SELECT COUNT(*) FROM sports_ai_source_permissions WHERE approved=1').fetchone()[0],
        'note': 'Predictions are stored only from collected results. Missing data stays missing; nothing is substituted.',
    }
