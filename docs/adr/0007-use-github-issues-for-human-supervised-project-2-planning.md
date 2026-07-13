---
status: accepted
---

# Use GitHub Issues for human-supervised Project 2.0 planning

Project 2.0 specs and implementation tickets will live as GitHub Issues in `Nishijujuba/video2pdf`, grouped under the `2.0` milestone. The completed local PRDs, issue files, and generated dependency views remain in place as the read-only Project 1.0 Legacy Planning Archive. `CONTEXT.md` and `docs/adr/` remain active project documentation.

## Considered Options

- Continue the local Markdown tracker: rejected because Project 2.0 work needs GitHub collaboration, issue relationships, and shared tracker visibility.
- Allow agents to publish inferred specs and tickets autonomously: rejected because planning decisions require human supervision.
- Add a mechanical fingerprint or CI freeze gate for the local archive: rejected because the repository uses human-supervised spec creation and ticket decomposition, and the agent instructions provide the required boundary for this workflow.

## Decision

Every Project 2.0 spec and ticket passes the Human Publication Gate before publication. `/to-spec` presents its testing seams and complete spec draft before creating a GitHub issue. `/to-tickets` presents its vertical slices, acceptance criteria, and blocking edges before creating issues, sub-issue relationships, or dependency relationships. Material changes after approval require renewed approval.

Approved specs and tickets use the canonical triage labels. New Project 2.0 specs default to the `2.0` milestone, and derived tickets inherit the parent spec's milestone. Pull requests are outside the request-triage surface.

## Consequences

The GitHub tracker becomes the authoritative planning surface for Project 2.0. No parallel local PRD or ticket is created for new work. The Project 1.0 archive remains available as prior art and historical evidence, while its files and generated views receive no new planning items, status changes, or regeneration.
