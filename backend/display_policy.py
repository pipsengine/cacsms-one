"""Floors for what the system is allowed to present.

Analysis can score every market. A live board shows one market per fixture,
the highest calibrated win probability, with model confidence used only when
those probabilities are equal. A missing probability is never treated as zero.
"""

LIVE_PROBABILITY = 0.80
DRAW_PROBABILITY = 0.60
SLIP_PROBABILITY = 0.70
LIVE_LIMIT = 10


def above(probability, floor):
    try:
        value = float(probability)
    except (TypeError, ValueError):
        return False
    return value == value and value > floor


def confidence_from_metrics(metrics_json):
    import json
    try:
        error = json.loads(metrics_json or '{}').get('calibration_error')
        value = 1 - float(error)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if value != value:
        return None
    return max(0.0, min(1.0, value))
