"""Fixture-scoped historical collection, form, and Poisson match analysis."""

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

from acquisition_engine import _json_body, _openligadb, _persist
from prediction_engine import (
    MIN_TEAM_GAMES,
    _football_score_grid,
    build_features,
    _aligned_team,
    competitions_for_league,
    domestic_team_rows,
    _names_for_club,
    league_key,
    parse_time,
    run_analysis,
    upcoming_fixture_sql,
    upcoming_order_sql,
)

log = logging.getLogger('sports_ai.match_analysis')


class AnalysisBusy(Exception):
    pass


def _league_key(competition):
    return league_key(competition)


def _rows_before(connection, team, competition, cutoff, sport='football'):
    key = _league_key(competition)
    names = competitions_for_league(connection, sport, competition)
    marks = ','.join('?' * len(names))
    league = [dict(raw) for raw in connection.execute(
        f"""SELECT f.id,f.competition,f.home,f.away,f.starts_at,f.source,f.observed_at,
                  r.home_score,r.away_score,r.settled_at,r.source AS result_source
           FROM sports_ai_fixtures f
           JOIN sports_ai_results r ON r.fixture_id=f.id
           WHERE f.sport=? AND f.competition IN ({marks}) AND r.status='final'
           ORDER BY f.starts_at""",
        (sport, *names),
    )]
    club_names = _names_for_club(team, league)
    rows = []
    for row in league:
        if row['home'] not in club_names and row['away'] not in club_names:
            continue
        try:
            kickoff = parse_time(row['starts_at'])
            settled = parse_time(row['settled_at'])
        except (TypeError, ValueError):
            continue
        if kickoff < cutoff and settled < cutoff:
            row['_kickoff'] = kickoff
            if row['home'] in club_names and row['home'] != team:
                row['home'] = team
            elif row['away'] in club_names and row['away'] != team:
                row['away'] = team
            rows.append(row)
    if len(rows) < MIN_TEAM_GAMES:
        seen = {row['id'] for row in rows}
        for item in domestic_team_rows(connection, sport, team, cutoff):
            if item['id'] in seen:
                continue
            rows.append(item)
            seen.add(item['id'])
        rows.sort(key=lambda item: item['_kickoff'])
    return rows


def _team_view(row, team):
    if row['home'] == team:
        return {'venue': 'home', 'opponent': row['away'], 'for': row['home_score'], 'against': row['away_score']}
    return {'venue': 'away', 'opponent': row['home'], 'for': row['away_score'], 'against': row['home_score']}


def _summarize(games):
    played = len(games)
    if not played:
        return {
            'played': 0, 'wins': 0, 'draws': 0, 'losses': 0, 'points': 0,
            'goals_for': 0, 'goals_against': 0, 'average_for': None, 'average_against': None,
            'clean_sheet_rate': None, 'btts_rate': None,
            'over_1_5': None, 'over_2_5': None, 'over_3_5': None, 'form': '',
        }
    wins = sum(1 for game in games if game['for'] > game['against'])
    draws = sum(1 for game in games if game['for'] == game['against'])
    losses = played - wins - draws
    goals_for = sum(game['for'] for game in games)
    goals_against = sum(game['against'] for game in games)
    clean = sum(1 for game in games if game['against'] == 0)
    btts = sum(1 for game in games if game['for'] > 0 and game['against'] > 0)

    def over(line):
        return sum(1 for game in games if game['for'] + game['against'] > line) / played

    return {
        'played': played,
        'wins': wins, 'draws': draws, 'losses': losses, 'points': wins * 3 + draws,
        'goals_for': goals_for, 'goals_against': goals_against,
        'average_for': goals_for / played, 'average_against': goals_against / played,
        'clean_sheet_rate': clean / played, 'btts_rate': btts / played,
        'over_1_5': over(1.5), 'over_2_5': over(2.5), 'over_3_5': over(3.5),
        'form': ''.join(
            'W' if game['for'] > game['against'] else 'D' if game['for'] == game['against'] else 'L'
            for game in games
        ),
    }


def _window(games, size):
    chosen = games[-size:]
    summary = _summarize(chosen)
    summary.update({'requested': size, 'available': len(chosen), 'complete': len(games) >= size})
    return summary


