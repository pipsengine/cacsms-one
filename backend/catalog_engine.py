"""Global sport, league and team catalog built only from authorized provider payloads."""

import hashlib
import json
import re
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from acquisition import CollectionError, _request
from db import connect

DISCOVERY_INTERVAL = timedelta(hours=6)
EVIDENCE_DIR = Path(__file__).resolve().parent / 'catalog_evidence'
MAX_DISCOVERY_ROWS = 4000
MAX_EVIDENCE_BYTES = 8 * 1024 * 1024
BACKFILL_ADAPTERS = {'openligadb', 'openfootball'}
SOURCE_PRIORITY = {
    'football_data': 10,
    'balldontlie': 10,
    'openligadb': 20,
    'sportsdataverse_csv': 20,
    'openfootball': 30,
    'authorized_json': 40,
    'the_odds_api': 50,
}
SPORT_ALIASES = {
    'football': 'football',
    'fussball': 'football',
    'soccer': 'football',
    'basketball': 'basketball',
    'tennis': 'tennis',
    'volleyball': 'volleyball',
    'baseball': 'baseball',
    'eishockey': 'ice_hockey',
    'ice hockey': 'ice_hockey',
    'icehockey': 'ice_hockey',
    'hockey': 'ice_hockey',
}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def normalize_name(value):
    text = unicodedata.normalize('NFKD', str(value or '')).encode('ascii', 'ignore').decode().casefold()
    return re.sub(r'[^a-z0-9]+', '', text)


def sport_for_openligadb(shortcut, season):
    """Use the catalog sport for a discovered shortcut. Uncatalogued shortcuts stay football."""
    with connect() as connection:
        row = connection.execute(
            """SELECT l.sport_code FROM sports_ai_league_sources s
               JOIN sports_ai_leagues l ON l.id=s.league_id
               WHERE s.provider_key='openligadb' AND s.external_id=?""",
            (f'{shortcut}:{season}',),
        ).fetchone()
    return row['sport_code'] if row else 'football'


def sport_code_from_name(value):
    text = str(value or '').replace('ß', 'ss').replace('ẞ', 'SS')
    text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode().casefold()
    text = re.sub(r'[^a-z0-9]+', ' ', text).strip()
    if not text:
        return ''
    if text in SPORT_ALIASES:
        return SPORT_ALIASES[text]
    return re.sub(r'[^a-z0-9]+', '_', text).strip('_')[:40]


def _policy(connection):
    row = connection.execute(
        "SELECT value_json FROM sports_ai_settings WHERE key='catalog_backfill'",
    ).fetchone()
    if not row:
        return {'per_day': 0}
    try:
        payload = json.loads(row['value_json'])
    except json.JSONDecodeError:
        return {'per_day': 0}
    per_day = payload.get('per_day', 0)
    try:
        per_day = int(per_day)
    except (TypeError, ValueError):
        per_day = 0
    return {'per_day': min(24, max(0, per_day))}


def set_backfill_policy(connection, per_day, actor):
    per_day = min(24, max(0, int(per_day)))
    connection.execute(
        """INSERT INTO sports_ai_settings(key,value_json,updated_at) VALUES('catalog_backfill',?,?)
           ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at""",
        (json.dumps({'per_day': per_day}), utc_now()),
    )
    connection.execute(
        """INSERT INTO sports_ai_audit_events(id,actor,action,metadata_json) VALUES(?,?,?,?)""",
        (str(uuid.uuid4()), actor, 'catalog.backfill_policy', json.dumps({'per_day': per_day})),
    )
    return {'per_day': per_day}


def register_sport(connection, code, name, actor, notes=''):
    if not re.fullmatch(r'[a-z][a-z0-9_]{1,40}', code):
        raise CollectionError('Sport code must be a lowercase identifier')
    name = str(name or '').strip()
    if len(name) < 2:
        raise CollectionError('Sport name is required')
    existing = connection.execute(
        'SELECT prediction_status FROM sports_ai_sports WHERE code=?', (code,),
    ).fetchone()
    if existing:
        raise CollectionError('That sport is already registered')
    connection.execute(
        """INSERT INTO sports_ai_sports
           (code,name,prediction_status,acquisition_status,notes)
           VALUES(?,?, 'not_modelled','awaiting_authorized_source',?)""",
        (code, name[:120], str(notes or '')[:500]),
    )
    connection.execute(
        """INSERT INTO sports_ai_audit_events(id,actor,action,metadata_json) VALUES(?,?,?,?)""",
        (str(uuid.uuid4()), actor, 'catalog.sport.registered', json.dumps({'code': code})),
    )
    return {'code': code, 'name': name, 'prediction_status': 'not_modelled'}


def _ensure_sport(connection, code, name):
    if not code:
        return False
    connection.execute(
        """INSERT OR IGNORE INTO sports_ai_sports
           (code,name,prediction_status,acquisition_status,notes)
           VALUES(?,?,'not_modelled','source_catalog_only',?)""",
        (code, (name or code).strip()[:120],
         'Registered from an authorized provider sport name. Match collection still requires a configured adapter.'),
    )
    connection.execute(
        """UPDATE sports_ai_sports SET acquisition_status='source_catalog_only'
           WHERE code=? AND acquisition_status='awaiting_authorized_source'""",
        (code,),
    )
    return True


def _country(connection, code, name, source_url, observed_at):
    code = str(code or '').strip().upper()
    name = str(name or '').strip()
    if not code or not name or not re.fullmatch(r'[A-Z0-9]{2,8}', code):
        return ''
    connection.execute(
        """INSERT INTO sports_ai_countries(code,name,source_url,observed_at) VALUES(?,?,?,?)
           ON CONFLICT(code) DO UPDATE SET name=excluded.name, source_url=excluded.source_url,
           observed_at=excluded.observed_at""",
        (code, name[:120], source_url, observed_at),
    )
    return code


def _sha256(body):
    return hashlib.sha256(body).hexdigest()


def _store_evidence(run_id, source_url, body):
    digest = _sha256(body)
    path = ''
    if len(body) <= MAX_EVIDENCE_BYTES:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        target = EVIDENCE_DIR / f'{run_id}.bin'
        target.write_bytes(body)
        path = str(target)
    return digest, path


