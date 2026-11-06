"""Starting competitions and market groups for discovery.

A group is registered even when no model can settle it. A probability is
produced only by the existing score or point model. This module does not
invent prices, scores, or probabilities.
"""

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path


def _group(sport, key, selection, period, kind, definition, requirement):
    return {
        'sport': sport,
        'key': key,
        'selection': selection,
        'period': period,
        'kind': kind,
        'definition': definition,
        'requirement': requirement,
    }


MARKET_GROUPS = (
    _group('football', 'FULL_TIME_RESULT', 'Home, Draw, Away', 'ft', 'score', 'Full-time 1X2 from the score distribution.', 'Final score.'),
    _group('football', 'DOUBLE_CHANCE', '1X, 12, X2', 'ft', 'score', 'Double chance from the same score distribution.', 'Final score.'),
    _group('football', 'DRAW_NO_BET', 'Home DNB, Away DNB', 'ft', 'score', 'Draw no bet. A draw is a push.', 'Final score.'),
    _group('football', 'TOTAL_GOALS', 'Over/Under 0.5 through 6.5 and other lines', 'ft', 'score', 'Full-time goals over or under a line.', 'Final score.'),
    _group('football', 'TEAM_TOTAL_GOALS', 'Home/Away Over/Under 0.5, 1.5, 2.5, 3.5', 'ft', 'score', 'One team’s goals over or under a line.', 'Final score.'),
    _group('football', 'BOTH_TEAMS_TO_SCORE', 'Yes, No', 'ft', 'score', 'Both teams score at least one goal.', 'Final score.'),
    _group('football', 'ASIAN_HANDICAP', 'Quarter-goal handicaps from -2.5 to +2.5', 'ft', 'score', 'Asian handicap on the full-time margin.', 'Final score and the displayed line.'),
    _group('football', 'EUROPEAN_HANDICAP', 'Home, Draw, Away on a three-way handicap', 'ft', 'score', 'Three-way handicap from the full-time score. It is not an Asian handicap.', 'Final score and the displayed line.'),
    _group('football', 'FIRST_HALF_1X2', 'Half-time 1X2, double chance, draw no bet', '1h', 'data_pending', 'Half-time result.', 'A stored half-time score.'),
    _group('football', 'HTFT', '1/1, 1/X, 1/2, X/1, X/X, X/2, 2/1, 2/X, 2/2', 'htft', 'data_pending', 'Half-time and full-time combination.', 'A stored half-time score and the full-time score.'),
    _group('football', 'HALF_TIME_GOALS', 'Over/Under 0.5, 1.5, 2.5', '1h', 'data_pending', 'Goals at half-time.', 'Stored first-half goals.'),
    _group('football', 'CORRECT_SCORE', '0-0, 1-0, 1-1, 2-0, 2-1 and other scores', 'ft', 'score', 'One cell of the score distribution.', 'Final score. A correct-score price is still required before activation.'),
    _group('football', 'FIRST_LAST_GOAL', 'Home, Away, No Goal', 'ft', 'data_pending', 'First or last team to score.', 'The order of goals. The final score does not record it.'),
    _group('football', 'ODD_EVEN_GOALS', 'Odd, Even', 'ft', 'score', 'Odd or even full-time goals.', 'Final score.'),
    _group('football', 'CORNERS', 'Over/Under 6.5 through 11.5 and other lines', 'ft', 'data_pending', 'Total corners.', 'A stored corner count.'),
    _group('football', 'TEAM_CORNERS', 'Home/Away corner totals and corner handicap', 'ft', 'data_pending', 'Team corners.', 'Stored team corner counts.'),
    _group('football', 'COMBINATION_MARKETS', 'Result and goals, double chance and goals, BTTS and result', 'ft', 'score', 'A defined pair from the same score distribution. Lines on one match stay dependent.', 'Final score and the exact combination SportyBet displays.'),
    _group('basketball', 'MATCH_WINNER', 'Home, Away, including overtime where the price says so', 'ft', 'score', 'Moneyline from the point model.', 'Final score including overtime when that is the stored result.'),
    _group('basketball', 'POINT_SPREAD', 'Home/Away spreads, including -1.5, -4.5, -7.5 and other lines', 'ft', 'score', 'Point spread from the same point distribution. Lines on one match are not independent.', 'Final score and the displayed line.'),
    _group('basketball', 'MATCH_TOTAL', 'Over/Under 145.5, 165.5, 185.5, 205.5, 225.5 and other lines', 'ft', 'score', 'Match total from the same point distribution.', 'Final score and the displayed line.'),
    _group('basketball', 'TEAM_TOTAL_POINTS', 'Home/Away total points', 'ft', 'score', 'One team’s points over or under a line.', 'Final score and the displayed line.'),
    _group('basketball', 'FIRST_HALF_WINNER', 'Home, Away, Draw if offered', '1h', 'data_pending', 'First-half winner.', 'A stored first-half score.'),
    _group('basketball', 'FIRST_HALF_TOTAL', 'First-half total points', '1h', 'data_pending', 'First-half total.', 'A stored first-half score.'),
    _group('basketball', 'SECOND_HALF_WINNER', 'Home, Away, Draw if offered', '2h', 'data_pending', 'Second-half winner.', 'A stored second-half score.'),
    _group('basketball', 'SECOND_HALF_TOTAL', 'Second-half total points', '2h', 'data_pending', 'Second-half total.', 'A stored second-half score.'),
    _group('basketball', 'QUARTER_1_WINNER', 'Home, Away, Draw if offered', 'q1', 'data_pending', 'First-quarter winner.', 'A stored quarter score.'),
    _group('basketball', 'QUARTER_2_WINNER', 'Home, Away, Draw if offered', 'q2', 'data_pending', 'Second-quarter winner.', 'A stored quarter score.'),
    _group('basketball', 'QUARTER_3_WINNER', 'Home, Away, Draw if offered', 'q3', 'data_pending', 'Third-quarter winner.', 'A stored quarter score.'),
    _group('basketball', 'QUARTER_4_WINNER', 'Home, Away, Draw if offered', 'q4', 'data_pending', 'Fourth-quarter winner.', 'A stored quarter score.'),
    _group('basketball', 'QUARTER_TOTAL', 'Quarter total points', 'quarter', 'data_pending', 'Points in one quarter.', 'A stored quarter score.'),
    _group('basketball', 'WINNING_MARGIN', 'Home/Away by a displayed point band', 'ft', 'score', 'Winning margin from the point distribution. Bands on one match are not independent.', 'Final score and the displayed band.'),
    _group('basketball', 'HALF_QUARTER_HANDICAP', 'Handicap for a half or quarter', 'period', 'data_pending', 'Period handicap.', 'A stored period score and the displayed line.'),
    _group('tennis', 'MATCH_WINNER', 'Player 1, Player 2', 'match', 'score', 'Match winner from the stored score. A probability waits for a model trained on tennis results.', 'A final score for this match and this sport’s own model.'),
    _group('tennis', 'FIRST_SET_WINNER', 'Player 1, Player 2', 'set1', 'unsupported', 'First-set winner.', 'Set scores and a tennis model.'),
    _group('tennis', 'SECOND_SET_WINNER', 'Player 1, Player 2', 'set2', 'unsupported', 'Second-set winner.', 'Set scores and a tennis model.'),
    _group('tennis', 'THIRD_SET_WINNER', 'Player 1, Player 2', 'set3', 'unsupported', 'Third-set winner when the match has a third set.', 'Set scores and a tennis model.'),
    _group('tennis', 'CORRECT_SET_SCORE', '2-0, 2-1, 3-0, 3-1, 3-2', 'match', 'unsupported', 'Correct set score.', 'Set scores and a tennis model.'),
    _group('tennis', 'TOTAL_GAMES', 'Over/Under 18.5, 20.5, 21.5, 22.5, 23.5 and other lines', 'match', 'unsupported', 'Total games.', 'Game totals and a tennis model.'),
    _group('tennis', 'GAME_HANDICAP', 'Player games handicap', 'match', 'unsupported', 'Game handicap.', 'Game totals and a tennis model.'),
    _group('tennis', 'TOTAL_SETS', 'Over/Under 2.5, 3.5, 4.5', 'match', 'unsupported', 'Total sets.', 'Set scores and a tennis model.'),
    _group('tennis', 'SET_HANDICAP', 'Player set handicap', 'match', 'unsupported', 'Set handicap.', 'Set scores and a tennis model.'),
    _group('tennis', 'FIRST_SET_TOTAL_GAMES', 'Over/Under 8.5, 9.5, 10.5, 12.5', 'set1', 'unsupported', 'First-set total games.', 'First-set games and a tennis model.'),
    _group('tennis', 'FIRST_SET_GAME_HANDICAP', 'First-set game handicap', 'set1', 'unsupported', 'First-set game handicap.', 'First-set games and a tennis model.'),
    _group('tennis', 'TIEBREAK_MATCH', 'Yes, No', 'match', 'unsupported', 'A tie-break occurs in the match.', 'Tie-break facts and a tennis model.'),
    _group('tennis', 'TIEBREAK_SET', 'Yes, No', 'set', 'unsupported', 'A tie-break occurs in a specified set.', 'Tie-break facts and a tennis model.'),
    _group('tennis', 'INDIVIDUAL_GAME_WINNER', 'Player 1, Player 2 for a specified game', 'game', 'unsupported', 'Winner of one game.', 'Game-level results and a tennis model.'),
    _group('ice_hockey', 'MATCH_WINNER_INCL_OT', 'Home, Away after overtime and shootout', 'incl_ot', 'unsupported', 'Winner including overtime and a shootout. This is not the 60-minute result.', 'A score split into regulation, overtime, and shootout.'),
    _group('ice_hockey', 'REGULATION_RESULT', 'Home, Draw, Away after 60 minutes', 'regulation', 'score', 'Regulation 1X2. The draw exists only in 60 minutes.', 'A stored 60-minute score.'),
    _group('ice_hockey', 'TOTAL_GOALS', 'Over/Under 3.5 through 7.5 and other lines', 'ft', 'score', 'Goals from the stored final score. A regulation total and an overtime total are different markets.', 'The score definition matching the price.'),
    _group('ice_hockey', 'TEAM_TOTAL_GOALS', 'Home/Away Over/Under 1.5, 2.5, 3.5', 'ft', 'score', 'One team’s goals from the stored final score.', 'The score definition matching the price.'),
    _group('ice_hockey', 'PUCK_LINE', 'Home/Away ±1.5, ±2.5 and other lines', 'ft', 'score', 'Puck line on the stored final-score margin. It is not the regulation 1X2.', 'Final score and the displayed line.'),
    _group('ice_hockey', 'PERIOD_1_WINNER', 'Home, Draw, Away', 'p1', 'data_pending', 'First-period winner.', 'A stored period score.'),
    _group('ice_hockey', 'PERIOD_2_WINNER', 'Home, Draw, Away', 'p2', 'data_pending', 'Second-period winner.', 'A stored period score.'),
    _group('ice_hockey', 'PERIOD_3_WINNER', 'Home, Draw, Away', 'p3', 'data_pending', 'Third-period winner.', 'A stored period score.'),
    _group('ice_hockey', 'PERIOD_1_TOTAL', 'Over/Under 0.5, 1.5, 2.5', 'p1', 'data_pending', 'First-period goals.', 'A stored period score.'),
    _group('ice_hockey', 'PERIOD_2_TOTAL', 'Over/Under 0.5, 1.5, 2.5', 'p2', 'data_pending', 'Second-period goals.', 'A stored period score.'),
    _group('ice_hockey', 'PERIOD_3_TOTAL', 'Over/Under 0.5, 1.5, 2.5', 'p3', 'data_pending', 'Third-period goals.', 'A stored period score.'),
    _group('ice_hockey', 'BOTH_TEAMS_TO_SCORE', 'Yes, No', 'ft', 'score', 'Both teams score in the stored final score.', 'Final score.'),
    _group('ice_hockey', 'CORRECT_SCORE', '1-0, 2-1, 3-2, 4-2 and other scores', 'ft', 'score', 'One cell of the goal distribution.', 'Final score and a correct-score price.'),
    _group('ice_hockey', 'FIRST_TEAM_TO_SCORE', 'Home, Away, No Goal', 'ft', 'data_pending', 'First team to score.', 'The order of goals.'),
    _group('ice_hockey', 'WINNING_MARGIN', 'Home/Away by 1, 2, or 3+', 'ft', 'score', 'Winning margin from the stored final score.', 'Final score.'),
)

