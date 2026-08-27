# Migration of retired Workflow 2.0 cutover authority state

## Executive conclusion

The first compatible Workflow Release Profile publication must retire the machine-local Global Gate, platform, and Batch cutover authorities through one project-level migration. The migration must preserve the Cross-Run Control Store and every current Video Workflow Run unchanged. It must move the old cutover JSON and SQLite file families into a read-only local audit bundle, record an explicit disposition for every activation intent, refresh intent, and platform candidate, and publish one Cutover Authority Tombstone as the migration commit marker.

The old authority state must not be imported into the Workflow Release Profile or into `workspace/.workflow-control/control.sqlite3`. The Profile is the repository-owned ordinary-admission authority. The Cross-Run Control Store remains the live coordination authority. Old cutover state becomes historical evidence only.

Absolute Exit Evidence paths are observations to preserve in the audit record. They are not repaired, rebound, copied into the Profile, or required to resolve. A missing historical path therefore records `unavailable_at_migration` and does not block retirement when the compatible Profile independently declares the completed release capability.

Prepared old cutover intents must not be completed after the Profile becomes authoritative. The migration records them as `abandoned_by_retirement`, preserves their exact old database and projected JSON state, and revokes any candidate-only privilege. Committed intents and `CONFIRMED` candidates are recorded as completed historical facts. Candidate Video Workflow Runs and their current Control Store authority remain untouched.

## Scope and invariants

This decision covers only the release-time authority stores that earlier cutover commands own:

- `active_global_gate.json`, optional `active_global_gate_policy.json`, and `global-gate-control.sqlite3`;
- `platform-authorities/<platform>.json` and `platform-kernel-control.sqlite3`;
- `active_batch.json` and `batch-cutover-control.sqlite3`;
- absolute Exit Evidence paths embedded in those JSON files or SQLite intent rows;
- platform candidate rows and activation or authority-refresh intent rows in those cutover databases.

The migration does not mutate or archive:

- `workspace/.workflow-control/control.sqlite3`, its identity files, backups, or sidecars;
- Video Workflow Run Records, output-path bindings, Claims, Leases, resource state, task attempts, promotions, delivery intents, or acceptance intents;
- candidate Run output directories or their current delivery state;
- Acceptance Report v2, rendered-page evidence, Delivery Guard state, or existing Legacy directories;
- repository ADRs, published Exit Evidence, or Git history.

These exclusions are authority boundaries. A row named `intent` in a cutover database is retired release-publication state. A Mutation Intent in the Cross-Run Control Store is live runtime coordination and must survive unchanged.

## Verified current-machine inventory

The read-only inventory was taken on 2026-08-27 from canonical branch `video-workflow-2.0` at `93d0a671877464a0336de1370c75b741aa71dc83`. It is a migration example, not a portable release fact.

| Store | Current local state | Migration consequence |
|---|---|---|
| Global Gate | Generation 1 authority and activation intent are committed. `active_global_gate_policy.json`, policy authority rows, and policy refresh intents are absent. | Archive the committed base authority. No policy overlay needs disposition. |
| Global Gate evidence path | `active_global_gate.json` and its committed intent refer to `q/evidence/global-gate/exit-evidence-manifest.json`; the target is absent. | Record `unavailable_at_migration`. Do not recreate or rebind it. |
| Bilibili platform | Generation 1 authority, committed activation intent, and one `CONFIRMED` candidate. Its Slice 12 evidence path currently resolves. | Archive all cutover state; retain the candidate Run as an ordinary governed Run record. |
| YouTube platform | Generation 2 authority, committed activation and refresh intents, and one `CONFIRMED` candidate. Its Slice 13 evidence path currently resolves. | Archive all cutover state; retain the candidate Run as an ordinary governed Run record. |
| Batch | Generation 2 authority with committed activation and refresh intents. Its Slice 14 evidence path currently resolves. | Archive all cutover state. |
| Prepared old intents | None in the three cutover databases. | The general migration contract still requires an explicit rule for other workspaces and interrupted future attempts. |

The observed stale Global Gate path confirms the distinction between release history and current admission authority. Recreating the missing `q` tree would restore a retired machine-local seam and would not strengthen the repository-owned Profile.

## Alternatives considered

### Import old JSON and SQLite rows into the Workflow Release Profile

Rejected. The old state binds machine-local paths, cutover generations, candidate identities, and historical publication mechanics. Importing it would make the Profile another projection of the retired stores and would preserve the ordinary-startup dependency that the Profile is intended to remove.

### Delete the old state after Profile publication

Rejected. Raw deletion would erase the only local record of interrupted publications, candidate disposition, and prior authority projections. It would also make crash recovery and operator diagnosis ambiguous.

