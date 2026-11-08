"""Displayed match history from BetExplorer and Soccerway.

A score is stored only when the page shows a finished result. A challenge,
subscription, or empty page stores nothing. This pass does not place a wager
and does not copy betting prices.
"""

import json
import re
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from html import unescape
from urllib.parse import urlsplit

from acquisition import CollectionError
from acquisition_engine import ARCHIVE_AFTER, MATCH_CLOSED, _normalize, _record, archived_settlement
from booking_schedule import WAT

LAGOS = WAT
HISTORY_KEY = 'history_browse'
MIN_GAP = timedelta(minutes=10)
MIN_GAP_WHEN_WAITING = timedelta(minutes=2)
COOLDOWN = timedelta(hours=6)
MAX_DAY_OFFSET = 400
BETEXPLORER_PER_PASS = 2
PRIORITY_PAGES = 6
SOCCERWAY_WAITING = 3

BETEXPLORER_SPORTS = (
    ('football', 'football'),
    ('basketball', 'basketball'),
    ('hockey', 'ice_hockey'),
    ('tennis', 'tennis'),
    ('volleyball', 'volleyball'),
    ('handball', 'handball'),
    ('baseball', 'baseball'),
    ('table-tennis', 'table_tennis'), ('badminton', 'badminton'),
    ('american-football', 'americanfootball'), ('rugby-union', 'rugby'),
    ('futsal', 'futsal'), ('snooker', 'snooker'), ('darts', 'darts'),
    ('mma', 'mma'), ('boxing', 'boxing'), ('cricket', 'cricket'),
    ('esports', 'esports'),
)
MODELLED_HISTORY = (
    ('football', 'football'),
    ('basketball', 'basketball'),
    ('hockey', 'ice_hockey'),
)
SOCCERWAY_SEED = (
    ('/full-time/', 'Recent results'),
    ('/england/premier-league/', 'England: Premier League'),
    ('/england/championship/', 'England: Championship'),
    ('/spain/laliga/', 'Spain: LaLiga'),
    ('/italy/serie-a/', 'Italy: Serie A'),
    ('/germany/bundesliga/', 'Germany: Bundesliga'),
    ('/germany/2-bundesliga/', 'Germany: 2. Bundesliga'),
    ('/france/ligue-1/', 'France: Ligue 1'),
    ('/netherlands/eredivisie/', 'Netherlands: Eredivisie'),
    ('/portugal/liga-portugal/', 'Portugal: Liga Portugal'),
    ('/norway/eliteserien/', 'Norway: Eliteserien'),
    ('/nigeria/npfl/', 'Nigeria: NPFL'),
    ('/europe/champions-league/', 'Europe: Champions League'),
    ('/europe/europa-league/', 'Europe: Europa League'),
    ('/europe/conference-league/', 'Europe: Conference League'),
    ('/africa/caf-champions-league/', 'Africa: CAF Champions League'),
)
ALLOWED_HOSTS = frozenset({
    'www.betexplorer.com', 'betexplorer.com',
    'ng.soccerway.com', 'www.soccerway.com', 'soccerway.com',
})
_BLOCKED = (
    'captcha', 'verify you are human', 'verify that you are human',
    'unusual traffic', 'access denied', 'subscribe to continue',
    'subscription required', 'just a moment',
)
_WOMEN = re.compile(r'\b(women|woman|ladies|frauen|female)\b', re.I)
def _table_rows(html):
    """Split result rows in linear time. A lazy cross-page match can stall on a large table."""
    for part in re.split(r'<tr\b', html or '', flags=re.I)[1:]:
        end = part.lower().find('</tr>')
        if end < 0:
            continue
        head, _, body = part[:end].partition('>')
        yield head, body
