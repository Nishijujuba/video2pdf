# Issue #15 Swarm Design Contract — Replace Legacy Batch with projections over guarded single-video Runs

This document is the shared coordination contract for all subagents implementing GitHub issue #15
("Replace Legacy Batch with projections over guarded single-video Runs", parent spec #41, arch spec #2, ADR 0036).
It pins the design so parallel agents produce consistent, composable work. Deviations must be reported to the master agent.

Repository root: `D:\Project\video2pdf\newskill-kimi` (branch `video-workflow-2.0`).
Python: `D:/Project/video2pdf/kimi/.venv/Scripts/python.exe`. Run with `-X utf8 -B`.

## Non-negotiables (from #15 AC + #41 spec + ADR 0036)

1. A registered **Batch Record** contract and **Batch Item Projection** contract bind deterministic source ordering
   and independent single-video `run_id` values.
2. Batch plan/run/recovery operations **cannot mutate** per-video checkpoints, generations, quality decisions,
   repair budgets, or delivery lifecycle. Batch is a read-only projection supervisor.
3. Interrupted item creation recovers **without duplicate Runs**; every projection rebuilds from authoritative Run state.
4. Batch-created Runs reuse the existing task envelopes, Role Projections, platform adapters, seals, repair limits,
   Acceptance Report v2, and Delivery Guard — never a parallel implementation.
5. Authentication breakers and fairness operate through existing platform and Resource Admission authority.
6. Batch item success requires **guarded `delivered` state**: `run.json["delivery"]["stage"] == "delivered"` plus a
   fresh passing `delivery_guard_report.json`. PDF existence, process exit code, cache state, free-form child
   workflow prompts, and global `--concurrency` authority are **retired**.
7. A schema-valid **Exit Evidence Manifest** (slice 14) proves recovery, reconstruction, fairness, breaker behavior,
   and guarded-delivered-only success before Batch activation.
8. **Nothing is permanently deleted.** Legacy batch files move under `待删除` or a clearly marked `legacy/` staging
   area with a README explaining the deprecation.

## Activation posture

Batch is a **capability cutover** (like Global Gate slice 11), NOT a platform cutover (slices 12/13).
It does not change `platform_statuses` (`bilibili`/`youtube` remain `active_kernel`).
The evidence manifest slice is **14**, name `batch-projection-cutover`, `activation_scope.kind: "batch_cutover"`.
Until the manifest is published, the new Batch CLI is implemented but not runtime-active authority;
the Legacy `run_batch.py` driver remains functional only for pre-existing legacy batch directories (mirroring the
"Existing Bilibili directories remain Legacy" policy). New batches route through the Kernel `batch-*` CLI.

## Batch Record contract (pinned)

New file `schemas/video-workflow/v5/batch-record.v1.schema.json`, registered in `schemas/video-workflow/registry.v1.json`
as `schema_name: "batch-record"`, `schema_version: "1.0.0"`, kind `contract`, with positive/negative example fixtures
under `tests/video_workflow/fixtures/contracts/` (`batch-record.valid.json`, `batch-record.invalid.json`).

Required fields (draft 2020-12, `additionalProperties: false`):

- `schema_name`: const `"batch-record"`
- `schema_version`: const `"1.0.0"`
- `kernel_version`: const `"2.0.0"`
- `batch_id`: 32-hex, deterministic — `sha256("batch\0" + canonical_platform + "\0" + batch_source_identity + "\0" + task_start + "\0" + request_id)[:32]`
- `batch_identity`: object with `{kind (enum: ["bilibili_playlist","bilibili_collection","bilibili_multipart","url_set"]), canonical_platform, batch_source_identity (sha256), source_url, original_title, task_start (ISO with tz), request_id}`
- `output_root`, `batch_dir`, `control_dir`: string paths
- `batch_stage`: enum `["planned","running","completed","blocked"]`
- `item_order`: array of objects `{item_index (int >= 1), part_id (string|null), canonical_item_id, canonical_url, title, selected (bool)}`, deterministically ordered (by item_index), minimum 1 item
- `run_mappings`: array of `{item_index, run_id (32-hex), request_id}` — one entry per *selected* item, created by `batch-run`, absent in `planned` stage
- `projections`: array of `{item_index, run_id, projection_revision (int >= 1), projection_sha256 (64-hex), item_projection (object — the Batch Item Projection instance)}`
- `created_at`, `updated_at`: ISO-8601 with timezone

Structural invariant `batch-record-v1` (registered in `contracts.py` `KNOWN_INVARIANTS` and a `_validate_batch_record_v1`):
`item_order` indices are exactly `1..len` (no gaps, no dupes); every `run_mappings` and `projections` entry references
an `item_index` that exists in `item_order`; `run_mappings` covers exactly the `selected` items; `projections[i]`
item_index set is a subset of `run_mappings` item_index set; `batch_stage == "planned"` implies `run_mappings == []`.

## Batch Item Projection contract (pinned)

New file `schemas/video-workflow/v5/batch-item-projection.v1.schema.json`, registry `schema_name: "batch-item-projection"`,
`schema_version: "1.0.0"`, kind `contract`, fixtures `batch-item-projection.valid.json` / `.invalid.json`.

Required fields (`additionalProperties: false`):

- `schema_name`: const `"batch-item-projection"`
- `schema_version`: const `"1.0.0"`
- `kernel_version`: const `"2.0.0"`
- `batch_id` (32-hex), `item_index` (int >= 1), `run_id` (32-hex)
- `run_state`: object — read-only mirror of authoritative run.json v4:
  `{phase (enum from run-record v4), source_state, source_blocker (string|null), coordination_revision (int >= 1), output_path, delivery: {stage (enum generating/ready_for_delivery/accepted/delivered/blocked), ownership: {session_id, generation}}}`
- `checkpoint`: `{name, status (enum ["current","stale"])}` — the run's current checkpoint, derived from run.json `checkpoints`
- `blocker`: `source_blocker` string or null
- `delivery_outcome`: `{delivery_stage (same enum), guarded_delivered (bool), acceptance_report_sha256 (string|null), guard_report_sha256 (string|null), delivered_at (ISO|null)}`
  — `guarded_delivered == true` only when `delivery_stage == "delivered"` AND `guard_report_sha256` is a valid passing guard report fingerprint (verified by the provider, not self-declared).
- `projection_revision` (int >= 1), `projected_at` (ISO), `source_authority`: `{run_record_sha256, guard_report_sha256 (nullable), accepted_at_projection}` — records exactly which authoritative artifacts this projection was built from.

Structural invariant `batch-item-projection-v1`: `guarded_delivered` is `true` only if
`delivery_outcome.delivery_stage == "delivered"` and `delivery_outcome.guard_report_sha256` is present and
`source_authority.run_record_sha256` is present. A projection carries **no write authority fields** (no phase,
checkpoint, or delivery mutation fields beyond the read-only mirror).

## Control Store changes (pinned)

New migration step **version 11** in `src/video2pdf_workflow_kernel/control_store.py` (ladder currently ends at 10, ~line 4006).

New tables (follow the `delivery_lifecycle` Style-B pattern — dedicated tables, deterministic natural-key ids,
partial unique indexes, insert-or-revive):

```sql
CREATE TABLE IF NOT EXISTS batch_records (
  batch_id TEXT PRIMARY KEY,
  batch_identity_json TEXT NOT NULL,
  batch_record_json TEXT NOT NULL,
  batch_record_sha256 TEXT NOT NULL UNIQUE,
  batch_stage TEXT NOT NULL CHECK(batch_stage IN ('planned','running','completed','blocked')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS batch_item_projections (
  batch_id TEXT NOT NULL REFERENCES batch_records(batch_id),
  item_index INTEGER NOT NULL CHECK(item_index >= 1),
  run_id TEXT NOT NULL REFERENCES run_bindings(run_id),
  projection_revision INTEGER NOT NULL CHECK(projection_revision >= 1),
  projection_json TEXT NOT NULL,
  projection_sha256 TEXT NOT NULL,
  projected_at TEXT NOT NULL,
  PRIMARY KEY (batch_id, item_index)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_projection_per_batch_item
  ON batch_item_projections(batch_id, item_index);
```

ControlStore method surface (Style A wrappers over these tables, mirroring `_planned_immediate`/CAS conventions where
appropriate; **batch never touches run_bindings/initialization_intents/delivery_lifecycle_intents**):

- `create_batch_record(batch_record_json, batch_identity_json) -> (batch_id, "CREATED" | "REPLAY")`
  — idempotent: if a row with the same `batch_id` (deterministic id) exists with byte-identical `batch_record_sha256`,
  return REPLAY; if exists with a different sha, raise `KernelConflict` (identity changed on replay).
- `get_batch_record(batch_id) -> dict | None`
- `update_batch_stage(batch_id, expected_stage, new_stage)` — CAS on batch_stage.
- `record_run_mapping(batch_id, item_index, run_id, request_id)` — append to `run_mappings` in batch_record_json
  (rewrite row atomically with new sha + updated_at). Idempotent: same (item_index, run_id, request_id) → no-op/REPLAY.
- `put_item_projection(batch_id, item_index, run_id, projection_revision, projection_json)` — upsert, always increments
  projection_revision when content differs (revision = previous revision + 1, never reused).
- `get_item_projection(batch_id, item_index) -> dict | None`
- `list_batch_records() -> list[dict]`
- `list_run_mappings(batch_id) -> list[{item_index, run_id, request_id}]`

`_require_batch_schema_absent` / `_create_batch_tables` / `_validate_batch_tables` helpers mirroring
`_require_delivery_lifecycle_schema_absent` (control_store.py:925) / `_create_delivery_lifecycle_tables` (914) /
`_validate_delivery_lifecycle_tables` (945). Add the two new tables to the `ControlStore.check()` `required_tables` set (~line 4520).

## Kernel module (pinned)

New module `src/video2pdf_workflow_kernel/batch_projection.py` exposing `BatchProjectionProvider` (deep module,
file-local `_connect`-style store access or ControlStore method delegation, fenced by the existing
`_exclusive_initial_delivery_lock` for creation and the `initial-delivery-task-index.lock`).

Public methods:

- `plan(workspace_root, contracts, *, platform, source_url, task_start, request_id, selection=None) -> BatchPlanResult`
  — deterministic enumeration. For a playlist/collection/multipart URL use the existing platform adapter candidate
  enumeration (`source_candidates.py`) or a yt-dlp flat playlist listing (no download); for `url_set` accept an
  explicit ordered list of canonical URLs. Applies `selection` (indexes or part ids); writes the Batch Record in
  `planned` stage under `batch_dir` (normalized title + timestamp under `workspace/`); **creates no Runs**.
- `run(workspace_root, contracts, *, batch_id, control_store_root, session_id, global_gate_binding, run_task_start) -> BatchRunResult`
  — for each selected planned item: derive the item's deterministic `run_id` via the **existing bootstrap formula**
  (`kernel.py:406-415`: `sha256(canonical_platform, canonical_item_id, task_start, request_id)[:32]`, where
  `request_id = f"{batch_id}:{item_index}"`), call `kernel.bootstrap_production_source(..., provider_kind="deterministic_locator")`
  then `kernel.initialize_production_source(probe, session_id=..., global_gate_binding=...)` (reuses the exact
  single-video path — task envelopes, projections, checkpoints all come from the kernel). If the binding already
  exists (interrupted earlier), `initialize_production_source` already reconciles + returns the existing run
  (kernel.py:622-632) — **no duplicate run**. Record the mapping, then submit the run's first task through
  `ResourceAdmission.claim_task(..., fairness_group_id=batch_id, batch_id=batch_id, ...)` — **only currently admitted
  work**; no pre-created futures, no global concurrency. Batch stage → `running`.
- `recover(workspace_root, contracts, *, batch_id, control_store_root) -> BatchRecoverResult`
  — for every `run_mappings` entry: reconcile the run via the kernel (`reconcile-run` path / `reconcile_initialization`
  when its intent is non-COMMITTED), then rebuild its projection. Idempotent; interrupted creation converges to one run.
- `rebuild_projections(workspace_root, contracts, *, batch_id) -> list[BatchItemProjection]`
  — rebuild EVERY item projection from authoritative Run state only:
  read `output_path/workflow/run.json` (v4, validate via `contracts.validate_run_record`), derive
  `phase/source_state/source_blocker/coordination_revision/checkpoint` from it, read
  `review/acceptance/delivery_guard_report.json` + `review/acceptance/acceptance_report.json` (when present),
  verify `guarded_delivered` per the contract invariant. Never writes run state. Writes projections via
  `put_item_projection`. Batch stage → `completed` when every selected item is guarded-delivered, `blocked` when any
  is delivery-stage `blocked` (or a user-input blocker), else `running`.
- `status(workspace_root, contracts, *, batch_id) -> BatchStatusResult` — read-only report of batch stage, per-item
  projection summaries (item_index, title, run_id, delivery_stage, guarded_delivered, blocker).

Item success rule (single helper, used by recover + status):
`is_guarded_delivered(projection) -> bool` requires `delivery_outcome.delivery_stage == "delivered"` and a
validated passing `delivery_guard_report.json` bound in the run's delivery projections (validate via
`guarded_delivery.validate_delivery_guard_report`, guarded_delivery.py:64). PDF existence is never consulted.

