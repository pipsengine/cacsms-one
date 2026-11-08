"""Draw research from each sport's own stored results and model probabilities.

A full-time draw is scored only for sports whose stored score can finish level.
Each league is compared with that sport's results. One sport's draw rate is not
applied to another. Half-time frequencies stay empty because no half-time score
is stored. Historical draw percentage is context. It is not the ranking score.
"""

import json
import math
from datetime import datetime, timedelta, timezone

from sport_support import GOAL_SPORTS as SCORE_SPORTS, sql_in

# Descriptive score frequencies remain available without a regulation-result
# model. A match-winner model never supplies a draw probability for these rows.
GOAL_SPORTS = SCORE_SPORTS + ('ice_hockey', 'handball', 'rugby', 'futsal')

STATUS_KEY = 'draw_intelligence_status'
REFRESH_HOURS = 6
LEAGUE_PRIOR = 30
TEAM_PRIOR = 12
HALF_LIFE_DAYS = 180
SCOPE_VERSION = 3


def _now(now=None):
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current


def _parse(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _rate(draws, matches):
    if matches <= 0:
        return None
    return draws / matches


def _uncertainty(rate, matches):
    if rate is None or matches <= 0:
        return None
    p = min(1 - 1e-6, max(1e-6, rate))
    return math.sqrt(p * (1 - p) / matches)


def shrink(rate, matches, prior, strength):
    """Pull a small sample toward the observed prior. No prior means no score."""
    if rate is None or matches <= 0 or prior is None:
        return None
    return (matches * rate + strength * prior) / (matches + strength)


def _weight(kickoff, now):
    age = max(0.0, (now - kickoff).total_seconds() / 86400)
    return 0.5 ** (age / HALF_LIFE_DAYS)


def _weighted_draw_rate(rows, now):
    weight = 0.0
    draws = 0.0
    for row in rows:
        item = _weight(row['kickoff'], now)
        weight += item
        draws += item * row['draw']
    if weight <= 0:
        return None
    return draws / weight


def rolling(rows, window):
    """Most recent matches first. The rate stays empty until the window is full."""
    chosen = rows[:window]
    count = len(chosen)
    if count < window:
        return {'matches': count, 'rate': None, 'complete': False}
    return {'matches': count, 'rate': _rate(sum(row['draw'] for row in chosen), count), 'complete': True}


def _quality(matches, seasons=1, team=False):
    if team:
        if matches < 8:
            return 'thin'
        if matches < 20:
            return 'limited'
        return 'usable'
    if matches < 30 or seasons < 2:
        return 'thin' if matches < 30 else 'limited'
    if matches < 80:
        return 'limited'
    return 'usable'


def _year(kickoff):
    return kickoff.year


def refresh_if_due(connection, now=None):
    current = _now(now)
    row = connection.execute(
        'SELECT value_json FROM sports_ai_settings WHERE key=?', (STATUS_KEY,),
    ).fetchone()
    stored = json.loads(row['value_json']) if row else {}
    previous = _parse(stored.get('refreshed_at'))
    if stored.get('scope_version') == SCOPE_VERSION and previous and current - previous < timedelta(hours=REFRESH_HOURS):
        return {'status': 'current', 'refreshed_at': stored.get('refreshed_at')}
    return refresh_draw_intelligence(connection, current)


def _gates(connection):
    row = connection.execute(
        "SELECT value_json FROM sports_ai_settings WHERE key='qualification_policy'",
    ).fetchone()
    stored = json.loads(row['value_json']) if row else {}
    return (
        int(stored.get('odds_max_age_minutes') or 15),
        float(stored.get('max_calibration_error') or 0.20),
        int(stored.get('min_holdout_fixtures') or 10),
    )


def _expected_goals(connection):
    leagues, teams = {}, {}
    for row in connection.execute(
        f"""SELECT f.sport, f.competition, f.home, f.away,
                  json_extract(s.features_json,'$.expected_home_goals') AS home_goals,
                  json_extract(s.features_json,'$.expected_away_goals') AS away_goals
           FROM sports_ai_feature_snapshots s
           JOIN sports_ai_fixtures f ON f.id=s.fixture_id
           WHERE f.sport IN ({sql_in(GOAL_SPORTS)})""",
    ):
        competition = row['competition'] or ''
        league = leagues.setdefault((row['sport'], competition), {'home': [], 'away': []})
        if row['home_goals'] is not None:
            league['home'].append(float(row['home_goals']))
            teams.setdefault((row['sport'], competition, row['home']), []).append(float(row['home_goals']))
        if row['away_goals'] is not None:
            league['away'].append(float(row['away_goals']))
            teams.setdefault((row['sport'], competition, row['away']), []).append(float(row['away_goals']))
    return leagues, teams


def _mean(values):
    return sum(values) / len(values) if values else None


def _active_model(connection):
    row = connection.execute(
        """SELECT model_name, version, parameters_json, metrics_json
           FROM sports_ai_model_registry WHERE sport='football' AND active=1""",
    ).fetchone()
    if not row:
        return {
            'available': False,
            'promoted': False,
            'reason': 'No active football model is stored. Upcoming fixtures are not ranked.',
        }
    parameters = json.loads(row['parameters_json'] or '{}')
    metrics = json.loads(row['metrics_json'] or '{}')
    return {
        'available': True,
        'promoted': False,
        'model_name': row['model_name'],
        'version': row['version'],
        'dixon_coles_rho': parameters.get('dixon_coles_rho'),
        'temperature': parameters.get('temperature'),
        'ensemble_weights': parameters.get('ensemble_weights'),
        'calibration_error': metrics.get('calibration_error'),
        'holdout_fixtures': metrics.get('holdout_fixtures'),
        'brier_score': metrics.get('brier_score'),
        'reason': 'Stored Dixon–Coles and ensemble probabilities are ranked. This refresh does not train or activate a replacement model.',
    }


def refresh_draw_intelligence(connection, now=None):
    current = _now(now)
    results = []
    for row in connection.execute(
        f"""SELECT f.sport, f.competition, f.home, f.away, f.starts_at, r.home_score, r.away_score
           FROM sports_ai_fixtures f
           JOIN sports_ai_results r ON r.fixture_id=f.id
           WHERE f.sport IN ({sql_in(GOAL_SPORTS)}) AND r.status='final'""",
    ):
        kickoff = _parse(row['starts_at'])
        if kickoff is None:
            continue
        results.append({
            'sport': row['sport'],
            'competition': row['competition'] or '',
            'home': row['home'], 'away': row['away'], 'kickoff': kickoff,
            'home_score': int(row['home_score']), 'away_score': int(row['away_score']),
            'draw': int(row['home_score'] == row['away_score']),
        })
    results, unscored = _without_unscored(results)
    by_sport = {}
    for row in results:
        by_sport.setdefault(row['sport'], []).append(row)
    global_rates = {sport: _weighted_draw_rate(rows, current) for sport, rows in by_sport.items()}
    leagues = _league_rows(results, global_rates, current)
    teams = _team_rows(results, leagues, global_rates, current)
    expected_leagues, expected_teams = _expected_goals(connection)
    for key, item in leagues.items():
        stored = expected_leagues.get(key) or {'home': [], 'away': []}
        item['detail']['expected_home_goals'] = _mean(stored['home'])
        item['detail']['expected_away_goals'] = _mean(stored['away'])
        item['detail']['expected_goals_matches'] = max(len(stored['home']), len(stored['away']))
    for key, item in teams.items():
        stored = expected_teams.get(key) or []
        item['detail']['expected_goals'] = _mean(stored)
        item['detail']['expected_goals_matches'] = len(stored)
    fixtures = _fixture_rows(connection, leagues, teams, current)
    validation = _validation(connection)
    moment = current.isoformat()
    connection.execute('DELETE FROM sports_ai_draw_leagues')
    connection.execute('DELETE FROM sports_ai_draw_teams')
    connection.execute('DELETE FROM sports_ai_draw_fixtures')
    for item in leagues.values():
        connection.execute(
            """INSERT INTO sports_ai_draw_leagues
               (sport,competition,matches,draws,draw_rate,reliability,uncertainty,seasons,score_00,score_11,score_22,
                goals_for,goals_against,quality,detail_json,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                item['sport'], item['competition'], item['matches'], item['draws'], item['draw_rate'], item['reliability'],
                item['uncertainty'], item['seasons'], item['score_00'], item['score_11'], item['score_22'],
                item['goals_for'], item['goals_against'], item['quality'], json.dumps(item['detail']), moment,
            ),
        )
    for item in teams.values():
        connection.execute(
            """INSERT INTO sports_ai_draw_teams
               (sport,competition,team,matches,draw_rate,home_matches,home_draw_rate,away_matches,away_draw_rate,
                roll5_matches,roll5_rate,roll10_matches,roll10_rate,roll20_matches,roll20_rate,
                goals_for,goals_against,reliability,uncertainty,schedule_strength,adjusted_balance,
                quality,detail_json,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                item['sport'], item['competition'], item['team'], item['matches'], item['draw_rate'], item['home_matches'],
                item['home_draw_rate'], item['away_matches'], item['away_draw_rate'],
                item['roll5_matches'], item['roll5_rate'], item['roll10_matches'], item['roll10_rate'],
                item['roll20_matches'], item['roll20_rate'], item['goals_for'], item['goals_against'],
                item['reliability'], item['uncertainty'], item['schedule_strength'], item['adjusted_balance'],
                item['quality'], json.dumps(item['detail']), moment,
            ),
        )
    for item in fixtures:
        connection.execute(
            """INSERT INTO sports_ai_draw_fixtures
               (fixture_id,sport,competition,home,away,starts_at,model_probability,implied_probability,edge,calibrated,
                qualification_state,league_reliability,home_reliability,away_reliability,rank_score,explanation,quality,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                item['fixture_id'], item['sport'], item['competition'], item['home'], item['away'], item['starts_at'],
                item['model_probability'], item['implied_probability'], item['edge'], item['calibrated'],
                item['qualification_state'], item['league_reliability'], item['home_reliability'],
                item['away_reliability'], item['rank_score'], item['explanation'], item['quality'], moment,
            ),
        )
    summary = {
        'refreshed_at': moment,
        'matches': len(results),
        'leagues': len(leagues),
        'teams': len(teams),
        'fixtures': len(fixtures),
        'ranked': sum(1 for item in fixtures if item['rank_score'] is not None),
        'scope_version': SCOPE_VERSION,
        'sports': sorted(by_sport),
        'draw_rates': global_rates,
        'global_draw_rate': global_rates.get('football'),
        'unscored_competitions': unscored,
        'note': 'Each sport is scored from its own finals and its own leagues. A draw rate is not copied from another sport. A competition whose every stored final is 0–0 is left out of the rate.',
        'halftime': None,
        'halftime_reason': 'No half-time score is stored.',
        'validation': validation,
        'model': _active_model(connection),
        'places_wager': False,
    }
    connection.execute(
        """INSERT INTO sports_ai_settings(key,value_json,updated_at) VALUES(?,?,?)
           ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at""",
        (STATUS_KEY, json.dumps(summary), moment),
    )
    return summary


def _without_unscored(results):
    grouped = {}
    for row in results:
        grouped.setdefault((row['sport'], row['competition']), []).append(row)
    dropped = {
        key for key, rows in grouped.items()
        if rows and all(item['home_score'] == 0 and item['away_score'] == 0 for item in rows)
    }
    kept = [row for row in results if (row['sport'], row['competition']) not in dropped]
    return kept, len(dropped)


def _league_rows(results, global_rates, now):
    grouped = {}
    for row in results:
        grouped.setdefault((row['sport'], row['competition']), []).append(row)
    output = {}
    for (sport, competition), rows in grouped.items():
        draws = sum(row['draw'] for row in rows)
        matches = len(rows)
        rate = _weighted_draw_rate(rows, now)
        prior = global_rates.get(sport)
        years = {_year(row['kickoff']) for row in rows}
        scores = {(0, 0): 0, (1, 1): 0, (2, 2): 0}
        home_goals = 0
        away_goals = 0
        buckets = {0: 0, 1: 0, 2: 0, 3: 0}
        for row in rows:
            pair = (row['home_score'], row['away_score'])
            if pair in scores:
                scores[pair] += 1
            home_goals += row['home_score']
            away_goals += row['away_score']
            total = row['home_score'] + row['away_score']
            buckets[min(3, total)] += 1
        output[(sport, competition)] = {
            'sport': sport,
            'competition': competition,
            'matches': matches,
            'draws': draws,
            'draw_rate': rate,
            'reliability': shrink(rate, matches, prior, LEAGUE_PRIOR),
            'uncertainty': _uncertainty(rate, matches),
            'seasons': len(years),
            'score_00': _rate(scores[(0, 0)], matches),
            'score_11': _rate(scores[(1, 1)], matches),
            'score_22': _rate(scores[(2, 2)], matches),
            'goals_for': home_goals / matches,
            'goals_against': away_goals / matches,
            'quality': _quality(matches, len(years)),
            'detail': {
                'calendar_years': sorted(years),
                'scoring_distribution': {
                    '0_goals': _rate(buckets[0], matches),
                    '1_goal': _rate(buckets[1], matches),
                    '2_goals': _rate(buckets[2], matches),
                    '3_or_more_goals': _rate(buckets[3], matches),
                },
                'home_draw_rate': rate,
                'away_draw_rate': rate,
                'venue_note': 'A league draw is a match result, so the home and away draw rates are the same stored rate.',
                'halftime': None,
                'halftime_reason': 'No half-time score is stored.',
                'prior': 'The reliability score shrinks the recency-weighted draw rate toward this sport’s own stored draw rate.',
                'regulation': 'Ice hockey uses the 60-minute score. A winner after overtime is not a draw.' if sport == 'ice_hockey' else None,
            },
        }
    return output


def _team_rows(results, leagues, global_rates, now):
    by_team = {}
    opponent_samples = {}
    for row in results:
        for team, venue, scored, conceded in (
            (row['home'], 'home', row['home_score'], row['away_score']),
            (row['away'], 'away', row['away_score'], row['home_score']),
        ):
            key = (row['sport'], row['competition'], team)
            by_team.setdefault(key, []).append({
                'kickoff': row['kickoff'], 'draw': row['draw'], 'venue': venue,
                'for': scored, 'against': conceded, 'opponent': row['away'] if venue == 'home' else row['home'],
            })
            opponent_samples.setdefault((row['sport'], row['competition'], row['away'] if venue == 'home' else row['home']), []).append(scored - conceded)
    output = {}
    for key, rows in by_team.items():
        sport, competition, team = key
        rows.sort(key=lambda item: item['kickoff'], reverse=True)
        matches = len(rows)
        rate = _weighted_draw_rate(rows, now)
        home = [item for item in rows if item['venue'] == 'home']
        away = [item for item in rows if item['venue'] == 'away']
        league_rate = (leagues.get((sport, competition)) or {}).get('draw_rate') or global_rates.get(sport)
        strengths = []
        for item in rows:
            sample = opponent_samples.get((sport, competition, item['opponent'])) or []
            if len(sample) >= 5:
                strengths.append(sum(sample) / len(sample))
        schedule = sum(strengths) / len(strengths) if strengths else None
        goals_for = sum(item['for'] for item in rows) / matches
        goals_against = sum(item['against'] for item in rows) / matches
        balance = goals_for - goals_against
        prior_strength = TEAM_PRIOR if schedule is not None else TEAM_PRIOR * 2
        roll5, roll10, roll20 = rolling(rows, 5), rolling(rows, 10), rolling(rows, 20)
        output[key] = {
            'sport': sport,
            'competition': competition,
            'team': team,
            'matches': matches,
            'draw_rate': rate,
            'home_matches': len(home),
            'home_draw_rate': _weighted_draw_rate(home, now),
            'away_matches': len(away),
            'away_draw_rate': _weighted_draw_rate(away, now),
            'roll5_matches': roll5['matches'],
            'roll5_rate': roll5['rate'],
            'roll10_matches': roll10['matches'],
            'roll10_rate': roll10['rate'],
            'roll20_matches': roll20['matches'],
            'roll20_rate': roll20['rate'],
            'goals_for': goals_for,
            'goals_against': goals_against,
            'reliability': shrink(rate, matches, league_rate, prior_strength),
            'uncertainty': _uncertainty(rate, matches),
            'schedule_strength': schedule,
            'adjusted_balance': None if schedule is None else balance - schedule,
            'quality': _quality(matches, team=True),
            'detail': {
                'defensive_strength': 'Goals against per stored match. Lower is stronger. This is not an expected-goals model.',
                'schedule_strength': 'Mean opponent goal difference from stored matches. Empty when no opponent has five stored matches.',
                'halftime': None,
            },
        }
    return output


def candidate_sort_key(item):
    """Value first, then calibrated probability. Historical draw rate is not a key."""
    return (
        item.get('rank_score') is None,
        -(item.get('rank_score') or 0),
        -(item.get('model_probability') or 0) if item.get('calibrated') else 1,
        item.get('starts_at') or '',
        item.get('fixture_id') or '',
    )


def _fixture_rows(connection, leagues, teams, now):
    horizon = (now + timedelta(days=7)).isoformat()
    odds_minutes, max_error, min_holdout = _gates(connection)
    upcoming = connection.execute(
        f"""SELECT id, sport, competition, home, away, starts_at
           FROM sports_ai_fixtures
           WHERE sport IN ({sql_in(GOAL_SPORTS)}) AND status IN ('scheduled','not_started') AND starts_at>? AND starts_at<=?
           ORDER BY starts_at""",
        (now.isoformat(), horizon),
    ).fetchall()
    predictions = {}
    for row in connection.execute(
        """SELECT p.fixture_id, p.probability, p.created_at, m.active, m.metrics_json,
                  o.expected_edge, q.decimal_odds, q.observed_at
           FROM sports_ai_engine_predictions p
           JOIN sports_ai_model_registry m ON m.id=p.model_id
           LEFT JOIN sports_ai_value_opportunities o ON o.prediction_id=p.id
           LEFT JOIN sports_ai_odds_snapshots q ON q.id=o.odds_id
           WHERE p.market='DRAW'""",
    ):
        current = predictions.get(row['fixture_id'])
        if current is None or (str(row['created_at']), str(row['observed_at'] or '')) > (str(current['created_at']), str(current['observed_at'] or '')):
            predictions[row['fixture_id']] = dict(row)
    decisions = {}
    for row in connection.execute(
        """SELECT fixture_id, state, updated_at FROM sports_ai_qualification_decisions
           WHERE upper(market)='DRAW'""",
    ):
        current = decisions.get(row['fixture_id'])
        if current is None or str(row['updated_at']) > str(current['updated_at']):
            decisions[row['fixture_id']] = dict(row)
    rows = []
    for fixture in upcoming:
        stored = predictions.get(fixture['id']) or {}
        metrics = json.loads(stored.get('metrics_json') or '{}') if stored else {}
        error = metrics.get('calibration_error')
        calibrated = (
            stored.get('probability') is not None
            and int(stored.get('active') or 0) == 1
            and error is not None
            and int(metrics.get('holdout_fixtures') or 0) >= min_holdout
            and float(error) <= max_error
        )
        implied = None
        fresh = False
        observed = _parse(stored.get('observed_at'))
        if stored.get('decimal_odds'):
            implied = 1 / float(stored['decimal_odds'])
            fresh = observed is not None and now - observed <= timedelta(minutes=odds_minutes)
        edge = float(stored['expected_edge']) if fresh and stored.get('expected_edge') is not None else None
        rank = edge if calibrated and edge is not None else None
        sport = fixture['sport']
        league = leagues.get((sport, fixture['competition'] or '')) or {}
        home = teams.get((sport, fixture['competition'] or '', fixture['home'])) or {}
        away = teams.get((sport, fixture['competition'] or '', fixture['away'])) or {}
        if not stored:
            explanation = 'No stored draw probability. The historical draw rate is not used as a prediction.'
            quality = 'missing_model'
        elif not calibrated:
            explanation = 'A draw probability is stored, but the model has no calibration error, so it is not ranked.'
            quality = 'uncalibrated'
        elif edge is None:
            explanation = 'Calibrated draw probability is stored. No fresh bookmaker price is stored, so there is no value rank.'
            quality = 'no_price'
        else:
            explanation = 'Ranked by this sport’s stored calibrated draw probability against the fresh price, not by historical draw percentage.'
            quality = 'ranked'
        rows.append({
            'fixture_id': fixture['id'],
            'sport': sport,
            'competition': fixture['competition'] or '',
            'home': fixture['home'],
            'away': fixture['away'],
            'starts_at': fixture['starts_at'],
            'model_probability': float(stored['probability']) if stored.get('probability') is not None else None,
            'implied_probability': implied if fresh else None,
            'edge': edge,
            'calibrated': int(bool(calibrated)),
            'qualification_state': (decisions.get(fixture['id']) or {}).get('state'),
            'league_reliability': league.get('reliability'),
            'home_reliability': home.get('reliability'),
            'away_reliability': away.get('reliability'),
            'rank_score': rank,
            'explanation': explanation,
            'quality': quality,
        })
    rows.sort(key=candidate_sort_key)
    return rows


def _validation(connection):
    pairs = []
    for row in connection.execute(
        """SELECT p.probability, p.created_at, r.home_score, r.away_score, r.settled_at
           FROM sports_ai_engine_predictions p
           JOIN sports_ai_results r ON r.fixture_id=p.fixture_id AND r.status='final'
           WHERE p.market='DRAW'""",
    ):
        created, settled = _parse(row['created_at']), _parse(row['settled_at'])
        if created and settled and created < settled:
            outcome = 1 if row['home_score'] == row['away_score'] else 0
            pairs.append((float(row['probability']) - outcome) ** 2)
    if not pairs:
        return {'sample_size': 0, 'brier_score': None, 'reason': 'No stored draw probability was recorded before a final result.'}
    return {'sample_size': len(pairs), 'brier_score': sum(pairs) / len(pairs), 'reason': 'Brier score of stored draw probabilities recorded before the final result.'}


def _stored_profiles(connection):
    leagues = {
        (row['sport'], row['competition']): {'reliability': row['reliability']}
        for row in connection.execute('SELECT sport, competition, reliability FROM sports_ai_draw_leagues')
    }
    teams = {
        (row['sport'], row['competition'], row['team']): {'reliability': row['reliability']}
        for row in connection.execute('SELECT sport, competition, team, reliability FROM sports_ai_draw_teams')
    }
    return leagues, teams


def summary(connection, now=None):
    row = connection.execute(
        'SELECT value_json FROM sports_ai_settings WHERE key=?', (STATUS_KEY,),
    ).fetchone()
    stored = json.loads(row['value_json']) if row else {}
    stored.setdefault('halftime', None)
    stored.setdefault('halftime_reason', 'No half-time score is stored.')
    stored['places_wager'] = False
    if stored.get('refreshed_at'):
        leagues, teams = _stored_profiles(connection)
        fixtures = _fixture_rows(connection, leagues, teams, _now(now))
        stored['fixtures'] = len(fixtures)
        stored['ranked'] = sum(1 for item in fixtures if item['rank_score'] is not None)
    return stored


def list_leagues(connection, competition='', quality=''):
    clauses, args = ['1=1'], []
    if competition:
        clauses.append('competition LIKE ?')
        args.append(f'%{competition}%')
    if quality:
        clauses.append('quality=?')
        args.append(quality)
    rows = []
    for row in connection.execute(
        f"""SELECT * FROM sports_ai_draw_leagues WHERE {' AND '.join(clauses)}
            ORDER BY CASE quality WHEN 'usable' THEN 0 WHEN 'limited' THEN 1 WHEN 'thin' THEN 2 ELSE 3 END,
                     reliability IS NULL, reliability DESC, matches DESC LIMIT 200""",
        args,
    ):
        item = dict(row)
        item['detail'] = json.loads(item.pop('detail_json') or '{}')
        rows.append(item)
    return rows


def list_teams(connection, competition='', quality=''):
    clauses, args = ['1=1'], []
    if competition:
        clauses.append('competition LIKE ?')
        args.append(f'%{competition}%')
    if quality:
        clauses.append('quality=?')
        args.append(quality)
    rows = []
    for row in connection.execute(
        f"""SELECT * FROM sports_ai_draw_teams WHERE {' AND '.join(clauses)}
            ORDER BY CASE quality WHEN 'usable' THEN 0 WHEN 'limited' THEN 1 WHEN 'thin' THEN 2 ELSE 3 END,
                     reliability IS NULL, reliability DESC, matches DESC LIMIT 200""",
        args,
    ):
        item = dict(row)
        item['detail'] = json.loads(item.pop('detail_json') or '{}')
        rows.append(item)
    return rows


def build_draw_router(auth):
    from fastapi import APIRouter, Depends
    from db import connect

    router = APIRouter(prefix='/api/sports-ai', tags=['Draw intelligence'])

    @router.get('/draw/summary')
    def draw_summary(_actor=Depends(auth)):
        with connect() as connection:
            return summary(connection)

    @router.get('/draw/leagues')
    def draw_leagues(competition: str = '', quality: str = '', _actor=Depends(auth)):
        with connect() as connection:
            return {'items': list_leagues(connection, competition, quality)}

    @router.get('/draw/teams')
    def draw_teams(competition: str = '', quality: str = '', _actor=Depends(auth)):
        with connect() as connection:
            return {'items': list_teams(connection, competition, quality)}

    @router.get('/draw/fixtures')
    def draw_fixtures(competition: str = '', _actor=Depends(auth)):
        with connect() as connection:
            return {'items': list_fixtures(connection, competition)}

    @router.post('/draw/refresh')
    def draw_refresh(_actor=Depends(auth)):
        with connect() as connection:
            return refresh_draw_intelligence(connection)

    return router


def list_fixtures(connection, competition='', now=None):
    """Rank from the current stored model, price and qualification decision."""
    leagues, teams = _stored_profiles(connection)
    rows = _fixture_rows(connection, leagues, teams, _now(now))
    if competition:
        needle = competition.casefold()
        rows = [row for row in rows if needle in (row['competition'] or '').casefold()]
    return rows[:200]
