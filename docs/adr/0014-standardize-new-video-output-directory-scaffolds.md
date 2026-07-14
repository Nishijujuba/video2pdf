# Standardize new video output directory scaffolds

Existing video outputs use many synonymous top-level directories, including `source`, `sources`, `materials`, `figures`, `figs`, `frames`, `review`, `reviews`, `reports`, and several ad hoc work folders. Prompt-only naming guidance has allowed platform workflows and agents to create incompatible layouts, which weakens path validation, recovery, and artifact discovery.

## Considered Options

- Keep directory naming as skill guidance: rejected because the existing workspace demonstrates widespread drift.
- Give each platform adapter its own scaffold: rejected because downstream writing, figure, review, compile, and acceptance contracts are shared.
- Accept aliases and normalize them silently during every command: rejected because ambiguous directories can contain conflicting artifacts and silent rebinding can validate the wrong file.

## Decision

Run Initialization creates exactly these canonical top-level directories for every new Video Workflow Run:

```text
workflow/
source/
figures/
work/
review/
待删除/
```

The canonical figure directory is `figures/` in plural form. New runs must not create top-level aliases such as `figure/`, `figs/`, `sources/`, `materials/`, `reviews/`, or role-specific `work_*` directories. Video Platform Adapters and semantic agents operate inside the scaffold supplied by the Video Workflow Kernel.

The Video Output Directory root is reserved for stable workflow-facing document artifacts, including `outline_contract.md`, root-level `section_*.tex`, `main.tex`, and the normalized final PDF. Root-level sections remain compatible with the existing Pyramid output gate. Other artifacts must belong to one of the six canonical directories.

## Consequences

Artifact paths become deterministic and can be validated before tools or subagents run. The kernel can reject unexpected top-level directories in new runs. The fixed second-level layout, dynamic per-section directories, legacy-directory migration rules, and treatment of additional root-level document files require follow-up decisions.
