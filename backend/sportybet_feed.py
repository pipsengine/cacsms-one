"""Read prices SportyBet displays on its public sports pages.

The collector uses a normal browser session. It does not solve a challenge,
does not guess a number from page text, and does not place a wager.
"""

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from db import connect
from sportybet_research import (
    BOOKMAKER,
    accept_displayed_quotes,
    blocked_page,
    map_market,
    store_observations,
    validate_decimal,
)

SECTIONS = (
    ('basketball', 'https://www.sportybet.com/ng/sport/basketball'),
    ('football', 'https://www.sportybet.com/ng/sport/football'),
    ('ice_hockey', 'https://www.sportybet.com/ng/sport/iceHockey'),
)
MAX_PAGES = 3
REFRESH_MINUTES = 15


def quotes_from_payload(payload, sport, source_url):
    """Turn the events object the sports page loads into explicit prices."""
    data = (payload or {}).get('data') or {}
    rows = []
    for tournament in data.get('tournaments') or []:
        competition = ' '.join(
            part for part in (tournament.get('categoryName'), tournament.get('name')) if part
        )
        for event in tournament.get('events') or []:
            home = (event.get('homeTeamName') or '').strip()
            away = (event.get('awayTeamName') or '').strip()
            if not home or not away or home == away:
                continue
            starts_at = _epoch_ms(event.get('estimateStartTime'))
            for market in event.get('markets') or []:
                if market.get('status') not in (0, '0'):
                    continue
                label = (market.get('desc') or market.get('name') or '').strip()
                line = _specifier_line(market.get('specifier') or '')
                if not label:
                    continue
                for outcome in market.get('outcomes') or []:
                    if outcome.get('isActive') not in (1, '1'):
                        continue
                    odds = validate_decimal(outcome.get('odds'))
                    selection = (outcome.get('desc') or '').strip()
                    if odds is None or not selection or not map_market(sport, label, selection, line).get('supported'):
                        continue
                    rows.append({
                        'sport': sport,
                        'competition': competition,
                        'home': home,
                        'away': away,
                        'starts_at': starts_at,
                        'market_label': label,
                        'selection_label': selection,
                        'decimal_odds': odds,
                        'line': line,
                        'source_url': source_url,
                    })
    return rows


def publish_quotes(connection, rows, source_url, actor='sports-ai-worker', now=None):
    now = now or datetime.now(timezone.utc)
    if not rows:
        return {'saved': 0, 'imported': 0, 'skipped': 0}
    stored = store_observations(connection, None, rows, source_url, now.isoformat())
    accepted = accept_displayed_quotes(connection, stored['saved'], actor, now)
    accepted['saved'] = len(stored['saved'])
    return accepted


def collect_displayed_odds():
    """Open the public sports pages and store the prices they display."""
    if not _due():
        return {'status': 'waiting', 'imported': 0}
    _authorize()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {'status': 'failed', 'error': 'Playwright is not installed.', 'imported': 0}
    imported = 0
    blocked = []
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            for sport, url in SECTIONS:
                try:
                    result = _read_section(page, sport, url)
                except Exception as error:
                    errors.append({'sport': sport, 'error': str(error)[:300]})
                    continue
                if result.get('blocked'):
                    blocked.append(sport)
                    continue
                with connect() as connection:
                    published = publish_quotes(connection, result['rows'], url)
                imported += published['imported']
        finally:
            browser.close()
    status = 'blocked' if blocked and not imported else 'collected' if imported else 'empty'
    _record(status, imported, blocked, errors)
    return {'status': status, 'imported': imported, 'blocked': blocked, 'errors': errors}


def _read_section(page, sport, url):
    captured = {}

    def remember(response):
        if response.status == 200 and 'pcUpcomingEvents' in response.url and 'payload' not in captured:
            try:
                captured['payload'] = response.json()
                captured['url'] = response.url
            except Exception:
                return

    page.on('response', remember)
    try:
        page.goto(url, wait_until='domcontentloaded', timeout=45000)
        page.wait_for_timeout(6000)
        text = ''
        try:
            text = page.locator('body').inner_text(timeout=8000)
        except Exception:
            text = ''
        if blocked_page(text):
            return {'blocked': blocked_page(text), 'rows': []}
        payload = captured.get('payload')
        if not payload:
            return {'blocked': None, 'rows': []}
        rows = []
        for body in _pages(page, captured.get('url'), payload):
            rows.extend(quotes_from_payload(body, sport, url))
        return {'blocked': None, 'rows': rows}
    finally:
        page.remove_listener('response', remember)


