# Use two-phase bootstrap and run initialization

The required Video Output Directory name depends on the original platform title and the task start timestamp. The title is unavailable until a platform metadata probe succeeds, while Cookie localization and probe diagnostics already require a short, writable location. Creating the final directory before the probe would require guessing its identity or renaming a populated directory later.

## Considered Options

- Name the final directory from the URL or platform ID: rejected because the project contract requires the normalized original video title plus the task start timestamp.
- Create a provisional Video Output Directory and rename it after acquisition: rejected because downstream paths, running tools, and recorded artifact bindings may already reference the provisional name.
- Probe directly from external Cookie and cache locations without a local bootstrap boundary: rejected because Cookie files may be updated and sandbox-safe diagnostic evidence needs a project-local location.

## Decision

Every new run starts with `bootstrap-probe`. The kernel freezes the local task start timestamp, assigns a short run identity, and places localized Cookie material, metadata output, and probe diagnostics under `<project-root>/待删除/pipeline-bootstrap/<run-id>/`. The probe is limited to platform identity, original title, duration, chapters, subtitle availability, and media-format availability.

After a successful probe, `init-run` calculates the normalized and path-budgeted final directory name from the original title and frozen timestamp. It creates the Video Output Directory and initial workflow contracts, then moves bootstrap evidence into `<video-output-dir>/待删除/bootstrap/`. Full source acquisition begins only after this transition.

For the one cold-start cutover candidate, `init-cutover-candidate` performs this initialization against the durable `PREPARED` binding. After `init-cutover-candidate`, run the public `source-acquire` command against the candidate's existing `--run-dir`; it attaches source evidence to that same Run and must not create a second Run.

When no usable CC subtitle exists, `source-acquire` must stage Whisper output through the Kernel-issued Whisper Task/Attempt and promote the validated Attempt before `source_ready` becomes current.

The candidate workflow must never call `source-live-smoke`; no second Run may be created for source acquisition.

An expired or rejected Cookie is a recoverable `user_input` Source Blocker: preserve the same Run and its evidence, do not count it as a delivery attempt failure, and immediately request a refreshed Cookie from the user.

After receiving the refreshed Cookie, close the source circuit breaker, run `source-blocker-resolve`, and retry `source-acquire` on the same Run with a new `source_epoch`.

The Cookie path and Cookie contents are credential-bearing secrets and must never appear in logs, reports, shared evidence, or task prompts.

If acquisition is interrupted after terminal proof persistence and before Resource Lease release, run `source-acquire-reconcile --run-dir <candidate-run-dir>`.

`source-acquire-reconcile` reloads the persisted terminal proof, releases the existing Lease, and advances or retries the interrupted Task on the same Run; it must not initialize or attach another Run.

The candidate activation sequence is `PREPARED` -> `INITIALIZED` -> `source_ready` -> `ready_for_delivery` with a provider-current passing Acceptance Report v2 -> `PROVISIONAL` -> `accepted` -> fresh current Delivery Guard -> `delivered` -> published Slice 12 Exit Evidence -> `CONFIRMED`.

No bootstrap evidence is permanently deleted. A failed or interrupted probe remains under the project `待删除` boundary for audit and manual cleanup. A populated Video Output Directory is not renamed as part of normal initialization.

## Consequences

The final directory has a stable identity before long-running downloads and agent work begin. Path budgeting can run before the deepest directory tree is created. The bootstrap record, retry identity rules, initialization scaffold, path-budget algorithm, and collision policy require follow-up decisions.