## CLI (pinned)

Add to `src/video2pdf_workflow_kernel/cli.py` (register in `_parser()` + `_execute` dispatch):

- `batch-plan` — `--platform bilibili|youtube --source-url --task-start --request-id [--selection N,N] [--url-set U1,U2] [--workspace-root]`; returns batch_id + batch_dir + item_order + evidence_path (batch record path).
- `batch-run` — `--batch-id --control-store-root --session-id [--run-task-start] [--fault-point]`; returns per-item `{item_index, run_id, run_dir, stage}`. Requires the platform authority current for each item platform (`BilibiliPlatformCutoverPublisher().require_current`) and a global gate binding.
- `batch-recover` — `--batch-id --control-store-root [--workspace-root]`; returns reconciled mappings + rebuilt projections.
- `batch-rebuild-projections` — `--batch-id`; returns projections.
- `batch-status` — `--batch-id`; returns the status report.

Result envelope: existing `_ok(command, ...)` shape. All batch commands are read-only with respect to run state except
`batch-run` (which creates runs through the kernel's own guarded initialization) and `batch-recover` (which only
calls the kernel's own reconcile functions).

## Legacy batch retirement (pinned)

`.agents/skills/bilibili-batch-render-pdf/`:
- Rewrite `SKILL.md` (+ `.claude/skills/bilibili-batch-render-pdf/SKILL.md` mirror — they must end byte-identical):
  the skill is now a Kernel-supervisor skill. New-batch flow: `batch-plan` → `batch-run` → `batch-recover`/`batch-status`.
  Success definition: item success requires guarded `delivered` (run.json stage + fresh passing Delivery Guard);
  explicitly state that PDF existence, process exit codes, and cached batch status are retired success authorities.
  Concurrency: through Resource Admission only; global `--concurrency` is retired. Auth failures: open the platform
  Resource Circuit Breaker (existing authority); do not implement a batch-local cookie scanner as a breaker.
  Keep the manual/reconcile modes documented as **legacy-only** for pre-existing batch directories.
- Move `scripts/run_batch.py`, `scripts/test_run_batch.py`, `references/`, `agents/` into
  `.agents/skills/bilibili-batch-render-pdf/legacy/` with a `legacy/README.md` stating: legacy driver retained for
  pre-existing batch directories only; new batches use the Kernel `batch-*` CLI; the legacy driver's PDF-existence
  success and global concurrency carry no new-batch authority. Do NOT delete the files.
- `bilibili-render-pdf/SKILL.md` note (line ~294 "Batch orchestration remains out of scope here") may stay; add
  "Batch orchestration now lives in the Kernel `batch-*` CLI" only if the change is small and safe.

## Exit Evidence (slice 14, pinned)

- `schemas/exit-evidence-manifest.v2.schema.json`: add `14` to `sliceDefinition.number` enum; add a 14th `oneOf`
  branch `batch-projection-cutover` modeled on the slice-11 global-gate branch (line ~369):
  required `result_bindings`, `atomic_members`, `atomic_member_status`, `mirror_checks`, `policy_status`;
  `slice.number const 14`, `slice.name const "batch-projection-cutover"`;
  `activation_scope: {"$ref": "#/$defs/batchActivationScope"}`; add `batchActivationScope` def
  `{kind: const "batch_cutover", runtime_authority_change: const true, components_activated: [..], qualification_contract_sha256}`;
  `platform_statuses` remains `{bilibili: active_kernel, youtube: active_kernel}` (new branch must NOT relax the
  required platform keys); `results` required `positive`/`negative`/`recovery`/`fencing`.
- `scripts/validate_slice_exit_evidence.py`: add a `validate_batch_exit_evidence` semantic check (mirror the
  slice-11 dispatch at line 1772) asserting batch record contract presence, at least one guarded-delivered projection
  in a recorded batch, negative evidence for duplicate-run and PDF-existence-success rejection, and fencing/recovery
  result bindings. Also register the slice-14 contract constants import.
- New `scripts/issue15_exit_evidence_contract.py` (closed contract: SLICE_NUMBER=14, SLICE_NAME="batch-projection-cutover",
  EVIDENCE_PREFIX="evidence/slice-14/", ATOMIC_MEMBERS ~14 (batch_record_contract, batch_item_projection_contract,
  control_store_batch_persistence, batch_projection_provider, batch_cli, legacy_batch_retirement, batch_skill,
  project_instructions, validators, tests, activation_documentation, mirrors, exit_evidence_schema, evidence_collector),
  RESULT_SPECS incl. positives `batch_record_contract_pass`, `projection_rebuild_pass`, `guarded_delivered_only_success`;
  negatives `duplicate_run_rejected`, `pdf_existence_success_rejected`, `per_video_mutation_rejected`;
  recovery `reconcile_interrupted_item_creation`; fencing `projection_revision_fencing`) and
  new `scripts/collect_issue15_exit_evidence.py` following the `collect_issue14_exit_evidence.py` two-phase pattern.
- Manifest published at `evidence/slice-14/` ONLY after the full test suite passes on the final implementation commit.
- Fixtures: `tests/video_workflow/fixtures/exit_evidence/slice14.valid.json` + a negative fixture.

## Tests (pinned)

New files under `tests/video_workflow/` (follow `test_issue13_*`/`test_issue14_*` patterns, `new_case_dir` +
`kernel_cli._execute` or subprocess `scripts/video_workflow.py`):

- `test_issue15_batch_contracts.py` — registry entries valid; batch-record/batch-item-projection positive+negative
  fixtures pass; invariant `_validate_batch_record_v1` / `_validate_batch_item_projection_v1` reject gaps, dupes,
  cross-references to unknown item_index, run_mappings on planned, guarded_delivered without guard sha.
- `test_issue15_control_store_batch.py` — migration v11 creates tables; `create_batch_record` idempotent REPLAY;
  conflicting replay raises; stage CAS; run mapping append idempotent; projection upsert increments revision,
  never reuses; `ControlStore.check()` passes with batch tables; partial-v11 migration detected (fail-closed).
- `test_issue15_batch_projection.py` — plan writes planned record without runs; run creates one run per selected item
  with the deterministic formula (recompute expected run_id and compare), records mapping, submits through Resource
  Admission with fairness_group_id=batch_id; interrupted creation: fault-point mid-initialization, then re-run
  `batch-run` → same run_id, no duplicate, reconcile converges; rebuild_projections reads run.json + guard report,
  sets guarded_delivered only with passing guard; per-video state untouched (run.json byte-identical before/after
  batch ops except kernel-owned transitions); item success requires guarded delivered (a run with stage generating
  or a bare PDF in the dir is NOT success).
- `test_issue15_batch_authority.py` — batch commands cannot mutate run checkpoints/generations/repair budgets/
  delivery lifecycle (assert run.json and delivery_lifecycle_intents unchanged by batch-recover/status/rebuild);
  auth breaker flows through ResourceAdmission circuit breaker (open youtube breaker → batch-run for a youtube item
  stays queued/blocked, bilibili item still admitted); fairness_group_id == batch_id on claims.
- `test_issue15_batch_cli.py` — CLI arg validation, envelope shape, error codes for unknown batch, planned-without-selection.
- `test_issue15_batch_policy_docs.py` — pins the new Batch activation prose (model on test_issue14_platform_policy_docs.py):
  AGENTS.md, CLAUDE.md, CONTEXT-MAP.md, decision-map, video-workflow CONTEXT.md, batch SKILL.md + mirror must contain
  the pinned batch status sentences; `.agents`/`.claude` batch SKILL.md byte-equal.
- `test_issue15_exit_evidence.py` — slice14.valid fixture validates; negative fixture rejected.

## File inventory the swarm will produce

- `schemas/video-workflow/v5/batch-record.v1.schema.json` + `batch-item-projection.v1.schema.json`
- `schemas/video-workflow/registry.v1.json` (+2 entries) and `contracts.py` (+2 invariants + 2 `_validate_*`)
- `tests/video_workflow/fixtures/contracts/{batch-record,batch-item-projection}.{valid,invalid}.json`
- `src/video2pdf_workflow_kernel/control_store.py` (migration v11 + methods)
- `src/video2pdf_workflow_kernel/batch_projection.py` (new)
- `src/video2pdf_workflow_kernel/cli.py` (+5 commands)
- `.agents/skills/bilibili-batch-render-pdf/SKILL.md` + `.claude` mirror + `legacy/` move
- `AGENTS.md`, `CLAUDE.md`, `CONTEXT-MAP.md`, `docs/adr/video-workflow-kernel-2.0-decision-map.md`,
  `docs/contexts/video-workflow/CONTEXT.md` (+ Batch activation prose)
- `schemas/exit-evidence-manifest.v2.schema.json` (+14 branch), `scripts/validate_slice_exit_evidence.py`,
  `scripts/issue15_exit_evidence_contract.py`, `scripts/collect_issue15_exit_evidence.py`
- `tests/video_workflow/test_issue15_*.py` + fixtures
- `evidence/slice-14/` (final, after tests pass)

## Verification commands (run from repo root, venv python)

```powershell
D:/Project/video2pdf/kimi/.venv/Scripts/python.exe -X utf8 -B scripts/video_workflow.py contracts-check
D:/Project/video2pdf/kimi/.venv/Scripts/python.exe -X utf8 -B -m unittest discover -s tests/video_workflow -p "test_issue15_*.py" -v
D:/Project/video2pdf/kimi/.venv/Scripts/python.exe -X utf8 -B -m unittest discover -s tests/video_workflow -v
D:/Project/video2pdf/kimi/.venv/Scripts/python.exe -X utf8 -B scripts/project_test_discovery.py
```
