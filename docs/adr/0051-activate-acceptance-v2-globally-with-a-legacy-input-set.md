# Activate Acceptance v2 globally with a legacy input set

Acceptance Report v1 is selected for retirement, while Bilibili and YouTube Kernel ownership will cut over at different later times. Activating v2 separately at each platform cutover would leave two delivery-authorizing report versions active. Freezing every Legacy delivery until both platforms cut over would also make the staged implementation unnecessarily disruptive. Legacy final artifacts need a fresh v2 input binding that does not pretend their historical workflow had a Kernel Run Record.

## Considered Options

- Keep Acceptance Report v1 active for Legacy Track delivery: rejected because ADR 0030 requires one delivery-authorizing report contract.
- Freeze all Legacy delivery until every Platform Kernel Cutover: rejected because gate modernization can be proven independently of run-ownership migration.
- Synthesize a complete Kernel Run Record for each Legacy directory: rejected because historical Run migration is explicitly deferred and missing state cannot be reconstructed honestly.
- Activate v2 globally with an immutable Legacy Acceptance Input Set: selected because both tracks use one fresh semantic gate while preserving their distinct coordination models.

## Decision

Implementation Slice 8 performs one Global Gate Cutover for Acceptance Report v2. The atomic group includes:

- Acceptance Report v2, Text and Visual Judgment Patch, Legacy Acceptance Input Set, Acceptance Execution Context, Acceptance Dimension Map, Skeleton, Task Authority Binding, and criterion-reference schemas;
- skeleton builders, dimension task generators, task Claim and patch-commit runtime, report-publication intent, materializer, validator, rendered-page provider, and Delivery Guard integration;
- `AGENTS.md`, `CLAUDE.md`, both platform skills, Final Acceptance skill, mirrored `.agents` and `.claude` assets, and all gate tests;
- an Exit Evidence Manifest proving that v1 is rejected and both Kernel and Legacy input adapters produce valid v2 provenance.

For an explicitly named Legacy video output directory, `legacy-acceptance-adopt` creates `review/acceptance/legacy_input_set.json`. It does not scan for an arbitrary latest PDF. The contract records at least:

- schema and provider versions, input-set identity, canonical Video Output Directory, and `input_track: legacy`;
- exact final artifact paths, sizes, SHA-256 digests, and gate-scoped generation identities;
- allowed-artifact manifest, current compile-provenance reference and classification, Acceptance Criteria, and Acceptance Dimension Map fingerprints;
- freshly generated rendered-page manifest, page count, and every page-image fingerprint;
- provider script identity, invocation evidence, and adoption time.

Adoption validates that every path stays inside the explicit video directory and that all provenance required by the active Legacy delivery policy exists. It grants no waiver for a missing mandatory compile report or malformed final artifact. When required provenance is absent, the input set blocks and identifies the rebuild or evidence step needed.

`acceptance-prepare` creates one script-owned Acceptance Execution Context under `review/acceptance/executions/<execution-id>/` and binds it to the immutable Legacy Acceptance Input Set. Its two Reviewer Task Envelopes and Task Claims use an `acceptance_execution` Task Authority Binding, so they require no `workflow/run.json`. Each validated Patch is committed through its own Acceptance Execution Mutation Intent before the provider may materialize the report. The context record is the module-local commit marker for Patch and report publication; it has no authority over historical workflow phase or delivery ownership.

The Final Acceptance Module uses the same dual Reviewer tasks, v2 materializer, criteria, failure rules, and Delivery Guard for both input tracks. Acceptance Report v2 provenance declares either a Kernel Artifact Generation binding or one Legacy Acceptance Input Set binding. Both are current fingerprinted inputs; neither accepts a v1 report as evidence. The Guard requires the current committed Acceptance Execution Context, both committed Patch generations, and the matching provider publication intent in addition to the report schema.

Creating a Legacy Acceptance Input Set or Acceptance Execution Context does not create `workflow/run.json`, import source media, infer checkpoints, resume a historical task, change deliverable Version Basis, or implement workspace migration. Any artifact change invalidates the set, execution context, rendered pages, Skeleton, and both Patches.

After the Global Gate Cutover, Acceptance Report v1 is rejected for every new delivery decision. Later Bilibili and YouTube Platform Kernel Cutovers transfer run ownership only and verify compatibility with the already active v2 gate.

## Consequences

The repository has one acceptance decision format throughout staged platform migration. Old PDFs can receive honest fresh dual review when their required final evidence is available. The v2 schema and Guard gain a bounded input-binding variant, while the semantic criteria and Reviewer behavior remain identical.
