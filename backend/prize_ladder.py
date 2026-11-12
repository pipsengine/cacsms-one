"""Prize ladder: one booking code per prize, from ₦100,000 to ₦200,000,000 on a ₦100 stake.

Practical process
1. Pool: every sport and league, a SportyBet price LEG_MIN..LEG_MAX seen in the last few hours,
   bookable binary markets only, one selection per match (jackpot_low.scan with wider odds).
2. Probabilities are SportyBet prices with the margin removed; nothing relies on the model.
3. For each prize (largest first) pick legs whose odds sit near the required average, so each leg
   stays modest (about 1.2-1.6): no team twice, at most MAX_PER_LEAGUE per league, each match on
   at most MAX_SHARE ladder tickets. Build to BUILD_LOW..BUILD_HIGH x the prize odds, leaving room
   for SportyBet to drop a leg it is not showing.
4. Booking requests a displayed code. Legs SportyBet does not show are dropped, but the booked
   ticket must still pay at least its prize at the displayed prices, or it is not booked.

Honesty: the chance of winning is roughly 1 / (prize odds) less the bookmaker margin on every
leg - from about 1 in 2,000 for ₦100,000 to about 1 in 5-10 million for ₦200,000,000. These are
booking codes; the system never stakes on them and single betting does not follow them.
"""
import json
import math
import uuid
from datetime import timedelta, timezone

PRODUCT = 'prize_ladder'
STAKE = 100
# Owner's structure (12 Nov 2026), objective 3: six jackpots on a ₦100 stake, every selection priced
# 1.60-2.95, matches from Monday to Sunday of the current week, one set per week.
PRIZES = (1_000_000, 10_000_000, 20_000_000, 50_000_000, 100_000_000, 200_000_000)
LEG_MIN, LEG_MAX = 1.60, 2.95
LEG_AIM = 1.95            # preferred per-leg odds: the ticket length follows from it
WEEKLY_RUNS = 1
LADDER_VERSION = 2       # the six-prize, 1.60-2.95 design of 12 Nov 2026
# Sports whose results the system collects and settles; the league record filter applies on top.
LADDER_SPORTS = ('football', 'basketball', 'ice_hockey', 'tennis')
MAX_LEGS = 45             # SportyBet accepts 50; 45 leaves room
MIN_LEGS = 6
MAX_PER_LEAGUE = 3
MAX_SHARE = 2             # a match may sit on at most two ladder tickets
BUILD_LOW, BUILD_HIGH, BUILD_AIM = 1.15, 1.50, 1.30   # built odds as a multiple of the prize odds
DAILY_RUNS = 1
RESERVES = 30            # spare legs tried on the page when SportyBet does not show a ticket leg
RESERVE_KEYS = ('opportunity_id', 'fixture_id', 'sport', 'competition', 'home', 'away', 'starts_at', 'market',
                'selection', 'decimal_odds', 'probability', 'edge', 'bookmaker', 'odds_observed_at',
                'quote_market', 'quote_outcome', 'quote_line', 'model_id', 'model_version', 'settlement_kind')
# Football has the best settled record (12 Nov 2026), so it may fill up to 60% of a ticket; the long
# ₦200M ticket (~22 legs at 1.60-2.95) still needs a second sport.
MAX_SPORT_SHARE = 0.60
MAX_HOURS_AHEAD = 48      # pages list near matches; hit rate fell to 60% beyond 24 h
# Every leg must start at least this long after the ticket is built: booking a long ladder takes
# time, and the owner needs time to stake the code. (2026-11-11: 15 of 17 codes had a leg kick off
# before they could be used, because legs from 45 minutes ahead were allowed.)
MIN_LEAD_HOURS = 3
LATER_PENALTY = 0.04      # per day ahead, on the odds-fit distance: sooner matches first
# Booking reliability. Over/under and BTTS lines are often not shown or not switchable on the page
# (football totals 50%, women's totals 20% on 2026-11-11), so the ladder uses result, double chance
# and winner markets only, and drops any sport/market the page found < MIN_HIT_RATE of the time.
RELIABLE_FAMILIES = ('result', 'double', 'winner')
MIN_HIT_RATE = 0.55
MIN_HIT_SAMPLES = 5    # no sport may fill more than 30% of a ticket
FOOTBALL = ('football', 'frauenfussball')
FOOTBALL_BONUS = 0.15     # football wins ties on fit (log-odds distance)
WAT = timezone(timedelta(hours=1), 'WAT')


