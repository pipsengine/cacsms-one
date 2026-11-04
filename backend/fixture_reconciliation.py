"""Link a bookmaker fixture to an official fixture only when the identity is unique.

Similar team names are not enough. Existing fixture rows are left in place.
"""

import json
import re
import uuid
from datetime import datetime, timedelta, timezone

OFFICIAL_SOURCES = {'openfootball', 'openligadb', 'football-data', 'football_data', 'sportsdataverse'}
CONFIRM_WINDOW = timedelta(minutes=20)


def _parse(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _key(value):
    return re.sub(r'[^a-z0-9]+', '', (value or '').casefold())


def _clock_known(moment):
    return bool(moment) and not (moment.hour == 0 and moment.minute == 0 and moment.second == 0)


def _team_ids(connection, sport, name):
    key = _key(name)
    if not key:
        return set()
    rows = connection.execute(
        """SELECT id FROM sports_ai_teams WHERE sport_code=? AND canonical_key=?
           UNION
           SELECT team_id FROM sports_ai_team_aliases WHERE alias_key=?""",
        (sport, key, key),
    ).fetchall()
    return {row[0] for row in rows}


def _side_match(connection, sport, left, right):
    if _key(left) and _key(left) == _key(right):
        return 'exact'
    left_ids, right_ids = _team_ids(connection, sport, left), _team_ids(connection, sport, right)
    if len(left_ids) == 1 and left_ids == right_ids:
        return 'alias'
    return None


def _competition_compatible(left, right):
    a, b = _key(left), _key(right)
    if not a or not b or a == b:
        return True
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) > 6 and short in long


def _save(connection, source_id, canonical_id, status, confidence, evidence, now):
    connection.execute(
        """INSERT INTO sports_ai_fixture_identity_links
           (id,source_fixture_id,canonical_fixture_id,status,confidence,evidence_json,created_at)
           VALUES(?,?,?,?,?,?,?)
           ON CONFLICT(source_fixture_id) DO UPDATE SET
             canonical_fixture_id=excluded.canonical_fixture_id,
             status=excluded.status, confidence=excluded.confidence,
             evidence_json=excluded.evidence_json, created_at=excluded.created_at""",
        (str(uuid.uuid4()), source_id, canonical_id, status, confidence, json.dumps(evidence), now.isoformat()),
    )


