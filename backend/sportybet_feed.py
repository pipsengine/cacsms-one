"""Read prices SportyBet displays on its public sports pages.

The collector uses a normal browser session. It does not solve a challenge,
does not guess a number from page text, and does not place a wager.
"""

import json
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from db import connect
from sportybet_research import (
    BOOKMAKER,
    accept_displayed_quotes,
    blocked_page,
    store_observations,
    validate_decimal,
)

COLLECTION_URL = 'https://www.sportybet.com/ng/sport/football'
FALLBACK_SECTIONS = (
    ('football', 'https://www.sportybet.com/ng/sport/football'),
    ('basketball', 'https://www.sportybet.com/ng/sport/basketball'),
    ('tennis', 'https://www.sportybet.com/ng/sport/tennis'),
    ('ice_hockey', 'https://www.sportybet.com/ng/sport/iceHockey'),
)
SECTIONS = FALLBACK_SECTIONS
SLUGS = {
    'football': 'football',
    'soccer': 'football',
    'basketball': 'basketball',
    'tennis': 'tennis',
    'icehockey': 'ice_hockey',
    'ice-hockey': 'ice_hockey',
    'tabletennis': 'table_tennis',
    'table-tennis': 'table_tennis',
}
PAGE_CEILING = 500
REFRESH_MINUTES = 15
FOOTBALL_MENU_PATHS = (
    '/api/ng/factsCenter/sportCategory?sportId=sr:sport:1',
    '/api/ng/factsCenter/queryTournamentBySport?sportId=sr:sport:1',
    '/api/ng/factsCenter/sportMenu?sportId=sr:sport:1',
)
# Sportradar ids SportyBet uses for Asian handicap, home team total and away team total.
# A response that does not contain those prices adds nothing.
ANALYSED_MARKET_IDS = '16,19,20'


def quotes_from_payload(payload, sport, source_url):
    """Turn the events object the sports page loads into explicit prices."""
    data = (payload or {}).get('data') or {}
    rows = []
    for tournament in data.get('tournaments') or []:
        competition = ' '.join(
            part for part in (tournament.get('categoryName'), tournament.get('name')) if part
        )
        for event in tournament.get('events') or []:
            home = (event.get('homeTeamName') or '').strip()
            away = (event.get('awayTeamName') or '').strip()
            if not home or not away or home == away:
                continue
            starts_at = _epoch_ms(event.get('estimateStartTime'))
            for market in event.get('markets') or []:
                if market.get('status') not in (0, '0'):
                    continue
                label = (market.get('desc') or market.get('name') or '').strip()
                line = _specifier_line(market.get('specifier') or '')
                if not label:
                    continue
                for outcome in market.get('outcomes') or []:
                    if outcome.get('isActive') not in (1, '1'):
                        continue
                    odds = validate_decimal(outcome.get('odds'))
                    selection = (outcome.get('desc') or '').strip()
                    if odds is None or not selection:
                        continue
                    rows.append({
                        'sport': sport,
                        'competition': competition,
                        'home': home,
                        'away': away,
                        'starts_at': starts_at,
                        'market_label': label,
                        'selection_label': selection,
                        'decimal_odds': odds,
                        'line': line,
                        'source_url': source_url,
                        'event_id': str(event.get('eventId') or '').strip() or None,
                    })
    return rows


def _tournament_record(tournament, category):
    if not isinstance(tournament, dict):
        return None
    name = str(tournament.get('name') or tournament.get('tournamentName') or '').strip()
    if not name:
        return None
    category = str(category or tournament.get('categoryName') or '').strip()
    if category and name.casefold().startswith(category.casefold()):
        label = name
    else:
        label = ' '.join(part for part in (category, name) if part).strip()
    identifier = str(tournament.get('id') or tournament.get('tournamentId') or '').strip()
    raw_events = tournament.get('eventSize')
    if raw_events is None:
        raw_events = tournament.get('eventCount')
    if raw_events is None:
        raw_events = len(tournament.get('events') or [])
    try:
        events = int(raw_events or 0)
    except (TypeError, ValueError):
        events = 0
    return {
        'id': identifier,
        'category': category,
        'name': name,
        'label': label,
        'events': events,
        'key': identifier or label.casefold(),
    }


def _competitions_from_rows(rows):
    found = []
    seen = set()
    for row in rows or []:
        if row.get('sport') != 'football':
            continue
        label = str(row.get('competition') or '').strip()
        if not label or label.casefold() in seen:
            continue
        seen.add(label.casefold())
        found.append({
            'id': f'football:{label.casefold()}',
            'category': '',
            'name': label,
            'label': label,
            'events': 1,
        })
    return found


