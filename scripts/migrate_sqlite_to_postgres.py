"""Copy the Sports AI SQLite database into PostgreSQL (the primary database).

Usage (from the repository root):
    python scripts/migrate_sqlite_to_postgres.py                 # full copy into an empty schema
    python scripts/migrate_sqlite_to_postgres.py --replace       # drop and rebuild the target schema
    python scripts/migrate_sqlite_to_postgres.py --verify        # compare row counts only

The source is opened read-only. The target is SPORTS_DATABASE_URL (environment or
backend/.env.local), schema SPORTS_PG_SCHEMA (default public). Stop the API and worker before
the final copy so no rows are written to SQLite after they were read.

Steps: create UNLOGGED tables (no seed rows), COPY every table in parallel rowid ranges (SQLite's rowid
is kept in each table's rowid column), build indexes and switch each table to LOGGED (crash-safe),
add foreign keys as NOT VALID (historical rows are not
re-checked; new rows are), move identity sequences past the copied values, ANALYZE.
"""
import argparse
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ.setdefault('SPORTS_PG_APP', 'sports-ai-migration')

import db  # noqa: E402
import pg_schema  # noqa: E402

BATCH = 20000
WORKERS = int(os.environ.get('SPORTS_MIGRATION_WORKERS', '7'))
CHUNK = 400000   # rowids per parallel copy task for large tables


def _source(path):
    connection = sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=60)
    connection.execute('PRAGMA query_only=1')
    connection.execute('PRAGMA cache_size=-262144')
    return connection


def _clean(value):
    if isinstance(value, str) and '\x00' in value:
        return value.replace('\x00', '')
    if isinstance(value, bytes):
        return value.decode('utf-8', 'replace').replace('\x00', '')
    return value