def match_fixture(connection, source_id, now=None):
    """Return a reconciliation decision. This does not update the fixture row."""
    current = now or datetime.now(timezone.utc)
    source = connection.execute('SELECT * FROM sports_ai_fixtures WHERE id=?', (source_id,)).fetchone()
    if not source:
        return {'status': 'unresolved', 'confidence': 0, 'canonical_fixture_id': None}
    kickoff = _parse(source['starts_at'])
    if source['source'] in OFFICIAL_SOURCES:
        result = connection.execute(
            "SELECT 1 FROM sports_ai_results WHERE fixture_id=? AND status='final'", (source_id,),
        ).fetchone()
        decision = {
            'status': 'unresolved',
            'confidence': 0,
            'canonical_fixture_id': None,
            'detail': 'An official final result is already stored.' if result else 'This is already an official provider fixture and no final result is stored.',
        }
        _save(connection, source_id, None, decision['status'], 0, decision, current)
        return decision
    if not kickoff:
        decision = {'status': 'unresolved', 'confidence': 0, 'canonical_fixture_id': None, 'detail': 'The source kickoff is missing.'}
        _save(connection, source_id, None, 'unresolved', 0, decision, current)
        return decision
    window_start = (kickoff - timedelta(hours=36)).isoformat()
    window_end = (kickoff + timedelta(hours=36)).isoformat()
    candidates = connection.execute(
        """SELECT id, source, competition, home, away, starts_at, status
           FROM sports_ai_fixtures
           WHERE sport=? AND id!=? AND starts_at BETWEEN ? AND ?""",
        (source['sport'], source_id, window_start, window_end),
    ).fetchall()
    ranked = []
    for candidate in candidates:
        if candidate['source'] not in OFFICIAL_SOURCES:
            continue
        other = _parse(candidate['starts_at'])
        home = _side_match(connection, source['sport'], source['home'], candidate['home'])
        away = _side_match(connection, source['sport'], source['away'], candidate['away'])
        if not home or not away or not other:
            continue
        method = 'alias' if 'alias' in (home, away) else 'exact'
        delta = abs((other - kickoff).total_seconds())
        clocks = _clock_known(kickoff) and _clock_known(other)
        competition_ok = _competition_compatible(source['competition'], candidate['competition'])
        ranked.append({
            'fixture_id': candidate['id'],
            'source': candidate['source'],
            'home': candidate['home'],
            'away': candidate['away'],
            'starts_at': candidate['starts_at'],
            'method': method,
            'delta_seconds': delta,
            'clocks': clocks,
            'competition_ok': competition_ok,
        })
    confirmable = [
        item for item in ranked
        if item['clocks'] and item['delta_seconds'] <= CONFIRM_WINDOW.total_seconds() and item['competition_ok']
    ]
    if len(confirmable) == 1:
        chosen = confirmable[0]
        confidence = 0.95 if chosen['method'] == 'exact' else 0.90
        decision = {
            'status': 'confirmed', 'confidence': confidence, 'canonical_fixture_id': chosen['fixture_id'],
            'detail': 'One official fixture matches sport, clock time, and verified team identity.',
            'candidates': ranked,
        }
    elif len(confirmable) > 1:
        decision = {
            'status': 'ambiguous', 'confidence': 0, 'canonical_fixture_id': None,
            'detail': 'More than one official fixture matches. No link was stored.',
            'candidates': ranked,
        }
    elif ranked:
        decision = {
            'status': 'review', 'confidence': 0, 'canonical_fixture_id': None,
            'detail': 'A possible official fixture was found, but the kickoff clock or competition is not exact enough to link.',
            'candidates': ranked,
        }
    else:
        decision = {
            'status': 'unresolved', 'confidence': 0, 'canonical_fixture_id': None,
            'detail': 'No official fixture shares the sport, kickoff window, and exact or aliased team identity.',
            'candidates': [],
        }
    _save(connection, source_id, decision['canonical_fixture_id'], decision['status'], decision['confidence'], decision, current)
    return decision


def reconcile_past_live_fixtures(connection, now=None):
    """Reconcile live-observed fixtures whose kickoff has passed and whose status is still scheduled."""
    current = now or datetime.now(timezone.utc)
    rows = connection.execute(
        """SELECT DISTINCT f.id
           FROM sports_ai_engine_predictions p
           JOIN sports_ai_fixtures f ON f.id=p.fixture_id
           WHERE p.observation_class='live_observed' AND f.status='scheduled' AND f.starts_at<=?""",
        (current.isoformat(),),
    ).fetchall()
    counts = {'confirmed': 0, 'ambiguous': 0, 'unresolved': 0, 'review': 0, 'status_updated': 0}
    for row in rows:
        decision = match_fixture(connection, row['id'], current)
        counts[decision['status']] = counts.get(decision['status'], 0) + 1
        if decision['status'] != 'confirmed' or not decision.get('canonical_fixture_id'):
            continue
        official = connection.execute(
            """SELECT r.status, c.status AS fixture_status
               FROM sports_ai_results r
               JOIN sports_ai_fixtures c ON c.id=r.fixture_id
               WHERE r.fixture_id=? AND r.status='final'""",
            (decision['canonical_fixture_id'],),
        ).fetchone()
        if not official:
            continue
        cursor = connection.execute(
            "UPDATE sports_ai_fixtures SET status='finished' WHERE id=? AND status='scheduled'",
            (row['id'],),
        )
        counts['status_updated'] += cursor.rowcount
    counts['examined'] = len(rows)
    counts['places_wager'] = False
    return counts
