"""Prepare a SportyBet booking code with real clicks. Never place a paid wager."""

import asyncio
import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from booking_schedule import describe, last_publication_at, phase_at, publication_due, publication_open
from db import connect as _db_connect


def connect(busy_timeout_ms=180000):
    """Booking writes wait up to 3 minutes for SQLite's write lock. A booking runs on its own
    thread after minutes of browser work, possibly after SportyBet has displayed a code; giving
    up after the default 20 s lost results and left jobs stuck in PREPARING."""
    return _db_connect(busy_timeout_ms)
from sportybet_feed import SECTIONS
from sportybet_research import blocked_page

BOOK = re.compile(r'^(book(\s*bet)?|booking\s*code|share\s*code)$', re.I)
CODE = re.compile(r'^[A-Z0-9]{5,16}$')
REFRESH_MINUTES = 15
# A selection SportyBet has not listed within this many hours is treated as not offered.
OFFER_HOURS = 24
# A ticket that failed this many times in a day is not retried until the next day.
MAX_DAILY_FAILURES = 3
# Tests redirect this (tests/conftest.py); a test page must never land in production evidence.
EVIDENCE = Path(os.environ.get('SPORTS_BOOKING_EVIDENCE_DIR') or Path(__file__).resolve().parent / 'booking_evidence')

