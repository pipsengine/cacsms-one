"""Which sports and leagues may supply selections, from their settled track record.

Owner's instruction (12 Nov 2026): use the right sports and leagues, not the ones that kept losing.
Applied to every product: singles, Sure Odds, category cards and the jackpot ladder.

* Never: simulated, virtual and e-sports copies of real matches (e.g. "Simulated Reality League").
* Not a league with a clearly bad record: at least LEAGUE_MIN_PICKS settled priced picks and a
  return of LEAGUE_MAX_LOSS or worse.
* Not a whole sport with a clearly bad record: at least SPORT_MIN_PICKS and SPORT_MAX_LOSS or worse.
A league or sport without enough settled picks is allowed until its record says otherwise.

The record: settled engine predictions with a SportyBet price stored before kickoff, odds 1.20-2.95
(the range the products use), one per match and market. Basketball handicaps are left out: their
stored prices carried the wrong sign until 12 Nov 2026. Recomputed at most every CACHE_SECONDS.
"""
import re
import time

LEAGUE_MIN_PICKS, LEAGUE_MAX_LOSS = 8, -0.10
SPORT_MIN_PICKS, SPORT_MAX_LOSS = 25, -0.10
RECORD_ODDS = (1.20, 2.95)
CACHE_SECONDS = 3600
SIMULATED_SPORTS = {'efootball', 'ebasketball', 'vfootball', 'vbasketball', 'counterstrike', 'dota2', 'lol', 'valorant'}
SIMULATED = re.compile(r'\b(srl|simulated|virtual|e-?sports?|esoccer|cyber|e-?football|e-?basketball)\b', re.I)

_CACHE = {'at': 0.0, 'record': None}


def simulated(sport, competition, home='', away=''):
    if str(sport or '').lower() in SIMULATED_SPORTS:
        return True
    return bool(SIMULATED.search(' '.join(str(x or '') for x in (competition, home, away))))


def _compute(connection):
    rows = connection.execute(
        """SELECT p.fixture_id, upper(p.market) market, p.settled_outcome, q.decimal_odds, f.sport, f.competition
           FROM sports_ai_engine_predictions p
           JOIN sports_ai_value_opportunities v ON v.prediction_id=p.id
           JOIN sports_ai_odds_snapshots q ON q.id=v.odds_id AND q.bookmaker='SportyBet'
           JOIN sports_ai_fixtures f ON f.id=p.fixture_id
           WHERE p.settled_outcome IN ('won','lost') AND p.created_at < f.starts_at AND q.observed_at < f.starts_at
             AND q.decimal_odds BETWEEN ? AND ?""", RECORD_ODDS,
    ).fetchall()
    picks = {}
    for row in rows:
        market = row['market'] if hasattr(row, 'keys') else row[1]
        if 'SPREAD' in market or 'HANDICAP' in market:
            continue
        picks[(row[0], market)] = (row[2] == 'won', float(row[3]), row[4], row[5] or '')
    sports, leagues = {}, {}
    for won, odds, sport, competition in picks.values():
        gain = (odds - 1) if won else -1.0
        for bucket, key in ((sports, sport), (leagues, (sport, competition))):
            n, total = bucket.get(key, (0, 0.0))
            bucket[key] = (n + 1, total + gain)
    blocked_sports = {s for s, (n, total) in sports.items() if n >= SPORT_MIN_PICKS and total / n <= SPORT_MAX_LOSS}
    blocked_leagues = {k for k, (n, total) in leagues.items() if n >= LEAGUE_MIN_PICKS and total / n <= LEAGUE_MAX_LOSS}
    return {'sports': {s: {'picks': n, 'return': round(t / n, 4)} for s, (n, t) in sports.items()},
            'blocked_sports': blocked_sports, 'blocked_leagues': blocked_leagues, 'picks': len(picks)}


def record(connection, fresh=False):
    if fresh or _CACHE['record'] is None or time.monotonic() - _CACHE['at'] > CACHE_SECONDS:
        try:
            _CACHE['record'] = _compute(connection)
        except Exception:  # never stop a product for want of the record; keep the last one
            if _CACHE['record'] is None:
                _CACHE['record'] = {'sports': {}, 'blocked_sports': set(), 'blocked_leagues': set(), 'picks': 0}
        _CACHE['at'] = time.monotonic()
    return _CACHE['record']


def allowed(connection, sport, competition, home='', away=''):
    if simulated(sport, competition, home, away):
        return False
    rec = record(connection)
    return sport not in rec['blocked_sports'] and (sport, competition or '') not in rec['blocked_leagues']


def filter_legs(connection, legs):
    """Keep selections from allowed sports and leagues (legs carry sport, competition, home, away)."""
    return [leg for leg in legs if allowed(connection, leg.get('sport'), leg.get('competition'),
                                           leg.get('home'), leg.get('away'))]


def clear():
    _CACHE.update(at=0.0, record=None)
