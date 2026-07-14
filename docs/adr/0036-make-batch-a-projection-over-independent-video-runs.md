# Make Batch a projection over independent video runs

The current Batch runner embeds a complete single-video workflow in every child prompt, keeps its own item status model, and treats one global concurrency value as the scheduling boundary. This duplicates workflow ownership and allows Batch behavior to drift from the single-video pipeline. It also reads the removed `args.venv_python` parser attribute while building a new manifest, so a new batch can fail before work begins.

## Considered Options

- Keep Batch as a second end-to-end workflow engine: rejected because prompt copies, success rules, recovery logic, and acceptance behavior can diverge from the Video Workflow Kernel.
- Let Batch infer progress from files in each output directory: rejected because file presence cannot prove checkpoint freshness, acceptance, or guarded delivery.
- Store authoritative item stages in both Batch and per-video records: rejected because reconciliation cannot identify which writer is correct after a crash.
- Make Batch a shallow supervisor over independent Video Workflow Runs: selected because it preserves one coordination authority per video and makes recovery deterministic.

## Decision

The Batch Supervisor exposes three initial operations:

- `batch-plan` enumerates a playlist, collection, multi-part video, or explicit URL set; applies the requested selection; and writes deterministic item order.
- `batch-run` asks the Video Workflow Kernel to create or explicitly resume one independent Video Workflow Run per selected item and submits runnable work through the Resource Admission Module.
- `batch-recover` reconciles referenced runs and rebuilds every Batch Item Projection from authoritative run, claim, attempt, checkpoint, and delivery evidence.

The Batch Record owns batch identity, source selection, item order, `run_id`, Video Output Directory, and the latest read-only projection for each item. A projection may expose phase, current checkpoint, blocker, and delivery outcome. The underlying Video Workflow Run Record remains authoritative and the Batch Supervisor never writes a video's phase, checkpoint, acceptance result, or delivery stage directly.

An item is successful only when the referenced Video Workflow Run has reached the delivered checkpoint with the required fresh passing Delivery Guard evidence. A PDF path, output-directory existence, process exit code, or cached Batch status cannot establish success.

Platform authentication failures open the corresponding Resource Circuit Breaker and stop admission of later platform work. Already started independent runs retain their own task and recovery state. Video-specific semantic or quality failures remain local to their run.

The Resource Admission Configuration replaces Batch's global end-to-end concurrency flag. The Supervisor submits only currently admitted work and does not pre-create futures for every item.

The first implementation must remove the stale `args.venv_python` access and derive runtime paths from the project-owned runtime contract. It must replace the current free-form child prompt with kernel-issued Task Envelopes and replace duplicated item-success logic with run projection.

## Consequences

Single-video and batch execution share the same checkpoints, schemas, repair limits, and delivery authority. Batch recovery becomes projection rebuilding instead of competing state repair. Batch reports remain useful for observability while carrying no independent delivery authority. Queue ordering and starvation behavior still require an explicit scheduling decision.