def _finish_run(connection, run_id, status, seen, saved, source_url, body, error=None):
    digest, path = ('', '')
    if body:
        digest, path = _store_evidence(run_id, source_url, body)
        connection.execute(
            """INSERT INTO sports_ai_catalog_evidence
               (id,run_id,source_url,sha256,byte_length,storage_path,captured_at)
               VALUES(?,?,?,?,?,?,?)""",
            (str(uuid.uuid4()), run_id, source_url, digest, len(body), path, utc_now()),
        )
    connection.execute(
        """UPDATE sports_ai_catalog_runs SET status=?,records_seen=?,records_saved=?,
           source_url=?,evidence_sha256=?,error=?,finished_at=? WHERE id=?""",
        (status, seen, saved, source_url, digest, str(error)[:500] if error else None, utc_now(), run_id),
    )


def _start_run(connection, provider_key, kind):
    run_id = str(uuid.uuid4())
    connection.execute(
        """INSERT INTO sports_ai_catalog_runs
           (id,provider_key,kind,status,started_at) VALUES(?,?,?,'running',?)""",
        (run_id, provider_key, kind, utc_now()),
    )
    return run_id


def _upsert_league(connection, provider, record):
    sport = record['sport_code']
    if not _ensure_sport(connection, sport, record.get('sport_name') or sport):
        return None
    name = record['name'].strip()
    season = str(record.get('season') or '')
    key = normalize_name(name)
    if not key or not name:
        return None
    observed = record.get('observed_at') or utc_now()
    country = _country(
        connection, record.get('country_code'), record.get('country_name'),
        record['source_url'], observed,
    )
    existing = connection.execute(
        """SELECT id FROM sports_ai_leagues
           WHERE sport_code=? AND canonical_key=? AND season=?""",
        (sport, key, season),
    ).fetchone()
    if existing:
        league_id = existing['id']
        connection.execute(
            """UPDATE sports_ai_leagues SET name=?, country_code=COALESCE(?, country_code),
               league_kind=?, updated_at=? WHERE id=?""",
            (name[:200], country or None, record.get('league_kind') or 'competition', observed, league_id),
        )
    else:
        league_id = str(uuid.uuid4())
        connection.execute(
            """INSERT INTO sports_ai_leagues
               (id,sport_code,country_code,name,division,season,canonical_key,league_kind,
                status,updated_at)
               VALUES(?,?,?,?,?,?,?,?, 'discovered',?)""",
            (league_id, sport, country or None, name[:200], str(record.get('division') or '')[:80],
             season[:40], key, record.get('league_kind') or 'competition', observed),
        )
    source_id = str(uuid.uuid4())
    priority = int(record.get('priority') or SOURCE_PRIORITY.get(provider['provider_key'], 100))
    connection.execute(
        """INSERT INTO sports_ai_league_sources
           (id,league_id,provider_id,provider_key,external_id,source_url,priority,observed_at)
           VALUES(?,?,?,?,?,?,?,?)
           ON CONFLICT(provider_key, external_id) DO UPDATE SET
           league_id=excluded.league_id, source_url=excluded.source_url,
           priority=excluded.priority, observed_at=excluded.observed_at,
           provider_id=excluded.provider_id""",
        (source_id, league_id, provider.get('id'), provider['provider_key'],
         record['external_id'][:200], record['source_url'], priority, observed),
    )
    source = connection.execute(
        """SELECT id, provider_id FROM sports_ai_league_sources
           WHERE provider_key=? AND external_id=?""",
        (provider['provider_key'], record['external_id'][:200]),
    ).fetchone()
    if source and record.get('backfill'):
        connection.execute(
            """INSERT INTO sports_ai_backfill_tasks
               (id,league_source_id,provider_id,status,created_at,updated_at)
               VALUES(?,?,?,'held',?,?)
               ON CONFLICT(league_source_id) DO NOTHING""",
            (str(uuid.uuid4()), source['id'], provider.get('id'), observed, observed),
        )
    return league_id


def _provider_map(connection):
    return {
        row['provider_key']: dict(row)
        for row in connection.execute('SELECT * FROM sports_ai_provider_registry')
    }


def discover_openligadb(provider):
    url = 'https://api.openligadb.de/getavailableleagues'
    body, _headers = _request(url)
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as error:
        raise CollectionError('OpenLigaDB league list was not valid JSON') from error
    if not isinstance(payload, list):
        raise CollectionError('OpenLigaDB league list was not an array')
    observed = utc_now()
    records = []
    truncated = len(payload) > MAX_DISCOVERY_ROWS
    for item in payload[:MAX_DISCOVERY_ROWS]:
        if not isinstance(item, dict):
            continue
        sport_name = str((item.get('sport') or {}).get('sportName') or '').strip()
        sport = sport_code_from_name(sport_name)
        name = str(item.get('leagueName') or '').strip()
        shortcut = str(item.get('leagueShortcut') or item.get('leagueId') or '').strip()
        season = str(item.get('leagueSeason') or '').strip()
        if not sport or not name or not shortcut:
            continue
        records.append({
            'sport_code': sport,
            'sport_name': sport_name or sport,
            'name': name,
            'season': season,
            'external_id': f'{shortcut}:{season}',
            'source_url': url,
            'observed_at': observed,
            'league_kind': 'competition',
            'backfill': True,
            'priority': SOURCE_PRIORITY['openligadb'],
        })
    return {'source_url': url, 'body': body, 'records': records, 'truncated': truncated}


def _openfootball_seasons():
    year = datetime.now(timezone.utc).year
    return [f'{year - 2}-{str(year - 1)[2:]}', f'{year - 1}-{str(year)[2:]}', f'{year}-{str(year + 1)[2:]}']


