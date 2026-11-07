"""Supervised SportyBet quote matching. Nothing here places a wager or invents a price."""

import hashlib
import json
import math
import re
import sqlite3
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone
from functools import lru_cache

from prediction_engine import expected_value, parse_time
from sport_support import GOAL_SPORTS, WINNER_SPORTS, recognised

BOOKMAKER = 'SportyBet'
PROVIDER_ID = 'provider-sportybet'
NAME_STOP = {'fc', 'afc', 'sc', 'cf', 'ac', 'club', 'the'}
PERIOD_MARKERS = (
    '1st half', 'first half', '2nd half', 'second half', 'half time', 'halftime',
    'corner', 'card', 'booking', 'quarter', 'period', 'first quarter',
)
DISPLAY_SPORTS = {sport for sport in (*GOAL_SPORTS, *WINNER_SPORTS, 'basketball') if recognised(sport)}
CONFIRM_MIN = 0.5
PROPOSE_MIN = 0.85


@lru_cache(maxsize=4000)
def normalize_team(value):
    text = unicodedata.normalize('NFKD', value or '')
    text = ''.join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r'[^a-z0-9]+', ' ', text.casefold())
    return ' '.join(token for token in text.split() if token not in NAME_STOP)


def _tokens(value):
    return set(normalize_team(value).split())


