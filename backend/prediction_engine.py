"""Source-backed, cutoff-safe football and basketball modeling."""

import hashlib
import json
import math
import re
import uuid
from datetime import datetime, timedelta, timezone

from prediction_math import (
    apply_isotonic,
    apply_sigmoid,
    fit_gradient_boosting,
    fit_isotonic,
    fit_ridge,
    fit_sigmoid,
    monte_carlo_settlement,
    predict_gradient_boosting,
    predict_ridge,
    simulate_scores,
)

def date_only_sql(alias='f'):
    """A midnight timestamp is a match day, not a 01:00 Nigeria kickoff."""
    precision = f"""IFNULL((
        SELECT m.time_precision FROM sports_ai_fixture_source_map m
        WHERE m.fixture_id={alias}.id ORDER BY m.observed_at DESC LIMIT 1
    ), 'instant')"""
    return f"(({precision})='date' OR {alias}.starts_at LIKE '%T00:00:00%')"


def upcoming_fixture_sql(alias='f'):
    """Scheduled fixtures still ahead. A date-only row stays eligible until the end of that day in Lagos."""
    dated = date_only_sql(alias)
    today = "date(datetime('now','+1 hour'))"
    return (
        f"{alias}.status='scheduled' AND ("
        f"({dated} AND date({alias}.starts_at)>={today})"
        f" OR (NOT ({dated}) AND {alias}.starts_at>datetime('now'))"
        f")"
    )


def upcoming_order_sql(alias='f'):
    today = "date(datetime('now','+1 hour'))"
    return (
        f"CASE WHEN date({alias}.starts_at)={today} THEN 0 ELSE 1 END, "
        f"CASE WHEN {date_only_sql(alias)} THEN 1 ELSE 0 END, "
        f"{alias}.starts_at"
    )


_SEASON_TEXT = re.compile(r'\b20\d{2}(?:\s*/\s*\d{2,4})?\b')
_LEAGUE_RULES = (
    (('2 fussball bundesliga', '2 bundesliga', 'bundesliga 2'), 'bundesliga-2'),
    (('1 fussball bundesliga', '1 bundesliga', 'deutsche bundesliga', 'germany bundesliga', 'bundesliga'), 'bundesliga-1'),
    (('efl cup', 'league cup', 'carabao'), 'efl-cup'),
    (('fa cup',), 'fa-cup'),
    (('championship',), 'championship'),
    (('premier league',), 'premier-league'),
    (('serie a',), 'serie-a'),
    (('primera', 'la liga', 'laliga'), 'la-liga'),
    (('ligue 1',), 'ligue-1'),
    (('primeira', 'liga portugal'), 'primeira'),
    (('eredivisie',), 'eredivisie'),
    (('copa del rey',), 'copa-del-rey'),
    (('champions league',), 'ucl'),
    (('europa league',), 'uel'),
    (('conference league',), 'uecl'),
    (('dfb pokal',), 'dfb-pokal'),
    (('nations league',), 'nations'),
    (('eliteserien',), 'eliteserien'),
    (('nba',), 'nba'),
)


def league_key(competition):
    """Identity of a league across season labels. Cups stay separate from league seasons."""
    text = str(competition or '').casefold().replace('ß', 'ss').replace('ü', 'u')
    text = _SEASON_TEXT.sub(' ', text)
    text = re.sub(r'[^a-z0-9]+', ' ', text).strip()
    for needles, key in _LEAGUE_RULES:
        if any(needle in text for needle in needles):
            return key
    return text


_CLUB_PREFIXES = {
    'fc', 'sc', 'sv', 'ac', 'cf', 'afc', 'cfc', 'vfb', 'vfl', 'tsg', 'bsc', 'fk', 'sk',
    'fsv', 'osc', 'rc', 'as', 'bc', 'ssc', 'ud', 'cd', 'ca', 'rb', 'tsv', 'rcd',
}
_TOKEN_FOLDS = {
    'munchen': 'munich', 'monchengladbach': 'gladbach', 'mgladbach': 'gladbach',
    'utd': 'united', 'nurnberg': 'nuremberg',
}


_CHAR_FOLDS = (
    ('ß', 'ss'), ('ü', 'u'), ('ö', 'o'), ('ä', 'a'), ('ø', 'o'), ('å', 'a'),
    ('æ', 'ae'), ('œ', 'oe'), ('ç', 'c'), ('ş', 's'), ('ğ', 'g'), ('ı', 'i'),
    ('ł', 'l'), ('đ', 'd'), ('š', 's'), ('ś', 's'), ('ž', 'z'), ('ź', 'z'),
    ('ż', 'z'), ('č', 'c'), ('ć', 'c'), ('ř', 'r'), ('ń', 'n'), ('ň', 'n'),
    ('ě', 'e'), ('ý', 'y'), ('é', 'e'), ('á', 'a'), ('í', 'i'), ('ó', 'o'),
    ('ú', 'u'), ('ñ', 'n'), ('ő', 'o'), ('ű', 'u'),
)


def _team_tokens(name):
    text = str(name or '').casefold()
    for source, target in _CHAR_FOLDS:
        text = text.replace(source, target)
    text = text.replace('´', ' ').replace("'", ' ')
    text = re.sub(r'[^a-z0-9]+', ' ', text)
    tokens = []
    for token in text.split():
        token = _TOKEN_FOLDS.get(token, token).replace('oe', 'o')
        if token in _CLUB_PREFIXES or token.isdigit() or len(token) < 3:
            continue
        tokens.append('turkey' if token == 'turkiye' else token)
    if tokens == ['psg']:
        return ['paris', 'saint', 'germain']
    if tokens == ['czechia']:
        return ['czech', 'republic']
    return tokens


def _token_fits(left, right):
    return left == right or right.startswith(left) or left.startswith(right)


def _teams_align(left, right):
    first, second = _team_tokens(left), _team_tokens(right)
    if not first or not second:
        return False
    if first == second:
        return True
    short, long = (first, second) if len(first) <= len(second) else (second, first)
    return all(any(_token_fits(token, other) for other in long) for token in short)


def _aligned_team(team, history):
    """Use the one historical club spelling when the short name is unambiguous."""
    if sum(1 for row in history if row['home'] == team or row['away'] == team) >= MIN_TEAM_GAMES:
        return team
    candidates = sorted({row['home'] for row in history} | {row['away'] for row in history})
    matches = [name for name in candidates if name != team and _teams_align(team, name)]
    if len(matches) == 1:
        return matches[0]
    return team


def competitions_for_league(connection, sport, competition):
    key = league_key(competition)
    names = [
        row['competition'] for row in connection.execute(
            'SELECT DISTINCT competition FROM sports_ai_fixtures WHERE sport=?', (sport,),
        ) if league_key(row['competition']) == key
    ]
    return names or [competition]


DOMESTIC_KEYS = frozenset({
    'bundesliga-1', 'bundesliga-2', 'championship', 'premier-league', 'serie-a',
    'la-liga', 'ligue-1', 'primeira', 'eredivisie', 'eliteserien',
})


def _final_rows(connection, sport, cutoff, keys):
    """Completed matches before cutoff whose competition maps to one of keys."""
    kept = []
    for raw in connection.execute(
        """SELECT f.id,f.competition,f.home,f.away,f.starts_at,f.statistics_json,f.observed_at,f.source,
                  r.home_score,r.away_score,r.settled_at,r.source AS result_source
           FROM sports_ai_fixtures f
           JOIN sports_ai_results r ON r.fixture_id=f.id
           WHERE f.sport=? AND r.status='final'""",
        (sport,),
    ):
        row = dict(raw)
        if league_key(row['competition']) not in keys:
            continue
        try:
            row['_kickoff'] = parse_time(row['starts_at'])
            settled = parse_time(row['settled_at'])
        except (TypeError, ValueError):
            continue
        if row['_kickoff'] < cutoff and settled < cutoff:
            row['_statistics'] = {}
            row['_metrics'] = {}
            kept.append(row)
    return kept


def _club_split(team, rows):
    """Games for the one historical club that matches team, in that club's main league."""
    aligned = _aligned_team(team, rows)
    games = []
    for row in rows:
        if row['home'] == aligned:
            games.append({
                **row, 'venue': 'home', 'for': float(row['home_score']), 'against': float(row['away_score']),
            })
        elif row['away'] == aligned:
            games.append({
                **row, 'venue': 'away', 'for': float(row['away_score']), 'against': float(row['home_score']),
            })
    if not games:
        return aligned, [], []
    counts = {}
    for game in games:
        key = league_key(game['competition'])
        counts[key] = counts.get(key, 0) + 1
    main_key = max(counts, key=counts.get)
    chosen = [game for game in games if league_key(game['competition']) == main_key]
    baseline = [row for row in rows if league_key(row['competition']) == main_key]
    return aligned, chosen, baseline


def domestic_team_rows(connection, sport, team, cutoff):
    """Stored league matches for a club when its current competition sample is short."""
    rows = _final_rows(connection, sport, cutoff, DOMESTIC_KEYS)
    aligned, games, _baseline = _club_split(team, rows)
    copied = []
    for game in games:
        item = dict(game)
        if aligned != team:
            if item['home'] == aligned:
                item['home'] = team
            elif item['away'] == aligned:
                item['away'] = team
        copied.append(item)
    return copied


MODEL_NAMES = {'football': 'sports-ai-football-ensemble', 'basketball': 'sports-ai-basketball-ensemble'}
VERSION = '2'
MIN_TEAM_GAMES = 3
MIN_VALIDATION_FIXTURES = 30
MAX_CONCURRENT_JOBS = 1
GOAL_FLOOR, GOAL_CEILING = 0.05, 6.5
POINT_FLOOR, POINT_CEILING = 60.0, 170.0
FOOTBALL_RIDGE_COLUMNS = (
    'intercept', 'expected_home_goals', 'expected_away_goals', 'home_rest_days',
    'away_rest_days', 'home_games_7d', 'away_games_7d', 'home_form', 'away_form',
)
BASKETBALL_RIDGE_COLUMNS = (
    'intercept', 'expected_home_points', 'expected_away_points', 'home_rest_days',
    'away_rest_days', 'home_games_3d', 'away_games_3d', 'home_form', 'away_form',
)
FOOTBALL_XG_KEYS = {'home_xg', 'away_xg', 'xg_home', 'xg_away'}
BASKETBALL_STAT_KEYS = {
    'possessions', 'pace', 'home_possessions', 'away_possessions', 'home_h1', 'away_h1',
    'home_first_half', 'away_first_half', 'injuries_out_home', 'injuries_out_away',
    'lineup_known_home', 'lineup_known_away',
}
FOOTBALL_MARKETS = (
    'HOME', 'DRAW', 'AWAY', 'DOUBLE_CHANCE_1X', 'DOUBLE_CHANCE_12',
    'DOUBLE_CHANCE_X2', 'DNB_HOME', 'DNB_AWAY', 'BTTS_YES', 'BTTS_NO',
)
FOOTBALL_MARKETS += tuple(
    f'{side}_{line}' for line in ('0.5', '1.5', '2.5', '3.5', '4.5')
    for side in ('OVER', 'UNDER')
)
FOOTBALL_MARKETS += tuple(
    f'{team}_{side}_{line}' for team in ('HOME', 'AWAY')
    for side in ('OVER', 'UNDER') for line in ('0.5', '1.5', '2.5')
)


def parse_time(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value
    if result.tzinfo is None:
        raise ValueError('Timestamps must be timezone-aware')
    return result.astimezone(timezone.utc)


def _half_life_days(connection):
    row = connection.execute("SELECT value_json FROM sports_ai_settings WHERE key='engine'").fetchone()
    if not row:
        return 180.0
    try:
        value = float(json.loads(row['value_json']).get('form_half_life_days', 180))
    except (TypeError, ValueError, json.JSONDecodeError):
        return 180.0
    return value if math.isfinite(value) and value > 0 else 180.0


def _weighted_average(records, field, cutoff, half_life, default=None):
    values = [
        (float(row[field]), math.exp(-max(0, (cutoff - row['_kickoff']).total_seconds() / 86400) / half_life))
        for row in records if row.get(field) is not None
    ]
    if not values:
        return default
    total = sum(weight for _, weight in values)
    return sum(value * weight for value, weight in values) / total


def _sample_sd(values):
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))


