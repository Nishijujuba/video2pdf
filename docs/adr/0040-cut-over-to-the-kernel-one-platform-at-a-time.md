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

Before a platform is activated, Kernel development for that platform is limited to repository fixtures and explicitly identified manual pilots without delivery authority. A pilot cannot create an accepted or delivered target.

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
