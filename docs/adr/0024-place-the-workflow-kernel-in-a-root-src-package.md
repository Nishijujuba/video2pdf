# Place the workflow kernel in a root src package

The current repository has no installable root Python package. Reusable scripts live inside individual skills and are invoked by file path. YouTube already calls the Bilibili compile wrapper, and the batch runner duplicates naming, directory, state, and review logic. Placing the shared workflow engine inside any existing platform or gate skill would preserve incorrect ownership and fragile cross-skill imports.

## Considered Options

- Extend `final-delivery-acceptance/scripts/delivery_guard.py` into the full workflow engine: rejected because final acceptance would own unrelated generation and orchestration state.
- Add `.agents/skills/video-workflow-kernel/`: rejected because `.agents/skills` is not a shared import package and the kernel is project infrastructure rather than a semantic agent capability.
- Put all implementation beside one-off files under `scripts/`: rejected because the directory already mixes unrelated utilities and would expose internal modules as entrypoints.
- Use a project-root `src` package with a thin stable launcher: selected because implementation ownership, imports, schemas, and CLI compatibility become explicit.

## Decision

The Video Workflow Kernel lives in `src/video2pdf_workflow_kernel/`. Its initial modules cover CLI dispatch, contracts, run state, scaffold and path policy, tasks and claims, artifact generations and promotion, checkpoints, source acquisition coordination, and platform adapters. Bilibili and YouTube implementations live behind the shared adapter interface under `src/video2pdf_workflow_kernel/adapters/`.

The stable entrypoint is `scripts/video_workflow.py`. It contains only repository-root discovery, `src` loading, UTF-8 process setup, and delegation to `video2pdf_workflow_kernel.cli:main`. Its official invocation uses the project-required Python runtime:

```powershell
D:\Project\video2pdf\kimi\.venv\Scripts\python.exe -X utf8 -B scripts\video_workflow.py <command>
```

The launcher resolves paths from `__file__`, so commands do not depend on the caller's working directory or an editable installation. The first implementation does not require creation of a root packaging project solely to execute the CLI.

Kernel-owned schemas live under `schemas/video-workflow/v1/`. The initial registry contains run record, Artifact Plan, Subagent Task Envelope, Source Manifest, and Source Acquisition Decision contracts. Gate-owned schemas remain with their Gate Providers. The kernel references each provider by an explicit executable path, schema or contract version, allowed inputs, and expected evidence instead of importing its private implementation.

Bilibili, YouTube, and Batch skills call the Workflow CLI and retain their semantic instructions. Batch remains a supervisor for playlist or multipart enumeration and parallel scheduling; per-video naming, initialization, task state, checkpoint reconciliation, and delivery readiness move into the kernel.

The existing batch constructor at `.agents/skills/bilibili-batch-render-pdf/scripts/run_batch.py` accesses the removed `args.venv_python` attribute while creating new work. This is a confirmed first-implementation defect and must be covered by a regression test when Batch is connected to the kernel.

## Consequences

Platform-neutral workflow code has one importable owner. Skills stop copying generated paths and state rules. Gate authority remains separate from coordination authority. Existing skill-local tools can be integrated through provider contracts before their physical relocation, allowing code ownership cleanup to proceed incrementally.
