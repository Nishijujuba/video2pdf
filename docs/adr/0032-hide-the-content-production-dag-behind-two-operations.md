# Hide the content production DAG behind two operations

Bilibili and YouTube skills repeat long-video segmentation, outline, writer, figure, Pyramid, integration, and compile ordering. Writer and Figure agents also have overlapping responsibility for captions, footnotes, and section TeX. The current compile wrapper recursively gathers files, allowing unrelated workspace contents to enter compilation. Callers should not need to reproduce this dependency graph or resolve shared writes.

## Considered Options

- Keep the production sequence in each platform skill: rejected because common dependencies and recovery rules remain prompt-owned and duplicated.
- Give one agent the complete PDF production task: rejected because context, concurrency, and independent gates collapse into one wide assignment.
- Let Writer and Figure agents both edit section TeX and reconcile later: rejected because completion order and merge behavior would decide canonical content.
- Use a deep production Module with isolated artifacts and deterministic integration: selected because the Interface stays small while orchestration complexity gains one owner.

## Decision

The Content Production Module exposes `production-plan(run)` and `production-advance(run)`. `production-plan` returns the currently runnable Subagent Task Envelopes from the checkpoint graph. `production-advance` validates completed attempts, performs Transactional Artifact Promotion, invokes applicable Gate Providers, and returns the next task set or a machine-readable block. Bilibili, YouTube, and Batch callers do not reproduce the internal DAG.

The initial dependency order is:

1. Validated Source Package;
2. Outline task and Outline Pyramid Gate;
3. Section Scaffold creation;
4. section-scoped Writer and Figure tasks where dependencies permit;
5. deterministic section integration and Section Pyramid Gate;
6. Integration Manifest creation and full-document integration;
7. Main Pyramid Gate;
8. Compile Manifest creation and guarded compilation.

Different canonical sections may run concurrently. Every canonical artifact retains one active writer. Writer Agents produce section content in attempt staging and reference kernel-issued Figure Slots. They may declare bounded new-figure candidates for later orchestration, but they do not create figure assets or edit Figure Manifests.

Figure Agents produce assets, source provenance and timestamp evidence, captions, Figure Manifests, and slot-bound TeX snippets in their own staging areas. They cannot edit `section_*.tex`. Integration scripts validate slot identity and contract completeness, then compose the accepted writer artifact with the accepted figure snippets into the canonical section generation. Section Pyramid evaluates the composed reader-facing section.

After every section generation passes its required gate, the kernel creates an Integration Manifest that fixes the exact section, figure, terminology, and artifact generations used to produce `main.tex`. Main Pyramid evaluates that integrated artifact.

The Compile Manifest lists every committed input allowed into guarded compilation. The compile provider stages only those files and their registered support dependencies. Recursive copying of the run directory is unsupported for new Kernel runs.

## Consequences

The production DAG and retry behavior gain one owner across both platforms. Writer and Figure work can proceed concurrently without shared-file races. Figures remain traceable to teaching purpose and source evidence. Compile inputs become reproducible and exclude unrelated files. Figure-wave scheduling and writer-proposed candidate admission require a follow-up decision.