def _json_object(value):
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _metric_map(connection, fixture_ids, cutoff):
    metrics = {fixture_id: {} for fixture_id in fixture_ids}
    if not fixture_ids:
        return metrics
    for start in range(0, len(fixture_ids), 200):
        chunk = fixture_ids[start:start + 200]
        placeholders = ','.join('?' for _ in chunk)
        rows = connection.execute(
            f"""SELECT fixture_id,metric,value_json,observed_at
                FROM sports_ai_match_statistics
                WHERE fixture_id IN ({placeholders})""",
            chunk,
        ).fetchall()
        for row in rows:
            try:
                observed = parse_time(row['observed_at'])
            except (TypeError, ValueError):
                continue
            if observed >= cutoff:
                continue
            payload = _json_object(row['value_json'])
            number = _finite(payload.get('value', payload.get(row['metric'])))
            if number is None and len(payload) == 1:
                number = _finite(next(iter(payload.values())))
            if number is None:
                number = _finite(row['value_json'])
            if number is not None:
                metrics[row['fixture_id']][row['metric']] = number
    return metrics


def _stat_number(statistics, metrics, *keys):
    for key in keys:
        if key in metrics and metrics[key] is not None:
            return metrics[key]
        if key in statistics:
            return _finite(statistics[key])
    return None


def build_features(connection, fixture):
    """Historical rows with kickoff and settlement strictly before the fixture cutoff."""
    cutoff = parse_time(fixture['starts_at'])
    half_life = _half_life_days(connection)
    league_names = competitions_for_league(connection, fixture['sport'], fixture['competition'])
    league_marks = ','.join('?' * len(league_names))
    rows = connection.execute(
        f"""SELECT f.id,f.competition,f.home,f.away,f.starts_at,f.statistics_json,f.observed_at,
                  r.home_score,r.away_score,r.source,r.settled_at
           FROM sports_ai_fixtures f
           JOIN sports_ai_results r ON r.fixture_id=f.id
           WHERE f.sport=? AND f.competition IN ({league_marks}) AND r.status='final'
           ORDER BY f.starts_at""",
        (fixture['sport'], *league_names),
    ).fetchall()
    history = []
    for raw in rows:
        row = dict(raw)
        try:
            row['_kickoff'] = parse_time(row['starts_at'])
            settled = parse_time(row['settled_at'])
        except (TypeError, ValueError):
            continue
        if row['_kickoff'] < cutoff and settled < cutoff:
            try:
                stats_observed = parse_time(row['observed_at']) if row.get('observed_at') else None
            except (TypeError, ValueError):
                stats_observed = None
            row['_statistics'] = _json_object(row.get('statistics_json')) if stats_observed and stats_observed < cutoff else {}
            history.append(row)
    metric_rows = _metric_map(connection, [row['id'] for row in history], cutoff)
    for row in history:
        row['_metrics'] = metric_rows.get(row['id'], {})

    def team_games(team):
        games = []
        for row in history:
            if row['home'] == team:
                games.append({
                    **row, 'venue': 'home', 'for': float(row['home_score']),
                    'against': float(row['away_score']),
                })
            elif row['away'] == team:
                games.append({
                    **row, 'venue': 'away', 'for': float(row['away_score']),
                    'against': float(row['home_score']),
                })
        return games

    home_name = _aligned_team(fixture['home'], history)
    away_name = _aligned_team(fixture['away'], history)
    home_all, away_all = team_games(home_name), team_games(away_name)
    home_games = [row for row in home_all if row['venue'] == 'home']
    away_games = [row for row in away_all if row['venue'] == 'away']
    if fixture['sport'] == 'football':
        notes = []
        domestic = None

        def side_rates(team, games, all_games, venue):
            if len(games) >= MIN_TEAM_GAMES:
                return all_games, games, history
            nonlocal domestic
            if domestic is None:
                domestic = _final_rows(connection, fixture['sport'], cutoff, DOMESTIC_KEYS)
            _aligned, club_games, baseline = _club_split(team, domestic)
            venue_games = [row for row in club_games if row['venue'] == venue]
            if len(venue_games) < MIN_TEAM_GAMES:
                return None, None, None
            notes.append(
                f"{team} uses {len(venue_games)} stored league {venue} matches because this competition has {len(games)}"
            )
            return club_games, venue_games, baseline

        home_all, home_games, home_baseline = side_rates(fixture['home'], home_games, home_all, 'home')
        away_all, away_games, away_baseline = side_rates(fixture['away'], away_games, away_all, 'away')
        if home_games is None or away_games is None:
            return None

        def averages(rows):
            return (
                _weighted_average(
                    [{'home_score': row['home_score'], '_kickoff': row['_kickoff']} for row in rows],
                    'home_score', cutoff, half_life,
                ),
                _weighted_average(
                    [{'away_score': row['away_score'], '_kickoff': row['_kickoff']} for row in rows],
                    'away_score', cutoff, half_life,
                ),
            )

        home_league_home, home_league_away = averages(home_baseline)
        away_league_home, away_league_away = averages(away_baseline)
        rates = (home_league_home, home_league_away, away_league_home, away_league_away)
        if any(value is None or value <= 0 for value in rates):
            return None
        competition_home, competition_away = averages(history) if len(history) >= 30 else (None, None)
        league_home = competition_home or home_league_home
        league_away = competition_away or away_league_away
        home_attack = _weighted_average(home_games, 'for', cutoff, half_life) / home_league_home
        home_defence = _weighted_average(home_games, 'against', cutoff, half_life) / home_league_away
        away_attack = _weighted_average(away_games, 'for', cutoff, half_life) / away_league_away
        away_defence = _weighted_average(away_games, 'against', cutoff, half_life) / away_league_home
        features = {
            'home_attack': home_attack, 'home_defence': home_defence,
            'away_attack': away_attack, 'away_defence': away_defence,
            'league_home_goals': league_home, 'league_away_goals': league_away,
            'expected_home_goals': league_home * home_attack * away_defence,
            'expected_away_goals': league_away * away_attack * home_defence,
            'history_basis': (
                'Rates use completed matches in this competition.'
                if not notes else 'Rates use stored league matches where this competition is short. ' + '; '.join(notes) + '.'
            ),
        }
    elif fixture['sport'] == 'basketball':
        if len(home_all) < MIN_TEAM_GAMES or len(away_all) < MIN_TEAM_GAMES:
            return None
        league_home = _weighted_average(
            [{'home_score': row['home_score'], '_kickoff': row['_kickoff']} for row in history],
            'home_score', cutoff, half_life,
        )
        league_away = _weighted_average(
            [{'away_score': row['away_score'], '_kickoff': row['_kickoff']} for row in history],
            'away_score', cutoff, half_life,
        )
        if not league_home or not league_away:
            return None
        home_for = _weighted_average(home_games or home_all, 'for', cutoff, half_life)
        home_against = _weighted_average(home_games or home_all, 'against', cutoff, half_life)
        away_for = _weighted_average(away_games or away_all, 'for', cutoff, half_life)
        away_against = _weighted_average(away_games or away_all, 'against', cutoff, half_life)
        league_sd = _sample_sd(
            [float(row['home_score']) for row in history] + [float(row['away_score']) for row in history],
        )

        def shrink_sd(sample_sd, sample_size):
            observed = sample_sd or 0.0
            if sample_size >= 20 and observed:
                return observed
            if not league_sd:
                return observed or None
            prior = 8
            return math.sqrt((sample_size * observed ** 2 + prior * league_sd ** 2) / (sample_size + prior))

        features = {
            'home_points_for': home_for, 'home_points_against': home_against,
            'away_points_for': away_for, 'away_points_against': away_against,
            'home_margin': home_for - home_against,
            'away_margin': away_for - away_against,
            'expected_home_points': home_for * away_against / league_away,
            'expected_away_points': away_for * home_against / league_home,
            'home_score_sd': shrink_sd(_sample_sd([row['for'] for row in home_all]), len(home_all)),
            'away_score_sd': shrink_sd(_sample_sd([row['for'] for row in away_all]), len(away_all)),
        }
        if not features['home_score_sd'] or not features['away_score_sd']:
            return None
        features['total_sd'] = math.sqrt(features['home_score_sd'] ** 2 + features['away_score_sd'] ** 2)
    else:
        return None

    def rest(games):
        if not games:
            return None
        latest = max(row['_kickoff'] for row in games)
        return (cutoff - latest).total_seconds() / 86400

    def recent_count(games, days):
        return float(sum(1 for row in games if (cutoff - row['_kickoff']).total_seconds() <= days * 86400))

    def form_points(games):
        if not games:
            return None
        total_weight = 0.0
        total = 0.0
        for row in games:
            age = max(0, (cutoff - row['_kickoff']).total_seconds() / 86400)
            weight = math.exp(-age / half_life)
            if fixture['sport'] == 'football':
                value = 3.0 if row['for'] > row['against'] else 1.0 if row['for'] == row['against'] else 0.0
            else:
                value = 1.0 if row['for'] > row['against'] else 0.0
            total += value * weight
            total_weight += weight
        return total / total_weight if total_weight else None

    def optional_average(games, picker):
        values = []
        for row in games:
            value = picker(row)
            if value is not None:
                values.append((value, row))
        if len(values) < 3:
            return None
        return _weighted_average(
            [{'_kickoff': row['_kickoff'], 'value': value} for value, row in values],
            'value', cutoff, half_life,
        )

    h2h = [
        row for row in history
        if {row['home'], row['away']} == {fixture['home'], fixture['away']}
    ]
    h2h_home_wins = sum(
        1 for row in h2h
        if (row['home'] == fixture['home'] and row['home_score'] > row['away_score'])
        or (row['away'] == fixture['home'] and row['away_score'] > row['home_score'])
    )
    h2h_draws = sum(1 for row in h2h if row['home_score'] == row['away_score'])
    sport_rows = connection.execute(
        """SELECT f.starts_at,r.home_score,r.away_score,r.settled_at
           FROM sports_ai_fixtures f
           JOIN sports_ai_results r ON r.fixture_id=f.id
           WHERE f.sport=? AND r.status='final'""",
        (fixture['sport'],),
    ).fetchall()
    competition_totals, sport_totals = [], []
    for row in sport_rows:
        try:
            kickoff = parse_time(row['starts_at'])
            settled = parse_time(row['settled_at'])
        except (TypeError, ValueError):
            continue
        if kickoff < cutoff and settled < cutoff:
            sport_totals.append(float(row['home_score']) + float(row['away_score']))
    for row in history:
        competition_totals.append(float(row['home_score']) + float(row['away_score']))
    league_strength = None
    if competition_totals and sport_totals and sum(sport_totals) > 0:
        league_strength = (sum(competition_totals) / len(competition_totals)) / (sum(sport_totals) / len(sport_totals))

    features.update({
        'home_rest_days': rest(home_all), 'away_rest_days': rest(away_all),
        'home_games': len(home_all), 'away_games': len(away_all),
        'home_games_7d': recent_count(home_all, 7), 'away_games_7d': recent_count(away_all, 7),
        'home_games_3d': recent_count(home_all, 3), 'away_games_3d': recent_count(away_all, 3),
        'home_form': form_points(home_all), 'away_form': form_points(away_all),
        'h2h_games': len(h2h), 'h2h_home_wins': h2h_home_wins, 'h2h_draws': h2h_draws,
        'h2h_away_wins': len(h2h) - h2h_home_wins - h2h_draws,
        'h2h_avg_goals': (
            sum(row['home_score'] + row['away_score'] for row in h2h) / len(h2h)
        ) if h2h else None,
        'league_strength': league_strength,
        'form_half_life_days': half_life,
        'historical_games': len(history),
    })
    if fixture['sport'] == 'football':
        features['home_xg_for'] = optional_average(home_games, lambda row: _stat_number(
            row['_statistics'], row['_metrics'], 'home_xg', 'xg_home') if row['venue'] == 'home' else _stat_number(
            row['_statistics'], row['_metrics'], 'away_xg', 'xg_away'))
        features['away_xg_for'] = optional_average(away_games, lambda row: _stat_number(
            row['_statistics'], row['_metrics'], 'away_xg', 'xg_away') if row['venue'] == 'away' else _stat_number(
            row['_statistics'], row['_metrics'], 'home_xg', 'xg_home'))
    else:
        features['home_pace'] = optional_average(home_all, lambda row: _stat_number(
            row['_statistics'], row['_metrics'], 'pace', 'possessions', 'home_possessions'))
        features['away_pace'] = optional_average(away_all, lambda row: _stat_number(
            row['_statistics'], row['_metrics'], 'pace', 'possessions', 'away_possessions'))
        features['home_h1_for'] = optional_average(home_all, lambda row: _stat_number(
            row['_statistics'], row['_metrics'], 'home_h1' if row['venue'] == 'home' else 'away_h1',
            'home_first_half' if row['venue'] == 'home' else 'away_first_half'))
        features['away_h1_for'] = optional_average(away_all, lambda row: _stat_number(
            row['_statistics'], row['_metrics'], 'away_h1' if row['venue'] == 'away' else 'home_h1',
            'away_first_half' if row['venue'] == 'away' else 'home_first_half'))
        features['injuries_out_home'] = optional_average(home_all, lambda row: _stat_number(
            row['_statistics'], row['_metrics'], 'injuries_out_home' if row['venue'] == 'home' else 'injuries_out_away'))
        features['injuries_out_away'] = optional_average(away_all, lambda row: _stat_number(
            row['_statistics'], row['_metrics'], 'injuries_out_away' if row['venue'] == 'away' else 'injuries_out_home'))
        home_h1_against = optional_average(home_all, lambda row: _stat_number(
            row['_statistics'], row['_metrics'], 'away_h1' if row['venue'] == 'home' else 'home_h1',
            'away_first_half' if row['venue'] == 'home' else 'home_first_half'))
        away_h1_against = optional_average(away_all, lambda row: _stat_number(
            row['_statistics'], row['_metrics'], 'home_h1' if row['venue'] == 'away' else 'away_h1',
            'home_first_half' if row['venue'] == 'away' else 'away_first_half'))
        h1_totals = []
        for row in history:
            home_half = _stat_number(row['_statistics'], row['_metrics'], 'home_h1', 'home_first_half')
            away_half = _stat_number(row['_statistics'], row['_metrics'], 'away_h1', 'away_first_half')
            if home_half is not None and away_half is not None:
                h1_totals.append(home_half + away_half)
        if features['home_h1_for'] is not None and features['away_h1_for'] is not None and home_h1_against is not None and away_h1_against is not None:
            features['expected_home_h1'] = (features['home_h1_for'] + away_h1_against) / 2
            features['expected_away_h1'] = (features['away_h1_for'] + home_h1_against) / 2
            features['h1_total_sd'] = _sample_sd(h1_totals)
        else:
            features['expected_home_h1'] = None
            features['expected_away_h1'] = None
            features['h1_total_sd'] = None
        if features['home_pace'] and features['away_pace'] and features['home_points_for'] and features['away_points_for']:
            pace = (features['home_pace'] + features['away_pace']) / 2
            features['expected_pace'] = pace
        else:
            features['expected_pace'] = None
    missing = sorted(key for key, value in features.items() if value is None)
    present = len(features) - len(missing)
    fingerprint_source = json.dumps({
        'ids': [row['id'] for row in history],
        'scores': [[row['home_score'], row['away_score'], row['settled_at']] for row in history],
        'half_life': half_life,
        'metrics': {row['id']: row['_metrics'] for row in history},
    }, sort_keys=True, default=str)
    return {
        'features': features,
        'source_refs': sorted({row['source'] for row in history}),
        'fixture_ids': sorted({row['id'] for row in home_all + away_all}),
        'cutoff_at': cutoff.isoformat(),
        'completeness': present / len(features) if features else 0,
        'missing': missing,
        'fingerprint': hashlib.sha256(fingerprint_source.encode()).hexdigest(),
    }


