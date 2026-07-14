---
status: accepted
---

# Adopt multi-context domain documentation

The root glossary grew to cover Project planning, the Video Workflow Kernel, Pyramid evaluation, Final Acceptance, and historical workspace maintenance under one language boundary. The project will replace that active monolith with a root `CONTEXT-MAP.md` and context-owned glossaries under `docs/contexts/`, so every canonical term has one owner and every cross-context exchange is explicit.

## Considered Options

- Keep one root glossary and add section headings: rejected because independent authorities and lifecycles would remain hidden behind one reading boundary.
- Create one context for every Module, Adapter, Agent role, or artifact family: rejected because Content Production, Source Acquisition, Content Assurance, Resource Admission, and Batch share the Video Workflow Run model and remain internal workflow boundaries.
- Keep a root compatibility glossary beside the map: rejected because it would preserve a second terminology authority and recreate the context load this decision removes.
- Use four active contexts plus one supporting context: selected because the split follows semantic decision authority, lifecycle ownership, and Published Language seams.

## Decision

The active contexts are Project Governance, Video Workflow, Pyramid Evaluation, and Final Acceptance. Legacy Workspace Maintenance is a supporting context. Frozen Project 1.0 planning language lives under `docs/archive/project-1.0/` and cannot govern new planning.

Legacy Workspace Maintenance may own canonical terms only for historical-directory identity, classification, and relocation. Its supporting status carries no authority over new-run naming, workflow lifecycle, or final delivery.

The previous root `CONTEXT.md` moves intact to `docs/archive/pre-context-map/CONTEXT.md` as historical evidence; no active root compatibility file remains. `CONTEXT-MAP.md` is the routing and relationship authority, while each `docs/contexts/<context>/CONTEXT.md` file is the sole authority for its terms. Consumers reference owner definitions and do not redefine Published Language locally.

Final Acceptance owns semantic quality decisions and the Acceptance Report. Video Workflow owns compile provenance, Delivery Targets, Delivery Guard mechanics, and delivery lifecycle state. Pyramid Evaluation owns its standards and Pyramid Gate Report; Video Workflow owns the checkpoints that invoke and consume that report. Content Production, Source Acquisition, Content Assurance, Resource Admission, and Batch remain Video Workflow Modules or Adapters.

The canonical vocabulary replaces `Pyramid Checkpoint` with `Pyramid Evaluation Target` and replaces the generic Pyramid result name `Gate Report` with `Pyramid Gate Report`. An active glossary admits a term only when it governs state, lifecycle, or authority; defines a formal exchange contract; or carries project-specific meaning that general technical language cannot preserve.

The global `docs/adr/` ledger and numbering remain unchanged. Component activation state remains in `video-workflow-kernel-2.0-decision-map.md`, because one context may contain active Legacy behavior and accepted target-only contracts at the same time.

## Consequences

`AGENTS.md`, `CLAUDE.md`, `docs/agents/domain.md`, active target-design references, and mirrored Delivery Glossary tests must stop depending on a root glossary. ADRs 0001–0007 and the frozen `docs/prd/` and `docs/issues/` records retain their historical wording, so their old root-glossary references may no longer resolve. Any root-glossary authority reference in ADR 0008 or later is an active documentation defect.

Active glossary definitions remain concise and contain identity, boundary, and ownership only. Paths, commands, schema fields, algorithms, thresholds, implementation slices, and tests stay with their executable contracts, ADRs, or implementation documentation. The repository adds no dedicated Context documentation validator; review and existing read-only checks remain responsible for structural consistency.
