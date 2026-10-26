# Independent agent security and correctness review

Reviewer: separate Codex review agent (`independent_security_review`).
Date: 2026-10-26.
Baseline: `main`, commit `bf7053e` (0.4.0), followed by review of the evolving working tree.
Final review target: the uncommitted 0.5.0 working tree on that branch, including
the final network synchronization and timeout corrections.

This review was performed by another agent, separately from the implementing agent.
It is not a third-party institutional audit, certification, penetration test, or
proof that the software has no vulnerabilities. No real GitHub writes, credential
inspection, commits, or pushes were performed by this reviewer. The reviewer changed
only this report; reproduction databases and simulated providers used temporary data.

## Scope and method

Reviewed engine execution, worker ownership, UNKNOWN recovery, approvals, shared SQL
store operations, SQLite migrations, optional PostgreSQL initialization/sessions,
GitHub and Demo adapters, CLI diagnostics, telemetry, and release/validation scripts.
Used source inspection, targeted offline reproductions, and regression tests.
The final extension includes the process-crash benchmark, separate-container network
fixture, deployment fixture files and CI configuration. All findings below were
addressed in the reviewed working tree. No remaining evidence-backed defect was
identified within the inspected scope; execution limitations remain stated below.

## Findings and resolution

### R1 — P2: repository identity was not checked before compensation

Original `src/agenttxn/adapters/github.py:130-136` closed an Issue using only the
original repository slug and Issue number. A repository rename or transfer followed
by reuse of its old slug could therefore target another repository's same-numbered
Issue. Reconciliation likewise did not check identity; label preflight did not freeze
repository identity. A mock reproduction confirmed a PATCH was constructed without
checking the stored repository ID.

Resolution reviewed: both action types now freeze a positive repository ID;
execution, compensation and reconciliation check that identity. Compensation refuses
a mismatch, while reconciliation preserves UNKNOWN. Legacy records lacking a usable
identity fail closed. Regression tests cover changed identity. **Addressed in the
reviewed working tree.** A separate identity read and subsequent API write are not
atomic; this cannot eliminate every remote race.

### R2 — P2: incomplete label pagination could misattribute ownership

Original `github.py:95-98,117-127,174-177` treated one response page as the complete
label set. A preexisting label on a later page could be recorded as newly added and
then deleted when label compensation was explicitly enabled. Compensation queries
could also mistake a later-page label for an absent label. A mock reproduction
confirmed incorrect `added_labels` and the resulting DELETE request.

