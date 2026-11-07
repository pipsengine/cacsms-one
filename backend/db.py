import os, sqlite3, threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
DB=Path(os.environ.get('SPORTS_SQLITE_PATH',str(Path(__file__).parent/'sports_demo.sqlite3'))).expanduser().resolve()
_migration_lock=threading.Lock()

@contextmanager
def connect(busy_timeout_ms=20000):
 DB.parent.mkdir(parents=True,exist_ok=True)
 c=sqlite3.connect(DB,timeout=max(5,int(busy_timeout_ms)/1000))
 c.row_factory=sqlite3.Row
 c.execute(f'PRAGMA busy_timeout={int(busy_timeout_ms)}')
 c.execute('PRAGMA foreign_keys=ON')
 try:
  yield c
  c.commit()
 except BaseException:
  c.rollback()
  raise
 finally:
  c.close()

def initialize():
 migrations=sorted((Path(__file__).parent.parent/'migrations').glob('*.sql'))
 with _migration_lock:
  existed=DB.exists() and DB.stat().st_size>0
  DB.parent.mkdir(parents=True,exist_ok=True)
  with connect() as c:
   c.execute('PRAGMA journal_mode=WAL')
   c.execute('''CREATE TABLE IF NOT EXISTS sports_ai_schema_migrations(
    name TEXT PRIMARY KEY, applied_at TEXT NOT NULL
   )''')
   applied={row['name'] for row in c.execute('SELECT name FROM sports_ai_schema_migrations')}
  pending=[migration for migration in migrations if migration.name not in applied]
  if pending and existed:
   stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
   backup=DB.with_name(f'{DB.stem}.sports-ai-backup-{stamp}{DB.suffix}')
   with connect() as source, sqlite3.connect(backup) as target:
    source.backup(target)
  for migration in pending:
   sql=migration.read_text(encoding='utf-8')
   statements=[]
   statement=''
   for line in sql.splitlines():
    statement+=line+'\n'
    if sqlite3.complete_statement(statement):
     if statement.strip():statements.append(statement)
     statement=''
   if statement.strip():
    raise ValueError(f'Incomplete SQL statement in migration {migration.name}')
   with connect() as c:
    if migration.name.startswith('035_'):
     c.execute('PRAGMA foreign_keys=OFF')
    c.execute('BEGIN IMMEDIATE')
    try:
     for sql_statement in statements:c.execute(sql_statement)
     c.execute('INSERT INTO sports_ai_schema_migrations(name,applied_at) VALUES(?,?)',
      (migration.name,datetime.now(timezone.utc).isoformat()))
     c.commit()
     if migration.name.startswith('035_'):
      c.execute('PRAGMA foreign_keys=ON')
    except BaseException:
     c.rollback()
     if migration.name.startswith('035_'):
      c.execute('PRAGMA foreign_keys=ON')
     raise