ADDITIONAL_GROUPS = (
    _group('football', 'GOALSCORER', 'Player goalscorer', 'ft', 'data_pending', 'A named player scores.', 'Player goal records.'),
    _group('football', 'SHOTS_ON_TARGET', 'Shots on target', 'ft', 'data_pending', 'Shots on target.', 'Stored shot counts.'),
    _group('football', 'CARDS', 'Cards', 'ft', 'data_pending', 'Cards.', 'A stored card count.'),
    _group('football', 'BOOKING_POINTS', 'Booking points', 'ft', 'data_pending', 'Booking points.', 'Stored booking points.'),
    _group('football', 'FIRST_HALF_CORNERS', 'First-half corners', '1h', 'data_pending', 'First-half corners.', 'Stored first-half corners.'),
    _group('football', 'SECOND_HALF_GOALS', 'Second-half goals', '2h', 'data_pending', 'Second-half goals.', 'Stored second-half goals.'),
    _group('football', 'WIN_EITHER_HALF', 'Win either half', 'htft', 'data_pending', 'A team wins at least one half.', 'Stored half-time and full-time scores.'),
    _group('football', 'WIN_BOTH_HALVES', 'Win both halves', 'htft', 'data_pending', 'A team wins both halves.', 'Stored half-time and full-time scores.'),
    _group('football', 'CLEAN_SHEET', 'Home or away clean sheet', 'ft', 'score', 'The opponent scores zero.', 'Final score.'),
    _group('football', 'WINNING_MARGIN', 'Home/Away by 1, 2, or 3+', 'ft', 'score', 'Full-time winning margin.', 'Final score.'),
    _group('football', 'NEXT_GOAL', 'Live next goal', 'live', 'data_pending', 'The next goal.', 'A live goal sequence.'),
    _group('basketball', 'RACE_TO_POINTS', 'Race to 10 or 20', 'ft', 'data_pending', 'First team to a point milestone.', 'A stored scoring sequence.'),
    _group('basketball', 'HIGHEST_SCORING_QUARTER', 'Highest scoring quarter', 'quarter', 'data_pending', 'Quarter with the most points.', 'Stored quarter scores.'),
    _group('basketball', 'ODD_EVEN_POINTS', 'Odd or even total', 'ft', 'data_pending', 'Odd or even final points. The point model does not yet price parity.', 'A parity model and the final score.'),
    _group('basketball', 'PLAYER_POINTS', 'Player points', 'ft', 'data_pending', 'A player’s points.', 'Player box scores.'),
    _group('basketball', 'PLAYER_REBOUNDS', 'Rebounds', 'ft', 'data_pending', 'A player’s rebounds.', 'Player box scores.'),
    _group('basketball', 'PLAYER_ASSISTS', 'Assists', 'ft', 'data_pending', 'A player’s assists.', 'Player box scores.'),
    _group('basketball', 'PLAYER_THREES', 'Three-pointers', 'ft', 'data_pending', 'A player’s three-pointers.', 'Player box scores.'),
    _group('basketball', 'DOUBLE_DOUBLE', 'Double-double', 'ft', 'data_pending', 'A player records a double-double.', 'Player box scores.'),
    _group('basketball', 'TRIPLE_DOUBLE', 'Triple-double', 'ft', 'data_pending', 'A player records a triple-double.', 'Player box scores.'),
    _group('basketball', 'PLAYER_PRA', 'Points, rebounds and assists', 'ft', 'data_pending', 'A player’s points plus rebounds plus assists.', 'Player box scores.'),
    _group('tennis', 'TOTAL_ACES', 'Total aces', 'match', 'unsupported', 'Aces in the match.', 'Serve statistics and a tennis model.'),
    _group('tennis', 'PLAYER_ACES', 'Player aces', 'match', 'unsupported', 'One player’s aces.', 'Serve statistics and a tennis model.'),
    _group('tennis', 'DOUBLE_FAULTS', 'Double faults', 'match', 'unsupported', 'Double faults.', 'Serve statistics and a tennis model.'),
    _group('tennis', 'BREAKS_OF_SERVE', 'Breaks of serve', 'match', 'unsupported', 'Breaks of serve.', 'Service breaks and a tennis model.'),
    _group('tennis', 'FIRST_BREAK', 'First player to break', 'match', 'unsupported', 'First player to break serve.', 'Service breaks and a tennis model.'),
    _group('tennis', 'SET_CORRECT_SCORE', 'Set correct score', 'set', 'unsupported', 'Correct game score of a set.', 'Game scores and a tennis model.'),
    _group('tennis', 'NEXT_GAME_WINNER', 'Next game winner', 'live', 'unsupported', 'Winner of the next game.', 'Live game state and a tennis model.'),
    _group('tennis', 'POINT_WINNER', 'Live point winner', 'live', 'unsupported', 'Winner of the next point.', 'Live point state and a tennis model.'),
    _group('ice_hockey', 'LAST_TEAM_TO_SCORE', 'Last team to score', 'ft', 'data_pending', 'Last team to score.', 'The order of goals.'),
    _group('ice_hockey', 'ODD_EVEN_GOALS', 'Odd, Even', 'ft', 'score', 'Odd or even goals in the stored final score.', 'Final score.'),
    _group('ice_hockey', 'SHOTS_ON_GOAL', 'Total shots on goal', 'ft', 'data_pending', 'Shots on goal.', 'Stored shot counts.'),
    _group('ice_hockey', 'PLAYER_SHOTS', 'Player shots', 'ft', 'data_pending', 'A player’s shots.', 'Player shot records.'),
    _group('ice_hockey', 'ANYTIME_GOALSCORER', 'Anytime goalscorer', 'ft', 'data_pending', 'A named player scores.', 'Player goal records.'),
    _group('ice_hockey', 'PLAYER_ASSISTS', 'Player assists', 'ft', 'data_pending', 'A player’s assists.', 'Player assist records.'),
    _group('ice_hockey', 'POWER_PLAY_GOALS', 'Power-play goals', 'ft', 'data_pending', 'Power-play goals.', 'Stored special-team goals.'),
    _group('ice_hockey', 'NEXT_GOAL', 'Live next goal', 'live', 'data_pending', 'The next goal.', 'A live goal sequence.'),
)

