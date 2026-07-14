# Treat SQLite sidecars and test databases as engine state

The project file-safety policy requires user artifacts and disposable workflow files to be moved into `待删除` instead of being permanently deleted by agents. SQLite rollback-journal mode creates and removes a `-journal` sidecar as part of the database engine's commit protocol. Intercepting that lifecycle or moving an active journal would corrupt transaction semantics. File-backed test databases are also needed for cross-process locks and crash recovery, while retaining their binary state forever adds no stable test value.

## Considered Options

- Require SQLite journals to be moved into `待删除` after every transaction: rejected because the journal belongs to the active transaction protocol and cannot be relocated safely.
- Use only in-memory databases in tests: rejected because they cannot prove file locking, process crash, durability PRAGMAs, backup, or restart behavior.
- Commit prebuilt SQLite test databases as golden files: rejected because binary databases couple tests to SQLite format state and migration history more tightly than source fixtures do.
- Exempt engine-managed sidecars and create disposable file-backed test stores inside `待删除`: selected because artifact safety and database correctness remain clear.

## Decision

The deletion policy continues to govern user artifacts, workflow artifacts, agent-managed intermediate files, recovery backups, and retired databases. SQLite Engine Sidecars created for an active database are a narrow engine-lifecycle exception. SQLite may create, modify, truncate, and reclaim its own `-journal` and other required transient sidecars. Agents and project scripts never issue manual deletion commands for those files and never move them while the database is open.

When a production Cross-Run Control Store is retired, replaced, or quarantined after an integrity failure, the Kernel first stops writers and closes every connection. It then moves the main database plus every surviving associated sidecar as one set into:

```text
待删除/control-store/<timestamp>/
```

Every test that exercises persistence, locking, migration, backup, or crash recovery creates a fresh Disposable Test Control Store under:

```text
待删除/kernel-test-runs/<test-run-id>/
```

These test databases are temporary diagnostic outputs. They have no long-term retention requirement and are never committed as golden fixtures. A normal passing test run may leave its directory for manual cleanup under the existing file-safety policy. A failing test writes a small `failure_manifest.json` that records the test identity, fault point, database path, effective PRAGMAs, and relevant log paths so the database can be inspected until the failure is resolved.

Durable test assets are source-controlled JSON fixtures, schema files, SQL migrations, deterministic seed builders, and assertions. Tests construct a new database from those sources. An in-memory SQLite database may support narrow Supplemental White-Box Tests, but every release-level locking, durability, migration, and reconciliation guarantee uses a real file-backed test store.

Production database backups are separate governed evidence produced through SQLite's backup API. They are never substituted for test fixtures, and production data is never copied into ordinary tests.

## Consequences

SQLite can preserve its documented transaction lifecycle without weakening artifact-retention rules. Test databases may accumulate under `待删除` until the user performs manual cleanup, so test reports should expose their paths and approximate sizes. Repository history stays free of unstable binary database fixtures.
