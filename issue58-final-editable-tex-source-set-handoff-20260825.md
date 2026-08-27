# Issue 58 Final Editable TeX Source Set — Agent Handoff

## 1. Handoff outcome

Issue 58 的实现和核心兼容测试已经完成，工作区仍未提交、未推送。后续代理应从最终 diff 审计和独立 Standards/Spec 双轴审查继续，禁止重做已经取得终态证据的测试，禁止开始 Tickets 59–61。

权威规格：

- Ticket 58: https://github.com/Nishijujuba/video2pdf/issues/58
- Parent spec 55: https://github.com/Nishijujuba/video2pdf/issues/55
- Local ADR: `D:\Project\video2pdf\issue58-final-editable-tex-source-set\docs\adr\0067-derive-final-editable-tex-source-sets-from-compile-evidence.md`
- Current diff: compare worktree against `origin/video-workflow-2.0`.

## 2. Live Git/GitHub state at handoff

- Repository source checkout: `D:\Project\video2pdf\newskill-kimi`
- Isolated worktree: `D:\Project\video2pdf\issue58-final-editable-tex-source-set`
- Branch: `codex/issue-58-final-editable-tex-source-set`
- Base and current HEAD: `be5888882f9ab847e768c285e016f076bf08e325`
- Merge base with `origin/video-workflow-2.0`: same SHA.
- Issue 58 was live-verified OPEN, labeled `ready-for-agent`, milestone `2.0`, without open blockers, and assigned to `Nishijujuba` before implementation.
- No commit, push, PR, resolution comment, or issue closure has occurred.
- Recheck GitHub state with `gh` before any GitHub mutation because live authority can drift.

The main checkout contains unrelated untracked Tencent material under `workspace/腾讯_AI Agent运行时安全防护与自迭代体系_qwen/`. It remains untouched and must remain untouched.

## 3. Approved public TDD seams

The user approved these three public seams:

1. Public `delivery-quality-final-compile` operation and its machine-readable compile evidence.
2. Public contract validator for membership, roles, fingerprints, generation binding, and stable failure codes.
3. Kernel and Legacy input tracks through the shared public Final Compile provider/adapter, proving semantic parity.

The earlier assumption that Kernel and Legacy have two separate adapters was corrected: both tracks share the same Final Compile provider/adapter. Testing still covers both public input tracks independently.

## 4. Implemented contract and behavior

The implementation introduces a standalone Video Workflow-owned `final-editable-tex-source-set/1.0.0` artifact. It preserves `final-compile-report/1.0.0` and manifest v1 semantics.

Implemented behavior includes:

- One generation-bound Final PDF binding and exactly one TeX entrypoint.
- Exact project-local consumed `.tex` closure derived from current compile dependency evidence.
- Entrypoint and included-source roles.
- Monolithic, root-level split chapters, nested includes, and consumed generated TeX snippets.
- Exclusion of unused drafts, unrelated TeX, runtime/workflow TeX, and non-TeX dependencies.
- Member path, role, generation identity, SHA-256 fingerprint, and size.
- Explicit `--tex-entrypoint-logical-id`; absent option retains exact `main.tex` monolithic compatibility.
- Optional named PDF/output directory support so multiple Final PDFs in one directory keep separate evidence and source closures while the default `final.pdf` layout remains compatible.
- Project-root boundary, `.tex` membership, duplicate logical ID/path, stale identity, entrypoint-evidence, closure, and PDF-binding validation.
- Real missing-include failure through the production Legacy CLI path.
- `kernel_version` recorded in the new artifact.

Files in scope are visible through `git status`; the principal new files are:

- `docs/adr/0067-derive-final-editable-tex-source-sets-from-compile-evidence.md`
- `schemas/video-workflow/v4/final-editable-tex-source-set.v1.schema.json`
- `src/video2pdf_workflow_kernel/final_tex_source_set.py`
- `tests/video_workflow/test_issue58_final_editable_tex_source_set.py`
- positive and negative contract fixtures under `tests/video_workflow/fixtures/contracts/`

Modified files cover context maps, schema registry, Final Compile CLI/provider/adapter/contracts, production and fixture guarded adapters, and compatibility tests. Use `git status --short` for the exact current list.

## 5. Stable failure contract

Verify the final table in `src/video2pdf_workflow_kernel/contracts.py` during review. Known stable codes include:

- `final_tex_entrypoint_missing`
- `final_tex_entrypoint_ambiguous`
- `final_tex_entrypoint_evidence_mismatch`
- `final_tex_dependency_evidence_mismatch`
- `final_tex_dependency_closure_incomplete`
- `final_tex_include_missing`
- `final_tex_pdf_binding_ambiguous`
- `final_tex_member_not_tex`
- `final_tex_member_outside_project`
- `final_tex_source_set_identity_stale`
- `final_tex_source_identity_stale`
- `final_tex_logical_id_duplicate`
- `final_tex_path_duplicate`

The exact gate/code pairs are authoritative in the current diff and tests.

## 6. Persisted terminal evidence

All paths below are under:

