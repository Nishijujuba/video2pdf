# Test workflow behavior at three public seams

The repository currently has useful skill-local tests for Pyramid, compilation, acceptance, delivery, and Batch behavior, while the planned root Workflow Kernel has no common test boundary. Binding release tests directly to private functions would make routine refactoring expensive. Prohibiting every internal test would also weaken diagnosis of transaction journals, path calculations, and scheduler selection.

## Considered Options

- Test mainly through private Python functions: rejected because internal module layout would become an accidental compatibility contract.
- Exercise only the top-level CLI: rejected because fault injection and deep state-machine failures would be difficult to isolate.
- Prohibit all white-box tests: rejected because complex deterministic algorithms benefit from small diagnostic tests.
- Define three public verification seams and allow supplemental white-box tests: selected because it protects behavior while preserving implementation freedom.

## Decision

Release-level workflow verification binds to exactly three kinds of public seam:

1. `scripts/video_workflow.py` CLI tests verify command parsing, exit status, filesystem results, idempotency, fail-closed behavior, and recovery visible to coordinators and users.
2. Deep Module Interface tests verify state transitions, Artifact Generations, Checkpoint Freshness, invalidation, claims, transactional promotion, resource admission, materialization, and recovery without depending on private call structure.
3. Gate Provider executable-contract tests verify the versioned inputs, outputs, exit codes, path permissions, freshness binding, and failure classifications for Pyramid, compilation, Content Assurance, Final Acceptance, and Delivery Guard providers.

Supplemental White-Box Tests may target difficult internal algorithms or injected journal boundaries. They carry no compatibility authority, may change with refactoring, and cannot serve as the only proof of a required behavior.

Root workflow tests live under `tests/video_workflow/` and initially use the standard-library `unittest` style already present in the repository. Skill-local provider tests remain near their executable providers. Test-created filesystem artifacts are placed under the project `待删除/kernel-test-runs/` tree and use unique run directories.

Every implementation slice has these minimum tests:

- one positive behavior through a Workflow Verification Seam;
- one fail-closed counterexample;
- schema positive and negative fixtures for every introduced contract;
- idempotency and fault-injection recovery tests for every persistent mutation;
- stale-generation and unauthorized-path fixtures for every semantic Judgment Patch;
- quota, fairness, starvation, and restart tests for every scheduling change.

Contract tests verify schema identifiers, reference resolution, registry completeness, unknown-version rejection, protected fields, and `additionalProperties` policy. Prompt tests verify role template identity, SHA-256, required policy clauses, and permission sets; they do not compare full prompt prose snapshots.

The initial state-machine suite uses table-driven transition cases and fixed-seed operation sequences. Property-testing libraries may be added after the public state model stabilizes. Golden end-to-end fixtures compare normalized directory trees, state, and stable contract fields, while excluding timestamps, process identifiers, and other declared volatile evidence.

## Consequences

The CLI, Module Interfaces, and Gate Provider contracts become the stable observable behavior of the system. Internal designs remain replaceable. Failures can still be diagnosed with focused white-box coverage. Each vertical slice must arrive with evidence at the same seam consumed by the next workflow component.
