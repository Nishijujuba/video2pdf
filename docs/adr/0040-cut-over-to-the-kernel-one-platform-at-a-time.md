# Cut over to the Kernel one platform at a time

The current executable workflow still uses Acceptance Report v1, one combined Acceptance Reviewer, Batch-owned item status, free-form child prompts, agent-created output directories, and recursive compile staging. Activating a partially implemented Kernel alongside those writers would create two coordination authorities inside one run. Switching Bilibili, YouTube, and Batch together would also combine unrelated platform and scheduling risks.

## Considered Options

- Replace both platforms and Batch in one release: rejected because the failure surface and rollback scope would be unnecessarily large.
- Keep long-term dual writes between legacy scripts and the Kernel: rejected because recovery cannot determine which state authority owns a conflicting transition.
- Select the track separately for every invocation: rejected because an operator flag can create inconsistent runs and untestable combinations.
- Perform one atomic Platform Kernel Cutover at a time: selected because each activation has a bounded contract and observable proof period.

## Decision

The implementation and activation order is:

1. Bilibili single-video runs;
2. YouTube single-video runs;
3. Batch supervision over independent Kernel Track runs.

Before a platform is activated, ordinary Kernel Run admission for that platform remains closed. Repository fixtures carry no delivery authority. A cold-start cutover may bind exactly one production candidate through the public two-stage seam: `platform-kernel-prepare` records the implementation commit, probe identity, Run identity, source identity, and session without publishing platform authority; `init-cutover-candidate` initializes only that binding. No ordinary `init-run` is admitted in this state.

After `init-cutover-candidate`, run the public `source-acquire` command against the candidate's existing `--run-dir`; it attaches source evidence to that same Run and must not create a second Run.

When no usable CC subtitle exists, `source-acquire` must stage Whisper output through the Kernel-issued Whisper Task/Attempt and promote the validated Attempt before `source_ready` becomes current.

The candidate workflow must never call `source-live-smoke`; no second Run may be created for source acquisition.

An expired or rejected Cookie is a recoverable `user_input` Source Blocker: preserve the same Run and its evidence, do not count it as a delivery attempt failure, and immediately request a refreshed Cookie from the user.

After receiving the refreshed Cookie, close the source circuit breaker, run `source-blocker-resolve`, and retry `source-acquire` on the same Run with a new `source_epoch`.

The Cookie path and Cookie contents are credential-bearing secrets and must never appear in logs, reports, shared evidence, or task prompts.

If acquisition is interrupted after terminal proof persistence and before Resource Lease release, run `source-acquire-reconcile --run-dir <candidate-run-dir>`.

`source-acquire-reconcile` reloads the persisted terminal proof, releases the existing Lease, and advances or retries the interrupted Task on the same Run; it must not initialize or attach another Run.

The candidate must reach `ready_for_delivery` through the normal Kernel lifecycle with a provider-current passing Acceptance Report v2. `platform-kernel-candidate-activate` may then publish a `PROVISIONAL` candidate-only state. That state permits only the bound candidate to advance to `accepted`, obtain a fresh current Delivery Guard, and use that Guard to advance to `delivered` so the guarded delivery can support the Exit Evidence Manifest. It does not transfer platform authority, admit a second candidate, or classify the platform as `active_kernel`.

After the delivered candidate's evidence is collected, formally validated, and published, `platform-kernel-activate` must bind that exact Run and produce `CONFIRMED`. Only `CONFIRMED` transfers ordinary new-run authority and opens the platform's regular `init-run` path. Reconciliation preserves the same fail-closed distinction among `PREPARED`, `INITIALIZED`, `PROVISIONAL`, and `CONFIRMED`.

The normative sequence is `PREPARED` -> `INITIALIZED` -> `source_ready` -> `ready_for_delivery` with a provider-current passing Acceptance Report v2 -> `PROVISIONAL` -> `accepted` -> fresh current Delivery Guard -> `delivered` -> published Slice 12 Exit Evidence -> `CONFIRMED`. `PREPARED`, `INITIALIZED`, and `PROVISIONAL` remain non-active candidate states throughout this sequence.

After activation, every new run for that platform is a Kernel Track run. Existing output directories remain on the Legacy Track. One run cannot switch tracks, combine state writers, receive dual status updates, or gain a synthesized `workflow/run.json` through ordinary reconciliation.

A Platform Kernel Cutover is one atomic repository change that updates all affected executable and instructional surfaces together:

- root Kernel package, CLI, registered schemas, prompts, and configuration;
- platform and shared skills under both `.agents/` and `.claude/`;
- `AGENTS.md` and `CLAUDE.md` shared workflow instructions;
- Gate Provider adapters, validators, Delivery Guard integration, and Workflow Verification Seam tests;
- verification that the already active Global Gate contracts accept Kernel Track provenance for this platform.

Acceptance Report v2 activates earlier through the Global Gate Cutover in ADR 0051. A Platform Kernel Cutover does not reactivate or fork the report schema. It proves that the platform's Run Record, Artifact Generations, final evidence, and delivery lifecycle integrate with the one already active v2 provider and Guard.

Compile Manifest activation and any other writer-authority transfer also update their provider, skills, project instructions, and tests atomically. The cutover check fails closed when a required mirrored file or executable contract remains on the prior policy.

Historical workspace migration remains deferred. Legacy directories stay readable and retain their evidence. A later explicit migration design may adopt selected artifacts, but ordinary startup, resume, reconcile, and Batch recovery never upgrade them automatically.

## Consequences

Bilibili becomes the first production proof of the Kernel. YouTube follows only after the Bilibili cutover Exit Evidence Manifest and one real guarded delivery pass. Batch begins only after both single-video paths expose the same Kernel Interface. The project must maintain an executable policy check that detects stale mirrored skills and partial cutover groups before activation.