FIND_ODDS = '''(args) => {
  const fold = value => (value || '').toLowerCase().normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').replace(/ä/g, 'a').replace(/ö/g, 'o').replace(/ü/g, 'u').replace(/ß/g, 'ss').replace(/ł/g, 'l').replace(/[^a-z0-9.]+/g, ' ');
  const skip = new Set(['fc','sc','sv','ac','cf','afc','cfc','vfb','vfl','tsg','bsc','fk','sk','dsc','estac','rcd','cb','bc','kk','bk']);
  const near = (token, word) => {
    if (token === word) return true;
    if (token.length < 6 || word.length < 6 || Math.abs(token.length - word.length) > 2) return false;
    const rows = [Array.from({length: word.length + 1}, (_, index) => index)];
    for (let i = 1; i <= token.length; i += 1) {
      const next = [i];
      for (let j = 1; j <= word.length; j += 1) next.push(Math.min(next[j - 1] + 1, rows[i - 1][j] + 1, rows[i - 1][j - 1] + (token[i - 1] === word[j - 1] ? 0 : 1)));
      rows.push(next);
    }
    return rows[token.length][word.length] <= 2;
  };
  const tokens = value => {
    const all = fold(value).split(' ').map(token => token.replace(/\\./g, '')).filter(Boolean);
    const kept = all.filter(token => !skip.has(token) && token.length > 1 && !/^\\d+$/.test(token));
    return kept.length ? kept : all;
  };
  const present = (blob, token) => blob.includes(token) || blob.split(' ').some(word => near(token, word.replace(/\\./g, '')));
  const wanted = tokens(args.home);
  const other = tokens(args.away);
  const normalize = value => fold(value).split(' ').filter(Boolean);
  const selection = normalize(args.selection);
  const side = args.side || 'home';
  const sideAliases = {
    home: ['home', '1'],
    draw: ['draw', 'x'],
    away: ['away', '2'],
  }[side] || [];
  const outcomes = [];
  document.querySelectorAll('[data-sports-ai-pick]').forEach(node => node.removeAttribute('data-sports-ai-pick'));
  for (const row of document.querySelectorAll('.match-row, article')) {
    const blob = fold(row.innerText || '');
    if (!wanted.every(token => present(blob, token)) || !other.every(token => present(blob, token))) continue;
    const prices = [];
    for (const node of row.querySelectorAll('.m-outcome, button, a, [role="button"]')) {
      if (node.classList.contains('m-outcome--disabled') || node.classList.contains('m-outcome-odds')) continue;
      const text = (node.innerText || '').trim();
      if (!/^\\d+(?:\\.\\d+)?$/.test(text)) continue;
      const odds = Number(text);
      if (!Number.isFinite(odds) || odds <= 1) continue;
      prices.push({node, odds});
    }
    if (!prices.length) continue;
    const explicit = prices.some(({node}) => node.hasAttribute('data-outcome') || node.hasAttribute('data-selection'));
    // 2 Way & O/U shows winner prices first, then a total line and over/under.
    if (args.two_way && !explicit && prices.length !== 2 && prices.length !== 5) continue;
    let matches = [];
    if (explicit) {
      matches = prices.filter(({node}) => {
        const label = normalize(node.getAttribute('data-outcome') || node.getAttribute('data-selection') || '');
        return sideAliases.some(alias => label.includes(alias)) ||
          (selection.length > 0 && selection.every(token => label.includes(token)));
      });
    } else if (args.two_way) {
      const index = side === 'away' ? 1 : side === 'home' ? 0 : -1;
      if (index >= 0 && index < prices.length) matches = [prices[index]];
    } else {
      const index = side === 'draw'
        ? (prices.length >= 3 ? 1 : -1)
        : side === 'away'
          ? (prices.length >= 3 ? 2 : prices.length === 2 ? 1 : -1)
          : side === 'home' ? 0 : -1;
      if (index >= 0 && index < prices.length) matches = [prices[index]];
    }
    outcomes.push(...matches);
  }
  if (outcomes.length === 1) {
    outcomes[0].node.setAttribute('data-sports-ai-pick', args.marker);
    return {matched: 1, shown: outcomes[0].odds.toFixed(2)};
  }
  return {matched: 0, nearby: []};
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
  const skip = new Set(['fc','sc','sv','ac','cf','afc','cfc','vfb','vfl','tsg','bsc','fk','sk','dsc','estac','rcd']);
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


def create_job(connection, selections, high_odds=False):
    from booking_policy import load
    from qualification_engine import require_bookable
    policy = load(connection)
    from qualification_engine import require_current_qualification
    product = _ticket_product(connection, selections[0]['ticket_id']) if selections and selections[0].get('ticket_id') else None
    # Low-odds jackpots use SportyBet's own price, not the (quarantined) basketball model.
    guarded = [] if product in ('jackpot_low', 'prize_ladder') else (
        selections if high_odds else [item for item in selections if item.get('sport') == 'basketball'])
    if guarded:
        require_current_qualification(connection, guarded, _now())
    if not high_odds and policy['require_live_qualification']:
        require_bookable(connection, selections)
    # Hard gate for every product; booking_policy relaxations cannot switch it off.
    # The one exception is the labelled no-edge favourites product (favourites.py): it is
    # positive-EV by design never, so it passes its own structural gate instead.
    if product == 'favourites' and not high_odds:
        from favourites import require_bookable as require_favourite
        integrity = require_favourite(connection, selections, _now())
    elif product in ('jackpot_low', 'prize_ladder') and not high_odds:
        from jackpot_low import require_bookable as require_jackpot
        integrity = require_jackpot(connection, selections, _now())
    else:
        from prediction_integrity import require_bookable as require_integrity
        integrity = require_integrity(connection, selections, _now())
    limit = 50 if product in ('jackpot_low', 'prize_ladder') else 30 if high_odds else 25
    if not selections or len(selections) > limit:
        raise ValueError(f'A booking needs 1 to {limit} selections')
    sports = {item['sport'] for item in selections}
    if not sports:
        raise ValueError('A booking needs a sport on every selection')
    signature = _signature(selections)
    ticket_id = selections[0].get('ticket_id')
    if ticket_id and policy['enforce_match_exposure']:
        from ticket_diversity import assert_available
        ticket = connection.execute('SELECT family_id,status,parameters_json FROM sports_ai_accumulator_tickets WHERE id=?', (ticket_id,)).fetchone()
        if ticket and json.loads(ticket['parameters_json'] or '{}').get('diversity_retired'):
            raise ValueError('This ticket was retired because a match is already allocated to another ticket.')
        assert_available(connection, selections, ignore_family=ticket['family_id'] if ticket else None)
    if ticket_id:
        linked = connection.execute(
            """SELECT id, status FROM sports_ai_booking_jobs
               WHERE ticket_id=? AND status IN ('QUEUED','PREPARING','CODE_GENERATED')
               ORDER BY created_at DESC LIMIT 1""",
            (ticket_id,),
        ).fetchone()
        if linked:
            return {'id': linked['id'], 'status': linked['status'], 'duplicate': True, 'places_wager': False}
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
           (id,created_at,updated_at,status,sport,selections_json,signature,places_wager,ticket_id)
           VALUES(?,?,?,?,?,?,?,0,?)""",
        (
            job_id, moment, moment, 'QUEUED',
            next(iter(sports)) if len(sports) == 1 else 'mixed',
            json.dumps([{**item, **integrity['legs'].get((item.get('fixture_id'), str(item.get('market') or '').upper()), {})}
                        for item in selections]), signature, ticket_id,
        ),
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


def _publication_anchor(connection):
    """The latest code request that still occupies the 15-minute testing slot.

    A line mismatch does not occupy the slot. The same ticket still waits 15 minutes
    before it is sent again.
    """
    return last_publication_at(connection)


def _booking_due(connection, moment):
    from booking_policy import load
    gap = timedelta(minutes=float(load(connection)['request_gap_minutes']))
    return publication_due(moment, _publication_anchor(connection), every=gap)


def select_booking(connection, now, ignore_gap=False):
    """Choose the ticket that is qualified now.

    During the testing period a code may be requested every 15 minutes. A failed job
    for a different ticket does not take that slot. The same ticket is not repeated
    inside 15 minutes.
    """
    phase = phase_at(now)
    open_now = ignore_gap or _booking_due(connection, now)
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
        """SELECT fixture_id, sport, competition, home, away, starts_at, market, selection, decimal_odds
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


def _learn_alias(connection, sport, canonical, shown, observation_id):
    if not canonical or not shown or canonical == shown:
        return
    connection.execute(
        """INSERT INTO sports_ai_bookmaker_team_names
           (id,sport,canonical_name,bookmaker,bookmaker_name,source,evidence_json,created_at)
           VALUES(?,?,?,'SportyBet',?,'observation',?,?)
           ON CONFLICT(sport,canonical_name,bookmaker) DO UPDATE SET bookmaker_name=excluded.bookmaker_name""",
        (str(uuid.uuid4()), sport, canonical, shown, json.dumps({'observation_id': observation_id}), _now().isoformat()),
    )


def _alias(connection, sport, name):
    row = connection.execute(
        """SELECT bookmaker_name FROM sports_ai_bookmaker_team_names
           WHERE sport=? AND canonical_name=? AND bookmaker='SportyBet'""", (sport, name),
    ).fetchone()
    return row['bookmaker_name'] if row else None


def page_identity(connection, selections):
    """Use the names SportyBet itself displays for each fixture.

    Legs carry names from the fixture store ("Hertha BSC", "2. Fussball-Bundesliga"),
    while the SportyBet page shows its own ("Hertha", "Germany 2. Bundesliga").
    The original names are kept under fixture_* keys for audit.
    """
    resolved = []
    for item in selections:
        item = dict(item)
        if item.get('page_identity'):
            resolved.append(item)
            continue
        observation = connection.execute(
            """SELECT id,home,away,competition,event_id FROM sports_ai_sportybet_observations
               WHERE fixture_id=? ORDER BY observed_at DESC LIMIT 1""", (item.get('fixture_id'),),
        ).fetchone()
        sport = item.get('sport')
        home, away = item.get('home') or '', item.get('away') or ''
        if observation:
            shown_home, shown_away = observation['home'], observation['away']
            competition = observation['competition'] or item.get('competition')
            _learn_alias(connection, sport, home, shown_home, observation['id'])
            _learn_alias(connection, sport, away, shown_away, observation['id'])
            item['sportybet_event_id'] = observation['event_id']
            source = 'observation'
        else:
            shown_home = _alias(connection, sport, home) or home
            shown_away = _alias(connection, sport, away) or away
            competition = item.get('competition')
            source = 'alias' if (shown_home, shown_away) != (home, away) else 'fixture'
        item.update(
            fixture_home=home, fixture_away=away, fixture_competition=item.get('competition'),
            home=shown_home, away=shown_away, competition=competition, page_identity=source,
        )
        if item.get('selection') == home:
            item['selection'] = shown_home
        elif item.get('selection') == away:
            item['selection'] = shown_away
        resolved.append(item)
    return resolved


def next_ticket_id(connection, now):
    """The next stored ticket to book, soonest first kickoff first.

    What else is required depends on the booking policy (booking_policy.py).
    """
    from booking_policy import load
    policy = load(connection)
    cutoff = (now - timedelta(minutes=REFRESH_MINUTES)).isoformat()
    clauses, args = [], [cutoff, (now - timedelta(days=1)).isoformat(), MAX_DAILY_FAILURES]
    if policy['require_live_qualification']:
        clauses.append("""AND NOT EXISTS (
               SELECT 1 FROM sports_ai_accumulator_legs l
               WHERE l.ticket_id=t.id
                 AND NOT EXISTS (
                   SELECT 1 FROM sports_ai_qualification_decisions d
                   WHERE d.fixture_id=l.fixture_id AND upper(d.market)=upper(l.market) AND d.state='QUALIFIED'
                     AND EXISTS (SELECT 1 FROM json_each(d.products_json) WHERE value='booking')
                 )
             )""")
    clauses.append("""AND NOT EXISTS (
               SELECT 1 FROM sports_ai_accumulator_legs l
               WHERE l.ticket_id=t.id AND l.decimal_odds<?
             )""")
    args.append(float(policy['min_leg_odds']))
    # Enough legs must still be ahead of kickoff to make a ticket.
    if policy['drop_unavailable_legs']:
        clauses.append("""AND (SELECT COUNT(*) FROM sports_ai_accumulator_legs l
               WHERE l.ticket_id=t.id AND julianday(l.starts_at)>julianday(?))
             >= MAX(1, MIN(?, (SELECT COUNT(*) FROM sports_ai_accumulator_legs l WHERE l.ticket_id=t.id)))""")
        args.extend([now.isoformat(), int(policy['min_legs'])])
    row = connection.execute(
        f"""SELECT t.id
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
             AND t.probability_basis='independent' AND t.joint_probability>0
             AND (SELECT COUNT(*) FROM sports_ai_booking_jobs j WHERE j.ticket_id=t.id
                  AND j.status IN ('FAILED','ODDS_CHANGED') AND j.updated_at>?) < ?
             {' '.join(clauses)}
           ORDER BY (SELECT MIN(starts_at) FROM sports_ai_accumulator_legs l WHERE l.ticket_id=t.id), t.created_at DESC
           LIMIT 1""",
        args,
    ).fetchone()
    return row['id'] if row else None


def _high_odds_ticket(connection, ticket_id):
    row = connection.execute(
        'SELECT parameters_json FROM sports_ai_accumulator_tickets WHERE id=?',
        (ticket_id,),
    ).fetchone()
    if not row:
        return False
    try:
        parameters = json.loads(row['parameters_json'] or '{}')
    except json.JSONDecodeError:
        return False
    if parameters.get('product') != 'high_odds':
        return False
    legs = connection.execute(
        'SELECT sport, market, decimal_odds, probability, bookmaker FROM sports_ai_accumulator_legs WHERE ticket_id=?',
        (ticket_id,),
    ).fetchall()
    from market_registry import holdout_ready
    prices = []
    for leg in legs:
        if not holdout_ready(connection, leg['sport'], leg['market']):
            return False
        try:
            odds = float(leg['decimal_odds'])
            probability = float(leg['probability'])
        except (TypeError, ValueError):
            return False
        if probability < 0.80 or leg['bookmaker'] != 'SportyBet':
            return False
        prices.append(odds)
    count = len(prices)
    if all(2 <= odds < 3 for odds in prices):
        return count == 12
    if all(3 <= odds < 4 for odds in prices):
        return count == 10
    if all(4 <= odds <= 5 for odds in prices):
        return count == 8
    product = 1.0
    for odds in prices:
        product *= odds
    if all(1 < odds <= 1.95 for odds in prices):
        return 15 <= count <= 30
    if all(odds > 5 for odds in prices):
        return 2 <= count <= 8 and product > 10000
    return False


def _apply_displayed_prices(connection, ticket_id, seen):
    """Keep the ticket on the price SportyBet displayed. A code is not a price."""
    product = 1.0
    for item in seen:
        try:
            odds = float(item.get('page_odds'))
        except (TypeError, ValueError):
            continue
        if not item.get('fixture_id') or not item.get('market') or odds <= 1:
            continue
        # One leg per fixture and market. The selection text may be SportyBet's name.
        connection.execute(
            """UPDATE sports_ai_accumulator_legs SET decimal_odds=?
               WHERE ticket_id=? AND fixture_id=? AND upper(market)=upper(?)""",
            (odds, ticket_id, item['fixture_id'], item['market']),
        )
    rows = connection.execute(
        'SELECT decimal_odds FROM sports_ai_accumulator_legs WHERE ticket_id=?',
        (ticket_id,),
    ).fetchall()
    for row in rows:
        product *= float(row['decimal_odds'])
    connection.execute(
        'UPDATE sports_ai_accumulator_tickets SET combined_odds=? WHERE id=?',
        (round(product, 2), ticket_id),
    )


def _sure_odds_ticket(connection, ticket_id):
    """A sure-odds ticket is at least two analyzed selections, combined from 2.00 to 2.95."""
    row = connection.execute(
        'SELECT parameters_json FROM sports_ai_accumulator_tickets WHERE id=?',
        (ticket_id,),
    ).fetchone()
    if not row:
        return False
    try:
        parameters = json.loads(row['parameters_json'] or '{}')
    except json.JSONDecodeError:
        return False
    if parameters.get('product') != 'sure_odds':
        return False
    legs = connection.execute(
        'SELECT sport, market, decimal_odds, probability, bookmaker FROM sports_ai_accumulator_legs WHERE ticket_id=?',
        (ticket_id,),
    ).fetchall()
    if len(legs) < 2:
        return False
    from market_registry import holdout_ready
    product = 1.0
    for leg in legs:
        if not holdout_ready(connection, leg['sport'], leg['market']):
            return False
        try:
            odds = float(leg['decimal_odds'])
            probability = float(leg['probability'])
        except (TypeError, ValueError):
            return False
        if probability < 0.80 or leg['bookmaker'] != 'SportyBet' or not (1 < odds <= 2.95):
            return False
        product *= odds
    return 2 <= product <= 2.95


def create_high_odds_job(connection, ticket_id):
    """Queue a qualified high-odds ticket. No wager is placed."""
    if not _high_odds_ticket(connection, ticket_id):
        raise ValueError('A high-odds booking needs selections at least 80% in one price band')
    selections = ticket_selections(connection, ticket_id)
    for item in selections:
        item['ticket_id'] = ticket_id
    return create_job(connection, selections, high_odds=True)


def create_sure_odds_job(connection, ticket_id):
    """Queue one sure-odds ticket. No operator selection and no wager."""
    if not _sure_odds_ticket(connection, ticket_id):
        raise ValueError('A sure-odds booking needs at least two selections of 80% or more, combined from 2.00 to 2.95')
    selections = ticket_selections(connection, ticket_id)
    for item in selections:
        item['ticket_id'] = ticket_id
    return create_job(connection, selections, high_odds=True)


def _selection_limit(connection, ticket_id):
    """Selections a stored ticket may carry: SportyBet takes 50; long products use that room."""
    return 50 if _ticket_product(connection, ticket_id) in ('jackpot_low', 'prize_ladder') else 25


def _ticket_product(connection, ticket_id):
    row = connection.execute(
        'SELECT parameters_json FROM sports_ai_accumulator_tickets WHERE id=?',
        (ticket_id,),
    ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row['parameters_json'] or '{}').get('product')
    except json.JSONDecodeError:
        return None


def _store_displayed_code(connection, ticket_id, code, evidence):
    from qualification_engine import require_current_qualification
    selections = ticket_selections(connection, ticket_id)
    stored_product = _ticket_product(connection, ticket_id)
    guarded = [] if stored_product in ('jackpot_low', 'prize_ladder') else selections if stored_product in ('high_odds', 'sure_odds') else [
        item for item in selections if item.get('sport') == 'basketball'
    ]
    if guarded:
        require_current_qualification(connection, guarded, _now())
    existing = connection.execute(
        "SELECT accumulator_ticket_id FROM sports_ai_booking_codes WHERE code=? AND bookmaker='SportyBet'",
        (code,),
    ).fetchall()
    others = [row['accumulator_ticket_id'] for row in existing if row['accumulator_ticket_id'] != ticket_id]
    if others:
        def selections(identifier):
            return {(r['fixture_id'], str(r['market']).upper()) for r in connection.execute(
                'SELECT fixture_id, market FROM sports_ai_accumulator_legs WHERE ticket_id=?', (identifier,))}
        mine = selections(ticket_id)
        twin = next((other for other in others if selections(other) == mine), None)
        if not twin:
            raise ValueError('SportyBet returned a code already registered to another ticket')
        # Same selections, same slip: this ticket duplicates one that is already coded. Retire it.
        row = connection.execute('SELECT parameters_json FROM sports_ai_accumulator_tickets WHERE id=?', (ticket_id,)).fetchone()
        parameters = json.loads((row['parameters_json'] if row else None) or '{}')
        parameters['duplicate_of'] = twin
        connection.execute(
            """UPDATE sports_ai_accumulator_tickets SET status='expired', publication_state='DUPLICATE', parameters_json=?
               WHERE id=?""", (json.dumps(parameters), ticket_id),
        )
        return
    if existing:
        return
    from booking_policy import load
    strict = load(connection)['require_live_qualification']
    from qualification_engine import ticket_is_bookable
    optimized = connection.execute('SELECT 1 FROM sports_ai_high_odds_tickets WHERE ticket_id=?',(ticket_id,)).fetchone()
    if strict and optimized:
        from high_odds_service import validate_snapshot
        if validate_snapshot(connection,ticket_id):
            raise ValueError('Finalized high-odds ticket no longer passes registration checks')
    product = _ticket_product(connection, ticket_id)
    # Sure/high-odds tickets are gated when the job is queued. Page prices can
    # move the combined total outside the build band after SportyBet shows the
    # code; that must not discard a real displayed code from Active Codes.
    queued_product = product in ('sure_odds', 'high_odds')
    if (
        strict
        and not optimized
        and not queued_product
        and not ticket_is_bookable(connection, ticket_id)
        and not _high_odds_ticket(connection, ticket_id)
        and not _sure_odds_ticket(connection, ticket_id)
    ):
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


FOCUS_MARKET = '''(args) => {
  const family = args.family || '';
  const labels = family === 'handicap'
    ? ['2 Way Handicap', 'Handicap', 'Asian Handicap', 'Spread']
    : family === 'winner'
      ? ['2 Way & O/U', '2 Way', 'Winner (incl. overtime)', 'Winner', 'Match Winner', 'Moneyline']
    : family === 'double'
      ? ['Double Chance']
      : ['O/U', 'Over/Under', 'Goals Over/Under', '2 Way & O/U', '3 Way & O/U'];
  const fold = value => (value || '').toLowerCase().normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').replace(/ä/g, 'a').replace(/ö/g, 'o').replace(/ü/g, 'u').replace(/ß/g, 'ss').replace(/ł/g, 'l').replace(/[^a-z0-9.]+/g, ' ');
  const skip = new Set(['fc','sc','sv','ac','cf','afc','cfc','vfb','vfl','tsg','bsc','fk','sk','dsc','estac','rcd','cb','bc','kk','bk']);
  const near = (token, word) => {
    if (token === word) return true;
    if (token.length < 6 || word.length < 6 || Math.abs(token.length - word.length) > 2) return false;
    const rows = [Array.from({length: word.length + 1}, (_, index) => index)];
    for (let i = 1; i <= token.length; i += 1) {
      const next = [i];
      for (let j = 1; j <= word.length; j += 1) next.push(Math.min(next[j - 1] + 1, rows[i - 1][j] + 1, rows[i - 1][j - 1] + (token[i - 1] === word[j - 1] ? 0 : 1)));
      rows.push(next);
    }
    return rows[token.length][word.length] <= 2;
  };
  const tokens = value => {
    const all = fold(value).split(' ').map(token => token.replace(/\\./g, '')).filter(Boolean);
    const kept = all.filter(token => !skip.has(token) && !/^\\d+$/.test(token));
    return kept.length ? kept : all;
  };
  const present = (blob, token) => blob.includes(token) || blob.split(' ').some(word => near(token, word.replace(/\\./g, '')));
  let scope = document;
  if (args.home) {
    const wanted = tokens(args.home);
    const other = tokens(args.away);
    const row = [...document.querySelectorAll('.match-row')].find(candidate => {
      const blob = fold(candidate.innerText || '');
      return wanted.every(token => present(blob, token)) && other.every(token => present(blob, token));
    });
    if (row) {
      row.scrollIntoView({block: 'center', inline: 'nearest'});
      let section = row.parentElement;
      while (section && !section.querySelector('.market-item')) section = section.parentElement;
      if (section) scope = section;
    }
  }
  const nodes = [...scope.querySelectorAll('.market-item, button, a, span, div, li')];
  for (const label of labels) {
    const node = nodes.find(item => {
      const text = (item.innerText || '').trim();
      const box = item.getBoundingClientRect();
      return text === label && box.width > 20 && box.height > 8;
    });
    if (node) {
      node.click();
      return label;
    }
  }
  return '';
}'''

FIND_LINE = '''(args) => {
  const fold = value => (value || '').toLowerCase().normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').replace(/ä/g, 'a').replace(/ö/g, 'o').replace(/ü/g, 'u').replace(/ß/g, 'ss').replace(/ł/g, 'l').replace(/[^a-z0-9.]+/g, ' ');
  const skip = new Set(['fc','sc','sv','ac','cf','afc','cfc','vfb','vfl','tsg','bsc','fk','sk','dsc','estac','rcd','cb','bc','kk','bk']);
  const near = (token, word) => {
    if (token === word) return true;
    if (token.length < 6 || word.length < 6 || Math.abs(token.length - word.length) > 2) return false;
    const rows = [Array.from({length: word.length + 1}, (_, index) => index)];
    for (let i = 1; i <= token.length; i += 1) {
      const next = [i];
      for (let j = 1; j <= word.length; j += 1) next.push(Math.min(next[j - 1] + 1, rows[i - 1][j] + 1, rows[i - 1][j - 1] + (token[i - 1] === word[j - 1] ? 0 : 1)));
      rows.push(next);
    }
    return rows[token.length][word.length] <= 2;
  };
  const tokens = value => {
    const all = fold(value).split(' ').map(token => token.replace(/\\./g, '')).filter(Boolean);
    const kept = all.filter(token => !skip.has(token) && !/^\\d+$/.test(token));
    return kept.length ? kept : all;
  };
  const present = (blob, token) => blob.includes(token) || blob.split(' ').some(word => near(token, word.replace(/\\./g, '')));
  const wanted = tokens(args.home);
  const other = tokens(args.away);
  const price = Number(args.odds).toFixed(2);
  const numeric = value => {
    const match = String(value || '').replace(',', '.').match(/-?\\d+(?:\\.\\d+)?/);
    return match ? Number(match[0]) : null;
  };
  const line = String(args.line || '');
  document.querySelectorAll('[data-sports-ai-pick]').forEach(node => node.removeAttribute('data-sports-ai-pick'));
  const rows = [...document.querySelectorAll('.match-row')].filter(candidate => {
    const blob = fold(candidate.innerText || '');
    return wanted.every(token => present(blob, token)) && other.every(token => present(blob, token));
  }).sort((left, right) => (left.innerText || '').length - (right.innerText || '').length);
  const row = rows.find(candidate => candidate.querySelector('.af-select, .m-outcome')) || rows[0];
  if (!row) return {matched: 0, nearby: [], line_visible: null};
  const selectedLine = [...row.querySelectorAll('.af-select-input')].map(node => (node.innerText || '').trim()).find(Boolean) || '';
  const shownLine = numeric(selectedLine);
  const wantedLine = numeric(line);
  const lineVisible = !line || (shownLine != null && wantedLine != null && Math.abs(shownLine - wantedLine) < 0.001);
  const prices = [...row.querySelectorAll('.m-outcome, button, a, [role="button"]')].filter(node => {
    if (node.classList.contains('m-outcome--disabled') || node.classList.contains('m-outcome-odds')) return false;
    if (node.closest('.specifiers-select, .af-select')) return false;
    const text = (node.innerText || '').trim();
    if (!/^\\d+(?:\\.\\d+)?$/.test(text)) return false;
    const bare = line.replace(/^-/, '');
    return text !== line && text !== bare;
  });
  const nearby = prices.slice(0, 8).map(node => ({text: Number(node.innerText).toFixed(2)}));
  if (!lineVisible) return {matched: 0, nearby, line_visible: false};
  const total = args.side === 'over' || args.side === 'under';
  const doubleIndex = { '1x': 0, '12': 1, 'x2': 2 }[args.side];
  const index = doubleIndex !== undefined
    ? doubleIndex
    : !total
    ? (args.side === 'away' ? 1 : 0)
    : prices.length >= 5
      ? (args.side === 'over' ? prices.length - 2 : prices.length - 1)
      : prices.length >= 4
        ? (args.side === 'over' ? 2 : 3)
        : (args.side === 'over' ? 0 : 1);
  const chosen = prices[index];
  if (!chosen) return {matched: 0, nearby, line_visible: true};
  const shown = Number(chosen.innerText);
  if (!Number.isFinite(shown) || shown <= 1) {
    return {matched: 0, nearby: [{text: Number.isFinite(shown) ? shown.toFixed(2) : ''}], line_visible: true};
  }
  chosen.setAttribute('data-sports-ai-pick', args.marker);
  return {matched: 1, nearby, line_visible: true, shown: shown.toFixed(2)};
}'''


def _market_family(market):
    name = (market or '').upper()
    if name.startswith('DOUBLE_CHANCE'):
        return 'double'
    if 'SPREAD' in name or 'HANDICAP' in name:
        return 'handicap'
    if name.startswith(('OVER_', 'UNDER_')) or '_OVER_' in name or '_UNDER_' in name:
        return 'total'
    return 'result'


def _market_line(market):
    if _market_family(market) in ('result', 'double'):
        return ''
    match = re.search(r'-?\d+(?:\.\d+)?$', market or '')
    return match.group(0) if match else ''


def _market_side(market):
    name = (market or '').upper()
    if name.startswith('DOUBLE_CHANCE_'):
        return name.split('_')[-1].lower()
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
  const fold = value => (value || '').toLowerCase().normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').replace(/ä/g, 'a').replace(/ö/g, 'o').replace(/ü/g, 'u').replace(/ß/g, 'ss').replace(/ł/g, 'l').replace(/[^a-z0-9.]+/g, ' ');
  const skip = new Set(['fc','sc','sv','ac','cf','afc','cfc','vfb','vfl','tsg','bsc','fk','sk','dsc','estac','rcd','cb','bc','kk','bk']);
  const near = (token, word) => {
    if (token === word) return true;
    if (token.length < 6 || word.length < 6 || Math.abs(token.length - word.length) > 2) return false;
    const rows = [Array.from({length: word.length + 1}, (_, index) => index)];
    for (let i = 1; i <= token.length; i += 1) {
      const next = [i];
      for (let j = 1; j <= word.length; j += 1) next.push(Math.min(next[j - 1] + 1, rows[i - 1][j] + 1, rows[i - 1][j - 1] + (token[i - 1] === word[j - 1] ? 0 : 1)));
      rows.push(next);
    }
    return rows[token.length][word.length] <= 2;
  };
  const present = (blob, token) => blob.includes(token) || blob.split(' ').some(word => near(token, word.replace(/\\./g, '')));
  const tokens = value => {
    const all = fold(value).split(' ').map(token => token.replace(/\\./g, '')).filter(Boolean);
    const kept = all.filter(token => !skip.has(token) && token.length > 1 && !/^\\d+$/.test(token));
    return kept.length ? kept : all;
  };
  const wanted = tokens(args.home);
  const other = tokens(args.away);
  const shown = [...document.querySelectorAll('.match-row, article, tr')].some(row => {
    const blob = fold(row.innerText || '');
    return wanted.every(token => present(blob, token)) && other.every(token => present(blob, token));
  });
  if (shown) return {visible: true, clicked: ''};
  const rows = [...document.querySelectorAll('li.category-list-item, li.tournament-list-item, .tournament-list-item')]
    .filter(item => !item.hasAttribute('data-sports-ai-opened'));
  const compTokens = fold(args.competition).split(' ').filter(word => word && !/^\\d{3,}$/.test(word) && (word.length > 2 || /^\\d+\\.?$/.test(word)));
  const labelOf = item => {
    if (item.classList.contains('tournament-list-item')) {
      const named = item.querySelector(':scope > .tournament-name') || item.querySelector('.tournament-name');
      const raw = (named && named.textContent) || item.getAttribute('title') || item.textContent || '';
      return fold(raw).trim().split(' ').slice(0, 8).join(' ');
    }
    const span = item.querySelector(':scope > .category-item span');
    return fold((span && span.textContent) || '').trim();
  };
  const measure = item => {
    let box = item.getBoundingClientRect();
    if ((box.width < 8 || box.height < 8) && item.classList.contains('tournament-list-item')) {
      try { item.scrollIntoView({block: 'nearest', inline: 'nearest'}); } catch (error) {}
      box = item.getBoundingClientRect();
    }
    return box;
  };
  document.querySelectorAll('[data-sports-ai-league]').forEach(node => node.removeAttribute('data-sports-ai-league'));
  let best = null;
  let bestRank = 0;
  for (const item of rows) {
    const tournament = item.classList.contains('tournament-list-item');
    // An expanded country already lists its tournaments. Clicking it again collapses it.
    if (!tournament && item.hasAttribute('data-sports-ai-opened') && item.querySelector(':scope .tournament-list-item .tournament-name')) continue;
    const box = measure(item);
    const named = tournament && (item.querySelector('.tournament-name') || item.getAttribute('title'));
    const parentCategory = tournament ? item.closest('li.category-list-item') : null;
    // SportyBet keeps nested tournament nodes at display:none; the open country
    // marker is the reliable signal that those rows can be clicked.
    const nestedReady = Boolean(tournament && named && parentCategory && parentCategory.hasAttribute('data-sports-ai-opened'));
    if (!nestedReady && (box.left < 0 || box.width < 36 || box.width > 420 || box.height < 8 || box.height > 64)) continue;
    if (!tournament && (box.left > 420 || box.width > 320)) continue;
    const text = labelOf(item);
    const label = text.split(' ').filter(Boolean);
    if (!label.length) continue;
    if (!compTokens.includes('srl') && label.includes('srl')) continue;
    if (!compTokens.includes('simulated') && label.includes('simulated')) continue;
    const alias = {english: 'england', dutch: 'netherlands', french: 'france', german: 'germany', fussball: 'germany'};
    const bare = word => word.replace(/\\.$/, '');
    const same = (token, word) => bare(token) === bare(word) || alias[token] === word || alias[word] === token;
    const hits = compTokens.filter(token => label.some(word => same(token, word))).length;
    const extras = label.filter(word => !compTokens.some(token => same(token, word))).length;
    const rank = hits * 20 - extras * 25 + (tournament ? 12 : 0) + (box.top > 0 && box.top < 900 ? 1 : 0);
    if (hits > 0 && rank > bestRank) {
      best = item;
      bestRank = rank;
    }
  }
  if (best) {
    best.setAttribute('data-sports-ai-league', '1');
    best.setAttribute('data-sports-ai-opened', '1');
    try { best.scrollIntoView({block: 'center', inline: 'nearest'}); } catch (error) {}
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
    result = await page.evaluate(REVEAL_FIXTURE, payload)
    for _ in range(6):
        if result.get('visible') or result.get('clicked') == 'search' or not result.get('clicked'):
            break
        clicked = await page.evaluate(
            '''() => {
              const node = document.querySelector('[data-sports-ai-league="1"]');
              if (!node) return false;
              try { node.scrollIntoView({block: 'center', inline: 'nearest'}); } catch (error) {}
              const box = node.querySelector('.checkbox') || node;
              box.click();
              return true;
            }'''
        )
        if not clicked:
            break
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
  const fold = value => (value || '').toLowerCase().replace(/ä/g, 'a').replace(/ö/g, 'o').replace(/ü/g, 'u').replace(/ß/g, 'ss').replace(/[^a-z0-9.]+/g, ' ');
  const skip = new Set(['fc','sc','sv','ac','cf','afc','cfc','vfb','vfl','tsg','bsc','fk','sk','dsc','estac','rcd']);
  const near = (token, word) => {
    if (token === word) return true;
    if (token.length < 6 || word.length < 6 || Math.abs(token.length - word.length) > 2) return false;
    const rows = [Array.from({length: word.length + 1}, (_, index) => index)];
    for (let i = 1; i <= token.length; i += 1) {
      const next = [i];
      for (let j = 1; j <= word.length; j += 1) next.push(Math.min(next[j - 1] + 1, rows[i - 1][j] + 1, rows[i - 1][j - 1] + (token[i - 1] === word[j - 1] ? 0 : 1)));
      rows.push(next);
    }
    return rows[token.length][word.length] <= 2;
  };
  const tokens = value => {
    const all = fold(value).split(' ').map(token => token.replace(/\\./g, '')).filter(Boolean);
    const kept = all.filter(token => !skip.has(token) && !/^\\d+$/.test(token));
    return kept.length ? kept : all;
  };
  const present = (blob, token) => blob.includes(token) || blob.split(' ').some(word => near(token, word.replace(/\\./g, '')));
  const wanted = tokens(args.home);
  const other = tokens(args.away);
  const rows = [...document.querySelectorAll('.match-row')].filter(candidate => {
    const blob = fold(candidate.innerText || '');
    return wanted.every(token => present(blob, token)) && other.every(token => present(blob, token));
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
  const numeric = value => {
    const match = String(value || '').replace(',', '.').match(/-?\\d+(?:\\.\\d+)?/);
    return match ? Number(match[0]) : null;
  };
  const current = ((control.querySelector('.af-select-input') || control).innerText || '').trim();
  const shownLine = numeric(current);
  const wantedLine = numeric(line);
  if (shownLine != null && wantedLine != null && Math.abs(shownLine - wantedLine) < 0.001) return 'shown';
  control.setAttribute('data-sports-ai-line', '1');
  return 'open';
}'''

