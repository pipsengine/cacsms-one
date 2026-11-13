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
- decimal odds >= MIN_ODDS (1.20). A selection priced below that is not bet alone; when
  2–3 such high-probability selections on different fixtures together reach MIN_ODDS
  with a combined win probability >= COMBO_MIN_PROBABILITY, they are queued as one
  combined selection (same ₦100 stake, settled only when every leg is graded).
- a combined selection replaces a fixture's single whenever its combined win probability is
  higher; all candidates are ranked by win probability first, expected value second.

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
DAILY_TARGET = (10, 15)
DAILY_LIMIT = 15            # 10 singles + 5 Sure Odds combined bets (owner, 12 Nov 2026)
DAILY_STAKE_LIMIT = 1500.0
MIN_EV = float(os.environ.get('SINGLE_MIN_EXPECTED_VALUE', '0.03'))
# Owner's structure (12 Nov 2026), objective 1:
#   singles   one selection priced 1.60-1.99;
#   combined  the day's Sure Odds tickets (objective 2, product 'sure_odds'), 2-3 legs, 2.00-2.95.
# Settled data 3-10 Nov (basketball handicap prices corrected): 1.60-2.00 was the least-losing
# band measured (-3.4%). Override with SINGLE_MIN_ODDS / SINGLE_MAX_ODDS.
MIN_ODDS = max(1.2, float(os.environ.get('SINGLE_MIN_ODDS', '1.60')))
MAX_ODDS = max(MIN_ODDS, float(os.environ.get('SINGLE_MAX_ODDS', '1.99')))
COMBINED_MIN_ODDS, COMBINED_MAX_ODDS = 2.00, 2.95
# The engine no longer builds its own combined selections from sub-minimum legs: combined bets
# are the Sure Odds tickets, so every one carries a SportyBet booking code.
ENGINE_COMBOS = False
COMBO_MAX_LEGS = 3
COMBO_MIN_PROBABILITY = float(os.environ.get('SINGLE_COMBO_MIN_PROBABILITY', '0.80'))
# Favourites (no edge): strong favourites from favourites.py, used only while edge selections
# do not fill the day. They follow the execution mode like every bet: paper in PAPER, rehearsed
# in LIVE_REHEARSAL, and real money in LIVE under authorization (owner's decision, 2026-11-11).
# Real-money bets: SINGLES_DAILY singles + COMBINED_DAILY combined, within sportybet_account.DAILY_COUNT.
FAVOURITE_DAILY_LIMIT = 10
FAVOURITE_NOTICE = 'Favourite (no edge) - expected to lose money over time.'
# Coded accumulator tickets are followed as ₦100 paper bets (kind TICKET), the same way as singles.
# Only codes SportyBet displayed before the first kickoff, from this date (WAT) on, are imported.
TICKET_IMPORT_FROM = '2026-11-11'
TICKET_PREFIX = 'ticket:'
# Only the Sure Odds tickets are followed (and, in LIVE mode, bet with real money). An allow-list,
# so a new or untagged product never reaches the account: jackpots, the prize ladder, the
# category cards and the retired favourites tickets are never bet.
TICKET_PRODUCTS = ('sure_odds',)
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


def evaluate(odds, probability, observed_at, starts_at, now=None, max_age_minutes=15, min_ev=None, min_odds=None):
    """Deterministic eligibility. A probability is an estimate, never a certainty.

    min_odds=0 evaluates a combined-selection leg, whose own price may sit below MIN_ODDS."""
    current = _now(now)
    floor = MIN_EV if min_ev is None else min_ev
    odds_floor = MIN_ODDS if min_odds is None else min_odds
    reasons = []
    try:
        odds, probability = float(odds), float(probability)
    except (TypeError, ValueError):
        return {'eligible': False, 'ev': None, 'reasons': ['MISSING_PRICE_OR_PROBABILITY']}
    if not 1 < odds <= 1000:
        reasons.append('INVALID_ODDS')
    elif odds < odds_floor - 1e-9:
        reasons.append('BELOW_MIN_ODDS')
    elif min_odds is None and odds > MAX_ODDS + 1e-9:
        reasons.append('ABOVE_MAX_ODDS')  # a combined-selection leg (min_odds=0) is capped as a whole
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


def day_limits(connection):
    """(bets, stake) allowed today. In LIVE mode the real-money limits of the account lane apply
    (10 bets / ₦1,000, or less if the authorization says so), not the paper limits: otherwise the
    queue kept filling with bets the live gate could never place (12 Nov 2026)."""
    if not _live(connection):
        return DAILY_LIMIT, DAILY_STAKE_LIMIT
    from sportybet_account import DAILY_COUNT, DAILY_STAKE, active_authorization
    grant = active_authorization(connection) or {}
    return (min(DAILY_LIMIT, DAILY_COUNT, grant.get('daily_count') or DAILY_COUNT),
            min(DAILY_STAKE_LIMIT, DAILY_STAKE, grant.get('daily_stake') or DAILY_STAKE))


SINGLES_DAILY = 10            # owner's structure (12 Nov 2026): 10 singles at 1.60-1.99 ...
COMBINED_DAILY = 5            # ... plus the day's 5 Sure Odds combined bets at 2.00-2.95 = 15 a day
COMBINED_KINDS = ('TICKET', 'COMBO')


def _group(kind):
    return 'combined' if (kind or 'SINGLE') in COMBINED_KINDS else 'single'


def _group_sql(group):
    return ("COALESCE(b.kind,'SINGLE') IN ('TICKET','COMBO')" if group == 'combined'
            else "COALESCE(b.kind,'SINGLE') NOT IN ('TICKET','COMBO')")


def _group_quota(group):
    return COMBINED_DAILY if group == 'combined' else SINGLES_DAILY


def _group_usage(connection, day, group):
    """(real-money orders sent, bets placed or queued) today for one product group."""
    sent = 0
    if _live(connection):
        sent = connection.execute(
            f"""SELECT COUNT(*) FROM sports_ai_single_live_orders o JOIN sports_ai_single_bets b ON b.id=o.bet_id
                WHERE o.kind='SUBMISSION' AND o.local_date=? AND o.status NOT IN ('REJECTED','FAILED','PENDING')
                  AND {_group_sql(group)}""", (day,)).fetchone()[0]
    engine = connection.execute(
        f"""SELECT COUNT(*) FROM sports_ai_single_bets b WHERE b.local_date=? AND {_group_sql(group)}
              AND b.status IN ({','.join('?' * (len(COUNTED) + 1))})"""
        + (_LIVE_ONLY.replace('sports_ai_single_bets.id', 'b.id') if _live(connection) else ''),
        (day, *COUNTED, 'QUEUED')).fetchone()[0]
    queued = connection.execute(
        f"SELECT COUNT(*) FROM sports_ai_single_bets b WHERE b.status='QUEUED' AND b.local_date=? AND {_group_sql(group)}",
        (day,)).fetchone()[0]
    return int(sent), max(int(engine), int(sent) + int(queued))