COMPETITIONS = (
    {'sport': 'football', 'key': 'england-premier-league', 'name': 'Premier League', 'region': 'England', 'aliases': ('premier league', 'premierleague')},
    {'sport': 'football', 'key': 'spain-la-liga', 'name': 'La Liga', 'region': 'Spain', 'aliases': ('la liga', 'laliga', 'primera division', 'primera división')},
    {'sport': 'football', 'key': 'italy-serie-a', 'name': 'Serie A', 'region': 'Italy', 'aliases': ('serie a',)},
    {'sport': 'football', 'key': 'germany-bundesliga', 'name': 'Bundesliga', 'region': 'Germany', 'aliases': ('bundesliga', 'fußball-bundesliga', 'fussball-bundesliga')},
    {'sport': 'football', 'key': 'france-ligue-1', 'name': 'Ligue 1', 'region': 'France', 'aliases': ('ligue 1', 'ligue1')},
    {'sport': 'football', 'key': 'netherlands-eredivisie', 'name': 'Eredivisie', 'region': 'Netherlands', 'aliases': ('eredivisie',)},
    {'sport': 'football', 'key': 'portugal-primeira-liga', 'name': 'Primeira Liga', 'region': 'Portugal', 'aliases': ('primeira liga', 'liga portugal')},
    {'sport': 'football', 'key': 'england-championship', 'name': 'EFL Championship', 'region': 'England', 'aliases': ('efl championship', 'english championship', 'england championship')},
    {'sport': 'football', 'key': 'uefa-champions-league', 'name': 'UEFA Champions League', 'region': 'Europe', 'aliases': ('champions league', 'uefa champions')},
    {'sport': 'football', 'key': 'uefa-europa-league', 'name': 'UEFA Europa League', 'region': 'Europe', 'aliases': ('europa league', 'uefa europa')},
    {'sport': 'basketball', 'key': 'usa-nba', 'name': 'NBA', 'region': 'USA', 'aliases': ('nba',)},
    {'sport': 'basketball', 'key': 'usa-wnba', 'name': 'WNBA', 'region': 'USA', 'aliases': ('wnba',)},
    {'sport': 'basketball', 'key': 'europe-euroleague', 'name': 'EuroLeague', 'region': 'Europe', 'aliases': ('euroleague', 'euro league')},
    {'sport': 'basketball', 'key': 'europe-eurocup', 'name': 'EuroCup', 'region': 'Europe', 'aliases': ('eurocup', 'euro cup')},
    {'sport': 'basketball', 'key': 'usa-ncaa-men', 'name': "NCAA Men's Basketball", 'region': 'USA', 'aliases': ('ncaa',)},
    {'sport': 'tennis', 'key': 'atp-tour', 'name': 'ATP Tour', 'region': "Men's professional", 'aliases': ('atp',)},
    {'sport': 'tennis', 'key': 'wta-tour', 'name': 'WTA Tour', 'region': "Women's professional", 'aliases': ('wta',)},
    {'sport': 'tennis', 'key': 'grand-slams', 'name': 'Grand Slams', 'region': 'Grand Slams', 'aliases': ('australian open', 'roland garros', 'roland-garros', 'wimbledon', 'us open')},
    {'sport': 'tennis', 'key': 'atp-challenger', 'name': 'ATP Challenger Tour', 'region': "Men's development", 'aliases': ('challenger',)},
    {'sport': 'ice_hockey', 'key': 'nhl', 'name': 'NHL', 'region': 'USA / Canada', 'aliases': ('nhl',)},
    {'sport': 'ice_hockey', 'key': 'ahl', 'name': 'AHL', 'region': 'USA / Canada', 'aliases': ('ahl',)},
    {'sport': 'ice_hockey', 'key': 'shl', 'name': 'SHL', 'region': 'Sweden', 'aliases': ('shl',)},
    {'sport': 'ice_hockey', 'key': 'liiga', 'name': 'Liiga', 'region': 'Finland', 'aliases': ('liiga',)},
)