def discover_openfootball(provider):
    records = []
    bodies = []
    used = ''
    errors = []
    for season in _openfootball_seasons():
        url = f'https://api.github.com/repos/openfootball/football.json/contents/{season}'
        try:
            body, _headers = _request(url, {'Accept': 'application/vnd.github+json'})
        except CollectionError as error:
            if 'HTTP 404' in str(error):
                continue
            errors.append(str(error))
            continue
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as error:
            raise CollectionError('OpenFootball directory listing was not valid JSON') from error
        if isinstance(payload, dict):
            message = payload.get('message') or 'OpenFootball directory listing was refused'
            errors.append(str(message))
            continue
        if not isinstance(payload, list):
            continue
        used = url
        bodies.append(body)
        for item in payload:
            if not isinstance(item, dict) or item.get('type') != 'file':
                continue
            filename = str(item.get('name') or '')
            download = str(item.get('download_url') or '')
            if not filename.endswith('.json') or not download.startswith('https://raw.githubusercontent.com/openfootball/'):
                continue
            records.append({
                'sport_code': 'football',
                'sport_name': 'Football',
                'name': filename,
                'season': season,
                'external_id': f'{season}/{filename}',
                'source_url': download,
                'observed_at': utc_now(),
                'league_kind': 'competition',
                'backfill': True,
                'priority': SOURCE_PRIORITY['openfootball'],
            })
    if not records and errors:
        raise CollectionError(errors[0])
    return {
        'source_url': used or 'https://api.github.com/repos/openfootball/football.json',
        'body': b'\n'.join(bodies),
        'records': records,
        'truncated': False,
    }


def discover_football_data(provider):
    import os
    token = os.environ.get('SPORTS_FOOTBALL_DATA_API_KEY')
    if not token:
        raise CollectionError('Set SPORTS_FOOTBALL_DATA_API_KEY before discovering football-data.org competitions')
    url = 'https://api.football-data.org/v4/competitions'
    body, _headers = _request(url, {'X-Auth-Token': token, 'Accept': 'application/json'})
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as error:
        raise CollectionError('football-data.org competition list was not valid JSON') from error
    competitions = payload.get('competitions') if isinstance(payload, dict) else None
    if not isinstance(competitions, list):
        raise CollectionError('football-data.org response did not contain competitions')
    observed = utc_now()
    records = []
    for item in competitions[:MAX_DISCOVERY_ROWS]:
        if not isinstance(item, dict):
            continue
        name = str(item.get('name') or '').strip()
        code = str(item.get('code') or item.get('id') or '').strip()
        if not name or not code:
            continue
        area = item.get('area') or {}
        season = str((item.get('currentSeason') or {}).get('startDate') or '')[:4]
        records.append({
            'sport_code': 'football',
            'sport_name': 'Football',
            'name': name,
            'season': season,
            'country_code': area.get('code'),
            'country_name': area.get('name'),
            'external_id': f'{code}:{season}',
            'source_url': url,
            'observed_at': observed,
            'league_kind': 'competition',
            'backfill': False,
            'priority': SOURCE_PRIORITY['football_data'],
        })
    return {'source_url': url, 'body': body, 'records': records, 'truncated': len(competitions) > MAX_DISCOVERY_ROWS}


def discover_balldontlie(provider):
    import os
    if not os.environ.get('SPORTS_BALLDONTLIE_API_KEY'):
        raise CollectionError('Set SPORTS_BALLDONTLIE_API_KEY before discovering BALLDONTLIE competitions')
    return {
        'source_url': 'https://api.balldontlie.io/nba/v1/games',
        'body': b'',
        'records': [],
        'truncated': False,
    }


def discover_sportsdataverse(provider):
    config = json.loads(provider.get('config_json') or '{}')
    url = str(config.get('dataset_url') or '').strip()
    if not url:
        raise CollectionError('Set a reviewed SportsDataverse dataset_url before discovery can register that dataset')
    return {'source_url': url, 'body': b'', 'records': [], 'truncated': False}


def _odds_sport(key, group):
    text = f'{key} {group}'.casefold()
    if 'soccer' in text:
        return 'football', 'Football'
    if 'basketball' in text:
        return 'basketball', 'Basketball'
    if 'tennis' in text:
        return 'tennis', 'Tennis'
    if 'baseball' in text:
        return 'baseball', 'Baseball'
    if 'icehockey' in text or 'ice hockey' in text:
        return 'ice_hockey', 'Ice hockey'
    if 'volleyball' in text:
        return 'volleyball', 'Volleyball'
    return '', ''


def discover_odds_api(provider):
    import os
    token = os.environ.get('SPORTS_ODDS_API_KEY')
    if not token:
        raise CollectionError('Set SPORTS_ODDS_API_KEY before discovering The Odds API sports')
    import urllib.parse
    url = 'https://api.the-odds-api.com/v4/sports'
    body, _headers = _request(f'{url}?{urllib.parse.urlencode({"apiKey": token})}')
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as error:
        raise CollectionError('The Odds API sport list was not valid JSON') from error
    if not isinstance(payload, list):
        raise CollectionError('The Odds API sport list was not an array')
    observed = utc_now()
    records = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        key = str(item.get('key') or '').strip()
        title = str(item.get('title') or '').strip()
        group = str(item.get('group') or '').strip()
        sport, sport_name = _odds_sport(key, group)
        if not sport or not key or not title:
            continue
        if item.get('has_outrights') and not item.get('active', True):
            continue
        records.append({
            'sport_code': sport,
            'sport_name': sport_name,
            'name': title,
            'season': '',
            'external_id': key,
            'source_url': url,
            'observed_at': observed,
            'league_kind': 'odds_market',
            'backfill': False,
            'priority': SOURCE_PRIORITY['the_odds_api'],
        })
    return {'source_url': url, 'body': body, 'records': records, 'truncated': False}


DISCOVERERS = {
    'openligadb': discover_openligadb,
    'openfootball': discover_openfootball,
    'football_data': discover_football_data,
    'balldontlie': discover_balldontlie,
    'sportsdataverse_csv': discover_sportsdataverse,
    'the_odds_api': discover_odds_api,
}


