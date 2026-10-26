import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from agenttxn.store import SCHEMA, SQLiteStore


class MigrationGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'state.db'

    def test_future_schema_rejected_before_any_initialization(self):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute('CREATE TABLE future_data(value TEXT)')
            connection.execute("INSERT INTO future_data VALUES('keep')")
            connection.execute('PRAGMA user_version=99')
            before = connection.execute('SELECT * FROM sqlite_master').fetchall()
            journal = connection.execute('PRAGMA journal_mode').fetchone()[0]
        with self.assertRaisesRegex(RuntimeError, 'newer than supported'):
            SQLiteStore(self.path)
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute('SELECT * FROM sqlite_master').fetchall(), before)
            self.assertEqual(connection.execute('PRAGMA journal_mode').fetchone()[0], journal)
            self.assertEqual(connection.execute('SELECT * FROM future_data').fetchall(), [('keep',)])

    def test_failed_backfill_rolls_back_and_can_reopen(self):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.executescript(SCHEMA)
            connection.execute("INSERT INTO transactions(id,intent,idempotency_key,status,policy_version,metadata_json,created_at,updated_at) VALUES('tx','legacy','tx','CREATED','old','{}','a','a')")
            connection.execute("INSERT INTO steps(id,transaction_id,sequence,action_kind,action_json,status) VALUES('step','tx',1,'demo.create','invalid json','PENDING')")
        with self.assertRaises(json.JSONDecodeError):
            SQLiteStore(self.path)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 0)
            self.assertNotIn('action_idempotency_key', [r[1] for r in connection.execute('PRAGMA table_info(steps)')])
            connection.execute('UPDATE steps SET action_json=?', (json.dumps({'idempotency_key': 'preserved'}),))
        SQLiteStore(self.path)
        SQLiteStore(self.path)
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 4)
            self.assertEqual(connection.execute('SELECT action_idempotency_key FROM steps').fetchone()[0], 'preserved')


if __name__ == '__main__':
    unittest.main()
