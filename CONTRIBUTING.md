# Contributing

Thanks for considering a contribution.

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]"
python -m unittest discover -s tests -v
```

- Add tests for behavior changes and failure paths.
- Never weaken the rule that unknown side-effect outcomes require reconciliation.
- Never store tokens or authorization headers in payloads or events.
- Document provider-specific compensation limitations.
- Keep the core independent of LLM and agent-framework SDKs.
- Keep pull requests focused.

Adapters must document action kinds, permissions, preflight behavior, idempotency assumptions,
reconciliation guarantees, and compensation semantics. A missing provider result is not proof
that an action never happened.
