"""Visible Chromium session for supervised SportyBet research.

The browser does not solve challenges, does not evade rate limits, and does not
place a wager. Unattended extraction stays off in this build.
"""

import os
import queue
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from browser_operator import BrowserResearchError, validate_research_url
from db import connect
from sportybet_research import blocked_page, parse_visible_text, store_observations, unattended_status

BOOKMAKER = 'SportyBet'
APPROVED_HOSTS = {'sportybet.com', 'www.sportybet.com'}
SECTION_URLS = {
    'home': 'https://www.sportybet.com/',
    'football': 'https://www.sportybet.com/ng/sport/football',
    'basketball': 'https://www.sportybet.com/ng/sport/basketball',
}
TERMINAL_STATES = {'COMPLETED', 'FAILED', 'BLOCKED', 'IDLE'}
NAVIGATION_GAP_SECONDS = 8
MAX_NAVIGATIONS = 6


def capture_directory():
    configured = os.environ.get('SPORTS_BROWSER_CAPTURE_DIR')
    path = Path(configured) if configured else Path(__file__).resolve().parent / 'sportybet_captures'
    path.mkdir(parents=True, exist_ok=True)
    return path


def session_timeout_seconds():
    try:
        return max(30, int(os.environ.get('SPORTS_BROWSER_SESSION_SECONDS', '1200')))
    except ValueError:
        return 1200


def _now():
    return datetime.now(timezone.utc).isoformat()


def _allowed_host(hostname):
    host = (hostname or '').casefold().rstrip('.')
    if os.environ.get('SPORTS_BROWSER_ALLOW_LOCAL_TESTS') == '1' and host in {'localhost', '127.0.0.1'}:
        return True
    return host == 'sportybet.com' or host.endswith('.sportybet.com')


class OperatorBusy(RuntimeError):
    pass


class SessionEnded(RuntimeError):
    pass


