"""Serve slow read-only page payloads from memory, refreshed in the background.

The dashboard (~12 s) and the autonomous overview (~27-45 s) were rebuilt on every request,
and the pages poll every 15-30 s. Each poll repeated the same heavy SQL over a 12 GB database
and competed with the betting lane for the SQLite lock.

cached_view(key, build, ttl):
* fresh copy (younger than ttl): returned at once;
* stale copy: returned at once, and one background thread rebuilds it;
* no copy yet (first request after a restart): built in the request.
A failed rebuild keeps the previous copy and is retried on the next request. Payloads carry
'cache_age_seconds' so a page can show how old its figures are.
"""
import threading
import time

_ENTRIES = {}
_LOCK = threading.Lock()


class _Entry:
    __slots__ = ('payload', 'built_at', 'refreshing', 'lock')

    def __init__(self):
        self.payload = None
        self.built_at = 0.0
        self.refreshing = False
        self.lock = threading.Lock()  # one synchronous first build per key


def _entry(key):
    with _LOCK:
        return _ENTRIES.setdefault(key, _Entry())


def _stamp(entry):
    payload = entry.payload
    if isinstance(payload, dict):
        payload = {**payload, 'cache_age_seconds': round(time.monotonic() - entry.built_at, 1)}
    return payload


def _refresh(entry, build):
    try:
        payload = build()
        entry.payload, entry.built_at = payload, time.monotonic()
    except Exception:  # keep serving the last good copy
        pass
    finally:
        entry.refreshing = False


def cached_view(key, build, ttl=30):
    entry = _entry(key)
    if entry.payload is None:
        with entry.lock:
            if entry.payload is None:  # first build; concurrent first requests wait for this one
                entry.payload, entry.built_at = build(), time.monotonic()
        return _stamp(entry)
    if time.monotonic() - entry.built_at >= ttl:
        with _LOCK:
            start = not entry.refreshing
            entry.refreshing = True
        if start:
            threading.Thread(target=_refresh, args=(entry, build), daemon=True, name=f'view-cache:{key}').start()
    return _stamp(entry)


def clear(key=None):
    with _LOCK:
        if key is None:
            _ENTRIES.clear()
        else:
            _ENTRIES.pop(key, None)
