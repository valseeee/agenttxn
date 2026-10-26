# Release Checklist — v0.4.0

Local results must be regenerated with `python scripts/validate_release.py`. Consult
`VALIDATION_RESULTS.txt` for the actual Python version and completion status. Do not
carry successful results from any earlier baseline into this release.

- [x] Run unittest, pytest, compileall and both offline examples.
- [x] Build wheel, install in a separate venv and verify the installed CLI version.
- [x] Run script-generated CLI smoke against that wheel; count entries and summary.
- [x] Verify old schema/plan/policy compatibility, future schema rejection without writes,
      migration rollback and repeat opening.
- [x] Verify malformed reconciliation without replay, forward and compensation UNKNOWN,
      worker contention and abrupt exits at claim/call/writeback boundaries.
- [x] Check exception redaction, GitHub allowlist and label-compensation opt-in.
- [x] Confirm README, Chinese handoff, architecture, threat model and adapter docs agree.

Archive verification is recorded after the ZIP closes in its `.zip.verification.txt`
sidecar: SHA-256, manifest and tests from the clean extraction. It is deliberately
separate from the embedded logs, which validate the working source before packaging.

Items above are marked after local validation; logs are the evidence.
The items below are recorded separately from local offline checks. GitHub Actions and
Release validation assets are the evidence; a configured workflow alone is not a pass.

- [ ] Run explicitly authorized live GitHub Issue tests in the chosen public repository;
      close the test Issues and check for credential leakage. A least-privilege token audit
      is separate from reusing an existing local login.
- [ ] Run remote CI matrix for Python 3.11, 3.12 and 3.13; configuration is not evidence.
- [ ] Validate Linux/macOS runtime; local Windows results do not prove those platforms.
- [ ] Validate multi-process crash injection and recovery under real provider latency.
- [x] Exercise SQLite busy/lock contention, conditional owner release and crash-boundary recovery.
- [x] Review security-sensitive code and document findings in `security-review.md`.
- [x] Document P3 backend, fencing, observability and benchmark designs in `future-design.md`.
- [ ] Validate cross-machine deployment (outside the supported single-machine product scope).
- [ ] Complete independent security review before production use.
