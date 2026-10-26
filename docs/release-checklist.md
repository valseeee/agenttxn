# Release checklist — v0.5.0

Source checklist describes required evidence. A configured test is not a successful run.
`VALIDATION_RESULTS.txt`, CI run IDs and the Release manifest record actual results for
the frozen commit. Historical 0.4.0 evidence is not reused as proof for changed source.

- [x] Implement optional PostgreSQL and test both backend contracts on real databases.
- [x] Reproduce/fix stale worker database writeback, GitHub repository identity and labels pagination.
- [x] Implement safe telemetry and actual read-only SQLite diagnostics.
- [x] Execute seeded crash benchmark on SQLite and PostgreSQL with one call/effect assertions.
- [x] Complete independent agent security review; review found bugs are fixed before freeze.
- [x] Document schema/API compatibility, backup/rollback and production boundaries.
- [ ] Final local unittest, pytest, compileall, demos, wheel install and CLI smoke.
- [ ] Nine remote OS/Python combinations run successfully for the final commit.
- [ ] PostgreSQL 18 contract/benchmark CI passes for the final commit.
- [ ] Separate-worker-container ownership/HTTP response-loss CI passes for the final commit.
- [ ] Manual live GitHub workflow uses contents:read/issues:write; all known test Issues close.
- [ ] Source ZIP manifest/checksum, clean extraction tests and credential scan pass.
- [ ] Frozen source, wheel, logs, manifest and checksums publish as a new Release.

Last unchecked items are release-time operations. Their successful logs/manifest supersede
this source-time pending status; this avoids committing claims before remote runs occur.
Default offline tests intentionally skip 7 PostgreSQL tests without the explicit test DSN;
the dedicated integration job supplies the DSN and must show no skipped backend tests.

Physical multi-host outage/partition, PostgreSQL HA, production capacity and institutional
third-party security certification are not tested or claimed. The user's one-host setup
uses separate containers as the supported network integration alternative.
