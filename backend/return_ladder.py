"""Basketball return ladder: shortest SportyBet winner prices, one pick per match.

A ₦100 stake is the reference. Each ticket is built so SportyBet's displayed
payout (odds return plus the accumulator bonus, never above the ₦200,000,000
sports cap) lands on a naira target. The booking lane only presses Book Bet.
Nothing here places a wager.

The hit rate of a fair price is about 1 divided by the combined odds. The
bookmaker margin makes the true chance smaller. These tickets are the
highest-probability way to reach each number with the prices on the board,
and they are still long shots.
"""
import json
import math
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone

PRODUCT = 'return_ladder'
STAKE = 100
CAP = 200_000_000
# (naira payout on ₦100, how many distinct codes)
TARGETS = (
    (500_000, 1),
    (1_000_000, 1),
    (5_000_000, 1),
    (10_000_000, 1),
    (20_000_000, 1),
    (50_000_000, 1),
    (100_000_000, 1),
    (200_000_000, 3),
)
PRICE_MIN, PRICE_MAX = 1.01, 2.20
MAX_LEGS = 50
MIN_LEAD = timedelta(minutes=20)
HORIZON = timedelta(days=14)
WAT = timezone(timedelta(hours=1), 'WAT')
MAIN_PAGE = 'https://www.sportybet.com/ng/sport/basketball'
SPORT_ID = 'sr:sport:2'
NCAA_PAGE = 'https://www.sportybet.com/ng/sport/basketball/sr:category:15/sr:tournament:648/'
WINNER = re.compile(
    r'^(winner(?:\s*\(incl\.?\s*overtime\))?|moneyline|2\s*-?\s*way|match winner)$',
    re.I,
)
_booking_lock = threading.Lock()
_booking_ticket = {'id': None}


def estimate_payout(prices, stake=STAKE):
    """Cash SportyBet is likely to print, including a typical accumulator bonus.

    The booked Potential Win is what the slip actually pays. This estimate only
    decides when to stop adding favourites. 1.60 is the middle of the bonus
    multiples read off basketball booking screens on 25 Nov 2026.
    """
    odds = 1.0
    for price in prices:
        odds *= float(price)
    return min(CAP, stake * odds * 1.60), odds


def _naira(value):
    return f'₦{value:,.0f}'


def _now(now=None):
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _favourite(quotes):
    """The shorter winner price when both sides are on the board and it is a favourite."""
    home = away = None
    for quote in quotes:
        if quote['side'] == 'H':
            home = quote
        elif quote['side'] == 'A':
            away = quote
    if not home or not away:
        return None
    pick = home if home['decimal_odds'] <= away['decimal_odds'] else away
    price = float(pick['decimal_odds'])
    if price < PRICE_MIN or price > PRICE_MAX or price >= 8:
        return None
    return {**pick, 'decimal_odds': price}


def favourites_from_rows(rows):
    """One winner per match from SportyBet quote rows. Broken one-sided prices are left out."""
    grouped = {}
    for row in rows:
        label = str(row.get('market_label') or '').strip()
        if not WINNER.match(label):
            continue
        home, away = row.get('home') or '', row.get('away') or ''
        selection = str(row.get('selection_label') or '').strip()
        folded = selection.casefold()
        if folded in {'home', '1'} or folded == home.casefold():
            side = 'H'
        elif folded in {'away', '2'} or folded == away.casefold():
            side = 'A'
        else:
            continue
        key = (home.casefold(), away.casefold(), str(row.get('starts_at') or ''))
        grouped.setdefault(key, []).append({
            'side': side,
            'home': home,
            'away': away,
            'competition': row.get('competition') or '',
            'starts_at': row.get('starts_at'),
            'decimal_odds': row.get('decimal_odds'),
            'page_url': row.get('source_url') or MAIN_PAGE,
            'event_id': row.get('event_id'),
        })
    legs = []
    for quotes in grouped.values():
        pick = _favourite(quotes)
        if pick and pick.get('starts_at'):
            pick['market'] = 'MONEYLINE_HOME' if pick['side'] == 'H' else 'MONEYLINE_AWAY'
            pick['selection'] = pick['home'] if pick['side'] == 'H' else pick['away']
            legs.append(pick)
    legs.sort(key=lambda leg: (leg['decimal_odds'], leg['starts_at'], leg['home']))
    return legs


def _payout_of(legs):
    if not legs:
        return 0
    payout, _odds = estimate_payout([item['decimal_odds'] for item in legs])
    return payout


