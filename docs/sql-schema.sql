PRAGMA foreign_keys=ON;
CREATE TABLE transactions (
 id TEXT PRIMARY KEY, intent TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,
 status TEXT NOT NULL, policy_version TEXT NOT NULL, plan_hash TEXT, metadata_json TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_error_json TEXT, row_version INTEGER NOT NULL DEFAULT 0, worker_token TEXT, worker_claimed_at TEXT
);
CREATE TABLE steps (
 id TEXT PRIMARY KEY, transaction_id TEXT NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
 sequence INTEGER NOT NULL, action_kind TEXT NOT NULL, action_json TEXT NOT NULL, action_idempotency_key TEXT, status TEXT NOT NULL,
 attempt_count INTEGER NOT NULL DEFAULT 0, preflight_json TEXT, result_json TEXT, compensation_json TEXT,
 last_error_json TEXT, UNIQUE(transaction_id,sequence)
);
CREATE TABLE attempts (
 id TEXT PRIMARY KEY, transaction_id TEXT NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
 step_id TEXT NOT NULL REFERENCES steps(id) ON DELETE CASCADE, attempt_no INTEGER NOT NULL, phase TEXT NOT NULL,
 status TEXT NOT NULL, request_fingerprint TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
 response_json TEXT, error_json TEXT, UNIQUE(step_id,attempt_no,phase)
);
CREATE TABLE approvals (
 id TEXT PRIMARY KEY, transaction_id TEXT NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
 plan_hash TEXT NOT NULL, approved_by TEXT NOT NULL, reason TEXT, status TEXT NOT NULL, approved_at TEXT NOT NULL
);
CREATE TABLE events (
 id TEXT PRIMARY KEY, transaction_id TEXT NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
 step_id TEXT REFERENCES steps(id) ON DELETE SET NULL, sequence INTEGER NOT NULL, event_type TEXT NOT NULL,
 payload_json TEXT NOT NULL, created_at TEXT NOT NULL, previous_hash TEXT, event_hash TEXT NOT NULL,
 UNIQUE(transaction_id,sequence)
);

CREATE UNIQUE INDEX idx_action_idempotency ON steps(action_kind,action_idempotency_key);
PRAGMA user_version=4;

CREATE TRIGGER steps_key_required_insert BEFORE INSERT ON steps
WHEN NEW.action_idempotency_key IS NULL
BEGIN SELECT RAISE(ABORT, 'action idempotency key required'); END;
CREATE TRIGGER steps_key_required_update BEFORE UPDATE OF action_idempotency_key ON steps
WHEN NEW.action_idempotency_key IS NULL
BEGIN SELECT RAISE(ABORT, 'action idempotency key required'); END;