def _tables(source):
    return [row[0] for row in source.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def _create_schema(raw, schema, source):
    statements, _names = pg_schema.build_schema_statements(source)
    tables = [s for s in statements if s.lstrip().upper().startswith('CREATE TABLE')]
    rest = [s for s in statements if s not in tables and not s.lstrip().upper().startswith('ALTER TABLE')]
    foreign_keys = []
    for name in _tables(source):
        foreign_keys += [clause for _c, clause in pg_schema.foreign_key_ddl(source, name, not_valid=True)]
    with raw.transaction():
        raw.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        for statement in tables:
            # Load into UNLOGGED tables (no WAL per row); _finish_table makes each one LOGGED
            # (fully crash-safe) after its rows and indexes are in place.
            raw.execute(statement.replace('CREATE TABLE IF NOT EXISTS', 'CREATE UNLOGGED TABLE IF NOT EXISTS', 1))
    return rest, foreign_keys


_ESCAPE = str.maketrans({'\\': '\\\\', '\t': '\\t', '\n': '\\n', '\r': '\\r', '\x00': ''})


def _text(value):
    """One value in PostgreSQL COPY text format."""
    if value is None:
        return '\\N'
    if isinstance(value, str):
        return value.translate(_ESCAPE)
    if isinstance(value, float):
        if value != value:
            return 'NaN'
        if value in (float('inf'), float('-inf')):
            return 'Infinity' if value > 0 else '-Infinity'
        return repr(value)
    if isinstance(value, bytes):
        return value.decode('utf-8', 'replace').translate(_ESCAPE)
    return str(value)


def _copy_table(source, raw, schema, table, low=None, high=None):
    columns = [row[1] for row in source.execute(f'PRAGMA table_info("{table}")')]
    target_columns = {row[0] for row in raw.execute(
        'SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name=%s',
        (schema, table)).fetchall()}
    missing = [column for column in columns if column not in target_columns]
    if missing:
        raise RuntimeError(f'{table}: columns missing in PostgreSQL: {missing}')
    select_columns = ', '.join(f'"{column}"' for column in columns)
    window = '' if low is None else f' WHERE rowid > {int(low)} AND rowid <= {int(high)}'
    query = f'SELECT rowid, {select_columns} FROM "{table}"{window} ORDER BY rowid'
    copy_columns = ['rowid'] + columns
    copy_sql = f'COPY "{schema}"."{table}" ({", ".join(chr(34) + c + chr(34) for c in copy_columns)}) FROM STDIN'
    count = 0
    cursor = source.execute(query)
    with raw.cursor() as target, target.copy(copy_sql) as copy:
        while True:
            rows = cursor.fetchmany(BATCH)
            if not rows:
                break
            copy.write(''.join('\t'.join(map(_text, row)) + '\n' for row in rows).encode('utf-8'))
            count += len(rows)
    return count


def _copy_worker(task):
    """Process-pool entry: copy one table with its own connections."""
    table, schema, low, high = task
    source = _source(db.DEFAULT_DB)
    raw = db._open_raw(db.database_url(), schema)
    try:
        raw.execute('SET synchronous_commit = off')
        began = time.time()
        with raw.transaction():
            count = _copy_table(source, raw, schema, table, low, high)
        return table, count, time.time() - began
    finally:
        raw.close()
        source.close()


def _finish_table(task):
    """Process-pool entry: build one table's indexes and triggers, then make it LOGGED."""
    table, schema, statements = task
    raw = db._open_raw(db.database_url(), schema)
    try:
        began = time.time()
        raw.execute("SET maintenance_work_mem = '512MB'")
        for statement in statements:
            raw.execute(statement)
        if table:
            raw.execute(f'ALTER TABLE "{schema}"."{table}" SET LOGGED')
        return table, time.time() - began
    finally:
        raw.close()


def migrate(replace=False):
    url = db.database_url()
    if not url:
        raise SystemExit('SPORTS_DATABASE_URL is not configured (backend/.env.local)')
    schema = os.environ.get('SPORTS_PG_SCHEMA', 'public')
    source_path = db.DEFAULT_DB
    print(f'source: {source_path} ({source_path.stat().st_size / 1e9:.1f} GB)')
    print(f'target: {db.describe()}')
    db._ensure_database(url)
    source = _source(source_path)
    raw = db._open_raw(url, schema)
    try:
        db._install_compat(raw)
        populated = raw.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema=%s AND table_name='sports_ai_fixtures'",
            (schema,)).fetchone()
        if populated:
            if not replace:
                raise SystemExit(f'Schema {schema} already has Sports AI tables. Use --replace to rebuild it.')
            raw.execute(f'DROP SCHEMA "{schema}" CASCADE')
            print(f'dropped schema {schema}')
        started = time.time()
        rest, foreign_keys = _create_schema(raw, schema, source)
        raw.execute('SET synchronous_commit = off')
        raw.execute("SET maintenance_work_mem = '1GB'")
        totals = {}
        sizes = {table: source.execute(f'SELECT MIN(rowid), MAX(rowid) FROM "{table}"').fetchone()
                 for table in _tables(source)}
        tasks = []
        for table, (low, high) in sizes.items():
            if high is None:
                tasks.append((table, schema, None, None, 0))
                continue
            span = high - (low or 1) + 1
            if span <= CHUNK:
                tasks.append((table, schema, None, None, span))
                continue
            start = (low or 1) - 1
            while start < high:   # big tables in rowid ranges, copied in parallel
                tasks.append((table, schema, start, min(start + CHUNK, high), CHUNK))
                start += CHUNK
        tasks.sort(key=lambda task: task[4], reverse=True)   # largest first keeps the pool busy
        from concurrent.futures import ProcessPoolExecutor, as_completed
        with ProcessPoolExecutor(max_workers=WORKERS) as pool:
            futures = [pool.submit(_copy_worker, task[:4]) for task in tasks]
            for done, future in enumerate(as_completed(futures), 1):
                table, count, seconds = future.result()
                totals[table] = totals.get(table, 0) + count
                if count > 50000 or seconds > 5:
                    print(f'  [{done}/{len(tasks)}] {table}: +{count:,} rows in {seconds:.0f}s '
                          f'({time.time() - started:.0f}s elapsed)', flush=True)
        print(f'copied {sum(totals.values()):,} rows in {time.time() - started:.0f}s; '
              'building indexes and switching tables to LOGGED...', flush=True)
        per_table = {table: [] for table in sizes}
        for statement in rest:
            match = re.search(r'\bON\s+"?(\w+)"?', statement, re.I)
            per_table.setdefault(match.group(1) if match else '', []).append(statement)
        with ProcessPoolExecutor(max_workers=WORKERS) as pool:
            work = sorted(per_table.items(), key=lambda item: (sizes.get(item[0]) or (0, 0))[1] or 0, reverse=True)
            for table, seconds in pool.map(_finish_table, [(table, schema, statements) for table, statements in work]):
                if seconds > 5:
                    print(f'  {table}: indexed and logged in {seconds:.0f}s ({time.time() - started:.0f}s elapsed)', flush=True)
        for clause in foreign_keys:
            raw.execute(clause)
        db.sync_identities(raw, schema)
        print('analyzing...', flush=True)
        raw.execute('ANALYZE')
        print(f'done in {time.time() - started:.0f}s')
    finally:
        raw.close()
        source.close()
    verify()


def verify():
    schema = os.environ.get('SPORTS_PG_SCHEMA', 'public')
    source = _source(db.DEFAULT_DB)
    raw = db._open_raw(db.database_url(), schema)
    problems = 0
    try:
        for table in _tables(source):
            expected = source.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            actual = raw.execute(f'SELECT COUNT(*) FROM "{schema}"."{table}"').fetchone()[0]
            if expected != actual:
                problems += 1
                print(f'  MISMATCH {table}: sqlite {expected:,} postgres {actual:,}')
        print('row counts match' if not problems else f'{problems} tables differ')
    finally:
        raw.close()
        source.close()
    return problems


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--replace', action='store_true', help='drop and rebuild the target schema')
    parser.add_argument('--verify', action='store_true', help='only compare row counts')
    args = parser.parse_args()
    if args.verify:
        sys.exit(1 if verify() else 0)
    migrate(replace=args.replace)