def _take(ordered, target, chosen=None):
    """Shortest favourites first. Once fifty legs are still short of the target, swap the shortest leg for the next longer favourite."""
    chosen = list(chosen or [])
    seen = {leg['fixture_id'] for leg in chosen}
    rest = [leg for leg in ordered if leg['fixture_id'] not in seen]
    goal = CAP if target >= CAP else target * 0.97
    while rest and len(chosen) < MAX_LEGS:
        leg = rest[0]
        payout = _payout_of(chosen + [leg])
        if chosen and target < CAP and payout > target and abs(payout - target) > abs(_payout_of(chosen) - target):
            rest.pop(0)
            continue
        rest.pop(0)
        chosen.append(leg)
        if payout >= goal:
            return chosen
    while chosen and rest and _payout_of(chosen) < goal:
        chosen.sort(key=lambda leg: (leg['decimal_odds'], leg.get('starts_at') or '', leg['home']))
        short = chosen[0]
        index = next((i for i, leg in enumerate(rest) if leg['decimal_odds'] > short['decimal_odds'] + 1e-9), None)
        if index is None:
            break
        chosen[0] = rest.pop(index)
    return chosen


def build_slips(pool):
    """Ten slips: one for each naira target, and three different ₦200m codes.

    Smaller targets are the same short-favourite ladder, stopped earlier.
    The extra ₦200m codes keep that core and swap the longest legs for the
    next favourites still off the ticket.
    """
    ordered = sorted(pool, key=lambda leg: (leg['decimal_odds'], leg['starts_at'], leg['home']))
    for index, leg in enumerate(ordered):
        leg.setdefault('fixture_id', f"{leg.get('home')}|{leg.get('away')}|{index}")
    slips = []
    base = []
    for target, copies in TARGETS:
        if copies == 1:
            base = _take(ordered, target, [])
            slips.append(_slip(target, 1, base, ordered))
            continue
        full = _take(ordered, target, [])
        slips.append(_slip(target, 1, full, ordered))
        used = {leg['fixture_id'] for leg in full}
        longer = [
            leg for leg in ordered
            if leg['fixture_id'] not in used and full and leg['decimal_odds'] + 1e-9 >= full[-1]['decimal_odds']
        ]
        for variant, drop in ((2, 2), (3, 4)):
            if len(full) <= drop + 6 or len(longer) < drop:
                slips.append(_slip(target, variant, list(full), ordered))
                continue
            swapped = list(full[:-drop]) + longer[:drop]
            if _payout_of(swapped) < CAP * 0.9:
                swapped = _take(ordered, target, swapped)
            slips.append(_slip(target, variant, swapped, ordered))
    return slips


def _slip(target, variant, legs, pool):
    prices = [leg['decimal_odds'] for leg in legs]
    payout, odds = estimate_payout(prices)
    used = {leg['fixture_id'] for leg in legs}
    floor = max((leg['decimal_odds'] for leg in legs), default=1)
    unused = [leg for leg in pool if leg['fixture_id'] not in used and leg['decimal_odds'] + 1e-9 >= floor]
    if len(unused) < 12:
        unused = [leg for leg in pool if leg['fixture_id'] not in used]
    reserves = unused[:12]
    fair = (1 / odds) if odds else 0
    return {
        'target': target,
        'variant': variant,
        'label': _naira(target) + (f' · {variant}' if target == CAP else ''),
        'legs': legs,
        'reserves': reserves,
        'combined_odds': odds,
        'estimated_payout': payout,
        'fair_chance': fair,
    }


EVENTS_API = 'https://www.sportybet.com/api/ng/factsCenter/pcUpcomingEvents'
FULL_BOARD_API = 'https://www.sportybet.com/api/ng/factsCenter/pcEvents'
WINNER_MARKET = '219'


def _page_for(tournament):
    category = str((tournament or {}).get('categoryId') or '')
    tournament_id = str((tournament or {}).get('id') or '')
    if category.startswith('sr:category:') and tournament_id.startswith('sr:tournament:'):
        return f'{MAIN_PAGE}/{category}/{tournament_id}/'
    if tournament_id == 'sr:tournament:648':
        return NCAA_PAGE
    return MAIN_PAGE


def fetch_live_rows():
    """Winner prices from SportyBet's public basketball board, including NCAA.

    A plain HTTP client is refused. The same browser the booking lane uses is
    accepted, so the board is read there and Book Bet is never pressed.
    """
    from browser_slots import browser_slot
    from playwright.sync_api import sync_playwright
    from source_permissions import require
    from sportybet_feed import quotes_from_payload

    require(MAIN_PAGE, 'collection')
    with browser_slot('return-ladder', wait=60, ttl=180):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(MAIN_PAGE, wait_until='domcontentloaded', timeout=45000)
                page.wait_for_timeout(1500)
                return _rows_from_board(page, quotes_from_payload)
            finally:
                browser.close()


