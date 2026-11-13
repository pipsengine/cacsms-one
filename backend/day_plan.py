"""The day plan for real-money singles: rank every game of the betting day first, then pick the best.

Owner's requirement (13 Nov 2026): analyse all games and pick those with the highest chance of winning,
not the first that come. Each cycle:

1. Candidates: every single that passes the engine's rules (value singles from the model, model
   favourites, and SportyBet favourites priced 1.60-1.99 in placeable markets and allowed leagues) with
   kickoff before the end of the betting day (06:00 WAT next morning, so night games such as the NBA
   count for the evening they are bet on).
2. Score: the chance of winning from evidence -
   - SportyBet's price with the margin removed, corrected by history (pick_scoring: how such selections
     actually did in this sport / market / price band / league),
   - blended with the model's market-anchored probability where an active model has a prediction,
   - nudged by price movement (shortening = money coming in; drifting = against) and price freshness.
3. Pick the day's best set within the singles quota: ranked by score, at most MAX_PER_LEAGUE per league,
   sport reserves (basketball) honoured.
4. Place nearer kickoff: a picked single is queued only from PLACE_WINDOW_HOURS before its kickoff, so
   until then a better game appearing later in the day (e.g. evening basketball priced in the afternoon)
   can take its place. Placed bets never change.

The plan, with every candidate's score and the reason it was or was not picked, is stored in the
settings table for the engine page.
"""
from datetime import datetime, timedelta, timezone

PLACE_WINDOW_HOURS = 4
MAX_PER_LEAGUE = 2
MODEL_WEIGHT = 0.5
MOVE_THRESHOLD = 0.03          # a 3% price change counts as movement
MOVE_ADJUST = 0.015            # small tie-breaker until closing-price tracking shows what movement is worth
STALE_PRICE_MINUTES = 60
STALE_ADJUST = 0.01
PLAN_KEY = 'single_day_plan'
SHOWN = 40
WAT = timezone(timedelta(hours=1), 'WAT')


def _now(now=None):
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc)


def _parse(value):
    try:
        moment = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def betting_day_end(now):
    """06:00 WAT after `now`: night games belong to the evening they are bet on."""
    local = _now(now).astimezone(WAT)
    end = local.replace(hour=6, minute=0, second=0, microsecond=0)
    if local >= end:
        end += timedelta(days=1)
    return end.astimezone(timezone.utc)


def _model_probability(connection, item, anchors):
    """Market-anchored probability of the active model's latest prediction for this selection, if any."""
    from market_anchor import anchor
    row = connection.execute(
        """SELECT p.probability FROM sports_ai_engine_predictions p
           JOIN sports_ai_model_registry m ON m.id=p.model_id AND m.active=1 AND m.calibration_status='validated'
           WHERE p.fixture_id=? AND upper(p.market)=upper(?) ORDER BY p.created_at DESC LIMIT 1""",
        (item['fixture_id'], item['market']),
    ).fetchone()
    if not row:
        return None
    try:
        return float(anchor(anchors, item['sport'], item.get('competition'), float(row[0]), float(item['decimal_odds']))['probability'])
    except Exception:
        return None


def _movement(connection, item, now):
    """Opening price (first in 24 h) over the current price, minus 1: >0 shortened, <0 drifted."""
    prediction = str(item.get('prediction_id') or '')
    if not prediction.startswith('jackpot:'):
        return None
    quote = connection.execute(
        'SELECT fixture_id, market, outcome, line FROM sports_ai_odds_snapshots WHERE id=?', (prediction.split(':', 1)[1],),
    ).fetchone()
    if not quote:
        return None
    first = connection.execute(
        """SELECT decimal_odds FROM sports_ai_odds_snapshots
           WHERE fixture_id=? AND bookmaker='SportyBet' AND market=? AND outcome=? AND IFNULL(line,-1)=IFNULL(?,-1)
             AND observed_at>=? ORDER BY observed_at LIMIT 1""",
        (quote[0], quote[1], quote[2], quote[3], (now - timedelta(hours=24)).isoformat()),
    ).fetchone()
    if not first or not first[0]:
        return None
    return float(first[0]) / float(item['decimal_odds']) - 1


def score(connection, item, now, anchors):
    """The plan's chance of winning for one candidate, with the parts it is made of."""
    odds = float(item['decimal_odds'])
    history_p = float(item['probability'])          # already history-corrected for SportyBet favourites
    model_p = _model_probability(connection, item, anchors)
    chance = history_p if model_p is None else (1 - MODEL_WEIGHT) * history_p + MODEL_WEIGHT * model_p
    move = _movement(connection, item, now)
    if move is not None and move >= MOVE_THRESHOLD:
        chance *= 1 + MOVE_ADJUST
    elif move is not None and move <= -MOVE_THRESHOLD:
        chance *= 1 - MOVE_ADJUST
    seen = _parse(item.get('odds_observed_at'))
    stale = bool(seen) and (now - seen).total_seconds() > STALE_PRICE_MINUTES * 60
    if stale:
        chance *= 1 - STALE_ADJUST
    chance = min(0.97, chance)
    return {
        'chance': round(chance, 4), 'ev': round(chance * odds - 1, 4),
        'market_chance': round(float(item.get('market_probability', history_p)), 4),
        'history_factor': round(float(item.get('history_factor', 1.0)), 3),
        'history_level': item.get('history_level', 'model pick'), 'history_sample': item.get('history_sample', 0),
        'model_chance': None if model_p is None else round(model_p, 4),
        'movement': None if move is None else round(move, 4), 'stale_price': stale,
    }