def run_catalog_discovery(provider_keys=None):
    summary = []
    with connect() as connection:
        providers = _provider_map(connection)
    selected = provider_keys or list(DISCOVERERS)
    for key in selected:
        discover = DISCOVERERS.get(key)
        provider = providers.get(key)
        if discover is None or provider is None:
            continue
        with connect() as connection:
            run_id = _start_run(connection, key, 'discovery')
        seen = saved = 0
        body = b''
        source_url = ''
        try:
            batch = discover(provider)
            source_url = batch['source_url']
            body = batch['body']
            seen = len(batch['records'])
            with connect() as connection:
                for record in batch['records']:
                    if _upsert_league(connection, provider, record):
                        saved += 1
                status = 'partial' if batch.get('truncated') else 'complete'
                if batch.get('truncated'):
                    connection.execute(
                        """INSERT INTO sports_ai_data_quality_issues
                           (id,provider_id,severity,issue_type,details_json)
                           VALUES(?,?,'medium','catalog_discovery_truncated',?)""",
                        (str(uuid.uuid4()), provider['id'], json.dumps({
                            'source_url': source_url,
                            'kept': MAX_DISCOVERY_ROWS,
                        })),
                    )
                _finish_run(connection, run_id, status, seen, saved, source_url, body)
                connection.execute(
                    """INSERT INTO sports_ai_audit_events(id,actor,action,metadata_json)
                       VALUES(?,?,?,?)""",
                    (str(uuid.uuid4()), 'sports-ai-catalog', 'catalog.discovery',
                     json.dumps({'provider_key': key, 'records_seen': seen, 'records_saved': saved})),
                )
            summary.append({'provider_key': key, 'status': status, 'records_seen': seen, 'records_saved': saved})
        except Exception as error:
            with connect() as connection:
                _finish_run(connection, run_id, 'failed', seen, saved, source_url, body, error)
                open_issue = connection.execute(
                    """SELECT 1 FROM sports_ai_data_quality_issues
                       WHERE provider_id=? AND issue_type='catalog_discovery_failed'
                         AND resolved_at IS NULL LIMIT 1""",
                    (provider['id'],),
                ).fetchone()
                if not open_issue:
                    connection.execute(
                        """INSERT INTO sports_ai_data_quality_issues
                           (id,provider_id,severity,issue_type,details_json)
                           VALUES(?,?,'medium','catalog_discovery_failed',?)""",
                        (str(uuid.uuid4()), provider['id'], json.dumps({'error': str(error)[:500]})),
                    )
            summary.append({'provider_key': key, 'status': 'failed', 'error': str(error)[:300]})
    saved = sum(item.get('records_saved', 0) for item in summary)
    failed = [item for item in summary if item['status'] == 'failed']
    status = 'failed' if failed and saved == 0 else 'partial' if failed else 'complete'
    indexed = sync_stored_fixtures()
    return {'status': status, 'saved': saved, 'providers': summary, 'fixtures_indexed': indexed}


def repair_canonical_keys(connection):
    """Fill blank fixture keys from stored identity. A collision keeps the source external id."""
    rows = connection.execute(
        """SELECT id, sport, home, away, starts_at, external_id
           FROM sports_ai_fixtures
           WHERE canonical_key IS NULL OR trim(canonical_key)=''""",
    ).fetchall()
    used = {
        row['canonical_key']
        for row in connection.execute(
            "SELECT canonical_key FROM sports_ai_fixtures WHERE canonical_key IS NOT NULL AND trim(canonical_key)!=''",
        )
    }
    updated = 0
    disambiguated = 0
    for row in rows:
        base = '|'.join((
            row['sport'] or '',
            normalize_name(row['home'] or ''),
            normalize_name(row['away'] or ''),
            str(row['starts_at'] or '')[:10],
        ))
        key = base
        if not row['home'] or not row['away'] or key in used:
            key = base + '|' + str(row['external_id'] or row['id'])
            disambiguated += 1
        if key in used:
            continue
        connection.execute('UPDATE sports_ai_fixtures SET canonical_key=? WHERE id=?', (key, row['id']))
        used.add(key)
        updated += 1
    return {'updated': updated, 'disambiguated': disambiguated, 'countries_invented': 0}


def sync_stored_fixtures():
    """Attach teams and leagues to fixtures already collected. Does not create matches."""
    with connect() as connection:
        rows = connection.execute(
            """SELECT id, source, external_id, sport, competition, home, away, starts_at, observed_at
               FROM sports_ai_fixtures""",
        ).fetchall()
        providers = _provider_map(connection)
        repair_canonical_keys(connection)
        touched = set()
        for row in rows:
            provider = providers.get(row['source']) or {'id': None, 'provider_key': row['source']}
            source = connection.execute(
                """SELECT source_url FROM sports_ai_fixture_source_map
                   WHERE fixture_id=? AND source_url!='' ORDER BY observed_at DESC LIMIT 1""",
                (row['id'],),
            ).fetchone()
            record = {
                'sport': row['sport'],
                'competition': row['competition'],
                'home': row['home'],
                'away': row['away'],
                'starts_at': row['starts_at'],
                'observed_at': row['observed_at'],
                'source_url': source['source_url'] if source else '',
                'external_id': row['external_id'],
                'provider_key': provider['provider_key'],
            }
            _ensure_team(connection, row['sport'], row['home'], record['source_url'], provider['provider_key'], row['observed_at'])
            _ensure_team(connection, row['sport'], row['away'], record['source_url'], provider['provider_key'], row['observed_at'])
            league_id = _link_league(connection, record)
            canonical = '|'.join((
                row['sport'], normalize_name(row['home']), normalize_name(row['away']),
                str(row['starts_at'] or '')[:10],
            ))
            connection.execute(
                'UPDATE sports_ai_fixtures SET league_id=COALESCE(?, league_id), canonical_key=? WHERE id=?',
                (league_id, canonical, row['id']),
            )
            if league_id:
                touched.add(league_id)
        for league_id in touched:
            refresh_league(connection, league_id)
        return len(rows)


def _freshness(last_observed, fixture_count):
    if not fixture_count:
        return 'not_collected'
    if not last_observed:
        return 'unknown'
    try:
        observed = datetime.fromisoformat(str(last_observed).replace('Z', '+00:00'))
    except ValueError:
        return 'unknown'
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - observed
    if age <= timedelta(hours=36):
        return 'fresh'
    if age <= timedelta(days=7):
        return 'current'
    return 'stale'


