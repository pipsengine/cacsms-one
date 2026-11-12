"""Link official final results stored on a duplicate fixture record to the fixture a prediction or bet used.

Fixtures arrive from several sources (openfootball, openligadb, sportsdataverse, SportyBet,
BetExplorer). Results mostly arrive from BetExplorer under its own record and its own team
spellings ("Nottingham" for "Nottingham Forest", "Manchester City" for "Man City", "Hertha
Berlin" for "Hertha BSC"). Without a link, played fixtures had no result, so tickets and bets
could not settle and models could not be scored or learn.

A result is copied only when exactly one candidate qualifies, in two passes:

1. Names: same sport family, kickoff within one day, and for both teams in the same home/away
   orientation every name word of the shorter name matches a word of the longer one (equal,
   a known short form such as "Utd"/"United", or a prefix of at least three letters such as
   "Man"/"Manchester"). "Manchester City" never matches "Manchester United".
2. Kickoff: only when pass 1 finds nothing. Kickoff within 20 minutes and, for both teams in
   the same orientation, a shared distinctive word (four letters or more, not a generic word
   such as "Real", "City" or "United").

In both passes a youth, women's or reserve side never matches a senior side ("Holstein Kiel"
is not "Holstein Kiel U19"), and a candidate that also matches with home and away swapped
makes the fixture ambiguous, so nothing is copied. The copy keeps the source result's time and
names its source record.
"""
import re
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone

STOP = {'fc', 'sc', 'cf', 'afc', 'ac', 'club', 'de', 'sk', 'fk', 'bc', 'kk', 'bk', 'cd', 'sv', 'the', 'and', 'women', 'w'}
# Short forms bookmakers use, expanded before comparing.
ALIASES = {'utd': 'united', 'man': 'manchester', 'spurs': 'tottenham', 'wolves': 'wolverhampton',
           'nottm': 'nottingham', 'bayer': 'leverkusen', 'inter': 'internazionale', 'psg': 'paris',
           'gladbach': 'monchengladbach', 'atl': 'atletico', 'dep': 'deportivo', 'sp': 'sporting',
           # English city names for clubs listed under their own spelling elsewhere.
           'nuremberg': 'nurnberg', 'munich': 'munchen', 'cologne': 'koln', 'turin': 'torino', 'lisbon': 'lisboa'}
# Words shared by many unrelated clubs; never enough on their own in the kickoff pass.
GENERIC = {'real', 'city', 'united', 'sporting', 'athletic', 'atletico', 'club', 'union', 'dynamo', 'dinamo',
           'olympic', 'olympique', 'racing', 'deportivo', 'santa', 'saint', 'san', 'town', 'county', 'rovers',
           'wanderers', 'athletico', 'football', 'futbol', 'calcio', 'sports', 'team', 'academy', 'internacional',
           'national', 'stade', 'borussia', 'sparta', 'slavia', 'spartak', 'lokomotiv', 'zenit', 'young', 'boys'}
# "srl"/"virtual"/"esports": simulated or e-sports copies of real fixtures, with their own scores.
SIDE_MARKER = re.compile(r'\b(u\d{2}|w|women|womens|ladies|fem|femenino|feminin|ii|iii|b|reserves?|res|youth|junior|juniors|academy'
                         r'|srl|virtual|esports|cyber|simulated)\b')
SPORT_FAMILY = {'frauenfussball': 'football'}
KICKOFF_PASS_MINUTES = 20
# A result from another fixture record is only the same game when its kickoff is within this window
# (covers providers storing the kickoff in a different time zone). 24 h linked back-to-back games:
# 12 Nov 2026, Trail Blazers v Thunder of 12 Nov got the 11 Nov game's 123-103 (real score 118-108).
LINK_WINDOW_HOURS = 12
DATE_ONLY_WINDOW_HOURS = 24
# ...and a result recorded this long before the fixture's kickoff belongs to an earlier game.
EARLY_RESULT_HOURS = 3


def _plain(name):
    text = (name or '').lower().translate(str.maketrans({'ø': 'o', 'æ': 'ae', 'ß': 'ss', 'ł': 'l', 'đ': 'd'}))
    text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode()
    # German/Nordic spellings written out ("Lillestroem", "Muenchen") read as the plain letter.
    return re.sub(r'(?<=[a-z])(oe|ue|ae)(?=[a-z])', lambda m: m.group(1)[0], text)


