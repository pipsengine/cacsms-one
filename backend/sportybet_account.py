"""Authenticated SportyBet account context for the Single Betting engine.

The shared Chromium infrastructure now has two isolated contexts:

* Public Sports Intelligence: the existing anonymous contexts used for odds
  collection and booking codes (sportybet_booking, sportybet_browser). Unchanged.
* Authenticated SportyBet Account: one context created from the operator's own
  logged-in session. It is used only for session health, the displayed balance,
  Bet History, and the Single Betting engine's betslip.

Rules this module enforces:

* The operator logs in personally in a visible window. No password is stored and
  no login challenge is solved; only the resulting session cookies are kept,
  encrypted with a key that never enters the database.
* A balance is account data, never authorization. Real-money submission needs an
  explicit, time-boxed grant (sports_ai_single_authorizations), LIVE mode, prior
  successful rehearsals, a healthy session, ₦100 stake and the daily limits.
* A rehearsal prepares and verifies the betslip in the account and does not click
  Place Bet.
* A submission is attempted once. If the result is unclear the engine is paused
  and the order is reconciled from Bet History; it is never retried.
* Nothing here invents an account figure. Every value carries the time it was read
  from SportyBet, and paper records stay in sports_ai_single_bets.
"""
import asyncio
import json
import os
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from db import connect

BASE_URL = os.environ.get('SPORTS_ACCOUNT_BASE_URL', 'https://www.sportybet.com').rstrip('/')
HOME_PATH = '/ng/'
HISTORY_PATH = '/ng/my_accounts/bet_history/sport_bets?isSettled=10'
STAKE = 100.0
DAILY_COUNT = 25
DAILY_STAKE = 2500.0
REQUIRED_REHEARSALS = 3
MAX_AUTHORIZATION_HOURS = 168
SESSION_CHECK_MINUTES = 20
LOGIN_WAIT_SECONDS = 600
MAX_PRICE_MOVE = 0.05
ACKNOWLEDGEMENT = 'I AUTHORIZE REAL MONEY BETTING ON MY SPORTYBET ACCOUNT'
MODES = ('PAPER', 'LIVE_REHEARSAL', 'LIVE')
WAT = timezone(timedelta(hours=1), 'WAT')
_LOGIN_LOCK = threading.Lock()
_LOGIN_THREAD = None


# ---------------------------------------------------------------- storage

def _secrets_dir():
    path = Path(os.environ.get('SPORTS_SECRETS_DIR') or Path(__file__).resolve().parent / '.secrets')
    path.mkdir(parents=True, exist_ok=True)
    return path


def evidence_dir():
    path = Path(os.environ.get('SPORTS_ACCOUNT_EVIDENCE_DIR') or Path(__file__).resolve().parent / 'account_evidence')
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cipher():
    from cryptography.fernet import Fernet
    configured = os.environ.get('SPORTS_SESSION_KEY')
    if configured:
        return Fernet(configured.encode())
    key_file = _secrets_dir() / 'sportybet_session.key'
    if not key_file.exists():
        key_file.write_bytes(Fernet.generate_key())
        try:
            os.chmod(key_file, 0o600)
        except OSError:
            pass
    return Fernet(key_file.read_bytes().strip())


def _session_file():
    return _secrets_dir() / 'sportybet_session.bin'


def save_session(state):
    _session_file().write_bytes(_cipher().encrypt(json.dumps(state).encode()))


def load_session():
    path = _session_file()
    if not path.exists():
        return None
    from cryptography.fernet import InvalidToken
    try:
        return json.loads(_cipher().decrypt(path.read_bytes()))
    except (InvalidToken, ValueError):
        return None


def forget_session():
    path = _session_file()
    if path.exists():
        path.unlink()


# ---------------------------------------------------------------- account state

def _now(now=None):
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc)


def _parse(value):
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment


def local_date(moment=None):
    return _now(moment).astimezone(WAT).date().isoformat()


def account(connection):
    row = connection.execute("SELECT * FROM sports_ai_sportybet_account WHERE id='primary'").fetchone()
    return dict(row) if row else {'status': 'NOT_CONNECTED', 'detail': ''}


def _set_account(connection, status, detail, now=None, **fields):
    moment = _now(now).isoformat()
    columns = {'status': status, 'detail': detail[:500], 'updated_at': moment, **fields}
    connection.execute(
        f"UPDATE sports_ai_sportybet_account SET {', '.join(f'{k}=?' for k in columns)} WHERE id='primary'",
        tuple(columns.values()),
    )


def _event(connection, bet_id, action, detail, now=None):
    from single_betting import _event as engine_event
    engine_event(connection, bet_id, action, detail, now)


def session_fresh(row, now=None, minutes=SESSION_CHECK_MINUTES):
    checked = _parse(row.get('last_ok_at'))
    return row.get('status') == 'CONNECTED' and checked is not None and _now(now) - checked <= timedelta(minutes=minutes)


# ---------------------------------------------------------------- page reading

LOGIN_STATE = r'''() => {
  const head = document.querySelector('.s-header, .m-header, header') || document.body;
  const visible = node => { const r = node.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const text = (head.innerText || '').replace(/\s+/g, ' ');
  const passwordShown = [...document.querySelectorAll('input[name="psd"], input[type="password"]')].some(visible);
  const account = /my\s*account/i.test(text);
  const money = text.match(/\b(NGN|₦)\s*([\d,]+(?:\.\d{1,2})?)/);
  return {
    logged_in: account && !passwordShown,
    password_shown: passwordShown,
    currency: money ? 'NGN' : null,
    balance: money ? Number(money[2].replace(/,/g, '')) : null,
    masked: account && !money && /NGN\s*\*+/.test(text),
  };
}'''

