"""Operator stop: jackpot products cannot generate tickets or booking codes."""
import json

REASON = 'Jackpot tickets and codes are disabled by the operator.'


def disabled_result():
    return {'status': 'disabled', 'created': 0, 'reason': REASON, 'places_wager': False}


def is_stopped(connection, ticket_id):
    if not ticket_id:
        return False
    row = connection.execute(
        'SELECT parameters_json FROM sports_ai_accumulator_tickets WHERE id=?', (ticket_id,),
    ).fetchone()
    params = json.loads(row[0] or '{}') if row else {}
    if params.get('product') in ('jackpot', 'jackpot_low', 'prize_ladder'):
        return True
    return bool(connection.execute(
        "SELECT 1 FROM sports_ai_high_odds_tickets WHERE ticket_id=? AND category_id='jackpot'", (ticket_id,),
    ).fetchone())


def require_enabled(connection, ticket_id):
    if is_stopped(connection, ticket_id):
        raise ValueError(REASON)
