"""Link official final results stored on a duplicate fixture record to the fixture a prediction used.

Fixtures arrive from several sources (openfootball, sportsdataverse, SportyBet,
BetExplorer). Results mostly arrive from BetExplorer under its own record and its
own team spellings ("Phoenix Suns" for "Suns"). Without a link, about two thirds of
played, predicted fixtures had no result, so tickets could not settle and models
could not be scored or learn.

A result is copied only when exactly one candidate qualifies:
* same sport, kickoff within one day,
* every remaining name token of the shorter team name appears in the longer one,
  for both teams, in the same home/away orientation ("Manchester City" never
  matches "Manchester United"),
* no candidate matches with home and away swapped.
The copy keeps the source result's time and names its source record.
"""
import re
import unicodedata
import uuid
from datetime import datetime, timezone

STOP = {'fc', 'sc', 'cf', 'afc', 'ac', 'club', 'de', 'sk', 'fk', 'bc', 'kk', 'bk', 'cd', 'sv', 'the', 'and', 'women', 'w'}


def tokens(name):
    text = unicodedata.normalize('NFKD', name or '').encode('ascii', 'ignore').decode().lower()
    words = [word for word in re.findall(r'[a-z0-9]+', text) if word not in STOP]
    return {word for word in words if len(word) > 2 or word.isdigit()}


def same_team(a, b):
    left, right = tokens(a), tokens(b)
    if not left or not right:
        return False
    short, long_ = (left, right) if len(left) <= len(right) else (right, left)
    return short <= long_


def _parse(value):
    try:
        moment = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def link_results(connection, now=None, limit=400, days=45):
    """Copy unambiguous final results onto played fixtures that have predictions but no result."""
    current = now or datetime.now(timezone.utc)
    missing = connection.execute(
        """SELECT f.id, f.sport, f.home, f.away, f.starts_at FROM sports_ai_fixtures f
           WHERE julianday(f.starts_at) < julianday(?) - (3.0/24) AND julianday(f.starts_at) > julianday(?) - ?
             AND EXISTS (SELECT 1 FROM sports_ai_engine_predictions p WHERE p.fixture_id=f.id)
             AND NOT EXISTS (SELECT 1 FROM sports_ai_results r WHERE r.fixture_id=f.id)
           ORDER BY f.starts_at DESC LIMIT ?""",
        (current.isoformat(), current.isoformat(), days, limit),
    ).fetchall()
    linked, ambiguous, unmatched = 0, 0, 0
    for fixture in missing:
        day = str(fixture['starts_at'])[:10]
        candidates = connection.execute(
            """SELECT f.id, f.home, f.away, r.home_score, r.away_score, r.settled_at, r.source
               FROM sports_ai_fixtures f JOIN sports_ai_results r ON r.fixture_id=f.id AND r.status='final'
               WHERE f.sport=? AND f.id<>? AND substr(f.starts_at,1,10) BETWEEN date(?,'-1 day') AND date(?,'+1 day')""",
            (fixture['sport'], fixture['id'], day, day),
        ).fetchall()
        straight = [c for c in candidates if same_team(fixture['home'], c['home']) and same_team(fixture['away'], c['away'])]
        swapped = [c for c in candidates if same_team(fixture['home'], c['away']) and same_team(fixture['away'], c['home'])]
        if len(straight) != 1 or swapped:
            if straight or swapped:
                ambiguous += 1
            else:
                unmatched += 1
            continue
        source = straight[0]
        kickoff = _parse(fixture['starts_at'])
        settled = _parse(source['settled_at'])
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
        linked += 1
    return {'checked': len(missing), 'linked': linked, 'ambiguous': ambiguous, 'unmatched': unmatched}