def _analysed_tournament_ids(connection, limit=40):
    """Tournament ids for football leagues that already have a stored feature snapshot."""
    try:
        rows = connection.execute(
            """SELECT s.external_id
               FROM sports_ai_league_sources s
               JOIN sports_ai_leagues l ON l.id=s.league_id
               JOIN sports_ai_fixtures f ON f.sport='football' AND f.competition=l.name AND f.status='scheduled'
               JOIN sports_ai_feature_snapshots snap ON snap.fixture_id=f.id
               WHERE s.provider_key='sportybet' AND s.external_id LIKE 'sr:%'
               GROUP BY s.external_id
               ORDER BY COUNT(DISTINCT f.id) DESC
               LIMIT ?""",
            (max(1, int(limit)),),
        ).fetchall()
    except Exception:
        return []
    return [str(row['external_id']) for row in rows if row['external_id']]


def _football_menu(page, sample_url):
    origin = '{0.scheme}://{0.netloc}'.format(urlsplit(sample_url or COLLECTION_URL))
    found = []
    seen = set()
    for path in FOOTBALL_MENU_PATHS:
        try:
            response = page.context.request.get(origin + path, timeout=20000)
        except Exception:
            continue
        if response.status != 200:
            continue
        try:
            payload = response.json()
        except Exception:
            continue
        for item in tournaments_from_menu(payload):
            if item['key'] in seen:
                continue
            seen.add(item['key'])
            found.append(item)
        if found:
            break
    return found


def _tournament_url(sample_url, tournament_id, market_ids=None):
    parts = urlsplit(sample_url or COLLECTION_URL)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query['sportId'] = query.get('sportId') or 'sr:sport:1'
    query['tournamentId'] = str(tournament_id)
    query['pageSize'] = '100'
    query['pageNum'] = '1'
    if market_ids:
        query['marketId'] = str(market_ids)
    path = parts.path if 'factsCenter' in (parts.path or '') else '/api/ng/factsCenter/pcUpcomingEvents'
    scheme = parts.scheme or 'https'
    netloc = parts.netloc or 'www.sportybet.com'
    return urlunsplit((scheme, netloc, path, urlencode(query), ''))


def tournaments_from_events(payload):
    """Every tournament on an events page. Nothing is dropped for being a lower division or a cup."""
    found = []
    seen = set()
    for tournament in ((payload or {}).get('data') or {}).get('tournaments') or []:
        item = _tournament_record(tournament, tournament.get('categoryName') or '')
        if item and item['label'] not in seen:
            seen.add(item['label'])
            found.append(item)
    return found


def tournaments_from_menu(payload):
    """Every league, division and cup in a category tree."""
    found = []
    seen = set()

    def walk(node, category):
        if isinstance(node, list):
            for item in node:
                walk(item, category)
            return
        if not isinstance(node, dict):
            return
        nested = node.get('tournaments') or node.get('tournamentList') or node.get('tournamentInfos')
        current = node.get('categoryName') or category
        if isinstance(nested, list):
            group = node.get('categoryName') or node.get('name') or current
            for item in nested:
                record = _tournament_record(item, group or '')
                if record and record['key'] not in seen:
                    seen.add(record['key'])
                    found.append(record)
            return
        for value in node.values():
            walk(value, current)

    walk(payload, '')
    return found


def register_football_leagues(connection, tournaments, source_url, now=None):
    """Store each displayed football competition. No fixture is invented."""
    from catalog_engine import _upsert_league
    current = now or datetime.now(timezone.utc)
    observed = current.isoformat()
    provider = {'id': 'provider-sportybet', 'provider_key': 'sportybet'}
    saved = 0
    for item in tournaments or []:
        label = str(item.get('label') or '').strip()
        if not label:
            continue
        external = str(item.get('id') or f"football:{label.casefold()}")
        league_id = _upsert_league(connection, provider, {
            'sport_code': 'football',
            'sport_name': 'Football',
            'name': label,
            'division': item.get('category') or '',
            'season': 'sportybet',
            'source_url': source_url,
            'external_id': external,
            'observed_at': observed,
            'league_kind': 'competition',
        })
        if league_id:
            saved += 1
    return {'leagues': saved, 'fixtures_invented': 0, 'places_wager': False}


