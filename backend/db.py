"""Database access for Sports AI: PostgreSQL (primary) or SQLite (fallback, tests by default).

Every module opens its connection through `connect()`. The backend is chosen by configuration:

* PostgreSQL when SPORTS_DATABASE_URL is set, in the environment or in backend/.env.local
  (a libpq conninfo string or a postgresql:// URL). This is the production database.
* SQLite (the file at SPORTS_SQLITE_PATH / sports_demo.sqlite3) otherwise.

The application SQL is written in SQLite's dialect. On PostgreSQL the connection wrapper below
translates each statement (placeholders, INSERT OR IGNORE, GLOB, LIKE case-insensitivity, ...),
the `sqlite_compat` schema (pg_compat.sql) supplies SQLite's built-in functions, and errors are
re-raised as the matching sqlite3 exceptions, so callers behave identically on both backends.

`DB` keeps its meaning as the database *identity* (tests swap it for a temporary path). On
PostgreSQL the default path maps to the `public` schema and any other path to its own schema.
"""
import hashlib
import os
import re
import sqlite3
import threading
import time
from contextlib import closing, contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

_HERE = Path(__file__).resolve().parent
DEFAULT_DB = _HERE / 'sports_demo.sqlite3'
DB = Path(os.environ.get('SPORTS_SQLITE_PATH', str(DEFAULT_DB))).expanduser().resolve()
_migration_lock = threading.Lock()


def _env_file_value(key):
    path = _HERE / '.env.local'
    try:
        for line in path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                name, value = line.split('=', 1)
                if name.strip() == key:
                    return value.strip().strip('"').strip("'")
    except OSError:
        return None
    return None


def database_url():
    """PostgreSQL conninfo, or None for SQLite. SPORTS_DATABASE_URL='' forces SQLite."""
    if 'SPORTS_DATABASE_URL' in os.environ:
        return os.environ['SPORTS_DATABASE_URL'].strip() or None
    return _env_file_value('SPORTS_DATABASE_URL')


# Pin the database for the life of this process: a long-running API or worker must never switch
# databases because backend/.env.local appeared or changed while it was running.
if 'SPORTS_DATABASE_URL' not in os.environ:
    os.environ['SPORTS_DATABASE_URL'] = _env_file_value('SPORTS_DATABASE_URL') or ''


def backend():
    return 'postgres' if database_url() else 'sqlite'


def is_postgres():
    return backend() == 'postgres'


# =============================================================================== SQLite backend

@contextmanager
def _process_migration_lock():
    # Use a separate database so the lock can span backups and commits on DB.
    # API and scheduler startup must not make concurrent multi-GB backups.
    DB.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(str(DB) + '.migration-lock', timeout=600)) as guard:
        guard.execute('BEGIN IMMEDIATE')
        try:
            yield
        finally:
            guard.rollback()


@contextmanager
def _sqlite_connect(busy_timeout_ms=20000):
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, timeout=max(5, int(busy_timeout_ms) / 1000))
    c.row_factory = sqlite3.Row
    c.execute(f'PRAGMA busy_timeout={int(busy_timeout_ms)}')
    c.execute('PRAGMA foreign_keys=ON')
    # Per-connection read tuning (no schema change). Defaults are a 2 MB page cache and on-disk temp
    # B-trees; page builds over the 15 GB database re-read the same pages and spilled every DISTINCT.
    c.execute('PRAGMA cache_size=-32768')      # 32 MB
    c.execute('PRAGMA temp_store=MEMORY')
    c.execute('PRAGMA mmap_size=268435456')    # 256 MB
    try:
        yield c
        c.commit()
    except BaseException:
        c.rollback()
        raise
    finally:
        c.close()


def _sqlite_initialize():
    # Resolved per call (not from _HERE): tests point db.__file__ at a temporary tree.
    migrations = sorted((Path(__file__).parent.parent / 'migrations').glob('*.sql'))
    with _migration_lock, _process_migration_lock():
        existed = DB.exists() and DB.stat().st_size > 0
        DB.parent.mkdir(parents=True, exist_ok=True)
        with _sqlite_connect() as c:
            c.execute('PRAGMA journal_mode=WAL')
            c.execute('''CREATE TABLE IF NOT EXISTS sports_ai_schema_migrations(
    name TEXT PRIMARY KEY, applied_at TEXT NOT NULL
   )''')
            applied = {row['name'] for row in c.execute('SELECT name FROM sports_ai_schema_migrations')}
        pending = [migration for migration in migrations if migration.name not in applied]
        if pending and existed:
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            backup = DB.with_name(f'{DB.stem}.sports-ai-backup-{stamp}{DB.suffix}')
            with _sqlite_connect() as source, closing(sqlite3.connect(backup)) as target:
                source.backup(target)
        for migration in pending:
            sql = migration.read_text(encoding='utf-8')
            statements = []
            statement = ''
            for line in sql.splitlines():
                statement += line + '\n'
                if sqlite3.complete_statement(statement):
                    if statement.strip():
                        statements.append(statement)
                    statement = ''
            if statement.strip():
                raise ValueError(f'Incomplete SQL statement in migration {migration.name}')
            with _sqlite_connect() as c:
                rebuild_references = migration.name.startswith(('035_', '042_'))
                if rebuild_references:
                    c.execute('PRAGMA foreign_keys=OFF')
                c.execute('BEGIN IMMEDIATE')
                try:
                    # Another process (API or scheduler) may have applied this migration
                    # since we built the pending list. Check while holding the write lock.
                    if c.execute('SELECT 1 FROM sports_ai_schema_migrations WHERE name=?',
                                 (migration.name,)).fetchone():
                        c.rollback()
                        if rebuild_references:
                            c.execute('PRAGMA foreign_keys=ON')
                        continue
                    for sql_statement in statements:
                        c.execute(sql_statement)
                    c.execute('INSERT INTO sports_ai_schema_migrations(name,applied_at) VALUES(?,?)',
                              (migration.name, datetime.now(timezone.utc).isoformat()))
                    if migration.name.startswith('042_'):
                        # This migration rebuilds categories only. Historical violations in
                        # unrelated tables must not prevent the API from starting.
                        for table in ('sports_ai_high_odds_tickets', 'sports_ai_high_odds_runs'):
                            if c.execute(f'''SELECT 1 FROM {table} child
        LEFT JOIN sports_ai_high_odds_categories parent ON parent.id=child.category_id
        WHERE child.category_id IS NOT NULL AND parent.id IS NULL LIMIT 1''').fetchone():
                                raise ValueError('Jackpot migration failed category foreign key validation')
                    c.commit()
                    if rebuild_references:
                        c.execute('PRAGMA foreign_keys=ON')
                except BaseException:
                    c.rollback()
                    if rebuild_references:
                        c.execute('PRAGMA foreign_keys=ON')
                    raise