_TOURNAMENT = re.compile(r'class="[^"\n]*\btable-main__tournament(?:\s[^"\n]*)?"[^>]*>(.*?)</a>', re.I | re.S)
_DT = re.compile(r'data-dt="(\d+),(\d+),(\d+),(\d+),(\d+)"')
_RESULT = re.compile(
    r'class="table-main__result".*?<strong>\s*(\d{1,3})\s*:\s*(\d{1,3})\s*</strong>',
    re.I | re.S,
)
_HOME_LINE = re.compile(r'table-main__teamLine--home[^>]*>(.*?)</span>', re.I | re.S)
_AWAY_LINE = re.compile(r'table-main__teamLine--away[^>]*>(.*?)</span>', re.I | re.S)
_TEAMS = re.compile(
    r'class="table-main__tt".*?<strong>(.*?)</strong>\s*-\s*([^<]+)</a>',
    re.I | re.S,
)
_HREF = re.compile(r'href="(/[a-z-]+/[^"]+/([A-Za-z0-9]{4,})/)"')
_TAGS = re.compile(r'<[^>]+>')
_SW_TIME = re.compile(r'(\d{1,2})\.(\d{1,2})\.\s+(\d{1,2}):(\d{2})')
_LEAGUE_HREF = re.compile(r'href="(/[a-z0-9-]+/[a-z0-9-]+/)"')
_SKIP_STATUS = re.compile(r'\b(postp|canc|award|walkover|retired|w\.o\.)\b', re.I)
_PAGE_SEGMENT = {
    'football': 'football', 'frauenfussball': 'football', 'basketball': 'basketball',
    'ice_hockey': 'hockey', 'tennis': 'tennis', 'volleyball': 'volleyball',
    'handball': 'handball', 'baseball': 'baseball',
    'table_tennis': 'table-tennis', 'americanfootball': 'american-football',
    'rugby': 'rugby-union', 'counterstrike': 'esports',
}


def _supplementary_set_score(sport, home_score, away_score):
    """A golden set is published beside the match. It is not the match score.

    Volleyball results are set counts, such as 3:2. A golden set is first to
    15 and is listed as its own row, such as 15:11.
    """
    if sport not in ('volleyball', 'beach_volleyball'):
        return False
    try:
        home, away = int(home_score), int(away_score)
    except (TypeError, ValueError):
        return False
    return max(home, away) >= 15 and abs(home - away) >= 2


def _plain(fragment):
    return ' '.join(unescape(_TAGS.sub(' ', fragment or '')).replace('\xa0', ' ').split())


def _blocked(title, text):
    sample = f'{title or ""}\n{(text or "")[:4000]}'.casefold()
    return any(marker in sample for marker in _BLOCKED)


def _kickoff(year, month, day, hour, minute, now):
    try:
        local = datetime(int(year), int(month), int(day), int(hour), int(minute), tzinfo=LAGOS)
    except ValueError:
        return None
    current = now.astimezone(LAGOS)
    if local > current + timedelta(hours=3):
        try:
            local = local.replace(year=local.year - 1)
        except ValueError:
            return None
    if local > current + timedelta(hours=3):
        return None
    return local.astimezone(timezone.utc)


def _sport_for(base_sport, competition):
    from sport_support import canonical_sport
    if base_sport == 'esports':
        text = (competition or '').casefold()
        for code, labels in (
            ('counterstrike', ('counter-strike', 'counter strike', 'cs2')),
            ('dota2', ('dota 2', 'dota2')),
            ('lol', ('league of legends',)),
            ('efootball', ('efootball', 'e-football', 'esoccer')),
            ('ebasketball', ('ebasketball', 'nba 2k', 'nba2k')),
        ):
            if any(label in text for label in labels):
                return code
        return None
    return canonical_sport(base_sport, competition)


def _player_names(home, away, href):
    """Expand abbreviated player labels only when the event's own slug agrees."""
    slug = href.strip('/').split('/')[-2].split('-')
    def prefix(label, parts):
        tokens = re.findall(r'[a-z]+', label.casefold())
        if len(tokens) < 2:
            return None
        if len(tokens[-1]) == 1:
            family, initial = tokens[:-1], tokens[-1]
        elif len(tokens[0]) == 1:
            family, initial = tokens[1:], tokens[0]
        else:
            return None
        n = len(family)
        if parts[:n] != family or len(parts) <= n or not parts[n].startswith(initial):
            return None
        return n + 1
    n = prefix(home, slug)
    if n:
        m = prefix(away, slug[n:])
        if m and n + m == len(slug):
            return ' '.join(slug[:n]).title(), ' '.join(slug[n:]).title()
    return home, away