`D:\Project\video2pdf\issue58-final-editable-tex-source-set\待删除\long-running\`

Strongest completed evidence:

- Full Issue 58 focused module, 31/31, exit 0: `issue58_review55_isolated_complete_focused_module_20260825_200929_95c80c08`
- Delivery Quality compatibility modules, exit 0: `issue58_review56_delivery_quality_compatibility_20260825_203440_3cf31ef9`
- Real Kernel / Run-record-free Legacy parity, exit 0: `issue58_review48_real_kernel_legacy_parity_green_20260825_184510_11054948`
- Public validators, 9 tests, exit 0: `issue58_review47_public_validators_green_20260825_181206_338f6b27`
- Contract fixture test, exit 0: `issue58_review46_contract_fixture_green_20260825_174753_9e036268`
- Contracts check, exit 0: `issue58_review49_contracts_check_20260825_190222_e32ae84c`
- Real missing include, exit 0: `issue58_review39_real_missing_include_green_20260825_170854_686e7764`
- Same-folder multiple PDF fixture, exit 0: `issue58_review54_multiple_pdf_short_fixture_20260825_200803_a87a1968`

Review56 terminal snapshot: `succeeded`, exit code `0`, elapsed `556.64s`, `acceptance_evidence_eligible=true`. Preserve every `待删除/` evidence directory. Do not restart completed commands.

Some production-adapter tests ran in an isolated clean clone because adapter authority binds to committed bytes. Review55 and Review56 used clean authority commit `5b654c8d02b6749040812d5c360bab6102ed72ef` under `待删除/i58full/4bca98`. This commit belongs to the isolated evidence clone, not the main feature worktree.

## 7. Review history and remaining review gate

Three independent review rounds found and drove public RED/GREEN repairs. The fourth repair round addressed:

- Entrypoint role binding to compile evidence.
- Structured adapter errors for missing and ambiguous entrypoints.
- Runtime `.tex` exclusion.
- Two Final PDFs in one folder with independent closures.
- A real missing include through production CLI.
- `kernel_version`.
- Project boundary and `.tex` constraints.
- Duplicate logical ID and duplicate path constraints.

A fresh independent fourth Standards/Spec review has not yet been completed. This is the next mandatory gate.

## 8. Required swarm topology for the next agent

The next agent must continue in swarm mode. Spawn agents with the repository-required fields:

- `model: "gpt-5.6-sol"`
- `reasoning_effort: "medium"`
- `fork_turns: "none"` or a small positive turn count

Recommended bounded roles:

1. One execution/TDD agent: sole writer, responsible for any review-driven public RED → minimal GREEN repair. Only this agent may edit.
2. One Standards reviewer: read-only review against `AGENTS.md`, repository standards, abstraction honesty, semantic branch precedence, fixture atomicity, lifecycle/error compatibility, and scope cleanliness.
3. One Spec reviewer: read-only review against Issue 58 and parent Issue 55, including all required document shapes, exclusions, fail-closed cases, multiple PDFs, ownership terminology, and historical-output exclusion.

Standards and Spec reviewers must remain independent. If either reports a P0/P1/P2 finding, the execution agent must add one public-behavior RED test and apply the smallest GREEN fix. Then rerun only affected tests and both review axes. No speculative refactor is authorized.

Suggested skills:

- `$tdd` for any repair.
- `$code-review` for the final independent two-axis review.
- `github:github` only when the final GitHub workflow begins; all GitHub operations must use `gh`.

## 9. Exact continuation sequence

1. Enter the isolated worktree and re-read root `AGENTS.md` plus `$tdd` and `$code-review` skill instructions.
2. Live-check Issue 58, parent Issue 55, blockers, assignee, and branch/worktree state with `gh` and Git.
3. Confirm Review56 from its existing run directory with `show` or `reconcile`; do not relaunch it.
4. Run read-only `git status --short`, `git diff --check`, `git diff --stat`, and full `git diff`.
5. Run the independent Standards and Spec review agents in parallel while the main agent audits the final diff and scope.
6. If reviews are clean, run any still-required skill/context/ADR contract checks and the repository-defined complete affected acceptance suite through the persisted runner. Before each check, state the specific failure it detects and the decision that failure would change.
7. Before commit, inspect status/diff again. Stage only explicit Issue 58 paths; never use `git add .`, `-A`, or `--all`.
8. Create one commit: `feat: derive final editable TeX source sets`.
9. Because production adapter authority is commit-bound, rerun the production-adapter-sensitive Final Compile, Legacy, Kernel parity, and affected compatibility/acceptance tests against the actual feature commit using persisted commands.
10. Push the feature branch and verify local SHA equals `git ls-remote origin refs/heads/codex/issue-58-final-editable-tex-source-set`.
11. Follow the repository's human approval flow. If a PR is required, create a draft PR and keep Issue 58 open.
12. Close Issue 58 only after the implementation enters the authoritative target branch and its remote SHA is verified. The resolution comment must include required behavior/evidence/review/commit details and end exactly with `This comment is AI-generated by Codex.` Re-read the published comment before closing.
13. Only after legitimate closure, recompute the Issue 55 sub-issue frontier. Do not begin a later ticket.

## 10. Safety and scope boundaries

- Never delete files. Preserve all `待删除/` test and persisted evidence.
- Never run `git reset --hard`, `git clean`, `git checkout -- <path>`, or stash user work.
- Do not touch the Tencent historical workspace.
- Do not stage unrelated files or test evidence.
- Tickets 59–61 remain explicitly out of scope: no Acceptance Manifest, Reviewer read set, Acceptance Report, or Delivery Guard semantics may be introduced.
- Existing Final Compile Input Set semantics for non-TeX dependencies remain intact.
- The current worktree is uncommitted. Any assumption that it is ready for issue closure is incorrect.