class SportyBetOperator:
    def __init__(self):
        self._lock = threading.Lock()
        self._commands = queue.Queue()
        self._thread = None
        self._session_id = None
        self._paused = False

    def recover(self):
        with connect() as connection:
            rows = connection.execute(
                """SELECT id, state FROM sports_ai_sportybet_sessions
                   WHERE state NOT IN ('COMPLETED','FAILED','BLOCKED','IDLE')"""
            ).fetchall()
            for row in rows:
                if row['id'] == self._session_id and self._thread and self._thread.is_alive():
                    continue
                self._event(
                    connection, row['id'], 'FAILED', 'recover', None,
                    'The browser process was not running. No quotes were imported during recovery.',
                )
                connection.execute(
                    """UPDATE sports_ai_sportybet_sessions
                       SET state='FAILED', error=?, ended_at=?, heartbeat_at=?, paused=0
                       WHERE id=?""",
                    ('Session recovered without a live browser. No quotes were imported.', _now(), _now(), row['id']),
                )

    def status(self):
        self.recover()
        with connect() as connection:
            session = connection.execute(
                'SELECT * FROM sports_ai_sportybet_sessions ORDER BY started_at DESC LIMIT 1'
            ).fetchone()
            payload = {
                'state': 'IDLE',
                'mode': 'supervised',
                'paused': False,
                'bookmaker': BOOKMAKER,
                'source_url': None,
                'last_operation': None,
                'observation_count': 0,
                'error': None,
                'session_id': None,
                'heartbeat_at': None,
                'started_at': None,
                'live': bool(self._thread and self._thread.is_alive()),
                'unattended': unattended_status(connection),
                'places_wager': False,
            }
            if session:
                payload.update({
                    'state': session['state'],
                    'mode': session['mode'],
                    'paused': bool(session['paused']),
                    'source_url': session['source_url'],
                    'last_operation': session['last_operation'],
                    'observation_count': session['observation_count'],
                    'error': session['error'],
                    'session_id': session['id'],
                    'heartbeat_at': session['heartbeat_at'],
                    'started_at': session['started_at'],
                })
            return payload

    def health(self):
        chromium = False
        detail = ''
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as playwright:
                chromium = os.path.exists(playwright.chromium.executable_path)
                detail = '' if chromium else 'Chromium is not installed. Run python -m playwright install chromium.'
        except ImportError:
            detail = 'Playwright is not installed. Install backend requirements and run python -m playwright install chromium.'
        except Exception as error:
            detail = str(error)[:300]
        current = self.status()
        return {
            'chromium': chromium,
            'detail': detail,
            'headed': os.environ.get('SPORTS_BROWSER_HEADLESS') != '1',
            'state': current['state'],
            'live': current['live'],
            'heartbeat_at': current['heartbeat_at'],
            'unattended_operational': False,
            'places_wager': False,
        }

    def start(self, actor, section='home', target_url=None):
        if self._thread and self._thread.is_alive():
            raise OperatorBusy('A browser session is already running. Pause or stop it before starting another.')
        self.recover()
        url = self._target_url(section, target_url)
        if not target_url:
            validate_research_url(url, APPROVED_HOSTS)
        session_id = str(uuid.uuid4())
        with connect() as connection:
            connection.execute(
                """INSERT INTO sports_ai_sportybet_sessions
                   (id,mode,state,last_operation,started_at,heartbeat_at,actor)
                   VALUES(?,'supervised','STARTING','Launching Chromium',?,?,?)""",
                (session_id, _now(), _now(), actor),
            )
            self._event(connection, session_id, 'STARTING', 'start', url, 'Supervised Chromium session requested.')
        self._commands = queue.Queue()
        self._paused = False
        self._session_id = session_id
        self._thread = threading.Thread(
            target=self._run, args=(session_id, url), name='sportybet-browser', daemon=True,
        )
        self._thread.start()
        return self.status()

    def pause(self):
        self._require_live()
        self._commands.put({'type': 'pause'})
        return self.status()

    def resume(self):
        self._require_live()
        self._commands.put({'type': 'resume'})
        return self.status()

    def stop(self):
        if self._thread and self._thread.is_alive():
            self._commands.put({'type': 'stop'})
            self._thread.join(timeout=20)
        elif self._session_id:
            with connect() as connection:
                connection.execute(
                    """UPDATE sports_ai_sportybet_sessions
                       SET state='COMPLETED', last_operation='Stopped', ended_at=?, paused=0 WHERE id=?""",
                    (_now(), self._session_id),
                )
        return self.status()

    def restart(self, actor, section='home'):
        self.stop()
        return self.start(actor, section=section)

    def navigate(self, section):
        self._require_live()
        if self._paused:
            raise OperatorBusy('The session is paused.')
        self._commands.put({'type': 'navigate', 'url': self._target_url(section, None)})
        return self.status()

    def _require_live(self):
        if not self._thread or not self._thread.is_alive():
            raise OperatorBusy('No browser session is running.')

    def _target_url(self, section, target_url):
        if target_url:
            if os.environ.get('SPORTS_BROWSER_ALLOW_LOCAL_TESTS') != '1':
                raise BrowserResearchError('Only SportyBet pages can be opened from the operator.')
            parsed = urlsplit(target_url)
            if parsed.scheme not in {'http', 'https'} or parsed.hostname not in {'localhost', '127.0.0.1'}:
                raise BrowserResearchError('Local browser tests can open only a loopback page.')
            return target_url
        if section not in SECTION_URLS:
            raise BrowserResearchError('Choose the SportyBet home, football or basketball page.')
        return SECTION_URLS[section]

    def _run(self, session_id, url):
        from browser_slots import BrowserBusy, browser_slot
        try:
            with browser_slot('operator-session', wait=60, ttl=session_timeout_seconds() + 120):
                return self._run_session(session_id, url)
        except BrowserBusy as busy:
            self._fail(session_id, f'{busy}. Try again when the current booking or collection finishes.')

    def _run_session(self, session_id, url):
        navigations = 0
        self._blocked = False
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            self._fail(session_id, f'Playwright is not installed. {error}')
            return
        headless = os.environ.get('SPORTS_BROWSER_HEADLESS') == '1'
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=headless)
                try:
                    context = browser.new_context(
                        viewport={'width': 1365, 'height': 900},
                        accept_downloads=False,
                        service_workers='block',
                    )
                    page = context.new_page()
                    page.set_default_navigation_timeout(25_000)
                    page.route('**/*', lambda route: self._guard(route))
                    self._visit(session_id, page, url)
                    navigations += 1
                    deadline = time.monotonic() + session_timeout_seconds()
                    last_navigation = time.monotonic()
                    while True:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            self._finish(session_id, 'FAILED', 'The supervised session reached its time limit and the browser was closed.')
                            break
                        try:
                            command = self._commands.get(timeout=min(1, remaining))
                        except queue.Empty:
                            self._heartbeat(session_id)
                            continue
                        deadline = time.monotonic() + session_timeout_seconds()
                        if command['type'] == 'stop':
                            state = 'BLOCKED' if self._blocked else 'COMPLETED'
                            detail = 'The operator closed the browser after a challenge stopped extraction.' if self._blocked else 'The operator stopped the browser.'
                            self._finish(session_id, state, detail)
                            break
                        if command['type'] == 'pause':
                            self._paused = True
                            self._mark(session_id, None, 'Paused', 'Navigation is paused. The visible page stays open.', paused=True)
                            continue
                        if command['type'] == 'resume':
                            self._paused = False
                            self._mark(session_id, None, 'Resumed', 'Navigation can continue.', paused=False)
                            continue
                        if command['type'] == 'navigate':
                            if self._blocked:
                                self._mark(session_id, 'BLOCKED', 'Navigation refused', 'A challenge stopped extraction. The page was not browsed further.')
                                continue
                            if self._paused:
                                self._mark(session_id, None, 'Navigation refused', 'Resume the session before opening another page.')
                                continue
                            if navigations >= MAX_NAVIGATIONS:
                                self._finish(session_id, 'FAILED', 'The session reached its page limit. Start a new supervised session to continue.')
                                break
                            wait = NAVIGATION_GAP_SECONDS - (time.monotonic() - last_navigation)
                            if wait > 0:
                                time.sleep(wait)
                            self._visit(session_id, page, command['url'])
                            navigations += 1
                            last_navigation = time.monotonic()
                    context.close()
                finally:
                    browser.close()
        except SessionEnded:
            return
        except Exception as error:
            self._fail(session_id, str(error)[:500])

    def _guard(self, route):
        target = urlsplit(route.request.url)
        host = (target.hostname or '').casefold().rstrip('.')
        local = os.environ.get('SPORTS_BROWSER_ALLOW_LOCAL_TESTS') == '1' and host in {'localhost', '127.0.0.1'}
        if target.scheme not in {'https', 'http'} or (target.scheme == 'http' and not local):
            route.abort()
            return
        if not _allowed_host(host):
            route.abort()
            return
        route.continue_()

    def _visit(self, session_id, page, url):
        self._mark(session_id, 'NAVIGATING', 'Opening page', url, source_url=url)
        response = page.goto(url, wait_until='domcontentloaded')
        status = response.status if response else None
        if status and status >= 400:
            self._screenshot(session_id, page, 'page-error')
            self._finish(session_id, 'FAILED', f'The page returned HTTP {status}. No quotes were read.')
            raise SessionEnded(f'HTTP {status}')
        text = ''
        try:
            text = page.locator('body').inner_text(timeout=8_000)
        except Exception:
            text = ''
        marker = blocked_page(text)
        shot = self._screenshot(session_id, page, 'page')
        if marker:
            self._blocked = True
            self._mark(
                session_id, 'BLOCKED', 'Challenge detected',
                f'The page asked for a challenge ({marker}). Extraction stopped and the challenge was not bypassed.',
                source_url=page.url, screenshot=shot,
                error=f'The page asked for a challenge ({marker}). Extraction stopped and the challenge was not bypassed.',
            )
            return
        self._mark(session_id, 'INSPECTING', 'Reading the visible page', 'Looking for fixture and price text. Nothing is imported yet.', source_url=page.url)
        observed_at = _now()
        rows = parse_visible_text(text)
        with connect() as connection:
            stored = store_observations(connection, session_id, rows, page.url, observed_at)
            count = connection.execute(
                'SELECT COUNT(*) FROM sports_ai_sportybet_observations WHERE session_id=?',
                (session_id,),
            ).fetchone()[0]
            connection.execute(
                'UPDATE sports_ai_sportybet_sessions SET observation_count=? WHERE id=?',
                (count, session_id),
            )
        detail = (
            f"Visible rows saved for confirmation: {len(stored['saved'])}. "
            f"Duplicates skipped: {stored['duplicates']}. "
            'Confirm a quote before it can be imported.'
            if rows else
            'No fixture price was identified on the visible page. You can record a quote you can see, or open another SportyBet page.'
        )
        self._mark(session_id, 'AWAITING_CONFIRMATION', 'Waiting for confirmation', detail, source_url=page.url, screenshot=shot)

    def _screenshot(self, session_id, page, name):
        directory = capture_directory() / session_id
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f'{name}-{uuid.uuid4().hex[:8]}.png'
        try:
            page.screenshot(path=str(path), full_page=False, timeout=10_000)
        except Exception:
            return None
        return str(path)

    def _mark(self, session_id, state, operation, detail, source_url=None, screenshot=None, paused=None, error=None):
        with connect() as connection:
            assignments = ['last_operation=?', 'heartbeat_at=?']
            values = [operation, _now()]
            if error is not None:
                assignments.append('error=?')
                values.append(error)
            if state:
                assignments.append('state=?')
                values.append(state)
            if source_url:
                assignments.append('source_url=?')
                values.append(source_url)
            if paused is not None:
                assignments.append('paused=?')
                values.append(int(paused))
            values.append(session_id)
            connection.execute(
                f"UPDATE sports_ai_sportybet_sessions SET {', '.join(assignments)} WHERE id=?",
                values,
            )
            current = connection.execute('SELECT state, source_url FROM sports_ai_sportybet_sessions WHERE id=?', (session_id,)).fetchone()
            self._event(
                connection, session_id, state or current['state'], operation,
                source_url or (current['source_url'] if current else None), detail, screenshot,
            )

    def _heartbeat(self, session_id):
        with connect() as connection:
            connection.execute(
                'UPDATE sports_ai_sportybet_sessions SET heartbeat_at=? WHERE id=?',
                (_now(), session_id),
            )

    def _finish(self, session_id, state, detail, screenshot=None):
        with connect() as connection:
            connection.execute(
                """UPDATE sports_ai_sportybet_sessions
                   SET state=?, last_operation=?, error=?, ended_at=?, heartbeat_at=?, paused=0 WHERE id=?""",
                (state, state.title(), None if state == 'COMPLETED' else detail, _now(), _now(), session_id),
            )
            self._event(connection, session_id, state, state.lower(), None, detail, screenshot)

    def _fail(self, session_id, detail):
        self._finish(session_id, 'FAILED', detail)

    def _event(self, connection, session_id, state, operation, source_url, detail, screenshot=None):
        connection.execute(
            """INSERT INTO sports_ai_sportybet_events
               (id,session_id,state,operation,source_url,detail,screenshot_path,created_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (str(uuid.uuid4()), session_id, state, operation, source_url, detail[:1000], screenshot, _now()),
        )


operator = SportyBetOperator()