PICK_LINE = '''(line) => {
  const numeric = value => {
    const match = String(value || '').replace(',', '.').match(/-?\\d+(?:\\.\\d+)?/);
    return match ? Number(match[0]) : null;
  };
  const wanted = numeric(line);
  document.querySelectorAll('[data-sports-ai-line-option]').forEach(node => node.removeAttribute('data-sports-ai-line-option'));
  const control = document.querySelector('[data-sports-ai-line="1"]');
  const anchor = control ? control.getBoundingClientRect() : null;
  const lists = [...document.querySelectorAll('.af-select-list')].filter(list => {
    const box = list.getBoundingClientRect();
    return box.height > 8 && getComputedStyle(list).display !== 'none';
  }).sort((left, right) => {
    if (!anchor) return 0;
    const distance = box => Math.abs(box.top - anchor.bottom) + Math.abs(box.left - anchor.left);
    return distance(left.getBoundingClientRect()) - distance(right.getBoundingClientRect());
  });
  const pools = lists.slice(0, 1).map(list => [...list.querySelectorAll('.af-select-item')]);
  for (const pool of pools) {
    const matches = pool.filter(item => {
      const shown = numeric(item.innerText || '');
      return wanted != null && shown != null && Math.abs(shown - wanted) < 0.001;
    });
    const node = matches.find(item => {
      const box = item.getBoundingClientRect();
      return box.width > 4 && box.height > 4;
    });
    if (!node) continue;
    node.scrollIntoView({block: 'center', inline: 'nearest'});
    node.setAttribute('data-sports-ai-line-option', '1');
    node.click();
    return true;
  }
  return false;
}'''

