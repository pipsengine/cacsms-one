"""Provider registry, bounded API collection, normalization and job persistence."""

import csv
import io
import json
import math
import os
import re
import unicodedata
import urllib.parse
import uuid
import time
from datetime import datetime, timedelta, timezone

from acquisition import CollectionError, _request, _as_utc
from db import connect

MAX_PAGES = 10
MAX_RECORDS = 1000
PAGE_SIZE = 100
API_KEYS = {
    'football_data': ('SPORTS_FOOTBALL_DATA_API_KEY',),
    'balldontlie': ('SPORTS_BALLDONTLIE_API_KEY',),
    'the_odds_api': ('SPORTS_ODDS_API_KEY',),
}
ADAPTER_INFO = {
    'football_data': {
        'sport': 'football', 'terms_url': 'https://www.football-data.org/coverage',
        'credential_env': 'SPORTS_FOOTBALL_DATA_API_KEY',
    },
    'openligadb': {
        'sport': 'football', 'terms_url': 'https://www.openligadb.de/Documentation',
        'credential_env': '',
    },
    'openfootball': {
        'sport': 'football', 'terms_url': 'https://github.com/openfootball/football.json',
        'credential_env': '',
    },
    'balldontlie': {
        'sport': 'basketball', 'terms_url': 'https://docs.balldontlie.io/',
        'credential_env': 'SPORTS_BALLDONTLIE_API_KEY',
    },
    'sportsdataverse_csv': {
        'sport': 'basketball', 'terms_url': 'https://sportsdataverse.org/',
        'credential_env': '',
    },
    'the_odds_api': {
        'sport': 'both', 'terms_url': 'https://the-odds-api.com/#get-access',
        'credential_env': 'SPORTS_ODDS_API_KEY',
    },
    'csv_import': {
        'sport': 'basketball', 'terms_url': 'https://sportsdataverse.org/',
        'credential_env': '',
    },
    'browser': {
        'sport': 'both', 'terms_url': 'https://www.betexplorer.com/',
        'credential_env': '',
    },
}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def validate_public_config(config):
    sensitive_markers=('apikey','token','secret','password','credential','authorization')

    def visit(value):
        if isinstance(value,dict):
            for key,child in value.items():
                normalized=re.sub(r'[^a-z]','',str(key).casefold())
                if any(marker in normalized for marker in sensitive_markers):
                    raise CollectionError(
                        'Provider secrets must be set as environment variables, not stored in configuration'
                    )
                visit(child)
        elif isinstance(value,list):
            for child in value:
                visit(child)
        elif isinstance(value,str):
            parsed=urllib.parse.urlsplit(value)
            if any(
                any(marker in re.sub(r'[^a-z]','',key.casefold())
                    for marker in sensitive_markers)
                for key,_ in urllib.parse.parse_qsl(parsed.query,keep_blank_values=True)
            ):
                raise CollectionError(
                    'Provider URLs must not contain credential query parameters'
                )

    visit(config)


def _json_body(url, headers=None):
    body, response_headers = _request(url, headers)
    try:
        return json.loads(body), response_headers
    except (TypeError, json.JSONDecodeError) as error:
        raise CollectionError('Provider returned malformed JSON') from error


def _safe_key(adapter):
    names = API_KEYS.get(adapter, ())
    for name in names:
        value = os.environ.get(name)
        if value:
            return name, value
    if names:
        raise CollectionError(f'Set {names[0]} in the backend environment to use this provider')
    return '', ''


def _check_rate_limit(provider):
    last_attempt = provider.get('last_attempt_at')
    if not last_attempt:
        return
    try:
        previous = datetime.fromisoformat(last_attempt.replace('Z','+00:00'))
    except (ValueError, AttributeError):
        return
    if previous.tzinfo is None:
        previous = previous.replace(tzinfo=timezone.utc)
    config = json.loads(provider['config_json'])
    per_minute = min(120,max(1,int(config.get('rate_limit_per_minute',10))))
    remaining = 60 / per_minute - (datetime.now(timezone.utc)-previous).total_seconds()
    if remaining > 0:
        raise CollectionError(
            f'Provider rate limit: wait {math.ceil(remaining)} seconds before the next request'
        )


def _record(source_id, external_id, sport, competition, home, away, starts_at,
            observed_at, source_url, status='scheduled', home_score=None,
            away_score=None, settled_at=None, statistics=None):
    if not all(isinstance(value, str) and value.strip()
               for value in (str(external_id), competition, home, away)):
        raise CollectionError('Provider match is missing an ID, competition or team name')
    if home == away:
        raise CollectionError('Provider match has the same home and away team')
    if (home_score is None) != (away_score is None):
        raise CollectionError('Provider match has an incomplete final score')
    if home_score is not None and (
        not 0 <= int(home_score) <= 300 or not 0 <= int(away_score) <= 300
    ):
        raise CollectionError('Provider score is outside the accepted range')
    if home_score is not None:
        kickoff_time=datetime.fromisoformat(_as_utc(starts_at))
        settlement_time=datetime.fromisoformat(_as_utc(settled_at or observed_at))
        if settlement_time < kickoff_time:
            raise CollectionError('Provider result settlement time precedes match kickoff')
    time_precision = 'date' if re.fullmatch(r'\d{4}-\d{2}-\d{2}',str(starts_at)) else 'instant'
    result = {
        'external_id': str(external_id)[:200],
        'sport': sport,
        'competition': competition.strip()[:200],
        'home': home.strip()[:200],
        'away': away.strip()[:200],
        'starts_at': _as_utc(starts_at),
        'time_precision':time_precision,
        'observed_at': _as_utc(observed_at),
        'source_url': source_url.split('?', 1)[0],
        'status': 'finished' if status == 'FINISHED' or (
            home_score is not None and away_score is not None
        ) else 'scheduled',
        'statistics': statistics or {},
    }
    if home_score is not None and away_score is not None:
        result['home_score'] = int(home_score)
        result['away_score'] = int(away_score)
        result['settled_at'] = _as_utc(settled_at or observed_at)
    return result