def catalogue_summary():
    sports = sorted({item['sport'] for item in MARKET_GROUPS})
    return {
        'sports': len(sports),
        'competitions': len(COMPETITIONS),
        'market_groups': len(MARKET_GROUPS),
        'additional_groups': len(ADDITIONAL_GROUPS),
        'by_sport': {
            sport: {
                'competitions': sum(1 for item in COMPETITIONS if item['sport'] == sport),
                'markets': sum(1 for item in MARKET_GROUPS if item['sport'] == sport),
            }
            for sport in sports
        },
    }


def match_competition(sport, name):
    text = ' '.join(str(name or '').casefold().replace('’', "'").split())
    found = None
    best = 0
    for item in COMPETITIONS:
        if item['sport'] != sport:
            continue
        for alias in item['aliases']:
            if len(alias) <= best:
                continue
            if re.search(rf'(^|[^a-z0-9]){re.escape(alias)}([^a-z0-9]|$)', text):
                found = item['key']
                best = len(alias)
    return found


def catalogue_rows(existing):
    """Rows for groups that the model refresh has not already registered."""
    rows = []
    for item in (*MARKET_GROUPS, *ADDITIONAL_GROUPS):
        if (item['sport'], item['key']) in existing:
            continue
        status = {'score': 'research', 'data_pending': 'data_pending', 'unsupported': 'unsupported'}[item['kind']]
        rows.append({
            'sport': item['sport'],
            'market_key': item['key'],
            'selection': item['selection'],
            'period': item['period'],
            'line_value': None,
            'settlement_rule': item['definition'],
            'data_requirements': item['requirement'],
            'model_source': 'catalogue' if item['kind'] != 'unsupported' else 'none',
            'model_version': None,
            'status': status,
            'evidence': {
                'catalogue': True,
                'kind': item['kind'],
                'dependency': 'same_match',
                'probability': None,
                'activated': False,
            },
        })
    return rows


