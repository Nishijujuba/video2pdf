---
status: proposed
---

# Run project tests from an external root with bounded process parallelism

Project tests will use a versioned suite registry, dynamic `unittest` discovery, an explicit External Test Root, and process-isolated module scheduling with a maximum concurrency of four. On the current Windows host, the standard External Test Root is `D:\tests`; the implementation accepts an explicit absolute path and does not hard-code a drive.

The runner creates immutable project, suite, and run identities below the External Test Root. Complete test identity remains in versioned JSON manifests while filesystem paths use short stable keys. Test-generated data moves to this external boundary; committed fixtures, schemas, and historical evidence remain in the repository. Direct single-test execution retains the existing project-local test root as a compatibility fallback.

The parallel runner first operates as a preflight. After Issue #9 is closed, one Promotion Trial on its branch binds the existing successful 4,849.187-second serial result and two manually launched parallel runs. The serial result at implementation commit `18f78fad0be5a66d2da6250dc268bc8de81fdbcc` contains 474 tests and is historical performance evidence only. The final Issue #9 and Promotion semantic closed set is obtained dynamically from the implementation commit. The current pre-Promotion working tree discovers 475 unique Video Workflow test IDs with SHA-256 `b315b255a81e06847f3c41a01fa36115dd40390924df395108684a0a3967f98f`; this observed count is evidence and is not a hard-coded protocol constraint. Both parallel runs must bind the same immutable discovery inventory and hash, pass, remain eligible as acceptance evidence, and finish within 1,800 seconds. After that one-time Promotion passes and cutover is applied, registered authoritative suites use parallel execution by default without repeating serial comparisons.

On Windows, every reserved runner artifact path must remain within 240 UTF-16 path units. The self-hosted `project-test-runner` suite reserves 199 units for its longest relative descendant, leaving at most 40 units for the External Test Root plus the joining separator. The runner derives the applicable root limit from the selected suite keys and rejects an over-budget root before project or worker creation.

This record remains `proposed` because the supplied decision source is proposed and the one-time Promotion Trial has not completed. Design documentation and implementation do not activate the cutover. Parallel execution remains a preflight until a passing Promotion Report authorizes the atomic `AGENTS.md` and `CLAUDE.md` default-command update.

Historical Slice evidence remains unchanged. Parallel failures fail closed; the legacy serial command remains available only for manual diagnosis and cannot automatically override a failed parallel gate.

## Considered Options

- Continue serial execution: rejected because the historical 474-test gate takes about 81 minutes.
- Use thread-level parallelism: rejected because module-global state, SQLite, environment patches, junctions, and Windows file operations require process isolation.
- Introduce `pytest-xdist` or another plugin: rejected because the existing `unittest` identity and evidence contracts can be preserved with the Python standard library.
- Automatically relocate worktrees: rejected because test data ownership and path identity should be explicit.
- Repeat serial/parallel promotion for every Issue: rejected because promotion is a one-time activation decision.

## Consequences

ADR 0039 remains authoritative for test seams and `unittest` style. Its project-local generated-data location is superseded for runner-managed parallel tests. The project-local location remains a compatibility fallback for direct test execution.

The current checkout has not crossed the Promotion boundary. Until a passing Promotion Report authorizes cutover, existing default test commands and runtime instructions remain authoritative, and the new runner carries preflight authority only.
