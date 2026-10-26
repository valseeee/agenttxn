"""Optional PostgreSQL store. Short writes serialize under a database advisory lock."""
from contextlib import contextmanager

from .errors import ConcurrentModificationError, DuplicateIdempotencyKeyError, StorageError
from .store import SQLStore

LOCK_KEY=0x4167656e7454786e
SCHEMA_VERSION=1
SCHEMA="""
CREATE TABLE IF NOT EXISTS transactions(
 id TEXT PRIMARY KEY, intent TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,
 status TEXT NOT NULL, policy_version TEXT NOT NULL, plan_hash TEXT, metadata_json TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_error_json TEXT,
 row_version INTEGER NOT NULL DEFAULT 0, worker_token TEXT, worker_claimed_at TEXT);
CREATE TABLE IF NOT EXISTS steps(
 id TEXT PRIMARY KEY, transaction_id TEXT NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
 sequence INTEGER NOT NULL, action_kind TEXT NOT NULL, action_json TEXT NOT NULL, status TEXT NOT NULL,
 attempt_count INTEGER NOT NULL DEFAULT 0, preflight_json TEXT, result_json TEXT,
 compensation_json TEXT, last_error_json TEXT, action_idempotency_key TEXT NOT NULL,
 UNIQUE(transaction_id,sequence));
CREATE UNIQUE INDEX IF NOT EXISTS idx_action_idempotency ON steps(action_kind,action_idempotency_key);
CREATE TABLE IF NOT EXISTS attempts(
 id TEXT PRIMARY KEY, transaction_id TEXT NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
 step_id TEXT NOT NULL REFERENCES steps(id) ON DELETE CASCADE, attempt_no INTEGER NOT NULL,
 phase TEXT NOT NULL, status TEXT NOT NULL, request_fingerprint TEXT NOT NULL,
 started_at TEXT NOT NULL, finished_at TEXT, response_json TEXT, error_json TEXT,
 UNIQUE(step_id,attempt_no,phase));
CREATE TABLE IF NOT EXISTS approvals(
 id TEXT PRIMARY KEY, transaction_id TEXT NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
 plan_hash TEXT NOT NULL, approved_by TEXT NOT NULL, reason TEXT, status TEXT NOT NULL, approved_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events(
 id TEXT PRIMARY KEY, transaction_id TEXT NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
 step_id TEXT REFERENCES steps(id) ON DELETE SET NULL, sequence INTEGER NOT NULL,
 event_type TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
 previous_hash TEXT, event_hash TEXT NOT NULL, UNIQUE(transaction_id,sequence));
"""


class Session:
    def __init__(self,connection): self.connection=connection

    def execute(self,query,params=None):
        # Only library-owned SQL templates reach this boundary. Values stay separately bound.
        return self.connection.execute(query.replace('?', '%s'),params)


class PostgresStore(SQLStore):
    def __init__(self,dsn):
        super().__init__()
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError:
            raise StorageError('install agenttxn[postgres] to use PostgreSQL') from None
        self._driver=psycopg
        self._row_factory=dict_row
        self._dsn=dsn
        self.path='postgresql'  # Never expose a credential-bearing DSN through diagnostics.
        with self._session(write=True,initialize=True) as session:
            exists=session.execute("SELECT to_regclass('agenttxn.schema_version') AS name").fetchone()['name']
            if exists:
                row=session.execute('SELECT version FROM agenttxn.schema_version WHERE singleton=1').fetchone()
                if row is None or row['version'] != SCHEMA_VERSION:
                    raise StorageError('unsupported PostgreSQL schema version; database left unchanged')
            session.execute('CREATE SCHEMA IF NOT EXISTS agenttxn')
            session.execute('SET LOCAL search_path TO agenttxn,pg_catalog')
            for statement in SCHEMA.split(';'):
                if statement.strip(): session.execute(statement)
            session.execute('CREATE TABLE IF NOT EXISTS schema_version(singleton INTEGER PRIMARY KEY CHECK(singleton=1),version INTEGER NOT NULL)')
            session.execute('INSERT INTO schema_version(singleton,version) VALUES(1,?) ON CONFLICT(singleton) DO NOTHING',(SCHEMA_VERSION,))

    @contextmanager
    def _session(self,write=False,initialize=False):
        try:
            with self._driver.connect(self._dsn,row_factory=self._row_factory,connect_timeout=5) as connection:
                session=Session(connection)
                session.execute("SET LOCAL lock_timeout='5s'")
                if write:
                    session.execute('SELECT pg_advisory_xact_lock(?)',(LOCK_KEY,))
                if not initialize:
                    session.execute('SET LOCAL search_path TO agenttxn,pg_catalog')
                    if write: self._check_owner(session)
                yield session
        except self._driver.errors.UniqueViolation as exc:
            constraint=exc.diag.constraint_name
            if constraint in {'transactions_idempotency_key_key','idx_action_idempotency'}:
                raise DuplicateIdempotencyKeyError('idempotency key already exists') from None
            raise ConcurrentModificationError('database uniqueness constraint rejected a concurrent write') from None
        except self._driver.Error:
            raise StorageError('PostgreSQL operation failed; inspect private database logs') from None

    def _db(self,write=False):
        return self._session(write=write)
