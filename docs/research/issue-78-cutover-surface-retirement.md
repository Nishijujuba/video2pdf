# Retirement plan for completed Workflow 2.0 cutover surfaces

## 1. Executive conclusion

Completed Global Gate, Bilibili, YouTube, and Batch cutovers should stop shaping the ordinary Workflow 2.0 interface. Ordinary Bilibili, YouTube, and Batch admission should depend on the compatible, repository-owned Workflow Release Profile selected by [Choose the ordinary-run release authority after cutover](https://github.com/Nishijujuba/video2pdf/issues/76), plus the live Control Store and current Run or Batch state. It should not replay historical Exit Evidence, reconstruct a cutover candidate, infer `active_legacy`, or require machine-local cutover authority JSON and SQLite rows to remain current.

The old surfaces divide into four report-local lifecycle classes:

1. **Remove from ordinary use:** historical policy replay, duplicate platform `require_current`, `active_legacy` inference for new work, Batch Exit Evidence replay, and skill instructions that send routine startup into authority repair.
2. **Retain as release maintenance:** complete Exit Evidence validators, Git lineage and qualification validation, managed-mirror validation, and atomic publication/audit mechanics needed to publish or audit a Workflow Release Profile.
3. **Archive after migration:** first-cutover activation commands, candidate states, candidate-only recovery commands, refresh commands whose sole purpose is advancing old JSON/SQLite authority generations, and their cutover-only tests and active-context instructions.
4. **Retain as Legacy maintenance:** explicit support for pre-existing Legacy video and batch directories, including Run-record-free Legacy final compile and Acceptance v2 adoption. These paths remain selected by an existing directory's identity; they never become a fallback for a new task.

This classification does not choose the final CLI names or JSON shape. [Prototype the simplified Workflow 2.0 startup contract](https://github.com/Nishijujuba/video2pdf/issues/79) owns that public interface decision. It also does not decide the disposition of [Restore Global Gate Exit Evidence schema consistency after qualification binding expansion](https://github.com/Nishijujuba/video2pdf/issues/56) or [Refresh stale Global Gate policy authority after canonical branch consolidation](https://github.com/Nishijujuba/video2pdf/issues/57); [Resolve open authority-repair issues under the post-release model](https://github.com/Nishijujuba/video2pdf/issues/80) owns that decision.

## 2. Decision rules

The labels below describe lifecycle placement inside this report. They are not new canonical domain terms.

| Placement | Question answered | Caller | Retention rule |
|---|---|---|---|
| Ordinary | Can this requested Run or Batch safely start or continue now? | Render and Batch skills, normal operators | Keep only current release compatibility, Control Store coordination, current source, Run, task, Batch, acceptance, and delivery checks. |
| Release maintenance | Can a candidate Workflow Release Profile be published, or can a published release be audited? | Explicit repository-owner maintenance | Keep complete historical validation behind one maintenance interface. Never call it from ordinary startup. |
| Transitional migration | Is old machine-local authority state or an interrupted old publication still unresolved? | One bounded migration/recovery operation | Keep only until every existing state instance has an explicit disposition; then remove the interface. |
| Historical archive | How did the original cutover work? | Maintainers reading Git history and ADRs | Preserve decisions and evidence in Git. Remove executable authority and active instructions. |
| Legacy maintenance | Does a pre-existing Legacy directory still need supported maintenance? | Explicit Legacy recovery chosen from existing directory identity | Retain while such directories remain supported. Never infer this path for a new request. |

The module test is direct: a normal caller should learn one release-compatibility interface, not the implementation history of four cutovers. Complete Exit Evidence validation still has value, so it belongs behind the smaller release-maintenance interface instead of being deleted. Candidate choreography has no post-cutover caller once old state is migrated, so preserving it as an executable ordinary interface would be shallow and misleading.

## 3. Ordinary policy and admission surfaces

### 3.1 Remove historical replay from ordinary admission

The current `workflow-policy-check` calls `GlobalGatePublisher.check_policy`, then calls platform policy checks when the machine-local platform database contains state. Missing platform state is initialized in the result as `active_legacy` (`src/video2pdf_workflow_kernel/cli.py:1027-1048`). The Global Gate check reads either a policy-refresh overlay or the base authority, dereferences historical evidence, and returns historical mirror data (`src/video2pdf_workflow_kernel/global_gate.py:1128-1173`). Platform `require_current` validates machine-local authority and the complete published Exit Evidence, and ordinary `init-run` calls it again ([Inventory post-release admission checks and their authority lifetime](https://github.com/Nishijujuba/video2pdf/blob/20ebf2418d341888057927481ab4a4ea5f4c562b/docs/research/issue-75-post-release-admission-check-inventory.md)).

The end-state ordinary path should therefore:

- validate one Workflow Release Profile for structural validity, contract compatibility, and the requested platform or Batch capability;
- validate the live Control Store at the mutation seam;
- bind the compatible release identity needed by current Run and delivery artifacts without binding an old evidence path;
- perform that admission once at the owning initialization or planning seam;
- omit full Global Gate, platform, and Batch Exit Evidence replay;
- omit historical mirror, command/result, qualification, candidate-delivery, lineage, artifact-fingerprint, and absolute-path validation;
- omit `active_legacy` synthesis for Bilibili, YouTube, or Batch when release state is missing.

The current implementation of `workflow-policy-check` must leave ordinary use. Issue 79 may keep the command name as a Profile-only diagnostic or may make `init-run` and `batch-plan` own admission directly. This report does not select between those public interfaces.

### 3.2 Retain current-state checks

The following surfaces keep ordinary authority:

- `bootstrap-probe` for deterministic source identity and source metadata capture (`src/video2pdf_workflow_kernel/cli.py:500-502`);
- `control-store-check`, Control Store initialization, health, fencing, backup/restore, and the safe reinitialization boundary resolved by [Define safe local Control Store reinitialization](https://github.com/Nishijujuba/video2pdf/issues/77);
- `init-run` and `reconcile-run` for Run/output binding and initialization publication;
- `reconcile-authority` for current Run mutation authority, rather than platform cutover authority;
- task Claim, Attempt, Resource Lease, completion, Promotion, and Mutation Intent checks;
- Batch Record, current Run projection, resource-admission, recovery, and guarded-delivered success checks;
- source faithfulness, writing quality, Pyramid validation, compile closure and provenance, complete rendered-page coverage, Acceptance Report v2, Delivery Guard, and delivery lifecycle checks.

The word `authority` is overloaded in current command names. `reconcile-authority` is a live Run-record reconciliation command (`src/video2pdf_workflow_kernel/cli.py:603-607`, `2020-2027`) and must not be retired with `platform-kernel-reconcile`, which owns historical cutover publication. Implementation work should preserve this distinction explicitly.

## 4. Candidate states and cutover commands

The active CLI exposes the full first-cutover choreography at `src/video2pdf_workflow_kernel/cli.py:296-415` and `511-554`. The active Video Workflow glossary and decision map still describe `PREPARED`, `INITIALIZED`, `PROVISIONAL`, and `CONFIRMED` as if ordinary maintainers need the sequence (`docs/contexts/video-workflow/CONTEXT.md:5-9`, `61-73`; `docs/adr/video-workflow-kernel-2.0-decision-map.md:31-35`). These states remain valid history. They should stop being active ordinary language.

| Surface | End-state placement | Reason |
|---|---|---|
| `platform-kernel-prepare` | Archive after migration | Selects the one cold-start evidence candidate for a cutover that is already complete. |
| `init-cutover-candidate` | Archive after migration | Creates the candidate-only Run; ordinary Runs use `init-run`. |
| `platform-kernel-candidate-activate` | Archive after migration | Publishes `PROVISIONAL` candidate authority solely to complete the original guarded-delivery proof. |
| `platform-kernel-candidate-reconcile` | Transitional migration, then archive | Has value only for a surviving interrupted candidate publication. It must not become general Run recovery. |
| `platform-kernel-candidate-rebind` | Transitional migration, then archive | Repairs a historical candidate binding. No ordinary or Profile-publication caller should rebind a completed cutover candidate. |
| `platform-kernel-activate` | Transitional migration, then archive | Converts a delivered candidate plus Slice evidence to `CONFIRMED`; Profile publication replaces release activation. |
| `platform-kernel-reconcile` | Transitional migration, then archive | May settle a pre-existing activation or refresh intent; it should disappear after those rows receive an explicit migration disposition. |
| `PREPARED`, `INITIALIZED`, `PROVISIONAL`, `CONFIRMED` platform states and candidate tables | Historical archive after migration | Preserve their meaning in ADR 0040 and historical evidence. Remove them from the ordinary interface and active glossary once no live old intent depends on them. |
| `global-gate-activate` | Transitional migration, then archive | The completed Global Gate activation is a release fact; future atomic release publication belongs to the Workflow Release Profile publisher. |
| `global-gate-reconcile` | Transitional migration, then archive | May settle a surviving prepared Global Gate activation. It is not an ordinary Run recovery command. |
| `batch-activate` | Transitional migration, then archive | The completed Batch cutover becomes a Profile capability, not a separately replayed ordinary authority. |
| Batch cutover `PREPARED`/`COMMITTED` state and activation intent | Historical archive after migration | Preserve history in ADRs and Git; remove executable dependency after old state is dispositioned. |

Old authority files and tables must not be silently ignored while a `PREPARED` intent may still represent an interrupted publication. Migration must classify every existing intent as completed, safely reconcilable, explicitly abandoned under a governed rule, or blocking. The exact data conversion and tombstone representation require a dedicated follow-on decision.

## 5. Authority refresh and audit surfaces

The repository currently advances three old authority families:

- `global-gate-policy-authority-refresh` publishes a new policy overlay and Exit Evidence generation (`src/video2pdf_workflow_kernel/cli.py:305-320`; `src/video2pdf_workflow_kernel/global_gate.py:785-1126`);
- `youtube-platform-authority-refresh` advances the YouTube platform authority generation from new Exit Evidence (`src/video2pdf_workflow_kernel/cli.py:399-415`);
- `batch-authority-refresh` advances Batch authority and rebinds Global Gate/platform prerequisites (`src/video2pdf_workflow_kernel/cli.py:330-344`; `src/video2pdf_workflow_kernel/batch_authority.py:445-1037`).

Their validation capabilities remain useful, while their public authority model conflicts with Issue 76's single Workflow Release Profile.

The end-state treatment is:

1. Move complete Exit Evidence, qualification, lineage, mirror, and artifact validation behind Profile publication and explicit release audit.
2. Publish or replace the Profile atomically only after that maintenance validation passes. A failed candidate leaves the prior compatible Profile authoritative.
3. Stop advancing independent Global Gate, platform, and Batch ordinary-admission generations.
4. Keep the old refresh/reconcile commands only during migration for already prepared intents. Archive them after migration.
5. Keep historical validators callable by the maintenance module or operator audit. Remove any call from `workflow-policy-check`, `init-run`, `batch-authority-check`, `batch-plan`, or render-skill startup.

This yields a deep release-maintenance module: callers provide a candidate release package and receive a publish/audit result; callers do not orchestrate separate Global Gate, platform, Batch, candidate, mirror, and refresh state machines.

## 6. Batch authority surfaces

The Batch skill currently requires `workflow-policy-check` followed by `batch-authority-check`; the latter revalidates `active_batch.json`, SQLite authority, Slice 14 Exit Evidence, Global Gate binding, and both platform bindings (`.agents/skills/bilibili-batch-render-pdf/SKILL.md:14-47`; `src/video2pdf_workflow_kernel/batch_authority.py:1044-1164`).

Ordinary Batch planning should instead require that the compatible Workflow Release Profile declares the Batch capability and required platform capability active. Live Batch safety remains in `batch-plan`, `batch-run`, `batch-recover`, `batch-rebuild-projections`, `batch-status`, Resource Admission, Run identity, and guarded-delivered projection rules.

`batch-authority-check` should leave the ordinary flow. Issue 79 may replace it with a Profile-only diagnostic or eliminate the separate precheck. `batch-activate`, `batch-authority-refresh`, and cutover reconciliation follow the transitional/archive treatment in Sections 4 and 5.

## 7. Mirror checks

Mirror equality has two different lifetimes:

1. **Current repository mirror maintenance stays.** `.agents` and `.claude` copies that remain supported must stay byte-identical when their shared instruction changes. The Batch skill already states this change-time verification (`.agents/skills/bilibili-batch-render-pdf/SKILL.md:196-201`). Equivalent repository validation can remain part of change verification and Profile publication.
2. **Historical manifest mirror replay leaves ordinary admission.** Global Gate and Slice 14 manifests capture `mirror_checks`; current policy/authority checks replay those old facts against live files (`src/video2pdf_workflow_kernel/global_gate.py:86-102`, `1128-1173`; `src/video2pdf_workflow_kernel/batch_authority.py:1044-1112`). That replay belongs only to release publication or operator audit.

Collectors, validators, contracts, and tests such as `scripts/collect_issue43_exit_evidence.py`, `scripts/collect_issue15_exit_evidence.py`, `scripts/validate_slice_exit_evidence.py`, and their Exit Evidence tests should remain only to the extent the release-maintenance module or explicit audit still calls them. Cutover-specific duplicate fixtures and default-suite registration should be archived with the corresponding executable cutover path. Current mirror equality must not depend on a closed manifest's absolute paths.

## 8. Skill and documentation instructions

### 8.1 Remove or rewrite ordinary startup instructions

The Bilibili and YouTube skills currently say every new task must run `workflow-policy-check`, interpret missing/stale/unconfirmed machine-local authority as a repair condition, and then run `init-run` (`.agents/skills/bilibili-render-pdf/SKILL.md:38-52`; `.agents/skills/youtube-render-pdf/SKILL.md:26-40`). The Batch skill similarly teaches routine authority checks and includes detailed activation/refresh repair commands (`.agents/skills/bilibili-batch-render-pdf/SKILL.md:14-61`).

Active skills should instead state:

- new work is Kernel-only for Bilibili and YouTube and Kernel-supervised for Batch;
- ordinary admission requires a compatible Workflow Release Profile with the requested capability active;
- live Control Store and Run/Batch coordination remain mandatory;
- a missing, malformed, incompatible, or inactive Profile blocks startup and routes to explicit release maintenance;
- ordinary skills do not regenerate historical qualification evidence or invoke old activation/refresh commands.

The exact commands and configuration examples wait for Issue 79.

### 8.2 Retain explicit Legacy maintenance

The following instructions remain active because they operate on pre-existing Legacy identities:

- direct acquisition and guarded compile references that are explicitly limited to a pre-existing Legacy video directory (`.agents/skills/bilibili-render-pdf/SKILL.md:52-76`, `604-664`; `.agents/skills/youtube-render-pdf/SKILL.md:40-83`, `508-565`);
- `delivery-quality-final-compile --input-track legacy` with explicit video-root containment;
- `legacy-acceptance-adopt`, Acceptance Report v2, per-page review, Delivery Guard, and no synthetic Run Record;
- the staged Legacy Batch driver for pre-existing batch directories only (`.agents/skills/bilibili-batch-render-pdf/SKILL.md:175-193`).

These are maintenance paths selected from durable existing-directory identity. They are not release fallbacks. The `active_legacy` default in `workflow-policy-check` and any instruction that redirects a new task to Legacy must be removed.

### 8.3 Archive historical narrative without erasing evidence

ADR 0040, ADR 0064, the completed Exit Evidence manifests, and Git history remain the authoritative record of how the cutovers occurred. The active decision map should present the Profile-based ordinary route and move candidate choreography into a clearly historical section or a linked ADR. Active context glossaries should stop presenting candidate states as current caller vocabulary after migration. Historical ADR text must not be rewritten to pretend the cutover used the future Profile.

Root `AGENTS.md`, `CLAUDE.md`, `CONTEXT-MAP.md`, the Video Workflow and Delivery Quality contexts, the decision map, both render skills, the Batch skill, and their `.agents`/`.claude` mirrors form the minimum documentation change surface. The implementation ticket graph will refine the exact set after the prototype and migration decision.

## 9. Legacy fallbacks and prohibited interpretations

The ordinary release model has no fallback ladder. For a new request:

```text
compatible Profile + requested capability active
  -> ordinary Kernel admission

missing / malformed / incompatible Profile, or inactive capability
  -> startup blocked; explicit release maintenance required
```

It must never produce:

```text
platform authority file absent
  -> infer active_legacy
  -> create a new Legacy directory or use the Legacy Batch driver
```

For an existing directory, track identity remains separate:

```text
existing Kernel Run Record and Control Store binding
  -> Kernel resume/reconcile

pre-existing Run-record-free Legacy directory
  -> explicit Legacy maintenance and shared Acceptance v2/Delivery Guard
```

No ordinary probe, resume, reconciliation, or Batch recovery may synthesize a Run Record for a Legacy directory or migrate it automatically.

## 10. Source and test disposition

No new tests are required by this design ticket. The later implementation can adapt, retire, or reclassify existing tests while preserving the map's standing prohibition on adding new test cases.

| Source family | Planned disposition |
|---|---|
| `global_gate.py`, `platform_kernel.py`, and `batch_authority.py` ordinary historical replay | Remove from ordinary call paths; retain only validation required by Profile publication/audit. |
| CLI parsers and dispatch for candidate activation and old authority refresh | Transitional migration only, then remove from the active CLI. |
| Exit Evidence schemas, collectors, validators, and published manifests | Retain as release/audit material; stop treating their local paths as ordinary authority. |
| Candidate/cutover-only tests (`test_issue13_*cutover*`, `test_issue14_*cutover*`, `test_issue15_*cutover*`, Global Gate activation/refresh tests) | Keep during migration and Profile-publication replacement; archive from the registered active suite when their executable seam is removed. |
| Live Run, Control Store, task, resource, Batch projection, Acceptance v2, Delivery Guard, and Legacy-input tests | Retain as active behavior checks. |
| Skill mirror and current contract-mirror checks | Retain as repository change/release maintenance; remove historical replay from ordinary startup. |

Removing a public parser before old PREPARED intents are dispositioned would hide recovery rather than resolve it. Keeping every old parser indefinitely would preserve several shallow interfaces that expose completed release implementation details. Migration is therefore the explicit prerequisite for archival.

## 11. Follow-on decisions now visible

The public CLI and configuration question already lives in [Prototype the simplified Workflow 2.0 startup contract](https://github.com/Nishijujuba/video2pdf/issues/79). The authority-repair issue disposition already lives in [Resolve open authority-repair issues under the post-release model](https://github.com/Nishijujuba/video2pdf/issues/80).

This investigation makes one additional question precise enough for its own ticket:

> How should existing Global Gate, platform, and Batch authority JSON, SQLite rows, absolute Exit Evidence paths, candidate state, and prepared activation or refresh intents be migrated, dispositioned, or tombstoned before the old commands are archived?

The final implementation and documentation ticket graph remains fog until the startup prototype, migration contract, and authority-repair disposition are resolved.

## 12. Risks, limitations, and unknowns

- A Profile-only ordinary admission path is approved conceptually by Issue 76; its exact schema, file location, CLI ownership, and current-artifact binding remain unresolved until Issue 79.
- Old ignored machine-local authority files and SQLite databases may contain states absent from Git. Static source inspection cannot prove the live population or safe migration rule.
- Archiving cutover tests too early can remove the only executable specification for a validator still used by Profile publication. Implementation must trace actual callers before each removal.
- Retaining Legacy maintenance preserves a deliberate second track for existing directories. It does not authorize automatic fallback or migration.
- The current branch contains the Issue 77 research decision. The Issue 75 inventory is linked from its published commit because that research branch is not merged into the canonical branch at this investigation point.
- This investigation is static. It did not initialize or mutate a Run, Control Store, cutover authority, Batch authority, acceptance target, or release evidence.

## 13. Evidence appendix

### Prior decisions

- [Inventory post-release admission checks and their authority lifetime](https://github.com/Nishijujuba/video2pdf/issues/75) and its [research asset](https://github.com/Nishijujuba/video2pdf/blob/20ebf2418d341888057927481ab4a4ea5f4c562b/docs/research/issue-75-post-release-admission-check-inventory.md).
- [Choose the ordinary-run release authority after cutover](https://github.com/Nishijujuba/video2pdf/issues/76).
- [Define safe local Control Store reinitialization](https://github.com/Nishijujuba/video2pdf/issues/77).

### Primary repository sources

- Public CLI surface: `src/video2pdf_workflow_kernel/cli.py:296-415`, `500-554`, `947-1085`, `1543-1568`, `1714-1915`, `2020-2027`.
- Global Gate validation, authority, refresh, and policy check: `src/video2pdf_workflow_kernel/global_gate.py:56-130`, `535-783`, `785-1173`.
- Platform candidate, activation, refresh, and currentness: `src/video2pdf_workflow_kernel/platform_kernel.py:154-179`, `260-566`, `2780-3703`.
- Batch activation, refresh, currentness, and reconcile: `src/video2pdf_workflow_kernel/batch_authority.py:282-1240`.
- Current platform and Legacy domain language: `docs/contexts/video-workflow/CONTEXT.md:1-73`, `231-255`, `485-505`.
- Current Delivery Quality and Legacy input authority: `docs/contexts/delivery-quality/CONTEXT.md:1-7`, `96-104`.
- Historical cutover decisions: `docs/adr/0040-cut-over-to-the-kernel-one-platform-at-a-time.md`; `docs/adr/0064-activate-global-acceptance-v2-gate.md`; `docs/adr/video-workflow-kernel-2.0-decision-map.md:11-35`, `149-157`.
- Render and Batch instructions: `.agents/skills/bilibili-render-pdf/SKILL.md:38-76`, `604-700`; `.agents/skills/youtube-render-pdf/SKILL.md:26-83`, `508-601`; `.agents/skills/bilibili-batch-render-pdf/SKILL.md:14-61`, `175-201` and their `.claude` mirrors.

### Verification discipline

No test, qualification, Exit Evidence generation, release audit, source acquisition, Run initialization, or authority mutation was executed. Repository changes are limited to this research asset on an isolated branch. GitHub writes are limited to the Issue claim and the later Wayfinder resolution lifecycle.