SLIP_STATE = r'''() => {
  const root = document.querySelector('.m-betslip-wrapper, .m-betslips, .m-betslip');
  if (!root) return {present: false};
  const text = (root.innerText || '').replace(/\s+/g, ' ').trim();
  const count = text.match(/Betslip\s*(\d+)/i);
  const toggles = [...root.querySelectorAll('[class*="inside-btn"]')].map(node => ({
    label: (node.innerText || '').trim().toUpperCase(), active: !/inactive/.test(node.className || '')}));
  const stakeInput = [...root.querySelectorAll('input')].find(node => /min/i.test(node.placeholder || '') || /m-input/.test(node.className || ''));
  const amount = label => { const m = text.match(new RegExp(label + '\\s*(?:NGN|₦)?\\s*([\\d,]+(?:\\.\\d+)?)', 'i')); return m ? Number(m[1].replace(/,/g, '')) : null; };
  const place = [...root.querySelectorAll('button, [role="button"]')].filter(node => /^place\s*bet$/i.test((node.innerText || '').trim()));
  return {
    present: true,
    text: text.slice(0, 600),
    count: count ? Number(count[1]) : null,
    real_active: toggles.some(t => t.label === 'REAL' && t.active),
    sim_active: toggles.some(t => t.label === 'SIM' && t.active),
    toggles,
    stake: stakeInput ? Number((stakeInput.value || '').replace(/,/g, '')) : null,
    total_stake: amount('Total Stake'),
    potential_win: amount('Potential Win'),
    place_buttons: place.length,
    place_disabled: place.length === 1 && !!(place[0].disabled || /disabled/.test(place[0].className || '')),
  };
}'''

CLEAR_SLIP = r'''() => {
  const root = document.querySelector('.m-betslip-wrapper, .m-betslips, .m-betslip');
  if (!root) return 0;
  const controls = [...root.querySelectorAll('button, span, a, i, div')].filter(node =>
    /^(remove all|clear all|delete all)$/i.test((node.innerText || '').trim()) ||
    /(^|\s)(m-icon-delete|m-icon-close|icon-delete|remove-all|delete-all)(\s|$)/.test(node.className || ''));
  const all = controls.find(node => /all/i.test(node.innerText || '') || /all/.test(node.className || ''));
  if (all) { all.click(); return 1; }
  controls.forEach(node => node.click());
  return controls.length;
}'''

MARK_REAL = r'''() => {
  const node = [...document.querySelectorAll('.m-betslip-wrapper [class*="inside-btn"]')]
    .find(item => (item.innerText || '').trim().toUpperCase() === 'REAL');
  if (!node) return false;
  node.click();
  return true;
}'''

MARK_PLACE = r'''() => {
  document.querySelectorAll('[data-sports-ai-place]').forEach(node => node.removeAttribute('data-sports-ai-place'));
  const root = document.querySelector('.m-betslip-wrapper, .m-betslips, .m-betslip');
  if (!root) return 0;
  const hits = [...root.querySelectorAll('button, [role="button"]')].filter(node => /^place\s*bet$/i.test((node.innerText || '').trim()));
  if (hits.length === 1) hits[0].setAttribute('data-sports-ai-place', '1');
  return hits.length;
}'''

POPUP_STATE = r'''() => {
  const pops = [...document.querySelectorAll('.m-pop-main, .m-dialog, [role="dialog"], .es-dialog, .m-confirm, .af-dialog')]
    .filter(node => { const r = node.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
  const text = pops.map(node => node.innerText || '').join(' ').replace(/\s+/g, ' ').trim();
  document.querySelectorAll('[data-sports-ai-confirm]').forEach(node => node.removeAttribute('data-sports-ai-confirm'));
  const confirms = pops.flatMap(node => [...node.querySelectorAll('button, [role="button"]')])
    .filter(node => /^confirm$/i.test((node.innerText || '').trim()));
  if (confirms.length === 1) confirms[0].setAttribute('data-sports-ai-confirm', '1');
  // Only the dialog is read: a banner elsewhere on the page must never count as a placed bet.
  const ref = text.match(/(?:ticket|bet|order)\s*(?:id|no\.?|number)\s*[:#]?\s*([A-Z0-9]{6,})/i);
  return {
    text: text.slice(0, 600),
    confirm_buttons: confirms.length,
    shows_stake: /(?:^|[^\d])100(?:\.00)?(?![\d])/.test(text),
    success: /submission successful|bet (?:was )?(?:placed|successful)|successfully placed|bet successful/i.test(text),
    odds_changed: /odds (?:have )?changed|accept (?:odds )?changes|price changed/i.test(text),
    insufficient: /insufficient (?:balance|funds)/i.test(text),
    rejected: /(?:bet|submission) (?:failed|rejected)|not accepted|unavailable|suspended/i.test(text),
    login: /log\s*in to|please log\s*in|session (?:has )?expired/i.test(text),
    ref: ref ? ref[1] : null,
  };
}'''


async def _login_state(page):
    try:
        await page.wait_for_selector('.s-header, .m-header, header', timeout=20000)
    except Exception:
        pass
    await page.wait_for_timeout(2500)
    return await page.evaluate(LOGIN_STATE)