def _openligadb(provider, config, limit=MAX_RECORDS):
    leagues = config.get('leagues') or [config.get('league', '')]
    seasons = config.get('seasons') or [config.get('season', datetime.now().year)]
    if isinstance(leagues, str):
        leagues = [leagues]
    if isinstance(seasons, (int, str)):
        seasons = [seasons]
    fixtures, urls, errors = [], [], []
    requests = [(str(league), int(season)) for league in leagues for season in seasons]
    for league, season in requests:
        try:
            batch = _openligadb_season(provider, league, season, limit)
        except CollectionError as error:
            errors.append(str(error))
            if len(requests) == 1:
                raise
            continue
        fixtures.extend(batch['fixtures'])
        urls.append(batch['source_url'])
    if not urls:
        raise CollectionError(errors[0] if errors else 'Configure a valid OpenLigaDB league and season')
    return {'fixtures': fixtures, 'standings': [], 'source_url': urls[0], 'pages': len(urls)}


def _openligadb_season(provider, league, season, limit):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,20}', league) or not 2000 <= season <= datetime.now().year + 1:
        raise CollectionError('Configure a valid OpenLigaDB league and season')
    url = f'https://api.openligadb.de/getmatchdata/{urllib.parse.quote(league)}/{season}'
    payload, _ = _json_body(url)
    if not isinstance(payload, list):
        raise CollectionError('OpenLigaDB response was not a match list')
    now = utc_now()
    fixtures = []
    for item in payload[:limit]:
        final = next(
            (row for row in reversed(item.get('matchResults') or [])
             if row.get('resultTypeKind') == 'After90Minutes'
             or 'end' in str(row.get('resultName', '')).lower()),
            None,
        )
        observed = item.get('lastUpdateDateTime') or now
        fixtures.append(_record(
            provider['provider_key'], item.get('matchID'),
            'football', item.get('leagueName') or league,
            (item.get('team1') or {}).get('teamName'),
            (item.get('team2') or {}).get('teamName'),
            item.get('matchDateTimeUTC') or item.get('matchDateTime'),
            observed, url,
            'FINISHED' if item.get('matchIsFinished') else 'SCHEDULED',
            final.get('pointsTeam1') if final else None,
            final.get('pointsTeam2') if final else None,
            observed,
        ))
    return {'fixtures': fixtures, 'source_url': url}


_TXT_MONTHS = {
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
    'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
}
_TXT_DATE = re.compile(
    r'^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+([A-Za-z]{3})\s+(\d{1,2})(?:\s+(20\d{2}))?$'
)
_TXT_VERSUS = re.compile(
    r'^(?:(\d{1,2}:\d{2})\s+)?(.+?)\s+v\s+(.+?)\s+(\d{1,2})-(\d{1,2})\b'
)
_TXT_INLINE = re.compile(
    r'^([A-Za-z].*?)\s+(\d{1,2})-(\d{1,2})\s+([A-Za-z].+?)(?:\s+@.*)?$'
)
_TXT_COUNTRY = re.compile(r'\s*\([A-Z]{3}\)')


def _openfootball_kind(url):
    parsed = urllib.parse.urlsplit(url)
    path = parsed.path
    if parsed.scheme != 'https' or parsed.hostname != 'raw.githubusercontent.com':
        return ''
    if path.startswith('/openfootball/football.json/') and path.endswith('.json'):
        return 'json'
    if path.startswith('/openfootball/champions-league/') and re.search(r'/(?:cl|el|conf)\.txt$', path):
        return 'txt'
    if (path.startswith('/openfootball/internationals/') and '/uefa_nations_league/' in path
            and path.endswith('.txt')):
        return 'txt'
    return ''


def _txt_team(value):
    text = _TXT_COUNTRY.sub('', value)
    return re.sub(r'\s+', ' ', text).strip(' .')