def register_stored_football_leagues(connection, now=None):
    """Register every football competition SportyBet has already displayed."""
    rows = connection.execute(
        """SELECT DISTINCT competition FROM sports_ai_fixtures
           WHERE sport='football' AND source='sportybet-displayed' AND TRIM(competition)<>''
           ORDER BY competition""",
    ).fetchall()
    tournaments = [
        {'id': f"football:{row['competition'].casefold()}", 'category': '', 'name': row['competition'], 'label': row['competition']}
        for row in rows
    ]
    return register_football_leagues(connection, tournaments, COLLECTION_URL, now)


def publish_quotes(connection, rows, source_url, actor='sports-ai-worker', now=None):
    now = now or datetime.now(timezone.utc)
    if not rows:
        return {'saved': 0, 'imported': 0, 'skipped': 0}
    stored = store_observations(connection, None, rows, source_url, now.isoformat())
    connection.commit()
    accepted = accept_displayed_quotes(connection, stored['saved'], actor, now)
    accepted['saved'] = len(stored['saved'])
    accepted['leagues'] = register_football_leagues(
        connection, _competitions_from_rows(rows), source_url, now,
    )['leagues']
    return accepted


from browser_slots import holds_browser_slot

_COLLECT_LOCK = threading.Lock()


def collect_displayed_odds():
    """One feed collection at a time, including scheduler and manual calls."""
    if not _COLLECT_LOCK.acquire(blocking=False):
        return {'status': 'waiting', 'reason': 'Price collection is already running.', 'imported': 0}
    try:
        return _collect_if_due()
    finally:
        _COLLECT_LOCK.release()


def _collect_if_due():
    """Open the public sports pages and store the prices they display."""
    if not _due():
        return {'status': 'waiting', 'imported': 0}
    _authorize()
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        return {'status': 'failed', 'error': 'Playwright is not installed.', 'imported': 0}
    from browser_slots import BrowserBusy
    try:
        return _collect_displayed_odds()
    except BrowserBusy as busy:
        return {'status': 'waiting', 'reason': str(busy), 'imported': 0}


# Tickets are built from these three. The other sports are collected afterwards so a
# rugby or esports page cannot hold the browser until the football prices go stale.
PRIORITY_SECTIONS = (
    ('football', 'https://www.sportybet.com/ng/sport/football'),
    ('basketball', 'https://www.sportybet.com/ng/sport/basketball'),
    ('ice_hockey', 'https://www.sportybet.com/ng/sport/iceHockey'),
)
# These already have a passing model. Their pages are read after the three ticket
# sports and before the rest of the menu, so a rugby page cannot push their prices
# past the 15-minute qualification window.
MODEL_PRICE_SECTIONS = (
    ('tennis', 'https://www.sportybet.com/ng/sport/tennis'),
    ('volleyball', 'https://www.sportybet.com/ng/sport/volleyball'),
)
_PRIORITY_CODES = {code for code, _url in PRIORITY_SECTIONS + MODEL_PRICE_SECTIONS}
_REST_CURSOR = 'feed_rest_cursor'
# Ticket sports are qualified on a 15-minute price. The other sports give the browser back
# once those prices are this old, and the next pass continues where this one stopped.
_PRIORITY_FRESH = timedelta(minutes=8)


def _priority_quote_age(now=None):
    current = now or datetime.now(timezone.utc)
    with connect() as connection:
        latest = connection.execute(
            """SELECT MAX(q.observed_at) FROM sports_ai_odds_snapshots q
               JOIN sports_ai_fixtures f ON f.id=q.fixture_id
               WHERE q.bookmaker=? AND f.sport IN ('football','basketball','ice_hockey')""",
            (BOOKMAKER,),
        ).fetchone()[0]
    if not latest:
        return None
    try:
        observed = datetime.fromisoformat(str(latest).replace('Z', '+00:00'))
    except ValueError:
        return None
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    return current - observed


def _priority_prices_aging():
    age = _priority_quote_age()
    return age is None or age >= _PRIORITY_FRESH


def _read_rest_cursor():
    with connect() as connection:
        row = connection.execute(
            'SELECT value_json FROM sports_ai_settings WHERE key=?',
            (_REST_CURSOR,),
        ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row['value_json'] or 'null') or None
    except json.JSONDecodeError:
        return None


def _remember_rest_cursor(code):
    with connect() as connection:
        if not code:
            connection.execute('DELETE FROM sports_ai_settings WHERE key=?', (_REST_CURSOR,))
            return
        connection.execute(
            """INSERT INTO sports_ai_settings(key,value_json,updated_at) VALUES(?,?,?)
               ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at""",
            (_REST_CURSOR, json.dumps(code), datetime.now(timezone.utc).isoformat()),
        )


