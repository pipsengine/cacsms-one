"""Rate-limited collectors for permitted public sports APIs and datasets."""

import csv
import io
import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit


MAX_RESPONSE_BYTES = 15 * 1024 * 1024
USER_AGENT = 'CACSMS-Sports-AI/1.0 (permitted public data client)'


class CollectionError(RuntimeError):
    pass


def _request(url, headers=None):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname:
        raise CollectionError('Provider URLs must use HTTPS')
    request_headers = {'User-Agent': USER_AGENT, 'Accept': 'application/json'}
    request_headers.update(headers or {})
    for attempt in range(3):
        request = urllib.request.Request(url, headers=request_headers)
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise CollectionError('Provider response exceeded the size limit')
                return body, dict(response.headers.items())
        except urllib.error.HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise CollectionError(f'Provider returned HTTP {error.code}') from error
        except (urllib.error.URLError, TimeoutError) as error:
            if attempt == 2:
                raise CollectionError(f'Provider request failed: {error.reason if hasattr(error, "reason") else error}') from error
        time.sleep(2 ** attempt)
    raise CollectionError('Provider request failed after retries')


def _as_utc(value):
    if not value:
        raise CollectionError('Provider match has no kickoff timestamp')
    parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _football_record(external_id, competition, home, away, starts_at, source, observed_at,
                     home_score=None, away_score=None):
    if not all(isinstance(value, str) and value.strip()
               for value in (str(external_id), competition, home, away)):
        raise CollectionError('Provider fixture is missing its identity or teams')
    record = {
        'external_id': str(external_id),
        'sport': 'football',
        'competition': competition.strip(),
        'home': home.strip(),
        'away': away.strip(),
        'starts_at': _as_utc(starts_at),
        'source': source,
        'observed_at': observed_at,
        'statistics': {},
        'odds': {},
    }
    if home_score is not None and away_score is not None:
        record['result'] = (int(home_score), int(away_score))
    return record


def _openligadb(config, source_key):
    league = str(config.get('league', ''))
    season = int(config.get('season', 0))
    if not league or len(league) > 20 or not 2000 <= season <= datetime.now().year + 1:
        raise CollectionError('Configure a valid OpenLigaDB league and season')
    url = f'https://api.openligadb.de/getmatchdata/{urllib.parse.quote(league)}/{season}'
    body, _ = _request(url)
    payload = json.loads(body)
    if not isinstance(payload, list):
        raise CollectionError('OpenLigaDB response was not a match list')
    observed = datetime.now(timezone.utc).isoformat()
    fixtures = []
    for match in payload:
        home = (match.get('team1') or {}).get('teamName')
        away = (match.get('team2') or {}).get('teamName')
        results = match.get('matchResults') or []
        final = next((item for item in reversed(results)
                      if 'end' in str(item.get('resultName', '')).lower()), None)
        if final is None and results:
            final = results[-1]
        fixtures.append(_football_record(
            match.get('matchID'), match.get('leagueName') or league, home, away,
            match.get('matchDateTimeUTC'), source_key, observed,
            final.get('pointsTeam1') if final else None,
            final.get('pointsTeam2') if final else None,
        ))
    return fixtures, url


def _openfootball(config, source_key):
    url = str(config.get('dataset_url', ''))
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname != 'raw.githubusercontent.com'
            or not parsed.path.startswith('/openfootball/football.json/')):
        raise CollectionError(
            'OpenFootball URL must be a reviewed raw.githubusercontent.com/openfootball/football.json dataset URL'
        )
    body, _ = _request(url, {'Accept': 'application/json'})
    payload = json.loads(body)
    matches = payload.get('matches') if isinstance(payload, dict) else None
    if not isinstance(matches, list):
        raise CollectionError('OpenFootball JSON did not contain a matches list')
    observed = datetime.now(timezone.utc).isoformat()
    competition = payload.get('name') or parsed.path.rsplit('/', 1)[-1]
    fixtures = []
    for index, match in enumerate(matches):
        score = (match.get('score') or {}).get('ft')
        if not isinstance(score, list) or len(score) < 2:
            score = (None, None)
        fixtures.append(_football_record(
            match.get('id') or f"{match.get('date')}:{match.get('team1')}:{match.get('team2')}:{index}",
            competition, match.get('team1'), match.get('team2'),
            match.get('date'), source_key, observed, score[0], score[1],
        ))
    return fixtures, url