def _rows_from_board(page, quotes_from_payload):
    """Winner prices for the basketball board. The list is a preview, so each tournament is read in full."""
    rows = {}
    tournament_ids = ['sr:tournament:648']
    deadline = datetime.now(timezone.utc) + timedelta(seconds=75)

    def absorb(payload):
        data = (payload or {}).get('data')
        tournaments = data if isinstance(data, list) else (data or {}).get('tournaments') or []
        for tournament in tournaments:
            if not isinstance(tournament, dict):
                continue
            identifier = str(tournament.get('id') or '')
            if identifier.startswith('sr:tournament:') and identifier not in tournament_ids:
                tournament_ids.append(identifier)
            page_url = _page_for(tournament)
            for row in quotes_from_payload({'data': {'tournaments': [tournament]}}, 'basketball', page_url):
                row['source_url'] = page_url
                rows[(row.get('event_id'), row.get('market_label'), row.get('selection_label'))] = row

    for number in range(1, 5):
        if datetime.now(timezone.utc) >= deadline:
            break
        response = page.context.request.get(
            f'{EVENTS_API}?sportId={SPORT_ID}&marketId={WINNER_MARKET}&pageSize=100&pageNum={number}&option=2',
            timeout=20000,
        )
        if response.status != 200:
            break
        body = response.json()
        if not ((body.get('data') or {}).get('tournaments')):
            break
        absorb(body)
    for tournament_id in list(tournament_ids)[:18]:
        if datetime.now(timezone.utc) >= deadline:
            break
        response = page.context.request.post(
            FULL_BOARD_API,
            data=json.dumps([{
                'sportId': SPORT_ID,
                'marketId': WINNER_MARKET,
                'tournamentId': [[tournament_id]],
            }]),
            headers={'Content-Type': 'application/json'},
            timeout=20000,
        )
        if response.status != 200:
            continue
        absorb(response.json())
    if not rows:
        raise RuntimeError('SportyBet basketball board returned no winner prices')
    return list(rows.values())


def load_stored_rows(connection, now=None):
    """Basketball winner quotes the collector stored in the last six hours."""
    current = _now(now)
    since = (current - timedelta(hours=6)).isoformat()
    stored = connection.execute(
        """SELECT sport, competition, home, away, starts_at, market_label, selection_label,
                  decimal_odds, source_url, event_id, observed_at
           FROM sports_ai_sportybet_observations
           WHERE sport='basketball' AND observed_at>=? AND decimal_odds>1
           ORDER BY observed_at DESC""",
        (since,),
    ).fetchall()
    newest = {}
    for row in stored:
        item = dict(row)
        key = (item['home'], item['away'], item['market_label'], item['selection_label'])
        newest.setdefault(key, item)
    return list(newest.values())


def _ensure_fixture(connection, leg, now):
    from sportybet_research import _ensure_displayed_fixture
    return _ensure_displayed_fixture(connection, {
        'sport': 'basketball',
        'competition': leg['competition'],
        'home': leg['home'],
        'away': leg['away'],
        'starts_at': leg['starts_at'],
    }, now)


def _remember_observation(connection, session_id, leg, fixture_id, observed_at):
    observation_id = str(uuid.uuid4())
    connection.execute(
        """INSERT INTO sports_ai_sportybet_observations
           (id,session_id,sport,competition,home,away,starts_at,market_label,selection_label,
            decimal_odds,source_url,observed_at,confirmation_status,line,evidence_json,created_at,event_id)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'proposed',NULL,'{}',?,?)""",
        (
            observation_id, session_id, 'basketball', leg.get('competition') or '',
            leg['home'], leg['away'], leg['starts_at'], 'Winner (incl. overtime)', leg['selection'],
            leg['decimal_odds'], leg.get('page_url') or MAIN_PAGE, observed_at, observed_at,
            leg.get('event_id'),
        ),
    )
    connection.execute(
        """UPDATE sports_ai_sportybet_observations
           SET fixture_id=?, match_status='confirmed' WHERE id=?""",
        (fixture_id, observation_id),
    )


def _leg_record(leg, fixture_id, observed_at):
    price = float(leg['decimal_odds'])
    return {
        'opportunity_id': f"return-ladder:{fixture_id}",
        'fixture_id': fixture_id,
        'sport': 'basketball',
        'competition': leg.get('competition') or '',
        'home': leg['home'],
        'away': leg['away'],
        'starts_at': leg['starts_at'],
        'market': leg['market'],
        'selection': leg['selection'],
        'decimal_odds': price,
        'probability': 1 / price,
        'edge': 0,
        'bookmaker': 'SportyBet',
        'odds_observed_at': observed_at,
        'quote_market': 'Winner (incl. overtime)',
        'quote_outcome': leg['selection'],
        'quote_line': None,
        'model_id': 'market-price',
        'model_version': 'return-ladder',
        'completeness': None,
        'reliability': None,
        'settlement_kind': 'binary',
        'page_url': leg.get('page_url') or MAIN_PAGE,
    }


