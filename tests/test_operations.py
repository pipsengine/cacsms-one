import os,sys,tempfile,unittest
from pathlib import Path
class OperationsTests(unittest.TestCase):
 def test_sqlite_migrations_are_additive(self):
  import sqlite3
  migrations=Path(__file__).resolve().parents[1]/'migrations'
  with tempfile.TemporaryDirectory() as d:
   conn=sqlite3.connect(str(Path(d)/'existing.db'))
   conn.execute('CREATE TABLE traders_positions(id INTEGER PRIMARY KEY, symbol TEXT)')
   conn.execute("INSERT INTO traders_positions(symbol) VALUES('XAUUSD')")
   for m in sorted(migrations.glob('*.sql')):conn.executescript(m.read_text())
   self.assertEqual(conn.execute('SELECT symbol FROM traders_positions').fetchone()[0],'XAUUSD')
   self.assertGreaterEqual(len([x for x in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'sports_ai_%'")]),15)
   conn.close()
if __name__=='__main__':unittest.main()
