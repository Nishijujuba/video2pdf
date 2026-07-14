# Video Workflow Context

Status: active domain language. Component runtime activation is recorded in the [Video Workflow Kernel 2.0 decision map](../../adr/video-workflow-kernel-2.0-decision-map.md).

This context owns the deterministic lifecycle of a video-to-PDF run, including source preparation, content production, content assurance, repair, compilation, final evidence, resource admission, Batch projection, delivery coordination, and new-run naming. Source Acquisition, Content Production, Content Assurance, Repair Planning, Resource Admission, and Batch remain internal Modules or Adapters inside this context.

The Pyramid Evaluation Context owns Pyramid standards, semantic evaluation, and the Pyramid Gate Report. The Final Acceptance Context owns acceptance criteria, Reviewer judgment, and the final semantic delivery decision; this context consumes those published decisions and owns their invocation timing, freshness consequences, repair routing, and delivery mechanics.

## Video Workflow Kernel

The authoritative lifecycle boundary for a single video-to-PDF run. It owns deterministic coordination, artifact state, checkpoints, recovery, and delivery mechanics while semantic judgment remains with registered providers and agents.

## Video Platform Adapter

An internal source-specific adapter that supplies platform identity and acquisition behavior to the Video Workflow Kernel while preserving the shared downstream lifecycle.

## Video Workflow Run

One durable execution of the Video Workflow Kernel for a single Video Output Directory. Its identity survives coordinator-session changes and explicit delivery-ownership handoffs.

## Video Workflow Run Record

The per-run coordination authority for identity, phase, checkpoints, Artifact Generations, dependencies, and delivery references. Provider decisions retain authority in their own reports.

## Kernel Track

The execution track for a Video Workflow Run created and governed entirely through activated Kernel contracts and operations.

## Legacy Track

The historical execution track for output directories created outside activated Kernel coordination. Legacy acceptance can use an explicit Final Acceptance input binding without creating a Video Workflow Run Record.

## Global Gate Cutover

An atomic activation of one shared gate contract across every affected execution track, together with all executable and instructional surfaces required for that authority.

## Platform Kernel Cutover

An atomic transfer of new-run lifecycle authority for one source platform to the Video Workflow Kernel.

## Contract Schema Version

The explicit compatibility identity of one machine-readable workflow contract. Readers accept only registered compatible versions.

## Kernel Schema Registry

The closed structural authority for Kernel-owned machine-readable contracts and their supported versions. Cross-artifact invariants may extend validation without redefining contract fields.

## Contract Skeleton

A script-created contract instance containing the structural identities and current mechanical evidence available at its generation checkpoint. Semantic agents may supply only the bounded judgment slots assigned by the governing task.

## Judgment Patch

A schema-constrained semantic response produced under a Subagent Task Envelope for provider materialization. It carries only authorized judgment fields and has no checkpoint or final-decision authority by itself.

## Materialized Gate Report

The provider-published authoritative report produced after bounded semantic input is validated against its current Contract Skeleton. The report owns the gate decision while the Video Workflow Run Record owns its coordination consequences.

## Gate Provider

An independently authoritative semantic or mechanical provider invoked through a declared workflow contract. Its report meaning remains owned by its governing Context.

## Artifact Plan

The run-level declaration of expected governed artifacts, their owners, and the Workflow Checkpoints at which valid instances can first exist.

## Earliest Valid Generation Checkpoint

The first Workflow Checkpoint where every dependency required for a valid and current artifact is available.

## Workflow Phase

A coarse progress projection for human observability over a Video Workflow Run. It carries no continuation authority.

## Workflow Checkpoint

A dependency-aware unit of workflow readiness whose result is bound to declared current evidence.

## Checkpoint Dependency

A directed readiness relationship in which one Workflow Checkpoint requires current evidence from another.

## Artifact Generation

One committed version of a canonical workflow artifact, identified by its logical identity, content fingerprint, producer, and generation order.

## Checkpoint Freshness

The Kernel-computed condition that a completed Workflow Checkpoint still refers to current Artifact Generations and current prerequisites.