async def _launch(playwright, headless=True):
    browser = await playwright.chromium.launch(headless=headless)
    state = load_session()
    if state is None:
        await browser.close()
        raise SessionMissing('No SportyBet session is saved. Connect the account first.')
    context = await browser.new_context(viewport={'width': 1440, 'height': 900}, storage_state=state)
    return browser, context


class SessionMissing(RuntimeError):
    pass


class SessionExpired(RuntimeError):
    pass


# ---------------------------------------------------------------- login

def _interactive_login():
    """Visible window; the operator types credentials and any OTP. Nothing is typed for them."""
    from playwright.sync_api import sync_playwright
    from browser_slots import browser_slot
    try:
        with browser_slot('account-login', wait=60, ttl=LOGIN_WAIT_SECONDS + 120), sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=False)
            try:
                context = browser.new_context(viewport={'width': 1366, 'height': 860})
                page = context.new_page()
                page.goto(BASE_URL + HOME_PATH, wait_until='domcontentloaded', timeout=60000)
                deadline = datetime.now(timezone.utc) + timedelta(seconds=LOGIN_WAIT_SECONDS)
                state = None
                while datetime.now(timezone.utc) < deadline:
                    if page.is_closed():
                        break
                    page.wait_for_timeout(2000)
                    try:
                        state = page.evaluate(LOGIN_STATE)
                    except Exception:
                        state = None
                    if state and state.get('logged_in'):
                        break
                if not state or not state.get('logged_in'):
                    with connect() as connection:
                        _set_account(connection, 'NOT_CONNECTED', 'The login window closed or timed out before a signed-in page was seen.')
                    return
                page.wait_for_timeout(2000)
                save_session(context.storage_state())
                now = _now()
                with connect() as connection:
                    _set_account(connection, 'CONNECTED', 'Signed in by the operator. Session cookies saved encrypted.',
                                 now, session_saved_at=now.isoformat(), last_check_at=now.isoformat(), last_ok_at=now.isoformat(),
                                 balance=state.get('balance'), balance_currency=state.get('currency'),
                                 balance_observed_at=now.isoformat() if state.get('balance') is not None else None)
                    _event(connection, None, 'ACCOUNT_CONNECTED', 'SportyBet account connected by operator login', now)
            finally:
                browser.close()
    except Exception as error:
        with connect() as connection:
            _set_account(connection, 'ERROR', f'Login window failed: {error}')


def start_login():
    global _LOGIN_THREAD
    with _LOGIN_LOCK:
        if _LOGIN_THREAD and _LOGIN_THREAD.is_alive():
            return {'status': 'AWAITING_LOGIN', 'started': False}
        with connect() as connection:
            _set_account(connection, 'AWAITING_LOGIN',
                         f'A Chromium window is open on the server machine. Sign in to SportyBet there within {LOGIN_WAIT_SECONDS // 60} minutes. '
                         'Tick "Keep me signed in" so the session lasts.')
        _LOGIN_THREAD = threading.Thread(target=_interactive_login, name='sportybet-login', daemon=True)
        _LOGIN_THREAD.start()
        return {'status': 'AWAITING_LOGIN', 'started': True}


def disconnect(actor):
    forget_session()
    with connect() as connection:
        _set_account(connection, 'NOT_CONNECTED', f'Session removed by {actor}.', balance=None,
                     balance_currency=None, balance_observed_at=None)
        _event(connection, None, 'ACCOUNT_DISCONNECTED', f'SportyBet session removed by {actor}')


# ---------------------------------------------------------------- health, balance, history

async def check_session_async(capture_history=False):
    from playwright.async_api import async_playwright
    from browser_slots import async_browser_slot
    now = _now()
    try:
        async with async_browser_slot('account', wait=60), async_playwright() as playwright:
            browser, context = await _launch(playwright)
            try:
                page = await context.new_page()
                await page.goto(BASE_URL + HOME_PATH, wait_until='domcontentloaded', timeout=45000)
                state = await _login_state(page)
                if not state.get('logged_in'):
                    raise SessionExpired('SportyBet shows the login form. The saved session has expired.')
                history = None
                if capture_history:
                    await page.goto(BASE_URL + HISTORY_PATH, wait_until='domcontentloaded', timeout=45000)
                    await page.wait_for_timeout(4000)
                    history = (await page.locator('body').inner_text(timeout=10000))[:60000]
                save_session(await context.storage_state())
            finally:
                await browser.close()
    except SessionMissing as error:
        with connect() as connection:
            _set_account(connection, 'NOT_CONNECTED', str(error), now, last_check_at=now.isoformat())
        return {'status': 'NOT_CONNECTED', 'detail': str(error)}
    except SessionExpired as error:
        with connect() as connection:
            _set_account(connection, 'EXPIRED', str(error), now, last_check_at=now.isoformat())
            _event(connection, None, 'SESSION_EXPIRED', 'SportyBet session expired. Live execution waits for a new login.', now)
        return {'status': 'EXPIRED', 'detail': str(error)}
    except Exception as error:
        with connect() as connection:
            _set_account(connection, 'ERROR', f'Session check failed: {error}', now, last_check_at=now.isoformat())
        return {'status': 'ERROR', 'detail': str(error)}
    with connect() as connection:
        fields = {'last_check_at': now.isoformat(), 'last_ok_at': now.isoformat()}
        if state.get('balance') is not None:
            fields.update(balance=state['balance'], balance_currency=state.get('currency') or 'NGN',
                          balance_observed_at=now.isoformat())
        detail = 'Signed in.' + (' Balance is hidden on SportyBet (eye icon); it was not read.' if state.get('masked') else '')
        _set_account(connection, 'CONNECTED', detail, now, **fields)
        if history:
            connection.execute('INSERT INTO sports_ai_sportybet_history(id,captured_at,page,text) VALUES(?,?,?,?)',
                               (str(uuid.uuid4()), now.isoformat(), HISTORY_PATH, history))
            reconcile(connection, history, now)
    return {'status': 'CONNECTED', 'balance': state.get('balance'), 'history_captured': bool(history)}


