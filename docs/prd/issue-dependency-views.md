# PRD: Issue Dependency Views

## Problem Statement

The local Markdown issue tracker already records Feature Issue Sets under `docs/issues/<feature-slug>/`. Each issue file carries status, feature, dependency, blocker, ADR, and Obsidian link metadata. Obsidian Graph View can show these documents as a graph, but it mixes execution dependencies with PRD links, ADR links, status tags, and general context references.

That mixed graph makes issue batches hard to execute. The user needs a dependency-focused view that answers these execution questions directly:

- Which issue can be picked up next?
- Which issue is waiting for which upstream issue?
- Which issue is blocked by its own status?
- Which issue batch is complete, active, or still waiting?

The view must stay grounded in tracker metadata, remain reviewable in Git, fit the existing Obsidian vault under `docs/`, and avoid turning a manually arranged graph into a second dependency source.

## Solution

Create generated Issue Dependency Views for the local Markdown tracker. Issue files remain the authoritative data source, and a generator produces Markdown dependency views under `docs/issues/_views/`.

The generator treats each `docs/issues/<feature-slug>/` directory as one Feature Issue Set. It reads issue files, builds execution dependency edges from `depends_on`, verifies that `blocks` matches the inverse relationship, calculates current execution state, and writes:

- `docs/issues/_views/<feature-slug>-dependencies.md`
- `docs/issues/_views/index.md`

The first implementation scope is Markdown only. Each single-feature page contains header metadata, consistency errors, next executable issues, waiting-on-dependencies issues, and a Mermaid dependency graph. Canvas output is a later extension.

The execution-order source of truth is `depends_on`. If issue B lists issue A in `depends_on`, issue A must be complete before issue B can be considered currently executable. The `blocks` field remains useful for human browsing and Obsidian graph exploration, and generator validation treats it as a redundant reverse index.

Node color represents only current issue status from frontmatter. Dependency meaning is carried by edges, layers, and dependency lists. Generated views are current at generation time, and validation mode detects stale views with a source issue fingerprint.

## User Stories

1. As a project owner, I want each issue batch to have a dependency-only view, so that execution order is visible without Graph View noise.
2. As a project owner, I want one index for all Feature Issue Sets, so that current issue batches can be scanned from one Obsidian entry point.
3. As a project owner, I want the index to show issue counts, so that each batch's size is visible before opening the detailed view.
4. As a project owner, I want the index to show status distribution, so that completed, ready, active, blocked, and review work are visible at batch level.
5. As a project owner, I want the index to show root issues, so that the starting points of each batch are clear.
6. As a project owner, I want the index to show currently executable issues, so that the next available work is visible immediately.
7. As a project owner, I want the index to separate status-blocked and dependency-blocked issues, so that different blocker causes stay distinguishable.
8. As an implementation agent, I want a `Next executable` section on each single-feature page, so that the correct issue can be chosen without manually tracing the graph.
9. As an implementation agent, I want currently executable issues sorted by issue number, so that the order is stable and easy to compare with file names.
10. As an implementation agent, I want a `Waiting on dependencies` section, so that future work explains which upstream issue must finish first.
11. As an implementation agent, I want dependency-blocked issues to name their incomplete upstream dependencies, so that handoff notes can point at concrete issue files.
12. As a reviewer, I want execution dependency edges to come only from `depends_on`, so that generated views have one authoritative direction.
13. As a reviewer, I want `blocks` checked as inverse metadata, so that stale reverse links are caught during validation.
14. As a reviewer, I want consistency errors shown in generated pages, so that metadata problems are visible during Obsidian review.
15. As a reviewer, I want validation mode to fail on consistency errors, so that automation cannot treat an invalid graph as trusted evidence.
16. As an Obsidian user, I want Mermaid Markdown as the primary view, so that dependency diagrams are readable in Obsidian and diffable in Git.
17. As an Obsidian user, I want Canvas generation deferred, so that first-version behavior stays centered on stable Markdown artifacts.
18. As an Obsidian user, I want dependency graphs to use left-to-right layout by dependency layer, so that the execution sequence reads naturally.
19. As an Obsidian user, I want node labels to preserve issue numbers, so that graph nodes remain traceable to issue files.
20. As an Obsidian user, I want node color to show current issue status, so that the graph is visually useful for execution state.
21. As an Obsidian user, I want a stable status color palette, so that color meaning stays consistent across batches.
22. As a maintainer, I want generated files to include generation time, source feature slug, source issue count, and source issue fingerprint, so that view freshness is auditable.
23. As a maintainer, I want the source fingerprint to cover only view-relevant metadata, so that comments and execution logs do not cause meaningless stale-view failures.
24. As a maintainer, I want validation mode to detect stale views, so that changed issue metadata triggers regeneration.
25. As a maintainer, I want default all-feature generation, so that the global index stays complete.
26. As a maintainer, I want single-feature generation, so that active editing can refresh one batch quickly.
27. As a maintainer, I want validation to detect missing issue links, so that broken dependency references are fixed early.
28. As a maintainer, I want validation to detect circular dependencies, so that execution order remains well-defined.
29. As a maintainer, I want validation to detect unknown statuses, so that node colors and executable-state logic stay deterministic.
30. As a future agent, I want tracker documentation to define these rules, so that new issue batches follow the same dependency-view contract.

