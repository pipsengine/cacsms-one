"""Compatibility entry point for model-backed booking-code categories."""
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
    """Run the same qualified model optimizer as the regular category worker."""
    import jackpot_low
    from high_odds_service import enqueue, generate_event
    if pool is not None:
        raise ValueError('Custom price-only pools cannot be used for model-backed category tickets')
    current = jackpot_low._now(now)
    event = enqueue(connection, 'category.requested', {'actor': actor}, f'category:{uuid.uuid4()}', current)
    reports = generate_event(connection, event, current)
    created = sum(len(report.get('created_ids', [])) for report in reports)
    return {'status': 'complete' if created else 'insufficient', 'created': created,
            'reports': reports, 'places_wager': False}
