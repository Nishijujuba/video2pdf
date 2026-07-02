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
- Link architectural decisions with `related_adrs`.
- Use `#status/needs-triage`, `#status/needs-info`, `#status/ready-for-agent`, `#status/ready-for-human`, `#status/in-progress`, `#status/blocked`, `#status/in-review`, `#status/done`, and `#status/wontfix` as Graph View groups.
- When an issue changes state, update `status`, the matching `status/<state>` tag, the body `Status:` line, and the `updated` date in the same edit.

## When A Skill Says "Publish To The Issue Tracker"

Create a new issue file under `docs/issues/<feature-slug>/`, creating the directory if needed. If the work starts from a PRD, link the PRD in the `feature` frontmatter field.

## When A Skill Says "Fetch The Relevant Ticket"

Read the file at the referenced path. The user will normally pass the path or the issue number directly.
