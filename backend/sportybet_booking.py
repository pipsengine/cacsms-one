"""Prepare a SportyBet booking code with real clicks. Never place a paid wager."""

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from booking_schedule import describe, phase_at, publication_open
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

INSPECT = '''(marker) => {
  const node = document.querySelector(marker);
  if (!node) return null;
  const name = (node.getAttribute('aria-label') || node.innerText || '').replace(/\\s+/g, ' ').trim();
  return {
    name,
    role: node.getAttribute('role') || node.tagName.toLowerCase(),
    disabled: !!(node.disabled || node.getAttribute('aria-disabled') === 'true'),
  };
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
    from suitability import sync_lifecycles
    sync_lifecycles(connection)
    moment = now or _now()
    fresh_after = (moment - timedelta(minutes=REFRESH_MINUTES)).isoformat()
    rows = connection.execute(
        """SELECT o.id AS opportunity_id, f.id AS fixture_id, f.sport, f.competition, f.home, f.away, f.starts_at,
                  p.market, p.probability, o.expected_edge AS edge, q.decimal_odds, q.outcome, q.observed_at
           FROM sports_ai_value_opportunities o
           JOIN sports_ai_engine_predictions p ON p.id=o.prediction_id
           JOIN sports_ai_fixtures f ON f.id=p.fixture_id
           JOIN sports_ai_model_registry m ON m.id=p.model_id
           JOIN sports_ai_odds_snapshots q ON q.id=o.odds_id
           WHERE o.status='qualified' AND o.expected_edge>0 AND m.status='active' AND m.lifecycle='active'
             AND q.bookmaker='SportyBet' AND q.observed_at>=? AND f.status='scheduled' AND f.starts_at>?
             AND EXISTS (
               SELECT 1 FROM sports_ai_qualification_decisions d
               WHERE d.opportunity_id=o.id AND d.state='QUALIFIED'
                 AND EXISTS (SELECT 1 FROM json_each(d.products_json) WHERE value='booking')
             )
           ORDER BY f.starts_at""",
        (fresh_after, moment.isoformat()),
    ).fetchall()
    selections = []
    for row in rows:
        item = dict(row)
        item['selection'] = item['home'] if item['market'] in ('HOME', 'MONEYLINE_HOME') else item['away'] if item['market'] in ('AWAY', 'MONEYLINE_AWAY') else item['outcome']
        selections.append(item)
    return selections


def create_job(connection, selections):
    from qualification_engine import require_bookable
    require_bookable(connection, selections)
    if not selections or len(selections) > 25:
        raise ValueError('A booking needs 1 to 25 qualified selections')
    sports = {item['sport'] for item in selections}
    if not sports:
        raise ValueError('A booking needs a sport on every selection')
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
        if key not in ('code', 'screenshot', 'error', 'odds_json', 'reason_code'):
            raise ValueError('invalid booking field')
        columns.append(f'{key}=?')
        values.append(value)
    values.append(job_id)
    connection.execute(f"UPDATE sports_ai_booking_jobs SET {','.join(columns)} WHERE id=?", values)


def _log_booking(connection, job_id, phase, message, level='info', ticket_id=None, session_id=None, detail=None):
    connection.execute(
        """INSERT INTO sports_ai_booking_logs
           (id,job_id,ticket_id,session_id,phase,level,message,detail_json,created_at)
           VALUES(?,?,?,?,?,?,?,?,?)""",
        (
            str(uuid.uuid4()), job_id, ticket_id, session_id, phase, level, message[:500],
            json.dumps(detail or {}), _now().isoformat(),
        ),
    )


def _open_session(connection, job_id):
    session_id = str(uuid.uuid4())
    moment = _now().isoformat()
    connection.execute(
        """INSERT INTO sports_ai_sportybet_sessions
           (id,bookmaker,mode,state,last_operation,started_at,heartbeat_at,actor)
           VALUES(?,'SportyBet','supervised','PREPARING','booking-code',?,?,?)""",
        (session_id, moment, moment, 'sports-ai-worker'),
    )
    connection.execute(
        'UPDATE sports_ai_booking_jobs SET session_id=? WHERE id=?',
        (session_id, job_id),
    )
    return session_id


def _close_session(connection, session_id, state, detail):
    if not session_id:
        return
    moment = _now().isoformat()
    connection.execute(
        """UPDATE sports_ai_sportybet_sessions
           SET state=?, last_operation=?, error=?, heartbeat_at=?, ended_at=?
           WHERE id=?""",
        (state, 'booking-code', None if state == 'CODE_GENERATED' else detail[:500], moment, moment, session_id),
    )
    connection.execute(
        """INSERT INTO sports_ai_sportybet_events
           (id,session_id,state,operation,detail,created_at)
           VALUES(?,?,?,?,?,?)""",
        (str(uuid.uuid4()), session_id, state, 'booking-code', detail[:1000], moment),
    )


def expire_displayed_codes(connection, now=None):
    """A displayed code expires at the earliest stored kickoff. No other expiry is invented."""
    moment = (now or _now()).isoformat()
    changed = connection.execute(
        """UPDATE sports_ai_booking_codes
           SET status='expired'
           WHERE status='verified' AND expires_at IS NOT NULL AND expires_at<=?""",
        (moment,),
    ).rowcount
    return changed


def can_retry(status, attempt, max_attempts, error):
    if status not in ('FAILED', 'ODDS_CHANGED'):
        return False
    if int(attempt or 0) >= int(max_attempts or 3):
        return False
    folded = (error or '').casefold()
    return not any(marker in folded for marker in ('blocked', 'captcha', 'access denied', 'verify you are human'))


def recent_logs(connection, limit=30):
    return [dict(row) for row in connection.execute(
        """SELECT id,job_id,ticket_id,session_id,phase,level,message,created_at
           FROM sports_ai_booking_logs ORDER BY created_at DESC LIMIT ?""",
        (limit,),
    )]


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


def _ticket_on_job(connection, job_id):
    row = connection.execute('SELECT selections_json FROM sports_ai_booking_jobs WHERE id=?', (job_id,)).fetchone()
    if not row:
        return None
    try:
        selections = json.loads(row['selections_json'] or '[]')
    except json.JSONDecodeError:
        return None
    return next((item.get('ticket_id') for item in selections if item.get('ticket_id')), None)


def select_booking(connection, now):
    """Choose the ticket that is qualified now.

    A failed job for a different ticket does not take the publication slot, and it does not
    impose a wait on the current ticket. The same ticket is not repeated inside 15 minutes.
    """
    phase = phase_at(now)
    open_now = publication_open(phase)
    queued = connection.execute(
        "SELECT id FROM sports_ai_booking_jobs WHERE status='QUEUED' ORDER BY created_at LIMIT 1",
    ).fetchone()
    ticket_id = next_ticket_id(connection, now) if open_now else None
    if queued:
        queued_ticket = _ticket_on_job(connection, queued['id'])
        if not open_now or not ticket_id or queued_ticket == ticket_id:
            return {
                'action': 'queued', 'job_id': queued['id'], 'phase': phase,
                'open': open_now, 'ticket_id': queued_ticket,
            }
    if not open_now:
        return {'action': 'closed', 'phase': phase, 'open': False, 'ticket_id': None}
    if not ticket_id:
        return {'action': 'waiting', 'phase': phase, 'open': True, 'ticket_id': None}
    return {'action': 'ticket', 'phase': phase, 'open': True, 'ticket_id': ticket_id}


def ticket_selections(connection, ticket_id):
    rows = connection.execute(
        """SELECT fixture_id, sport, competition, home, away, market, selection, decimal_odds
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
             AND COALESCE(json_extract(t.parameters_json, '$.tier_mode'), 'standard')!='simulation'
             AND NOT EXISTS (
               SELECT 1 FROM sports_ai_accumulator_legs l
               WHERE l.ticket_id=t.id AND l.decimal_odds<1.3
             )
             AND NOT EXISTS (
               SELECT 1 FROM sports_ai_accumulator_legs l
               WHERE l.ticket_id=t.id
                 AND NOT EXISTS (
                   SELECT 1 FROM sports_ai_qualification_decisions d
                   WHERE d.fixture_id=l.fixture_id AND upper(d.market)=upper(l.market) AND d.state='QUALIFIED'
                     AND EXISTS (SELECT 1 FROM json_each(d.products_json) WHERE value='booking')
                 )
             )
           ORDER BY t.created_at DESC
           LIMIT 1""",
        (cutoff,),
    ).fetchone()
    return row['id'] if row else None


def _store_displayed_code(connection, ticket_id, code, evidence):
    from qualification_engine import ticket_is_bookable
    if not ticket_is_bookable(connection, ticket_id):
        raise ValueError('A displayed code is stored only when every leg has a current QUALIFIED decision')
    moment = _now().isoformat()
    expiry = connection.execute(
        'SELECT MIN(starts_at) FROM sports_ai_accumulator_legs WHERE ticket_id=? AND starts_at IS NOT NULL',
        (ticket_id,),
    ).fetchone()
    connection.execute(
        """INSERT INTO sports_ai_booking_codes
           (id,accumulator_ticket_id,code,verification_method,evidence,status,bookmaker,expires_at,verified_at,integration_status,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (
            str(uuid.uuid4()), ticket_id, code, 'displayed', evidence, 'verified', 'SportyBet',
            expiry[0] if expiry else None, moment, 'displayed', moment,
        ),
    )


FOCUS_MARKET = '''(family) => {
  const labels = family === 'handicap'
    ? ['2 Way Handicap', 'Handicap', 'Asian Handicap', 'Spread']
    : ['O/U', 'Over/Under', 'Goals Over/Under', '2 Way & O/U'];
  const nodes = [...document.querySelectorAll('button, a, span, div, li')];
  for (const label of labels) {
    const node = nodes.find(item => {
      const text = (item.innerText || '').trim();
      const box = item.getBoundingClientRect();
      return text === label && box.width > 20 && box.width < 220 && box.height > 8 && box.top > 60 && box.top < 520;
    });
    if (node) {
      node.click();
      return label;
    }
  }
  return '';
}'''

FIND_LINE = '''(args) => {
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
  const line = String(args.line || '');
  document.querySelectorAll('[data-sports-ai-pick]').forEach(node => node.removeAttribute('data-sports-ai-pick'));
  const rows = [...document.querySelectorAll('.match-row')].filter(candidate => {
    const blob = fold(candidate.innerText || '');
    return wanted.every(token => blob.includes(token)) && other.every(token => blob.includes(token));
  }).sort((left, right) => (left.innerText || '').length - (right.innerText || '').length);
  const row = rows.find(candidate => candidate.querySelector('.af-select, .m-outcome')) || rows[0];
  if (!row) return {exact: 0, nearby: [], line_visible: null};
  const blob = row.innerText || '';
  const lineVisible = !line || blob.includes(line);
  const prices = [...row.querySelectorAll('.m-outcome, button, a, [role="button"]')].filter(node => {
    if (node.classList.contains('m-outcome--disabled') || node.classList.contains('m-outcome-odds')) return false;
    if (node.closest('.specifiers-select, .af-select')) return false;
    const text = (node.innerText || '').trim();
    if (!/^\\d+(?:\\.\\d+)?$/.test(text)) return false;
    const bare = line.replace(/^-/, '');
    return text !== line && text !== bare;
  });
  const nearby = prices.slice(0, 8).map(node => ({text: Number(node.innerText).toFixed(2)}));
  if (!lineVisible) return {exact: 0, nearby, line_visible: false};
  const total = args.side === 'over' || args.side === 'under';
  const index = !total
    ? (args.side === 'away' ? 1 : 0)
    : prices.length >= 5
      ? (args.side === 'over' ? prices.length - 2 : prices.length - 1)
      : prices.length >= 4
        ? (args.side === 'over' ? 2 : 3)
        : (args.side === 'over' ? 0 : 1);
  const chosen = prices[index];
  if (!chosen) return {exact: 0, nearby, line_visible: true};
  const shown = Number(chosen.innerText).toFixed(2);
  if (shown !== price) return {exact: 0, nearby: [{text: shown}], line_visible: true};
  chosen.setAttribute('data-sports-ai-pick', args.marker);
  return {exact: 1, nearby, line_visible: true};
}'''


def _market_family(market):
    name = (market or '').upper()
    if 'SPREAD' in name or 'HANDICAP' in name:
        return 'handicap'
    if name.startswith(('OVER_', 'UNDER_')) or '_OVER_' in name or '_UNDER_' in name:
        return 'total'
    return 'result'


def _market_line(market):
    if _market_family(market) == 'result':
        return ''
    match = re.search(r'-?\d+(?:\.\d+)?$', market or '')
    return match.group(0) if match else ''


def _market_side(market):
    name = (market or '').upper()
    if name.startswith('UNDER') or '_UNDER_' in name:
        return 'under'
    if name.startswith('OVER') or '_OVER_' in name:
        return 'over'
    if 'AWAY' in name:
        return 'away'
    if name == 'DRAW':
        return 'draw'
    return 'home'


REVEAL_FIXTURE = '''(args) => {
  const fold = value => (value || '').toLowerCase().replace(/[^a-z0-9.]+/g, ' ');
  const skip = new Set(['fc','sc','sv','ac','cf','afc','cfc','vfb','vfl','tsg','bsc','fk','sk']);
  const tokens = value => {
    const all = fold(value).split(' ').map(token => token.replace(/\\./g, '')).filter(Boolean);
    const kept = all.filter(token => !skip.has(token) && !/^\\d+$/.test(token));
    return kept.length ? kept : all;
  };
  const wanted = tokens(args.home);
  const other = tokens(args.away);
  const shown = [...document.querySelectorAll('.match-row, article, tr')].some(row => {
    const blob = fold(row.innerText || '');
    return wanted.every(token => blob.includes(token)) && other.every(token => blob.includes(token));
  });
  if (shown) return {visible: true, clicked: ''};
  const rows = [...document.querySelectorAll('li.category-list-item, li.tournament-list-item')];
  const compTokens = fold(args.competition).split(' ').filter(word => word.length > 2 && !/^\\d+$/.test(word));
  const labelOf = item => {
    if (item.classList.contains('tournament-list-item')) {
      const named = item.querySelector(':scope > .tournament-name') || item.querySelector('.tournament-name');
      return fold((named && named.textContent) || '').trim();
    }
    const span = item.querySelector(':scope > .category-item span');
    return fold((span && span.textContent) || '').trim();
  };
  document.querySelectorAll('[data-sports-ai-league]').forEach(node => node.removeAttribute('data-sports-ai-league'));
  let best = null;
  let bestRank = 0;
  for (const item of rows) {
    const box = item.getBoundingClientRect();
    if (box.left < 0 || box.left > 420 || box.width < 36 || box.width > 320 || box.height < 8 || box.height > 48) continue;
    const text = labelOf(item);
    const label = text.split(' ').filter(word => word && !/^\\d+$/.test(word));
    if (!label.length) continue;
    if (!compTokens.includes('srl') && label.includes('srl')) continue;
    if (!compTokens.includes('simulated') && label.includes('simulated')) continue;
    const alias = {english: 'england', dutch: 'netherlands', french: 'france', german: 'germany'};
    const same = (token, word) => token === word || alias[token] === word || alias[word] === token;
    const hits = compTokens.filter(token => label.some(word => same(token, word))).length;
    const extras = label.filter(word => !compTokens.some(token => same(token, word))).length;
    const rank = hits * 20 - extras * 25 + (item.classList.contains('tournament-list-item') ? 6 : 0) + (box.top > 0 && box.top < 900 ? 1 : 0);
    if (hits > 0 && rank > bestRank) {
      best = item;
      bestRank = rank;
    }
  }
  if (best) {
    best.setAttribute('data-sports-ai-league', '1');
    return {visible: false, clicked: labelOf(best).slice(0, 40)};
  }
  const search = document.querySelector('input[placeholder*="Search" i], input[type="search"]');
  if (search) return {visible: false, clicked: 'search'};
  return {visible: false, clicked: ''};
}'''


async def _reveal_fixture(page, item):
    """Open the league row, then the tournament row, until this match is on the page."""
    payload = {
        'home': item.get('home') or '',
        'away': item.get('away') or '',
        'competition': item.get('competition') or '',
    }
    previous = ''
    result = {}
    for _ in range(3):
        result = await page.evaluate(REVEAL_FIXTURE, payload)
        if result.get('visible') or result.get('clicked') == 'search' or not result.get('clicked'):
            break
        if result.get('clicked') == previous:
            break
        previous = result.get('clicked')
        await page.locator('[data-sports-ai-league="1"]').click()
        await page.wait_for_timeout(1500)
        result = await page.evaluate(REVEAL_FIXTURE, payload)
        if result.get('visible'):
            return result
    if result.get('clicked') == 'search':
        box = page.locator('input[placeholder*="Search" i], input[type="search"]').first
        await box.fill(item.get('home') or '')
        await page.wait_for_timeout(1500)
    return result


SWITCH_LINE = '''(args) => {
  const fold = value => (value || '').toLowerCase().replace(/[^a-z0-9.]+/g, ' ');
  const tokens = value => fold(value).split(' ').filter(token => token && !/^\\d+$/.test(token));
  const wanted = tokens(args.home);
  const other = tokens(args.away);
  const rows = [...document.querySelectorAll('.match-row')].filter(candidate => {
    const blob = fold(candidate.innerText || '');
    return wanted.every(token => blob.includes(token)) && other.every(token => blob.includes(token));
  }).sort((left, right) => (left.innerText || '').length - (right.innerText || '').length);
  const row = rows.find(candidate => {
    const control = candidate.querySelector('.af-select, .specifiers-select');
    return control && control.getBoundingClientRect().width > 8;
  }) || rows[0];
  if (!row) return '';
  row.scrollIntoView({block: 'center', inline: 'nearest'});
  document.querySelectorAll('[data-sports-ai-line]').forEach(node => node.removeAttribute('data-sports-ai-line'));
  const line = String(args.line || '');
  const control = row.querySelector('.af-select-title') || row.querySelector('.af-select, .specifiers-select');
  if (!control || control.getBoundingClientRect().width < 8) return '';
  const current = ((control.querySelector('.af-select-input') || control).innerText || '').trim();
  if (current === line) return 'shown';
  control.setAttribute('data-sports-ai-line', '1');
  return 'open';
}'''

PICK_LINE = '''(line) => {
  document.querySelectorAll('[data-sports-ai-line-option]').forEach(node => node.removeAttribute('data-sports-ai-line-option'));
  const lists = [...document.querySelectorAll('.af-select-list')].filter(list => {
    const box = list.getBoundingClientRect();
    return box.height > 8 && getComputedStyle(list).display !== 'none';
  });
  const pool = lists.length ? lists.flatMap(list => [...list.querySelectorAll('.af-select-item, span, div')]) : [...document.querySelectorAll('.af-select-item')];
  const node = pool.find(item => {
    const text = (item.innerText || '').trim();
    const box = item.getBoundingClientRect();
    return text === line && box.width > 4 && box.height > 4;
  });
  if (!node) return false;
  node.setAttribute('data-sports-ai-line-option', '1');
  return true;
}'''


async def _switch_line(page, item):
    """Open the line control and choose the quoted line. The price is clicked only when it matches."""
    line = _market_line(item.get('market'))
    if not line:
        return False
    state = await page.evaluate(SWITCH_LINE, {
        'home': item.get('home') or '', 'away': item.get('away') or '', 'line': line,
    })
    if state != 'open':
        return state == 'shown'
    control = page.locator('[data-sports-ai-line="1"]')
    await control.scroll_into_view_if_needed()
    await control.click(force=True)
    for _ in range(4):
        await page.wait_for_timeout(500)
        if await page.evaluate(PICK_LINE, line):
            await page.locator('[data-sports-ai-line-option="1"]').click()
            await page.wait_for_timeout(500)
            return True
    return False


async def _focus_market(page, market):
    family = _market_family(market)
    if family == 'result':
        return ''
    label = await page.evaluate(FOCUS_MARKET, family)
    if label:
        await page.wait_for_timeout(800)
    return label


async def prepare_slip(page, selections, book=True):
    """Click the displayed price for each selection. Book only when asked. Never place a wager."""
    await page.wait_for_selector('.match-row, article', timeout=20000)
    body = await page.locator('body').inner_text(timeout=8000)
    if blocked_page(body):
        raise RuntimeError('Page is blocked. No booking code was requested.')
    seen = []
    for index, item in enumerate(selections):
        marker = f'pick-{index}'
        async def find_price():
            family = _market_family(item.get('market'))
            if family != 'result':
                await _focus_market(page, item.get('market'))
                return await page.evaluate(FIND_LINE, {
                    'home': item['home'], 'away': item['away'], 'odds': float(item['decimal_odds']),
                    'line': _market_line(item.get('market')), 'side': _market_side(item.get('market')), 'marker': marker,
                })
            return await page.evaluate(FIND_ODDS, {
                'home': item['home'], 'away': item['away'], 'odds': float(item['decimal_odds']), 'marker': marker,
            })
        hits = await find_price()
        revealed = {}
        if hits.get('exact') != 1:
            revealed = await _reveal_fixture(page, item)
            hits = await find_price()
        if hits.get('exact') != 1 and hits.get('line_visible') is False and _market_line(item.get('market')):
            await _switch_line(page, item)
            hits = await find_price()
        if hits['exact'] == 1:
            control = await page.evaluate(INSPECT, f'[data-sports-ai-pick="{marker}"]')
            name = (control or {}).get('name') or ''
            if not control or control.get('disabled') or re.search(r'place\s*bet|bet\s*now|\bpay\b|deposit|confirm\s*bet', name, re.I):
                raise RuntimeError(f"The price control is not an allowed selection: {item['home']} vs {item['away']}")
            seen.append({
                'fixture_id': item['fixture_id'], 'stored_odds': item['decimal_odds'],
                'page_odds': float(item['decimal_odds']), 'control': control,
            })
            await page.locator(f'[data-sports-ai-pick="{marker}"]').click()
            await page.wait_for_timeout(400)
            continue
        if hits['exact'] == 0 and hits.get('line_visible') is False:
            seen.append({'fixture_id': item['fixture_id'], 'stored_odds': item['decimal_odds'], 'page_odds': None})
            return {
                'status': 'ODDS_CHANGED', 'seen': seen, 'code': None,
                'error': 'The quoted line is not the line shown for this match.',
            }
        if hits['exact'] == 0 and hits['nearby']:
            seen.append({'fixture_id': item['fixture_id'], 'stored_odds': item['decimal_odds'], 'page_odds': float(hits['nearby'][0]['text'])})
            return {'status': 'ODDS_CHANGED', 'seen': seen, 'code': None}
        opened = revealed.get('clicked') or 'none'
        raise RuntimeError(f"Selection control missing or ambiguous: {item['home']} vs {item['away']} (league control: {opened})")
    if not book:
        return {'status': 'SELECTED', 'seen': seen, 'code': None}
    return await request_displayed_code(page, selections, seen)


async def request_displayed_code(page, selections, seen):
    controls = await page.evaluate(FIND_BOOK)
    if controls != 1:
        raise RuntimeError('Booking-code control missing or ambiguous')
    control = await page.evaluate(INSPECT, '[data-sports-ai-book="1"]')
    name = (control or {}).get('name') or ''
    if not control or control.get('disabled') or not BOOK.search(name) or re.search(r'place\s*bet|bet\s*now|\bpay\b|deposit|confirm\s*bet', name, re.I):
        raise RuntimeError('The booking-code control is not the allowed share control')
    await page.locator('[data-sports-ai-book="1"]').click()
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
    session_id = None
    with connect() as connection:
        job = connection.execute('SELECT * FROM sports_ai_booking_jobs WHERE id=?', (job_id,)).fetchone()
        if not job:
            raise ValueError('Booking job not found')
        if job['status'] != 'QUEUED':
            return {'id': job_id, 'status': job['status'], 'places_wager': False}
        attempt = int(job['attempt'] or 0) + 1
        if attempt > int(job['max_attempts'] or 3):
            return {'id': job_id, 'status': 'FAILED', 'error': 'Retry limit reached', 'places_wager': False}
        selections = json.loads(job['selections_json'])
        session_id = _open_session(connection, job_id)
        connection.execute(
            """UPDATE sports_ai_booking_jobs
               SET status='PREPARING', attempt=?, session_id=?, updated_at=? WHERE id=?""",
            (attempt, session_id, _now().isoformat(), job_id),
        )
        ticket_id = next((item.get('ticket_id') for item in selections if item.get('ticket_id')), None)
        _log_booking(connection, job_id, 'prepare', 'Opening the public SportyBet page', ticket_id=ticket_id, session_id=session_id, detail={'attempt': attempt})
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
            except Exception:
                EVIDENCE.mkdir(parents=True, exist_ok=True)
                try:
                    await page.screenshot(path=str(EVIDENCE / f'{job_id}.png'), full_page=False)
                except Exception:
                    pass
                raise
            finally:
                await browser.close()
        with connect() as connection:
            ticket_id = next((item.get('ticket_id') for item in selections if item.get('ticket_id')), None)
            if result['status'] == 'CODE_GENERATED':
                _set_job(connection, job_id, 'CODE_GENERATED', code=result['code'], screenshot=str(shot), odds_json=json.dumps(result['seen']), reason_code='CODE_VERIFIED')
                if ticket_id:
                    _store_displayed_code(connection, ticket_id, result['code'], str(shot))
                    _record_diagnostic(connection, ticket_id, 'CODE_VERIFIED', 'SportyBet displayed one booking code and it matched the expected format.', _now())
                _log_booking(connection, job_id, 'published', 'SportyBet displayed a booking code', ticket_id=ticket_id, session_id=session_id, detail={'evidence': str(shot)})
                _close_session(connection, session_id, 'CODE_GENERATED', 'Displayed booking code stored')
            else:
                reason = classify_booking_failure('', result['status'])
                _set_job(connection, job_id, result['status'], odds_json=json.dumps(result['seen']), error=result.get('error') or 'The displayed price no longer matches the qualified quote', reason_code=reason)
                if ticket_id:
                    _record_diagnostic(connection, ticket_id, reason, 'The displayed price no longer matches the qualified quote.', _now())
                _log_booking(connection, job_id, 'odds', 'The displayed price no longer matches the qualified quote', level='warning', ticket_id=ticket_id, session_id=session_id)
                _close_session(connection, session_id, result['status'], 'Displayed price changed')
        result.update({'id': job_id, 'places_wager': False})
        return result
    except Exception as exc:
        message = str(exc)[:500]
        reason = classify_booking_failure(message)
        with connect() as connection:
            _set_job(connection, job_id, 'FAILED', error=message, reason_code=reason)
            if ticket_id:
                _record_diagnostic(connection, ticket_id, reason, message, _now())
            _log_booking(connection, job_id, 'failed', message, level='error', session_id=session_id)
            _close_session(connection, session_id, 'FAILED', message)
        return {'id': job_id, 'status': 'FAILED', 'error': message, 'code': None, 'places_wager': False}


def classify_booking_failure(message, status=None):
    """Map a bookmaker outcome to a diagnostic code. This does not invent a code."""
    text = (message or '').casefold()
    if status == 'CODE_GENERATED':
        return 'CODE_VERIFIED'
    if status == 'ODDS_CHANGED' or 'no longer matches' in text:
        return 'ODDS_CHANGED'
    if any(marker in text for marker in ('challenge', 'captcha', 'verify you are human', 'blocked')):
        return 'ACCESS_CHALLENGE'
    if 'session' in text:
        return 'SESSION_EXPIRED'
    if 'did not show one booking code' in text or 'booking-code control' in text:
        return 'CODE_NOT_RETURNED'
    if 'booking slip did not include' in text or 'not a booking code' in text:
        return 'CODE_VERIFICATION_FAILED'
    if 'http ' in text or text.startswith('http'):
        return 'BOOKMAKER_UNAVAILABLE'
    if 'missing' in text or 'ambiguous' in text:
        return 'FIXTURE_NOT_FOUND'
    return 'BOOKMAKER_UNAVAILABLE'


def _record_diagnostic(connection, ticket_id, reason, detail, now):
    previous = connection.execute(
        """SELECT reason_code, created_at FROM sports_ai_booking_diagnostics
           WHERE ticket_id IS ? ORDER BY created_at DESC LIMIT 1""",
        (ticket_id,),
    ).fetchone()
    if previous and previous['reason_code'] == reason:
        try:
            seen = datetime.fromisoformat(str(previous['created_at']).replace('Z', '+00:00'))
            if now - seen < timedelta(minutes=15):
                return False
        except ValueError:
            pass
    connection.execute(
        """INSERT INTO sports_ai_booking_diagnostics(id,ticket_id,reason_code,detail,created_at)
           VALUES(?,?,?,?,?)""",
        (str(uuid.uuid4()), ticket_id, reason, detail[:500], now.isoformat()),
    )
    return True


def _latest_leg_quote(connection, leg):
    """The newest SportyBet price for this selection. A different total or spread line is not this price."""
    line = leg.get('quote_line')
    params = [
        leg['fixture_id'], leg.get('quote_market') or leg['market'],
        leg.get('quote_outcome') or leg['selection'], leg['market'],
    ]
    line_sql = ''
    if line is not None and line != '':
        line_sql = ' AND line IS NOT NULL AND abs(line - ?) < 0.001'
        params.append(float(line))
    return connection.execute(
        f"""SELECT decimal_odds, observed_at FROM sports_ai_odds_snapshots
            WHERE fixture_id=? AND bookmaker='SportyBet'
              AND (
                (market=? AND lower(outcome)=lower(?))
                OR upper(market)=upper(?)
              ){line_sql}
            ORDER BY observed_at DESC LIMIT 1""",
        params,
    ).fetchone()


def examine_ready_tickets(connection, now=None, window_open=None):
    """Revalidate tickets before a booking window. A closed window does not open a browser."""
    current = now or _now()
    phase = phase_at(current)
    if window_open is None:
        window_open = publication_open(phase)
    minutes = REFRESH_MINUTES
    qualified = connection.execute(
        """SELECT id, status, combined_odds, publication_state, parameters_json
           FROM sports_ai_accumulator_tickets
           WHERE status='qualified'
             AND COALESCE(json_extract(parameters_json, '$.tier_mode'), 'standard')!='simulation'""",
    ).fetchall()
    held = connection.execute(
        """SELECT id, status, combined_odds, publication_state, parameters_json
           FROM sports_ai_accumulator_tickets
           WHERE status='review_required'
             AND COALESCE(json_extract(parameters_json, '$.tier_mode'), 'standard')!='simulation'
             AND EXISTS (
               SELECT 1 FROM sports_ai_accumulator_legs l
               WHERE l.ticket_id=sports_ai_accumulator_tickets.id AND l.starts_at>?
             )
           ORDER BY COALESCE(joint_probability, -1) DESC
           LIMIT 80""",
        (current.isoformat(),),
    ).fetchall()
    tickets = list(qualified) + list(held)
    summary = {'examined': 0, 'ready': 0, 'demoted': 0, 'restored': 0, 'reasons': {}, 'opens_browser': False, 'places_wager': False, 'phase': phase}
    _record_recent_lapses(connection, current)
    for ticket in tickets:
        summary['examined'] += 1
        reason, detail, next_status = _ticket_preflight(connection, ticket, current, window_open, minutes)
        summary['reasons'][reason] = summary['reasons'].get(reason, 0) + 1
        if reason in ('READY', 'WINDOW_CLOSED'):
            if ticket['status'] != 'qualified':
                connection.execute(
                    """UPDATE sports_ai_accumulator_tickets
                       SET status='qualified', publication_state='READY_FOR_WINDOW' WHERE id=?""",
                    (ticket['id'],),
                )
                connection.execute(
                    """INSERT INTO sports_ai_ticket_monitoring_events
                       (id,ticket_id,event_type,details_json,created_at) VALUES(?,?,?,?,?)""",
                    (str(uuid.uuid4()), ticket['id'], 'booking_preflight', json.dumps({'reason_code': 'RESTORED', 'detail': detail}), current.isoformat()),
                )
                summary['restored'] += 1
            elif reason != 'READY':
                _record_diagnostic(connection, ticket['id'], reason, detail, current)
            summary['ready'] += 1
            continue
        if ticket['status'] == 'qualified':
            _record_diagnostic(connection, ticket['id'], reason, detail, current)
        if next_status and next_status != ticket['status']:
            publication = 'EXPIRED' if next_status == 'expired' else None
            connection.execute(
                'UPDATE sports_ai_accumulator_tickets SET status=?, publication_state=? WHERE id=?',
                (next_status, publication, ticket['id']),
            )
            connection.execute(
                """INSERT INTO sports_ai_ticket_monitoring_events
                   (id,ticket_id,event_type,details_json,created_at) VALUES(?,?,?,?,?)""",
                (str(uuid.uuid4()), ticket['id'], 'booking_preflight', json.dumps({'reason_code': reason, 'detail': detail}), current.isoformat()),
            )
            summary['demoted'] += 1
    return summary


def _record_recent_lapses(connection, now):
    """Give a reason to tickets the monitor already removed in this session."""
    cutoff = (now - timedelta(hours=6)).isoformat()
    rows = connection.execute(
        """SELECT t.id FROM sports_ai_accumulator_tickets t
           WHERE t.status='review_required'
             AND NOT EXISTS (SELECT 1 FROM sports_ai_booking_diagnostics d WHERE d.ticket_id=t.id)
             AND EXISTS (
               SELECT 1 FROM sports_ai_ticket_monitoring_events e
               WHERE e.ticket_id=t.id AND e.event_type='qualification_lapsed' AND e.created_at>=?
             )""",
        (cutoff,),
    ).fetchall()
    for row in rows:
        _record_diagnostic(connection, row['id'], 'TICKET_NOT_QUALIFIED', 'A required selection is no longer QUALIFIED.', now)


def _ticket_preflight(connection, ticket, now, window_open, minutes):
    legs = [dict(row) for row in connection.execute(
        'SELECT * FROM sports_ai_accumulator_legs WHERE ticket_id=?', (ticket['id'],),
    )]
    if not legs:
        return 'TICKET_NOT_QUALIFIED', 'The ticket has no stored selections.', 'review_required'
    from qualification_engine import selection_is_qualified
    for leg in legs:
        fixture = connection.execute(
            'SELECT status, starts_at FROM sports_ai_fixtures WHERE id=?', (leg['fixture_id'],),
        ).fetchone()
        if not fixture:
            return 'FIXTURE_NOT_FOUND', f"{leg['home']} vs {leg['away']} is not in the fixture store.", 'review_required'
        kickoff = _parse_booking_time(leg['starts_at'] or fixture['starts_at'])
        if fixture['status'] not in ('scheduled', 'not_started') or (kickoff and kickoff <= now):
            return 'FIXTURE_NOT_FOUND', f"{leg['home']} vs {leg['away']} is no longer a scheduled pre-match fixture.", 'expired'
        if not selection_is_qualified(connection, leg['fixture_id'], leg['market']):
            decision = connection.execute(
                """SELECT reason_code FROM sports_ai_qualification_decisions
                   WHERE fixture_id=? AND upper(market)=upper(?)
                   ORDER BY updated_at DESC LIMIT 1""",
                (leg['fixture_id'], leg['market']),
            ).fetchone()
            if decision and decision['reason_code'] == 'RISK_REVIEW':
                return 'TICKET_NOT_QUALIFIED', 'This selection is held for risk review and cannot be booked.', 'review_required'
            return 'TICKET_NOT_QUALIFIED', f"{leg['market']} no longer has a current QUALIFIED decision.", 'review_required'
        if (leg.get('bookmaker') or '') != 'SportyBet':
            return 'BOOKMAKER_UNAVAILABLE', 'The stored price is not a SportyBet quote.', 'review_required'
        quote = _latest_leg_quote(connection, leg)
        observed = _parse_booking_time(quote['observed_at'] if quote else leg.get('odds_observed_at'))
        if not observed or (now - observed).total_seconds() / 60 > minutes:
            return 'ODDS_STALE', 'The SportyBet price is older than the 15-minute freshness window.', 'review_required'
        if quote and abs(float(quote['decimal_odds']) - float(leg['decimal_odds'])) / float(leg['decimal_odds']) > 0.02:
            return 'ODDS_CHANGED', 'The current SportyBet price no longer matches the ticket.', 'review_required'
    if not window_open:
        return 'WINDOW_CLOSED', 'The Africa/Lagos publication window is closed. The ticket stays ready and no browser is opened.', None
    return 'READY', 'The ticket is still qualified with a fresh SportyBet price.', None


def _parse_booking_time(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def process_booking_queue(now=None):
    """Request one prepared ticket's booking code inside a WAT publication window.

    Does not open a browser when none qualify, when the window is closed, or when the retry limit is reached.
    """
    moment = now or _now()
    phase = phase_at(moment)
    with connect() as connection:
        examined = examine_ready_tickets(connection, moment)
        expired = expire_displayed_codes(connection, moment)
        qualified = qualified_selections(connection, moment)
        choice = select_booking(connection, moment)
        if choice['action'] == 'closed':
            return {
                'status': 'outside_window' if choice.get('job_id') or qualified else 'waiting',
                'phase': phase,
                'qualified': len(qualified),
                'expired': expired,
                'examined': examined,
                'places_wager': False,
            }
        if choice['action'] == 'waiting':
            return {
                'status': 'waiting', 'phase': phase, 'qualified': len(qualified),
                'expired': expired, 'examined': examined, 'places_wager': False,
                'reason': 'No current qualified ticket is ready for a code.',
            }
        if choice['action'] == 'queued' and not choice['open']:
            return {
                'status': 'outside_window', 'phase': phase, 'qualified': len(qualified),
                'expired': expired, 'examined': examined, 'places_wager': False,
            }
        if choice['action'] == 'queued':
            job_id = choice['job_id']
        else:
            ticket_id = choice['ticket_id']
            selections = ticket_selections(connection, ticket_id)
            if not selections or len(selections) > 25:
                return {'status': 'waiting', 'phase': phase, 'qualified': len(qualified), 'expired': expired, 'examined': examined, 'places_wager': False}
            created = create_job(connection, selections)
            if created.get('duplicate'):
                return {'status': created['status'], 'phase': phase, 'duplicate': True, 'examined': examined, 'places_wager': False}
            job_id = created['id']
            _log_booking(connection, job_id, phase, 'Queued a qualified ticket for a displayed booking code', ticket_id=selections[0].get('ticket_id'))
    import asyncio
    result = asyncio.run(execute_job(job_id))
    result['phase'] = phase
    result['expired'] = expired
    result['examined'] = examined
    return result


def confirm_ticket(ticket_id, now=None):
    """Request the displayed code for one ticket the operator named.

    A closed publication window does not block this request. Qualification,
    the 15-minute price, and the price on the page still do. No wager is placed.
    """
    moment = now or _now()
    from market_registry import link_stored_quotes
    from qualification_engine import revalidate
    with connect() as connection:
        link_stored_quotes(connection, moment, limit=8000)
        revalidate(connection, 'sports-ai-worker', moment, limit=2000)
        ticket = connection.execute(
            'SELECT id, status FROM sports_ai_accumulator_tickets WHERE id=?',
            (ticket_id,),
        ).fetchone()
        if not ticket:
            return {'status': 'waiting', 'places_wager': False, 'opens_browser': False, 'reason': 'Ticket is not stored.', 'reason_code': 'TICKET_NOT_QUALIFIED'}
        reason, detail, _next = _ticket_preflight(connection, ticket, moment, True, REFRESH_MINUTES)
        if reason != 'READY':
            return {
                'status': 'waiting', 'places_wager': False, 'opens_browser': False,
                'reason': detail, 'reason_code': reason,
            }
        if ticket['status'] != 'qualified':
            connection.execute(
                "UPDATE sports_ai_accumulator_tickets SET status='qualified', publication_state=NULL WHERE id=?",
                (ticket_id,),
            )
        created = create_job(connection, ticket_selections(connection, ticket_id))
        if created.get('duplicate'):
            return {'status': created['status'], 'duplicate': True, 'places_wager': False, 'opens_browser': False}
        job_id = created['id']
        _log_booking(
            connection, job_id, 'confirmation',
            'Operator asked for this ticket booking code. No wager is placed.',
            ticket_id=ticket_id,
        )
    import asyncio
    result = asyncio.run(execute_job(job_id))
    result['confirmation'] = True
    result['places_wager'] = False
    return result


def confirm_missed_publication(now=None):
    """Request one code that a closed publication window skipped.

    Qualification, freshness, and the bookmaker page stay in force. This does not
    change the daily windows and does not place a wager.
    """
    moment = now or _now()
    with connect() as connection:
        examined = examine_ready_tickets(connection, moment, window_open=True)
        ticket_id = next_ticket_id(connection, moment)
        if not ticket_id:
            return {
                'status': 'waiting', 'confirmation': True, 'examined': examined,
                'places_wager': False, 'opens_browser': False,
            }
        selections = ticket_selections(connection, ticket_id)
        if not selections or len(selections) > 25:
            return {
                'status': 'waiting', 'confirmation': True, 'examined': examined,
                'places_wager': False, 'opens_browser': False,
            }
        created = create_job(connection, selections)
        if created.get('duplicate'):
            return {
                'status': created['status'], 'confirmation': True, 'duplicate': True,
                'places_wager': False, 'opens_browser': False,
            }
        job_id = created['id']
        _log_booking(
            connection, job_id, 'confirmation',
            'Operator confirmation of the missed 12:00–13:00 publication. No wager is placed.',
            ticket_id=ticket_id,
        )
    import asyncio
    result = asyncio.run(execute_job(job_id))
    result['confirmation'] = True
    result['places_wager'] = False
    result['examined'] = examined
    return result


def book_prepared_tickets(limit=1, now=None):
    """Book the next prepared tickets whose prices are still on the page."""
    moment = now or _now()
    phase = phase_at(moment)
    if not publication_open(phase):
        return [{'status': 'outside_window', 'phase': phase, 'places_wager': False}]
    import asyncio
    results = []
    for _ in range(max(1, limit)):
        with connect() as connection:
            ticket_id = next_ticket_id(connection, _now())
            if not ticket_id:
                break
            selections = ticket_selections(connection, ticket_id)
            if not selections or len(selections) > 25:
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


def diagnose_booking(connection, now=None):
    """Trace the booking path without opening a browser or storing a code."""
    moment = now or _now()
    phase = phase_at(moment)
    schedule = describe(moment)
    qualified = qualified_selections(connection, moment)
    ticket_id = next_ticket_id(connection, moment)
    decisions = connection.execute(
        """SELECT d.id, f.home, f.away, d.market, d.state, o.status AS opportunity_status,
                  q.bookmaker, q.observed_at
           FROM sports_ai_qualification_decisions d
           JOIN sports_ai_fixtures f ON f.id=d.fixture_id
           LEFT JOIN sports_ai_value_opportunities o ON o.id=d.opportunity_id
           LEFT JOIN sports_ai_odds_snapshots q ON q.id=o.odds_id
           WHERE d.state='QUALIFIED'""",
    ).fetchall()
    blockers = []
    if not publication_open(phase):
        blockers.append({'code': 'outside_publication_window', 'detail': schedule['note']})
    if not decisions:
        blockers.append({'code': 'no_qualified_decision', 'detail': 'No decision is currently QUALIFIED.'})
    if not qualified:
        blockers.append({'code': 'qualified_decision_has_no_fresh_bookable_price', 'detail': 'A QUALIFIED decision is not attached to a fresh SportyBet price on an active model.'})
    if not ticket_id:
        blockers.append({'code': 'no_currently_qualified_ticket', 'detail': 'Autonomous ticket building stores a ticket only when every leg is currently QUALIFIED. None is ready.'})
    rows = []
    for row in decisions:
        reason = 'ready_for_a_publication_window' if row['bookmaker'] == 'SportyBet' and row['opportunity_status'] == 'qualified' else 'linked_price_missing_or_not_qualified'
        rows.append({
            'fixture': f"{row['home']} vs {row['away']}",
            'market': row['market'],
            'opportunity_status': row['opportunity_status'],
            'bookmaker': row['bookmaker'],
            'odds_observed_at': row['observed_at'],
            'blocker': reason,
        })
    return {
        'places_wager': False,
        'publishes_code': False,
        'opens_browser': False,
        'phase': phase,
        'qualified_decisions': len(decisions),
        'bookable_selections': len(qualified),
        'ticket_id': ticket_id,
        'blockers': blockers,
        'decisions': rows,
    }


def build_booking_router(auth):
    from fastapi import APIRouter, Depends
    router = APIRouter(prefix='/api/sports-ai', tags=['Sports AI booking'])

    @router.get('/bookings')
    def bookings(user=Depends(auth)):
        with connect() as connection:
            jobs = list_jobs(connection)
            logs = recent_logs(connection)
        return {
            'items': jobs,
            'logs': logs,
            'schedule': describe(_now()),
            'places_wager': False,
            'qualified_required': True,
            'message': 'A code is stored only when SportyBet displays it during a WAT publication window. No wager is placed. Zero codes is a valid result.',
        }

    @router.post('/bookings/run')
    def run_booking(user=Depends(auth)):
        return process_booking_queue()

    return router
