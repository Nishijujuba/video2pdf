# Issue Tracker: GitHub

Project 2.0 specs and implementation tickets live as GitHub Issues in `Nishijujuba/video2pdf`. Use the `gh` CLI for tracker operations.

All spec and ticket publication is human-supervised.

## Planning Artifact Language

All new Project 2.0 Specs and Implementation Tickets must be written in English.

Use ASCII punctuation in GitHub publication payloads when practical.
Existing Project 1.0 planning artifacts remain unchanged.

## Tracker Boundary

- Existing files under `docs/prd/` and `docs/issues/` form the read-only Project 1.0 Legacy Planning Archive.
- Agents may read the archive as historical evidence and prior art.
- Do not add, edit, rename, regenerate, or update status in the archive.
- Project 2.0 specs and tickets must be created as GitHub Issues.
- Pull requests are not a triage or feature-request surface.

## Human Publication Gate

Agents may inspect the repository, analyze requirements, and prepare drafts before approval.

Publishing requires explicit human approval of the exact material being published:

- `/to-spec` must show the proposed testing seams and complete spec draft before `gh issue create`.
- `/to-tickets` must show the proposed ticket titles, vertical slices, acceptance criteria, and blocking edges before creating issues or relationships.
- Creating or editing an issue, assigning a milestone, creating a sub-issue relationship, and creating a dependency edge are publication operations.
- Material changes after approval require renewed approval.
- Agents must not create speculative, background, or inferred specs and tickets without explicit invocation and approval.

## Project 2.0 Milestone

- New Project 2.0 specs use the `2.0` milestone by default.
- Tickets derived from a spec inherit the parent spec's milestone.
- Triage labels describe readiness or disposition.
- Milestones describe project-version membership.

## Spec Workflow

When `/to-spec` publishes an approved spec:

1. Create one GitHub issue containing the complete spec.
2. Apply the `ready-for-agent` label.
3. Apply the `2.0` milestone unless the human approver selected another milestone.
4. Treat the resulting GitHub issue as the authoritative spec.
5. Do not create a parallel local PRD.

## Ticket Workflow

When `/to-tickets` publishes an approved breakdown:

1. Create one GitHub issue per approved vertical slice in dependency order.
2. Reference the parent spec in every ticket.
3. Link tickets to the spec as GitHub sub-issues when supported.
4. Apply the `ready-for-agent` label.
5. Inherit the parent spec's milestone.
6. Record blocking edges with GitHub native issue dependencies when supported.
7. When native dependencies are unavailable, add a `Blocked by: #<issue>` section to the ticket body.
8. Do not close or rewrite the parent spec.

## Core Commands

- Create: `gh issue create`
- Read: `gh issue view <number> --comments`
- List: `gh issue list`
- Comment: `gh issue comment <number>`
- Label or milestone: `gh issue edit <number>`
- Close: `gh issue close <number>`

Infer the repository from the configured Git remote when commands run inside this checkout.

## Native Relationships

GitHub sub-issues represent containment: a spec contains its implementation tickets.

GitHub dependencies represent execution order: a blocked ticket cannot enter the work frontier until every blocker is closed.

When native relationships are unavailable, preserve both relationships explicitly in issue bodies.