def check_session(capture_history=False):
    return asyncio.run(check_session_async(capture_history))


def reconcile(connection, history_text, now=None):
    """Match placed or unclear submissions to Bet History by the SportyBet ticket reference."""
    current = _now(now)
    text = re.sub(r'\s+', ' ', history_text or '')
    updated = 0
    for order in connection.execute(
        "SELECT * FROM sports_ai_single_live_orders WHERE kind='SUBMISSION' AND status IN ('PLACED','UNKNOWN','SUBMITTING')",
    ).fetchall():
        ref = order['bookmaker_ref']
        if not ref or ref not in text:
            continue
        window = text[text.index(ref):text.index(ref) + 400]
        status = None
        if re.search(r'\bwon\b', window, re.I):
            status = 'SETTLED_WON'
        elif re.search(r'\blost\b', window, re.I):
            status = 'SETTLED_LOST'
        elif re.search(r'\b(void|cancel+ed|refund)', window, re.I):
            status = 'SETTLED_VOID'
        elif order['status'] != 'PLACED':
            status = 'PLACED'
        if status:
            connection.execute(
                'UPDATE sports_ai_single_live_orders SET status=?, reconciled_at=?, updated_at=? WHERE id=?',
                (status, current.isoformat(), current.isoformat(), order['id']),
            )
            _event(connection, order['bet_id'], 'RECONCILED', f'SportyBet ticket {ref} shown in Bet History as {status.replace("SETTLED_", "").lower()}', current)
            updated += 1
    return updated


# ---------------------------------------------------------------- authorization and mode

def active_authorization(connection, now=None):
    moment = _now(now).isoformat()
    row = connection.execute(
        """SELECT * FROM sports_ai_single_authorizations
           WHERE revoked_at IS NULL AND granted_at<=? AND expires_at>? ORDER BY granted_at DESC LIMIT 1""",
        (moment, moment),
    ).fetchone()
    return dict(row) if row else None


def rehearsals_passed(connection, now=None):
    since = (_now(now) - timedelta(days=7)).isoformat()
    return connection.execute(
        "SELECT COUNT(*) FROM sports_ai_single_live_orders WHERE kind='REHEARSAL' AND status='REHEARSED' AND created_at>=?",
        (since,),
    ).fetchone()[0]


def grant_authorization(connection, actor, acknowledgement, hours, now=None):
    current = _now(now)
    if (acknowledgement or '').strip() != ACKNOWLEDGEMENT:
        raise ValueError(f'Type the acknowledgement exactly: {ACKNOWLEDGEMENT}')
    if not 1 <= int(hours) <= MAX_AUTHORIZATION_HOURS:
        raise ValueError(f'Authorization lasts between 1 and {MAX_AUTHORIZATION_HOURS} hours')
    row = account(connection)
    if row['status'] != 'CONNECTED':
        raise ValueError('Connect the SportyBet account and pass a session check first')
    passed = rehearsals_passed(connection, current)
    if passed < REQUIRED_REHEARSALS:
        raise ValueError(f'{REQUIRED_REHEARSALS} successful live rehearsals are required first; {passed} recorded in the last 7 days')
    grant = {
        'id': str(uuid.uuid4()), 'granted_by': actor, 'acknowledgement': ACKNOWLEDGEMENT,
        'stake': STAKE, 'daily_count': DAILY_COUNT, 'daily_stake': DAILY_STAKE,
        'granted_at': current.isoformat(), 'expires_at': (current + timedelta(hours=int(hours))).isoformat(),
    }
    connection.execute(
        f"INSERT INTO sports_ai_single_authorizations({','.join(grant)}) VALUES({','.join('?' * len(grant))})",
        tuple(grant.values()),
    )
    _event(connection, None, 'AUTHORIZED', f'Real-money submission authorized by {actor} until {grant["expires_at"][:16]} UTC', current)
    return grant


def revoke_authorization(connection, actor, now=None):
    current = _now(now)
    count = connection.execute(
        'UPDATE sports_ai_single_authorizations SET revoked_at=?, revoked_by=? WHERE revoked_at IS NULL AND expires_at>?',
        (current.isoformat(), actor, current.isoformat()),
    ).rowcount
    _event(connection, None, 'AUTHORIZATION_REVOKED', f'Real-money authorization revoked by {actor}', current)
    return count


def execution_mode(connection):
    from single_betting import runtime
    mode = runtime(connection).get('execution_mode') or 'PAPER'
    return mode if mode in MODES else 'PAPER'


def set_execution_mode(connection, mode, actor, now=None):
    from single_betting import RUNTIME_KEY, _save_setting, runtime
    if mode not in MODES:
        raise ValueError('Unknown execution mode')
    if mode != 'PAPER' and account(connection)['status'] != 'CONNECTED':
        raise ValueError('Connect the SportyBet account before choosing a live mode')
    if mode == 'LIVE' and not active_authorization(connection, now):
        raise ValueError('LIVE needs an active real-money authorization')
    state = runtime(connection)
    state.update({'execution_mode': mode, 'mode_changed_by': actor, 'mode_changed_at': _now(now).isoformat()})
    _save_setting(connection, RUNTIME_KEY, state, now)
    _event(connection, None, 'MODE', f'Execution mode set to {mode} by {actor}', now)
    return state


