# Release code review — 0.4.0

This is the implementing agent's release review, not an independent third-party audit.
Scope: engine execution/recovery, SQLite migration/claims/audit, Demo and GitHub providers,
CLI diagnostics, package filtering and credential handling in validation scripts.

## Findings addressed

- Invalid reconciliation states previously authorized retry: strict normalization and
  optional confirmed/inconclusive evidence now prevent that.
- Scalar provider results were accepted as success: execute/compensate require JSON objects.
- Corrupt Demo files silently became empty worlds: malformed data now stops queries/writes.
- Stale Demo instances missed another instance's persisted effects: reload before operations.
- Mutable GitHub labels/open Issues were treated as proof of non-execution: preserve UNKNOWN.
- Irreversible Demo actions could advertise compensation: compensation now requires reversible.
- Step/attempt status changes escaped verification: compare those records against audit history.
- Historical preflight digests falsely invalidated recovered preparation: compare current data
  with its latest digest while retaining older snapshots inside the verified event chain.
- Future schema initialization changed tables before rejection: check the version first.
- ZIP packaging could include extra environments/runtime data: use explicit source roots and filters.
- Live GitHub Issue lists showed read-after-create visibility delay: the opt-in validator
  now bounds repeated reconciliation queries without resending the uncertain create.
- Windows CI used a short-name temporary directory: archive-origin verification now
  resolves both paths before comparison, preserving the check across equivalent path aliases.

Regression tests cover these paths. Network fault tests use fake tokens and suppress private
exception text. Live validation is opt-in, allowlisted and closes only known test Issues.

## Remaining boundaries

Provider result dictionaries and action payloads must be non-secret; arbitrary schema-free
business data cannot be generically redacted without losing meaning. A malicious provider
can lie using a syntactically valid result. Full database writers can rewrite the event chain.

Worker tokens are not remote fencing. An operator who falsely confirms worker termination
can cause duplicate external actions. The Demo JSON world is a test fixture, not a concurrent
multi-transaction provider service. Real GitHub issue history and removed labels cannot be
globally rolled back. Existing publishing credentials may have broader-than-minimal scopes.

Independent review, production deployment audit, cross-machine fencing and adversarial
provider guarantees remain outside this release. See `future-design.md` before extending
ownership, storage or automated retry behavior.
