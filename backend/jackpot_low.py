"""Low-odds jackpot: three long accumulators of strong favourites across every sport and league.

Practical process
1. Scan every sport and league for SportyBet prices between MIN_ODDS and MAX_ODDS seen in the
   last PRICE_HOURS, on matches LEAD_MINUTES..HORIZON_HOURS away.
2. Estimate each selection's win probability from SportyBet's own price with the bookmaker margin
   removed (the market anchor gives football's model zero weight: SportyBet's price is the better
   estimate). Nothing here relies on the model.
3. Keep one selection per match (the most likely), skip simulated games, map each price to a
   market the booking browser can place; markets with a booking track record go first.
4. Deal the pool round-robin into TICKETS tickets of LEGS legs: no team twice on a ticket, at most
   MAX_PER_LEAGUE legs from one league, so the three tickets are equally strong.
5. The booking lane requests a displayed SportyBet code for each; a leg SportyBet does not show is
   dropped, but a ticket must keep at least MIN_BOOKED legs.

Honesty: every leg is short-priced, but the joint chance of 45 legs is small (typically 1-5%),
and the bookmaker margin compounds per leg. These are booking codes, never wagers; the single-
betting engine does not follow them.
"""
import json
import re
import uuid
from datetime import datetime, timedelta, timezone

from sportybet_booking import OFFER_HOURS

PRODUCT = 'jackpot_low'
TICKETS = 3
LEGS = 45
MIN_BOOKED = 40
MIN_ODDS, MAX_ODDS = 1.01, 1.20
PRICE_HOURS = 3
LEAD_MINUTES = 45
HORIZON_HOURS = 30
MAX_PER_LEAGUE = 3
DEFAULT_MARGIN = 1.06
DAILY_RUNS = 1          # one set of three jackpots per Africa/Lagos day
WAT = timezone(timedelta(hours=1), 'WAT')
SIMULATED_SPORTS = ('efootball', 'vfootball', 'ebasketball')
# Sports/markets the booking browser has placed before rank first; others fill the remainder.
PROVEN_SPORTS = ('football', 'frauenfussball', 'basketball')
# Result/winner, double chance, half-goal totals, both-teams-to-score: binary and bookable.
BOOKABLE = re.compile(r'^(HOME|AWAY|DRAW|MONEYLINE_(HOME|AWAY)|DOUBLE_CHANCE_(1X|X2|12)|(OVER|UNDER)_\d+\.5|BTTS_(YES|NO))$')
NOTICE = ('Low-odds jackpot - {legs} short-priced favourites across {sports} sports. Each leg is likely, '
          'but all {legs} must win: the estimated chance is about {chance}. A booking code, not a wager.')


def _now(now=None):
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc)


def _margins(rows):
    """Overround per fixture/market/line from every outcome priced together; default otherwise."""
    groups = {}
    for row in rows:
        key = (row['fixture_id'], row['market'], row['line'])
        groups.setdefault(key, {})[row['outcome']] = float(row['decimal_odds'])
    margins = {}
    for key, prices in groups.items():
        if len(prices) >= 2:
            total = sum(1 / odds for odds in prices.values() if odds > 1)
            if 1.0 <= total <= 1.25:
                margins[key] = total
    return margins


