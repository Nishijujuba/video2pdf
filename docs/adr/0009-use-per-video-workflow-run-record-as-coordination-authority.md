# Use a per-video workflow run record as coordination authority

The existing session target, task index, and video delivery target coordinate final delivery, session routing, and ownership. They do not describe source acquisition, outline, section, figure, Pyramid Gate, integration, or compile progress, so recovery before the delivery phase still depends on interpreting scattered files.

## Considered Options

- Derive workflow state from whichever files exist: rejected because partial writes, stale reports, and abandoned artifacts make file presence ambiguous.
- Expand the project task index into the full workflow record: rejected because it is a project-level ownership and observability projection shared across concurrent runs.
- Expand the delivery target into the full workflow record: rejected because its contract is intentionally bounded to final delivery and acceptance.

## Decision

Every new Video Workflow Run has one authoritative coordination record at `<video-output-dir>/workflow/run.json`. The record has a versioned schema and stable run identity, declares the platform adapter and artifact bindings, tracks workflow coordination state, and references checkpoint evidence with fingerprints. A transition succeeds only after the kernel validates the referenced files and their gate-specific reports.

The run record does not copy or override semantic decisions. Pyramid Gate JSON remains authoritative for pyramid continuation, `review/latex/compile_report.json` remains authoritative compile provenance, and `review/acceptance/acceptance_report.json` remains the only machine-readable final-acceptance decision source. The run record stores their paths, fingerprints, and coordination consequences.

Session-scoped `current.json` remains the Stop Hook routing projection, `task-index.json` remains the ownership and observability projection, and `review/acceptance/delivery_target.json` remains the bounded final-delivery contract. For Kernel Track runs, the Run Record is the delivery-stage commit marker and ADR 0055 coordinates all four files through one Delivery Lifecycle Mutation Intent. Their intent identity, run revision, and stage must agree whenever delivery lifecycle state exists.

## Consequences

Interrupted work can resume from one declared run identity without guessing from directory contents. Every kernel command must be idempotent against the run record and current artifacts. The checkpoint model, invalidation rules, atomic multi-file update order, and legacy-output import boundary require explicit follow-up decisions.
