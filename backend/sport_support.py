"""Settlement families for sports the collector already stores.

Efootball and virtual football use the football score model on their own finals.
Tennis, table tennis, volleyball and MMA settle a match winner from the stored
score. A probability is produced only after that sport's own model passes the
existing holdout gates. This module does not invent a score or a price.
"""

GOAL_SPORTS = ('football', 'frauenfussball', 'efootball', 'vfootball')
WINNER_SPORTS = (
    'tennis', 'table_tennis', 'volleyball', 'mma', 'baseball', 'handball', 'ice_hockey',
    'cricket', 'americanfootball', 'badminton', 'boxing', 'darts', 'counterstrike',
    'dota2', 'ebasketball', 'futsal', 'lol', 'rugby', 'snooker',
    'beach_volleyball', 'basketball_3x3', 'eice_hockey', 'etennis',
)
ANALYSED_SPORTS = GOAL_SPORTS + ('basketball',) + WINNER_SPORTS
PIPELINE_VERSION = 'coverage-v3'


def canonical_sport(sport, competition=''):
    import re
    code = str(sport or '').lower()
    code = {'tischtennis': 'table_tennis', 'icehockey': 'ice_hockey',
            'american_football': 'americanfootball'}.get(code, code)
    if code == 'football' and re.search(r'\b(women|woman|ladies|frauen|female)\b', competition or '', re.I):
        return 'frauenfussball'
    return code

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
    code = canonical_sport(sport)
    return code in ANALYSED_SPORTS or code in WINNER_SPORTS


def coverage_status(sport, active_models, active_markets):
    """Active only when this sport has a validated active model and an active market."""
    code = canonical_sport(sport)
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