def scan(connection, now=None):
    """Best low-odds selection per match across every sport, most likely first."""
    from accumulator_engine import _simulated_competition
    from prediction_engine import normalize_quote_market
    current = _now(now)
    rows = connection.execute(
        """SELECT q.id, q.fixture_id, q.market, q.outcome, q.line, q.decimal_odds, q.observed_at,
                  f.sport, f.competition, f.home, f.away, f.starts_at
           FROM sports_ai_odds_snapshots q
           JOIN sports_ai_fixtures f ON f.id=q.fixture_id
           WHERE q.bookmaker='SportyBet' AND q.observed_at>=?
             AND f.status IN ('scheduled','not_started') AND f.starts_at>? AND f.starts_at<?
             -- The booking lane skips matches with no recent SportyBet page observation.
             AND EXISTS (SELECT 1 FROM sports_ai_sportybet_observations s
                         WHERE s.fixture_id=f.id AND s.observed_at>=?)
             AND q.observed_at=(SELECT MAX(q2.observed_at) FROM sports_ai_odds_snapshots q2
                                WHERE q2.fixture_id=q.fixture_id AND q2.bookmaker='SportyBet'
                                  AND q2.market=q.market AND q2.outcome=q.outcome AND IFNULL(q2.line,-1)=IFNULL(q.line,-1))""",
        ((current - timedelta(hours=PRICE_HOURS)).isoformat(),
         (current + timedelta(minutes=LEAD_MINUTES)).isoformat(),
         (current + timedelta(hours=HORIZON_HOURS)).isoformat(),
         (current - timedelta(hours=OFFER_HOURS)).isoformat()),
    ).fetchall()
    margins = _margins(rows)
    from booking_policy import load as booking_policy
    best = {}
    for row in rows:
        odds = float(row['decimal_odds'])
        if not MIN_ODDS <= odds <= MAX_ODDS:
            continue
        if row['sport'] in SIMULATED_SPORTS or _simulated_competition(row['competition']):
            continue
        fixture = {'home': row['home'], 'away': row['away']}
        try:
            market = normalize_quote_market(row['sport'], fixture, dict(row))
        except Exception:
            market = None
        if not market or not BOOKABLE.match(market):
            continue  # only markets the booking browser places, and never a line that can push
        margin = margins.get((row['fixture_id'], row['market'], row['line']), DEFAULT_MARGIN)
        probability = min(0.99, (1 / odds) / margin)
        leg = {
            'opportunity_id': f"jackpot:{row['id']}", 'fixture_id': row['fixture_id'], 'sport': row['sport'],
            'competition': row['competition'] or '', 'home': row['home'], 'away': row['away'],
            'starts_at': row['starts_at'], 'market': market, 'selection': row['outcome'] or market,
            'decimal_odds': odds, 'probability': probability, 'edge': probability * odds - 1,
            'bookmaker': 'SportyBet', 'odds_observed_at': row['observed_at'],
            'quote_market': row['market'], 'quote_outcome': row['outcome'], 'quote_line': row['line'],
            'model_id': 'market-price', 'model_version': 'sportybet-fair-price', 'completeness': None,
            'reliability': None, 'settlement_kind': 'binary',
        }
        held = best.get(row['fixture_id'])
        if held is None or (leg['probability'], -leg['decimal_odds']) > (held['probability'], -held['decimal_odds']):
            best[row['fixture_id']] = leg
    proven = lambda leg: 0 if leg['sport'] in PROVEN_SPORTS else 1
    return sorted(best.values(), key=lambda l: (proven(l), -l['probability'], l['starts_at'], l['fixture_id']))


def _teams(leg):
    return {str(leg['home'] or '').strip().casefold(), str(leg['away'] or '').strip().casefold()} - {''}


def deal(pool, tickets=TICKETS, legs=LEGS):
    """Round-robin the pool into equally strong tickets, honouring team and league limits."""
    hands = [[] for _ in range(tickets)]
    teams = [set() for _ in range(tickets)]
    leagues = [{} for _ in range(tickets)]
    turn = 0
    for leg in pool:
        for step in range(tickets):
            index = (turn + step) % tickets
            if len(hands[index]) >= legs or _teams(leg) & teams[index]:
                continue
            key = (leg['sport'], leg['competition'])
            if leagues[index].get(key, 0) >= MAX_PER_LEAGUE:
                continue
            hands[index].append(leg)
            teams[index] |= _teams(leg)
            leagues[index][key] = leagues[index].get(key, 0) + 1
            turn = index + 1
            break
        if all(len(hand) >= legs for hand in hands):
            break
    return hands


def _built_today(connection, current):
    start = current.astimezone(WAT).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    return connection.execute(
        "SELECT COUNT(DISTINCT run_id) FROM sports_ai_accumulator_tickets WHERE json_extract(parameters_json,'$.product')=? AND created_at>=?",
        (PRODUCT, start.isoformat()),
    ).fetchone()[0]


