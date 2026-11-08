"""Settlement families for sports the collector already stores.

Efootball and virtual football use the football score model on their own finals.
Tennis, table tennis, volleyball and MMA settle a match winner from the stored
score. A probability is produced only after that sport's own model passes the
existing holdout gates. This module does not invent a score or a price.
"""

GOAL_SPORTS = ('football', 'frauenfussball', 'ice_hockey', 'efootball', 'vfootball')
WINNER_SPORTS = ('tennis', 'table_tennis', 'tischtennis', 'volleyball', 'mma')
ANALYSED_SPORTS = GOAL_SPORTS + ('basketball',)

COLLECTED_SPORTS = {
    'efootball': 'Efootball',
    'vfootball': 'Virtual football',
    'table_tennis': 'Table tennis',
    'tischtennis': 'Tischtennis',
    'mma': 'MMA',
    'tennis': 'Tennis',
    'volleyball': 'Volleyball',
}


def recognised(sport):
    code = str(sport or '').lower()
    return code in ANALYSED_SPORTS or code in WINNER_SPORTS


def coverage_status(sport, active_models, active_markets):
    """Active only when this sport has a validated active model and an active market."""
    code = str(sport or '').lower()
    known = {str(item).lower() for item in active_models}
    if code in known and active_markets:
        return 'Active'
    if code in known or recognised(code):
        return 'Research'
    return 'Unsupported'


def sql_in(sports):
    return ','.join("'" + code + "'" for code in sports)


def ensure_collected_sports(connection):
    """Register a collected sport without marking it modelled."""
    for code, name in COLLECTED_SPORTS.items():
        connection.execute(
            """INSERT OR IGNORE INTO sports_ai_sports
               (code,name,prediction_status,acquisition_status,notes)
               VALUES(?,?,'not_modelled','source_catalog_only',?)""",
            (
                code, name,
                'Fixtures and prices are collected from SportyBet. A probability waits for this sport’s own final scores.',
            ),
        )