# =============================================================================== SQL translation

_TOKEN = re.compile(r"""('(?:[^']|'')*')|("(?:[^"]|"")*")|(--[^\n]*)|(/\*.*?\*/)""", re.S)
_DATE_FUNCS = re.compile(r'\b(date|datetime|time)\s*\(', re.I)
_MIXED_ALIAS = re.compile(r'\bAS\s+([A-Za-z_][A-Za-z0-9_]*)\b')
_GLOB = re.compile(r"\bGLOB\s*'((?:[^']|'')*)'", re.I)
_WRITE = re.compile(r'^\s*(?:WITH\b.*?\)\s*)?(INSERT|UPDATE|DELETE|REPLACE)\b', re.I | re.S)
_DDL = re.compile(r'^\s*(CREATE|ALTER|DROP)\b', re.I)


def _glob_to_regex(match):
    pattern = match.group(1).replace("''", "'")
    out = []
    for char in pattern:
        if char == '*':
            out.append('.*')
        elif char == '?':
            out.append('.')
        elif char in '[]':
            out.append(char)
        else:
            out.append(re.escape(char))
    regex = '^' + ''.join(out) + '$'
    return "~ '" + regex.replace("'", "''") + "'"


def _code(part, named):
    """Transform a SQL fragment that is outside string literals, identifiers and comments."""
    part = part.replace('%', '%%')
    if named:
        part = re.sub(r'(?<![:\w]):([A-Za-z_][A-Za-z0-9_]*)', r'%(\1)s', part)
    # SQLite takes any number as a condition: CASE WHEN ? THEN ... with 0/1 bound.
    part = re.sub(r'\bCASE\s+WHEN\s+\?\s+THEN\b', 'CASE WHEN CAST(? AS BIGINT) <> 0 THEN', part, flags=re.I)
    # A bare parameter tested for NULL has no type PostgreSQL can infer ("? IS NULL OR col = ?").
    part = re.sub(r'(?<![\w.])\?(\s+IS\s+(?:NOT\s+)?NULL\b)', r'CAST(? AS TEXT)\1', part, flags=re.I)
    # SQLite's IS / IS NOT compare any two values NULL-safely ("a.x IS b.x", "line IS ?").
    _keep = r'(?!NULL\b|TRUE\b|FALSE\b|UNKNOWN\b|DISTINCT\b|NOT\b|JSON\b|DOCUMENT\b)'
    part = re.sub(r'\bIS\s+NOT\s+' + _keep, 'IS DISTINCT FROM ', part, flags=re.I)
    part = re.sub(r'\bIS\s+' + _keep, 'IS NOT DISTINCT FROM ', part, flags=re.I)
    part = part.replace('?', '%s')
    part = part.replace('==', '=')
    part = re.sub(r'(?<!I)\bLIKE\b', 'ILIKE', part, flags=re.I)
    part = re.sub(r'\bNOT\s+INDEXED\b|\bINDEXED\s+BY\s+\w+', '', part, flags=re.I)
    part = _DATE_FUNCS.sub(lambda m: f'sqlite_{m.group(1).lower()}(', part)
    part = re.sub(r'\bCURRENT_TIMESTAMP\b', 'sqlite_now()', part, flags=re.I)
    part = re.sub(r'\bCURRENT_DATE\b', "sqlite_date('now')", part, flags=re.I)
    part = re.sub(r'\bAS\s+REAL\s*\)', 'AS DOUBLE PRECISION)', part, flags=re.I)
    part = re.sub(r'\bAS\s+INTEGER\s*\)', 'AS BIGINT)', part, flags=re.I)
    part = re.sub(r'\bIN\s*\(\s*\)', 'IN (NULL)', part, flags=re.I)
    # SQLite accepts CROSS JOIN with an ON clause (an inner join with a join-order hint).
    part = re.sub(r'\bCROSS\s+JOIN\b(?=\s+[\w."]+(?:\s+(?:AS\s+)?(?!ON\b)\w+)?\s+ON\b)', 'JOIN', part, flags=re.I)
    part = _MIXED_ALIAS.sub(
        lambda m: f'AS "{m.group(1)}"' if (m.group(1) != m.group(1).lower() and m.group(1) != m.group(1).upper())
        else m.group(0), part)
    return part


_JSON_CALL = r"json_extract\(\s*[\w.]+\s*,\s*'[^']*'\s*\)"


def _json_types(text):
    """SQLite's json_extract() returns typed values; the PostgreSQL one returns text. Add the
    casts SQLite's typing implied: numeric next to <, >, / or a numeric COALESCE default, and
    text equality against a bound parameter (5 = 5 and '5' = '5' both hold)."""
    text = re.sub(rf'({_JSON_CALL})\s*(=|<>|!=)\s*\?', r'\1 \2 CAST(? AS TEXT)', text, flags=re.I)
    text = re.sub(rf'COALESCE\(\s*({_JSON_CALL})\s*,\s*(-?\d+(?:\.\d+)?)\s*\)',
                  r'COALESCE(CAST(\1 AS DOUBLE PRECISION), \2)', text, flags=re.I)
    text = re.sub(rf'(?<!CAST\()({_JSON_CALL})(?=\s*(?:>=|<=|>|<|\*|/))',
                  r'CAST(\1 AS DOUBLE PRECISION)', text, flags=re.I)
    text = re.sub(rf'(?<=[/*])\s*({_JSON_CALL})', r' CAST(\1 AS DOUBLE PRECISION)', text, flags=re.I)
    text = re.sub(rf'(?<!CAST\()({_JSON_CALL})(\s*(?:=|<>|!=)\s*-?\d+(?:\.\d+)?\b)',
                  r'CAST(\1 AS DOUBLE PRECISION)\2', text, flags=re.I)
    return text


