"""Operator stop: retired jackpot products cannot generate tickets or booking codes.

The weekly jackpot ladder (prize_ladder: ₦1M-₦200M, legs 1.60-2.95) was re-enabled by the owner on
12 Nov 2026. The single-tier ₦200M category ('jackpot') and the low-odds jackpot stay stopped.
"""
import json

REASON = 'This jackpot product is disabled by the operator; jackpots come from the weekly jackpot ladder.'
STOPPED_PRODUCTS = ('jackpot', 'jackpot_low')


def disabled_result():
    return {'status': 'disabled', 'created': 0, 'reason': REASON, 'places_wager': False}


def is_stopped(connection, ticket_id):
    if not ticket_id:
        return False
    row = connection.execute(
        'SELECT parameters_json FROM sports_ai_accumulator_tickets WHERE id=?', (ticket_id,),
    ).fetchone()
    params = json.loads(row[0] or '{}') if row else {}
    if params.get('product') in STOPPED_PRODUCTS:
        return True
    return bool(connection.execute(
        "SELECT 1 FROM sports_ai_high_odds_tickets WHERE ticket_id=? AND category_id='jackpot'", (ticket_id,),
    ).fetchone())


def require_enabled(connection, ticket_id):
    if is_stopped(connection, ticket_id):
        raise ValueError(REASON)