### Reconcile every prepared old intent before retirement

Rejected as a universal rule. Completing an old activation or refresh after Profile publication creates a new retired authority generation with no current consumer. Reconciliation remains useful only before Profile authority is published when an operator explicitly chooses to finish the old release operation. Once migration starts, prepared cutover intents receive a retirement disposition.

### Archive the old stores and commit retirement with a tombstone

Selected. This preserves local audit evidence, removes old state from active locations, prevents accidental resurrection, keeps the Profile independent, and leaves the Cross-Run Control Store untouched.

## Selected domain model

The migration uses three separate artifacts with different authority:

1. **Workflow Release Profile**: repository-owned authority for ordinary admission and contract compatibility.
2. **Retired Cutover Audit Bundle**: local historical material containing the original cutover JSON and complete SQLite file families plus a normalized inventory and disposition report. It grants no runtime authority.
3. **Cutover Authority Tombstone**: the project-local commit marker proving that the old active locations were retired under one exact Profile identity and pointing to the audit bundle. It grants no ordinary-admission authority.

The Tombstone prevents an old command from treating missing active files as a fresh activation opportunity during the transition window. Old cutover mutators must check it first and fail with a stable `cutover_authority_retired` result. After those commands are archived, the Tombstone remains useful for idempotent migration, diagnostics, and local audit discovery.

## One deep migration interface

The public migration interface should be one command:

```powershell
python scripts\video_workflow.py retire-cutover-authority `
  --project-config config\workflow-project.v1.json
```

The project configuration locates the compatible Workflow Release Profile and project-local roots. The command owns inventory, quiescence, classification, archival, disposition, commit, and interrupted-run recovery. Re-running the same command resumes or returns the committed result; callers do not sequence separate Global Gate, platform, and Batch migration commands.

This interface is deep because the caller supplies one stable configuration identity while the module owns all retired schemas and ordering. Exposing table names, platform-specific switches, old generations, or per-file move commands would leak the retired implementation into the new operator contract.

## Migration protocol

### 1. Admission and fencing

The command must fail closed unless:

- the referenced Workflow Release Profile is present, structurally valid, and compatible with the running contracts;
- every capability represented by a committed old authority is active in the Profile;
- no requested capability would be inferred from a missing or non-terminal old candidate;
- the Cross-Run Control Store passes its existing identity and integrity check;
- an exclusive project-level retirement lock proves that no old cutover mutator or another migration is active.

The migration reads the Cross-Run Control Store only to prove that the live store is healthy and to identify candidate Run records that must remain untouched. It writes no Control Store row.

### 2. Prepare the retirement record

The module creates a project-local `PREPARED` Cutover Authority Retirement Record under a staging directory outside every old active path. The record includes:

- Workflow Release Profile schema, path, and compatibility identity;
- each discovered old JSON, SQLite database, and SQLite sidecar path;
- the old authority generation and embedded fingerprints already present in the stores;
- each activation and refresh intent with its original state and retirement disposition;
- each platform candidate identity, candidate state, and retained Run disposition;
- each embedded Exit Evidence path plus `available_at_migration` or `unavailable_at_migration`;
- an explicit empty entry for each expected surface that was absent;
- migration state, start time, and stable failure code when preparation cannot continue.

No new checksum inventory is required. The existing authority fingerprints are preserved as historical fields. Migration correctness relies on exclusive access, SQLite-consistent capture, same-volume moves, exact path inventory, and post-move structural reopening.

### 3. Classify old state

The classification is deterministic:

| Old state | Retirement disposition | Effect on current runtime |
|---|---|---|
| Committed authority row and matching projected JSON | `archived_completed_release_state` | Profile remains the only ordinary-admission authority. |
| Committed intent | `archived_committed_publication` | No replay or new generation. |
| Prepared activation or refresh intent | `abandoned_by_retirement` | Preserve intent and any staged/projected bytes; do not commit the old operation. |
| Cancelled Batch refresh intent | `archived_cancelled_publication` | Preserve its prior-authority and reason fields. |
| `CONFIRMED` platform candidate | `archived_confirmed_candidate`; candidate Run is `retained_video_workflow_run` | Remove candidate-only release meaning; do not change the Run. |
| `PREPARED`, `INITIALIZING`, `INITIALIZED`, or `PROVISIONAL` platform candidate | `abandoned_candidate_role`; candidate Run is `retained_video_workflow_run` when its live binding remains valid | Revoke candidate-only privilege. The Run continues only through ordinary Run and delivery authority. |
| Missing referenced Exit Evidence | `unavailable_at_migration` | Does not block retirement. It remains a historical limitation. |
| Existing referenced Exit Evidence | `available_at_migration` | Leave the evidence at its existing repository or local audit location. Do not copy it into the Profile. |
| Old JSON/SQLite conflict or corrupt database | `unresolved_retired_state` | Block automatic commit and emit a manual migration brief; do not initialize a replacement old store. |

The corrupt/conflicting case blocks because the migration cannot state what it preserved. Missing historical evidence alone does not block because the Profile has independent repository authority.

### 4. Archive as one file-family operation

The module moves each retired JSON and complete SQLite family, including `-wal`, `-shm`, or `-journal` sidecars when present, from its active location into a single local audit directory such as:

```text
workspace/.workflow-release-history/
  retired-cutover-authority/<migration-id>/
    original/
    retirement-record.json