def build(connection, actor='operator', now=None):
    """Read the basketball board, store one ticket per target, and leave booking to Book Bet."""
    from accumulator_engine import CONFIG_ID, _insert_ticket, default_config
    current = _now(now)
    source = 'live'
    notice = ''
    try:
        rows = fetch_live_rows()
    except Exception as error:
        rows = []
        notice = str(error)[:240]
    if not rows:
        rows = load_stored_rows(connection, current)
        source = 'stored'
    horizon = current + MIN_LEAD
    raw = []
    for leg in favourites_from_rows(rows):
        try:
            kickoff = datetime.fromisoformat(str(leg['starts_at']).replace('Z', '+00:00'))
        except (TypeError, ValueError):
            continue
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)
        if kickoff <= horizon or kickoff > current + HORIZON:
            continue
        if re.search(r'simulat|ebasketball|e-basketball|virtual', leg.get('competition') or '', re.I):
            continue
        leg['starts_at'] = kickoff.isoformat()
        raw.append(leg)
    # The same match can appear on the main board and a tournament page. Keep the shorter price.
    unique = {}
    for leg in raw:
        key = (leg['home'].casefold(), leg['away'].casefold())
        current_leg = unique.get(key)
        if current_leg is None or leg['decimal_odds'] < current_leg['decimal_odds']:
            unique[key] = leg
    pool = sorted(unique.values(), key=lambda leg: (leg['decimal_odds'], leg['starts_at'], leg['home']))
    for index, leg in enumerate(pool):
        leg['fixture_id'] = f"pending-{index}"
    slips = build_slips(pool) if pool else []
    connection.execute(
        """UPDATE sports_ai_accumulator_tickets
           SET status='expired', publication_state='REPLACED'
           WHERE status='qualified' AND json_extract(parameters_json,'$.product')=?
             AND NOT EXISTS (
               SELECT 1 FROM sports_ai_booking_codes b
               WHERE b.accumulator_ticket_id=sports_ai_accumulator_tickets.id
                 AND TRIM(COALESCE(b.code,''))<>''
             )""",
        (PRODUCT,),
    )
    observed_at = current.isoformat()
    session_id = str(uuid.uuid4())
    connection.execute(
        """INSERT INTO sports_ai_sportybet_sessions
           (id,bookmaker,mode,state,last_operation,started_at,heartbeat_at,actor)
           VALUES(?,'SportyBet','supervised','COMPLETE','return-ladder',?,?,?)""",
        (session_id, observed_at, observed_at, actor),
    )
    fixture_of = {}
    for leg in pool:
        fixture = _ensure_fixture(connection, leg, current)
        if not fixture:
            continue
        leg['fixture_id'] = fixture['id']
        fixture_of[(leg['home'].casefold(), leg['away'].casefold())] = fixture['id']
        try:
            _remember_observation(connection, session_id, leg, fixture['id'], observed_at)
        except Exception:
            pass
    config = default_config()
    config.update(max_legs=MAX_LEGS, max_per_competition=MAX_LEGS, max_per_team=1, min_legs=2)
    run_id = str(uuid.uuid4())
    connection.execute(
        """INSERT INTO sports_ai_accumulator_runs
           (id,config_id,sport_mode,status,trigger,idempotency_key,parameters_json,actor,started_at)
           VALUES(?,?,'basketball','running','return_ladder',?,'{}',?,?)""",
        (run_id, CONFIG_ID, f"return-ladder:{current.strftime('%Y%m%dT%H%M%S%f')}", actor, observed_at),
    )
    created = []
    for slip in slips:
        legs = []
        for leg in slip['legs']:
            fixture_id = leg.get('fixture_id') or fixture_of.get((leg['home'].casefold(), leg['away'].casefold()))
            if fixture_id:
                legs.append(_leg_record(leg, fixture_id, observed_at))
        if len(legs) < 2:
            continue
        prices = [leg['decimal_odds'] for leg in legs]
        payout, odds = estimate_payout(prices)
        fair = 1 / odds
        reserves = []
        for leg in slip['reserves']:
            fixture_id = leg.get('fixture_id') or fixture_of.get((leg['home'].casefold(), leg['away'].casefold()))
            if fixture_id:
                reserves.append(_leg_record(leg, fixture_id, observed_at))
        pages = {leg['fixture_id']: leg['page_url'] for leg in legs + reserves}
        one_in = f'1 in {odds:,.0f}'
        ticket = {
            'tier': 2,
            'tier_label': slip['label'],
            'tier_mode': 'standard',
            'shared_matches': True,
            'coverage': PRODUCT,
            'coverage_sport': 'basketball',
            'legs': legs,
            'signature': '|'.join(sorted(f"{leg['fixture_id']}:{leg['market']}" for leg in legs)),
            'score': fair,
            'combined_odds': odds,
            'joint_probability': fair,
            'estimated_edge': 0,
            'probability_basis': 'independent',
            'sport_mode': 'basketball',
            'issues': [('return_ladder', 'pass', 'One winner per match. Leagues are not capped: the shortest prices are the ticket.')],
            'explanation': (
                f"Basketball return ladder: ₦{STAKE} is aimed at {slip['label']} "
                f"(estimated payout {_naira(payout)} at {odds:,.2f}x, before the live bonus). "
                f"{len(legs)} winner prices, shortest first, one market per match. "
                f"A fair price would land about {one_in}. SportyBet's margin makes the true chance smaller. "
                'A booking code, not a wager.'
            ),
            'status': 'qualified',
        }
        ticket_id = _insert_ticket(connection, run_id, ticket, config, current, 'created', f"Return ladder {slip['label']}")
        row = connection.execute(
            'SELECT parameters_json FROM sports_ai_accumulator_tickets WHERE id=?', (ticket_id,),
        ).fetchone()
        parameters = json.loads(row['parameters_json'] or '{}')
        parameters.update(
            product=PRODUCT, leg_source='displayed', prize=slip['target'], stake=STAKE,
            target_payout=slip['target'], variant=slip['variant'], positive_edge=False,
            estimated_payout=round(payout, 2), pages=pages,
            reserve=[{key: leg.get(key) for key in (
                'fixture_id', 'sport', 'competition', 'home', 'away', 'starts_at', 'market',
                'selection', 'decimal_odds', 'page_url',
            )} for leg in reserves],
        )
        connection.execute(
            'UPDATE sports_ai_accumulator_tickets SET parameters_json=?, finalized=1 WHERE id=?',
            (json.dumps(parameters), ticket_id),
        )
        created.append({
            'ticket_id': ticket_id, 'target': slip['target'], 'variant': slip['variant'],
            'legs': len(legs), 'odds': round(odds, 2), 'estimated_payout': round(payout, 2),
        })
    connection.execute(
        "UPDATE sports_ai_accumulator_runs SET status='complete', summary_json=?, finished_at=? WHERE id=?",
        (json.dumps({
            'tickets': created, 'pool': len(pool), 'source': source, 'places_wager': False, 'notice': notice,
        }), observed_at, run_id),
    )
    return {
        'status': 'complete' if created else 'insufficient',
        'created': len(created),
        'tickets': created,
        'pool': len(pool),
        'source': source,
        'notice': notice,
        'places_wager': False,
    }