@lru_cache(maxsize=4096)
def translate(sql, named=False):
    """SQLite SQL -> PostgreSQL SQL (psycopg placeholders)."""
    text = sql.strip().rstrip(';').strip()
    text = _GLOB.sub(_glob_to_regex, text)
    text = _json_types(text)
    ignore = bool(re.match(r'\s*INSERT\s+OR\s+IGNORE\b', text, re.I))
    if ignore:
        text = re.sub(r'^\s*INSERT\s+OR\s+IGNORE\b', 'INSERT', text, flags=re.I)
    if re.match(r'\s*(INSERT\s+OR\s+REPLACE|REPLACE\s+INTO)\b', text, re.I):
        # PgConnection.execute rewrites these into an upsert before translation.
        raise sqlite3.OperationalError('INSERT OR REPLACE needs the table key; run it through PgConnection.execute')
    out, position = [], 0
    for match in _TOKEN.finditer(text):
        out.append(_code(text[position:match.start()], named))
        literal, identifier, line_comment, block_comment = match.groups()
        if literal is not None:
            out.append(literal.replace('%', '%%'))
        elif identifier is not None:
            # SQLite reads "" (and a double-quoted word that is no column) as a string literal.
            out.append("''" if identifier == '""' else identifier.replace('%', '%%'))
        position = match.end()
    out.append(_code(text[position:], named))
    result = ''.join(out)
    if ignore:
        returning = re.search(r'\bRETURNING\b', result, re.I)
        if returning:
            result = result[:returning.start()] + 'ON CONFLICT DO NOTHING ' + result[returning.start():]
        else:
            result += ' ON CONFLICT DO NOTHING'
    return result


# =============================================================================== PostgreSQL backend

WRITER_LOCK_KEY = 0x5350_4F52   # pg_advisory_xact_lock key shared by every write transaction
MIGRATION_LOCK_KEY = 0x5350_4D49
COMPAT_FILE = _HERE / 'pg_compat.sql'
SERIAL_WRITES = os.environ.get('SPORTS_PG_SERIAL_WRITES', '1') != '0'
SLOW_MS = float(os.environ.get('SPORTS_PG_SLOW_MS', '0') or 0)   # log statements slower than this


class Row:
    """sqlite3.Row equivalent: index and column-name access, iteration, keys(), dict(row).

    Deliberately not a tuple subclass: FastAPI encodes tuples as lists but objects with keys()
    as dicts, which is what it did with sqlite3.Row.
    """
    __slots__ = ('_values',)
    _names = ()
    _index = {}

    def __init__(self, values):
        self._values = tuple(values)

    def __getitem__(self, key):
        if isinstance(key, str):
            position = self._index.get(key)
            if position is None:
                position = self._index.get(key.lower())
                if position is None:
                    raise IndexError(f'No item with that key: {key}')
            return self._values[position]
        return self._values[key]

    def keys(self):
        return list(self._names)

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def __eq__(self, other):
        if isinstance(other, Row):
            return self._names == other._names and self._values == other._values
        return NotImplemented

    def __hash__(self):
        return hash((self._names, self._values))

    def __repr__(self):
        return f'<Row {dict(zip(self._names, self._values))!r}>'


@lru_cache(maxsize=2048)
def _row_class(names):
    index = {}
    for position, name in enumerate(names):
        index.setdefault(name, position)
        index.setdefault(name.lower(), position)
    return type('Row', (Row,), {'__slots__': (), '_names': names, '_index': index})


def _row_factory(cursor):
    if cursor.description is None:
        return None
    cls = _row_class(tuple(column.name for column in cursor.description))
    return lambda values: cls(values)


_ROWID_WORD = re.compile(r'\browid\b', re.I)
_PRIMARY_KEYS = {}
_OR_IGNORE = re.compile(r'\s*INSERT\s+OR\s+IGNORE\b', re.I)


def _row_factory_without_rowid(cursor):
    if cursor.description is None:
        return None
    names = [column.name for column in cursor.description]
    if 'rowid' not in names:
        return _row_factory(cursor)
    drop = names.index('rowid')
    cls = _row_class(tuple(name for position, name in enumerate(names) if position != drop))
    return lambda values: cls(values[:drop] + values[drop + 1:])


def _ignorable(error):
    _psycopg, _adapt, errors, _pq = _pg_modules()
    return isinstance(error, (errors.NotNullViolation, errors.CheckViolation, errors.UniqueViolation))


def _pg_modules():
    import psycopg
    from psycopg import adapt, errors, pq
    return psycopg, adapt, errors, pq


