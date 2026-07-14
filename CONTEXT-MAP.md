# Context Map

This repository uses four active contexts and one supporting context. Every canonical term has one owning glossary; cross-context consumers use the Published Language below and do not redefine it.

Runtime activation is independent from domain-document presence. The [Video Workflow Kernel 2.0 decision map](./docs/adr/video-workflow-kernel-2.0-decision-map.md) records component-level `target_only`, `active_legacy`, `active_global_gate`, and `active_kernel` status.

## Contexts

| Context | Classification | Owns | Excludes |
|---|---|---|---|
| [Project Governance](./docs/contexts/project-governance/CONTEXT.md) | active | Current Project 2.0 planning language and Human Publication authority | Runtime state and frozen Project 1.0 execution language |
| [Video Workflow](./docs/contexts/video-workflow/CONTEXT.md) | active | Run lifecycle, source preparation, production, assurance, repair, compile provenance, resources, Batch projections, and delivery mechanics | Pyramid semantic judgment, Final Acceptance semantic judgment, and historical-directory relocation |
| [Pyramid Evaluation](./docs/contexts/pyramid-evaluation/CONTEXT.md) | active | Pyramid standards, evaluation targets, semantic review, Pyramid Gate Reports, and waiver boundaries | Workflow invocation timing, checkpoints, and continuation state |
| [Final Acceptance](./docs/contexts/final-acceptance/CONTEXT.md) | active | Final quality criteria, Reviewer partitions, semantic evidence, and the Acceptance Report | Artifact generation, compilation, Delivery Targets, Delivery Guard mechanics, and delivery lifecycle state |
| [Legacy Workspace Maintenance](./docs/contexts/legacy-workspace-maintenance/CONTEXT.md) | supporting | Evidence-based classification and relocation of historical video documentation directories | New-run naming, Final PDF naming, Run Migration, and active workflow state |

## Published Language and Relationships

| Relationship | Published Language | Authority boundary |
|---|---|---|
| Project Governance → implementation work | `Project 2.0 Spec`, `Implementation Ticket`, `Human Publication Gate` | Approved planning defines implementation scope and carries no runtime or delivery authority. |
| Video Workflow → Pyramid Evaluation | `Pyramid Evaluation Target` plus the exact `Artifact Generation` and evaluation context | Video Workflow chooses when and what to evaluate; Pyramid Evaluation owns the semantic result. |
| Pyramid Evaluation → Video Workflow | `Pyramid Gate Report` | The report owns Pyramid judgment; Video Workflow owns checkpoint, repair, stop, and continuation consequences. |
| Video Workflow → Final Acceptance | `Final Evidence Checkpoint`, `Allowed Artifact Manifest`, `Render Evidence Manifest`, and workflow-owned `Judgment Patch` mechanics | Video Workflow owns evidence production and task mechanics; Final Acceptance controls what evidence may support its semantic decision. |
| Final Acceptance → Video Workflow | `Acceptance Report` | The report is the sole machine-readable final quality decision; Video Workflow owns Delivery Guard evaluation and delivery progression. |
| Video Workflow → Legacy Workspace Maintenance | `Normalized Video Title` and canonical naming boundaries | Historical relocation reuses current normalization language without creating or mutating a Video Workflow Run. |
| Legacy Workspace Maintenance → operators | `Valid Video Output Directory`, `Video Artifact Date`, and `Historical Workspace Relocation` | Relocation results authorize historical directory movement only and carry no active Run authority. |

## Internal Workflow Boundaries

Source Acquisition, Content Production, Content Assurance, Repair Planning, Resource Admission, and Batch are Modules or Adapters inside the Video Workflow Context. Their interfaces reduce coupling while their state remains governed by the shared Video Workflow Run, Checkpoint, Artifact Generation, task, and promotion model.

## Archived Language

- [Project 1.0 Planning Context](./docs/archive/project-1.0/CONTEXT.md) preserves the frozen local Issue and dependency-view language. It cannot govern new Project 2.0 planning.
- [Pre-Context-Map Project Glossary](./docs/archive/pre-context-map/CONTEXT.md) preserves the former root monolith as historical evidence. It is superseded and has no current terminology authority.

## Decision Records

The repository retains one global ADR ledger under [`docs/adr/`](./docs/adr/). [ADR 0057](./docs/adr/0057-adopt-multi-context-domain-documentation.md) records this domain-document boundary; context-specific readers follow the map to relevant global decisions rather than relocating ADRs.