def ensure_starting_competitions(connection, now=None):
    """Register the 23 starting competitions. No fixture is created."""
    current = now or datetime.now(timezone.utc)
    saved = 0
    for item in COMPETITIONS:
        existing = connection.execute(
            """SELECT id FROM sports_ai_leagues
               WHERE sport_code=? AND canonical_key=? AND season='starting'""",
            (item['sport'], item['key']),
        ).fetchone()
        if existing:
            continue
        sport = connection.execute(
            'SELECT code FROM sports_ai_sports WHERE code=?', (item['sport'],),
        ).fetchone()
        if not sport:
            continue
        connection.execute(
            """INSERT INTO sports_ai_leagues
               (id,sport_code,country_code,name,division,season,canonical_key,league_kind,
                status,freshness,fixture_count,result_count,source_count,created_at,updated_at)
               VALUES(?,?,?,?,?,'starting',?,'competition','discovered','catalogue',0,0,0,?,?)""",
            (
                str(uuid.uuid4()), item['sport'], None, item['name'], item['region'], item['key'],
                current.isoformat(), current.isoformat(),
            ),
        )
        saved += 1
    matched = set()
    for row in connection.execute('SELECT sport, competition FROM sports_ai_fixtures'):
        key = match_competition(row['sport'], row['competition'])
        if key:
            matched.add((row['sport'], key))
    return {
        'registered': len(COMPETITIONS),
        'added': saved,
        'matched_groups': len(matched),
        'fixtures_invented': 0,
        'places_wager': False,
    }