@lru_cache(maxsize=1)
def _adapters():
    """Parameter dumpers and result loaders that reproduce sqlite3's Python types."""
    psycopg, adapt, _errors, _pq = _pg_modules()

    class TextDumper(adapt.Dumper):
        oid = 0  # unknown: lets the server infer the type, as SQLite's affinity did

        def dump(self, obj):
            return self.render(obj).encode('utf-8')

    class BoolDumper(TextDumper):
        # Untyped '1'/'0': PostgreSQL reads it as boolean in CASE WHEN ? and as 1/0 in a BIGINT
        # column or comparison, which is how SQLite treated Python booleans.
        def render(self, obj):
            return '1' if obj else '0'

    class DatetimeDumper(TextDumper):
        def render(self, obj):
            return obj.isoformat(' ')   # sqlite3's default datetime adapter

    class DateDumper(TextDumper):
        def render(self, obj):
            return obj.isoformat()

    class DecimalDumper(TextDumper):
        def render(self, obj):
            return str(obj)

    class NumericLoader(adapt.Loader):
        # SUM()/AVG() over BIGINT and numeric literals produce NUMERIC; SQLite returned int/float.
        def load(self, data):
            text = bytes(data).decode()
            if '.' in text or 'e' in text.lower() or text in ('NaN', 'Infinity', '-Infinity'):
                return float(text)
            return int(text)

    class NamedDateLoader(adapt.Loader):
        def load(self, data):
            return bytes(data).decode()

    adapters = adapt.AdaptersMap(psycopg.adapters)
    adapters.register_dumper(bool, BoolDumper)
    adapters.register_dumper(datetime, DatetimeDumper)
    adapters.register_dumper(date, DateDumper)
    adapters.register_dumper(Decimal, DecimalDumper)
    adapters.register_loader('numeric', NumericLoader)
    # Values of type date/timestamp come back as the text SQLite would have returned.
    for name in ('date', 'timestamp', 'timestamptz', 'time'):
        adapters.register_loader(name, NamedDateLoader)
    return adapters


_slot_of = {}      # test database path -> recycled schema
_slot_owner = {}   # recycled schema -> path
_dirty_slots = set()
_slot_counter = [0]


def _schema_for(path):
    path = Path(path).resolve()
    if path == DEFAULT_DB.resolve():
        return os.environ.get('SPORTS_PG_SCHEMA', 'public')
    slots = int(os.environ.get('SPORTS_PG_TEST_SLOTS', '0') or 0)
    if slots <= 0:
        return 't_' + hashlib.sha1(str(path).lower().encode()).hexdigest()[:16]
    # Test runs: a handful of schemas are reused round-robin. A newly seen path gets the least
    # recently assigned schema, which is emptied and re-seeded on its first use.
    key = str(path).lower()
    if key not in _slot_of:
        slot = f't_slot{_slot_counter[0] % slots}'
        _slot_counter[0] += 1
        previous = _slot_owner.get(slot)
        if previous:
            _slot_of.pop(previous, None)
        _slot_owner[slot] = key
        _slot_of[key] = slot
        _dirty_slots.add(slot)
    return _slot_of[key]


def _translate_error(error):
    """psycopg exception -> the sqlite3 exception callers already handle."""
    _psycopg, _adapt, errors, _pq = _pg_modules()
    message = str(error).strip().splitlines()[0] if str(error).strip() else type(error).__name__
    if isinstance(error, errors.IntegrityError):
        return sqlite3.IntegrityError(message)
    if isinstance(error, (errors.LockNotAvailable, errors.QueryCanceled, errors.DeadlockDetected,
                          errors.SerializationFailure)):
        return sqlite3.OperationalError(f'database is locked ({message})')
    if isinstance(error, errors.RaiseException):
        return sqlite3.IntegrityError(message)
    return sqlite3.OperationalError(message)


class _Cursor:
    """Result of PgConnection.execute(): the sqlite3 cursor surface the code uses."""
    __slots__ = ('_cursor', 'rowcount', 'description', 'lastrowid')

    def __init__(self, cursor):
        self._cursor = cursor
        self.rowcount = cursor.rowcount if cursor is not None else -1
        self.description = cursor.description if cursor is not None else None
        self.lastrowid = None

    def fetchone(self):
        if self.description is None:
            return None
        return self._cursor.fetchone()

    def fetchall(self):
        if self.description is None:
            return []
        return self._cursor.fetchall()

    def fetchmany(self, size=None):
        if self.description is None:
            return []
        return self._cursor.fetchmany(size) if size else self._cursor.fetchmany()

    def __iter__(self):
        if self.description is None:
            return iter(())
        return iter(self._cursor)

    def close(self):
        if self._cursor is not None:
            self._cursor.close()


class _Static(_Cursor):
    """A cursor over fixed rows (PRAGMA emulation)."""
    __slots__ = ('_rows', '_position')

    def __init__(self, names, rows):
        super().__init__(None)
        cls = _row_class(tuple(names))
        self._rows = [cls(tuple(row)) for row in rows]
        self._position = 0
        self.description = [(name,) for name in names] if names else None

    def fetchone(self):
        if self._position >= len(self._rows):
            return None
        self._position += 1
        return self._rows[self._position - 1]

    def fetchall(self):
        rows, self._position = self._rows[self._position:], len(self._rows)
        return rows

    def fetchmany(self, size=None):
        size = size or 1
        rows = self._rows[self._position:self._position + size]
        self._position += len(rows)
        return rows

    def __iter__(self):
        return iter(self.fetchall())

    def close(self):
        pass


