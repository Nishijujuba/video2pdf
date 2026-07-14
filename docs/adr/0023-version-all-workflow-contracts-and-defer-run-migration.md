# Version all workflow contracts and defer run migration

The kernel will generate several durable JSON contracts and a versioned directory scaffold. Resume and validation cannot safely infer compatibility from field presence or silently rewrite older evidence. Full migration support also carries transaction, preservation, and semantic revalidation costs that are not required to establish the first kernel contract.

## Considered Options

- Leave versions implicit until a breaking change occurs: rejected because the first incompatible artifact would already lack a reliable interpretation key.
- Silently upgrade contracts during resume: rejected because resume would mutate evidence and could hide semantic invalidation.
- Implement complete migration machinery in the first kernel release: deferred because it expands the initial critical path without improving new-run determinism.
- Version every contract now and register an explicit future migration protocol: selected because compatibility fails safely while migration implementation remains independently schedulable.

## Decision

Every `workflow/run.json`, Subagent Task Envelope, Artifact Plan, Source Manifest, and other kernel-owned machine-readable contract records `schema_name`, `schema_version`, and `kernel_version`. The Video Workflow Run Record additionally records `scaffold_version`. Pyramid, compile, acceptance, and other gate-owned reports retain independently versioned schemas controlled by their validators.

Readers accept only explicitly registered compatible versions. Missing or unknown versions block the operation with a machine-readable compatibility error. Resume never performs an implicit migration. `workflow-doctor` is a read-only diagnostic contract that reports versions, supported ranges, missing fields, scaffold differences, and migration need.

The migration design is registered for a later milestone. `migrate-run --plan` will produce a deterministic Run Migration Plan without modifying the run. A future explicit `migrate-run --apply` will preserve old contract files under the script-created `待删除/migrations/<migration-id>/`, use a transaction journal and commit marker, and block on unknown files or path conflicts. Reports that cannot be upgraded mechanically will remain historical evidence while their checkpoints become `stale` and require fresh evaluation.

No `migrate-run` execution path is required in the first implementation phase. Initial implementation covers version emission, supported-version validation, fail-closed resume, and read-only diagnostics. Encountering an older or unsupported run produces a migration-required brief instead of changing it.

## Consequences

New runs are self-describing from the first release. Unsupported state cannot be interpreted optimistically. The initial kernel stays focused on deterministic creation and validation. Legacy and future run migration remains a recorded design obligation with an explicit scope boundary rather than an undocumented compatibility promise.