def live_usage(connection, day):
    row = connection.execute(
        """SELECT COUNT(*) n, COALESCE(SUM(stake),0) staked FROM sports_ai_single_live_orders
           WHERE kind='SUBMISSION' AND local_date=? AND status NOT IN ('REJECTED','FAILED')""",
        (day,),
    ).fetchone()
    return int(row['n']), float(row['staked'])


def submission_gate(connection, now=None):
    """Every reason real money cannot be submitted right now. Empty means allowed."""
    current = _now(now)
    reasons = []
    from single_betting import runtime
    state = runtime(connection)
    if state.get('paused'):
        reasons.append('Kill switch is active')
    if execution_mode(connection) != 'LIVE':
        reasons.append('Execution mode is not LIVE')
    grant = active_authorization(connection, current)
    if not grant:
        reasons.append('No active real-money authorization')
    row = account(connection)
    if row['status'] != 'CONNECTED':
        reasons.append(f"SportyBet session is {row['status']}")
    count, staked = live_usage(connection, local_date(current))
    limit_count = min(DAILY_COUNT, (grant or {}).get('daily_count') or DAILY_COUNT)
    limit_stake = min(DAILY_STAKE, (grant or {}).get('daily_stake') or DAILY_STAKE)
    if count >= limit_count:
        reasons.append(f'Daily live limit reached ({count}/{limit_count})')
    if staked + STAKE > limit_stake:
        reasons.append(f'Daily live stake ceiling reached (₦{staked:,.0f}/₦{limit_stake:,.0f})')
    if connection.execute(
        "SELECT 1 FROM sports_ai_single_live_orders WHERE kind='SUBMISSION' AND status IN ('UNKNOWN','SUBMITTING') LIMIT 1",
    ).fetchone():
        reasons.append('An earlier submission has an unclear result and must be reconciled first')
    return reasons, grant


# ---------------------------------------------------------------- execution

def _order(connection, bet, kind, grant_id, now):
    order_id = str(uuid.uuid4())
    connection.execute(
        """INSERT INTO sports_ai_single_live_orders
           (id,bet_id,kind,status,local_date,stake,expected_odds,authorization_id,created_at,updated_at)
           VALUES(?,?,?,'PENDING',?,?,?,?,?,?)""",
        (order_id, bet['id'], kind, local_date(now), STAKE, bet['decimal_odds'], grant_id, now.isoformat(), now.isoformat()),
    )
    return order_id


def _finish_order(order_id, status, now=None, **fields):
    moment = _now(now).isoformat()
    columns = {'status': status, 'updated_at': moment, **fields}
    if 'checks_json' in columns and not isinstance(columns['checks_json'], str):
        columns['checks_json'] = json.dumps(columns['checks_json'], default=str)
    with connect() as connection:
        connection.execute(
            f"UPDATE sports_ai_single_live_orders SET {', '.join(f'{k}=?' for k in columns)} WHERE id=?",
            (*columns.values(), order_id),
        )


async def _prepare_in_account(page, bet):
    """Open the sport page in the signed-in context, add exactly this one selection, set ₦100."""
    from sportybet_booking import _open_sport, prepare_slip
    local = BASE_URL.startswith(('http://127.0.0.1', 'http://localhost'))
    await _open_sport(page, bet['sport'], f"{BASE_URL}/ng/sport/{bet['sport']}" if local else None)
    state = await _login_state(page)
    if not state.get('logged_in'):
        raise SessionExpired('SportyBet shows the login form. The saved session has expired.')
    slip = await page.evaluate(SLIP_STATE)
    if slip.get('present') and (slip.get('count') or 0) > 0:
        await page.evaluate(CLEAR_SLIP)
        await page.wait_for_timeout(1200)
        slip = await page.evaluate(SLIP_STATE)
        if (slip.get('count') or 0) > 0:
            return {'ok': False, 'code': 'SLIP_NOT_EMPTY', 'error': 'The account betslip already held selections that could not be cleared.', 'slip': slip}
    item = {'fixture_id': bet['fixture_id'], 'sport': bet['sport'], 'competition': bet['competition'],
            'home': bet['home'], 'away': bet['away'], 'market': bet['market'], 'selection': '',
            'decimal_odds': bet['decimal_odds']}
    picked = await prepare_slip(page, [item], book=False, max_move=MAX_PRICE_MOVE)
    if picked['status'] != 'SELECTED' or not picked.get('seen'):
        return {'ok': False, 'code': 'PRICE_OR_MARKET', 'error': picked.get('error') or 'The selection was not found at its price.', 'picked': picked}
    page_odds = float(picked['seen'][0].get('page_odds') or 0)
    from single_betting import MIN_EV
    ev = page_odds * float(bet['probability']) - 1
    if ev < MIN_EV:
        return {'ok': False, 'code': 'EDGE_GONE', 'error': f'At the displayed {page_odds:.2f} the expected value is {ev:+.3f}.', 'page_odds': page_odds}
    slip = await page.evaluate(SLIP_STATE)
    if not slip.get('real_active') and slip.get('sim_active'):
        await page.evaluate(MARK_REAL)
        await page.wait_for_timeout(800)
    stake = page.locator('.m-betslip-wrapper input.m-input, .m-betslips input.m-input').first
    await stake.fill('')
    await stake.type(str(int(STAKE)))
    await stake.press('Tab')
    await page.wait_for_timeout(1000)
    slip = await page.evaluate(SLIP_STATE)
    checks = {
        'one_selection': slip.get('count') == 1,
        'teams_on_slip': all(token.casefold() in (slip.get('text') or '').casefold()
                             for token in (bet['home'].split()[0], bet['away'].split()[0])),
        'real_mode': bool(slip.get('real_active')) and not slip.get('sim_active'),
        'stake_100': slip.get('stake') == STAKE and (slip.get('total_stake') in (None, STAKE)),
        'potential_win': slip.get('potential_win') is not None and abs(slip['potential_win'] - STAKE * page_odds) <= max(1.0, STAKE * page_odds * 0.01),
        'single_place_button': slip.get('place_buttons') == 1 and not slip.get('place_disabled'),
        'balance_covers_stake': state.get('balance') is None or state['balance'] >= STAKE,
    }
    return {'ok': all(checks.values()), 'code': 'CHECKS' if not all(checks.values()) else 'READY',
            'checks': checks, 'slip': slip, 'page_odds': page_odds, 'ev': ev, 'balance': state.get('balance')}