## Artifact Drift

The blocking state detected when a canonical artifact differs from its committed Artifact Generation outside a governed promotion or adoption operation.

## Run Migration Plan

A target-only, deferred description of changes required to move an existing run between supported contract or scaffold versions. It has no active execution authority until an explicit migration cutover activates the corresponding operation.

## Explicit Run Resume

The operation that continues a specifically identified existing Video Workflow Run after its identity and compatibility contracts validate.

## Bootstrap Probe

The bounded initial operation that establishes source identity, original title, and frozen task start time before the final run directory is named.

## Run Initialization

The deterministic transition from a successful Bootstrap Probe to a durable Video Workflow Run, named Video Output Directory, and initial coordination contracts.

## Video Output Directory

The durable artifact boundary for one video task, containing its source, content, review, compile, delivery, and disposable evidence.

## Normalized Video Title

The original platform title after project naming normalization, used as the human-readable identity seed for a new Video Output Directory.

## Video Output Scaffold

The versioned standard layout that defines the governed artifact zones for a new Video Workflow Run.

## Scaffold Version

The compatibility identity of the Video Output Scaffold rules that govern one run's artifact locations and path budget.

## Workflow Path Budget

The end-to-end Windows path capacity reserved for a Video Workflow Run and every descendant defined by its Scaffold Version.

## Stable Truncation Hash

A deterministic identity suffix retained when a human-readable title component must be shortened to satisfy the Workflow Path Budget.

## Video Deliverable Version

The explicit human-selected version of an intentional deliverable derived from the same canonical video source. It is independent from run identity, task attempts, contract versions, and scaffold versions.

## Version Basis

The declared evidence lineage for a Video Deliverable Version, distinguishing a rebuild from original source evidence from a revision that consumes a prior delivery.

## Final PDF Filename

The normalized delivered filename derived from the established article title or, when no separate article title exists, the original video title. It also carries any explicit Video Deliverable Version required by the run.

## Subagent Task Envelope

The immutable execution contract for one subagent assignment, binding its authority, dependencies, permitted evidence, write boundary, and required outputs.

## Task Authority Binding

The discriminated identity of the coordination authority allowed to validate and commit one subagent task.

## Generated Task Prompt

The immutable semantic prompt produced from registered role guidance and the applicable platform policy for one Subagent Task Envelope.

## Task Completion Gate

The deterministic validation boundary that decides whether a Task Attempt is eligible for promotion. A passing result leaves canonical state unchanged until promotion commits.

## Task Claim

The durable exclusive ownership record for one active logical task and its declared write set within a Task Authority Binding.

## Claim Fencing Token

The monotonic generation that identifies the current Task Claim and prevents a superseded worker from publishing late results.

## Task Attempt

One isolated execution of a logical subagent task whose outputs remain staged until validation and promotion succeed.

## Transactional Artifact Promotion

The recoverable operation that publishes a validated Task Attempt as canonical Artifact Generations under its Task Authority Binding.

## Mutation Intent

A durable cross-store record that coordinates one governed filesystem publication through preparation, commit-marker replacement, and final reconciliation.

## Run Promotion Slot

The exclusive per-run right to publish canonical artifacts and replace the Video Workflow Run Record through one non-terminal Mutation Intent.

## Cross-Run Control Store

The project-level transactional authority for coordination shared across runs, including claims, leases, scheduling, publication slots, and Mutation Intents. Per-run lifecycle authority remains in each Video Workflow Run Record.

## Control Store Unavailable

The global fail-closed state entered when the Cross-Run Control Store cannot prove its integrity or transaction authority. Governed Kernel mutations remain blocked until evidence-bearing restoration and reconciliation pass.

## Source Acquisition Module

The internal workflow module that turns platform evidence into a technically validated, immutable source handoff for downstream production.

## Source Acquisition Mode

The run-declared strategy for obtaining original-source evidence, separating fresh platform acquisition from explicit verified reuse.

## Source Acquisition Agent

The isolated semantic role used for fresh source preparation when subtitle choice, fallback selection, or explicit source-gap judgment requires interpretation.

