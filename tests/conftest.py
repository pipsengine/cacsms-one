"""Keep tests away from production artifacts.

The booking tests drive a local test page ("Pistons76ers ... PLCY42") through the
real booking flow. Without this, every run wrote that page's screenshot into
backend/booking_evidence, where it appeared as SportyBet evidence.
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

_EVIDENCE = Path(tempfile.mkdtemp(prefix='sports-test-evidence-'))

# Tests never touch the production database. By default they run on temporary SQLite files
# (SPORTS_DATABASE_URL='' overrides backend/.env.local). With SPORTS_TEST_POSTGRES=1 they run on
# the separate database named by SPORTS_TEST_DATABASE_URL (default: db_cacsms-one_test on the
# production server's credentials), one schema per temporary database path.
if os.environ.get('SPORTS_TEST_POSTGRES') == '1':
    _test_url = os.environ.get('SPORTS_TEST_DATABASE_URL')
    if not _test_url:
        import db as _db_for_url
        os.environ.pop('SPORTS_DATABASE_URL', None)
        _live_url = _db_for_url.database_url()
        if not _live_url:
            raise RuntimeError('SPORTS_TEST_POSTGRES=1 needs SPORTS_TEST_DATABASE_URL or backend/.env.local')
        from psycopg.conninfo import conninfo_to_dict, make_conninfo
        _params = conninfo_to_dict(_live_url)
        _params['dbname'] = _params['dbname'] + '_test'
        _test_url = make_conninfo(**_params)
    if 'test' not in _test_url:
        raise RuntimeError('The PostgreSQL test database name must contain "test"')
    os.environ['SPORTS_DATABASE_URL'] = _test_url
    os.environ.setdefault('SPORTS_PG_TEST_SLOTS', '4')
    import psycopg as _psycopg
    import db as _db
    _db._ensure_database(_test_url)
    # Recycled slot schemas (t_slotN) are emptied on first use and the default-path schema
    # (public) once per run, so nothing is dropped and rebuilt. Hashed per-path schemas from
    # runs without slots are dropped.
    with _psycopg.connect(_test_url, autocommit=True) as _admin:
        for (_schema,) in _admin.execute(
                "SELECT nspname FROM pg_namespace WHERE nspname LIKE 't\\_%' AND nspname NOT LIKE 't\\_slot%'").fetchall():
            _admin.execute(f'DROP SCHEMA IF EXISTS "{_schema}" CASCADE')
    _db._dirty_slots.add('public')
else:
    os.environ['SPORTS_DATABASE_URL'] = ''
# Set before any test module imports sportybet_booking, so the module default is safe too.
os.environ['SPORTS_BOOKING_EVIDENCE_DIR'] = str(_EVIDENCE)
# Account session files and screenshots never touch the real .secrets or account_evidence.
os.environ['SPORTS_SECRETS_DIR'] = str(_EVIDENCE / 'secrets')
os.environ['SPORTS_ACCOUNT_EVIDENCE_DIR'] = str(_EVIDENCE / 'account')


@pytest.fixture(autouse=True)
def _isolated_booking_evidence(tmp_path, monkeypatch):
    import sportybet_booking
    monkeypatch.setattr(sportybet_booking, 'EVIDENCE', tmp_path / 'booking_evidence')
    yield