class PgConnection:
    """sqlite3.Connection-compatible wrapper around a psycopg connection.

    Transactions follow sqlite3's legacy mode: reads run in autocommit; the first INSERT, UPDATE
    or DELETE (or an explicit BEGIN) opens a transaction that commit()/rollback() ends. Write
    transactions take one advisory lock, so writers are serialized exactly as SQLite serialized
    them, while readers never wait. Inside a transaction each write runs under a savepoint, so a
    caught IntegrityError leaves the transaction usable, as it does in SQLite.
    """

    def __init__(self, raw, schema, lock_timeout_ms):
        self._raw = raw
        self.schema = schema
        self._lock_timeout_ms = lock_timeout_ms
        self._locked = False
        self._savepoint_names = []
        self._savepoint_opened = None
        self.row_factory = None
        self.isolation_level = ''
        self.foreign_keys_off = False

    # -------------------------------------------------------------- transactions
    @property
    def in_transaction(self):
        _psycopg, _adapt, _errors, pq = _pg_modules()
        return self._raw.info.transaction_status != pq.TransactionStatus.IDLE

    def _run(self, sql, params=None):
        started = time.perf_counter() if SLOW_MS else 0
        try:
            return self._raw.execute(sql, params)
        except Exception as error:
            if _is_pg_error(error):
                raise _translate_error(error) from error
            raise
        finally:
            if SLOW_MS and (time.perf_counter() - started) * 1000 >= SLOW_MS:
                import sys
                print(f'[db slow {(time.perf_counter() - started) * 1000:.0f} ms] {sql[:300]!r}', file=sys.stderr)

    def _begin(self, lock):
        if not self.in_transaction:
            self._run('BEGIN')
            self._locked = False
            self._savepoint_names = []
            self._savepoint_opened = None
        if lock and SERIAL_WRITES and not self._locked:
            self._run('SELECT pg_advisory_xact_lock(%s)', (WRITER_LOCK_KEY,))
            self._locked = True

    def commit(self):
        if self.in_transaction:
            self._run('COMMIT')
        self._locked = False
        self._savepoint_names = []
        self._savepoint_opened = None

    def rollback(self):
        if self.in_transaction:
            try:
                self._raw.execute('ROLLBACK')
            except Exception:
                pass
        self._locked = False
        self._savepoint_names = []
        self._savepoint_opened = None

    def close(self):
        self._raw.close()

    # -------------------------------------------------------------- statements
    def _special(self, sql):
        """Transaction control and PRAGMA statements. Returns a cursor, or None if not special."""
        head = sql.strip().rstrip(';').strip()
        upper = head.upper()
        if re.match(r'BEGIN(\s+(DEFERRED|IMMEDIATE|EXCLUSIVE))?(\s+TRANSACTION)?$', upper):
            self._begin(lock=not upper.startswith('BEGIN DEFERRED') and upper != 'BEGIN' and upper != 'BEGIN TRANSACTION')
            return _Static([], [])
        if upper in ('COMMIT', 'END', 'COMMIT TRANSACTION', 'END TRANSACTION'):
            self.commit()
            return _Static([], [])
        if upper in ('ROLLBACK', 'ROLLBACK TRANSACTION'):
            self.rollback()
            return _Static([], [])
        if upper.startswith('SAVEPOINT') or upper.startswith('RELEASE') or upper.startswith('ROLLBACK TO'):
            name = head.split()[-1].strip('"').lower()
            if upper.startswith('SAVEPOINT'):
                opened = not self.in_transaction
                self._begin(lock=True)
                cursor = _Cursor(self._run(head))
                self._savepoint_names.append(name)
                if opened:
                    self._savepoint_opened = name
                return cursor
            if not self.in_transaction or name not in self._savepoint_names:
                # SQLite raises without harming the transaction; PostgreSQL would abort it.
                raise sqlite3.OperationalError(f'no such savepoint: {name}')
            if upper.startswith('RELEASE') and self._savepoint_names[0] == name and self._savepoint_opened == name:
                # Releasing the savepoint that opened the transaction commits it (SQLite).
                self.commit()
                return _Static([], [])
            cursor = _Cursor(self._run(head))
            if upper.startswith('RELEASE'):
                del self._savepoint_names[len(self._savepoint_names) - 1 - self._savepoint_names[::-1].index(name):]
            else:   # ROLLBACK TO keeps the savepoint itself, drops the ones opened after it
                del self._savepoint_names[len(self._savepoint_names) - self._savepoint_names[::-1].index(name):]
            return cursor
        if upper.startswith('PRAGMA'):
            return self._pragma(head)
        return None

    def _pragma(self, statement):
        body = statement[6:].strip()
        name = re.match(r'(\w+)', body).group(1).lower() if re.match(r'(\w+)', body) else ''
        if name == 'journal_mode':
            return _Static(['journal_mode'], [('postgresql',)])
        if name == 'database_list':
            return _Static(['seq', 'name', 'file'], [(0, 'main', f'postgresql:{self.schema}')])
        if name == 'table_info':
            table = re.search(r'\(\s*["\']?(\w+)', body).group(1)
            rows = self._raw.execute(
                """SELECT ordinal_position - 1, column_name, upper(data_type), (is_nullable = 'NO')::int,
                          column_default, 0
                   FROM information_schema.columns WHERE table_schema = %s AND table_name = %s
                     AND column_name <> 'rowid' ORDER BY ordinal_position""", (self.schema, table)).fetchall()
            return _Static(['cid', 'name', 'type', 'notnull', 'dflt_value', 'pk'], rows)
        if name == 'integrity_check' or name == 'quick_check':
            return _Static([name], [('ok',)])
        if name == 'foreign_keys':
            value = body.split('=', 1)[1].strip().lower() if '=' in body else None
            if value in ('off', '0', 'false', 'no'):
                # Test fixtures switch foreign keys off to insert partial rows (this session only).
                self._raw.execute('SET session_replication_role = replica')
                self.foreign_keys_off = True
            elif value in ('on', '1', 'true', 'yes'):
                self._raw.execute('SET session_replication_role = origin')
                self.foreign_keys_off = False
            else:
                return _Static(['foreign_keys'], [(0 if self.foreign_keys_off else 1,)])
            return _Static([], [])
        return _Static([], [])   # busy_timeout, cache_size, journal tuning, ...: not applicable

    def _cursor(self, sql):
        # SQLite's SELECT * never includes the implicit rowid; hide our rowid column unless the
        # statement asks for it by name.
        factory = _row_factory if _ROWID_WORD.search(sql) else _row_factory_without_rowid
        return self._raw.cursor(row_factory=factory)

    def _primary_key(self, table):
        key = (self.schema, table)
        if key not in _PRIMARY_KEYS:
            rows = self._raw.execute(
                """SELECT a.attname FROM pg_index i
                   JOIN pg_class c ON c.oid = i.indrelid JOIN pg_namespace n ON n.oid = c.relnamespace
                   JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY(i.indkey)
                   WHERE i.indisprimary AND n.nspname = %s AND c.relname = %s""", (self.schema, table)).fetchall()
            _PRIMARY_KEYS[key] = [row[0] for row in rows]
        return _PRIMARY_KEYS[key]

    def _unique_keys(self, table):
        key = ('unique', self.schema, table)
        if key not in _PRIMARY_KEYS:
            rows = self._raw.execute(
                """SELECT i.indexrelid, array_agg(a.attname ORDER BY a.attnum) FROM pg_index i
                   JOIN pg_class c ON c.oid = i.indrelid JOIN pg_namespace n ON n.oid = c.relnamespace
                   JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY(i.indkey)
                   WHERE i.indisunique AND i.indpred IS NULL AND n.nspname = %s AND c.relname = %s
                     AND a.attname <> 'rowid'
                   GROUP BY i.indexrelid""", (self.schema, table)).fetchall()
            _PRIMARY_KEYS[key] = [list(row[1]) for row in rows]
        return _PRIMARY_KEYS[key]

    def _replace(self, sql, parameters):
        """SQLite REPLACE: delete every row that conflicts on any unique key, then insert.
        Handles INSERT OR REPLACE INTO t(cols) VALUES(?, ...); returns None for other shapes."""
        match = re.match(r'\s*(?:INSERT\s+OR\s+REPLACE|REPLACE)\s+INTO\s+"?(\w+)"?\s*\(([^)]*)\)\s*VALUES\s*\(([^()]*)\)\s*;?\s*$',
                         sql, re.I | re.S)
        if not match or isinstance(parameters, dict):
            return None
        table = match.group(1)
        columns = [c.strip().strip('"') for c in match.group(2).split(',')]
        values = [v.strip() for v in match.group(3).split(',')]
        simple = re.compile(r"^(\?|'(?:[^']|'')*'|-?\d+(?:\.\d+)?|NULL)$", re.I)
        if len(values) != len(columns) or not all(simple.match(v) for v in values) \
                or values.count('?') != len(parameters):
            return None
        bound, position = {}, 0
        for column, value in zip(columns, values):
            if value == '?':
                bound[column] = ('%s', parameters[position])
                position += 1
            else:
                bound[column] = (value, None)
        self._begin(lock=True)
        for unique in self._unique_keys(table):
            if all(column in bound for column in unique):
                where = ' AND '.join(f'"{column}" IS NOT DISTINCT FROM {bound[column][0]}' for column in unique)
                self._write(f'DELETE FROM {table}', f'DELETE FROM "{table}" WHERE {where}',
                            tuple(bound[column][1] for column in unique if bound[column][0] == '%s'))
        insert = f'INSERT INTO {table} ({match.group(2)}) VALUES ({match.group(3)})'
        return self._write(insert, translate(insert), tuple(parameters))

    def _replace_to_upsert(self, sql):
        """INSERT OR REPLACE INTO t(cols) ... -> INSERT ... ON CONFLICT (primary key) DO UPDATE."""
        match = re.match(r'\s*(?:INSERT\s+OR\s+REPLACE|REPLACE)\s+INTO\s+"?(\w+)"?\s*\(([^)]*)\)(.*)$', sql, re.I | re.S)
        if not match:
            raise sqlite3.OperationalError('INSERT OR REPLACE needs an explicit column list on PostgreSQL')
        table, columns, rest = match.group(1), [c.strip().strip('"') for c in match.group(2).split(',')], match.group(3)
        keys = self._primary_key(table)
        if not keys:
            raise sqlite3.OperationalError(f'INSERT OR REPLACE into {table}: the table has no primary key')
        updates = ', '.join(f'"{c}" = EXCLUDED."{c}"' for c in columns if c not in keys) or f'"{keys[0]}" = EXCLUDED."{keys[0]}"'
        return (f'INSERT INTO {table} ({", ".join(columns)}){rest.rstrip().rstrip(";")} '
                f'ON CONFLICT ({", ".join(keys)}) DO UPDATE SET {updates}')

    def set_trace_callback(self, callback):
        """sqlite3-compatible statement tracing (the callback receives each SQL statement)."""
        self._trace = callback

    def execute(self, sql, parameters=()):
        if getattr(self, '_trace', None):
            self._trace(sql)
        special = self._special(sql)
        if special is not None:
            return special
        if re.match(r'\s*(INSERT\s+OR\s+REPLACE|REPLACE\s+INTO)\b', sql, re.I):
            replaced = self._replace(sql, parameters)
            if replaced is not None:
                return replaced
            sql = self._replace_to_upsert(sql)
        named = isinstance(parameters, dict)
        statement = translate(sql, named)
        params = parameters if named else tuple(parameters or ())
        if _WRITE.match(sql):
            self._begin(lock=True)
            return self._write(sql, statement, params)
        if _DDL.match(sql) and self.in_transaction:
            self._begin(lock=True)
        started = time.perf_counter() if SLOW_MS else 0
        try:
            return _Cursor(self._cursor(sql).execute(statement, params))
        except Exception as error:
            if _is_pg_error(error):
                raise _translate_error(error) from error
            raise
        finally:
            if SLOW_MS and (time.perf_counter() - started) * 1000 >= SLOW_MS:
                import sys
                print(f'[db slow {(time.perf_counter() - started) * 1000:.0f} ms] {statement[:300]!r}', file=sys.stderr)

    def _write(self, sql, statement, params):
        self._run('SAVEPOINT sqlite_stmt')
        started = time.perf_counter() if SLOW_MS else 0
        try:
            cursor = self._cursor(sql).execute(statement, params)
            if SLOW_MS and (time.perf_counter() - started) * 1000 >= SLOW_MS:
                import sys
                print(f'[db slow {(time.perf_counter() - started) * 1000:.0f} ms] {statement[:300]!r}', file=sys.stderr)
        except Exception as error:
            try:
                self._raw.execute('ROLLBACK TO SAVEPOINT sqlite_stmt')
                self._raw.execute('RELEASE SAVEPOINT sqlite_stmt')
            except Exception:
                pass
            if _is_pg_error(error) and _OR_IGNORE.match(sql) and _ignorable(error):
                # SQLite's INSERT OR IGNORE also skips rows that break NOT NULL or CHECK.
                return _Static([], [])
            if _is_pg_error(error):
                raise _translate_error(error) from error
            raise
        result = _Cursor(cursor)
        self._raw.execute('RELEASE SAVEPOINT sqlite_stmt')
        return result

    def executemany(self, sql, seq_of_parameters):
        rows = list(seq_of_parameters)
        named = bool(rows) and isinstance(rows[0], dict)
        statement = translate(sql, named)
        if _WRITE.match(sql):
            self._begin(lock=True)
        cursor = self._raw.cursor()
        try:
            cursor.executemany(statement, rows if named else [tuple(row) for row in rows])
        except Exception as error:
            if _is_pg_error(error):
                raise _translate_error(error) from error
            raise
        return _Cursor(cursor)

    def executescript(self, script):
        from pg_schema import split_statements
        for statement in split_statements(script):
            self.execute(statement)
        self.commit()

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        if kind is None:
            self.commit()
        else:
            self.rollback()
        return False


