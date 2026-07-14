# Separate target-design acceptance from runtime activation

The Workflow Kernel decisions are being accepted before their scripts, schemas, providers, and skills exist. Treating an accepted ADR as immediate runtime activation would make `CONTEXT-MAP.md` and the owning context glossaries describe dual Reviewers and Kernel-owned artifacts while current `AGENTS.md`, `CLAUDE.md`, and skills still execute the Legacy Track. Updating all current instructions to the target behavior before executables exist would also make ordinary runs impossible.

## Considered Options

- Treat every accepted design ADR as immediately active runtime policy: rejected because unimplemented commands and schemas cannot authorize work.
- Rewrite all Legacy instructions to target behavior during design: rejected because the project still needs one executable current workflow before cutover.
- Keep target design undocumented until implementation: rejected because implementation tickets need an approved coherent contract.
- Distinguish accepted target design from explicit runtime activation: selected because planning and execution each retain one authority.

## Decision

ADRs beginning with ADR 0008 and their Workflow Kernel 2.0 glossary terms are accepted target design. They guide implementation and review but do not activate executable behavior by being merged.

Before activation, Legacy Track behavior is governed by the current `AGENTS.md`, `CLAUDE.md`, installed project skills, validators, guards, and schemas. No agent may run an unimplemented Kernel command, create a synthetic `workflow/run.json`, claim Acceptance Report v2 authority, or combine target mechanics with a Legacy Track run.

Runtime authority changes only through an explicit cutover whose versioned Exit Evidence Manifest proves that every member of its atomic group is present and passing. A Global Gate Cutover may activate a shared gate such as Acceptance v2 across both tracks. A Platform Kernel Cutover activates Kernel Track run creation for one source platform. Each cutover updates the relevant instructions, mirrored skills, schemas, providers, hooks, guards, and tests in one repository change.

After a cutover, the new executable contract governs its declared scope. Unaffected Legacy components continue under their existing policy until their own cutover. Status documentation must name each component as `target_only`, `active_legacy`, `active_global_gate`, or `active_kernel` and cannot infer activation from an ADR number alone.

## Consequences

The repository can hold a complete future architecture without creating two active workflow authorities. Every implementation issue must state whether it builds inactive target capability or performs an authority cutover. Policy checks must reject target commands in Legacy instructions before activation and stale Legacy commands inside an activated scope afterward.
