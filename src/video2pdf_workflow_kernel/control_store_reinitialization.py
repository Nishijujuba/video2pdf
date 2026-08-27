from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from typing import Any
import uuid

from .contracts import ContractRegistry
from .control_store import ControlStore, SCHEMA_VERSION
from .errors import ContractError, ControlStoreUnavailable
from .utils import (
    canonical_json_bytes,
    normalized_physical_path,
    read_json,
    sha256_file,
    write_json_atomic,
)


RECOVERY_SENTINEL_NAME = ".workflow-control-recovery.json"
REINITIALIZATION_ROOT_NAME = ".workflow-control-reinitialization"

_INTENT_TABLES = (
    "run_state_mutation_intents",
    "task_promotion_intents",
    "source_publication_intents",
    "delivery_lifecycle_intents",
)
_RESOURCE_TABLES = (
    "resource_configurations",
    "resource_sequences",
    "resource_lease_resources",
    "resource_fairness_cursors",
    "resource_circuit_breakers",
    "resource_control_events",
)

REINITIALIZATION_FAULT_POINTS = frozenset(
    {
        "after_prepared",
        "after_old_moved",
        "after_new_published",
        "after_reconciling",
        "after_committed",
    }
)


class ReinitializationInterruption(RuntimeError):
    """Test-only interruption after a durable reinitialization boundary."""

    def __init__(self, fault_point: str) -> None:
        super().__init__(f"injected Control Store reinitialization interruption at {fault_point}")
        self.fault_point = fault_point


