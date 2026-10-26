# Architecture

AgentTxn is a synchronous modular monolith. The coordinator, policy engine, SQLite store,
event recorder, and adapters run in one process.

1. Create a transaction with a stable idempotency key.
2. Add serializable action specs while the transaction is `CREATED`.
3. `prepare()` validates policy and adapters, runs read-only preflight, freezes a plan hash,
   and decides whether approval is required.
4. Approval, if required, is bound to that exact plan hash.
5. `commit()` persists an attempt before invoking a side-effecting adapter.
6. The result is persisted before the next step begins.
7. Known failures can trigger reverse-order compensation.
8. Uncertain results pause until provider-specific reconciliation resolves them.

SQLite writes use short `BEGIN IMMEDIATE` transactions. Network calls are never performed
inside a SQLite write transaction. The event hash chain detects inconsistent history, but
is not tamper-proof against an attacker who can rewrite the entire database.

External SaaS APIs do not generally share a prepare/commit protocol. AgentTxn uses an
orchestrated Saga model, not global ACID. SQLite is intended for one machine and modest
write concurrency, not distributed high availability.


## 0.2.0 execution and recovery hardening

Step claims and their attempt rows are written atomically. A worker that finds a step already claimed returns without executing it. The transaction policy identity is a fingerprint of the complete policy configuration, not only the human-readable version string.

`resume()` does not automatically reconcile a step that is still marked in-flight, because that step may belong to a live worker. After confirming that the old worker has stopped, call `reconcile()` (or the CLI `tx reconcile`) to query the provider and resume safely. This is a deliberate safety boundary; no generic local process can perfectly infer whether an external request is still running.

## 0.3.0 transaction-wide ownership

A persistent `worker_token` on each transaction is claimed under `BEGIN IMMEDIATE` before
`prepare`, `commit`, `resume` or `reconcile`. The owner is released in `finally` during
normal return, but an OS/process crash leaves it persisted. Claims never auto-expire.
An operator may force takeover only through `reconcile(confirm_worker_stopped=True)`.
This does **not** fence a still-running remote worker; confirming its termination is
mandatory. Nested calls within the same thread reuse the same owner token.

Preparation recovery can also use `resume(confirm_worker_stopped=True)` after the old
process is confirmed stopped. v0.3.1 tests use spawned OS processes and abrupt exits
after step claim, after provider persistence, and after local step writeback for both
forward execution and compensation. These tests use a local fake provider, not network
latency or cross-machine fencing.

The frozen plan now includes adapter preflight data; digest-bearing audit events bind
preflight, result and compensation JSON to the event chain. v0.2 legacy hashes remain
readable with a warning. SQLite v4 migration adds explicit idempotency and worker columns.

## 0.4.0 provider evidence

Optional capability declarations are descriptive; they do not bypass reconciliation.
Adapters may return structured evidence or the original tuple. Unconfirmed evidence is
UNKNOWN and only confirmed outcomes can advance a step. The audit records evidence level
and verifies persisted step/attempt statuses against their events. No schema migration is
introduced. See `provider-capabilities.md` and `future-design.md` for API and design boundaries.