def collect_fixtures(provider, connection):
    """Fetch one provider page and idempotently persist its fixtures and final scores."""
    config = json.loads(provider['config_json'])
    if provider['adapter'] == 'openligadb':
        records, source_url = _openligadb(config, provider['provider_key'])
    elif provider['adapter'] == 'openfootball':
        records, source_url = _openfootball(config, provider['provider_key'])
    elif provider['adapter'] == 'the_odds_api':
        return collect_odds(provider, connection, config)
    else:
        raise CollectionError('This source uses a manual import workflow, not an automatic collector')
    for record in records:
        existing = connection.execute(
            'SELECT id FROM sports_ai_fixtures WHERE source=? AND external_id=?',
            (record['source'], record['external_id']),
        ).fetchone()
        fixture_id = existing['id'] if existing else str(uuid.uuid4())
        connection.execute(
            """INSERT INTO sports_ai_fixtures
               (id,external_id,source,sport,competition,home,away,starts_at,status,
                statistics_json,odds_json,observed_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(source,external_id) DO UPDATE SET
               competition=excluded.competition,home=excluded.home,away=excluded.away,
               starts_at=excluded.starts_at,status=excluded.status,
               observed_at=excluded.observed_at,updated_at=CURRENT_TIMESTAMP""",
            (fixture_id,record['external_id'],record['source'],record['sport'],
             record['competition'],record['home'],record['away'],record['starts_at'],
             'finished' if 'result' in record else 'scheduled',
             json.dumps(record['statistics']),json.dumps(record['odds']),record['observed_at']),
        )
        if 'result' in record:
            connection.execute(
                """INSERT INTO sports_ai_results
                   (id,fixture_id,home_score,away_score,source,status,settled_at)
                   VALUES(?,?,?,?,?,'final',?)
                   ON CONFLICT(fixture_id) DO UPDATE SET
                   home_score=excluded.home_score,away_score=excluded.away_score,
                   source=excluded.source,status='final',settled_at=excluded.settled_at""",
                (str(uuid.uuid4()),fixture_id,*record['result'],record['source'],record['observed_at']),
            )
    return {'fixtures': len(records), 'source_url': source_url}


def collect_odds(provider, connection, config):
    key = os.environ.get('SPORTS_ODDS_API_KEY')
    if not key:
        raise CollectionError('Set SPORTS_ODDS_API_KEY to enable this optional odds provider')
    sport_keys = config.get('sport_keys', [])
    if not isinstance(sport_keys, list) or not sport_keys:
        raise CollectionError('Configure the provider sport_keys')
    count = 0
    base = 'https://api.the-odds-api.com/v4'
    for sport_key in sport_keys[:4]:
        if not isinstance(sport_key, str) or not sport_key.replace('_', '').isalnum():
            raise CollectionError('Invalid odds provider sport key')
        query = urllib.parse.urlencode({
            'apiKey': key,
            'regions': config.get('regions', 'uk'),
            'markets': config.get('markets', 'h2h'),
            'oddsFormat': 'decimal',
        })
        url = f'{base}/sports/{urllib.parse.quote(sport_key)}/odds?{query}'
        body, _ = _request(url)
        events = json.loads(body)
        if not isinstance(events, list):
            raise CollectionError('Odds provider response was not an event list')
        for event in events:
            kickoff = parse_provider_time = _as_utc(event.get('commence_time'))
            fixture = connection.execute(
                """SELECT * FROM sports_ai_fixtures WHERE sport=?
                   AND lower(home)=lower(?) AND lower(away)=lower(?)""",
                ('basketball' if 'basketball' in sport_key else 'football',
                 event.get('home_team'),event.get('away_team')),
            ).fetchall()
            fixture = next((row for row in fixture if abs(
                (datetime.fromisoformat(row['starts_at'].replace('Z','+00:00'))
                 - datetime.fromisoformat(kickoff.replace('Z','+00:00'))).total_seconds()
            ) <= 6 * 3600), None)
            if not fixture:
                continue
            for bookmaker in event.get('bookmakers') or []:
                for market in bookmaker.get('markets') or []:
                    for outcome in market.get('outcomes') or []:
                        price = outcome.get('price')
                        if not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 1:
                            continue
                        connection.execute(
                            """INSERT OR IGNORE INTO sports_ai_odds_snapshots
                               (id,fixture_id,provider_id,bookmaker,market,outcome,line,
                                decimal_odds,source_url,observed_at)
                               VALUES(?,?,?,?,?,?,?,?,?,?)""",
                            (str(uuid.uuid4()),fixture['id'],provider['id'],
                             bookmaker.get('title') or bookmaker.get('key') or 'Unknown',
                             market.get('key','unknown'),str(outcome.get('name','')),
                             outcome.get('point'),float(price),
                             f'{base}/sports/{urllib.parse.quote(sport_key)}/odds',
                             market.get('last_update') or bookmaker.get('last_update')
                             or datetime.now(timezone.utc).isoformat()),
                        )
                        count += 1
    return {'odds_quotes': count, 'source_url': base}


