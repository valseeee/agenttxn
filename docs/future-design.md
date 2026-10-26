# Deferred infrastructure designs

These are design boundaries for the handoff's P3 directions, not implemented features.
The release remains a single-machine SQLite Saga coordinator.

## Stronger ownership and other persistence backends

A future PostgreSQL store must preserve atomic transaction claim, step claim plus attempt,
conditional transitions, event sequencing, schema version checks and migration rollback.
Test the same contract against both backends before adding a backend selection option.
Do not introduce an unused store abstraction in this release.

An epoch/generation would be incremented atomically on explicit takeover. Every local
write would compare that epoch, fencing stale database writers. This does not fence a
request already sent to SaaS: provider-native idempotency or a provider-side generation
check is additionally required. Wall-clock lease expiry alone is insufficient. Cross-machine
deployment requires a database/host architecture and provider protocol not supplied here.

## Provider reconciliation

The optional evidence API is the extension point. Future providers should document the
scope/retention of native idempotency keys and how stable IDs resolve action status.
GitHub pagination can improve finding positive evidence but cannot generally establish
non-execution after a mutable resource disappears. Preserve UNKNOWN unless absence is
authoritative under the provider's documented contract.

## Observability and benchmarks

Structured events should expose transaction ID, step ID, phase, attempt number, outcome
and duration; omit credentials, action bodies and arbitrary exception strings. Metrics
can count unresolved outcomes, takeover and failed compensation without embedding payloads.
Any export should be opt-in and keep the event chain authoritative locally.

A future benchmark must specify CPU/disk, database journal mode, process count, provider
latency distribution, crash point and durable effect counts. Throughput without duplicate
effect/UNKNOWN assertions is not a reliability benchmark. Current tests cover local
process crash boundaries, contention and injected response loss; they do not establish
HA, production latency bounds or arbitrary-provider exactly-once.

## Security review

Release code review checks credentials, mutable provider state, unknown-result replay,
audit mismatches and packaging. An independent third-party security audit requires a
separate reviewer and cannot be self-certified by the implementing agent.