def poisson_distribution(rate, maximum=18):
    if not math.isfinite(rate) or rate < 0:
        raise ValueError('Poisson rate must be finite and non-negative')
    probabilities = [math.exp(-rate) * rate ** goals / math.factorial(goals)
                     for goals in range(maximum + 1)]
    total = sum(probabilities)
    return [value / total for value in probabilities]


def normal_cdf(value):
    return (1 + math.erf(value / math.sqrt(2))) / 2


def _line_settlement(value, line, over):
    if abs(line * 4 - round(line * 4)) > 1e-8:
        return None
    doubled = line * 2
    if abs(doubled - round(doubled)) > 1e-8:
        low, high = math.floor(doubled) / 2, math.ceil(doubled) / 2
        left, right = _line_settlement(value, low, over), _line_settlement(value, high, over)
        return None if left is None or right is None else (left + right) / 2
    if line.is_integer() and value == line:
        return 0
    return 1 if ((value > line) == over) else -1


def _settlement_from_grid(outcomes):
    result = {'p_win': 0.0, 'p_half_win': 0.0, 'p_push': 0.0, 'p_half_loss': 0.0, 'p_loss': 0.0}
    for probability, settlement in outcomes:
        key = {1: 'p_win', .5: 'p_half_win', 0: 'p_push', -.5: 'p_half_loss', -1: 'p_loss'}.get(settlement)
        if key:
            result[key] += probability
    total = sum(result.values())
    return {key: value / total for key, value in result.items()} if total else None


def _football_score_grid(features, rho=0.0):
    home = poisson_distribution(features['expected_home_goals'])
    away = poisson_distribution(features['expected_away_goals'])
    grid = []
    for h, ph in enumerate(home):
        for a, pa in enumerate(away):
            correction = 1.0
            if (h, a) == (0, 0):
                correction = 1 - features['expected_home_goals'] * features['expected_away_goals'] * rho
            elif (h, a) == (0, 1):
                correction = 1 + features['expected_home_goals'] * rho
            elif (h, a) == (1, 0):
                correction = 1 + features['expected_away_goals'] * rho
            elif (h, a) == (1, 1):
                correction = 1 - rho
            grid.append((h, a, ph * pa * max(0.0, correction)))
    total = sum(item[2] for item in grid)
    if total:
        grid = [(h, a, probability / total) for h, a, probability in grid]
    return grid


def football_market(features, market, rho=0.0):
    grid = _football_score_grid(features, rho)
    market = market.upper()
    matcher = re.fullmatch(r'(OVER|UNDER)_(\d+(?:\.\d+)?)', market)
    team_total = re.fullmatch(r'(HOME|AWAY)_(OVER|UNDER)_(\d+(?:\.\d+)?)', market)
    handicap = re.fullmatch(r'(HOME|AWAY)_(?:HANDICAP|SPREAD)_(-?\d+(?:\.\d+)?)', market)

    def selection(h, a):
        if market == 'HOME': return int(h > a)
        if market == 'DRAW': return int(h == a)
        if market == 'AWAY': return int(h < a)
        if market == 'DOUBLE_CHANCE_1X': return int(h >= a)
        if market == 'DOUBLE_CHANCE_12': return int(h != a)
        if market == 'DOUBLE_CHANCE_X2': return int(h <= a)
        if market == 'DNB_HOME': return 0 if h == a else (1 if h > a else -1)
        if market == 'DNB_AWAY': return 0 if h == a else (1 if h < a else -1)
        if market == 'BTTS_YES': return int(h > 0 and a > 0)
        if market == 'BTTS_NO': return int(h == 0 or a == 0)
        if matcher:
            return _line_settlement(h + a, float(matcher.group(2)), matcher.group(1) == 'OVER')
        if team_total:
            score = h if team_total.group(1) == 'HOME' else a
            return _line_settlement(score, float(team_total.group(3)), team_total.group(2) == 'OVER')
        if handicap:
            margin = h - a if handicap.group(1) == 'HOME' else a - h
            return _line_settlement(margin, -float(handicap.group(2)), True)
        return None

    return _settlement_from_grid(
        (probability, result) for h, a, probability in grid
        if (result := selection(h, a)) is not None
    )


def basketball_market(features, market):
    market = market.upper()
    home, away = features['expected_home_points'], features['expected_away_points']
    total_sd = features['total_sd']
    margin_sd = total_sd
    moneyline = market in ('HOME', 'MONEYLINE_HOME', 'AWAY', 'MONEYLINE_AWAY')
    if moneyline:
        home_win = normal_cdf((home - away) / margin_sd)
        probability = home_win if market in ('HOME', 'MONEYLINE_HOME') else 1 - home_win
        return {'p_win': probability, 'p_half_win': 0, 'p_push': 0, 'p_half_loss': 0, 'p_loss': 1 - probability}
    total = re.fullmatch(r'(OVER|UNDER)_(\d+(?:\.\d+)?)', market)
    team = re.fullmatch(r'(HOME|AWAY)_(OVER|UNDER)_(\d+(?:\.\d+)?)', market)
    spread = re.fullmatch(r'(HOME|AWAY)_SPREAD_(-?\d+(?:\.\d+)?)', market)
    if total:
        mean = home + away
        line = float(total.group(2))
        over = total.group(1) == 'OVER'
        if line.is_integer():
            push = normal_cdf((line + .5 - mean) / total_sd) - normal_cdf((line - .5 - mean) / total_sd)
            win = 1 - normal_cdf((line + .5 - mean) / total_sd) if over else normal_cdf((line - .5 - mean) / total_sd)
            return {'p_win': win, 'p_half_win': 0, 'p_push': push, 'p_half_loss': 0, 'p_loss': 1 - win - push}
        probability = (1 - normal_cdf((line - mean) / total_sd)) if over else normal_cdf((line - mean) / total_sd)
    elif team:
        mean = home if team.group(1) == 'HOME' else away
        sd = features['home_score_sd'] if team.group(1) == 'HOME' else features['away_score_sd']
        line = float(team.group(3))
        over = team.group(2) == 'OVER'
        if line.is_integer():
            push = normal_cdf((line + .5 - mean) / sd) - normal_cdf((line - .5 - mean) / sd)
            win = 1 - normal_cdf((line + .5 - mean) / sd) if over else normal_cdf((line - .5 - mean) / sd)
            return {'p_win': win, 'p_half_win': 0, 'p_push': push, 'p_half_loss': 0, 'p_loss': 1 - win - push}
        probability = (1 - normal_cdf((line - mean) / sd)) if over else normal_cdf((line - mean) / sd)
    elif spread:
        mean = home - away if spread.group(1) == 'HOME' else away - home
        line = -float(spread.group(2))
        over = True
        if line.is_integer():
            push = normal_cdf((line + .5 - mean) / margin_sd) - normal_cdf((line - .5 - mean) / margin_sd)
            win = 1 - normal_cdf((line + .5 - mean) / margin_sd)
            return {'p_win': win, 'p_half_win': 0, 'p_push': push, 'p_half_loss': 0, 'p_loss': 1 - win - push}
        probability = 1 - normal_cdf((line - mean) / margin_sd)
    elif (first_half := re.fullmatch(r'FIRST_HALF_(OVER|UNDER)_(\d+(?:\.\d+)?)', market)):
        mean = features.get('expected_home_h1')
        away_half = features.get('expected_away_h1')
        sd = features.get('h1_total_sd')
        if mean is None or away_half is None or not sd:
            return None
        line = float(first_half.group(2))
        over = first_half.group(1) == 'OVER'
        total_mean = mean + away_half
        if line.is_integer():
            push = normal_cdf((line + .5 - total_mean) / sd) - normal_cdf((line - .5 - total_mean) / sd)
            win = 1 - normal_cdf((line + .5 - total_mean) / sd) if over else normal_cdf((line - .5 - total_mean) / sd)
            return {'p_win': win, 'p_half_win': 0, 'p_push': push, 'p_half_loss': 0, 'p_loss': 1 - win - push}
        probability = (1 - normal_cdf((line - total_mean) / sd)) if over else normal_cdf((line - total_mean) / sd)
    else:
        return None
    return {'p_win': probability, 'p_half_win': 0, 'p_push': 0, 'p_half_loss': 0, 'p_loss': 1 - probability}


def market_probability(sport, market, features, rho=0.0):
    if sport == 'football':
        return football_market(features, market, rho)
    if sport == 'basketball':
        return basketball_market(features, market)
    return None


def expected_value(settlement, odds):
    if not settlement or not math.isfinite(odds) or odds <= 1:
        return None
    return (
        settlement['p_win'] * (odds - 1)
        + settlement['p_half_win'] * (odds - 1) / 2
        - settlement['p_half_loss'] / 2
        - settlement['p_loss']
    )