def plan_singles(connection, now, day, pool):
    """Singles to queue now. Ranks the whole betting day; queues only picks whose placement window opened."""
    import single_betting as sb
    from market_anchor import load as load_anchors
    current = _now(now)
    end = betting_day_end(current).isoformat()
    room = sb.room_today(connection, day, 'single')
    favourites_left = max(0, sb.FAVOURITE_DAILY_LIMIT - sb._favourites_today(connection, day))
    anchors = load_anchors(connection)
    items, seen_fixtures = [], set()
    for item in list(pool.get('eligible') or []) + list(pool.get('favourites') or []):
        if str(item['starts_at']) >= end or item['fixture_id'] in seen_fixtures:
            continue
        seen_fixtures.add(item['fixture_id'])
        items.append({**item, 'plan': score(connection, item, current, anchors)})
    items.sort(key=lambda i: (-i['plan']['chance'], -i['plan']['ev'], str(i['starts_at']), i['fixture_id']))

    # Leagues already used today count towards MAX_PER_LEAGUE.
    per_league = {}
    for row in connection.execute(
        f"""SELECT sport, competition FROM sports_ai_single_bets WHERE local_date=?
              AND COALESCE(kind,'SINGLE') NOT IN ('TICKET','COMBO')
              AND status IN ({','.join('?' * (len(sb.COUNTED) + 1))})""" + (sb._LIVE_ONLY if sb._live(connection) else ''),
        (day, *sb.COUNTED, 'QUEUED'),
    ):
        key = (row[0], row[1] or '')
        per_league[key] = per_league.get(key, 0) + 1
    eligible, reasons = [], {}
    for item in items:
        key = (item['sport'], item.get('competition') or '')
        if per_league.get(key, 0) >= MAX_PER_LEAGUE:
            reasons[item['fixture_id']] = f'league already has {MAX_PER_LEAGUE} bets today'
            continue
        eligible.append(item)
        per_league[key] = per_league.get(key, 0) + 1
    chosen = sb.with_sport_reserve(eligible, room, sb.sport_reserve_left(connection, day, current))
    # The favourites limit counts picks only (it used to count every eligible favourite, 13 Nov 2026).
    kept, favourites_taken = [], 0
    for item in chosen:
        if item.get('kind') == 'FAVOURITE':
            if favourites_taken >= favourites_left:
                reasons[item['fixture_id']] = 'favourites limit for today reached'
                continue
            favourites_taken += 1
        kept.append(item)
    chosen = kept
    chosen_ids = {i['fixture_id'] for i in chosen}
    opens = lambda item: _parse(item['starts_at']) - timedelta(hours=PLACE_WINDOW_HOURS)
    queue_now = [i for i in chosen if opens(i) <= current]

    shown = []
    for rank, item in enumerate(items[:SHOWN], start=1):
        if item['fixture_id'] in chosen_ids:
            status = 'placing now' if item in queue_now else f"picked · places from {opens(item).astimezone(WAT):%H:%M} WAT"
        else:
            status = 'not picked: ' + reasons.get(item['fixture_id'], 'lower chance than the picks' if room else 'singles quota for today is full')
        shown.append({
            'rank': rank, 'fixture_id': item['fixture_id'], 'fixture': f"{item['home']} vs {item['away']}",
            'sport': item['sport'], 'competition': item.get('competition') or '', 'market': item['market'],
            'selection': item.get('selection'), 'odds': float(item['decimal_odds']), 'starts_at': item['starts_at'],
            'kind': item.get('kind') or 'SINGLE', 'picked': item['fixture_id'] in chosen_ids, 'status': status, **item['plan'],
        })
    sb._save_setting(connection, PLAN_KEY, {
        'day': day, 'built_at': current.isoformat(), 'betting_day_end': end, 'room': room,
        'candidates': len(items), 'picked': len(chosen), 'placing_now': len(queue_now),
        'place_window_hours': PLACE_WINDOW_HOURS, 'max_per_league': MAX_PER_LEAGUE, 'rows': shown,
    }, current)
    return [{k: v for k, v in i.items() if k != 'plan'} for i in queue_now]


def plan_view(connection):
    import single_betting as sb
    return sb._setting(connection, PLAN_KEY, {})