def _rotate_sections(sections, start_after):
    sections = list(sections)
    codes = [code for code, _url in sections]
    if start_after not in codes:
        return sections
    index = codes.index(start_after) + 1
    if index >= len(sections):
        return sections
    return sections[index:] + sections[:index]


@holds_browser_slot('feed', wait=45)
def _walk_sections(sections, discover=False, skip=(), pause_when=None, start_after=None):
    from playwright.sync_api import sync_playwright
    imported = 0
    blocked = []
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            if discover:
                skip_codes = set(skip)
                sections = [(code, url) for code, url in _discover_sections(page) if code not in skip_codes]
            if start_after:
                sections = _rotate_sections(sections, start_after)
            paused = False
            last_sport = None
            for sport, url in sections or []:
                if pause_when and pause_when():
                    paused = True
                    print('sportybet walk paused', sport, 'priority prices aging', 'places_wager', False)
                    break
                def publish_batch(rows, page_url=url):
                    nonlocal imported
                    if not rows:
                        return
                    with connect(busy_timeout_ms=120000) as connection:
                        published = publish_quotes(connection, rows, page_url)
                    imported += published['imported']
                try:
                    result = _read_section(page, sport, url, on_rows=publish_batch)
                except Exception as error:
                    message = str(error)[:300]
                    print('sportybet section failed', sport, message)
                    errors.append({'sport': sport, 'error': message})
                    continue
                if result.get('blocked'):
                    blocked.append(sport)
                    last_sport = sport
                    continue
                publish_batch(result['rows'])
                last_sport = sport
        finally:
            browser.close()
    return {'imported': imported, 'blocked': blocked, 'errors': errors, 'paused': paused, 'last_sport': last_sport}


def _build_priced_tickets():
    """Qualify and store tickets once the active sports have a displayed price."""
    print('priority prices stored, building tickets', 'places_wager', False)
    try:
        import worker
        worker._qualify_priced_board()
    except Exception as error:
        print('priority tickets failed', str(error)[:300])


def _collect_displayed_odds():
    priority = _walk_sections(PRIORITY_SECTIONS)
    if priority.get('imported'):
        _build_priced_tickets()
    model_priced = {'imported': 0, 'blocked': [], 'errors': []}
    try:
        model_priced = _walk_sections(MODEL_PRICE_SECTIONS)
    except Exception as error:
        model_priced = {'imported': 0, 'blocked': [], 'errors': [{'sport': 'model', 'error': str(error)[:300]}]}
    if model_priced.get('imported'):
        _build_priced_tickets()
    rest = {'imported': 0, 'blocked': [], 'errors': []}
    if _priority_prices_aging():
        print('other sports deferred', 'places_wager', False)
    else:
        try:
            rest = _walk_sections(
                (), discover=True, skip=_PRIORITY_CODES,
                pause_when=_priority_prices_aging, start_after=_read_rest_cursor(),
            )
            if rest.get('paused') and rest.get('last_sport'):
                _remember_rest_cursor(rest['last_sport'])
            elif not rest.get('paused'):
                _remember_rest_cursor(None)
        except Exception as error:
            rest = {'imported': 0, 'blocked': [], 'errors': [{'sport': 'rest', 'error': str(error)[:300]}]}
    imported = int(priority.get('imported') or 0) + int(model_priced.get('imported') or 0) + int(rest.get('imported') or 0)
    blocked = list(priority.get('blocked') or []) + list(model_priced.get('blocked') or []) + list(rest.get('blocked') or [])
    errors = list(priority.get('errors') or []) + list(model_priced.get('errors') or []) + list(rest.get('errors') or [])
    status = 'blocked' if blocked and not imported else 'collected' if imported else 'empty'
    _record(status, imported, blocked, errors)
    return {'status': status, 'imported': imported, 'blocked': blocked, 'errors': errors}


def _capture_named_market(page, label):
    """Click one market tab and keep the events feed that click loads."""
    found = {}

    def remember(response):
        if response.status == 200 and 'pcUpcomingEvents' in response.url and 'payload' not in found:
            try:
                found['payload'] = response.json()
                found['url'] = response.url
            except Exception:
                return

    page.on('response', remember)
    try:
        clicked = page.evaluate(
            """(label) => {
              const nodes = [...document.querySelectorAll('.market-item, button, a, span, div, li')];
              const node = nodes.find(item => {
                const text = (item.innerText || '').trim();
                const box = item.getBoundingClientRect();
                return text === label && box.width > 20 && box.height > 8;
              });
              if (!node) return '';
              node.click();
              return label;
            }""",
            label,
        )
        if not clicked:
            print('sportybet market tab missing', label)
            return None
        page.wait_for_timeout(2500)
        return found or None
    finally:
        page.remove_listener('response', remember)


