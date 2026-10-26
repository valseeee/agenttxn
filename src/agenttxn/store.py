from __future__ import annotations
from contextlib import contextmanager
from pathlib import Path
import hashlib, json, sqlite3, uuid
from .errors import (ConcurrentModificationError, DuplicateIdempotencyKeyError, InvalidTransitionError, NotFoundError, PlanFrozenError)
from .models import (ActionSpec, ApprovalRecord, EventRecord, StepRecord, StepStatus,
                     TransactionRecord, TransactionStatus, canonical_json, utc_now)
from .state_machine import assert_step_transition, assert_transition

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS transactions(
 id TEXT PRIMARY KEY, intent TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,
 status TEXT NOT NULL, policy_version TEXT NOT NULL, plan_hash TEXT, metadata_json TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_error_json TEXT, row_version INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS steps(
 id TEXT PRIMARY KEY, transaction_id TEXT NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
 sequence INTEGER NOT NULL, action_kind TEXT NOT NULL, action_json TEXT NOT NULL, status TEXT NOT NULL,
 attempt_count INTEGER NOT NULL DEFAULT 0, preflight_json TEXT, result_json TEXT,
 compensation_json TEXT, last_error_json TEXT, UNIQUE(transaction_id,sequence));
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

def _loads(s):
    return json.loads(s) if s is not None else None

def _hash_event(txid, stepid, seq, typ, payload, created, previous):
    body = {"transaction_id":txid,"step_id":stepid,"sequence":seq,"event_type":typ,
            "payload":payload,"created_at":created,"previous_hash":previous}
    return hashlib.sha256(canonical_json(body).encode()).hexdigest()

class SQLiteStore:
    def __init__(self, path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        with self._db() as c:
            version=c.execute("PRAGMA user_version").fetchone()[0]
            if version > 4:
                raise RuntimeError(f"database schema version {version} is newer than supported version 4")
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)
        self._migrate()

    def _migrate(self):
        """Upgrade existing v0.2 SQLite databases in place, without JSON1."""
        with self._db(True) as c:
            version=c.execute("PRAGMA user_version").fetchone()[0]
            if version > 4:
                raise RuntimeError(f"database schema version {version} is newer than supported version 4")
            step_cols={r["name"] for r in c.execute("PRAGMA table_info(steps)")}
            if "action_idempotency_key" not in step_cols:
                c.execute("ALTER TABLE steps ADD COLUMN action_idempotency_key TEXT")
            # Drop the legacy expression index before UPDATE so migration works even
            # when the SQLite build does not provide JSON1.
            c.execute("DROP INDEX IF EXISTS idx_action_idempotency")
            rows=c.execute("SELECT id,action_json FROM steps WHERE action_idempotency_key IS NULL").fetchall()
            for row in rows:
                key=json.loads(row["action_json"])["idempotency_key"]
                c.execute("UPDATE steps SET action_idempotency_key=? WHERE id=?",(key,row["id"]))
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_action_idempotency ON steps(action_kind,action_idempotency_key)")
            c.execute("""CREATE TRIGGER IF NOT EXISTS steps_key_required_insert
                BEFORE INSERT ON steps WHEN NEW.action_idempotency_key IS NULL
                BEGIN SELECT RAISE(ABORT, 'action idempotency key required'); END""")
            c.execute("""CREATE TRIGGER IF NOT EXISTS steps_key_required_update
                BEFORE UPDATE OF action_idempotency_key ON steps WHEN NEW.action_idempotency_key IS NULL
                BEGIN SELECT RAISE(ABORT, 'action idempotency key required'); END""")
            tx_cols={r["name"] for r in c.execute("PRAGMA table_info(transactions)")}
            if "worker_token" not in tx_cols:
                c.execute("ALTER TABLE transactions ADD COLUMN worker_token TEXT")
            if "worker_claimed_at" not in tx_cols:
                c.execute("ALTER TABLE transactions ADD COLUMN worker_claimed_at TEXT")
            c.execute("PRAGMA user_version=4")

    @contextmanager
    def _db(self, write=False):
        c = sqlite3.connect(self.path, timeout=5)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA busy_timeout=5000")
        try:
            if write: c.execute("BEGIN IMMEDIATE")
            yield c
            if write: c.commit()
        except Exception:
            if write: c.rollback()
            raise
        finally: c.close()

    def _event(self, c, txid, typ, payload, stepid=None):
        row = c.execute("SELECT sequence,event_hash FROM events WHERE transaction_id=? ORDER BY sequence DESC LIMIT 1",(txid,)).fetchone()
        seq = row["sequence"] + 1 if row else 1
        prev = row["event_hash"] if row else None
        created = utc_now()
        h = _hash_event(txid,stepid,seq,typ,payload,created,prev)
        c.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?)",
                  (str(uuid.uuid4()),txid,stepid,seq,typ,canonical_json(payload),created,prev,h))

    def create_transaction(self, transaction_id, intent, idempotency_key, policy_version, metadata):
        now = utc_now()
        try:
            with self._db(True) as c:
                c.execute("INSERT INTO transactions(id,intent,idempotency_key,status,policy_version,metadata_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                          (transaction_id,intent,idempotency_key,"CREATED",policy_version,canonical_json(metadata),now,now))
                self._event(c,transaction_id,"TRANSACTION_CREATED",{"intent":intent,"idempotency_key":idempotency_key})
        except sqlite3.IntegrityError as e:
            if "idempotency_key" in str(e) or "transactions.idempotency_key" in str(e):
                raise DuplicateIdempotencyKeyError(f"transaction idempotency key already exists: {idempotency_key}") from e
            raise
        return self.get_transaction(transaction_id)

    def get_transaction(self, txid):
        with self._db() as c: row = c.execute("SELECT * FROM transactions WHERE id=?",(txid,)).fetchone()
        if not row: raise NotFoundError(f"transaction not found: {txid}")
        return TransactionRecord(row["id"],row["intent"],row["idempotency_key"],TransactionStatus(row["status"]),
            row["policy_version"],row["plan_hash"],_loads(row["metadata_json"]) or {},row["created_at"],
            row["updated_at"],_loads(row["last_error_json"]),row["row_version"])

    def list_transactions(self, limit=50):
        with self._db() as c:
            ids = c.execute("SELECT id FROM transactions ORDER BY created_at DESC LIMIT ?",(limit,)).fetchall()
        return [self.get_transaction(r["id"]) for r in ids]

    def claim_worker(self, txid, token, *, force=False):
        """Transaction-wide exclusive owner. Never auto-expire a claim.

        force=True is only for explicit operator-confirmed takeover of a stopped worker.
        """
        with self._db(True) as c:
            row=c.execute("SELECT worker_token FROM transactions WHERE id=?",(txid,)).fetchone()
            if not row: raise NotFoundError(f"transaction not found: {txid}")
            if row["worker_token"] is not None and not force:
                raise ConcurrentModificationError("transaction is owned by another worker; verify it stopped before takeover")
            c.execute("UPDATE transactions SET worker_token=?,worker_claimed_at=? WHERE id=?",
                      (token,utc_now(),txid))
            self._event(c,txid,"WORKER_CLAIMED",{"takeover":bool(row["worker_token"]),"forced":force})

    def release_worker(self, txid, token):
        with self._db(True) as c:
            c.execute("UPDATE transactions SET worker_token=NULL,worker_claimed_at=NULL WHERE id=? AND worker_token=?",
                      (txid,token))
            if c.execute("SELECT changes()").fetchone()[0]:
                self._event(c,txid,"WORKER_RELEASED",{})

    def worker_info(self, txid):
        with self._db() as c:
            row=c.execute("SELECT worker_token,worker_claimed_at FROM transactions WHERE id=?",(txid,)).fetchone()
        if not row: raise NotFoundError(f"transaction not found: {txid}")
        return {"claimed":row["worker_token"] is not None,"claimed_at":row["worker_claimed_at"]}

    def transition_transaction(self, txid, target, event_payload=None):
        """Atomically validate and persist a state transition plus its audit event."""
        with self._db(True) as c:
            row = c.execute("SELECT status FROM transactions WHERE id=?", (txid,)).fetchone()
            if not row:
                raise NotFoundError(f"transaction not found: {txid}")
            current = TransactionStatus(row["status"])
            if current == target:
                pass
            else:
                assert_transition(current, target)
                now = utc_now()
                c.execute("UPDATE transactions SET status=?,updated_at=?,row_version=row_version+1 WHERE id=? AND status=?",
                          (target.value, now, txid, current.value))
                if c.execute("SELECT changes()").fetchone()[0] != 1:
                    raise ConcurrentModificationError(f"transaction changed concurrently: {txid}")
                payload = {"from": current.value, "to": target.value, **(event_payload or {})}
                self._event(c, txid, "TRANSACTION_STATUS_CHANGED", payload)
        return self.get_transaction(txid)

    def update_transaction(self, txid, *, status=None, plan_hash=None, last_error=None, event_type="TRANSACTION_UPDATED", event_payload=None):
        with self._db(True) as c:
            row = c.execute("SELECT * FROM transactions WHERE id=?",(txid,)).fetchone()
            if not row: raise NotFoundError(f"transaction not found: {txid}")
            if status is not None and status.value != row["status"]:
                raise InvalidTransitionError("use transition_transaction() to change transaction status")
            s = row["status"]
            ph = plan_hash if plan_hash is not None else row["plan_hash"]
            err = canonical_json(last_error) if last_error is not None else row["last_error_json"]
            c.execute("UPDATE transactions SET status=?,plan_hash=?,last_error_json=?,updated_at=?,row_version=row_version+1 WHERE id=?",
                      (s,ph,err,utc_now(),txid))
            payload = dict(event_payload or {})
            payload.setdefault("status",s)
            if plan_hash is not None: payload.setdefault("plan_hash",plan_hash)
            self._event(c,txid,event_type,payload)
        return self.get_transaction(txid)

    def add_step(self, txid, action):
        sid = str(uuid.uuid4())
        try:
            with self._db(True) as c:
                tx = c.execute("SELECT id,status FROM transactions WHERE id=?",(txid,)).fetchone()
                if not tx:
                    raise NotFoundError(f"transaction not found: {txid}")
                if tx["status"] != TransactionStatus.CREATED.value:
                    raise PlanFrozenError("actions can only be added while CREATED")
                seq = c.execute("SELECT COALESCE(MAX(sequence),0)+1 n FROM steps WHERE transaction_id=?",(txid,)).fetchone()["n"]
                c.execute("INSERT INTO steps(id,transaction_id,sequence,action_kind,action_json,action_idempotency_key,status) VALUES(?,?,?,?,?,?,?)",
                          (sid,txid,seq,action.kind,canonical_json(action.to_dict()),action.idempotency_key,"PENDING"))
                self._event(c,txid,"STEP_ADDED",{"step_id":sid,"sequence":seq,"kind":action.kind},sid)
        except sqlite3.IntegrityError as e:
            if "idx_action_idempotency" in str(e) or "steps.action_kind" in str(e) or "steps.action_idempotency_key" in str(e):
                raise DuplicateIdempotencyKeyError(f"action idempotency key already exists: {action.idempotency_key}") from e
            raise
        return self.get_step(sid)

    def _step(self,row):
        return StepRecord(row["id"],row["transaction_id"],row["sequence"],ActionSpec.from_dict(_loads(row["action_json"])),
            StepStatus(row["status"]),row["attempt_count"],_loads(row["preflight_json"]),_loads(row["result_json"]),
            _loads(row["compensation_json"]),_loads(row["last_error_json"]))

    def get_step(self, sid):
        with self._db() as c: row = c.execute("SELECT * FROM steps WHERE id=?",(sid,)).fetchone()
        if not row: raise NotFoundError(f"step not found: {sid}")
        return self._step(row)

    def get_steps(self, txid):
        with self._db() as c: rows = c.execute("SELECT * FROM steps WHERE transaction_id=? ORDER BY sequence",(txid,)).fetchall()
        return [self._step(r) for r in rows]

    def update_step(self, sid, *, status=None, attempt_count=None, preflight=None, result=None, compensation=None, last_error=None, event_type="STEP_UPDATED"):
        fields, vals = [], []
        for name, val, col in [("status",status.value if status else None,"status"),("attempt_count",attempt_count,"attempt_count"),
            ("preflight",canonical_json(preflight) if preflight is not None else None,"preflight_json"),
            ("result",canonical_json(result) if result is not None else None,"result_json"),
            ("compensation",canonical_json(compensation) if compensation is not None else None,"compensation_json"),
            ("last_error",canonical_json(last_error) if last_error is not None else None,"last_error_json")]:
            if val is not None: fields.append(f"{col}=?"); vals.append(val)
        with self._db(True) as c:
            row = c.execute("SELECT transaction_id,status FROM steps WHERE id=?",(sid,)).fetchone()
            if not row: raise NotFoundError(f"step not found: {sid}")
            if status is not None:
                assert_step_transition(StepStatus(row["status"]),status)
            if fields:
                vals.append(sid); c.execute(f"UPDATE steps SET {','.join(fields)} WHERE id=?",vals)
            payload={"step_id":sid}
            if status: payload["status"]=status.value
            for label, value in (("preflight_sha256",preflight),("result_sha256",result),("compensation_sha256",compensation)):
                if value is not None:
                    payload[label]=hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
            self._event(c,row["transaction_id"],event_type,payload,sid)
        return self.get_step(sid)

    def claim_step(self, txid, sid, *, attempt_no, phase, fingerprint, allowed_statuses, target_status):
        """Atomically claim a step and create its attempt before calling an adapter.

        Returns the attempt ID if this worker won the claim, otherwise None. This is
        the key guard against two workers executing the same pending step concurrently.
        """
        aid, now = str(uuid.uuid4()), utc_now()
        allowed = {status.value for status in allowed_statuses}
        with self._db(True) as c:
            row = c.execute("SELECT transaction_id,status FROM steps WHERE id=?", (sid,)).fetchone()
            if not row:
                raise NotFoundError(f"step not found: {sid}")
            if row["transaction_id"] != txid:
                raise ConcurrentModificationError("step does not belong to this transaction")
            if row["status"] not in allowed:
                return None
            assert_step_transition(StepStatus(row["status"]),target_status)
            c.execute("UPDATE steps SET status=?,attempt_count=CASE WHEN ?='forward' THEN ? ELSE attempt_count END WHERE id=? AND status=?",
                      (target_status.value, phase, attempt_no, sid, row["status"]))
            if c.execute("SELECT changes()").fetchone()[0] != 1:
                return None
            c.execute("INSERT INTO attempts(id,transaction_id,step_id,attempt_no,phase,status,request_fingerprint,started_at) VALUES(?,?,?,?,?,'STARTED',?,?)",
                      (aid,txid,sid,attempt_no,phase,fingerprint,now))
            self._event(c,txid,"ATTEMPT_STARTED",{"attempt_id":aid,"step_id":sid,"attempt_no":attempt_no,"phase":phase},sid)
            self._event(c,txid,"STEP_EXECUTION_STARTED" if phase == "forward" else "COMPENSATION_STARTED",
                        {"step_id":sid,"attempt_id":aid,"phase":phase},sid)
        return aid

    def next_attempt_no(self, sid, phase):
        with self._db() as c:
            row=c.execute("SELECT COALESCE(MAX(attempt_no),0)+1 AS n FROM attempts WHERE step_id=? AND phase=?",(sid,phase)).fetchone()
        return row["n"]

    def get_attempts(self, txid):
        with self._db() as c:
            return [dict(row) for row in c.execute("SELECT * FROM attempts WHERE transaction_id=? ORDER BY started_at,id",(txid,))]

    def create_attempt(self, txid, sid, no, phase, fingerprint):
        aid, now = str(uuid.uuid4()), utc_now()
        with self._db(True) as c:
            c.execute("INSERT INTO attempts(id,transaction_id,step_id,attempt_no,phase,status,request_fingerprint,started_at) VALUES(?,?,?,?,?,'STARTED',?,?)",
                      (aid,txid,sid,no,phase,fingerprint,now))
            self._event(c,txid,"ATTEMPT_STARTED",{"attempt_id":aid,"step_id":sid,"attempt_no":no,"phase":phase},sid)
        return aid

    def resolve_open_attempt(self, sid, phase, status, response=None, error=None):
        """Close the latest STARTED attempt once provider reconciliation resolves it."""
        with self._db(True) as c:
            row=c.execute("SELECT id,transaction_id FROM attempts WHERE step_id=? AND phase=? AND status='STARTED' ORDER BY attempt_no DESC LIMIT 1",
                          (sid,phase)).fetchone()
            if not row:
                return False
            c.execute("UPDATE attempts SET status=?,finished_at=?,response_json=?,error_json=? WHERE id=? AND status='STARTED'",
                      (status,utc_now(),canonical_json(response) if response is not None else None,
                       canonical_json(error) if error is not None else None,row["id"]))
            if c.execute("SELECT changes()").fetchone()[0] != 1:
                return False
            self._event(c,row["transaction_id"],"ATTEMPT_RECONCILED",{"attempt_id":row["id"],"step_id":sid,
                "phase":phase,"status":status},sid)
        return True

    def finish_attempt(self, aid, status, response=None, error=None):
        with self._db(True) as c:
            row=c.execute("SELECT transaction_id,step_id FROM attempts WHERE id=?",(aid,)).fetchone()
            if not row: raise NotFoundError(f"attempt not found: {aid}")
            c.execute("UPDATE attempts SET status=?,finished_at=?,response_json=?,error_json=? WHERE id=? AND status='STARTED'",
                (status,utc_now(),canonical_json(response) if response is not None else None,
                 canonical_json(error) if error is not None else None,aid))
            if c.execute("SELECT changes()").fetchone()[0] != 1:
                raise ConcurrentModificationError("attempt already finished or reconciled")
            self._event(c,row["transaction_id"],"ATTEMPT_FINISHED",{"attempt_id":aid,"status":status,"error":error},row["step_id"])

    def add_approval(self, txid, plan_hash, approved_by, reason):
        """Atomically bind an approval to the currently awaiting, frozen plan."""
        aid, now = str(uuid.uuid4()), utc_now()
        with self._db(True) as c:
            row = c.execute("SELECT status,plan_hash FROM transactions WHERE id=?", (txid,)).fetchone()
            if not row:
                raise NotFoundError(f"transaction not found: {txid}")
            if row["status"] != TransactionStatus.AWAITING_APPROVAL.value:
                raise InvalidTransitionError("approval requires AWAITING_APPROVAL")
            if row["plan_hash"] != plan_hash:
                raise ConcurrentModificationError("plan hash changed before approval was stored")
            assert_transition(TransactionStatus.AWAITING_APPROVAL, TransactionStatus.APPROVED)
            c.execute("INSERT INTO approvals VALUES(?,?,?,?,?,'ACTIVE',?)",(aid,txid,plan_hash,approved_by,reason,now))
            self._event(c,txid,"APPROVAL_GRANTED",{"approval_id":aid,"plan_hash":plan_hash,"approved_by":approved_by})
            c.execute("UPDATE transactions SET status=?,updated_at=?,row_version=row_version+1 WHERE id=? AND status=?",
                      (TransactionStatus.APPROVED.value,now,txid,TransactionStatus.AWAITING_APPROVAL.value))
            if c.execute("SELECT changes()").fetchone()[0] != 1:
                raise ConcurrentModificationError("transaction changed while approval was being recorded")
            self._event(c,txid,"TRANSACTION_STATUS_CHANGED",{"from":TransactionStatus.AWAITING_APPROVAL.value,
                "to":TransactionStatus.APPROVED.value,"approval_id":aid})
        return ApprovalRecord(aid,txid,plan_hash,approved_by,reason,"ACTIVE",now)

    def get_active_approval(self, txid):
        with self._db() as c:
            r=c.execute("SELECT * FROM approvals WHERE transaction_id=? AND status='ACTIVE' ORDER BY approved_at DESC LIMIT 1",(txid,)).fetchone()
        return ApprovalRecord(r["id"],r["transaction_id"],r["plan_hash"],r["approved_by"],r["reason"],r["status"],r["approved_at"]) if r else None

    def get_events(self, txid):
        with self._db() as c: rows=c.execute("SELECT * FROM events WHERE transaction_id=? ORDER BY sequence",(txid,)).fetchall()
        return [EventRecord(r["id"],r["transaction_id"],r["step_id"],r["sequence"],r["event_type"],_loads(r["payload_json"]),
            r["created_at"],r["previous_hash"],r["event_hash"]) for r in rows]

    def record_event(self, txid, event_type, payload, stepid=None):
        with self._db(True) as c:
            self._event(c,txid,event_type,payload,stepid)

    def verify_event_chain(self, txid):
        events=self.get_events(txid); prev=None
        for i,e in enumerate(events,1):
            if e.sequence != i: return False, f"event sequence gap at {i}"
            if e.previous_hash != prev: return False, f"previous hash mismatch at event {i}"
            actual=_hash_event(e.transaction_id,e.step_id,e.sequence,e.event_type,e.payload,e.created_at,e.previous_hash)
            if actual != e.event_hash: return False, f"event hash mismatch at event {i}"
            prev=e.event_hash
        return True, f"verified {len(events)} events"

    def raw_connection_for_tests(self):
        c=sqlite3.connect(self.path); c.row_factory=sqlite3.Row; return c
