"""Category cards (objective 3) from SportyBet prices: one booking-code ticket per card per run.

Owner's structure (12 Nov 2026): the eight cards (2.00-4.99 ... 500-1,000) are filled with tickets
whose every selection is priced 1.60-2.95, combined to land inside the card's range.

Why market prices: the model-qualified pool the optimizer used (high_odds_service) is empty on most
days (12 Nov: 0 of ~2,000 selections qualified), so the cards stayed at 0/5. Like the jackpot ladder,
these tickets use SportyBet's current prices with the margin removed, reliable markets only (result,
double chance, winner), sports whose results are settled, and the league track-record filter.
At most DAILY_PER_CARD tickets per card per Africa/Lagos day, one new ticket per card per run, a
match on one card ticket at a time, first match at least MIN_LEAD_HOURS away. Codes only - the
system never stakes on these.
"""
import json
import math
import uuid
from datetime import timedelta, timezone

PRODUCT = 'category_market'
DAILY_PER_CARD = 5
HORIZON_HOURS = 36
MIN_LEAD_HOURS = 2
LEG_MIN, LEG_MAX = 1.60, 2.95
WAT = timezone(timedelta(hours=1), 'WAT')


def _day_start(current):
    return current.astimezone(WAT).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat()


def made_today(connection, current):
    counts = {}
    for row in connection.execute(
        """SELECT json_extract(parameters_json,'$.category') c, COUNT(*) n FROM sports_ai_accumulator_tickets
           WHERE json_extract(parameters_json,'$.product')=? AND created_at>=?
             AND json_extract(parameters_json,'$.retired') IS NULL GROUP BY 1""",
        (PRODUCT, _day_start(current)),
    ):
        counts[row[0]] = row[1]
    return counts


def _busy_matches(connection):
    """Matches already on an open category ticket: a match sits on one card ticket at a time."""
    return {row[0] for row in connection.execute(
        """SELECT l.fixture_id FROM sports_ai_accumulator_legs l JOIN sports_ai_accumulator_tickets t ON t.id=l.ticket_id
           WHERE json_extract(t.parameters_json,'$.product')=? AND t.status='qualified'""", (PRODUCT,))}


def market_pool(connection, current):
    import jackpot_low
    import prize_ladder
    from league_quality import filter_legs
    saved = (jackpot_low.MIN_ODDS, jackpot_low.MAX_ODDS)
    jackpot_low.MIN_ODDS, jackpot_low.MAX_ODDS = LEG_MIN, LEG_MAX
    try:
        scanned = jackpot_low.scan(connection, current, horizon_hours=HORIZON_HOURS,
                                   keep=prize_ladder.reliable_keep(connection))
    finally:
        jackpot_low.MIN_ODDS, jackpot_low.MAX_ODDS = saved
    if connection.in_transaction:
        connection.commit()
    earliest = (current + timedelta(hours=MIN_LEAD_HOURS)).isoformat()
    legs = filter_legs(connection, [l for l in scanned if l['sport'] in prize_ladder.LADDER_SPORTS])
    legs = prize_ladder.reliable(legs, prize_ladder.hit_rates(connection))
    import pick_scoring
    history = pick_scoring.table()
    return [l for l in legs if str(l['starts_at']) >= earliest and LEG_MIN <= l['decimal_odds'] <= LEG_MAX
            and pick_scoring.proven(l, history)]   # evidence-backed selection types only (14 Nov 2026)


