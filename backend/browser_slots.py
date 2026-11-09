"""Shared Chromium slots and failure classification for every browser operator.

The API process, the worker loop and the high-odds background thread each
launched Chromium independently. Two bookings plus a feed collection could run
at once, compete for memory and time out. Every launch now holds one of a
fixed number of slots stored in SQLite, so the limit holds across processes.
A lease expires after ``ttl`` seconds, so a crashed holder cannot block others.
"""
import asyncio
import os
import re
import threading
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone

from db import connect

DEFAULT_TTL = 15 * 60
DEFAULT_WAIT = 90


class BrowserBusy(RuntimeError):
    """Every slot is held. The caller should retry later without spending an attempt."""


def _now():
    return datetime.now(timezone.utc)


def _owner(purpose):
    return f'{os.getpid()}:{threading.get_ident()}:{purpose}:{uuid.uuid4().hex[:8]}'


def try_acquire(purpose, ttl=DEFAULT_TTL, now=None):
    """Claim a free or expired slot. Returns the owner token, or None."""
    current = now or _now()
    owner = _owner(purpose)
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        row = connection.execute(
            """SELECT slot FROM sports_ai_browser_slots
               WHERE owner='' OR julianday(expires_at)<=julianday(?)
               ORDER BY slot LIMIT 1""", (current.isoformat(),),
        ).fetchone()
        if not row:
            return None
        connection.execute(
            """UPDATE sports_ai_browser_slots SET owner=?,purpose=?,acquired_at=?,expires_at=? WHERE slot=?""",
            (owner, purpose, current.isoformat(), (current + timedelta(seconds=ttl)).isoformat(), row['slot']),
        )
    return owner


def release(owner):
    with connect() as connection:
        connection.execute(
            """UPDATE sports_ai_browser_slots SET owner='',purpose='',acquired_at=NULL,
               expires_at='1970-01-01T00:00:00+00:00' WHERE owner=?""", (owner,),
        )


def slot_status(connection, now=None):
    current = now or _now()
    rows = [dict(r) for r in connection.execute('SELECT * FROM sports_ai_browser_slots ORDER BY slot')]
    for row in rows:
        expires = row['expires_at']
        row['busy'] = bool(row['owner']) and expires > current.isoformat()
    return rows


@contextmanager
def browser_slot(purpose, wait=DEFAULT_WAIT, ttl=DEFAULT_TTL, poll=2.0):
    deadline = time.monotonic() + wait
    owner = try_acquire(purpose, ttl)
    while owner is None:
        if time.monotonic() >= deadline:
            raise BrowserBusy(f'No browser slot became free within {wait:.0f}s for {purpose}')
        time.sleep(poll)
        owner = try_acquire(purpose, ttl)
    try:
        yield owner
    finally:
        release(owner)


@asynccontextmanager
async def async_browser_slot(purpose, wait=DEFAULT_WAIT, ttl=DEFAULT_TTL, poll=2.0):
    deadline = time.monotonic() + wait
    owner = await asyncio.to_thread(try_acquire, purpose, ttl)
    while owner is None:
        if time.monotonic() >= deadline:
            raise BrowserBusy(f'No browser slot became free within {wait:.0f}s for {purpose}')
        await asyncio.sleep(poll)
        owner = await asyncio.to_thread(try_acquire, purpose, ttl)
    try:
        yield owner
    finally:
        await asyncio.to_thread(release, owner)


# Errors where the page or network was unavailable, not where the work was wrong.
TRANSIENT = re.compile(
    r'net::ERR_(NAME_NOT_RESOLVED|INTERNET_DISCONNECTED|CONNECTION_(RESET|REFUSED|CLOSED|TIMED_OUT)|'
    r'NETWORK_CHANGED|TIMED_OUT|ADDRESS_UNREACHABLE)|Timeout \d+ms exceeded|Target (page, context or browser )?'
    r'(has been )?closed|Browser has been closed|Connection closed|BrowserBusy|No browser slot|robots\.txt could not be read',
    re.I,
)
INSTALL = re.compile(r"Executable doesn't exist|playwright install|BrowserType\.launch: .*not found", re.I)


def classify_browser_error(error):
    """Return 'transient', 'install' or 'permanent' for an exception or message."""
    text = f'{type(error).__name__}: {error}' if isinstance(error, BaseException) else str(error or '')
    if isinstance(error, BrowserBusy) or TRANSIENT.search(text):
        return 'transient'
    if INSTALL.search(text):
        return 'install'
    return 'permanent'


def holds_browser_slot(purpose, wait=DEFAULT_WAIT, ttl=DEFAULT_TTL):
    """Decorator: run a sync or async function while holding one browser slot."""
    import functools
    import inspect

    def wrap(function):
        if inspect.iscoroutinefunction(function):
            @functools.wraps(function)
            async def run_async(*args, **kwargs):
                async with async_browser_slot(purpose, wait=wait, ttl=ttl):
                    return await function(*args, **kwargs)
            return run_async

        @functools.wraps(function)
        def run(*args, **kwargs):
            with browser_slot(purpose, wait=wait, ttl=ttl):
                return function(*args, **kwargs)
        return run
    return wrap
