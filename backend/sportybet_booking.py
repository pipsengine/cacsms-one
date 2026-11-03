"""Prepare a SportyBet booking code with real clicks. Never place a paid wager."""

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from db import connect
from sportybet_feed import SECTIONS
from sportybet_research import blocked_page

BOOK = re.compile(r'^(book(\s*bet)?|booking\s*code|share\s*code)$', re.I)
CODE = re.compile(r'^[A-Z0-9]{5,16}$')
REFRESH_MINUTES = 15
EVIDENCE = Path(__file__).resolve().parent / 'booking_evidence'

FIND_ODDS = '''(args) => {
  const fold = value => (value || '').toLowerCase().replace(/[^a-z0-9.]+/g, ' ');
  const skip = new Set(['fc','sc','sv','ac','cf','afc','cfc','vfb','vfl','tsg','bsc','fk','sk']);
  const tokens = value => {
    const all = fold(value).split(' ').map(token => token.replace(/\\./g, '')).filter(Boolean);
    const kept = all.filter(token => !skip.has(token) && !/^\\d+$/.test(token));
    return kept.length ? kept : all;
  };
  const wanted = tokens(args.home);
  const other = tokens(args.away);
  const price = Number(args.odds).toFixed(2);
  const exact = [];
  const nearby = [];
  document.querySelectorAll('[data-sports-ai-pick]').forEach(node => node.removeAttribute('data-sports-ai-pick'));
  for (const row of document.querySelectorAll('.match-row, article')) {
    const blob = fold(row.innerText || '');
    if (!wanted.every(token => blob.includes(token)) || !other.every(token => blob.includes(token))) continue;
    for (const node of row.querySelectorAll('.m-outcome, button, a, [role="button"]')) {
      if (node.classList.contains('m-outcome--disabled') || node.classList.contains('m-outcome-odds')) continue;
      const text = (node.innerText || '').trim();
      if (!/^\\d+(?:\\.\\d+)?$/.test(text)) continue;
      const shown = Number(text).toFixed(2);
      const hit = {text: shown};
      nearby.push(hit);
      if (shown === price) exact.push(node);
    }
  }
  if (exact.length === 1) exact[0].setAttribute('data-sports-ai-pick', args.marker);
  return {exact: exact.length, nearby: nearby.slice(0, 8)};
}'''

FIND_BOOK = '''() => {
  const deny = /place\\s*bet|bet\\s*now|\\bpay\\b|deposit|login|stake|confirm\\s*bet/i;
  const allow = /^(book(\\s*bet)?|booking\\s*code|share\\s*code)$/i;
  document.querySelectorAll('[data-sports-ai-book]').forEach(node => node.removeAttribute('data-sports-ai-book'));
  const hits = [...document.querySelectorAll('button, a, [role="button"], span')].filter(node => {
    const text = (node.innerText || '').trim();
    return allow.test(text) && !deny.test(text);
  });
  if (hits.length === 1) hits[0].setAttribute('data-sports-ai-book', '1');
  return hits.length;
}'''

READ_CODES = '''() => {
  const pattern = /^[A-Z0-9]{5,16}$/;
  const root = document.querySelector('.booking-code-share, [data-booking-code], .booking-code');
  if (!root) return [];
  const nodes = root.matches('input') ? [root] : [root, ...root.querySelectorAll('input')];
  return [...new Set(nodes.map(node => (node.value || node.innerText || '').trim()).filter(text => pattern.test(text)))];
}'''

SLIP_CONTAINS = '''(args) => {
  const fold = value => (value || '').toLowerCase().replace(/[^a-z0-9.]+/g, ' ');
  const skip = new Set(['fc','sc','sv','ac','cf','afc','cfc','vfb','vfl','tsg','bsc','fk','sk']);
  const tokens = value => {
    const all = fold(value).split(' ').map(token => token.replace(/\\./g, '')).filter(Boolean);
    const kept = all.filter(token => !skip.has(token) && !/^\\d+$/.test(token));
    return kept.length ? kept : all;
  };
  const present = (token, blob) => blob.includes(token) || (token.length >= 6 && blob.includes(token.slice(0, 5)));
  const parts = [...document.querySelectorAll('.m-pop-main, .share-bet-container')].map(node => node.innerText || '');
  const blob = fold(parts.join(' '));
  const text = parts.join(' ').replace(/\\s+/g, ' ').slice(0, 400);
  return {ok: args.names.every(name => tokens(name).every(token => present(token, blob))), text};
}'''


def _now():
    return datetime.now(timezone.utc)


def _signature(selections):
    ticket = str(selections[0].get('ticket_id') or '')
    parts = sorted(f"{item['fixture_id']}|{item['market']}|{item['selection']}" for item in selections)
    body = '|'.join(parts)
    return f'{ticket}|{body}' if ticket else body


