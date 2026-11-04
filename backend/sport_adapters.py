"""Market adapters for sports that do not yet have a validated model.

Each adapter refuses to return a probability. A name in this file is not activation.
"""

UNSUPPORTED = {
    'tennis': ('MATCH_WINNER', 'SET_HANDICAP', 'TOTAL_GAMES'),
    'volleyball': ('MATCH_WINNER', 'SET_HANDICAP', 'TOTAL_POINTS'),
    'baseball': ('MONEYLINE', 'RUN_LINE', 'TOTAL_RUNS'),
    'ice_hockey': ('MONEYLINE', 'PUCK_LINE', 'TOTAL_GOALS'),
    'cricket': ('MATCH_WINNER', 'TOTAL_RUNS'),
    'handball': ('MATCH_WINNER', 'HANDICAP', 'TOTAL_GOALS'),
}


def probability(sport, market, features=None):
    """No adapter invents a price or a probability."""
    if sport in UNSUPPORTED and market in UNSUPPORTED[sport]:
        return None
    return None