READ_OFFERED_LINES = '''() => {
  const numeric = value => {
    const match = String(value || '').replace(',', '.').match(/-?\\d+(?:\\.\\d+)?/);
    return match ? Number(match[0]) : null;
  };
  const values = [];
  for (const node of document.querySelectorAll('.af-select-item')) {
    const number = numeric(node.innerText || '');
    if (number == null || values.some(item => Math.abs(item - number) < 0.001)) continue;
    values.push(number);
    if (values.length >= 40) break;
  }
  return values;
}'''


def _numeric_line(value):
    match = re.search(r'-?\d+(?:\.\d+)?', str(value or '').replace(',', '.'))
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _same_line(shown, wanted):
    left = _numeric_line(shown)
    right = _numeric_line(wanted)
    return left is not None and right is not None and abs(left - right) < 0.001


def _line_text(number):
    if abs(number - round(number)) < 0.001:
        return str(int(round(number)))
    return f'{number:g}'


def _line_choices(line):
    return [line] if line else []


async def _switch_line(page, item):
    """Open the line control and choose only the ticket's exact line."""
    line = _market_line(item.get('market'))
    if not line:
        return False
    choices = _line_choices(line)
    for choice in choices:
        state = await page.evaluate(SWITCH_LINE, {
            'home': item.get('home') or '', 'away': item.get('away') or '', 'line': choice,
        })
        if state == 'shown':
            item['_page_line'] = choice
            return True
    state = await page.evaluate(SWITCH_LINE, {
        'home': item.get('home') or '', 'away': item.get('away') or '', 'line': choices[0],
    })
    if state != 'open':
        return False
    await page.keyboard.press('Escape')
    await page.wait_for_timeout(200)
    state = await page.evaluate(SWITCH_LINE, {
        'home': item.get('home') or '', 'away': item.get('away') or '', 'line': choices[0],
    })
    if state == 'shown':
        item['_page_line'] = choices[0]
        return True
    if state != 'open':
        return False
    control = page.locator('[data-sports-ai-line="1"]')
    await control.scroll_into_view_if_needed()
    await control.click(force=True)
    for choice in choices:
        for _ in range(4):
            await page.wait_for_timeout(500)
            if await page.evaluate(PICK_LINE, choice):
                await page.wait_for_timeout(700)
                shown = await page.evaluate(
                    '''() => {
                      const control = document.querySelector('[data-sports-ai-line="1"]');
                      const input = control && control.querySelector('.af-select-input');
                      return ((input && input.innerText) || '').trim();
                    }'''
                )
                if _same_line(shown, choice):
                    item['_page_line'] = choice
                    return True
    try:
        item['_offered_lines'] = await page.evaluate(READ_OFFERED_LINES)
    except Exception:
        item['_offered_lines'] = []
    return False