async def execute_async(bet, kind, now=None):
    """Rehearse or submit one queued single. Returns (bet_outcome, order_status, detail)."""
    from playwright.async_api import async_playwright
    from browser_slots import async_browser_slot
    current = _now(now)
    grant_id = None
    with connect() as connection:
        if kind == 'SUBMISSION':
            reasons, grant = submission_gate(connection, current)
            if reasons:
                return 'WAIT', None, '; '.join(reasons)
            grant_id = grant['id']
        if float(bet['stake']) != STAKE:
            return 'CANCEL', None, 'Stake is not the fixed ₦100'
        order_id = _order(connection, bet, kind, grant_id, current)
    shot = evidence_dir() / f'{order_id}.png'
    clicked = False
    try:
        async with async_browser_slot('account', wait=90), async_playwright() as playwright:
            browser, context = await _launch(playwright)
            try:
                page = await context.new_page()
                ready = await _prepare_in_account(page, bet)
                await page.screenshot(path=str(shot), full_page=False)
                if not ready['ok']:
                    _finish_order(order_id, 'REJECTED' if ready['code'] in ('PRICE_OR_MARKET', 'EDGE_GONE') else 'FAILED',
                                  checks_json=ready, page_odds=ready.get('page_odds'), error=ready.get('error') or ready['code'],
                                  evidence_path=str(shot))
                    return 'CANCEL', ready['code'], ready.get('error') or f"Betslip checks failed: {[k for k, v in ready.get('checks', {}).items() if not v]}"
                if kind == 'REHEARSAL':
                    await page.evaluate(CLEAR_SLIP)
                    save_session(await context.storage_state())
                    _finish_order(order_id, 'REHEARSED', checks_json=ready, page_odds=ready['page_odds'], evidence_path=str(shot))
                    return 'PAPER_PLACED', 'REHEARSED', f"Rehearsed in account at {ready['page_odds']:.2f}; Place Bet not clicked"
                # ---- real money: last gate, mark SUBMITTING, click once
                with connect() as connection:
                    reasons, _grant = submission_gate(connection, _now())
                if reasons:
                    _finish_order(order_id, 'FAILED', checks_json=ready, error='; '.join(reasons), evidence_path=str(shot))
                    return 'WAIT', 'FAILED', '; '.join(reasons)
                if await page.evaluate(MARK_PLACE) != 1:
                    _finish_order(order_id, 'FAILED', checks_json=ready, error='Place Bet control missing or ambiguous', evidence_path=str(shot))
                    return 'CANCEL', 'FAILED', 'Place Bet control missing or ambiguous'
                _finish_order(order_id, 'SUBMITTING', checks_json=ready, page_odds=ready['page_odds'],
                              submitted_at=_now().isoformat(), evidence_path=str(shot))
                clicked = True
                await page.locator('[data-sports-ai-place="1"]').click()
                outcome = None
                for _ in range(30):
                    await page.wait_for_timeout(1000)
                    popup = await page.evaluate(POPUP_STATE)
                    if popup['odds_changed'] or popup['insufficient'] or popup['login'] or popup['rejected']:
                        outcome = popup
                        break
                    if popup['confirm_buttons'] == 1 and not (outcome or {}).get('confirmed'):
                        # Confirm only a dialog that states the ₦100 stake. Anything else is left
                        # unconfirmed and treated as unclear (engine pauses; nothing is retried).
                        if not popup['shows_stake']:
                            outcome = {**popup, 'unexpected_confirm': True}
                            break
                        await page.locator('[data-sports-ai-confirm="1"]').click()
                        outcome = {'confirmed': True}
                        continue
                    if popup['success']:
                        outcome = popup
                        break
                await page.screenshot(path=str(shot), full_page=False)
                save_session(await context.storage_state())
            finally:
                await browser.close()
    except SessionExpired as error:
        _finish_order(order_id, 'UNKNOWN' if clicked else 'FAILED', error=str(error), evidence_path=str(shot) if shot.exists() else None)
        with connect() as connection:
            _set_account(connection, 'EXPIRED', str(error))
        return ('UNKNOWN' if clicked else 'WAIT'), 'SESSION_EXPIRED', str(error)
    except Exception as error:
        _finish_order(order_id, 'UNKNOWN' if clicked else 'FAILED', error=str(error)[:500],
                      evidence_path=str(shot) if shot.exists() else None)
        return ('UNKNOWN' if clicked else 'CANCEL'), 'ERROR', str(error)[:300]
    outcome = outcome or {}
    if outcome.get('success'):
        _finish_order(order_id, 'PLACED', bookmaker_ref=outcome.get('ref'))
        return 'LIVE_PLACED', 'PLACED', f"SportyBet accepted the single{(' – ticket ' + outcome['ref']) if outcome.get('ref') else ''}"
    if outcome.get('odds_changed') or outcome.get('insufficient') or outcome.get('rejected'):
        reason = 'odds changed (not accepted)' if outcome.get('odds_changed') else 'insufficient balance' if outcome.get('insufficient') else 'rejected by SportyBet'
        _finish_order(order_id, 'REJECTED', error=reason + ': ' + outcome.get('text', '')[:300])
        return 'CANCEL', 'REJECTED', reason
    _finish_order(order_id, 'UNKNOWN', error='No clear confirmation was seen after Place Bet: ' + (outcome.get('text') or '')[:300])
    return 'UNKNOWN', 'UNKNOWN', 'No clear confirmation after Place Bet; engine paused for reconciliation'