The [GitHub endpoint documentation](https://docs.github.com/en/rest/issues/labels#list-labels-for-an-issue)
specifies a default page size of 30. Resolution reviewed: `_labels()` now reads all
pages with 100 results per page, validates page shape, and supplies complete sets to
preflight, stale-state checks and reconciliation. Tests exercise a second page and
preservation of preexisting labels. **Addressed in the reviewed working tree.**
Shared label mutation and multi-request pagination still do not provide an atomic
provider snapshot; label removal remains an explicit opt-in risk.

### R3 — regression fixture caused an unbounded test loop

The first version of the new GitHub regression tests checked `'page=1' in path`,
which also matched `per_page=100` on subsequent pages. Every page therefore returned
100 mock labels and the test loop never terminated. This was reported to the
implementer and corrected to match `&page=1` and `&page=2`. The corrected five-test
module passes. **Addressed; test fixture defect, not a provider vulnerability.**

### R4 — P2: network race probe relied on process scheduling

In the initial `scripts/network_probe.py`, `worker()` announced READY before it had
discovered the transaction, and `coordinate()` prepared the transaction as soon as
two READY rows exist. The lookup accepts only PREPARED/COMMITTING. An already-ready
worker delayed until the winner commits can therefore poll forever until the
60-second timeout. A worker delayed between discovery and `commit()` can instead see
a final transaction and raise `InvalidTransitionError`, which is not handled as the
expected BLOCKED result. The provider's fixed 0.4-second sleep is not a synchronization
guarantee. This is a possible false failure of the integration probe, not evidence
of a database ownership defect.

Resolution statically reviewed: each worker discovers the prepared transaction and
announces ARMED; the coordinator waits for both before opening START. The first
provider POST keeps the winning claim occupied until the second worker has actually
reported BLOCKED. This establishes overlap through observed outcomes rather than
elapsed sleeps. A follow-up review caught a 10-second HTTP timeout shorter than the
60-second provider barrier; this was corrected to 75 seconds, with coordinator and
worker waits defaulting to 90 seconds and the provider barrier explicitly bounded at
60 seconds. Compose now supplies the isolated test DSN to the provider for its
barrier query. **Addressed by final static review; container execution was not
performed by this reviewer.**

## New storage, ownership and diagnostics review

No additional evidence-backed security or correctness defect was identified in the
reviewed changes. PostgreSQL uses separately bound SQL parameters, an explicit schema,
short transaction-scoped advisory locks, and generic database errors that omit DSNs.
Both storage backends check the active worker token inside the same serialized write
transaction. A worker whose token was replaced cannot persist its late response.
This checks local database ownership and cannot fence an already issued SaaS request.

SQLite diagnostics open an existing schema-v4 database in `mode=ro`, reject missing
databases and legacy schemas instead of creating/migrating them, and refuse normal
write operations. Telemetry exports fixed status/event labels and excludes arbitrary
action payloads, preflight/results, provider errors, approver names and intent text.
Existing raw `show`/`events` commands still intentionally expose their local records.

## Independent validation evidence

Executed with the project virtual environment and temporary storage on D:.

- `test_github_repository_safety.py`: 5 passed.
- `test_owner_guard.py`: 1 passed.
- `test_telemetry.py`: 8 passed.
- `test_store_contract.py`: SQLite 6 passed; PostgreSQL 7 passed. The reviewer
  independently executed all 13 tests in 19.657 seconds against the explicitly
  authorized loopback-only temporary `agenttxn_test` database and role. The isolated
  test cluster uses no password; no production connection or credentials were used.
  PostgreSQL checks include reopen/audit, compensation, exclusive claim, future
  schema rejection, replaced-token rejection, uniqueness and UNKNOWN reconciliation.

Together these modules provide 27 independently passed targeted tests.

The reviewer also independently executed `scripts/benchmark.py --transactions 4
--workers 2 --latency-ms 5 --seed 1` against both SQLite and the authorized isolated
PostgreSQL database (adding `--postgres`). Each run recovered three process exits,
one each after claim, after external simulated effect and after step persistence.
Each reported four provider calls, four effects, five attempts and valid final
audits. SQLite elapsed 2.521 seconds; PostgreSQL elapsed 17.854 seconds. These small
runs validate crash boundaries and duplicate checks, not comparative performance.
The simulated provider is local JSON and these are process-crash tests, not power-loss
or storage-device durability tests.

Full release validation was not independently executed. Docker CLI was available,
but its Linux daemon was not running (`dockerDesktopLinuxEngine` pipe absent), so
the reviewer did not independently execute Compose. Dockerfile, Compose configuration,
network probe and CI wiring were reviewed statically. Container execution results
from another agent or CI must be identified separately.

## Remaining boundaries

No claim is made about production PostgreSQL roles, TLS, database administration,
remote fencing, exactly-once effects, malicious providers, or least-privilege audits
of real publishing credentials. Full database writers can rewrite local audit
history. Adapter business payloads/results must be non-secret; generic exception
redaction does not sanitize arbitrary business data. Demo JSON is a simulated
provider and does not provide concurrent multi-transaction storage guarantees.
The network fixture places workers and a delayed HTTP provider in separate container
network namespaces on one host. It does not establish physical multi-machine
availability, network-partition safety, cross-region behavior or external fencing.
