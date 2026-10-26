# Optional provider capabilities and evidence

Existing adapters remain valid. `capabilities(action)` is optional and returns
`ProviderCapabilities(native_idempotency=False, stable_lookup=False)` by default.
`engine.capabilities_for(action)` exposes the declaration to SDK callers. A declaration
does not authorize replay and is not a proof about a particular call. Demo's declarations
apply only to its simulated provider; GitHub declares neither native capability.

Adapters can continue returning `(ReconcileOutcome, dict | None)`. New adapters may return:

```python
from agenttxn import ReconcileOutcome
from agenttxn.adapters import EvidenceLevel, ReconciliationEvidence

evidence = ReconciliationEvidence(
    outcome=ReconcileOutcome.SUCCEEDED,
    result={"external_id": "provider-resource-42"},
    level=EvidenceLevel.CONFIRMED,
)
```

The default evidence level is INCONCLUSIVE. Any inconclusive response normalizes to
UNKNOWN, including a proposed NOT_EXECUTED. Only explicit CONFIRMED evidence can authorize
success or retry. Legacy tuples preserve the adapter's existing obligation to prove
non-execution. Invalid outcomes, levels and result objects stay unresolved.

The audit records the outcome, phase and evidence level without free-text provider errors.
Result objects must contain only non-secret JSON. There is no new database schema,
changed plan hash, implicit retry of UNKNOWN or expiry of worker ownership.

GitHub Issue lookup is bounded to the latest 100 Issues; no match is UNKNOWN. Labels can
be removed and Issues reopened by collaborators after a successful write, so absence of
labels and an open Issue do not prove a previous write/compensation never happened.
