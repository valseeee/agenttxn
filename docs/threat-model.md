# Threat Model

## In scope
- Duplicate transaction/action registration.
- Invalid state transitions.
- Process crash during or after a side effect.
- Lost response after a provider may have committed.
- Stale human approval after plan mutation.
- Accidental event-history corruption.
- Accidental credential persistence.
- Compensation removing state that predated the transaction.

## Mitigations
- Stable idempotency keys and database uniqueness constraints.
- Explicit state machine and persisted attempt records.
- Reconciliation instead of blind retry for uncertain outcomes.
- Approval bound to plan hash.
- Event hash chain.
- Runtime credential injection.
- Adapter records what it changed and compensates only its own changes.

## Out of scope
- An attacker with full write access to the host/database.
- Malicious or compromised providers.
- Credential theft outside AgentTxn.
- General-purpose sandboxing for arbitrary Python.
- Hardware-backed remote attestation.
- Global exactly-once execution across arbitrary providers.

Use least-privilege tokens, keep the database directory private, avoid secrets in action
payloads, and test against sandbox repositories.

## v0.3.0 additional considerations

- A transaction-level worker claim prevents concurrent coordinators from advancing a
  transaction in the same SQLite database. Forced takeover is unsafe if the old worker
  is still alive: there is no external fencing token.
- A SHA-256 audit digest detects inconsistent step payloads but cannot prevent a database
  administrator from rewriting both records and hashes.
- Provider error messages may include credentials, so persisted errors contain generic
  messages and exception type only. User-supplied action payloads remain persisted;
  never include secrets in them.
- GitHub labels are shared state. Removing a label can erase a collaborator's work;
  automatic label removal is disabled by default.

## v0.3.1 verification boundaries

Malformed provider reconciliation evidence cannot authorize a retry. This protects against
adapter mistakes, not a provider that lies using a valid outcome. Future schema versions
are rejected before schema/journal initialization. Spawned-process tests cover local crash
boundaries; remote fencing, real network latency and independent security review remain
unverified. Secrets in provider result dictionaries remain the adapter author's responsibility.

## v0.4.0 review

Corrupt Demo state now fails closed and queries reload persistent state. GitHub label absence
and Issue reopening are not evidence that an earlier write never occurred. Forward and
compensation results must be JSON objects. Step/attempt statuses are compared to audit
history in addition to hashes. These checks detect inconsistent local history, not a fully
rewritten database or a malicious provider returning valid-looking evidence.

Release validation uses fake tokens in offline tests. Live tests require explicit repository
allowlisting and a confirmation flag, close their known test Issues, and keep credentials
out of output. Existing local publishing credentials may have broader permissions than a
dedicated least-privilege test token; no least-privilege audit is claimed.

## 0.5.0 additions

Database ownership is checked before scoped writes, including after a provider response.
This prevents stale local writes, not duplicate external requests after unsafe takeover.
PostgreSQL DSNs are private runtime configuration; driver errors are replaced with generic
StorageError messages. Grant database access only to trusted coordinators: SQL writers can
rewrite audit chains. Use TLS and service-scoped credentials in actual deployments.

GitHub repository ID is frozen during prepare and rechecked before writes and reconciliation.
A renamed/replaced slug fails closed. Label observations use every page; concurrent changes
still cannot establish exclusive label ownership. Legacy unpinned GitHub plans are rejected.
Payload-free telemetry is separate from raw local `show`/`events`, which expose stored records.
The independent agent review is documented in `independent-security-review.md`.
