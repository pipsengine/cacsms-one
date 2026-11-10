"""Single Betting AI: autonomous PAPER singles fed by the shared 9-stage pipeline.

Ported from the cacsms-single-engine integration package onto the host's
sqlite3 + migration conventions. The engine never runs its own discovery,
odds collection or model; it consumes current QUALIFIED decisions from the
qualification stage, the latest stored SportyBet quote, and verified results.

Rules (fixed, not configurable from the browser):
- one selection per bet, ₦100 fixed stake
- 20–25 bets per Africa/Lagos day is a target, not a minimum; 25 is a hard cap
- ₦2,500 daily stake cap
- one live single per fixture (correlation rule)
- fresh price, kickoff at least 60 s away, expected value >= MIN_EV

Every record is a paper record. Real-money submission is not implemented and
the API answers 403. No SportyBet balance or ticket reference is invented.
"""
import json
import math
import os
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException

from db import connect

STAKE = 100.0
DAILY_TARGET = (20, 25)
DAILY_LIMIT = 25
DAILY_STAKE_LIMIT = 2500.0
MIN_EV = float(os.environ.get('SINGLE_MIN_EXPECTED_VALUE', '0.03'))
MIN_SECONDS_TO_START = 60
PLACE_PER_TICK = 2
TICK_SECONDS = 30
WAT = timezone(timedelta(hours=1), 'WAT')  # Africa/Lagos has no daylight saving.
RUNTIME_KEY = 'single_betting_runtime'
STATUS_KEY = 'single_betting_status'
OPEN_FIXTURE = ('scheduled', 'not_started')
COUNTED = ('PLACED', 'WON', 'LOST', 'VOID')
SETTLED = ('WON', 'LOST', 'VOID')

LABELS = {
    'HOME': 'Home Win (1)', 'DRAW': 'Draw (X)', 'AWAY': 'Away Win (2)',
    'HOME_OR_DRAW': 'Double Chance 1X', 'DRAW_OR_AWAY': 'Double Chance X2', 'HOME_OR_AWAY': 'Double Chance 12',
    'BTTS_YES': 'Both Teams Score – Yes', 'BTTS_NO': 'Both Teams Score – No',
    'MONEYLINE_HOME': 'Home Win', 'MONEYLINE_AWAY': 'Away Win',
}


def _now(now=None):
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc)


