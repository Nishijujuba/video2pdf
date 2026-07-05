# PRD: Final Delivery Guard and Bounded Repair Loop

## Problem Statement

The project already has a Final Delivery Acceptance Gate for rendered video-to-PDF outputs. That gate defines the independent Acceptance Reviewer, the Acceptance Criteria File, rendered page evidence, and `acceptance_report.json` as the only machine-readable delivery decision source.

Two delivery gaps remain.

First, an existing PDF can fail Final Delivery Acceptance after it has already been rendered. The current workflow states that repair subagents may fix failed criteria and rerun acceptance, yet it does not define a bounded old-PDF repair mode that can safely locate the matching TeX source, section files, figures, tables, and build artifacts. Without an explicit boundary, a repair agent could search too broadly across historical outputs and modify the wrong video directory.

Second, a render workflow can still reach the final response without a fresh passing Final Delivery Acceptance result. Documentation alone is too weak as the last defense. The user needs a project-local guard that detects the active delivery target, verifies the mechanical acceptance contract, and blocks final delivery when the acceptance evidence is missing, failed, malformed, stale, or outside the allowed project boundary.

The user will invoke PDF generation skills only from `D:\Project\video2pdf\newskill-kimi`. The solution should therefore focus on this project directory, its `workspace\` layout, its `.agents\skills` tree, and its `.codex` project hook configuration. Cross-project plugin packaging is premature for this requirement.

## Solution

Implement a project-local Final Delivery Guard and bounded repair loop as an extension of the existing Final Delivery Acceptance Gate.

The main entry points remain `/bilibili-render-pdf` and `/youtube-render-pdf`. Those render skills become the recommended and enforced workflow path for producing a deliverable PDF. They render the PDF, create an explicit delivery target, run Final Delivery Acceptance through a separate subagent, run bounded repair when acceptance fails, and only deliver after `delivery_guard.py` records a fresh mechanical pass.

The `final-delivery-acceptance` skill gains an old-PDF repair mode. This mode accepts a PDF plus a required video output boundary. If the user passes only a PDF path, the skill may infer the video output directory only when the PDF is already inside a valid video output directory. If inference is ambiguous or the PDF is isolated, the workflow stops and asks for an explicit `video_output_dir`. Repair subagents may inspect and modify only files inside that video output directory.

The repair loop is bounded to three attempts. Each failed acceptance attempt produces a repair brief from the Acceptance Report, including failed criteria, criterion results, visual scan evidence, rendered page evidence, and reviewer revision guidance. A repair subagent revises the affected TeX, section files, figures, tables, or credibility caveat placement inside the video output directory. The workflow recompiles or regenerates affected final artifacts, refreshes rendered page evidence and stale upstream evidence, and starts a fresh independent Acceptance Reviewer run from the final-artifacts-only context. After three failed attempts, the workflow blocks delivery and writes a manual repair brief. Automatic waiver is outside this PRD.

Add a lightweight project Stop hook as a final safety guard. The hook reads the current delivery target, checks whether a fresh passing `delivery_guard_report.json` already proves the mechanical delivery contract, and runs `delivery_guard.py check` only when that proof is missing or stale. The hook blocks final delivery when the guard check fails. The blocking message must explicitly instruct the next agent to use subagents to run the Final Delivery Acceptance workflow.

The hook stays mechanical. It must not launch the Acceptance Reviewer, repair subagents, `xelatex`, page rendering, or the three-attempt repair loop. Those longer actions belong to the render skills and the `final-delivery-acceptance` skill.

### Concrete artifact paths

Project-level active target:

- `.codex/delivery-targets/current.json`

Video-output-level delivery target and guard evidence:

- `<video-output-dir>/review/acceptance/delivery_target.json`
- `<video-output-dir>/review/acceptance/delivery_guard_report.json`
- `<video-output-dir>/review/acceptance/manual_repair_brief.md`

Attempt evidence:

- `<video-output-dir>/review/acceptance/attempts/attempt_01/acceptance_report.json`
- `<video-output-dir>/review/acceptance/attempts/attempt_01/acceptance_summary.md`
- `<video-output-dir>/review/acceptance/attempts/attempt_01/repair_brief.md`
- `<video-output-dir>/review/acceptance/attempts/attempt_01/changed_files.json`
- `<video-output-dir>/review/acceptance/attempts/attempt_02/...`
- `<video-output-dir>/review/acceptance/attempts/attempt_03/...`

Existing latest acceptance artifacts remain in their fixed locations:

- `<video-output-dir>/review/acceptance/allowed_artifacts_manifest.json`
- `<video-output-dir>/review/acceptance/rendered_pages/page_0001.png`
- `<video-output-dir>/review/acceptance/acceptance_report.json`
- `<video-output-dir>/review/acceptance/acceptance_summary.md`

The latest report path always represents the most recent Acceptance Reviewer run. Historical reports are preserved under `review/acceptance/attempts/`.

Implementation targets:

- `.agents/skills/final-delivery-acceptance/SKILL.md`
- `.agents/skills/final-delivery-acceptance/scripts/delivery_guard.py`
- `.agents/skills/final-delivery-acceptance/scripts/validate_acceptance_report.py`
- `.agents/skills/final-delivery-acceptance/scripts/render_pdf_pages.py`
- `.agents/skills/bilibili-render-pdf/SKILL.md`
- `.agents/skills/youtube-render-pdf/SKILL.md`
- `.codex/` project hook configuration
- `AGENTS.md`
- `CLAUDE.md`

Claude Code receives synchronized documentation requirements through `CLAUDE.md`. A Claude Code-specific hook is outside this PRD.

### Delivery target contract

The project-level active target file identifies the single current delivery workflow inside this project.

```json
{
  "schema_version": "1.0",
  "stage": "ready_for_delivery",
  "video_output_dir": "workspace/example_video_20260705_120000",
  "target_file": "workspace/example_video_20260705_120000/review/acceptance/delivery_target.json",
  "source_skill": "bilibili-render-pdf",
  "updated_at": "2026-07-05T12:00:00+08:00"
}
```

Allowed `stage` values:

- `generating`: the PDF workflow is still producing or revising artifacts; the Stop hook allows normal intermediate work.
- `ready_for_delivery`: the PDF has been rendered and final delivery is being prepared; the Stop hook must enforce the delivery guard.
- `accepted`: Final Delivery Acceptance has passed and the guard must verify freshness before delivery.
- `delivered`: the final response has delivered the PDF; the render skill clears `.codex/delivery-targets/current.json`.
- `blocked`: the repair loop failed or the target is invalid; the Stop hook blocks and gives explicit recovery instructions.

The video-output-level target binds the guard to concrete artifacts.

```json
{
  "schema_version": "1.0",
  "stage": "ready_for_delivery",
  "video_output_dir": ".",
  "final_pdf": "final.pdf",
  "main_tex": "main.tex",
  "allowed_artifacts_manifest": "review/acceptance/allowed_artifacts_manifest.json",
  "acceptance_report": "review/acceptance/acceptance_report.json",
  "delivery_guard_report": "review/acceptance/delivery_guard_report.json",
  "attempt_limit": 3
}
```

The project-level `video_output_dir` must resolve under `D:\Project\video2pdf\newskill-kimi\workspace\`, unless the workflow received an explicit current-project video output directory. Any path escaping `D:\Project\video2pdf\newskill-kimi` blocks delivery.

### Delivery guard report contract

`delivery_guard.py` writes the mechanical guard result.

```json
{
  "schema_version": "1.0",
  "status": "pass",
  "checked_at": "2026-07-05T12:10:00+08:00",
  "stage": "accepted",
  "video_output_dir": "workspace/example_video_20260705_120000",
  "final_pdf": "final.pdf",
  "validated_by": "delivery_guard.py",
  "acceptance_report_status": "pass",
  "artifact_fingerprints": [],
  "checked_conditions": [],
  "blocking_message": null
}
```

A pass means the machine delivery contract holds. It does not judge PDF quality. The Acceptance Reviewer remains responsible for quality judgment.

The guard pass conditions are:

- the active target exists and has stage `ready_for_delivery` or `accepted`;
- the video output directory is inside the current project boundary;
- `review/acceptance/allowed_artifacts_manifest.json` exists;
- `review/acceptance/acceptance_report.json` exists;
- the acceptance report validates with `validate_acceptance_report.py validate --enforce-decision`;
- the acceptance report has `overall_status: "pass"`;
- the final PDF is present in the allowed artifact manifest;
- rendered page evidence covers every current PDF page;
- the guard report binds current fingerprints for the final PDF, main TeX, manifest, acceptance report, and other final artifacts named in the manifest;
- an existing guard report is treated as pass only when those fingerprints still match current artifacts.

### Stop hook behavior

The Stop hook reads `.codex/delivery-targets/current.json`.

If no current target exists, the hook allows the response.

If the target stage is `generating`, the hook allows the response.

If the target stage is `ready_for_delivery` or `accepted`, the hook checks `delivery_guard_report.json`. When the report is missing, failed, malformed, or stale, the hook runs `delivery_guard.py check` once. If the guard still fails, the hook blocks the response.

If the target stage is `blocked`, the hook blocks the response and points to the failed attempt evidence or manual repair brief.

If the target stage is `delivered`, the render skill should already have cleared the project-level target. If a delivered target remains, the hook may allow the response and report that stale project state should be cleaned by the workflow.

Every blocking message must require subagent execution, using wording equivalent to:

```text
Final Delivery Guard blocked delivery. Use a separate Acceptance Reviewer subagent and repair subagents to run the Final Delivery Acceptance workflow for <video_output_dir>. Do not deliver this PDF until delivery_guard.py records a fresh pass.
```

## User Stories

1. As a video-to-PDF workflow owner, I want old PDFs to enter Final Delivery Acceptance with an explicit video output directory, so that repair work stays inside the correct artifact boundary.
2. As a video-to-PDF workflow owner, I want the skill to infer the video output directory only when the PDF is already inside one, so that filename similarity cannot route repair to the wrong project.
3. As a video-to-PDF workflow owner, I want isolated PDFs to require an explicit `video_output_dir`, so that ambiguous historical artifacts do not trigger broad workspace searches.
4. As a video-to-PDF workflow owner, I want repair subagents to inspect `main.tex`, `section_*.tex`, figures, tables, and build artifacts only inside the bound directory, so that the repair scope is mechanically constrained.
5. As a video-to-PDF workflow owner, I want a failed Acceptance Report to become a repair brief, so that repair agents target the actual failed criteria.
6. As a video-to-PDF workflow owner, I want the repair brief to include failed criteria, visual scan evidence, page numbers, and revision guidance, so that repair does not rely on hidden chat context.
7. As a video-to-PDF workflow owner, I want repair subagents to be separate from the Acceptance Reviewer, so that final judgment stays independent.
8. As a video-to-PDF workflow owner, I want each repair to rerender or regenerate affected final artifacts, so that the next reviewer sees the current PDF.
9. As a video-to-PDF workflow owner, I want stale rendered page evidence to be refreshed after repair, so that visual scan coverage matches the current PDF.
10. As a video-to-PDF workflow owner, I want stale upstream evidence to be refreshed when repair changes upstream artifacts, so that all gates remain current.
11. As a video-to-PDF workflow owner, I want the Acceptance Reviewer to run from final artifacts only after every repair, so that repair context cannot influence acceptance judgment.
12. As a video-to-PDF workflow owner, I want old failed reports preserved under attempt folders, so that the repair history remains auditable.
13. As a video-to-PDF workflow owner, I want the latest `acceptance_report.json` to stay at the fixed root acceptance path, so that existing validators and checklists continue to find the current decision.
14. As a video-to-PDF workflow owner, I want a maximum of three repair attempts, so that the workflow cannot loop indefinitely.
15. As a video-to-PDF workflow owner, I want three failed attempts to create `manual_repair_brief.md`, so that a human can see the remaining defects and previous repair attempts.
16. As a video-to-PDF workflow owner, I want automatic waiver excluded from the repair loop, so that a hard delivery failure remains visible.
17. As a video-to-PDF workflow owner, I want `/bilibili-render-pdf` to own final delivery enforcement, so that the existing Bilibili entry point remains the normal workflow.
18. As a video-to-PDF workflow owner, I want `/youtube-render-pdf` to own final delivery enforcement, so that the existing YouTube entry point remains the normal workflow.
19. As a video-to-PDF workflow owner, I want a thin delivery guard script, so that hooks and render skills share one mechanical check.
20. As a video-to-PDF workflow owner, I want the guard script to record its own report, so that hook decisions can be audited.
21. As a video-to-PDF workflow owner, I want the Stop hook to reuse a fresh passing guard report, so that repeated final responses do not redo unnecessary checks.
22. As a video-to-PDF workflow owner, I want the Stop hook to run the guard when the report is missing or stale, so that a skipped check is detected before delivery.
23. As a video-to-PDF workflow owner, I want the Stop hook to block `ready_for_delivery` targets without a guard pass, so that final delivery cannot bypass acceptance evidence.
24. As a video-to-PDF workflow owner, I want ordinary discussions to proceed when there is no active delivery target, so that the guard does not interfere with unrelated work.
25. As a video-to-PDF workflow owner, I want `generating` targets to pass through the Stop hook, so that long-running intermediate workflow steps can continue.
26. As a video-to-PDF workflow owner, I want `accepted` targets to verify guard freshness, so that a pass remains tied to current artifacts.
27. As a video-to-PDF workflow owner, I want `blocked` targets to stop final responses, so that unresolved acceptance failures cannot be hidden.
28. As a video-to-PDF workflow owner, I want `delivered` targets to be cleared after delivery, so that future tasks are not blocked by stale state.
29. As a video-to-PDF workflow owner, I want delivery target paths confined to this project, so that a hook cannot act on unrelated folders.
30. As a video-to-PDF workflow owner, I want the guard to require the final PDF to appear in the allowed artifact manifest, so that the reviewed artifact is the delivered artifact.
31. As a video-to-PDF workflow owner, I want the guard to validate acceptance report fingerprints, so that old reports cannot approve changed artifacts.
32. As a video-to-PDF workflow owner, I want rendered page evidence coverage checked mechanically, so that a claimed visual scan cannot skip pages.
33. As a video-to-PDF workflow owner, I want the guard to avoid semantic quality judgment, so that semantic acceptance remains the independent reviewer's responsibility.
34. As an Acceptance Reviewer, I want the repair loop to preserve my failed reports as evidence, so that later attempts can be compared against the actual acceptance findings.
35. As an Acceptance Reviewer, I want every rerun to start from the allowed final artifact context, so that my review role stays read-only and independent.
36. As a repair subagent, I want a scoped repair brief with allowed files and failed criteria, so that I can modify the right source artifacts.
37. As a repair subagent, I want the attempt number and previous changes recorded, so that repeated repairs can avoid losing earlier fixes.
38. As a future agent, I want `current.json` to identify the active delivery target, so that hook behavior does not depend on prompt guessing.
39. As a future agent, I want a documented stage lifecycle, so that generated, accepted, blocked, and delivered states are handled consistently.
40. As a future agent, I want hook blocking messages to require subagent execution, so that the workflow preserves the independent Acceptance Reviewer and repair roles.
41. As a future agent, I want Claude Code instructions synchronized in `CLAUDE.md`, so that the same final delivery rules are visible outside Codex.
42. As a future agent, I want plugin packaging deferred, so that the project-local contract can stabilize before distribution.
43. As a future agent, I want implementation tests at the video output directory level, so that delivery behavior is verified from observable artifacts.
44. As a future agent, I want hook tests to use fixture targets and guard reports, so that Stop hook behavior can be verified without running a full video workflow.
45. As a reader, I want every delivered PDF to have passed the final acceptance guard, so that reader-facing defects are caught before delivery.

## Implementation Decisions

- The first implementation is project-local to `D:\Project\video2pdf\newskill-kimi`.
- Cross-project plugin commands are deferred.
- The existing Bilibili and YouTube render skills remain the primary PDF generation entry points.
- The `final-delivery-acceptance` skill owns old-PDF acceptance, bounded repair, and manual repair brief behavior.
- Old-PDF repair requires an explicit video output directory unless the PDF path is already inside one valid video output directory.
- Repair subagents may read and edit only inside the bound video output directory.
- The Acceptance Reviewer remains read-only and may inspect only allowed final artifacts, the criteria file, the allowed manifest, and rendered page evidence.
- Repair and acceptance remain separate roles across every attempt.
- The repair loop has an attempt limit of three.
- After the third failed attempt, delivery is blocked and a manual repair brief is required.
- Automatic waiver is outside this workflow.
- Attempt evidence is preserved under numbered attempt directories.
- The latest Acceptance Report remains at `review/acceptance/acceptance_report.json`.
- `delivery_target.json` records the target PDF, main TeX, manifest, acceptance report, guard report, stage, and attempt limit.
- `.codex/delivery-targets/current.json` records the single active project delivery target.
- The stage lifecycle is `generating`, `ready_for_delivery`, `accepted`, `delivered`, and `blocked`.
- The render skills clear the project-level current target after successful final delivery.
- `delivery_guard.py` provides the shared mechanical check for render skills and the Stop hook.
- `delivery_guard.py` validates the existing Acceptance Report through the report validator with decision enforcement.
- `delivery_guard.py` writes `delivery_guard_report.json`.
- `delivery_guard_report.json` is a mechanical proof of freshness and contract validity.
- A guard pass never replaces Acceptance Reviewer judgment.
- The Stop hook reads the project-level active target and dispatches only the guard check.
- The Stop hook runs on `Stop` first. `UserPromptSubmit` remains out of scope for this version.
- The Stop hook may write `delivery_guard_report.json` through `delivery_guard.py`.
- The Stop hook must not launch the Acceptance Reviewer, repair subagents, page rendering, or LaTeX compilation.
- The Stop hook blocking message must explicitly require subagent-based Final Delivery Acceptance.
- Claude Code receives synchronized instructions through `CLAUDE.md`.
- A Claude Code-specific hook is out of scope for this PRD.

## Testing Decisions

- The highest test seam is the video output directory delivery guard. Tests should build fixture video output directories and verify guard pass, fail, and stale states through files on disk.
- `delivery_guard.py` should be tested through its CLI and report output. Tests should assert exit codes, `delivery_guard_report.json`, blocking messages, and artifact fingerprints.
- Guard tests should cover missing `current.json`, missing `delivery_target.json`, invalid stages, path escape attempts, missing manifest, missing Acceptance Report, failed Acceptance Report, stale Acceptance Report, missing rendered page evidence, page-count mismatch, and fresh pass.
- Existing `validate_acceptance_report.py` tests remain the authority for acceptance report schema, forbidden context, visual scan coverage, and fingerprint freshness.
- Stop hook tests should use fixture current-target files and fixture guard reports. They should verify pass-through for no target and `generating`, blocking for `ready_for_delivery` without a fresh guard pass, blocking for `blocked`, and pass for `accepted` with a fresh guard pass.
- Skill contract tests should read `bilibili-render-pdf`, `youtube-render-pdf`, `final-delivery-acceptance`, `AGENTS.md`, and `CLAUDE.md` to verify the documented workflow order and required subagent roles.
- Old-PDF repair tests should verify boundary behavior: PDF inside one valid video output directory can infer the directory, isolated PDF requires explicit directory, and path escapes are rejected.
- Attempt evidence tests should verify that failed reports, summaries, repair briefs, and changed file lists are preserved under numbered attempt directories.
- Manual repair brief tests should verify that three failed attempts produce `manual_repair_brief.md` and set the active target stage to `blocked`.
- Tests should avoid real Bilibili or YouTube downloads.
- Unit tests should avoid real model calls. Reviewer and repair subagent behavior should be verified through generated artifact contracts, prompts, and fixture reports.
- End-to-end smoke verification can use a small fixture PDF and fixture acceptance reports to prove the mechanical guard path without running a full video generation.

## Out of Scope

- Creating a cross-project plugin command.
- Creating a new umbrella `video-pdf-delivery` skill.
- Enabling `UserPromptSubmit` hook behavior.
- Implementing a Claude Code-specific hook.
- Allowing automatic waiver after failed acceptance.
- Expanding Final Delivery Acceptance criteria categories.
- Replacing the existing Acceptance Criteria File or Acceptance Report schema.
- Replacing Pyramid Gate or independent subtitle/content review.
- Running full Bilibili or YouTube video generation as part of implementation tests.
- Allowing repair agents to search outside the bound video output directory.

## Further Notes

This PRD is a follow-up to the existing Final Delivery Acceptance Gate. The earlier gate defines the semantic and visual acceptance standard. This PRD adds the missing operational enforcement: bounded old-PDF repair, explicit target state, mechanical guard proof, and a Stop hook that blocks delivery when that proof is absent.

The main design risk is recursive or long-running hook behavior. The Stop hook must stay small and mechanical. When acceptance or repair is needed, the hook should block and tell the agent to run Final Delivery Acceptance with separate subagents.

The second design risk is stale project state. The render skills must update the active target stage intentionally and clear the project-level target after delivery.

The third design risk is confusing a guard pass with quality acceptance. `delivery_guard.py` only proves that the acceptance evidence is fresh, complete, and decision-enforced. The Acceptance Reviewer remains the quality judge.