def parse_betexplorer_results(html, base_sport, page_url, observed_at, now):
    """Finished rows from one BetExplorer results table."""
    competition = ''
    found = []
    for attrs, body in _table_rows(html):
        tournament = _TOURNAMENT.search(body)
        if tournament:
            competition = _plain(tournament.group(1))[:200]
            continue
        stamp = _DT.search(attrs)
        score = _RESULT.search(body)
        if not stamp or not score or not competition or _SKIP_STATUS.search(_plain(body)):
            continue
        home_line, away_line = _HOME_LINE.search(body), _AWAY_LINE.search(body)
        if home_line and away_line:
            home, away = _plain(home_line.group(1)), _plain(away_line.group(1))
        else:
            teams = _TEAMS.search(body)
            if not teams:
                continue
            home, away = _plain(teams.group(1)), _plain(teams.group(2))
        link = _HREF.search(body)
        segment = _PAGE_SEGMENT.get(base_sport, base_sport)
        if not home or not away or home == away or not link or not link.group(1).startswith(f'/{segment}/'):
            continue
        if base_sport in ('tennis', 'table_tennis', 'badminton'):
            home, away = _player_names(home, away, link.group(1))
        day, month, year, hour, minute = stamp.groups()
        kickoff = _kickoff(year, month, day, hour, minute, now)
        if kickoff is None:
            continue
        sport = _sport_for(base_sport, competition)
        if not sport:
            continue
        home_score, away_score = int(score.group(1)), int(score.group(2))
        if _supplementary_set_score(sport, home_score, away_score):
            continue
        found.append({
            'external_id': link.group(2),
            'sport': sport,
            'competition': competition,
            'home': home,
            'away': away,
            'starts_at': kickoff.isoformat(),
            'home_score': home_score,
            'away_score': away_score,
            'source_url': 'https://www.betexplorer.com' + link.group(1),
            'observed_at': observed_at,
        })
    return found


_VOID_TAGS = frozenset({
    'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'source', 'track', 'wbr',
})


class _SoccerwayParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.country = ''
        self.competition = ''
        self.current = None
        self.matches = []
        self.leagues = []

    def handle_starttag(self, tag, attrs):
        if tag in _VOID_TAGS:
            return
        item = {'tag': tag, 'attrs': dict(attrs), 'text': []}
        item['classes'] = set((item['attrs'].get('class') or '').split())
        self.stack.append(item)
        if 'event__match' in item['classes']:
            raw_id = item['attrs'].get('id') or ''
            self.current = {
                'id': raw_id.split('_')[-1],
                'time': '', 'home': '', 'away': '', 'scores': [], 'href': '',
                'competition': self.competition, 'country': self.country,
            }
        href = item['attrs'].get('href') or ''
        if self.current is not None and 'mid=' in href:
            self.current['href'] = href
        if 'headerLeague__title' in item['classes'] and href.startswith('/'):
            self.leagues.append(href.split('?', 1)[0])

    def handle_data(self, data):
        if self.stack and data:
            self.stack[-1]['text'].append(data)

    def handle_startendtag(self, tag, attrs):
        return

    def handle_endtag(self, tag):
        if tag in _VOID_TAGS or not self.stack:
            return
        item = self.stack.pop()
        text = ' '.join(''.join(item['text']).split())
        if self.stack and text:
            self.stack[-1]['text'].append(text)
        classes = item['classes']
        if 'headerLeague__title-text' in classes and text:
            self.competition = text
        if 'headerLeague__category-text' in classes and text:
            self.country = text.strip(' :')
        if self.current is None:
            return
        if item['attrs'].get('data-testid') == 'wcl-stageTime' and text:
            self.current['time'] = text
        if 'event__homeParticipant' in classes and text:
            self.current['home'] = text
        if 'event__awayParticipant' in classes and text:
            self.current['away'] = text
        if item['attrs'].get('data-testid') == 'wcl-tableScore' and text:
            self.current['scores'].append(text)
        if 'event__match' in classes:
            self.current['competition'] = self.competition or self.current['competition']
            self.current['country'] = self.country or self.current['country']
            self.matches.append(self.current)
            self.current = None


def parse_soccerway_results(html, observed_at, now):
    """Finished football rows from a Soccerway results or full-time page."""
    parser = _SoccerwayParser()
    try:
        parser.feed(html or '')
        parser.close()
    except Exception:
        return [], []
    found = []
    for match in parser.matches:
        stamp = _SW_TIME.search(match.get('time') or '')
        scores = match.get('scores') or []
        if not stamp or len(scores) < 2 or _SKIP_STATUS.search(match.get('time') or ''):
            continue
        if not (scores[0].isdigit() and scores[1].isdigit()):
            continue
        home, away = (match.get('home') or '').strip(), (match.get('away') or '').strip()
        if not home or not away or home == away:
            continue
        day, month, hour, minute = stamp.groups()
        kickoff = _kickoff(now.astimezone(LAGOS).year, month, day, hour, minute, now)
        if kickoff is None:
            continue
        country = match.get('country') or ''
        name = match.get('competition') or ''
        competition = f'{country.title()}: {name}' if country and name else (name or country)
        if not competition:
            continue
        external_id = match.get('id') or f'{home}-{away}-{kickoff.date().isoformat()}'
        href = match.get('href') or ''
        if href.startswith('http'):
            source_url = href.split('?', 1)[0]
        elif href.startswith('/'):
            source_url = 'https://ng.soccerway.com' + href.split('?', 1)[0]
        else:
            source_url = 'https://ng.soccerway.com/'
        found.append({
            'external_id': external_id[:200],
            'sport': _sport_for('football', competition),
            'competition': competition[:200],
            'home': home[:200],
            'away': away[:200],
            'starts_at': kickoff.isoformat(),
            'home_score': int(scores[0]),
            'away_score': int(scores[1]),
            'source_url': source_url,
            'observed_at': observed_at,
        })
    leagues = []
    seen = set()
    for href in list(parser.leagues) + _LEAGUE_HREF.findall(html or ''):
        path = href.split('?', 1)[0]
        if not path.endswith('/'):
            path += '/'
        if path in seen or path.count('/') != 3:
            continue
        seen.add(path)
        leagues.append(path)
    return found, leagues


