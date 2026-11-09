"""Market-anchored probabilities for every sport and league.

Settled data showed the model alone is no better than SportyBet's price, and that
when the model rates a pick far above the market it is mostly overconfident. A
blend of the two beats either alone. This module learns, per sport and per league:

* ``margin``: the bookmaker overround, so 1/odds can be turned into a fair probability,
* ``weight``: how much to trust the model against that fair price (0 = market only),
* ``cap``: how far above the market the model may go before a pick is held back.

Learning uses only predictions stored before kickoff with a linked SportyBet price
and an official final result. Fits are chronological: the first 70% chooses the
parameters and the last 30% checks them. A league with little data leans on its
sport's values; a league where the model does not beat the market gets weight 0,
so its picks carry no edge and stop qualifying. Nothing here places a wager.
"""
import json
from collections import defaultdict
from datetime import datetime, timezone

KEY = 'market_anchor'
DEFAULT_WEIGHT = 0.5      # settled basketball data: a 50/50 blend scored best
DEFAULT_MARGIN = 1.06
DEFAULT_CAP = 0.15        # model may exceed the fair price by at most 15 points
PROVEN_CAP = 0.25         # allowed once a league's blend beats the market on held-out data
MIN_SPORT_PAIRS = 40
MIN_LEAGUE_PAIRS = 20
LEAGUE_PRIOR = 60         # pseudo-count pulling a league towards its sport
MIN_HOLDOUT = 12
DROP_EVIDENCE = 100      # held-out matches needed before the model can be switched off entirely
WEIGHT_FLOOR = 0.25      # until then the model keeps at least this weight
GRID = [round(step * 0.05, 2) for step in range(21)]


def _now(now=None):
    return now or datetime.now(timezone.utc)


def load(connection):
    row = connection.execute('SELECT value_json FROM sports_ai_settings WHERE key=?', (KEY,)).fetchone()
    if not row or not row[0]:
        return {}
    try:
        return json.loads(row[0])
    except json.JSONDecodeError:
        return {}


def _league_key(sport, league):
    return f'{sport}|{(league or "").strip().casefold()}'


def parameters(anchors, sport, league):
    """The parameters in force for one sport and league, with where they came from."""
    leagues = anchors.get('leagues') or {}
    sports = anchors.get('sports') or {}
    item = leagues.get(_league_key(sport, league))
    if item:
        return {**item, 'source': 'league'}
    item = sports.get(sport)
    if item:
        return {**item, 'source': 'sport'}
    return {'weight': DEFAULT_WEIGHT, 'margin': DEFAULT_MARGIN, 'cap': DEFAULT_CAP, 'source': 'default'}


def set_model_only(connection):
    """Use the model's probability unchanged (tests of other behaviour; not for production)."""
    connection.execute(
        """INSERT INTO sports_ai_settings(key,value_json,updated_at) VALUES(?,?,CURRENT_TIMESTAMP)
           ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json""", (KEY, json.dumps({'mode': 'model_only'})),
    )


def anchor(anchors, sport, league, model_probability, odds):
    """Blend the model probability with SportyBet's fair price."""
    fair = min(0.999, max(0.001, (1.0 / float(odds)) / DEFAULT_MARGIN))
    if (anchors or {}).get('mode') == 'model_only':
        return {'probability': float(model_probability), 'fair': fair, 'weight': 1.0,
                'gap': float(model_probability) - fair, 'cap': 1.0, 'source': 'model_only'}
    params = parameters(anchors or {}, sport, league)
    fair = min(0.999, max(0.001, (1.0 / float(odds)) / float(params['margin'])))
    weight = float(params['weight'])
    blended = weight * float(model_probability) + (1 - weight) * fair
    return {
        'probability': min(0.999, max(0.001, blended)), 'fair': fair, 'weight': weight,
        'gap': float(model_probability) - fair, 'cap': float(params['cap']), 'source': params['source'],
    }


def _pairs(connection):
    """Pre-kickoff predictions with a SportyBet price and a final result, one per fixture and market."""
    from accumulator_engine import settle_result
    rows = connection.execute(
        """SELECT p.fixture_id, p.market, p.probability, p.created_at, f.sport, f.competition, f.starts_at,
                  q.decimal_odds, r.home_score, r.away_score
           FROM sports_ai_value_opportunities v
           JOIN sports_ai_engine_predictions p ON p.id=v.prediction_id
           JOIN sports_ai_odds_snapshots q ON q.id=v.odds_id
           JOIN sports_ai_fixtures f ON f.id=p.fixture_id
           JOIN sports_ai_results r ON r.fixture_id=f.id AND r.status='final'
           WHERE q.bookmaker='SportyBet' AND p.created_at<f.starts_at AND q.decimal_odds>1""",
    ).fetchall()
    latest = {}
    for row in rows:
        key = (row['fixture_id'], row['market'])
        if key in latest and str(latest[key]['created_at']) >= str(row['created_at']):
            continue
        latest[key] = row
    pairs = []
    for row in latest.values():
        outcome = settle_result(row['sport'], row['market'], row['home_score'], row['away_score'])
        if outcome not in ('won', 'lost'):
            continue
        try:
            probability, odds = float(row['probability']), float(row['decimal_odds'])
        except (TypeError, ValueError):
            continue
        if not 0 < probability < 1:
            continue
        pairs.append({'sport': row['sport'], 'league': row['competition'], 'kickoff': str(row['starts_at']),
                      'p': probability, 'odds': odds, 'y': 1 if outcome == 'won' else 0})
    return pairs