REFERENCE_PATH = Path(__file__).resolve().parent / 'data' / 'reference_catalogue.json'


def load_reference_catalogue():
    """The workbook list. Every row is a discovery candidate, not a verified live league."""
    return json.loads(REFERENCE_PATH.read_text(encoding='utf-8'))


def _reference_key(country, name):
    from catalog_engine import normalize_name
    return normalize_name(f'{country} {name}')


def _reference_market_key(family):
    key = re.sub(r'[^A-Za-z0-9]+', '_', str(family or '')).strip('_').upper()
    return key[:80]


def reference_market_rows(existing):
    """Register each workbook market family. An existing settlement row is left as it is."""
    rows = []
    for item in load_reference_catalogue()['markets']:
        key = _reference_market_key(item['family'])
        if not key or (item['sport'], key) in existing:
            continue
        rows.append({
            'sport': item['sport'],
            'market_key': key,
            'selection': item['family'],
            'period': 'reference',
            'line_value': None,
            'settlement_rule': 'Listed in the reference catalogue. Not verified live. No probability is produced from this row.',
            'data_requirements': (item.get('readiness') or 'A sport-specific model and a verified price.')[:500],
            'model_source': 'catalogue',
            'model_version': None,
            'status': 'data_pending',
            'evidence': {
                'catalogue': True,
                'verified_live': False,
                'probability': None,
                'activated': False,
                'mode': item.get('mode') or '',
            },
        })
    return rows