class ControlStoreReinitialization:
    """Prepare a healthy Store for a later authority-preserving replacement."""

    def __init__(
        self,
        workspace_root: Path,
        *,
        project_root: Path | None = None,
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self.project_root = (
            project_root.resolve()
            if project_root is not None
            else Path(__file__).resolve().parents[2]
        )
        self.contracts = ContractRegistry(self.project_root)
        self.sentinel_path = self.workspace_root / RECOVERY_SENTINEL_NAME
        self.operations_root = (
            self.workspace_root / REINITIALIZATION_ROOT_NAME / "operations"
        )

    def prepare_eligibility(
        self,
        *,
        coordinator_session_id: str,
        prepared_at: str,
    ) -> dict[str, Any]:
        self._validate_inputs(
            coordinator_session_id=coordinator_session_id,
            recorded_at=prepared_at,
        )
        if self.sentinel_path.exists():
            raise ControlStoreUnavailable(
                "Control Store recovery already has persistent authority",
                data={
                    "gate": "reinitialization_recovery_authority_conflict",
                    "sentinel_path": str(self.sentinel_path),
                },
            )

        store = ControlStore(self.workspace_root, self.contracts)
        health = store.check()
        if health.status != "ok":
            raise ControlStoreUnavailable(
                "Control Store reinitialization preparation requires a healthy Store"
            )

        operation_id = f"reinitialize-{uuid.uuid4().hex}"
        operation_dir = self.operations_root / operation_id
        snapshot_path = operation_dir / "eligibility-snapshot.json"

        connection = store._connect()
        sentinel_created = False
        try:
            connection.execute("BEGIN IMMEDIATE")
            if self.sentinel_path.exists():
                raise ControlStoreUnavailable(
                    "Control Store recovery already has persistent authority",
                    data={
                        "gate": "reinitialization_recovery_authority_conflict",
                        "sentinel_path": str(self.sentinel_path),
                    },
                )
            table_rows = self._table_rows(connection)
            unresolved = self._unresolved_ownership(table_rows)
            if unresolved:
                raise ControlStoreUnavailable(
                    "Control Store reinitialization eligibility is blocked by "
                    "unresolved authority",
                    data={
                        "gate": "reinitialization_unresolved_ownership",
                        "unresolved_ownership": unresolved,
                    },
                )

            run_bindings, mutation_chains, delivery_projections = (
                self._bound_run_authority(table_rows)
            )
            current_epoch = self._current_store_epoch(table_rows)
            snapshot = {
                "schema_name": (
                    "control-store-reinitialization-eligibility-snapshot"
                ),
                "schema_version": "1.0.0",
                "kernel_version": "2.0.0",
                "snapshot_id": operation_id,
                "prepared_at": prepared_at,
                "coordinator_session_id": coordinator_session_id,
                "store_identity": {
                    "store_id": store.store_id,
                    "marker_sha256": sha256_file(store.marker_path),
                    "anchor_sha256": sha256_file(store.anchor_path),
                    "database_relpath": ".workflow-control/control.sqlite3",
                },
                "workspace_identity": {
                    "workspace_path": str(self.workspace_root),
                    "normalized_workspace_path": normalized_physical_path(
                        self.workspace_root
                    ),
                },
                "schema_generation": SCHEMA_VERSION,
                "maintenance_fence_id": operation_id,
                "current_store_epoch": current_epoch,
                "proposed_replacement_epoch": current_epoch + 1,
                "authority_inventory": {
                    "run_bindings": run_bindings,
                    "committed_mutation_chains": mutation_chains,
                    "batch_records": table_rows["batch_records"],
                    "batch_item_projections": table_rows[
                        "batch_item_projections"
                    ],
                    "delivery_projections": delivery_projections,
                    "claims": table_rows["task_claims"],
                    "attempts": table_rows["task_attempts"],
                    "queues": table_rows["resource_queue_entries"],
                    "reservations": [
                        row
                        for row in table_rows["resource_queue_entries"]
                        if row["reservation_state"] != "NONE"
                    ],
                    "leases": table_rows["resource_leases"],
                    "publication_slots": table_rows[
                        "projection_publication_slots"
                    ],
                    "resource_state": {
                        table: table_rows[table] for table in _RESOURCE_TABLES
                    },
                    "initialization_intents": table_rows[
                        "initialization_intents"
                    ],
                    "mutation_intents": {
                        table: table_rows[table] for table in _INTENT_TABLES
                    },
                    "complete_store_rows": table_rows,
                },
                "unresolved_ownership": [],
            }
            self.contracts.validate(
                "control-store-reinitialization-eligibility-snapshot",
                snapshot,
            )
            sentinel = {
                "schema_name": "control-store-reinitialization-sentinel",
                "schema_version": "1.0.0",
                "kernel_version": "2.0.0",
                "operation_id": operation_id,
                "operation": "reinitialization",
                "state": "PREPARING",
                "coordinator_session_id": coordinator_session_id,
                "created_at": prepared_at,
                "snapshot_path": str(snapshot_path),
                "store_id": store.store_id,
                "proposed_replacement_epoch": current_epoch + 1,
            }
            self.contracts.validate(
                "control-store-reinitialization-sentinel", sentinel
            )
            self._write_json_new(self.sentinel_path, sentinel)
            sentinel_created = True
            operation_dir.mkdir(parents=True, exist_ok=False)
            snapshot_sha256 = self._write_json_new(snapshot_path, snapshot)
            sentinel["state"] = "ELIGIBILITY_PUBLISHED"
            sentinel["snapshot_sha256"] = snapshot_sha256
            self.contracts.validate(
                "control-store-reinitialization-sentinel", sentinel
            )
            write_json_atomic(self.sentinel_path, sentinel)
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            if sentinel_created:
                try:
                    blocked = read_json(self.sentinel_path)
                    blocked["state"] = "BLOCKED"
                    write_json_atomic(self.sentinel_path, blocked)
                except (OSError, json.JSONDecodeError, TypeError):
                    pass
            raise
        finally:
            connection.close()

        return {
            "classification": (
                "control_store_reinitialization_eligibility_published"
            ),
            "operation_id": operation_id,
            "snapshot_path": str(snapshot_path),
            "snapshot_sha256": snapshot_sha256,
            "store_id": store.store_id,
            "proposed_replacement_epoch": current_epoch + 1,
        }

    def abandon_preparation(
        self,
        *,
        operation_id: str,
        coordinator_session_id: str,
        abandoned_at: str,
    ) -> dict[str, Any]:
        self._validate_inputs(
            coordinator_session_id=coordinator_session_id,
            recorded_at=abandoned_at,
        )
        sentinel = self._load_selected_sentinel(operation_id)
        if sentinel.get("coordinator_session_id") != coordinator_session_id:
            raise ControlStoreUnavailable(
                "Control Store reinitialization coordinator authority disagrees"
            )
        snapshot_path = Path(str(sentinel["snapshot_path"]))
        operation_dir = self.operations_root / operation_id
        try:
            snapshot_path.resolve().relative_to(operation_dir.resolve())
        except ValueError as exc:
            raise ControlStoreUnavailable(
                "Control Store reinitialization snapshot path is outside its operation"
            ) from exc
        if (
            not snapshot_path.is_file()
            or sha256_file(snapshot_path) != sentinel.get("snapshot_sha256")
        ):
            raise ControlStoreUnavailable(
                "Control Store reinitialization eligibility snapshot is unavailable or changed"
            )
        snapshot = read_json(snapshot_path)
        if (
            snapshot.get("snapshot_id") != operation_id
            or snapshot.get("maintenance_fence_id") != operation_id
        ):
            raise ControlStoreUnavailable(
                "Control Store reinitialization eligibility authority disagrees"
            )

        store = ControlStore(self.workspace_root, self.contracts)
        if snapshot.get("store_identity", {}).get("store_id") != store.store_id:
            raise ControlStoreUnavailable(
                "Control Store identity changed during reinitialization preparation"
            )
        connection = store._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            selected = self._load_selected_sentinel(operation_id)
            if selected != sentinel:
                raise ControlStoreUnavailable(
                    "Control Store reinitialization fence changed during abandonment"
                )
            abandonment_path = operation_dir / "abandonment.json"
            abandonment = {
                "schema_name": "control-store-reinitialization-abandonment",
                "schema_version": "1.0.0",
                "kernel_version": "2.0.0",
                "operation_id": operation_id,
                "snapshot_sha256": sentinel["snapshot_sha256"],
                "store_id": store.store_id,
                "coordinator_session_id": coordinator_session_id,
                "abandoned_at": abandoned_at,
                "replacement_identity_published": False,
            }
            self.contracts.validate(
                "control-store-reinitialization-abandonment", abandonment
            )
            if abandonment_path.exists():
                if read_json(abandonment_path) != abandonment:
                    raise ControlStoreUnavailable(
                        "Control Store reinitialization abandonment authority "
                        "disagrees"
                    )
                abandonment_sha256 = sha256_file(abandonment_path)
            else:
                abandonment_sha256 = self._write_json_new(
                    abandonment_path, abandonment
                )
            archived_sentinel = operation_dir / "abandoned-sentinel.json"
            os.replace(self.sentinel_path, archived_sentinel)
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

        return {
            "classification": (
                "control_store_reinitialization_preparation_abandoned"
            ),
            "operation_id": operation_id,
            "snapshot_path": str(snapshot_path),
            "abandonment_path": str(abandonment_path),
            "abandonment_sha256": abandonment_sha256,
            "store_id": store.store_id,
            "replacement_identity_published": False,
        }

    def reinitialize_selected(
        self,
        snapshot_path: Path,
        *,
        coordinator_session_id: str,
        reinitialized_at: str,
        fault_point: str | None = None,
    ) -> dict[str, Any]:
        """Replace a lost Store from its one pre-loss eligibility authority."""
        self._validate_inputs(
            coordinator_session_id=coordinator_session_id,
            recorded_at=reinitialized_at,
        )
        self._validate_fault_point(fault_point)
        snapshot_path = snapshot_path.resolve()
        sentinel = self._load_selected_sentinel_for_replacement()
        operation_id = str(sentinel["operation_id"])
        operation_dir = self.operations_root / operation_id
        selected_path = Path(str(sentinel["snapshot_path"])).resolve()
        if snapshot_path != selected_path:
            raise ControlStoreUnavailable(
                "selected Control Store reinitialization snapshot is differently bound",
                data={"gate": "reinitialization_snapshot_binding"},
            )
        if sentinel.get("coordinator_session_id") != coordinator_session_id:
            raise ControlStoreUnavailable(
                "Control Store reinitialization coordinator authority disagrees",
                data={"gate": "reinitialization_coordinator_binding"},
            )
        snapshot = self._validate_selected_snapshot(
            sentinel, snapshot_path, operation_dir
        )
        state_path = operation_dir / "reinitialization-state.json"
        if state_path.exists():
            raise ControlStoreUnavailable(
                "Control Store reinitialization already has a durable replacement operation; resume it",
                data={
                    "gate": "reinitialization_operation_already_started",
                    "operation_id": operation_id,
                },
            )
        self._require_replacement_needed()
        candidate_root = operation_dir / "replacement" / "candidate"
        prior_root = operation_dir / "replacement" / "prior"
        state = {
            "schema_name": "control-store-reinitialization-state",
            "schema_version": "1.0.0",
            "kernel_version": "2.0.0",
            "operation_id": operation_id,
            "snapshot_path": str(snapshot_path),
            "snapshot_sha256": str(sentinel["snapshot_sha256"]),
            "coordinator_session_id": coordinator_session_id,
            "replacement_store_epoch": int(snapshot["proposed_replacement_epoch"]),
            "candidate_root": str(candidate_root),
            "prior_root": str(prior_root),
            "state": "PREPARED",
            "state_history": [
                {"state": "PREPARED", "recorded_at": reinitialized_at}
            ],
        }
        self.contracts.validate("control-store-reinitialization-state", state)
        self._write_json_new(state_path, state)
        sentinel.update(
            {
                "state": "PREPARED",
                "state_path": str(state_path),
                "recovery_token_sha256": hashlib.sha256(
                    operation_id.encode("utf-8")
                ).hexdigest(),
            }
        )
        self.contracts.validate("control-store-reinitialization-sentinel", sentinel)
        write_json_atomic(self.sentinel_path, sentinel)
        self._inject_fault(fault_point, "after_prepared")
        return self._continue_replacement(
            state,
            snapshot,
            continued_at=reinitialized_at,
            fault_point=fault_point,
        )

    def resume_replacement(
        self,
        *,
        operation_id: str,
        resumed_at: str,
        fault_point: str | None = None,
    ) -> dict[str, Any]:
        """Resume the exact durable replacement selected before interruption."""
        self._validate_inputs(
            coordinator_session_id="reinitialization-resume",
            recorded_at=resumed_at,
        )
        self._validate_fault_point(fault_point)
        sentinel = self._load_resume_sentinel(operation_id)
        if sentinel.get("operation_id") != operation_id:
            raise ControlStoreUnavailable(
                "Control Store reinitialization resume operation disagrees",
                data={"gate": "reinitialization_operation_binding"},
            )
        operation_dir = self.operations_root / operation_id
        state_path = Path(
            str(
                sentinel.get(
                    "state_path",
                    operation_dir / "reinitialization-state.json",
                )
            )
        )
        try:
            state_path.resolve().relative_to(operation_dir.resolve())
        except (OSError, ValueError) as exc:
            raise ControlStoreUnavailable(
                "Control Store reinitialization state authority is outside its operation"
            ) from exc
        try:
            state = read_json(state_path)
        except (OSError, json.JSONDecodeError) as exc:
            raise ControlStoreUnavailable(
                "Control Store reinitialization state authority is unavailable"
            ) from exc
        if (
            state.get("operation_id") != operation_id
            or state.get("snapshot_sha256") != sentinel.get("snapshot_sha256")
        ):
            raise ControlStoreUnavailable(
                "Control Store reinitialization state authority disagrees"
            )
        try:
            self.contracts.validate("control-store-reinitialization-state", state)
        except ContractError as exc:
            raise ControlStoreUnavailable(
                "Control Store reinitialization state authority is contradictory"
            ) from exc
        snapshot_path = Path(str(state["snapshot_path"]))
        snapshot = self._validate_selected_snapshot(
            sentinel, snapshot_path, operation_dir
        )
        if sentinel.get("state") == "ELIGIBILITY_PUBLISHED":
            sentinel.update(
                {
                    "state": "PREPARED",
                    "state_path": str(state_path),
                    "recovery_token_sha256": hashlib.sha256(
                        operation_id.encode("utf-8")
                    ).hexdigest(),
                }
            )
            self.contracts.validate(
                "control-store-reinitialization-sentinel", sentinel
            )
            write_json_atomic(self.sentinel_path, sentinel)
        return self._continue_replacement(
            state,
            snapshot,
            continued_at=resumed_at,
            fault_point=fault_point,
        )

    def _load_resume_sentinel(self, operation_id: str) -> dict[str, Any]:
        if self.sentinel_path.exists():
            return self._load_selected_sentinel_for_replacement()
        archived_path = (
            self.operations_root / operation_id / "committed-sentinel.json"
        )
        try:
            sentinel = read_json(archived_path)
            self.contracts.validate(
                "control-store-reinitialization-sentinel", sentinel
            )
        except (OSError, json.JSONDecodeError, ContractError) as exc:
            raise ControlStoreUnavailable(
                "Control Store reinitialization resume authority is unavailable",
                data={"gate": "reinitialization_authority_missing"},
            ) from exc
        if (
            sentinel.get("operation_id") != operation_id
            or sentinel.get("state") != "COMMITTED"
        ):
            raise ControlStoreUnavailable(
                "archived Control Store reinitialization authority disagrees"
            )
        return sentinel

    def _continue_replacement(
        self,
        state: dict[str, Any],
        snapshot: dict[str, Any],
        *,
        continued_at: str,
        fault_point: str | None,
    ) -> dict[str, Any]:
        operation_id = str(state["operation_id"])
        operation_dir = self.operations_root / operation_id
        state_path = operation_dir / "reinitialization-state.json"
        candidate_root = Path(str(state["candidate_root"]))
        prior_root = Path(str(state["prior_root"]))
        try:
            if state["state"] == "COMMITTED":
                return self._complete_committed_operation(state, snapshot)
            if state["state"] == "BLOCKED":
                state["state"] = state.pop("blocked_from_state")
                self.contracts.validate(
                    "control-store-reinitialization-state", state
                )
                write_json_atomic(state_path, state)
            if state["state"] == "PREPARED":
                self._ensure_candidate(
                    candidate_root, prior_root, snapshot, operation_id
                )
                self._move_replaced_identity(prior_root)
                self._advance_state(state, state_path, "OLD_MOVED", continued_at)
                self._update_reinitialization_sentinel("OLD_MOVED")
            self._inject_fault(fault_point, "after_old_moved")

            if state["state"] == "OLD_MOVED":
                self._publish_candidate(candidate_root, operation_id, snapshot)
                self._advance_state(state, state_path, "NEW_PUBLISHED", continued_at)
                self._update_reinitialization_sentinel("NEW_PUBLISHED")
            elif state["state"] in {"NEW_PUBLISHED", "RECONCILING"}:
                self._validate_published_replacement(operation_id, snapshot)
            self._inject_fault(fault_point, "after_new_published")

            self._advance_state(state, state_path, "RECONCILING", continued_at)
            self._update_reinitialization_sentinel("RECONCILING")
            reconciliation = self._reconcile_imported_authority(
                operation_id, snapshot
            )
            self._inject_fault(fault_point, "after_reconciling")
            report = self._passing_report(
                state, snapshot, reconciliation, continued_at
            )
            report_path = (
                self.workspace_root
                / ".workflow-control"
                / "control_store_reinitialization_report.json"
            )
            self.contracts.validate("control-store-reinitialization-report", report)
            write_json_atomic(report_path, report)
            self._advance_state(state, state_path, "COMMITTED", continued_at)
            self._update_reinitialization_sentinel(
                "COMMITTED", report_path=report_path
            )
            self._inject_fault(fault_point, "after_committed")
            return self._complete_committed_operation(state, snapshot)
        except ReinitializationInterruption:
            raise
        except BaseException as exc:
            if state.get("state") == "COMMITTED":
                raise ControlStoreUnavailable(
                    "Control Store reinitialization committed; sentinel archival remains pending",
                    data={
                        "gate": "reinitialization_commit_archive_pending",
                        "operation_id": operation_id,
                    },
                ) from exc
            report_path = operation_dir / "blocked-reinitialization-report.json"
            blocked = {
                "schema_name": "control-store-reinitialization-report",
                "schema_version": "1.0.0",
                "kernel_version": "2.0.0",
                "operation_id": operation_id,
                "snapshot_sha256": state["snapshot_sha256"],
                "final_global_status": "blocked",
                "replacement_store_epoch": state["replacement_store_epoch"],
                "blocked_gate": getattr(exc, "data", {}).get(
                    "gate", "reinitialization_reconciliation"
                ),
                "diagnostic": str(exc),
                "recorded_at": continued_at,
            }
            self.contracts.validate("control-store-reinitialization-report", blocked)
            write_json_atomic(report_path, blocked)
            state["blocked_from_state"] = str(state["state"])
            self._advance_state(state, state_path, "BLOCKED", continued_at)
            self._update_reinitialization_sentinel(
                "BLOCKED", report_path=report_path
            )
            if isinstance(exc, ControlStoreUnavailable):
                exc.data.setdefault("evidence_path", str(report_path))
                raise
            raise ControlStoreUnavailable(
                "Control Store reinitialization blocked",
                data={
                    "gate": "reinitialization_reconciliation",
                    "evidence_path": str(report_path),
                },
            ) from exc

    def _complete_committed_operation(
        self, state: dict[str, Any], snapshot: dict[str, Any]
    ) -> dict[str, Any]:
        operation_id = str(state["operation_id"])
        operation_dir = self.operations_root / operation_id
        report_path = (
            self.workspace_root
            / ".workflow-control"
            / "control_store_reinitialization_report.json"
        )
        try:
            report = read_json(report_path)
            self.contracts.validate(
                "control-store-reinitialization-report", report
            )
        except (OSError, json.JSONDecodeError, ContractError) as exc:
            raise ControlStoreUnavailable(
                "committed Control Store reinitialization report is unavailable"
            ) from exc
        if (
            report.get("operation_id") != operation_id
            or report.get("snapshot_sha256") != state.get("snapshot_sha256")
            or report.get("store_id")
            != snapshot.get("store_identity", {}).get("store_id")
            or report.get("final_global_status") != "passed"
        ):
            raise ControlStoreUnavailable(
                "committed Control Store reinitialization report disagrees"
            )
        archived_sentinel = operation_dir / "committed-sentinel.json"
        if self.sentinel_path.exists():
            sentinel = read_json(self.sentinel_path)
            if sentinel.get("state") != "COMMITTED":
                self._update_reinitialization_sentinel(
                    "COMMITTED", report_path=report_path
                )
                sentinel = read_json(self.sentinel_path)
            if (
                sentinel.get("report_path") != str(report_path)
                or sentinel.get("report_sha256") != sha256_file(report_path)
            ):
                raise ControlStoreUnavailable(
                    "committed Control Store reinitialization sentinel disagrees"
                )
            if archived_sentinel.exists():
                raise ControlStoreUnavailable(
                    "committed Control Store reinitialization has competing sentinels"
                )
            os.replace(self.sentinel_path, archived_sentinel)
        else:
            try:
                archived = read_json(archived_sentinel)
                self.contracts.validate(
                    "control-store-reinitialization-sentinel", archived
                )
            except (OSError, json.JSONDecodeError, ContractError) as exc:
                raise ControlStoreUnavailable(
                    "committed Control Store reinitialization sentinel is unavailable"
                ) from exc
            if (
                archived.get("operation_id") != operation_id
                or archived.get("state") != "COMMITTED"
                or archived.get("report_path") != str(report_path)
                or archived.get("report_sha256") != sha256_file(report_path)
            ):
                raise ControlStoreUnavailable(
                    "archived Control Store reinitialization sentinel disagrees"
                )
        return {
            "classification": "control_store_reinitialization_complete",
            "operation_id": operation_id,
            "snapshot_path": state["snapshot_path"],
            "report_path": str(report_path),
            "replacement_store_epoch": state["replacement_store_epoch"],
        }

    def _validate_selected_snapshot(
        self,
        sentinel: dict[str, Any],
        snapshot_path: Path,
        operation_dir: Path,
    ) -> dict[str, Any]:
        try:
            snapshot_path = snapshot_path.resolve()
            snapshot_path.relative_to(operation_dir.resolve())
        except (OSError, ValueError) as exc:
            raise ControlStoreUnavailable(
                "Control Store reinitialization snapshot is outside its operation",
                data={"gate": "reinitialization_snapshot_binding"},
            ) from exc
        if (
            not snapshot_path.is_file()
            or sha256_file(snapshot_path) != sentinel.get("snapshot_sha256")
        ):
            raise ControlStoreUnavailable(
                "Control Store reinitialization snapshot is missing or stale",
                data={"gate": "reinitialization_snapshot_freshness"},
            )
        try:
            snapshot = read_json(snapshot_path)
            self.contracts.validate(
                "control-store-reinitialization-eligibility-snapshot", snapshot
            )
        except (OSError, json.JSONDecodeError, ContractError) as exc:
            raise ControlStoreUnavailable(
                "Control Store reinitialization snapshot is contradictory",
                data={"gate": "reinitialization_snapshot_contract"},
            ) from exc
        if (
            snapshot.get("snapshot_id") != sentinel.get("operation_id")
            or snapshot.get("maintenance_fence_id") != sentinel.get("operation_id")
            or snapshot.get("store_identity", {}).get("store_id")
            != sentinel.get("store_id")
            or snapshot.get("proposed_replacement_epoch")
            != sentinel.get("proposed_replacement_epoch")
            or snapshot.get("workspace_identity", {}).get(
                "normalized_workspace_path"
            )
            != normalized_physical_path(self.workspace_root)
            or snapshot.get("schema_generation") != SCHEMA_VERSION
        ):
            raise ControlStoreUnavailable(
                "Control Store reinitialization snapshot authority disagrees",
                data={"gate": "reinitialization_snapshot_binding"},
            )
        self._validate_snapshot_filesystem(snapshot)
        return snapshot

    def _validate_snapshot_filesystem(self, snapshot: dict[str, Any]) -> None:
        for binding in snapshot["authority_inventory"]["run_bindings"]:
            run_path = Path(str(binding["run_record_path"]))
            try:
                record = read_json(run_path)
                self.contracts.validate_run_record(record)
            except (OSError, json.JSONDecodeError, ContractError) as exc:
                raise ControlStoreUnavailable(
                    "imported Run authority is unavailable",
                    data={"gate": "reinitialization_run_reconciliation"},
                ) from exc
            if (
                record.get("run_id") != binding.get("run_id")
                or sha256_file(run_path) != binding.get("run_record_sha256")
                or record.get("coordination_revision")
                != binding.get("coordination_revision")
            ):
                raise ControlStoreUnavailable(
                    "imported Run authority changed after eligibility",
                    data={"gate": "reinitialization_snapshot_freshness"},
                )

    def _require_replacement_needed(self) -> None:
        try:
            store = ControlStore(self.workspace_root, self.contracts)
            if store.check().status == "ok":
                raise ControlStoreUnavailable(
                    "Control Store is healthy; replacement is not required",
                    data={"gate": "reinitialization_recovery_not_required"},
                )
        except ControlStoreUnavailable as exc:
            if exc.data.get("gate") == "reinitialization_recovery_not_required":
                raise

    def _materialize_candidate(
        self,
        candidate_root: Path,
        snapshot: dict[str, Any],
        operation_id: str,
    ) -> None:
        candidate_root.mkdir(parents=True, exist_ok=False)
        candidate = self._candidate_store(candidate_root, operation_id)
        candidate.control_dir.mkdir(parents=True, exist_ok=False)
        candidate._create_database()
        write_json_atomic(candidate.marker_path, candidate._identity_record("marker"))
        write_json_atomic(candidate.anchor_path, candidate._identity_record("anchor"))
        rows = self._replacement_rows(snapshot)
        connection = candidate._connect_raw()
        try:
            connection.execute("PRAGMA foreign_keys=OFF")
            connection.execute("BEGIN IMMEDIATE")
            for table in rows:
                connection.execute(f'DELETE FROM "{table}"')
            for table, table_rows in rows.items():
                for row in table_rows:
                    columns = list(row)
                    column_sql = ", ".join(f'"{column}"' for column in columns)
                    placeholders = ", ".join("?" for _ in columns)
                    connection.execute(
                        f'INSERT INTO "{table}" ({column_sql}) VALUES ({placeholders})',
                        tuple(row[column] for column in columns),
                    )
            connection.execute("COMMIT")
            connection.execute("PRAGMA foreign_keys=ON")
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise ControlStoreUnavailable(
                    "replacement Control Store import violates foreign keys",
                    data={"gate": "reinitialization_import"},
                )
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()
        candidate._validate_existing()
        if candidate.store_fencing_epoch != snapshot["proposed_replacement_epoch"]:
            raise ControlStoreUnavailable(
                "replacement Control Store fencing epoch disagrees",
                data={"gate": "reinitialization_epoch"},
            )

    def _ensure_candidate(
        self,
        candidate_root: Path,
        prior_root: Path,
        snapshot: dict[str, Any],
        operation_id: str,
    ) -> None:
        prior_root.mkdir(parents=True, exist_ok=True)
        if candidate_root.exists():
            try:
                candidate = self._candidate_store(candidate_root, operation_id)
                candidate._validate_existing()
                if (
                    candidate.store_fencing_epoch
                    == snapshot["proposed_replacement_epoch"]
                ):
                    return
            except ControlStoreUnavailable:
                pass
            partial_index = 1
            while (prior_root / f"partial-candidate-{partial_index:02d}").exists():
                partial_index += 1
            os.replace(
                candidate_root,
                prior_root / f"partial-candidate-{partial_index:02d}",
            )
        self._materialize_candidate(candidate_root, snapshot, operation_id)

    def _candidate_store(
        self, candidate_root: Path, operation_id: str
    ) -> ControlStore:
        """Bind ControlStore validation to the durable off-canonical candidate."""
        candidate = ControlStore.__new__(ControlStore)
        candidate._configure(
            self.workspace_root,
            self.contracts,
            recovery_operation_token=operation_id,
        )
        candidate.control_dir = candidate_root / ".workflow-control"
        candidate.path = candidate.control_dir / "control.sqlite3"
        candidate.marker_path = candidate.control_dir / "control-store.json"
        candidate.anchor_dir = candidate_root
        candidate.anchor_path = candidate_root / "anchor.json"
        return candidate

    def _move_replaced_identity(self, prior_root: Path) -> None:
        live = ControlStore.__new__(ControlStore)
        live._configure(self.workspace_root, self.contracts)
        prior_control = prior_root / ".workflow-control"
        prior_anchor = prior_root / "anchor.json"
        if live.control_dir.exists():
            if prior_control.exists():
                raise ControlStoreUnavailable(
                    "replaced Control Store identity is contradictory",
                    data={"gate": "reinitialization_prior_identity"},
                )
            os.replace(live.control_dir, prior_control)
        if live.anchor_path.exists():
            if prior_anchor.exists():
                raise ControlStoreUnavailable(
                    "replaced Control Store anchor is contradictory",
                    data={"gate": "reinitialization_prior_identity"},
                )
            os.replace(live.anchor_path, prior_anchor)

    def _publish_candidate(
        self,
        candidate_root: Path,
        operation_id: str,
        snapshot: dict[str, Any],
    ) -> None:
        live = ControlStore.__new__(ControlStore)
        live._configure(
            self.workspace_root,
            self.contracts,
            recovery_operation_token=operation_id,
        )
        candidate_control = candidate_root / ".workflow-control"
        candidate_anchor = candidate_root / "anchor.json"
        if not live.control_dir.exists():
            if not candidate_control.exists():
                raise ControlStoreUnavailable(
                    "replacement Control Store candidate is unavailable",
                    data={"gate": "reinitialization_publication"},
                )
            os.replace(candidate_control, live.control_dir)
        live.anchor_dir.mkdir(parents=True, exist_ok=True)
        if not live.anchor_path.exists():
            if not candidate_anchor.exists():
                raise ControlStoreUnavailable(
                    "replacement Control Store anchor candidate is unavailable",
                    data={"gate": "reinitialization_publication"},
                )
            os.replace(candidate_anchor, live.anchor_path)
        self._validate_published_replacement(operation_id, snapshot)

    def _validate_published_replacement(
        self, operation_id: str, snapshot: dict[str, Any]
    ) -> ControlStore:
        store = ControlStore(
            self.workspace_root,
            self.contracts,
            recovery_operation_token=operation_id,
        )
        store.check()
        if store.store_fencing_epoch != snapshot["proposed_replacement_epoch"]:
            raise ControlStoreUnavailable(
                "published replacement fencing epoch disagrees",
                data={"gate": "reinitialization_epoch"},
            )
        return store

    def _reconcile_imported_authority(
        self, operation_id: str, snapshot: dict[str, Any]
    ) -> dict[str, Any]:
        store = self._validate_published_replacement(operation_id, snapshot)
        from .kernel import VideoWorkflowKernel

        kernel = VideoWorkflowKernel(
            self.workspace_root,
            _control_store_recovery_token=operation_id,
        )
        run_ids: list[str] = []
        run_dirs: dict[str, Path] = {}
        for binding in snapshot["authority_inventory"]["run_bindings"]:
            run_id = str(binding["run_id"])
            kernel.reconcile_authority("kernel_run", run_id)
            run_ids.append(run_id)
            run_dirs[run_id] = Path(str(binding["output_path"]))

        batch_ids: list[str] = []
        nested_batch_run_ids: set[str] = set()
        batch_records: dict[str, dict[str, Any]] = {}
        for row in snapshot["authority_inventory"]["batch_records"]:
            try:
                record = json.loads(str(row["batch_record_json"]))
                self.contracts.validate("batch-record", record)
            except (TypeError, ValueError, ContractError) as exc:
                raise ControlStoreUnavailable(
                    "imported Batch authority is contradictory",
                    data={"gate": "reinitialization_batch_reconciliation"},
                ) from exc
            if record.get("batch_id") != row.get("batch_id"):
                raise ControlStoreUnavailable(
                    "imported Batch identity disagrees",
                    data={"gate": "reinitialization_batch_reconciliation"},
                )
            batch_id = str(row["batch_id"])
            batch_ids.append(batch_id)
            batch_records[batch_id] = record
            for mapping in record.get("run_mappings", []):
                mapped_run_id = str(mapping["run_id"])
                if mapped_run_id not in run_dirs:
                    raise ControlStoreUnavailable(
                        "imported nested Batch item has no Run authority",
                        data={
                            "gate": "reinitialization_batch_reconciliation"
                        },
                    )
                nested_batch_run_ids.add(mapped_run_id)
        run_id_set = set(run_ids)
        projection_ids: list[str] = []
        for row in snapshot["authority_inventory"]["batch_item_projections"]:
            try:
                projection = json.loads(str(row["projection_json"]))
                self.contracts.validate("batch-item-projection", projection)
            except (TypeError, ValueError, ContractError) as exc:
                raise ControlStoreUnavailable(
                    "imported Batch projection is contradictory",
                    data={"gate": "reinitialization_batch_reconciliation"},
                ) from exc
            if str(row["run_id"]) not in run_id_set:
                raise ControlStoreUnavailable(
                    "imported Batch projection has no Run authority",
                    data={"gate": "reinitialization_batch_reconciliation"},
                )
            if (
                projection.get("batch_id") != row.get("batch_id")
                or projection.get("item_index") != row.get("item_index")
                or projection.get("run_id") != row.get("run_id")
            ):
                raise ControlStoreUnavailable(
                    "imported Batch projection row identity disagrees",
                    data={"gate": "reinitialization_batch_reconciliation"},
                )
            projection_ids.append(
                f'{row["batch_id"]}:{int(row["item_index"])}'
            )

        from .batch_projection import BatchProjectionProvider

        batch_provider = BatchProjectionProvider()
        for batch_id in sorted(batch_records):
            record = batch_records[batch_id]
            batch_provider.rebuild_projections(
                Path(str(record["output_root"])),
                self.contracts,
                batch_id=batch_id,
                control_store_root=self.workspace_root,
                recovery_operation_token=operation_id,
            )

        delivery_run_ids: list[str] = []
        bindings = {
            str(item["run_id"]): item
            for item in snapshot["authority_inventory"]["run_bindings"]
        }
        for projection in snapshot["authority_inventory"]["delivery_projections"]:
            run_id = str(projection["run_id"])
            binding = bindings.get(run_id)
            if binding is None:
                raise ControlStoreUnavailable(
                    "imported delivery projection has no Run authority",
                    data={"gate": "reinitialization_delivery_reconciliation"},
                )
            record = read_json(Path(str(binding["run_record_path"])))
            if record.get("delivery") != projection.get("delivery"):
                raise ControlStoreUnavailable(
                    "imported delivery projection disagrees with Run authority",
                    data={"gate": "reinitialization_delivery_reconciliation"},
                )
            from .delivery_lifecycle import DeliveryLifecycleProvider

            delivery_result = DeliveryLifecycleProvider(
                self.project_root
            ).reconcile(run_dir=run_dirs[run_id])
            if (
                delivery_result.get("run_id") != run_id
                or delivery_result.get("stage")
                != projection.get("delivery", {}).get("stage")
            ):
                raise ControlStoreUnavailable(
                    "public delivery reconciliation disagrees with imported authority",
                    data={
                        "gate": "reinitialization_delivery_reconciliation"
                    },
                )
            delivery_run_ids.append(run_id)

        connection = store._connect()
        try:
            actual_rows = self._table_rows(connection)
        finally:
            connection.close()
        expected_rows = self._replacement_rows(snapshot)
        if actual_rows != expected_rows:
            raise ControlStoreUnavailable(
                "replacement Control Store import is incomplete or changed during reconciliation",
                data={"gate": "reinitialization_import_reconciliation"},
            )
        return {
            "run_ids": sorted(run_ids),
            "batch_ids": sorted(batch_ids),
            "nested_batch_run_ids": sorted(nested_batch_run_ids),
            "batch_projection_ids": sorted(projection_ids),
            "delivery_run_ids": sorted(delivery_run_ids),
            "table_row_counts": {
                table: len(rows) for table, rows in sorted(actual_rows.items())
            },
        }

    @staticmethod
    def _replacement_rows(snapshot: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        rows = json.loads(
            json.dumps(snapshot["authority_inventory"]["complete_store_rows"])
        )
        metadata_rows = [
            row
            for row in rows["control_store_metadata"]
            if row["key"] != "store_fencing_epoch"
        ]
        metadata_rows.append(
            {
                "key": "store_fencing_epoch",
                "value": str(snapshot["proposed_replacement_epoch"]),
            }
        )
        rows["control_store_metadata"] = sorted(
            metadata_rows, key=lambda row: row["key"]
        )
        return rows

    def _passing_report(
        self,
        state: dict[str, Any],
        snapshot: dict[str, Any],
        reconciliation: dict[str, Any],
        recorded_at: str,
    ) -> dict[str, Any]:
        return {
            "schema_name": "control-store-reinitialization-report",
            "schema_version": "1.0.0",
            "kernel_version": "2.0.0",
            "operation_id": state["operation_id"],
            "snapshot_sha256": state["snapshot_sha256"],
            "store_id": snapshot["store_identity"]["store_id"],
            "replacement_store_epoch": state["replacement_store_epoch"],
            "final_global_status": "passed",
            "identity_agreement": "passed",
            "database_authority_agreement": "passed",
            "filesystem_authority_agreement": "passed",
            "output_binding_agreement": "passed",
            "mutation_chain_agreement": "passed",
            "projection_agreement": "passed",
            "unresolved_ownership": [],
            "imported_authority": reconciliation,
            "recorded_at": recorded_at,
        }

    def _load_selected_sentinel_for_replacement(self) -> dict[str, Any]:
        try:
            sentinel = read_json(self.sentinel_path)
            self.contracts.validate(
                "control-store-reinitialization-sentinel", sentinel
            )
        except (OSError, json.JSONDecodeError, ContractError) as exc:
            raise ControlStoreUnavailable(
                "Control Store reinitialization authority is unavailable",
                data={"gate": "reinitialization_authority_missing"},
            ) from exc
        if (
            sentinel.get("operation") != "reinitialization"
            or sentinel.get("state")
            not in {
                "ELIGIBILITY_PUBLISHED",
                "PREPARED",
                "OLD_MOVED",
                "NEW_PUBLISHED",
                "RECONCILING",
                "COMMITTED",
                "BLOCKED",
            }
        ):
            raise ControlStoreUnavailable(
                "selected Control Store reinitialization authority is not active",
                data={"gate": "reinitialization_authority_state"},
            )
        return sentinel

    def _advance_state(
        self,
        state: dict[str, Any],
        state_path: Path,
        target: str,
        recorded_at: str,
    ) -> None:
        if state.get("state") != target:
            state["state"] = target
            state.setdefault("state_history", []).append(
                {"state": target, "recorded_at": recorded_at}
            )
            self.contracts.validate("control-store-reinitialization-state", state)
            write_json_atomic(state_path, state)

    def _update_reinitialization_sentinel(
        self, state: str, *, report_path: Path | None = None
    ) -> None:
        sentinel = read_json(self.sentinel_path)
        sentinel["state"] = state
        if report_path is not None:
            sentinel["report_path"] = str(report_path)
            sentinel["report_sha256"] = sha256_file(report_path)
        self.contracts.validate("control-store-reinitialization-sentinel", sentinel)
        write_json_atomic(self.sentinel_path, sentinel)

    @staticmethod
    def _validate_fault_point(fault_point: str | None) -> None:
        if fault_point is not None and fault_point not in REINITIALIZATION_FAULT_POINTS:
            raise ContractError("unknown Control Store reinitialization fault point")

    @staticmethod
    def _inject_fault(fault_point: str | None, boundary: str) -> None:
        if fault_point == boundary:
            raise ReinitializationInterruption(boundary)

    def _load_selected_sentinel(self, operation_id: str) -> dict[str, Any]:
        try:
            sentinel = read_json(self.sentinel_path)
        except (OSError, json.JSONDecodeError) as exc:
            raise ControlStoreUnavailable(
                "Control Store reinitialization fence is unavailable"
            ) from exc
        if (
            not isinstance(sentinel, dict)
            or sentinel.get("operation") != "reinitialization"
            or sentinel.get("state") != "ELIGIBILITY_PUBLISHED"
            or sentinel.get("operation_id") != operation_id
        ):
            raise ControlStoreUnavailable(
                "selected Control Store reinitialization preparation is not active"
            )
        return sentinel

    def _bound_run_authority(
        self, table_rows: dict[str, list[dict[str, Any]]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        initializations = {
            row["run_id"]: row for row in table_rows["initialization_intents"]
        }
        chain_tables = (
            "run_state_mutation_intents",
            "task_promotion_intents",
            "source_publication_intents",
            "delivery_lifecycle_intents",
        )
        bound_runs: list[dict[str, Any]] = []
        chains: list[dict[str, Any]] = []
        delivery_projections: list[dict[str, Any]] = []
        for binding in table_rows["run_bindings"]:
            run_id = str(binding["run_id"])
            initialization = initializations.get(run_id)
            if initialization is None or initialization["state"] != "COMMITTED":
                raise ControlStoreUnavailable(
                    "Control Store Run binding lacks committed initialization",
                    data={"run_id": run_id},
                )
            run_path = Path(str(binding["output_path"])) / "workflow" / "run.json"
            try:
                run_record = read_json(run_path)
                self.contracts.validate_run_record(run_record)
            except (OSError, json.JSONDecodeError, ContractError) as exc:
                raise ControlStoreUnavailable(
                    "Control Store Run Record is unavailable or invalid during "
                    "reinitialization preparation",
                    data={"run_id": run_id, "run_record_path": str(run_path)},
                ) from exc
            if run_record.get("run_id") != run_id:
                raise ControlStoreUnavailable(
                    "Control Store Run binding and Run Record identity disagree",
                    data={"run_id": run_id},
                )

            entries: list[dict[str, Any]] = [
                {
                    "kind": "initialization",
                    "authority_id": initialization["intent_id"],
                    "revision": 1,
                    "run_record_sha256": initialization["run_record_sha256"],
                }
            ]
            for table in chain_tables:
                for row in table_rows[table]:
                    if row.get("run_id") != run_id or row["state"] != "COMMITTED":
                        continue
                    entries.append(
                        {
                            "kind": table,
                            "authority_id": row.get("mutation_id")
                            or row.get("intent_id"),
                            "revision": int(row["expected_run_revision"]) + 1,
                            "run_record_sha256": row[
                                "replacement_run_record_sha256"
                            ],
                        }
                    )
            entries.sort(key=lambda item: (item["revision"], item["kind"]))
            expected_revisions = list(range(1, len(entries) + 1))
            actual_revisions = [entry["revision"] for entry in entries]
            current_sha256 = sha256_file(run_path)
            if (
                actual_revisions != expected_revisions
                or run_record.get("coordination_revision") != entries[-1]["revision"]
                or current_sha256 != entries[-1]["run_record_sha256"]
            ):
                raise ControlStoreUnavailable(
                    "Control Store committed mutation chain and current Run Record disagree",
                    data={"run_id": run_id},
                )
            bound_runs.append(
                {
                    **binding,
                    "run_record_path": str(run_path),
                    "run_record_sha256": current_sha256,
                    "coordination_revision": int(
                        run_record["coordination_revision"]
                    ),
                    "phase": run_record["phase"],
                }
            )
            chains.append({"run_id": run_id, "entries": entries})
            if isinstance(run_record.get("delivery"), dict):
                delivery_projections.append(
                    {"run_id": run_id, "delivery": run_record["delivery"]}
                )
        return bound_runs, chains, delivery_projections

    @staticmethod
    def _current_store_epoch(
        table_rows: dict[str, list[dict[str, Any]]]
    ) -> int:
        metadata = {
            row["key"]: row["value"]
            for row in table_rows["control_store_metadata"]
        }
        raw_epoch = metadata.get("store_fencing_epoch", "0")
        try:
            epoch = int(raw_epoch)
        except (TypeError, ValueError) as exc:
            raise ControlStoreUnavailable(
                "Control Store fencing epoch metadata is invalid"
            ) from exc
        if epoch < 0:
            raise ControlStoreUnavailable(
                "Control Store fencing epoch metadata is invalid"
            )
        return epoch

    @staticmethod
    def _table_rows(
        connection: sqlite3.Connection,
    ) -> dict[str, list[dict[str, Any]]]:
        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        ]
        result: dict[str, list[dict[str, Any]]] = {}
        for table in tables:
            primary_keys = [
                str(row[1])
                for row in connection.execute(f'PRAGMA table_info("{table}")')
                if int(row[5]) > 0
            ]
            order = primary_keys or [
                str(row[1])
                for row in connection.execute(f'PRAGMA table_info("{table}")')
            ]
            order_sql = ", ".join(f'"{column}"' for column in order)
            rows = connection.execute(
                f'SELECT * FROM "{table}" ORDER BY {order_sql}'
            ).fetchall()
            result[table] = [dict(row) for row in rows]
        return result

    @staticmethod
    def _unresolved_ownership(
        table_rows: dict[str, list[dict[str, Any]]]
    ) -> list[dict[str, Any]]:
        unresolved: list[dict[str, Any]] = []

        def collect(
            table: str,
            identity: str,
            predicate: Any,
        ) -> None:
            for row in table_rows[table]:
                if predicate(row):
                    unresolved.append(
                        {
                            "kind": table,
                            "identity": str(row[identity]),
                            "state": str(row.get("state") or row.get("reservation_state")),
                        }
                    )

        collect("task_claims", "task_id", lambda row: row["state"] == "ACTIVE")
        collect(
            "task_attempts",
            "attempt_id",
            lambda row: row["state"]
            in {"CLAIMED", "VALIDATED_WAITING_FOR_PROMOTION"},
        )
        collect(
            "resource_queue_entries",
            "queue_id",
            lambda row: row["state"] in {"QUEUED", "ADMITTED"},
        )
        for row in table_rows["resource_queue_entries"]:
            if row["reservation_state"] in {"PENDING", "ACTIVE"}:
                unresolved.append(
                    {
                        "kind": "resource_reservation",
                        "identity": str(row["queue_id"]),
                        "state": str(row["reservation_state"]),
                    }
                )
        collect(
            "resource_leases",
            "lease_id",
            lambda row: row["state"] in {"starting", "active", "unknown"},
        )
        collect(
            "projection_publication_slots",
            "slot_id",
            lambda row: row["state"] == "HELD",
        )
        collect(
            "initialization_intents",
            "intent_id",
            lambda row: row["state"] not in {"COMMITTED", "ABORTED"},
        )
        collect(
            "run_state_mutation_intents",
            "mutation_id",
            lambda row: row["state"] == "PREPARED",
        )
        for table in (
            "task_promotion_intents",
            "source_publication_intents",
            "delivery_lifecycle_intents",
        ):
            collect(
                table,
                "intent_id",
                lambda row: row["state"]
                in {"PREPARED", "FILES_PUBLISHED", "RECORD_COMMITTED"},
            )
        return sorted(
            unresolved,
            key=lambda item: (item["kind"], item["identity"]),
        )

    @staticmethod
    def _write_json_new(path: Path, value: dict[str, Any]) -> str:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = canonical_json_bytes(value)
        try:
            with path.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError as exc:
            raise ControlStoreUnavailable(
                "Control Store reinitialization authority already exists",
                data={"evidence_path": str(path)},
            ) from exc
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _validate_inputs(
        *, coordinator_session_id: str, recorded_at: str
    ) -> None:
        if not coordinator_session_id.strip():
            raise ContractError(
                "Control Store reinitialization requires a coordinator session"
            )
        try:
            timestamp = datetime.fromisoformat(recorded_at)
        except (TypeError, ValueError) as exc:
            raise ContractError(
                "Control Store reinitialization timestamp must be ISO 8601"
            ) from exc
        if timestamp.tzinfo is None:
            raise ContractError(
                "Control Store reinitialization timestamp requires a timezone offset"
            )