def _latest_code(connection, ticket_id):
    row = connection.execute(
        """SELECT code, verified_at FROM sports_ai_booking_codes
           WHERE accumulator_ticket_id=? AND TRIM(COALESCE(code,''))<>''
           ORDER BY verified_at DESC LIMIT 1""",
        (ticket_id,),
    ).fetchone()
    return dict(row) if row else None


def _latest_job(connection, ticket_id):
    row = connection.execute(
        """SELECT status, error, reason_code, updated_at FROM sports_ai_booking_jobs
           WHERE ticket_id=? ORDER BY created_at DESC LIMIT 1""",
        (ticket_id,),
    ).fetchone()
    return dict(row) if row else None


def _span(legs):
    """The WAT dates a slip covers. A slip may start today and finish on a later day."""
    moments = []
    for leg in legs:
        try:
            moment = datetime.fromisoformat(str(leg.get('starts_at')).replace('Z', '+00:00'))
        except (TypeError, ValueError):
            continue
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        moments.append(moment.astimezone(WAT))
    if not moments:
        return ''
    first, last = min(moments), max(moments)
    if first.date() == last.date():
        return first.strftime('%d %b')
    if first.year == last.year and first.month == last.month:
        return f"{first.strftime('%d')}–{last.strftime('%d %b')}"
    return f"{first.strftime('%d %b')}–{last.strftime('%d %b')}"


def _still_ahead(legs, now):
    current = _now(now)
    for leg in legs:
        try:
            moment = datetime.fromisoformat(str(leg.get('starts_at')).replace('Z', '+00:00'))
        except (TypeError, ValueError):
            continue
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        if moment > current:
            return True
    return False


def view(connection, now=None):
    """The live ladder, including slips built on an earlier day, until the matches have started."""
    current = _now(now)
    rows = connection.execute(
        """SELECT id, status, combined_odds, joint_probability, leg_count, explanation,
                  parameters_json, created_at
           FROM sports_ai_accumulator_tickets
           WHERE json_extract(parameters_json,'$.product')=?
             AND status NOT IN ('void','settled')
           ORDER BY created_at DESC""",
        (PRODUCT,),
    ).fetchall()
    tickets = []
    for row in rows:
        parameters = json.loads(row['parameters_json'] or '{}')
        if row['status'] == 'expired' and not _latest_code(connection, row['id']):
            continue
        legs = [dict(item) for item in connection.execute(
            """SELECT home, away, competition, market, selection, decimal_odds, starts_at
               FROM sports_ai_accumulator_legs WHERE ticket_id=? ORDER BY decimal_odds, starts_at""",
            (row['id'],),
        )]
        if not _still_ahead(legs, current):
            continue
        code = _latest_code(connection, row['id'])
        job = _latest_job(connection, row['id'])
        tickets.append({
            'ticket_id': row['id'],
            'target': parameters.get('prize'),
            'variant': parameters.get('variant') or 1,
            'label': _naira(parameters.get('prize') or 0) + (
                f" · {parameters.get('variant')}" if parameters.get('prize') == CAP else ''
            ),
            'legs': row['leg_count'],
            'combined_odds': row['combined_odds'],
            'estimated_payout': parameters.get('estimated_payout'),
            'displayed_payout': parameters.get('displayed_payout'),
            'fair_chance': row['joint_probability'],
            'code': code['code'] if code else None,
            'status': 'coded' if code else (job['status'] if job else row['status']),
            'job_error': (job or {}).get('error'),
            'span': _span(legs),
            'selections': legs,
            'created_at': row['created_at'],
        })
    tickets.sort(key=lambda item: (item['target'] or 0, item['variant'] or 1, item['created_at']))
    run = connection.execute(
        """SELECT summary_json, finished_at FROM sports_ai_accumulator_runs
           WHERE trigger='return_ladder' ORDER BY started_at DESC LIMIT 1""",
    ).fetchone()
    summary = json.loads(run['summary_json'] or '{}') if run else {}
    return {
        'places_wager': False,
        'stake': STAKE,
        'cap': CAP,
        'pool': summary.get('pool'),
        'source': summary.get('source'),
        'notice': summary.get('notice') or '',
        'generated_at': run['finished_at'] if run else None,
        'booking_ticket': _booking_ticket['id'],
        'tickets': tickets,
    }


