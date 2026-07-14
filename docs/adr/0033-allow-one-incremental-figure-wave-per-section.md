# Allow one incremental figure wave per section

Outline planning can identify figures required by the global teaching structure, while detailed writing can reveal local explanations that need an additional diagram, comparison, or inspected source frame. Waiting for all writing before starting figures wastes parallelism. Automatically accepting an unlimited chain of writer and figure requests would create an unbounded agent loop.

## Considered Options

- Generate every figure only after all Writer tasks finish: rejected because required outline figures cannot run concurrently with writing.
- Allow only outline-declared figures: rejected because later explanation gaps would be forced into prose or deferred to final repair.
- Let Writer and Figure agents recursively request more waves: rejected because termination, budget, and checkpoint readiness would become unpredictable.
- Run an outline-required wave plus one section-scoped incremental wave: selected because it combines planned concurrency with bounded adaptation.

## Decision

The accepted Outline Contract declares stable `required_figure_slots` for each section. The Required Figure Wave starts after Outline Pyramid and Section Scaffold creation and may run concurrently with the corresponding Writer task.

A Writer Agent may return `new_figure_candidates` in its task output. Every New Figure Candidate includes a kernel-valid candidate identity, canonical section identity, teaching purpose, intended placement, source timestamp or other evidence, proposed figure type, reason prose alone is insufficient, and `required` or `optional` priority. A candidate is a request and cannot create its own Figure Slot or write an asset.

After one section's Writer attempt passes its completion contract, `production-advance` validates candidate evidence, exact duplicates, section ownership, and the run's configured incremental-figure budget. It assigns kernel-issued Figure Slots to admitted candidates and may immediately launch that section's Incremental Figure Wave while other sections continue writing. There is no all-section barrier.

The section waits for every admitted required figure and any admitted optional figure before deterministic integration and Section Pyramid. When no candidate is admitted, integration proceeds as soon as the Writer and Required Figure Wave inputs are ready. Optional candidates beyond the configured budget remain recorded in the Artifact Plan as unproduced proposals.

Each section may have at most one Incremental Figure Wave. Writer, Figure, and integration tasks cannot request a third production wave. An admitted required candidate that cannot be produced blocks the section with explicit evidence; it cannot disappear silently. Figure needs discovered after production integration enter the applicable review or Repair Plan and do not reopen production-wave recursion.

## Consequences

Planned figures gain maximum concurrency and section-specific teaching gaps still receive visual treatment. Sections advance independently without a global Writer barrier. Candidate schemas, duplicate detection, budget configuration, and required-figure failure tests become part of the production Module. The one-wave limit guarantees termination.
