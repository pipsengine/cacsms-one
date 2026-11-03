"""Allowlisted browser jobs for the existing Sports AI worker.

Odds are never inferred from arbitrary page text. SportyBet is queued only
after an unattended-collection authorization exists, and even then this module
does not extract prices without a reviewed adapter.
"""

import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
import uuid

from browser_operator import BrowserResearchError, validate_research_url
from db import connect
from sportybet_research import blocked_page, unattended_status

SPORTYBET_MARKERS = ('sportybet.com',)


def now():
    return datetime.now(timezone.utc)


def allowlist():
    return {item.strip().casefold() for item in os.environ.get('SPORTS_AI_AUTHORIZED_DOMAINS', '').split(',') if item.strip()}


def authorized_urls():
    return [item.strip() for item in os.environ.get('SPORTS_AI_AUTHORIZED_URLS', '').split(',') if item.strip()]


def _sportybet(hostname):
    host = (hostname or '').casefold().rstrip('.')
    return any(host == marker or host.endswith('.' + marker) for marker in SPORTYBET_MARKERS)


def check_url(url, connection=None):
    parsed = urlsplit(url)
    host = (parsed.hostname or '').casefold().rstrip('.')
    local = (
        os.environ.get('SPORTS_BROWSER_ALLOW_LOCAL_TESTS') == '1'
        and host in {'localhost', '127.0.0.1'}
        and parsed.scheme in {'http', 'https'}
        and not parsed.username and not parsed.password and not parsed.fragment
    )
    if not local:
        try:
            validate_research_url(url, allowlist())
        except BrowserResearchError as error:
            raise ValueError(str(error)) from error
    if _sportybet(host):
        if connection is None:
            with connect() as owned:
                authorized = unattended_status(owned)['authorized']
        else:
            authorized = unattended_status(connection)['authorized']
        if not authorized:
            raise ValueError('SportyBet is not authorized for unattended collection')
    return host


def create_job(url, connection=None):
    check_url(url, connection)
    job_id = str(uuid.uuid4())

    def insert(db):
        active = db.execute(
            """SELECT 1 FROM sports_ai_browser_jobs
               WHERE url=? AND status IN ('queued','claimed','running')""",
            (url,),
        ).fetchone()
        if active:
            raise ValueError('That page is already queued.')
        db.execute(
            """INSERT INTO sports_ai_browser_jobs (id,url,status,created_at) VALUES (?,?,?,?)""",
            (job_id, url, 'queued', now().isoformat()),
        )

    if connection is None:
        with connect() as db:
            insert(db)
    else:
        insert(connection)
    return job_id


def get_job(job_id):
    with connect() as db:
        row = db.execute('SELECT * FROM sports_ai_browser_jobs WHERE id=?', (job_id,)).fetchone()
        return dict(row) if row else None


def list_jobs(limit=50):
    with connect() as db:
        return [dict(row) for row in db.execute(
            'SELECT * FROM sports_ai_browser_jobs ORDER BY created_at DESC LIMIT ?',
            (min(int(limit), 100),),
        )]


def record_quote(job_id, fixture_name, market, selection, decimal_odds, source_url):
    """Store a supplied quote as unconfirmed. It does not enter the value engine."""
    check_url(source_url)
    if not all(isinstance(value, str) and value.strip() for value in (fixture_name, market, selection)):
        raise ValueError('Fixture, market and selection are required')
    if not (1.0 < float(decimal_odds) < 100000):
        raise ValueError('Invalid decimal odds')
    observation_id = str(uuid.uuid4())
    with connect() as db:
        if not db.execute('SELECT 1 FROM sports_ai_browser_jobs WHERE id=?', (job_id,)).fetchone():
            raise ValueError('Unknown job')
        db.execute(
            """INSERT INTO sports_ai_browser_observations
               (id,job_id,source_url,fixture_name,market,selection,decimal_odds,observed_at,confirmed)
               VALUES (?,?,?,?,?,?,?,?,0)""",
            (observation_id, job_id, source_url, fixture_name.strip(), market.strip(), selection.strip(), float(decimal_odds), now().isoformat()),
        )
        db.execute('UPDATE sports_ai_browser_jobs SET observations=observations+1 WHERE id=?', (job_id,))
    return observation_id


def list_observations(job_id):
    with connect() as db:
        return [dict(row) for row in db.execute(
            'SELECT * FROM sports_ai_browser_observations WHERE job_id=? ORDER BY observed_at DESC',
            (job_id,),
        )]


def enqueue_authorized_jobs(connection):
    """Queue explicitly configured pages. An empty allowlist queues nothing."""
    queued = 0
    skipped = []
    for url in authorized_urls():
        try:
            create_job(url, connection)
            queued += 1
        except ValueError as error:
            skipped.append({'url': url, 'reason': str(error)})
    return {
        'status': 'queued' if queued else 'skipped',
        'queued': queued,
        'skipped': skipped,
        'authorized_domains': sorted(allowlist()),
        'reason': (
            'No authorized pages were queued. Permitted API providers continue on their own schedule. '
            'SportyBet is not visited unless unattended collection is authorized and its domain is explicitly allowlisted. '
            'Page text is not converted into odds.'
            if not queued else
            'Authorized pages were queued. Odds are not inferred from generic page text.'
        ),
    }


