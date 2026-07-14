# Separate semantic prompts from mechanical workflow contracts

The same output paths, Pyramid commands, compile instructions, acceptance steps, and delivery rules currently appear across project instructions and long Bilibili, YouTube, and Batch skill documents. Dynamic mechanics in prose consume context, drift independently, and force agents to reconstruct facts that scripts can generate. Semantic role guidance still requires prompt form and should remain readable to agents and humans.

## Considered Options

- Keep every skill self-contained with complete workflow instructions: rejected because common mechanics must be updated repeatedly and remain prompt-authoritative.
- Move every instruction into Python code: rejected because writing, figure selection, source interpretation, and review judgment require semantic guidance.
- Assign one authority to each kind of information and generate task prompts from versioned semantic templates: selected because mechanical and semantic changes become local.

## Decision

The project adopts this responsibility matrix:

- `CONTEXT-MAP.md` owns context relationships and routes each term to its single authoritative glossary under `docs/contexts/`.
- `docs/adr/` owns decisions, alternatives, assumptions, and consequences.
- the Workflow Kernel Package and registered schemas own paths, naming, scaffold creation, state transitions, checkpoints, retries, fingerprints, skeletons, provider invocation, and task permissions.
- `AGENTS.md` and `CLAUDE.md` own repository safety rules, mandatory kernel use, required agent roles, and short non-bypassable quality principles.
- platform `SKILL.md` files own triggers, platform semantics, cookie and subtitle policy, and content-quality guidance.
- Role Prompt Templates own language-understanding work for Source Acquisition, Outline, Writer, Figure, Consistency, Independent Review, Acceptance Review, and repair roles.
- each Subagent Task Envelope owns the current run's dynamic inputs, outputs, paths, permissions, schemas, and evidence fingerprints.

Versioned semantic templates live under `prompts/video-workflow/roles/`; Bilibili and YouTube semantic overlays live under `prompts/video-workflow/platforms/`. A canonical project-policy template supplies the workflow block synchronized into `AGENTS.md` and `CLAUDE.md`, while file-specific rules outside that block remain independent.

`task-prepare` selects the registered role template and applicable platform overlay, validates their declared versions, computes SHA-256 fingerprints, and writes an immutable Generated Task Prompt in the task directory. The task envelope records those template identities and fingerprints. The generated prompt references the envelope and does not copy its paths, schemas, fingerprint values, or transition rules.

The Workflow CLI exposes `status` and `next-actions` so current mechanics come from state instead of skill prose. CLI `--help` is the authority for command parameters. Skills keep a minimal bootstrap invocation and semantic instructions; they do not maintain command sequences for later checkpoints.

`workflow-policy-check` verifies template registration and versions, Generated Task Prompt fingerprints, synchronization of marked project-policy blocks, and known forbidden legacy path or stage literals in managed prompt regions. This mechanical lint cannot prove every prose sentence has perfect ownership; review remains necessary for semantic duplication outside detectable markers.

## Consequences

Agent context is spent on source content and judgment. Mechanical changes become local to kernel contracts. Shared role behavior stops drifting between Bilibili and YouTube. Prompt provenance becomes auditable per task. Initial implementation must extract current semantic guidance carefully so quality requirements are not lost while duplicated procedures are removed.