def qualified_selections(connection, now=None):
    """Selections the existing value engine has already qualified. Nothing else is bookable."""
    moment = (now or _now()).isoformat()
    rows = connection.execute(
        """SELECT o.id AS opportunity_id, f.id AS fixture_id, f.sport, f.competition, f.home, f.away, f.starts_at,
                  p.market, p.probability, o.expected_edge AS edge, q.decimal_odds, q.outcome, q.observed_at
           FROM sports_ai_value_opportunities o
           JOIN sports_ai_engine_predictions p ON p.id=o.prediction_id
           JOIN sports_ai_fixtures f ON f.id=p.fixture_id
           JOIN sports_ai_model_registry m ON m.id=p.model_id
           JOIN sports_ai_odds_snapshots q ON q.id=o.odds_id
           WHERE o.status='qualified' AND o.expected_edge>0 AND m.status='active'
             AND q.bookmaker='SportyBet' AND f.status='scheduled' AND f.starts_at>?
           ORDER BY f.starts_at""",
        (moment,),
    ).fetchall()
    selections = []
    for row in rows:
        item = dict(row)
        item['selection'] = item['home'] if item['market'] in ('HOME', 'MONEYLINE_HOME') else item['away'] if item['market'] in ('AWAY', 'MONEYLINE_AWAY') else item['outcome']
        selections.append(item)
    return selections


def create_job(connection, selections):
    if not selections or len(selections) > 6:
        raise ValueError('A booking needs 1 to 6 qualified selections')
    sports = {item['sport'] for item in selections}
    if not sports or len(sports) > 2:
        raise ValueError('A booking uses football, basketball, or both')
    signature = _signature(selections)
    existing = connection.execute(
        """SELECT id,status FROM sports_ai_booking_jobs
           WHERE signature=? AND status IN ('QUEUED','PREPARING','CODE_GENERATED')
           ORDER BY created_at DESC LIMIT 1""",
        (signature,),
    ).fetchone()
    if existing:
        return {'id': existing['id'], 'status': existing['status'], 'duplicate': True, 'places_wager': False}
    job_id = str(uuid.uuid4())
    moment = _now().isoformat()
    connection.execute(
        """INSERT INTO sports_ai_booking_jobs
           (id,created_at,updated_at,status,sport,selections_json,signature,places_wager)
           VALUES(?,?,?,?,?,?,?,0)""",
        (job_id, moment, moment, 'QUEUED', next(iter(sports)) if len(sports) == 1 else 'mixed', json.dumps(selections), signature),
    )
    return {'id': job_id, 'status': 'QUEUED', 'duplicate': False, 'places_wager': False}


def _set_job(connection, job_id, status, **changes):
    columns = ['status=?', 'updated_at=?']
    values = [status, _now().isoformat()]
    for key, value in changes.items():
        if key not in ('code', 'screenshot', 'error', 'odds_json'):
            raise ValueError('invalid booking field')
        columns.append(f'{key}=?')
        values.append(value)
    values.append(job_id)
    connection.execute(f"UPDATE sports_ai_booking_jobs SET {','.join(columns)} WHERE id=?", values)


def list_jobs(connection):
    jobs = []
    for row in connection.execute(
        'SELECT * FROM sports_ai_booking_jobs ORDER BY created_at DESC LIMIT 50',
    ):
        item = dict(row)
        item['selections'] = json.loads(item.pop('selections_json') or '[]')
        item['places_wager'] = False
        odds = [selection['decimal_odds'] for selection in item['selections']]
        item['combined_odds'] = round(__import__('math').prod(odds), 4) if odds else None
        jobs.append(item)
    return jobs


def _due(connection, now):
    latest = connection.execute(
        'SELECT updated_at,status FROM sports_ai_booking_jobs ORDER BY updated_at DESC LIMIT 1',
    ).fetchone()
    if not latest:
        return True
    if latest['status'] in ('QUEUED', 'PREPARING'):
        return True
    try:
        seen = datetime.fromisoformat(str(latest['updated_at']).replace('Z', '+00:00'))
    except ValueError:
        return True
    return now - seen >= timedelta(minutes=REFRESH_MINUTES)


def ticket_selections(connection, ticket_id):
    rows = connection.execute(
        """SELECT fixture_id, sport, home, away, market, selection, decimal_odds
           FROM sports_ai_accumulator_legs
           WHERE ticket_id=?
           ORDER BY starts_at""",
        (ticket_id,),
    ).fetchall()
    selections = []
    for row in rows:
        item = dict(row)
        item['ticket_id'] = ticket_id
        selections.append(item)
    return selections