def _football_txt_fixtures(provider, url, text, limit=MAX_RECORDS):
    """Public-domain Football.TXT results. Extra-time and penalty lines are skipped."""
    competition = ''
    year = None
    previous_month = None
    current_date = None
    fixtures = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or line.startswith('▪') or line.startswith('('):
            continue
        if line.startswith('='):
            competition = line.lstrip('=').strip()
            found = re.search(r'(20\d{2})', competition)
            if found:
                year = int(found.group(1))
            continue
        dated = _TXT_DATE.match(line)
        if dated:
            month = _TXT_MONTHS.get(dated.group(1).casefold())
            if month is None or year is None:
                current_date = None
                continue
            if dated.group(3):
                year = int(dated.group(3))
            elif previous_month is not None and month < previous_month:
                year += 1
            previous_month = month
            current_date = f'{year:04d}-{month:02d}-{int(dated.group(2)):02d}'
            continue
        if current_date is None or 'pen.' in line or 'a.e.t.' in line or 'awarded' in line.casefold():
            continue
        versus = _TXT_VERSUS.match(line) if ' v ' in line else None
        inline = None if versus else _TXT_INLINE.match(line)
        if versus:
            clock, home, away, home_score, away_score = versus.groups()
        elif inline:
            home, home_score, away_score, away = inline.groups()
            clock = None
        else:
            continue
        home, away = _txt_team(home), _txt_team(away)
        if not home or not away or home == away:
            continue
        kickoff = f'{current_date}T{clock}:00+00:00' if clock else current_date
        if clock:
            settled = (datetime.fromisoformat(kickoff) + timedelta(hours=2)).isoformat()
        else:
            settled = kickoff
        external_id = f'{competition}|{current_date}|{home}|{away}'
        try:
            fixtures.append(_record(
                provider['provider_key'], external_id[:200], 'football', competition or 'Football',
                home, away, kickoff, settled, url,
                home_score=int(home_score), away_score=int(away_score), settled_at=settled,
            ))
        except CollectionError:
            continue
        if len(fixtures) >= limit:
            break
    if not fixtures:
        raise CollectionError('Football.TXT file did not contain completed matches')
    return fixtures


def _openfootball(provider, config, limit=MAX_RECORDS):
    urls = []
    if config.get('dataset_url'):
        urls.append(str(config['dataset_url']))
    extra = config.get('dataset_urls') or []
    if isinstance(extra, str):
        extra = [extra]
    for item in extra:
        if item and str(item) not in urls:
            urls.append(str(item))
    if not urls:
        raise CollectionError(
            'OpenFootball URL must be a reviewed raw.githubusercontent.com/openfootball/football.json dataset URL'
        )
    fixtures, used, errors = [], [], []
    for url in urls:
        try:
            fixtures.extend(_openfootball_url(provider, url, limit))
            used.append(url)
        except CollectionError as error:
            errors.append(str(error))
            if len(urls) == 1:
                raise
    if not used:
        raise CollectionError(errors[0] if errors else 'OpenFootball returned no matches')
    return {'fixtures': fixtures, 'standings': [], 'source_url': used[0], 'pages': len(used)}


def _openfootball_url(provider, url, limit):
    kind = _openfootball_kind(url)
    if not kind:
        raise CollectionError(
            'OpenFootball URL must be a reviewed football.json, champions-league, or nations-league dataset'
        )
    if kind == 'txt':
        body, _headers = _request(url, {'Accept': 'text/plain'})
        try:
            text = body.decode('utf-8-sig')
        except UnicodeDecodeError as error:
            raise CollectionError('Football.TXT dataset is not valid UTF-8') from error
        return _football_txt_fixtures(provider, url, text, limit)
    payload, _ = _json_body(url, {'Accept': 'application/json'})
    if not isinstance(payload, dict) or not isinstance(payload.get('matches'), list):
        raise CollectionError('OpenFootball JSON must contain a matches list')
    now = utc_now()
    fixtures = []
    for index, item in enumerate(payload['matches'][:limit]):
        score_data = item.get('score') or {}
        full_time = (
            score_data.get('ft') if isinstance(score_data,dict)
            else score_data if isinstance(score_data,list) else None
        )
        scores = full_time if isinstance(full_time, list) and len(full_time) >= 2 else [None, None]
        fixtures.append(_record(
            provider['provider_key'],
            item.get('id') or f"{item.get('date')}:{item.get('team1')}:{item.get('team2')}:{index}",
            'football', payload.get('name') or parsed.path.rsplit('/', 1)[-1],
            item.get('team1'), item.get('team2'), item.get('date'), now, url,
            home_score=scores[0], away_score=scores[1],
            settled_at=item.get('date') if scores[0] is not None and scores[1] is not None else None,
        ))
    return fixtures


