---
status: proposed
---

# Run project tests from an external root with bounded process parallelism

Project tests will use a versioned suite registry, dynamic `unittest` discovery, an explicit External Test Root, and process-isolated module scheduling with a maximum concurrency of four. On the current Windows host, the standard External Test Root is `D:\tests`; the implementation accepts an explicit absolute path and does not hard-code a drive.

The runner creates immutable project, suite, and run identities below the External Test Root. Complete test identity remains in versioned JSON manifests while filesystem paths use short stable keys. Test-generated data moves to this external boundary; committed fixtures, schemas, and historical evidence remain in the repository. Direct single-test execution retains the existing project-local test root as a compatibility fallback.

The parallel runner first operates as a preflight. After Issue #9 is closed, one Promotion Trial on its branch binds the existing successful 4,849.187-second serial result and two manually launched parallel runs. The serial result at implementation commit `18f78fad0be5a66d2da6250dc268bc8de81fdbcc` contains 474 tests and is historical performance evidence only. The final Issue #9 and Promotion semantic closed set is obtained dynamically from the implementation commit. The current pre-Promotion working tree discovers 475 unique Video Workflow test IDs with SHA-256 `b315b255a81e06847f3c41a01fa36115dd40390924df395108684a0a3967f98f`; this observed count is evidence and is not a hard-coded protocol constraint. Both parallel runs must bind the same immutable discovery inventory and hash, pass, remain eligible as acceptance evidence, and finish within 1,800 seconds. After that one-time Promotion passes and cutover is applied, registered authoritative suites use parallel execution by default without repeating serial comparisons.

Promotion Report v2 governs the subsequently authorized optimization-test
superset. Version 1 retains exact 475-to-475 equality. Version 2 requires the
complete final Issue #9 475-ID inventory as an exact subset, plus exactly the
24 Option B regression IDs whose canonical ID-set SHA-256 is
`53a65650fb48bab050e4e236e3fd0e2b448a0aac7ff9b6599ebf0d1cae549121`.
The derived 499-ID closed set has canonical SHA-256
`ea008eb2d56cf7bed8e489a0bf1dfabeabbb8f70410b65f1891c68a067ce36b7`.
Any baseline removal or rename, unauthorized addition, duplicate, reassigned
ID, or mismatched inventory fails closed. Counts and summary booleans carry no
authority without the bound, recomputed ID inventories.

Version 2 also binds both runs to one implementation commit, Registry
fingerprint, complete module assignment, and complete passed semantic-outcome
inventory. Each run must use jobs four, observe peak concurrency four, finish
with exit code zero, remain `no_secret_detected` and acceptance-evidence
eligible, and record persisted elapsed time at or below 1,800 seconds. The
validator reads the original external-root `discovery.json` and `summary.json`
plus original persisted `command.json`, `status.json`, `exit-code.txt`, and
`stdout.log`; local convenience summaries cannot substitute for those raw
artifacts. The authorized test modules and affected production sources are
content-fingerprinted, and the post-optimization safety authority binds live
Control Store checks with zero health-memo hits. The Control Store regression
module currently contains 32 AST `self.assert*` calls after the reviewed
contention-classification repair; the earlier design-time count of 20 is
historical and cannot override the current source fingerprint.

Promotion v2 additionally binds the baseline to the original final Issue #9
475-ID discovery artifact and its raw SHA-256. Every declared raw artifact path
must be the canonical reserved path; contained copies, aliases, reparse points,
and cloned run trees are invalid evidence. Validation consumes and fingerprints
one immutable byte snapshot per artifact, proves two distinct persisted UUIDv4
run identities and process-creation identities, and recomputes module ownership
from the current Registry and discovery contract. The validated chain covers
the project marker, test-run record, discovery, worker assignments and results,
worker stdout and stderr fingerprints, ordered event state machine, timings,
summary, persisted command, status, exit code, and completion event. Elapsed
time must be finite and lie in the closed interval from zero through 1,800
seconds.

Promotion v2 uses a local, fail-closed provenance model. Unsigned offline
files cannot prove absolute authenticity. A writer with access to the local
worktree and both evidence roots can forge JSON, hashes, process-shaped
values, and random nonce fields. These values provide copy detection,
lineage binding, and substitution-tamper controls; they are not
cryptographic attestations.

This boundary cannot resist an adversary with arbitrary write access to the
worktree, frozen execution source, and every unsigned evidence artifact.
The enforceable contract detects ordinary tampering, synthetic packages, and
execution from dirty or source-drifted bytes. Before creating a run, the
runner requires a clean Git worktree apart from explicitly named
evidence-output directories, records `HEAD`, its Git tree, and a closed
SHA-256 inventory of the actual runner, scheduler/worker, Registry, schema,
validator, and the complete tracked runtime-authority roots. Independent
discovery and workers import and execute only from the identity-bound frozen
snapshot. A local `.git` indirection targets a run-owned Git authority with
independent refs pinned to the manifest commit and tree; shared objects remain
content-addressed. Its link, config, alternates file, identities, and hashes
are part of the source manifest. Nested temporary repositories retain normal
Git discovery semantics. Promotion validation recomputes every inventory entry
and Git authority binding and rejects equal-`HEAD` evidence whose executed
bytes differ.