def calibration_metrics(samples):
    if not samples:
        return {'sample_size': 0, 'brier_score': None, 'log_loss': None, 'calibration_error': None, 'bins': []}
    count = len(samples)
    brier = log_loss = calibration_error = 0.0
    bins = [{'n': 0, 'p': 0.0, 'y': 0.0, 'lower': index / 10, 'upper': (index + 1) / 10} for index in range(10)]
    for probability, outcome in samples:
        p = min(1 - 1e-15, max(1e-15, probability))
        brier += (p - outcome) ** 2
        log_loss -= outcome * math.log(p) + (1 - outcome) * math.log(1 - p)
        bucket = bins[min(9, int(probability * 10))]
        bucket['n'] += 1
        bucket['p'] += probability
        bucket['y'] += outcome
    for bucket in bins:
        observed = bucket['y'] / bucket['n'] if bucket['n'] else None
        predicted = bucket['p'] / bucket['n'] if bucket['n'] else None
        if bucket['n']:
            calibration_error += bucket['n'] / count * abs(observed - predicted)
        bucket.update({
            'sample_size': bucket.pop('n'),
            'mean_predicted': predicted,
            'observed_frequency': observed,
        })
        bucket.pop('p')
        bucket.pop('y')
    return {
        'sample_size': count, 'brier_score': brier / count,
        'log_loss': log_loss / count, 'calibration_error': calibration_error,
        'bins': bins,
    }


def _temperature(probabilities, temperature):
    values = [math.log(max(1e-15, value)) / temperature for value in probabilities]
    peak = max(values)
    values = [math.exp(value - peak) for value in values]
    return [value / sum(values) for value in values]


def _fit_dixon_coles_rho(rows):
    if not rows:
        return 0.0
    best_rho, best_loss = 0.0, float('inf')
    for step in range(-15, 16):
        rho = step / 100
        loss = 0.0
        for features, home_score, away_score in rows:
            grid = _football_score_grid(features, rho)
            probability = next(
                (value for home, away, value in grid
                 if home == home_score and away == away_score),
                1e-15,
            )
            loss -= math.log(max(1e-15, probability))
        if loss < best_loss:
            best_rho, best_loss = rho, loss
    return best_rho


def _with_rates(features, home_rate, away_rate, sport):
    adjusted = dict(features)
    floor, ceiling = (GOAL_FLOOR, GOAL_CEILING) if sport == 'football' else (POINT_FLOOR, POINT_CEILING)
    home_key = 'expected_home_goals' if sport == 'football' else 'expected_home_points'
    away_key = 'expected_away_goals' if sport == 'football' else 'expected_away_points'
    adjusted[home_key] = min(ceiling, max(floor, home_rate))
    adjusted[away_key] = min(ceiling, max(floor, away_rate))
    return adjusted


def _family_rates(features, sport, family, ridge, booster):
    home_key = 'expected_home_goals' if sport == 'football' else 'expected_home_points'
    away_key = 'expected_away_goals' if sport == 'football' else 'expected_away_points'
    floor, ceiling = (GOAL_FLOOR, GOAL_CEILING) if sport == 'football' else (POINT_FLOOR, POINT_CEILING)
    if family == 'ridge' and ridge:
        home, away = predict_ridge(ridge.get('home'), features), predict_ridge(ridge.get('away'), features)
        if home is None or away is None:
            return None
        return min(ceiling, max(floor, home)), min(ceiling, max(floor, away))
    if family == 'gradient_boosting' and booster:
        return predict_gradient_boosting(booster, features, floor, ceiling)
    return features[home_key], features[away_key]


def _score_seed(features, sport):
    home_key = 'expected_home_goals' if sport == 'football' else 'expected_home_points'
    away_key = 'expected_away_goals' if sport == 'football' else 'expected_away_points'
    key = f"{sport}:{features.get(home_key)}:{features.get(away_key)}:{features.get('home_score_sd')}"
    return int(hashlib.sha256(key.encode()).hexdigest()[:8], 16)


def basketball_monte_carlo_market(features, market, draws=2000):
    if not features.get('home_score_sd') or not features.get('away_score_sd'):
        return None
    samples = [
        (round(home), round(away))
        for home, away in simulate_scores(
            features['expected_home_points'], features['expected_away_points'],
            features['home_score_sd'], features['away_score_sd'], draws=draws,
            seed=_score_seed(features, 'basketball'),
        )
    ]
    market = market.upper()

    def settle(home, away):
        if market in ('HOME', 'MONEYLINE_HOME'):
            return 1 if home > away else 0 if home == away else -1
        if market in ('AWAY', 'MONEYLINE_AWAY'):
            return 1 if away > home else 0 if home == away else -1
        total = re.fullmatch(r'(OVER|UNDER)_(\d+(?:\.\d+)?)', market)
        team = re.fullmatch(r'(HOME|AWAY)_(OVER|UNDER)_(\d+(?:\.\d+)?)', market)
        spread = re.fullmatch(r'(HOME|AWAY)_SPREAD_(-?\d+(?:\.\d+)?)', market)
        first_half = re.fullmatch(r'FIRST_HALF_(OVER|UNDER)_(\d+(?:\.\d+)?)', market)
        if total:
            return _line_settlement(home + away, float(total.group(2)), total.group(1) == 'OVER')
        if team:
            score = home if team.group(1) == 'HOME' else away
            return _line_settlement(score, float(team.group(3)), team.group(2) == 'OVER')
        if spread:
            margin = home - away if spread.group(1) == 'HOME' else away - home
            return _line_settlement(margin, -float(spread.group(2)), True)
        if first_half and features.get('expected_home_h1') is not None and features.get('h1_total_sd'):
            return None
        return None

    if market.startswith('FIRST_HALF_'):
        return market_probability('basketball', market, features)
    return monte_carlo_settlement(samples, settle)


def _outcome_probabilities(sport, features, home_score, away_score, rho, family, ridge, booster):
    rates = _family_rates(features, sport, 'baseline' if family == 'monte_carlo' else family, ridge, booster)
    if not rates or rates[0] is None:
        return None, None
    adjusted = _with_rates(features, rates[0], rates[1], sport)
    if sport == 'football':
        probabilities = [
            market_probability('football', name, adjusted, rho)['p_win']
            for name in ('HOME', 'DRAW', 'AWAY')
        ]
        actual = 0 if home_score > away_score else 2 if home_score < away_score else 1
    elif family == 'monte_carlo':
        settlement = basketball_monte_carlo_market(adjusted, 'MONEYLINE_HOME', draws=800)
        if not settlement:
            return None, None
        probabilities = [settlement['p_win'], max(0.0, 1 - settlement['p_win'] - settlement['p_push'])]
        actual = int(home_score > away_score)
    else:
        home = market_probability('basketball', 'MONEYLINE_HOME', adjusted)
        probabilities = [home['p_win'], 1 - home['p_win']]
        actual = int(home_score > away_score)
    if any(value is None or not math.isfinite(value) or value < 0 for value in probabilities):
        return None, None
    return probabilities, actual


def _mean_logloss(sport, rows, rho, family, ridge, booster):
    if not rows:
        return None
    loss = 0.0
    for row in rows:
        probabilities, actual = _outcome_probabilities(
            sport, row['features'], row['home_score'], row['away_score'], rho, family, ridge, booster,
        )
        if not probabilities:
            return None
        loss -= math.log(max(1e-15, probabilities[actual]))
    return loss / len(rows)


def _fit_structural_models(sport, train_rows):
    tuples = [(row['features'], row['home_score'], row['away_score']) for row in train_rows]
    rho = _fit_dixon_coles_rho(tuples) if sport == 'football' else 0.0
    columns = FOOTBALL_RIDGE_COLUMNS if sport == 'football' else BASKETBALL_RIDGE_COLUMNS
    home_target = 'home_score'
    ridge = None
    home_model = fit_ridge([(row['features'], row['home_score']) for row in train_rows], columns, home_target)
    away_model = fit_ridge([(row['features'], row['away_score']) for row in train_rows], columns, 'away_score')
    if home_model and away_model:
        ridge = {'home': home_model, 'away': away_model}
    booster = None
    names = [column for column in columns if column != 'intercept']
    if sport == 'football':
        booster = fit_gradient_boosting(
            tuples, names, 'home', 'away', 'expected_home_goals', 'expected_away_goals',
        )
    else:
        booster = fit_gradient_boosting(
            tuples, names, 'home', 'away', 'expected_home_points', 'expected_away_points',
        )
    return rho, ridge, booster


