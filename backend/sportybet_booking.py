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
  const wanted = fold(args.home).split(' ').filter(Boolean);
  const other = fold(args.away).split(' ').filter(Boolean);
  const price = Number(args.odds).toFixed(2);
  const exact = [];
  const nearby = [];
  for (const node of document.querySelectorAll('button, a, [role="button"]')) {
    const text = (node.innerText || '').trim();
    if (!/^\\d+(?:\\.\\d+)?$/.test(text)) continue;
    let parent = node;
    for (let depth = 0; depth < 8 && parent; depth += 1, parent = parent.parentElement) {
      const blob = fold(parent.innerText || '');
      const teams = wanted.every(token => blob.includes(token)) && other.every(token => blob.includes(token));
      if (!teams) continue;
      const box = node.getBoundingClientRect();
      if (box.width < 8 || box.height < 8) break;
      const hit = {x: box.x + box.width / 2, y: box.y + box.height / 2, text};
      nearby.push(hit);
      if (text === price || text === String(Number(price))) exact.push(hit);
      break;
    }
  }
  return {exact: exact.slice(0, 5), nearby: nearby.slice(0, 8)};
}'''

FIND_BOOK = '''() => {
  const deny = /place\\s*bet|bet\\s*now|\\bpay\\b|deposit|login|stake|confirm\\s*bet/i;
  const allow = /^(book(\\s*bet)?|booking\\s*code|share\\s*code)$/i;
  return [...document.querySelectorAll('button, a, [role="button"]')].map(node => {
    const text = (node.innerText || '').trim();
    const box = node.getBoundingClientRect();
    return {text, x: box.x + box.width / 2, y: box.y + box.height / 2, denied: deny.test(text), allowed: allow.test(text)};
  }).filter(item => item.allowed && !item.denied);
}'''

READ_CODES = '''() => {
  const pattern = /^[A-Z0-9]{5,16}$/;
  return [...document.querySelectorAll('[data-booking-code], .booking-code, input')].map(node => (node.value || node.innerText || '').trim()).filter(text => pattern.test(text));
}'''


def _now():
    return datetime.now(timezone.utc)


def _signature(selections):
    parts = sorted(f"{item['fixture_id']}|{item['market']}|{item['selection']}" for item in selections)
    return '|'.join(parts)


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
    if len(sports) != 1:
        raise ValueError('One booking uses one sport')
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
        (job_id, moment, moment, 'QUEUED', next(iter(sports)), json.dumps(selections), signature),
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


async def prepare_slip(page, selections):
    """Click the displayed price, then the booking-code control. Never a place-bet control."""
    body = await page.locator('body').inner_text(timeout=5000)
    if blocked_page(body):
        raise RuntimeError('Page is blocked. No booking code was requested.')
    seen = []
    for item in selections:
        hits = await page.evaluate(FIND_ODDS, {
            'home': item['home'], 'away': item['away'], 'odds': float(item['decimal_odds']),
        })
        exact = hits['exact']
        if len(exact) != 1:
            if hits['nearby']:
                seen.append({'fixture_id': item['fixture_id'], 'stored_odds': item['decimal_odds'], 'page_odds': float(hits['nearby'][0]['text'])})
                return {'status': 'ODDS_CHANGED', 'seen': seen, 'code': None}
            raise RuntimeError(f"Selection control missing or ambiguous: {item['home']} vs {item['away']}")
        current = float(exact[0]['text'])
        seen.append({'fixture_id': item['fixture_id'], 'stored_odds': item['decimal_odds'], 'page_odds': current})
        if abs(current - float(item['decimal_odds'])) > 0.001:
            return {'status': 'ODDS_CHANGED', 'seen': seen, 'code': None}
        await page.mouse.click(exact[0]['x'], exact[0]['y'])
    controls = await page.evaluate(FIND_BOOK)
    if len(controls) != 1:
        raise RuntimeError('Booking-code control missing or ambiguous')
    await page.mouse.click(controls[0]['x'], controls[0]['y'])
    await page.wait_for_timeout(500)
    codes = await page.evaluate(READ_CODES)
    if len(codes) != 1 or not CODE.fullmatch(codes[0]):
        raise RuntimeError('The page did not show one booking code')
    return {'status': 'CODE_GENERATED', 'seen': seen, 'code': codes[0]}


async def execute_job(job_id, page_url=None):
    from playwright.async_api import async_playwright
    with connect() as connection:
        job = connection.execute('SELECT * FROM sports_ai_booking_jobs WHERE id=?', (job_id,)).fetchone()
        if not job:
            raise ValueError('Booking job not found')
        if job['status'] != 'QUEUED':
            return {'id': job_id, 'status': job['status'], 'places_wager': False}
        selections = json.loads(job['selections_json'])
        sport = job['sport']
        _set_job(connection, job_id, 'PREPARING')
    url = page_url or dict(SECTIONS).get(sport)
    if not url:
        with connect() as connection:
            _set_job(connection, job_id, 'FAILED', error='No public page is configured for this sport')
        return {'id': job_id, 'status': 'FAILED', 'places_wager': False}
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                context = await browser.new_context(viewport={'width': 1440, 'height': 900})
                page = await context.new_page()
                response = await page.goto(url, wait_until='domcontentloaded', timeout=30000)
                if response and response.status >= 400:
                    raise RuntimeError(f'HTTP {response.status}')
                await page.wait_for_timeout(300)
                result = await prepare_slip(page, selections)
                EVIDENCE.mkdir(parents=True, exist_ok=True)
                shot = EVIDENCE / f'{job_id}.png'
                await page.screenshot(path=str(shot), full_page=True)
                await context.close()
            finally:
                await browser.close()
        with connect() as connection:
            if result['status'] == 'CODE_GENERATED':
                _set_job(connection, job_id, 'CODE_GENERATED', code=result['code'], screenshot=str(shot), odds_json=json.dumps(result['seen']))
            else:
                _set_job(connection, job_id, result['status'], odds_json=json.dumps(result['seen']), error='The displayed price no longer matches the qualified quote')
        result.update({'id': job_id, 'places_wager': False})
        return result
    except Exception as exc:
        with connect() as connection:
            _set_job(connection, job_id, 'FAILED', error=str(exc)[:500])
        return {'id': job_id, 'status': 'FAILED', 'error': str(exc)[:500], 'code': None, 'places_wager': False}


def process_booking_queue():
    """Queue at most one booking from qualified selections. Does not open a browser when none qualify."""
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
            selections = qualified_selections(connection, now)
            if not selections:
                return {'status': 'waiting', 'qualified': 0, 'places_wager': False}
            sport = selections[0]['sport']
            group = [item for item in selections if item['sport'] == sport][:6]
            created = create_job(connection, group)
            if created.get('duplicate'):
                return {'status': created['status'], 'duplicate': True, 'places_wager': False}
            job_id = created['id']
    import asyncio
    return asyncio.run(execute_job(job_id))


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