## Implementation Decisions

- A Feature Issue Set is one issue directory created from one feature-level planning unit.
- The generator will read each Feature Issue Set independently.
- The default generator run will process every Feature Issue Set.
- The generator will also support a single-feature option for active editing.
- Markdown Mermaid is the first-version dependency-view artifact.
- Canvas output is a later extension.
- The generated index links to every single-feature dependency view.
- The generated index summarizes issue count, status distribution, root issues, currently executable issues, status-blocked issues, and dependency-blocked issues.
- The single-feature view includes header metadata, consistency errors, next executable issues, waiting-on-dependencies issues, and a Mermaid dependency graph.
- Execution dependency edges are derived from `depends_on`.
- The `blocks` field is a reverse index and must match the inverse of `depends_on`.
- Currently executable issues are issues with status `ready-for-agent` or `ready-for-human` whose dependencies are all `done`.
- Status-blocked issues are issues whose own status is `blocked`.
- Dependency-blocked issues are otherwise executable issues that still depend on at least one issue that is not `done`.
- Mermaid graphs use left-to-right layout by dependency layer.
- Root issues occupy the first dependency layer.
- Dependent issues occupy later dependency layers.
- Node labels retain the original issue number.
- Node color represents only frontmatter `status`.
- Dependency-blocked state appears in the index or compact annotations, while edges and layers communicate dependency structure.
- The fixed status palette is shared by generated Mermaid views and any future Canvas views.
- Generated Markdown views include `generated_at`, `source_feature_slug`, `source_issue_count`, and `source_issue_fingerprint`.
- The source issue fingerprint covers issue relative path, title, `status`, `feature`, `depends_on`, `blocks`, and `related_adrs`.
- Issue body prose, execution logs, comments, and unrelated content are outside the fingerprint.
- Validation mode recomputes fingerprints and fails on stale views.
- Validation mode fails on missing issue links, inverse-edge mismatches, circular dependencies, unknown statuses, and stale generated views.
- Validation mode exits non-zero when consistency errors exist.
- Tracker documentation and the domain glossary define the terms used by the generator.

## Testing Decisions

- The highest-value test seam is the generator command-line interface. Tests should exercise observable behavior through input fixture issue directories, generated Markdown files, validation exit codes, and reported consistency errors.
- Tests should treat generated Markdown as the external contract. Assertions should verify required sections, header metadata, Mermaid edges, node classes, and index summaries.
- Metadata parsing tests should cover frontmatter fields, issue titles, empty dependency lists, multiple dependencies, related ADRs, and body content that must not affect fingerprints.
- Fingerprint tests should prove that status and dependency metadata changes alter the fingerprint.
- Fingerprint tests should prove that execution log or comment changes do not alter the fingerprint.
- Executable-state tests should cover ready issues with no dependencies, ready issues with completed dependencies, ready issues with incomplete dependencies, human-ready issues, in-progress issues, in-review issues, done issues, blocked issues, and wontfix issues.
- Dependency-blocked tests should verify that otherwise executable downstream issues list their incomplete upstream dependencies.
- Status-blocked tests should verify that `status: blocked` issues are separate from dependency-blocked issues.
- Inverse-edge validation tests should cover missing reverse `blocks` entries and stale reverse `blocks` entries.
- Missing-link validation tests should cover dependency links that do not resolve to issue files in the same Feature Issue Set.
- Cycle validation tests should cover simple two-node cycles and longer cycles.
- Unknown-status tests should cover invalid frontmatter status values.
- Stale-view tests should generate a view, modify view-relevant metadata, and confirm validation fails until regeneration.
- Index tests should cover multiple Feature Issue Sets, completed batches, active batches, and batches with consistency errors.
- Mermaid tests should cover left-to-right graph direction, dependency edges, node class assignment, and stable issue-number labels.

## Out of Scope

- Obsidian Canvas generation.
- Background file watching or automatic live refresh.
- External issue tracker integration.
- Changing the meaning of existing issue statuses.
- Replacing Obsidian Graph View for general document exploration.
- Editing issue files from the generator.
- Inferring dependencies from prose links, PRD links, ADR links, or tags.
- Using issue body text to calculate dependency-view freshness.
- Creating new issue batches from PRDs.
- Implementing a GUI inside Obsidian.

## Further Notes

The important design boundary is source-of-truth ownership. Issue files remain the source of truth; generated views are cached projections. When metadata changes, the generator refreshes the projection and validation mode proves whether the projection is still fresh.

The main usability risk is overloading visual color. Color must continue to mean current issue status only. Dependency meaning should come from edges, dependency layers, and the explicit lists above the graph.

The main maintenance risk is allowing two dependency directions to drift. The generator should read `depends_on` for execution order and treat `blocks` as a consistency check.