def _football_data(provider, config, limit=MAX_RECORDS):
    _, token = _safe_key(provider['adapter'])
    competition = str(config.get('competition', 'PL')).upper()
    if not re.fullmatch(r'[A-Z0-9_-]{2,20}', competition):
        raise CollectionError('Configure a valid football-data.org competition code')
    season = int(config.get('season', datetime.now().year))
    if not 2000 <= season <= datetime.now().year + 1:
        raise CollectionError('Configure a season between 2000 and next year')
    now = datetime.now(timezone.utc)
    date_from = str(config.get('date_from') or (now - timedelta(days=30)).date())
    date_to = str(config.get('date_to') or (now + timedelta(days=180)).date())
    for date_value in (date_from, date_to):
        try:
            datetime.strptime(date_value, '%Y-%m-%d')
        except ValueError as error:
            raise CollectionError('date_from and date_to must use YYYY-MM-DD') from error
    base = f'https://api.football-data.org/v4/competitions/{urllib.parse.quote(competition)}/matches'
    fixtures, pages, offset = [], 0, 0
    state = json.loads(provider.get('cursor_json') or '{}')
    offset = int(state.get('next_offset',0))
    while pages < MAX_PAGES and len(fixtures) < limit:
        if pages:
            time.sleep(max(0.5,60/min(120,max(1,int(config.get('rate_limit_per_minute',10))))))
        query = urllib.parse.urlencode({
            'season': season, 'dateFrom': date_from, 'dateTo': date_to,
            'limit': min(PAGE_SIZE, limit - len(fixtures)), 'offset': offset,
        })
        url = f'{base}?{query}'
        payload, _ = _json_body(url, {'X-Auth-Token': token, 'Accept': 'application/json'})
        records = payload.get('matches') if isinstance(payload, dict) else None
        if not isinstance(records, list):
            raise CollectionError('football-data.org response did not contain matches')
        observed = utc_now()
        for item in records:
            score = item.get('score') or {}
            full = score.get('fullTime') or {}
            competition_name = (item.get('competition') or {}).get('name') or competition
            fixtures.append(_record(
                provider['provider_key'], item.get('id'), 'football',
                competition_name,
                (item.get('homeTeam') or {}).get('name'),
                (item.get('awayTeam') or {}).get('name'),
                item.get('utcDate'), item.get('lastUpdated') or observed, url,
                item.get('status') or 'SCHEDULED',
                full.get('home'), full.get('away'),
                item.get('lastUpdated') or observed,
            ))
        pages += 1
        count = (payload.get('resultSet') or {}).get('count', len(records))
        offset += len(records)
        if not records or len(records) < PAGE_SIZE or offset >= count:
            break
    standings, standings_url = [], f'https://api.football-data.org/v4/competitions/{urllib.parse.quote(competition)}/standings?season={season}'
    table_payload, _ = _json_body(
        standings_url, {'X-Auth-Token': token, 'Accept': 'application/json'},
    )
    observed = utc_now()
    for table in table_payload.get('standings', []) if isinstance(table_payload, dict) else []:
        for row in table.get('table', []):
            team = (row.get('team') or {}).get('name')
            if not team:
                continue
            standings.append({
                'competition': (table_payload.get('competition') or {}).get('name') or competition,
                'season': str(season), 'team': team,
                'position': row.get('position'), 'played': row.get('playedGames'),
                'won': row.get('won'), 'drawn': row.get('draw'),
                'lost': row.get('lost'), 'points': row.get('points'),
                'goal_difference': row.get('goalDifference'),
                'details': row, 'source_url': standings_url,
                'observed_at': observed,
            })
    return {'fixtures': fixtures, 'standings': standings,
            'source_url': base, 'pages': pages + 1,
            'next_offset':offset if offset < count else None}


def _balldontlie(provider, config, limit=MAX_RECORDS):
    _, token = _safe_key(provider['adapter'])
    season = int(config.get('season', datetime.now().year))
    if not 1946 <= season <= datetime.now().year + 1:
        raise CollectionError('Configure a valid NBA season')
    base = 'https://api.balldontlie.io/nba/v1/games'
    fixtures, pages = [], 0
    state = json.loads(provider.get('cursor_json') or '{}')
    cursor = state.get('next_cursor')
    while pages < MAX_PAGES and len(fixtures) < limit:
        if pages:
            time.sleep(max(0.5, 60 / min(120, max(1, int(config.get('rate_limit_per_minute', 10))))))
        query = [('seasons[]', str(season)), ('per_page', str(min(PAGE_SIZE, limit - len(fixtures))))]
        if cursor:
            query.append(('cursor', str(cursor)))
        url = f'{base}?{urllib.parse.urlencode(query)}'
        payload, _ = _json_body(url, {'Authorization': token, 'Accept': 'application/json'})
        records = payload.get('data') if isinstance(payload, dict) else None
        if not isinstance(records, list):
            raise CollectionError('BALLDONTLIE response did not contain a data list')
        observed = utc_now()
        for game in records:
            home_score, away_score = game.get('home_team_score'), game.get('visitor_team_score')
            status = 'FINISHED' if home_score is not None and away_score is not None else 'SCHEDULED'
            fixtures.append(_record(
                provider['provider_key'], game.get('id'), 'basketball',
                game.get('season') and f'NBA {game.get("season")}' or 'NBA',
                (game.get('home_team') or {}).get('full_name'),
                (game.get('visitor_team') or {}).get('full_name'),
                game.get('date'), game.get('updated_at') or observed, url,
                status, home_score, away_score, game.get('updated_at') or observed,
                {
                    'home_team_id': (game.get('home_team') or {}).get('id'),
                    'away_team_id': (game.get('visitor_team') or {}).get('id'),
                    'postseason': game.get('postseason'),
                    'period': game.get('period'),
                },
            ))
        pages += 1
        cursor = (payload.get('meta') or {}).get('next_cursor')
        if not records or not cursor:
            break
    return {'fixtures': fixtures, 'standings': [], 'source_url': base, 'pages': pages,
            'next_cursor':cursor}


def _csv_value(row, *names):
    for name in names:
        value = row.get(name)
        if value is not None and str(value).strip() not in ('', 'NA', 'null', 'None'):
            return str(value).strip()
    return ''


def _optional_score(value):
    if value is None or str(value).strip() in ('', 'NA', 'null', 'None'):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise CollectionError('Basketball scores in the dataset must be integers') from error
    if not number.is_integer() or not 0 <= number <= 300:
        raise CollectionError('Basketball scores must be whole numbers between 0 and 300')
    return int(number)


def _schedule_final(normalized, home_score, away_score):
    if 'status_type_completed' in normalized or 'status_type_state' in normalized:
        completed = str(normalized.get('status_type_completed') or '').strip().lower() == 'true'
        state = str(normalized.get('status_type_state') or '').strip().lower()
        return completed or state == 'post'
    return home_score is not None and away_score is not None