def _profile(rows, team):
    games = []
    for row in rows:
        view = _team_view(row, team)
        games.append({
            **view,
            'competition': row['competition'],
            'kickoff_at': row['starts_at'],
            'source': row['result_source'] or row['source'],
            'settled_at': row['settled_at'],
        })
    home = [game for game in games if game['venue'] == 'home']
    away = [game for game in games if game['venue'] == 'away']
    recent = _window(games, 5)
    previous = _window(games[:-5], 5) if len(games) > 5 else None
    trend = None
    if recent['played'] and previous and previous['played']:
        recent_ppg = recent['points'] / recent['played']
        previous_ppg = previous['points'] / previous['played']
        if recent_ppg > previous_ppg + 0.25:
            trend = 'recent points per match are higher than the preceding sample'
        elif recent_ppg < previous_ppg - 0.25:
            trend = 'recent points per match are lower than the preceding sample'
        else:
            trend = 'recent points per match are close to the preceding sample'
    return {
        'team': team,
        'played': len(games),
        'home_played': len(home),
        'away_played': len(away),
        'overall': _summarize(games),
        'home': _summarize(home),
        'away': _summarize(away),
        'last_5': _window(games, 5),
        'last_10': _window(games, 10),
        'last_15': _window(games, 15),
        'trend': trend,
        'matches': games[-15:],
    }


def _same_competition_counts(connection, fixture, cutoff):
    names = competitions_for_league(connection, fixture['sport'], fixture['competition'])
    marks = ','.join('?' * len(names))
    clubs = [dict(row) for row in connection.execute(
        f"""SELECT f.home, f.away FROM sports_ai_fixtures f
            JOIN sports_ai_results r ON r.fixture_id=f.id
            WHERE f.sport=? AND f.competition IN ({marks}) AND r.status='final'""",
        (fixture['sport'], *names),
    )]
    home_name = _aligned_team(fixture['home'], clubs)
    away_name = _aligned_team(fixture['away'], clubs)
    def count(team, venue):
        column = 'f.home' if venue == 'home' else 'f.away'
        return connection.execute(
            f"""SELECT COUNT(*) FROM sports_ai_fixtures f
                JOIN sports_ai_results r ON r.fixture_id=f.id
                WHERE f.sport=? AND f.competition IN ({marks}) AND {column}=?
                  AND r.status='final' AND f.starts_at<? AND r.settled_at<?""",
            (fixture['sport'], *names, team, cutoff.isoformat(), cutoff.isoformat()),
        ).fetchone()[0]
    return {
        'home_team_matches': count(home_name, 'home') + count(home_name, 'away'),
        'away_team_matches': count(away_name, 'home') + count(away_name, 'away'),
        'home_venue_matches': count(home_name, 'home'),
        'away_venue_matches': count(away_name, 'away'),
        'minimum_venue_matches': MIN_TEAM_GAMES,
    }


def _backfill_openligadb(connection, fixture):
    provider_row = connection.execute(
        """SELECT * FROM sports_ai_provider_registry
           WHERE provider_key='openligadb' AND enabled=1 AND terms_reviewed=1""",
    ).fetchone()
    if not provider_row or fixture.get('source') != 'openligadb':
        return {'attempted': False, 'saved': 0, 'detail': 'Fixture is not linked to the enabled OpenLigaDB provider.'}
    provider = dict(provider_row)
    config = json.loads(provider['config_json'])
    league = str(config.get('league') or '')
    try:
        season = int(config.get('season'))
    except (TypeError, ValueError):
        return {'attempted': False, 'saved': 0, 'detail': 'OpenLigaDB season is not configured.'}
    saved = 0
    collected = []
    for year in (season - 1, season - 2):
        existing = connection.execute(
            """SELECT COUNT(*) FROM sports_ai_fixtures
               WHERE source='openligadb' AND competition LIKE '%Bundesliga%' AND starts_at LIKE ?""",
            (f'{year}-%',),
        ).fetchone()[0]
        if existing >= 50:
            collected.append({'season': year, 'saved': 0, 'detail': f'{existing} matches already stored'})
            continue
        batch = _openligadb(provider, {'league': league, 'season': year})
        seen, count = _persist(connection, provider, batch)
        saved += count
        collected.append({'season': year, 'seen': seen, 'saved': count, 'source_url': batch['source_url']})
    return {'attempted': True, 'saved': saved, 'seasons': collected, 'league': league, 'configured_season': season}