```

Candidate Run directories and Exit Evidence files stay where they are. The audit directory is not `待删除`: it is retained historical material. If a later operator chooses cleanup, the repository deletion rule requires moving it into a scoped `待删除/` directory for manual deletion.

SQLite files must be captured only after exclusive access is proven. The module then reopens the archived databases read-only and verifies that the expected tables and classified rows remain readable. A failure leaves the retirement record `PREPARED` and blocks Tombstone publication.

### 5. Publish the Tombstone last

The module atomically publishes one Cutover Authority Tombstone at a canonical project-local path after every expected active path is absent and every archived file family is readable. The Tombstone records:

- schema and migration identity;
- terminal state `RETIRED`;
- bound Workflow Release Profile identity;
- audit-bundle path;
- capability set retired;
- disposition counts and any historical limitations;
- completion time.

Tombstone publication is the commit point. Before it exists, a repeated migration resumes from the `PREPARED` retirement record. After it exists, a repeated migration validates the same Profile binding and archive location and returns idempotent success. A different Profile binding or a re-created old active file is a conflict.

## Failure and recovery semantics

- Failure before any move leaves all old active state in place and the retirement record `PREPARED`.
- Failure after some moves resumes from the exact inventory; it never creates an empty old database and never calls old activation or refresh logic.
- Failure after all moves and before Tombstone publication reopens the archive, rechecks active-path absence, and publishes the same Tombstone.
- A Tombstone plus re-created old active state is `retired_authority_resurrected` and blocks old commands and migration until the conflicting files receive explicit operator disposition.
- A prepared old intent is never silently labeled committed. Its retirement disposition remains visible in the retirement record even when projected JSON bytes had already been written.
- A missing candidate Run binding is recorded as an unresolved historical limitation. It does not justify synthesizing a Run or changing the Profile.

The migration report is the only new local state machine. It must not add `ABORTED` values to old tables whose constraints accept only `PREPARED` and `COMMITTED`; disposition lives in the retirement record while the old database remains byte-preserved.

## Contract and documentation consequences

The proposed implementation handoff has four vertical slices for later human approval:

1. **Publish the Workflow Release Profile contract and release-maintenance interface**: materialize the repository-owned Profile, compatibility validation, candidate publication gate, and explicit release audit without historical-path reads during ordinary admission.
2. **Retire machine-local cutover authority state**: implement the deep migration interface, Retirement Record, audit bundle, Tombstone, old-command retirement check, and manual brief for corrupt or conflicting state.
3. **Replace ordinary startup with the Profile-backed `start-run` interface**: remove historical replay, duplicate platform `require_current`, candidate reconstruction, and new-task Legacy inference while preserving the live Control Store and final-quality lifecycle.
4. **Archive cutover commands and synchronize operator contracts**: remove migration-only commands after the supported migration window, retain explicit Legacy maintenance, update active skills/context/decision map/mirrors, and preserve historical Git and Exit Evidence.

The blocking order is `1 -> 2 -> 3 -> 4`. Slice 2 needs the Profile identity before it can retire old state. Slice 3 must not switch ordinary admission while old mutators can still race the migration commit. Slice 4 follows executable migration and startup replacement so operators retain one supported path throughout the transition.

No new test cases are part of this effort. Existing contract and integration checks may be updated or removed with the owning implementation slice, consistent with the map's standing constraint.

## Resulting map state

The migration decision makes the remaining implementation and documentation graph precise. No additional Wayfinder investigation is required before implementation ticket publication. The next action is a human-approved `/to-tickets` handoff using the four slices above, followed by implementation outside this planning map.

## Limitations

- The current-machine inventory is a dated local observation and may differ in another checkout.
- The exact JSON schemas and error payloads for the Profile, Retirement Record, and Tombstone belong to the implementation specification.
- This investigation did not mutate authority files, SQLite databases, candidate Runs, the Cross-Run Control Store, or historical evidence.
- No tests, policy checks, reconcile commands, refresh commands, activation commands, or qualification runs were executed.
