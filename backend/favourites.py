"""Favourites: labelled NO-EDGE accumulator tickets for booking codes.

Why this exists: since the market anchor (market_anchor.py) set football's model weight
to 0 on held-out evidence, no football selection can show a positive edge, basketball is
quarantined until a loss-audited candidate passes, and nothing QUALIFIES. Every edge
product then stops, and no booking code is produced at all.

This lane books short accumulators of strong favourites instead. It is NOT a value
product and says so on every ticket: SportyBet's margin (~5-6% per leg) means these
tickets lose money on average. The edge products are untouched and resume on their own.

A leg must pass every check the edge lanes apply EXCEPT positive edge:
- latest prediction of an active, validated model, binary settlement
- a stored qualification decision whose only blockers are price/edge related
  (never a quarantine, quality hold, model or calibration suspension)
- a linked SportyBet price no older than the qualification freshness window
- market-anchored probability >= MIN_PROBABILITY, odds MIN_ODDS..MAX_ODDS
- a market the browser booking has placed before; never basketball or simulated games
- fixture scheduled, kickoff at least LEAD_MINUTES away

Tickets: 3-4 legs on different fixtures with no shared team, at most 2 per league,
combined odds TICKET_ODDS. At most DAILY_LIMIT per Africa/Lagos day. Nothing here
places a wager; the booking lane only requests a displayed booking code.
"""
import json
import re
import uuid
import zlib
from datetime import datetime, timedelta, timezone

PRODUCT = 'favourites'
MIN_PROBABILITY = 0.70
MIN_ODDS, MAX_ODDS = 1.15, 1.90
TICKET_ODDS = (1.50, 2.50)
MIN_LEGS, MAX_LEGS = 3, 4
MAX_PER_LEAGUE = 2
LEAD_MINUTES = 20
HORIZON_HOURS = 30
PER_RUN = 3
DAILY_LIMIT = 12
WAT = timezone(timedelta(hours=1), 'WAT')
# Decision reasons that only concern the price or the edge. Anything else (quarantine,
# quality hold, model/calibration suspension, identity) keeps the selection out.
PRICE_OR_EDGE_REASONS = ('QUALIFIED_OK', 'EDGE_BELOW_MARKET', 'ODDS_STALE', 'MARKET_UNAVAILABLE',
                         'MARKET_DISAGREEMENT', 'CONSERVATIVE_EV_NOT_POSITIVE')
EXCLUDED_SPORTS = ('basketball', 'ebasketball', 'efootball', 'vfootball')
MARKETS = re.compile(r'^(HOME|AWAY|DRAW|DOUBLE_CHANCE_(1X|X2|12)|(OVER|UNDER)_\d+(\.5)?|BTTS_(YES|NO))$')
NOTICE = ('Favourites ticket - NO positive edge. Strong favourites priced by SportyBet; the bookmaker '
          'margin means this ticket is expected to lose money over time. A booking code, not a wager.')


def _now(now=None):
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc)