def build(connection, actor='sports-ai-worker', now=None, force=False):
    """Store today's three low-odds jackpots for the booking lane. Never places a wager."""
    from accumulator_engine import CONFIG_ID, combine_ticket, default_config, _insert_ticket
    from ticket_diversity import unallocated
    current = _now(now)
    if not force and _built_today(connection, current) >= DAILY_RUNS:
        return {'status': 'already_built', 'created': 0, 'places_wager': False}
    pool = unallocated(connection, scan(connection, current), current)
    hands = deal(pool)
    if any(len(hand) < MIN_BOOKED for hand in hands):
        return {'status': 'insufficient', 'created': 0, 'pool': len(pool),
                'legs': [len(hand) for hand in hands], 'places_wager': False}
    config = default_config()
    config.update(max_per_competition=MAX_PER_LEAGUE, max_legs=LEGS)
    run_id = str(uuid.uuid4())
    connection.execute(
        """INSERT INTO sports_ai_accumulator_runs
           (id,config_id,sport_mode,status,trigger,idempotency_key,parameters_json,actor,started_at)
           VALUES(?,?,'mixed','running','jackpot_low',?,'{}',?,?)""",
        (run_id, CONFIG_ID, f"jackpot-low:{current.strftime('%Y%m%dT%H%M%S%f')}", actor, current.isoformat()),
    )
    created = []
    for number, legs in enumerate(hands, start=1):
        combined = combine_ticket(legs, config)
        joint = 1.0
        odds = 1.0
        for leg in legs:
            joint *= leg['probability']
            odds *= leg['decimal_odds']
        sports = sorted({leg['sport'] for leg in legs})
        chance = f"{joint * 100:.1f}%" if joint >= 0.001 else 'below 0.1%'
        ticket = {
            'tier': 2, 'tier_label': f'Low-odds jackpot {number}', 'tier_mode': 'standard',
            'coverage': PRODUCT, 'coverage_sport': 'mixed', 'legs': legs,
            'signature': '|'.join(sorted(f"{l['fixture_id']}:{l['market']}" for l in legs)),
            'score': joint, 'components': {}, **combined,
            # Independent legs on different matches with no shared team: joint = product of legs.
            'combined_odds': odds, 'joint_probability': joint, 'estimated_edge': joint * odds - 1,
            'probability_basis': 'independent', 'sport_mode': 'mixed',
            'explanation': NOTICE.format(legs=len(legs), sports=len(sports), chance=chance)
                           + ' Probabilities are SportyBet prices with the bookmaker margin removed.',
            'status': 'qualified',
        }
        ticket_id = _insert_ticket(connection, run_id, ticket, config, current, 'created', f'Low-odds jackpot {number}')
        row = connection.execute('SELECT parameters_json FROM sports_ai_accumulator_tickets WHERE id=?', (ticket_id,)).fetchone()
        parameters = json.loads(row['parameters_json'] or '{}')
        parameters.update(product=PRODUCT, jackpot_number=number, positive_edge=False, min_booked_legs=MIN_BOOKED)
        connection.execute('UPDATE sports_ai_accumulator_tickets SET parameters_json=?, finalized=1 WHERE id=?',
                           (json.dumps(parameters), ticket_id))
        created.append({'ticket_id': ticket_id, 'legs': len(legs), 'odds': round(odds, 2), 'chance': chance, 'sports': sports})
    connection.execute(
        "UPDATE sports_ai_accumulator_runs SET status='complete', summary_json=?, finished_at=? WHERE id=?",
        (json.dumps({'tickets': created, 'pool': len(pool), 'places_wager': False}), current.isoformat(), run_id),
    )
    return {'status': 'complete', 'created': len(created), 'tickets': created, 'pool': len(pool), 'places_wager': False}


def require_bookable(connection, selections, now=None):
    """Booking gate for low-odds jackpots (instead of the positive-EV gate they cannot meet):
    known scheduled matches before kickoff, no shared match or team, no simulated game."""
    from accumulator_engine import _simulated_competition
    current = _now(now)
    if not selections or len(selections) > 50:
        raise ValueError('A jackpot booking needs 1 to 50 selections')
    fixtures, teams = set(), set()
    for item in selections:
        fixture_id = item.get('fixture_id')
        if fixture_id in fixtures:
            raise ValueError('Two legs share a fixture')
        fixtures.add(fixture_id)
        row = connection.execute('SELECT sport,competition,home,away,starts_at,status FROM sports_ai_fixtures WHERE id=?',
                                 (fixture_id,)).fetchone()
        if row is None:
            raise ValueError('A leg refers to an unknown fixture')
        if row['sport'] in SIMULATED_SPORTS or _simulated_competition(row['competition']):
            raise ValueError('Simulated games are not allowed on a jackpot')
        for team in (row['home'], row['away']):
            key = str(team or '').strip().casefold()
            if key and key in teams:
                raise ValueError(f'{team} appears in two legs')
            teams.add(key)
        try:
            kickoff = datetime.fromisoformat(str(row['starts_at']).replace('Z', '+00:00'))
        except ValueError:
            kickoff = None
        if kickoff is None or kickoff.astimezone(timezone.utc) <= current or row['status'] not in ('scheduled', 'not_started'):
            raise ValueError('A leg has started or is not scheduled')
    return {'legs': {}, 'product': PRODUCT, 'positive_edge': False}