## Source Acquisition Decision

The bounded semantic response that records permitted source-selection and fallback judgments while scripts retain ownership of acquisition mechanics and evidence structure.

## Validated Source Package

The complete script-finalized source-material handoff consumed by outline, writing, figure, and source-faithfulness work.

## Source Manifest

The machine-readable inventory and technical provenance record for a Validated Source Package.

## Verified Source Import

An explicit, validated, run-local reuse of original-source artifacts from a prior package that preserves the receiving run's independent evidence boundary.

## Source Reopen

The governed operation that permits a finalized source package to change and invalidates every dependent Workflow Checkpoint.

## Content Production Module

The internal deep Module that advances the platform-neutral production graph from a Validated Source Package through outline, section, figure, integration, and compile-readiness work.

## Figure Slot

A stable workflow identity for one intended figure placement and teaching purpose, shared by writers, figure producers, and integration.

## Figure Manifest

The formal Figure Agent deliverable that binds a Figure Slot to its asset, provenance, caption, and placement contribution without granting ownership of canonical section text.

## Required Figure Wave

The initial section-scoped set of Figure tasks declared by the accepted outline and eligible to run alongside Writer work.

## New Figure Candidate

A bounded Writer proposal for a teaching visual discovered during drafting. The Video Workflow Kernel decides admission and assigns any resulting Figure Slot.

## Incremental Figure Wave

The single bounded additional Figure task set that may follow Writer output for one section before section integration.

## Integration Manifest

The exact committed section, figure, terminology, and artifact generations selected to produce the integrated document.

## Compile Manifest

The exact allowlist of committed project inputs authorized for one guarded compile attempt.

## Compile Dependency Closure

The condition where every project input actually consumed by a guarded compile is covered by the current Compile Manifest and every remaining input belongs to an approved runtime dependency.

## Compile Dependency Gap

A blocking mechanical finding that identifies a missing, undeclared, escaped, stale, or unsupported compile input.

## Delivery Glossary

The workflow-owned terminology contract for source-English expressions that carry explanatory work in a non-English teaching PDF. It provides stable naming evidence to writers, Content Assurance, and any manifest-authorized Final Acceptance review.

## Core English Expression

A source-language expression whose stable meaning is required to preserve the teaching argument and therefore belongs in the Delivery Glossary.

## Body Display Strategy

The Delivery Glossary policy that controls how a Core English Expression appears in reader-facing prose.

## Source English Preservation Location

The Delivery Glossary policy that records where the original source expression remains recoverable for evidence alignment.

## New Term Candidate

A post-outline proposal to add a newly discovered Core English Expression to the Delivery Glossary before consistency review.

## Content Assurance Module

The internal deep Module that coordinates independent Consistency and Source-Faithfulness reviews over one integrated draft generation.

## Consistency Reviewer

The read-only Content Assurance Adapter that evaluates terminology, notation, references, transitions, figure-slot use, and cross-section coherence.

## Source-Faithfulness Reviewer

The read-only Content Assurance Adapter that compares the integrated draft and figure provenance with the Validated Source Package for omissions, unsupported claims, and source drift.

## Content Assurance Checkpoint

The computed readiness state requiring fresh passing Consistency and Source-Faithfulness reports bound to the same integrated draft generation.

## Content Assurance Failure Set

The normalized repair input that collects current blocking findings from both Content Assurance reports while preserving those reports as separate decision authorities.

## Repair Planning Module

The internal deterministic Module that converts a current Content Assurance failure set or Final Acceptance failure decision into conflict-aware repair work.

## Repair Plan

The validated contract that binds one repair cycle's failures to task capabilities, dependencies, and exact read and write boundaries.

## Repair Capability

A registered class of artifact change with a deterministic ownership boundary for repair planning.

## Integration Repair

A repair task that owns changes whose required write boundaries overlap or cannot be assigned safely to independent repair workers.

## Compile Provenance

The mechanical evidence that binds a compile operation to its declared inputs, provider identity, execution mode, and produced artifacts.

## LaTeX Compile Guard

