# Start implementation with an offline source-ready tracer bullet

The planned Workflow Kernel spans platform probing, deterministic filesystem creation, schemas, state transitions, subagent execution, content production, assurance, acceptance, repair, delivery, and Batch scheduling. Starting with all external providers would combine filesystem, network, cookie, downloader, Whisper, LLM, LaTeX, and PDF-rendering failures before the coordination core has a proven recovery model. Starting with schemas alone would leave the essential cross-module behavior untested.

## Considered Options

- Implement every contract and directory without an executable vertical path: rejected because schema completeness cannot prove transactional initialization or recoverability.
- Begin with a live Bilibili download and semantic Source Acquisition Agent: rejected because external faults would obscure Kernel defects during the first slice.
- Begin with final Acceptance v2 and adapt upstream artifacts later: rejected because acceptance requires current Artifact Generations and checkpoints that do not yet exist.
- Run an offline fixture from Bootstrap Probe through `source_ready`: selected because it crosses the first durable workflow boundary using controlled evidence.

## Decision

The first implementation slice is the Source-Ready Tracer Bullet. It introduces:

- the root `src/video2pdf_workflow_kernel/` package and thin `scripts/video_workflow.py` CLI;
- the initial Schema Registry and versioned contracts needed by this slice;
- a Fixture Platform Adapter backed by a small immutable local source package;
- Bootstrap Probe and Run Initialization;
- title normalization, UTF-16 Windows path budgeting, collision handling, and deterministic complete Scaffold creation;
- the initial Video Workflow Run Record and Artifact Plan;
- `verified_import` of the fixture into the run-local source directories;
- source-package structural and fingerprint validation;
- Artifact Generation registration and the `source_ready` Workflow Checkpoint;
- initialization journal recovery through `reconcile-run`.

The Fixture Platform Adapter follows the production Platform Adapter interface and may not bypass Kernel operations. It supplies local probe metadata and importable source evidence. It performs no network request, cookie access, downloader invocation, Whisper transcription, or semantic judgment.

The slice exits only when automated tests prove all of these conditions:

- one offline fixture reaches a current `source_ready` checkpoint;
- the produced directory tree exactly matches the canonical Scaffold and every governed directory was created by the Kernel;
- every emitted JSON artifact validates against the registered schema version;
- repeated operations are idempotent or return a stable already-complete result;
- a path at the supported boundary succeeds and a path beyond the 240 UTF-16-unit budget fails before leaving a partial run;
- identity and output-path collisions fail closed;
- injected interruption at each initialization journal boundary reconciles to a complete old state or complete new state;
- imported source drift invalidates `source_ready`.

Live source download, cookie handling, Whisper fallback, Source Acquisition Agent judgment, production prompts, LaTeX, acceptance, repair, and Batch behavior remain later slices. No artifact produced by this tracer bullet has delivery authority.

## Consequences

The first milestone proves real filesystem, schema, state, fingerprint, and recovery behavior with deterministic inputs. Production adapters can later reuse an already tested boundary. The fixture must remain small, immutable, source-licensed for repository use, and rich enough to exercise subtitle, metadata, and media identity contracts.
