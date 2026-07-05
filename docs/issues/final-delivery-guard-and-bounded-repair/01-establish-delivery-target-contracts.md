---
type: issue
status: done
feature: "[[prd/final-delivery-guard-and-bounded-repair]]"
depends_on: []
blocks:
  - "[[issues/final-delivery-guard-and-bounded-repair/02-implement-delivery-guard-cli]]"
  - "[[issues/final-delivery-guard-and-bounded-repair/04-add-bounded-old-pdf-repair-mode]]"
related_adrs:
  - "[[adr/0002-use-json-acceptance-contract-for-final-delivery-quality]]"
owner: unassigned
created: 2026-07-05
updated: 2026-07-05
tags:
  - issue
  - status/done
---

# 01 - Establish delivery target contracts

Status: done

## Goal

Create the project-level and video-output-level delivery target contract so every later guard, hook, and repair path resolves the same active delivery target inside the project boundary.

## What to build

Add the target-file contract and boundary resolver for Final Delivery Guard workflows. A completed slice should let tests create fixture target files, resolve the active video output directory, validate allowed stages, reject path escapes, and bind a final PDF, main TeX file, acceptance manifest, Acceptance Report, guard report, and attempt limit to one video output directory.

This slice should make target state explicit before any guard or repair behavior starts. It should also document the lifecycle states: `generating`, `ready_for_delivery`, `accepted`, `delivered`, and `blocked`.

## Context

This issue starts the implementation of [[prd/final-delivery-guard-and-bounded-repair]]. It extends the existing Final Delivery Acceptance Gate from [[prd/final-delivery-acceptance-gate]] and preserves the decision source defined by [[adr/0002-use-json-acceptance-contract-for-final-delivery-quality]].

The PRD defines the project-level target at `.codex/delivery-targets/current.json` and the video-output-level target at `review/acceptance/delivery_target.json`.

## Dependencies

- Depends on: none
- Blocks: [[issues/final-delivery-guard-and-bounded-repair/02-implement-delivery-guard-cli]], [[issues/final-delivery-guard-and-bounded-repair/04-add-bounded-old-pdf-repair-mode]]

## User Stories Covered

19, 29, 38, 39, 42, 43

## Expected Touched Paths

- `.agents/skills/final-delivery-acceptance/SKILL.md`
- `.agents/skills/final-delivery-acceptance/scripts/delivery_guard.py`
- `.codex/delivery-targets/current.json` fixture or documented generated path
- Tests or fixtures for target files under the established local test location

## Acceptance Tests

- A fixture `current.json` with `stage: "ready_for_delivery"` resolves to exactly one video output directory under `D:\Project\video2pdf\newskill-kimi`.
- A fixture video-output `delivery_target.json` binds `final_pdf`, `main_tex`, `allowed_artifacts_manifest`, `acceptance_report`, `delivery_guard_report`, and `attempt_limit`.
- The resolver accepts only the defined stage values.
- The resolver rejects absolute paths outside `D:\Project\video2pdf\newskill-kimi`.
- The resolver rejects relative paths that escape the project boundary through `..`.
- The resolver records clear blocking messages for missing target files, malformed JSON, missing required fields, invalid stage values, and path escape attempts.

## Delivery Blocking Behavior

- A malformed active target must block delivery once the workflow reaches `ready_for_delivery`, `accepted`, or `blocked`.
- A target outside the project boundary must block delivery.
- A missing target must allow ordinary discussion and unrelated work.

## Acceptance Criteria

- [x] The delivery target contract is documented in the final-delivery acceptance skill.
- [x] The resolver validates project-level and video-output-level target files.
- [x] Boundary checks prevent path escape from the current project.
- [x] The stage lifecycle is documented with the hook and render-skill meaning of each stage.
- [x] Fixture tests cover valid target resolution, invalid stages, malformed target files, missing fields, and path escape attempts.

## Execution Log

- 2026-07-05: Created from [[prd/final-delivery-guard-and-bounded-repair]].
- 2026-07-05: Implemented `delivery_guard.py` target resolver and fixture coverage in `test_delivery_guard.py`; verified with final-delivery-acceptance unittest discovery.

## Comments
