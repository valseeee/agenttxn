# v0.5.0

- Add optional PostgreSQL persistence with shared backend contracts and serialized ownership checks.
- Fence stale database writers after explicit takeover; no remote-provider fencing claim.
- Add read-only, payload-free telemetry, Prometheus metrics and actionable alert projections.
- Pin GitHub repository identity before writes/reconciliation and paginate issue labels.
- Add crash reliability benchmarks, separate-container network integration and scoped-token live CI.
- Record independent agent security review, operational boundaries and upgrade instructions.

# Changelog

## 0.4.0 — Provider evidence and release readiness

- Add optional provider capability declarations and evidence levels; legacy adapter tuples remain supported.
- Inconclusive evidence cannot authorize success or replay, even with a native-idempotency declaration.
- Validate object results from execute/compensate; verify step and attempt statuses against audit history.
- Verify current data against its latest digest so recovered preparation can retain older audit snapshots.
- Fail closed on corrupt Demo provider state and reload provider state before querying or writing.
- Do not infer non-execution from mutable GitHub labels or reopened Issues.
- Add transport failures, SQLite busy contention and more process-crash boundaries.
- Add explicitly opted-in live GitHub checks and a nine-job OS/Python release-validation matrix.
- Keep SQLite schema v4 and existing plan/policy hash formats; document future backend/fencing designs.

## 0.3.1 — Validate reconciliation evidence before retry

- Reject malformed reconciliation outcomes/results without replaying forward or compensation calls.
- Reject future database schemas before changing tables or journal mode.
- Add spawned-process ownership/crash tests and migration rollback/reopen coverage.
- Generate CLI counts from actual subprocess checks; reproduce offline tests, examples,
  wheel installation and CLI smoke through one release validation script.
- Expand CI checks and harden source ZIP filtering; emit a verified SHA-256 file.
- Adapter API and database schema v4 remain compatible.

## 0.3.0 — Worker ownership, audit binding, safe recovery

- Transaction-wide persistent worker ownership with explicit operator-confirmed takeover.
- Recovery of interrupted PREPARING transactions.
- Conservative UNKNOWN handling for unexpected adapter/compensation errors and unserializable results.
- Frozen plan includes preflight; step result/preflight/compensation SHA-256 audit digests.
- Full SHA-256 policy identity with v0.2 truncated-identity compatibility.
- SQLite schema v4 migration: explicit action idempotency key column and ordinary unique index; no JSON1 index dependency.
- Runtime precondition checks based on adapter preflight.
- GitHub CLI repository allowlist and label-compensation opt-in.
- Read-only CLI diagnostics without adapter loading; `version`, `tx events`, worker visibility.
- Redacted adapter exception messages in persisted records.
- Regression tests for worker races, migration, audit tampering, preconditions and UNKNOWN semantics.

## 0.2.0 — 恢复与执行安全增强

- 修复已落库步骤失败的崩溃恢复、补偿中断恢复、原子步骤 claim。
- 增强状态机、策略指纹、输入校验和 GitHub 写入结果判断。

## 0.1.0 — 初始 alpha

- SQLite 状态持久化、审批、幂等键、Saga 补偿、不确定结果核对、审计链、CLI 与 Demo/GitHub 适配器。