def _book_worker(ticket_id):
    import asyncio
    from sportybet_booking import connect, create_job, execute_job, ticket_selections
    produced = False
    try:
        with connect() as connection:
            selections = ticket_selections(connection, ticket_id)
            if not selections:
                return
            job = create_job(connection, selections)
        if job.get('status') != 'CODE_GENERATED':
            asyncio.run(execute_job(job['id']))
        with connect() as connection:
            produced = _latest_code(connection, ticket_id) is not None
    finally:
        with _booking_lock:
            if _booking_ticket['id'] == ticket_id:
                _booking_ticket['id'] = None
    if produced:
        start_booking()


def start_booking(connection, ticket_id=None):
    """Ask SportyBet for the next ladder code. Book Bet only; the stake is not placed."""
    if ticket_id is None:
        row = connection.execute(
            """SELECT t.id FROM sports_ai_accumulator_tickets t
               WHERE t.status='qualified' AND json_extract(t.parameters_json,'$.product')=?
                 AND NOT EXISTS (
                   SELECT 1 FROM sports_ai_booking_codes b
                   WHERE b.accumulator_ticket_id=t.id AND TRIM(COALESCE(b.code,''))<>''
                 )
                 AND (SELECT MIN(l.starts_at) FROM sports_ai_accumulator_legs l WHERE l.ticket_id=t.id) > ?
               ORDER BY CAST(json_extract(t.parameters_json,'$.prize') AS INTEGER),
                        CAST(json_extract(t.parameters_json,'$.variant') AS INTEGER)
               LIMIT 1""",
            (PRODUCT, datetime.now(timezone.utc).isoformat()),
        ).fetchone()
        ticket_id = row['id'] if row else None
    if not ticket_id:
        return {'status': 'empty', 'places_wager': False}
    with _booking_lock:
        if _booking_ticket['id']:
            return {'status': 'busy', 'ticket_id': _booking_ticket['id'], 'places_wager': False}
        _booking_ticket['id'] = ticket_id
    threading.Thread(target=_book_worker, args=(ticket_id,), daemon=True).start()
    return {'status': 'booking', 'ticket_id': ticket_id, 'places_wager': False}


def remember_payout(ticket_id, payout, odds):
    from sportybet_booking import connect
    with connect() as connection:
        row = connection.execute(
            'SELECT parameters_json FROM sports_ai_accumulator_tickets WHERE id=?', (ticket_id,),
        ).fetchone()
        if not row:
            return
        parameters = json.loads(row['parameters_json'] or '{}')
        if payout is not None:
            parameters['displayed_payout'] = payout
        if odds is not None:
            parameters['displayed_odds'] = odds
        connection.execute(
            'UPDATE sports_ai_accumulator_tickets SET parameters_json=? WHERE id=?',
            (json.dumps(parameters), ticket_id),
        )


READ_WIN = '''() => {
  const root = document.querySelector('.m-betslip-wrapper, .m-betslips, .m-betslip');
  const text = root ? root.innerText : '';
  const win = text.match(/Potential Win\\s*([\\d,]+\\.\\d+)/);
  const odds = text.match(/\\bOdds\\s*([\\d,]+\\.\\d+)/);
  const number = value => value ? Number(value.replace(/,/g, '')) : null;
  return {win: number(win && win[1]), odds: number(odds && odds[1])};
}'''

READ_DIALOG = '''() => {
  const text = document.body.innerText || '';
  const start = text.indexOf('Booking Code');
  const slice = start >= 0 ? text.slice(start, start + 420) : '';
  const code = slice.match(/Booking Code\\s*([A-Z0-9]{5,16})/);
  const odds = slice.match(/Odds\\s*([\\d,]+\\.\\d+)/);
  const payout = slice.match(/Payout\\s*([\\d,]+\\.\\d+)/);
  const number = value => value ? Number(value.replace(/,/g, '')) : null;
  return {code: code && code[1], odds: number(odds && odds[1]), payout: number(payout && payout[1])};
}'''