def recover_interrupted(connection, now=None):
    """A process that died between click and confirmation leaves SUBMITTING. Treat it as unclear."""
    current = _now(now)
    stale = (current - timedelta(minutes=5)).isoformat()
    rows = connection.execute(
        "SELECT id, bet_id FROM sports_ai_single_live_orders WHERE status='SUBMITTING' AND updated_at<?", (stale,),
    ).fetchall()
    for row in rows:
        connection.execute("UPDATE sports_ai_single_live_orders SET status='UNKNOWN', error=?, updated_at=? WHERE id=?",
                           ('Interrupted after Place Bet was clicked; reconcile from Bet History.', current.isoformat(), row['id']))
    if rows:
        from single_betting import set_paused
        set_paused(connection, True, 'live-recovery', current)
    return len(rows)


def run_once(now=None):
    """One live-lane step: recover, keep the session checked, then execute the head of the queue."""
    from single_betting import next_for_execution, apply_execution
    current = _now(now)
    with connect() as connection:
        # The worker lane can start while the API process is still applying migration 051
        # (it backs up the database first). Wait quietly instead of failing every 30 seconds.
        if not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sports_ai_single_live_orders'",
        ).fetchone():
            return {'status': 'paper', 'reason': 'waiting for migration 051'}
        recover_interrupted(connection, current)
        mode = execution_mode(connection)
        row = account(connection)
    if mode == 'PAPER':
        return {'status': 'paper'}
    if row['status'] in ('NOT_CONNECTED', 'AWAITING_LOGIN'):
        return {'status': 'waiting_for_login'}
    if not session_fresh(row, current):
        checked = check_session(capture_history=True)
        if checked['status'] != 'CONNECTED':
            return {'status': 'session_' + checked['status'].lower()}
    with connect() as connection:
        bet = next_for_execution(connection, current)
        if not bet:
            return {'status': 'idle'}
        kind = 'REHEARSAL'
        if mode == 'LIVE':
            reasons, _grant = submission_gate(connection, current)
            if not reasons:
                kind = 'SUBMISSION'
            elif any('authorization' in r or 'LIVE' in r for r in reasons):
                kind = 'REHEARSAL'
            else:
                return {'status': 'gated', 'reasons': reasons}
    outcome, order_status, detail = asyncio.run(execute_async(bet, kind, current))
    with connect() as connection:
        apply_execution(connection, bet, outcome, order_status, detail, kind)
    return {'status': outcome.lower(), 'kind': kind, 'detail': detail}


# ---------------------------------------------------------------- read model

def status_view(connection, now=None):
    current = _now(now)
    row = account(connection)
    grant = active_authorization(connection, current)
    reasons, _ = submission_gate(connection, current)
    count, staked = live_usage(connection, local_date(current))
    orders = [dict(r) for r in connection.execute(
        """SELECT o.*, b.home, b.away, b.selection, b.sport FROM sports_ai_single_live_orders o
           LEFT JOIN sports_ai_single_bets b ON b.id=o.bet_id ORDER BY o.created_at DESC LIMIT 20""",
    ).fetchall()]
    for order in orders:
        order['checks'] = json.loads(order.pop('checks_json') or '{}').get('checks')
        order['evidence'] = bool(order.pop('evidence_path', None))
    exceptions = connection.execute(
        "SELECT COUNT(*) FROM sports_ai_single_live_orders WHERE status IN ('UNKNOWN','SUBMITTING','FAILED') AND local_date=?",
        (local_date(current),),
    ).fetchone()[0]
    history = connection.execute('SELECT captured_at FROM sports_ai_sportybet_history ORDER BY captured_at DESC LIMIT 1').fetchone()
    observed = _parse(row.get('balance_observed_at'))
    return {
        'account': {
            'status': row['status'], 'detail': row.get('detail'), 'session_saved_at': row.get('session_saved_at'),
            'last_check_at': row.get('last_check_at'), 'last_ok_at': row.get('last_ok_at'),
            'session_fresh': session_fresh(row, current),
            'login_window_open': bool(_LOGIN_THREAD and _LOGIN_THREAD.is_alive()),
        },
        'balance': {
            'value': row.get('balance'), 'currency': row.get('balance_currency'), 'observed_at': row.get('balance_observed_at'),
            'status': 'NOT_CONNECTED' if row.get('balance') is None else ('SYNCED' if observed and current - observed <= timedelta(minutes=60) else 'STALE'),
            'source': 'Read from the signed-in SportyBet header' if row.get('balance') is not None else None,
        },
        'execution': {
            'mode': execution_mode(connection), 'modes': list(MODES),
            'authorization': grant, 'acknowledgement': ACKNOWLEDGEMENT,
            'rehearsals_passed': rehearsals_passed(connection, current), 'rehearsals_required': REQUIRED_REHEARSALS,
            'submission_allowed': not reasons, 'blocked_by': reasons,
            'live_today': {'count': count, 'staked': staked, 'limit_count': DAILY_COUNT, 'limit_stake': DAILY_STAKE},
        },
        'reconciliation': {'last_history_capture': history['captured_at'] if history else None,
                           'unclear': connection.execute(
                               "SELECT COUNT(*) FROM sports_ai_single_live_orders WHERE status IN ('UNKNOWN','SUBMITTING')").fetchone()[0]},
        'exceptions_today': exceptions,
        'orders': orders,
    }