async def _focus_market(page, item):
    market = item.get('market') if isinstance(item, dict) else item
    family = _market_family(market)
    if str(market or '').startswith('MONEYLINE_'):
        family = 'winner'
    if family == 'result':
        return ''
    payload = {'family': family}
    if isinstance(item, dict):
        payload['home'] = item.get('home') or ''
        payload['away'] = item.get('away') or ''
    label = await page.evaluate(FOCUS_MARKET, payload)
    if label:
        await page.wait_for_timeout(800)
    return label


async def prepare_slip(page, selections, book=True, skip_unpriced=False, max_move=None):
    """Select each ticket outcome at its current displayed price. Never place a wager.

    A high-odds ticket can leave out a line SportyBet is not showing, then book the rest.
    """
    await page.wait_for_selector('.match-row, article', timeout=20000)
    body = await page.locator('body').inner_text(timeout=8000)
    if blocked_page(body):
        raise RuntimeError('Page is blocked. No booking code was requested.')
    seen = []
    skipped = []
    for index, item in enumerate(selections):
        marker = f'pick-{index}'
        async def find_price(focus=True):
            family = _market_family(item.get('market'))
            if family != 'result':
                if focus:
                    await _focus_market(page, item)
                return await page.evaluate(FIND_LINE, {
                    'home': item['home'], 'away': item['away'], 'odds': float(item['decimal_odds']),
                    'line': item.get('_page_line') or _market_line(item.get('market')),
                    'side': _market_side(item.get('market')), 'marker': marker,
                })
            if focus and str(item.get('market') or '').startswith('MONEYLINE_'):
                await _focus_market(page, item)
            return await page.evaluate(FIND_ODDS, {
                'home': item['home'], 'away': item['away'], 'selection': item.get('selection') or '',
                'side': _market_side(item.get('market')), 'marker': marker,
                'two_way': str(item.get('market') or '').startswith('MONEYLINE_'),
            })
        hits = await find_price()
        revealed = {}
        if hits.get('matched') != 1:
            revealed = await _reveal_fixture(page, item)
            hits = await find_price()
        if hits.get('matched') != 1 and hits.get('line_visible') is False and _market_line(item.get('market')):
            await _switch_line(page, item)
            hits = await find_price(focus=False)
        if hits['matched'] == 1 and max_move is not None:
            shown = float(hits.get('shown') or item['decimal_odds'])
            stored = float(item['decimal_odds'])
            if stored > 1 and abs(shown / stored - 1) > max_move:
                # Far too far from the stored price to be a market move: likely the wrong control.
                item['_rejected_price'] = shown
                if skip_unpriced:
                    skipped.append(item)
                    continue
                seen.append({'fixture_id': item['fixture_id'], 'stored_odds': stored, 'page_odds': shown})
                return {'status': 'ODDS_CHANGED', 'seen': seen, 'code': None,
                        'error': f"{item['home']} vs {item['away']} {item.get('market')}: page shows {shown:.2f} against {stored:.2f}; "
                                 'not booked because it is probably a different selection.'}
        if hits['matched'] == 1:
            control = await page.evaluate(INSPECT, f'[data-sports-ai-pick="{marker}"]')
            name = (control or {}).get('name') or ''
            if not control or control.get('disabled') or re.search(r'place\s*bet|bet\s*now|\bpay\b|deposit|confirm\s*bet', name, re.I):
                raise RuntimeError(f"The price control is not an allowed selection: {item['home']} vs {item['away']}")
            seen.append({
                'fixture_id': item['fixture_id'], 'market': item.get('market'), 'selection': item.get('selection'),
                'stored_odds': item['decimal_odds'],
                'page_odds': float(hits.get('shown') or item['decimal_odds']), 'control': control,
            })
            await page.locator(f'[data-sports-ai-pick="{marker}"]').click()
            await page.wait_for_timeout(400)
            continue
        if skip_unpriced and hits.get('matched') != 1:
            skipped.append(item)
            continue
        if hits['matched'] == 0 and hits.get('line_visible') is False:
            seen.append({'fixture_id': item['fixture_id'], 'stored_odds': item['decimal_odds'], 'page_odds': None})
            offered = item.get('_offered_lines') or []
            wanted = _numeric_line(item.get('_page_line') or _market_line(item.get('market')))
            listed = wanted is not None and any(abs(float(value) - wanted) < 0.001 for value in offered)
            detail = 'The quoted line is not the line shown for this match.'
            if listed:
                detail = f'{detail} The page lists {_line_text(wanted)} and the stored price stays with that line.'
            elif offered:
                shown = ', '.join(_line_text(float(value)) for value in offered[:12])
                detail = f'{detail} The page offered {shown}, and no stored SportyBet price matches one of those lines.'
            return {
                'status': 'ODDS_CHANGED', 'seen': seen, 'code': None,
                'error': detail,
            }
        if hits['matched'] == 0 and hits['nearby']:
            seen.append({'fixture_id': item['fixture_id'], 'stored_odds': item['decimal_odds'], 'page_odds': float(hits['nearby'][0]['text'])})
            return {'status': 'ODDS_CHANGED', 'seen': seen, 'code': None}
        opened = revealed.get('clicked') or 'none'
        raise RuntimeError(f"Selection control missing or ambiguous: {item['home']} vs {item['away']} (league control: {opened})")
    if not book:
        return {'status': 'SELECTED', 'seen': seen, 'code': None, 'skipped': skipped}
    booked = [item for item in selections if item not in skipped]
    result = await request_displayed_code(page, booked, seen)
    result['skipped'] = skipped
    return result


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
    sport = 'football' if sport == 'frauenfussball' else sport
    labels = {'football': 'Football', 'basketball': 'Basketball'}
    if sport not in labels:
        with connect() as connection:
            registered=connection.execute('SELECT name FROM sports_ai_sports WHERE code=?',(sport,)).fetchone()
        if registered: labels[sport]=registered['name']
    if reuse:
        clicked = await page.evaluate(
            '''(label) => {
              if (!label) return false;
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
        from urllib.parse import urlsplit, urlunsplit
        with connect() as connection:
            source = connection.execute(
                """SELECT q.source_url FROM sports_ai_odds_snapshots q
                   JOIN sports_ai_fixtures f ON f.id=q.fixture_id
                   WHERE f.sport=? AND q.bookmaker='SportyBet' ORDER BY q.observed_at DESC LIMIT 1""", (sport,),
            ).fetchone()
        parsed = urlsplit(source['source_url']) if source else None
        if parsed and parsed.scheme == 'https' and parsed.hostname == 'www.sportybet.com' and parsed.path.startswith('/ng/sport/'):
            url = urlunsplit(('https', 'www.sportybet.com', '/'.join(parsed.path.split('/')[:4]), '', ''))
    if not url:
        raise RuntimeError('No public page is configured for this sport')
    if not url.startswith(('http://127.0.0.1', 'http://localhost')):
        from source_permissions import require
        await asyncio.to_thread(require, url, 'booking')
    response = await page.goto(url, wait_until='domcontentloaded', timeout=45000)
    if response and response.status >= 400:
        raise RuntimeError(f'HTTP {response.status}')


async def _top_up_from_reserves(page, booked, seen, policy, limit=50, chunk=3):
    """Add reserve legs SportyBet does show until the slip reaches its prize again (max `limit` legs)."""
    ticket_id = next((item.get('ticket_id') for item in booked if item.get('ticket_id')), None)
    if not ticket_id:
        return []
    from prize_ladder import reserve_legs
    with connect() as connection:
        reserves = reserve_legs(connection, ticket_id)
    teams = {str(t or '').strip().casefold() for item in booked for t in (item.get('home'), item.get('away'))}
    pool = [dict(leg, ticket_id=ticket_id) for leg in reserves
            if not ({str(leg['home']).strip().casefold(), str(leg['away']).strip().casefold()} & teams)]
    page_sport = lambda item: 'football' if item.get('sport') == 'frauenfussball' else item.get('sport')
    added = []
    for sport in list(dict.fromkeys(page_sport(leg) for leg in pool)):
        group = [leg for leg in pool if page_sport(leg) == sport]
        await _open_sport(page, sport, None, reuse=True)
        for start in range(0, len(group), chunk):
            if not _prize_short(booked + added, seen) or len(booked) + len(added) >= limit:
                return added
            batch = group[start:start + max(1, min(chunk, limit - len(booked) - len(added)))]
            part = await prepare_slip(page, batch, book=False, skip_unpriced=True,
                                      max_move=float(policy['max_price_move']) if policy['accept_displayed_odds'] else None)
            if part['status'] != 'SELECTED':
                continue
            found = {item.get('fixture_id') for item in part['seen']}
            seen.extend(part['seen'])
            added.extend(leg for leg in batch if leg['fixture_id'] in found)
    return added


def _prize_short(booked, seen):
    """For a prize-ladder ticket: (displayed odds, prize odds) when the booked legs fall short."""
    ticket_id = next((item.get('ticket_id') for item in booked if item.get('ticket_id')), None)
    if not ticket_id:
        return None
    from prize_ladder import minimum_odds
    with connect() as connection:
        floor = minimum_odds(connection, ticket_id)
    if floor is None:
        return None
    prices = {item.get('fixture_id'): float(item.get('page_odds') or 0) for item in seen}
    shown = 1.0
    for item in booked:
        shown *= prices.get(item.get('fixture_id')) or float(item.get('decimal_odds') or 1)
    return (shown, floor) if shown < floor else None


def _flexible_high_odds(selections):
    """The 1.95 band can be booked without a line SportyBet is not showing."""
    ticket_id = next((item.get('ticket_id') for item in selections if item.get('ticket_id')), None)
    if not ticket_id:
        return False
    with connect() as connection:
        row = connection.execute(
            'SELECT parameters_json FROM sports_ai_accumulator_tickets WHERE id=?',
            (ticket_id,),
        ).fetchone()
    if not row:
        return False
    try:
        parameters = json.loads(row['parameters_json'] or '{}')
    except json.JSONDecodeError:
        return False
    return parameters.get('product') == 'high_odds' and parameters.get('band') == 'to-1.95'


def _drop_unpriced_legs(connection, ticket_id, skipped):
    """Keep the stored ticket on the selections SportyBet actually priced."""
    from high_odds_board import _slip_signature
    for item in skipped:
        connection.execute(
            """DELETE FROM sports_ai_accumulator_legs
               WHERE ticket_id=? AND fixture_id=? AND upper(market)=upper(?)""",
            (ticket_id, item.get('fixture_id'), item.get('market')),
        )
    legs = connection.execute(
        'SELECT fixture_id, market, decimal_odds, probability FROM sports_ai_accumulator_legs WHERE ticket_id=?',
        (ticket_id,),
    ).fetchall()
    if not legs:
        return
    product = 1.0
    joint = 1.0
    for leg in legs:
        product *= float(leg['decimal_odds'])
        joint *= float(leg['probability'])
    row = connection.execute(
        'SELECT parameters_json FROM sports_ai_accumulator_tickets WHERE id=?',
        (ticket_id,),
    ).fetchone()
    try:
        parameters = json.loads(row['parameters_json'] or '{}')
    except json.JSONDecodeError:
        parameters = {}
    parameters['leg_count'] = len(legs)
    if parameters.get('product') == 'high_odds':
        signature = _slip_signature(parameters.get('day') or '', parameters.get('band') or '', legs)
    else:
        from accumulator_engine import selection_signature
        signature = selection_signature(legs)
    connection.execute(
        """UPDATE sports_ai_accumulator_tickets
           SET combined_odds=?, joint_probability=?, leg_count=?, signature=?, parameters_json=?
           WHERE id=?""",
        (round(product, 2), joint, len(legs), signature, json.dumps(parameters), ticket_id),
    )


async def execute_job(job_id, page_url=None, slot_wait=60):
    """Run one booking job while holding a shared browser slot.

    When every slot is busy the job stays QUEUED and no attempt is spent.
    """
    from browser_slots import BrowserBusy, async_browser_slot
    try:
        async with async_browser_slot('booking', wait=slot_wait):
            return await _execute_job(job_id, page_url)
    except BrowserBusy as busy:
        with connect() as connection:
            _log_booking(connection, job_id, 'prepare', str(busy), level='warning')
        return {'id': job_id, 'status': 'QUEUED', 'reason_code': 'BROWSER_BUSY', 'error': str(busy), 'places_wager': False}


async def _execute_job(job_id, page_url=None):
    from playwright.async_api import async_playwright
    session_id = None
    replaced_ticket_id = None
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        job = connection.execute('SELECT * FROM sports_ai_booking_jobs WHERE id=?', (job_id,)).fetchone()
        if not job:
            raise ValueError('Booking job not found')
        if job['status'] != 'QUEUED':
            return {'id': job_id, 'status': job['status'], 'places_wager': False}
        attempt = int(job['attempt'] or 0) + 1
        if attempt > int(job['max_attempts'] or 3):
            _set_job(connection,job_id,'FAILED',error='Retry limit reached',reason_code='RETRY_EXHAUSTED')
            return {'id': job_id, 'status': 'FAILED', 'error': 'Retry limit reached', 'places_wager': False}
        selections = json.loads(job['selections_json'])
        from qualification_engine import require_current_qualification
        market_priced = any(_ticket_product(connection, item.get('ticket_id')) in ('jackpot_low', 'prize_ladder')
                            for item in selections if item.get('ticket_id'))
        # Jackpot and prize-ladder legs are priced by SportyBet itself, not by the quarantined basketball model.
        guarded = [] if market_priced else [item for item in selections if item.get('sport') == 'basketball'
                   or _ticket_product(connection, item.get('ticket_id')) in ('high_odds', 'sure_odds')]
        try:
            if guarded:
                require_current_qualification(connection, guarded, _now())
        except ValueError as error:
            _set_job(connection, job_id, 'FAILED', error=str(error), reason_code='QUALIFICATION_LAPSED')
            return {'id': job_id, 'status': 'FAILED', 'error': str(error), 'places_wager': False}
        from booking_policy import load as load_policy
        policy = load_policy(connection)
        from high_odds_service import validate_snapshot
        optimized_id = next((item.get('ticket_id') for item in selections if item.get('ticket_id')),None)
        # With displayed odds accepted, a moved price or lapsed decision is not a reason to stop.
        problems = validate_snapshot(connection,optimized_id) if optimized_id and not policy['accept_displayed_odds'] else []
        if problems:
            _set_job(connection,job_id,'FAILED',error='; '.join(problems),reason_code='SNAPSHOT_REVALIDATION')
            return {'id':job_id,'status':'FAILED','error':'; '.join(problems),'places_wager':False}
        from ticket_diversity import assert_available, TicketExposureConflict
        linked_id = selections[0].get('ticket_id') if selections else None
        linked_ticket = connection.execute('SELECT family_id,parameters_json FROM sports_ai_accumulator_tickets WHERE id=?', (linked_id,)).fetchone() if linked_id else None
        try:
            if policy['enforce_match_exposure']:
                if linked_ticket and json.loads(linked_ticket['parameters_json'] or '{}').get('diversity_retired'):
                    raise TicketExposureConflict('This ticket was retired because its matches overlap another active ticket.')
                assert_available(connection, selections, ignore_family=linked_ticket['family_id'] if linked_ticket else None)
        except TicketExposureConflict as exc:
            _set_job(connection, job_id, 'FAILED', error=str(exc), reason_code='FIXTURE_EXPOSURE')
            return {'id': job_id, 'status': 'FAILED', 'reason_code': 'FIXTURE_EXPOSURE', 'error': str(exc), 'places_wager': False}
        selections = page_identity(connection, selections)
        # A match that has kicked off is never booked. Drop it while enough legs remain.
        moment = _now()
        started_legs = [item for item in selections if (_parse_booking_time(item.get('starts_at')) or moment) <= moment
                        and item.get('starts_at')]
        from booking_policy import legs_needed
        minimum = legs_needed(policy, len(selections))
        jackpot_ticket = next((item.get('ticket_id') for item in selections if item.get('ticket_id')), None)
        if jackpot_ticket and _ticket_product(connection, jackpot_ticket) == 'jackpot_low':
            from jackpot_low import MIN_BOOKED
            minimum = min(MIN_BOOKED, len(selections))  # a jackpot keeps at least 40 legs or is not booked
        # A leg SportyBet has not listed for a day is not on its pages; do not open a browser for it.
        since = (moment - timedelta(hours=OFFER_HOURS)).isoformat()
        unlisted = [item for item in selections if item not in started_legs and not connection.execute(
            'SELECT 1 FROM sports_ai_sportybet_observations WHERE fixture_id=? AND observed_at>=? LIMIT 1',
            (item.get('fixture_id'), since)).fetchone()]
        if unlisted and policy['drop_unavailable_legs']:
            started_legs = started_legs + unlisted
        if started_legs:
            selections = [item for item in selections if item not in started_legs]
            if not policy['drop_unavailable_legs'] or len(selections) < minimum:
                names = ', '.join(f"{i.get('home')} vs {i.get('away')}" for i in unlisted[:6])
                message = (f'{len(started_legs) - len(unlisted)} selection(s) already kicked off and {len(unlisted)} are not listed by SportyBet'
                           f"{(': ' + names) if names else ''}; {len(selections)} remain, {minimum} needed")
                code = 'LEGS_NOT_OFFERED' if unlisted else 'FIXTURE_STARTED'
                _set_job(connection, job_id, 'FAILED', error=message, reason_code=code)
                if ticket_id_for_job := next((i.get('ticket_id') for i in started_legs if i.get('ticket_id')), None):
                    _record_diagnostic(connection, ticket_id_for_job, code, message[:500], moment)
                return {'id': job_id, 'status': 'FAILED', 'reason_code': code, 'error': message, 'places_wager': False}
        session_id = _open_session(connection, job_id)
        connection.execute(
            """UPDATE sports_ai_booking_jobs
               SET status='PREPARING', attempt=?, session_id=?, updated_at=? WHERE id=?""",
            (attempt, session_id, _now().isoformat(), job_id),
        )
        ticket_id = next((item.get('ticket_id') for item in selections if item.get('ticket_id')), None)
        _log_booking(connection, job_id, 'prepare', 'Opening the public SportyBet page', ticket_id=ticket_id, session_id=session_id, detail={'attempt': attempt})
    def page_sport(item):
        return 'football' if item.get('sport') == 'frauenfussball' else item.get('sport')
    sports = []
    for item in selections:
        if page_sport(item) not in sports:
            sports.append(page_sport(item))
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                context = await browser.new_context(viewport={'width': 1440, 'height': 900})
                page = await context.new_page()
                seen = []
                skipped = []
                reserve_added = []
                result = None
                flexible = policy['drop_unavailable_legs'] or _flexible_high_odds(selections)
                needed = minimum if policy['drop_unavailable_legs'] else 15
                for index, sport in enumerate(sports):
                    await _open_sport(page, sport, page_url if len(sports) == 1 else None, reuse=index > 0)
                    group = [item for item in selections if page_sport(item) == sport]
                    part = await prepare_slip(page, group, book=False, skip_unpriced=flexible,
                                              max_move=float(policy['max_price_move']) if policy['accept_displayed_odds'] else None)
                    skipped.extend(part.get('skipped') or [])
                    if part['status'] != 'SELECTED':
                        result = part
                        break
                    seen.extend(part['seen'])
                if result is None:
                    clicked = {item.get('fixture_id') for item in seen}
                    booked = [item for item in selections if item.get('fixture_id') in clicked]
                    if _prize_short(booked, seen):
                        # Prize ladder: replace legs SportyBet does not show with reserves, on this page.
                        added = await _top_up_from_reserves(page, booked, seen, policy)
                        if added:
                            selections = selections + added
                            reserve_added.extend(added)
                            clicked = {item.get('fixture_id') for item in seen}
                            booked = [item for item in selections if item.get('fixture_id') in clicked]
                    if flexible and len(booked) < needed:
                        missing = [item for item in selections if item.get('fixture_id') not in clicked]
                        names = ', '.join(f"{i.get('home')} vs {i.get('away')} ({i.get('market')})" for i in missing[:6])
                        result = {
                            'status': 'FAILED', 'reason_code': 'LEGS_NOT_OFFERED', 'seen': seen, 'code': None,
                            'error': f'SportyBet is not offering {len(missing)} of {len(selections)} selection(s): {names}. '
                                     f'At least {needed} are needed to book.',
                        }
                    elif _prize_short(booked, seen):
                        shown, floor = _prize_short(booked, seen)
                        result = {
                            'status': 'FAILED', 'reason_code': 'PRIZE_SHORT', 'seen': seen, 'code': None,
                            'error': f'After SportyBet dropped unavailable legs the ticket pays {shown:,.0f}x; '
                                     f'its prize needs at least {floor:,.0f}x. Not booked.',
                        }
                    elif policy['accept_displayed_odds']:
                        # Book at the prices on the page; they are stored on the ticket below.
                        result = await request_displayed_code(page, booked, seen)
                        result['skipped'] = [item for item in selections if item.get('fixture_id') not in clicked]
                    else:
                        from high_odds_service import check_displayed_prices
                        try:
                            check_displayed_prices(optimized_id, booked, seen)
                        except ValueError as price_error:
                            if 'Displayed price changed' not in str(price_error):
                                raise
                            from high_odds_service import requalify_displayed_ticket
                            with connect() as connection:
                                connection.execute('BEGIN IMMEDIATE')
                                replacement = requalify_displayed_ticket(connection, optimized_id, seen)
                                if not replacement:
                                    raise
                                next_selections = ticket_selections(connection, replacement)
                                next_job = create_job(connection, next_selections)
                                if next_job.get('duplicate'):
                                    raise ValueError('Replacement booking is already being processed')
                                _set_job(connection, job_id, 'SUPERSEDED', reason_code='ODDS_CHANGED',
                                         error='Requalified at displayed prices; replacement ticket '+replacement,
                                         odds_json=json.dumps(seen))
                                _close_session(connection, session_id, 'SUPERSEDED', 'Requalified displayed prices')
                                replaced_ticket_id = ticket_id
                                ticket_id = optimized_id = replacement
                                job_id = next_job['id']
                                session_id = _open_session(connection, job_id)
                                connection.execute("UPDATE sports_ai_booking_jobs SET status='PREPARING',attempt=1 WHERE id=?", (job_id,))
                                connection.execute("UPDATE sports_ai_high_odds_tickets SET registration_state='queued',booking_job_id=? WHERE ticket_id=?", (job_id,ticket_id))
                            selections = booked = next_selections
                            check_displayed_prices(optimized_id, booked, seen)
                        result = await request_displayed_code(page, booked, seen)
                        result['skipped'] = [item for item in selections if item.get('fixture_id') not in clicked]
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
            if result['status'] == 'CODE_GENERATED' and guarded:
                shown = {(item.get('fixture_id'), item.get('market')): item.get('page_odds')
                         for item in result.get('seen') or []}
                displayed = [{**item, 'decimal_odds': shown.get((item.get('fixture_id'), item.get('market')))}
                             for item in guarded]
                try:
                    require_current_qualification(connection, displayed, _now())
                except ValueError as error:
                    result = {**result, 'status': 'FAILED', 'error': str(error),
                              'reason_code': 'QUALIFICATION_LAPSED'}
            if result['status'] == 'CODE_GENERATED' and ticket_id and reserve_added:
                from prize_ladder import add_legs
                add_legs(connection, ticket_id, reserve_added)
                _log_booking(connection, job_id, 'policy', f'Replaced unavailable legs with {len(reserve_added)} reserve selection(s)',
                             ticket_id=ticket_id, session_id=session_id,
                             detail={'added': [f"{i.get('home')} vs {i.get('away')} {i.get('market')}" for i in reserve_added]})
            if result['status'] == 'CODE_GENERATED':
                dropped = (result.get('skipped') or []) + started_legs
                if ticket_id and dropped:
                    rejected = [i for i in dropped if i.get('_rejected_price') is not None]
                    if rejected:
                        _log_booking(connection, job_id, 'policy', f'{len(rejected)} selection(s) not booked: page price too far from the stored price',
                                     ticket_id=ticket_id, session_id=session_id,
                                     detail={'rejected': [{'fixture_id': i['fixture_id'], 'market': i.get('market'),
                                                           'stored': i.get('decimal_odds'), 'displayed': i['_rejected_price']} for i in rejected]})
                    _drop_unpriced_legs(connection, ticket_id, dropped)
                    _log_booking(connection, job_id, 'policy', f'Dropped {len(dropped)} selection(s) SportyBet did not offer or that had kicked off',
                                 ticket_id=ticket_id, session_id=session_id,
                                 detail={'dropped': [f"{i.get('home')} vs {i.get('away')} {i.get('market')}" for i in dropped]})
                moved = [item for item in result.get('seen') or [] if item.get('page_odds') is not None
                         and abs(float(item['page_odds']) - float(item.get('stored_odds') or 0)) > 0.000001]
                if ticket_id and moved:
                    _log_booking(connection, job_id, 'policy', f'Booked at {len(moved)} changed displayed price(s)',
                                 ticket_id=ticket_id, session_id=session_id,
                                 detail={'changes': [{'fixture_id': i['fixture_id'], 'market': i.get('market'),
                                                      'stored': i.get('stored_odds'), 'displayed': i['page_odds']} for i in moved]})
                _set_job(connection, job_id, 'CODE_GENERATED', code=result['code'], screenshot=str(shot), odds_json=json.dumps(result['seen']), reason_code='CODE_VERIFIED')
                if ticket_id:
                    # Store before rewriting leg prices. Applying page odds first can
                    # push a sure-odds total outside 2.00–2.95 and discard the code.
                    _store_displayed_code(connection, ticket_id, result['code'], str(shot))
                    _apply_displayed_prices(connection, ticket_id, result.get('seen') or [])
                    _record_diagnostic(connection, ticket_id, 'CODE_VERIFIED', 'SportyBet displayed one booking code and it matched the expected format.', _now())
                _log_booking(connection, job_id, 'published', 'SportyBet displayed a booking code', ticket_id=ticket_id, session_id=session_id, detail={'evidence': str(shot)})
                _close_session(connection, session_id, 'CODE_GENERATED', 'Displayed booking code stored')
            else:
                reason = result.get('reason_code') or classify_booking_failure('', result['status'])
                message = result.get('error') or 'The displayed price no longer matches the qualified quote'
                _set_job(connection, job_id, result['status'], odds_json=json.dumps(result['seen']), error=message, reason_code=reason)
                if ticket_id:
                    _record_diagnostic(connection, ticket_id, reason, message[:500], _now())
                _log_booking(connection, job_id, 'odds' if reason == 'ODDS_CHANGED' else 'availability', message, level='warning', ticket_id=ticket_id, session_id=session_id)
                _close_session(connection, session_id, result['status'], message)
        if replaced_ticket_id:
            from high_odds_service import registration_result
            with connect() as connection:
                registration_result(connection,ticket_id,result)
            result.update({'ticket_id':ticket_id,'supersedes_ticket_id':replaced_ticket_id})
        result.update({'id': job_id, 'places_wager': False})
        return result
    except Exception as exc:
        message = str(exc)[:500]
        reason = classify_booking_failure(message)
        from browser_slots import classify_browser_error
        if classify_browser_error(exc) == 'transient':
            # The page or network was unavailable. Requeue with backoff and
            # give the attempt back, so an outage does not exhaust retries.
            with connect() as connection:
                connection.execute(
                    """UPDATE sports_ai_booking_jobs SET status='QUEUED',attempt=?,error=?,reason_code='NETWORK_UNAVAILABLE',
                       updated_at=? WHERE id=?""", (max(0, attempt - 1), message, _now().isoformat(), job_id),
                )
                _log_booking(connection, job_id, 'recovery', 'Network or page unavailable; requeued without spending an attempt',
                             level='warning', session_id=session_id, detail={'error': message})
                _close_session(connection, session_id, 'FAILED', message)
            return {'id': job_id, 'status': 'QUEUED', 'reason_code': 'NETWORK_UNAVAILABLE', 'error': message, 'code': None, 'places_wager': False}
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
    if status == 'ODDS_CHANGED' or 'no longer matches' in text or 'displayed price changed' in text:
        return 'ODDS_CHANGED'
    if any(marker in text for marker in ('challenge', 'captcha', 'verify you are human', 'blocked')):
        return 'ACCESS_CHALLENGE'
    if 'session' in text:
        return 'SESSION_EXPIRED'
    if 'did not show one booking code' in text or 'booking-code control' in text:
        return 'CODE_NOT_RETURNED'
    if 'booking slip did not include' in text or 'not a booking code' in text:
        return 'CODE_VERIFICATION_FAILED'
    if 'net::err_' in text or re.search(r'timeout \d+ms exceeded', text):
        return 'NETWORK_UNAVAILABLE'
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


def retire_duplicate_tickets(connection, now=None):
    """Retire uncoded tickets whose selections equal a ticket that already has a code.

    SportyBet returns the same code for the same slip, so such a ticket can never get
    its own code. Its failed jobs are marked SUPERSEDED with the original code.
    """
    moment = (now or _now()).isoformat()
    # Older runs reported missing selections as an odds change; give them their real reason.
    connection.execute(
        """UPDATE sports_ai_booking_jobs SET status='FAILED', reason_code='LEGS_NOT_OFFERED',
             error='SportyBet was not offering enough of these selections to book the ticket (not an odds change).'
           WHERE status='ODDS_CHANGED' AND error='SportyBet is not showing enough of these lines to book the ticket.'""",
    )
    # Jobs that only re-displayed another ticket's code are duplicates, not new codes.
    connection.execute(
        """UPDATE sports_ai_booking_jobs SET status='SUPERSEDED', reason_code='DUPLICATE_SLIP',
             error='Same selections as a ticket already coded ' || COALESCE(code,'') || '; this duplicate was retired.'
           WHERE status='CODE_GENERATED' AND ticket_id IN (
             SELECT id FROM sports_ai_accumulator_tickets WHERE publication_state='DUPLICATE')""",
    )
    # A ticket holding a displayed code is not retired for sharing a match; restore any that were.
    from booking_policy import load as booking_policy
    if not booking_policy(connection)['enforce_match_exposure']:
        connection.execute(
            """UPDATE sports_ai_accumulator_tickets
               SET status=CASE WHEN json_extract(parameters_json,'$.product') IN ('sure_odds','high_odds')
                               THEN 'unverified_probability' ELSE 'qualified' END,
                   publication_state=NULL
               WHERE publication_state='DIVERSITY_RETIRED'
                 AND EXISTS (SELECT 1 FROM sports_ai_booking_codes b WHERE b.accumulator_ticket_id=sports_ai_accumulator_tickets.id
                             AND b.verification_method='displayed' AND TRIM(COALESCE(b.code,''))<>'')
                 AND EXISTS (SELECT 1 FROM sports_ai_accumulator_legs l WHERE l.ticket_id=sports_ai_accumulator_tickets.id
                             AND julianday(l.starts_at)>julianday(?))""", (moment,),
        )
    rows = connection.execute(
        """SELECT t.id, l.fixture_id, upper(l.market) market,
                  (SELECT b.code FROM sports_ai_booking_codes b WHERE b.accumulator_ticket_id=t.id
                     AND b.verification_method='displayed' AND TRIM(COALESCE(b.code,''))<>'' LIMIT 1) code
           FROM sports_ai_accumulator_tickets t JOIN sports_ai_accumulator_legs l ON l.ticket_id=t.id
           WHERE t.status NOT IN ('expired','void','settled')
              OR EXISTS (SELECT 1 FROM sports_ai_booking_codes b WHERE b.accumulator_ticket_id=t.id
                         AND b.verification_method='displayed' AND TRIM(COALESCE(b.code,''))<>'')""",
    ).fetchall()
    tickets = {}
    for row in rows:
        entry = tickets.setdefault(row['id'], {'code': row['code'], 'legs': set()})
        entry['legs'].add((row['fixture_id'], row['market']))
    coded = {frozenset(v['legs']): (k, v['code']) for k, v in tickets.items() if v['code']}
    retired = 0
    for ticket_id, entry in tickets.items():
        twin = coded.get(frozenset(entry['legs']))
        if entry['code'] or not twin or twin[0] == ticket_id:
            continue
        row = connection.execute('SELECT status,parameters_json FROM sports_ai_accumulator_tickets WHERE id=?', (ticket_id,)).fetchone()
        if not row or row['status'] in ('expired', 'void', 'settled'):
            continue
        parameters = json.loads(row['parameters_json'] or '{}')
        parameters['duplicate_of'] = twin[0]
        connection.execute(
            """UPDATE sports_ai_accumulator_tickets SET status='expired', publication_state='DUPLICATE', parameters_json=?
               WHERE id=?""", (json.dumps(parameters), ticket_id),
        )
        connection.execute(
            """UPDATE sports_ai_booking_jobs SET status='SUPERSEDED', reason_code='DUPLICATE_SLIP', error=?, updated_at=?
               WHERE ticket_id=? AND status IN ('FAILED','ODDS_CHANGED','QUEUED')""",
            (f'Same selections as a ticket already coded {twin[1]}; this duplicate was retired.', moment, ticket_id),
        )
        retired += 1
    return retired


def examine_ready_tickets(connection, now=None, window_open=None):
    """Revalidate tickets before a booking window. A closed window does not open a browser."""
    current = now or _now()
    phase = phase_at(current)
    if window_open is None:
        window_open = publication_open(phase)
    minutes = REFRESH_MINUTES
    duplicates = retire_duplicate_tickets(connection, current)
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
    summary['duplicates_retired'] = duplicates
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
    from booking_policy import load
    policy = load(connection)
    if not policy['require_live_qualification'] and policy['accept_displayed_odds']:
        return _lenient_preflight(connection, legs, now, window_open, policy)
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
    if not window_open:
        return 'WINDOW_CLOSED', 'The next code request waits for the 15-minute gap. The ticket stays ready and no browser is opened.', None
    return 'READY', 'The ticket is still qualified and its same-selection SportyBet quote is fresh.', None


def _lenient_preflight(connection, legs, now, window_open, policy):
    """Booking policy preflight: only kickoff and leg count can hold a ticket back.

    Qualification churn and price age do not stop the request; the page price is
    used when the code is requested. Legs that kicked off or left the fixture store
    are dropped at booking time while enough legs remain.
    """
    live = 0
    started = 0
    for leg in legs:
        fixture = connection.execute(
            'SELECT status, starts_at FROM sports_ai_fixtures WHERE id=?', (leg['fixture_id'],),
        ).fetchone()
        kickoff = _parse_booking_time(leg['starts_at'] or (fixture['starts_at'] if fixture else None))
        if kickoff and kickoff <= now:
            started += 1
        elif fixture and fixture['status'] in ('scheduled', 'not_started'):
            live += 1
    from booking_policy import legs_needed
    needed = legs_needed(policy, len(legs))
    if live < needed:
        if started:
            return 'FIXTURE_NOT_FOUND', f'Only {live} selection(s) are still before kickoff; {needed} are needed.', 'expired'
        return 'FIXTURE_NOT_FOUND', f'Only {live} selection(s) are still in the fixture store; {needed} are needed.', 'review_required'
    if not window_open:
        return 'WINDOW_CLOSED', 'The next code request waits for the request gap. The ticket stays ready and no browser is opened.', None
    return 'READY', 'Booking at the prices SportyBet displays; changed prices are stored on the ticket.', None


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


def recover_stopped_bookings(connection, now=None):
    """Release interrupted browser jobs; a dead PREPARING row cannot block forever."""
    current = now or _now()
    # A browser slot lasts 15 minutes, so a booking still PREPARING after 20 has stopped.
    cutoff = (current-timedelta(minutes=20)).isoformat()
    rows = connection.execute("SELECT id,ticket_id,session_id FROM sports_ai_booking_jobs WHERE status='PREPARING' AND updated_at<?", (cutoff,)).fetchall()
    for row in rows:
        _set_job(connection,row['id'],'FAILED',reason_code='WORKER_STOPPED',error='Booking worker stopped before displaying a code; eligible tickets will be retried.')
        _close_session(connection,row['session_id'],'FAILED','Interrupted booking recovered')
        if row['ticket_id']:
            _record_diagnostic(connection,row['ticket_id'],'WORKER_STOPPED','Interrupted booking recovered; no code was recorded.',current)
    return len(rows)


def process_booking_queue(now=None, ignore_gap=False):
    """Request one prepared ticket's booking code every 15 minutes during testing.

    Does not open a browser when none qualify, when a code was requested inside the last 15 minutes, or when the retry limit is reached.
    """
    moment = now or _now()
    phase = phase_at(moment)
    with connect() as connection:
        recover_stopped_bookings(connection, moment)
        open_now = ignore_gap or _booking_due(connection, moment)
        examined = examine_ready_tickets(connection, moment, window_open=open_now)
        expired = expire_displayed_codes(connection, moment)
        qualified = qualified_selections(connection, moment)
        choice = select_booking(connection, moment, ignore_gap=ignore_gap)
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
            if not selections or len(selections) > _selection_limit(connection, ticket_id):
                return {'status': 'waiting', 'phase': phase, 'qualified': len(qualified), 'expired': expired, 'examined': examined, 'places_wager': False}
            try:
                created = create_job(connection, selections)
            except ValueError as refusal:
                # A guard refused this ticket (e.g. a quarantined basketball leg). Raising failed
                # the whole lane and re-picked the same ticket every 30 seconds, so nothing else
                # could be booked. Record the refusal; the picker moves on to the next ticket.
                _refuse_ticket(connection, ticket_id, selections, str(refusal), moment)
                return {'status': 'NOT_BOOKABLE', 'reason_code': 'NOT_BOOKABLE', 'ticket_id': ticket_id,
                        'error': str(refusal), 'phase': phase, 'examined': examined, 'places_wager': False}
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


def _refuse_ticket(connection, ticket_id, selections, reason, moment):
    """A FAILED job with the guard's reason: next_ticket_id skips the ticket for
    REFRESH_MINUTES and stops after MAX_DAILY_FAILURES. No browser is opened."""
    stamp = moment.isoformat()
    connection.execute(
        """INSERT INTO sports_ai_booking_jobs
           (id,created_at,updated_at,status,reason_code,error,sport,selections_json,signature,places_wager,ticket_id)
           VALUES(?,?,?,'FAILED','NOT_BOOKABLE',?,?,?,?,0,?)""",
        (str(uuid.uuid4()), stamp, stamp, reason[:500],
         next(iter({i.get('sport') for i in selections}), 'mixed') if len({i.get('sport') for i in selections}) == 1 else 'mixed',
         json.dumps(selections, default=str), _signature(selections), ticket_id),
    )
    _record_diagnostic(connection, ticket_id, 'NOT_BOOKABLE', reason, moment)


def book_batch(max_codes=None):
    """Request up to ``max_codes`` codes back to back, then wait for the request gap.

    The first request respects the gap; the rest of the batch follows straight on.
    Stops early when no ticket is ready or a request did not produce a code.
    """
    from booking_policy import current
    limit = int(max_codes or current()['batch_size'])
    results = []
    for index in range(max(1, limit)):
        result = process_booking_queue(ignore_gap=index > 0)
        results.append(result)
        if result.get('status') != 'CODE_GENERATED':
            # A failed ticket does not end the batch; an empty queue or closed gap does.
            if result.get('status') in ('waiting', 'outside_window') or result.get('duplicate'):
                break
    codes = [r.get('code') for r in results if r.get('status') == 'CODE_GENERATED']
    return {'status': 'complete' if codes else (results[-1].get('status') if results else 'waiting'),
            'codes': codes, 'attempts': len(results), 'places_wager': False}


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
        revalidate(connection, 'sports-ai-worker', moment, limit=2000, release_lock=True)
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
        if not selections or len(selections) > _selection_limit(connection, ticket_id):
            return {
                'status': 'waiting', 'confirmation': True, 'examined': examined,
                'places_wager': False, 'opens_browser': False,
            }
        try:
            created = create_job(connection, selections)
        except ValueError as refusal:
            _refuse_ticket(connection, ticket_id, selections, str(refusal), moment)
            return {'status': 'NOT_BOOKABLE', 'confirmation': True, 'error': str(refusal),
                    'places_wager': False, 'opens_browser': False}
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
    with connect() as connection:
        if not _booking_due(connection, moment):
            return [{'status': 'outside_window', 'phase': phase, 'places_wager': False}]
    import asyncio
    results = []
    for _ in range(max(1, limit)):
        with connect() as connection:
            ticket_id = next_ticket_id(connection, _now())
            if not ticket_id:
                break
            selections = ticket_selections(connection, ticket_id)
            if not selections or len(selections) > _selection_limit(connection, ticket_id):
                break
            try:
                created = create_job(connection, selections)
            except ValueError as refusal:
                _refuse_ticket(connection, ticket_id, selections, str(refusal), _now())
                results.append({'status': 'NOT_BOOKABLE', 'ticket_id': ticket_id, 'error': str(refusal)})
                continue
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
    schedule = describe(moment, _publication_anchor(connection))
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
    if not _booking_due(connection, moment):
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
            schedule = describe(_now(), _publication_anchor(connection))
        return {
            'items': jobs,
            'logs': logs,
            'schedule': schedule,
            'places_wager': False,
            'qualified_required': True,
            'message': 'A code is stored only when SportyBet displays it during a WAT publication window. No wager is placed. Zero codes is a valid result.',
        }

    @router.post('/bookings/run')
    def run_booking(user=Depends(auth)):
        return book_batch()

    return router