def room_today(connection, day, group=None):
    """Bets that may still be queued today. The day's total (15 / ₦1,500, or the authorization's
    lower limits in LIVE mode) and, with `group`, that product's own quota (10 singles, 5 combined)
    must both have room. In LIVE mode real-money orders already sent count as well as the queue."""
    count_limit, stake_limit = day_limits(connection)
    count, staked = _day_usage(connection, day)
    if _live(connection):
        from sportybet_account import live_usage
        live_n, live_staked = live_usage(connection, day)
        queued = connection.execute(
            "SELECT COUNT(*) FROM sports_ai_single_bets WHERE status='QUEUED' AND local_date=?", (day,)).fetchone()[0]
        count, staked = max(count, live_n + queued), max(staked, live_staked + queued * STAKE)
    room = max(0, min(count_limit - int(count), int((stake_limit - float(staked)) // STAKE)))
    if group:
        room = min(room, max(0, _group_quota(group) - _group_usage(connection, day, group)[1]))
    return room


def live_limit_reached(connection, day, group=None):
    """True when real-money orders already sent use up the day's total, or (with `group`) that
    product's quota. LIVE mode only."""
    if not _live(connection):
        return False
    from sportybet_account import live_usage
    count_limit, stake_limit = day_limits(connection)
    live_n, live_staked = live_usage(connection, day)
    if live_n >= count_limit or live_staked + STAKE > stake_limit:
        return True
    return bool(group) and _group_usage(connection, day, group)[0] >= _group_quota(group)


def _target(connection):
    """Daily target shown on the page, never above the day's limit."""
    limit = day_limits(connection)[0]
    return [min(DAILY_TARGET[0], limit), min(DAILY_TARGET[1], limit)]


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
                             WHERE b.fixture_id=d.fixture_id AND b.status!='CANCELLED')
             AND NOT EXISTS (SELECT 1 FROM sports_ai_single_bet_legs l
                             JOIN sports_ai_single_bets b ON b.id=l.bet_id
                             WHERE l.fixture_id=d.fixture_id AND b.status!='CANCELLED')""",
        (current.isoformat(),),
    ).fetchall()
    best = {}
    legs = {}
    rejected = {}
    if not rows:
        # Nothing upstream is QUALIFIED; say so instead of an empty reason list.
        rejected['NO_QUALIFIED_SELECTION'] = connection.execute(
            "SELECT COUNT(DISTINCT fixture_id) FROM sports_ai_qualification_decisions WHERE starts_at>?",
            (current.isoformat(),),
        ).fetchone()[0] or 1
    unbookable = _unbookable_fixtures(connection)
    from league_quality import allowed as league_allowed
    for row in rows:
        if row['fixture_status'] not in OPEN_FIXTURE:
            continue
        if row['fixture_id'] in unbookable:
            rejected['NOT_FOUND_ON_SPORTYBET'] = rejected.get('NOT_FOUND_ON_SPORTYBET', 0) + 1
            continue
        if not league_allowed(connection, row['sport'], row['competition'], row['home'], row['away']):
            rejected['LEAGUE_TRACK_RECORD'] = rejected.get('LEAGUE_TRACK_RECORD', 0) + 1
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
        combinable = verdict['reasons'] == ['BELOW_MIN_ODDS']
        if not verdict['eligible'] and not combinable:
            for reason in verdict['reasons']:
                rejected[reason] = rejected.get(reason, 0) + 1
            continue
        item = {
            'kind': 'SINGLE', 'decision_id': row['decision_id'], 'prediction_id': row['prediction_id'],
            'fixture_id': row['fixture_id'], 'sport': row['sport'], 'competition': row['competition'] or '',
            'home': row['home'], 'away': row['away'], 'market': str(row['market']).upper(),
            'selection': selection_label(row['market']), 'decimal_odds': float(quote['decimal_odds']),
            'probability': float(probability), 'expected_value': verdict['ev'],
            'starts_at': row['starts_at'], 'odds_observed_at': quote['observed_at'],
        }
        target = legs if combinable else best
        held = target.get(row['fixture_id'])
        if held is None or _chance_key(item) < _chance_key(held):
            target[row['fixture_id']] = item
    if not ENGINE_COMBOS:
        if legs:
            rejected['BELOW_MIN_ODDS'] = rejected.get('BELOW_MIN_ODDS', 0) + len(legs)
        legs = {}
    combos, singles, unused, replaced = choose(best, legs)
    if unused:
        rejected['BELOW_MIN_ODDS'] = rejected.get('BELOW_MIN_ODDS', 0) + unused
    if replaced:
        rejected['SINGLE_REPLACED_BY_COMBINED'] = rejected.get('SINGLE_REPLACED_BY_COMBINED', 0) + replaced
    eligible = sorted([*singles, *combos], key=_chance_key)
    used = {item['fixture_id'] for item in singles} | {l['fixture_id'] for c in combos for l in c['legs']}
    return {'eligible': eligible, 'favourites': favourite_candidates(connection, current, used),
            'rejected': rejected, 'max_odds_age_minutes': max_age}


UNBOOKABLE_HOURS = 24


def _unbookable_fixtures(connection):
    """Matches the account could not find on SportyBet's page in the last day. Re-queuing them only
    repeats the same failure (one match was retried five times in seven minutes), so they are skipped."""
    try:
        rows = connection.execute(
            """SELECT b.fixture_id FROM sports_ai_single_live_orders o JOIN sports_ai_single_bets b ON b.id=o.bet_id
               WHERE o.status='FAILED' AND o.error LIKE 'Selection control missing%'
                 AND julianday(o.created_at) > julianday('now') - ?
               UNION SELECT l.fixture_id FROM sports_ai_single_live_orders o JOIN sports_ai_single_bet_legs l ON l.bet_id=o.bet_id
               WHERE o.status='FAILED' AND o.error LIKE 'Selection control missing%'
                 AND julianday(o.created_at) > julianday('now') - ?""",
            (UNBOOKABLE_HOURS / 24, UNBOOKABLE_HOURS / 24),
        ).fetchall()
    except Exception:  # account tables missing on an old database
        return set()
    return {row[0] for row in rows}


def _active_fixtures(connection):
    rows = connection.execute(
        """SELECT fixture_id FROM sports_ai_single_bets WHERE status!='CANCELLED'
           UNION SELECT l.fixture_id FROM sports_ai_single_bet_legs l
           JOIN sports_ai_single_bets b ON b.id=l.bet_id WHERE b.status!='CANCELLED'""",
    ).fetchall()
    return {row[0] for row in rows} | _unbookable_fixtures(connection)


def _favourite_item(leg):
    return {
        'kind': 'FAVOURITE', 'decision_id': None, 'prediction_id': leg['opportunity_id'],
        'fixture_id': leg['fixture_id'], 'sport': leg['sport'], 'competition': leg['competition'],
        'home': leg['home'], 'away': leg['away'], 'market': leg['market'],
        'selection': selection_label(leg['market']), 'decimal_odds': float(leg['decimal_odds']),
        'probability': float(leg['probability']),
        'expected_value': round(leg['probability'] * leg['decimal_odds'] - 1, 6),
        'starts_at': leg['starts_at'], 'odds_observed_at': leg['odds_observed_at'],
    }


def _ticket_fixtures(connection):
    """Matches on open favourites accumulator tickets (built, being booked, or coded)."""
    rows = connection.execute(
        """SELECT l.fixture_id FROM sports_ai_accumulator_legs l
           JOIN sports_ai_accumulator_tickets t ON t.id=l.ticket_id
           WHERE json_extract(t.parameters_json,'$.product')='favourites' AND t.status='qualified'""",
    ).fetchall()
    return {row[0] for row in rows}


def import_coded_tickets(connection, now=None):
    """Follow each accumulator ticket SportyBet displayed a booking code for, as one ₦100 PAPER bet.

    Processed like a single: daily count and stake caps, settlement from verified results, P/L.
    Placed at the moment the code was displayed, and only when that was before the first kickoff,
    so a result is never known at placement. Nothing is submitted to SportyBet.
    """
    current = _now(now)
    live_mode = (runtime(connection).get('execution_mode') or 'PAPER') != 'PAPER'
    rows = connection.execute(
        f"""SELECT t.id, t.combined_odds, t.joint_probability, t.parameters_json, b.code, b.created_at AS coded_at,
                  (SELECT MIN(l.starts_at) FROM sports_ai_accumulator_legs l WHERE l.ticket_id=t.id) AS first_kickoff
           FROM sports_ai_accumulator_tickets t
           JOIN sports_ai_booking_codes b ON b.accumulator_ticket_id=t.id
            AND b.verification_method='displayed' AND TRIM(COALESCE(b.code,''))<>''
           WHERE b.created_at>=? AND t.combined_odds BETWEEN ? AND ? AND t.joint_probability>0
             AND json_extract(t.parameters_json,'$.product') IN ({','.join('?' * len(TICKET_PRODUCTS))})
             AND NOT EXISTS (SELECT 1 FROM sports_ai_single_bets s WHERE s.id=?||t.id)
           ORDER BY b.created_at""",
        (datetime.fromisoformat(TICKET_IMPORT_FROM).replace(tzinfo=WAT).astimezone(timezone.utc).isoformat(),
         COMBINED_MIN_ODDS - 1e-9, COMBINED_MAX_ODDS + 1e-9, *TICKET_PRODUCTS, TICKET_PREFIX),
    ).fetchall()
    imported = 0
    for row in rows:
        coded, kickoff = _parse(row['coded_at']), _parse(row['first_kickoff'])
        if coded is None or kickoff is None or coded >= kickoff:
            continue  # a code displayed after kickoff is never followed
        if live_mode and (kickoff - current).total_seconds() < MIN_SECONDS_TO_START:
            continue  # too close to kickoff to place on the account
        legs = connection.execute(
            """SELECT fixture_id, sport, competition, home, away, market, selection, decimal_odds, probability,
                      starts_at, odds_observed_at FROM sports_ai_accumulator_legs WHERE ticket_id=? ORDER BY starts_at""",
            (row['id'],),
        ).fetchall()
        if not legs:
            continue
        day = local_date(current if live_mode else coded)
        if room_today(connection, day, 'combined') <= 0:
            continue
        taken = _active_fixtures(connection)
        if any(leg['fixture_id'] in taken for leg in legs):
            continue  # correlation rule: a match is never backed twice
        params = json.loads(row['parameters_json'] or '{}')
        favourite = params.get('product') == 'favourites'
        odds, probability = float(row['combined_odds']), float(row['joint_probability'])
        bet_id = TICKET_PREFIX + row['id']
        first = legs[0]
        sports = {leg['sport'] for leg in legs}
        label = ('Favourites ticket (no edge)' if favourite else 'Qualified ticket') + f" · code {row['code']}"
        connection.execute(
            """INSERT OR IGNORE INTO sports_ai_single_bets
               (id,decision_id,prediction_id,fixture_id,sport,competition,home,away,market,selection,decimal_odds,
                probability,expected_value,stake,mode,status,local_date,starts_at,odds_observed_at,created_at,placed_at,kind,reason)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,'PAPER',?,?,?,?,?,?,'TICKET',?)""",
            (bet_id, None, None, first['fixture_id'], first['sport'] if len(sports) == 1 else 'mixed',
             ' · '.join(dict.fromkeys(leg['competition'] for leg in legs if leg['competition'])),
             first['home'], first['away'], 'TICKET', label, odds, min(probability, 0.999999),
             round(probability * odds - 1, 6), STAKE, 'QUEUED' if live_mode else 'PLACED', day, kickoff.isoformat(),
             first['odds_observed_at'], (current if live_mode else coded).isoformat(),
             None if live_mode else coded.isoformat(), (FAVOURITE_NOTICE if favourite else '')[:300]),
        )
        if not connection.execute('SELECT 1 FROM sports_ai_single_bets WHERE id=?', (bet_id,)).fetchone():
            continue
        for number, leg in enumerate(legs, start=1):
            connection.execute(
                """INSERT OR IGNORE INTO sports_ai_single_bet_legs
                   (bet_id,leg_no,fixture_id,sport,competition,home,away,market,selection,decimal_odds,probability,
                    starts_at,odds_observed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (bet_id, number, leg['fixture_id'], leg['sport'], leg['competition'] or '', leg['home'], leg['away'],
                 leg['market'], leg['selection'] or leg['market'], float(leg['decimal_odds']),
                 min(max(float(leg['probability'] or 0.5), 0.000001), 0.999999), leg['starts_at'], leg['odds_observed_at']),
            )
        _event(connection, bet_id, 'QUEUED' if live_mode else 'PLACED',
               (f"Ticket queued for the account – code {row['code']}" if live_mode
                else f"Paper ticket placed – code {row['code']}")
               + f", {len(legs)} legs @ {odds:.2f} (₦{STAKE:,.0f})" + (' · no edge' if favourite else ''), current)
        imported += 1
    return imported


def favourite_candidates(connection, now=None, exclude=()):
    """Favourites priced at or above MIN_ODDS on fixtures not already backed. No edge by design."""
    from favourites import candidates, lane_of
    current = _now(now)
    taken = _active_fixtures(connection) | set(exclude) | _ticket_fixtures(connection)
    try:
        from league_quality import filter_legs
        legs = filter_legs(connection, candidates(connection, current))
    except Exception:  # favourites are optional; the edge engine must keep running
        return []
    # The favourites ticket lane is retired (owner's structure, 12 Nov), so singles use every favourite.
    tickets_full = True
    # Each price batch is split with the ticket lane (favourites.lane_of); once the tickets' daily
    # cap is reached, singles may use the whole batch.
    items = [_favourite_item(leg) for leg in legs
             if leg['fixture_id'] not in taken and MIN_ODDS - 1e-9 <= float(leg['decimal_odds']) <= MAX_ODDS + 1e-9
             and (tickets_full or lane_of(leg) == 'single')]
    # Model favourites need a 70% anchored chance, which a 1.60-1.99 price almost never has, so the
    # 1.60-1.99 singles also come from SportyBet's own favourites (12 Nov 2026: none otherwise).
    have = {item['fixture_id'] for item in items} | taken
    try:
        items += [item for item in market_favourites(connection, current) if item['fixture_id'] not in have]
    except Exception:
        pass
    return items


MARKET_SINGLE_FLOOR = 0.55          # SportyBet's chance with the margin removed (about 1.73 at a 5% margin)
MARKET_SINGLE_LEAD_MINUTES = 60
MARKET_SINGLE_HORIZON_HOURS = 24


def market_favourites(connection, now=None):
    """SportyBet-priced favourites in MIN_ODDS..MAX_ODDS: reliable markets (result / double chance /
    winner) in sports with settled results and leagues with an acceptable record, kickoff at least
    MARKET_SINGLE_LEAD_MINUTES away. No model edge: the margin makes these lose ~5% on average."""
    import jackpot_low
    import prize_ladder
    from league_quality import filter_legs
    current = _now(now)
    saved = (jackpot_low.MIN_ODDS, jackpot_low.MAX_ODDS)
    jackpot_low.MIN_ODDS, jackpot_low.MAX_ODDS = MIN_ODDS, MAX_ODDS
    try:
        scanned = jackpot_low.scan(connection, current, horizon_hours=MARKET_SINGLE_HORIZON_HOURS,
                                   keep=prize_ladder.reliable_keep(connection))
    finally:
        jackpot_low.MIN_ODDS, jackpot_low.MAX_ODDS = saved
    earliest = (current + timedelta(minutes=MARKET_SINGLE_LEAD_MINUTES)).isoformat()
    legs = prize_ladder.reliable(filter_legs(connection, scanned), prize_ladder.hit_rates(connection))
    legs = [leg for leg in legs if leg['sport'] in prize_ladder.LADDER_SPORTS
            and leg['probability'] >= MARKET_SINGLE_FLOOR and str(leg['starts_at']) >= earliest]
    legs.sort(key=lambda leg: (-leg['probability'], leg['starts_at'], leg['fixture_id']))
    return [_favourite_item(leg) for leg in legs]


def _recheck_market(connection, bet, current):
    """Re-check a SportyBet-priced favourite: same quote, newest price, still in range, not started."""
    import jackpot_low
    snapshot = str(bet['prediction_id'] or '').split(':', 1)[1]
    origin = connection.execute(
        'SELECT fixture_id, market, outcome, line FROM sports_ai_odds_snapshots WHERE id=?', (snapshot,),
    ).fetchone()
    if origin is None:
        return None, 'The SportyBet price this favourite was chosen from is no longer stored.'
    fixture = connection.execute('SELECT status, starts_at FROM sports_ai_fixtures WHERE id=?', (bet['fixture_id'],)).fetchone()
    if fixture is None or fixture['status'] not in ('scheduled', 'not_started'):
        return None, 'The match is no longer scheduled.'
    if str(fixture['starts_at']) < (current + timedelta(minutes=jackpot_low.LEAD_MINUTES)).isoformat():
        return None, 'Kickoff is too close.'
    quote = connection.execute(
        """SELECT decimal_odds, observed_at FROM sports_ai_odds_snapshots
           WHERE fixture_id=? AND bookmaker='SportyBet' AND market=? AND outcome=? AND IFNULL(line,-1)=IFNULL(?,-1)
           ORDER BY observed_at DESC LIMIT 1""",
        (origin['fixture_id'], origin['market'], origin['outcome'], origin['line']),
    ).fetchone()
    if quote is None or str(quote['observed_at']) < (current - timedelta(hours=jackpot_low.PRICE_HOURS)).isoformat():
        return None, 'No recent SportyBet price for this favourite.'
    odds = float(quote['decimal_odds'])
    if not MIN_ODDS - 1e-9 <= odds <= MAX_ODDS + 1e-9:
        return None, f"Price {odds:.2f} is outside the {MIN_ODDS:.2f}-{MAX_ODDS:.2f} range."
    # Same margin as when chosen: the chance moves inversely with the price.
    probability = min(0.99, float(bet['probability']) * float(bet['decimal_odds']) / odds)
    if probability < MARKET_SINGLE_FLOOR:
        return None, f'SportyBet now rates it {probability:.0%}, below {MARKET_SINGLE_FLOOR:.0%}.'
    return {'decimal_odds': odds, 'observed_at': quote['observed_at'],
            'ev': round(probability * odds - 1, 6), 'probability': probability}, ''


def _chance_key(item):
    """Higher chance of winning first; expected value breaks ties."""
    return (-round(item['probability'], 6), -item['expected_value'], item['starts_at'], item['fixture_id'])


def choose(singles, legs):
    """Decide singles versus combined selections, fixture by fixture.

    singles: fixture -> best single priced >= MIN_ODDS. legs: fixture -> best selection priced
    below MIN_ODDS that passes every other rule. A combined selection is preferred over a
    fixture's single whenever its combined win probability is higher; otherwise that leg is
    withdrawn and the remaining legs are regrouped. Returns
    (combos, singles kept, number of unused legs on fixtures without a single, singles replaced)."""
    pool = list(legs.values())
    while True:
        combos, leftover = build_combos(pool)
        losers = {leg['fixture_id'] for combo in combos for leg in combo['legs']
                  if leg['fixture_id'] in singles and singles[leg['fixture_id']]['probability'] >= combo['probability']}
        if not losers:
            break
        pool = [leg for leg in pool if leg['fixture_id'] not in losers]
    used = {leg['fixture_id'] for combo in combos for leg in combo['legs']}
    kept = [item for fixture, item in singles.items() if fixture not in used]
    unused = sum(1 for leg in legs.values() if leg['fixture_id'] not in used and leg['fixture_id'] not in singles)
    return combos, kept, unused, len(used & set(singles))


def _product(values):
    out = 1.0
    for value in values:
        out *= float(value)
    return out


def combine(legs):
    """One combined selection over legs on different fixtures (independent events)."""
    odds = round(_product(l['decimal_odds'] for l in legs), 4)
    probability = _product(l['probability'] for l in legs)
    sports = {l['sport'] for l in legs}
    first = legs[0]
    return {
        'kind': 'COMBO', 'legs': list(legs),
        'decision_id': first.get('decision_id'), 'prediction_id': first.get('prediction_id'),
        'fixture_id': first['fixture_id'], 'sport': first['sport'] if len(sports) == 1 else 'mixed',
        'competition': ' · '.join(dict.fromkeys(l.get('competition') or '' for l in legs if l.get('competition'))),
        'home': first['home'], 'away': first['away'], 'market': 'COMBO',
        'selection': ' + '.join(l['selection'] for l in legs),
        'decimal_odds': odds, 'probability': probability,
        'expected_value': round(odds * probability - 1, 6),
        'starts_at': min(l['starts_at'] for l in legs),
        'odds_observed_at': min((l.get('odds_observed_at') or '') for l in legs) or None,
    }


def combo_ok(combo):
    return (MIN_ODDS - 1e-9 <= combo['decimal_odds'] <= MAX_ODDS + 1e-9 and combo['probability'] >= COMBO_MIN_PROBABILITY
            and combo['expected_value'] >= MIN_EV)


def build_combos(pool):
    """Group sub-MIN_ODDS legs into combined selections that reach MIN_ODDS.

    Greedy, highest probability first. Each group adds the leg that reaches MIN_ODDS with the
    highest combined probability, or else the most probable leg, up to COMBO_MAX_LEGS. A group
    is kept only if it reaches MIN_ODDS with combined probability >= COMBO_MIN_PROBABILITY.
    Returns (combos, legs left unused)."""
    pool = sorted(pool, key=lambda l: (-l['probability'], l['starts_at'], l['fixture_id']))
    combos, unused = [], []
    while pool:
        group = [pool.pop(0)]
        while pool and len(group) < COMBO_MAX_LEGS and _product(l['decimal_odds'] for l in group) < MIN_ODDS - 1e-9:
            base_odds = _product(l['decimal_odds'] for l in group)
            closing = [l for l in pool if base_odds * l['decimal_odds'] >= MIN_ODDS - 1e-9]
            pick = max(closing, key=lambda l: l['probability']) if closing else pool[0]
            pool.remove(pick)
            group.append(pick)
        combo = combine(group)
        if combo_ok(combo):
            combos.append(combo)
        else:
            unused.extend(group)
    return combos, unused


# In LIVE mode the daily caps count real submissions and the queue only. A bet only rehearsed
# (Place Bet not clicked) staked nothing and must not use up the day's real-money capacity.
# The real-money ceiling itself is enforced separately from live orders (sportybet_account).
_LIVE_ONLY = """ AND (status='QUEUED' OR EXISTS (SELECT 1 FROM sports_ai_single_live_orders o
                 WHERE o.bet_id=sports_ai_single_bets.id AND o.kind='SUBMISSION'))"""


def _live(connection):
    return runtime(connection).get('execution_mode') == 'LIVE'


def _favourites_today(connection, day):
    return connection.execute(
        "SELECT COUNT(*) FROM sports_ai_single_bets WHERE local_date=? AND kind='FAVOURITE' AND status!='CANCELLED'"
        + (_LIVE_ONLY if _live(connection) else ''), (day,),
    ).fetchone()[0]


def _day_usage(connection, day):
    row = connection.execute(
        f"""SELECT COUNT(*) AS n, COALESCE(SUM(stake),0) AS staked FROM sports_ai_single_bets
            WHERE local_date=? AND status IN ({','.join('?' * (len(COUNTED) + 1))})"""
        + (_LIVE_ONLY if _live(connection) else ''),
        (day, *COUNTED, 'QUEUED'),
    ).fetchone()
    return int(row['n']), float(row['staked'])


def _kind(bet):
    try:
        return bet['kind'] or 'SINGLE'
    except (IndexError, KeyError):
        return 'SINGLE'


def _legs(connection, bet_id):
    return connection.execute(
        'SELECT * FROM sports_ai_single_bet_legs WHERE bet_id=? ORDER BY leg_no', (bet_id,),
    ).fetchall()


def _recheck_combo(connection, bet, current, max_age):
    from qualification_engine import selection_is_qualified
    legs = _legs(connection, bet['id'])
    if len(legs) < 2:
        return None, 'Combined selection has fewer than two legs.'
    fresh_legs = []
    for leg in legs:
        name = f"{leg['home']} vs {leg['away']}"
        if not selection_is_qualified(connection, leg['fixture_id'], leg['market']):
            return None, f'Leg {name} no longer has a current QUALIFIED decision.'
        quote = _latest_quote(connection, leg['fixture_id'], leg['market'])
        if quote is None:
            return None, f'No SportyBet price is stored for leg {name}.'
        verdict = evaluate(quote['decimal_odds'], leg['probability'], quote['observed_at'], leg['starts_at'],
                           current, max_age, min_odds=0)
        if not verdict['eligible']:
            return None, f'Leg {name} re-validation failed: ' + ', '.join(verdict['reasons'])
        fresh_legs.append({**dict(leg), 'decimal_odds': float(quote['decimal_odds']), 'odds_observed_at': quote['observed_at']})
    combo = combine(fresh_legs)
    if not combo_ok(combo):
        return None, (f"Combined selection no longer qualifies: odds {combo['decimal_odds']:.2f} (min {MIN_ODDS:.2f}), "
                      f"probability {combo['probability']:.2f} (min {COMBO_MIN_PROBABILITY:.2f}), EV {combo['expected_value']:+.3f}")
    return {'decimal_odds': combo['decimal_odds'], 'observed_at': combo['odds_observed_at'], 'ev': combo['expected_value'],
            'probability': combo['probability'], 'legs': fresh_legs}, ''


def _recheck_ticket(connection, bet, current):
    """A followed ticket may be placed while every leg is still a scheduled match before kickoff.
    Prices are checked on the SportyBet page itself (each leg within MAX_PRICE_MOVE)."""
    from jackpot_stop import is_stopped, REASON
    if str(bet['id']).startswith(TICKET_PREFIX) and is_stopped(connection, bet['id'][len(TICKET_PREFIX):]):
        return None, REASON
    legs = _legs(connection, bet['id'])
    if not legs:
        return None, 'The ticket has no legs.'
    for leg in legs:
        fixture = connection.execute('SELECT status, starts_at FROM sports_ai_fixtures WHERE id=?', (leg['fixture_id'],)).fetchone()
        kickoff = _parse(fixture['starts_at'] if fixture else leg['starts_at'])
        if not fixture or fixture['status'] not in OPEN_FIXTURE or kickoff is None \
                or (kickoff - current).total_seconds() < MIN_SECONDS_TO_START:
            return None, f"{leg['home']} vs {leg['away']} is no longer before kickoff."
    fresh_legs = [dict(leg) for leg in legs]
    odds = round(_product(l['decimal_odds'] for l in fresh_legs), 4)
    probability = _product(l['probability'] for l in fresh_legs)
    return {'decimal_odds': odds, 'observed_at': bet['odds_observed_at'], 'ev': round(odds * probability - 1, 6),
            'probability': min(probability, 0.999999), 'legs': fresh_legs}, ''


def _apply_fresh_legs(connection, bet_id, fresh):
    for leg in fresh.get('legs') or []:
        connection.execute(
            'UPDATE sports_ai_single_bet_legs SET decimal_odds=?, odds_observed_at=? WHERE bet_id=? AND leg_no=?',
            (leg['decimal_odds'], leg['odds_observed_at'], bet_id, leg['leg_no']),
        )


def _recheck(connection, bet, current, max_age):
    from qualification_engine import selection_is_qualified
    if _kind(bet) == 'COMBO':
        return _recheck_combo(connection, bet, current, max_age)
    if _kind(bet) == 'TICKET':
        return _recheck_ticket(connection, bet, current)
    if _kind(bet) == 'FAVOURITE' and str(bet['prediction_id'] or '').startswith('jackpot:'):
        return _recheck_market(connection, bet, current)
    if _kind(bet) == 'FAVOURITE':
        from favourites import candidates
        leg = next(iter(candidates(connection, current, fixture_id=bet['fixture_id'], market=bet['market'])), None)
        if leg is None:
            return None, 'No longer a favourite with a fresh SportyBet price and every safety check passing.'
        if not MIN_ODDS - 1e-9 <= float(leg['decimal_odds']) <= MAX_ODDS + 1e-9:
            return None, f"Price {leg['decimal_odds']:.2f} is outside the {MIN_ODDS:.2f}-{MAX_ODDS:.2f} range."
        return {'decimal_odds': float(leg['decimal_odds']), 'observed_at': leg['odds_observed_at'],
                'ev': round(leg['probability'] * leg['decimal_odds'] - 1, 6), 'probability': leg['probability']}, ''
    if not selection_is_qualified(connection, bet['fixture_id'], bet['market']):
        return None, 'The selection no longer has a current QUALIFIED decision.'
    quote = _latest_quote(connection, bet['fixture_id'], bet['market'])
    if quote is None:
        return None, 'No SportyBet price is stored for this selection.'
    verdict = evaluate(quote['decimal_odds'], bet['probability'], quote['observed_at'], bet['starts_at'], current, max_age)
    if not verdict['eligible']:
        return None, 'Re-validation failed: ' + ', '.join(verdict['reasons'])
    return {'decimal_odds': float(quote['decimal_odds']), 'observed_at': quote['observed_at'], 'ev': verdict['ev']}, ''


def _fixture(bet, legs=None):
    if legs:
        return ' + '.join(f"{l['home']} vs {l['away']}" for l in legs)
    return f"{bet['home']} vs {bet['away']}"


def stamp_legs(connection, bet_id, status):
    """One SportyBet code = one bet: every selection in it takes the code's result (owner, 13 Nov 2026).
    A selection that came through inside a lost code is lost, never counted as a win."""
    connection.execute('UPDATE sports_ai_single_bet_legs SET status=? WHERE bet_id=?', (status, bet_id))


def _settle_combos(connection, current):
    from rollover_engine import _outcome
    settled = 0
    for bet in connection.execute(
        "SELECT * FROM sports_ai_single_bets WHERE status='PLACED' AND kind IN ('COMBO','TICKET')",
    ).fetchall():
        legs = _legs(connection, bet['id'])
        grades = []
        for leg in legs:
            grade = leg['status'] if leg['status'] != 'OPEN' else None
            if grade is None:
                score = connection.execute(
                    "SELECT home_score, away_score FROM sports_ai_results WHERE fixture_id=? AND status='final' LIMIT 1",
                    (leg['fixture_id'],),
                ).fetchone()
                outcome = None if score is None else _outcome(leg['sport'], leg['market'], int(score['home_score']), int(score['away_score']))
                if outcome is not None:
                    grade = {'WON': 'WON', 'LOST': 'LOST'}.get(outcome, 'VOID')
                    connection.execute('UPDATE sports_ai_single_bet_legs SET status=? WHERE bet_id=? AND leg_no=?',
                                       (grade, bet['id'], leg['leg_no']))
            grades.append(grade)
        if 'LOST' in grades:
            status, payout = 'LOST', 0.0
        elif None in grades or not grades:
            continue
        elif all(g == 'VOID' for g in grades):
            status, payout = 'VOID', float(bet['stake'])
        else:  # a void leg counts at 1.00, as on the bookmaker
            odds = _product(l['decimal_odds'] for l, g in zip(legs, grades) if g == 'WON')
            status, payout = 'WON', round(bet['stake'] * odds, 2)
        connection.execute(
            """UPDATE sports_ai_single_bets SET status=?, payout=?, profit=?, settled_at=?
               WHERE id=? AND status='PLACED'""",
            (status, payout, round(payout - bet['stake'], 2), current.isoformat(), bet['id']),
        )
        stamp_legs(connection, bet['id'], status)
        _event(connection, bet['id'], 'SETTLED',
               f"Combined settled {status.lower()} – {_fixture(bet, legs)} (₦{payout - bet['stake']:+,.0f})", current)
        settled += 1
    return settled


def settle(connection, now=None):
    """Settle placed paper singles from verified final results."""
    from rollover_engine import _outcome
    current = _now(now)
    settled = 0
    for bet in connection.execute(
        """SELECT b.*, r.home_score, r.away_score FROM sports_ai_single_bets b
           JOIN sports_ai_results r ON r.fixture_id=b.fixture_id AND r.status='final'
           WHERE b.status='PLACED' AND COALESCE(b.kind,'SINGLE') NOT IN ('COMBO','TICKET')""",
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
    return settled + _settle_combos(connection, current)


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
        combo = _kind(bet) == 'COMBO'
        favourite = _kind(bet) == 'FAVOURITE'
        # In a live mode the account lane places (or rehearses) the head of the queue.
        if live_mode or result['placed'] >= PLACE_PER_TICK:
            continue
        if bet['local_date'] != day and room_today(connection, day, _group(_kind(bet))) <= 0:
            continue
        connection.execute(
            """UPDATE sports_ai_single_bets
               SET status='PLACED', decimal_odds=?, odds_observed_at=?, expected_value=?, placed_at=?, local_date=?,
                   probability=COALESCE(?, probability)
               WHERE id=? AND status='QUEUED'""",
            (fresh['decimal_odds'], fresh['observed_at'], fresh['ev'], current.isoformat(), day,
             fresh.get('probability'), bet['id']),
        )
        _apply_fresh_legs(connection, bet['id'], fresh)
        label = 'Paper combined bet placed' if combo else 'Paper favourite placed (no edge)' if favourite else 'Paper bet placed'
        _event(connection, bet['id'], 'PLACED',
               f"{label} – {_fixture(bet, fresh.get('legs'))} {bet['selection']} @ {fresh['decimal_odds']:.2f} (₦{bet['stake']:,.0f})", current)
        result['placed'] += 1

    # Coded accumulator tickets become paper bets first, so favourite singles never take their matches.
    result['tickets_imported'] = import_coded_tickets(connection, current)

    # Refill the queue within the daily caps.
    pool = load_candidates(connection, current) if candidates is None else candidates
    full = {group: live_limit_reached(connection, day, group) for group in ('single', 'combined')}
    if any(full.values()):
        # Today's real money (in total, or for one product) is used up: those queued bets cannot be placed.
        for bet in connection.execute(
            "SELECT * FROM sports_ai_single_bets WHERE status='QUEUED' AND local_date=?", (day,),
        ).fetchall():
            if not full[_group(_kind(bet))]:
                continue
            connection.execute(
                "UPDATE sports_ai_single_bets SET status='CANCELLED', reason=? WHERE id=? AND status='QUEUED'",
                ('Daily real-money limit reached', bet['id']))
            _event(connection, bet['id'], 'CANCELLED', f'Removed from queue – {_fixture(bet)}: daily real-money limit reached', current)
            result['cancelled'] = result.get('cancelled', 0) + 1
    room = room_today(connection, day, 'single')
    picks = list(pool['eligible'][:room])
    favourite_room = min(room - len(picks), FAVOURITE_DAILY_LIMIT - _favourites_today(connection, day))
    if favourite_room > 0:
        picks += (pool.get('favourites') or [])[:favourite_room]
    for item in picks:
        bet_id = str(uuid.uuid4())
        kind = item.get('kind') or 'SINGLE'
        cursor = connection.execute(
            """INSERT OR IGNORE INTO sports_ai_single_bets
               (id,decision_id,prediction_id,fixture_id,sport,competition,home,away,market,selection,decimal_odds,
                probability,expected_value,stake,mode,status,local_date,starts_at,odds_observed_at,created_at,kind)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,'PAPER','QUEUED',?,?,?,?,?)""",
            (bet_id, item.get('decision_id'), item.get('prediction_id'), item['fixture_id'], item['sport'],
             item.get('competition', ''), item['home'], item['away'], item['market'], item['selection'],
             item['decimal_odds'], item['probability'], item['expected_value'], STAKE, day, item['starts_at'],
             item.get('odds_observed_at'), current.isoformat(), kind),
        )
        if cursor.rowcount:
            for number, leg in enumerate(item.get('legs') or [], start=1):
                connection.execute(
                    """INSERT INTO sports_ai_single_bet_legs
                       (bet_id,leg_no,decision_id,prediction_id,fixture_id,sport,competition,home,away,market,selection,
                        decimal_odds,probability,starts_at,odds_observed_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (bet_id, number, leg.get('decision_id'), leg.get('prediction_id'), leg['fixture_id'], leg['sport'],
                     leg.get('competition', ''), leg['home'], leg['away'], leg['market'], leg['selection'],
                     leg['decimal_odds'], leg['probability'], leg['starts_at'], leg.get('odds_observed_at')),
                )
            result['queued'] += 1
            what = (f"Combined selection qualified ({len(item['legs'])} legs, p={item['probability']:.2f})" if kind == 'COMBO'
                    else f"Favourite queued (no edge{', real money' if _live(connection) else ', paper only'}, p={item['probability']:.2f})" if kind == 'FAVOURITE'
                    else 'Selection qualified')
            _event(connection, bet_id, 'QUEUED',
                   f"{what} – {_fixture(item, item.get('legs'))} {item['selection']} @ {item['decimal_odds']:.2f}, EV {item['expected_value']:+.3f}", current)
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
    # Combined bets (the day's Sure Odds tickets, at most 5) go before singles, so the last
    # slots of a day are not all taken by favourites.
    for bet in connection.execute(
        "SELECT * FROM sports_ai_single_bets WHERE status='QUEUED' ORDER BY kind IN ('TICKET','COMBO') DESC, created_at, id",
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
        if bet['local_date'] != day and room_today(connection, day, _group(_kind(bet))) <= 0:
            return None
        item = dict(bet)
        item.update(decimal_odds=fresh['decimal_odds'], odds_observed_at=fresh['observed_at'], expected_value=fresh['ev'],
                    kind=_kind(bet), legs=fresh.get('legs') or [])
        if fresh.get('probability') is not None:
            item['probability'] = fresh['probability']
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
               SET status='PLACED', decimal_odds=?, odds_observed_at=?, expected_value=?, placed_at=?, local_date=?, reason=?,
                   probability=?
               WHERE id=? AND status='QUEUED'""",
            (bet['decimal_odds'], bet.get('odds_observed_at'), bet['expected_value'], current.isoformat(), day,
             detail[:300], bet['probability'], bet['id']),
        )
        _apply_fresh_legs(connection, bet['id'], bet)
        if outcome == 'LIVE_PLACED':
            # The price SportyBet accepted on the betslip, not the quote the bet was queued at
            # (12 Nov 2026: Benin v CAR was stored at 1.63 but accepted, and paid, at 1.68).
            connection.execute(
                """UPDATE sports_ai_single_bets SET decimal_odds=COALESCE((
                       SELECT page_odds FROM sports_ai_single_live_orders
                       WHERE bet_id=? AND kind='SUBMISSION' AND page_odds>1), decimal_odds) WHERE id=?""",
                (bet['id'], bet['id']),
            )
        action = {'PAPER_PLACED': 'REHEARSED', 'LIVE_PLACED': 'LIVE_PLACED', 'UNKNOWN': 'EXCEPTION'}[outcome]
        _event(connection, bet['id'], action, f"{_fixture(bet, bet.get('legs'))} {bet['selection']} – {detail}", current)
        if outcome == 'UNKNOWN':
            set_paused(connection, True, 'live-execution', current)
    elif outcome == 'CANCEL':
        connection.execute(
            "UPDATE sports_ai_single_bets SET status='CANCELLED', reason=? WHERE id=? AND status='QUEUED'",
            (f'{kind.title()} stopped: {detail}'[:300], bet['id']),
        )
        _event(connection, bet['id'], 'CANCELLED', f"{_fixture(bet, bet.get('legs'))} – {kind.lower()} stopped: {detail}", current)
    else:  # WAIT: session or gate not ready; the bet stays queued
        last = connection.execute(
            'SELECT action, detail FROM sports_ai_single_events WHERE bet_id=? ORDER BY created_at DESC LIMIT 1', (bet['id'],),
        ).fetchone()
        message = f"{_fixture(bet, bet.get('legs'))} waiting – {detail}"
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

def _bet_public(connection, row):
    item = dict(row)
    legs = [dict(l) for l in _legs(connection, row['id'])] if _kind(row) in ('COMBO', 'TICKET') else []
    item['kind'] = _kind(row)
    item['legs'] = legs
    item['fixture'] = _fixture(row, legs)
    # The mode column always says PAPER; a SUBMISSION order is what makes a bet real money.
    item['real_money'] = bool(connection.execute(
        "SELECT 1 FROM sports_ai_single_live_orders WHERE bet_id=? AND kind='SUBMISSION' AND status NOT IN ('FAILED','NOT_PLACED') LIMIT 1",
        (row['id'],),
    ).fetchone())
    item['booking_code'] = None
    if item['kind'] == 'TICKET' and str(row['id']).startswith(TICKET_PREFIX):
        code = connection.execute(
            "SELECT code FROM sports_ai_booking_codes WHERE accumulator_ticket_id=? AND verification_method='displayed' "
            "ORDER BY created_at DESC LIMIT 1", (row['id'][len(TICKET_PREFIX):],),
        ).fetchone()
        item['booking_code'] = code['code'] if code else None
    return item


def _heartbeat(connection, current):
    row = connection.execute(
        "SELECT heartbeat_at FROM sports_ai_worker_leases WHERE id='scheduler-leader'",
    ).fetchone()
    seen = _parse(row['heartbeat_at']) if row else None
    return {'last': None if not seen else seen.isoformat(),
            'fresh': bool(seen) and (current - seen).total_seconds() < 120}


_REAL_BET = """ AND EXISTS (SELECT 1 FROM sports_ai_single_live_orders o WHERE o.bet_id=sports_ai_single_bets.id
                 AND o.kind='SUBMISSION' AND o.status NOT IN ('REJECTED','FAILED','PENDING'))"""


def dashboard(connection, now=None):
    current = _now(now)
    day = local_date(current)
    # In LIVE mode the figures are real money only, as on Match Results: paper bets and rehearsals
    # made the 30-day profit +113.70 (8 won / 3 lost) when real money was +9.70 (4 / 3), 13 Nov 2026.
    real = _REAL_BET if _live(connection) else ''
    today = connection.execute(
        f"SELECT * FROM sports_ai_single_bets WHERE local_date=? AND status IN ({','.join('?' * len(COUNTED))})" + real,
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
            WHERE local_date>=? AND status IN ({','.join('?' * len(COUNTED))})""" + real,
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
    risk = staked_today / day_limits(connection)[1]
    return {
        'mode': state.get('execution_mode') or 'PAPER', 'places_wager': False, 'real_money_enabled': False,
        'generated_at': current.isoformat(), 'local_date': day, 'timezone': 'Africa/Lagos',
        'autonomous': {'running': beat['fresh'] and not state.get('paused'), 'paused': bool(state.get('paused')),
                       'worker_fresh': beat['fresh'], 'last_cycle': status.get('last_run')},
        'balance': _account_balance(connection, current),
        'rules': {'stake': STAKE, 'daily_target': _target(connection), 'daily_limit': day_limits(connection)[0],
                  'daily_stake_limit': day_limits(connection)[1], 'min_ev': MIN_EV,
                  'min_odds': MIN_ODDS, 'max_odds': MAX_ODDS, 'combined_min_odds': COMBINED_MIN_ODDS, 'combined_max_odds': COMBINED_MAX_ODDS, 'combo_max_legs': COMBO_MAX_LEGS, 'combo_min_probability': COMBO_MIN_PROBABILITY,
                  'favourite_daily_limit': FAVOURITE_DAILY_LIMIT, 'favourite_notice': FAVOURITE_NOTICE},
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
            'on_target': len(today) >= _target(connection)[0],
            'within_risk': risk <= 1,
            'system_healthy': beat['fresh'] and not status.get('error'),
        },
    }


def _execution_price_health(connection, current):
    """Read the latest relevant quotes through the fixture/time index, not all price history."""
    row = connection.execute(
        """SELECT MAX((SELECT q.observed_at FROM sports_ai_odds_snapshots q
                        WHERE q.fixture_id=f.id AND q.bookmaker='SportyBet'
                        ORDER BY q.observed_at DESC LIMIT 1)) AS latest
           FROM sports_ai_fixtures f
           WHERE f.status IN ('scheduled','not_started') AND f.starts_at>?
             AND f.starts_at<?""",
        (current.isoformat(), (current + timedelta(hours=30)).isoformat()),
    ).fetchone()
    latest = _parse(row['latest']) if row else None
    age = None if latest is None else (current - latest).total_seconds() / 60
    return {'latest': None if latest is None else latest.isoformat(),
            'age_minutes': None if age is None else round(age, 1),
            'fresh': age is not None and 0 <= age <= _max_odds_age(connection)}


def _execution_pipeline(connection, current, price):
    """Use the worker's stored snapshot; a dashboard refresh never recounts price history."""
    row = connection.execute(
        'SELECT created_at,stages_json FROM sports_ai_pipeline_snapshots ORDER BY created_at DESC LIMIT 1',
    ).fetchone()
    observed = _parse(row['created_at']) if row else None
    fresh = bool(observed and 0 <= (current - observed).total_seconds() <= 900)
    counts = {s['stage']: s['count'] for s in json.loads(row['stages_json'])} if row else {}
    specs = [('Discover', 'DISCOVERED'), ('Collect Odds', 'ODDS_AVAILABLE'), ('Validate', 'DATA_READY'),
             ('Analyse', 'PREDICTED'), ('Qualify', 'QUALIFIED'), ('Tickets & Booking Codes', 'TICKET_READY'),
             ('Settle', None), ('Learn', None), ('Optimize', None)]
    stages = [{'name': name, 'count': counts.get(key, 0) if fresh else 'Snapshot not current',
               'status': ('Completed' if counts.get(key) else 'Waiting') if fresh else 'Waiting'}
              for name, key in specs]
    stages[1].update(status='Completed' if price['fresh'] else 'Attention',
                     count='Fresh prices' if price['fresh'] else 'Prices need refreshing')
    qualification = _setting(connection, 'qualification_status', {})
    evaluated = _parse(qualification.get('evaluated_at'))
    if evaluated and 0 <= (current - evaluated).total_seconds() <= 900:
        qualified = int((qualification.get('counts') or {}).get('QUALIFIED', 0))
        stages[4].update(status='Completed' if qualified else 'Waiting', count=f'{qualified} in latest check')
    return stages, None if observed is None else observed.isoformat()


def engine_view(connection, now=None):
    current = _now(now)
    day = local_date(current)
    price = _execution_price_health(connection, current)
    stages, pipeline_at = _execution_pipeline(connection, current, price)
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
        item = _bet_public(connection, bet)
        item['stage'] = 'Placing' if index < PLACE_PER_TICK else 'Queued'
        wait = math.ceil((index + 1) / PLACE_PER_TICK) * TICK_SECONDS
        base = last or current
        item['eta_seconds'] = max(0, int((base + timedelta(seconds=wait) - current).total_seconds()))
        queue.append(item)
    recent = [_bet_public(connection, r) for r in connection.execute(
        # Placed bets only: a cancelled attempt (e.g. a match SportyBet's page could not show) was never
        # placed. It stays in the activity log and the SportyBet executions tab with its reason.
        "SELECT * FROM sports_ai_single_bets WHERE local_date=? AND status IN ('PLACED','WON','LOST','VOID') "
        "ORDER BY COALESCE(placed_at,created_at) DESC LIMIT 15",
        (day,),
    ).fetchall()]
    events = [dict(r) for r in connection.execute(
        'SELECT action, detail, created_at FROM sports_ai_single_events ORDER BY created_at DESC LIMIT 50',
    ).fetchall()]
    eligible = int(status.get('eligible') or 0)
    focus = queue[0] if queue else None
    try:
        from sportybet_account import status_view
        live = status_view(connection, current)
    except Exception:
        live = None
    blocked = (live or {}).get('execution', {}).get('blocked_by') or []
    last_order = next((order for order in (live or {}).get('orders', []) if order['kind'] == 'SUBMISSION'), None)
    last_placed = connection.execute(
        "SELECT MAX(submitted_at) FROM sports_ai_single_live_orders WHERE kind='SUBMISSION' AND status IN ('PLACED','SETTLED_WON','SETTLED_LOST','SETTLED_VOID')",
    ).fetchone()[0]
    reason = ('Execution is paused.' if state.get('paused') else '; '.join(blocked) if blocked
              else 'Waiting for an eligible selection. No bet is currently being submitted.' if not queue
              else 'Eligible selections are queued for the execution worker.')
    return {
        'mode': 'PAPER' if not live else live['execution']['mode'],
        'places_wager': bool(live and live['execution']['submission_allowed']),
        'real_money_enabled': bool(live and live['execution']['submission_allowed']),
        'generated_at': current.isoformat(),
        'strategy': {'stake': STAKE, 'daily_target': _target(connection), 'daily_limit': day_limits(connection)[0],
                     'daily_stake_limit': day_limits(connection)[1], 'min_ev': MIN_EV, 'min_odds': MIN_ODDS, 'max_odds': MAX_ODDS, 'combined_min_odds': COMBINED_MIN_ODDS, 'combined_max_odds': COMBINED_MAX_ODDS,
                     'combo_max_legs': COMBO_MAX_LEGS, 'combo_min_probability': COMBO_MIN_PROBABILITY,
                     'favourite_daily_limit': FAVOURITE_DAILY_LIMIT, 'favourite_notice': FAVOURITE_NOTICE, 'place_per_tick': PLACE_PER_TICK,
                     'tick_seconds': TICK_SECONDS},
        'autonomous': {'running': beat['fresh'] and not state.get('paused'), 'paused': bool(state.get('paused')),
                       'changed_by': state.get('changed_by'), 'changed_at': state.get('changed_at')},
        'pipeline': stages,
        'pipeline_updated_at': pipeline_at,
        'execution_status': {'state': 'BLOCKED' if blocked or state.get('paused') else 'WAITING' if not queue else 'QUEUED',
                             'reason': reason, 'last_placed_at': last_placed,
                             'last_attempt': last_order},
        'cards': {'ready': eligible, 'in_execution': len(queue), 'executed': executed,
                  'remaining': room_today(connection, day), 'exceptions': exceptions},
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
            'data_sync': price['latest'] if price['fresh'] else None,
            'latest_price_at': price['latest'], 'price_age_minutes': price['age_minutes'],
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
        return [_bet_public(connection, r) for r in rows]

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