def next_ticket_id(connection, now):
    cutoff = (now - timedelta(minutes=REFRESH_MINUTES)).isoformat()
    row = connection.execute(
        """SELECT t.id
           FROM sports_ai_accumulator_tickets t
           WHERE t.status='qualified'
             AND NOT EXISTS (
               SELECT 1 FROM sports_ai_booking_codes b
               WHERE b.accumulator_ticket_id=t.id
                 AND b.verification_method='displayed'
                 AND b.verified_at IS NOT NULL
                 AND TRIM(COALESCE(b.code,''))<>''
             )
             AND NOT EXISTS (
               SELECT 1 FROM sports_ai_booking_jobs j
               WHERE j.signature LIKE t.id || '|%'
                 AND (j.status IN ('QUEUED','PREPARING','CODE_GENERATED') OR j.updated_at>?)
             )
           ORDER BY t.created_at DESC
           LIMIT 1""",
        (cutoff,),
    ).fetchone()
    return row['id'] if row else None


def _store_displayed_code(connection, ticket_id, code, evidence):
    moment = _now().isoformat()
    connection.execute(
        """INSERT INTO sports_ai_booking_codes
           (id,accumulator_ticket_id,code,verification_method,evidence,status,bookmaker,verified_at,integration_status,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (str(uuid.uuid4()), ticket_id, code, 'displayed', evidence, 'verified', 'SportyBet', moment, 'displayed', moment),
    )


async def prepare_slip(page, selections, book=True):
    """Click the displayed price for each selection. Book only when asked. Never place a wager."""
    await page.wait_for_selector('.match-row, article', timeout=20000)
    body = await page.locator('body').inner_text(timeout=8000)
    if blocked_page(body):
        raise RuntimeError('Page is blocked. No booking code was requested.')
    seen = []
    for index, item in enumerate(selections):
        marker = f'pick-{index}'
        hits = await page.evaluate(FIND_ODDS, {
            'home': item['home'], 'away': item['away'], 'odds': float(item['decimal_odds']), 'marker': marker,
        })
        if hits['exact'] == 1:
            seen.append({'fixture_id': item['fixture_id'], 'stored_odds': item['decimal_odds'], 'page_odds': float(item['decimal_odds'])})
            await page.locator(f'[data-sports-ai-pick="{marker}"]').evaluate('node => node.click()')
            await page.wait_for_timeout(400)
            continue
        if hits['exact'] == 0 and hits['nearby']:
            seen.append({'fixture_id': item['fixture_id'], 'stored_odds': item['decimal_odds'], 'page_odds': float(hits['nearby'][0]['text'])})
            return {'status': 'ODDS_CHANGED', 'seen': seen, 'code': None}
        raise RuntimeError(f"Selection control missing or ambiguous: {item['home']} vs {item['away']}")
    if not book:
        return {'status': 'SELECTED', 'seen': seen, 'code': None}
    return await request_displayed_code(page, selections, seen)


async def request_displayed_code(page, selections, seen):
    controls = await page.evaluate(FIND_BOOK)
    if controls != 1:
        raise RuntimeError('Booking-code control missing or ambiguous')
    await page.locator('[data-sports-ai-book="1"]').evaluate('node => node.click()')
    try:
        await page.wait_for_selector('.booking-code-share, [data-booking-code]', timeout=8000)
        await page.wait_for_timeout(800)
    except Exception:
        pass
    if await page.locator('.m-pop-main, .booking-code-share').count():
        names = [item['home'] for item in selections] + [item['away'] for item in selections]
        check = await page.evaluate(SLIP_CONTAINS, {'names': names})
        if not check['ok']:
            raise RuntimeError(f"The booking slip did not include every selected game. Slip: {check['text']}")
    codes = await page.evaluate(READ_CODES)
    if len(codes) != 1 or not CODE.fullmatch(codes[0]):
        raise RuntimeError('The page did not show one booking code')
    return {'status': 'CODE_GENERATED', 'seen': seen, 'code': codes[0]}


async def _open_sport(page, sport, page_url=None, reuse=False):
    labels = {'football': 'Football', 'basketball': 'Basketball'}
    if reuse:
        clicked = await page.evaluate(
            '''(label) => {
              const link = [...document.querySelectorAll('a')].find(node => (node.innerText || '').trim() === label && node.getBoundingClientRect().width > 0);
              if (!link) return false;
              link.click();
              return true;
            }''',
            labels.get(sport, ''),
        )
        if clicked:
            await page.wait_for_timeout(2500)
            await page.wait_for_selector('.match-row, article', timeout=20000)
            return
    url = page_url or dict(SECTIONS).get(sport)
    if not url:
        raise RuntimeError('No public page is configured for this sport')
    response = await page.goto(url, wait_until='domcontentloaded', timeout=45000)
    if response and response.status >= 400:
        raise RuntimeError(f'HTTP {response.status}')


async def execute_job(job_id, page_url=None):
    from playwright.async_api import async_playwright
    with connect() as connection:
        job = connection.execute('SELECT * FROM sports_ai_booking_jobs WHERE id=?', (job_id,)).fetchone()
        if not job:
            raise ValueError('Booking job not found')
        if job['status'] != 'QUEUED':
            return {'id': job_id, 'status': job['status'], 'places_wager': False}
        selections = json.loads(job['selections_json'])
        _set_job(connection, job_id, 'PREPARING')
    sports = []
    for item in selections:
        if item.get('sport') not in sports:
            sports.append(item.get('sport'))
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                context = await browser.new_context(viewport={'width': 1440, 'height': 900})
                page = await context.new_page()
                seen = []
                result = None
                for index, sport in enumerate(sports):
                    await _open_sport(page, sport, page_url if len(sports) == 1 else None, reuse=index > 0)
                    group = [item for item in selections if item.get('sport') == sport]
                    part = await prepare_slip(page, group, book=False)
                    if part['status'] != 'SELECTED':
                        result = part
                        break
                    seen.extend(part['seen'])
                if result is None:
                    result = await request_displayed_code(page, selections, seen)
                EVIDENCE.mkdir(parents=True, exist_ok=True)
                shot = EVIDENCE / f'{job_id}.png'
                await page.screenshot(path=str(shot), full_page=False)
                await context.close()
            finally:
                await browser.close()
        with connect() as connection:
            ticket_id = next((item.get('ticket_id') for item in selections if item.get('ticket_id')), None)
            if result['status'] == 'CODE_GENERATED':
                _set_job(connection, job_id, 'CODE_GENERATED', code=result['code'], screenshot=str(shot), odds_json=json.dumps(result['seen']))
                if ticket_id:
                    _store_displayed_code(connection, ticket_id, result['code'], str(shot))
            else:
                _set_job(connection, job_id, result['status'], odds_json=json.dumps(result['seen']), error='The displayed price no longer matches the qualified quote')
        result.update({'id': job_id, 'places_wager': False})
        return result
    except Exception as exc:
        with connect() as connection:
            _set_job(connection, job_id, 'FAILED', error=str(exc)[:500])
        return {'id': job_id, 'status': 'FAILED', 'error': str(exc)[:500], 'code': None, 'places_wager': False}


def process_booking_queue():
    """Request one prepared ticket's booking code. Does not open a browser when none qualify."""
    now = _now()
    with connect() as connection:
        if not _due(connection, now):
            return {'status': 'waiting', 'places_wager': False}
        queued = connection.execute(
            "SELECT id FROM sports_ai_booking_jobs WHERE status='QUEUED' ORDER BY created_at LIMIT 1",
        ).fetchone()
        if queued:
            job_id = queued['id']
        else:
            ticket_id = next_ticket_id(connection, now)
            qualified = qualified_selections(connection, now)
            if not ticket_id:
                return {'status': 'waiting', 'qualified': len(qualified), 'places_wager': False}
            selections = ticket_selections(connection, ticket_id)
            if not selections or len(selections) > 6:
                return {'status': 'waiting', 'qualified': len(qualified), 'places_wager': False}
            created = create_job(connection, selections)
            if created.get('duplicate'):
                return {'status': created['status'], 'duplicate': True, 'places_wager': False}
            job_id = created['id']
    import asyncio
    return asyncio.run(execute_job(job_id))


def book_prepared_tickets(limit=1):
    """Book the next prepared tickets whose prices are still on the page."""
    import asyncio
    results = []
    for _ in range(max(1, limit)):
        with connect() as connection:
            ticket_id = next_ticket_id(connection, _now())
            if not ticket_id:
                break
            selections = ticket_selections(connection, ticket_id)
            if not selections or len(selections) > 6:
                break
            created = create_job(connection, selections)
            if created.get('duplicate'):
                results.append(created)
                continue
            job_id = created['id']
        result = asyncio.run(execute_job(job_id))
        results.append(result)
        if result.get('status') == 'CODE_GENERATED':
            break
    return results


def build_booking_router(auth):
    from fastapi import APIRouter, Depends
    router = APIRouter(prefix='/api/sports-ai', tags=['Sports AI booking'])

    @router.get('/bookings')
    def bookings(user=Depends(auth)):
        with connect() as connection:
            jobs = list_jobs(connection)
        return {
            'items': jobs,
            'places_wager': False,
            'qualified_required': True,
            'message': 'A code is stored only when SportyBet displays it. No wager is placed.',
        }

    @router.post('/bookings/run')
    def run_booking(user=Depends(auth)):
        return process_booking_queue()

    return router
