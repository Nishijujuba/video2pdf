---
status: proposed
---

# Run project tests from an external root with bounded process parallelism

Project tests will use a versioned suite registry, dynamic `unittest` discovery, an explicit External Test Root, and process-isolated module scheduling with a maximum concurrency of four. On the current Windows host, the standard External Test Root is `D:\tests`; the implementation accepts an explicit absolute path and does not hard-code a drive.

The runner creates immutable project, suite, and run identities below the External Test Root. Complete test identity remains in versioned JSON manifests while filesystem paths use short stable keys. Test-generated data moves to this external boundary; committed fixtures, schemas, and historical evidence remain in the repository. Direct single-test execution retains the existing project-local test root as a compatibility fallback.

The parallel runner first operates as a preflight. After Issue #9 is closed, one Promotion Trial on its branch binds the existing successful 4,849.187-second serial result and two manually launched parallel runs. Both parallel runs must cover the same 474 Video Workflow test IDs, pass, remain eligible as acceptance evidence, and finish within 1,800 seconds. After that one-time promotion, registered authoritative suites use parallel execution by default without repeating serial comparisons.

Historical Slice evidence remains unchanged. Parallel failures fail closed; the legacy serial command remains available only for manual diagnosis and cannot automatically override a failed parallel gate.

## Considered Options

- Continue serial execution: rejected because the current 474-test gate takes about 81 minutes.
- Use thread-level parallelism: rejected because module-global state, SQLite, environment patches, junctions, and Windows file operations require process isolation.
- Introduce `pytest-xdist` or another plugin: rejected because the existing `unittest` identity and evidence contracts can be preserved with the Python standard library.
- Automatically relocate worktrees: rejected because test data ownership and path identity should be explicit.
- Repeat serial/parallel promotion for every Issue: rejected because promotion is a one-time activation decision.

## Consequences

ADR 0039 remains authoritative for test seams and `unittest` style. Its project-local generated-data location is superseded for runner-managed parallel tests. The project-local location remains a compatibility fallback for direct test execution.
