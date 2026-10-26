# AgentTxn

**Durable Saga transactions for AI agent actions.**

[中文](README.md) · [Examples](examples/README.md) · [Download v0.5.0](https://github.com/valseeee/agenttxn/releases/tag/v0.5.0) · [Operations](docs/operations.md)

AgentTxn is a lightweight Python 3.11+ Saga coordinator for agents that write to external systems. It persists execution plans, approvals, action attempts, results, and compensation records. SQLite is the dependency-free default; PostgreSQL is optional through the Python SDK. No LLM framework is required.

Use it when a sequence of external writes needs approval, an audit trail, and explicit handling of failures or uncertain outcomes. A GitHub Issues adapter is included; other providers require a custom adapter.

**Limits:** no cross-provider ACID or arbitrary-provider exactly-once guarantee. A lost response may hide a successful write. Uncertain outcomes enter `NEEDS_RECONCILIATION` instead of authorizing blind retries. Compensation is a new business action, not a global rollback. Worker ownership does not fence requests at external APIs. The CLI currently uses SQLite.

## Try the offline examples

Download and extract the [v0.5.0 source ZIP](https://github.com/valseeee/agenttxn/releases/download/v0.5.0/agenttxn-v0.5.0-source.zip), then enter `agenttxn-v0.5.0`. Use Python 3.11 or newer. Neither example needs a token or calls an external API; temporary state is removed on exit.

Windows PowerShell (`py -3` must select Python 3.11+):

```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install .
.venv\Scripts\python.exe examples\successful_transaction.py
.venv\Scripts\python.exe examples\demo_transaction.py
```

Linux/macOS (`python3` must be Python 3.11+):

```bash
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/python examples/successful_transaction.py
.venv/bin/python examples/demo_transaction.py
```

The success example should print `COMMITTED` and `verification: True`. The failure example intentionally fails a later step and should print `final: COMPENSATED` and `event chain valid: True`.

These demos explicitly disable approval for reversible actions. Configure approval for real integrations; the [SDK example](README.md#python-sdk-示例) includes explicit approval.

## Install into an existing project

Inside a Python 3.11+ virtual environment:

```bash
python -m pip install "https://github.com/valseeee/agenttxn/releases/download/v0.5.0/agenttxn-0.5.0-py3-none-any.whl"
agenttxn version
```

The version should be `0.5.0`. The wheel installs the SDK and CLI. Download the source ZIP separately for examples. See the [asset and checksum guide](docs/releases/v0.5.0.md).

For PostgreSQL through the SDK:

```bash
python -m pip install "agenttxn[postgres] @ https://github.com/valseeee/agenttxn/releases/download/v0.5.0/agenttxn-0.5.0-py3-none-any.whl"
```

Use `Engine.open_postgres(dsn, ...)`; see [backend configuration and operational limits](docs/operations.md). The CLI remains SQLite-only.

## Next steps

| Entry | What it demonstrates |
|---|---|
| [Successful transaction](examples/successful_transaction.py) | Simulated resource creation and audit verification |
| [Failure and compensation](examples/demo_transaction.py) | Compensation after a later step fails |
| [GitHub Issue](examples/github_issue.py) | A real write with interactive approval; use a sandbox and a least-privilege token |
| [Adapter guide](docs/adapters.md) | Integrating another provider |
| [Transaction semantics](docs/transaction-semantics.md) | Recovery, UNKNOWN outcomes, and compensation boundaries |
| [Operations](docs/operations.md) | PostgreSQL, diagnostics, and deployment validation |

## v0.5.0

Adds optional PostgreSQL storage, stale database-writer protection, payload-free diagnostics, and GitHub repository identity/label pagination fixes. See [CHANGELOG](CHANGELOG.md) and [upgrade instructions](交接说明_0.5.0.md). Stop workers, back up state, and reconcile pending effects before upgrading.

Published evidence: [release CI](https://github.com/valseeee/agenttxn/actions/runs/40273519724), [scoped-token integration](https://github.com/valseeee/agenttxn/actions/runs/40273530021), and [validation assets](https://github.com/valseeee/agenttxn/releases/tag/v0.5.0). Container checks ran on one physical CI host; they do not establish multi-host disaster recovery. An independent agent review is not third-party security certification.

License: [Apache-2.0](LICENSE).