async def _slip_count(page):
    return await page.evaluate(
        '''() => [...document.querySelectorAll('.m-item')]
          .filter(row => row.querySelector('.m-item-odds') && row.innerText.length < 400).length'''
    )


async def _drop_shortest(page):
    """Remove the shortest price. A full slip of 1.0x chalk cannot reach the naira target."""
    return await page.evaluate(
        '''() => {
          const items = [...document.querySelectorAll('.m-item')]
            .filter(row => row.querySelector('.m-item-odds') && row.innerText.length < 400);
          let best = null, price = Infinity;
          for (const item of items) {
            const value = parseFloat(item.querySelector('.m-item-odds').innerText);
            if (value < price) { price = value; best = item; }
          }
          const name = best ? (best.innerText || '').slice(0, 180) : '';
          best?.querySelector('.m-icon-delete')?.click();
          return {name, price: Number.isFinite(price) ? price : null};
        }'''
    )


async def _show_multiple(page):
    await page.evaluate(
        '''() => {
          const cell = [...document.querySelectorAll('.m-table-cell')]
            .find(node => (node.innerText || '').trim() === 'Multiple');
          if (cell) cell.click();
        }'''
    )
    await page.wait_for_timeout(600)


async def _read_win(page):
    await _show_multiple(page)
    stake = page.locator('.m-betslip-wrapper input.m-input, .m-betslips input.m-input').first
    try:
        await stake.fill(str(STAKE))
        await stake.press('Tab')
        await page.wait_for_timeout(700)
    except Exception:
        pass
    return await page.evaluate(READ_WIN)


async def _open_board(page, url, first):
    from sportybet_booking import _open_sport
    if first:
        await _open_sport(page, 'basketball', url, reuse=False)
        return
    await page.evaluate('(next) => { location.href = next }', url)
    await page.wait_for_selector('.match-row, article', timeout=25000)
    await page.wait_for_timeout(1200)