# ---------------------------------------------------------------- router

class GrantIn(BaseModel):
    acknowledgement: str = Field(min_length=10, max_length=200)
    hours: int = Field(ge=1, le=MAX_AUTHORIZATION_HOURS)


class ModeIn(BaseModel):
    mode: str


class ResolveIn(BaseModel):
    order_id: str
    outcome: str  # PLACED or NOT_PLACED, as verified by the operator in SportyBet Bet History
    bookmaker_ref: str | None = Field(default=None, max_length=40)


def build_account_router(auth, require_admin_key):
    router = APIRouter(prefix='/api/sports-ai/single-betting/account', tags=['Single Betting AI'])

    def admin(x_sports_admin_key):
        require_admin_key(x_sports_admin_key)

    @router.get('')
    def get_status(actor: str = Depends(auth)):
        with connect() as connection:
            return status_view(connection)

    @router.post('/connect')
    def post_connect(actor: str = Depends(auth), x_sports_admin_key: str | None = Header(default=None)):
        admin(x_sports_admin_key)
        return start_login()

    @router.post('/disconnect')
    def post_disconnect(actor: str = Depends(auth), x_sports_admin_key: str | None = Header(default=None)):
        admin(x_sports_admin_key)
        disconnect(actor)
        with connect() as connection:
            revoke_authorization(connection, actor)
            set_execution_mode(connection, 'PAPER', actor)
        return {'status': 'NOT_CONNECTED'}

    @router.post('/check')
    def post_check(actor: str = Depends(auth)):
        return check_session(capture_history=True)

    @router.post('/mode')
    def post_mode(body: ModeIn, actor: str = Depends(auth), x_sports_admin_key: str | None = Header(default=None)):
        admin(x_sports_admin_key)
        try:
            with connect() as connection:
                return set_execution_mode(connection, body.mode, actor)
        except ValueError as error:
            raise HTTPException(409, str(error))

    @router.post('/authorize')
    def post_authorize(body: GrantIn, actor: str = Depends(auth), x_sports_admin_key: str | None = Header(default=None)):
        admin(x_sports_admin_key)
        try:
            with connect() as connection:
                return grant_authorization(connection, actor, body.acknowledgement, body.hours)
        except ValueError as error:
            raise HTTPException(409, str(error))

    @router.post('/revoke')
    def post_revoke(actor: str = Depends(auth), x_sports_admin_key: str | None = Header(default=None)):
        admin(x_sports_admin_key)
        with connect() as connection:
            revoked = revoke_authorization(connection, actor)
            if execution_mode(connection) == 'LIVE':
                set_execution_mode(connection, 'LIVE_REHEARSAL', actor)
        return {'revoked': revoked}

    @router.post('/resolve')
    def post_resolve(body: ResolveIn, actor: str = Depends(auth), x_sports_admin_key: str | None = Header(default=None)):
        """The operator records what SportyBet Bet History shows for an unclear submission."""
        admin(x_sports_admin_key)
        if body.outcome not in ('PLACED', 'NOT_PLACED'):
            raise HTTPException(422, 'outcome must be PLACED or NOT_PLACED')
        with connect() as connection:
            order = connection.execute("SELECT * FROM sports_ai_single_live_orders WHERE id=? AND status IN ('UNKNOWN','SUBMITTING')",
                                       (body.order_id,)).fetchone()
            if not order:
                raise HTTPException(404, 'No unclear submission with that id')
            status = 'PLACED' if body.outcome == 'PLACED' else 'REJECTED'
            now = _now().isoformat()
            connection.execute(
                'UPDATE sports_ai_single_live_orders SET status=?, bookmaker_ref=COALESCE(?,bookmaker_ref), reconciled_at=?, updated_at=?, error=? WHERE id=?',
                (status, body.bookmaker_ref, now, now, f'Resolved by {actor} from SportyBet Bet History', body.order_id),
            )
            if status == 'REJECTED':
                connection.execute("UPDATE sports_ai_single_bets SET status='CANCELLED', reason=? WHERE id=? AND status='PLACED'",
                                   ('Operator confirmed SportyBet did not accept the submission.', order['bet_id']))
            _event(connection, order['bet_id'], 'RECONCILED', f'Unclear submission resolved as {body.outcome} by {actor}')
        return {'status': status}

    return router