def _quality(fixture_count, result_count, source_count, freshness, open_issues):
    if fixture_count <= 0:
        return 0
    score = 40
    if result_count:
        score += 20
    if source_count >= 2:
        score += 10
    if freshness == 'fresh':
        score += 20
    elif freshness == 'current':
        score += 10
    if open_issues == 0:
        score += 10
    return score


def refresh_league(connection, league_id):
    row = connection.execute(
        """SELECT COUNT(*) AS fixtures,
                  SUM(CASE WHEN r.id IS NOT NULL THEN 1 ELSE 0 END) AS results,
                  MAX(f.observed_at) AS observed_at
           FROM sports_ai_fixtures f
           LEFT JOIN sports_ai_results r ON r.fixture_id=f.id
           WHERE f.league_id=?""",
        (league_id,),
    ).fetchone()
    sources = connection.execute(
        'SELECT COUNT(*) FROM sports_ai_league_sources WHERE league_id=?', (league_id,),
    ).fetchone()[0]
    issues = connection.execute(
        """SELECT COUNT(*) FROM sports_ai_data_quality_issues q
           JOIN sports_ai_fixtures f ON f.id=q.fixture_id
           WHERE f.league_id=? AND q.resolved_at IS NULL AND q.severity IN ('high','critical')""",
        (league_id,),
    ).fetchone()[0]
    fixture_count = int(row['fixtures'] or 0)
    result_count = int(row['results'] or 0)
    freshness = _freshness(row['observed_at'], fixture_count)
    score = _quality(fixture_count, result_count, sources, freshness, issues)
    connection.execute(
        """UPDATE sports_ai_leagues SET fixture_count=?, result_count=?, source_count=?,
           last_observed_at=?, freshness=?, quality_score=?,
           status=CASE WHEN ?>0 THEN 'collected' ELSE status END, updated_at=?
           WHERE id=?""",
        (fixture_count, result_count, sources, row['observed_at'], freshness, score,
         fixture_count, utc_now(), league_id),
    )


def _ensure_team(connection, sport, name, source_url, provider_key, observed_at):
    key = normalize_name(name)
    if not key:
        return None
    _ensure_sport(connection, sport, sport)
    row = connection.execute(
        'SELECT id, name FROM sports_ai_teams WHERE sport_code=? AND canonical_key=?',
        (sport, key),
    ).fetchone()
    if row:
        if row['name'] != name:
            connection.execute(
                """INSERT OR IGNORE INTO sports_ai_team_aliases
                   (id,team_id,alias,alias_key,provider_key) VALUES(?,?,?,?,?)""",
                (str(uuid.uuid4()), row['id'], name[:200], key, provider_key),
            )
        return row['id']
    team_id = str(uuid.uuid4())
    connection.execute(
        """INSERT INTO sports_ai_teams
           (id,sport_code,canonical_key,name,source_url,observed_at)
           VALUES(?,?,?,?,?,?)""",
        (team_id, sport, key, str(name).strip()[:200], source_url, observed_at),
    )
    return team_id


def _link_league(connection, record):
    source = connection.execute(
        """SELECT l.id, l.name FROM sports_ai_league_sources s
           JOIN sports_ai_leagues l ON l.id=s.league_id
           WHERE s.source_url=? AND l.sport_code=?""",
        (record.get('source_url') or '', record['sport']),
    ).fetchone()
    if source and str(source['name']).endswith('.json') and record.get('competition'):
        renamed = record['competition'].strip()
        key = normalize_name(renamed)
        clash = connection.execute(
            """SELECT id FROM sports_ai_leagues
               WHERE sport_code=? AND canonical_key=? AND season=(
                 SELECT season FROM sports_ai_leagues WHERE id=?) AND id!=?""",
            (record['sport'], key, source['id'], source['id']),
        ).fetchone()
        if key and not clash:
            connection.execute(
                'UPDATE sports_ai_leagues SET name=?, canonical_key=?, updated_at=? WHERE id=?',
                (renamed[:200], key, utc_now(), source['id']),
            )
        return source['id']
    rows = connection.execute(
        'SELECT id, season FROM sports_ai_leagues WHERE sport_code=? AND name=?',
        (record['sport'], record['competition']),
    ).fetchall()
    if len(rows) == 1:
        return rows[0]['id']
    year = str(record.get('starts_at') or '')[:4]
    matched = [row['id'] for row in rows if year and year in (row['season'] or '')]
    if len(matched) == 1:
        return matched[0]
    if rows:
        return None
    provider = {'id': None, 'provider_key': record.get('provider_key') or 'stored-fixture'}
    return _upsert_league(connection, provider, {
        'sport_code': record['sport'],
        'sport_name': record['sport'],
        'name': record['competition'],
        'season': '',
        'external_id': f"fixture:{record['sport']}:{normalize_name(record['competition'])}",
        'source_url': record.get('source_url') or '',
        'observed_at': record.get('observed_at'),
        'league_kind': 'competition',
        'backfill': False,
    })