def import_basketball_rows(connection, provider, rows):
    if provider['adapter'] != 'csv_import' or provider['sport'] != 'basketball':
        raise CollectionError('This provider does not accept basketball result imports')
    if not isinstance(rows, list) or not rows or len(rows) > 5000:
        raise CollectionError('Provide between 1 and 5000 normalized basketball result rows')
    observed = datetime.now(timezone.utc).isoformat()
    imported = 0
    for row in rows:
        try:
            external_id = str(row['id'])
            date = _as_utc(row['date'])
            home, away = str(row['home']).strip(), str(row['away']).strip()
            home_score, away_score = int(row['home_score']), int(row['away_score'])
            competition = str(row.get('competition') or 'Basketball dataset').strip()
            settlement_value = row.get('settled_at')
            settlement = observed
            if settlement_value:
                parsed_settlement = datetime.fromisoformat(
                    str(settlement_value).replace('Z','+00:00')
                )
                if parsed_settlement.tzinfo is None:
                    raise ValueError('settled_at must include a timezone')
                settlement = parsed_settlement.astimezone(timezone.utc).isoformat()
        except KeyError as error:
            raise CollectionError('Rows require id, date, home, away, home_score and away_score') from error
        except (TypeError, ValueError) as error:
            raise CollectionError(f'Basketball dataset row is invalid: {error}') from error
        if (not external_id or not home or not away or home_score < 0 or away_score < 0
                or home_score > 300 or away_score > 300):
            raise CollectionError('Basketball result values are outside the accepted range')
        if datetime.fromisoformat(settlement.replace('Z','+00:00')) < datetime.fromisoformat(date):
            raise CollectionError('Basketball result settlement time precedes match kickoff')
        existing = connection.execute(
            'SELECT id FROM sports_ai_fixtures WHERE source=? AND external_id=?',
            (provider['provider_key'],external_id),
        ).fetchone()
        fixture_id = existing['id'] if existing else str(uuid.uuid4())
        connection.execute(
            """INSERT INTO sports_ai_fixtures
               (id,external_id,source,sport,competition,home,away,starts_at,status,
                statistics_json,odds_json,observed_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(source,external_id) DO UPDATE SET
               home=excluded.home,away=excluded.away,starts_at=excluded.starts_at,
               status='finished',observed_at=excluded.observed_at,updated_at=CURRENT_TIMESTAMP""",
            (fixture_id,external_id,provider['provider_key'],'basketball',competition,
             home,away,date,'finished','{}','{}',observed),
        )
        connection.execute(
            """INSERT INTO sports_ai_results
               (id,fixture_id,home_score,away_score,source,status,settled_at)
               VALUES(?,?,?,?,?,'final',?)
               ON CONFLICT(fixture_id) DO UPDATE SET
               home_score=excluded.home_score,away_score=excluded.away_score,
               source=excluded.source,status='final',settled_at=excluded.settled_at""",
            (str(uuid.uuid4()),fixture_id,home_score,away_score,
             provider['provider_key'],settlement),
        )
        imported += 1
    return imported
