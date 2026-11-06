"""Market adapters for sports that do not yet have a validated model.

Each adapter refuses to return a probability. A name in this file is not activation.
"""

def _tennis_names():
    from discovery_catalogue import ADDITIONAL_GROUPS, MARKET_GROUPS
    return tuple(
        item['key'] for item in (*MARKET_GROUPS, *ADDITIONAL_GROUPS)
        if item['sport'] == 'tennis' and item['key'] != 'MATCH_WINNER'
    )


UNSUPPORTED = {
    'tennis': _tennis_names(),
    'volleyball': ('SET_HANDICAP', 'TOTAL_POINTS'),
    'baseball': ('MONEYLINE', 'RUN_LINE', 'TOTAL_RUNS'),
    'ice_hockey': ('MONEYLINE',),
    'cricket': ('MATCH_WINNER', 'TOTAL_RUNS'),
    'handball': ('MATCH_WINNER', 'HANDICAP', 'TOTAL_GOALS'),
    'table_tennis': ('GAME_HANDICAP', 'TOTAL_POINTS'),
    'tischtennis': ('GAME_HANDICAP', 'TOTAL_POINTS'),
}


def probability(sport, market, features=None):
    """No adapter invents a price or a probability."""
    if sport in UNSUPPORTED and market in UNSUPPORTED[sport]:
        return None
    return None