def validate_decimal(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 1 or number > 1000:
        return None
    return round(number, 4)


def _quarter_line(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or abs(number * 4 - round(number * 4)) > 1e-8:
        return None
    return number


def _european_handicap(sport, selection, line, market):
    if sport not in ('football', 'frauenfussball') or 'handicap' not in f'{market} {selection}'.casefold():
        return None
    parsed = _quarter_line(line)
    if parsed is None:
        found = re.search(r'(-?\d+(?:\.\d+)?)', f'{market} {selection}')
        parsed = _quarter_line(found.group(1)) if found else 0.0
    if parsed is None:
        parsed = 0.0
    pick = selection.casefold()
    if pick in {'1', 'home', 'home win'} or pick.startswith('home'):
        side = 'HOME'
    elif pick in {'x', 'draw', 'tie'}:
        side = 'DRAW'
    elif pick in {'2', 'away', 'away win'} or pick.startswith('away'):
        side = 'AWAY'
    else:
        return None
    return {
        'supported': True, 'canonical_market': f'EUROPEAN_{side}_{parsed:g}', 'line': parsed,
        'provider_market': 'european_handicap', 'provider_outcome': side,
    }


def map_market(sport, market_label, selection_label, line=None, home=None, away=None):
    """Return a canonical market only when the settlement definition is the same."""
    market = (market_label or '').strip()
    selection = (selection_label or '').strip()
    combined = f'{market} {selection}'.casefold()
    if any(marker in combined for marker in PERIOD_MARKERS):
        return {'supported': False, 'reason': 'A different period or statistic is not the full-match market.'}
    if 'european' in combined or '3-way' in combined or '3 way' in combined:
        mapped = _european_handicap(sport, selection, line, market)
        if mapped:
            return mapped
        return {'supported': False, 'reason': 'A three-way handicap is not the Asian handicap settlement.'}
    parsed_line = _quarter_line(line)
    line_in_text = re.search(r'(-?\d+(?:\.\d+)?)', f'{market} {selection}')
    if parsed_line is None and line_in_text and any(word in combined for word in ('over', 'under', 'handicap', 'spread', 'total')):
        parsed_line = _quarter_line(line_in_text.group(1))
    pick = selection.casefold()
    if sport in GOAL_SPORTS:
        if sport == 'ice_hockey' and any(word in combined for word in ('overtime', 'shootout', 'incl. ot', 'including ot')):
            return {'supported': False, 'reason': 'A winner after overtime or a shootout is not the 60-minute result.'}
        if 'handicap' not in combined and (
            any(name in market.casefold() for name in ('1x2', 'match result', 'full time', 'fulltime'))
            or market.casefold() in {'1x2', 'result'}
        ):
            if pick in {'1', 'home', 'home win'}:
                return {'supported': True, 'canonical_market': 'HOME', 'line': None, 'provider_market': 'h2h', 'provider_outcome': 'home'}
            if pick in {'x', 'draw'}:
                return {'supported': True, 'canonical_market': 'DRAW', 'line': None, 'provider_market': 'h2h', 'provider_outcome': 'draw'}
            if pick in {'2', 'away', 'away win'}:
                return {'supported': True, 'canonical_market': 'AWAY', 'line': None, 'provider_market': 'h2h', 'provider_outcome': 'away'}
        if 'odd' in pick or 'even' in pick:
            if 'odd' in pick and 'even' not in pick:
                return {'supported': True, 'canonical_market': 'ODD_GOALS', 'line': None, 'provider_market': 'odd_even', 'provider_outcome': 'odd'}
            if 'even' in pick:
                return {'supported': True, 'canonical_market': 'EVEN_GOALS', 'line': None, 'provider_market': 'odd_even', 'provider_outcome': 'even'}
        if 'clean sheet' in combined:
            side = 'AWAY' if 'away' in combined and 'home' not in combined else 'HOME'
            return {'supported': True, 'canonical_market': f'CLEAN_SHEET_{side}', 'line': None, 'provider_market': 'clean_sheet', 'provider_outcome': side}
        if 'winning margin' in combined or 'win by' in combined:
            band = '3PLUS' if re.search(r'3\s*\+|3\s*or more|or more', pick) else '1' if re.search(r'\b1\b', pick) else '2' if re.search(r'\b2\b', pick) else None
            side = 'HOME' if pick.startswith('home') or pick in {'1', 'home'} or 'home' in pick else 'AWAY' if 'away' in pick or pick in {'2', 'away'} else None
            if band and side:
                return {'supported': True, 'canonical_market': f'MARGIN_{side}_{band}', 'line': None, 'provider_market': 'margin', 'provider_outcome': f'{side}_{band}'}
        if 'draw no bet' in market.casefold() or market.casefold() in {'dnb'}:
            if pick.startswith('home') or pick in {'1', 'home'}:
                return {'supported': True, 'canonical_market': 'DNB_HOME', 'line': None, 'provider_market': 'draw_no_bet', 'provider_outcome': 'home'}
            if pick.startswith('away') or pick in {'2', 'away'}:
                return {'supported': True, 'canonical_market': 'DNB_AWAY', 'line': None, 'provider_market': 'draw_no_bet', 'provider_outcome': 'away'}
        if 'double chance' in market.casefold():
            token = re.sub(r'[^a-z0-9]', '', pick)
            words = set(re.findall(r'[a-z]+', pick))
            mapped = {'1x': 'DOUBLE_CHANCE_1X', '12': 'DOUBLE_CHANCE_12', 'x2': 'DOUBLE_CHANCE_X2'}.get(token)
            if mapped is None and 'draw' in words and 'home' in words and 'away' not in words:
                mapped = 'DOUBLE_CHANCE_1X'
            elif mapped is None and 'home' in words and 'away' in words:
                mapped = 'DOUBLE_CHANCE_12'
            elif mapped is None and 'draw' in words and 'away' in words:
                mapped = 'DOUBLE_CHANCE_X2'
            if mapped:
                return {'supported': True, 'canonical_market': mapped, 'line': None, 'provider_market': 'double_chance', 'provider_outcome': token or mapped}
        score_handicap = re.fullmatch(r'handicap\s+(\d+)\s*:\s*(\d+)', market.casefold())
        if score_handicap and sport in ('football', 'frauenfussball'):
            handicap = int(score_handicap.group(1)) - int(score_handicap.group(2))
            if pick.startswith('home') or pick in {'1', 'home'}:
                side = 'HOME'
            elif pick.startswith('draw') or pick in {'x', 'draw', 'tie'}:
                side = 'DRAW'
            elif pick.startswith('away') or pick in {'2', 'away'}:
                side = 'AWAY'
            else:
                side = None
            if side:
                return {
                    'supported': True, 'canonical_market': f'EUROPEAN_{side}_{handicap:g}', 'line': handicap,
                    'provider_market': 'european_handicap', 'provider_outcome': side,
                }
        if market.casefold().replace(' ', '') in {'gg/ng', 'ggng'} or 'both teams' in combined or re.search(r'\bbtts\b', combined):
            if pick in {'yes', 'gg', 'btts yes'}:
                return {'supported': True, 'canonical_market': 'BTTS_YES', 'line': None, 'provider_market': 'btts', 'provider_outcome': 'yes'}
            if pick in {'no', 'ng', 'btts no'}:
                return {'supported': True, 'canonical_market': 'BTTS_NO', 'line': None, 'provider_market': 'btts', 'provider_outcome': 'no'}
        totals = re.search(r'\b(over|under)\b', selection.casefold())
        folded_market = market.casefold()
        team_market = any(phrase in folded_market for phrase in (
            'home team', 'away team', 'team total', 'team goals', 'home total', 'away total',
            'home goals', 'away goals',
        ))
        if team_market and totals and parsed_line is not None and 'handicap' not in combined and 'spread' not in combined:
            side = 'HOME' if 'home' in folded_market else 'AWAY' if 'away' in folded_market else None
            if side:
                return {
                    'supported': True,
                    'canonical_market': f'{side}_{totals.group(1).upper()}_{parsed_line:g}',
                    'line': parsed_line,
                    'provider_market': 'team_totals',
                    'provider_outcome': f'{side}_{totals.group(1).upper()}_{parsed_line:g}',
                }
        if totals and parsed_line is not None and 'handicap' not in combined and 'spread' not in combined:
            side = totals.group(1).upper()
            return {
                'supported': True, 'canonical_market': f'{side}_{parsed_line:g}', 'line': parsed_line,
                'provider_market': 'totals', 'provider_outcome': side,
            }
        if 'asian handicap' in combined or re.search(r'\bah\b', combined):
            if parsed_line is None:
                return {'supported': False, 'reason': 'The Asian handicap line is missing or not a supported quarter line.'}
            side = 'HOME' if pick in {'1', 'home'} or 'home' in pick else 'AWAY' if pick in {'2', 'away'} or 'away' in pick else None
            if side:
                signed = parsed_line if side == 'HOME' else -parsed_line if 'home' not in pick else parsed_line
                if pick in {'1', 'home'} or pick.startswith('home'):
                    signed = parsed_line
                elif pick in {'2', 'away'} or pick.startswith('away'):
                    signed = parsed_line
                return {
                    'supported': True,
                    'canonical_market': f'{side}_HANDICAP_{signed:g}',
                    'line': signed,
                    'provider_market': 'spreads',
                    'provider_outcome': f'{side}_HANDICAP_{signed:g}',
                }
    if sport == 'basketball':
        if any(name in market.casefold() for name in ('moneyline', 'money line', 'winner', 'match winner')):
            if pick in {'1', 'home', 'home win'}:
                return {'supported': True, 'canonical_market': 'MONEYLINE_HOME', 'line': None, 'provider_market': 'h2h', 'provider_outcome': 'home'}
            if pick in {'2', 'away', 'away win'}:
                return {'supported': True, 'canonical_market': 'MONEYLINE_AWAY', 'line': None, 'provider_market': 'h2h', 'provider_outcome': 'away'}
            if pick in {'x', 'draw'}:
                return {'supported': False, 'reason': 'Basketball moneyline has no draw selection.'}
        if any(name in combined for name in ('total', 'over', 'under')) and 'spread' not in combined and 'handicap' not in combined:
            totals = re.search(r'\b(over|under)\b', selection.casefold())
            if totals and parsed_line is not None:
                side = totals.group(1).upper()
                return {
                    'supported': True, 'canonical_market': f'{side}_{parsed_line:g}', 'line': parsed_line,
                    'provider_market': 'totals', 'provider_outcome': side,
                }
        if 'spread' in combined or 'handicap' in combined:
            if parsed_line is None:
                return {'supported': False, 'reason': 'The point-spread line is missing or not a supported quarter line.'}
            side = 'HOME' if pick in {'1', 'home'} or pick.startswith('home') else 'AWAY' if pick in {'2', 'away'} or pick.startswith('away') else None
            if side:
                return {
                    'supported': True,
                    'canonical_market': f'{side}_SPREAD_{parsed_line:g}',
                    'line': parsed_line,
                    'provider_market': 'spreads',
                    'provider_outcome': f'{side}_SPREAD_{parsed_line:g}',
                }
    if sport in WINNER_SPORTS:
        folded = market.casefold()
        if any(word in folded for word in ('set', 'game', 'point', 'handicap', 'total', 'over', 'under', 'ace', 'break')):
            return {
                'supported': False,
                'reason': 'Only the match winner is settled from the stored score. This line needs its own result.',
            }
        if any(word in folded for word in ('winner', 'moneyline', 'money line', 'to win', 'match result')) or folded in {'1x2', 'result'}:
            if pick in {'x', 'draw'}:
                return {'supported': False, 'reason': 'This sport has no draw selection.'}
            home_name = str(home or '').casefold()
            away_name = str(away or '').casefold()
            if pick in {'1', 'home', 'home win'} or (home_name and pick == home_name):
                return {'supported': True, 'canonical_market': 'MONEYLINE_HOME', 'line': None, 'provider_market': 'h2h', 'provider_outcome': 'home'}
            if pick in {'2', 'away', 'away win'} or (away_name and pick == away_name):
                return {'supported': True, 'canonical_market': 'MONEYLINE_AWAY', 'line': None, 'provider_market': 'h2h', 'provider_outcome': 'away'}
            return {'supported': False, 'reason': 'The winner selection does not match either named side.'}
    return {'supported': False, 'reason': 'No verified mapping exists for this SportyBet market label.'}


def parse_visible_text(text):
    """Read explicit fixture lines and simple visible prices. Nothing is confirmed."""
    rows = []
    for raw in (text or '').splitlines():
        line = raw.strip()
        if line.startswith('FIXTURE|'):
            parts = line.split('|')
            if len(parts) != 9:
                continue
            _, sport, competition, home, away, kickoff, market, selection, price = parts
            if sport not in DISPLAY_SPORTS or not home.strip() or not away.strip():
                continue
            rows.append({
                'sport': sport,
                'competition': competition.strip(),
                'home': home.strip(),
                'away': away.strip(),
                'starts_at': kickoff.strip() or None,
                'market_label': market.strip(),
                'selection_label': selection.strip(),
                'decimal_odds': validate_decimal(price),
            })
            continue
        match = re.fullmatch(r'(.+?)\s+vs\.?\s+(.+?)\s+(\d+\.\d{2})', line, flags=re.IGNORECASE)
        if not match:
            continue
        rows.append({
            'sport': None,
            'competition': '',
            'home': match.group(1).strip(),
            'away': match.group(2).strip(),
            'starts_at': None,
            'market_label': 'Visible price',
            'selection_label': 'Unspecified',
            'decimal_odds': validate_decimal(match.group(3)),
        })
    return rows


def blocked_page(text):
    folded = (text or '').casefold()
    markers = (
        'captcha', 'verify you are human', 'verify that you are human',
        'unusual traffic', 'access denied', 'just a moment',
    )
    return next((marker for marker in markers if marker in folded), None)


def _same_team(left, right):
    first, second = _tokens(left), _tokens(right)
    if not first or not second:
        return False
    return first == second or first <= second or second <= first


def score_fixture(observation, fixture):
    fixture = dict(fixture)
    names = _same_team(observation.get('home'), fixture['home']) and _same_team(observation.get('away'), fixture['away'])
    swapped = _same_team(observation.get('home'), fixture['away']) and _same_team(observation.get('away'), fixture['home']) and not names
    if not names and not swapped:
        return None
    score = 0.35 if swapped else 0.55
    reasons = ['home and away are reversed'] if swapped else ['home and away names match']
    observed_competition = _tokens(observation.get('competition'))
    fixture_competition = _tokens(fixture.get('competition'))
    if observed_competition and fixture_competition and observed_competition & fixture_competition:
        score += 0.15
        reasons.append('competition tokens overlap')
    observed_kick = observation.get('starts_at')
    try:
        left = parse_time(observed_kick) if observed_kick else None
        right = parse_time(fixture['starts_at'])
    except (TypeError, ValueError):
        left = None
        right = None
    if left and right:
        delta = abs((left - right).total_seconds())
        if left.date() == right.date():
            score += 0.2
            reasons.append('kickoff date matches')
        if delta <= 3 * 3600:
            score += 0.1
            reasons.append('kickoff is within three hours')
    score = round(min(score, 0.49 if swapped else 1), 4)
    return {
        'fixture_id': fixture['id'],
        'sport': fixture['sport'],
        'home': fixture['home'],
        'away': fixture['away'],
        'competition': fixture['competition'],
        'starts_at': fixture['starts_at'],
        'confidence': score,
        'swapped': swapped,
        'reasons': reasons,
    }


def rank_fixtures(connection, observation, limit=5):
    sport = observation.get('sport')
    query = 'SELECT id,sport,competition,home,away,starts_at,status FROM sports_ai_fixtures'
    args = []
    if sport in {'football', 'basketball'}:
        query += ' WHERE sport=?'
        args.append(sport)
    ranked = []
    for row in connection.execute(query, args):
        scored = score_fixture(observation, row)
        if scored:
            ranked.append(scored)
    ranked.sort(key=lambda item: (-item['confidence'], item['starts_at'] or ''))
    best = ranked[0] if ranked else None
    second = ranked[1] if len(ranked) > 1 else None
    ambiguous = bool(best and second and best['confidence'] >= CONFIRM_MIN and best['confidence'] - second['confidence'] < 0.15)
    return {'candidates': ranked[:limit], 'ambiguous': ambiguous, 'best': best}


def _odds_age_limit(connection):
    row = connection.execute("SELECT value_json FROM sports_ai_settings WHERE key='engine'").fetchone()
    if not row:
        return 15
    try:
        return int(json.loads(row['value_json']).get('odds_max_age_minutes', 15))
    except (TypeError, ValueError, json.JSONDecodeError):
        return 15


def _min_edge(connection):
    row = connection.execute("SELECT value_json FROM sports_ai_settings WHERE key='engine'").fetchone()
    if not row:
        return 0.05
    try:
        return float(json.loads(row['value_json']).get('min_edge', 0.05))
    except (TypeError, ValueError, json.JSONDecodeError):
        return 0.05


def quote_age_status(observed_at, now, limit_minutes):
    observed = parse_time(observed_at)
    age = (now - observed).total_seconds() / 60
    if age < -5:
        return 'rejected', 'The observation timestamp is in the future.'
    if age > limit_minutes:
        return 'stale', 'The displayed quote is older than the configured odds age.'
    return 'current', None


def store_observations(connection, session_id, rows, source_url, observed_at):
    saved, duplicates = [], []
    for row in rows:
        observation_id = str(uuid.uuid4())
        try:
            connection.execute(
                """INSERT INTO sports_ai_sportybet_observations
                   (id,session_id,sport,competition,home,away,starts_at,market_label,selection_label,
                    decimal_odds,source_url,observed_at,confirmation_status,line,evidence_json,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'proposed',?,'{}',?)""",
                (
                    observation_id, session_id, row.get('sport'), row.get('competition') or '',
                    row['home'], row['away'], row.get('starts_at'), row['market_label'], row['selection_label'],
                    row.get('decimal_odds'), source_url, observed_at, row.get('line'), observed_at,
                ),
            )
        except sqlite3.IntegrityError:
            duplicates.append(row)
            continue
        saved.append(observation_id)
        if len(saved) % 80 == 0:
            connection.commit()
    return {'saved': saved, 'duplicates': len(duplicates)}


def confirm_observation(connection, observation_id, actor, now):
    row = _observation(connection, observation_id)
    if row['decimal_odds'] is None:
        raise ValueError('A visible decimal price is required before confirmation.')
    if row['confirmation_status'] == 'rejected':
        raise ValueError('This observation was rejected.')
    evidence = json.loads(row['evidence_json'] or '{}')
    evidence['confirmed_by'] = actor
    evidence['confirmed_at'] = now.isoformat()
    connection.execute(
        """UPDATE sports_ai_sportybet_observations
           SET confirmation_status='confirmed', evidence_json=? WHERE id=?""",
        (json.dumps(evidence), observation_id),
    )
    return _observation(connection, observation_id)


def match_observation(connection, observation_id, fixture_id, acknowledge_ambiguity=False):
    row = _observation(connection, observation_id)
    fixture = connection.execute(
        'SELECT id,sport,competition,home,away,starts_at,status FROM sports_ai_fixtures WHERE id=?',
        (fixture_id,),
    ).fetchone()
    if not fixture:
        raise ValueError('That fixture is not in the Sports AI records.')
    if row['sport'] and fixture['sport'] != row['sport']:
        raise ValueError('The SportyBet sport and the stored fixture sport differ.')
    ranked = rank_fixtures(connection, {**dict(row), 'sport': row['sport'] or fixture['sport']})
    chosen = next((item for item in ranked['candidates'] if item['fixture_id'] == fixture_id), None)
    if not chosen:
        chosen = score_fixture(row, fixture)
    if not chosen or chosen['confidence'] < CONFIRM_MIN or chosen['swapped']:
        raise ValueError('The name, competition or kickoff evidence is too weak to associate this fixture.')
    if ranked['ambiguous'] and not acknowledge_ambiguity:
        raise ValueError('More than one stored fixture is close. Choose one explicitly and acknowledge the ambiguity.')
    connection.execute(
        """UPDATE sports_ai_sportybet_observations
           SET fixture_id=?, match_status='confirmed', match_confidence=?, sport=COALESCE(sport, ?)
           WHERE id=?""",
        (fixture_id, chosen['confidence'], fixture['sport'], observation_id),
    )
    return _observation(connection, observation_id)


def validate_observation_market(connection, observation_id):
    row = _observation(connection, observation_id)
    if row['sport'] not in DISPLAY_SPORTS:
        raise ValueError('Set the sport by matching a stored fixture before validating the market.')
    mapped = map_market(row['sport'], row['market_label'], row['selection_label'], row['line'], row['home'], row['away'])
    if not mapped.get('supported'):
        connection.execute(
            """UPDATE sports_ai_sportybet_observations
               SET market_status='unsupported', canonical_market=NULL, rejection_reason=? WHERE id=?""",
            (mapped.get('reason'), observation_id),
        )
        raise ValueError(mapped.get('reason') or 'This market is not supported.')
    connection.execute(
        """UPDATE sports_ai_sportybet_observations
           SET market_status='confirmed', canonical_market=?, line=?, rejection_reason=NULL WHERE id=?""",
        (mapped['canonical_market'], mapped['line'], observation_id),
    )
    return _observation(connection, observation_id)


def import_observation(connection, observation_id, actor, now=None):
    """Copy one confirmed quote into odds history and assess value without inventing edge."""
    now = now or datetime.now(timezone.utc)
    row = _observation(connection, observation_id)
    if row['confirmation_status'] != 'confirmed':
        raise ValueError('Confirm the visible quote before import.')
    if row['match_status'] != 'confirmed' or not row['fixture_id']:
        raise ValueError('Match the quote to one stored fixture before import.')
    if row['market_status'] != 'confirmed' or not row['canonical_market']:
        raise ValueError('Validate the market mapping before import.')
    odds = validate_decimal(row['decimal_odds'])
    if odds is None:
        raise ValueError('The decimal price is missing or invalid.')
    limit = _odds_age_limit(connection)
    age_status, age_reason = quote_age_status(row['observed_at'], now, limit)
    if age_status != 'current':
        connection.execute(
            """UPDATE sports_ai_sportybet_observations
               SET expiry_status=?, import_status='rejected', rejection_reason=? WHERE id=?""",
            ('stale' if age_status == 'stale' else 'expired', age_reason, observation_id),
        )
        raise ValueError(age_reason)
    mapped = map_market(row['sport'], row['market_label'], row['selection_label'], row['line'], row['home'], row['away'])
    if not mapped.get('supported') or mapped['canonical_market'] != row['canonical_market']:
        raise ValueError('The stored market mapping no longer matches the SportyBet label.')
    recent = connection.execute(
        """SELECT decimal_odds, observed_at FROM sports_ai_odds_snapshots
           WHERE fixture_id=? AND bookmaker=? AND market=? AND outcome=? AND IFNULL(line,-999)=IFNULL(?, -999)""",
        (row['fixture_id'], BOOKMAKER, mapped['provider_market'], mapped['provider_outcome'], mapped['line']),
    ).fetchall()
    observed = parse_time(row['observed_at'])
    if any(
        abs(float(item['decimal_odds']) - odds) < 0.0001
        and abs((parse_time(item['observed_at']) - observed).total_seconds()) <= 60
        for item in recent
    ):
        raise ValueError('This quote is already stored for that fixture, market and time.')
    odds_id = str(uuid.uuid4())
    evidence = json.loads(row['evidence_json'] or '{}')
    evidence['imported_by'] = actor
    evidence['imported_at'] = now.isoformat()
    evidence['canonical_market'] = row['canonical_market']
    connection.execute(
        """INSERT INTO sports_ai_odds_snapshots
           (id,fixture_id,provider_id,bookmaker,market,outcome,line,decimal_odds,source_url,observed_at,
            confirmation_status,expiry_status,original_label,evidence_json)
           VALUES(?,?,?,?,?,?,?,?,?,?,'confirmed','current',?,?)""",
        (
            odds_id, row['fixture_id'], PROVIDER_ID, BOOKMAKER, mapped['provider_market'],
            mapped['provider_outcome'], mapped['line'], odds, row['source_url'], row['observed_at'],
            f"{row['market_label']} / {row['selection_label']}", json.dumps(evidence),
        ),
    )
    connection.execute(
        """INSERT INTO sports_ai_odds_history(id,fixture_id,market,decimal_odds,source,observed_at)
           VALUES(?,?,?,?,?,?)""",
        (str(uuid.uuid4()), row['fixture_id'], row['canonical_market'], odds, BOOKMAKER, row['observed_at']),
    )
    value_status, edge = _assess_value(connection, row, odds_id, odds, now)
    connection.execute(
        """UPDATE sports_ai_sportybet_observations
           SET import_status='imported', odds_id=?, value_status=?, expiry_status='current', evidence_json=?
           WHERE id=?""",
        (odds_id, value_status, json.dumps(evidence), observation_id),
    )
    return {'observation': dict(_observation(connection, observation_id)), 'expected_edge': edge, 'value_status': value_status}


def _assess_value(connection, observation, odds_id, odds, now):
    fixture = connection.execute('SELECT * FROM sports_ai_fixtures WHERE id=?', (observation['fixture_id'],)).fetchone()
    prediction = connection.execute(
        """SELECT p.*, m.active, m.status AS model_status, m.metrics_json
           FROM sports_ai_engine_predictions p
           JOIN sports_ai_model_registry m ON m.id=p.model_id
           WHERE p.fixture_id=? AND p.market=? AND p.created_at < ?
           ORDER BY p.created_at DESC LIMIT 1""",
        (observation['fixture_id'], observation['canonical_market'], fixture['starts_at']),
    ).fetchone()
    if not prediction:
        return 'awaiting_model', None
    try:
        kickoff = parse_time(fixture['starts_at'])
    except (TypeError, ValueError):
        return 'rejected', None
    if kickoff <= now:
        _insert_opportunity(connection, prediction['id'], odds_id, None, None, 'expired', 'The fixture has already started.', now)
        return 'expired', None
    issue = connection.execute(
        """SELECT 1 FROM sports_ai_data_quality_issues
           WHERE fixture_id=? AND resolved_at IS NULL AND severity IN ('high','critical') LIMIT 1""",
        (fixture['id'],),
    ).fetchone()
    if issue:
        _insert_opportunity(connection, prediction['id'], odds_id, None, None, 'suspended', 'An open high-severity data-quality issue blocks qualification.', now)
        return 'suspended', None
    production = bool(prediction['active']) and prediction['model_status'] in ('validated', 'active')
    from market_registry import is_active
    calibrated = production and (
        (fixture['sport'] == 'football' and observation['canonical_market'] in {'HOME', 'DRAW', 'AWAY'})
        or (fixture['sport'] == 'basketball' and observation['canonical_market'] in {'MONEYLINE_HOME', 'MONEYLINE_AWAY'})
        or is_active(connection, fixture['sport'], observation['canonical_market'])
    )
    if not production or not calibrated:
        reason = 'The model is not both explicitly activated and validated for this market.'
        _insert_opportunity(connection, prediction['id'], odds_id, None, None, 'awaiting_calibration', reason, now)
        return 'awaiting_calibration', None
    settlement = json.loads(prediction['settlement_json'])
    edge = expected_value(settlement, odds)
    if edge is None:
        _insert_opportunity(connection, prediction['id'], odds_id, None, None, 'rejected', 'Expected value could not be calculated from the stored settlement.', now)
        return 'rejected', None
    minimum = _min_edge(connection)
    if edge < minimum:
        _insert_opportunity(
            connection, prediction['id'], odds_id, 1 / odds, edge, 'rejected',
            'Expected value is below the configured edge. This is not a guaranteed selection.', now,
        )
        return 'rejected', edge
    _insert_opportunity(
        connection, prediction['id'], odds_id, 1 / odds, edge, 'qualified',
        'Confirmed current SportyBet quote and an active validated model meet the qualification checks. Expected value is an estimate, not a guarantee.',
        now,
    )
    return 'qualified', edge


def _insert_opportunity(connection, prediction_id, odds_id, implied, edge, status, reason, now):
    if odds_id is not None:
        existing = connection.execute(
            'SELECT 1 FROM sports_ai_value_opportunities WHERE prediction_id=? AND odds_id=?',
            (prediction_id, odds_id),
        ).fetchone()
        if existing:
            return
    connection.execute(
        """INSERT INTO sports_ai_value_opportunities
           (id,prediction_id,odds_id,implied_probability,expected_edge,status,reason,evaluated_at)
           VALUES(?,?,?,?,?,?,?,?)""",
        (str(uuid.uuid4()), prediction_id, odds_id, implied, edge, status, reason, now.isoformat()),
    )


def list_observations(connection, sport=None, confirmation=None, page=1, page_size=8):
    clauses, args = [], []
    if sport:
        clauses.append('sport=?')
        args.append(sport)
    if confirmation:
        clauses.append('confirmation_status=?')
        args.append(confirmation)
    where = (' WHERE ' + ' AND '.join(clauses)) if clauses else ''
    total = connection.execute(f'SELECT COUNT(*) FROM sports_ai_sportybet_observations{where}', args).fetchone()[0]
    rows = connection.execute(
        f"""SELECT * FROM sports_ai_sportybet_observations{where}
            ORDER BY created_at DESC LIMIT ? OFFSET ?""",
        (*args, page_size, (page - 1) * page_size),
    ).fetchall()
    return {'items': [dict(row) for row in rows], 'total': total, 'page': page, 'page_size': page_size}


def _observation(connection, observation_id):
    row = connection.execute(
        'SELECT * FROM sports_ai_sportybet_observations WHERE id=?', (observation_id,),
    ).fetchone()
    if not row:
        raise LookupError('Observation not found')
    return row


def _rank_loaded(fixtures, observation):
    sport = observation.get('sport')
    ranked = []
    for fixture in fixtures:
        if sport and fixture['sport'] != sport:
            continue
        scored = score_fixture(observation, fixture)
        if scored:
            ranked.append(scored)
    ranked.sort(key=lambda item: (-item['confidence'], item['starts_at'] or ''))
    best = ranked[0] if ranked else None
    second = ranked[1] if len(ranked) > 1 else None
    ambiguous = bool(best and second and best['confidence'] >= CONFIRM_MIN and best['confidence'] - second['confidence'] < 0.15)
    return {'ambiguous': ambiguous, 'best': best}


def _ensure_displayed_fixture(connection, row, now):
    """Store an upcoming match SportyBet displayed when no schedule fixture is the same game."""
    sport = row.get('sport')
    competition = (row.get('competition') or '').strip()
    home = (row.get('home') or '').strip()
    away = (row.get('away') or '').strip()
    starts_at = row.get('starts_at')
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,40}', sport or '') or not competition or not home or not away or not starts_at:
        return None
    try:
        kickoff = parse_time(starts_at)
    except (TypeError, ValueError):
        return None
    if kickoff <= now:
        return None
    identity = '|'.join((
        sport,
        ' '.join(sorted(_tokens(home))),
        ' '.join(sorted(_tokens(away))),
        kickoff.isoformat(),
    ))
    external_id = hashlib.sha256(identity.encode()).hexdigest()[:32]
    existing = connection.execute(
        'SELECT id,sport,competition,home,away,starts_at,status FROM sports_ai_fixtures WHERE source=? AND external_id=?',
        ('sportybet-displayed', external_id),
    ).fetchone()
    if existing:
        return dict(existing)
    fixture_id = str(uuid.uuid4())
    connection.execute(
        """INSERT INTO sports_ai_fixtures
           (id,external_id,source,sport,competition,home,away,starts_at,status,statistics_json,odds_json,observed_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (fixture_id, external_id, 'sportybet-displayed', sport, competition[:200], home[:200], away[:200],
         kickoff.isoformat(), 'scheduled', '{}', '{}', now.isoformat()),
    )
    return {
        'id': fixture_id, 'sport': sport, 'competition': competition, 'home': home, 'away': away,
        'starts_at': kickoff.isoformat(), 'status': 'scheduled',
    }


def accept_displayed_quotes(connection, observation_ids, actor, now=None):
    """Import structured SportyBet prices when one stored fixture is clearly the same match."""
    now = now or datetime.now(timezone.utc)
    fixtures = [dict(row) for row in connection.execute(
        'SELECT id,sport,competition,home,away,starts_at,status FROM sports_ai_fixtures',
    )]
    by_token = {}
    for fixture in fixtures:
        for token in _tokens(fixture['home']) | _tokens(fixture['away']):
            by_token.setdefault(token, []).append(fixture)
    imported, skipped = 0, 0
    for index, observation_id in enumerate(observation_ids, start=1):
        row = dict(_observation(connection, observation_id))
        pool = {}
        for token in _tokens(row['home']) | _tokens(row['away']):
            for fixture in by_token.get(token, ()):
                pool[fixture['id']] = fixture
        ranked = _rank_loaded(pool.values(), row)
        best = ranked.get('best')
        names_match = bool(best) and not best.get('swapped') and best['confidence'] >= 0.55
        if ranked.get('ambiguous') or (best and best.get('swapped')) or (names_match and best['confidence'] < 0.7):
            skipped += 1
            if index % 15 == 0:
                connection.commit()
            continue
        if not names_match:
            created = _ensure_displayed_fixture(connection, row, now)
            if not created:
                skipped += 1
                if index % 15 == 0:
                    connection.commit()
                continue
            for token in _tokens(created['home']) | _tokens(created['away']):
                by_token.setdefault(token, []).append(created)
            best = {
                'fixture_id': created['id'], 'sport': created['sport'], 'confidence': 1.0,
                'starts_at': created['starts_at'], 'swapped': False,
            }
        try:
            kickoff = parse_time(best['starts_at'])
            observed = parse_time(row['starts_at']) if row.get('starts_at') else None
        except (TypeError, ValueError):
            kickoff = observed = None
        if not kickoff or not observed or abs((kickoff - observed).total_seconds()) > 6 * 3600:
            skipped += 1
            if index % 15 == 0:
                connection.commit()
            continue
        try:
            connection.execute(
                """UPDATE sports_ai_sportybet_observations
                   SET fixture_id=?, match_status='confirmed', match_confidence=?, sport=COALESCE(sport, ?)
                   WHERE id=?""",
                (best['fixture_id'], best['confidence'], best['sport'], observation_id),
            )
            mapped = map_market(row['sport'] or best['sport'], row['market_label'], row['selection_label'], row['line'], row['home'], row['away'])
            if not mapped.get('supported'):
                _store_unmapped_price(connection, observation_id, actor, now, mapped.get('reason'))
            else:
                validate_observation_market(connection, observation_id)
                confirm_observation(connection, observation_id, actor, now)
                import_observation(connection, observation_id, actor, now)
            imported += 1
        except (ValueError, LookupError, sqlite3.IntegrityError):
            skipped += 1
        if index % 15 == 0:
            connection.commit()
    return {'imported': imported, 'skipped': skipped}


def _store_unmapped_price(connection, observation_id, actor, now, reason):
    """Keep a price SportyBet displayed even when no model can settle that market yet.

    The price is stored. No probability and no qualified opportunity are created.
    """
    row = _observation(connection, observation_id)
    odds = validate_decimal(row['decimal_odds'])
    if odds is None or not row['fixture_id']:
        raise ValueError(reason or 'The displayed price cannot be stored.')
    label = f"{row['market_label']} / {row['selection_label']}"[:180]
    recent = connection.execute(
        """SELECT decimal_odds, observed_at FROM sports_ai_odds_snapshots
           WHERE fixture_id=? AND bookmaker=? AND market=? AND outcome=? AND IFNULL(line,-999)=IFNULL(?, -999)""",
        (row['fixture_id'], BOOKMAKER, row['market_label'], row['selection_label'], row['line']),
    ).fetchall()
    observed = parse_time(row['observed_at'])
    if any(
        abs(float(item['decimal_odds']) - odds) < 0.0001
        and abs((parse_time(item['observed_at']) - observed).total_seconds()) <= 60
        for item in recent
    ):
        return
    odds_id = str(uuid.uuid4())
    evidence = json.loads(row['evidence_json'] or '{}')
    evidence.update({'imported_by': actor, 'imported_at': now.isoformat(), 'settlement': 'not_modelled'})
    connection.execute(
        """INSERT INTO sports_ai_odds_snapshots
           (id,fixture_id,provider_id,bookmaker,market,outcome,line,decimal_odds,source_url,observed_at,
            confirmation_status,expiry_status,original_label,evidence_json)
           VALUES(?,?,?,?,?,?,?,?,?,?,'confirmed','current',?,?)""",
        (
            odds_id, row['fixture_id'], PROVIDER_ID, BOOKMAKER, row['market_label'], row['selection_label'],
            row['line'], odds, row['source_url'], row['observed_at'], label, json.dumps(evidence),
        ),
    )
    connection.execute(
        """UPDATE sports_ai_sportybet_observations
           SET confirmation_status='confirmed', market_status='unsupported', canonical_market=NULL,
               rejection_reason=?, import_status='imported', odds_id=?, evidence_json=?
           WHERE id=?""",
        (reason or 'SportyBet displayed this price. No settlement model is stored for it.', odds_id, json.dumps(evidence), observation_id),
    )


def unattended_status(connection, now=None):
    now = now or datetime.now(timezone.utc)
    row = connection.execute(
        """SELECT evidence, expires_at, status FROM sports_ai_collection_authorizations
           WHERE bookmaker=? AND scope='unattended_extraction' AND status='authorized'
           ORDER BY created_at DESC LIMIT 1""",
        (BOOKMAKER,),
    ).fetchone()
    authorized = False
    if row and row['evidence'].strip():
        if not row['expires_at']:
            authorized = True
        else:
            try:
                authorized = parse_time(row['expires_at']) > now
            except (TypeError, ValueError):
                authorized = False
    recent_cutoff = (now - timedelta(hours=6)).isoformat()
    recent = connection.execute(
        """SELECT COUNT(*) FROM sports_ai_odds_snapshots
           WHERE bookmaker=? AND observed_at>=?""",
        (BOOKMAKER, recent_cutoff),
    ).fetchone()[0]
    operational = bool(authorized and recent)
    if operational:
        reason = 'Automatic collection is storing the prices SportyBet displays. No wager is placed.'
    elif authorized:
        reason = 'Automatic collection is authorized and has not stored a SportyBet price in the last six hours.'
    else:
        reason = 'Automatic SportyBet collection has not been authorized. No wager is placed.'
    return {'authorized': authorized, 'operational': operational, 'reason': reason}
