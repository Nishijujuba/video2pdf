from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import sqlite3
from typing import Any, Callable, TypeVar

from .control_store import RESOURCE_CLASSES
from .errors import (
    ContractError,
    ControlStoreUnavailable,
    KernelConflict,
    ResourceAdmissionFault,
)
from .models import ResourceAdmissionState
from .utils import canonical_json_bytes


_LaunchResult = TypeVar("_LaunchResult")
LAUNCH_FAULT_POINTS = frozenset({"after_launch_authorized"})
RESOURCE_CLAIM_FAULT_POINTS = frozenset(
    {
        "after_claim_before_enqueue",
        "after_claim_enqueue_before_schedule",
        "after_claim_schedule_before_commit",
    }
)
RESOURCE_RECLAIM_FAULT_POINTS = frozenset(
    {
        "after_reclaim_before_enqueue",
        "after_reclaim_enqueue_before_schedule",
        "after_reclaim_schedule_before_commit",
    }
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ResourceAdmission:
    """Deep Module for durable cross-run capacity admission and launch authority."""

    def __init__(self, kernel: Any) -> None:
        self.kernel = kernel

    @staticmethod
    def _rotate(values: list[str], cursor: str | None) -> list[str]:
        if not values or cursor is None:
            return values
        for index, value in enumerate(values):
            if value > cursor:
                return values[index:] + values[:index]
        return values

    @staticmethod
    def _cursor(
        connection: sqlite3.Connection, *, level: str, scope_id: str
    ) -> str | None:
        row = connection.execute(
            "SELECT cursor_value FROM resource_fairness_cursors "
            "WHERE level=? AND scope_id=?",
            (level, scope_id),
        ).fetchone()
        return None if row is None else str(row["cursor_value"])

    @staticmethod
    def _required(item: Any) -> tuple[str, ...]:
        try:
            required = tuple(json.loads(str(item["required_resources_json"])))
        except json.JSONDecodeError as exc:
            raise ControlStoreUnavailable(
                "queued Resource Request is invalid"
            ) from exc
        if not required or len(required) != len(set(required)):
            raise ControlStoreUnavailable(
                "queued Resource Request is empty or duplicated"
            )
        return required

    def _set_cursor(
        self,
        connection: sqlite3.Connection,
        *,
        level: str,
        scope_id: str,
        cursor_value: str,
    ) -> None:
        store = self.kernel._require_control_store()
        scheduling_seq = store._next_resource_sequence(connection, "scheduling")
        connection.execute(
            "INSERT INTO resource_fairness_cursors("
            "level, scope_id, cursor_value, scheduling_seq) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(level, scope_id) DO UPDATE SET "
            "cursor_value=excluded.cursor_value, "
            "scheduling_seq=excluded.scheduling_seq",
            (level, scope_id, cursor_value, scheduling_seq),
        )

    def _admit(
        self,
        connection: sqlite3.Connection,
        *,
        item: Any,
        required: tuple[str, ...],
        configuration: Any,
        updated_at: str,
    ) -> None:
        store = self.kernel._require_control_store()
        owner = connection.execute(
            "SELECT coordinator_session_id, worker_id FROM task_claims "
            "WHERE task_id=? AND attempt_id=? AND claim_generation=? "
            "AND state='ACTIVE'",
            (item["task_id"], item["attempt_id"], item["claim_generation"]),
        ).fetchone()
        if owner is None:
            raise ControlStoreUnavailable(
                "Resource admission has no current Task Claim owner"
            )
        admitted_seq = store._next_resource_sequence(connection, "admission")
        lease_id = str(item["lease_candidate_id"])
        connection.execute(
            "INSERT INTO resource_leases("
            "lease_id, queue_id, task_id, attempt_id, claim_generation, state, "
            "coordinator_session_id, worker_id, "
            "admission_configuration_id, admission_configuration_version, "
            "admission_configuration_sha256, launch_token, admitted_seq, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'starting', ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                lease_id,
                item["queue_id"],
                item["task_id"],
                item["attempt_id"],
                item["claim_generation"],
                owner["coordinator_session_id"],
                owner["worker_id"],
                configuration["configuration_id"],
                configuration["configuration_version"],
                configuration["configuration_sha256"],
                item["launch_token"],
                admitted_seq,
                updated_at,
            ),
        )
        connection.executemany(
            "INSERT INTO resource_lease_resources(lease_id, resource_class) "
            "VALUES (?, ?)",
            [(lease_id, resource) for resource in required],
        )
        changed = connection.execute(
            "UPDATE resource_queue_entries SET state='ADMITTED', lease_id=?, "
            "admitted_seq=?, reservation_state=CASE "
            "WHEN reservation_state IN ('ACTIVE','PENDING') THEN 'TERMINATED' "
            "ELSE reservation_state END WHERE queue_id=? AND state='QUEUED'",
            (lease_id, admitted_seq, item["queue_id"]),
        )
        if changed.rowcount != 1:
            raise ControlStoreUnavailable(
                "Resource queue admission compare-and-set failed"
            )
        event_seq = store._next_resource_sequence(connection, "event")
        connection.execute(
            "INSERT INTO resource_control_events("
            "event_seq, event_kind, queue_id, lease_id, configuration_id, "
            "configuration_version, configuration_sha256, payload_json) "
            "VALUES (?, 'admitted', ?, ?, ?, ?, ?, ?)",
            (
                event_seq,
                item["queue_id"],
                lease_id,
                configuration["configuration_id"],
                configuration["configuration_version"],
                configuration["configuration_sha256"],
                canonical_json_bytes({}).decode("utf-8"),
            ),
        )

    @staticmethod
    def _open_breakers(connection: sqlite3.Connection) -> set[str]:
        return {
            str(row["resource_class"])
            for row in connection.execute(
                "SELECT resource_class FROM resource_circuit_breakers "
                "WHERE state='OPEN'"
            ).fetchall()
        }

    def _active_reservation_sets(
        self, connection: sqlite3.Connection
    ) -> list[set[str]]:
        return [
            set(self._required(row))
            for row in connection.execute(
                "SELECT * FROM resource_queue_entries "
                "WHERE state='QUEUED' AND reservation_state='ACTIVE' "
                "ORDER BY reservation_seq"
            ).fetchall()
        ]

    def _promote_pending_reservations(
        self, connection: sqlite3.Connection, configuration: Any
    ) -> None:
        store = self.kernel._require_control_store()
        active_sets = self._active_reservation_sets(connection)
        pending = connection.execute(
            "SELECT * FROM resource_queue_entries WHERE state='QUEUED' "
            "AND reservation_state='PENDING' ORDER BY reservation_seq"
        ).fetchall()
        for item in pending:
            required = set(self._required(item))
            if any(not required.isdisjoint(active) for active in active_sets):
                continue
            promoted = connection.execute(
                "UPDATE resource_queue_entries SET reservation_state='ACTIVE' "
                "WHERE queue_id=? AND state='QUEUED' "
                "AND reservation_state='PENDING'",
                (item["queue_id"],),
            )
            if promoted.rowcount != 1:
                raise ControlStoreUnavailable(
                    "Draining Reservation promotion compare-and-set failed"
                )
            active_sets.append(required)
            event_seq = store._next_resource_sequence(connection, "event")
            connection.execute(
                "INSERT INTO resource_control_events("
                "event_seq, event_kind, queue_id, lease_id, configuration_id, "
                "configuration_version, configuration_sha256, payload_json) "
                "VALUES (?, 'reservation_activated', ?, NULL, ?, ?, ?, ?)",
                (
                    event_seq,
                    item["queue_id"],
                    configuration["configuration_id"],
                    configuration["configuration_version"],
                    configuration["configuration_sha256"],
                    canonical_json_bytes({}).decode("utf-8"),
                ),
            )

    def _record_bypasses(
        self,
        connection: sqlite3.Connection,
        *,
        blocked: list[Any],
        configuration: Any,
    ) -> bool:
        store = self.kernel._require_control_store()
        try:
            configuration_body = json.loads(str(configuration["configuration_json"]))
            threshold = int(configuration_body["bypass_threshold"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ControlStoreUnavailable(
                "Resource Admission bypass threshold is invalid"
            ) from exc
        seen: set[str] = set()
        reservation_changed = False
        for item in blocked:
            queue_id = str(item["queue_id"])
            if queue_id in seen:
                continue
            seen.add(queue_id)
            current = connection.execute(
                "SELECT * FROM resource_queue_entries WHERE queue_id=? "
                "AND state='QUEUED'",
                (queue_id,),
            ).fetchone()
            if current is None:
                continue
            new_count = int(current["bypass_count"]) + 1
            connection.execute(
                "UPDATE resource_queue_entries SET bypass_count=? "
                "WHERE queue_id=? AND state='QUEUED'",
                (new_count, queue_id),
            )
            event_seq = store._next_resource_sequence(connection, "event")
            connection.execute(
                "INSERT INTO resource_control_events("
                "event_seq, event_kind, queue_id, lease_id, configuration_id, "
                "configuration_version, configuration_sha256, payload_json) "
                "VALUES (?, 'bypassed', ?, NULL, ?, ?, ?, ?)",
                (
                    event_seq,
                    queue_id,
                    configuration["configuration_id"],
                    configuration["configuration_version"],
                    configuration["configuration_sha256"],
                    canonical_json_bytes({}).decode("utf-8"),
                ),
            )
            if current["reservation_state"] != "NONE" or new_count < threshold:
                continue
            reservation_seq = store._next_resource_sequence(
                connection, "reservation"
            )
            required = set(self._required(current))
            active_sets = self._active_reservation_sets(connection)
            reservation_state = (
                "ACTIVE"
                if all(required.isdisjoint(active) for active in active_sets)
                else "PENDING"
            )
            connection.execute(
                "UPDATE resource_queue_entries SET reservation_state=?, "
                "reservation_seq=? WHERE queue_id=? AND state='QUEUED' "
                "AND reservation_state='NONE'",
                (reservation_state, reservation_seq, queue_id),
            )
            reservation_changed = True
            event_seq = store._next_resource_sequence(connection, "event")
            connection.execute(
                "INSERT INTO resource_control_events("
                "event_seq, event_kind, queue_id, lease_id, configuration_id, "
                "configuration_version, configuration_sha256, payload_json) "
                "VALUES (?, ?, ?, NULL, ?, ?, ?, ?)",
                (
                    event_seq,
                    (
                        "reservation_activated"
                        if reservation_state == "ACTIVE"
                        else "reservation_pending"
                    ),
                    queue_id,
                    configuration["configuration_id"],
                    configuration["configuration_version"],
                    configuration["configuration_sha256"],
                    canonical_json_bytes({}).decode("utf-8"),
                ),
            )
        return reservation_changed

    def _feasibility(
        self,
        connection: sqlite3.Connection,
        *,
        required: tuple[str, ...],
        capacities: dict[str, int],
        open_breakers: set[str],
        active_reservation_sets: list[set[str]],
        owns_reservation: bool,
    ) -> tuple[bool, bool, str | None]:
        store = self.kernel._require_control_store()
        if any(resource not in capacities for resource in required):
            raise ControlStoreUnavailable(
                "queued Resource Request names an unknown Resource Class"
            )
        if any(resource in open_breakers for resource in required):
            return False, False, "circuit_breaker"
        required_set = set(required)
        if not owns_reservation and any(
            not required_set.isdisjoint(reserved)
            for reserved in active_reservation_sets
        ):
            return False, True, "reservation"
        feasible = all(
            store._resource_usage(connection, resource) < capacities[resource]
            for resource in required
        )
        return feasible, not feasible, None if feasible else "configuration_capacity"

    def _record_configuration_block(
        self,
        connection: sqlite3.Connection,
        *,
        item: Any,
        configuration: Any,
    ) -> None:
        store = self.kernel._require_control_store()
        changed = connection.execute(
            "UPDATE resource_queue_entries SET "
            "last_blocked_configuration_id=?, "
            "last_blocked_configuration_version=?, "
            "last_blocked_configuration_sha256=? "
            "WHERE queue_id=? AND state='QUEUED' AND ("
            "last_blocked_configuration_id IS NULL OR "
            "last_blocked_configuration_id<>? OR "
            "last_blocked_configuration_version<>? OR "
            "last_blocked_configuration_sha256<>?)",
            (
                configuration["configuration_id"],
                configuration["configuration_version"],
                configuration["configuration_sha256"],
                item["queue_id"],
                configuration["configuration_id"],
                configuration["configuration_version"],
                configuration["configuration_sha256"],
            ),
        )
        if changed.rowcount != 1:
            return
        event_seq = store._next_resource_sequence(connection, "event")
        payload = canonical_json_bytes(
            {"reason": "configuration_capacity"}
        ).decode("utf-8")
        connection.execute(
            "INSERT INTO resource_control_events("
            "event_seq, event_kind, queue_id, lease_id, configuration_id, "
            "configuration_version, configuration_sha256, payload_json) "
            "VALUES (?, 'configuration_blocked', ?, NULL, ?, ?, ?, ?)",
            (
                event_seq,
                item["queue_id"],
                configuration["configuration_id"],
                configuration["configuration_version"],
                configuration["configuration_sha256"],
                payload,
            ),
        )

    def _schedule(
        self, connection: sqlite3.Connection, updated_at: str
    ) -> None:
        store = self.kernel._require_control_store()
        configuration = store._active_resource_configuration_row(connection)
        capacities = store._resource_capacities(configuration)
        while True:
            self._promote_pending_reservations(connection, configuration)
            queued = connection.execute(
                "SELECT * FROM resource_queue_entries WHERE state='QUEUED' "
                "ORDER BY enqueue_seq"
            ).fetchall()
            if not queued:
                return
            open_breakers = self._open_breakers(connection)
            active_reservation_sets = self._active_reservation_sets(connection)
            active_reservations = [
                item for item in queued if item["reservation_state"] == "ACTIVE"
            ]
            active_reservations.sort(key=lambda item: int(item["reservation_seq"]))
            admitted_reservation = False
            for item in active_reservations:
                required = self._required(item)
                feasible, _, reason = self._feasibility(
                    connection,
                    required=required,
                    capacities=capacities,
                    open_breakers=open_breakers,
                    active_reservation_sets=active_reservation_sets,
                    owns_reservation=True,
                )
                if feasible:
                    self._admit(
                        connection,
                        item=item,
                        required=required,
                        configuration=configuration,
                        updated_at=updated_at,
                    )
                    self._set_cursor(
                        connection,
                        level="RUN",
                        scope_id=str(item["fairness_group_id"]),
                        cursor_value=str(item["run_id"]),
                    )
                    self._set_cursor(
                        connection,
                        level="GROUP",
                        scope_id="global",
                        cursor_value=str(item["fairness_group_id"]),
                    )
                    admitted_reservation = True
                    break
                if reason == "configuration_capacity":
                    self._record_configuration_block(
                        connection,
                        item=item,
                        configuration=configuration,
                    )
            if admitted_reservation:
                continue
            ordinary = [
                item for item in queued if item["reservation_state"] == "NONE"
            ]
            if not ordinary:
                return
            groups = sorted({str(item["fairness_group_id"]) for item in ordinary})
            group_cursor = self._cursor(
                connection, level="GROUP", scope_id="global"
            )
            admitted_in_pass = False
            blocked: list[Any] = []
            restart_pass = False
            for group_id in self._rotate(groups, group_cursor):
                group_rows = [
                    item
                    for item in ordinary
                    if str(item["fairness_group_id"]) == group_id
                ]
                run_ids = sorted({str(item["run_id"]) for item in group_rows})
                run_cursor = self._cursor(
                    connection, level="RUN", scope_id=group_id
                )
                admitted_item = None
                for run_id in self._rotate(run_ids, run_cursor):
                    run_rows = sorted(
                        (
                            item
                            for item in group_rows
                            if str(item["run_id"]) == run_id
                        ),
                        key=lambda item: int(item["enqueue_seq"]),
                    )
                    for item in run_rows:
                        required = self._required(item)
                        feasible, bypassable, reason = self._feasibility(
                            connection,
                            required=required,
                            capacities=capacities,
                            open_breakers=open_breakers,
                            active_reservation_sets=active_reservation_sets,
                            owns_reservation=False,
                        )
                        if feasible:
                            self._admit(
                                connection,
                                item=item,
                                required=required,
                                configuration=configuration,
                                updated_at=updated_at,
                            )
                            self._set_cursor(
                                connection,
                                level="RUN",
                                scope_id=group_id,
                                cursor_value=run_id,
                            )
                            self._set_cursor(
                                connection,
                                level="GROUP",
                                scope_id="global",
                                cursor_value=group_id,
                            )
                            group_cursor = group_id
                            admitted_item = item
                            admitted_in_pass = True
                            restart_pass = self._record_bypasses(
                                connection,
                                blocked=blocked,
                                configuration=configuration,
                            )
                            blocked = []
                            break
                        if reason == "configuration_capacity":
                            self._record_configuration_block(
                                connection,
                                item=item,
                                configuration=configuration,
                            )
                        if bypassable:
                            blocked.append(item)
                    if admitted_item is not None:
                        break
                if restart_pass:
                    break
            if restart_pass:
                continue
            if not admitted_in_pass:
                return

    @staticmethod
    def _state(row: Any) -> ResourceAdmissionState:
        try:
            required = tuple(json.loads(str(row["required_resources_json"])))
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ControlStoreUnavailable("Resource Admission status is invalid") from exc
        lease_id = None if row["lease_id"] is None else str(row["lease_id"])
        lease_state = None if row["lease_state"] is None else str(row["lease_state"])
        configuration_id = (
            row["admission_configuration_id"]
            if lease_id is not None
            else row["enqueue_configuration_id"]
        )
        configuration_version = (
            row["admission_configuration_version"]
            if lease_id is not None
            else row["enqueue_configuration_version"]
        )
        configuration_sha256 = (
            row["admission_configuration_sha256"]
            if lease_id is not None
            else row["enqueue_configuration_sha256"]
        )
        return ResourceAdmissionState(
            queue_id=str(row["queue_id"]),
            task_id=str(row["task_id"]),
            attempt_id=str(row["attempt_id"]),
            run_id=str(row["run_id"]),
            fairness_group_id=str(row["fairness_group_id"]),
            batch_id=None if row["batch_id"] is None else str(row["batch_id"]),
            claim_generation=int(row["claim_generation"]),
            queue_state=str(row["state"]).lower(),
            required_resources=required,
            configuration_id=str(configuration_id),
            configuration_version=int(configuration_version),
            configuration_sha256=str(configuration_sha256),
            lease_id=lease_id,
            lease_state=lease_state,
            launch_token=None if lease_id is None else str(row["launch_token"]),
            launch_authorization_state=(
                None
                if row["launch_authorization_state"] is None
                else str(row["launch_authorization_state"])
            ),
            launch_required_resources=(
                None
                if row["launch_required_resources_json"] is None
                else tuple(json.loads(str(row["launch_required_resources_json"])))
            ),
            launch_eligible=(
                lease_state == "starting"
                and row["launch_authorization_state"] == "AVAILABLE"
                and row["current_claim_state"] == "ACTIVE"
                and row["current_claim_attempt_id"] == row["attempt_id"]
                and row["current_claim_generation"] == row["claim_generation"]
            ),
            bypass_count=int(row["bypass_count"]),
            reservation_state=str(row["reservation_state"]).lower(),
            reservation_seq=(
                None
                if row["reservation_seq"] is None
                else int(row["reservation_seq"])
            ),
        )

    def claim_task(
        self,
        *,
        authority_id: str,
        task_id: str,
        envelope_sha256: str,
        write_set: tuple[str, ...],
        attempt_path: str,
        coordinator_session_id: str,
        worker_id: str,
        claimed_at: str,
        required_resources: tuple[str, ...],
        fairness_group_id: str | None = None,
        batch_id: str | None = None,
        fault_point: str | None = None,
    ) -> Any:
        if (
            fault_point is not None
            and fault_point not in RESOURCE_CLAIM_FAULT_POINTS
        ):
            raise ContractError(
                f"unknown Resource Admission claim fault point: {fault_point}"
            )
        store = self.kernel._preflight_control_store()
        return store.claim_task(
            authority_id=authority_id,
            task_id=task_id,
            envelope_sha256=envelope_sha256,
            write_set=write_set,
            attempt_path=attempt_path,
            coordinator_session_id=coordinator_session_id,
            worker_id=worker_id,
            claimed_at=claimed_at,
            required_resources=required_resources,
            fairness_group_id=fairness_group_id,
            batch_id=batch_id,
            resource_scheduler=self._schedule,
            fault_point=fault_point,
        )

    def reclaim_task(
        self,
        *,
        authority_id: str,
        task_id: str,
        expected_attempt_id: str,
        expected_claim_generation: int,
        attempt_path: str,
        coordinator_session_id: str,
        worker_id: str,
        reason: str,
        reclaimed_at: str,
        required_resources: tuple[str, ...],
        fairness_group_id: str | None = None,
        batch_id: str | None = None,
        fault_point: str | None = None,
    ) -> Any:
        if (
            fault_point is not None
            and fault_point not in RESOURCE_RECLAIM_FAULT_POINTS
        ):
            raise ContractError(
                f"unknown Resource Admission reclaim fault point: {fault_point}"
            )
        store = self.kernel._preflight_control_store()
        return store.reclaim_task(
            authority_id=authority_id,
            task_id=task_id,
            expected_attempt_id=expected_attempt_id,
            expected_claim_generation=expected_claim_generation,
            attempt_path=attempt_path,
            coordinator_session_id=coordinator_session_id,
            worker_id=worker_id,
            reason=reason,
            reclaimed_at=reclaimed_at,
            required_resources=required_resources,
            fairness_group_id=fairness_group_id,
            batch_id=batch_id,
            resource_scheduler=self._schedule,
            fault_point=fault_point,
        )

    def status(self, task_id: str, attempt_id: str) -> ResourceAdmissionState:
        store = self.kernel._preflight_control_store()
        row = store.resource_status(task_id, attempt_id)
        if row is None:
            raise ControlStoreUnavailable(
                "Task Attempt has no Resource Admission authority"
            )
        return self._state(row)

    def release_resource_lease(
        self,
        attempt_id: str,
        claim_generation: int,
        launch_token: str,
        *,
        terminal_evidence: dict[str, Any],
    ) -> ResourceAdmissionState:
        store = self.kernel._preflight_control_store()
        candidate = store.resource_status_by_attempt(attempt_id)
        if candidate is None or candidate["lease_id"] is None:
            raise ControlStoreUnavailable(
                "Resource Lease release has no persisted Lease identity"
            )
        if terminal_evidence.get("evidence_class") not in {
            "provider_terminal_result",
            "local_process_terminated",
        }:
            raise ContractError(
                "normal Resource Lease release requires trusted provider or local process evidence"
            )
        from .resource_recovery import ResourceRecovery

        evidence_json, evidence_sha256 = ResourceRecovery(
            self.kernel,
            provider_verifiers=self.kernel._resource_provider_verifiers,
            local_process_inspector=self.kernel._local_process_inspector,
        )._evidence_record(
            lease_id=str(candidate["lease_id"]),
            attempt_id=attempt_id,
            expected_claim_generation=claim_generation,
            evidence=terminal_evidence,
        )
        row = store.release_resource_lease(
            attempt_id=attempt_id,
            claim_generation=claim_generation,
            launch_token=launch_token,
            terminal_evidence_json=evidence_json,
            terminal_evidence_sha256=evidence_sha256,
            released_at=_utc_now(),
            resource_scheduler=self._schedule,
        )
        return self._state(row)

    def scheduler_status(self) -> dict[str, Any]:
        store = self.kernel._preflight_control_store()
        snapshot = store.resource_scheduler_snapshot()
        group_cursor = None
        run_cursors: dict[str, str] = {}
        cursor_sequences: dict[str, int] = {}
        for item in snapshot["cursors"]:
            if item["level"] == "GROUP" and item["scope_id"] == "global":
                group_cursor = str(item["cursor_value"])
            elif item["level"] == "RUN":
                run_cursors[str(item["scope_id"])] = str(item["cursor_value"])
            cursor_sequences[
                f"{item['level']}:{item['scope_id']}"
            ] = int(item["scheduling_seq"])
        return {
            "group_cursor": group_cursor,
            "run_cursors": run_cursors,
            "cursor_sequences": cursor_sequences,
            "sequences": snapshot["sequences"],
            "reservations": [
                {
                    "queue_id": str(item["queue_id"]),
                    "task_id": str(item["task_id"]),
                    "attempt_id": str(item["attempt_id"]),
                    "state": str(item["reservation_state"]).lower(),
                    "reservation_seq": int(item["reservation_seq"]),
                    "resources": list(
                        json.loads(str(item["required_resources_json"]))
                    ),
                }
                for item in snapshot["reservations"]
            ],
            "events": [
                {
                    "event_seq": int(item["event_seq"]),
                    "event_kind": str(item["event_kind"]),
                    "queue_id": (
                        None if item["queue_id"] is None else str(item["queue_id"])
                    ),
                    "lease_id": (
                        None if item["lease_id"] is None else str(item["lease_id"])
                    ),
                    "configuration_id": str(item["configuration_id"]),
                    "configuration_version": int(item["configuration_version"]),
                    "configuration_sha256": str(item["configuration_sha256"]),
                    "payload": json.loads(str(item["payload_json"])),
                }
                for item in snapshot["events"]
            ],
        }

    def capacity_status(self) -> dict[str, Any]:
        store = self.kernel._preflight_control_store()
        return store.resource_capacity_snapshot()

    def activate_configuration(self, configuration: dict[str, Any]) -> dict[str, Any]:
        self.kernel.contracts.validate(
            "resource-admission-configuration", configuration
        )
        resources = configuration.get("resources")
        configured_classes = (
            []
            if not isinstance(resources, list)
            else [
                item.get("resource_class") if isinstance(item, dict) else None
                for item in resources
            ]
        )
        if (
            len(configured_classes) != len(RESOURCE_CLASSES)
            or any(not isinstance(item, str) for item in configured_classes)
            or set(configured_classes) != set(RESOURCE_CLASSES)
        ):
            raise ContractError(
                "Resource Admission Configuration must govern every fixed "
                "Resource Class exactly once"
            )
        configuration_json = canonical_json_bytes(configuration).decode("utf-8")
        configuration_sha256 = hashlib.sha256(
            configuration_json.encode("utf-8")
        ).hexdigest()
        store = self.kernel._preflight_control_store()
        row = store.activate_resource_configuration(
            configuration=configuration,
            configuration_json=configuration_json,
            configuration_sha256=configuration_sha256,
            activated_at=_utc_now(),
            resource_scheduler=self._schedule,
        )
        return {
            "configuration_id": str(row["configuration_id"]),
            "configuration_version": int(row["configuration_version"]),
            "configuration_sha256": str(row["configuration_sha256"]),
            "state": str(row["state"]).lower(),
        }

    def set_circuit_breaker(
        self,
        resource_class: str,
        *,
        state: str,
        reason: str,
        platform: str | None = None,
    ) -> dict[str, Any]:
        if resource_class not in RESOURCE_CLASSES:
            raise ContractError(
                "Resource Circuit Breaker names an unknown Resource Class"
            )
        normalized_state = state.upper()
        if normalized_state not in {"OPEN", "CLOSED"}:
            raise ContractError("Resource Circuit Breaker state must be open or closed")
        if not reason.strip():
            raise ContractError("Resource Circuit Breaker requires a reason")
        if platform is not None:
            if resource_class not in {"bilibili_download", "youtube_download"}:
                raise ContractError(
                    "platform-scoped breakers require a platform download Resource Class"
                )
            if platform not in {"bilibili", "youtube"}:
                raise ContractError("Resource Circuit Breaker platform is unknown")
            expected_resource = f"{platform}_download"
            if resource_class != expected_resource:
                raise ContractError(
                    "Resource Circuit Breaker platform disagrees with Resource Class"
                )
            scope_kind = "platform"
            breaker_key = f"platform:{platform}:{resource_class}"
        else:
            scope_kind = "resource"
            breaker_key = f"resource:{resource_class}"
        payload = {
            "breaker_key": breaker_key,
            "resource_class": resource_class,
            "scope_kind": scope_kind,
            "platform": platform,
            "state": normalized_state.lower(),
            "reason": reason,
        }
        payload_json = canonical_json_bytes(payload).decode("utf-8")
        store = self.kernel._preflight_control_store()
        row = store.set_resource_circuit_breaker(
            breaker_key=breaker_key,
            resource_class=resource_class,
            platform=platform,
            state=normalized_state,
            reason=reason,
            payload_json=payload_json,
            updated_at=_utc_now(),
            resource_scheduler=self._schedule,
        )
        return {
            **payload,
            "state": str(row["state"]).lower(),
            "updated_seq": int(row["updated_seq"]),
        }

    def circuit_breaker_status(self) -> list[dict[str, Any]]:
        store = self.kernel._preflight_control_store()
        return [
            {
                "breaker_key": str(row["breaker_key"]),
                "resource_class": str(row["resource_class"]),
                "scope_kind": "platform" if row["platform"] is not None else "resource",
                "platform": None if row["platform"] is None else str(row["platform"]),
                "state": str(row["state"]).lower(),
                "reason": None if row["reason"] is None else str(row["reason"]),
                "updated_seq": int(row["updated_seq"]),
            }
            for row in store.resource_circuit_breaker_snapshot()
        ]

    def launch_admitted_task(
        self,
        attempt_id: str,
        claim_generation: int,
        required_resources: tuple[str, ...],
        launcher: Callable[[str], _LaunchResult],
        *,
        fault_point: str | None = None,
    ) -> _LaunchResult:
        if fault_point is not None and fault_point not in LAUNCH_FAULT_POINTS:
            raise ContractError(
                f"unknown Resource Admission launch fault point: {fault_point}"
            )
        if (
            not isinstance(required_resources, tuple)
            or not required_resources
            or tuple(sorted(required_resources)) != required_resources
            or len(required_resources) != len(set(required_resources))
            or any(not isinstance(resource, str) for resource in required_resources)
        ):
            raise ContractError(
                "Resource launch request must be a non-empty, unique, stably sorted tuple"
            )
        required_resources_json = canonical_json_bytes(
            list(required_resources)
        ).decode("utf-8")
        required_resources_sha256 = hashlib.sha256(
            required_resources_json.encode("utf-8")
        ).hexdigest()
        store = self.kernel._preflight_control_store()
        row = store.authorize_resource_launch(
            attempt_id=attempt_id,
            claim_generation=claim_generation,
            required_resources=required_resources,
            updated_at=_utc_now(),
        )
        state = self._state(row)
        if state.launch_token is None:
            raise ControlStoreUnavailable(
                "admitted Resource Lease lacks its launch token"
            )
        if fault_point == "after_launch_authorized":
            raise ResourceAdmissionFault(fault_point)
        try:
            result = launcher(state.launch_token)
        except Exception:
            store.mark_resource_launch_unknown(
                attempt_id=attempt_id,
                claim_generation=claim_generation,
                launch_token=state.launch_token,
                required_resources_sha256=required_resources_sha256,
                failure_stage="launcher_exception",
                updated_at=_utc_now(),
            )
            raise
        launch_identity_json = None
        launch_identity_sha256 = None
        if isinstance(result, dict) and "process_identity" in result:
            process_identity = result["process_identity"]
            if not isinstance(process_identity, dict) or set(process_identity) != {
                "pid",
                "process_creation_identity",
                "launch_token",
            }:
                store.mark_resource_launch_unknown(
                    attempt_id=attempt_id,
                    claim_generation=claim_generation,
                    launch_token=state.launch_token,
                    required_resources_sha256=required_resources_sha256,
                    failure_stage="process_identity_validation",
                    updated_at=_utc_now(),
                )
                raise ContractError(
                    "launcher process identity has an invalid field set"
                )
            if (
                not isinstance(process_identity["pid"], int)
                or process_identity["pid"] < 1
                or not isinstance(process_identity["process_creation_identity"], str)
                or not process_identity["process_creation_identity"].strip()
                or process_identity["launch_token"] != state.launch_token
            ):
                store.mark_resource_launch_unknown(
                    attempt_id=attempt_id,
                    claim_generation=claim_generation,
                    launch_token=state.launch_token,
                    required_resources_sha256=required_resources_sha256,
                    failure_stage="process_identity_validation",
                    updated_at=_utc_now(),
                )
                raise ContractError("launcher process identity is invalid")
            launch_identity_json = canonical_json_bytes(process_identity).decode(
                "utf-8"
            )
            launch_identity_sha256 = hashlib.sha256(
                launch_identity_json.encode("utf-8")
            ).hexdigest()
        _, confirmed = store.confirm_resource_launch(
            attempt_id=attempt_id,
            claim_generation=claim_generation,
            launch_token=state.launch_token,
            required_resources_sha256=required_resources_sha256,
            launch_execution_identity_json=launch_identity_json,
            launch_execution_identity_sha256=launch_identity_sha256,
            updated_at=_utc_now(),
        )
        if not confirmed:
            raise KernelConflict(
                "Resource launch callback completed after its Task Claim fence was lost",
                data={
                    "attempt_id": attempt_id,
                    "claim_generation": claim_generation,
                },
            )
        return result
