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
