# Separate run identity from manual deliverable version

Repeated execution for the same video can mean several different things: retrying initialization, resuming interrupted work, intentionally producing a revised PDF, or starting an unrelated new run. Reusing directories based on a matching URL, title, or modification time would make those intentions ambiguous and could overwrite durable evidence.

## Considered Options

- Automatically resume the most recent directory for the same video: rejected because source matching and recency do not prove user intent or workflow identity.
- Keep one mutable directory per video and overwrite its contents for later versions: rejected because it destroys auditability and can invalidate gate evidence silently.
- Treat every retry or acceptance repair as a new `v2`-style version: rejected because operational retries and intentional deliverable revisions have different meanings.
- Give every run an immutable identity and give intentional deliverables a separate human-selected version: selected because recovery, idempotency, and content lineage remain independently observable.

## Decision

Every Bootstrap Probe creates an immutable `run_id`. A normal invocation creates a new Video Workflow Run even when its canonical source identity matches an existing run. Continuing existing work requires an Explicit Run Resume that names the existing Video Output Directory. The kernel must not discover or reuse a run by title, URL, timestamp, or filesystem recency.

Repeating Run Initialization for the same `run_id` is idempotent only when `workflow/run.json`, canonical source identity, resolved output path, and scaffold version match. Any mismatch at the resolved run path blocks the operation without overwriting existing files.

The initial human-readable directory candidate retains the normalized title and task-start timestamp. When another run already owns that same candidate, the new run resolves a collision-safe candidate with `_r{run-id-prefix-8}`. The kernel shortens the title portion as needed so both the 96-unit component budget and the 240-unit absolute Workflow Path Budget remain satisfied. If that resolved collision-safe path is already owned by a different identity, initialization blocks.

A Video Deliverable Version is stored separately in the Video Workflow Run Record as a positive integer. Its human form is `v1`, `v2`, and later values. The default is `v1`; the kernel accepts a higher value only when the user explicitly requests it. The kernel never infers or increments this value by scanning prior directories. Generating `v2` for the same video creates a new run and preserves the earlier run.

The version field is always present in `workflow/run.json`. Default `v1` paths preserve the existing unversioned form. For explicitly requested `v2` and later values, the version marker is visible in both the output directory and final delivered PDF: `{normalized-video-title}_v2_{yyyyMMdd_HHmmss}` and `{normalized-article-title}_v2.pdf`. The kernel includes this marker when applying component and absolute path budgets. Multiple independent runs at the same Video Deliverable Version remain distinct through their timestamps and `run_id` values.

Every run also declares its Version Basis. `source_only` means downstream generation uses original video evidence such as video, audio, subtitles, cover material, platform metadata, and their Source Manifest. It does not require or imply an earlier delivered PDF, TeX tree, or review report. `prior_delivery` means the run intentionally consumes an earlier delivery and therefore records a verifiable `supersedes` binding to its `run_id` and artifact fingerprints, or to a fingerprinted legacy artifact when no run record exists.

Every explicitly selected version above `v1` records a human-authored `revision_reason`. The kernel does not require version numbers to be contiguous and does not infer lineage from the version number. A `source_only` version omits `supersedes`; a `prior_delivery` version requires it. Both modes remain bound to the canonical source identity for the same video.

## Consequences

Resume behavior becomes deterministic and auditable. Same-second launches cannot overwrite each other. A user can intentionally produce `v2` for the same source without confusing that revision with a retry, repair attempt, or new session. Existing `v1` naming remains stable, and later versions are recognizable without opening metadata. Higher versions may rebuild directly from original source evidence, while runs that consume a prior delivery preserve verifiable lineage.
