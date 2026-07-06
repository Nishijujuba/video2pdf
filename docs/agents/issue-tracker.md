# Issue Tracker: Local Markdown

Issues and PRDs for this repo live as markdown files under `docs/`, which is the local Obsidian vault root for work tracking.

## Repository Knowledge Root

- Obsidian vault root: `docs/`
- PRDs live under `docs/prd/`
- ADRs live under `docs/adr/`
- Implementation issues live under `docs/issues/<feature-slug>/`
- Agent configuration lives under `docs/agents/`

Keeping issues, PRDs, and ADRs under the same `docs/` root lets Obsidian Graph View show the execution graph as links between work items, requirements, and decisions.

## File Conventions

- One feature per issue directory: `docs/issues/<feature-slug>/`
- The feature PRD is a markdown file under `docs/prd/<prd-slug>.md`
- Implementation issues are `docs/issues/<feature-slug>/<NN>-<slug>.md`, numbered from `01`
- Each issue must link to its PRD with an Obsidian internal link, such as `[[prd/pyramid-principle-codex-exec-gate]]`
- Dependencies, blockers, related ADRs, and follow-up issues must use Obsidian internal links so Graph View can draw edges
- Execution dependency order is authoritative in `depends_on`; `blocks` is a redundant reverse index and should be checked against the inverse of `depends_on`
- Comments and conversation history append to the bottom of the file under a `## Comments` heading

## Issue Status

Each issue records its status in three synchronized places:

1. `status` in YAML frontmatter is the canonical machine-readable state.
2. `tags` includes `issue` and one matching `status/<state>` tag for Obsidian Graph View filtering and grouping.
3. `Status:` near the top of the body preserves compatibility with skills and agents that read the older plain-text convention.

Allowed `status` values are:

- `needs-triage`
- `needs-info`
- `ready-for-agent`
- `ready-for-human`
- `in-progress`
- `blocked`
- `in-review`
- `done`
- `wontfix`

The first four values match the triage states defined in `docs/agents/triage-labels.md`. The execution-only values mean:

- `in-progress`: an agent or human is actively working the issue.
- `blocked`: work has started, but cannot continue because of a dependency, missing tool, failed verification, or unresolved decision.
- `in-review`: implementation appears complete, but independent review or final verification is still pending.
- `done`: implementation, documentation, and verification are complete enough to close the issue.

The `status` field and `status/<state>` tag must agree. For example, an issue with `status: in-progress` must include `status/in-progress` in `tags`.

## Issue Template

```markdown
---
type: issue
status: ready-for-agent
feature: "[[prd/<prd-slug>]]"
depends_on: []
blocks: []
related_adrs: []
owner: unassigned
created: YYYY-MM-DD
updated: YYYY-MM-DD
tags:
  - issue
  - status/ready-for-agent
---

# NN - Issue title

Status: ready-for-agent

## Goal

State the outcome this issue must produce.

## Context

Link the relevant PRD, ADRs, source files, or earlier issues.

## Dependencies

- Depends on: none
- Blocks: none

## Acceptance Criteria

- [ ] The expected behavior or artifact is produced.
- [ ] Relevant documentation or tests are updated.
- [ ] Verification evidence is recorded.

## Execution Log

- YYYY-MM-DD: Created.

## Comments
```

## Graph View Rules

- Link every issue to its PRD through the `feature` field and, when helpful, again in the `## Context` section.
- Link dependency edges explicitly with `depends_on` and `blocks`.
- Treat `depends_on` as the source of truth for generated dependency views.
- Treat `blocks` as a human-readable reverse index. If `blocks` disagrees with the inverse of `depends_on`, fix the issue metadata before trusting the dependency view.
- Link architectural decisions with `related_adrs`.
- Use `#status/needs-triage`, `#status/needs-info`, `#status/ready-for-agent`, `#status/ready-for-human`, `#status/in-progress`, `#status/blocked`, `#status/in-review`, `#status/done`, and `#status/wontfix` as Graph View groups.
- When an issue changes state, update `status`, the matching `status/<state>` tag, the body `Status:` line, and the `updated` date in the same edit.

## Dependency View Rules

Obsidian Graph View shows all wiki links, so it is useful for document context but too noisy for issue execution order. Use a generated dependency view when the question is which issue must happen before another issue.