def _release(connection):
    """Commit pending writes so SQLite's single write lock is not held through a long read-only scan
    (the booking lane waits on that lock; holding it for minutes lost bookings)."""
    if connection.in_transaction:
        connection.commit()


def prize_odds(prize):
    return prize / STAKE


def _teams(leg):
    return {str(leg['home'] or '').strip().casefold(), str(leg['away'] or '').strip().casefold()} - {''}


def family(market):
    name = str(market or '').upper()
    if name.startswith('DOUBLE_CHANCE'):
        return 'double'
    if name.startswith('MONEYLINE_'):
        return 'winner'
    if name in ('HOME', 'AWAY', 'DRAW'):
        return 'result'
    return 'other'


def hit_rates(connection, days=7):
    """Share of attempted legs SportyBet's page showed, per (sport, market family), from recent bookings."""
    from datetime import datetime
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    counts = {}
    for row in connection.execute(
        """SELECT selections_json, odds_json FROM sports_ai_booking_jobs
           WHERE updated_at>=? AND odds_json IS NOT NULL AND status IN ('CODE_GENERATED','FAILED','ODDS_CHANGED')""",
        (since,),
    ):
        try:
            selections, seen = json.loads(row['selections_json'] or '[]'), json.loads(row['odds_json'] or '[]')
        except (TypeError, ValueError):
            continue
        if not isinstance(seen, list) or not seen:
            continue
        found = {item.get('fixture_id') for item in seen if item.get('page_odds')}
        for item in selections:
            key = (item.get('sport'), family(item.get('market')))
            hit, total = counts.get(key, (0, 0))
            counts[key] = (hit + (item.get('fixture_id') in found), total + 1)
    return counts


def reliable(legs, rates):
    """Keep legs the booking page reliably shows."""
    kept = []
    for leg in legs:
        fam = family(leg['market'])
        if fam not in RELIABLE_FAMILIES:
            continue
        hit, total = rates.get((leg['sport'], fam), (0, 0))
        if total >= MIN_HIT_SAMPLES and hit / total < MIN_HIT_RATE:
            continue
        kept.append(leg)
    return kept