def _walk_forward(sport, observations):
    folds = []
    if len(observations) < 15:
        return folds
    start = max(10, len(observations) // 2)
    step = max(1, len(observations) // 5)
    fold_index = 0
    while start < len(observations) and fold_index < 5:
        end = min(len(observations), start + step)
        loss = _mean_logloss(sport, observations[start:end], 0.0, 'baseline', None, None)
        folds.append({
            'fold_index': fold_index, 'model_family': 'baseline', 'market': 'match_outcome',
            'sample_size': end - start, 'log_loss': loss,
        })
        fold_index += 1
        start = end
    return folds


def train_model(connection, sport):
    fixtures = [dict(row) for row in connection.execute(
        """SELECT f.*,r.home_score,r.away_score,r.settled_at
           FROM sports_ai_fixtures f JOIN sports_ai_results r ON r.fixture_id=f.id
           WHERE f.sport=? AND r.status='final' ORDER BY f.starts_at""", (sport,),
    )]
    observations = []
    for fixture in fixtures:
        try:
            kickoff = parse_time(fixture['starts_at'])
            settled = parse_time(fixture['settled_at'])
        except (TypeError, ValueError):
            continue
        if settled < kickoff:
            continue
        built = build_features(connection, fixture)
        if not built:
            continue
        observations.append({
            'features': built['features'], 'home_score': fixture['home_score'],
            'away_score': fixture['away_score'], 'fixture_id': fixture['id'],
            'kickoff': fixture['starts_at'], 'missing': built['missing'],
        })
    count = len(observations)
    train_end = int(count * .5) if count >= 40 else int(count * .7)
    calibration_end = int(count * .7)
    train_rows = observations[:train_end]
    calibration_rows = observations[train_end:calibration_end]
    holdout = observations[calibration_end:]
    rho, ridge, booster = _fit_structural_models(sport, train_rows)
    family_losses = {}
    for family in ('baseline', 'ridge', 'gradient_boosting', 'monte_carlo'):
        if family == 'ridge' and not ridge:
            continue
        if family == 'gradient_boosting' and not booster:
            continue
        if family == 'monte_carlo' and sport != 'basketball':
            continue
        loss = _mean_logloss(sport, holdout, rho, family, ridge, booster)
        if loss is not None:
            family_losses[family] = loss
    if not family_losses:
        family_losses['baseline'] = _mean_logloss(sport, holdout, rho, 'baseline', None, None)
    usable = {name: loss for name, loss in family_losses.items() if loss is not None}
    if usable:
        best_loss = min(usable.values())
        kept = {name: loss for name, loss in usable.items() if loss <= best_loss * 1.02 or name == 'baseline'}
        inverse = {name: 1 / max(loss, 1e-6) for name, loss in kept.items()}
        weight_total = sum(inverse.values())
        ensemble_weights = {name: value / weight_total for name, value in inverse.items()}
        selected_family = min(usable, key=usable.get)
    else:
        ensemble_weights, selected_family = {'baseline': 1.0}, 'baseline'
    temperature_rows = calibration_rows or train_rows
    temperature, best_temperature_loss = 1.0, float('inf')
    for step in range(50, 201, 2):
        candidate = step / 100
        loss = 0.0
        scored = 0
        for row in temperature_rows:
            probabilities, actual = _outcome_probabilities(
                sport, row['features'], row['home_score'], row['away_score'], rho, 'baseline', None, None,
            )
            if not probabilities:
                continue
            calibrated = _temperature(probabilities, candidate)
            loss -= math.log(max(1e-15, calibrated[actual]))
            scored += 1
        if scored and loss < best_temperature_loss:
            temperature, best_temperature_loss = candidate, loss
    binary_pairs = []
    for row in temperature_rows:
        probabilities, actual = _outcome_probabilities(
            sport, row['features'], row['home_score'], row['away_score'], rho, 'baseline', None, None,
        )
        if not probabilities:
            continue
        binary_pairs.append((probabilities[0], 1.0 if actual == 0 else 0.0))
    sigmoid_model = fit_sigmoid(binary_pairs)
    isotonic_blocks = fit_isotonic(binary_pairs) if len(binary_pairs) >= 8 else []
    calibration_method = 'temperature_scaling'
    samples, multiclass_logloss = [], 0.0
    for row in holdout:
        probabilities, actual = _outcome_probabilities(
            sport, row['features'], row['home_score'], row['away_score'], rho,
            'baseline' if selected_family == 'monte_carlo' else selected_family, ridge, booster,
        )
        if not probabilities:
            continue
        calibrated = _temperature(probabilities, temperature)
        multiclass_logloss -= math.log(max(1e-15, calibrated[actual]))
        samples.extend((value, float(index == actual)) for index, value in enumerate(calibrated))
    metrics = calibration_metrics(samples)
    metrics.update({
        'fixtures_scored': count, 'calibration_fixtures': len(temperature_rows),
        'holdout_fixtures': len(holdout),
        'multiclass_log_loss': multiclass_logloss / len(holdout) if holdout else None,
        'family_log_loss': family_losses,
        'walk_forward_folds': len(_walk_forward(sport, observations)),
        'gradient_boosting': 'trained' if booster else 'not_trained_insufficient_rows',
        'ridge': 'trained' if ridge else 'not_trained_insufficient_rows',
        'selected_family': selected_family,
    })
    validated = (
        count >= MIN_VALIDATION_FIXTURES
        and len(holdout) >= 10
        and metrics['calibration_error'] is not None
        and metrics['calibration_error'] <= .20
    )
    folds = _walk_forward(sport, observations) if validated or count >= 15 else _walk_forward(sport, observations)
    run_id, model_id = str(uuid.uuid4()), str(uuid.uuid4())
    model_name = MODEL_NAMES[sport]
    version = str(connection.execute(
        'SELECT COUNT(*)+1 FROM sports_ai_training_runs WHERE sport=? AND model_name=?',
        (sport, model_name),
    ).fetchone()[0])
    timestamps = [row['kickoff'] for row in observations]
    window = {'start': min(timestamps) if timestamps else None, 'end': max(timestamps) if timestamps else None}
    feature_schema = sorted({key for row in observations for key in row['features']}) if observations else []
    params = {
        'calibration': calibration_method, 'temperature': temperature,
        'sigmoid': sigmoid_model, 'isotonic': isotonic_blocks,
        'fit_partition': 'chronological 50/20/30 when 40 or more fixtures, otherwise 70/30',
        'dixon_coles_rho': rho, 'selected_family': selected_family,
        'ensemble_weights': ensemble_weights, 'ridge': ridge,
        'gradient_boosting': booster, 'feature_schema': feature_schema,
    }
    created = datetime.now(timezone.utc).isoformat()
    status = 'validated' if validated else 'insufficient_data'
    artifact = f'sqlite:sports_ai_model_registry:{model_id}'
    connection.execute(
        """INSERT INTO sports_ai_training_runs
           (id,sport,model_name,version,status,training_window_json,parameters_json,metrics_json,sample_size,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (run_id, sport, model_name, version, status, json.dumps(window), json.dumps(params),
         json.dumps(metrics), count, created),
    )
    connection.execute(
        """INSERT INTO sports_ai_model_registry
           (id,training_run_id,sport,model_name,version,market,status,active,parameters_json,metrics_json,
            feature_schema_json,artifact_ref,calibration_status,created_at)
           VALUES(?,?,?,?,?,'match_outcome',?,0,?,?,?,?,?,?)""",
        (model_id, run_id, sport, model_name, version,
         'validated' if validated else 'unvalidated', json.dumps(params), json.dumps(metrics),
         json.dumps(feature_schema), artifact,
         'validated' if validated else 'uncalibrated', created),
    )
    connection.execute(
        """INSERT INTO sports_ai_model_versions
           (id,model_id,version,status,parameters_json,metrics_json,artifact_ref,created_at)
           VALUES(?,?,?,?,?,?,?,?)""",
        (str(uuid.uuid4()), model_id, version, status, json.dumps(params), json.dumps(metrics), artifact, created),
    )
    connection.execute(
        """INSERT INTO sports_ai_calibration_results
           (id,model_id,market,method,sample_size,metrics_json,held_out_start,held_out_end)
           VALUES(?,?,?,?,?,?,?,?)""",
        (str(uuid.uuid4()), model_id, 'match_outcome', calibration_method,
         len(holdout), json.dumps(metrics),
         holdout[0]['kickoff'] if holdout else None,
         holdout[-1]['kickoff'] if holdout else None),
    )
    for index, row in enumerate(observations):
        split = 'train' if index < train_end else 'calibration' if index < calibration_end else 'holdout'
        connection.execute(
            """INSERT INTO sports_ai_training_datasets
               (id,training_run_id,fixture_id,kickoff_at,features_json,label_json,split)
               VALUES(?,?,?,?,?,?,?)""",
            (str(uuid.uuid4()), run_id, row['fixture_id'], row['kickoff'],
             json.dumps(row['features']), json.dumps({
                 'home_score': row['home_score'], 'away_score': row['away_score'],
                 'missing': row['missing'],
             }), split),
        )
    for fold in folds:
        connection.execute(
            """INSERT INTO sports_ai_validation_results
               (id,training_run_id,model_family,market,fold_index,metrics_json,sample_size)
               VALUES(?,?,?,?,?,?,?)""",
            (str(uuid.uuid4()), run_id, fold['model_family'], fold['market'], fold['fold_index'],
             json.dumps({'log_loss': fold['log_loss']}), fold['sample_size']),
        )
    return {
        'model_id': model_id, 'training_run_id': run_id, 'sport': sport,
        'model_name': model_name, 'version': version, 'status': status, 'active': False,
        'metrics': metrics, 'parameters': params, 'training_window': window,
    }


def normalize_quote_market(sport, fixture, quote):
    key, outcome, line = quote['market'].lower(), str(quote['outcome']), quote.get('line')
    home, away = fixture['home'].casefold(), fixture['away'].casefold()
    pick = outcome.casefold()
    if key in ('h2h', 'moneyline'):
        if pick in ('draw', 'tie'):
            return 'DRAW' if sport == 'football' else None
        if pick in ('home', 'moneyline_home') or pick == home:
            return 'HOME' if sport == 'football' else 'MONEYLINE_HOME'
        if pick in ('away', 'moneyline_away') or pick == away:
            return 'AWAY' if sport == 'football' else 'MONEYLINE_AWAY'
    if key in ('totals', 'total'):
        match = re.fullmatch(r'(OVER|UNDER)_(-?\d+(?:\.\d+)?)', outcome.upper())
        if match:
            return f'{match.group(1)}_{float(match.group(2)):g}'
        if pick.upper() in ('OVER', 'UNDER') and line is not None:
            return f'{pick.upper()}_{float(line):g}'
    if key in ('btts', 'both_teams_to_score') and sport == 'football' and pick in ('yes', 'no'):
        return 'BTTS_' + pick.upper()
    if key in ('spreads', 'spread') and line is not None:
        normalized = re.fullmatch(r'(HOME|AWAY)_SPREAD_(-?\d+(?:\.\d+)?)', outcome.upper())
        if normalized:
            return f'{normalized.group(1)}_SPREAD_{float(normalized.group(2)):g}'
        side = 'HOME' if pick in ('home', home) else 'AWAY' if pick in ('away', away) else None
        if side:
            return f'{side}_SPREAD_{float(line):g}'
    return None


def _outcomes(sport, features=None):
    if sport == 'football':
        markets = list(FOOTBALL_MARKETS)
        for line in ('-1.5', '-1', '-0.75', '-0.5', '-0.25', '0', '0.25', '0.5', '0.75', '1', '1.5'):
            markets.append(f'HOME_HANDICAP_{line}')
            markets.append(f'AWAY_HANDICAP_{line}')
        return markets
    markets = ['MONEYLINE_HOME', 'MONEYLINE_AWAY'] + [
        f'{side}_{line}' for line in ('199.5', '209.5', '219.5', '229.5', '239.5')
        for side in ('OVER', 'UNDER')
    ] + [
        f'{team}_{side}_{line}' for team in ('HOME', 'AWAY')
        for side in ('OVER', 'UNDER') for line in ('79.5', '89.5', '99.5', '109.5')
    ] + ['HOME_SPREAD_-5.5', 'AWAY_SPREAD_5.5', 'HOME_SPREAD_-3.5', 'AWAY_SPREAD_3.5']
    if features and features.get('h1_total_sd') and features.get('expected_home_h1') is not None and features.get('expected_away_h1') is not None:
        line = math.floor(features['expected_home_h1'] + features['expected_away_h1']) + 0.5
        markets.extend([f'FIRST_HALF_OVER_{line:g}', f'FIRST_HALF_UNDER_{line:g}'])
    return markets


def _select_quotes(quotes):
    grouped = {}
    for quote in quotes:
        grouped.setdefault((quote['bookmaker'], quote['market'], quote['outcome'], quote.get('line')), []).append(quote)
    selected, conflicts = [], []
    for key, items in grouped.items():
        items.sort(key=lambda item: item['observed_at'])
        current = [item for item in items if item['observed_at'] == items[-1]['observed_at']]
        prices = {round(float(item['decimal_odds']), 4) for item in current}
        if len(prices) > 1:
            conflicts.append({'bookmaker': key[0], 'market': key[1], 'outcome': key[2], 'line': key[3]})
            continue
        chosen = dict(current[-1])
        chosen['opening_decimal_odds'] = float(items[0]['decimal_odds'])
        chosen['quote_count'] = len(items)
        selected.append(chosen)
    return selected, conflicts


def _blend(left, right, weight):
    return {
        key: left[key] * (1 - weight) + right[key] * weight
        for key in ('p_win', 'p_half_win', 'p_push', 'p_half_loss', 'p_loss')
    }


def settle_market(sport, market, features, parameters):
    weights = parameters.get('ensemble_weights') or {'baseline': 1.0}
    rho = float(parameters.get('dixon_coles_rho') or 0.0)
    home = away = used = 0.0
    for family, weight in weights.items():
        if family == 'monte_carlo':
            continue
        rates = _family_rates(features, sport, family, parameters.get('ridge'), parameters.get('gradient_boosting'))
        if not rates or rates[0] is None or rates[1] is None:
            continue
        home += weight * rates[0]
        away += weight * rates[1]
        used += weight
    adjusted = _with_rates(features, home / used, away / used, sport) if used else dict(features)
    analytical = market_probability(sport, market, adjusted, rho)
    monte_carlo_weight = float(weights.get('monte_carlo', 0.0)) if sport == 'basketball' else 0.0
    if sport != 'basketball' or monte_carlo_weight <= 0 or not analytical:
        return adjusted, analytical
    simulated = basketball_monte_carlo_market(adjusted, market)
    if not simulated:
        return adjusted, analytical
    if monte_carlo_weight >= 0.999:
        return adjusted, simulated
    return adjusted, _blend(analytical, simulated, monte_carlo_weight)


def _scorelines(sport, features, rho):
    if sport == 'football':
        ranked = sorted(_football_score_grid(features, rho), key=lambda item: item[2], reverse=True)[:8]
        return [{'home_goals': home, 'away_goals': away, 'probability': probability} for home, away, probability in ranked]
    mean = features['expected_home_points'] + features['expected_away_points']
    deviation = features['total_sd']
    ranges = []
    for center in (mean - 12, mean - 4, mean + 4, mean + 12):
        low, high = center - 4, center + 4
        probability = normal_cdf((high - mean) / deviation) - normal_cdf((low - mean) / deviation)
        ranges.append({'low_points': round(low, 1), 'high_points': round(high, 1), 'probability': probability})
    return ranges


def _explain(sport, market, features, settlement, missing):
    if sport == 'football':
        summary = (
            f"{market} probability {settlement['p_win']:.3f} uses opponent-adjusted expected goals "
            f"{features['expected_home_goals']:.2f}-{features['expected_away_goals']:.2f}. "
            f"Home attack is {features['home_attack']:.2f} and away attack is {features['away_attack']:.2f}, "
            f"against league averages of {features['league_home_goals']:.2f} home goals and "
            f"{features['league_away_goals']:.2f} away goals. "
            f"Samples: {int(features['home_games'])} home-team matches and {int(features['away_games'])} away-team matches. "
        )
        if features.get('h2h_games'):
            summary += (
                f"Head-to-head matches before cutoff: {int(features['h2h_games'])}, "
                f"averaging {features['h2h_avg_goals']:.2f} goals. "
            )
        else:
            summary += 'No head-to-head match is stored before the cutoff. '
        if features.get('home_xg_for') is None and features.get('away_xg_for') is None:
            summary += 'No source expected-goals values are available, so rates use goals scored and conceded. '
        else:
            summary += 'Source expected goals were included where at least three historical values existed. '
        summary += features.get('history_basis') or 'Rates use completed matches in this competition.'
        factors = [{
            'name': 'opponent_adjusted_expected_goals',
            'home': features['expected_home_goals'], 'away': features['expected_away_goals'],
            'home_attack': features['home_attack'], 'away_attack': features['away_attack'],
            'home_defence': features['home_defence'], 'away_defence': features['away_defence'],
        }]
    else:
        summary = (
            f"{market} probability {settlement['p_win']:.3f} uses opponent-adjusted expected points "
            f"{features['expected_home_points']:.1f}-{features['expected_away_points']:.1f} "
            f"and scoring standard deviations {features['home_score_sd']:.1f} and {features['away_score_sd']:.1f}. "
        )
        if features.get('expected_pace') is None:
            summary += 'Pace and possessions were not in the source data and were not imputed. '
        else:
            summary += f"Recorded pace average is {features['expected_pace']:.1f}. "
        if features.get('injuries_out_home') is None and features.get('injuries_out_away') is None:
            summary += 'Injury and lineup figures were not supplied.'
        factors = [{
            'name': 'opponent_adjusted_expected_points',
            'home': features['expected_home_points'], 'away': features['expected_away_points'],
            'home_score_sd': features['home_score_sd'], 'away_score_sd': features['away_score_sd'],
            'expected_pace': features.get('expected_pace'),
        }]
    if features.get('home_rest_days') is not None and features.get('away_rest_days') is not None:
        summary += (
            f" Rest before cutoff: home {features['home_rest_days']:.1f} days, away {features['away_rest_days']:.1f} days."
            f" Matches in the previous seven days: home {int(features['home_games_7d'])}, away {int(features['away_games_7d'])}."
        )
    return summary, factors, missing


def _rank_score(edge, calibration_error, probability, completeness, fresh):
    reliability = 0.0 if calibration_error is None else max(0.0, 1 - float(calibration_error))
    bounded = min(1 - 1e-6, max(1e-6, probability))
    uncertainty = -(bounded * math.log(bounded) + (1 - bounded) * math.log(1 - bounded)) / math.log(2)
    edge_component = 0.0 if edge is None else max(0.0, min(1.0, float(edge)))
    score = (
        0.30 * reliability + 0.30 * edge_component + 0.15 * (1 - uncertainty)
        + 0.15 * (completeness or 0) + 0.10 * (1.0 if fresh else 0.0)
    )
    return score, uncertainty


def run_analysis(connection, actor, audit, deadline=None, fixture_id=None):
    now = datetime.now(timezone.utc)
    if fixture_id:
        selected = connection.execute(
            'SELECT * FROM sports_ai_fixtures WHERE id=?', (fixture_id,),
        ).fetchone()
        fixtures = [dict(selected)] if selected else []
    else:
        fixtures = [dict(row) for row in connection.execute(
            f"""SELECT * FROM sports_ai_fixtures f
                WHERE {upcoming_fixture_sql('f')}
                  AND NOT EXISTS (SELECT 1 FROM sports_ai_engine_predictions p WHERE p.fixture_id=f.id)
                  AND NOT EXISTS (
                    SELECT 1 FROM sports_ai_prediction_jobs j
                    WHERE j.idempotency_key='insufficient:' || f.id
                      AND j.finished_at > datetime('now','-12 hours')
                  )
                ORDER BY {upcoming_order_sql('f')}
                LIMIT 40"""
        )]
    models = {}
    for sport in ('football', 'basketball'):
        model = connection.execute(
            'SELECT * FROM sports_ai_model_registry WHERE sport=? AND active=1', (sport,),
        ).fetchone()
        if not model:
            model = connection.execute(
                'SELECT * FROM sports_ai_model_registry WHERE sport=? ORDER BY created_at DESC LIMIT 1', (sport,),
            ).fetchone()
        if not model:
            train_model(connection, sport)
            model = connection.execute(
                'SELECT * FROM sports_ai_model_registry WHERE sport=? ORDER BY created_at DESC LIMIT 1', (sport,),
            ).fetchone()
        if model:
            models[sport] = dict(model)
    settings_row = connection.execute(
        "SELECT value_json FROM sports_ai_settings WHERE key='engine'",
    ).fetchone()
    settings = json.loads(settings_row['value_json']) if settings_row else {}
    min_edge = float(settings.get('min_edge', .05))
    odds_age_limit = int(settings.get('odds_max_age_minutes', 15))
    run_id = str(uuid.uuid4())
    connection.execute(
        """INSERT INTO sports_ai_analysis_runs
           (id,fixture_count,selection_count,ticket_count,status,details_json)
           VALUES(?,?,?,?,?,?)""",
        (run_id,len(fixtures),0,0,'running',json.dumps({
            'pipeline': ['feature_engineering','inference','calibration','odds_matching','value_assessment'],
            'minimum_edge': min_edge, 'odds_max_age_minutes': odds_age_limit,
        })),
    )
    stats = {'predictions': 0, 'opportunities': 0, 'qualified': 0, 'skipped': {}, 'snapshots_reused': 0}
    for fixture in fixtures:
        if deadline and datetime.now(timezone.utc) > deadline:
            raise TimeoutError('Prediction job exceeded its timeout')
        model = models.get(fixture['sport'])
        if not model:
            continue
        try:
            built = build_features(connection, fixture)
            if not built:
                stats['skipped']['insufficient_history'] = stats['skipped'].get('insufficient_history', 0) + 1
                moment = datetime.now(timezone.utc).isoformat()
                connection.execute(
                    """INSERT INTO sports_ai_prediction_jobs
                       (id,idempotency_key,trigger,status,attempt,started_at,finished_at,details_json)
                       VALUES(?,?, 'analysis','skipped',1,?,?,?)
                       ON CONFLICT(idempotency_key) DO UPDATE SET
                         status='skipped', finished_at=excluded.finished_at, details_json=excluded.details_json""",
                    (str(uuid.uuid4()), f"insufficient:{fixture['id']}", moment, moment,
                     json.dumps({'fixture_id': fixture['id'], 'reason': 'insufficient_history'})),
                )
                continue
            existing_snapshot = connection.execute(
                """SELECT id,input_fingerprint FROM sports_ai_feature_snapshots
                   WHERE fixture_id=? AND cutoff_at=?""",
                (fixture['id'], built['cutoff_at']),
            ).fetchone()
            if existing_snapshot and existing_snapshot['input_fingerprint'] == built['fingerprint']:
                feature_id = existing_snapshot['id']
                stats['snapshots_reused'] += 1
            else:
                feature_id = existing_snapshot['id'] if existing_snapshot else str(uuid.uuid4())
                connection.execute(
                    """INSERT INTO sports_ai_feature_snapshots
                       (id,fixture_id,sport,cutoff_at,features_json,source_refs_json,generated_at,
                        completeness,input_fingerprint)
                       VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(fixture_id,cutoff_at) DO UPDATE SET
                       features_json=excluded.features_json,source_refs_json=excluded.source_refs_json,
                       generated_at=excluded.generated_at,completeness=excluded.completeness,
                       input_fingerprint=excluded.input_fingerprint""",
                    (feature_id, fixture['id'], fixture['sport'], built['cutoff_at'],
                     json.dumps(built['features'], allow_nan=False), json.dumps({
                         'sources': built['source_refs'], 'fixture_ids': built['fixture_ids'],
                         'missing': built['missing'],
                     }), now.isoformat(), built['completeness'], built['fingerprint']),
                )
                feature_id = connection.execute(
                    'SELECT id FROM sports_ai_feature_snapshots WHERE fixture_id=? AND cutoff_at=?',
                    (fixture['id'], built['cutoff_at']),
                ).fetchone()['id']
            quotes = [dict(row) for row in connection.execute(
                """SELECT o.* FROM sports_ai_odds_snapshots o
                   JOIN sports_ai_provider_registry p ON p.id=o.provider_id
                   WHERE o.fixture_id=? AND p.enabled=1 AND p.terms_reviewed=1 AND p.status='operational'""",
                (fixture['id'],),
            )]
            selected_quotes, conflicts = _select_quotes(quotes)
            mapped = []
            for quote in selected_quotes:
                canonical = normalize_quote_market(fixture['sport'], fixture, quote)
                if canonical:
                    connection.execute(
                        """INSERT OR IGNORE INTO sports_ai_market_mappings
                           (id,sport,provider_market,provider_outcome,line,canonical_market)
                           VALUES(?,?,?,?,?,?)""",
                        (str(uuid.uuid4()), fixture['sport'], quote['market'], quote['outcome'],
                         quote.get('line'), canonical),
                    )
                mapped.append((quote, canonical))
            conflicted = set()
            for conflict in conflicts:
                canonical = normalize_quote_market(fixture['sport'], fixture, {
                    'market': conflict['market'], 'outcome': conflict['outcome'], 'line': conflict['line'],
                })
                if canonical:
                    conflicted.add(canonical)
            features = built['features']
            parameters = json.loads(model['parameters_json'] or '{}')
            if not model['active']:
                parameters = {
                    'ensemble_weights': {'baseline': 1.0},
                    'dixon_coles_rho': float(parameters.get('dixon_coles_rho') or 0.0),
                    'temperature': 1.0,
                }
            temperature = float(parameters.get('temperature', 1.0))
            markets = set(_outcomes(fixture['sport'], features))
            markets.update(market for _, market in mapped if market)
            markets.update(conflicted)
            for market in sorted(markets):
                adjusted, settlement = settle_market(fixture['sport'], market, features, parameters)
                if not settlement:
                    continue
                rho = float(parameters.get('dixon_coles_rho') or 0.0)
                if model['active'] and fixture['sport']=='football' and market in ('HOME','DRAW','AWAY'):
                    base=[market_probability('football',name,adjusted,rho)['p_win']
                          for name in ('HOME','DRAW','AWAY')]
                    calibrated=_temperature(base,temperature)
                    probability=calibrated[('HOME','DRAW','AWAY').index(market)]
                    settlement={'p_win':probability,'p_half_win':0.0,'p_push':0.0,
                                'p_half_loss':0.0,'p_loss':1-probability}
                elif model['active'] and fixture['sport']=='basketball' and market in ('MONEYLINE_HOME','MONEYLINE_AWAY'):
                    raw=market_probability('basketball','MONEYLINE_HOME',adjusted)['p_win']
                    calibrated=_temperature([raw,1-raw],temperature)
                    probability=calibrated[0 if market=='MONEYLINE_HOME' else 1]
                    settlement={'p_win':probability,'p_half_win':0.0,'p_push':0.0,
                                'p_half_loss':0.0,'p_loss':1-probability}
                market_calibrated=(
                    model['active'] and
                    ((fixture['sport']=='football' and market in ('HOME','DRAW','AWAY'))
                     or (fixture['sport']=='basketball' and market in ('MONEYLINE_HOME','MONEYLINE_AWAY')))
                )
                prediction_id = str(uuid.uuid4())
                summary, factors, missing = _explain(
                    fixture['sport'], market, adjusted, settlement, built['missing'],
                )
                reliability = json.loads(model['metrics_json'])
                evidence = {
                    'summary': summary,
                    'factors': factors,
                    'features': adjusted,
                    'raw_features': features,
                    'missing': missing,
                    'source_refs': built['source_refs'],
                    'historical_games': features['historical_games'],
                    'predicted_probability': settlement['p_win'],
                    'probability': settlement['p_win'],
                    'data_completeness': built['completeness'],
                    'model_reliability': reliability,
                    'calibration_status': 'validated' if market_calibrated else 'awaiting_market_calibration',
                    'model_name': model['model_name'],
                    'model_version': model['version'],
                    'model_family': parameters.get('selected_family', 'baseline'),
                    'scorelines': _scorelines(fixture['sport'], adjusted, rho),
                    'market_implied_probability': None,
                }
                connection.execute(
                    """INSERT INTO sports_ai_engine_predictions
                       (id,run_id,fixture_id,model_id,feature_id,market,outcome,probability,
                        settlement_json,explanation_json,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (prediction_id,run_id,fixture['id'],model['id'],feature_id,market,market,
                     settlement['p_win'],json.dumps(settlement),json.dumps(evidence),now.isoformat()),
                )
                connection.execute(
                    """INSERT INTO sports_ai_prediction_explanations
                       (id,prediction_id,summary,factors_json,missing_json)
                       VALUES(?,?,?,?,?)""",
                    (str(uuid.uuid4()), prediction_id, summary, json.dumps(factors), json.dumps(missing)),
                )
                stats['predictions'] += 1
                available = [(quote, canonical) for quote, canonical in mapped if canonical == market]
                if market in conflicted and not available:
                    connection.execute(
                        """INSERT INTO sports_ai_value_opportunities
                           (id,prediction_id,status,reason,evaluated_at,rank_score,uncertainty,data_completeness)
                           VALUES(?,?,?,?,?,?,?,?)""",
                        (str(uuid.uuid4()), prediction_id, 'rejected',
                         'Duplicate quotes at the same timestamp disagree, so no price was selected.',
                         now.isoformat(), None, None, built['completeness']),
                    )
                    stats['opportunities'] += 1
                    continue
                if not available:
                    status = 'awaiting_calibration' if not model['active'] else 'awaiting_odds'
                    reason = 'Model is not explicitly activated after validation.' if not model['active'] else 'No permitted bookmaker quote is available.'
                    rank, uncertainty = _rank_score(None, reliability.get('calibration_error'), settlement['p_win'], built['completeness'], False)
                    connection.execute(
                        """INSERT INTO sports_ai_value_opportunities
                           (id,prediction_id,status,reason,evaluated_at,rank_score,uncertainty,data_completeness)
                           VALUES(?,?,?,?,?,?,?,?)""",
                        (str(uuid.uuid4()),prediction_id,status,reason,now.isoformat(),rank,uncertainty,built['completeness']),
                    )
                    stats['opportunities'] += 1
                    continue
                for quote, _ in available:
                    observed = parse_time(quote['observed_at'])
                    age = (now - observed).total_seconds() / 60
                    issue = connection.execute(
                        """SELECT 1 FROM sports_ai_data_quality_issues
                           WHERE fixture_id=? AND resolved_at IS NULL
                           AND severity IN ('high','critical') LIMIT 1""",
                        (fixture['id'],),
                    ).fetchone()
                    fresh = 0 <= age <= odds_age_limit
                    if age < -5:
                        status, reason = 'rejected', 'Odds timestamp is in the future.'
                    elif age > odds_age_limit:
                        status, reason = 'expired', 'Odds quote is stale.'
                    elif issue:
                        status, reason = 'suspended', 'Unresolved fixture data-quality issue.'
                    elif not market_calibrated:
                        status, reason = 'awaiting_calibration', 'This market has no validated calibration evidence.'
                    elif built['completeness'] < 0.5:
                        status, reason = 'rejected', 'Underlying match data is too incomplete for qualification.'
                    elif parse_time(fixture['starts_at']) <= now:
                        status, reason = 'expired', 'The fixture has already started.'
                    else:
                        edge = expected_value(settlement,float(quote['decimal_odds']))
                        if edge is None or edge < min_edge:
                            status, reason = 'rejected', 'Expected value is below the configured edge. This is not a guaranteed or risk-free selection.'
                        else:
                            status, reason = 'qualified', 'Fresh permitted quote and validated active model meet the qualification checks. Expected value is an estimate, not a guarantee.'
                            stats['qualified'] += 1
                    edge = expected_value(settlement,float(quote['decimal_odds']))
                    rank, uncertainty = _rank_score(
                        edge, reliability.get('calibration_error'), settlement['p_win'], built['completeness'], fresh,
                    )
                    evidence['market_implied_probability'] = 1 / float(quote['decimal_odds'])
                    evidence['opening_decimal_odds'] = quote.get('opening_decimal_odds')
                    evidence['current_decimal_odds'] = float(quote['decimal_odds'])
                    connection.execute(
                        'UPDATE sports_ai_engine_predictions SET explanation_json=? WHERE id=?',
                        (json.dumps(evidence), prediction_id),
                    )
                    connection.execute(
                        """INSERT INTO sports_ai_value_opportunities
                           (id,prediction_id,odds_id,implied_probability,expected_edge,status,reason,evaluated_at,
                            rank_score,uncertainty,data_completeness)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                        (str(uuid.uuid4()),prediction_id,quote['id'],
                         1 / float(quote['decimal_odds']),edge,status,reason,now.isoformat(),
                         rank,uncertainty,built['completeness']),
                    )
                    stats['opportunities'] += 1
        except (ValueError,TypeError,KeyError,OverflowError) as error:
            kind = type(error).__name__
            stats['skipped'][kind] = stats['skipped'].get(kind, 0) + 1
    details = {**stats,'minimum_edge':min_edge,'odds_max_age_minutes':odds_age_limit,
               'activated_models':[item['id'] for item in models.values() if item['active']]}
    connection.execute(
        "UPDATE sports_ai_analysis_runs SET selection_count=?,status='complete',details_json=? WHERE id=?",
        (stats['qualified'],json.dumps(details),run_id),
    )
    audit(connection,actor,'prediction_engine.run',{'run_id':run_id,**stats})
    return {'run_id':run_id,'fixtures':len(fixtures),**stats,
            'model_activation_required':not any(item['active'] for item in models.values())}


def recover_timed_out_jobs(connection, now=None):
    now = now or datetime.now(timezone.utc)
    recovered = 0
    rows = connection.execute(
        "SELECT id,started_at,timeout_seconds FROM sports_ai_prediction_jobs WHERE status='running'",
    ).fetchall()
    for row in rows:
        try:
            started = parse_time(row['started_at'])
        except (TypeError, ValueError):
            started = now - timedelta(days=1)
        if (now - started).total_seconds() > int(row['timeout_seconds']):
            connection.execute(
                """UPDATE sports_ai_prediction_jobs
                   SET status='timed_out',finished_at=?,error='Processing timeout' WHERE id=?""",
                (now.isoformat(), row['id']),
            )
            recovered += 1
    return recovered


def enqueue_prediction_job(connection, trigger):
    key = f"{trigger}:{datetime.now(timezone.utc).strftime('%Y%m%d%H')}"
    connection.execute(
        """INSERT OR IGNORE INTO sports_ai_prediction_jobs
           (id,idempotency_key,trigger,status,timeout_seconds,details_json)
           VALUES(?,?,?,'queued',600,'{}')""",
        (str(uuid.uuid4()), key, trigger),
    )
    return key


def execute_prediction_job(connection, actor, audit, idempotency_key, trigger='manual', timeout_seconds=600):
    now = datetime.now(timezone.utc)
    recover_timed_out_jobs(connection, now)
    existing = connection.execute(
        'SELECT * FROM sports_ai_prediction_jobs WHERE idempotency_key=?',
        (idempotency_key,),
    ).fetchone()
    if existing and existing['status'] == 'complete':
        return {
            'job_id': existing['id'], 'status': 'skipped', 'reason': 'idempotent_replay',
            'result': json.loads(existing['details_json'] or '{}'),
        }
    running = connection.execute(
        "SELECT COUNT(*) FROM sports_ai_prediction_jobs WHERE status='running'",
    ).fetchone()[0]
    if running >= MAX_CONCURRENT_JOBS:
        return {
            'job_id': existing['id'] if existing else None,
            'status': 'skipped', 'reason': 'concurrency_limit',
        }
    if existing and existing['status'] in ('failed', 'timed_out') and int(existing['attempt']) >= 3:
        return {'job_id': existing['id'], 'status': 'skipped', 'reason': 'retry_limit'}
    attempt = int(existing['attempt']) + 1 if existing and existing['status'] in ('failed', 'timed_out') else (
        int(existing['attempt']) if existing else 1
    )
    job_id = existing['id'] if existing else str(uuid.uuid4())
    if existing:
        connection.execute(
            """UPDATE sports_ai_prediction_jobs
               SET status='running',attempt=?,started_at=?,finished_at=NULL,error=NULL,trigger=?
               WHERE id=?""",
            (attempt, now.isoformat(), trigger, job_id),
        )
    else:
        connection.execute(
            """INSERT INTO sports_ai_prediction_jobs
               (id,idempotency_key,trigger,status,attempt,started_at,timeout_seconds,details_json)
               VALUES(?,?,?,'running',?,?,?,'{}')""",
            (job_id, idempotency_key, trigger, attempt, now.isoformat(), timeout_seconds),
        )
    try:
        result = run_analysis(
            connection, actor, audit, deadline=now + timedelta(seconds=timeout_seconds),
        )
    except TimeoutError as error:
        connection.execute(
            """UPDATE sports_ai_prediction_jobs
               SET status='timed_out',finished_at=?,error=? WHERE id=?""",
            (datetime.now(timezone.utc).isoformat(), str(error), job_id),
        )
        return {'job_id': job_id, 'status': 'timed_out', 'error': str(error)}
    except Exception as error:
        connection.execute(
            """UPDATE sports_ai_prediction_jobs
               SET status='failed',finished_at=?,error=? WHERE id=?""",
            (datetime.now(timezone.utc).isoformat(), str(error)[:500], job_id),
        )
        raise
    connection.execute(
        """UPDATE sports_ai_prediction_jobs
           SET status='complete',finished_at=?,details_json=? WHERE id=?""",
        (datetime.now(timezone.utc).isoformat(), json.dumps(result), job_id),
    )
    return {'job_id': job_id, 'status': 'complete', **result}


def rollback_model(connection, model_id):
    model = connection.execute(
        'SELECT * FROM sports_ai_model_registry WHERE id=?', (model_id,),
    ).fetchone()
    if not model:
        return None
    if not model['active']:
        return {'rolled_back': False, 'reason': 'Model is not the active version.'}
    previous = connection.execute(
        """SELECT * FROM sports_ai_model_registry
           WHERE sport=? AND id<>? AND activated_at IS NOT NULL
           ORDER BY activated_at DESC LIMIT 1""",
        (model['sport'], model_id),
    ).fetchone()
    restored = None
    if previous:
        metrics = json.loads(previous['metrics_json'])
        eligible = (
            previous['calibration_status'] == 'validated'
            or (
                metrics.get('calibration_error') is not None
                and metrics['calibration_error'] <= .20
                and int(metrics.get('holdout_fixtures') or 0) >= 10
                and int(metrics.get('fixtures_scored') or 0) >= MIN_VALIDATION_FIXTURES
            )
        )
        if eligible:
            restored = previous['id']
    connection.execute(
        """UPDATE sports_ai_model_registry
           SET active=0,status='inactive',replaced_by=? WHERE id=?""",
        (restored, model_id),
    )
    if restored:
        connection.execute(
            """UPDATE sports_ai_model_registry
               SET active=1,status='active',activated_at=? WHERE id=?""",
            (datetime.now(timezone.utc).isoformat(), restored),
        )
    return {'rolled_back': True, 'restored_model_id': restored}


PRODUCTION_TARGETS = (('football', '2'), ('basketball', '1'))
MAX_CALIBRATION_ERROR = 0.20
DEGRADED_CALIBRATION_ERROR = 0.25


def _save_setting(connection, key, payload, now):
    connection.execute(
        """INSERT INTO sports_ai_settings(key,value_json,updated_at) VALUES(?,?,?)
           ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at""",
        (key, json.dumps(payload), now.isoformat()),
    )


def _governance_state(connection):
    row = connection.execute("SELECT value_json FROM sports_ai_settings WHERE key='model_governance'").fetchone()
    if not row:
        return {'decisions': [], 'blocked_ids': []}
    try:
        state = json.loads(row['value_json'])
    except (TypeError, json.JSONDecodeError):
        state = {}
    state.setdefault('decisions', [])
    state.setdefault('blocked_ids', [])
    return state


def _decision_reasons(model):
    reasons = []
    metrics = json.loads(model['metrics_json'] or '{}')
    families = metrics.get('family_log_loss') or {}
    selected = metrics.get('selected_family')
    selected_loss = families.get(selected)
    baseline = families.get('baseline')
    if model['status'] not in ('validated', 'active') or model['training_status'] != 'validated':
        reasons.append('The training run is not validated.')
    if int(model['calibration_sample_size'] or 0) < 10 or int(metrics.get('holdout_fixtures') or 0) < 10:
        reasons.append('The chronological holdout has fewer than 10 fixtures.')
    if int(metrics.get('fixtures_scored') or 0) < MIN_VALIDATION_FIXTURES:
        reasons.append(f'The scored sample is below {MIN_VALIDATION_FIXTURES} fixtures.')
    error = metrics.get('calibration_error')
    if error is None or error > MAX_CALIBRATION_ERROR:
        reasons.append(f'Holdout calibration error is above {MAX_CALIBRATION_ERROR:.2f}.')
    if baseline is None or selected_loss is None:
        reasons.append('The holdout does not contain both a baseline and a selected-family log loss.')
    elif selected_loss >= baseline:
        reasons.append(
            f'{selected} holdout log loss {selected_loss:.4f} does not beat the baseline {baseline:.4f}.'
        )
    return reasons, metrics


def govern_models(connection, actor, audit, now=None):
    """Activate Football v2 and Basketball v1 only from their stored chronological holdout."""
    current = now or datetime.now(timezone.utc)
    state = _governance_state(connection)
    blocked = set(state.get('blocked_ids') or [])
    decisions = []
    for sport, version in PRODUCTION_TARGETS:
        model = connection.execute(
            """SELECT m.*, t.status AS training_status, c.sample_size AS calibration_sample_size
               FROM sports_ai_model_registry m
               JOIN sports_ai_training_runs t ON t.id=m.training_run_id
               LEFT JOIN sports_ai_calibration_results c ON c.model_id=m.id
               WHERE m.sport=? AND m.version=? AND m.model_name=?
               ORDER BY m.created_at DESC LIMIT 1""",
            (sport, version, MODEL_NAMES[sport]),
        ).fetchone()
        if not model:
            decisions.append({'sport': sport, 'version': version, 'action': 'missing', 'reasons': ['No stored model version was found.']})
            continue
        if model['id'] in blocked:
            decisions.append({
                'sport': sport, 'version': version, 'model_id': model['id'], 'action': 'blocked',
                'reasons': ['This version was rolled back after live degradation and stays inactive until a new training run.'],
            })
            continue
        reasons, metrics = _decision_reasons(model)
        if model['active']:
            decisions.append({
                'sport': sport, 'version': version, 'model_id': model['id'], 'action': 'already_active',
                'reasons': [], 'calibration_error': metrics.get('calibration_error'),
                'holdout_fixtures': metrics.get('holdout_fixtures'),
            })
            continue
        if reasons:
            decisions.append({
                'sport': sport, 'version': version, 'model_id': model['id'], 'action': 'inactive',
                'reasons': reasons, 'calibration_error': metrics.get('calibration_error'),
                'holdout_fixtures': metrics.get('holdout_fixtures'),
            })
            continue
        connection.execute(
            "UPDATE sports_ai_model_registry SET active=0,status='inactive' WHERE sport=? AND active=1 AND id<>?",
            (sport, model['id']),
        )
        connection.execute(
            """UPDATE sports_ai_model_registry SET active=1,status='active',activated_at=? WHERE id=?""",
            (current.isoformat(), model['id']),
        )
        decisions.append({
            'sport': sport, 'version': version, 'model_id': model['id'], 'action': 'activated',
            'reasons': [], 'calibration_error': metrics.get('calibration_error'),
            'holdout_fixtures': metrics.get('holdout_fixtures'),
            'selected_family': metrics.get('selected_family'),
            'family_log_loss': metrics.get('family_log_loss'),
        })
        if audit:
            audit(connection, actor, 'models.activate', {'model_id': model['id'], 'sport': sport, 'version': version, 'source': 'holdout_governance'})
    state['decisions'] = decisions
    state['evaluated_at'] = current.isoformat()
    _save_setting(connection, 'model_governance', state, current)
    return {'decisions': decisions, 'activated': [item['model_id'] for item in decisions if item['action'] == 'activated']}


def review_degradation(connection, actor, audit, now=None):
    """Roll an active model back when settled pre-match picks show calibration error above the production ceiling."""
    current = now or datetime.now(timezone.utc)
    state = _governance_state(connection)
    rolled = []
    models = connection.execute(
        """SELECT id,sport,version FROM sports_ai_model_registry WHERE active=1 AND status='active'""",
    ).fetchall()
    for model in models:
        rows = connection.execute(
            """SELECT calibration_error, sample_size FROM sports_ai_model_evaluations
               WHERE model_id=? AND market IN ('HOME','DRAW','AWAY','MONEYLINE_HOME','MONEYLINE_AWAY')
                 AND sample_size>=20 AND created_at>=COALESCE((SELECT activated_at FROM sports_ai_model_registry WHERE id=?),'')
               ORDER BY created_at DESC LIMIT 6""",
            (model['id'], model['id']),
        ).fetchall()
        if not rows:
            continue
        worst = max(row['calibration_error'] or 0 for row in rows)
        if worst <= DEGRADED_CALIBRATION_ERROR:
            continue
        result = rollback_model(connection, model['id'])
        blocked = set(state.get('blocked_ids') or [])
        blocked.add(model['id'])
        state['blocked_ids'] = sorted(blocked)
        rolled.append({'model_id': model['id'], 'sport': model['sport'], 'calibration_error': worst, 'rollback': result})
        if audit:
            audit(connection, actor, 'models.rollback', {'model_id': model['id'], 'reason': 'live_calibration_error', 'calibration_error': worst})
    if rolled:
        _save_setting(connection, 'model_governance', state, current)
    return {'rolled_back': rolled}


def reprice_active_batch(connection, actor, audit, limit=20, now=None):
    """Re-infer headline markets with the active model and qualify only a fresh displayed SportyBet price."""
    current = now or datetime.now(timezone.utc)
    settings_row = connection.execute("SELECT value_json FROM sports_ai_settings WHERE key='engine'").fetchone()
    settings = json.loads(settings_row['value_json']) if settings_row else {}
    min_edge = float(settings.get('min_edge', 0.05))
    odds_age = int(settings.get('odds_max_age_minutes', 15))
    fresh_after = current - timedelta(minutes=odds_age)
    latest = connection.execute(
        "SELECT MAX(observed_at) FROM sports_ai_odds_snapshots WHERE bookmaker='SportyBet'",
    ).fetchone()[0]
    summary = {
        'checked': 0, 'repriced': 0, 'qualified': 0, 'rejected': 0, 'stale': 0,
        'min_edge': min_edge, 'odds_max_age_minutes': odds_age, 'latest_sportybet_quote': latest,
    }
    run_id = str(uuid.uuid4())
    fixtures = connection.execute(
        f"""SELECT f.*, m.id AS model_id, m.parameters_json, m.metrics_json, m.activated_at, m.active
           FROM sports_ai_fixtures f
           JOIN sports_ai_model_registry m ON m.sport=f.sport AND m.active=1 AND m.status='active'
           WHERE {upcoming_fixture_sql('f')}
             AND EXISTS (
               SELECT 1 FROM sports_ai_odds_snapshots q
               WHERE q.fixture_id=f.id AND q.bookmaker='SportyBet' AND q.observed_at>=?
             )
             AND EXISTS (SELECT 1 FROM sports_ai_feature_snapshots s WHERE s.fixture_id=f.id)
           ORDER BY {upcoming_order_sql('f')} LIMIT ?""",
        (fresh_after.isoformat(), max(1, min(int(limit), 40))),
    ).fetchall()
    if fixtures:
        connection.execute(
            """INSERT INTO sports_ai_analysis_runs
               (id,fixture_count,selection_count,ticket_count,status,details_json)
               VALUES(?,?,?,?,?,?)""",
            (run_id, len(fixtures), 0, 0, 'running', json.dumps({'pipeline': 'active_reprice'})),
        )
    if not fixtures and latest:
        summary['reason'] = f'No upcoming fixture has a SportyBet quote inside the {odds_age} minute freshness window.'
    for fixture in fixtures:
        summary['checked'] += 1
        snapshot = connection.execute(
            """SELECT id, features_json, completeness FROM sports_ai_feature_snapshots
               WHERE fixture_id=? ORDER BY generated_at DESC LIMIT 1""",
            (fixture['id'],),
        ).fetchone()
        if not snapshot or snapshot['completeness'] is None or snapshot['completeness'] < 0.5:
            summary['rejected'] += 1
            continue
        features = json.loads(snapshot['features_json'])
        parameters = json.loads(fixture['parameters_json'] or '{}')
        markets = ('HOME', 'DRAW', 'AWAY') if fixture['sport'] == 'football' else ('MONEYLINE_HOME', 'MONEYLINE_AWAY')
        quotes = [dict(row) for row in connection.execute(
            """SELECT * FROM sports_ai_odds_snapshots
               WHERE fixture_id=? AND bookmaker='SportyBet' AND source_url<>'' AND observed_at>=?
               ORDER BY observed_at DESC""",
            (fixture['id'], fresh_after.isoformat()),
        )]
        by_market = {}
        for quote in quotes:
            canonical = normalize_quote_market(fixture['sport'], fixture, quote)
            if canonical in markets and canonical not in by_market:
                by_market[canonical] = quote
        if not by_market:
            summary['stale'] += 1
            continue
        temperature = float(parameters.get('temperature', 1.0))
        rho = float(parameters.get('dixon_coles_rho') or 0.0)
        for market, quote in by_market.items():
            existing = connection.execute(
                """SELECT 1 FROM sports_ai_engine_predictions
                   WHERE fixture_id=? AND market=? AND created_at>=? LIMIT 1""",
                (fixture['id'], market, fixture['activated_at'] or current.isoformat()),
            ).fetchone()
            if existing:
                continue
            adjusted, settlement = settle_market(fixture['sport'], market, features, parameters)
            if not settlement:
                continue
            if fixture['sport'] == 'football':
                base = [market_probability('football', name, adjusted, rho)['p_win'] for name in markets]
                calibrated = _temperature(base, temperature)
                probability = min(1 - 1e-6, max(1e-6, calibrated[markets.index(market)]))
                settlement = {'p_win': probability, 'p_half_win': 0.0, 'p_push': 0.0, 'p_half_loss': 0.0, 'p_loss': 1 - probability}
            elif market in ('MONEYLINE_HOME', 'MONEYLINE_AWAY'):
                raw = market_probability('basketball', 'MONEYLINE_HOME', adjusted)['p_win']
                calibrated = _temperature([raw, 1 - raw], temperature)
                probability = calibrated[0 if market == 'MONEYLINE_HOME' else 1]
                settlement = {'p_win': probability, 'p_half_win': 0.0, 'p_push': 0.0, 'p_half_loss': 0.0, 'p_loss': 1 - probability}
            prediction_id = str(uuid.uuid4())
            connection.execute(
                """INSERT INTO sports_ai_engine_predictions
                   (id,run_id,fixture_id,model_id,feature_id,market,outcome,probability,settlement_json,explanation_json,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (prediction_id, run_id, fixture['id'], fixture['model_id'], snapshot['id'], market, market,
                 settlement['p_win'], json.dumps(settlement), json.dumps({'source': 'active_model_reprice'}), current.isoformat()),
            )
            summary['repriced'] += 1
            edge = expected_value(settlement, float(quote['decimal_odds']))
            if edge is None or edge < min_edge:
                status, reason = 'rejected', 'Expected value is below the configured edge. This is not a guaranteed selection.'
                summary['rejected'] += 1
            else:
                status, reason = 'qualified', 'A fresh SportyBet displayed price and the active calibrated model clear the configured edge. Expected value is an estimate, not a guarantee.'
                summary['qualified'] += 1
            connection.execute(
                """INSERT INTO sports_ai_value_opportunities
                   (id,prediction_id,odds_id,implied_probability,expected_edge,status,reason,evaluated_at,data_completeness)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (str(uuid.uuid4()), prediction_id, quote['id'], 1 / float(quote['decimal_odds']), edge, status, reason,
                 current.isoformat(), snapshot['completeness']),
            )
    if summary['checked'] and not summary['qualified'] and 'reason' not in summary:
        summary['reason'] = 'Fresh displayed prices were checked and none cleared the configured edge.'
    if fixtures:
        connection.execute(
            """UPDATE sports_ai_analysis_runs
               SET selection_count=?, ticket_count=0, status='complete', details_json=? WHERE id=?""",
            (summary['qualified'], json.dumps(summary), run_id),
        )
    _save_setting(connection, 'value_cycle', summary, current)
    if audit:
        audit(connection, actor, 'value.reprice', summary)
    return summary