def sync_persisted_batch(connection, provider, batch):
    touched = set()
    for record in batch.get('fixtures') or []:
        observed = record.get('observed_at') or utc_now()
        _ensure_team(connection, record['sport'], record['home'], record.get('source_url', ''), provider['provider_key'], observed)
        _ensure_team(connection, record['sport'], record['away'], record.get('source_url', ''), provider['provider_key'], observed)
        league_id = _link_league(connection, {**record, 'provider_key': provider['provider_key']})
        canonical = '|'.join((
            record['sport'], normalize_name(record['home']), normalize_name(record['away']),
            str(record.get('starts_at') or '')[:10],
        ))
        mapped = connection.execute(
            """SELECT fixture_id FROM sports_ai_fixture_source_map
               WHERE provider_id=? AND external_id=?""",
            (provider['id'], record['external_id']),
        ).fetchone()
        if not mapped:
            continue
        connection.execute(
            'UPDATE sports_ai_fixtures SET league_id=COALESCE(?, league_id), canonical_key=? WHERE id=?',
            (league_id, canonical, mapped['fixture_id']),
        )
        others = connection.execute(
            """SELECT COUNT(*) FROM sports_ai_fixture_source_map
               WHERE fixture_id=? AND provider_id!=?""",
            (mapped['fixture_id'], provider['id']),
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO sports_ai_fixture_reconciliations
               (id,fixture_id,provider_key,external_id,status,detail,observed_at)
               VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(fixture_id, provider_key, external_id) DO UPDATE SET
               status=excluded.status, detail=excluded.detail, observed_at=excluded.observed_at""",
            (str(uuid.uuid4()), mapped['fixture_id'], provider['provider_key'], record['external_id'],
             'matched' if others else 'single_source',
             'Another authorized source already identifies this fixture' if others else '',
             observed),
        )
        if league_id:
            touched.add(league_id)
    for league_id in touched:
        refresh_league(connection, league_id)


def queue_backfill(connection, source_ids):
    if not source_ids:
        raise CollectionError('Choose at least one discovered league source')
    selected = []
    for source_id in source_ids[:50]:
        row = connection.execute(
            """SELECT s.id, s.provider_id, p.enabled, p.terms_reviewed, p.adapter
               FROM sports_ai_league_sources s
               LEFT JOIN sports_ai_provider_registry p ON p.id=s.provider_id
               WHERE s.id=?""",
            (source_id,),
        ).fetchone()
        if not row:
            continue
        if row['adapter'] in BACKFILL_ADAPTERS and (not row['enabled'] or not row['terms_reviewed']):
            raise CollectionError('Review terms and enable the provider before queueing historical collection')
        selected.append(row)
    queued = 0
    now = utc_now()
    for row in selected:
        if row['adapter'] not in BACKFILL_ADAPTERS:
            connection.execute(
                """INSERT INTO sports_ai_backfill_tasks
                   (id,league_source_id,provider_id,status,last_error,created_at,updated_at)
                   VALUES(?,?,?,'skipped',?,?,?)
                   ON CONFLICT(league_source_id) DO UPDATE SET status='skipped',
                   last_error=excluded.last_error, updated_at=excluded.updated_at""",
                (str(uuid.uuid4()), row['id'], row['provider_id'],
                 'This source has no bounded historical collector. Use its normal collection schedule.',
                 now, now),
            )
            continue
        connection.execute(
            """INSERT INTO sports_ai_backfill_tasks
               (id,league_source_id,provider_id,status,created_at,updated_at)
               VALUES(?,?,?,'queued',?,?)
               ON CONFLICT(league_source_id) DO UPDATE SET status='queued',
               last_error=NULL, updated_at=excluded.updated_at""",
            (str(uuid.uuid4()), row['id'], row['provider_id'], now, now),
        )
        queued += 1
    return {'queued': queued}


def run_queued_backfill():
    with connect() as connection:
        task = connection.execute(
            """SELECT t.id, t.attempt, t.league_source_id, s.external_id, s.source_url, s.provider_key,
                      l.sport_code, p.id AS provider_id, p.enabled, p.terms_reviewed, p.adapter
               FROM sports_ai_backfill_tasks t
               JOIN sports_ai_league_sources s ON s.id=t.league_source_id
               JOIN sports_ai_leagues l ON l.id=s.league_id
               JOIN sports_ai_provider_registry p ON p.id=t.provider_id
               WHERE t.status='queued'
               ORDER BY t.updated_at LIMIT 1""",
        ).fetchone()
        if not task:
            return {'status': 'idle'}
        if not task['enabled'] or not task['terms_reviewed'] or task['adapter'] not in BACKFILL_ADAPTERS:
            connection.execute(
                """UPDATE sports_ai_backfill_tasks SET status='held', last_error=?, updated_at=?
                   WHERE id=?""",
                ('Provider is disabled, terms are not reviewed, or it has no backfill collector', utc_now(), task['id']),
            )
            return {'status': 'held', 'task_id': task['id']}
        claimed = connection.execute(
            """UPDATE sports_ai_backfill_tasks SET status='running', attempt=attempt+1, updated_at=?
               WHERE id=? AND status='queued'""",
            (utc_now(), task['id']),
        )
        if claimed.rowcount != 1:
            return {'status': 'busy'}
        provider = dict(connection.execute(
            'SELECT * FROM sports_ai_provider_registry WHERE id=?', (task['provider_id'],),
        ).fetchone())
    try:
        from acquisition_engine import (
            _openfootball_url, _openligadb_season, _persist,
        )
        if provider['adapter'] == 'openligadb':
            shortcut, _, season = task['external_id'].partition(':')
            if not season.isdigit():
                raise CollectionError('Discovered OpenLigaDB league is missing a season')
            batch = _openligadb_season(provider, shortcut, int(season), 1000, sport=task['sport_code'])
            batch = {'fixtures': batch['fixtures'], 'standings': [], 'source_url': batch['source_url'], 'pages': 1}
        else:
            fixtures = _openfootball_url(provider, task['source_url'], 1000)
            batch = {'fixtures': fixtures, 'standings': [], 'source_url': task['source_url'], 'pages': 1}
        with connect() as connection:
            seen, saved = _persist(connection, provider, batch)
            connection.execute(
                """UPDATE sports_ai_leagues SET
                   fixture_count=(
                     SELECT COUNT(*) FROM sports_ai_fixtures f
                     WHERE f.sport=sports_ai_leagues.sport_code
                       AND lower(f.competition)=lower(sports_ai_leagues.name)
                   ),
                   freshness='collected', updated_at=?
                   WHERE id=(SELECT league_id FROM sports_ai_league_sources WHERE id=?)""",
                (utc_now(), task['league_source_id']),
            )
            connection.execute(
                """UPDATE sports_ai_backfill_tasks SET status='complete', records_saved=?,
                   last_error=NULL, updated_at=? WHERE id=?""",
                (saved, utc_now(), task['id']),
            )
        return {'status': 'complete', 'task_id': task['id'], 'records_seen': seen, 'records_saved': saved}
    except Exception as error:
        with connect() as connection:
            attempt = task['attempt'] + 1
            status = 'queued' if attempt < 3 else 'failed'
            connection.execute(
                """UPDATE sports_ai_backfill_tasks SET status=?, last_error=?, updated_at=? WHERE id=?""",
                (status, str(error)[:500], utc_now(), task['id']),
            )
        return {'status': status, 'task_id': task['id'], 'error': str(error)[:300]}


def queue_current_league_collection(connection):
    """Release one current-season league for a catalogued sport.

    Historical seasons and the daily backfill cap stay untouched. A sport
    stays not modelled until it has its own passing holdout.
    """
    sports = (
        'handball', 'ice_hockey', 'tischtennis', 'frauenfussball',
        'cricket', 'volleyball', 'tennis', 'baseball',
    )
    placeholders = ','.join('?' for _ in sports)
    active = connection.execute(
        f"""SELECT COUNT(*) FROM sports_ai_backfill_tasks t
            JOIN sports_ai_league_sources s ON s.id=t.league_source_id
            JOIN sports_ai_leagues l ON l.id=s.league_id
            WHERE t.status IN ('queued','running')
              AND l.sport_code IN ({placeholders}) AND l.season>='2025'""",
        sports,
    ).fetchone()[0]
    if active:
        return 0
    row = connection.execute(
        f"""SELECT t.id FROM sports_ai_backfill_tasks t
            JOIN sports_ai_league_sources s ON s.id=t.league_source_id
            JOIN sports_ai_leagues l ON l.id=s.league_id
            JOIN sports_ai_provider_registry p ON p.id=t.provider_id
            WHERE t.status='held' AND p.adapter='openligadb' AND p.enabled=1 AND p.terms_reviewed=1
              AND l.sport_code IN ({placeholders}) AND l.season>='2025'
            ORDER BY l.sport_code, l.name LIMIT 1""",
        sports,
    ).fetchone()
    if not row:
        return 0
    connection.execute(
        "UPDATE sports_ai_backfill_tasks SET status='queued', updated_at=? WHERE id=? AND status='held'",
        (utc_now(), row['id']),
    )
    return 1


def release_and_run_backfill():
    with connect() as connection:
        queue_current_league_collection(connection)
        per_day = _policy(connection)['per_day']
        today = datetime.now(timezone.utc).date().isoformat()
        used = connection.execute(
            """SELECT COUNT(*) FROM sports_ai_backfill_tasks
               WHERE status IN ('queued','running','complete') AND updated_at>=?""",
            (today,),
        ).fetchone()[0]
        queued = connection.execute(
            "SELECT COUNT(*) FROM sports_ai_backfill_tasks WHERE status IN ('queued','running')",
        ).fetchone()[0]
        if queued == 0 and per_day > used:
            held = connection.execute(
                """SELECT t.id FROM sports_ai_backfill_tasks t
                   JOIN sports_ai_provider_registry p ON p.id=t.provider_id
                   WHERE t.status='held' AND p.enabled=1 AND p.terms_reviewed=1
                     AND p.adapter IN ('openligadb','openfootball')
                   ORDER BY t.created_at LIMIT 1""",
            ).fetchone()
            if held:
                connection.execute(
                    "UPDATE sports_ai_backfill_tasks SET status='queued', updated_at=? WHERE id=? AND status='held'",
                    (utc_now(), held['id']),
                )
    return run_queued_backfill()


def run_due_catalog(force=False):
    due = force
    if not due:
        with connect() as connection:
            row = connection.execute(
                """SELECT finished_at FROM sports_ai_catalog_runs
                   WHERE kind='discovery' AND status IN ('complete','partial')
                   ORDER BY finished_at DESC LIMIT 1""",
            ).fetchone()
        due = True
        if row and row['finished_at']:
            try:
                last = datetime.fromisoformat(row['finished_at'].replace('Z', '+00:00'))
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                due = datetime.now(timezone.utc) - last >= DISCOVERY_INTERVAL
            except ValueError:
                due = True
    discovered = run_catalog_discovery() if due else {'status': 'skipped', 'saved': 0, 'providers': []}
    backfill = release_and_run_backfill()
    return {**discovered, 'backfill': backfill}


def _page(connection, sql, params, page, page_size):
    total = connection.execute(f'SELECT COUNT(*) FROM ({sql}) AS counted', params).fetchone()[0]
    rows = connection.execute(
        sql + ' LIMIT ? OFFSET ?', (*params, page_size, (page - 1) * page_size),
    ).fetchall()
    return {'items': [dict(row) for row in rows], 'total': total, 'page': page, 'page_size': page_size}


def list_sports(connection):
    rows = []
    for row in connection.execute(
        """SELECT s.*,
                  (SELECT COUNT(*) FROM sports_ai_leagues l WHERE l.sport_code=s.code) AS leagues,
                  (SELECT COUNT(*) FROM sports_ai_teams t WHERE t.sport_code=s.code) AS teams,
                  (SELECT COUNT(*) FROM sports_ai_fixtures f WHERE f.sport=s.code) AS fixtures
           FROM sports_ai_sports s ORDER BY s.name""",
    ):
        item = dict(row)
        item['leagues'] = item['leagues'] or 0
        item['teams'] = item['teams'] or 0
        item['fixtures'] = item['fixtures'] or 0
        rows.append(item)
    return rows


def list_countries(connection, query=''):
    clauses, params = [], []
    if query:
        clauses.append("(code LIKE ? ESCAPE '\\' OR name LIKE ? ESCAPE '\\')")
        like = _like(query)
        params.extend((like, like))
    where = (' WHERE ' + ' AND '.join(clauses)) if clauses else ''
    return [dict(row) for row in connection.execute(
        f"""SELECT c.code, c.name, c.source_url, c.observed_at,
                   COUNT(l.id) AS leagues
            FROM sports_ai_countries c
            LEFT JOIN sports_ai_leagues l ON l.country_code=c.code
            {where}
            GROUP BY c.code ORDER BY c.name LIMIT 500""",
        params,
    )]


def _like(query):
    escaped = str(query).replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    return f'%{escaped}%'


def list_leagues(connection, sport='', country='', query='', freshness='', page=1, page_size=50):
    clauses, params = [], []
    if sport:
        clauses.append('l.sport_code=?')
        params.append(sport)
    if country:
        clauses.append('l.country_code=?')
        params.append(country.upper())
    if freshness:
        clauses.append('l.freshness=?')
        params.append(freshness)
    if query:
        clauses.append("(l.name LIKE ? ESCAPE '\\' OR l.season LIKE ? ESCAPE '\\' OR IFNULL(c.name,'') LIKE ? ESCAPE '\\')")
        like = _like(query)
        params.extend((like, like, like))
    where = (' WHERE ' + ' AND '.join(clauses)) if clauses else ''
    sql = f"""SELECT l.id, l.sport_code, l.country_code, c.name AS country_name, l.name,
                     l.division, l.season, l.league_kind, l.status, l.quality_score, l.freshness,
                     l.last_observed_at, l.fixture_count, l.result_count, l.source_count,
                     s.provider_key, s.external_id, s.source_url, s.id AS source_id, s.priority,
                     b.status AS backfill_status, b.last_error AS backfill_error
              FROM sports_ai_leagues l
              LEFT JOIN sports_ai_countries c ON c.code=l.country_code
              LEFT JOIN sports_ai_league_sources s ON s.league_id=l.id
              LEFT JOIN sports_ai_backfill_tasks b ON b.league_source_id=s.id
              {where}
              ORDER BY l.sport_code, l.name, l.season"""
    return _page(connection, sql, params, page, page_size)


def list_teams(connection, sport='', query='', page=1, page_size=50):
    clauses, params = [], []
    if sport:
        clauses.append('t.sport_code=?')
        params.append(sport)
    if query:
        clauses.append("(t.name LIKE ? ESCAPE '\\' OR t.canonical_key LIKE ? ESCAPE '\\')")
        like = _like(query)
        params.extend((like, like))
    where = (' WHERE ' + ' AND '.join(clauses)) if clauses else ''
    sql = f"""SELECT t.id, t.sport_code, t.name, t.canonical_key, t.country_code,
                     t.source_url, t.observed_at,
                     (SELECT COUNT(*) FROM sports_ai_team_aliases a WHERE a.team_id=t.id) AS aliases
              FROM sports_ai_teams t
              {where}
              ORDER BY t.sport_code, t.name"""
    return _page(connection, sql, params, page, page_size)


def list_errors(connection, limit=100):
    runs = [dict(row) for row in connection.execute(
        """SELECT id, provider_key, kind, status, error, records_seen, records_saved,
                  source_url, started_at, finished_at
           FROM sports_ai_catalog_runs
           WHERE error IS NOT NULL AND error!=''
           ORDER BY started_at DESC LIMIT ?""",
        (limit,),
    )]
    issues = []
    for row in connection.execute(
        """SELECT id, provider_id, fixture_id, severity, issue_type, details_json, created_at
           FROM sports_ai_data_quality_issues
           WHERE resolved_at IS NULL
             AND issue_type IN (
               'catalog_discovery_failed','catalog_discovery_truncated',
               'result_conflict','provider_collection_failed','provider_connection_test_failed'
             )
           ORDER BY created_at DESC LIMIT ?""",
        (limit,),
    ):
        item = dict(row)
        item['details'] = json.loads(item.pop('details_json') or '{}')
        issues.append(item)
    tasks = [dict(row) for row in connection.execute(
        """SELECT t.id, t.status, t.attempt, t.last_error, t.updated_at, s.provider_key, s.external_id
           FROM sports_ai_backfill_tasks t
           JOIN sports_ai_league_sources s ON s.id=t.league_source_id
           WHERE t.status='failed' OR (t.last_error IS NOT NULL AND t.last_error!='')
           ORDER BY t.updated_at DESC LIMIT ?""",
        (limit,),
    )]
    return {'discovery_errors': runs, 'quality_issues': issues, 'backfill_errors': tasks}


def coverage(connection):
    from analysis_coverage import coverage_report
    sports = connection.execute('SELECT COUNT(*) FROM sports_ai_sports').fetchone()[0]
    leagues = connection.execute('SELECT COUNT(*) FROM sports_ai_leagues').fetchone()[0]
    teams = connection.execute('SELECT COUNT(*) FROM sports_ai_teams').fetchone()[0]
    countries = connection.execute('SELECT COUNT(*) FROM sports_ai_countries').fetchone()[0]
    linked = connection.execute(
        "SELECT COUNT(*) FROM sports_ai_fixtures WHERE league_id IS NOT NULL AND league_id!=''",
    ).fetchone()[0]
    freshness = {
        row['freshness']: row['total']
        for row in connection.execute(
            'SELECT freshness, COUNT(*) AS total FROM sports_ai_leagues GROUP BY freshness',
        )
    }
    last = connection.execute(
        """SELECT finished_at, status FROM sports_ai_catalog_runs
           WHERE kind='discovery' ORDER BY started_at DESC LIMIT 1""",
    ).fetchone()
    providers = []
    for row in connection.execute(
        """SELECT p.provider_key, p.display_name, p.adapter, p.enabled, p.terms_reviewed, p.status,
                  p.last_success_at, p.last_error, p.credential_env,
                  (SELECT COUNT(*) FROM sports_ai_league_sources s WHERE s.provider_id=p.id) AS leagues
           FROM sports_ai_provider_registry p ORDER BY p.display_name""",
    ):
        item = dict(row)
        item['credential_configured'] = (not item['credential_env']) or bool(__import__('os').environ.get(item['credential_env']))
        item.pop('credential_env', None)
        item['enabled'] = bool(item['enabled'])
        item['terms_reviewed'] = bool(item['terms_reviewed'])
        providers.append(item)
    return {
        'sports': sports,
        'countries': countries,
        'leagues': leagues,
        'teams': teams,
        'fixtures_linked': linked,
        'freshness': freshness,
        'backfill': _policy(connection),
        'last_discovery': dict(last) if last else None,
        'providers': providers,
        'analysis': coverage_report(connection),
    }