def _is_pg_error(error):
    try:
        import psycopg
    except ImportError:
        return False
    return isinstance(error, psycopg.Error)


class _Pool:
    """Small per-process pool of idle connections keyed by (conninfo, schema)."""

    def __init__(self, size=12):
        self.size = size
        self.idle = {}
        self.lock = threading.Lock()

    def get(self, key):
        with self.lock:
            bucket = self.idle.get(key) or []
            while bucket:
                raw = bucket.pop()
                if not raw.closed and not raw.broken:
                    return raw
        return None

    def put(self, key, raw):
        if raw.closed or raw.broken:
            return
        with self.lock:
            bucket = self.idle.setdefault(key, [])
            if len(bucket) < self.size:
                bucket.append(raw)
                return
        raw.close()

    def clear(self):
        with self.lock:
            buckets, self.idle = self.idle, {}
        for bucket in buckets.values():
            for raw in bucket:
                try:
                    raw.close()
                except Exception:
                    pass


_pool = _Pool()
_known_schemas = set()


def _open_raw(url, schema):
    psycopg, _adapt, _errors, _pq = _pg_modules()
    raw = psycopg.connect(url, autocommit=True, row_factory=_row_factory, prepare_threshold=None,
                          context=_adapters(),
                          application_name=os.environ.get('SPORTS_PG_APP', 'sports-ai'))
    raw.execute(f'SET search_path = "{schema}", sqlite_compat, pg_catalog')
    raw.execute("SET TIME ZONE 'UTC'")
    # Session planner settings for this workload (NVMe SSD, short OLTP queries plus page reports).
    # Server-wide memory (shared_buffers) is left to the DBA because other databases share it.
    raw.execute(os.environ.get(
        'SPORTS_PG_SESSION_SETTINGS',
        "SET random_page_cost = 1.1; SET effective_io_concurrency = 200; SET work_mem = '32MB'; "
        "SET jit = off; SET effective_cache_size = '6GB'"))
    return raw