- Generate dependency views from the project root with `python scripts/generate_issue_dependency_views.py`.
- Refresh one Feature Issue Set with `python scripts/generate_issue_dependency_views.py --feature <feature-slug>`.
- Validate dependency consistency and generated-view freshness without writes with `python scripts/generate_issue_dependency_views.py --check`; `--validate` is an alias.
- The script must support a generation mode that writes `docs/issues/_views/` artifacts and a validation mode that checks consistency without modifying files.
- By default, the script processes every `docs/issues/<feature-slug>/` directory.
- The script should also support a single-feature option for refreshing or validating one Feature Issue Set while it is being edited.
- Validation must fail for missing issue links, `depends_on` and `blocks` inverse mismatches, circular dependencies, unknown statuses, and stale generated views.
- Validation mode must exit non-zero when consistency errors exist.
- Every generated Markdown view must include header metadata with `generated_at`, `source_feature_slug`, `source_issue_count`, and `source_issue_fingerprint`.
- Validation mode must recompute the source issue fingerprint from current issue metadata and fail when it differs from the fingerprint recorded in the generated view.
- The source issue fingerprint covers only dependency-view inputs: issue relative path, title, `status`, `feature`, `depends_on`, `blocks`, and `related_adrs`.
- Issue body prose, execution logs, comments, and unrelated content are outside the dependency-view fingerprint.
- A dependency view is scoped to one Feature Issue Set, meaning one `docs/issues/<feature-slug>/` directory.
- The dependency view reads only issue files in that directory.
- The dependency view draws execution edges from `depends_on`.
- The Mermaid graph uses left-to-right layout by dependency layer. Root issues appear in the first layer, and dependent issues appear in later layers.
- Node labels must keep the original issue number so the graph remains traceable to the issue files.
- Node colors must represent the current issue `status` from frontmatter. Color must not carry dependency meaning.
- Dependency-blocked state should appear in the dependency index or a compact node annotation, while execution dependency remains visible through edges and layers.
- Generated node colors are current at generation time. Validation mode must detect stale generated views after issue metadata changes.
- Use this fixed status color palette in generated Mermaid and Canvas dependency views:
  - `done`: green, suggested Mermaid fill `#2ea043`
  - `ready-for-agent`: blue, suggested Mermaid fill `#0969da`
  - `ready-for-human`: purple, suggested Mermaid fill `#8250df`
  - `in-progress`: yellow, suggested Mermaid fill `#d4a72c`
  - `blocked`: red, suggested Mermaid fill `#cf222e`
  - `in-review`: orange, suggested Mermaid fill `#bc4c00`
  - `needs-info`: gray, suggested Mermaid fill `#8c959f`
  - `needs-triage`: light gray, suggested Mermaid fill `#d0d7de`
  - `wontfix`: dark gray, suggested Mermaid fill `#57606a`
- The dependency index treats an issue as currently executable only when its status is `ready-for-agent` or `ready-for-human`, and all issues listed in `depends_on` have status `done`.
- Issues with status `in-progress`, `blocked`, `in-review`, `done`, or `wontfix` are not currently executable, though they remain visible in the dependency view.
- The dependency index must separate status-blocked issues from dependency-blocked issues. A status-blocked issue has `status: blocked`; a dependency-blocked issue has an otherwise executable status while one or more `depends_on` entries are not yet `done`.
- PRDs, ADRs, status tags, and general context links may be shown as metadata or surrounding context, but they should not create execution edges.
- The primary generated view is `docs/issues/_views/<feature-slug>-dependencies.md`, using a Mermaid graph inside Markdown.
- A generated Obsidian Canvas view may also be stored as `docs/issues/_views/<feature-slug>.canvas` when spatial layout helps review the batch.
- The Markdown Mermaid view is the reviewable dependency-view artifact. The Canvas view is a secondary browsing artifact and must be regenerated from the same issue metadata.
- The generated index is `docs/issues/_views/index.md`. It links to every Feature Issue Set dependency view and summarizes issue count, status distribution, root issues, currently executable issues, and blocked issues.
- The first implementation scope is Markdown only: generate one Mermaid dependency view per Feature Issue Set plus `docs/issues/_views/index.md`. Canvas generation is a later extension.
- Generated views should include a `Consistency errors` section. The section should say `None` when no consistency errors exist and list actionable errors when dependency metadata or view freshness is invalid.
- Each single-feature dependency view must include a `Next executable` section above the Mermaid graph. It lists currently executable issues from that Feature Issue Set, sorted by issue number.
- Each single-feature dependency view must include a `Waiting on dependencies` section above the Mermaid graph. It lists dependency-blocked issues and the upstream issues they are waiting for, sorted by issue number.

## When A Skill Says "Publish To The Issue Tracker"

Create a new issue file under `docs/issues/<feature-slug>/`, creating the directory if needed. If the work starts from a PRD, link the PRD in the `feature` frontmatter field.

## When A Skill Says "Fetch The Relevant Ticket"

Read the file at the referenced path. The user will normally pass the path or the issue number directly.