def _prefer_upcoming(parsed_rows, limit):
    now = datetime.now(timezone.utc)
    upcoming, earlier = [], []
    for item in parsed_rows:
        kickoff = str(item[0]).strip()
        try:
            when = datetime.fromisoformat(_as_utc(kickoff))
        except (ValueError, CollectionError):
            continue
        date_only = bool(re.fullmatch(r'\d{4}-\d{2}-\d{2}', kickoff))
        ahead = when.date() >= now.date() if date_only else when >= now
        if item[4] is None and ahead:
            upcoming.append(item)
        else:
            earlier.append(item)
    upcoming.sort(key=lambda item: item[0])
    earlier.sort(key=lambda item: item[0])
    if len(upcoming) >= limit:
        return upcoming[:limit]
    return earlier[-(limit - len(upcoming)):] + upcoming


def _sportsdataverse_csv(provider, config, limit=MAX_RECORDS):
    url = str(config.get('dataset_url', ''))
    parsed = urllib.parse.urlsplit(url)
    allowed = {'raw.githubusercontent.com', 'github.com', 'githubusercontent.com'}
    if (parsed.scheme != 'https' or parsed.hostname not in allowed
            or not parsed.path.lower().endswith('.csv')):
        raise CollectionError(
            'Configure an HTTPS CSV dataset URL hosted by an approved SportsDataverse GitHub repository'
        )
    body, _ = _request(url, {'Accept': 'text/csv, text/plain'})
    try:
        reader = csv.DictReader(io.StringIO(body.decode('utf-8-sig')))
        rows = list(reader)
    except (UnicodeDecodeError, csv.Error) as error:
        raise CollectionError('SportsDataverse dataset is not a valid UTF-8 CSV') from error
    fields = {name.strip().lower() for name in (reader.fieldnames or []) if name}
    if not fields or not ({'home', 'home_name', 'home_display_name'} & fields) or not (
            {'away', 'away_name', 'away_display_name'} & fields):
        raise CollectionError(
            'SportsDataverse CSV needs team columns such as home/away or home_name/away_name'
        )
    observed = utc_now()
    parsed_rows = []
    for row in rows:
        normalized = {str(key).strip().lower(): value for key, value in row.items() if key}
        home = _csv_value(normalized, 'home', 'home_name', 'home_display_name')
        away = _csv_value(normalized, 'away', 'away_name', 'away_display_name')
        kickoff = _csv_value(normalized, 'game_date_time', 'date', 'game_date', 'start_date')
        external_id = _csv_value(normalized, 'id', 'game_id')
        if not home or not away or not kickoff or not external_id or home == away:
            continue
        home_score = _optional_score(normalized.get('home_score'))
        away_score = _optional_score(normalized.get('away_score'))
        if (home_score is None) != (away_score is None):
            continue
        if not _schedule_final(normalized, home_score, away_score):
            home_score = away_score = None
        parsed_rows.append((kickoff, external_id, home, away, home_score, away_score, normalized))
    row_limit = 1600 if limit >= MAX_RECORDS else limit
    chosen = _prefer_upcoming(parsed_rows, row_limit)
    fixtures = []
    for kickoff, external_id, home, away, home_score, away_score, normalized in chosen:
        season = _csv_value(normalized, 'season')
        fixtures.append(_record(
            provider['provider_key'], external_id, 'basketball',
            normalized.get('competition') or (f'NBA {season}' if season else 'NBA'),
            home, away, kickoff, observed, url,
            'FINISHED' if home_score is not None else 'SCHEDULED',
            home_score, away_score,
            _csv_value(normalized, 'settled_at') or (kickoff if home_score is not None else None),
        ))
    return {'fixtures': fixtures[:row_limit], 'standings': [], 'source_url': url, 'pages': 1}


def _normalize(value):
    text = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode().casefold()
    return re.sub(r'[^a-z0-9]+', '', text)


def _fixture_for(connection, provider, record):
    mapped = connection.execute(
        'SELECT fixture_id FROM sports_ai_fixture_source_map WHERE provider_id=? AND external_id=?',
        (provider['id'], record['external_id']),
    ).fetchone()
    if mapped:
        return mapped['fixture_id']
    existing = connection.execute(
        'SELECT id FROM sports_ai_fixtures WHERE source=? AND external_id=?',
        (provider['provider_key'], record['external_id']),
    ).fetchone()
    if existing:
        return existing['id']
    teams = {_normalize(record['home']), _normalize(record['away'])}
    candidates = connection.execute(
        'SELECT id,home,away,starts_at FROM sports_ai_fixtures WHERE sport=?',
        (record['sport'],),
    ).fetchall()
    event_time = datetime.fromisoformat(record['starts_at'].replace('Z', '+00:00'))
    date_precision_fixtures = {
        row['fixture_id'] for row in connection.execute(
            """SELECT DISTINCT fixture_id FROM sports_ai_fixture_source_map
               WHERE time_precision='date'"""
        ).fetchall()
    }
    matching = []
    for candidate in candidates:
        candidate_teams = {_normalize(candidate['home']), _normalize(candidate['away'])}
        if candidate_teams != teams:
            continue
        try:
            candidate_time = datetime.fromisoformat(candidate['starts_at'].replace('Z', '+00:00'))
        except ValueError:
            continue
        is_date_precision = candidate['id'] in date_precision_fixtures
        tolerance = 36 if record.get('time_precision') == 'date' or is_date_precision else 3
        difference = abs((event_time - candidate_time).total_seconds())
        if difference <= tolerance * 3600:
            matching.append((difference,candidate['id']))
    if matching:
        matching.sort()
        if len(matching) == 1 or matching[0][0] < matching[1][0]:
            return matching[0][1]
    return str(uuid.uuid4())