def _pg_connect_raw(busy_timeout_ms):
    url = database_url()
    schema = _schema_for(DB)
    key = (url, schema)
    raw = _pool.get(key)
    if raw is None:
        raw = _open_raw(url, schema)
    raw.execute(f'SET lock_timeout = {int(busy_timeout_ms)}')
    return key, raw, schema


@contextmanager
def _pg_connect(busy_timeout_ms=20000):
    schema = _schema_for(DB)
    if schema not in _known_schemas or schema in _dirty_slots:
        _pg_initialize()
    key, raw, schema = _pg_connect_raw(busy_timeout_ms)
    connection = PgConnection(raw, schema, busy_timeout_ms)
    healthy = True
    try:
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        try:
            if connection.in_transaction:
                raw.execute('ROLLBACK')
            if connection.foreign_keys_off:
                raw.execute('SET session_replication_role = origin')
        except Exception:
            healthy = False
        if healthy:
            _pool.put(key, raw)
        else:
            raw.close()


def _admin_url(url):
    """Same server, maintenance database (for CREATE DATABASE)."""
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    params = conninfo_to_dict(url)
    params['dbname'] = 'postgres'
    return make_conninfo(**params)


_ensured = set()


def _ensure_database(url):
    if url in _ensured:
        return
    psycopg, _adapt, errors, _pq = _pg_modules()
    try:
        psycopg.connect(url, connect_timeout=10).close()
    except psycopg.OperationalError as error:
        if 'does not exist' not in str(error):
            raise
        from psycopg.conninfo import conninfo_to_dict
        name = conninfo_to_dict(url)['dbname']
        with psycopg.connect(_admin_url(url), autocommit=True) as admin:
            try:
                admin.execute(f"CREATE DATABASE \"{name}\" ENCODING 'UTF8' TEMPLATE template0")
            except errors.DuplicateDatabase:
                pass
    _ensured.add(url)


def _install_compat(raw):
    text = COMPAT_FILE.read_text(encoding='utf-8')
    digest = hashlib.sha1(text.encode()).hexdigest()
    raw.execute('CREATE SCHEMA IF NOT EXISTS sqlite_compat')
    raw.execute('CREATE TABLE IF NOT EXISTS sqlite_compat.meta (key text PRIMARY KEY, value text NOT NULL)')
    row = raw.execute("SELECT value FROM sqlite_compat.meta WHERE key = 'compat_digest'").fetchone()
    if row and row[0] == digest:
        return
    raw.execute(text)
    raw.execute("""INSERT INTO sqlite_compat.meta(key, value) VALUES ('compat_digest', %s)
                   ON CONFLICT (key) DO UPDATE SET value = excluded.value""", (digest,))


@lru_cache(maxsize=1)
def _baseline():
    """(DDL statements, migration names, seed rows) for a brand-new schema."""
    from pg_schema import build_schema_statements, replay_migrations, seed_rows
    replay, names = replay_migrations()
    try:
        statements, _ = build_schema_statements(replay)
        return statements, names, seed_rows(replay)
    finally:
        replay.close()