The Promotion authority-source closed set is declared once in
`project_test_source_provenance.py` and fingerprinted in both the superset
authority and the report implementation binding. It contains the fixed runner
runtime sources, the v2 authority generator, both Option B test modules, and
the affected `contracts.py` and `control_store.py` production sources. For
every declared path, the validator requires equality across the
`implementation.commit` Git blob, both execution-source manifests, both frozen
files, the report authority fingerprint, and validator-time live bytes. The
optimization safety review's `reviewed_source_commit` must equal
`implementation.commit`, and its production-source fingerprints must equal
the same closed-set entries.

The clean-worktree exception is path- and authority-based. An unchanged copy
of a tracked source inside an explicitly allowed evidence-output directory is
accepted when the original tracked path and bytes remain unchanged. Such a
copy is not registered, is not added to the execution-source manifest or
frozen inventory, and is not placed on the discovery or worker import path.
Tracked modification, deletion, rename or move remains rejected. When Git
porcelain reports an `R` or `C` record, the gate checks both recorded paths;
an untracked source outside the explicit output boundary also remains
rejected.

Windows execution identity is a relation rather than a PID alone. Runner,
supervisor, discovery, and worker records bind PID, process creation time,
executable file identity, and the observable parent relation where the
operating system exposes them. Reserved artifacts bind opened-file identity
and timestamps through one handle with before/after `fstat`, non-reparse path
checks, and Windows final-handle-path proof. PID reuse is accepted only when
the creation identity proves a different process instance. A field that cannot
be independently recomputed after execution is diagnostic evidence only and
cannot independently authorize Promotion.

The persisted target is either the runner process itself or a Windows
environment launcher whose directly observed child is the runner. The
validator accepts only those two explicit relations. Discovery receives the
runner-observed launcher identity over a one-use stdin handshake. The
interpreter that imports test modules and writes `discovery.json` then
self-observes its complete identity and accepts either direct execution or a
launcher-child relation. Its exact command, launcher identity, self identity,
and relationship are bound across `discovery.json`, `test-run.json`, and the
scheduling/completion events. Each worker launcher is a direct child of the
runner, and the worker's self-observed process is a direct child of that
launcher.

The validator accepts Promotion execution evidence only from the canonical
worktree `待删除/long-running/<runner-created>` persisted root and the
canonical `D:\tests\video2pdf\video-workflow\<runner-created>` external root.
The persisted runner creates an independent 256-bit random run nonce and
injects the run ID and nonce into the target environment. `test-run.json`
binds both values to the external run, the project-marker fingerprint, the
runner process identity, and the discovery process identity. Each module
assignment carries its own 256-bit launch nonce; the worker result reports
the worker-observed PID and process-creation identity, and the scheduler binds
that triple through started/completed events and the summary. Repeated PIDs
are permitted only when process-creation identities differ. This handles
ordinary Windows PID reuse while rejecting a reused worker identity. The
scheduler source fingerprint is part of the implementation binding.

Every raw evidence object is recursively closed to unknown fields, including
`discovery.suites[]` and its roots. Each file is opened once, read and hashed
from that handle, checked with `fstat` before and after, checked against the
canonical path and non-reparse components before and after the open, and on
Windows checked through the final path of the opened handle. If path identity
cannot be proved, the validator returns no authorization. The required
`authorization_model` report object discloses the remaining same-account
local-attacker limitation.

Promotion v2 declares
`persisted-command-v1.0.0-current-success-shape` and requires every field that
the current runner always writes to `command.json` and a successful terminal
`status.json`. It validates field types, offset-aware finite timestamps,
runner-defined timestamp ordering, task-name normalization, and the
`stdout.log`, `stderr.log`, and `command.log` sizes through identity-proved
file snapshots. The frozen historical baseline and pre-current optimization
evidence use an explicit legacy shape branch; that branch cannot authorize
either of the two Promotion v2 parallel runs.

On Windows, every reserved runner artifact path must remain within 240 UTF-16
path units. Before creating `video2pdf`, a project, or a worker, the runner
enumerates the selected commit-bound execution-source inventory and computes
the longest complete reserved relative path from the actual suite key, fixed
timestamp and short-run-ID shape, module/log artifacts, frozen Git authority,
and every `execution-source-files/<tracked-authority-path>` destination. The
self-hosted projection remains part of this calculation. The permitted
External Test Root length is derived from that complete inventory; no
historical reserved-length constant authorizes a run.

This record remains `proposed` because the supplied decision source is proposed and the one-time Promotion Trial has not completed. Promotion v2 schema, validator, and authority implementation do not activate the cutover. Parallel execution remains a preflight until two qualifying runs and a passing Promotion Report authorize the atomic `AGENTS.md` and `CLAUDE.md` default-command update.

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