def ensure_reference_catalogue(connection, now=None):
    """Register every workbook sport, league and division. No fixture is created."""
    current = now or datetime.now(timezone.utc)
    observed = current.isoformat()
    payload = load_reference_catalogue()
    for sport in payload['sports']:
        connection.execute(
            """INSERT OR IGNORE INTO sports_ai_sports
               (code,name,prediction_status,acquisition_status,notes)
               VALUES(?,?,'not_modelled','source_catalog_only',?)""",
            (
                sport['code'], sport['name'],
                'Listed in the reference catalogue. Not verified live. No probability is produced until this sport has its own passing model.',
            ),
        )
    added = 0
    for item in payload['competitions']:
        key = _reference_key(item['country'], item['name'])
        label = f"{item['country']} — {item['name']}" if item['country'] else item['name']
        existing = connection.execute(
            """SELECT id FROM sports_ai_leagues
               WHERE sport_code=? AND canonical_key=? AND season='catalogue'""",
            (item['sport'], key),
        ).fetchone()
        if existing:
            connection.execute(
                """UPDATE sports_ai_leagues
                   SET name=?, division=?, updated_at=?
                   WHERE id=?""",
                (label[:200], item['division'][:120], observed, existing['id']),
            )
            continue
        connection.execute(
            """INSERT INTO sports_ai_leagues
               (id,sport_code,country_code,name,division,season,canonical_key,league_kind,
                status,freshness,fixture_count,result_count,source_count,created_at,updated_at)
               VALUES(?,?,NULL,?,?,'catalogue',?,'competition','discovered','catalogue',0,0,0,?,?)""",
            (str(uuid.uuid4()), item['sport'], label[:200], item['division'][:120], key, observed, observed),
        )
        added += 1
    football = sum(1 for item in payload['competitions'] if item['sport'] == 'football')
    return {
        'competitions': len(payload['competitions']),
        'football': football,
        'sports': len(payload['sports']),
        'markets': len(payload['markets']),
        'added': added,
        'fixtures_invented': 0,
        'places_wager': False,
        'verified_live': False,
    }