def _insert_seed(raw, seed):
    for table, columns, rows in seed:
        quoted = ', '.join(f'"{column}"' for column in columns)
        marks = ', '.join(['%s'] * len(columns))
        with raw.cursor() as cursor:
            cursor.executemany(f'INSERT INTO "{table}" ({quoted}) VALUES ({marks}) ON CONFLICT DO NOTHING',
                               [tuple(row) for row in rows])


def sync_identities(raw, schema, include_rowid=True):
    """Move identity sequences past the highest stored value (after explicit-value inserts)."""
    columns = raw.execute(
        """SELECT c.relname, a.attname, pg_get_serial_sequence(format('%%I.%%I', n.nspname, c.relname), a.attname)
           FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname = %s AND a.attidentity <> '' AND NOT a.attisdropped AND c.relkind = 'r'
             AND (%s = 1 OR a.attname <> 'rowid')""", (schema, 1 if include_rowid else 0)).fetchall()
    for table, column, sequence in columns:
        if sequence:
            raw.execute(
                f"""SELECT setval('{sequence}', GREATEST((SELECT COALESCE(MAX("{column}"), 0)
                                                     FROM "{schema}"."{table}"), 1))""")


def _reset_schema(raw, schema):
    """Empty a recycled test schema and restore the migration seed rows."""
    _statements, names, seed = _baseline()
    tables = [row[0] for row in raw.execute(
        'SELECT tablename FROM pg_tables WHERE schemaname = %s', (schema,)).fetchall()]
    with raw.transaction():
        raw.execute('SET LOCAL session_replication_role = replica')   # skip FK and immutability triggers
        raw.execute('SET LOCAL synchronous_commit = off')
        # Evidence tables keep their immutability triggers even in replica mode; TRUNCATE skips
        # row triggers, plain DELETE (cheaper for the many small tables) is used elsewhere.
        guarded = {row[0] for row in raw.execute(
            """SELECT DISTINCT c.relname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
               JOIN pg_namespace n ON n.oid = c.relnamespace
               WHERE n.nspname = %s AND NOT t.tgisinternal AND t.tgenabled = 'A'""", (schema,)).fetchall()}
        if guarded:
            raw.execute('TRUNCATE ' + ', '.join(f'"{schema}"."{table}"' for table in sorted(guarded)))
        for table in tables:
            if table not in guarded:
                raw.execute(f'DELETE FROM "{schema}"."{table}"')
        _insert_seed(raw, seed)
        stamp = datetime.now(timezone.utc).isoformat()
        with raw.cursor() as cursor:
            cursor.executemany('INSERT INTO sports_ai_schema_migrations(name, applied_at) VALUES (%s, %s) '
                               'ON CONFLICT DO NOTHING', [(name, stamp) for name in names])


def _pg_initialize():
    from pg_schema import translate_migration
    url = database_url()
    schema = _schema_for(DB)
    with _migration_lock:
        _ensure_database(url)
        raw = _open_raw(url, schema)
        try:
            raw.execute('SELECT pg_advisory_lock(%s)', (MIGRATION_LOCK_KEY,))
            try:
                _install_compat(raw)
                raw.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
                exists = raw.execute(
                    "SELECT 1 FROM pg_tables WHERE schemaname = %s AND tablename = 'sports_ai_schema_migrations'",
                    (schema,)).fetchone()
                migrations = sorted((_HERE.parent / 'migrations').glob('*.sql'))
                if not exists:
                    statements, names, seed = _baseline()
                    with raw.transaction():
                        raw.execute('SET LOCAL synchronous_commit = off')
                        for statement in statements:
                            raw.execute(statement)
                        _insert_seed(raw, seed)
                        stamp = datetime.now(timezone.utc).isoformat()
                        for name in names:
                            raw.execute('INSERT INTO sports_ai_schema_migrations(name, applied_at) VALUES (%s, %s) '
                                        'ON CONFLICT DO NOTHING', (name, stamp))
                        sync_identities(raw, schema)
                    _dirty_slots.discard(schema)
                elif schema in _dirty_slots:
                    _reset_schema(raw, schema)
                    sync_identities(raw, schema, include_rowid=False)
                    _dirty_slots.discard(schema)
                else:
                    applied = {row[0] for row in raw.execute('SELECT name FROM sports_ai_schema_migrations')}
                    for migration in migrations:
                        if migration.name in applied:
                            continue
                        with raw.transaction():
                            for statement in translate_migration(migration.read_text(encoding='utf-8')):
                                if _WRITE.match(statement):
                                    raw.execute(translate(statement))
                                else:
                                    raw.execute(statement)
                            raw.execute('INSERT INTO sports_ai_schema_migrations(name, applied_at) VALUES (%s, %s)',
                                        (migration.name, datetime.now(timezone.utc).isoformat()))
            finally:
                raw.execute('SELECT pg_advisory_unlock(%s)', (MIGRATION_LOCK_KEY,))
        finally:
            raw.close()
    _known_schemas.add(schema)


def drop_schema(path):
    """Remove the PostgreSQL schema that belongs to a (test) database path."""
    schema = _schema_for(path)
    if schema == 'public' or not is_postgres():
        return
    raw = _open_raw(database_url(), schema)
    try:
        raw.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    finally:
        raw.close()
    _known_schemas.discard(schema)
    _pool.clear()


# =============================================================================== public API

@contextmanager
def connect(busy_timeout_ms=20000):
    if is_postgres():
        with _pg_connect(busy_timeout_ms) as connection:
            yield connection
    else:
        with _sqlite_connect(busy_timeout_ms) as connection:
            yield connection


def initialize():
    if is_postgres():
        _known_schemas.discard(_schema_for(DB))
        _pg_initialize()
    else:
        _sqlite_initialize()


def describe():
    """Human-readable description of the active database (for health pages and logs)."""
    if is_postgres():
        from psycopg.conninfo import conninfo_to_dict
        params = conninfo_to_dict(database_url())
        return {'backend': 'postgresql', 'host': params.get('host', 'localhost'), 'port': params.get('port', '5432'),
                'database': params.get('dbname'), 'schema': _schema_for(DB)}
    return {'backend': 'sqlite', 'path': str(DB)}