def _blank_state():
    return {
        'sport_index': 0,
        'day_offset': 1,
        'priority_sport': 0,
        'priority_day': 1,
        'read_at': {},
        'discovered': [],
        'last_run_at': '',
        'cooldown_until': '',
        'places_wager': False,
    }


def _load_state(connection):
    row = connection.execute(
        "SELECT value_json FROM sports_ai_settings WHERE key=?", (HISTORY_KEY,),
    ).fetchone()
    state = _blank_state()
    if not row:
        return state
    try:
        stored = json.loads(row['value_json'] or '{}')
    except json.JSONDecodeError:
        return state
    if isinstance(stored, dict):
        state.update({key: stored.get(key, state[key]) for key in state})
    state['places_wager'] = False
    return state


def _save_state(connection, state, now):
    state = {**state, 'last_run_at': now.isoformat(), 'places_wager': False}
    connection.execute(
        """INSERT INTO sports_ai_settings(key,value_json,updated_at) VALUES(?,?,?)
           ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at""",
        (HISTORY_KEY, json.dumps(state), now.isoformat()),
    )
    return state


def _parse_stamp(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def wanted_league_keys(connection):
    """League keys that have an upcoming fixture and no finished score under that name."""
    from prediction_engine import league_key, upcoming_fixture_sql
    from sport_support import ANALYSED_SPORTS, sql_in
    finished = {
        (row['sport'], league_key(row['competition']))
        for row in connection.execute(
            """SELECT DISTINCT f.sport, f.competition
               FROM sports_ai_fixtures f
               JOIN sports_ai_results r ON r.fixture_id=f.id AND r.status='final'""",
        )
    }
    keys = set()
    for row in connection.execute(
        f"""SELECT sport, competition FROM sports_ai_fixtures f
            WHERE {upcoming_fixture_sql('f')}
              AND f.sport IN ({sql_in(ANALYSED_SPORTS)})""",
    ):
        if (row['sport'], league_key(row['competition'])) in finished:
            continue
        keys.add((row['sport'], league_key(row['competition'])))
    return keys


def _soccerway_catalog(state):
    catalog = [{'path': path, 'name': name} for path, name in SOCCERWAY_SEED]
    known = {item['path'] for item in catalog}
    for path in state.get('discovered') or []:
        if path not in known:
            catalog.append({'path': path, 'name': path.strip('/').replace('-', ' ')})
            known.add(path)
    return catalog


def _soccerway_due(item, state, wanted, now):
    from prediction_engine import league_key
    seen = _parse_stamp((state.get('read_at') or {}).get(item['path']))
    if seen is None:
        return True
    age = now - seen
    if league_key(item['name']) in wanted or ('football', league_key(item['name'])) in wanted or ('frauenfussball', league_key(item['name'])) in wanted:
        return age >= timedelta(hours=24)
    return age >= timedelta(days=7)


def _history_days(state, sports, day_key, sport_key, pages, now):
    """The next result days, in the same order a successful visit advances the cursor."""
    count = len(sports)
    offset = int(state.get(day_key) or 1)
    index = int(state.get(sport_key) or 0)
    visits = []
    if offset < 1 or count == 0:
        return visits
    for step in range(pages):
        position = index + step
        day_offset = offset + (position // count)
        if day_offset > MAX_DAY_OFFSET:
            break
        path, sport = sports[position % count]
        day = now.astimezone(LAGOS).date() - timedelta(days=day_offset)
        next_position = position + 1
        nxt = {
            f'next_{sport_key}': next_position % count,
            f'next_{day_key}': offset + (next_position // count),
        }
        visits.append({
            'kind': 'betexplorer',
            'sport': sport,
            'url': (
                f'https://www.betexplorer.com/{path}/results/'
                f'?year={day.year}&month={day.month:02d}&day={day.day:02d}'
            ),
            'cursor': 'priority' if sport_key == 'priority_sport' else 'general',
            **nxt,
        })
    return visits


def plan_visits(state, wanted, now):
    """Result pages for leagues that still have no finished score, then the general walk."""
    # Reserve general capacity even while priority leagues have missing results.
    visits = _history_days(state, BETEXPLORER_SPORTS, 'day_offset', 'sport_index', BETEXPLORER_PER_PASS, now)
    if wanted:
        visits += _history_days(
            state, MODELLED_HISTORY, 'priority_day', 'priority_sport', PRIORITY_PAGES, now,
        )
        soccerway_limit = SOCCERWAY_WAITING
    else:
        soccerway_limit = 1
    from prediction_engine import league_key
    catalog = _soccerway_catalog(state)
    due = [item for item in catalog if _soccerway_due(item, state, wanted, now)]
    due.sort(key=lambda item: (0 if league_key(item['name']) in wanted or ('football', league_key(item['name'])) in wanted else 1, catalog.index(item)))
    for item in due[:soccerway_limit]:
        path = item['path'] if item['path'].endswith('/') else item['path'] + '/'
        results = path if path == '/full-time/' else path + 'results/'
        visits.append({
            'kind': 'soccerway',
            'sport': 'football',
            'path': path,
            'url': 'https://ng.soccerway.com' + results,
        })
    return visits


def _advance(state, visit, now):
    if visit['kind'] == 'betexplorer':
        if visit.get('cursor') == 'priority':
            state['priority_sport'] = int(visit['next_priority_sport'])
            state['priority_day'] = int(visit['next_priority_day'])
        else:
            state['sport_index'] = int(visit.get('next_sport_index', state.get('sport_index') or 0))
            state['day_offset'] = int(visit.get('next_day_offset', state.get('day_offset') or 1))
        return state
    read_at = dict(state.get('read_at') or {})
    read_at[visit.get('path') or visit['url']] = now.isoformat()
    state['read_at'] = read_at
    return state


def _provider(connection, key):
    row = connection.execute(
        'SELECT * FROM sports_ai_provider_registry WHERE provider_key=?', (key,),
    ).fetchone()
    return dict(row) if row else None


def _fixture_id(connection, provider, record, date_only):
    """Match a displayed result to a stored fixture without scanning the whole sport."""
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
    import uuid
    kickoff = datetime.fromisoformat(record['starts_at'])
    home, away = _normalize(record['home']), _normalize(record['away'])
    matches = []
    for candidate in connection.execute(
        """SELECT id, home, away, starts_at FROM sports_ai_fixtures
           WHERE sport=? AND starts_at BETWEEN ? AND ?""",
        (record['sport'], (kickoff - timedelta(hours=36)).isoformat(), (kickoff + timedelta(hours=36)).isoformat()),
    ):
        # The same two clubs can play split squads at the same time, one at each
        # home rink. Reversed home and away is a different match.
        if _normalize(candidate['home']) != home or _normalize(candidate['away']) != away:
            continue
        try:
            candidate_time = datetime.fromisoformat(candidate['starts_at'].replace('Z', '+00:00'))
        except ValueError:
            continue
        tolerance = 36 if record.get('time_precision') == 'date' or candidate['id'] in date_only else 3
        difference = abs((kickoff - candidate_time).total_seconds())
        if difference <= tolerance * 3600:
            matches.append((difference, candidate['id']))
    if matches:
        matches.sort()
        if len(matches) == 1 or matches[0][0] < matches[1][0]:
            return matches[0][1]
    return str(uuid.uuid4())


def _release_analysis_holds(connection, opened):
    """Let a skipped fixture be analysed again once its league has a new final."""
    from prediction_engine import league_key, upcoming_fixture_sql
    released = 0
    for sport in sorted({item[0] for item in opened}):
        keys = {key for item_sport, key in opened if item_sport == sport}
        rows = connection.execute(
            f"""SELECT f.id, f.competition FROM sports_ai_fixtures f
                WHERE f.sport=? AND {upcoming_fixture_sql('f')}
                  AND EXISTS (
                    SELECT 1 FROM sports_ai_prediction_jobs j
                    WHERE j.idempotency_key='insufficient:' || f.id
                  )""",
            (sport,),
        )
        for row in rows:
            if league_key(row['competition']) not in keys:
                continue
            connection.execute(
                "DELETE FROM sports_ai_prediction_jobs WHERE idempotency_key=?",
                (f"insufficient:{row['id']}",),
            )
            connection.execute('DELETE FROM sports_ai_analysis_coverage WHERE fixture_id=?', (row['id'],))
            released += 1
    return released


REPAIR_SPORTS = ('basketball', 'tennis', 'volleyball')


def repair_imported_settlements(connection, sports=REPAIR_SPORTS):
    """Replace a shared catalogue timestamp with each match's close.

    One stamp on matches kicked off more than 36 hours apart is the download
    time. Training then treats every earlier final as still unknown. A settlement
    time that belongs to one match is left unchanged. Football and ice hockey
    already have settlement times that training can use, so they stay out of the
    default set.
    """
    selected = tuple(sports)
    if not selected:
        return {'updated': 0, 'sports': []}
    marks = ','.join('?' for _ in selected)
    grouped = {}
    for row in connection.execute(
        f"""SELECT r.fixture_id, r.settled_at, f.starts_at
            FROM sports_ai_results r
            JOIN sports_ai_fixtures f ON f.id=r.fixture_id
            WHERE r.status='final' AND f.sport IN ({marks})""",
        selected,
    ):
        grouped.setdefault(row['settled_at'], []).append(row)
    updated = 0
    for items in grouped.values():
        parsed = []
        for item in items:
            try:
                kickoff = datetime.fromisoformat(str(item['starts_at']).replace('Z', '+00:00'))
                settled = datetime.fromisoformat(str(item['settled_at']).replace('Z', '+00:00'))
            except ValueError:
                continue
            if kickoff.tzinfo is None or settled.tzinfo is None:
                continue
            kickoff, settled = kickoff.astimezone(timezone.utc), settled.astimezone(timezone.utc)
            parsed.append((item['fixture_id'], kickoff, settled))
        if len(parsed) < 2 or max(item[1] for item in parsed) - min(item[1] for item in parsed) <= ARCHIVE_AFTER:
            continue
        for fixture_id, kickoff, settled in parsed:
            if settled - kickoff <= ARCHIVE_AFTER:
                continue
            closed = kickoff + MATCH_CLOSED
            if closed >= settled:
                continue
            connection.execute(
                'UPDATE sports_ai_results SET settled_at=? WHERE fixture_id=?',
                (closed.isoformat(), fixture_id),
            )
            updated += 1
    return {'updated': updated, 'sports': list(selected)}


def _store(connection, provider, rows, observed_at):
    import uuid
    from catalog_engine import sync_persisted_batch
    from prediction_engine import league_key
    records = []
    for row in rows:
        try:
            records.append(_record(
                provider['id'], row['external_id'], row['sport'], row['competition'],
                row['home'], row['away'], row['starts_at'], observed_at, row['source_url'],
                'FINISHED', row['home_score'], row['away_score'],
                archived_settlement(row['starts_at'], observed_at),
            ))
        except CollectionError:
            continue
    if not records:
        return 0, 0
    date_only = {
        row['fixture_id'] for row in connection.execute(
            "SELECT fixture_id FROM sports_ai_fixture_source_map WHERE time_precision='date'",
        )
    }
    seen = saved = 0
    opened = set()
    now = observed_at
    for record in records:
        seen += 1
        if _supplementary_set_score(record['sport'], record['home_score'], record['away_score']):
            continue
        fixture_id = _fixture_id(connection, provider, record, date_only)
        previous = connection.execute(
            'SELECT id FROM sports_ai_fixtures WHERE id=?', (fixture_id,),
        ).fetchone()
        existing_result = None
        if previous:
            existing_result = connection.execute(
                'SELECT home_score, away_score FROM sports_ai_results WHERE fixture_id=?',
                (fixture_id,),
            ).fetchone()
        stored = None if not existing_result else (existing_result['home_score'], existing_result['away_score'])
        stored_side = stored is not None and _supplementary_set_score(record['sport'], *stored)
        if previous:
            connection.execute(
                """UPDATE sports_ai_fixtures SET status=?, observed_at=?, updated_at=CURRENT_TIMESTAMP
                   WHERE id=?""",
                (record['status'], record['observed_at'], fixture_id),
            )
        else:
            connection.execute(
                """INSERT INTO sports_ai_fixtures
                   (id,external_id,source,sport,competition,home,away,starts_at,status,
                    statistics_json,odds_json,observed_at)
                   VALUES(?,?,?,?,?,?,?,?,?,'{}','{}',?)""",
                (fixture_id, record['external_id'], provider['provider_key'], record['sport'],
                 record['competition'], record['home'], record['away'], record['starts_at'],
                 record['status'], record['observed_at']),
            )
        connection.execute(
            """INSERT INTO sports_ai_fixture_source_map
               (id,provider_id,external_id,fixture_id,source_updated_at,observed_at,time_precision,source_url)
               VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(provider_id,external_id) DO UPDATE SET
               fixture_id=excluded.fixture_id,source_updated_at=excluded.source_updated_at,
               observed_at=excluded.observed_at,time_precision=excluded.time_precision,
               source_url=excluded.source_url""",
            (str(uuid.uuid4()), provider['id'], record['external_id'], fixture_id,
             record['observed_at'], now, record.get('time_precision', 'instant'),
             record.get('source_url', '')),
        )
        incoming = (record['home_score'], record['away_score'])
        if stored and stored != incoming and not (stored_side and not incoming_side):
            open_conflict = connection.execute(
                """SELECT 1 FROM sports_ai_data_quality_issues
                   WHERE fixture_id=? AND issue_type='result_conflict' AND resolved_at IS NULL
                   LIMIT 1""",
                (fixture_id,),
            ).fetchone()
            if not open_conflict:
                connection.execute(
                    """INSERT INTO sports_ai_data_quality_issues
                       (id,fixture_id,provider_id,severity,issue_type,details_json)
                       VALUES(?,?,?,'high','result_conflict',?)""",
                    (str(uuid.uuid4()), fixture_id, provider['id'], json.dumps({
                        'stored': [existing_result['home_score'], existing_result['away_score']],
                        'incoming': [record['home_score'], record['away_score']],
                        'provider_key': provider['provider_key'],
                    })),
                )
        else:
            connection.execute(
                """INSERT INTO sports_ai_results
                   (id,fixture_id,home_score,away_score,source,status,settled_at)
                   VALUES(?,?,?,?,?,'final',?)
                   ON CONFLICT(fixture_id) DO UPDATE SET home_score=excluded.home_score,
                   away_score=excluded.away_score,source=excluded.source,status='final',
                   settled_at=excluded.settled_at""",
                (str(uuid.uuid4()), fixture_id, record['home_score'], record['away_score'],
                 provider['provider_key'], record['settled_at']),
            )
            saved += 1
            opened.add((record['sport'], league_key(record['competition'])))
        record['provider_key'] = provider['provider_key']
        if seen % 40 == 0:
            connection.commit()
    sync_persisted_batch(connection, provider, {'fixtures': records, 'standings': []})
    if opened:
        _release_analysis_holds(connection, opened)
    return seen, saved


def _mark_provider(connection, provider, now, saved, error):
    if not provider:
        return
    if error:
        connection.execute(
            """UPDATE sports_ai_provider_registry
               SET last_attempt_at=?, last_error=?, status='error' WHERE id=?""",
            (now.isoformat(), error[:500], provider['id']),
        )
        return
    connection.execute(
        """UPDATE sports_ai_provider_registry
           SET last_attempt_at=?, last_success_at=?, last_error='', status='operational' WHERE id=?""",
        (now.isoformat(), now.isoformat(), provider['id']),
    )


def _host_allowed(url):
    host = (urlsplit(url).hostname or '').casefold().rstrip('.')
    return host in ALLOWED_HOSTS


def _read_with_playwright(visits):
    import os
    from pathlib import Path
    installed = Path(r'C:\Users\chrisogbaisi\AppData\Local\ms-playwright')
    current = Path(os.environ.get('PLAYWRIGHT_BROWSERS_PATH', ''))
    if installed.exists() and not current.exists():
        os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(installed)
    from playwright.sync_api import sync_playwright
    pages = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            context = browser.new_context(
                viewport={'width': 1365, 'height': 900},
                timezone_id='Africa/Lagos',
                locale='en-NG',
            )
            page = context.new_page()
            page.set_default_timeout(8000)
            page.set_default_navigation_timeout(30000)
            for visit in visits:
                item = {'url': visit['url'], 'html': '', 'title': '', 'error': ''}
                if not _host_allowed(visit['url']):
                    item['error'] = 'host not approved for match history'
                    pages.append(item)
                    continue
                try:
                    response = page.goto(visit['url'], wait_until='domcontentloaded', timeout=30000)
                    status = response.status if response else 0
                    try:
                        selector = 'td.table-main__result' if visit['kind'] == 'betexplorer' else '.event__match'
                        page.wait_for_selector(selector, timeout=12000)
                    except Exception:
                        pass
                    def _body():
                        return page.locator('body').inner_html(timeout=12000)

                    html_parts = []
                    try:
                        html_parts.append(_body())
                    except Exception:
                        html_parts.append('')
                    if visit['kind'] == 'soccerway' and visit.get('path') != '/full-time/':
                        for _ in range(3):
                            try:
                                opened = page.evaluate(
                                    """() => {
                                      const button = [...document.querySelectorAll('button,a,span')].find((el) => {
                                        const text = (el.innerText || '').trim();
                                        return text.length < 40 && /show more/i.test(text);
                                      });
                                      if (!button) return false;
                                      button.click();
                                      return true;
                                    }"""
                                )
                            except Exception:
                                break
                            if not opened:
                                break
                            page.wait_for_timeout(1000)
                            try:
                                html_parts.append(_body())
                            except Exception:
                                break
                    final_host = (urlsplit(page.url).hostname or '').casefold()
                    title = page.title() or ''
                    html = '\n'.join(part for part in html_parts if part)
                    if final_host not in ALLOWED_HOSTS:
                        item['error'] = f'page left the approved history host for {final_host or "an unknown host"}'
                    elif _blocked(title, html[:4000]):
                        item['blocked'] = True
                        item['error'] = 'page asked for a challenge or a subscription'
                    elif status >= 400:
                        item['skip'] = True
                        item['error'] = f'page returned HTTP {status}'
                    else:
                        item['html'] = html
                        item['title'] = title[:300]
                except Exception as error:
                    item['error'] = str(error)[:300]
                pages.append(item)
            context.close()
        finally:
            browser.close()
    return pages


def collect_match_history(now=None, reader=None, force=False):
    """Browse the next history pages and store the finished scores they display."""
    from db import connect
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    observed = current.isoformat()
    with connect() as connection:
        state = _load_state(connection)
        last_run = _parse_stamp(state.get('last_run_at'))
        cooldown = _parse_stamp(state.get('cooldown_until'))
        wanted = wanted_league_keys(connection)
        providers = {
            'betexplorer': _provider(connection, 'betexplorer'),
            'soccerway': _provider(connection, 'soccerway'),
        }
        gap = MIN_GAP_WHEN_WAITING if wanted else MIN_GAP
        if not force and last_run and current < last_run + gap:
            return {'status': 'waiting', 'saved': 0, 'places_wager': False}
        if cooldown and current < cooldown:
            return {'status': 'cooldown', 'saved': 0, 'places_wager': False, 'until': cooldown.isoformat()}
    if not providers['betexplorer'] or not providers['soccerway']:
        return {'status': 'missing_provider', 'saved': 0, 'places_wager': False}
    visits = [
        visit for visit in plan_visits(state, wanted, current)
        if providers.get('betexplorer' if visit['kind'] == 'betexplorer' else 'soccerway', {}).get('enabled')
        and providers.get('betexplorer' if visit['kind'] == 'betexplorer' else 'soccerway', {}).get('terms_reviewed')
    ]
    if not visits:
        with connect() as connection:
            _save_state(connection, state, current)
        return {'status': 'idle', 'saved': 0, 'places_wager': False}
    try:
        pages = (reader or _read_with_playwright)(visits)
    except Exception as error:
        return {'status': 'failed', 'saved': 0, 'error': str(error)[:300], 'places_wager': False}
    saved = seen = 0
    blocked = ''
    blocked_key = ''
    accepted = []
    for visit, page in zip(visits, pages):
        page = page or {}
        html = page.get('html') or ''
        if page.get('blocked'):
            blocked = page.get('error') or 'page asked for a challenge or a subscription'
            blocked_key = 'betexplorer' if visit['kind'] == 'betexplorer' else 'soccerway'
            break
        if page.get('error') and not page.get('skip'):
            state = _advance(state, visit, current)
            continue
        if visit['kind'] == 'betexplorer':
            rows = parse_betexplorer_results(html, visit['sport'], visit['url'], observed, current)
            leagues = []
        else:
            rows, leagues = parse_soccerway_results(html, observed, current)
            discovered = list(state.get('discovered') or [])
            for path in leagues:
                if path not in discovered and path not in {item[0] for item in SOCCERWAY_SEED}:
                    discovered.append(path)
            state['discovered'] = discovered[:400]
        rendered = 'event__match' in html or 'table-main__result' in html
        if rows or rendered or page.get('skip'):
            accepted.append((visit, rows))
        state = _advance(state, visit, current)
    for visit, rows in accepted:
        provider = providers['betexplorer' if visit['kind'] == 'betexplorer' else 'soccerway']
        with connect() as connection:
            page_seen, page_saved = _store(connection, provider, rows, observed)
            _mark_provider(connection, provider, current, page_saved, '')
        seen += page_seen
        saved += page_saved
    with connect() as connection:
        if blocked:
            state['cooldown_until'] = (current + COOLDOWN).isoformat()
            _mark_provider(connection, providers.get(blocked_key), current, 0, blocked)
        else:
            state['cooldown_until'] = ''
        _save_state(connection, state, current)
    status = 'blocked' if blocked and not saved else 'collected' if saved else 'empty'
    return {
        'status': status,
        'saved': saved,
        'seen': seen,
        'pages': len(accepted),
        'error': blocked,
        'places_wager': False,
    }