async def book_slip(page, selections):
    """Click the planned favourites, read Potential Win, then Book Bet.

    A full navigation can drop the slip. The click is repeated once when that
    happens. The code is requested only when the displayed payout is near the
    target, or on the ₦200,000,000 cap.
    """
    from sportybet_booking import connect, prepare_slip, request_displayed_code
    ticket_id = next((item.get('ticket_id') for item in selections if item.get('ticket_id')), None)
    parameters = {}
    if ticket_id:
        with connect() as connection:
            row = connection.execute(
                'SELECT parameters_json FROM sports_ai_accumulator_tickets WHERE id=?', (ticket_id,),
            ).fetchone()
        parameters = json.loads(row['parameters_json'] or '{}') if row else {}
    pages = parameters.get('pages') or {}
    target = float(parameters.get('target_payout') or 0)
    groups = {}
    for item in selections:
        url = pages.get(item.get('fixture_id')) or MAIN_PAGE
        groups.setdefault(url, []).append(item)
    seen = []
    skipped = []
    added = []
    try:
        # Stay on the basketball board and search for a match that is listed on a later day
        # or another league. Opening each league page clears the slip.
        await _open_board(page, MAIN_PAGE, True)
        part = await prepare_slip(page, selections, book=False, skip_unpriced=True, max_move=0.35)
        seen.extend(part.get('seen') or [])
        skipped.extend(part.get('skipped') or [])
        clicked = {item.get('fixture_id') for item in seen}
        for url, group in groups.items():
            if url.rstrip('/') == MAIN_PAGE.rstrip('/') or len(clicked) >= MAX_LEGS or await _slip_count(page) >= MAX_LEGS:
                continue
            missing = [item for item in group if item.get('fixture_id') not in clicked]
            if not missing:
                continue
            before = await _slip_count(page)
            await _open_board(page, url, False)
            if await _slip_count(page) + 1 < before and clicked:
                await _open_board(page, MAIN_PAGE, False)
                await prepare_slip(
                    page, [item for item in selections if item.get('fixture_id') in clicked],
                    book=False, skip_unpriced=True, max_move=0.35,
                )
                await _open_board(page, url, False)
            part = await prepare_slip(page, missing, book=False, skip_unpriced=True, max_move=0.35)
            skipped.extend(part.get('skipped') or [])
            for item in part.get('seen') or []:
                if item.get('fixture_id') not in clicked:
                    seen.append(item)
                    clicked.add(item.get('fixture_id'))
        clicked = {item.get('fixture_id') for item in seen}
        booked = [item for item in selections if item.get('fixture_id') in clicked]
        from sportybet_booking import dismiss_selection_limit
        await dismiss_selection_limit(page)
        shown = await _read_win(page)
        payout = shown.get('win')
        reserves = list(parameters.get('reserve') or [])
        while (
            reserves and len(booked) < MAX_LEGS and await _slip_count(page) < MAX_LEGS and target
            and (payout is None or (target >= CAP and payout < CAP) or (target < CAP and payout < target * 0.90))
        ):
            chunk, reserves = reserves[:3], reserves[3:]
            part = await prepare_slip(page, chunk, book=False, skip_unpriced=True, max_move=0.35)
            extra = [item for item in chunk if item.get('fixture_id') in {row.get('fixture_id') for row in (part.get('seen') or [])}]
            if not extra:
                continue
            added.extend(extra)
            booked.extend(extra)
            seen.extend(part.get('seen') or [])
            shown = await _read_win(page)
            payout = shown.get('win')
        swaps = 0
        while reserves and target and payout and swaps < 8 and len(booked) > 2 and (
            (target >= CAP and payout < CAP) or (target < CAP and payout < target * 0.97)
        ):
            if await _slip_count(page) >= MAX_LEGS:
                dropped = await _drop_shortest(page)
                if not dropped or not dropped.get('name'):
                    break
                await page.wait_for_timeout(400)
                booked = [
                    item for item in booked
                    if item.get('home') not in dropped['name'] and item.get('away') not in dropped['name']
                ]
            nxt = reserves.pop(0)
            part = await prepare_slip(page, [nxt], book=False, skip_unpriced=True, max_move=0.35)
            extra = [nxt] if nxt.get('fixture_id') in {row.get('fixture_id') for row in (part.get('seen') or [])} else []
            if not extra:
                continue
            added.extend(extra)
            booked.extend(extra)
            seen.extend(part.get('seen') or [])
            shown = await _read_win(page)
            payout = shown.get('win')
            swaps += 1
        if target and target < CAP and payout and payout > target * 1.15:
            for _ in range(3):
                if not payout or payout <= target * 1.12 or len(booked) <= 2:
                    break
                removed = await page.evaluate(
                    '''() => {
                      const items = [...document.querySelectorAll('.m-item')]
                        .filter(row => row.querySelector('.m-item-odds') && row.innerText.length < 400);
                      let best = null, price = 0;
                      for (const item of items) {
                        const value = parseFloat(item.querySelector('.m-item-odds').innerText);
                        if (value > price) { price = value; best = item; }
                      }
                      const name = best ? (best.querySelector('.m-item-team')?.innerText || '') : '';
                      best?.querySelector('.m-icon-delete')?.click();
                      return name;
                    }'''
                )
                await page.wait_for_timeout(500)
                if removed:
                    booked = [item for item in booked if item.get('home') not in removed and item.get('away') not in removed]
                shown = await _read_win(page)
                payout = shown.get('win')
        if target >= CAP and payout is not None and payout < CAP * 0.90:
            return {
                'status': 'FAILED', 'reason_code': 'PRIZE_SHORT', 'seen': seen, 'code': None, 'added': added,
                'skipped': skipped,
                'error': f'The slip shows {_naira(payout)} and the ₦200,000,000 cap was not reached. Not booked.',
            }
        if target and target < CAP and payout is not None and payout < target * 0.80:
            return {
                'status': 'FAILED', 'reason_code': 'PRIZE_SHORT', 'seen': seen, 'code': None, 'added': added,
                'skipped': skipped,
                'error': f'The slip shows {_naira(payout)} against a {_naira(target)} target. Not booked.',
            }
        result = await request_displayed_code(page, booked, seen, accept_displayed=True)
        dialog = await page.evaluate(READ_DIALOG)
        if ticket_id:
            remember_payout(ticket_id, dialog.get('payout') or payout, dialog.get('odds') or shown.get('odds'))
        result.update(seen=seen, added=added, skipped=[item for item in selections if item.get('fixture_id') not in {row.get('fixture_id') for row in seen}])
        return result
    except Exception as error:
        return {
            'status': 'FAILED', 'reason_code': 'CODE_NOT_RETURNED', 'seen': seen, 'code': None,
            'added': added, 'skipped': skipped, 'error': str(error)[:500],
        }


def build_router(auth):
    from fastapi import APIRouter, HTTPException
    router = APIRouter(prefix='/api/sports-ai/return-ladder', tags=['Basketball return ladder'])

    @router.get('')
    def current(user=Depends_auth(auth)):
        from sportybet_booking import connect
        with connect() as connection:
            return view(connection)

    @router.post('/generate')
    def generate(user=Depends_auth(auth)):
        from sportybet_booking import connect
        try:
            with connect() as connection:
                return build(connection, actor=str(user))
        except Exception as error:
            raise HTTPException(502, str(error)[:300]) from error

    @router.post('/book')
    def book(user=Depends_auth(auth)):
        from sportybet_booking import connect
        with connect() as connection:
            return start_booking(connection)

    return router


def Depends_auth(auth):
    """FastAPI needs the dependency at decoration time."""
    from fastapi import Depends
    return Depends(auth)