def tokens(name):
    words = [ALIASES.get(word, word) for word in re.findall(r'[a-z0-9]+', _plain(name))]
    return {word for word in words if word not in STOP and (len(word) > 2 or word.isdigit())}


def side_markers(name):
    """Youth, women's and reserve markers: two names only match when these agree."""
    found = set()
    for marker in SIDE_MARKER.findall(_plain(name)):
        found.add({'w': 'women', 'womens': 'women', 'ladies': 'women', 'fem': 'women', 'femenino': 'women',
                   'feminin': 'women', 'reserve': 'reserves', 'res': 'reserves', 'b': 'reserves', 'ii': 'reserves',
                   'junior': 'youth', 'juniors': 'youth', 'academy': 'youth'}.get(marker, marker))
    return found


def _word_match(a, b):
    if a == b:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= 3 and not short.isdigit() and long_.startswith(short)


def same_team(a, b):
    """Every word of the shorter name matches a word of the longer one."""
    if side_markers(a) != side_markers(b):
        return False
    left, right = tokens(a), tokens(b)
    if not left or not right:
        return False
    short, long_ = (left, right) if len(left) <= len(right) else (right, left)
    return all(any(_word_match(word, other) for other in long_) for word in short)


def related_team(a, b):
    """A shared distinctive word (kickoff pass only)."""
    if side_markers(a) != side_markers(b):
        return False
    left = {w for w in tokens(a) if len(w) >= 4 and w not in GENERIC and not w.isdigit()}
    right = {w for w in tokens(b) if len(w) >= 4 and w not in GENERIC and not w.isdigit()}
    return any(_word_match(x, y) for x in left for y in right)


def _parse(value):
    try:
        moment = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _one(matches):
    """Several records of the same game (from different sources) count as one when they agree on the score."""
    return matches[0] if len({(m['home_score'], m['away_score']) for m in matches}) == 1 else None


def find_match(fixture, candidates):
    """(candidate, pass) for the one result that belongs to this fixture, or (None, reason)."""
    straight = [c for c in candidates if same_team(fixture['home'], c['home']) and same_team(fixture['away'], c['away'])]
    swapped = [c for c in candidates if same_team(fixture['home'], c['away']) and same_team(fixture['away'], c['home'])]
    if straight or swapped:
        chosen = _one(straight) if straight and not swapped else None
        return (chosen, 'names') if chosen else (None, 'ambiguous')
    kickoff = _parse(fixture['starts_at'])
    if not kickoff:
        return None, 'unmatched'
    near = [c for c in candidates if _parse(c['starts_at'])
            and abs((_parse(c['starts_at']) - kickoff).total_seconds()) <= KICKOFF_PASS_MINUTES * 60]
    straight = [c for c in near if related_team(fixture['home'], c['home']) and related_team(fixture['away'], c['away'])]
    swapped = [c for c in near if related_team(fixture['home'], c['away']) and related_team(fixture['away'], c['home'])]
    if straight or swapped:
        chosen = _one(straight) if straight and not swapped else None
        return (chosen, 'kickoff') if chosen else (None, 'ambiguous')
    return None, 'unmatched'


def _missing(connection, current, days, limit):
    """Played fixtures without a result: those bet on first, then those with a prediction."""
    return connection.execute(
        """SELECT f.id, f.sport, f.home, f.away, f.starts_at,
                  (EXISTS (SELECT 1 FROM sports_ai_accumulator_legs l WHERE l.fixture_id=f.id)
                   OR EXISTS (SELECT 1 FROM sports_ai_single_bets s WHERE s.fixture_id=f.id)
                   OR EXISTS (SELECT 1 FROM sports_ai_single_bet_legs sl WHERE sl.fixture_id=f.id)) AS bet_on
           FROM sports_ai_fixtures f
           WHERE julianday(f.starts_at) < julianday(?) AND julianday(f.starts_at) > julianday(?)
             AND (EXISTS (SELECT 1 FROM sports_ai_engine_predictions p WHERE p.fixture_id=f.id)
                  OR EXISTS (SELECT 1 FROM sports_ai_accumulator_legs l WHERE l.fixture_id=f.id)
                  OR EXISTS (SELECT 1 FROM sports_ai_single_bets s WHERE s.fixture_id=f.id)
                  OR EXISTS (SELECT 1 FROM sports_ai_single_bet_legs sl WHERE sl.fixture_id=f.id))
             AND NOT EXISTS (SELECT 1 FROM sports_ai_results r WHERE r.fixture_id=f.id)
           ORDER BY bet_on DESC, f.starts_at DESC LIMIT ?""",
        (_iso(current, hours=-3), _iso(current, days=-days), limit),
    ).fetchall()