def _parse(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def local_date(moment):
    return _now(moment).astimezone(WAT).date().isoformat()


def selection_label(market):
    name = str(market or '').upper()
    if name in LABELS:
        return LABELS[name]
    for prefix, label in (('OVER_', 'Over'), ('UNDER_', 'Under')):
        if name.startswith(prefix):
            line = name[len(prefix):].replace('_', '.')
            return f'{label} {line}'
    return name.replace('_', ' ').title() or 'Selection'


def evaluate(odds, probability, observed_at, starts_at, now=None, max_age_minutes=15, min_ev=None):
    """Deterministic eligibility. A probability is an estimate, never a certainty."""
    current = _now(now)
    floor = MIN_EV if min_ev is None else min_ev
    reasons = []
    try:
        odds, probability = float(odds), float(probability)
    except (TypeError, ValueError):
        return {'eligible': False, 'ev': None, 'reasons': ['MISSING_PRICE_OR_PROBABILITY']}
    if not 1 < odds <= 1000:
        reasons.append('INVALID_ODDS')
    if not 0 < probability < 1:
        reasons.append('INVALID_PROBABILITY')
    observed, kickoff = _parse(observed_at), _parse(starts_at)
    if observed is None:
        reasons.append('PRICE_TIMESTAMP_MISSING')
    else:
        age = (current - observed).total_seconds()
        if age < -60:
            reasons.append('FUTURE_PRICE')
        if age > max_age_minutes * 60:
            reasons.append('STALE_ODDS')
    if kickoff is None or (kickoff - current).total_seconds() < MIN_SECONDS_TO_START:
        reasons.append('EVENT_TOO_CLOSE')
    ev = odds * probability - 1
    if ev < floor:
        reasons.append('INSUFFICIENT_EDGE')
    return {'eligible': not reasons, 'ev': round(ev, 6), 'reasons': reasons}


def capacity(today_count, today_staked):
    by_count = DAILY_LIMIT - int(today_count)
    by_stake = int((DAILY_STAKE_LIMIT - float(today_staked)) // STAKE)
    return max(0, min(by_count, by_stake))


# ---------------------------------------------------------------- settings

def _setting(connection, key, fallback):
    row = connection.execute('SELECT value_json FROM sports_ai_settings WHERE key=?', (key,)).fetchone()
    if not row:
        return dict(fallback)
    try:
        value = json.loads(row['value_json'])
    except (TypeError, json.JSONDecodeError):
        return dict(fallback)
    return value if isinstance(value, dict) else dict(fallback)


def _save_setting(connection, key, value, now):
    connection.execute(
        """INSERT INTO sports_ai_settings(key,value_json,updated_at) VALUES(?,?,?)
           ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at""",
        (key, json.dumps(value), _now(now).isoformat()),
    )


def runtime(connection):
    return _setting(connection, RUNTIME_KEY, {'paused': False})


def set_paused(connection, paused, actor, now=None):
    state = runtime(connection)
    state.update({'paused': bool(paused), 'changed_by': actor, 'changed_at': _now(now).isoformat()})
    _save_setting(connection, RUNTIME_KEY, state, now)
    _event(connection, None, 'PAUSED' if paused else 'RESUMED',
           f'Single betting {"paused (kill switch)" if paused else "resumed"} by {actor}', now)
    return state


def _event(connection, bet_id, action, detail, now=None):
    connection.execute(
        'INSERT INTO sports_ai_single_events(id,bet_id,action,detail,created_at) VALUES(?,?,?,?,?)',
        (str(uuid.uuid4()), bet_id, action, detail[:500], _now(now).isoformat()),
    )


def _max_odds_age(connection):
    policy = _setting(connection, 'qualification_policy', {})
    try:
        return max(1, int(policy.get('odds_max_age_minutes') or 15))
    except (TypeError, ValueError):
        return 15


# ---------------------------------------------------------------- selection

def _latest_quote(connection, fixture_id, market):
    return connection.execute(
        """SELECT p.probability, q.decimal_odds, q.observed_at
           FROM sports_ai_value_opportunities o
           JOIN sports_ai_engine_predictions p ON p.id=o.prediction_id
           JOIN sports_ai_odds_snapshots q ON q.id=o.odds_id
           WHERE p.fixture_id=? AND upper(p.market)=upper(?) AND q.bookmaker='SportyBet' AND q.decimal_odds>1
           ORDER BY q.observed_at DESC LIMIT 1""",
        (fixture_id, market),
    ).fetchone()


def load_candidates(connection, now=None):
    """Current QUALIFIED, bookable decisions with a fresh SportyBet price and positive edge."""
    current = _now(now)
    max_age = _max_odds_age(connection)
    rows = connection.execute(
        """SELECT d.id AS decision_id, d.prediction_id, d.fixture_id, d.sport, d.competition, d.market,
                  d.evidence_json, d.rank_score, f.home, f.away, f.starts_at, f.status AS fixture_status
           FROM sports_ai_qualification_decisions d
           JOIN sports_ai_fixtures f ON f.id=d.fixture_id
           WHERE d.state='QUALIFIED' AND f.starts_at>?
             AND EXISTS (SELECT 1 FROM json_each(d.products_json) WHERE value='booking')
             AND NOT EXISTS (SELECT 1 FROM sports_ai_single_bets b
                             WHERE b.fixture_id=d.fixture_id AND b.status!='CANCELLED')""",
        (current.isoformat(),),
    ).fetchall()
    best = {}
    rejected = {}
    for row in rows:
        if row['fixture_status'] not in OPEN_FIXTURE:
            continue
        quote = _latest_quote(connection, row['fixture_id'], row['market'])
        try:
            evidence = json.loads(row['evidence_json'] or '{}')
        except json.JSONDecodeError:
            evidence = {}
        probability = evidence.get('probability')
        if probability is None and quote is not None:
            probability = quote['probability']
        if quote is None:
            rejected['NO_SPORTYBET_PRICE'] = rejected.get('NO_SPORTYBET_PRICE', 0) + 1
            continue
        verdict = evaluate(quote['decimal_odds'], probability, quote['observed_at'], row['starts_at'], current, max_age)
        if not verdict['eligible']:
            for reason in verdict['reasons']:
                rejected[reason] = rejected.get(reason, 0) + 1
            continue
        item = {
            'decision_id': row['decision_id'], 'prediction_id': row['prediction_id'],
            'fixture_id': row['fixture_id'], 'sport': row['sport'], 'competition': row['competition'] or '',
            'home': row['home'], 'away': row['away'], 'market': str(row['market']).upper(),
            'selection': selection_label(row['market']), 'decimal_odds': float(quote['decimal_odds']),
            'probability': float(probability), 'expected_value': verdict['ev'],
            'starts_at': row['starts_at'], 'odds_observed_at': quote['observed_at'],
        }
        held = best.get(row['fixture_id'])
        if held is None or item['expected_value'] > held['expected_value']:
            best[row['fixture_id']] = item
    eligible = sorted(best.values(), key=lambda i: (-i['expected_value'], i['starts_at'], i['fixture_id']))
    return {'eligible': eligible, 'rejected': rejected, 'max_odds_age_minutes': max_age}


def _day_usage(connection, day):
    row = connection.execute(
        f"""SELECT COUNT(*) AS n, COALESCE(SUM(stake),0) AS staked FROM sports_ai_single_bets
            WHERE local_date=? AND status IN ({','.join('?' * (len(COUNTED) + 1))})""",
        (day, *COUNTED, 'QUEUED'),
    ).fetchone()
    return int(row['n']), float(row['staked'])


def _recheck(connection, bet, current, max_age):
    from qualification_engine import selection_is_qualified
    if not selection_is_qualified(connection, bet['fixture_id'], bet['market']):
        return None, 'The selection no longer has a current QUALIFIED decision.'
    quote = _latest_quote(connection, bet['fixture_id'], bet['market'])
    if quote is None:
        return None, 'No SportyBet price is stored for this selection.'
    verdict = evaluate(quote['decimal_odds'], bet['probability'], quote['observed_at'], bet['starts_at'], current, max_age)
    if not verdict['eligible']:
        return None, 'Re-validation failed: ' + ', '.join(verdict['reasons'])
    return {'decimal_odds': float(quote['decimal_odds']), 'observed_at': quote['observed_at'], 'ev': verdict['ev']}, ''


def _fixture(bet):
    return f"{bet['home']} vs {bet['away']}"


def settle(connection, now=None):
    """Settle placed paper singles from verified final results."""
    from rollover_engine import _outcome
    current = _now(now)
    settled = 0
    for bet in connection.execute(
        """SELECT b.*, r.home_score, r.away_score FROM sports_ai_single_bets b
           JOIN sports_ai_results r ON r.fixture_id=b.fixture_id AND r.status='final'
           WHERE b.status='PLACED'""",
    ).fetchall():
        outcome = _outcome(bet['sport'], bet['market'], int(bet['home_score']), int(bet['away_score']))
        if outcome is None:
            continue
        status = {'WON': 'WON', 'LOST': 'LOST'}.get(outcome, 'VOID')
        payout = round(bet['stake'] * bet['decimal_odds'], 2) if status == 'WON' else (bet['stake'] if status == 'VOID' else 0.0)
        connection.execute(
            """UPDATE sports_ai_single_bets SET status=?, payout=?, profit=?, settled_at=?
               WHERE id=? AND status='PLACED'""",
            (status, payout, round(payout - bet['stake'], 2), current.isoformat(), bet['id']),
        )
        _event(connection, bet['id'], 'SETTLED',
               f"Settled {status.lower()} – {_fixture(bet)} {bet['home_score']}-{bet['away_score']} (₦{payout - bet['stake']:+,.0f})", current)
        settled += 1
    return settled


def run_cycle(connection, now=None, candidates=None):
    """One autonomous pass: settle, re-validate the queue, place, refill. Paper only."""
    current = _now(now)
    day = local_date(current)
    max_age = _max_odds_age(connection)
    result = {'settled': settle(connection, current), 'placed': 0, 'queued': 0, 'cancelled': 0,
              'mode': 'PAPER', 'places_wager': False}
    state = runtime(connection)
    if state.get('paused'):
        result['status'] = 'paused'
        _save_setting(connection, STATUS_KEY, {**result, 'last_run': current.isoformat(), 'eligible': 0, 'rejected': {}}, current)
        return result

    # Re-validate the queue and place the next PLACE_PER_TICK entries. In a live mode the
    # account lane (sportybet_account.run_once) places the head of the queue instead.
    live_mode = (state.get('execution_mode') or 'PAPER') != 'PAPER'
    queued = connection.execute(
        "SELECT * FROM sports_ai_single_bets WHERE status='QUEUED' ORDER BY created_at, id",
    ).fetchall()
    for bet in queued:
        fresh, reason = _recheck(connection, bet, current, max_age)
        if fresh is None:
            connection.execute(
                "UPDATE sports_ai_single_bets SET status='CANCELLED', reason=? WHERE id=? AND status='QUEUED'",
                (reason, bet['id']),
            )
            _event(connection, bet['id'], 'CANCELLED', f'Removed from queue – {_fixture(bet)}: {reason}', current)
            result['cancelled'] += 1
            continue
        if live_mode or result['placed'] >= PLACE_PER_TICK:
            continue
        count, staked = _day_usage(connection, day)
        if bet['local_date'] != day and capacity(count, staked) <= 0:
            continue
        connection.execute(
            """UPDATE sports_ai_single_bets
               SET status='PLACED', decimal_odds=?, odds_observed_at=?, expected_value=?, placed_at=?, local_date=?
               WHERE id=? AND status='QUEUED'""",
            (fresh['decimal_odds'], fresh['observed_at'], fresh['ev'], current.isoformat(), day, bet['id']),
        )
        _event(connection, bet['id'], 'PLACED',
               f"Paper bet placed – {_fixture(bet)} {bet['selection']} @ {fresh['decimal_odds']:.2f} (₦{bet['stake']:,.0f})", current)
        result['placed'] += 1

    # Refill the queue within the daily caps.
    pool = load_candidates(connection, current) if candidates is None else candidates
    count, staked = _day_usage(connection, day)
    room = capacity(count, staked)
    for item in pool['eligible'][:room]:
        bet_id = str(uuid.uuid4())
        cursor = connection.execute(
            """INSERT OR IGNORE INTO sports_ai_single_bets
               (id,decision_id,prediction_id,fixture_id,sport,competition,home,away,market,selection,decimal_odds,
                probability,expected_value,stake,mode,status,local_date,starts_at,odds_observed_at,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,'PAPER','QUEUED',?,?,?,?)""",
            (bet_id, item.get('decision_id'), item.get('prediction_id'), item['fixture_id'], item['sport'],
             item.get('competition', ''), item['home'], item['away'], item['market'], item['selection'],
             item['decimal_odds'], item['probability'], item['expected_value'], STAKE, day, item['starts_at'],
             item.get('odds_observed_at'), current.isoformat()),
        )
        if cursor.rowcount:
            result['queued'] += 1
            _event(connection, bet_id, 'QUEUED',
                   f"Selection qualified – {_fixture(item)} {item['selection']} @ {item['decimal_odds']:.2f}, EV {item['expected_value']:+.3f}", current)
    result['status'] = 'complete'
    _save_setting(connection, STATUS_KEY, {
        **result, 'last_run': current.isoformat(), 'eligible': len(pool['eligible']),
        'rejected': pool.get('rejected', {}),
        'freshest_price': max((i.get('odds_observed_at') or '' for i in pool['eligible']), default=None) or None,
    }, current)
    return result


# ---------------------------------------------------------------- live execution hand-off

def next_for_execution(connection, now=None):
    """Head of the queue after re-validation, within today's caps. Failing entries are cancelled."""
    current = _now(now)
    max_age = _max_odds_age(connection)
    for bet in connection.execute(
        "SELECT * FROM sports_ai_single_bets WHERE status='QUEUED' ORDER BY created_at, id",
    ).fetchall():
        fresh, reason = _recheck(connection, bet, current, max_age)
        if fresh is None:
            connection.execute(
                "UPDATE sports_ai_single_bets SET status='CANCELLED', reason=? WHERE id=? AND status='QUEUED'",
                (reason, bet['id']),
            )
            _event(connection, bet['id'], 'CANCELLED', f'Removed from queue – {_fixture(bet)}: {reason}', current)
            continue
        day = local_date(current)
        count, staked = _day_usage(connection, day)
        if bet['local_date'] != day and capacity(count, staked) <= 0:
            return None
        item = dict(bet)
        item.update(decimal_odds=fresh['decimal_odds'], odds_observed_at=fresh['observed_at'], expected_value=fresh['ev'])
        return item
    return None


def apply_execution(connection, bet, outcome, order_status, detail, kind, now=None):
    """Record what the account lane did. A live result never rewrites the paper mode column."""
    current = _now(now)
    day = local_date(current)
    if outcome in ('PAPER_PLACED', 'LIVE_PLACED', 'UNKNOWN'):
        # An unclear submission counts as placed: money may have left the account.
        connection.execute(
            """UPDATE sports_ai_single_bets
               SET status='PLACED', decimal_odds=?, odds_observed_at=?, expected_value=?, placed_at=?, local_date=?, reason=?
               WHERE id=? AND status='QUEUED'""",
            (bet['decimal_odds'], bet.get('odds_observed_at'), bet['expected_value'], current.isoformat(), day,
             detail[:300], bet['id']),
        )
        action = {'PAPER_PLACED': 'REHEARSED', 'LIVE_PLACED': 'LIVE_PLACED', 'UNKNOWN': 'EXCEPTION'}[outcome]
        _event(connection, bet['id'], action, f"{_fixture(bet)} {bet['selection']} – {detail}", current)
        if outcome == 'UNKNOWN':
            set_paused(connection, True, 'live-execution', current)
    elif outcome == 'CANCEL':
        connection.execute(
            "UPDATE sports_ai_single_bets SET status='CANCELLED', reason=? WHERE id=? AND status='QUEUED'",
            (f'{kind.title()} stopped: {detail}'[:300], bet['id']),
        )
        _event(connection, bet['id'], 'CANCELLED', f"{_fixture(bet)} – {kind.lower()} stopped: {detail}", current)
    else:  # WAIT: session or gate not ready; the bet stays queued
        last = connection.execute(
            'SELECT action, detail FROM sports_ai_single_events WHERE bet_id=? ORDER BY created_at DESC LIMIT 1', (bet['id'],),
        ).fetchone()
        message = f"{_fixture(bet)} waiting – {detail}"
        if not last or last['action'] != 'WAITING' or last['detail'] != message[:500]:
            _event(connection, bet['id'], 'WAITING', message, current)


def _account_balance(connection, current):
    """The balance exactly as last read from the signed-in SportyBet header, with its time."""
    from sportybet_account import status_view
    try:
        view = status_view(connection, current)
    except Exception:
        return {'value': None, 'status': 'NOT_CONNECTED', 'observed_at': None,
                'note': 'No SportyBet account is connected. A balance is never simulated.'}
    balance = view['balance']
    note = {'SYNCED': 'Read from your signed-in SportyBet account.',
            'STALE': 'Last value read from SportyBet; the session has not been checked recently.',
            'NOT_CONNECTED': 'No SportyBet account is connected. A balance is never simulated.'}[balance['status']]
    return {**balance, 'account_status': view['account']['status'], 'note': note}


# ---------------------------------------------------------------- read models

def _bet_public(row):
    item = dict(row)
    item['fixture'] = _fixture(row)
    return item


def _heartbeat(connection, current):
    row = connection.execute(
        "SELECT heartbeat_at FROM sports_ai_worker_leases WHERE id='scheduler-leader'",
    ).fetchone()
    seen = _parse(row['heartbeat_at']) if row else None
    return {'last': None if not seen else seen.isoformat(),
            'fresh': bool(seen) and (current - seen).total_seconds() < 120}


def dashboard(connection, now=None):
    current = _now(now)
    day = local_date(current)
    today = connection.execute(
        f"SELECT * FROM sports_ai_single_bets WHERE local_date=? AND status IN ({','.join('?' * len(COUNTED))})",
        (day, *COUNTED),
    ).fetchall()
    wins = sum(1 for b in today if b['status'] == 'WON')
    losses = sum(1 for b in today if b['status'] == 'LOST')
    settled_today = [b for b in today if b['status'] in SETTLED]
    staked_settled = sum(b['stake'] for b in settled_today)
    profit_today = round(sum(b['profit'] or 0 for b in settled_today), 2)
    staked_today = sum(b['stake'] for b in today)

    start = (current.astimezone(WAT) - timedelta(days=29)).date()
    days = [(start + timedelta(days=i)).isoformat() for i in range(30)]
    history = connection.execute(
        f"""SELECT local_date, status, profit, stake FROM sports_ai_single_bets
            WHERE local_date>=? AND status IN ({','.join('?' * len(COUNTED))})""",
        (days[0], *COUNTED),
    ).fetchall()
    per_day = {d: {'date': d, 'profit': 0.0, 'wins': 0, 'losses': 0, 'bets': 0} for d in days}
    for row in history:
        bucket = per_day.get(row['local_date'])
        if not bucket:
            continue
        bucket['bets'] += 1
        bucket['profit'] += row['profit'] or 0
        bucket['wins'] += row['status'] == 'WON'
        bucket['losses'] += row['status'] == 'LOST'
    running = 0.0
    trend = []
    for d in days:
        running += per_day[d]['profit']
        trend.append({'date': d, 'cumulative': round(running, 2), 'profit': round(per_day[d]['profit'], 2)})
    w30 = sum(v['wins'] for v in per_day.values())
    l30 = sum(v['losses'] for v in per_day.values())

    by_sport = {}
    for b in today:
        by_sport[b['sport']] = by_sport.get(b['sport'], 0) + 1
    status = _setting(connection, STATUS_KEY, {})
    beat = _heartbeat(connection, current)
    state = runtime(connection)
    risk = staked_today / DAILY_STAKE_LIMIT
    return {
        'mode': state.get('execution_mode') or 'PAPER', 'places_wager': False, 'real_money_enabled': False,
        'generated_at': current.isoformat(), 'local_date': day, 'timezone': 'Africa/Lagos',
        'autonomous': {'running': beat['fresh'] and not state.get('paused'), 'paused': bool(state.get('paused')),
                       'worker_fresh': beat['fresh'], 'last_cycle': status.get('last_run')},
        'balance': _account_balance(connection, current),
        'rules': {'stake': STAKE, 'daily_target': list(DAILY_TARGET), 'daily_limit': DAILY_LIMIT,
                  'daily_stake_limit': DAILY_STAKE_LIMIT, 'min_ev': MIN_EV},
        'today': {
            'executed': len(today), 'wins': wins, 'losses': losses,
            'pending': sum(1 for b in today if b['status'] == 'PLACED'),
            'win_rate': None if not (wins + losses) else round(wins / (wins + losses), 4),
            'profit': profit_today, 'roi': None if not staked_settled else round(profit_today / staked_settled, 4),
            'staked': staked_today, 'risk_ratio': round(risk, 4),
        },
        'trend': trend,
        'last7': [per_day[d] for d in days[-7:]],
        'by_sport': [{'sport': k, 'count': v} for k, v in sorted(by_sport.items(), key=lambda kv: -kv[1])],
        'thirty_day': {'profit': round(running, 2), 'wins': w30, 'losses': l30,
                       'win_rate': None if not (w30 + l30) else round(w30 / (w30 + l30), 4)},
        'insights': {
            'on_target': len(today) >= DAILY_TARGET[0],
            'within_risk': risk <= 1,
            'system_healthy': beat['fresh'] and not status.get('error'),
        },
    }


def engine_view(connection, now=None):
    current = _now(now)
    day = local_date(current)
    from workflow_status import workflow_board
    try:
        board = workflow_board(connection, current)
        stages = [{'name': s['name'], 'status': s['status'], 'count': s['count']} for s in board['stages']]
    except Exception as error:  # the board is advisory; the engine view must still load
        stages, board = [], {'worker': f'unavailable: {error}'}
    status = _setting(connection, STATUS_KEY, {})
    state = runtime(connection)
    beat = _heartbeat(connection, current)
    queued = connection.execute(
        "SELECT * FROM sports_ai_single_bets WHERE status='QUEUED' ORDER BY created_at, id",
    ).fetchall()
    count, staked = _day_usage(connection, day)
    executed = connection.execute(
        f"SELECT COUNT(*) FROM sports_ai_single_bets WHERE local_date=? AND status IN ({','.join('?' * len(COUNTED))})",
        (day, *COUNTED),
    ).fetchone()[0]
    exceptions = connection.execute(
        "SELECT COUNT(*) FROM sports_ai_single_bets WHERE status='CANCELLED' AND local_date=?", (day,),
    ).fetchone()[0]
    singles = {'Tickets & Booking Codes': ('Tickets (Single)', f'{executed} placed today')}
    for stage in stages:
        if stage['name'] in singles:
            stage['name'], stage['count'] = singles[stage['name']]
            stage['status'] = 'Running' if queued else ('Completed' if executed else 'Waiting')
    last = _parse(status.get('last_run'))
    queue = []
    for index, bet in enumerate(queued):
        item = _bet_public(bet)
        item['stage'] = 'Placing' if index < PLACE_PER_TICK else 'Queued'
        wait = math.ceil((index + 1) / PLACE_PER_TICK) * TICK_SECONDS
        base = last or current
        item['eta_seconds'] = max(0, int((base + timedelta(seconds=wait) - current).total_seconds()))
        queue.append(item)
    recent = [_bet_public(r) for r in connection.execute(
        "SELECT * FROM sports_ai_single_bets WHERE local_date=? AND status!='QUEUED' ORDER BY COALESCE(placed_at,created_at) DESC LIMIT 15",
        (day,),
    ).fetchall()]
    events = [dict(r) for r in connection.execute(
        'SELECT action, detail, created_at FROM sports_ai_single_events ORDER BY created_at DESC LIMIT 20',
    ).fetchall()]
    eligible = int(status.get('eligible') or 0)
    focus = queue[0] if queue else (recent[0] if recent else None)
    try:
        from sportybet_account import status_view
        live = status_view(connection, current)
    except Exception:
        live = None
    return {
        'mode': 'PAPER' if not live else live['execution']['mode'],
        'places_wager': bool(live and live['execution']['submission_allowed']),
        'real_money_enabled': bool(live and live['execution']['submission_allowed']),
        'generated_at': current.isoformat(),
        'strategy': {'stake': STAKE, 'daily_target': list(DAILY_TARGET), 'daily_limit': DAILY_LIMIT,
                     'daily_stake_limit': DAILY_STAKE_LIMIT, 'min_ev': MIN_EV, 'place_per_tick': PLACE_PER_TICK,
                     'tick_seconds': TICK_SECONDS},
        'autonomous': {'running': beat['fresh'] and not state.get('paused'), 'paused': bool(state.get('paused')),
                       'changed_by': state.get('changed_by'), 'changed_at': state.get('changed_at')},
        'pipeline': stages,
        'cards': {'ready': eligible, 'in_execution': len(queue), 'executed': executed,
                  'remaining': capacity(count, staked), 'exceptions': exceptions},
        'queue': queue,
        'recent': recent,
        'focus': focus,
        'events': events,
        'last_cycle': status,
        'health': {
            'intelligence_feed': 'Active' if stages else 'Unavailable',
            'selection_engine': 'Paused' if state.get('paused') else ('Running' if last else 'Waiting for first cycle'),
            'execution_worker': 'Running' if beat['fresh'] else 'Heartbeat stale',
            'browser_session': (live or {}).get('account', {}).get('status', 'NOT_CONNECTED'),
            'data_sync': status.get('freshest_price'),
            'last_update': status.get('last_run'),
        },
        'automation': {
            'auto_select': True, 'auto_place_paper': not state.get('paused'), 'auto_settle': True,
            'auto_learn': True, 'booking_code_capture': False,
            'real_money': bool(live and live['execution']['submission_allowed']),
        },
        'live': live,
    }


# ---------------------------------------------------------------- router

def build_single_betting_router(auth, require_admin_key):
    router = APIRouter(prefix='/api/sports-ai/single-betting', tags=['Single Betting AI'])

    @router.get('/dashboard')
    def get_dashboard(actor: str = Depends(auth)):
        with connect() as connection:
            return dashboard(connection)

    @router.get('/engine')
    def get_engine(actor: str = Depends(auth)):
        with connect() as connection:
            return engine_view(connection)

    @router.get('/bets')
    def get_bets(status: str | None = None, limit: int = 100, actor: str = Depends(auth)):
        limit = max(1, min(int(limit), 500))
        with connect() as connection:
            if status:
                rows = connection.execute(
                    'SELECT * FROM sports_ai_single_bets WHERE status=? ORDER BY created_at DESC LIMIT ?',
                    (status.upper(), limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    'SELECT * FROM sports_ai_single_bets ORDER BY created_at DESC LIMIT ?', (limit,),
                ).fetchall()
        return [_bet_public(r) for r in rows]

    @router.post('/execution/kill-switch')
    def kill(actor: str = Depends(auth), x_sports_admin_key: str | None = Header(default=None)):
        require_admin_key(x_sports_admin_key)
        with connect() as connection:
            return set_paused(connection, True, actor)

    @router.post('/execution/resume')
    def resume(actor: str = Depends(auth), x_sports_admin_key: str | None = Header(default=None)):
        require_admin_key(x_sports_admin_key)
        with connect() as connection:
            return set_paused(connection, False, actor)

    @router.post('/real-money/submit')
    def real_money(actor: str = Depends(auth)):
        raise HTTPException(403, 'Real-money submission is not implemented. It requires explicit authorization '
                                 'and a compliant, permitted provider integration.')

    return router
