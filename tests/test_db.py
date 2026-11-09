import multiprocessing
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'backend'))
import db


def initialize_together(root, barrier):
    db.DB = Path(root) / 'test.sqlite3'
    db.__file__ = str(Path(root) / 'backend' / 'db.py')
    read_text = Path.read_text

    def slow_read(path, *args, **kwargs):
        sql = read_text(path, *args, **kwargs)
        # Leave time for the other process to attempt startup during migration.
        if path.name == '001_test.sql':
            time.sleep(0.5)
        return sql

    barrier.wait(timeout=20)
    with patch.object(Path, 'read_text', slow_read):
        db.initialize()


class MigrationTests(unittest.TestCase):
    def test_invalid_category_reference_rolls_back_migration(self):
        with tempfile.TemporaryDirectory() as root:
            database = Path(root) / 'test.sqlite3'
            migrations = Path(root) / 'migrations'
            migrations.mkdir()
            (migrations / '042_test.sql').write_text(
                'CREATE TABLE migration_effect(value INTEGER);\n', encoding='utf-8')
            with closing(sqlite3.connect(database)) as connection:
                connection.executescript('''
                    CREATE TABLE sports_ai_high_odds_categories(id TEXT PRIMARY KEY);
                    CREATE TABLE sports_ai_high_odds_tickets(category_id TEXT);
                    CREATE TABLE sports_ai_high_odds_runs(category_id TEXT);
                    INSERT INTO sports_ai_high_odds_tickets VALUES('missing');
                ''')
            with patch.object(db, 'DB', database), patch.object(
                    db, '__file__', str(Path(root) / 'backend' / 'db.py')):
                with self.assertRaisesRegex(ValueError, 'category foreign key'):
                    db.initialize()
            with closing(sqlite3.connect(database)) as connection:
                self.assertIsNone(connection.execute(
                    "SELECT name FROM sqlite_master WHERE name='migration_effect'").fetchone())
                self.assertEqual(connection.execute(
                    'SELECT COUNT(*) FROM sports_ai_schema_migrations').fetchone()[0], 0)

    def test_concurrent_processes_apply_migrations_once(self):
        with tempfile.TemporaryDirectory() as root:
            # Match the existing application database, already in WAL mode.
            with closing(sqlite3.connect(Path(root) / 'test.sqlite3')) as connection:
                connection.execute('PRAGMA journal_mode=WAL')
                connection.executescript('''
                    CREATE TABLE sports_ai_high_odds_categories(id TEXT PRIMARY KEY);
                    CREATE TABLE sports_ai_high_odds_tickets(category_id TEXT
                        REFERENCES sports_ai_high_odds_categories(id));
                    CREATE TABLE sports_ai_high_odds_runs(category_id TEXT
                        REFERENCES sports_ai_high_odds_categories(id));
                    CREATE TABLE old_parent(id INTEGER PRIMARY KEY);
                    CREATE TABLE old_child(parent_id INTEGER REFERENCES old_parent(id));
                    INSERT INTO old_child VALUES(99);
                ''')
            migrations = Path(root) / 'migrations'
            migrations.mkdir()
            (migrations / '001_test.sql').write_text(
                'CREATE TABLE effects(value INTEGER);\n'
                'INSERT INTO effects VALUES(1);\n', encoding='utf-8')
            # Exercise the foreign-key-disabled migration path as well.
            (migrations / '042_test.sql').write_text(
                'INSERT INTO effects VALUES(42);\n', encoding='utf-8')
            context = multiprocessing.get_context('spawn')
            barrier = context.Barrier(2)
            processes = [context.Process(target=initialize_together,
                                        args=(root, barrier)) for _ in range(2)]
            try:
                for process in processes:
                    process.start()
                for process in processes:
                    process.join(timeout=30)
                    self.assertEqual(process.exitcode, 0)
                with closing(sqlite3.connect(Path(root) / 'test.sqlite3')) as connection:
                    self.assertEqual(connection.execute(
                        'SELECT value FROM effects ORDER BY value').fetchall(),
                        [(1,), (42,)])
                    self.assertEqual(connection.execute(
                        'SELECT COUNT(*) FROM sports_ai_schema_migrations').fetchone()[0], 2)
                self.assertEqual(len(list(Path(root).glob('*.sports-ai-backup-*.sqlite3'))), 1)
            finally:
                for process in processes:
                    if process.is_alive():
                        process.terminate()
                    process.join()


if __name__ == '__main__':
    unittest.main()