def _persist(connection, provider, batch):
    seen = saved = 0
    now = utc_now()
    for record in batch['fixtures']:
        seen += 1
        fixture_id = _fixture_for(connection, provider, record)
        previous = connection.execute(
            'SELECT id FROM sports_ai_fixtures WHERE id=?', (fixture_id,),
        ).fetchone()
        if previous:
            mapped_elsewhere=connection.execute(
                """SELECT 1 FROM sports_ai_fixture_source_map
                   WHERE fixture_id=? AND provider_id!=? LIMIT 1""",
                (fixture_id,provider['id']),
            ).fetchone()
            connection.execute(
                """UPDATE sports_ai_fixtures SET
                   competition=CASE WHEN ? THEN competition ELSE ? END,
                   home=CASE WHEN ? THEN home ELSE ? END,
                   away=CASE WHEN ? THEN away ELSE ? END,
                   starts_at=CASE WHEN ?='date' THEN starts_at ELSE ? END,
                   status=?,observed_at=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (int(bool(mapped_elsewhere)),record['competition'],
                 int(bool(mapped_elsewhere)),record['home'],
                 int(bool(mapped_elsewhere)),record['away'],
                 record.get('time_precision','instant'),record['starts_at'],
                 record['status'],record['observed_at'],fixture_id),
            )
        else:
            connection.execute(
                """INSERT INTO sports_ai_fixtures
                   (id,external_id,source,sport,competition,home,away,starts_at,status,
                    statistics_json,odds_json,observed_at)
                   VALUES(?,?,?,?,?,?,?,?,?,'{}','{}',?)""",
                (fixture_id,record['external_id'],provider['provider_key'],record['sport'],
                 record['competition'],record['home'],record['away'],record['starts_at'],
                 record['status'],record['observed_at']),
            )
        connection.execute(
            """INSERT INTO sports_ai_fixture_source_map
               (id,provider_id,external_id,fixture_id,source_updated_at,observed_at,time_precision,source_url)
               VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(provider_id,external_id) DO UPDATE SET
               fixture_id=excluded.fixture_id,source_updated_at=excluded.source_updated_at,
               observed_at=excluded.observed_at,time_precision=excluded.time_precision,
               source_url=excluded.source_url""",
            (str(uuid.uuid4()),provider['id'],record['external_id'],fixture_id,
             record['observed_at'],now,record.get('time_precision','instant'),
             record.get('source_url', '')),
        )
        if record.get('home_score') is not None and record.get('away_score') is not None:
            connection.execute(
                """INSERT INTO sports_ai_results
                   (id,fixture_id,home_score,away_score,source,status,settled_at)
                   VALUES(?,?,?,?,?,'final',?)
                   ON CONFLICT(fixture_id) DO UPDATE SET home_score=excluded.home_score,
                   away_score=excluded.away_score,source=excluded.source,status='final',
                   settled_at=excluded.settled_at""",
                (str(uuid.uuid4()),fixture_id,record['home_score'],record['away_score'],
                 provider['provider_key'],record['settled_at']),
            )
        for metric, value in record.get('statistics', {}).items():
            connection.execute(
                """INSERT OR IGNORE INTO sports_ai_match_statistics
                   (id,fixture_id,provider_id,metric,value_json,source_url,source_updated_at,observed_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (str(uuid.uuid4()),fixture_id,provider['id'],metric,
                 json.dumps(value,allow_nan=False),record['source_url'],
                 record['observed_at'],now),
            )
        saved += 1
    for row in batch.get('standings', []):
        connection.execute(
            """INSERT OR IGNORE INTO sports_ai_standings_snapshots
               (id,provider_id,competition,season,team,position,played,won,drawn,lost,
                points,goal_difference,details_json,source_url,observed_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (str(uuid.uuid4()),provider['id'],row['competition'],row['season'],row['team'],
             row.get('position'),row.get('played'),row.get('won'),row.get('drawn'),
             row.get('lost'),row.get('points'),row.get('goal_difference'),
             json.dumps(row.get('details', {}),allow_nan=False),row['source_url'],
             row['observed_at']),
        )
    return seen, saved


def collect_provider(provider, connection=None, limit=MAX_RECORDS):
    config = json.loads(provider['config_json'])
    adapter = provider['adapter']
    collectors = {
        'openligadb': _openligadb,
        'openfootball': _openfootball,
        'football_data': _football_data,
        'balldontlie': _balldontlie,
        'sportsdataverse_csv': _sportsdataverse_csv,
    }
    if adapter == 'the_odds_api':
        key_name, token = _safe_key(adapter)
        del key_name
        url = 'https://api.the-odds-api.com/v4/sports'
        query = urllib.parse.urlencode({'apiKey':token})
        payload, headers = _json_body(f'{url}?{query}')
        if not isinstance(payload,list):
            raise CollectionError('The Odds API returned an invalid sport list')
        metadata = {
            'source_url':url,'pages':1,'fixtures':[],'standings':[],
            'sports':payload,
        }
        if connection is None:
            return metadata, len(payload), 0
        from acquisition import collect_odds
        result = collect_odds(provider,connection,config)
        saved = result.get('odds_quotes',0)
        result['credits_remaining'] = headers.get('x-requests-remaining')
        result['source_url'] = url
        return result, saved, saved
    collector = collectors.get(adapter)
    if collector is None:
        raise CollectionError('This source requires a dataset upload or approved browser workflow')
    batch = collector(provider, config, limit=limit)
    if connection is None:
        return batch, len(batch['fixtures']), 0
    seen, saved = _persist(connection, provider, batch)
    return batch, seen, saved


def test_provider(provider):
    _check_rate_limit(provider)
    batch, seen, _ = collect_provider(provider,limit=PAGE_SIZE)
    if not seen:
        raise CollectionError('Connection succeeded but the provider returned no fixture records')
    sample = batch.get('fixtures', [])[:1] or batch.get('sports', [])[:1]
    return {
        'message': f"Connection succeeded; read {seen} provider record(s).",
        'records_seen': seen,
        'sample': sample[0] if sample else None,
        'source_url': batch['source_url'].split('?', 1)[0],
        'pages': batch['pages'],
    }


def create_job(provider_id, kind='collection', retry_of=None):
    job_id = str(uuid.uuid4())
    now = utc_now()
    attempt = 1
    if retry_of:
        with connect() as connection:
            previous = connection.execute(
                'SELECT attempt FROM sports_ai_acquisition_jobs WHERE id=?', (retry_of,),
            ).fetchone()
            attempt = (previous['attempt'] if previous else 0) + 1
    with connect() as connection:
        connection.execute(
            """INSERT INTO sports_ai_acquisition_jobs
               (id,provider_id,retry_of,kind,status,attempt,started_at,logs_json)
               VALUES(?,?,?,?,'running',?,?,?)""",
            (job_id,provider_id,retry_of,kind,attempt,now,
             json.dumps([{'at':now,'level':'info','message':'Job started'}])),
        )
    return job_id


def finish_job(job_id, status, seen=0, saved=0, error=None, message=None):
    now = utc_now()
    with connect() as connection:
        row = connection.execute(
            'SELECT logs_json FROM sports_ai_acquisition_jobs WHERE id=?', (job_id,),
        ).fetchone()
        logs = json.loads(row['logs_json'] or '[]') if row else []
        logs.append({
            'at':now, 'level':'error' if error else 'info',
            'message':str(error)[:500] if error else (message or f'{saved} record(s) persisted'),
        })
        connection.execute(
            """UPDATE sports_ai_acquisition_jobs SET status=?,finished_at=?,
               records_seen=?,records_saved=?,logs_json=?,error=? WHERE id=?""",
            (status,now,seen,saved,json.dumps(logs),str(error)[:500] if error else None,job_id),
        )


def run_collection(provider_id, retry_of=None, actor='sports-ai-acquisition'):
    job_id = create_job(provider_id, retry_of=retry_of)
    seen = saved = 0
    try:
        with connect() as connection:
            provider_row = connection.execute(
                'SELECT * FROM sports_ai_provider_registry WHERE id=?', (provider_id,),
            ).fetchone()
            if not provider_row:
                raise CollectionError('Provider not found')
            provider = dict(provider_row)
            if not provider['enabled'] or not provider['terms_reviewed']:
                raise CollectionError('Provider is disabled or its terms have not been reviewed')
            _check_rate_limit(provider)
            connection.execute(
                'UPDATE sports_ai_provider_registry SET last_attempt_at=? WHERE id=?',
                (utc_now(),provider_id),
            )
        if provider['adapter'] == 'the_odds_api':
            with connect() as connection:
                batch, seen, saved = collect_provider(provider, connection)
        else:
            batch, seen, _ = collect_provider(provider)
            with connect() as connection:
                seen, saved = _persist(connection,provider,batch)
        completed = utc_now()
        status = 'complete' if saved else 'partial'
        cursor_json=json.dumps({
            key:batch[key] for key in ('next_cursor','next_offset')
            if batch.get(key) is not None
        })
        with connect() as connection:
            connection.execute(
                """UPDATE sports_ai_provider_registry SET cursor_json=?,
                   status=?,last_success_at=CASE WHEN ?>0 THEN ? ELSE last_success_at END,
                   last_attempt_at=?,last_error=NULL,next_run_at=? WHERE id=?""",
                (cursor_json,'operational' if saved else 'empty',saved,completed,completed,
                 (datetime.now(timezone.utc)+timedelta(
                     minutes=provider.get('interval_minutes',360)
                 )).isoformat(),provider_id),
            )
            connection.execute(
                """INSERT INTO sports_ai_audit_events(id,actor,action,metadata_json)
                   VALUES(?,?,?,?)""",
                (str(uuid.uuid4()),actor,'acquisition.provider.collection',
                 json.dumps({'provider_id':provider_id,'job_id':job_id,
                             'records_seen':seen,'records_saved':saved})),
            )
        finish_job(job_id,status,seen,saved,message=batch['source_url'].split('?',1)[0])
        return {'job_id':job_id,'status':status,'records_seen':seen,
                'records_saved':saved,'source_url':batch['source_url'].split('?',1)[0]}
    except Exception as error:
        finish_job(job_id,'failed',seen,saved,error=error)
        with connect() as connection:
            row = connection.execute(
                'SELECT interval_minutes,config_json FROM sports_ai_provider_registry WHERE id=?',
                (provider_id,),
            ).fetchone()
            interval = max(
                60 / min(120,max(1,int(
                    json.loads(row['config_json']).get('rate_limit_per_minute',10)
                ))),
                min(60,row['interval_minutes']*60),
            ) if row else 60
            connection.execute(
                """UPDATE sports_ai_provider_registry SET status='error',
                   last_attempt_at=?,last_error=?,next_run_at=? WHERE id=?""",
                (utc_now(),str(error)[:500],
                 (datetime.now(timezone.utc)+timedelta(seconds=interval)).isoformat(),
                 provider_id),
            )
            connection.execute(
                """INSERT INTO sports_ai_data_quality_issues
                   (id,provider_id,severity,issue_type,details_json)
                   VALUES(?,?,'high','provider_collection_failed',?)""",
                (str(uuid.uuid4()),provider_id,json.dumps({'job_id':job_id,'error':str(error)[:500]})),
            )
            connection.execute(
                """INSERT INTO sports_ai_audit_events(id,actor,action,metadata_json)
                   VALUES(?,?,?,?)""",
                (str(uuid.uuid4()),actor,'acquisition.provider.collection.failed',
                 json.dumps({'provider_id':provider_id,'job_id':job_id,
                             'error':str(error)[:300]})),
            )
        raise


def _availability(provider):
    if provider['adapter'] == 'browser':
        return 'Browser Collection Not Authorized'
    if provider.get('credential_env') and not os.environ.get(provider['credential_env']):
        return 'Provider Not Configured'
    if provider.get('last_error'):
        return 'Connection Failed'
    if provider.get('status') == 'operational' and provider.get('last_success_at'):
        return 'Connected'
    return 'Awaiting Real Data'


def browser_runtime():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {
            'installed': False, 'chromium': False,
            'status': 'Browser Collection Not Authorized',
            'detail': 'Playwright is not installed. Install backend requirements and run python -m playwright install chromium.',
        }
    try:
        with sync_playwright() as playwright:
            present = os.path.exists(playwright.chromium.executable_path)
    except Exception as error:
        return {
            'installed': True, 'chromium': False,
            'status': 'Browser Collection Not Authorized',
            'detail': str(error)[:240],
        }
    return {
        'installed': True, 'chromium': present,
        'status': 'Chromium available' if present else 'Browser Collection Not Authorized',
        'detail': '' if present else 'Chromium is not installed. Run python -m playwright install chromium.',
    }


def connection_diagnostics(connection):
    """Report stored integration state. Missing credentials are never replaced with sample data."""
    from sportybet_adapter import integration_status
    providers = []
    for row in connection.execute(
        """SELECT id,provider_key,display_name,sport,adapter,enabled,terms_reviewed,status,
                  credential_env,last_success_at,last_error,terms_url
           FROM sports_ai_provider_registry ORDER BY sport, display_name""",
    ):
        item = dict(row)
        item['availability'] = _availability(item)
        item['credential_configured'] = (not item['credential_env']) or bool(os.environ.get(item['credential_env']))
        item.pop('credential_env', None)
        providers.append(item)
    counts = [dict(row) for row in connection.execute(
        'SELECT sport, source, COUNT(*) AS fixtures FROM sports_ai_fixtures GROUP BY sport, source ORDER BY sport, source',
    )]
    odds = connection.execute('SELECT COUNT(*) FROM sports_ai_odds_snapshots').fetchone()[0]
    sportybet = integration_status()
    sportybet['availability'] = (
        'Connected' if sportybet['automatic_booking_code'] else 'SportyBet Integration Unavailable'
    )
    lease = connection.execute(
        "SELECT owner, expires_at, heartbeat_at FROM sports_ai_worker_leases WHERE id='scheduler-leader'",
    ).fetchone()
    jobs = [dict(row) for row in connection.execute(
        """SELECT id, provider_id, status, started_at, finished_at, records_seen, records_saved, error
           FROM sports_ai_acquisition_jobs ORDER BY started_at DESC LIMIT 12""",
    )]
    return {
        'backend': 'Connected',
        'sqlite': 'Connected',
        'journal_mode': connection.execute('PRAGMA journal_mode').fetchone()[0],
        'data_mode': 'live',
        'demo_data_substituted': False,
        'fixtures_by_source': counts,
        'football_fixtures': sum(row['fixtures'] for row in counts if row['sport'] == 'football'),
        'basketball_fixtures': sum(row['fixtures'] for row in counts if row['sport'] == 'basketball'),
        'odds_status': 'Odds Unavailable' if odds == 0 else 'Quotes stored',
        'odds_snapshots': odds,
        'providers': providers,
        'browser': browser_runtime(),
        'approved_browser_hosts': connection.execute(
            "SELECT COUNT(*) FROM sports_ai_source_permissions WHERE approved=1 AND hostname NOT LIKE '%betexplorer%'",
        ).fetchone()[0],
        'scheduler': {
            'leader': dict(lease) if lease else {},
            'enabled_provider_schedules': connection.execute(
                """SELECT COUNT(*) FROM sports_ai_acquisition_schedules
                   WHERE enabled=1 AND paused=0""",
            ).fetchone()[0],
            'detail': 'backend/worker.py --loop is the collector. The web process does not run a second scheduler.',
        },
        'recent_collections': jobs,
        'sportybet': sportybet,
        'prediction_note': 'Predictions are stored only from collected results. A high hit rate is not an expected return, and missing bookmaker prices stay unavailable.',
    }