def _brier(values):
    return sum((p - y) ** 2 for p, y in values) / len(values) if values else None


def _fit(pairs, fallback=None):
    """Chronological 70/30 fit of margin and weight, with held-out scores."""
    ordered = sorted(pairs, key=lambda item: item['kickoff'])
    cut = max(1, int(len(ordered) * 0.7))
    train, hold = ordered[:cut], ordered[cut:]
    wins = sum(item['y'] for item in train)
    implied = sum(1 / item['odds'] for item in train)
    margin = min(1.15, max(1.0, implied / wins)) if wins else (fallback or {}).get('margin', DEFAULT_MARGIN)

    def blend(item, weight):
        fair = min(0.999, max(0.001, (1 / item['odds']) / margin))
        return weight * item['p'] + (1 - weight) * fair

    weight = min(GRID, key=lambda w: (_brier([(blend(i, w), i['y']) for i in train]), abs(w - DEFAULT_WEIGHT)))
    scores = {
        'model': _brier([(i['p'], i['y']) for i in hold]),
        'market': _brier([(blend(i, 0.0), i['y']) for i in hold]),
        'blend': _brier([(blend(i, weight), i['y']) for i in hold]),
    }
    proven = len(hold) >= MIN_HOLDOUT and scores['blend'] is not None and scores['blend'] < scores['market']
    # The model must earn its weight on unseen matches; otherwise trust the market.
    if len(hold) >= MIN_HOLDOUT and scores['blend'] is not None and scores['blend'] > scores['market'] + 0.002:
        weight = 0.0
    if len(hold) < DROP_EVIDENCE:
        weight = max(weight, WEIGHT_FLOOR)  # too few unseen matches to switch the model off
    return {
        'weight': weight, 'margin': round(margin, 4), 'cap': PROVEN_CAP if proven else DEFAULT_CAP,
        'pairs': len(ordered), 'holdout': len(hold), 'proven': proven,
        'holdout_brier': {k: (round(v, 4) if v is not None else None) for k, v in scores.items()},
    }


def learn(connection, now=None):
    """Refit every sport and league from settled data and store the result."""
    current = _now(now)
    if load(connection).get('mode') == 'model_only':
        return load(connection)
    pairs = _pairs(connection)
    by_sport, by_league = defaultdict(list), defaultdict(list)
    for item in pairs:
        by_sport[item['sport']].append(item)
        by_league[_league_key(item['sport'], item['league'])].append(item)
    sports = {}
    for sport, items in by_sport.items():
        if len(items) >= MIN_SPORT_PAIRS:
            sports[sport] = {**_fit(items), 'status': 'learned'}
        else:
            sports[sport] = {'weight': DEFAULT_WEIGHT, 'margin': DEFAULT_MARGIN, 'cap': DEFAULT_CAP,
                             'pairs': len(items), 'status': 'default'}
    leagues = {}
    for key, items in by_league.items():
        sport = key.split('|', 1)[0]
        base = sports.get(sport) or {'weight': DEFAULT_WEIGHT, 'margin': DEFAULT_MARGIN}
        if len(items) < MIN_LEAGUE_PAIRS:
            continue
        own = _fit(items, base)
        n = len(items)
        shrunk = (n * own['weight'] + LEAGUE_PRIOR * float(base['weight'])) / (n + LEAGUE_PRIOR)
        if own['holdout'] >= DROP_EVIDENCE and own['weight'] == 0.0:
            shrunk = 0.0  # the model failed this league on enough unseen matches
        leagues[key] = {**own, 'weight': round(shrunk, 3), 'league_weight': own['weight'], 'status': 'learned'}
    result = {'learned_at': current.isoformat(), 'pairs': len(pairs), 'sports': sports, 'leagues': leagues}
    connection.execute(
        """INSERT INTO sports_ai_settings(key,value_json,updated_at) VALUES(?,?,?)
           ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at""",
        (KEY, json.dumps(result), current.isoformat()),
    )
    return result


def due(connection, now=None, hours=6):
    stored = load(connection)
    if stored.get('mode') == 'model_only':
        return False
    learned = stored.get('learned_at')
    if not learned:
        return True
    try:
        return (_now(now) - datetime.fromisoformat(learned)).total_seconds() >= hours * 3600
    except ValueError:
        return True
