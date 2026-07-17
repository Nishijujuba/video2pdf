from __future__ import annotations

from datetime import datetime
import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import sqlite3
import stat
from typing import Any
import uuid

from .contracts import ContractRegistry
from .control_store import ControlStore, SCHEMA_VERSION
from .errors import ContractError, ControlStoreUnavailable, KernelError
from .utils import canonical_json_bytes, read_json, sha256_file, write_json_atomic


BACKUP_MANIFEST_NAME = "backup-manifest.json"
RECOVERY_SENTINEL_NAME = ".workflow-control-recovery.json"
_BACKUP_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_RESOURCE_CLASSES = (
    "bilibili_download",
    "youtube_download",
    "whisper",
    "codex_semantic",
    "latex",
    "pdf_render",
    "visual_acceptance",
)


def _is_link_or_reparse(path: Path) -> bool:
    try:
        result = path.lstat()
    except OSError:
        return False
    return stat.S_ISLNK(result.st_mode) or bool(
        getattr(result, "st_file_attributes", 0)
        & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _sqlite_primary_error_code(exc: sqlite3.Error) -> int | None:
    error_code = getattr(exc, "sqlite_errorcode", None)
    if not isinstance(error_code, int):
        return None
    return error_code & 0xFF


class RestoreInterruption(RuntimeError):
    """Test-only process boundary raised after durable restore state publication."""


class _RestoreQuiescenceUnavailable(ControlStoreUnavailable):
    """Retryable PREPARED restore failure with no live authority movement."""


class _RestoreInventoryCorrupt(ControlStoreUnavailable):
    """Confirmed live-store corruption eligible for atomic quarantine."""


class ControlStoreRecovery:
    """File-backed backup and restore coordinator outside live Kernel startup."""

    def __init__(
        self,
        workspace_root: Path,
        *,
        project_root: Path | None = None,
    ) -> None:
        requested_workspace_root = Path(os.path.abspath(workspace_root))
        if _is_link_or_reparse(requested_workspace_root):
            raise ControlStoreUnavailable(
                "Control Store recovery workspace is a link or reparse point",
                data={"evidence_path": str(requested_workspace_root)},
            )
        self.workspace_root = requested_workspace_root.resolve()
        self.project_root = (
            project_root.resolve()
            if project_root is not None
            else Path(__file__).resolve().parents[2]
        )
        self.contracts = ContractRegistry(self.project_root)
        self.sentinel_path = self.workspace_root / RECOVERY_SENTINEL_NAME

    def create_backup(
        self,
        store: ControlStore,
        backup_dir: Path,
        *,
        backup_id: str,
        coordinator_session_id: str,
        created_at: str,
    ) -> dict[str, Any]:
        self._validate_operation_inputs(
            backup_id=backup_id,
            coordinator_session_id=coordinator_session_id,
            created_at=created_at,
        )
        if store.workspace_root != self.workspace_root:
            raise ContractError(
                "Control Store backup coordinator and store workspace disagree"
            )
        backup_dir = backup_dir.resolve()
        if backup_dir == self.workspace_root or self.workspace_root in backup_dir.parents:
            raise ContractError(
                "Control Store backup must live outside the governed workspace"
            )
        backup_dir.mkdir(parents=True, exist_ok=False)
        operation_id = f"backup-{uuid.uuid4().hex}"
        sentinel = {
            "schema_name": "control-store-recovery-sentinel",
            "schema_version": "1.0.0",
            "kernel_version": "2.0.0",
            "operation_id": operation_id,
            "operation": "backup",
            "state": "QUIESCING",
            "backup_id": backup_id,
            "coordinator_session_id": coordinator_session_id,
            "created_at": created_at,
        }
        self._create_sentinel(sentinel)
        try:
            store.quiesce_writers()
            sentinel["state"] = "SNAPSHOTTING"
            write_json_atomic(self.sentinel_path, sentinel)

            database_path = backup_dir / "control.sqlite3"
            store.backup_to(database_path)
            marker = read_json(store.marker_path)
            anchor = read_json(store.anchor_path)
            self.contracts.validate("control-store-identity", marker)
            self.contracts.validate("control-store-identity", anchor)
            write_json_atomic(backup_dir / "control-store.json", marker)
            write_json_atomic(backup_dir / "anchor.json", anchor)
            candidate_health = ControlStore.validate_backup_candidate(
                self.workspace_root,
                self.contracts,
                backup_dir,
            )

            manifest = {
                "schema_name": "control-store-backup-manifest",
                "schema_version": "1.0.0",
                "kernel_version": "2.0.0",
                "backup_id": backup_id,
                "operation_id": operation_id,
                "created_at": created_at,
                "coordinator_session_id": coordinator_session_id,
                "snapshot_method": "sqlite_backup_api",
                "quiescence_proven": True,
                "store_id": store.store_id,
                "workspace_path": str(self.workspace_root),
                "control_store_schema_version": SCHEMA_VERSION,
                "candidate_integrity": {
                    "status": candidate_health.status,
                    "quick_check": candidate_health.quick_check,
                    "foreign_keys": "ok",
                    "exact_schema_and_semantics": "passed",
                },
                "active_resource_configuration": (
                    store.active_resource_configuration_identity()
                ),
                "run_authorities": list(store.run_authority_ids()),
                "artifacts": {
                    "database": {
                        "path": "control.sqlite3",
                        "sha256": sha256_file(database_path),
                    },
                    "marker": {
                        "path": "control-store.json",
                        "sha256": sha256_file(backup_dir / "control-store.json"),
                    },
                    "anchor": {
                        "path": "anchor.json",
                        "sha256": sha256_file(backup_dir / "anchor.json"),
                    },
                },
            }
            manifest_path = backup_dir / BACKUP_MANIFEST_NAME
            self.contracts.validate("control-store-backup-manifest", manifest)
            write_json_atomic(manifest_path, manifest)
            self._archive_completed_sentinel(operation_id, sentinel)
            return {
                "classification": "control_store_backup_complete",
                "backup_id": backup_id,
                "manifest_path": str(manifest_path),
                "database_path": str(database_path),
            }
        except BaseException:
            self._mark_sentinel_failed(sentinel)
            raise

    def restore_selected(
        self,
        backup_dir: Path,
        *,
        backup_id: str,
        coordinator_session_id: str,
        restored_at: str,
        fault_point: str | None = None,
    ) -> dict[str, Any]:
        """Restore one explicit backup before constructing an ordinary Kernel."""
        self._validate_operation_inputs(
            backup_id=backup_id,
            coordinator_session_id=coordinator_session_id,
            created_at=restored_at,
        )
        allowed_faults = {
            "after_prepared",
            "after_old_moved",
            "after_new_published",
            "after_validated",
            "after_reconciling",
            "after_committed",
            "after_state_record_before_sentinel",
        }
        if fault_point is not None and fault_point not in allowed_faults:
            raise ContractError("unknown Control Store restore fault point")
        self._fault_point = fault_point
        backup_dir = backup_dir.resolve()
        if backup_dir == self.workspace_root or self.workspace_root in backup_dir.parents:
            raise ContractError(
                "selected Control Store backup must live outside the governed workspace"
            )
        (
            manifest,
            validated_manifest_sha256,
            selected_backup_inventory,
        ) = self._validate_selected_backup(
            backup_dir,
            backup_id,
        )
        operation_id = f"restore-{uuid.uuid4().hex}"
        operation_dir = (
            self.workspace_root
            / "待删除"
            / "control-store-restores"
            / operation_id
        )
        prior_dir = operation_dir / "prior"
        staging_root = operation_dir / "staging"
        candidate_dir = staging_root / "candidate"
        for recovery_path, authority in (
            (operation_dir, "Control Store restore operation authority"),
            (prior_dir, "Control Store restore quarantine"),
            (staging_root, "Control Store restore staging root"),
            (candidate_dir, "Control Store restore staged candidate"),
        ):
            self._assert_controlled_recovery_path(
                recovery_path,
                authority=authority,
            )
        prior_dir.mkdir(parents=True, exist_ok=False)
        staging_root.mkdir(parents=False, exist_ok=False)
        self._materialize_staging_candidate(backup_dir, candidate_dir, manifest)
        self._assert_selected_backup_unchanged(
            backup_dir,
            manifest,
            validated_manifest_sha256,
        )

        sentinel = {
            "schema_name": "control-store-restore-sentinel",
            "schema_version": "1.0.0",
            "kernel_version": "2.0.0",
            "operation_id": operation_id,
            "operation": "restore",
            "state": "PREPARED",
            "backup_id": backup_id,
            "selected_backup_sha256": validated_manifest_sha256,
            "coordinator_session_id": coordinator_session_id,
            "created_at": restored_at,
            "recovery_token_epoch": 1,
            "recovery_token_sha256": "",
        }
        state_record: dict[str, Any] = {
            "schema_name": "control-store-restore-state",
            "schema_version": "1.0.0",
            "kernel_version": "2.0.0",
            "operation_id": operation_id,
            "backup_id": backup_id,
            "selected_backup_sha256": validated_manifest_sha256,
            "workspace_path": str(self.workspace_root),
            "backup_dir": str(backup_dir),
            "coordinator_session_id": coordinator_session_id,
            "restored_at": restored_at,
            "selected_manifest": manifest,
            "selected_backup_inventory": selected_backup_inventory,
            "writer_lock": None,
            "quarantined_artifacts": [],
            "quarantined_live_inventory": None,
            "resource_inventory_gaps": [],
            "recovery_token_epoch": 1,
            "recovery_token_sha256": "",
            "state": "PREPARED",
            "state_revision": 0,
            "state_history": [],
        }
        recovery_token = secrets.token_hex(32)
        recovery_token_sha256 = hashlib.sha256(
            recovery_token.encode("utf-8")
        ).hexdigest()
        state_record["recovery_token_sha256"] = recovery_token_sha256
        sentinel["recovery_token_sha256"] = recovery_token_sha256
        state_path = operation_dir / "restore-state.json"
        self._assert_controlled_recovery_path(
            state_path,
            authority="Control Store restore state authority",
        )
        self.contracts.validate("control-store-restore-state", state_record)
        write_json_atomic(state_path, state_record)
        sentinel.update(
            {
                "state_path": str(state_path),
                "state_revision": 0,
                "state_sha256": sha256_file(state_path),
            }
        )
        self.contracts.validate("control-store-restore-sentinel", sentinel)
        restore_lock = self._acquire_restore_lock(operation_dir)
        try:
            self._validate_staged_candidate(candidate_dir, manifest)
            self._create_sentinel(sentinel)
            self._advance_restore_state(
                state_record,
                operation_dir,
                sentinel,
                "PREPARED",
                restored_at,
            )
        except BaseException:
            self._release_restore_lock(restore_lock)
            raise
        try:
            live = ControlStore.__new__(ControlStore)
            live._configure(self.workspace_root, self.contracts)
            for recovery_path, authority in (
                (prior_dir, "Control Store restore quarantine"),
                (staging_root, "Control Store restore staging root"),
                (candidate_dir, "Control Store restore staged candidate"),
                (live.control_dir, "live Control Store authority"),
            ):
                self._assert_controlled_recovery_path(
                    recovery_path,
                    authority=authority,
                )
            if live.path.is_file():
                try:
                    self._quiesce_live_store(live)
                except _RestoreInventoryCorrupt as exc:
                    (
                        writer_lock,
                        quarantined_live_inventory,
                        resource_inventory_gaps,
                    ) = self._corrupt_live_inventory_evidence(live, exc)
                else:
                    try:
                        quarantined_live_inventory = (
                            self._resource_authority_inventory(
                                live,
                                status="readable",
                            )
                        )
                        resource_inventory_gaps = self._resource_inventory_gaps(
                            quarantined_live_inventory,
                            selected_backup_inventory,
                        )
                    except _RestoreInventoryCorrupt as exc:
                        (
                            writer_lock,
                            quarantined_live_inventory,
                            resource_inventory_gaps,
                        ) = self._corrupt_live_inventory_evidence(live, exc)
                    else:
                        writer_lock = "sqlite_exclusive"
            else:
                writer_lock = "canonical_database_absent"
                quarantined_live_inventory = self._empty_resource_inventory(
                    status="absent"
                )
                resource_inventory_gaps = self._resource_inventory_gaps(
                    quarantined_live_inventory,
                    selected_backup_inventory,
                )

            prior_control_dir = prior_dir / ".workflow-control"
            quarantined_artifacts = self._quarantine_inventory(live, prior_dir)
            state_record.update(
                {
                    "writer_lock": writer_lock,
                    "quarantined_artifacts": quarantined_artifacts,
                    "quarantined_live_inventory": quarantined_live_inventory,
                    "resource_inventory_gaps": resource_inventory_gaps,
                }
            )
            if live.control_dir.exists():
                os.replace(live.control_dir, prior_control_dir)
            if live.anchor_path.exists():
                os.replace(live.anchor_path, prior_dir / "anchor.json")
            self._advance_restore_state(
                state_record,
                operation_dir,
                sentinel,
                "OLD_MOVED",
                restored_at,
            )

            publish_control_dir = staging_root / "published-control"
            self._assert_controlled_recovery_path(
                publish_control_dir,
                authority="Control Store restore publication staging",
            )
            publish_control_dir.mkdir(parents=False, exist_ok=False)
            os.replace(
                candidate_dir / "control.sqlite3",
                publish_control_dir / "control.sqlite3",
            )
            os.replace(
                candidate_dir / "control-store.json",
                publish_control_dir / "control-store.json",
            )
            live.anchor_dir.mkdir(parents=True, exist_ok=True)
            os.replace(publish_control_dir, live.control_dir)
            os.replace(candidate_dir / "anchor.json", live.anchor_path)
            self._advance_restore_state(
                state_record,
                operation_dir,
                sentinel,
                "NEW_PUBLISHED",
                restored_at,
            )

            restored_store = ControlStore(
                self.workspace_root,
                self.contracts,
                recovery_operation_token=recovery_token,
            )
            published_health = restored_store.check()
            self._advance_restore_state(
                state_record,
                operation_dir,
                sentinel,
                "VALIDATED",
                restored_at,
            )

            gaps, filesystem_records = self._reconciliation_gaps(restored_store)
            gaps.extend(resource_inventory_gaps)
            self._advance_restore_state(
                state_record,
                operation_dir,
                sentinel,
                "RECONCILING",
                restored_at,
            )
            from .kernel import VideoWorkflowKernel

            kernel = VideoWorkflowKernel(
                self.workspace_root,
                _control_store_recovery_token=recovery_token,
            )
            reconciled: list[str] = []
            reconcile_failures: list[dict[str, Any]] = []
            blocked_authority_ids = {
                str(gap["authority_id"])
                for gap in gaps
                if gap.get("authority_kind") == "kernel_run"
            }
            eligible_records = {
                run_id: value
                for run_id, value in filesystem_records.items()
                if run_id in set(restored_store.run_authority_ids())
                and run_id not in blocked_authority_ids
            }
            for run_id in sorted(eligible_records):
                try:
                    kernel.reconcile_authority("kernel_run", run_id)
                except KernelError as exc:
                    reconcile_failures.append(
                        {
                            "classification": "authority_reconciliation_failed",
                            "authority_kind": "kernel_run",
                            "authority_id": run_id,
                            "error_classification": exc.classification,
                            "diagnostic": str(exc),
                        }
                    )
                else:
                    reconciled.append(run_id)
            lost_sessions = self._resource_reconciliation_lost_sessions(
                state_record["selected_backup_inventory"]
            )
            recovery_coordinator = f"control-store-{operation_id}"
            resource_result = kernel.resource_reconcile(
                current_coordinator_session_id=recovery_coordinator,
                lost_coordinator_session_ids=lost_sessions,
            )
            transitioned_lease_ids = (
                self._resource_reconciliation_transitioned_leases(
                    restored_store,
                    state_record["selected_backup_inventory"],
                    recovery_coordinator,
                )
            )
            if not set(resource_result["transitioned_lease_ids"]).issubset(
                transitioned_lease_ids
            ):
                raise ControlStoreUnavailable(
                    "Resource reconciliation returned an unbound Lease transition"
                )
            capacity_status = kernel.resource_capacity_status()["resources"]
            resource_recovery = {
                "current_coordinator_session_id": recovery_coordinator,
                "lost_coordinator_session_ids": sorted(lost_sessions),
                "transitioned_lease_ids": sorted(
                    transitioned_lease_ids
                ),
                "unknown_lease_ids": sorted(resource_result["unknown_lease_ids"]),
                "capacity_released": resource_result["capacity_released"],
                "resource_usage": {
                    resource_class: int(status["usage"])
                    for resource_class, status in sorted(capacity_status.items())
                },
                "selected_store_inventory": self._resource_authority_inventory(
                    restored_store,
                    status="readable",
                ),
                "quarantined_live_inventory": quarantined_live_inventory,
                "conservative_capacity_unresolved": bool(
                    resource_inventory_gaps
                ),
            }
            active_claim_gaps = [
                {
                    "classification": "active_claim_requires_manual_recovery",
                    "authority_kind": "kernel_run",
                    "authority_id": str(claim["authority_id"]),
                    "task_id": str(claim["task_id"]),
                    "attempt_id": str(claim["attempt_id"]),
                    "claim_generation": int(claim["claim_generation"]),
                    "claim_coordinator_session_id": str(
                        claim["coordinator_session_id"]
                    ),
                }
                for claim in restored_store.active_task_claims()
            ]
            final_gaps = [
                *gaps,
                *reconcile_failures,
                *self._post_reconciliation_gaps(restored_store, eligible_records),
                *active_claim_gaps,
            ]
            if final_gaps:
                return self._block_recovery(
                    operation_dir=operation_dir,
                    state_record=state_record,
                    sentinel=sentinel,
                    restored_at=restored_at,
                    manifest=manifest,
                    gaps=final_gaps,
                    reconciled_authorities=reconciled,
                    resource_recovery=resource_recovery,
                    quarantined_artifacts=quarantined_artifacts,
                    writer_lock=writer_lock,
                    published_health=published_health,
                )

            report = self._recovery_report(
                operation_id=operation_id,
                manifest=manifest,
                manifest_sha256=validated_manifest_sha256,
                writer_lock=writer_lock,
                quarantined_artifacts=quarantined_artifacts,
                published_health=published_health,
                reconciled_authorities=reconciled,
                resource_recovery=resource_recovery,
                unresolved_gaps=[],
                final_global_status="passed",
                reported_at=restored_at,
            )
            self.contracts.validate("control-store-recovery-report", report)
            report_path = (
                self.workspace_root
                / ".workflow-control"
                / "control_store_recovery_report.json"
            )
            self._assert_controlled_recovery_path(
                report_path,
                authority="Control Store recovery report",
            )
            write_json_atomic(report_path, report)
            state_record["recovery_report_path"] = str(report_path)
            state_record["recovery_report_sha256"] = sha256_file(report_path)
            sentinel["recovery_report_path"] = str(report_path)
            sentinel["recovery_report_sha256"] = state_record[
                "recovery_report_sha256"
            ]
            self._advance_restore_state(
                state_record,
                operation_dir,
                sentinel,
                "COMMITTED",
                restored_at,
            )
            return self._finalize_committed_restore(
                operation_id=operation_id,
                operation_dir=operation_dir,
            )
        except RestoreInterruption:
            raise
        except _RestoreQuiescenceUnavailable:
            raise
        except BaseException as exc:
            if (
                self.sentinel_path.exists()
                and state_record.get("state") != "COMMITTED"
            ):
                sentinel["failure"] = type(exc).__name__
                try:
                    self._advance_restore_state(
                        state_record,
                        operation_dir,
                        sentinel,
                        "BLOCKED",
                        restored_at,
                    )
                except BaseException:
                    sentinel["state"] = "BLOCKED"
                    write_json_atomic(self.sentinel_path, sentinel)
            raise
        finally:
            self._release_restore_lock(restore_lock)

    def resume_restore(
        self,
        *,
        operation_id: str,
        resumed_at: str,
    ) -> dict[str, Any]:
        """Resume one durable restore authority without constructing a normal Kernel."""
        if re.fullmatch(r"restore-[0-9a-f]{32}", operation_id) is None:
            raise ContractError("Control Store restore operation identity is invalid")
        self._validate_timestamp(resumed_at)
        operation_dir = (
            self.workspace_root
            / "待删除"
            / "control-store-restores"
            / operation_id
        )
        self._assert_controlled_recovery_path(
            operation_dir,
            authority="Control Store restore operation authority",
        )
        if not operation_dir.is_dir() or _is_link_or_reparse(operation_dir):
            raise ControlStoreUnavailable(
                "Control Store restore operation authority is unavailable",
                data={"operation_id": operation_id},
            )

        restore_lock = self._acquire_restore_lock(operation_dir)
        try:
            self._assert_controlled_recovery_path(
                self.sentinel_path,
                authority="active Control Store restore sentinel",
            )
            if _is_link_or_reparse(self.sentinel_path) or (
                self.sentinel_path.exists()
                and not self.sentinel_path.is_file()
            ):
                raise ControlStoreUnavailable(
                    "active Control Store restore sentinel is linked or non-file",
                    data={"evidence_path": str(self.sentinel_path)},
                )
            active = self.sentinel_path.is_file()
            sentinel_path = (
                self.sentinel_path
                if active
                else operation_dir / "sentinel.json"
            )
            sentinel, state_record = self._load_restore_authority(
                operation_id=operation_id,
                operation_dir=operation_dir,
                sentinel_path=sentinel_path,
                active=active,
            )
            if not active:
                if state_record["state"] != "COMMITTED":
                    raise ControlStoreUnavailable(
                        "archived Control Store restore authority is not committed"
                    )
                return self._completed_restore_result(
                    operation_dir,
                    state_record,
                    sentinel,
                )
            if state_record["state"] == "BLOCKED":
                raise ControlStoreUnavailable(
                    "blocked Control Store restore requires explicit manual recovery",
                    data={
                        "operation_id": operation_id,
                        "evidence_path": sentinel.get("recovery_report_path")
                        or str(self.sentinel_path),
                        "orphan_report_path": sentinel.get("orphan_report_path"),
                    },
                )

            recovery_token = self._rotate_recovery_token(
                state_record,
                operation_dir,
                sentinel,
            )
            while True:
                state = str(state_record["state"])
                if state == "PREPARED":
                    self._resume_move_old_store(state_record, operation_dir)
                    self._advance_restore_state(
                        state_record,
                        operation_dir,
                        sentinel,
                        "OLD_MOVED",
                        resumed_at,
                    )
                    continue
                if state == "OLD_MOVED":
                    self._resume_publish_selected_store(state_record, operation_dir)
                    self._advance_restore_state(
                        state_record,
                        operation_dir,
                        sentinel,
                        "NEW_PUBLISHED",
                        resumed_at,
                    )
                    continue
                if state == "NEW_PUBLISHED":
                    self._open_published_store(recovery_token).check()
                    self._advance_restore_state(
                        state_record,
                        operation_dir,
                        sentinel,
                        "VALIDATED",
                        resumed_at,
                    )
                    continue
                if state == "VALIDATED":
                    self._open_published_store(recovery_token).check()
                    self._advance_restore_state(
                        state_record,
                        operation_dir,
                        sentinel,
                        "RECONCILING",
                        resumed_at,
                    )
                    continue
                if state == "RECONCILING":
                    result = self._resume_reconcile_and_commit(
                        state_record=state_record,
                        operation_dir=operation_dir,
                        sentinel=sentinel,
                        recovery_token=recovery_token,
                        resumed_at=resumed_at,
                    )
                    if result["classification"] != "control_store_restore_complete":
                        return result
                    continue
                if state == "COMMITTED":
                    return self._finalize_committed_restore(
                        operation_id=operation_id,
                        operation_dir=operation_dir,
                    )
                raise ControlStoreUnavailable(
                    f"Control Store restore state cannot be resumed: {state}"
                )
        finally:
            self._release_restore_lock(restore_lock)

    def _resume_move_old_store(
        self,
        state_record: dict[str, Any],
        operation_dir: Path,
    ) -> None:
        candidate_dir = operation_dir / "staging" / "candidate"
        prior_dir = operation_dir / "prior"
        self._assert_controlled_recovery_path(
            prior_dir,
            authority="Control Store restore quarantine",
        )
        self._validate_staged_candidate(candidate_dir, state_record["selected_manifest"])
        unexpected_prior = {
            path.name
            for path in prior_dir.iterdir()
            if path.name not in {".workflow-control", "anchor.json"}
        }
        if unexpected_prior:
            raise ControlStoreUnavailable(
                "PREPARED restore quarantine contains unsupported artifacts"
            )
        live = ControlStore.__new__(ControlStore)
        live._configure(self.workspace_root, self.contracts)
        prior_control_dir = prior_dir / ".workflow-control"
        prior_anchor_path = prior_dir / "anchor.json"
        if (
            (prior_control_dir.exists() and live.control_dir.exists())
            or (prior_anchor_path.exists() and live.anchor_path.exists())
        ):
            raise ControlStoreUnavailable(
                "PREPARED restore duplicates live and quarantined authority"
            )
        inventory_store = live
        if prior_control_dir.exists():
            if _is_link_or_reparse(prior_control_dir) or not prior_control_dir.is_dir():
                raise ControlStoreUnavailable(
                    "PREPARED restore quarantine Control Store is invalid"
                )
            inventory_store = ControlStore.__new__(ControlStore)
            inventory_store._configure(self.workspace_root, self.contracts)
            inventory_store.control_dir = prior_control_dir
            inventory_store.path = prior_control_dir / "control.sqlite3"
            inventory_store.marker_path = prior_control_dir / "control-store.json"
        selected_backup_inventory = state_record["selected_backup_inventory"]
        if inventory_store.path.is_file():
            try:
                self._quiesce_live_store(inventory_store)
            except _RestoreInventoryCorrupt as exc:
                (
                    writer_lock,
                    quarantined_live_inventory,
                    resource_inventory_gaps,
                ) = self._corrupt_live_inventory_evidence(inventory_store, exc)
            else:
                try:
                    quarantined_live_inventory = (
                        self._resource_authority_inventory(
                            inventory_store,
                            status="readable",
                        )
                    )
                    resource_inventory_gaps = self._resource_inventory_gaps(
                        quarantined_live_inventory,
                        selected_backup_inventory,
                    )
                except _RestoreInventoryCorrupt as exc:
                    (
                        writer_lock,
                        quarantined_live_inventory,
                        resource_inventory_gaps,
                    ) = self._corrupt_live_inventory_evidence(
                        inventory_store,
                        exc,
                    )
                else:
                    writer_lock = "sqlite_exclusive"
        else:
            writer_lock = "canonical_database_absent"
            quarantined_live_inventory = self._empty_resource_inventory(
                status="absent"
            )
            resource_inventory_gaps = self._resource_inventory_gaps(
                quarantined_live_inventory,
                selected_backup_inventory,
            )

        quarantined_artifacts = self._quarantine_inventory_from_layout(
            live=live,
            prior_dir=prior_dir,
        )
        state_record.update(
            {
                "writer_lock": writer_lock,
                "quarantined_artifacts": quarantined_artifacts,
                "quarantined_live_inventory": quarantined_live_inventory,
                "resource_inventory_gaps": resource_inventory_gaps,
            }
        )
        if live.control_dir.exists() and not prior_control_dir.exists():
            os.replace(live.control_dir, prior_dir / ".workflow-control")
        if live.anchor_path.exists() and not prior_anchor_path.exists():
            os.replace(live.anchor_path, prior_dir / "anchor.json")

    def _quarantine_inventory_from_layout(
        self,
        *,
        live: ControlStore,
        prior_dir: Path,
    ) -> list[dict[str, Any]]:
        prior_control_dir = prior_dir / ".workflow-control"
        control_dir = (
            prior_control_dir
            if prior_control_dir.exists()
            else live.control_dir
        )
        inventory: list[dict[str, Any]] = []
        if control_dir.exists():
            if _is_link_or_reparse(control_dir) or not control_dir.is_dir():
                raise ControlStoreUnavailable(
                    "restore quarantine Control Store directory is invalid"
                )
            inventory.append(
                {
                    "artifact_kind": "control_directory",
                    "original_path": str(live.control_dir),
                    "quarantine_path": str(prior_control_dir),
                }
            )
            for path in sorted(control_dir.iterdir(), key=lambda item: item.name):
                if _is_link_or_reparse(path) or not path.is_file():
                    raise ControlStoreUnavailable(
                        "restore quarantine contains a linked or non-file artifact"
                    )
                if path.name == "control.sqlite3":
                    artifact_kind = "database"
                elif path.name == "control-store.json":
                    artifact_kind = "marker"
                elif path.name.startswith("control.sqlite3-"):
                    artifact_kind = "sqlite_sidecar"
                else:
                    artifact_kind = "control_metadata"
                inventory.append(
                    {
                        "artifact_kind": artifact_kind,
                        "original_path": str(live.control_dir / path.name),
                        "quarantine_path": str(prior_control_dir / path.name),
                        "sha256": sha256_file(path),
                    }
                )
        prior_anchor_path = prior_dir / "anchor.json"
        anchor_path = (
            prior_anchor_path
            if prior_anchor_path.exists()
            else live.anchor_path
        )
        if anchor_path.exists():
            if _is_link_or_reparse(anchor_path) or not anchor_path.is_file():
                raise ControlStoreUnavailable(
                    "restore quarantine anchor is invalid"
                )
            inventory.append(
                {
                    "artifact_kind": "anchor",
                    "original_path": str(live.anchor_path),
                    "quarantine_path": str(prior_anchor_path),
                    "sha256": sha256_file(anchor_path),
                }
            )
        return inventory

    def _resume_publish_selected_store(
        self,
        state_record: dict[str, Any],
        operation_dir: Path,
    ) -> None:
        candidate_dir = operation_dir / "staging" / "candidate"
        staging_root = operation_dir / "staging"
        live = ControlStore.__new__(ControlStore)
        live._configure(self.workspace_root, self.contracts)
        publish_control_dir = staging_root / "published-control"
        for recovery_path, authority in (
            (staging_root, "Control Store restore staging root"),
            (candidate_dir, "Control Store restore staged candidate"),
            (publish_control_dir, "Control Store restore publication staging"),
        ):
            self._assert_controlled_recovery_path(
                recovery_path,
                authority=authority,
            )
        locations = self._validate_partial_publication_layout(
            state_record=state_record,
            operation_dir=operation_dir,
        )
        if not live.control_dir.exists():
            if not publish_control_dir.exists():
                publish_control_dir.mkdir(parents=False, exist_ok=False)
            if locations["database"] == "candidate":
                os.replace(
                    candidate_dir / "control.sqlite3",
                    publish_control_dir / "control.sqlite3",
                )
            if locations["marker"] == "candidate":
                os.replace(
                    candidate_dir / "control-store.json",
                    publish_control_dir / "control-store.json",
                )
            if not (
                (publish_control_dir / "control.sqlite3").is_file()
                and (publish_control_dir / "control-store.json").is_file()
            ):
                raise ControlStoreUnavailable(
                    "OLD_MOVED restore cannot complete Control Store publication"
                )
            os.replace(publish_control_dir, live.control_dir)
        live.anchor_dir.mkdir(parents=True, exist_ok=True)
        if not live.anchor_path.exists():
            os.replace(candidate_dir / "anchor.json", live.anchor_path)
        self._validate_published_artifact_fingerprints(
            state_record["selected_manifest"],
            live,
        )

    def _validate_partial_publication_layout(
        self,
        *,
        state_record: dict[str, Any],
        operation_dir: Path,
    ) -> dict[str, str]:
        candidate_dir = operation_dir / "staging" / "candidate"
        publish_control_dir = operation_dir / "staging" / "published-control"
        live = ControlStore.__new__(ControlStore)
        live._configure(self.workspace_root, self.contracts)
        for recovery_path, authority in (
            (candidate_dir, "Control Store restore staged candidate"),
            (publish_control_dir, "Control Store restore publication staging"),
            (live.control_dir, "published Control Store authority"),
        ):
            self._assert_controlled_recovery_path(
                recovery_path,
                authority=authority,
            )
        for directory in (candidate_dir, publish_control_dir, live.control_dir):
            if directory.exists() and (
                _is_link_or_reparse(directory) or not directory.is_dir()
            ):
                raise ControlStoreUnavailable(
                    "OLD_MOVED restore publication directory is invalid"
                )
        if _is_link_or_reparse(live.anchor_path):
            raise ControlStoreUnavailable(
                "OLD_MOVED restore publication anchor is linked"
            )
        artifact_locations = {
            "database": (
                ("candidate", candidate_dir / "control.sqlite3"),
                ("publishing", publish_control_dir / "control.sqlite3"),
                ("live", live.path),
            ),
            "marker": (
                ("candidate", candidate_dir / "control-store.json"),
                ("publishing", publish_control_dir / "control-store.json"),
                ("live", live.marker_path),
            ),
            "anchor": (
                ("candidate", candidate_dir / "anchor.json"),
                ("live", live.anchor_path),
            ),
        }
        manifest = state_record["selected_manifest"]
        locations: dict[str, str] = {}
        for artifact_name, candidates in artifact_locations.items():
            present = [
                (location, path)
                for location, path in candidates
                if path.exists()
            ]
            if len(present) != 1:
                raise ControlStoreUnavailable(
                    f"OLD_MOVED restore has ambiguous {artifact_name} authority"
                )
            location, path = present[0]
            if not path.is_file() or _is_link_or_reparse(path):
                raise ControlStoreUnavailable(
                    f"OLD_MOVED restore {artifact_name} authority is invalid"
                )
            if not hmac.compare_digest(
                sha256_file(path),
                manifest["artifacts"][artifact_name]["sha256"],
            ):
                raise ControlStoreUnavailable(
                    f"OLD_MOVED restore {artifact_name} fingerprint drifted"
                )
            locations[artifact_name] = location
        if locations["database"] == "live" or locations["marker"] == "live":
            if not (
                locations["database"] == "live"
                and locations["marker"] == "live"
                and not publish_control_dir.exists()
            ):
                raise ControlStoreUnavailable(
                    "OLD_MOVED restore has a partial canonical control directory"
                )
        elif publish_control_dir.exists():
            if locations["database"] == "candidate" and (
                locations["marker"] == "publishing"
            ):
                raise ControlStoreUnavailable(
                    "OLD_MOVED restore publication order is impossible"
                )
        elif (
            locations["database"] != "candidate"
            or locations["marker"] != "candidate"
        ):
            raise ControlStoreUnavailable(
                "OLD_MOVED restore publication topology is impossible"
            )
        return locations

    @staticmethod
    def _validate_published_artifact_fingerprints(
        manifest: dict[str, Any],
        live: ControlStore,
    ) -> None:
        for artifact_name, path in {
            "database": live.path,
            "marker": live.marker_path,
            "anchor": live.anchor_path,
        }.items():
            if (
                not path.is_file()
                or _is_link_or_reparse(path)
                or not hmac.compare_digest(
                    sha256_file(path),
                    manifest["artifacts"][artifact_name]["sha256"],
                )
            ):
                raise ControlStoreUnavailable(
                    f"published Control Store {artifact_name} fingerprint drifted"
                )

    def _open_published_store(self, recovery_token: str) -> ControlStore:
        store = ControlStore(
            self.workspace_root,
            self.contracts,
            recovery_operation_token=recovery_token,
        )
        return store

    def _resume_reconcile_and_commit(
        self,
        *,
        state_record: dict[str, Any],
        operation_dir: Path,
        sentinel: dict[str, Any],
        recovery_token: str,
        resumed_at: str,
    ) -> dict[str, Any]:
        restored_store = self._open_published_store(recovery_token)
        published_health = restored_store.check()
        gaps, filesystem_records = self._reconciliation_gaps(restored_store)
        resource_inventory_gaps = list(state_record["resource_inventory_gaps"])
        gaps.extend(resource_inventory_gaps)

        from .kernel import VideoWorkflowKernel

        kernel = VideoWorkflowKernel(
            self.workspace_root,
            _control_store_recovery_token=recovery_token,
        )
        reconciled: list[str] = []
        reconcile_failures: list[dict[str, Any]] = []
        blocked_authority_ids = {
            str(gap["authority_id"])
            for gap in gaps
            if gap.get("authority_kind") == "kernel_run"
        }
        restored_authority_ids = set(restored_store.run_authority_ids())
        eligible_records = {
            run_id: value
            for run_id, value in filesystem_records.items()
            if run_id in restored_authority_ids
            and run_id not in blocked_authority_ids
        }
        for run_id in sorted(eligible_records):
            try:
                kernel.reconcile_authority("kernel_run", run_id)
            except KernelError as exc:
                reconcile_failures.append(
                    {
                        "classification": "authority_reconciliation_failed",
                        "authority_kind": "kernel_run",
                        "authority_id": run_id,
                        "error_classification": exc.classification,
                        "diagnostic": str(exc),
                    }
                )
            else:
                reconciled.append(run_id)
        lost_sessions = self._resource_reconciliation_lost_sessions(
            state_record["selected_backup_inventory"]
        )
        operation_id = str(state_record["operation_id"])
        recovery_coordinator = f"control-store-{operation_id}"
        resource_result = kernel.resource_reconcile(
            current_coordinator_session_id=recovery_coordinator,
            lost_coordinator_session_ids=lost_sessions,
        )
        transitioned_lease_ids = self._resource_reconciliation_transitioned_leases(
            restored_store,
            state_record["selected_backup_inventory"],
            recovery_coordinator,
        )
        if not set(resource_result["transitioned_lease_ids"]).issubset(
            transitioned_lease_ids
        ):
            raise ControlStoreUnavailable(
                "Resource reconciliation returned an unbound Lease transition"
            )
        capacity_status = kernel.resource_capacity_status()["resources"]
        quarantined_live_inventory = state_record["quarantined_live_inventory"]
        resource_recovery = {
            "current_coordinator_session_id": recovery_coordinator,
            "lost_coordinator_session_ids": sorted(lost_sessions),
            "transitioned_lease_ids": sorted(
                transitioned_lease_ids
            ),
            "unknown_lease_ids": sorted(resource_result["unknown_lease_ids"]),
            "capacity_released": resource_result["capacity_released"],
            "resource_usage": {
                resource_class: int(status["usage"])
                for resource_class, status in sorted(capacity_status.items())
            },
            "selected_store_inventory": self._resource_authority_inventory(
                restored_store,
                status="readable",
            ),
            "quarantined_live_inventory": quarantined_live_inventory,
            "conservative_capacity_unresolved": bool(resource_inventory_gaps),
        }
        active_claim_gaps = [
            {
                "classification": "active_claim_requires_manual_recovery",
                "authority_kind": "kernel_run",
                "authority_id": str(claim["authority_id"]),
                "task_id": str(claim["task_id"]),
                "attempt_id": str(claim["attempt_id"]),
                "claim_generation": int(claim["claim_generation"]),
                "claim_coordinator_session_id": str(
                    claim["coordinator_session_id"]
                ),
            }
            for claim in restored_store.active_task_claims()
        ]
        final_gaps = [
            *gaps,
            *reconcile_failures,
            *self._post_reconciliation_gaps(restored_store, eligible_records),
            *active_claim_gaps,
        ]
        manifest = state_record["selected_manifest"]
        quarantined_artifacts = list(state_record["quarantined_artifacts"])
        writer_lock = state_record["writer_lock"]
        if not isinstance(writer_lock, str):
            raise ControlStoreUnavailable(
                "RECONCILING restore has no durable writer-lock evidence"
            )
        if final_gaps:
            return self._block_recovery(
                operation_dir=operation_dir,
                state_record=state_record,
                sentinel=sentinel,
                restored_at=resumed_at,
                manifest=manifest,
                gaps=final_gaps,
                reconciled_authorities=reconciled,
                resource_recovery=resource_recovery,
                quarantined_artifacts=quarantined_artifacts,
                writer_lock=writer_lock,
                published_health=published_health,
            )

        report = self._recovery_report(
            operation_id=operation_id,
            manifest=manifest,
            manifest_sha256=str(state_record["selected_backup_sha256"]),
            writer_lock=writer_lock,
            quarantined_artifacts=quarantined_artifacts,
            published_health=published_health,
            reconciled_authorities=reconciled,
            resource_recovery=resource_recovery,
            unresolved_gaps=[],
            final_global_status="passed",
            reported_at=resumed_at,
        )
        self.contracts.validate("control-store-recovery-report", report)
        report_path = (
            self.workspace_root
            / ".workflow-control"
            / "control_store_recovery_report.json"
        )
        self._assert_controlled_recovery_path(
            report_path,
            authority="Control Store recovery report",
        )
        write_json_atomic(report_path, report)
        state_record["recovery_report_path"] = str(report_path)
        state_record["recovery_report_sha256"] = sha256_file(report_path)
        sentinel["recovery_report_path"] = str(report_path)
        sentinel["recovery_report_sha256"] = state_record[
            "recovery_report_sha256"
        ]
        self._advance_restore_state(
            state_record,
            operation_dir,
            sentinel,
            "COMMITTED",
            resumed_at,
        )
        return {
            "classification": "control_store_restore_complete",
            "backup_id": manifest["backup_id"],
            "operation_dir": str(operation_dir),
            "report_path": str(report_path),
            "reconciled_authorities": reconciled,
        }

    def _finalize_committed_restore(
        self,
        *,
        operation_id: str,
        operation_dir: Path,
    ) -> dict[str, Any]:
        """Validate persisted completion authority once, then archive its sentinel."""
        self._assert_controlled_recovery_path(
            operation_dir,
            authority="committed Control Store restore operation authority",
        )
        self._assert_controlled_recovery_path(
            self.sentinel_path,
            authority="committed Control Store restore sentinel",
        )
        sentinel, state_record = self._load_restore_authority(
            operation_id=operation_id,
            operation_dir=operation_dir,
            sentinel_path=self.sentinel_path,
            active=True,
        )
        if state_record["state"] != "COMMITTED":
            raise ControlStoreUnavailable(
                "Control Store restore cannot finalize before COMMITTED"
            )
        result = self._completed_restore_result(
            operation_dir,
            state_record,
            sentinel,
        )
        archive_path = operation_dir / "sentinel.json"
        self._assert_controlled_recovery_path(
            archive_path,
            authority="Control Store restore sentinel archive",
        )
        if archive_path.exists() or _is_link_or_reparse(archive_path):
            raise ControlStoreUnavailable(
                "Control Store restore sentinel archive already exists"
            )
        self._assert_controlled_recovery_path(
            self.sentinel_path,
            authority="committed Control Store restore sentinel",
        )
        os.replace(self.sentinel_path, archive_path)
        return result

    def _completed_restore_result(
        self,
        operation_dir: Path,
        state_record: dict[str, Any],
        sentinel: dict[str, Any],
    ) -> dict[str, Any]:
        report_path = (
            self.workspace_root
            / ".workflow-control"
            / "control_store_recovery_report.json"
        )
        try:
            recorded_report_authority = Path(
                str(state_record["recovery_report_path"])
            )
            self._assert_controlled_recovery_path(
                recorded_report_authority,
                authority="committed Control Store recovery report",
            )
            recorded_report_path = recorded_report_authority.resolve()
            recorded_report_sha256 = str(
                state_record["recovery_report_sha256"]
            )
        except (KeyError, OSError, TypeError, ValueError) as exc:
            raise ControlStoreUnavailable(
                "committed Control Store recovery report binding is absent"
            ) from exc
        if (
            state_record.get("state") != "COMMITTED"
            or sentinel.get("state") != "COMMITTED"
            or sentinel.get("operation_id") != state_record.get("operation_id")
            or sentinel.get("backup_id") != state_record.get("backup_id")
            or sentinel.get("recovery_report_path")
            != state_record.get("recovery_report_path")
            or sentinel.get("recovery_report_sha256")
            != state_record.get("recovery_report_sha256")
        ):
            raise ControlStoreUnavailable(
                "committed Control Store recovery report sentinel binding disagrees"
            )
        self._assert_controlled_recovery_path(
            report_path,
            authority="committed Control Store recovery report",
        )
        if (
            recorded_report_path != report_path.resolve()
            or not report_path.is_file()
            or _is_link_or_reparse(report_path)
            or not hmac.compare_digest(
                sha256_file(report_path),
                recorded_report_sha256,
            )
        ):
            raise ControlStoreUnavailable(
                "committed Control Store recovery report fingerprint drifted"
            )
        try:
            report = read_json(report_path)
            self.contracts.validate("control-store-recovery-report", report)
        except (OSError, json.JSONDecodeError, ContractError) as exc:
            raise ControlStoreUnavailable(
                f"committed Control Store recovery report is invalid: {exc}"
            ) from exc
        if (
            report.get("operation_id") != state_record["operation_id"]
            or report.get("final_global_status") != "passed"
            or report.get("selected_backup", {}).get("backup_id")
            != state_record["backup_id"]
        ):
            raise ControlStoreUnavailable(
                "committed Control Store recovery report authority is inconsistent"
            )
        reconciled = sorted(
            str(item["authority_id"])
            for item in report["reconciled_identities"]
        )
        return {
            "classification": "control_store_restore_complete",
            "backup_id": state_record["backup_id"],
            "operation_dir": str(operation_dir),
            "report_path": str(report_path),
            "reconciled_authorities": reconciled,
        }

    def diagnostic_status(self) -> dict[str, Any]:
        """Read external recovery authority without opening the replaceable database."""
        if not self.sentinel_path.exists():
            return {
                "classification": "control_store_recovery_idle",
                "state": "IDLE",
                "sentinel_path": str(self.sentinel_path),
            }
        try:
            sentinel = read_json(self.sentinel_path)
        except (OSError, json.JSONDecodeError) as exc:
            return {
                "classification": "control_store_recovery_blocked",
                "state": "UNREADABLE",
                "sentinel_path": str(self.sentinel_path),
                "diagnostic": str(exc),
            }
        if not isinstance(sentinel, dict):
            return {
                "classification": "control_store_recovery_blocked",
                "state": "INVALID",
                "sentinel_path": str(self.sentinel_path),
            }
        return {
            "classification": (
                "control_store_recovery_blocked"
                if sentinel.get("state") == "BLOCKED"
                else "control_store_recovery_active"
            ),
            "state": sentinel.get("state"),
            "operation": sentinel.get("operation"),
            "operation_id": sentinel.get("operation_id"),
            "backup_id": sentinel.get("backup_id"),
            "orphan_report_path": sentinel.get("orphan_report_path"),
            "recovery_report_path": sentinel.get("recovery_report_path"),
            "sentinel_path": str(self.sentinel_path),
        }

    def _validate_selected_backup(
        self,
        backup_dir: Path,
        backup_id: str,
    ) -> tuple[dict[str, Any], str, dict[str, Any]]:
        manifest_path = backup_dir / BACKUP_MANIFEST_NAME
        try:
            manifest_sha256 = sha256_file(manifest_path)
        except OSError as exc:
            raise ControlStoreUnavailable(
                f"selected Control Store backup manifest is unavailable: {exc}"
            ) from exc
        manifest = load_backup_manifest(backup_dir)
        if sha256_file(manifest_path) != manifest_sha256:
            raise ControlStoreUnavailable(
                "selected Control Store backup manifest changed during validation"
            )
        try:
            self.contracts.validate("control-store-backup-manifest", manifest)
        except ContractError as exc:
            raise ControlStoreUnavailable(
                f"selected Control Store backup manifest contract is invalid: {exc}"
            ) from exc
        if (
            manifest.get("schema_name") != "control-store-backup-manifest"
            or manifest.get("schema_version") != "1.0.0"
            or manifest.get("backup_id") != backup_id
            or manifest.get("snapshot_method") != "sqlite_backup_api"
            or manifest.get("quiescence_proven") is not True
            or manifest.get("control_store_schema_version") != SCHEMA_VERSION
        ):
            raise ControlStoreUnavailable(
                "selected Control Store backup manifest authority is invalid"
            )
        try:
            manifest_workspace = Path(str(manifest["workspace_path"])).resolve()
        except (KeyError, OSError, TypeError, ValueError) as exc:
            raise ControlStoreUnavailable(
                "selected Control Store backup workspace identity is invalid"
            ) from exc
        if manifest_workspace != self.workspace_root:
            raise ControlStoreUnavailable(
                "selected Control Store backup belongs to another workspace"
            )
        artifacts = manifest.get("artifacts")
        expected_paths = {
            "database": "control.sqlite3",
            "marker": "control-store.json",
            "anchor": "anchor.json",
        }
        if not isinstance(artifacts, dict) or set(artifacts) != set(expected_paths):
            raise ControlStoreUnavailable(
                "selected Control Store backup artifact inventory is invalid"
            )
        for name, relative_path in expected_paths.items():
            artifact = artifacts.get(name)
            if (
                not isinstance(artifact, dict)
                or set(artifact) != {"path", "sha256"}
                or artifact.get("path") != relative_path
            ):
                raise ControlStoreUnavailable(
                    "selected Control Store backup artifact binding is invalid"
                )
            path = backup_dir / relative_path
            if not path.is_file() or _is_link_or_reparse(path):
                raise ControlStoreUnavailable(
                    f"selected Control Store backup artifact is missing: {relative_path}"
                )
            if sha256_file(path) != artifact.get("sha256"):
                raise ControlStoreUnavailable(
                    f"selected Control Store backup artifact fingerprint differs: {relative_path}"
                )
        health = ControlStore.validate_backup_candidate(
            self.workspace_root,
            self.contracts,
            backup_dir,
        )
        if health.status != "ok" or health.schema_version != SCHEMA_VERSION:
            raise ControlStoreUnavailable(
                "selected Control Store backup candidate is unhealthy"
            )
        candidate = ControlStore.__new__(ControlStore)
        candidate._configure(self.workspace_root, self.contracts)
        candidate.control_dir = backup_dir
        candidate.path = backup_dir / "control.sqlite3"
        candidate.marker_path = backup_dir / "control-store.json"
        candidate.anchor_path = backup_dir / "anchor.json"
        if manifest["store_id"] != candidate.store_id:
            raise ControlStoreUnavailable(
                "selected Control Store backup store identity differs from authority"
            )
        if manifest["run_authorities"] != list(candidate.run_authority_ids()):
            raise ControlStoreUnavailable(
                "selected Control Store backup Run authority inventory differs from database"
            )
        if (
            manifest["active_resource_configuration"]
            != candidate.active_resource_configuration_identity()
        ):
            raise ControlStoreUnavailable(
                "selected Control Store backup Resource Admission Configuration differs from database"
            )
        selected_inventory = self._resource_authority_inventory(
            candidate,
            status="readable",
        )
        return manifest, manifest_sha256, selected_inventory

    @staticmethod
    def _empty_resource_inventory(*, status: str) -> dict[str, Any]:
        return {
            "status": status,
            "active_claims": [],
            "nonterminal_leases": [],
            "resource_usage": {
                resource_class: 0 for resource_class in _RESOURCE_CLASSES
            },
        }

    @staticmethod
    def _corrupt_live_inventory_evidence(
        store: ControlStore,
        exc: _RestoreInventoryCorrupt,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        return (
            "atomic_directory_quarantine",
            {
                "status": "unreadable_or_invalid",
                "active_claims": [],
                "nonterminal_leases": [],
                "resource_usage": None,
            },
            [
                {
                    "classification": (
                        "quarantined_live_resource_inventory_"
                        "unreadable_or_invalid"
                    ),
                    "authority_kind": "control_store",
                    "authority_id": store.store_id,
                    "diagnostic": f"{type(exc).__name__}: {exc}",
                }
            ],
        )

    @staticmethod
    def _quiesce_live_store(store: ControlStore) -> None:
        """Probe exclusive access while guaranteeing handle closure on corruption."""
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                f"file:{store.path.as_posix()}?mode=rw",
                uri=True,
                timeout=20,
                isolation_level=None,
            )
            connection.execute("PRAGMA busy_timeout=20000")
            connection.execute("BEGIN EXCLUSIVE")
            connection.execute("ROLLBACK")
        except sqlite3.Error as exc:
            if connection is not None and connection.in_transaction:
                try:
                    connection.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
            primary_code = _sqlite_primary_error_code(exc)
            data = {"sqlite_primary_error_code": primary_code}
            if primary_code in {
                sqlite3.SQLITE_CORRUPT,
                sqlite3.SQLITE_NOTADB,
            }:
                raise _RestoreInventoryCorrupt(
                    f"live Control Store SQLite corruption detected: {exc}",
                    data=data,
                ) from exc
            raise _RestoreQuiescenceUnavailable(
                f"live Control Store SQLite quiescence failed: {exc}",
                data=data,
            ) from exc
        except OSError as exc:
            if connection is not None and connection.in_transaction:
                try:
                    connection.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
            raise _RestoreQuiescenceUnavailable(
                f"live Control Store SQLite quiescence failed: {exc}"
            ) from exc
        finally:
            if connection is not None:
                connection.close()

    def _resource_authority_inventory(
        self,
        store: ControlStore,
        *,
        status: str,
    ) -> dict[str, Any]:
        """Read capacity-bearing authority from one SQLite snapshot."""
        connection: sqlite3.Connection | None = None
        try:
            connection = store._connect()
            connection.execute("BEGIN")
            claims = [
                {
                    "task_id": str(row["task_id"]),
                    "authority_id": str(row["authority_id"]),
                    "attempt_id": str(row["attempt_id"]),
                    "claim_generation": int(row["claim_generation"]),
                    "coordinator_session_id": str(row["coordinator_session_id"]),
                    "worker_id": str(row["worker_id"]),
                }
                for row in connection.execute(
                    "SELECT task_id, authority_id, attempt_id, claim_generation, "
                    "coordinator_session_id, worker_id FROM task_claims "
                    "WHERE state='ACTIVE' ORDER BY task_id"
                ).fetchall()
            ]
            lease_rows = connection.execute(
                "SELECT lease_id, task_id, attempt_id, claim_generation, "
                "coordinator_session_id, worker_id, state FROM resource_leases "
                "WHERE state IN ('starting','active','unknown') ORDER BY lease_id"
            ).fetchall()
            leases: list[dict[str, Any]] = []
            usage = {resource_class: 0 for resource_class in _RESOURCE_CLASSES}
            for row in lease_rows:
                resources = [
                    str(resource[0])
                    for resource in connection.execute(
                        "SELECT resource_class FROM resource_lease_resources "
                        "WHERE lease_id=? ORDER BY resource_class",
                        (row["lease_id"],),
                    ).fetchall()
                ]
                for resource_class in resources:
                    if resource_class not in usage:
                        raise _RestoreInventoryCorrupt(
                            "live Control Store has an unknown capacity-bearing Resource Class"
                        )
                    usage[resource_class] += 1
                leases.append(
                    {
                        "lease_id": str(row["lease_id"]),
                        "task_id": str(row["task_id"]),
                        "attempt_id": str(row["attempt_id"]),
                        "claim_generation": int(row["claim_generation"]),
                        "coordinator_session_id": str(
                            row["coordinator_session_id"]
                        ),
                        "worker_id": str(row["worker_id"]),
                        "state": str(row["state"]),
                        "required_resources": resources,
                    }
                )
            connection.execute("COMMIT")
        except _RestoreInventoryCorrupt:
            if connection is not None and connection.in_transaction:
                try:
                    connection.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
            raise
        except ControlStoreUnavailable as exc:
            if connection is not None and connection.in_transaction:
                try:
                    connection.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
            raise _RestoreQuiescenceUnavailable(
                "live Control Store Resource authority inventory access failed: "
                f"{exc}",
                data=exc.data,
            ) from exc
        except sqlite3.Error as exc:
            if connection is not None and connection.in_transaction:
                try:
                    connection.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
            primary_code = _sqlite_primary_error_code(exc)
            data = {"sqlite_primary_error_code": primary_code}
            if primary_code in {
                sqlite3.SQLITE_ERROR,
                sqlite3.SQLITE_CORRUPT,
                sqlite3.SQLITE_NOTADB,
                sqlite3.SQLITE_SCHEMA,
            }:
                raise _RestoreInventoryCorrupt(
                    "live Control Store Resource authority inventory is "
                    f"structurally invalid: {exc}",
                    data=data,
                ) from exc
            raise _RestoreQuiescenceUnavailable(
                "live Control Store Resource authority inventory access failed: "
                f"{exc}",
                data=data,
            ) from exc
        except (TypeError, ValueError) as exc:
            if connection is not None and connection.in_transaction:
                try:
                    connection.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
            raise _RestoreInventoryCorrupt(
                "live Control Store Resource authority inventory has invalid "
                f"row structure: {exc}"
            ) from exc
        except OSError as exc:
            if connection is not None and connection.in_transaction:
                try:
                    connection.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
            raise _RestoreQuiescenceUnavailable(
                "live Control Store Resource authority inventory access failed: "
                f"{exc}"
            ) from exc
        finally:
            if connection is not None:
                connection.close()
        return {
            "status": status,
            "active_claims": claims,
            "nonterminal_leases": leases,
            "resource_usage": usage,
        }

    @staticmethod
    def _resource_inventory_gaps(
        live_inventory: dict[str, Any],
        selected_inventory: dict[str, Any],
    ) -> list[dict[str, Any]]:
        gaps: list[dict[str, Any]] = []
        for collection, identity, authority_kind, live_only, selected_only, differs in (
            (
                "active_claims",
                "task_id",
                "task_claim",
                "quarantined_live_claim_not_in_selected_store",
                "selected_store_claim_not_in_quarantined_live",
                "quarantined_live_claim_differs_from_selected_store",
            ),
            (
                "nonterminal_leases",
                "lease_id",
                "resource_lease",
                "quarantined_live_lease_not_in_selected_store",
                "selected_store_lease_not_in_quarantined_live",
                "quarantined_live_lease_differs_from_selected_store",
            ),
        ):
            live = {str(item[identity]): item for item in live_inventory[collection]}
            selected = {
                str(item[identity]): item for item in selected_inventory[collection]
            }
            for authority_id in sorted(set(live) | set(selected)):
                if authority_id not in selected:
                    classification = live_only
                    item = live[authority_id]
                    difference = "quarantined_live_only"
                elif authority_id not in live:
                    classification = selected_only
                    item = selected[authority_id]
                    difference = "selected_store_only"
                elif live[authority_id] != selected[authority_id]:
                    classification = differs
                    item = live[authority_id]
                    difference = "identity_mismatch"
                else:
                    continue
                gap = {
                    "classification": classification,
                    "authority_kind": authority_kind,
                    "authority_id": authority_id,
                    "difference": difference,
                    "task_id": item["task_id"],
                    "attempt_id": item["attempt_id"],
                    "claim_generation": item["claim_generation"],
                    "claim_coordinator_session_id": item[
                        "coordinator_session_id"
                    ],
                }
                if authority_kind == "resource_lease":
                    gap.update(
                        {
                            "lease_id": item["lease_id"],
                            "lease_state": item["state"],
                            "required_resources": item["required_resources"],
                        }
                    )
                gaps.append(gap)
        return gaps

    @staticmethod
    def _resource_reconciliation_lost_sessions(
        selected_backup_inventory: dict[str, Any],
    ) -> tuple[str, ...]:
        if selected_backup_inventory.get("status") != "readable":
            raise ControlStoreUnavailable(
                "selected Resource reconciliation baseline is unreadable"
            )
        return tuple(
            sorted(
                {
                    str(lease["coordinator_session_id"])
                    for lease in selected_backup_inventory["nonterminal_leases"]
                    if lease["state"] in {"starting", "active"}
                }
            )
        )

    @staticmethod
    def _resource_reconciliation_transitioned_leases(
        store: ControlStore,
        selected_backup_inventory: dict[str, Any],
        recovery_coordinator: str,
    ) -> list[str]:
        baseline = {
            str(lease["lease_id"]): lease
            for lease in selected_backup_inventory["nonterminal_leases"]
            if lease["state"] in {"starting", "active"}
        }
        if not baseline:
            return []
        transitioned: list[str] = []
        connection = store._connect()
        try:
            for lease_id, expected in sorted(baseline.items()):
                current = connection.execute(
                    "SELECT state FROM resource_leases WHERE lease_id=?",
                    (lease_id,),
                ).fetchone()
                if current is None or str(current["state"]) != "unknown":
                    raise ControlStoreUnavailable(
                        "Resource reconciliation baseline Lease did not reach unknown"
                    )
                events = connection.execute(
                    "SELECT payload_json FROM resource_control_events "
                    "WHERE event_kind='lease_unknown' AND lease_id=? "
                    "ORDER BY event_seq",
                    (lease_id,),
                ).fetchall()
                matching_events = 0
                for event in events:
                    try:
                        payload = json.loads(str(event["payload_json"]))
                    except (TypeError, ValueError) as exc:
                        raise ControlStoreUnavailable(
                            "Resource reconciliation audit payload is invalid"
                        ) from exc
                    if payload == {
                        "current_coordinator_session_id": recovery_coordinator,
                        "lost_coordinator_session_id": str(
                            expected["coordinator_session_id"]
                        ),
                        "prior_worker_id": str(expected["worker_id"]),
                        "attempt_id": str(expected["attempt_id"]),
                        "claim_generation": int(expected["claim_generation"]),
                    }:
                        matching_events += 1
                if matching_events != 1:
                    raise ControlStoreUnavailable(
                        "Resource reconciliation transition lacks one exact audit event"
                    )
                transitioned.append(lease_id)
        finally:
            connection.close()
        return transitioned

    @staticmethod
    def _assert_selected_backup_unchanged(
        backup_dir: Path,
        manifest: dict[str, Any],
        manifest_sha256: str,
    ) -> None:
        try:
            if sha256_file(backup_dir / BACKUP_MANIFEST_NAME) != manifest_sha256:
                raise ControlStoreUnavailable(
                    "selected Control Store backup manifest changed before publication"
                )
            for artifact_name in ("database", "marker", "anchor"):
                artifact = manifest["artifacts"][artifact_name]
                if sha256_file(backup_dir / artifact["path"]) != artifact["sha256"]:
                    raise ControlStoreUnavailable(
                        "selected Control Store backup artifact changed before publication: "
                        f"{artifact['path']}"
                    )
        except OSError as exc:
            raise ControlStoreUnavailable(
                f"selected Control Store backup changed before publication: {exc}"
            ) from exc

    def _materialize_staging_candidate(
        self,
        backup_dir: Path,
        candidate_dir: Path,
        manifest: dict[str, Any],
    ) -> None:
        self._assert_controlled_recovery_path(
            candidate_dir,
            authority="Control Store restore staged candidate",
        )
        candidate_dir.mkdir(parents=True, exist_ok=False)
        self._assert_controlled_recovery_path(
            candidate_dir,
            authority="Control Store restore staged candidate",
        )
        source: sqlite3.Connection | None = None
        target: sqlite3.Connection | None = None
        try:
            source = sqlite3.connect(
                f"file:{(backup_dir / 'control.sqlite3').as_posix()}?mode=ro",
                uri=True,
                isolation_level=None,
            )
            target = sqlite3.connect(
                str(candidate_dir / "control.sqlite3"),
                isolation_level=None,
            )
            source.backup(target)
        except sqlite3.Error as exc:
            raise ControlStoreUnavailable(
                f"selected Control Store backup cannot be materialized: {exc}"
            ) from exc
        finally:
            if target is not None:
                target.close()
            if source is not None:
                source.close()
        write_json_atomic(
            candidate_dir / "control-store.json",
            read_json(backup_dir / "control-store.json"),
        )
        write_json_atomic(
            candidate_dir / "anchor.json",
            read_json(backup_dir / "anchor.json"),
        )
        for artifact_name, relative_path in {
            "database": "control.sqlite3",
            "marker": "control-store.json",
            "anchor": "anchor.json",
        }.items():
            expected_sha256 = manifest["artifacts"][artifact_name]["sha256"]
            actual_sha256 = sha256_file(candidate_dir / relative_path)
            if actual_sha256 != expected_sha256:
                raise ControlStoreUnavailable(
                    "staged Control Store candidate differs from the selected "
                    f"backup manifest: {relative_path}"
                )
        ControlStore.validate_backup_candidate(
            self.workspace_root,
            self.contracts,
            candidate_dir,
        )

    @staticmethod
    def _validate_timestamp(value: str) -> None:
        try:
            timestamp = datetime.fromisoformat(value)
        except (TypeError, ValueError) as exc:
            raise ContractError(
                "Control Store recovery timestamp must be ISO 8601"
            ) from exc
        if timestamp.tzinfo is None:
            raise ContractError(
                "Control Store recovery timestamp requires a timezone offset"
            )

    def _assert_controlled_recovery_path(
        self,
        path: Path,
        *,
        authority: str,
    ) -> Path:
        """Reject lexical escape and every existing link/reparse ancestor."""
        lexical_path = Path(os.path.abspath(path))
        try:
            relative = lexical_path.relative_to(self.workspace_root)
        except ValueError as exc:
            raise ControlStoreUnavailable(
                f"{authority} escapes the governed recovery workspace",
                data={"evidence_path": str(lexical_path)},
            ) from exc

        current = self.workspace_root
        controlled_paths = [current]
        for part in relative.parts:
            current = current / part
            controlled_paths.append(current)
        for controlled_path in controlled_paths:
            try:
                path_stat = controlled_path.lstat()
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise ControlStoreUnavailable(
                    f"{authority} cannot be inspected without following links",
                    data={"evidence_path": str(controlled_path)},
                ) from exc
            if stat.S_ISLNK(path_stat.st_mode) or bool(
                getattr(path_stat, "st_file_attributes", 0)
                & stat.FILE_ATTRIBUTE_REPARSE_POINT
            ):
                raise ControlStoreUnavailable(
                    f"{authority} traverses a link or reparse point",
                    data={"evidence_path": str(controlled_path)},
                )
        return lexical_path

    def _assert_open_lock_identity(self, lock_path: Path, handle: Any) -> None:
        self._assert_controlled_recovery_path(
            lock_path,
            authority="Control Store restore lock path",
        )
        try:
            path_stat = lock_path.lstat()
            handle_stat = os.fstat(handle.fileno())
        except OSError as exc:
            raise ControlStoreUnavailable(
                "Control Store restore lock identity cannot be verified",
                data={"evidence_path": str(lock_path)},
            ) from exc
        if (
            stat.S_ISLNK(path_stat.st_mode)
            or bool(
                getattr(path_stat, "st_file_attributes", 0)
                & stat.FILE_ATTRIBUTE_REPARSE_POINT
            )
            or not stat.S_ISREG(path_stat.st_mode)
            or not stat.S_ISREG(handle_stat.st_mode)
            or (path_stat.st_dev, path_stat.st_ino)
            != (handle_stat.st_dev, handle_stat.st_ino)
        ):
            raise ControlStoreUnavailable(
                "Control Store restore lock handle/path identity changed",
                data={"evidence_path": str(lock_path)},
            )

    def _acquire_restore_lock(self, operation_dir: Path) -> tuple[Any, str]:
        lock_path = operation_dir / "restore-operation.lock"
        self._assert_controlled_recovery_path(
            operation_dir,
            authority="Control Store restore operation authority",
        )
        self._assert_controlled_recovery_path(
            lock_path,
            authority="Control Store restore lock path",
        )
        if _is_link_or_reparse(lock_path) or (
            lock_path.exists() and not lock_path.is_file()
        ):
            raise ControlStoreUnavailable(
                "Control Store restore lock path is linked or non-file; "
                "reparse points are unsupported",
                data={
                    "operation_dir": str(operation_dir),
                    "evidence_path": str(lock_path),
                },
            )
        handle = lock_path.open("a+b")
        try:
            self._assert_open_lock_identity(lock_path, handle)
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
                os.fsync(handle.fileno())
            handle.seek(0)
            try:
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                backend = "msvcrt_byte_range"
            except ImportError:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                backend = "fcntl_flock"
            self._assert_open_lock_identity(lock_path, handle)
        except ControlStoreUnavailable:
            handle.close()
            raise
        except (OSError, BlockingIOError) as exc:
            handle.close()
            raise ControlStoreUnavailable(
                "another process owns the Control Store restore authority",
                data={
                    "operation_dir": str(operation_dir),
                    "evidence_path": str(self.sentinel_path),
                },
            ) from exc
        return handle, backend

    @staticmethod
    def _release_restore_lock(lock: tuple[Any, str]) -> None:
        handle, backend = lock
        try:
            handle.seek(0)
            if backend == "msvcrt_byte_range":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def _load_restore_authority(
        self,
        *,
        operation_id: str,
        operation_dir: Path,
        sentinel_path: Path,
        active: bool,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        self._assert_controlled_recovery_path(
            operation_dir,
            authority="Control Store restore operation authority",
        )
        self._assert_controlled_recovery_path(
            sentinel_path,
            authority="Control Store restore sentinel authority",
        )
        if (
            not sentinel_path.is_file()
            or _is_link_or_reparse(sentinel_path)
        ):
            raise ControlStoreUnavailable(
                "Control Store restore sentinel authority is unavailable",
                data={"evidence_path": str(sentinel_path)},
            )
        try:
            sentinel = read_json(sentinel_path)
            self.contracts.validate("control-store-restore-sentinel", sentinel)
        except (OSError, json.JSONDecodeError, ContractError) as exc:
            raise ControlStoreUnavailable(
                f"Control Store restore sentinel authority is invalid: {exc}",
                data={"evidence_path": str(sentinel_path)},
            ) from exc
        state_path = operation_dir / "restore-state.json"
        try:
            recorded_state_authority = Path(str(sentinel["state_path"]))
            self._assert_controlled_recovery_path(
                recorded_state_authority,
                authority="Control Store restore state authority",
            )
            recorded_state_path = recorded_state_authority.resolve()
        except (OSError, TypeError, ValueError) as exc:
            raise ControlStoreUnavailable(
                "Control Store restore state path authority is invalid"
            ) from exc
        if (
            recorded_state_path != state_path.resolve()
            or not state_path.is_file()
            or _is_link_or_reparse(state_path)
        ):
            raise ControlStoreUnavailable(
                "Control Store restore state topology is invalid"
            )
        try:
            state_record = read_json(state_path)
            self.contracts.validate("control-store-restore-state", state_record)
        except (OSError, json.JSONDecodeError, ContractError) as exc:
            raise ControlStoreUnavailable(
                f"Control Store restore state authority is invalid: {exc}"
            ) from exc
        immutable_pairs = (
            (sentinel.get("operation_id"), state_record.get("operation_id")),
            (sentinel.get("backup_id"), state_record.get("backup_id")),
            (
                sentinel.get("selected_backup_sha256"),
                state_record.get("selected_backup_sha256"),
            ),
            (
                sentinel.get("coordinator_session_id"),
                state_record.get("coordinator_session_id"),
            ),
            (sentinel.get("created_at"), state_record.get("restored_at")),
        )
        if any(left != right for left, right in immutable_pairs):
            raise ControlStoreUnavailable(
                "Control Store restore state and sentinel authority disagree"
            )
        if (
            state_record["operation_id"] != operation_id
            or Path(str(state_record["workspace_path"])).resolve()
            != self.workspace_root
        ):
            raise ControlStoreUnavailable(
                "Control Store restore operation/workspace authority is inconsistent"
            )
        history_states = [
            str(item["state"])
            for item in state_record["state_history"]
        ]
        progression = [
            "PREPARED",
            "OLD_MOVED",
            "NEW_PUBLISHED",
            "VALIDATED",
            "RECONCILING",
            "COMMITTED",
        ]
        current_state = str(state_record["state"])
        bootstrap_prepared = (
            current_state == "PREPARED"
            and history_states == []
            and state_record["state_revision"] == 0
            and state_record["recovery_token_epoch"] == 1
        )
        if current_state == "BLOCKED":
            if (
                not history_states
                or history_states[-1] != "BLOCKED"
                or history_states[:-1]
                != progression[: len(history_states) - 1]
            ):
                raise ControlStoreUnavailable(
                    "Control Store restore blocked history is impossible"
                )
        else:
            if current_state not in progression:
                raise ControlStoreUnavailable(
                    "Control Store restore state is unsupported"
                )
            expected_history = progression[: progression.index(current_state) + 1]
            if history_states != expected_history and not bootstrap_prepared:
                raise ControlStoreUnavailable(
                    "Control Store restore state history is non-contiguous"
                )
        expected_revision = (
            len(history_states)
            + int(state_record["recovery_token_epoch"])
            - 1
        )
        if int(state_record["state_revision"]) != expected_revision:
            raise ControlStoreUnavailable(
                "Control Store restore revision/token epoch relation is invalid"
            )
        actual_state_sha256 = sha256_file(state_path)
        dynamic_exact = (
            sentinel.get("state") == state_record.get("state")
            and sentinel.get("state_revision")
            == state_record.get("state_revision")
            and sentinel.get("recovery_token_epoch")
            == state_record.get("recovery_token_epoch")
            and sentinel.get("recovery_token_sha256")
            == state_record.get("recovery_token_sha256")
        )
        if dynamic_exact:
            if not hmac.compare_digest(
                actual_state_sha256,
                str(sentinel["state_sha256"]),
            ):
                raise ControlStoreUnavailable(
                    "Control Store restore state fingerprint disagrees with sentinel"
                )
        else:
            sentinel_state = str(sentinel.get("state"))
            sentinel_revision = sentinel.get("state_revision")
            sentinel_epoch = sentinel.get("recovery_token_epoch")
            state_revision = state_record.get("state_revision")
            state_epoch = state_record.get("recovery_token_epoch")
            next_state = {
                "PREPARED": "OLD_MOVED",
                "OLD_MOVED": "NEW_PUBLISHED",
                "NEW_PUBLISHED": "VALIDATED",
                "VALIDATED": "RECONCILING",
                "RECONCILING": "COMMITTED",
            }.get(sentinel_state)
            initial_prepared_write = (
                sentinel_state == "PREPARED"
                and sentinel_revision == 0
                and current_state == "PREPARED"
                and history_states == ["PREPARED"]
            )
            legal_state_write_ahead = (
                isinstance(sentinel_revision, int)
                and isinstance(state_revision, int)
                and state_revision == sentinel_revision + 1
                and state_epoch == sentinel_epoch
                and state_record.get("recovery_token_sha256")
                == sentinel.get("recovery_token_sha256")
                and (
                    initial_prepared_write
                    or current_state == next_state
                    or (
                        current_state == "BLOCKED"
                        and sentinel_state
                        in {
                            "PREPARED",
                            "OLD_MOVED",
                            "NEW_PUBLISHED",
                            "VALIDATED",
                            "RECONCILING",
                        }
                    )
                )
            )
            legal_token_rotation_ahead = (
                isinstance(sentinel_revision, int)
                and isinstance(state_revision, int)
                and isinstance(sentinel_epoch, int)
                and isinstance(state_epoch, int)
                and state_revision == sentinel_revision + 1
                and state_epoch == sentinel_epoch + 1
                and current_state == sentinel_state
                and state_record.get("recovery_token_sha256")
                != sentinel.get("recovery_token_sha256")
            )
            if (
                not active
                or not (
                    legal_state_write_ahead
                    or legal_token_rotation_ahead
                )
            ):
                raise ControlStoreUnavailable(
                    "Control Store restore state and sentinel authority disagree"
                )
            sentinel["state"] = current_state
            sentinel["state_revision"] = state_revision
            sentinel["recovery_token_epoch"] = state_epoch
            sentinel["recovery_token_sha256"] = state_record[
                "recovery_token_sha256"
            ]
            sentinel["state_sha256"] = actual_state_sha256
            if current_state in {"COMMITTED", "BLOCKED"}:
                report_fields = [
                    "recovery_report_path",
                    "recovery_report_sha256",
                ]
                if current_state == "BLOCKED":
                    report_fields.extend(
                        ["orphan_report_path", "orphan_report_sha256"]
                    )
                for field in report_fields:
                    if field in state_record:
                        sentinel[field] = state_record[field]
            self.contracts.validate("control-store-restore-sentinel", sentinel)
            write_json_atomic(self.sentinel_path, sentinel)
        self._validate_restore_topology(
            state_record=state_record,
            operation_dir=operation_dir,
        )
        if bootstrap_prepared:
            if not active:
                raise ControlStoreUnavailable(
                    "archived restore cannot contain an unstarted PREPARED authority"
                )
            self._advance_restore_state(
                state_record,
                operation_dir,
                sentinel,
                "PREPARED",
                str(state_record["restored_at"]),
            )
        if current_state == "BLOCKED":
            self._validate_blocked_report_bindings(
                state_record=state_record,
                sentinel=sentinel,
                operation_dir=operation_dir,
            )
        if active and sentinel_path.resolve() != self.sentinel_path.resolve():
            raise ControlStoreUnavailable(
                "active Control Store restore sentinel path is inconsistent"
            )
        return sentinel, state_record

    def _validate_blocked_report_bindings(
        self,
        *,
        state_record: dict[str, Any],
        sentinel: dict[str, Any],
        operation_dir: Path,
    ) -> None:
        report_path = (
            self.workspace_root
            / ".workflow-control"
            / "control_store_recovery_report.json"
        )
        if (
            Path(str(state_record["recovery_report_path"])).resolve()
            != report_path.resolve()
            or not report_path.is_file()
            or _is_link_or_reparse(report_path)
            or not hmac.compare_digest(
                sha256_file(report_path),
                str(state_record["recovery_report_sha256"]),
            )
        ):
            raise ControlStoreUnavailable(
                "blocked Control Store recovery report binding drifted"
            )
        try:
            report = read_json(report_path)
            self.contracts.validate("control-store-recovery-report", report)
        except (OSError, json.JSONDecodeError, ContractError) as exc:
            raise ControlStoreUnavailable(
                f"blocked Control Store recovery report is invalid: {exc}"
            ) from exc
        if (
            report.get("operation_id") != state_record["operation_id"]
            or report.get("final_global_status") != "blocked"
        ):
            raise ControlStoreUnavailable(
                "blocked Control Store recovery report authority disagrees"
            )
        for field in ("recovery_report_path", "recovery_report_sha256"):
            if sentinel.get(field) != state_record.get(field):
                raise ControlStoreUnavailable(
                    "blocked recovery report sentinel binding disagrees"
                )
        orphan_fields = (
            "orphan_report_path",
            "orphan_report_sha256",
        )
        has_orphan_binding = all(field in state_record for field in orphan_fields)
        if any(field in state_record for field in orphan_fields) != has_orphan_binding:
            raise ControlStoreUnavailable(
                "blocked orphan report binding is incomplete"
            )
        if not has_orphan_binding:
            if any(sentinel.get(field) is not None for field in orphan_fields):
                raise ControlStoreUnavailable(
                    "blocked sentinel has an unbound orphan report"
                )
            return
        orphan_path = operation_dir / "orphaned-filesystem-commit.json"
        if (
            Path(str(state_record["orphan_report_path"])).resolve()
            != orphan_path.resolve()
            or not orphan_path.is_file()
            or _is_link_or_reparse(orphan_path)
            or not hmac.compare_digest(
                sha256_file(orphan_path),
                str(state_record["orphan_report_sha256"]),
            )
        ):
            raise ControlStoreUnavailable(
                "blocked orphan report binding drifted"
            )
        try:
            orphan_report = read_json(orphan_path)
            self.contracts.validate(
                "orphaned-filesystem-commit-report",
                orphan_report,
            )
        except (OSError, json.JSONDecodeError, ContractError) as exc:
            raise ControlStoreUnavailable(
                f"blocked orphan report is invalid: {exc}"
            ) from exc
        if orphan_report.get("operation_id") != state_record["operation_id"]:
            raise ControlStoreUnavailable(
                "blocked orphan report authority disagrees"
            )
        for field in orphan_fields:
            if sentinel.get(field) != state_record.get(field):
                raise ControlStoreUnavailable(
                    "blocked orphan report sentinel binding disagrees"
                )

    def _validate_restore_topology(
        self,
        *,
        state_record: dict[str, Any],
        operation_dir: Path,
    ) -> None:
        state = str(state_record["state"])
        candidate_dir = operation_dir / "staging" / "candidate"
        prior_dir = operation_dir / "prior"
        live = ControlStore.__new__(ControlStore)
        live._configure(self.workspace_root, self.contracts)
        for recovery_path, authority in (
            (operation_dir, "Control Store restore operation authority"),
            (operation_dir / "restore-state.json", "Control Store restore state authority"),
            (operation_dir / "staging", "Control Store restore staging root"),
            (candidate_dir, "Control Store restore staged candidate"),
            (prior_dir, "Control Store restore quarantine"),
        ):
            self._assert_controlled_recovery_path(
                recovery_path,
                authority=authority,
            )
        if state == "PREPARED":
            self._validate_staged_candidate(
                candidate_dir,
                state_record["selected_manifest"],
            )
            unexpected_prior = {
                path.name
                for path in prior_dir.iterdir()
                if path.name not in {".workflow-control", "anchor.json"}
            }
            if unexpected_prior:
                raise ControlStoreUnavailable(
                    "PREPARED restore topology contains unsupported quarantine"
                )
            prior_control_dir = prior_dir / ".workflow-control"
            prior_anchor_path = prior_dir / "anchor.json"
            if (
                (prior_control_dir.exists() and live.control_dir.exists())
                or (prior_anchor_path.exists() and live.anchor_path.exists())
            ):
                raise ControlStoreUnavailable(
                    "PREPARED restore topology duplicates quarantined authority"
                )
            for path in (prior_control_dir, live.control_dir):
                if path.exists() and (_is_link_or_reparse(path) or not path.is_dir()):
                    raise ControlStoreUnavailable(
                        "PREPARED restore control-directory topology is invalid"
                    )
            for path in (prior_anchor_path, live.anchor_path):
                if path.exists() and (_is_link_or_reparse(path) or not path.is_file()):
                    raise ControlStoreUnavailable(
                        "PREPARED restore anchor topology is invalid"
                    )
            return
        if state == "OLD_MOVED":
            self._validate_partial_publication_layout(
                state_record=state_record,
                operation_dir=operation_dir,
            )
            return
        if state in {
            "NEW_PUBLISHED",
            "VALIDATED",
            "RECONCILING",
            "COMMITTED",
            "BLOCKED",
        }:
            if (
                not live.path.is_file()
                or not live.marker_path.is_file()
                or not live.anchor_path.is_file()
            ):
                raise ControlStoreUnavailable(
                    f"{state} restore topology has an incomplete published store"
                )
            if (operation_dir / "staging" / "published-control").exists():
                raise ControlStoreUnavailable(
                    f"{state} restore topology contains partial publication"
                )
            for name in ("control.sqlite3", "control-store.json", "anchor.json"):
                if (candidate_dir / name).exists():
                    raise ControlStoreUnavailable(
                        f"{state} restore topology retained staged artifact {name}"
                    )
            return
        raise ControlStoreUnavailable(
            f"Control Store restore topology has unsupported state: {state}"
        )

    def _validate_staged_candidate(
        self,
        candidate_dir: Path,
        manifest: dict[str, Any],
    ) -> None:
        self._assert_controlled_recovery_path(
            candidate_dir,
            authority="Control Store restore staged candidate",
        )
        if not candidate_dir.is_dir() or _is_link_or_reparse(candidate_dir):
            raise ControlStoreUnavailable(
                "staged Control Store restore candidate is unavailable"
            )
        for artifact_name, relative_path in {
            "database": "control.sqlite3",
            "marker": "control-store.json",
            "anchor": "anchor.json",
        }.items():
            path = candidate_dir / relative_path
            self._assert_controlled_recovery_path(
                path,
                authority="Control Store restore staged artifact",
            )
            if not path.is_file() or _is_link_or_reparse(path):
                raise ControlStoreUnavailable(
                    f"staged Control Store restore artifact is invalid: {relative_path}"
                )
            expected_sha256 = manifest["artifacts"][artifact_name]["sha256"]
            if not hmac.compare_digest(sha256_file(path), expected_sha256):
                raise ControlStoreUnavailable(
                    f"staged Control Store restore artifact drifted: {relative_path}"
                )
        ControlStore.validate_backup_candidate(
            self.workspace_root,
            self.contracts,
            candidate_dir,
        )

    def _rotate_recovery_token(
        self,
        state_record: dict[str, Any],
        operation_dir: Path,
        sentinel: dict[str, Any],
    ) -> str:
        recovery_token = secrets.token_hex(32)
        token_sha256 = hashlib.sha256(
            recovery_token.encode("utf-8")
        ).hexdigest()
        state_record["recovery_token_epoch"] = (
            int(state_record["recovery_token_epoch"]) + 1
        )
        state_record["recovery_token_sha256"] = token_sha256
        state_record["state_revision"] = int(state_record["state_revision"]) + 1
        state_path = operation_dir / "restore-state.json"
        self._assert_controlled_recovery_path(
            state_path,
            authority="Control Store restore state authority",
        )
        self._assert_controlled_recovery_path(
            self.sentinel_path,
            authority="active Control Store restore sentinel",
        )
        self.contracts.validate("control-store-restore-state", state_record)
        write_json_atomic(state_path, state_record)
        if getattr(self, "_fault_point", None) == (
            "after_token_state_record_before_sentinel"
        ):
            raise RestoreInterruption(
                "injected Control Store recovery-token rotation interruption"
            )
        sentinel["recovery_token_epoch"] = state_record["recovery_token_epoch"]
        sentinel["recovery_token_sha256"] = token_sha256
        sentinel["state_revision"] = state_record["state_revision"]
        sentinel["state_sha256"] = sha256_file(state_path)
        self.contracts.validate("control-store-restore-sentinel", sentinel)
        write_json_atomic(self.sentinel_path, sentinel)
        return recovery_token

    def _advance_restore_state(
        self,
        state_record: dict[str, Any],
        operation_dir: Path,
        sentinel: dict[str, Any],
        state: str,
        recorded_at: str,
    ) -> None:
        progression = {
            None: "PREPARED",
            "PREPARED": "OLD_MOVED",
            "OLD_MOVED": "NEW_PUBLISHED",
            "NEW_PUBLISHED": "VALIDATED",
            "VALIDATED": "RECONCILING",
            "RECONCILING": "COMMITTED",
        }
        history = state_record["state_history"]
        previous = None if not history else str(history[-1]["state"])
        if state == "BLOCKED":
            if previous in {None, "COMMITTED", "BLOCKED"}:
                raise ControlStoreUnavailable(
                    "Control Store restore cannot enter BLOCKED from this state"
                )
        elif progression.get(previous) != state:
            raise ControlStoreUnavailable(
                f"Control Store restore transition is invalid: {previous} -> {state}"
            )
        state_record["state"] = state
        state_record["state_revision"] = int(state_record["state_revision"]) + 1
        history.append({"state": state, "recorded_at": recorded_at})
        if int(state_record["state_revision"]) != (
            len(history) + int(state_record["recovery_token_epoch"]) - 1
        ):
            raise ControlStoreUnavailable(
                "Control Store restore state revision/token epoch lost continuity"
            )
        state_path = operation_dir / "restore-state.json"
        self._assert_controlled_recovery_path(
            state_path,
            authority="Control Store restore state authority",
        )
        self._assert_controlled_recovery_path(
            self.sentinel_path,
            authority="active Control Store restore sentinel",
        )
        self.contracts.validate("control-store-restore-state", state_record)
        write_json_atomic(state_path, state_record)
        if getattr(self, "_fault_point", None) == (
            "after_state_record_before_sentinel"
        ):
            raise RestoreInterruption(
                "injected Control Store restore state/sentinel interruption"
            )
        sentinel["state"] = state
        sentinel["state_path"] = str(state_path)
        sentinel["state_revision"] = state_record["state_revision"]
        sentinel["state_sha256"] = sha256_file(state_path)
        self.contracts.validate("control-store-restore-sentinel", sentinel)
        write_json_atomic(self.sentinel_path, sentinel)
        if getattr(self, "_fault_point", None) == f"after_{state.casefold()}":
            raise RestoreInterruption(
                f"injected Control Store restore interruption after {state}"
            )

    def _filesystem_run_records(self) -> dict[str, tuple[Path, dict[str, Any]]]:
        records: dict[str, tuple[Path, dict[str, Any]]] = {}
        for candidate in sorted(self.workspace_root.iterdir(), key=lambda path: path.name):
            if candidate.name in {".workflow-control", "待删除"}:
                continue
            record_path = candidate / "workflow" / "run.json"
            if not record_path.is_file():
                continue
            if _is_link_or_reparse(candidate) or _is_link_or_reparse(record_path):
                raise ControlStoreUnavailable(
                    "filesystem Run Record discovery encountered a linked path"
                )
            try:
                record = read_json(record_path)
                self.contracts.validate_run_record(record)
            except (OSError, json.JSONDecodeError, ContractError) as exc:
                raise ControlStoreUnavailable(
                    f"filesystem Run Record is invalid during restore: {record_path}: {exc}"
                ) from exc
            run_id = str(record["run_id"])
            if run_id in records:
                raise ControlStoreUnavailable(
                    f"filesystem Run authority is duplicated during restore: {run_id}"
                )
            records[run_id] = (candidate.resolve(), record)
        return records

    @staticmethod
    def _path_fingerprint_evidence(
        path: Path,
        *,
        display_path: str,
        expected_sha256: str | None,
        evidence_kind: str,
    ) -> dict[str, Any]:
        actual_sha256: str | None = None
        if _is_link_or_reparse(path) or (path.exists() and not path.is_file()):
            status = "linked_or_non_file"
        elif not path.is_file():
            status = "missing"
        else:
            actual_sha256 = sha256_file(path)
            if expected_sha256 is None:
                status = "available"
            elif actual_sha256 == expected_sha256:
                status = "matching"
            else:
                status = "hash_mismatch"
        return {
            "evidence_kind": evidence_kind,
            "path": display_path,
            "expected_sha256": expected_sha256,
            "actual_sha256": actual_sha256,
            "status": status,
        }

    @staticmethod
    def _canonical_artifact_evidence(
        run_dir: Path,
        artifact_generations: dict[str, Any],
    ) -> list[dict[str, Any]]:
        evidence: list[dict[str, Any]] = []
        for logical_id in sorted(artifact_generations):
            generation = artifact_generations[logical_id]
            relative = PurePosixPath(str(generation["path"]))
            candidate = run_dir.joinpath(*relative.parts)
            try:
                candidate.resolve(strict=False).relative_to(run_dir)
            except ValueError:
                actual_sha256 = None
                status = "path_escape"
            else:
                if _is_link_or_reparse(candidate) or (
                    candidate.exists() and not candidate.is_file()
                ):
                    actual_sha256 = None
                    status = "linked_or_non_file"
                elif not candidate.is_file():
                    actual_sha256 = None
                    status = "missing"
                else:
                    actual_sha256 = sha256_file(candidate)
                    status = (
                        "matching"
                        if actual_sha256 == generation["sha256"]
                        else "hash_mismatch"
                    )
            evidence.append(
                {
                    "logical_id": logical_id,
                    "path": str(generation["path"]),
                    "generation": int(generation["generation"]),
                    "expected_sha256": str(generation["sha256"]),
                    "actual_sha256": actual_sha256,
                    "status": status,
                }
            )
        return evidence

    @staticmethod
    def _restored_intent_matches(
        store: ControlStore,
        intent_id: str,
    ) -> list[dict[str, Any]]:
        matches: list[dict[str, Any]] = []
        queries = (
            (
                "initialization",
                "SELECT run_id, state FROM initialization_intents WHERE intent_id=?",
            ),
            (
                "run_state_mutation",
                "SELECT run_id, state FROM run_state_mutation_intents WHERE mutation_id=?",
            ),
            (
                "task_promotion",
                "SELECT run_id, state FROM task_promotion_intents WHERE intent_id=?",
            ),
        )
        connection = sqlite3.connect(
            f"file:{store.path.as_posix()}?mode=ro",
            uri=True,
        )
        connection.row_factory = sqlite3.Row
        try:
            for intent_kind, query in queries:
                row = connection.execute(query, (intent_id,)).fetchone()
                if row is not None:
                    matches.append(
                        {
                            "intent_kind": intent_kind,
                            "authority_id": str(row["run_id"]),
                            "state": str(row["state"]),
                        }
                    )
        finally:
            connection.close()
        return matches

    def _orphan_staging_and_preservation_evidence(
        self,
        run_dir: Path,
        *,
        missing_intent_id: str | None,
        missing_intent_kind: str,
        run_record_sha256: str,
    ) -> tuple[str, list[dict[str, Any]], str, list[dict[str, Any]]]:
        if missing_intent_kind == "initialization":
            candidates = sorted(
                run_dir.glob("*/bootstrap/prepared-run.json"),
                key=lambda item: item.as_posix(),
            )
            staging = [
                self._path_fingerprint_evidence(
                    candidate,
                    display_path=candidate.relative_to(run_dir).as_posix(),
                    expected_sha256=run_record_sha256,
                    evidence_kind="initialization_prepared_run",
                )
                for candidate in candidates
            ]
            return (
                "available" if staging else "unavailable",
                staging,
                "not_applicable",
                [],
            )

        if missing_intent_id is None:
            return "unavailable", [], "unavailable", []

        matching_journal: tuple[Path, dict[str, Any]] | None = None
        for journal_path in sorted(
            run_dir.glob("workflow/tasks/*/attempts/*/p.json"),
            key=lambda item: item.as_posix(),
        ):
            if _is_link_or_reparse(journal_path) or not journal_path.is_file():
                continue
            try:
                journal = read_json(journal_path)
                self.contracts.validate("task-promotion-journal", journal)
            except (OSError, json.JSONDecodeError, ContractError):
                continue
            if journal.get("intent_id") == missing_intent_id:
                matching_journal = (journal_path, journal)
                break
        if matching_journal is None:
            return "unavailable", [], "unavailable", []

        journal_path, journal = matching_journal
        staging = [
            self._path_fingerprint_evidence(
                journal_path,
                display_path=journal_path.relative_to(run_dir).as_posix(),
                expected_sha256=None,
                evidence_kind="task_promotion_journal",
            )
        ]
        preservation: list[dict[str, Any]] = []
        required_preservation_count = 0
        for output in journal["outputs"]:
            attempt_relative = PurePosixPath(str(output["attempt_path"]))
            attempt_output = journal_path.parent.joinpath(*attempt_relative.parts)
            staging.append(
                self._path_fingerprint_evidence(
                    attempt_output,
                    display_path=attempt_output.relative_to(run_dir).as_posix(),
                    expected_sha256=str(output["sha256"]),
                    evidence_kind="task_attempt_output",
                )
            )
            prior_sha256 = output.get("prior_sha256")
            if prior_sha256 is None:
                continue
            required_preservation_count += 1
            preservation_relative = PurePosixPath(str(output["preservation_path"]))
            preservation_path = run_dir.joinpath(*preservation_relative.parts)
            preservation.append(
                self._path_fingerprint_evidence(
                    preservation_path,
                    display_path=str(output["preservation_path"]),
                    expected_sha256=str(prior_sha256),
                    evidence_kind="prior_artifact_generation",
                )
            )
        staging_status = (
            "available"
            if all(item["status"] in {"available", "matching"} for item in staging)
            else "partial"
        )
        if required_preservation_count == 0:
            preservation_status = "not_required"
        elif all(item["status"] == "matching" for item in preservation):
            preservation_status = "complete"
        else:
            preservation_status = "incomplete"
        return staging_status, staging, preservation_status, preservation

    def _orphan_gap_evidence(
        self,
        store: ControlStore,
        run_dir: Path,
        record: dict[str, Any],
        *,
        run_record_sha256: str,
    ) -> dict[str, Any]:
        subsequent_intent_id = record.get("last_mutation_intent_id")
        if isinstance(subsequent_intent_id, str) and subsequent_intent_id:
            missing_intent_kind = "subsequent_mutation"
            missing_intent_id: str | None = subsequent_intent_id
        elif int(record["coordination_revision"]) == 1:
            missing_intent_kind = "initialization"
            missing_intent_id = str(record["initialization_intent_id"])
        else:
            missing_intent_kind = "unavailable_from_coordination_record"
            missing_intent_id = None
        restored_matches = (
            []
            if missing_intent_id is None
            else self._restored_intent_matches(store, missing_intent_id)
        )
        if missing_intent_id is None:
            authority_status = "not_provable_from_coordination_record"
        elif not restored_matches:
            authority_status = "absent_from_selected_store"
        elif len(restored_matches) == 1:
            authority_status = "present_in_selected_store"
        else:
            authority_status = "ambiguous_in_selected_store"
        artifact_generations = json.loads(
            json.dumps(record["artifact_generations"], ensure_ascii=False)
        )
        (
            staging_status,
            staging_evidence,
            preservation_status,
            preservation_evidence,
        ) = self._orphan_staging_and_preservation_evidence(
            run_dir,
            missing_intent_id=missing_intent_id,
            missing_intent_kind=missing_intent_kind,
            run_record_sha256=run_record_sha256,
        )
        return {
            "coordination_record_path": "workflow/run.json",
            "coordination_revision": int(record["coordination_revision"]),
            "missing_intent_id": missing_intent_id,
            "missing_intent_kind": missing_intent_kind,
            "intent_authority_status": authority_status,
            "restored_intent_matches": restored_matches,
            "run_record_sha256": run_record_sha256,
            "artifact_generations": artifact_generations,
            "canonical_artifacts": self._canonical_artifact_evidence(
                run_dir,
                artifact_generations,
            ),
            "staging_evidence_status": staging_status,
            "staging_evidence": staging_evidence,
            "preservation_status": preservation_status,
            "preservation_evidence": preservation_evidence,
        }

    def _reconciliation_gaps(
        self,
        store: ControlStore,
    ) -> tuple[list[dict[str, Any]], dict[str, tuple[Path, dict[str, Any]]]]:
        records = self._filesystem_run_records()
        database_ids = set(store.run_authority_ids())
        filesystem_ids = set(records)
        gaps: list[dict[str, Any]] = []
        for run_id in sorted(filesystem_ids - database_ids):
            run_dir, record = records[run_id]
            actual_sha = sha256_file(run_dir / "workflow" / "run.json")
            gaps.append(
                {
                    "classification": "orphaned_filesystem_commit",
                    "authority_kind": "kernel_run",
                    "authority_id": run_id,
                    "run_dir": str(run_dir),
                    **self._orphan_gap_evidence(
                        store,
                        run_dir,
                        record,
                        run_record_sha256=actual_sha,
                    ),
                }
            )
        for run_id in sorted(database_ids - filesystem_ids):
            binding = store.binding_for_run(run_id)
            gaps.append(
                {
                    "classification": "missing_filesystem_authority",
                    "authority_kind": "kernel_run",
                    "authority_id": run_id,
                    "bound_output_path": None
                    if binding is None
                    else str(binding["output_path"]),
                }
            )
        for run_id in sorted(database_ids & filesystem_ids):
            run_dir, _record = records[run_id]
            binding = store.binding_for_run(run_id)
            if binding is None or Path(str(binding["output_path"])).resolve() != run_dir:
                gaps.append(
                    {
                        "classification": "authority_binding_contradiction",
                        "authority_kind": "kernel_run",
                        "authority_id": run_id,
                        "run_dir": str(run_dir),
                        "bound_output_path": None
                        if binding is None
                        else str(binding["output_path"]),
                    }
                )
        return gaps, records

    def _post_reconciliation_gaps(
        self,
        store: ControlStore,
        records: dict[str, tuple[Path, dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        gaps: list[dict[str, Any]] = []
        for run_id in sorted(records):
            run_dir, _discovered_record = records[run_id]
            record_path = run_dir / "workflow" / "run.json"
            record = read_json(record_path)
            self.contracts.validate_run_record(record)
            if record.get("run_id") != run_id:
                raise ControlStoreUnavailable(
                    "filesystem Run authority identity changed during reconciliation"
                )
            actual_sha = sha256_file(record_path)
            authority_sha = store.current_run_record_sha(run_id)
            initialization = store.intent_for_run(run_id)
            initialization_state = (
                None if initialization is None else str(initialization["state"])
            )
            if authority_sha == actual_sha and initialization_state == "COMMITTED":
                continue
            gaps.append(
                {
                    "classification": "orphaned_filesystem_commit",
                    "authority_kind": "kernel_run",
                    "authority_id": run_id,
                    "run_dir": str(run_dir),
                    **self._orphan_gap_evidence(
                        store,
                        run_dir,
                        record,
                        run_record_sha256=actual_sha,
                    ),
                    "restored_authority_sha256": authority_sha,
                    "initialization_state": initialization_state,
                }
            )
        return gaps

    def _quarantine_inventory(
        self,
        live: ControlStore,
        prior_dir: Path,
    ) -> list[dict[str, Any]]:
        inventory: list[dict[str, Any]] = []
        if live.control_dir.exists():
            inventory.append(
                {
                    "artifact_kind": "control_directory",
                    "original_path": str(live.control_dir),
                    "quarantine_path": str(prior_dir / ".workflow-control"),
                }
            )
            for path in sorted(live.control_dir.iterdir(), key=lambda item: item.name):
                if _is_link_or_reparse(path) or not path.is_file():
                    raise ControlStoreUnavailable(
                        "live Control Store contains an unsupported linked or non-file artifact"
                    )
                if path.name == "control.sqlite3":
                    artifact_kind = "database"
                elif path.name == "control-store.json":
                    artifact_kind = "marker"
                elif path.name.startswith("control.sqlite3-"):
                    artifact_kind = "sqlite_sidecar"
                else:
                    artifact_kind = "control_metadata"
                inventory.append(
                    {
                        "artifact_kind": artifact_kind,
                        "original_path": str(path),
                        "quarantine_path": str(
                            prior_dir / ".workflow-control" / path.name
                        ),
                        "sha256": sha256_file(path),
                    }
                )
        if live.anchor_path.exists():
            if _is_link_or_reparse(live.anchor_path) or not live.anchor_path.is_file():
                raise ControlStoreUnavailable(
                    "live Control Store anchor is linked or invalid"
                )
            inventory.append(
                {
                    "artifact_kind": "anchor",
                    "original_path": str(live.anchor_path),
                    "quarantine_path": str(prior_dir / "anchor.json"),
                    "sha256": sha256_file(live.anchor_path),
                }
            )
        return inventory

    def _recovery_report(
        self,
        *,
        operation_id: str,
        manifest: dict[str, Any],
        manifest_sha256: str,
        writer_lock: str,
        quarantined_artifacts: list[dict[str, Any]],
        published_health: Any,
        reconciled_authorities: list[str],
        resource_recovery: dict[str, Any],
        unresolved_gaps: list[dict[str, Any]],
        final_global_status: str,
        reported_at: str,
    ) -> dict[str, Any]:
        if reconciled_authorities != sorted(set(reconciled_authorities)):
            raise ControlStoreUnavailable(
                "Control Store recovery reconciled authority inventory is unstable"
            )
        if (
            final_global_status == "passed"
            and reconciled_authorities != manifest["run_authorities"]
        ):
            raise ControlStoreUnavailable(
                "passing Control Store recovery omitted a restored Run authority"
            )
        for field in (
            "lost_coordinator_session_ids",
            "transitioned_lease_ids",
            "unknown_lease_ids",
        ):
            values = resource_recovery.get(field)
            if not isinstance(values, list) or values != sorted(set(values)):
                raise ControlStoreUnavailable(
                    f"Control Store recovery {field} inventory is unstable"
                )
        integrity_result = {
            "status": "passed",
            "quick_check": "ok",
            "foreign_keys": "ok",
            "exact_schema_and_semantics": "passed",
        }
        return {
            "schema_name": "control-store-recovery-report",
            "schema_version": "1.0.0",
            "kernel_version": "2.0.0",
            "operation_id": operation_id,
            "selected_backup": {
                "backup_id": manifest["backup_id"],
                "manifest_sha256": manifest_sha256,
                "database_sha256": manifest["artifacts"]["database"]["sha256"],
                "store_id": manifest["store_id"],
                "schema_version": manifest["control_store_schema_version"],
            },
            "quiescence": {
                "status": "passed",
                "writer_lock": writer_lock,
                "sentinel_path": str(self.sentinel_path),
            },
            "quarantined_artifacts": quarantined_artifacts,
            "integrity_results": {
                "selected_candidate": dict(integrity_result),
                "staged_candidate": dict(integrity_result),
                "published_store": {
                    **integrity_result,
                    "quick_check": str(published_health.quick_check),
                },
            },
            "schema_validation": {
                "selected_version": manifest["control_store_schema_version"],
                "required_version": SCHEMA_VERSION,
                "validation": "exact_match",
                "migration": "rejected_not_performed",
            },
            "reconciled_identities": [
                {
                    "authority_kind": "kernel_run",
                    "authority_id": run_id,
                    "outcome": "reconciled",
                }
                for run_id in reconciled_authorities
            ],
            "resource_recovery": resource_recovery,
            "unresolved_gaps": unresolved_gaps,
            "final_global_status": final_global_status,
            "global_mutation_blocked": final_global_status == "blocked",
            "unblock_requires_sentinel_absent": True,
            "reported_at": reported_at,
        }

    def _block_recovery(
        self,
        *,
        operation_dir: Path,
        state_record: dict[str, Any],
        sentinel: dict[str, Any],
        restored_at: str,
        manifest: dict[str, Any],
        gaps: list[dict[str, Any]],
        reconciled_authorities: list[str],
        resource_recovery: dict[str, Any],
        quarantined_artifacts: list[dict[str, Any]],
        writer_lock: str,
        published_health: Any,
    ) -> dict[str, Any]:
        has_orphaned_filesystem_commit = any(
            gap.get("classification") == "orphaned_filesystem_commit"
            for gap in gaps
        )
        orphan_path: Path | None = None
        if has_orphaned_filesystem_commit:
            orphan_report = {
                "schema_name": "orphaned-filesystem-commit-report",
                "schema_version": "1.0.0",
                "kernel_version": "2.0.0",
                "operation_id": state_record["operation_id"],
                "selected_backup_id": manifest["backup_id"],
                "classification": "orphaned_filesystem_commit",
                "gaps": gaps,
                "global_mutation_blocked": True,
                "manual_recovery_required": True,
            }
            self.contracts.validate(
                "orphaned-filesystem-commit-report",
                orphan_report,
            )
            orphan_path = operation_dir / "orphaned-filesystem-commit.json"
            self._assert_controlled_recovery_path(
                orphan_path,
                authority="orphaned filesystem commit report",
            )
            write_json_atomic(orphan_path, orphan_report)
        report_path = (
            self.workspace_root
            / ".workflow-control"
            / "control_store_recovery_report.json"
        )
        report = self._recovery_report(
            operation_id=str(state_record["operation_id"]),
            manifest=manifest,
            manifest_sha256=str(state_record["selected_backup_sha256"]),
            writer_lock=writer_lock,
            quarantined_artifacts=quarantined_artifacts,
            published_health=published_health,
            reconciled_authorities=reconciled_authorities,
            resource_recovery=resource_recovery or {},
            unresolved_gaps=gaps,
            final_global_status="blocked",
            reported_at=restored_at,
        )
        self.contracts.validate("control-store-recovery-report", report)
        self._assert_controlled_recovery_path(
            report_path,
            authority="Control Store recovery report",
        )
        write_json_atomic(report_path, report)
        state_record["recovery_report_path"] = str(report_path)
        state_record["recovery_report_sha256"] = sha256_file(report_path)
        sentinel["recovery_report_path"] = str(report_path)
        sentinel["recovery_report_sha256"] = state_record[
            "recovery_report_sha256"
        ]
        if orphan_path is not None:
            state_record["orphan_report_path"] = str(orphan_path)
            state_record["orphan_report_sha256"] = sha256_file(orphan_path)
            sentinel["orphan_report_path"] = str(orphan_path)
            sentinel["orphan_report_sha256"] = state_record[
                "orphan_report_sha256"
            ]
        self._advance_restore_state(
            state_record,
            operation_dir,
            sentinel,
            "BLOCKED",
            restored_at,
        )
        result = {
            "classification": (
                "orphaned_filesystem_commit"
                if has_orphaned_filesystem_commit
                else "control_store_restore_blocked"
            ),
            "backup_id": manifest["backup_id"],
            "operation_dir": str(operation_dir),
            "report_path": str(report_path),
            "reconciled_authorities": reconciled_authorities,
        }
        if orphan_path is not None:
            result["orphan_report_path"] = str(orphan_path)
        return result

    @staticmethod
    def _validate_operation_inputs(
        *,
        backup_id: str,
        coordinator_session_id: str,
        created_at: str,
    ) -> None:
        if not isinstance(backup_id, str) or _BACKUP_ID.fullmatch(backup_id) is None:
            raise ContractError("selected Control Store backup identity is invalid")
        if not isinstance(coordinator_session_id, str) or not coordinator_session_id.strip():
            raise ContractError("Control Store recovery requires a coordinator session")
        try:
            timestamp = datetime.fromisoformat(created_at)
        except (TypeError, ValueError) as exc:
            raise ContractError("Control Store recovery timestamp must be ISO 8601") from exc
        if timestamp.tzinfo is None:
            raise ContractError("Control Store recovery timestamp requires a timezone offset")

    def _create_sentinel(self, sentinel: dict[str, Any]) -> None:
        self._assert_controlled_recovery_path(
            self.sentinel_path,
            authority="active Control Store restore sentinel",
        )
        if self.sentinel_path.exists():
            raise ControlStoreUnavailable(
                "Control Store recovery already has persistent authority",
                data={"sentinel_path": str(self.sentinel_path)},
            )
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        payload = canonical_json_bytes(sentinel)
        try:
            with self.sentinel_path.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError as exc:
            raise ControlStoreUnavailable(
                "Control Store recovery already has persistent authority",
                data={"sentinel_path": str(self.sentinel_path)},
            ) from exc

    def _archive_completed_sentinel(
        self,
        operation_id: str,
        sentinel: dict[str, Any],
    ) -> None:
        sentinel["state"] = "COMPLETED"
        write_json_atomic(self.sentinel_path, sentinel)
        archive_dir = (
            self.workspace_root
            / "待删除"
            / "control-store-backups"
            / operation_id
        )
        archive_dir.mkdir(parents=True, exist_ok=False)
        os.replace(self.sentinel_path, archive_dir / "sentinel.json")

    def _mark_sentinel_failed(self, sentinel: dict[str, Any]) -> None:
        if not self.sentinel_path.exists():
            return
        sentinel["state"] = "BLOCKED"
        try:
            write_json_atomic(self.sentinel_path, sentinel)
        except OSError:
            pass


def load_backup_manifest(backup_dir: Path) -> dict[str, Any]:
    manifest_path = backup_dir.resolve() / BACKUP_MANIFEST_NAME
    try:
        manifest = read_json(manifest_path)
    except (OSError, json.JSONDecodeError) as exc:
        raise ControlStoreUnavailable(
            f"selected Control Store backup manifest is unreadable: {exc}"
        ) from exc
    if not isinstance(manifest, dict):
        raise ControlStoreUnavailable(
            "selected Control Store backup manifest root is invalid"
        )
    return manifest