The controlled workflow boundary that enforces bounded and provenance-bearing LaTeX compilation.

## LaTeX Compile Report

The machine-readable Compile Provenance record for one guarded LaTeX compile. It carries no semantic quality decision.

## Temporary Compile

A diagnostic compile used during production or repair whose outputs carry no final-delivery authority.

## Final Compile

The guarded compile that produces the PDF intended for delivery and current final Compile Provenance.

## Final Artifact Seal

The immutable binding of the assured integrated document and every declared final compile input before Final Compile.

## Render Evidence Manifest

The mechanical binding of one final PDF generation to its complete rendered-page set and fingerprints. It proves coverage and freshness while visual judgment remains in the Final Acceptance Context.

## Final Evidence Checkpoint

The computed workflow readiness state requiring a current Final Artifact Seal, Final Compile, final PDF, compile provenance, allowed-artifact boundary, and complete Render Evidence Manifest.

## Allowed Artifact Manifest

The workflow-generated allowlist of final artifacts and rendered evidence exposed to the Final Acceptance Context. It constrains review access and carries no semantic decision.

## Resource Admission Module

The internal deep Module that admits runnable tasks only when every declared constrained resource has available authority.

## Resource Class

A named constrained execution capacity consumed by a task or provider operation.

## Resource Admission Configuration

The versioned project contract that defines admission capacity and starvation-protection policy for Resource Classes.

## Resource Lease

The durable record that reserves a task's complete admitted Resource Class request until its physical consumption reaches an evidence-backed terminal state.

## Unknown Resource Lease

A Resource Lease whose worker outcome cannot be proven after recovery and therefore continues consuming its declared capacity.

## Overcommitted Resource Class

A Resource Class whose existing Lease usage exceeds a newly activated lower capacity. Existing work drains while new admissions preserve the reduced limit.

## Resource Circuit Breaker

A fault-domain pause that blocks new admissions for an affected resource or source platform while unrelated work remains eligible.

## Fairness Group

The scheduling identity used to share Resource Admission opportunities across independent runs and Batch groups.

## Draining Reservation

A durable starvation-protection state that reserves future availability of a task's complete Resource Class set while existing holders finish.

## Pending Draining Reservation

A sequenced starvation-protection request waiting behind an earlier reservation with an overlapping Resource Class set.

## Batch Supervisor

The internal shallow coordinator that enumerates multi-item sources, creates or resumes independent Video Workflow Runs, and submits their work through Resource Admission.

## Batch Record

The durable Batch-level record for source selection, deterministic item order, run mappings, and rebuildable item projections. It carries no per-video lifecycle or delivery authority.

## Batch Item Projection

A read-only, rebuildable view of one Video Workflow Run inside a Batch Record.

## Delivery Target

The bounded workflow projection that identifies the final artifacts, required evidence, ownership, and delivery stage for one Video Output Directory. Kernel Track lifecycle authority remains in the Video Workflow Run Record.

## Session-Scoped Delivery Target

The active Delivery Target projection routed through one coordinator session for bounded Stop-hook and final-delivery checks.

## Delivery Task Index

The project-level projection of delivery tasks used for ownership, recovery, and observability across video runs.

## Delivery Target Ownership

The exclusive relationship between one Video Output Directory and the coordinator session authorized to advance its delivery lifecycle.

## Delivery Target Handoff

The explicit, auditable transfer of Delivery Target Ownership between coordinator sessions.

## Delivery Lifecycle Mutation Intent

The specialized Mutation Intent that coordinates one delivery-stage, ownership, or archival change across the Run Record and every delivery projection.

## Projection Publication Slot

The durable exclusive right to replace one canonical delivery projection or archive target during a Delivery Lifecycle Mutation Intent.

## Delivery Guard

The mechanical final-delivery gate that verifies current paths, manifests, fingerprints, compile provenance, final evidence, and the Final Acceptance Context's semantic decision. It owns no semantic quality judgment.

## Delivery Guard Report

The machine-readable proof that the Delivery Guard evaluated the current Delivery Target and either authorized delivery or recorded a blocking contract failure.
