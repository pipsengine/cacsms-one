"""Pick scoring: how likely a selection really is, from evidence rather than SportyBet's price alone.

Owner's requirement (13 Nov 2026): analyse every game first and pick the ones with the highest chance of
winning, instead of the first that come. SportyBet's price (margin removed) is only the starting point;
history shows where that price is too generous or too mean:

- History (calibration): every finished match SportyBet priced and that has a verified final score. For
  each selection, the last SportyBet price before kickoff is turned into a fair probability (margin
  removed, as the live scan does) and graded on the score. Per sport + market family + price band, and per
  league, the system knows how often such selections actually won versus how often the price said they
  would. That ratio (the history factor) moves a candidate's chance up or down.
- Small samples are not trusted: every factor is shrunk towards 1.0 (league towards its sport), with
  SHRINK_EXPECTED_WINS pseudo-wins, so a league with 3 lucky results barely moves anything.
- The model (where it has a fresh prediction, via the market anchor) and price movement are blended in
  by day_plan when the day is planned.

The table is built in the worker (BUILD_DAYS of history) and kept in a file next to the database for
CACHE_HOURS, so pages and the API never compute it.
"""
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

BUILD_DAYS = 90
CACHE_HOURS = 24
SHRINK_EXPECTED_WINS = 40.0   # pseudo-count: a bucket needs ~40 expected wins before its own rate dominates
FACTOR_RANGE = (0.85, 1.15)   # history never moves a fair probability by more than 15%
MAX_PROBABILITY = 0.97
BANDS = ((1.0, 1.3), (1.3, 1.6), (1.6, 2.0), (2.0, 3.0), (3.0, 1000.0))
_CACHE = {'table': None, 'at': 0.0}


def band(odds):
    odds = float(odds)
    for low, high in BANDS:
        if low <= odds < high:
            return f'{low:.1f}-{high:.1f}'
    return 'other'


def family(market):
    from prize_ladder import family as ladder_family
    name = str(market or '').upper()
    if name.startswith(('OVER_', 'UNDER_')):
        return 'total'
    if name.startswith('BTTS_'):
        return 'btts'
    return ladder_family(name)


def _cache_file():
    import db
    return Path(db.DB).resolve().parent / 'pick_calibration.json'


# ---------------------------------------------------------------- building the history table

def build(connection, now=None, days=BUILD_DAYS):
    """Grade the last pre-kickoff SportyBet price of every selection on finished, verified matches."""
    from accumulator_engine import settle_result, _simulated_competition
    from jackpot_low import BOOKABLE, DEFAULT_MARGIN, SIMULATED_SPORTS, _label_market, _margins
    from prediction_engine import normalize_quote_market
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    since = (current - timedelta(days=days)).isoformat()
    fixtures = connection.execute(
        """SELECT f.id, f.sport, f.competition, f.home, f.away, f.starts_at, r.home_score, r.away_score
           FROM sports_ai_results r JOIN sports_ai_fixtures f ON f.id=r.fixture_id
           WHERE r.status='final' AND f.starts_at>=? AND f.starts_at<?
             AND EXISTS (SELECT 1 FROM sports_ai_sportybet_observations o WHERE o.fixture_id=f.id)""",
        (since, current.isoformat()),
    ).fetchall()
    buckets = {}
    graded = 0
    for fx in fixtures:
        if fx['sport'] in SIMULATED_SPORTS or _simulated_competition(fx['competition']):
            continue
        rows = connection.execute(
            """SELECT fixture_id, market, outcome, line, decimal_odds, observed_at FROM sports_ai_odds_snapshots
               WHERE fixture_id=? AND bookmaker='SportyBet' AND observed_at<? AND decimal_odds>1
               ORDER BY observed_at""",
            (fx['id'], fx['starts_at']),
        ).fetchall()
        latest = {}
        for row in rows:  # ascending: the last one kept is the closing price
            latest[(row['market'], row['outcome'], row['line'])] = row
        if not latest:
            continue
        rows = list(latest.values())
        margins = _margins(rows)
        fixture = {'home': fx['home'], 'away': fx['away']}
        seen = set()
        for row in rows:
            odds = float(row['decimal_odds'])
            try:
                market = normalize_quote_market(fx['sport'], fixture, dict(row))
            except Exception:
                market = None
            market = market or _label_market(fx['sport'], row['market'], row['outcome'], row['line'])
            if not market or not BOOKABLE.match(market) or market in seen:
                continue
            outcome = settle_result(fx['sport'], market, fx['home_score'], fx['away_score'])
            if outcome not in ('won', 'lost'):
                continue
            seen.add(market)
            fair = min(0.99, (1 / odds) / margins.get((row['fixture_id'], row['market'], row['line']), DEFAULT_MARGIN))
            fam, bnd = family(market), band(odds)
            for key in (f"{fx['sport']}|{fam}|{bnd}", f"{fx['sport']}|{fam}|{bnd}|{fx['competition'] or ''}"):
                n, wins, expected = buckets.get(key, (0, 0, 0.0))
                buckets[key] = (n + 1, wins + (outcome == 'won'), expected + fair)
            graded += 1
    table = {'built_at': current.isoformat(), 'days': days, 'fixtures': len(fixtures), 'selections': graded,
             'buckets': {k: [n, w, round(e, 4)] for k, (n, w, e) in buckets.items()}}
    _save(table)
    _CACHE.update(table=table, at=time.monotonic())
    return table