def _iso(moment, days=0, hours=0):
    return (moment + timedelta(days=days, hours=hours)).isoformat()


def _family(sport):
    return SPORT_FAMILY.get(sport, sport)


SEASON = re.compile(r'\b(20\d{2})(?:\s*[/-]\s*(\d{2,4}))?\b')


def stale_season(competition, kickoff):
    """A record whose competition names a season that ended before the kickoff year (an old
    openfootball season re-dated into the future, e.g. "UEFA Champions League 2021/22" on a
    2026 date) is history, never the result of a current match."""
    found = SEASON.findall(competition or '')
    if not found or not kickoff:
        return False
    start, end = found[-1]
    last = int(start) if not end else (int(end) if len(end) == 4 else int(start[:2] + end))
    return last < kickoff.year - 1 or (last < kickoff.year and kickoff.month > 7)


def _result_index(connection, missing):
    """Every stored final result near the missing fixtures, loaded once and bucketed by sport and day."""
    kickoffs = [k for k in (_parse(f['starts_at']) for f in missing) if k]
    index = {}
    if not kickoffs:
        return index
    for row in connection.execute(
        """SELECT f.id, f.sport, f.competition, f.home, f.away, f.starts_at, r.home_score, r.away_score, r.settled_at, r.source
           FROM sports_ai_results r JOIN sports_ai_fixtures f ON f.id=r.fixture_id
           WHERE r.status='final' AND julianday(f.starts_at) BETWEEN julianday(?) AND julianday(?)""",
        (_iso(min(kickoffs), days=-2), _iso(max(kickoffs), days=2)),
    ):
        kickoff = _parse(row['starts_at'])
        if kickoff and not stale_season(row['competition'], kickoff):
            index.setdefault((_family(row['sport']), kickoff.date()), []).append(row)
    return index


def _candidates(index, fixture):
    kickoff = _parse(fixture['starts_at'])
    if not kickoff:
        return []
    rows = []
    for offset in (-1, 0, 1):
        rows += index.get((_family(fixture['sport']), (kickoff + timedelta(days=offset)).date()), [])
    return [r for r in rows if r['id'] != fixture['id']
            and abs((_parse(r['starts_at']) - kickoff).total_seconds())
            <= max(_window_hours(kickoff), _window_hours(_parse(r['starts_at']))) * 3600]


def _window_hours(kickoff):
    # Some feeds store only the date (00:00): the real kickoff is any time that day, so the old 24 h.
    return DATE_ONLY_WINDOW_HOURS if (kickoff.hour, kickoff.minute, kickoff.second) == (0, 0, 0) else LINK_WINDOW_HOURS


def link_results(connection, now=None, limit=1500, days=45):
    """Copy unambiguous final results onto played fixtures that were predicted or bet on."""
    current = now or datetime.now(timezone.utc)
    missing = _missing(connection, current, days, limit)
    index = _result_index(connection, missing)
    counts = {'checked': len(missing), 'linked': 0, 'linked_by_kickoff': 0, 'ambiguous': 0, 'unmatched': 0}
    for fixture in missing:
        source, how = find_match(fixture, _candidates(index, fixture))
        if source is None:
            counts[how] += 1
            continue
        kickoff = _parse(fixture['starts_at'])
        settled = _parse(source['settled_at'])
        if kickoff and settled and _window_hours(kickoff) == LINK_WINDOW_HOURS                 and settled < kickoff - timedelta(hours=EARLY_RESULT_HOURS):
            counts['ambiguous'] += 1  # recorded before this game started: an earlier meeting of the teams
            continue
        if kickoff and (not settled or settled < kickoff):
            settled = current  # a result is only usable for scoring when stored after kickoff
        connection.execute(
            """INSERT OR IGNORE INTO sports_ai_results(id,fixture_id,home_score,away_score,status,source,settled_at)
               VALUES(?,?,?,?, 'final', ?, ?)""",
            (str(uuid.uuid4()), fixture['id'], source['home_score'], source['away_score'],
             f"linked:{source['source']}:{source['id']}"[:200], (settled or current).isoformat()),
        )
        connection.execute(
            "UPDATE sports_ai_fixtures SET status='finished' WHERE id=? AND status IN ('scheduled','not_started','live')",
            (fixture['id'],),
        )
        counts['linked'] += 1
        counts['linked_by_kickoff'] += how == 'kickoff'
    return counts
