# PostgreSQL, diagnostics and reliability validation

## Backend selection

SQLite remains the dependency-free default and keeps schema v4. For shared storage:

```python
# pip install 'agenttxn[postgres]'
import os
from agenttxn import Engine, Policy
from agenttxn.adapters import DemoAdapter
engine = Engine.open_postgres(os.environ['AGENTTXN_DSN'],
    policy=Policy.default_safe(), adapters=[DemoAdapter()])
```

Use a dedicated database and trusted coordinator role. First open creates the `agenttxn`
schema and tables, so the role needs schema-creation rights. Runtime initialization still
checks/creates schema objects; deployment currently requires the same object-owner role.
Use PostgreSQL TLS and credentials supplied by the host secret manager, never action payloads.
The optional dependency is psycopg 3; default wheel installation does not require it.
PostgreSQL schema v1 is independent of SQLite v4; automatic data transfer is not supported.
Future versions are rejected. Driver errors omit connection strings. No connection pool or
automatic failover is provided. A coarse advisory transaction lock serializes short writes;
this favors correctness and is not a high-throughput scaling guarantee.

Each worker-scoped database write verifies its current token in the same locked transaction.
After takeover a stale worker cannot write back a result. Before explicit takeover the
operator must still establish that the original worker stopped: database checks cannot
stop a request already sent to an external API. Tokens never expire automatically.

## Safe diagnostic projections

```text
agenttxn --db state.db metrics --format json
agenttxn --db state.db metrics --format prometheus
agenttxn --db state.db alerts
agenttxn --db state.db tx telemetry TRANSACTION_ID
```

SQLite diagnostics require an existing current-schema database and open it read-only.
They do not initialize adapters, create directories or migrate old schemas. `show` and
`events` remain raw local diagnostics and may expose business records. `telemetry` exports
fixed types/status fields, IDs and timestamps; it omits action bodies, errors and approvers.
Metrics count current database states, including attempts and claimed workers. Alerts list
NEEDS_RECONCILIATION and PARTIAL_FAILURE; they do not automatically notify or change state.
SDK PostgreSQL users can call `agenttxn.telemetry.metrics/alerts/events(engine.store, ...)`.
The CLI `--db` remains a SQLite path. No HTTP exporter or background monitor is installed.

## Reproducible fault benchmark

```text
python scripts/benchmark.py --transactions 12 --workers 2 --latency-ms 30 --seed 1
python scripts/benchmark.py --postgres --transactions 12 --workers 2 --latency-ms 30
```

The PostgreSQL variant requires `AGENTTXN_POSTGRES_TEST_DSN` and a dedicated database whose
name ends `_test`. It adds test transactions; never use a business database. The benchmark
uses spawned processes, durable simulated effects, seeded delay and exits after claim,
after effect and after local writeback. The parent joins the stopped process before recovery.
Every transaction must commit, verify its audit and have exactly one provider call/effect.
Output records process count, timing distribution, OS/CPU/Python/SQLite/journal and throughput.
Disk device is not measured; these small recovery exercises are not production capacity tests.

## Separate network namespaces with one physical host

```text
docker compose -p agenttxn-integration -f deploy/compose.yml up --build --abort-on-container-exit --exit-code-from coordinator
docker compose -p agenttxn-integration -f deploy/compose.yml down -v
```

This is an isolated test fixture: two worker containers share PostgreSQL 18 and call a
separate HTTP provider container. No ports are published. The coordinator uses explicit
barriers and holds the first provider response until the second worker is rejected; then
it checks exclusive ownership, three effects/three calls, response-loss reconciliation and
valid audits. Test credentials are deliberately public fixture values. Compose removes the
ephemeral database when stopped. Do not reuse this file as a production deployment.

CI runs this on a single Linux host with separate container network namespaces. This
provides actual network communication between separate processes without a second user
machine; it does not test physical-host loss, network partitions, PostgreSQL HA or remote
fencing. Those environmental guarantees remain outside this release's claims.

## GitHub live checks

`Scoped GitHub integration` is manual-only. Its ephemeral GITHUB_TOKEN requests only
`contents:read` and `issues:write`, allowlists the current repository and runs three real
Issue cases with known-ID cleanup. Default unit CI never writes Issues. This validates
the adapter with a scoped runtime credential, not the scope of the account used to publish.