def _save(table):
    try:
        path = _cache_file()
        temp = path.with_name(f'{path.name}.{os.getpid()}.tmp')
        temp.write_text(json.dumps(table), encoding='utf8')
        os.replace(temp, path)
    except OSError:
        pass


def table(max_age_hours=CACHE_HOURS * 2):
    """The stored history table, or None. Never builds (the worker does)."""
    if _CACHE['table'] is not None and time.monotonic() - _CACHE['at'] < 600:
        return _CACHE['table']
    try:
        path = _cache_file()
        if time.time() - path.stat().st_mtime > max_age_hours * 3600:
            return None
        data = json.loads(path.read_text(encoding='utf8'))
    except (OSError, ValueError):
        return None
    _CACHE.update(table=data, at=time.monotonic())
    return data


def refresh_if_stale(connection, now=None):
    """Worker hook: rebuild once the table is older than CACHE_HOURS."""
    try:
        age = time.time() - _cache_file().stat().st_mtime
    except OSError:
        age = None
    if age is not None and age < CACHE_HOURS * 3600:
        return {'status': 'fresh', 'age_hours': round(age / 3600, 1)}
    built = build(connection, now)
    return {'status': 'built', 'selections': built['selections'], 'fixtures': built['fixtures']}


# ---------------------------------------------------------------- using it

def _shrunk(stats, prior):
    """Observed/expected win ratio, shrunk towards `prior` with SHRINK_EXPECTED_WINS pseudo-wins."""
    if not stats:
        return prior, 0
    n, wins, expected = stats
    return (wins + SHRINK_EXPECTED_WINS * prior) / (expected + SHRINK_EXPECTED_WINS), n


def history_factor(sport, competition, market, odds, data=None):
    """(factor, sample, level): how much more (>1) or less (<1) often such selections won than priced."""
    data = data if data is not None else table()
    if not data:
        return 1.0, 0, 'no history yet'
    buckets = data.get('buckets') or {}
    base = f'{sport}|{family(market)}|{band(odds)}'
    sport_factor, sport_n = _shrunk(buckets.get(base), 1.0)
    league_factor, league_n = _shrunk(buckets.get(f'{base}|{competition or ""}'), sport_factor)
    factor = min(FACTOR_RANGE[1], max(FACTOR_RANGE[0], league_factor))
    level = 'league' if league_n else 'sport' if sport_n else 'no history yet'
    return factor, league_n or sport_n, level


def calibrated(leg, data=None):
    """Fair probability of a scanned leg corrected by history (never more than FACTOR_RANGE)."""
    factor, _, _ = history_factor(leg.get('sport'), leg.get('competition'), leg.get('market'), leg['decimal_odds'], data)
    return min(MAX_PROBABILITY, float(leg['probability']) * factor)


# ---------------------------------------------------------------- proven selection types (codes)

PROVEN_MIN_SAMPLE = 30      # graded selections of this sport + market family + price band
PROVEN_MIN_FACTOR = 1.02    # must have won at least 2% more often than priced (after shrinkage)


def proven(leg, data=None):
    """A code selection is used only where history shows its kind wins more often than priced
    (owner, 14 Nov 2026: category and jackpot codes had a 0 win record built from unproven legs,
    tennis winners above all: 63.7% won against 68.7% priced)."""
    data = data if data is not None else table()
    if not data:
        return False
    stats = (data.get('buckets') or {}).get(f"{leg.get('sport')}|{family(leg.get('market'))}|{band(leg['decimal_odds'])}")
    if not stats or stats[0] < PROVEN_MIN_SAMPLE:
        return False
    factor, _, _ = history_factor(leg.get('sport'), leg.get('competition'), leg.get('market'), leg['decimal_odds'], data)
    return factor >= PROVEN_MIN_FACTOR


def one_cut_chance(probabilities):
    """Chance that at most one selection loses (SportyBet's One Cut still pays, at reduced odds)."""
    import math
    probabilities = [float(p) for p in probabilities]
    every = math.prod(probabilities)
    one_lost = sum((1 - p) * math.prod(q for j, q in enumerate(probabilities) if j != i) for i, p in enumerate(probabilities))
    return every + one_lost
