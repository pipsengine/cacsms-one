"""Source-backed, cutoff-safe football and basketball modeling."""

import itertools
import json
import math
import re
import uuid
from datetime import datetime, timezone

MODEL_NAMES = {'football': 'sports-ai-football-poisson', 'basketball': 'sports-ai-basketball-normal'}
VERSION = '1'
MIN_TEAM_GAMES = 3
MIN_VALIDATION_FIXTURES = 30
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


def _weighted_average(records, field, cutoff, default=None):
    values = [
        (float(row[field]), math.exp(-max(0, (cutoff - row['_kickoff']).total_seconds() / 86400) / 180))
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


def build_features(connection, fixture):
    """Historical rows with kickoff and settlement strictly before the fixture cutoff."""
    cutoff = parse_time(fixture['starts_at'])
    rows = connection.execute(
        """SELECT f.id,f.competition,f.home,f.away,f.starts_at,r.home_score,
                  r.away_score,r.source,r.settled_at
           FROM sports_ai_fixtures f
           JOIN sports_ai_results r ON r.fixture_id=f.id
           WHERE f.sport=? AND f.competition=? AND r.status='final'
           ORDER BY f.starts_at""",
        (fixture['sport'], fixture['competition']),
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
            history.append(row)

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

    home_all, away_all = team_games(fixture['home']), team_games(fixture['away'])
    home_games = [row for row in home_all if row['venue'] == 'home']
    away_games = [row for row in away_all if row['venue'] == 'away']
    if fixture['sport'] == 'football':
        if len(home_games) < MIN_TEAM_GAMES or len(away_games) < MIN_TEAM_GAMES:
            return None
        league_home = _weighted_average(
            [{'home_score': row['home_score'], '_kickoff': row['_kickoff']} for row in history],
            'home_score', cutoff,
        )
        league_away = _weighted_average(
            [{'away_score': row['away_score'], '_kickoff': row['_kickoff']} for row in history],
            'away_score', cutoff,
        )
        if not league_home or not league_away:
            return None
        home_attack = _weighted_average(home_games, 'for', cutoff) / league_home
        home_defence = _weighted_average(home_games, 'against', cutoff) / league_away
        away_attack = _weighted_average(away_games, 'for', cutoff) / league_away
        away_defence = _weighted_average(away_games, 'against', cutoff) / league_home
        features = {
            'home_attack': home_attack, 'home_defence': home_defence,
            'away_attack': away_attack, 'away_defence': away_defence,
            'league_home_goals': league_home, 'league_away_goals': league_away,
            'expected_home_goals': league_home * home_attack * away_defence,
            'expected_away_goals': league_away * away_attack * home_defence,
        }
    elif fixture['sport'] == 'basketball':
        if len(home_all) < MIN_TEAM_GAMES or len(away_all) < MIN_TEAM_GAMES:
            return None
        league_home = _weighted_average(
            [{'home_score': row['home_score'], '_kickoff': row['_kickoff']} for row in history],
            'home_score', cutoff,
        )
        league_away = _weighted_average(
            [{'away_score': row['away_score'], '_kickoff': row['_kickoff']} for row in history],
            'away_score', cutoff,
        )
        if not league_home or not league_away:
            return None
        home_for = _weighted_average(home_games or home_all, 'for', cutoff)
        home_against = _weighted_average(home_games or home_all, 'against', cutoff)
        away_for = _weighted_average(away_games or away_all, 'for', cutoff)
        away_against = _weighted_average(away_games or away_all, 'against', cutoff)
        features = {
            'home_points_for': home_for, 'home_points_against': home_against,
            'away_points_for': away_for, 'away_points_against': away_against,
            'home_margin': home_for - home_against,
            'away_margin': away_for - away_against,
            'expected_home_points': league_home * home_for / league_home * away_against / league_home,
            'expected_away_points': league_away * away_for / league_away * home_against / league_away,
            'home_score_sd': _sample_sd([row['for'] for row in home_all]),
            'away_score_sd': _sample_sd([row['for'] for row in away_all]),
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

    features.update({
        'home_rest_days': rest(home_all), 'away_rest_days': rest(away_all),
        'home_games': len(home_all), 'away_games': len(away_all),
        'historical_games': len(history),
    })
    return {
        'features': features,
        'source_refs': sorted({row['source'] for row in history}),
        'fixture_ids': sorted({row['id'] for row in home_all + away_all}),
        'cutoff_at': cutoff.isoformat(),
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
    bins = [{'n': 0, 'p': 0.0, 'y': 0.0} for _ in range(10)]
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


def train_model(connection, sport):
    fixtures = [dict(row) for row in connection.execute(
        """SELECT f.*,r.home_score,r.away_score,r.settled_at
           FROM sports_ai_fixtures f JOIN sports_ai_results r ON r.fixture_id=f.id
           WHERE f.sport=? AND r.status='final' ORDER BY f.starts_at""", (sport,),
    )]
    observations = []
    timestamps = []
    for fixture in fixtures:
        try:
            if parse_time(fixture['settled_at']) >= parse_time(fixture['starts_at']):
                continue
        except (TypeError, ValueError):
            continue
        built = build_features(connection, fixture)
        if not built:
            continue
        features = built['features']
        observations.append((features,fixture['home_score'],fixture['away_score']))
        timestamps.append(fixture['starts_at'])
    split = int(len(observations) * .7)
    calibration_set, holdout = observations[:split], observations[split:]
    rho = _fit_dixon_coles_rho(calibration_set) if sport == 'football' else 0.0
    def probabilities_for(observation):
        features, home_score, away_score = observation
        if sport == 'football':
            probabilities = [
                market_probability('football',name,features,rho)['p_win']
                for name in ('HOME','DRAW','AWAY')
            ]
            actual = 0 if home_score > away_score else 2 if home_score < away_score else 1
        else:
            home = market_probability('basketball','MONEYLINE_HOME',features)
            probabilities = [home['p_win'],1-home['p_win']]
            actual = int(home_score > away_score)
        return probabilities,actual
    temperature, best_loss = 1.0, float('inf')
    for step in range(50, 201, 2):
        candidate = step / 100
        loss = 0.0
        for observation in calibration_set:
            probabilities,actual = probabilities_for(observation)
            calibrated = _temperature(probabilities, candidate)
            loss -= math.log(max(1e-15, calibrated[actual]))
        if calibration_set and loss < best_loss:
            temperature, best_loss = candidate, loss
    samples = []
    multiclass_logloss = 0.0
    for observation in holdout:
        probabilities,actual = probabilities_for(observation)
        calibrated = _temperature(probabilities, temperature)
        multiclass_logloss -= math.log(max(1e-15, calibrated[actual]))
        samples.extend((value, float(index == actual)) for index, value in enumerate(calibrated))
    metrics = calibration_metrics(samples)
    metrics.update({
        'fixtures_scored': len(observations), 'calibration_fixtures': len(calibration_set),
        'holdout_fixtures': len(holdout),
        'multiclass_log_loss': multiclass_logloss / len(holdout) if holdout else None,
    })
    validated = (
        len(observations) >= MIN_VALIDATION_FIXTURES
        and len(holdout) >= 10
        and metrics['calibration_error'] is not None
        and metrics['calibration_error'] <= .20
    )
    run_id, model_id = str(uuid.uuid4()), str(uuid.uuid4())
    model_name = MODEL_NAMES[sport]
    version = str(connection.execute(
        'SELECT COUNT(*)+1 FROM sports_ai_training_runs WHERE sport=? AND model_name=?',
        (sport, model_name),
    ).fetchone()[0])
    window = {'start': min(timestamps) if timestamps else None, 'end': max(timestamps) if timestamps else None}
    params = {'calibration': 'temperature_scaling', 'temperature': temperature,
              'fit_partition': 'chronological first 70%', 'dixon_coles_rho': rho}
    created = datetime.now(timezone.utc).isoformat()
    status = 'validated' if validated else 'insufficient_data'
    connection.execute(
        """INSERT INTO sports_ai_training_runs
           (id,sport,model_name,version,status,training_window_json,parameters_json,metrics_json,sample_size,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (run_id,sport,model_name,version,status,json.dumps(window),json.dumps(params),
         json.dumps(metrics),len(observations),created),
    )
    connection.execute(
        """INSERT INTO sports_ai_model_registry
           (id,training_run_id,sport,model_name,version,status,active,parameters_json,metrics_json,created_at)
           VALUES(?,?,?,?,?,?,0,?,?,?)""",
        (model_id,run_id,sport,model_name,version,
         'validated' if validated else 'unvalidated',json.dumps(params),json.dumps(metrics),created),
    )
    connection.execute(
        """INSERT INTO sports_ai_calibration_results
           (id,model_id,market,method,sample_size,metrics_json,held_out_start,held_out_end)
           VALUES(?,?,?,?,?,?,?,?)""",
        (str(uuid.uuid4()),model_id,'match_outcome','temperature_scaling',
         len(holdout),json.dumps(metrics),
         timestamps[split] if split < len(timestamps) else None,
         timestamps[-1] if timestamps else None),
    )
    return {
        'model_id': model_id, 'sport': sport, 'model_name': model_name,
        'version': version, 'status': status, 'active': False,
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


def _outcomes(sport):
    if sport == 'football':
        return list(FOOTBALL_MARKETS)
    return ['MONEYLINE_HOME', 'MONEYLINE_AWAY'] + [
        f'{side}_{line}' for line in ('199.5', '209.5', '219.5', '229.5', '239.5')
        for side in ('OVER', 'UNDER')
    ] + [
        f'{team}_{side}_{line}' for team in ('HOME', 'AWAY')
        for side in ('OVER', 'UNDER') for line in ('79.5', '89.5', '99.5', '109.5')
    ] + ['HOME_SPREAD_-5.5', 'AWAY_SPREAD_5.5']


def run_analysis(connection, actor, audit):
    now = datetime.now(timezone.utc)
    fixtures = [dict(row) for row in connection.execute(
        """SELECT * FROM sports_ai_fixtures WHERE starts_at>? AND status='scheduled'
           ORDER BY starts_at""", (now.isoformat(),),
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
    stats = {'predictions': 0, 'opportunities': 0, 'qualified': 0, 'skipped': {}}
    for fixture in fixtures:
        model = models.get(fixture['sport'])
        if not model:
            continue
        try:
            built = build_features(connection, fixture)
            if not built:
                stats['skipped']['insufficient_history'] = stats['skipped'].get('insufficient_history', 0) + 1
                continue
            feature_id = str(uuid.uuid4())
            connection.execute(
                """INSERT INTO sports_ai_feature_snapshots
                   (id,fixture_id,sport,cutoff_at,features_json,source_refs_json,generated_at)
                   VALUES(?,?,?,?,?,?,?) ON CONFLICT(fixture_id,cutoff_at) DO UPDATE SET
                   features_json=excluded.features_json,source_refs_json=excluded.source_refs_json""",
                (feature_id,fixture['id'],fixture['sport'],built['cutoff_at'],
                 json.dumps(built['features'],allow_nan=False),json.dumps({
                     'sources':built['source_refs'],'fixture_ids':built['fixture_ids'],
                 }),now.isoformat()),
            )
            feature_id = connection.execute(
                'SELECT id FROM sports_ai_feature_snapshots WHERE fixture_id=? AND cutoff_at=?',
                (fixture['id'],built['cutoff_at']),
            ).fetchone()['id']
            quotes = [dict(row) for row in connection.execute(
                """SELECT o.* FROM sports_ai_odds_snapshots o
                   JOIN sports_ai_provider_registry p ON p.id=o.provider_id
                   WHERE o.fixture_id=? AND p.enabled=1 AND p.terms_reviewed=1 AND p.status='operational'""",
                (fixture['id'],),
            )]
            mapped = [(quote,normalize_quote_market(fixture['sport'],fixture,quote)) for quote in quotes]
            markets = set(_outcomes(fixture['sport']))
            markets.update(market for _, market in mapped if market)
            features = built['features']
            parameters = json.loads(model['parameters_json'])
            rho = float(parameters.get('dixon_coles_rho', 0.0))
            temperature = float(parameters.get('temperature', 1.0))
            for market in sorted(markets):
                settlement = market_probability(fixture['sport'],market,features,rho)
                if not settlement:
                    continue
                if model['active'] and fixture['sport']=='football' and market in ('HOME','DRAW','AWAY'):
                    base=[market_probability('football',name,features,rho)['p_win']
                          for name in ('HOME','DRAW','AWAY')]
                    calibrated=_temperature(base,temperature)
                    probability=calibrated[('HOME','DRAW','AWAY').index(market)]
                    settlement={'p_win':probability,'p_half_win':0.0,'p_push':0.0,
                                'p_half_loss':0.0,'p_loss':1-probability}
                elif model['active'] and fixture['sport']=='basketball' and market in ('MONEYLINE_HOME','MONEYLINE_AWAY'):
                    raw=market_probability('basketball','MONEYLINE_HOME',features)['p_win']
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
                evidence = {
                    'features': features, 'source_refs': built['source_refs'],
                    'historical_games': features['historical_games'],
                    'probability': settlement['p_win'],
                    'data_completeness': 1.0,
                    'model_reliability': json.loads(model['metrics_json']),
                    'calibration_status': 'validated' if market_calibrated else 'awaiting_market_calibration',
                    'model_name': model['model_name'], 'model_version': model['version'],
                }
                connection.execute(
                    """INSERT INTO sports_ai_engine_predictions
                       (id,run_id,fixture_id,model_id,feature_id,market,outcome,probability,
                        settlement_json,explanation_json,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (prediction_id,run_id,fixture['id'],model['id'],feature_id,market,market,
                     settlement['p_win'],json.dumps(settlement),json.dumps(evidence),now.isoformat()),
                )
                stats['predictions'] += 1
                available = [(quote, canonical) for quote, canonical in mapped if canonical == market]
                if not available:
                    status = 'awaiting_calibration' if not model['active'] else 'awaiting_odds'
                    reason = 'Model is not explicitly activated after validation.' if not model['active'] else 'No permitted bookmaker quote is available.'
                    connection.execute(
                        """INSERT INTO sports_ai_value_opportunities
                           (id,prediction_id,status,reason,evaluated_at) VALUES(?,?,?,?,?)""",
                        (str(uuid.uuid4()),prediction_id,status,reason,now.isoformat()),
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
                    if age < -5:
                        status, reason = 'rejected', 'Odds timestamp is in the future.'
                    elif age > odds_age_limit:
                        status, reason = 'expired', 'Odds quote is stale.'
                    elif issue:
                        status, reason = 'suspended', 'Unresolved fixture data-quality issue.'
                    elif not market_calibrated:
                        status, reason = 'awaiting_calibration', 'This market has no validated calibration evidence.'
                    else:
                        edge = expected_value(settlement,float(quote['decimal_odds']))
                        if edge is None or edge < min_edge:
                            status, reason = 'rejected', 'Expected value is below the configured edge.'
                        else:
                            status, reason = 'qualified', 'Fresh permitted quote and validated active model meet all qualification checks.'
                            stats['qualified'] += 1
                    edge = expected_value(settlement,float(quote['decimal_odds']))
                    connection.execute(
                        """INSERT INTO sports_ai_value_opportunities
                           (id,prediction_id,odds_id,implied_probability,expected_edge,status,reason,evaluated_at)
                           VALUES(?,?,?,?,?,?,?,?)""",
                        (str(uuid.uuid4()),prediction_id,quote['id'],
                         1 / float(quote['decimal_odds']),edge,status,reason,now.isoformat()),
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