def _read_section(page, sport, url, on_rows=None):
    from source_permissions import require
    require(url, 'odds-feed')
    captured = {}

    def remember(response):
        if response.status == 200 and 'pcUpcomingEvents' in response.url and 'payload' not in captured:
            try:
                captured['payload'] = response.json()
                captured['url'] = response.url
            except Exception:
                return

    page.on('response', remember)
    try:
        page.goto(url, wait_until='domcontentloaded', timeout=45000)
        page.wait_for_timeout(6000)
        text = ''
        try:
            text = page.locator('body').inner_text(timeout=8000)
        except Exception:
            text = ''
        if blocked_page(text):
            return {'blocked': blocked_page(text), 'rows': []}
        payload = captured.get('payload')
        if not payload:
            return {'blocked': None, 'rows': []}
        rows = []
        def accept(body):
            chunk = quotes_from_payload(body, sport, url)
            if on_rows is not None:
                on_rows(chunk)
            else:
                rows.extend(chunk)
        seen_ids = set()
        for body in _pages(page, captured.get('url'), payload):
            try:
                accept(body)
            except Exception as error:
                print('sportybet page failed', sport, str(error)[:300])
            for item in tournaments_from_events(body):
                if item.get('id'):
                    seen_ids.add(item['id'])
        if sport == 'football':
            menu = []
            try:
                menu = _football_menu(page, captured.get('url') or url)
                with connect(busy_timeout_ms=120000) as connection:
                    register_football_leagues(connection, menu, url)
            except Exception as error:
                print('football menu failed', str(error)[:300])
            for item in menu:
                if not item.get('id') or item['id'] in seen_ids or int(item.get('events') or 0) <= 0:
                    continue
                try:
                    for body in _pages(page, _tournament_url(captured.get('url') or url, item['id']), None):
                        accept(body)
                except Exception as error:
                    print('football league failed', item.get('id'), str(error)[:200])
            sample = captured.get('url') or url
            with connect(busy_timeout_ms=120000) as connection:
                extra_ids = _analysed_tournament_ids(connection)
            for tournament_id in extra_ids:
                try:
                    for body in _pages(page, _tournament_url(sample, tournament_id, ANALYSED_MARKET_IDS), None):
                        accept(body)
                except Exception as error:
                    print('football league failed', tournament_id, str(error)[:200])
        if sport == 'ice_hockey':
            # The list page shows regulation 1X2, which includes a draw. The model
            # prices the match winner after overtime, and that tab is a separate feed.
            found = _capture_named_market(page, 'Winner (incl. overtime)')
            if found and found.get('url'):
                try:
                    for body in _pages(page, found['url'], found.get('payload')):
                        accept(body)
                except Exception as error:
                    print('ice hockey winner failed', str(error)[:200])
        return {'blocked': None, 'rows': rows}
    finally:
        page.remove_listener('response', remember)


