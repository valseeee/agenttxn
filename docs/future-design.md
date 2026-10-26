# Implemented directions and remaining boundaries

The handoff's P3 directions now have concrete implementations: optional PostgreSQL,
structured safe telemetry, metrics/alert projections, provider evidence interfaces,
repeatable crash benchmarks and an independent agent review. See `operations.md`,
`provider-capabilities.md` and `independent-security-review.md` for actual scope.

Backend contract tests run against both stores. Database ownership checks fence stale
writes; they cannot fence external requests already in flight. Native provider idempotency
and authoritative reconciliation remain necessary. Never add wall-clock token expiry as
a substitute for confirming that the old worker stopped.

Container CI exercises shared-database ownership across separate network namespaces and
HTTP response loss. Physical multi-host failure, database HA, remote fencing, production
capacity and arbitrary-provider exactly-once require additional infrastructure/protocols
and are not release guarantees. Metrics are read-only projections, not an alert-delivery
service. An independent agent review is not an institutional third-party certification.