def _days_ahead(leg, now):
    from datetime import datetime
    try:
        kickoff = datetime.fromisoformat(str(leg['starts_at']).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return 1.0
    return max(0.0, (kickoff - now).total_seconds() / 86400)


def pick(pool, prize, uses, now=None):
    """Legs for one prize: odds near the required per-leg average, landing in the build band."""
    target = prize_odds(prize)
    return pick_range(pool, target * BUILD_LOW, target * BUILD_HIGH, target * BUILD_AIM, uses, now)


def pick_range(pool, low, high, aim, uses, now=None, min_legs=MIN_LEGS):
    """Legs whose combined odds land in [low, high], close to `aim`. Used by the jackpot ladder and,
    with min_legs=2, by the category cards (category_market.py)."""
    slots = max(min_legs, min(MAX_LEGS - 3, round(math.log(aim) / math.log(LEG_AIM))))
    chosen, odds, teams, leagues, sports = [], 1.0, set(), {}, {}
    sport_cap = max(2, math.ceil(MAX_SPORT_SHARE * slots))
    available = [leg for leg in pool if uses.get(leg['fixture_id'], 0) < MAX_SHARE]
    while (odds < low or len(chosen) < min_legs) and len(chosen) < MAX_LEGS:
        left = max(1, slots - len(chosen))
        want = (aim / odds) ** (1 / left)
        best, score = None, None
        for leg in available:
            if _teams(leg) & teams or leagues.get((leg['sport'], leg['competition']), 0) >= MAX_PER_LEAGUE:
                continue
            if sports.get(leg['sport'], 0) >= sport_cap:
                continue
            if odds * leg['decimal_odds'] > high:
                continue
            reach = odds * leg['decimal_odds']
            # A leg that completes the ticket inside the band beats one that does not; among those,
            # the one closest to BUILD_AIM x the prize.
            # Only a leg that also reaches the minimum number of legs can finish the ticket.
            finishing = reach >= low and len(chosen) + 1 >= min_legs
            distance = abs(math.log(reach / aim)) if finishing else abs(math.log(leg['decimal_odds']) - math.log(want))
            if leg['sport'] in FOOTBALL:
                distance = max(0.0, distance - FOOTBALL_BONUS)
            if now is not None:
                distance += LATER_PENALTY * _days_ahead(leg, now)
            fit = (0 if finishing else 1, round(distance, 3), sports.get(leg['sport'], 0))
            rank = (fit, uses.get(leg['fixture_id'], 0), -leg['probability'])
            if score is None or rank < score:
                best, score = leg, rank
        if best is None:
            break
        chosen.append(best)
        available.remove(best)
        odds *= best['decimal_odds']
        teams |= _teams(best)
        key = (best['sport'], best['competition'])
        leagues[key] = leagues.get(key, 0) + 1
        sports[best['sport']] = sports.get(best['sport'], 0) + 1
    if odds < low or len(chosen) < min_legs:
        return None
    return chosen


def _hours_to_sunday(current):
    """Through the end of this Sunday (Africa/Lagos), at least 30 hours: weekend football counts."""
    local = current.astimezone(WAT)
    end = (local + timedelta(days=(6 - local.weekday()) % 7)).replace(hour=23, minute=59, second=0, microsecond=0)
    return max(30, (end - local).total_seconds() / 3600)


def rebuild(connection, actor='operator', now=None):
    """Replace this week's ladder tickets that have no displayed code yet, then build again."""
    import jackpot_low
    current = jackpot_low._now(now)
    retired = 0
    for row in connection.execute(
        """SELECT t.id FROM sports_ai_accumulator_tickets t
           WHERE json_extract(t.parameters_json,'$.product')=? AND t.status='qualified'
             AND NOT EXISTS (SELECT 1 FROM sports_ai_booking_codes b WHERE b.accumulator_ticket_id=t.id
                             AND b.verification_method='displayed' AND TRIM(COALESCE(b.code,''))<>'')
             AND NOT EXISTS (SELECT 1 FROM sports_ai_booking_jobs j WHERE j.ticket_id=t.id AND j.status='PREPARING')""",
        (PRODUCT,),
    ).fetchall():
        connection.execute("UPDATE sports_ai_accumulator_tickets SET status='expired' WHERE id=?", (row['id'],))
        connection.execute("UPDATE sports_ai_booking_jobs SET status='SUPERSEDED', updated_at=? WHERE ticket_id=? AND status='QUEUED'",
                           (current.isoformat(), row['id']))
        retired += 1
    if connection.in_transaction:
        connection.commit()   # release the write lock before the long market scan
    result = build(connection, actor, current, force=True)
    result['retired'] = retired
    return result


HEAL_REASONS = ('PRIZE_SHORT', 'LEGS_NOT_OFFERED', 'QUALIFICATION_LAPSED', 'FIXTURE_NOT_FOUND', 'ODDS_CHANGED',
                'BOOKING_TIMEOUT', 'WORKER_STOPPED')
MAX_HEALS = 3


def heal(connection, actor='sports-ai-worker', now=None):
    """Rebuild each prize whose booking SportyBet refused, with fresh legs and without the matches
    the page did not show. At most MAX_HEALS rebuilds per prize per WAT day."""
    import jackpot_low
    current = jackpot_low._now(now)
    _release(connection)
    start = current.astimezone(WAT).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat()
    healed = []
    for ticket in connection.execute(
        """SELECT t.id, json_extract(t.parameters_json,'$.prize') AS prize FROM sports_ai_accumulator_tickets t
           WHERE json_extract(t.parameters_json,'$.product')=? AND t.status='qualified'
             AND NOT EXISTS (SELECT 1 FROM sports_ai_booking_codes b WHERE b.accumulator_ticket_id=t.id
                             AND b.verification_method='displayed' AND TRIM(COALESCE(b.code,''))<>'')""",
        (PRODUCT,),
    ).fetchall():
        job = connection.execute(
            """SELECT status, reason_code, selections_json, odds_json FROM sports_ai_booking_jobs
               WHERE ticket_id=? ORDER BY updated_at DESC LIMIT 1""", (ticket['id'],),
        ).fetchone()
        if not job or job['status'] != 'FAILED' or job['reason_code'] not in HEAL_REASONS:
            continue
        rebuilt = connection.execute(
            """SELECT COUNT(*) FROM sports_ai_accumulator_tickets WHERE json_extract(parameters_json,'$.product')=?
               AND json_extract(parameters_json,'$.prize')=? AND status='expired' AND created_at>=?""",
            (PRODUCT, ticket['prize'], start),
        ).fetchone()[0]
        if rebuilt >= MAX_HEALS:
            continue
        try:
            shown = {item.get('fixture_id') for item in json.loads(job['odds_json'] or '[]') if item.get('page_odds')}
            missing = [item.get('fixture_id') for item in json.loads(job['selections_json'] or '[]')
                       if item.get('fixture_id') not in shown]
        except (TypeError, ValueError):
            missing = []
        connection.execute("UPDATE sports_ai_accumulator_tickets SET status='expired' WHERE id=?", (ticket['id'],))
        if connection.in_transaction:
            connection.commit()
        result = build(connection, actor, current, force=True, prizes=[int(ticket['prize'])], exclude=missing)
        healed.append({'prize': int(ticket['prize']), 'created': result['created'], 'excluded': len(missing)})
    return healed


def _built_this_week(connection, current):
    local = current.astimezone(WAT)
    start = (local - timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    # Only ladders of the current design count: the 17-prize, 1.10-2.20 ladders built before
    # 12 Nov 2026 must not use up this week's set.
    return connection.execute(
        """SELECT COUNT(DISTINCT run_id) FROM sports_ai_accumulator_tickets WHERE json_extract(parameters_json,'$.product')=?
           AND json_extract(parameters_json,'$.ladder_version')=? AND created_at>=?""",
        (PRODUCT, LADDER_VERSION, start.isoformat()),
    ).fetchone()[0]


def _naira(value):
    return f'₦{value:,.0f}'


def build(connection, actor='sports-ai-worker', now=None, force=False, prizes=None, exclude=()):
    """Store this week's jackpot ladder (one ticket per prize) for the booking lane. Never places a wager.
    With `prizes`, only those prize tickets are built; `exclude` lists matches to leave out."""
    import jackpot_low
    from accumulator_engine import CONFIG_ID, combine_ticket, default_config, _insert_ticket
    from ticket_diversity import unallocated
    current = jackpot_low._now(now)
    if not force and _built_this_week(connection, current) >= WEEKLY_RUNS:
        return {'status': 'already_built', 'created': 0, 'places_wager': False}
    _release(connection)
    saved = (jackpot_low.MIN_ODDS, jackpot_low.MAX_ODDS)
    jackpot_low.MIN_ODDS, jackpot_low.MAX_ODDS = LEG_MIN, LEG_MAX
    try:
        pool = jackpot_low.scan(connection, current, horizon_hours=_hours_to_sunday(current))
    finally:
        jackpot_low.MIN_ODDS, jackpot_low.MAX_ODDS = saved
    rates = hit_rates(connection)
    earliest = (current + timedelta(hours=MIN_LEAD_HOURS)).isoformat()
    from league_quality import filter_legs
    pool = filter_legs(connection, [leg for leg in pool if leg['sport'] in LADDER_SPORTS])
    candidates = [leg for leg in reliable(pool, rates)
                  if leg['fixture_id'] not in set(exclude) and str(leg['starts_at']) >= earliest]
    # Matches on other products' active tickets stay out; matches on ladder tickets may be shared
    # (up to MAX_SHARE), so offer those back.
    ladder_matches = {row[0] for row in connection.execute(
        """SELECT l.fixture_id FROM sports_ai_accumulator_legs l JOIN sports_ai_accumulator_tickets t ON t.id=l.ticket_id
           WHERE json_extract(t.parameters_json,'$.product')=? AND t.status='qualified'""", (PRODUCT,))}
    free = {leg['fixture_id'] for leg in unallocated(connection, candidates, current)}
    pool = [leg for leg in candidates if leg['fixture_id'] in free or leg['fixture_id'] in ladder_matches]
    config = default_config()
    config.update(max_per_competition=MAX_PER_LEAGUE, max_legs=MAX_LEGS)
    uses, created, skipped, run_id = {}, [], [], None
    if prizes:
        # Matches already on the other active ladder tickets count toward MAX_SHARE.
        for row in connection.execute(
            """SELECT l.fixture_id FROM sports_ai_accumulator_legs l JOIN sports_ai_accumulator_tickets t ON t.id=l.ticket_id
               WHERE json_extract(t.parameters_json,'$.product')=? AND t.status='qualified'""", (PRODUCT,),
        ).fetchall():
            uses[row['fixture_id']] = uses.get(row['fixture_id'], 0) + 1
    for prize in sorted(prizes or PRIZES, reverse=True):          # longest tickets first
        legs = pick(pool, prize, uses, current)
        if not legs:
            skipped.append(prize)
            continue
        if run_id is None:
            run_id = str(uuid.uuid4())
            connection.execute(
                """INSERT INTO sports_ai_accumulator_runs
                   (id,config_id,sport_mode,status,trigger,idempotency_key,parameters_json,actor,started_at)
                   VALUES(?,?,'mixed','running','prize_ladder',?,'{}',?,?)""",
                (run_id, CONFIG_ID, f"prize-ladder:{current.strftime('%Y%m%dT%H%M%S%f')}", actor, current.isoformat()),
            )
        for leg in legs:
            uses[leg['fixture_id']] = uses.get(leg['fixture_id'], 0) + 1
        odds = math.prod(leg['decimal_odds'] for leg in legs)
        joint = math.prod(leg['probability'] for leg in legs)
        combined = combine_ticket(legs, config)
        one_in = f"1 in {1 / joint:,.0f}" if joint > 0 else 'negligible'
        ticket = {
            'tier': 2, 'tier_label': f'Prize {_naira(prize)}', 'tier_mode': 'standard', 'shared_matches': True,
            'coverage': PRODUCT, 'coverage_sport': 'mixed', 'legs': legs,
            'signature': '|'.join(sorted(f"{l['fixture_id']}:{l['market']}" for l in legs)),
            'score': joint, 'components': {}, **combined,
            'combined_odds': odds, 'joint_probability': joint, 'estimated_edge': joint * odds - 1,
            'probability_basis': 'independent', 'sport_mode': 'mixed',
            'explanation': (f"Prize ladder: ₦{STAKE} pays at least {_naira(prize)} (built at {odds:,.0f}x, "
                            f"{_naira(odds * STAKE)}). {len(legs)} legs across "
                            f"{len({l['competition'] for l in legs})} leagues; estimated chance {one_in}. "
                            'Probabilities are SportyBet prices with the margin removed. A booking code, not a wager.'),
            'status': 'qualified',
        }
        ticket_id = _insert_ticket(connection, run_id, ticket, config, current, 'created', f'Prize ladder {_naira(prize)}')
        row = connection.execute('SELECT parameters_json FROM sports_ai_accumulator_tickets WHERE id=?', (ticket_id,)).fetchone()
        parameters = json.loads(row['parameters_json'] or '{}')
        parameters.update(product=PRODUCT, ladder_version=LADDER_VERSION, prize=prize, stake=STAKE,
                          min_odds=prize_odds(prize), positive_edge=False,
                          reserve=reserves(pool, legs, rates))
        connection.execute('UPDATE sports_ai_accumulator_tickets SET parameters_json=?, finalized=1 WHERE id=?',
                           (json.dumps(parameters), ticket_id))
        created.append({'prize': prize, 'legs': len(legs), 'odds': round(odds, 1), 'chance': one_in})
    if run_id:
        connection.execute(
            "UPDATE sports_ai_accumulator_runs SET status='complete', summary_json=?, finished_at=? WHERE id=?",
            (json.dumps({'tickets': created, 'skipped': skipped, 'pool': len(pool), 'places_wager': False}),
             current.isoformat(), run_id),
        )
    _release(connection)
    return {'status': 'complete' if created else 'insufficient', 'created': len(created), 'tickets': created,
            'skipped': skipped, 'pool': len(pool), 'places_wager': False}


RESERVE_PER_SPORT = 8


def _rate(rates, leg):
    hit, total = (rates or {}).get((leg['sport'], family(leg['market'])), (0, 0))
    return hit / total if total >= MIN_HIT_SAMPLES else 0.75   # unmeasured: middling


def reserves(pool, legs, rates=None):
    """Spare legs for the booking page: not on the ticket, no team already on it, modest odds.
    Ranked by how reliably SportyBet's page has shown that sport/market, then soonest kickoff,
    and spread across sports (at most RESERVE_PER_SPORT each)."""
    teams = set().union(*(_teams(leg) for leg in legs))
    used = {leg['fixture_id'] for leg in legs}
    spare = [leg for leg in pool if leg['fixture_id'] not in used and not (_teams(leg) & teams)
             and LEG_MIN <= leg['decimal_odds'] <= LEG_MAX]
    spare.sort(key=lambda leg: (-round(_rate(rates, leg), 2), leg['starts_at'], -leg['probability']))
    picked, seen_teams, per_sport = [], set(), {}
    for leg in spare:
        if _teams(leg) & seen_teams or per_sport.get(leg['sport'], 0) >= RESERVE_PER_SPORT:
            continue
        picked.append({key: leg.get(key) for key in RESERVE_KEYS})
        seen_teams |= _teams(leg)
        per_sport[leg['sport']] = per_sport.get(leg['sport'], 0) + 1
        if len(picked) >= RESERVES:
            break
    return picked


def reserve_legs(connection, ticket_id):
    row = connection.execute('SELECT parameters_json FROM sports_ai_accumulator_tickets WHERE id=?', (ticket_id,)).fetchone()
    if not row:
        return []
    parameters = json.loads(row['parameters_json'] or '{}')
    return list(parameters.get('reserve') or []) if parameters.get('product') == PRODUCT else []


def add_legs(connection, ticket_id, legs):
    """Store reserve legs that were placed on the booked slip, so the ticket matches its code."""
    for leg in legs:
        connection.execute(
            """INSERT INTO sports_ai_accumulator_legs
               (id,ticket_id,opportunity_id,fixture_id,sport,competition,home,away,starts_at,market,selection,
                decimal_odds,probability,edge,bookmaker,odds_observed_at,quote_market,quote_outcome,quote_line,
                model_id,model_version,data_completeness,reliability,settlement_kind,qualification_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (str(uuid.uuid4()), ticket_id, leg.get('opportunity_id') or f"reserve:{leg['fixture_id']}", leg['fixture_id'],
             leg['sport'], leg.get('competition') or '', leg['home'], leg['away'], leg['starts_at'], leg['market'],
             leg.get('selection') or leg['market'], float(leg['decimal_odds']), leg.get('probability'), leg.get('edge'),
             'SportyBet', leg.get('odds_observed_at'), leg.get('quote_market') or leg['market'],
             leg.get('quote_outcome') or leg['market'], leg.get('quote_line'), leg.get('model_id') or 'market-price',
             leg.get('model_version') or '', None, None, 'binary', json.dumps({'decision': 'reserve'})),
        )
    legs_now = connection.execute('SELECT decimal_odds, probability FROM sports_ai_accumulator_legs WHERE ticket_id=?',
                                  (ticket_id,)).fetchall()
    odds = math.prod(float(r['decimal_odds']) for r in legs_now)
    joint = math.prod(float(r['probability'] or 0) for r in legs_now)
    connection.execute('UPDATE sports_ai_accumulator_tickets SET combined_odds=?, joint_probability=?, leg_count=? WHERE id=?',
                       (odds, joint, len(legs_now), ticket_id))


def minimum_odds(connection, ticket_id):
    """The prize odds a booked ladder ticket must still reach after SportyBet drops legs."""
    row = connection.execute('SELECT parameters_json FROM sports_ai_accumulator_tickets WHERE id=?', (ticket_id,)).fetchone()
    if not row:
        return None
    parameters = json.loads(row['parameters_json'] or '{}')
    return float(parameters['min_odds']) if parameters.get('product') == PRODUCT else None