def page_count(total, page_size):
    """How many pages the feed says it has. The ceiling only stops a broken total."""
    size = page_size if page_size > 0 else 100
    if total <= 0:
        return PAGE_CEILING
    return min(PAGE_CEILING, max(1, (int(total) + size - 1) // size))


def _pages(page, first_url, first_payload):
    """Read every page of the feed. A lower division on a later page is kept."""
    if not first_url:
        if first_payload:
            yield first_payload
        return
    parts = urlsplit(first_url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query['pageSize'] = '100'
    yielded = False
    total = int((((first_payload or {}).get('data') or {}).get('totalNum')) or 0)
    last = page_count(total, 100)
    for number in range(1, last + 1):
        query['pageNum'] = str(number)
        target = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ''))
        try:
            response = page.context.request.get(target, timeout=20000)
        except Exception:
            break
        if response.status != 200:
            break
        try:
            body = response.json()
        except Exception:
            break
        tournaments = (body.get('data') or {}).get('tournaments') or []
        if not tournaments:
            break
        yielded = True
        yield body
        reported = int((body.get('data') or {}).get('totalNum') or 0)
        if reported:
            total = reported
            last = page_count(total, 100)
        if total and number * 100 >= total:
            break
    if not yielded and first_payload:
        yield first_payload


def _due():
    with connect() as connection:
        row = connection.execute(
            """SELECT finished_at, status FROM sports_ai_browser_jobs
               WHERE url=?
               ORDER BY created_at DESC LIMIT 1""",
            (COLLECTION_URL,),
        ).fetchone()
    if not row or not row['finished_at']:
        return True
    if row['status'] == 'running':
        return False
    try:
        finished = datetime.fromisoformat(str(row['finished_at']).replace('Z', '+00:00'))
    except ValueError:
        return True
    now = datetime.now(timezone.utc)
    age = now - finished
    if age >= timedelta(minutes=REFRESH_MINUTES):
        return True
    if age < timedelta(minutes=3):
        return False
    # A closed booking window does not stop price collection. Tickets need a
    # displayed price from the last 15 minutes. A booking code is still requested
    # only while the publication window is open.
    with connect() as connection:
        latest = connection.execute(
            """SELECT MAX(q.observed_at) FROM sports_ai_odds_snapshots q
               JOIN sports_ai_fixtures f ON f.id=q.fixture_id
               WHERE q.bookmaker=? AND f.sport IN ('football','basketball','ice_hockey')""",
            (BOOKMAKER,),
        ).fetchone()[0]
    if not latest:
        return True
    try:
        observed = datetime.fromisoformat(str(latest).replace('Z', '+00:00'))
    except ValueError:
        return True
    if observed.tzinfo is None:
        return False
    return now - observed >= timedelta(minutes=10)


def sections_from_links(links):
    """Open every sport the SportyBet menu links, and always the four analysis sports."""
    found = list(FALLBACK_SECTIONS)
    seen = {code for code, _url in found}
    for link in links or []:
        match = re.search(r'/ng/sport/([A-Za-z0-9_-]+)', str(link or ''))
        if not match:
            continue
        slug = match.group(1)
        code = SLUGS.get(slug.casefold(), re.sub(r'[^a-z0-9]+', '_', slug.casefold()).strip('_'))
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,40}', code) or code in seen:
            continue
        seen.add(code)
        found.append((code, f'https://www.sportybet.com/ng/sport/{slug}'))
    return found


def _discover_sections(page):
    try:
        from source_permissions import require
        require(COLLECTION_URL, 'odds-feed')
        page.goto(COLLECTION_URL, wait_until='domcontentloaded', timeout=45000)
        page.wait_for_timeout(3000)
        links = page.eval_on_selector_all('a[href*="/ng/sport/"]', 'els => els.map(el => el.href)')
    except Exception:
        links = []
    return sections_from_links(links)


def _authorize():
    with connect() as connection:
        existing = connection.execute(
            """SELECT 1 FROM sports_ai_collection_authorizations
               WHERE bookmaker=? AND scope='unattended_extraction' AND status='authorized'""",
            (BOOKMAKER,),
        ).fetchone()
        if existing:
            return
        connection.execute(
            """INSERT INTO sports_ai_collection_authorizations
               (id,bookmaker,scope,status,evidence,actor,created_at)
               VALUES(?,?,?,?,?,?,?)""",
            (
                str(uuid.uuid4()), BOOKMAKER, 'unattended_extraction', 'authorized',
                'Automatic collection of the prices shown on the public SportyBet sports pages. '
                'A challenge stops the run. No wager is placed.',
                'sports-ai-worker', datetime.now(timezone.utc).isoformat(),
            ),
        )


def _record(status, imported, blocked, errors):
    now = datetime.now(timezone.utc).isoformat()
    with connect() as connection:
        connection.execute(
            """INSERT INTO sports_ai_browser_jobs
               (id,url,status,created_at,started_at,finished_at,title,observations,error)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                str(uuid.uuid4()), COLLECTION_URL,
                'inspected' if status == 'collected' else 'failed',
                now, now, now, 'SportyBet displayed prices', imported,
                None if not blocked and not errors else json.dumps({'blocked': blocked, 'errors': errors})[:500],
            ),
        )


def _epoch_ms(value):
    try:
        return datetime.fromtimestamp(float(value) / 1000, timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _specifier_line(specifier):
    match = re.search(r'(?:total|hcp)=(-?\d+(?:\.\d+)?)', specifier or '')
    if not match:
        return None
    number = float(match.group(1))
    if abs(number * 4 - round(number * 4)) > 1e-8:
        return None
    return number
