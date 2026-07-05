---
type: issue
status: done
feature: "[[prd/final-delivery-guard-and-bounded-repair]]"
depends_on:
  - "[[issues/final-delivery-guard-and-bounded-repair/03-enforce-delivery-guard-with-stop-hook]]"
  - "[[issues/final-delivery-guard-and-bounded-repair/05-integrate-guard-and-repair-into-render-skills]]"
blocks: []
related_adrs:
  - "[[adr/0002-use-json-acceptance-contract-for-final-delivery-quality]]"
owner: unassigned
created: 2026-07-05
updated: 2026-07-05
tags:
  - issue
  - status/done
---

# 06 - Add end to end guard fixture tests and doc sync

Status: done

## Goal

Add final fixture-based verification that proves the target lifecycle, Stop hook, guard CLI, render-skill workflow, bounded repair loop, and project instructions agree.

## What to build

Create the end-to-end mechanical verification layer for [[prd/final-delivery-guard-and-bounded-repair]]. A completed slice should use small fixture PDFs, fixture manifests, fixture rendered pages, and fixture Acceptance Reports to exercise the project-local delivery lifecycle without real Bilibili downloads, YouTube downloads, model calls, or long-running LaTeX builds.

The tests should verify that the same contract is visible in `final-delivery-acceptance`, `bilibili-render-pdf`, `youtube-render-pdf`, `AGENTS.md`, `CLAUDE.md`, and `.codex` hook configuration.

## Context

This issue closes the delivery guard feature after the hook from [[issues/final-delivery-guard-and-bounded-repair/03-enforce-delivery-guard-with-stop-hook]] and render-skill integration from [[issues/final-delivery-guard-and-bounded-repair/05-integrate-guard-and-repair-into-render-skills]] are in place.

## Dependencies

- Depends on: [[issues/final-delivery-guard-and-bounded-repair/03-enforce-delivery-guard-with-stop-hook]], [[issues/final-delivery-guard-and-bounded-repair/05-integrate-guard-and-repair-into-render-skills]]
- Blocks: none

## User Stories Covered

24, 25, 26, 27, 28, 40, 41, 43, 44, 45

## Expected Touched Paths

- `.agents/skills/final-delivery-acceptance/SKILL.md`
- `.agents/skills/bilibili-render-pdf/SKILL.md`
- `.agents/skills/youtube-render-pdf/SKILL.md`
- `.codex/` project hook configuration
- `AGENTS.md`
- `CLAUDE.md`
- Fixture PDFs, manifests, reports, rendered pages, and hook-test fixtures under the established local test location

## Acceptance Tests

- A fixture successful workflow reaches a fresh passing `delivery_guard_report.json` and clears project-level delivered state.
- A fixture failed-then-repaired workflow preserves failed attempt evidence, refreshes artifacts, reruns acceptance from final artifacts only, and then passes the guard.
- A fixture three-failure workflow writes `manual_repair_brief.md`, sets target state to `blocked`, and makes the Stop hook block final delivery.
- A fixture stale-report workflow fails because artifact fingerprints changed after acceptance.
- A fixture missing-page workflow fails because rendered-page evidence no longer covers every PDF page.
- Contract tests verify that `AGENTS.md`, `CLAUDE.md`, `final-delivery-acceptance`, `bilibili-render-pdf`, and `youtube-render-pdf` describe the same stage lifecycle, subagent separation, attempt limit, and guard-before-delivery rule.
- Hook fixture tests verify no-target and `generating` pass-through behavior for unrelated work.

## Delivery Blocking Behavior

- The end-to-end fixture suite must fail when a final response could bypass acceptance or a fresh guard pass.
- The suite must fail when project instructions and skill instructions disagree on the stage lifecycle, attempt limit, or required subagent separation.
- The suite must fail when stale acceptance evidence can approve a changed final PDF.

## Acceptance Criteria

- [x] Fixture tests cover successful delivery, failed-then-repaired delivery, three-attempt failure, stale evidence, missing rendered pages, hook pass-through, and hook blocking.
- [x] Contract tests verify synchronized documentation across skills, `.codex`, `AGENTS.md`, and `CLAUDE.md`.
- [x] Verification avoids real video downloads, real model calls, and full production video generation.
- [x] The issue set can be closed only after all previous guard and repair slices pass their local tests.
- [x] The final verification command and result are recorded in the execution log when implemented.

## Execution Log

- 2026-07-05: Created from [[prd/final-delivery-guard-and-bounded-repair]].
- 2026-07-05: Final verification passed: `python -X utf8 -B -m unittest discover .agents\skills\final-delivery-acceptance\scripts -p "test_*.py"` ran 25 tests OK; Bilibili helper tests ran 2 tests OK; `.codex/hooks.json` validated with `json.tool`; `git diff --check` reported line-ending warnings only.

## Comments
