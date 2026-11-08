"""Sport-specific, chronological Elo match-winner features.

Ratings are rebuilt from that sport and competition's observed final outcomes.
No score, point total, spread or price is inferred by this model.
"""

import hashlib
import json
import math
import re
import unicodedata


def build_winner_features(connection, fixture):
    from sport_support import PIPELINE_VERSION
    from prediction_engine import competitions_for_league, parse_time
    cutoff = parse_time(fixture['starts_at'])
    individual = fixture['sport'] in ('tennis', 'table_tennis', 'badminton', 'boxing', 'mma', 'darts', 'snooker', 'etennis')
    names = competitions_for_league(connection, fixture['sport'], fixture['competition'])
    marks = ','.join('?' for _ in names)
    rows = connection.execute(
        f"""SELECT f.id,f.home,f.away,f.starts_at,f.statistics_json,
                   r.home_score,r.away_score,r.settled_at,r.source
            FROM sports_ai_fixtures f JOIN sports_ai_results r ON r.fixture_id=f.id
            WHERE f.sport=? AND {'1=1' if individual else f'f.competition IN ({marks})'} AND r.status='final'
              AND julianday(f.starts_at)<? AND julianday(r.settled_at)<?
            ORDER BY julianday(f.starts_at),f.id""",
        (fixture['sport'], *([] if individual else names), cutoff.timestamp() / 86400 + 2440587.5,
         cutoff.timestamp() / 86400 + 2440587.5),
    ).fetchall()
    ratings, counts, last = {}, {}, {}
    ids, sources, observations = [], set(), []
    def team(name):
        # Initials and numbers distinguish players and doubles partnerships.
        return re.sub(r'[^\w]+', ' ', unicodedata.normalize('NFKC', str(name)).casefold()).strip()
    draws = 0
    for raw in rows:
        row = dict(raw)
        home, away = team(row['home']), team(row['away'])
        if home == away:
            continue
        # Cricket innings totals do not establish the winner. Only an explicit
        # sourced match outcome may train this sport.
        if fixture['sport'] == 'cricket':
            stats = json.loads(row['statistics_json'] or '{}')
            winner = stats.get('match_winner')
            if winner not in ('home', 'away', 'draw'):
                continue
            actual = {'home': 1.0, 'away': 0.0, 'draw': 0.5}[winner]
        else:
            actual = 1.0 if row['home_score'] > row['away_score'] else 0.0 if row['home_score'] < row['away_score'] else 0.5
        rh, ra = ratings.get(home, 1500.0), ratings.get(away, 1500.0)
        expected = 1 / (1 + 10 ** ((ra - rh) / 400))
        adjustment = 24 * (actual - expected)
        ratings[home], ratings[away] = rh + adjustment, ra - adjustment
        for side in (home, away):
            counts[side] = counts.get(side, 0) + 1
            last[side] = parse_time(row['starts_at'])
        draws += actual == 0.5
        ids.append(row['id'])
        sources.add(row['source'])
        observations.append((row['id'], actual, row['settled_at']))
    def resolve(name):
        key = team(name)
        if key in counts:
            return key
        # Full participant tokens may appear surname-first in result sources.
        matches = [known for known in counts if sorted(known.split()) == sorted(key.split())]
        return matches[0] if len(matches) == 1 else key
    home, away = resolve(fixture['home']), resolve(fixture['away'])
    if counts.get(home, 0) < 3 or counts.get(away, 0) < 3:
        return None
    probability = 1 / (1 + 10 ** ((ratings[away] - ratings[home]) / 400))
    features = {
        'pipeline_version': PIPELINE_VERSION,
        'home_rating': ratings[home], 'away_rating': ratings[away],
        'winner_probability': probability, 'draw_rate': draws / len(ids),
        'home_games': counts[home], 'away_games': counts[away],
        'historical_games': len(ids),
        'home_rest_days': (cutoff - last[home]).total_seconds() / 86400,
        'away_rest_days': (cutoff - last[away]).total_seconds() / 86400,
        'history_basis': 'Chronological match outcomes in this sport and competition; no cross-sport ratings.',
    }
    return {
        'features': features, 'source_refs': sorted(sources), 'fixture_ids': ids,
        'cutoff_at': cutoff.isoformat(), 'completeness': 1.0, 'missing': [],
        'fingerprint': hashlib.sha256((PIPELINE_VERSION + json.dumps(observations)).encode()).hexdigest(),
    }


def winner_market(market, features, temperature=1.0):
    if market not in ('MONEYLINE_HOME', 'MONEYLINE_AWAY') or features.get('winner_probability') is None:
        return None
    p = min(1 - 1e-9, max(1e-9, features['winner_probability']))
    logit = math.log(p / (1 - p)) / max(0.01, temperature)
    p = 1 / (1 + math.exp(-max(-700, min(700, logit))))
    if market == 'MONEYLINE_AWAY':
        p = 1 - p
    push = features.get('draw_rate', 0.0)
    return {'p_win': p * (1 - push), 'p_loss': (1 - p) * (1 - push),
            'p_push': push, 'p_half_win': 0.0, 'p_half_loss': 0.0}