def _parse(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _freshness_minutes(connection):
    from qualification_engine import load_policy
    try:
        return max(1, int(load_policy(connection).get('odds_max_age_minutes') or 15))
    except (TypeError, ValueError):
        return 15


def _release(connection):
    """Commit pending writes so SQLite's single write lock is not held through a long read-only scan
    (the booking lane waits on that lock; holding it for minutes lost bookings)."""
    if connection.in_transaction:
        connection.commit()


def candidates(connection, now=None, fixture_id=None, market=None):
    """Best favourite per fixture, highest anchored probability first. With fixture_id/market,
    only that selection is evaluated (used to re-check a queued favourite before placement)."""
    from accumulator_engine import _reliability, _settlement_kind, _simulated_competition
    from market_anchor import anchor, load as load_anchors
    current = _now(now)
    fresh = (current - timedelta(minutes=_freshness_minutes(connection))).isoformat()
    anchors = load_anchors(connection)
    rows = connection.execute(
        f"""SELECT d.reason_code, p.id AS prediction_id, p.market, p.outcome, p.probability, p.settlement_json,
                   m.id AS model_id, m.version AS model_version, m.metrics_json,
                   f.id AS fixture_id, f.sport, f.competition, f.home, f.away, f.starts_at,
                   q.decimal_odds, q.observed_at, q.market AS quote_market, q.outcome AS quote_outcome, q.line AS quote_line,
                   s.completeness
            FROM sports_ai_qualification_decisions d
            JOIN sports_ai_engine_predictions p ON p.id=d.prediction_id
            JOIN sports_ai_model_registry m ON m.id=p.model_id
             AND m.active=1 AND m.lifecycle='active' AND m.calibration_status='validated'
            JOIN sports_ai_fixtures f ON f.id=d.fixture_id AND f.status IN ('scheduled','not_started')
            LEFT JOIN sports_ai_feature_snapshots s ON s.id=p.feature_id
            JOIN sports_ai_odds_snapshots q ON q.id=(
              SELECT q2.id FROM sports_ai_value_opportunities o2
              JOIN sports_ai_odds_snapshots q2 ON q2.id=o2.odds_id
              WHERE o2.prediction_id=p.id AND q2.bookmaker='SportyBet' AND q2.decimal_odds>1
              ORDER BY q2.observed_at DESC LIMIT 1)
            WHERE f.starts_at>? AND f.starts_at<?
              AND f.sport NOT IN ({','.join('?' * len(EXCLUDED_SPORTS))})
              AND d.reason_code IN ({','.join('?' * len(PRICE_OR_EDGE_REASONS))})
              AND q.observed_at>=?
              AND p.created_at=(SELECT MAX(p2.created_at) FROM sports_ai_engine_predictions p2
                                WHERE p2.fixture_id=p.fixture_id AND p2.market=p.market)
              -- The newest decision on THIS prediction. Decisions on an old model's predictions for the
              -- same match are often newer (MODEL_INACTIVE) and must not mask the active model's own.
              AND d.updated_at=(SELECT MAX(d2.updated_at) FROM sports_ai_qualification_decisions d2
                                WHERE d2.prediction_id=d.prediction_id)
              AND NOT EXISTS (SELECT 1 FROM sports_ai_data_quality_issues i
                              WHERE i.fixture_id=f.id AND i.resolved_at IS NULL AND i.severity IN ('high','critical'))
              AND (? IS NULL OR f.id=?) AND (? IS NULL OR upper(p.market)=upper(?))""",
        ((current + timedelta(minutes=LEAD_MINUTES)).isoformat(),
         (current + timedelta(hours=HORIZON_HOURS)).isoformat(),
         *EXCLUDED_SPORTS, *PRICE_OR_EDGE_REASONS, fresh, fixture_id, fixture_id, market, market),
    ).fetchall()
    best = {}
    for row in rows:
        market = str(row['market'] or '').upper()
        if not MARKETS.match(market) or _simulated_competition(row['competition']):
            continue
        if _settlement_kind(row['settlement_json']) != 'binary':
            continue
        try:
            odds, model_p = float(row['decimal_odds']), float(row['probability'])
        except (TypeError, ValueError):
            continue
        if not (MIN_ODDS <= odds <= MAX_ODDS) or not 0 < model_p < 1:
            continue
        anchored = anchor(anchors, row['sport'], row['competition'], model_p, odds)
        probability = anchored['probability']
        if probability < MIN_PROBABILITY:
            continue
        leg = {
            'opportunity_id': row['prediction_id'], 'fixture_id': row['fixture_id'], 'sport': row['sport'],
            'competition': row['competition'] or '', 'home': row['home'], 'away': row['away'],
            'starts_at': row['starts_at'], 'market': market, 'selection': row['outcome'] or market,
            'decimal_odds': odds, 'probability': probability, 'edge': probability * odds - 1,
            'bookmaker': 'SportyBet', 'odds_observed_at': row['observed_at'],
            'quote_market': row['quote_market'] or market, 'quote_outcome': row['quote_outcome'] or row['outcome'] or market,
            'quote_line': row['quote_line'], 'model_id': row['model_id'], 'model_version': row['model_version'] or '',
            'completeness': row['completeness'], 'reliability': _reliability(row['metrics_json']),
            'settlement_kind': 'binary', 'model_probability': model_p, 'anchor_weight': anchored['weight'],
        }
        held = best.get(row['fixture_id'])
        if held is None or (leg['probability'], leg['decimal_odds']) > (held['probability'], held['decimal_odds']):
            best[row['fixture_id']] = leg
    return sorted(best.values(), key=lambda l: (-l['probability'], l['starts_at'], l['fixture_id']))


def lane_of(leg):
    """Which product a favourite belongs to. Stable per match, so the ticket and single lanes can
    split each price batch without coordinating: below single betting's minimum (single_betting.MIN_ODDS)
    only a ticket can use it; otherwise an even split by match id."""
    from single_betting import MIN_ODDS
    if float(leg['decimal_odds']) < MIN_ODDS - 1e-9:
        return 'ticket'
    return 'single' if zlib.crc32(str(leg['fixture_id']).encode()) % 2 == 0 else 'ticket'


def _singles_cap_reached(connection, current):
    from single_betting import FAVOURITE_DAILY_LIMIT, _favourites_today, local_date
    return _favourites_today(connection, local_date(current)) >= FAVOURITE_DAILY_LIMIT


def _single_fixtures(connection):
    """Matches backed by single betting (singles, combined bets or followed tickets)."""
    rows = connection.execute(
        """SELECT fixture_id FROM sports_ai_single_bets WHERE status!='CANCELLED'
           UNION SELECT l.fixture_id FROM sports_ai_single_bet_legs l
           JOIN sports_ai_single_bets b ON b.id=l.bet_id WHERE b.status!='CANCELLED'""",
    ).fetchall()
    return {row[0] for row in rows}


def _teams(leg):
    return {str(leg['home'] or '').strip().casefold(), str(leg['away'] or '').strip().casefold()} - {''}


def pack(pool):
    """Greedy, most probable first: 3-4 legs, distinct fixtures and teams, <= 2 per league,
    combined odds inside TICKET_ODDS. Each leg is used at most once."""
    remaining = list(pool)
    tickets = []
    while len(remaining) >= MIN_LEGS:
        chosen, odds, teams, leagues = [], 1.0, set(), {}
        for leg in list(remaining):
            if len(chosen) >= MAX_LEGS or (len(chosen) >= MIN_LEGS and odds >= TICKET_ODDS[0]):
                break
            if _teams(leg) & teams or leagues.get(leg['competition'], 0) >= MAX_PER_LEAGUE:
                continue
            if odds * leg['decimal_odds'] > TICKET_ODDS[1]:
                continue
            chosen.append(leg)
            odds *= leg['decimal_odds']
            teams |= _teams(leg)
            leagues[leg['competition']] = leagues.get(leg['competition'], 0) + 1
        if len(chosen) < MIN_LEGS or not TICKET_ODDS[0] <= odds <= TICKET_ODDS[1]:
            break
        tickets.append(chosen)
        for leg in chosen:
            remaining.remove(leg)
    return tickets


def _today_count(connection, current):
    start = current.astimezone(WAT).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    return connection.execute(
        """SELECT COUNT(*) FROM sports_ai_accumulator_tickets
           WHERE json_extract(parameters_json,'$.product')=? AND created_at>=?""",
        (PRODUCT, start.isoformat()),
    ).fetchone()[0]


def build_favourite_tickets(connection, actor='sports-ai-worker', now=None):
    """Store up to PER_RUN favourites tickets for the booking lane. No wager is placed."""
    from accumulator_engine import CONFIG_ID, combine_ticket, default_config, _insert_ticket, open_selection_signatures
    from ticket_diversity import unallocated
    current = _now(now)
    _release(connection)
    room = min(PER_RUN, DAILY_LIMIT - _today_count(connection, current))
    if room <= 0:
        return {'status': 'daily_limit', 'created': 0, 'candidates': 0, 'places_wager': False}
    singles_full = _singles_cap_reached(connection, current)
    backed = _single_fixtures(connection)
    pool = [leg for leg in candidates(connection, current)
            if leg['fixture_id'] not in backed and (singles_full or lane_of(leg) == 'ticket')]
    pool = unallocated(connection, pool, current)
    open_signatures = open_selection_signatures(connection)
    config = default_config()
    config['max_per_competition'] = MAX_PER_LEAGUE
    created, run_id = [], None
    for legs in pack(pool)[:room]:
        signature = '|'.join(sorted(f"{l['fixture_id']}:{l['market']}" for l in legs))
        if signature in open_signatures:
            continue
        combined = combine_ticket(legs, config)
        if combined['probability_basis'] != 'independent' or not combined.get('joint_probability'):
            continue
        if run_id is None:
            run_id = str(uuid.uuid4())
            connection.execute(
                """INSERT INTO sports_ai_accumulator_runs
                   (id,config_id,sport_mode,status,trigger,idempotency_key,parameters_json,actor,started_at)
                   VALUES(?,?,'mixed','running','favourites',?,'{}',?,?)""",
                (run_id, CONFIG_ID, f"favourites:{current.strftime('%Y%m%dT%H%M%S%f')}", actor, current.isoformat()),
            )
        names = sorted({l['competition'] for l in legs if l['competition']})
        ticket = {
            'tier': 2, 'tier_label': 'Favourites (no edge)', 'tier_mode': 'standard',
            'coverage': PRODUCT, 'coverage_sport': 'mixed', 'legs': legs, 'signature': signature,
            'score': combined['joint_probability'], 'components': {}, **combined,
            'sport_mode': combined['sport_mode'],
            'explanation': (f"{NOTICE} {len(legs)} favourites across {len(names)} league(s) ({'; '.join(names)}); "
                            f"each leg has a market-anchored win probability of at least {MIN_PROBABILITY:.0%} "
                            f"and a SportyBet price from the last {_freshness_minutes(connection)} minutes. "
                            + combined['explanation']),
            'status': 'qualified',
        }
        ticket_id = _insert_ticket(connection, run_id, ticket, config, current, 'created', 'Favourites (no edge) ticket')
        row = connection.execute('SELECT parameters_json FROM sports_ai_accumulator_tickets WHERE id=?', (ticket_id,)).fetchone()
        parameters = json.loads(row['parameters_json'] or '{}')
        parameters.update(product=PRODUCT, positive_edge=False, notice=NOTICE)
        connection.execute('UPDATE sports_ai_accumulator_tickets SET parameters_json=?, finalized=1 WHERE id=?',
                           (json.dumps(parameters), ticket_id))
        open_signatures.add(signature)
        created.append(ticket_id)
    if run_id:
        connection.execute(
            "UPDATE sports_ai_accumulator_runs SET status='complete', summary_json=?, finished_at=? WHERE id=?",
            (json.dumps({'tickets': len(created), 'candidates': len(pool), 'positive_edge': False, 'places_wager': False}),
             current.isoformat(), run_id),
        )
    _release(connection)
    return {'status': 'complete' if created else 'insufficient', 'created': len(created), 'ticket_ids': created,
            'candidates': len(pool), 'places_wager': False}


def require_bookable(connection, selections, now=None):
    """Booking gate for favourites tickets, used INSTEAD of prediction_integrity.require_bookable,
    whose positive-EV requirement this product cannot meet by design. Every other structural
    check is kept: known scheduled fixture before kickoff, no shared fixture or team, an allowed
    market, and no basketball or simulated game."""
    from accumulator_engine import _simulated_competition
    current = _now(now)
    if not selections:
        raise ValueError('Booking requires at least one selection')
    fixtures, teams = set(), set()
    for item in selections:
        fixture_id, market = item.get('fixture_id'), str(item.get('market') or '').upper()
        if fixture_id in fixtures:
            raise ValueError('Two legs share a fixture; correlated legs cannot be booked together')
        fixtures.add(fixture_id)
        fixture = connection.execute(
            'SELECT sport,competition,home,away,starts_at,status FROM sports_ai_fixtures WHERE id=?', (fixture_id,),
        ).fetchone()
        if fixture is None:
            raise ValueError('A leg refers to an unknown fixture')
        if fixture['sport'] in EXCLUDED_SPORTS or _simulated_competition(fixture['competition']):
            raise ValueError(f"{fixture['sport']} is not allowed on a favourites ticket")
        if not MARKETS.match(market):
            raise ValueError(f'{market} is not an allowed favourites market')
        for team in (fixture['home'], fixture['away']):
            key = str(team or '').strip().casefold()
            if key and key in teams:
                raise ValueError(f'{team} appears in two legs; correlated legs cannot be booked together')
            teams.add(key)
        kickoff = _parse(fixture['starts_at'])
        if kickoff is None or kickoff <= current or fixture['status'] not in ('scheduled', 'not_started'):
            raise ValueError('A leg has started or is not scheduled')
    return {'legs': {}, 'product': PRODUCT, 'positive_edge': False}
