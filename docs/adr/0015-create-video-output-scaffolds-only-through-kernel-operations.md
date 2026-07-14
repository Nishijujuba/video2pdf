# Create video output scaffolds only through kernel operations

A canonical directory list remains weak when prompts still ask agents or individual tools to create folders. Directory spelling, section identity, and disposable output names would continue to drift even though the documented layout is fixed.

## Considered Options

- Let agents create documented directories as needed: rejected because correct spelling and placement would still depend on prompt compliance.
- Let every tool create arbitrary parent directories: rejected because tools could silently establish new artifact locations outside the scaffold contract.
- Create every possible dynamic directory during initialization: rejected because section identifiers and some tool slots do not exist until later checkpoints.

## Decision

Only deterministic Video Workflow Kernel operations create directories governed by the Video Output Scaffold.

`init-run` creates the fixed structure:

```text
workflow/
workflow/tasks/
source/metadata/
source/subtitles/
source/media/
source/cover/
figures/
work/source-acquisition/
work/outline/
work/writers/
work/figures/
work/integration/
work/repairs/
review/pyramid/
review/consistency/
review/independent/
review/latex/
review/acceptance/attempts/
review/acceptance/executions/
review/acceptance/rendered_pages/
待删除/
```

After the accepted outline declares canonical `section_XX` identifiers, a section-scaffold operation creates matching `figures/section_XX/`, `work/writers/section_XX/`, and `work/figures/section_XX/` directories. Before a subagent starts, `task-prepare` creates its dynamic directory under `workflow/tasks/`. Before a tool needs disposable storage, a kernel operation may create only a registered `待删除` slot such as `bootstrap/`, `downloads/`, `frame-candidates/`, `latex-build/`, `pyramid-evaluator/`, `acceptance-rendered-pages/`, `task-promotions/`, or the deferred `migrations/` slot.

Agents receive paths from the Video Workflow Run Record or their handoff contract. They must not create alternate top-level, role, section, review, or disposable directory names. Artifact generators validate that their target directory belongs to the current scaffold version before writing.

The existing batch paths `review/consistency_review.md` and `review/independent_review.md` move to `review/consistency/report.md` and `review/independent/report.md`; batch reconcile code and tests must change with the scaffold implementation.

## Consequences

Directory creation becomes testable, idempotent, and independent of agent prompt compliance. Dynamic parallel work uses kernel-issued section identities. Existing scripts that call unrestricted parent-directory creation need contract-aware updates. The scaffold schema, validation command, path-length budget, unknown-directory policy, and legacy migration behavior require follow-up decisions.
