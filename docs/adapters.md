# Adapter Contract

Each adapter implements `validate`, `preflight`, `execute`, `compensate`, `reconcile`, and
`can_compensate`. Validation and preflight must not perform the target business side effect.
Reconciliation must query provider state and must not repeat the forward action.

Return `(ReconcileOutcome, dict | None)` from reconciliation. The outcome must explicitly
be `SUCCEEDED`, `NOT_EXECUTED`, or `UNKNOWN` (their string values are also accepted).
Return `NOT_EXECUTED` only with evidence sufficient to authorize a retry. Invalid outcomes,
non-object evidence, NaN and unserializable evidence keep the transaction waiting for
reconciliation; exception text is not persisted. `None` remains supported for existing adapters.

Alternatively return `ReconciliationEvidence`; see `provider-capabilities.md`. INCONCLUSIVE
evidence stays UNKNOWN regardless of the proposed outcome. Optional `capabilities(action)`
declarations never independently authorize a retry.

Raise `ActionExecutionError` only when the adapter knows the action failed. Raise
`OutcomeUnknownError` when a write may have reached the provider but the outcome is unknown.
Never include credentials or authorization headers in exceptions or event payloads.

## GitHub adapter

Supported actions:
- `github.issue.create`
- `github.issue.add_labels`

Issue creation uses a hidden correlation marker. One matching issue confirms success. No match
does not prove non-execution and remains unknown. Closing an issue is semantic compensation,
not true rollback. Label compensation removes only labels absent during preflight and added
by this transaction.


## Recovery safety boundary

When a persisted step is in-flight after a process restart, the caller must confirm that the original worker is no longer running before requesting reconciliation. An adapter's `reconcile` method must be a read/query operation only: it must not repeat the forward action or compensation. If provider state cannot establish the outcome, return `ReconcileOutcome.UNKNOWN`.

## v0.3.0 GitHub safety

`github.issue.add_labels` compensation is disabled by default. Removing a label is unsafe
when collaborators can add the same label concurrently. The opt-in
`GitHubIssuesAdapter(token, allow_label_compensation=True)` is only appropriate when the
repository is controlled and this risk is explicitly accepted. The CLI requires
`AGENTTXN_GITHUB_REPOS=owner/repo` before registering a GitHub write adapter.

Unexpected adapter exceptions and invalid return objects are UNKNOWN, not proof of a
failed write. Never put access tokens in action payloads or preflight results. An adapter's
preflight should return only JSON-serializable, non-secret data. The engine's preconditions
are a subset comparison against these preflight keys and cannot guarantee remote CAS.