def _pages(page, first_url, first_payload):
    bodies = [first_payload]
    total = int(((first_payload or {}).get('data') or {}).get('totalNum') or 0)
    if not first_url or total <= 100:
        return bodies
    parts = urlsplit(first_url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    last = min(MAX_PAGES, max(1, (total + 99) // 100))
    for number in range(2, last + 1):
        query['pageNum'] = str(number)
        target = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ''))
        response = page.context.request.get(target, timeout=20000)
        if response.status != 200:
            break
        try:
            body = response.json()
        except Exception:
            break
        if not ((body.get('data') or {}).get('tournaments')):
            break
        bodies.append(body)
    return bodies


def _due():
    with connect() as connection:
        row = connection.execute(
            """SELECT finished_at, status FROM sports_ai_browser_jobs
               WHERE url='https://www.sportybet.com/ng/sport/basketball'
               ORDER BY created_at DESC LIMIT 1"""
        ).fetchone()
    if not row or not row['finished_at']:
        return True
    if row['status'] == 'running':
        return False
    try:
        finished = datetime.fromisoformat(str(row['finished_at']).replace('Z', '+00:00'))
    except ValueError:
        return True
    now = datetime.now(timezone.utc)
    age = now - finished
    if age >= timedelta(minutes=REFRESH_MINUTES):
        return True
    if age < timedelta(minutes=3):
        return False
    from booking_schedule import phase_at, publication_open
    if not publication_open(phase_at(now)):
        return False
    with connect() as connection:
        latest = connection.execute(
            "SELECT MAX(observed_at) FROM sports_ai_odds_snapshots WHERE bookmaker=?",
            (BOOKMAKER,),
        ).fetchone()[0]
    if not latest:
        return True
    try:
        observed = datetime.fromisoformat(str(latest).replace('Z', '+00:00'))
    except ValueError:
        return True
    if observed.tzinfo is None:
        return False
    return now - observed >= timedelta(minutes=10)


def _authorize():
    with connect() as connection:
        existing = connection.execute(
            """SELECT 1 FROM sports_ai_collection_authorizations
               WHERE bookmaker=? AND scope='unattended_extraction' AND status='authorized'""",
            (BOOKMAKER,),
        ).fetchone()
        if existing:
            return
        connection.execute(
            """INSERT INTO sports_ai_collection_authorizations
               (id,bookmaker,scope,status,evidence,actor,created_at)
               VALUES(?,?,?,?,?,?,?)""",
            (
                str(uuid.uuid4()), BOOKMAKER, 'unattended_extraction', 'authorized',
                'Automatic collection of the prices shown on the public SportyBet sports pages. '
                'A challenge stops the run. No wager is placed.',
                'sports-ai-worker', datetime.now(timezone.utc).isoformat(),
            ),
        )


def _record(status, imported, blocked, errors):
    now = datetime.now(timezone.utc).isoformat()
    with connect() as connection:
        connection.execute(
            """INSERT INTO sports_ai_browser_jobs
               (id,url,status,created_at,started_at,finished_at,title,observations,error)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                str(uuid.uuid4()), 'https://www.sportybet.com/ng/sport/basketball',
                'inspected' if status == 'collected' else 'failed',
                now, now, now, 'SportyBet displayed prices', imported,
                None if not blocked and not errors else json.dumps({'blocked': blocked, 'errors': errors})[:500],
            ),
        )


def _epoch_ms(value):
    try:
        return datetime.fromtimestamp(float(value) / 1000, timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _specifier_line(specifier):
    match = re.search(r'(?:total|hcp)=(-?\d+(?:\.\d+)?)', specifier or '')
    if not match:
        return None
    number = float(match.group(1))
    if abs(number * 4 - round(number * 4)) > 1e-8:
        return None
    return number