def _standings(connection, fixture):
    provider_row = connection.execute(
        """SELECT * FROM sports_ai_provider_registry
           WHERE provider_key='openligadb' AND enabled=1 AND terms_reviewed=1""",
    ).fetchone()
    if not provider_row or 'bundesliga' not in fixture['competition'].casefold():
        return []
    provider = dict(provider_row)
    config = json.loads(provider['config_json'])
    league = str(config.get('league') or '')
    season = str(config.get('season') or '')
    if not league or not season:
        return []
    fresh = (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat()
    cached = connection.execute(
        """SELECT COUNT(*) FROM sports_ai_standings_snapshots
           WHERE provider_id=? AND competition=? AND season=? AND observed_at>=?""",
        (provider['id'], fixture['competition'], season, fresh),
    ).fetchone()[0]
    source_url = f'https://api.openligadb.de/getbltable/{league}/{season}'
    if not cached:
        payload, _headers = _json_body(source_url)
        if not isinstance(payload, list):
            raise ValueError('OpenLigaDB table response was not a list')
        observed = datetime.now(timezone.utc).isoformat()
        for index, row in enumerate(payload, start=1):
            team = row.get('teamName')
            if not team:
                continue
            connection.execute(
                """INSERT INTO sports_ai_standings_snapshots
                   (id,provider_id,competition,season,team,position,played,won,drawn,lost,
                    points,goal_difference,details_json,source_url,observed_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (str(uuid.uuid4()), provider['id'], fixture['competition'], season, team, index,
                 row.get('matches'), row.get('won'), row.get('draw'), row.get('lost'),
                 row.get('points'), row.get('goalDiff'), json.dumps({
                     'team_id': row.get('teamId'), 'goals_for': row.get('goals'),
                     'goals_against': row.get('opponentGoals'), 'short_name': row.get('shortName'),
                 }), source_url, observed),
            )
    latest = connection.execute(
        """SELECT MAX(observed_at) FROM sports_ai_standings_snapshots
           WHERE provider_id=? AND competition=? AND season=?""",
        (provider['id'], fixture['competition'], season),
    ).fetchone()[0]
    if not latest:
        return []
    table = []
    for row in connection.execute(
        """SELECT team,position,played,won,drawn,lost,points,goal_difference,details_json,source_url,observed_at
           FROM sports_ai_standings_snapshots
           WHERE provider_id=? AND competition=? AND season=? AND observed_at=?
           ORDER BY position""",
        (provider['id'], fixture['competition'], season, latest),
    ):
        item = dict(row)
        item['details'] = json.loads(item.pop('details_json') or '{}')
        table.append(item)
    return table


def _forecast_level(model, sport='football'):
    method = 'opponent-adjusted Poisson estimate' if sport == 'football' else 'opponent-adjusted scoring estimate'
    if not model:
        return 'descriptive', f'No {sport} model is registered, so only stored match history is shown.'
    if model['active'] and model['status'] == 'active':
        return (
            'secondary',
            'The active registry model is not recorded as trained on this competition alone. '
            f'Probabilities are the {method} from this competition\'s stored results.',
        )
    return (
        'secondary',
        f"Registry model {model['model_name']} v{model['version']} is {model['status']} and not activated. "
        f'Probabilities are the {method} from this competition\'s completed matches before kickoff. '
        'They are not presented as a promoted or league-calibrated production forecast.',
    )


def _basketball_forecast(level, note, features, home_probability, away_probability, outcome_sum, counts, model):
    return {
        'level': level,
        'note': note,
        'expected_home_points': features.get('expected_home_points'),
        'expected_away_points': features.get('expected_away_points'),
        'home_score_sd': features.get('home_score_sd'),
        'away_score_sd': features.get('away_score_sd'),
        'total_sd': features.get('total_sd'),
        'outcome_probability_sum': outcome_sum,
        'moneyline_home': home_probability,
        'moneyline_away': away_probability,
        'scorelines': [],
        'matrix': [],
        'sample': {
            'home_games': features.get('home_games'),
            'away_games': features.get('away_games'),
            'historical_games': features.get('historical_games'),
            'venue_counts': counts,
        },
        'model': {
            'name': model['model_name'] if model else None,
            'version': model['version'] if model else None,
            'status': model['status'] if model else None,
            'active': bool(model['active']) if model else False,
        },
    }


def analyze_fixture(connection, fixture_id, actor, audit):
    fixture_row = connection.execute(
        'SELECT * FROM sports_ai_fixtures WHERE id=?', (fixture_id,),
    ).fetchone()
    if not fixture_row:
        raise LookupError(fixture_id)
    fixture = dict(fixture_row)
    running = connection.execute(
        """SELECT id FROM sports_ai_prediction_jobs
           WHERE status='running' AND trigger='match-analysis'
             AND json_extract(details_json,'$.fixture_id')=?""",
        (fixture_id,),
    ).fetchone()
    if running:
        raise AnalysisBusy(running['id'])
    now = datetime.now(timezone.utc)
    job_id = str(uuid.uuid4())
    connection.execute(
        """INSERT INTO sports_ai_prediction_jobs
           (id,idempotency_key,trigger,status,attempt,started_at,timeout_seconds,details_json)
           VALUES(?,?,'match-analysis','running',1,?,180,?)""",
        (job_id, f'match-{fixture_id}-{job_id}', now.isoformat(), json.dumps({'fixture_id': fixture_id})),
    )
    stages = []

    def stage(name, status, detail):
        stages.append({'name': name, 'status': status, 'detail': detail})
        log.info('%s %s %s', name, status, detail)

    try:
        cutoff = parse_time(fixture['starts_at'])
        sport = fixture['sport']
        if sport == 'football' and fixture.get('source') == 'openligadb':
            try:
                backfill = _backfill_openligadb(connection, fixture)
                stage('collection', 'complete' if backfill.get('attempted') else 'skipped', json.dumps(backfill))
            except Exception as error:
                stage('collection', 'failed', str(error)[:300])
        home_rows = _rows_before(connection, fixture['home'], fixture['competition'], cutoff, sport) if sport in ('football', 'basketball') else []
        away_rows = _rows_before(connection, fixture['away'], fixture['competition'], cutoff, sport) if sport in ('football', 'basketball') else []
        if sport == 'basketball':
            stage(
                'collection',
                'complete' if home_rows and away_rows else 'insufficient',
                f"Stored finals before kickoff: {fixture['home']} {len(home_rows)}, {fixture['away']} {len(away_rows)}. The authorized feed is the SportsDataverse schedule; it has no separate statistics file.",
            )
        elif sport != 'football' or fixture.get('source') != 'openligadb':
            stage('collection', 'skipped', 'No additional approved historical feed applies to this fixture source.')
        try:
            table = _standings(connection, fixture) if sport == 'football' else []
            if sport == 'basketball':
                stage('standings', 'skipped', 'The authorized basketball schedule file does not publish a league table.')
            else:
                stage('standings', 'complete' if table else 'skipped', f'{len(table)} table rows stored' if table else 'No league table was returned by an enabled provider.')
        except Exception as error:
            table = []
            stage('standings', 'failed', str(error)[:300])
        counts = _same_competition_counts(connection, fixture, cutoff) if sport == 'football' else {
            'home_team_matches': len(home_rows),
            'away_team_matches': len(away_rows),
            'minimum_team_matches': MIN_TEAM_GAMES,
        }
        missing = []
        if sport == 'football':
            home_league = sum(1 for row in home_rows if row['home'] == fixture['home'])
            away_league = sum(1 for row in away_rows if row['away'] == fixture['away'])
            if counts['home_venue_matches'] < MIN_TEAM_GAMES and home_league < MIN_TEAM_GAMES:
                missing.append(
                    f"{fixture['home']} has {counts['home_venue_matches']} completed home matches in this competition and {home_league} stored league home matches before kickoff; {MIN_TEAM_GAMES} are required for the Poisson rates."
                )
            elif counts['home_venue_matches'] < MIN_TEAM_GAMES:
                counts['home_sample'] = f'{home_league} stored league home matches'
            if counts['away_venue_matches'] < MIN_TEAM_GAMES and away_league < MIN_TEAM_GAMES:
                missing.append(
                    f"{fixture['away']} has {counts['away_venue_matches']} completed away matches in this competition and {away_league} stored league away matches before kickoff; {MIN_TEAM_GAMES} are required for the Poisson rates."
                )
            elif counts['away_venue_matches'] < MIN_TEAM_GAMES:
                counts['away_sample'] = f'{away_league} stored league away matches'
        elif sport == 'basketball':
            if len(home_rows) < MIN_TEAM_GAMES:
                missing.append(f"{fixture['home']} has {len(home_rows)} completed matches before kickoff; {MIN_TEAM_GAMES} are required for the scoring model.")
            if len(away_rows) < MIN_TEAM_GAMES:
                missing.append(f"{fixture['away']} has {len(away_rows)} completed matches before kickoff; {MIN_TEAM_GAMES} are required for the scoring model.")
        ready = 'Venue sample sizes meet the Poisson minimum.' if sport == 'football' else 'Stored pre-cutoff samples were checked for the scoring model.'
        stage('validation', 'complete' if not missing else 'insufficient', ' '.join(missing) or ready)
        home_profile = _profile(home_rows, fixture['home']) if home_rows else None
        away_profile = _profile(away_rows, fixture['away']) if away_rows else None
        if home_profile and not home_profile['last_15']['complete']:
            missing.append(f"{fixture['home']} has {home_profile['played']} completed league matches before kickoff, fewer than the requested 15.")
        if away_profile and not away_profile['last_15']['complete']:
            missing.append(f"{fixture['away']} has {away_profile['played']} completed league matches before kickoff, fewer than the requested 15.")
        h2h = [
            row for row in home_rows
            if {row['home'], row['away']} == {fixture['home'], fixture['away']}
        ]
        if sport in ('football', 'basketball') and not h2h:
            missing.append('No completed head-to-head match between these teams is stored before kickoff.')
        if sport == 'basketball':
            missing.append('Pace, efficiency, injuries and lineups are not in the stored schedule file.')
        else:
            missing.append('Expected goals, injuries and lineups are not in the stored provider payloads.')
        stage('features', 'complete', 'Form and venue splits were calculated from stored final scores. Unavailable source fields were left blank.')
        try:
            analysis = run_analysis(connection, actor, audit, fixture_id=fixture_id)
        except Exception as error:
            analysis = {'error': str(error)[:300]}
            stage('inference', 'failed', analysis['error'])
        predictions = _latest_predictions(connection, fixture_id)
        model = connection.execute(
            """SELECT * FROM sports_ai_model_registry
               WHERE sport=? ORDER BY active DESC, created_at DESC LIMIT 1""",
            (fixture['sport'],),
        ).fetchone()
        model = dict(model) if model else None
        level, level_note = _forecast_level(model, fixture['sport'])
        forecast = None
        if predictions.get('HOME') and predictions.get('DRAW') and predictions.get('AWAY'):
            home_explanation = predictions['HOME']['explanation']
            features = home_explanation.get('features') or {}
            parameters = json.loads(model['parameters_json']) if model else {}
            rho = float(parameters.get('dixon_coles_rho') or 0.0)
            if not rho:
                missing.append('Dixon-Coles rho is not stored on the registry model, so the correction was not applied.')
            grid = _football_score_grid(features, rho) if fixture['sport'] == 'football' and features.get('expected_home_goals') is not None else []
            matrix = [
                {'home_goals': home, 'away_goals': away, 'probability': probability}
                for home, away, probability in grid if home <= 5 and away <= 5
            ]
            outcome_sum = predictions['HOME']['probability'] + predictions['DRAW']['probability'] + predictions['AWAY']['probability']
            if abs(outcome_sum - 1) > 0.02:
                missing.append(f'Home, draw and away probabilities summed to {outcome_sum:.4f}, so the forecast was withheld.')
                level, level_note = 'descriptive', 'The outcome probabilities were not mathematically consistent.'
            else:
                forecast = {
                    'level': level,
                    'note': level_note,
                    'expected_home_goals': features.get('expected_home_goals'),
                    'expected_away_goals': features.get('expected_away_goals'),
                    'home_attack': features.get('home_attack'),
                    'home_defence': features.get('home_defence'),
                    'away_attack': features.get('away_attack'),
                    'away_defence': features.get('away_defence'),
                    'league_home_goals': features.get('league_home_goals'),
                    'league_away_goals': features.get('league_away_goals'),
                    'dixon_coles_rho': rho,
                    'outcome_probability_sum': outcome_sum,
                    'scorelines': home_explanation.get('scorelines') or [],
                    'matrix': matrix,
                    'sample': {
                        'home_games': features.get('home_games'),
                        'away_games': features.get('away_games'),
                        'historical_games': features.get('historical_games'),
                        'venue_counts': counts,
                    },
                    'model': {
                        'name': model['model_name'] if model else None,
                        'version': model['version'] if model else None,
                        'status': model['status'] if model else None,
                        'active': bool(model['active']) if model else False,
                    },
                }
        elif predictions.get('MONEYLINE_HOME') and predictions.get('MONEYLINE_AWAY'):
            home_explanation = predictions['MONEYLINE_HOME']['explanation']
            features = home_explanation.get('features') or {}
            home_probability = predictions['MONEYLINE_HOME']['probability']
            away_probability = predictions['MONEYLINE_AWAY']['probability']
            outcome_sum = home_probability + away_probability
            if abs(outcome_sum - 1) > 0.02:
                missing.append(f'Home and away moneylines summed to {outcome_sum:.4f}, so the forecast was withheld.')
                level, level_note = 'descriptive', 'The moneyline probabilities were not mathematically consistent.'
            elif home_probability < 0.02 or home_probability > 0.98:
                missing.append(
                    f"The baseline moneyline is {home_probability:.1%} because the expected margin is large relative to the stored scoring variance. It was not capped."
                )
                forecast = _basketball_forecast(level, level_note, features, home_probability, away_probability, outcome_sum, counts, model)
            else:
                forecast = _basketball_forecast(level, level_note, features, home_probability, away_probability, outcome_sum, counts, model)
        else:
            level, level_note = 'descriptive', ' '.join(missing) or 'Stored history does not meet the forecast minimum.'
        if not any(item['name'] == 'inference' for item in stages):
            if forecast is None:
                stage('inference', 'skipped', level_note)
                status = 'descriptive'
            elif fixture['sport'] == 'basketball':
                stage('inference', 'complete', f"Scoring model stored. Moneyline sum {forecast['outcome_probability_sum']:.4f}.")
                status = 'complete'
            else:
                stage('inference', 'complete', f"Poisson forecast stored. Home/draw/away sum {forecast['outcome_probability_sum']:.4f}.")
                status = 'complete'
        else:
            status = 'descriptive'
        source = connection.execute(
            """SELECT m.source_url,p.display_name,m.observed_at
               FROM sports_ai_fixture_source_map m
               JOIN sports_ai_provider_registry p ON p.id=m.provider_id
               WHERE m.fixture_id=? ORDER BY m.observed_at DESC LIMIT 1""",
            (fixture_id,),
        ).fetchone()
        markets = []
        for name in (
            'HOME', 'DRAW', 'AWAY', 'BTTS_YES', 'BTTS_NO', 'OVER_2.5', 'UNDER_2.5',
            'OVER_1.5', 'UNDER_1.5', 'OVER_3.5', 'UNDER_3.5', 'DNB_HOME', 'DNB_AWAY',
            'MONEYLINE_HOME', 'MONEYLINE_AWAY', 'OVER_219.5', 'UNDER_219.5',
            'HOME_SPREAD_-5.5', 'AWAY_SPREAD_5.5',
        ):
            row = predictions.get(name)
            if not row:
                continue
            markets.append({
                'market': name,
                'probability': row['probability'],
                'decimal_odds': None,
                'implied_probability': None,
                'expected_edge': None,
                'odds_status': 'Odds Unavailable',
            })
        if not markets:
            missing.append('No bookmaker odds are stored, and no model probability was produced for the supported markets.')
        else:
            missing.append('No permitted bookmaker quote is stored, so implied probability and value edge are unavailable.')
        report = {
            'fixture_id': fixture_id,
            'home': fixture['home'],
            'away': fixture['away'],
            'competition': fixture['competition'],
            'kickoff_at': fixture['starts_at'],
            'fixture_status': fixture['status'],
            'provider': source['display_name'] if source else fixture.get('source'),
            'source_url': source['source_url'] if source else None,
            'source_observed_at': source['observed_at'] if source else fixture.get('observed_at'),
            'stages': stages,
            'counts': counts,
            'home_profile': home_profile,
            'away_profile': away_profile,
            'head_to_head': [{
                'kickoff_at': row['starts_at'], 'home': row['home'], 'away': row['away'],
                'home_score': row['home_score'], 'away_score': row['away_score'],
                'source': row['result_source'] or row['source'], 'competition': row['competition'],
            } for row in h2h],
            'standings': [
                row for row in table if row['team'] in (fixture['home'], fixture['away'])
            ],
            'forecast': forecast,
            'forecast_level': forecast['level'] if forecast else 'descriptive',
            'forecast_note': forecast['note'] if forecast else level_note,
            'markets': markets,
            'missing': missing,
            'analysis_job': {'id': analysis.get('job_id') if isinstance(analysis, dict) else None, 'stats': analysis},
            'generated_at': datetime.now(timezone.utc).isoformat(),
        }
        connection.execute(
            """INSERT INTO sports_ai_match_analyses(id,fixture_id,status,report_json)
               VALUES(?,?,?,?)""",
            (str(uuid.uuid4()), fixture_id, status, json.dumps(report)),
        )
        audit(connection, actor, 'match_analysis.completed', {
            'fixture_id': fixture_id, 'status': status, 'job_id': job_id,
        })
        connection.execute(
            """UPDATE sports_ai_prediction_jobs
               SET status='complete',finished_at=?,details_json=? WHERE id=?""",
            (datetime.now(timezone.utc).isoformat(), json.dumps({'fixture_id': fixture_id, 'status': status}), job_id),
        )
        return attach_live_evidence(connection, report)
    except Exception as error:
        connection.execute(
            """UPDATE sports_ai_prediction_jobs
               SET status='failed',finished_at=?,error=? WHERE id=?""",
            (datetime.now(timezone.utc).isoformat(), str(error)[:500], job_id),
        )
        log.exception('match analysis failed for %s', fixture_id)
        raise


def _latest_predictions(connection, fixture_id):
    run = connection.execute(
        """SELECT run_id FROM sports_ai_engine_predictions
           WHERE fixture_id=? ORDER BY created_at DESC LIMIT 1""",
        (fixture_id,),
    ).fetchone()
    if not run:
        return {}
    rows = {}
    for raw in connection.execute(
        """SELECT market,probability,explanation_json,created_at
           FROM sports_ai_engine_predictions
           WHERE fixture_id=? AND run_id=?""",
        (fixture_id, run['run_id']),
    ):
        item = dict(raw)
        item['explanation'] = json.loads(item.pop('explanation_json') or '{}')
        rows[item['market']] = item
    return rows


_QUOTE_KEYS = {
    'HOME': ('h2h', 'home', None),
    'DRAW': ('h2h', 'draw', None),
    'AWAY': ('h2h', 'away', None),
    'MONEYLINE_HOME': ('h2h', 'home', None),
    'MONEYLINE_AWAY': ('h2h', 'away', None),
    'BTTS_YES': ('btts', 'yes', None),
    'BTTS_NO': ('btts', 'no', None),
    'OVER_1.5': ('totals', 'over', 1.5),
    'UNDER_1.5': ('totals', 'under', 1.5),
    'OVER_2.5': ('totals', 'over', 2.5),
    'UNDER_2.5': ('totals', 'under', 2.5),
    'OVER_3.5': ('totals', 'over', 3.5),
    'UNDER_3.5': ('totals', 'under', 3.5),
}


def attach_live_evidence(connection, report):
    """Overlay stored quotes and the value engine's latest decision. Never invents a price or an edge."""
    if not isinstance(report, dict) or not report.get('fixture_id'):
        return report
    fixture_id = report['fixture_id']
    for market in report.get('markets') or []:
        key = _QUOTE_KEYS.get(market['market'])
        quote = None
        if key:
            book, outcome, line = key
            quote = connection.execute(
                """SELECT decimal_odds, bookmaker, observed_at, source_url
                   FROM sports_ai_odds_snapshots
                   WHERE fixture_id=? AND market=? AND outcome=? AND IFNULL(line,-999)=IFNULL(?, -999)
                   ORDER BY observed_at DESC LIMIT 1""",
                (fixture_id, book, outcome, line),
            ).fetchone()
        if quote:
            market['decimal_odds'] = quote['decimal_odds']
            market['bookmaker'] = quote['bookmaker']
            market['odds_observed_at'] = quote['observed_at']
            market['odds_source_url'] = quote['source_url']
            market['implied_probability'] = 1 / float(quote['decimal_odds'])
            market['odds_status'] = quote['bookmaker']
        opportunity = connection.execute(
            """SELECT o.expected_edge, o.implied_probability, o.status, o.reason, o.uncertainty, o.data_completeness
               FROM sports_ai_value_opportunities o
               JOIN sports_ai_engine_predictions p ON p.id=o.prediction_id
               WHERE p.fixture_id=? AND p.market=?
               ORDER BY o.evaluated_at DESC LIMIT 1""",
            (fixture_id, market['market']),
        ).fetchone()
        if opportunity:
            market['expected_edge'] = opportunity['expected_edge']
            market['value_status'] = opportunity['status']
            market['value_reason'] = opportunity['reason']
            market['uncertainty'] = opportunity['uncertainty']
            market['data_completeness'] = opportunity['data_completeness']
            if market.get('implied_probability') is None:
                market['implied_probability'] = opportunity['implied_probability']
    registry = connection.execute(
        """SELECT model_name, version, status, active, metrics_json
           FROM sports_ai_model_registry
           WHERE sport=(SELECT sport FROM sports_ai_fixtures WHERE id=?)
           ORDER BY active DESC, created_at DESC LIMIT 1""",
        (fixture_id,),
    ).fetchone()
    if registry:
        try:
            metrics = json.loads(registry['metrics_json'] or '{}')
        except json.JSONDecodeError:
            metrics = {}
        report['registry_model'] = {
            'name': registry['model_name'],
            'version': registry['version'],
            'status': registry['status'],
            'active': bool(registry['active']),
            'brier_score': metrics.get('brier_score'),
            'log_loss': metrics.get('log_loss'),
            'calibration_error': metrics.get('calibration_error'),
            'holdout_fixtures': metrics.get('holdout_fixtures'),
        }
    return report


def latest_report(connection, fixture_id):
    row = connection.execute(
        """SELECT report_json,status,created_at FROM sports_ai_match_analyses
           WHERE fixture_id=? ORDER BY created_at DESC LIMIT 1""",
        (fixture_id,),
    ).fetchone()
    if not row:
        return None
    report = json.loads(row['report_json'])
    report['stored_status'] = row['status']
    report['stored_at'] = row['created_at']
    return attach_live_evidence(connection, report)


def analyse_upcoming_batch(connection, actor, audit, limit=8):
    """Analyse a bounded set of upcoming fixtures that are stale or unanalysed."""
    limit = max(1, min(int(limit), 40))
    rows = connection.execute(
        f"""SELECT f.id, f.home, f.away FROM sports_ai_fixtures f
           WHERE {upcoming_fixture_sql('f')}
             AND (
               NOT EXISTS (
                 SELECT 1 FROM sports_ai_match_analyses a WHERE a.fixture_id=f.id
               )
               OR EXISTS (
                 SELECT 1 FROM sports_ai_results r
                 JOIN sports_ai_fixtures other ON other.id=r.fixture_id
                 WHERE r.settled_at>(
                   SELECT MAX(a.created_at) FROM sports_ai_match_analyses a WHERE a.fixture_id=f.id
                 )
                   AND other.sport=f.sport AND other.id<>f.id
                   AND (other.home IN (f.home, f.away) OR other.away IN (f.home, f.away))
               )
             )
           ORDER BY
             CASE WHEN EXISTS (
               SELECT 1 FROM sports_ai_match_analyses a WHERE a.fixture_id=f.id
             ) THEN 1 ELSE 0 END,
             {upcoming_order_sql('f')}
           LIMIT ?""",
        (limit,),
    ).fetchall()
    results = []
    for row in rows:
        try:
            report = analyze_fixture(connection, row['id'], actor, audit)
            results.append({
                'fixture_id': row['id'],
                'fixture': f"{row['home']} vs {row['away']}",
                'status': report.get('forecast_level') or report.get('stored_status'),
            })
        except AnalysisBusy:
            results.append({'fixture_id': row['id'], 'status': 'busy'})
        except Exception as error:
            results.append({'fixture_id': row['id'], 'status': 'failed', 'error': str(error)[:200]})
    log.info('autonomous batch attempted %s completed %s', len(rows), len(results))
    return {'status': 'complete', 'attempted': len(rows), 'results': results}