def build(connection, actor='sports-ai-worker', now=None, pool=None):
    """One new ticket per card that is below DAILY_PER_CARD today. Never places a wager."""
    import jackpot_low
    import prize_ladder
    from accumulator_engine import CONFIG_ID, combine_ticket, default_config, _insert_ticket
    from high_odds_service import categories, JACKPOT
    current = jackpot_low._now(now)
    cards = [c for c in categories(connection) if c['id'] != JACKPOT]
    counts = made_today(connection, current)
    due = [c for c in cards if counts.get(c['id'], 0) < DAILY_PER_CARD]
    if not due:
        return {'status': 'daily_target_reached', 'created': 0, 'places_wager': False}
    busy = _busy_matches(connection)
    pool = [l for l in (market_pool(connection, current) if pool is None else pool) if l['fixture_id'] not in busy]
    # One active ticket per match across every product (ticket_diversity): matches already on the
    # jackpot ladder or a Sure Odds ticket stay out, or storing the ticket would be refused.
    from ticket_diversity import unallocated, TicketExposureConflict
    pool = unallocated(connection, pool, current)
    config = default_config()
    config.update(max_per_competition=prize_ladder.MAX_PER_LEAGUE, max_legs=prize_ladder.MAX_LEGS)
    created, skipped, run_id, used = [], [], None, set()
    for card in sorted(due, key=lambda c: c['min_odds']):
        low, high = float(card['min_odds']), float(card['max_odds'])
        legs = prize_ladder.pick_range([l for l in pool if l['fixture_id'] not in used], low, high,
                                       math.sqrt(low * high), {}, current, min_legs=2,
                                       max_legs=min(8, prize_ladder.leg_cap(math.sqrt(low * high))))
        if not legs:
            skipped.append(card['id'])
            continue
        odds = math.prod(l['decimal_odds'] for l in legs)
        if not low <= round(odds, 2) <= high:
            skipped.append(card['id'])
            continue
        used |= {l['fixture_id'] for l in legs}
        if run_id is None:
            run_id = str(uuid.uuid4())
            connection.execute(
                """INSERT INTO sports_ai_accumulator_runs
                   (id,config_id,sport_mode,status,trigger,idempotency_key,parameters_json,actor,started_at)
                   VALUES(?,?,'mixed','running','category_market',?,'{}',?,?)""",
                (run_id, CONFIG_ID, f"category-market:{current.strftime('%Y%m%dT%H%M%S%f')}", actor, current.isoformat()),
            )
        joint = math.prod(l['probability'] for l in legs)
        one_in = f'1 in {1 / joint:,.0f}' if joint > 0 else 'negligible'
        ticket = {
            'tier': 2, 'tier_label': f"Card {card['label']}", 'tier_mode': 'standard', 'shared_matches': False,
            'coverage': PRODUCT, 'coverage_sport': 'mixed', 'legs': legs,
            'signature': '|'.join(sorted(f"{l['fixture_id']}:{l['market']}" for l in legs)),
            'score': joint, 'components': {}, **combine_ticket(legs, config),
            'combined_odds': odds, 'joint_probability': joint, 'estimated_edge': joint * odds - 1,
            'probability_basis': 'independent', 'sport_mode': 'mixed',
            'explanation': (f"Category {card['label']}: {len(legs)} selections priced {LEG_MIN:.2f}-{LEG_MAX:.2f}, "
                            f"combined {odds:,.2f}; estimated chance {one_in}{prize_ladder.one_cut_note(legs)}. "
                            'Selections are proven types only (history); chances are SportyBet prices without the '
                            'margin, corrected by history. A booking code, not a wager.'),
            'status': 'qualified',
        }
        try:
            ticket_id = _insert_ticket(connection, run_id, ticket, config, current, 'created', f"Card {card['label']}")
        except TicketExposureConflict:
            skipped.append(card['id'])   # a match was taken meanwhile; the next run builds this card
            continue
        row = connection.execute('SELECT parameters_json FROM sports_ai_accumulator_tickets WHERE id=?', (ticket_id,)).fetchone()
        parameters = json.loads(row['parameters_json'] or '{}')
        import pick_scoring
        parameters.update(one_cut_probability=pick_scoring.one_cut_chance([l['probability'] for l in legs]))
        parameters.update(product=PRODUCT, category=card['id'], positive_edge=False, places_wager=False,
                          reserve=prize_ladder.reserves(pool, legs, prize_ladder.hit_rates(connection)))
        connection.execute('UPDATE sports_ai_accumulator_tickets SET parameters_json=?, finalized=1 WHERE id=?',
                           (json.dumps(parameters), ticket_id))
        created.append({'card': card['id'], 'legs': len(legs), 'odds': round(odds, 2), 'chance': one_in})
    if run_id:
        connection.execute(
            "UPDATE sports_ai_accumulator_runs SET status='complete', summary_json=?, finished_at=? WHERE id=?",
            (json.dumps({'tickets': created, 'skipped': skipped, 'pool': len(pool), 'places_wager': False}),
             current.isoformat(), run_id),
        )
    if connection.in_transaction:
        connection.commit()
    return {'status': 'complete' if created else 'insufficient', 'created': len(created), 'tickets': created,
            'skipped': skipped, 'pool': len(pool), 'places_wager': False}
