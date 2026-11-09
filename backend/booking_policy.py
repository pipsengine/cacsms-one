"""Booking policy: which checks may stop a SportyBet booking-code request.

The default books every stored, non-simulation ticket at the prices SportyBet
displays when the code is requested:

* a leg that lost its live QUALIFIED decision does not stop the booking,
* a price that moved, or is older than the freshness window, does not stop it;
  the displayed price is stored on the leg and the combined odds are recomputed,
* a leg SportyBet does not show (line withdrawn, match missing, already started)
  is dropped while at least ``min_legs`` remain,
* shared matches between tickets and the 15-minute global gap no longer block.

These limits always hold, whatever the policy says:
* no wager is placed, and a code is stored only when SportyBet displays it,
* a match that has kicked off is never booked,
* an access challenge or blocked page stops the run,
* source permissions (terms review and robots.txt) still apply.

Every relaxed check leaves an audit row in sports_ai_booking_logs.
"""
import json
from datetime import datetime, timezone

KEY = 'booking_policy'

DEFAULTS = {
    'require_live_qualification': False,
    'accept_displayed_odds': True,
    'drop_unavailable_legs': True,
    'enforce_match_exposure': False,
    'min_legs': 2,
    'min_leg_odds': 1.01,
    'request_gap_minutes': 3,
    'batch_size': 5,
    # A displayed price this far from the stored one is treated as the wrong control, not a price move.
    'max_price_move': 0.30,
}

# The gates in force before this policy existed.
STRICT = {
    'require_live_qualification': True,
    'accept_displayed_odds': False,
    'drop_unavailable_legs': False,
    'enforce_match_exposure': True,
    'min_leg_odds': 1.3,
    'request_gap_minutes': 15,
    'batch_size': 1,
    'max_price_move': 0.30,
}

LIMITS = {'max_price_move': (0.05, 5.0), 'batch_size': (1, 20), 'min_legs': (1, 25), 'min_leg_odds': (1.01, 10.0), 'request_gap_minutes': (1, 60)}


def load(connection):
    row = connection.execute('SELECT value_json FROM sports_ai_settings WHERE key=?', (KEY,)).fetchone()
    stored = {}
    if row and row[0]:
        try:
            stored = json.loads(row[0])
        except json.JSONDecodeError:
            stored = {}
    return {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}


def legs_needed(policy, leg_count):
    """Fewest legs a ticket may keep: never below min_legs, never above its own size."""
    return max(1, min(int(policy['min_legs']), int(leg_count or 0)))


def current():
    from db import connect
    with connect() as connection:
        return load(connection)


def save(connection, updates, now=None):
    unknown = set(updates) - set(DEFAULTS)
    if unknown:
        raise ValueError(f'Unknown booking policy setting: {", ".join(sorted(unknown))}')
    merged = {**load(connection), **updates}
    for name in ('require_live_qualification', 'accept_displayed_odds', 'drop_unavailable_legs', 'enforce_match_exposure'):
        if not isinstance(merged[name], bool):
            raise ValueError(f'{name} must be true or false')
    for name, (low, high) in LIMITS.items():
        value = float(merged[name])
        if not low <= value <= high:
            raise ValueError(f'{name} must be between {low} and {high}')
    merged['min_legs'] = int(merged['min_legs'])
    merged['batch_size'] = int(merged['batch_size'])
    moment = (now or datetime.now(timezone.utc)).isoformat()
    connection.execute(
        """INSERT INTO sports_ai_settings(key,value_json,updated_at) VALUES(?,?,?)
           ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at""",
        (KEY, json.dumps(merged), moment),
    )
    return merged