def recover_browser_jobs(connection, moment=None):
    moment = moment or now()
    recovered = 0
    rows = connection.execute(
        "SELECT * FROM sports_ai_browser_jobs WHERE status IN ('claimed','running')",
    ).fetchall()
    for row in rows:
        stamp = row['heartbeat_at'] or row['started_at'] or row['created_at']
        try:
            seen = datetime.fromisoformat(str(stamp).replace('Z', '+00:00'))
            if seen.tzinfo is None:
                seen = seen.replace(tzinfo=timezone.utc)
        except ValueError:
            seen = moment - timedelta(hours=1)
        if (moment - seen).total_seconds() < 120:
            continue
        attempt = int(row['attempt']) + 1
        if attempt >= int(row['max_attempts']):
            connection.execute(
                """UPDATE sports_ai_browser_jobs
                   SET status='failed', error=?, finished_at=?, attempt=? WHERE id=?""",
                ('The browser worker heartbeat expired before the page finished.', moment.isoformat(), attempt, row['id']),
            )
        else:
            connection.execute(
                """UPDATE sports_ai_browser_jobs
                   SET status='queued', error=?, attempt=?, next_attempt_at=? WHERE id=?""",
                ('Recovered after a missed heartbeat.', attempt, (moment + timedelta(minutes=2 ** attempt)).isoformat(), row['id']),
            )
        recovered += 1
    return recovered


def _claim(connection, moment):
    row = connection.execute(
        """SELECT * FROM sports_ai_browser_jobs
           WHERE status='queued' AND (next_attempt_at IS NULL OR next_attempt_at<=?)
           ORDER BY created_at LIMIT 1""",
        (moment.isoformat(),),
    ).fetchone()
    if not row:
        return None
    attempt = int(row['attempt']) + 1
    connection.execute(
        """UPDATE sports_ai_browser_jobs
           SET status='running', started_at=?, heartbeat_at=?, attempt=? WHERE id=? AND status='queued'""",
        (moment.isoformat(), moment.isoformat(), attempt, row['id']),
    )
    return row['id']


def inspect_job(job_id):
    """Open one allowlisted page. A challenge stops the job and stores no odds."""
    job = get_job(job_id)
    if not job:
        raise ValueError('Unknown job')
    check_url(job['url'])
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        _fail(job_id, 'Playwright is not installed.')
        raise RuntimeError('Playwright is not installed.') from error
    headless = os.environ.get('SPORTS_BROWSER_HEADLESS', '1') == '1'
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=headless)
            try:
                context = browser.new_context(service_workers='block', accept_downloads=False)
                page = context.new_page()
                page.set_default_navigation_timeout(30_000)
                response = page.goto(job['url'], wait_until='domcontentloaded')
                check_url(page.url)
                if response and response.status >= 400:
                    raise RuntimeError(f'HTTP {response.status}')
                text = ''
                try:
                    text = page.locator('body').inner_text(timeout=8_000)
                except Exception:
                    text = ''
                marker = blocked_page(text)
                if marker:
                    raise RuntimeError(f'The page asked for a challenge ({marker}). Extraction stopped.')
                title = (page.title() or '')[:300]
                with connect() as db:
                    db.execute(
                        """UPDATE sports_ai_browser_jobs
                           SET status='inspected', title=?, finished_at=?, heartbeat_at=?, error=NULL WHERE id=?""",
                        (title, now().isoformat(), now().isoformat(), job_id),
                    )
            finally:
                browser.close()
    except Exception as error:
        _fail(job_id, str(error)[:500])
        raise


def _fail(job_id, message):
    moment = now()
    with connect() as db:
        row = db.execute('SELECT attempt, max_attempts FROM sports_ai_browser_jobs WHERE id=?', (job_id,)).fetchone()
        if not row:
            return
        if int(row['attempt']) < int(row['max_attempts']):
            db.execute(
                """UPDATE sports_ai_browser_jobs
                   SET status='queued', error=?, next_attempt_at=? WHERE id=?""",
                (message, (moment + timedelta(minutes=2 ** int(row['attempt']))).isoformat(), job_id),
            )
        else:
            db.execute(
                """UPDATE sports_ai_browser_jobs
                   SET status='failed', error=?, finished_at=? WHERE id=?""",
                (message, moment.isoformat(), job_id),
            )


def process_browser_queue():
    """Run at most one queued page outside the scheduler transaction."""
    with connect() as connection:
        recovered = recover_browser_jobs(connection)
        job_id = _claim(connection, now())
    if not job_id:
        return {'status': 'idle', 'recovered': recovered}
    try:
        inspect_job(job_id)
        return {'status': 'inspected', 'job_id': job_id, 'recovered': recovered}
    except Exception as error:
        return {'status': 'failed', 'job_id': job_id, 'error': str(error)[:300], 'recovered': recovered}


def browser_summary(connection):
    def count(status):
        return connection.execute(
            'SELECT COUNT(*) FROM sports_ai_browser_jobs WHERE status=?', (status,),
        ).fetchone()[0]
    last = connection.execute(
        """SELECT status, error, title, finished_at FROM sports_ai_browser_jobs
           ORDER BY COALESCE(finished_at, started_at, created_at) DESC LIMIT 1"""
    ).fetchone()
    observations = connection.execute('SELECT COUNT(*) FROM sports_ai_browser_observations').fetchone()[0]
    unconfirmed = connection.execute(
        'SELECT COUNT(*) FROM sports_ai_browser_observations WHERE confirmed=0'
    ).fetchone()[0]
    sportybet = unattended_status(connection)
    return {
        'queued': count('queued'),
        'running': count('running'),
        'failed': count('failed'),
        'inspected': count('inspected'),
        'observations': observations,
        'unconfirmed': unconfirmed,
        'last_status': None if not last else last['status'],
        'last_error': None if not last else last['error'],
        'last_title': None if not last else last['title'],
        'last_finished_at': None if not last else last['finished_at'],
        'authorized_domains': sorted(allowlist()),
        'authorized_urls': len(authorized_urls()),
        'sportybet_unattended': sportybet,
        'extracts_odds_from_text': False,
        'places_wager': False,
    }
