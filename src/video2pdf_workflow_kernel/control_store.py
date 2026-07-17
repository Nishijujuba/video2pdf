from __future__ import annotations

from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import time
from typing import Callable, Iterator, TypeVar
import uuid

from .contracts import ContractRegistry
from .errors import (
    ArtifactDrift,
    ContractError,
    ControlStoreUnavailable,
    KernelConflict,
    ResourceAdmissionBlocked,
    ResourceAdmissionFault,
)
from .models import ControlStoreHealth
from .utils import (
    canonical_json_bytes,
    normalized_physical_path,
    read_json,
    sha256_file,
    write_json_atomic,
)


SCHEMA_VERSION = 8
BUSY_TIMEOUT_MS = 5000
RESOURCE_CLASSES = frozenset(
    {
        "bilibili_download",
        "youtube_download",
        "whisper",
        "codex_semantic",
        "latex",
        "pdf_render",
        "visual_acceptance",
    }
)
RESOURCE_SEQUENCE_NAMES = frozenset(
    {"enqueue", "admission", "reservation", "event", "scheduling", "breaker"}
)
RESOURCE_EVENT_KINDS = frozenset(
    {
        "enqueued",
        "admitted",
        "bypassed",
        "reservation_pending",
        "reservation_activated",
        "configuration_blocked",
        "configuration_activated",
        "invalidated_by_reclaim",
        "released",
        "lease_unknown",
        "lease_resolved",
        "breaker_opened",
        "breaker_closed",
    }
)
RESOURCE_LAUNCH_FAILURE_STAGES = frozenset(
    {"launcher_exception", "process_identity_validation", "claim_generation_fence"}
)
LOCK_PROBE_TIMEOUT_MS = 100
SNAPSHOT_RETRY_LIMIT = 3
MARKER_NAME = "control-store.json"
DATABASE_RELPATH = ".workflow-control/control.sqlite3"

RUN_STATE_MUTATION_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS run_state_mutation_intents ("
    "mutation_id TEXT PRIMARY KEY, "
    "operation TEXT NOT NULL CHECK(operation='source_drift_invalidation'), "
    "run_id TEXT NOT NULL, expected_run_revision INTEGER NOT NULL, "
    "old_run_record_sha256 TEXT NOT NULL, "
    "predecessor_committed_sha256 TEXT NOT NULL, "
    "replacement_run_record_sha256 TEXT NOT NULL, "
    "replacement_run_record_json TEXT NOT NULL, "
    "state TEXT NOT NULL CHECK(state IN ('PREPARED','COMMITTED','ABORTED')), "
    "mutation_identity TEXT NOT NULL UNIQUE, "
    "UNIQUE(run_id, operation, expected_run_revision))"
)
RUN_STATE_MUTATION_INDEX_SQL = (
    "CREATE UNIQUE INDEX IF NOT EXISTS one_prepared_source_drift_mutation_per_run "
    "ON run_state_mutation_intents(run_id, operation) WHERE state='PREPARED'"
)
RUN_STATE_MUTATION_IDENTITY_VERSIONS_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS run_state_mutation_identity_versions ("
    "mutation_id TEXT PRIMARY KEY REFERENCES "
    "run_state_mutation_intents(mutation_id), "
    "identity_version TEXT NOT NULL CHECK(identity_version IN "
    "('legacy-v1','evidence-v2')), "
    "row_identity TEXT NOT NULL UNIQUE)"
)
TASK_CLAIMS_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS task_claims ("
    "task_id TEXT PRIMARY KEY, "
    "authority_kind TEXT NOT NULL CHECK(authority_kind='kernel_run'), "
    "authority_id TEXT NOT NULL REFERENCES run_bindings(run_id), "
    "envelope_sha256 TEXT NOT NULL, write_set_json TEXT NOT NULL, "
    "state TEXT NOT NULL CHECK(state IN ('ACTIVE','TERMINAL')), "
    "claim_generation INTEGER NOT NULL CHECK(claim_generation >= 1), "
    "attempt_id TEXT NOT NULL UNIQUE, "
    "coordinator_session_id TEXT NOT NULL, worker_id TEXT NOT NULL, "
    "reclaim_reason TEXT, updated_at TEXT NOT NULL)"
)
TASK_CLAIMS_AUTHORITY_STATE_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS task_claims_by_authority_state_task "
    "ON task_claims(authority_kind, authority_id, state, task_id)"
)
TASK_CLAIM_AUTHORITIES_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS task_claim_authorities ("
    "task_id TEXT PRIMARY KEY REFERENCES task_claims(task_id), "
    "claim_record_json TEXT NOT NULL, "
    "claim_record_sha256 TEXT NOT NULL UNIQUE)"
)
TASK_ATTEMPTS_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS task_attempts ("
    "attempt_id TEXT PRIMARY KEY, "
    "task_id TEXT NOT NULL REFERENCES task_claims(task_id), "
    "claim_generation INTEGER NOT NULL CHECK(claim_generation >= 1), "
    "attempt_path TEXT NOT NULL UNIQUE, "
    "state TEXT NOT NULL CHECK(state IN "
    "('CLAIMED','VALIDATED_WAITING_FOR_PROMOTION','STALE','COMMITTED_COMPLETE','ABANDONED','FAILED')), "
    "completion_sha256 TEXT, "
    "UNIQUE(task_id, claim_generation))"
)
TASK_ATTEMPT_AUTHORITIES_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS task_attempt_authorities ("
    "attempt_id TEXT PRIMARY KEY REFERENCES task_attempts(attempt_id), "
    "attempt_record_json TEXT NOT NULL, "
    "attempt_record_sha256 TEXT NOT NULL UNIQUE)"
)
TASK_COMPLETION_AUTHORITIES_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS task_completion_authorities ("
    "attempt_id TEXT PRIMARY KEY REFERENCES task_attempts(attempt_id), "
    "completion_record_json TEXT NOT NULL)"
)
TASK_PROMOTION_IDENTITY_VERSIONS_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS task_promotion_identity_versions ("
    "intent_id TEXT PRIMARY KEY REFERENCES task_promotion_intents(intent_id), "
    "identity_version TEXT NOT NULL CHECK(identity_version IN ('legacy-v1','evidence-v2')))"
)
TASK_PROMOTION_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS task_promotion_intents ("
    "intent_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES run_bindings(run_id), "
    "task_id TEXT NOT NULL REFERENCES task_claims(task_id), "
    "attempt_id TEXT NOT NULL REFERENCES task_attempts(attempt_id), "
    "claim_generation INTEGER NOT NULL CHECK(claim_generation >= 1), "
    "expected_run_revision INTEGER NOT NULL CHECK(expected_run_revision >= 1), "
    "old_run_record_sha256 TEXT NOT NULL, replacement_run_record_sha256 TEXT NOT NULL, "
    "replacement_run_record_json TEXT NOT NULL, outputs_json TEXT NOT NULL, "
    "journal_sha256 TEXT, "
    "state TEXT NOT NULL CHECK(state IN "
    "('PREPARED','FILES_PUBLISHED','RECORD_COMMITTED','COMMITTED','ABORTED')), "
    "intent_identity TEXT NOT NULL UNIQUE)"
)
TASK_PROMOTION_INDEX_SQL = (
    "CREATE UNIQUE INDEX IF NOT EXISTS one_nonterminal_task_promotion_per_run "
    "ON task_promotion_intents(run_id) "
    "WHERE state IN ('PREPARED','FILES_PUBLISHED','RECORD_COMMITTED')"
)
TASK_RECLAIM_TRANSITIONS_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS task_reclaim_transitions ("
    "transition_id TEXT PRIMARY KEY, "
    "authority_id TEXT NOT NULL REFERENCES run_bindings(run_id), "
    "task_id TEXT NOT NULL REFERENCES task_claims(task_id), "
    "prior_attempt_id TEXT NOT NULL REFERENCES task_attempts(attempt_id), "
    "replacement_attempt_id TEXT NOT NULL UNIQUE "
    "REFERENCES task_attempts(attempt_id), "
    "prior_claim_generation INTEGER NOT NULL CHECK(prior_claim_generation >= 1), "
    "replacement_claim_generation INTEGER NOT NULL "
    "CHECK(replacement_claim_generation = prior_claim_generation + 1), "
    "recovery_reason TEXT NOT NULL CHECK(length(trim(recovery_reason)) > 0), "
    "prior_coordinator_session_id TEXT NOT NULL, prior_worker_id TEXT NOT NULL, "
    "replacement_coordinator_session_id TEXT NOT NULL, "
    "replacement_worker_id TEXT NOT NULL, reclaimed_at TEXT NOT NULL, "
    "transition_record_json TEXT NOT NULL, "
    "UNIQUE(task_id, prior_claim_generation), "
    "UNIQUE(task_id, replacement_claim_generation))"
)
RESOURCE_CONFIGURATIONS_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS resource_configurations ("
    "configuration_id TEXT PRIMARY KEY, schema_version TEXT NOT NULL, "
    "configuration_version INTEGER NOT NULL UNIQUE CHECK(configuration_version >= 1), "
    "configuration_sha256 TEXT NOT NULL UNIQUE, configuration_json TEXT NOT NULL, "
    "state TEXT NOT NULL CHECK(state IN ('ACTIVE','RETIRED')))"
)
RESOURCE_CONFIGURATIONS_ACTIVE_INDEX_SQL = (
    "CREATE UNIQUE INDEX IF NOT EXISTS one_active_resource_configuration "
    "ON resource_configurations(state) WHERE state='ACTIVE'"
)
RESOURCE_SEQUENCES_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS resource_sequences ("
    "sequence_name TEXT PRIMARY KEY, value INTEGER NOT NULL CHECK(value >= 0))"
)
RESOURCE_QUEUE_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS resource_queue_entries ("
    "queue_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES task_claims(task_id), "
    "attempt_id TEXT NOT NULL UNIQUE REFERENCES task_attempts(attempt_id), "
    "run_id TEXT NOT NULL REFERENCES run_bindings(run_id), "
    "fairness_group_id TEXT NOT NULL, batch_id TEXT, "
    "enqueue_seq INTEGER NOT NULL UNIQUE, required_resources_json TEXT NOT NULL, "
    "claim_generation INTEGER NOT NULL CHECK(claim_generation >= 1), "
    "enqueue_configuration_id TEXT NOT NULL REFERENCES resource_configurations(configuration_id), "
    "enqueue_configuration_version INTEGER NOT NULL, "
    "enqueue_configuration_sha256 TEXT NOT NULL, "
    "request_binding_sha256 TEXT NOT NULL, "
    "state TEXT NOT NULL CHECK(state IN ('QUEUED','ADMITTED','INVALIDATED')), "
    "bypass_count INTEGER NOT NULL DEFAULT 0 CHECK(bypass_count >= 0), "
    "reservation_state TEXT NOT NULL DEFAULT 'NONE' "
    "CHECK(reservation_state IN ('NONE','PENDING','ACTIVE','TERMINATED')), "
    "reservation_seq INTEGER UNIQUE, lease_candidate_id TEXT NOT NULL UNIQUE, "
    "launch_token TEXT NOT NULL UNIQUE, lease_id TEXT UNIQUE, admitted_seq INTEGER UNIQUE, "
    "last_blocked_configuration_id TEXT, last_blocked_configuration_version INTEGER, "
    "last_blocked_configuration_sha256 TEXT)"
)
RESOURCE_QUEUE_STATE_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS resource_queue_by_state_enqueue "
    "ON resource_queue_entries(state, enqueue_seq)"
)
RESOURCE_LEASES_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS resource_leases ("
    "lease_id TEXT PRIMARY KEY, queue_id TEXT NOT NULL UNIQUE "
    "REFERENCES resource_queue_entries(queue_id), "
    "task_id TEXT NOT NULL REFERENCES task_claims(task_id), "
    "attempt_id TEXT NOT NULL REFERENCES task_attempts(attempt_id), "
    "claim_generation INTEGER NOT NULL CHECK(claim_generation >= 1), "
    "coordinator_session_id TEXT NOT NULL, worker_id TEXT NOT NULL, "
    "state TEXT NOT NULL CHECK(state IN ('starting','active','unknown','released','resolved')), "
    "admission_configuration_id TEXT NOT NULL "
    "REFERENCES resource_configurations(configuration_id), "
    "admission_configuration_version INTEGER NOT NULL, "
    "admission_configuration_sha256 TEXT NOT NULL, launch_token TEXT NOT NULL UNIQUE, "
    "launch_authorization_state TEXT NOT NULL DEFAULT 'AVAILABLE' "
    "CHECK(launch_authorization_state IN ('AVAILABLE','CONSUMED','COMPLETED')), "
    "launch_required_resources_json TEXT, launch_required_resources_sha256 TEXT, "
    "launch_authorized_at TEXT, launch_completed_at TEXT, "
    "launch_execution_identity_json TEXT, launch_execution_identity_sha256 TEXT, "
    "terminal_evidence_json TEXT, terminal_evidence_sha256 TEXT, "
    "admitted_seq INTEGER NOT NULL UNIQUE, updated_at TEXT NOT NULL)"
)
RESOURCE_LEASE_RESOURCES_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS resource_lease_resources ("
    "lease_id TEXT NOT NULL REFERENCES resource_leases(lease_id), "
    "resource_class TEXT NOT NULL, PRIMARY KEY(lease_id, resource_class))"
)
RESOURCE_LEASE_RESOURCES_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS resource_lease_resources_by_class "
    "ON resource_lease_resources(resource_class, lease_id)"
)
RESOURCE_FAIRNESS_CURSORS_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS resource_fairness_cursors ("
    "level TEXT NOT NULL CHECK(level IN ('GROUP','RUN')), scope_id TEXT NOT NULL, "
    "cursor_value TEXT NOT NULL, scheduling_seq INTEGER NOT NULL, "
    "PRIMARY KEY(level, scope_id))"
)
RESOURCE_BREAKERS_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS resource_circuit_breakers ("
    "breaker_key TEXT PRIMARY KEY, resource_class TEXT NOT NULL, platform TEXT, "
    "state TEXT NOT NULL CHECK(state IN ('OPEN','CLOSED')), reason TEXT, "
    "updated_seq INTEGER NOT NULL)"
)
RESOURCE_EVENTS_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS resource_control_events ("
    "event_seq INTEGER PRIMARY KEY, event_kind TEXT NOT NULL, queue_id TEXT, "
    "lease_id TEXT, configuration_id TEXT NOT NULL, configuration_version INTEGER NOT NULL, "
    "configuration_sha256 TEXT NOT NULL, payload_json TEXT NOT NULL)"
)


@dataclass(frozen=True)
class _MigrationPlan:
    source_version: int
    run_state_identity_rows: tuple[tuple[object, ...], ...]
    task_attempt_authority_rows: tuple[tuple[object, ...], ...]
    task_completion_authority_rows: tuple[tuple[object, ...], ...]
    task_promotion_identity_rows: tuple[tuple[object, ...], ...]
    task_reclaim_transition_rows: tuple[tuple[object, ...], ...]
    task_claim_authority_rows: tuple[tuple[object, ...], ...]
    resource_configuration_json: str
    resource_configuration_sha256: str


_MutationPlanT = TypeVar("_MutationPlanT")


def _normalized_sql(value: str | None) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", "", value).casefold().replace("ifnotexists", "")


class ControlStore:
    """Cross-run transaction authority for Kernel bindings and mutation intents."""

    def __init__(
        self,
        workspace_root: Path,
        contracts: ContractRegistry,
        *,
        recovery_operation_token: str | None = None,
    ) -> None:
        self._configure(
            workspace_root,
            contracts,
            recovery_operation_token=recovery_operation_token,
        )
        self._validate_existing()

    def _configure(
        self,
        workspace_root: Path,
        contracts: ContractRegistry,
        *,
        recovery_operation_token: str | None = None,
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self.contracts = contracts
        self._recovery_operation_token = recovery_operation_token
        raw = str(self.workspace_root)
        if raw.startswith("\\\\"):
            raise ControlStoreUnavailable("UNC workspace roots are unsupported")
        self.control_dir = self.workspace_root / ".workflow-control"
        self.path = self.control_dir / "control.sqlite3"
        self.marker_path = self.control_dir / MARKER_NAME
        self.recovery_sentinel_path = (
            self.workspace_root / ".workflow-control-recovery.json"
        )
        self.store_id = hashlib.sha256(
            f"video-workflow-control-store-v1\0{normalized_physical_path(self.workspace_root)}".encode(
                "utf-8"
            )
        ).hexdigest()
        self.anchor_dir = self.workspace_root.parent / ".video-workflow-control-anchors"
        self.anchor_path = self.anchor_dir / f"{self.store_id}.json"

    def _resource_configuration_identity(self) -> tuple[dict, str, str]:
        configuration = self.contracts.canonical_instance(
            "resource-admission-configuration"
        )
        canonical = canonical_json_bytes(configuration).decode("utf-8")
        return (
            configuration,
            canonical,
            hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        )

    @classmethod
    def identity_evidence_exists(cls, workspace_root: Path) -> bool:
        workspace = workspace_root.resolve()
        store_id = hashlib.sha256(
            f"video-workflow-control-store-v1\0{normalized_physical_path(workspace)}".encode(
                "utf-8"
            )
        ).hexdigest()
        control_dir = workspace / ".workflow-control"
        recovery_sentinel = workspace / ".workflow-control-recovery.json"
        anchor = workspace.parent / ".video-workflow-control-anchors" / f"{store_id}.json"
        return (
            recovery_sentinel.exists()
            or anchor.exists()
            or control_dir.exists()
            and any(control_dir.iterdir())
        )

    @classmethod
    def initialize(
        cls, workspace_root: Path, contracts: ContractRegistry
    ) -> "ControlStore":
        store = cls.__new__(cls)
        store._configure(workspace_root, contracts)
        store._assert_mutation_allowed()
        anchor_exists = store.anchor_path.is_file()
        marker_exists = store.marker_path.is_file()
        database_exists = store.path.is_file()
        if anchor_exists or marker_exists or database_exists:
            if not (anchor_exists and marker_exists and database_exists):
                raise ControlStoreUnavailable(
                    "Control Store anchor/marker/database identity is incomplete; automatic replacement is forbidden"
                )
            store._validate_existing()
            return store
        if store.control_dir.exists() and any(store.control_dir.iterdir()):
            raise ControlStoreUnavailable(
                "Control Store directory contains unrecognized state"
            )
        anchor = store._identity_record("anchor")
        marker = store._identity_record("marker")
        store.contracts.validate("control-store-identity", anchor)
        store.contracts.validate("control-store-identity", marker)
        try:
            store.anchor_dir.mkdir(parents=True, exist_ok=True)
            write_json_atomic(store.anchor_path, anchor)
            store.control_dir.mkdir(parents=True, exist_ok=True)
            store._create_database()
            write_json_atomic(store.marker_path, marker)
        except (sqlite3.Error, OSError) as exc:
            raise ControlStoreUnavailable(
                f"Control Store initialization failed: {exc}"
            ) from exc
        store._validate_existing()
        return store

    def _identity_record(self, record_kind: str) -> dict[str, str]:
        return {
            "schema_name": "control-store-identity",
            "schema_version": "1.0.0",
            "kernel_version": "2.0.0",
            "record_kind": record_kind,
            "store_id": self.store_id,
            "workspace_path": str(self.workspace_root),
            "database_relpath": DATABASE_RELPATH,
        }

    def _connect_raw(self, *, create: bool = False) -> sqlite3.Connection:
        if create:
            target: str = str(self.path)
            uri = False
        else:
            target = f"file:{self.path.as_posix()}?mode=rw"
            uri = True
        try:
            connection = sqlite3.connect(
                target,
                uri=uri,
                timeout=BUSY_TIMEOUT_MS / 1000,
                isolation_level=None,
            )
        except (sqlite3.Error, OSError) as exc:
            raise ControlStoreUnavailable(
                f"Control Store database connection failed: {exc}"
            ) from exc
        connection.row_factory = sqlite3.Row
        return connection

    def _connect(self) -> sqlite3.Connection:
        connection = self._connect_raw()
        try:
            connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
            journal_mode = str(
                connection.execute("PRAGMA journal_mode").fetchone()[0]
            ).lower()
            if journal_mode != "delete":
                raise ControlStoreUnavailable(
                    f"Control Store journal_mode is not DELETE: {journal_mode}"
                )
            connection.execute("PRAGMA synchronous=EXTRA")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA trusted_schema=OFF")
            return connection
        except BaseException:
            connection.close()
            raise

    def _assert_mutation_allowed(self) -> None:
        if not self.recovery_sentinel_path.exists():
            return
        data: dict[str, object] = {
            "sentinel_path": str(self.recovery_sentinel_path),
        }
        try:
            sentinel = read_json(self.recovery_sentinel_path)
        except (OSError, json.JSONDecodeError):
            sentinel = None
        if isinstance(sentinel, dict):
            data.update(
                {
                    "operation_id": sentinel.get("operation_id"),
                    "operation": sentinel.get("operation"),
                    "state": sentinel.get("state"),
                }
            )
            expected_token_sha256 = sentinel.get("recovery_token_sha256")
            if (
                sentinel.get("operation") == "restore"
                and sentinel.get("state")
                in {"NEW_PUBLISHED", "VALIDATED", "RECONCILING"}
                and isinstance(expected_token_sha256, str)
                and isinstance(self._recovery_operation_token, str)
                and hmac.compare_digest(
                    hashlib.sha256(
                        self._recovery_operation_token.encode("utf-8")
                    ).hexdigest(),
                    expected_token_sha256,
                )
            ):
                return
        raise ControlStoreUnavailable(
            "Control Store mutation is blocked by persistent recovery authority",
            data=data,
        )

    @staticmethod
    def _data_version(connection: sqlite3.Connection) -> int:
        return int(connection.execute("PRAGMA data_version").fetchone()[0])

    def _begin_immediate_if_snapshot_unchanged(
        self,
        connection: sqlite3.Connection,
        expected_data_version: int,
    ) -> bool:
        connection.execute("BEGIN IMMEDIATE")
        if self._data_version(connection) == expected_data_version:
            return True
        connection.execute("ROLLBACK")
        return False

    @contextmanager
    def _planned_immediate(
        self,
        planner: Callable[[sqlite3.Connection], _MutationPlanT],
    ) -> Iterator[tuple[sqlite3.Connection, _MutationPlanT]]:
        """Authenticate and plan on one snapshot before taking the writer lock.

        ``PRAGMA data_version`` is the compare-and-swap token between the read
        snapshot and ``BEGIN IMMEDIATE``.  A retry always invokes ``planner``
        again on the same connection so callers never reuse stale authority or
        digest work in the writer phase.
        """
        for _attempt in range(SNAPSHOT_RETRY_LIMIT):
            self._assert_mutation_allowed()
            connection = self._connect()
            try:
                connection.execute("BEGIN")
                snapshot_data_version = self._data_version(connection)
                self._validate_reclaim_history_before_mutation(connection)
                plan = planner(connection)
                connection.execute("COMMIT")
                if not self._begin_immediate_if_snapshot_unchanged(
                    connection,
                    snapshot_data_version,
                ):
                    continue
                self._assert_mutation_allowed()
                try:
                    yield connection, plan
                    connection.execute("COMMIT")
                except BaseException:
                    if connection.in_transaction:
                        connection.execute("ROLLBACK")
                    raise
                return
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            finally:
                connection.close()
        raise ControlStoreUnavailable(
            "Control Store changed during mutation preflight; bounded retry exhausted"
        )

    @contextmanager
    def _immediate(self) -> Iterator[sqlite3.Connection]:
        with self._planned_immediate(lambda _connection: None) as (connection, _plan):
            yield connection

    def _create_database(self) -> None:
        if self.path.exists():
            raise ControlStoreUnavailable("refusing to initialize over an existing database")
        resource_configuration, resource_json, resource_sha256 = (
            self._resource_configuration_identity()
        )
        connection = self._connect_raw(create=True)
        try:
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("PRAGMA synchronous=EXTRA")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA trusted_schema=OFF")
            connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
            connection.execute(
                "CREATE TABLE control_store_metadata ("
                "key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE run_bindings ("
                "run_id TEXT PRIMARY KEY, normalized_path TEXT NOT NULL UNIQUE, "
                "output_path TEXT NOT NULL, initialization_intent_id TEXT NOT NULL UNIQUE)"
            )
            connection.execute(
                "CREATE TABLE initialization_intents ("
                "intent_id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE, "
                "output_path TEXT NOT NULL, staging_path TEXT NOT NULL, "
                "state TEXT NOT NULL CHECK(state IN "
                "('PREPARED','PUBLISHED','RECORD_COMMITTED','COMMITTED','ABORTED')), "
                "run_record_sha256 TEXT, "
                "expected_run_record_sha256 TEXT, "
                "canonical_platform TEXT, canonical_item_id TEXT, "
                "source_identity TEXT, source_manifest_sha256 TEXT)"
            )
            self._create_run_state_mutation_table(connection)
            self._create_run_state_mutation_identity_table(connection)
            self._create_task_tables(connection)
            self._create_resource_tables(connection)
            self._insert_resource_configuration(
                connection,
                resource_configuration,
                resource_json,
                resource_sha256,
            )
            connection.execute(
                "INSERT INTO control_store_metadata(key, value) VALUES ('store_id', ?)",
                (self.store_id,),
            )
            connection.execute("INSERT INTO schema_migrations(version) VALUES (1)")
            connection.execute("INSERT INTO schema_migrations(version) VALUES (2)")
            connection.execute("INSERT INTO schema_migrations(version) VALUES (3)")
            connection.execute("INSERT INTO schema_migrations(version) VALUES (4)")
            connection.execute("INSERT INTO schema_migrations(version) VALUES (5)")
            connection.execute("INSERT INTO schema_migrations(version) VALUES (6)")
            connection.execute("INSERT INTO schema_migrations(version) VALUES (7)")
            connection.execute("INSERT INTO schema_migrations(version) VALUES (8)")
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def _validate_existing(self) -> None:
        if (
            not self.anchor_path.is_file()
            or not self.marker_path.is_file()
            or not self.path.is_file()
        ):
            raise ControlStoreUnavailable(
                "Control Store anchor/marker/database identity is absent or incomplete"
            )
        try:
            anchor = read_json(self.anchor_path)
            marker = read_json(self.marker_path)
        except (OSError, json.JSONDecodeError) as exc:
            raise ControlStoreUnavailable(
                f"Control Store identity JSON is unreadable: {exc}"
            ) from exc
        try:
            self.contracts.validate("control-store-identity", anchor)
            self.contracts.validate("control-store-identity", marker)
        except ContractError as exc:
            raise ControlStoreUnavailable(
                f"Control Store identity contract is invalid: {exc}"
            ) from exc
        if anchor != self._identity_record("anchor"):
            raise ControlStoreUnavailable("Control Store anchor identity is invalid")
        if marker != self._identity_record("marker"):
            raise ControlStoreUnavailable("Control Store marker identity is invalid")
        try:
            self._migrate_existing()
        except ControlStoreUnavailable:
            raise
        except (sqlite3.Error, OSError) as exc:
            raise ControlStoreUnavailable(
                f"Control Store migration failed: {exc}"
            ) from exc
        try:
            connection = self._connect_raw()
            try:
                row = connection.execute(
                    "SELECT value FROM control_store_metadata WHERE key='store_id'"
                ).fetchone()
                version = connection.execute(
                    "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
                ).fetchone()[0]
            finally:
                connection.close()
        except sqlite3.Error as exc:
            raise ControlStoreUnavailable(f"Control Store database is invalid: {exc}") from exc
        if row is None or row[0] != self.store_id:
            raise ControlStoreUnavailable("Control Store database identity disagrees with marker")
        if int(version) != SCHEMA_VERSION:
            raise ControlStoreUnavailable(
                f"unknown Control Store schema version: {version}"
            )

    @staticmethod
    def _migration_versions(connection: sqlite3.Connection) -> list[int]:
        versions = [
            int(row[0])
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        ]
        expected = list(range(1, (versions[-1] if versions else 0) + 1))
        if versions != expected:
            raise ControlStoreUnavailable(
                f"Control Store migration ledger is not contiguous: {versions}"
            )
        return versions

    @staticmethod
    def _create_run_state_mutation_table(connection: sqlite3.Connection) -> None:
        connection.execute(RUN_STATE_MUTATION_TABLE_SQL)
        connection.execute(RUN_STATE_MUTATION_INDEX_SQL)
        ControlStore._validate_run_state_mutation_table(connection)

    @staticmethod
    def _validate_run_state_mutation_table(connection: sqlite3.Connection) -> None:
        expected_columns = {
            "mutation_id",
            "operation",
            "run_id",
            "expected_run_revision",
            "old_run_record_sha256",
            "predecessor_committed_sha256",
            "replacement_run_record_sha256",
            "replacement_run_record_json",
            "state",
            "mutation_identity",
        }
        columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(run_state_mutation_intents)"
            ).fetchall()
        }
        if columns != expected_columns:
            raise ControlStoreUnavailable(
                "Control Store run-state mutation table is incomplete"
            )
        indexes = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA index_list(run_state_mutation_intents)"
            ).fetchall()
        }
        if "one_prepared_source_drift_mutation_per_run" not in indexes:
            raise ControlStoreUnavailable(
                "Control Store run-state mutation active-intent index is missing"
            )
        expected_sql = {
            "run_state_mutation_intents": RUN_STATE_MUTATION_TABLE_SQL,
            "one_prepared_source_drift_mutation_per_run": RUN_STATE_MUTATION_INDEX_SQL,
        }
        for name, expected in expected_sql.items():
            row = connection.execute(
                "SELECT sql FROM sqlite_master WHERE name=?", (name,)
            ).fetchone()
            if row is None or _normalized_sql(row[0]) != _normalized_sql(expected):
                raise ControlStoreUnavailable(
                    f"Control Store SQL authority differs for {name}"
                )

    @staticmethod
    def _create_run_state_mutation_identity_table(
        connection: sqlite3.Connection,
    ) -> None:
        connection.execute(RUN_STATE_MUTATION_IDENTITY_VERSIONS_TABLE_SQL)
        ControlStore._validate_run_state_mutation_identity_table(connection)

    @staticmethod
    def _validate_run_state_mutation_identity_table(
        connection: sqlite3.Connection,
    ) -> None:
        columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(run_state_mutation_identity_versions)"
            ).fetchall()
        }
        if columns != {"mutation_id", "identity_version", "row_identity"}:
            raise ControlStoreUnavailable(
                "Control Store run-state mutation identity table is incomplete"
            )
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE "
            "name='run_state_mutation_identity_versions'"
        ).fetchone()
        if row is None or _normalized_sql(row[0]) != _normalized_sql(
            RUN_STATE_MUTATION_IDENTITY_VERSIONS_TABLE_SQL
        ):
            raise ControlStoreUnavailable(
                "Control Store SQL authority differs for "
                "run_state_mutation_identity_versions"
            )

    @staticmethod
    def _create_task_tables(connection: sqlite3.Connection) -> None:
        connection.execute(TASK_CLAIMS_TABLE_SQL)
        connection.execute(TASK_CLAIMS_AUTHORITY_STATE_INDEX_SQL)
        connection.execute(TASK_CLAIM_AUTHORITIES_TABLE_SQL)
        connection.execute(TASK_ATTEMPTS_TABLE_SQL)
        connection.execute(TASK_ATTEMPT_AUTHORITIES_TABLE_SQL)
        connection.execute(TASK_COMPLETION_AUTHORITIES_TABLE_SQL)
        connection.execute(TASK_PROMOTION_TABLE_SQL)
        connection.execute(TASK_PROMOTION_INDEX_SQL)
        connection.execute(TASK_PROMOTION_IDENTITY_VERSIONS_TABLE_SQL)
        connection.execute(TASK_RECLAIM_TRANSITIONS_TABLE_SQL)
        ControlStore._validate_task_tables(connection)
        ControlStore._validate_task_claim_authority_table(connection)
        ControlStore._validate_task_reclaim_transition_table(connection)

    @staticmethod
    def _create_resource_tables(connection: sqlite3.Connection) -> None:
        for statement in (
            RESOURCE_CONFIGURATIONS_TABLE_SQL,
            RESOURCE_CONFIGURATIONS_ACTIVE_INDEX_SQL,
            RESOURCE_SEQUENCES_TABLE_SQL,
            RESOURCE_QUEUE_TABLE_SQL,
            RESOURCE_QUEUE_STATE_INDEX_SQL,
            RESOURCE_LEASES_TABLE_SQL,
            RESOURCE_LEASE_RESOURCES_TABLE_SQL,
            RESOURCE_LEASE_RESOURCES_INDEX_SQL,
            RESOURCE_FAIRNESS_CURSORS_TABLE_SQL,
            RESOURCE_BREAKERS_TABLE_SQL,
            RESOURCE_EVENTS_TABLE_SQL,
        ):
            connection.execute(statement)
        connection.executemany(
            "INSERT OR IGNORE INTO resource_sequences(sequence_name, value) "
            "VALUES (?, 0)",
            [(name,) for name in sorted(RESOURCE_SEQUENCE_NAMES)],
        )

    @staticmethod
    def _insert_resource_configuration(
        connection: sqlite3.Connection,
        configuration: dict,
        configuration_json: str,
        configuration_sha256: str,
    ) -> None:
        connection.execute(
            "INSERT INTO resource_configurations("
            "configuration_id, schema_version, configuration_version, "
            "configuration_sha256, configuration_json, state) "
            "VALUES (?, ?, ?, ?, ?, 'ACTIVE')",
            (
                configuration["configuration_id"],
                configuration["schema_version"],
                configuration["configuration_version"],
                configuration_sha256,
                configuration_json,
            ),
        )

    @staticmethod
    def _validated_resource_json(value: object, owner: str) -> tuple[object, str, str]:
        try:
            parsed = json.loads(str(value))
            canonical = canonical_json_bytes(parsed).decode("utf-8")
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ControlStoreUnavailable(f"{owner} JSON is invalid") from exc
        fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if str(value) != canonical:
            raise ControlStoreUnavailable(f"{owner} JSON is not canonical")
        return parsed, canonical, fingerprint

    def _validate_resource_terminal_evidence(
        self,
        lease: sqlite3.Row,
        terminal_json: object,
        terminal_sha256: object,
        *,
        allowed_evidence_classes: set[str],
    ) -> dict[str, object]:
        terminal, _, terminal_fingerprint = self._validated_resource_json(
            terminal_json,
            "Resource Lease terminal evidence",
        )
        if str(terminal_sha256) != terminal_fingerprint:
            raise ControlStoreUnavailable(
                "Resource Lease terminal evidence fingerprint is invalid"
            )
        if not isinstance(terminal, dict):
            raise ControlStoreUnavailable(
                "Resource Lease terminal evidence is invalid"
            )
        try:
            self.contracts.validate(
                "resource-lease-resolution-evidence",
                terminal,
            )
        except ContractError as exc:
            raise ControlStoreUnavailable(
                "Resource Lease terminal evidence is invalid"
            ) from exc
        if (
            terminal["lease_id"] != lease["lease_id"]
            or terminal["attempt_id"] != lease["attempt_id"]
            or int(terminal["claim_generation"])
            != int(lease["claim_generation"])
            or terminal["evidence_class"] not in allowed_evidence_classes
        ):
            raise ControlStoreUnavailable(
                "Resource Lease terminal evidence identity disagrees"
            )
        return terminal

    def _validate_resource_lease_lifecycle(
        self,
        lease: sqlite3.Row,
        required_resources: tuple[str, ...],
    ) -> None:
        state = str(lease["state"])
        authorization = str(lease["launch_authorization_state"])
        launch_json = lease["launch_required_resources_json"]
        launch_sha256 = lease["launch_required_resources_sha256"]
        authorized_at = lease["launch_authorized_at"]
        completed_at = lease["launch_completed_at"]
        execution_json = lease["launch_execution_identity_json"]
        execution_sha256 = lease["launch_execution_identity_sha256"]
        terminal_json = lease["terminal_evidence_json"]
        terminal_sha256 = lease["terminal_evidence_sha256"]

        if any(
            not str(lease[field]).strip()
            for field in (
                "lease_id",
                "queue_id",
                "task_id",
                "attempt_id",
                "coordinator_session_id",
                "worker_id",
                "launch_token",
                "updated_at",
            )
        ):
            raise ControlStoreUnavailable("Resource Lease identity is incomplete")
        if (launch_json is None) != (launch_sha256 is None):
            raise ControlStoreUnavailable(
                "Resource Lease launch Resource set fingerprint is incomplete"
            )
        if (execution_json is None) != (execution_sha256 is None):
            raise ControlStoreUnavailable(
                "Resource Lease launch execution identity fingerprint is incomplete"
            )
        if (terminal_json is None) != (terminal_sha256 is None):
            raise ControlStoreUnavailable(
                "Resource Lease terminal evidence fingerprint is incomplete"
            )

        if authorization == "AVAILABLE":
            if any(
                value is not None
                for value in (
                    launch_json,
                    launch_sha256,
                    authorized_at,
                    completed_at,
                    execution_json,
                    execution_sha256,
                )
            ):
                raise ControlStoreUnavailable(
                    "available Resource launch authority retains consumed fields"
                )
        else:
            if launch_json is None or authorized_at is None:
                raise ControlStoreUnavailable(
                    "consumed Resource launch authority lacks its immutable Resource set"
                )
            parsed_launch, _, parsed_launch_sha256 = self._validated_resource_json(
                launch_json,
                "Resource Lease launch Resource set",
            )
            if (
                parsed_launch != list(required_resources)
                or str(launch_sha256) != parsed_launch_sha256
            ):
                raise ControlStoreUnavailable(
                    "Resource Lease launch Resource set differs from normalized authority"
                )
            if authorization == "CONSUMED":
                if any(
                    value is not None
                    for value in (
                        completed_at,
                        execution_json,
                        execution_sha256,
                    )
                ):
                    raise ControlStoreUnavailable(
                        "consumed Resource launch authority has completion-only fields"
                    )
            elif authorization == "COMPLETED":
                if completed_at is None:
                    raise ControlStoreUnavailable(
                        "completed Resource launch authority lacks completion time"
                    )
                if execution_json is not None:
                    execution, _, execution_fingerprint = (
                        self._validated_resource_json(
                            execution_json,
                            "Resource Lease launch execution identity",
                        )
                    )
                    if (
                        not isinstance(execution, dict)
                        or set(execution)
                        != {"pid", "process_creation_identity", "launch_token"}
                        or not isinstance(execution["pid"], int)
                        or execution["pid"] < 1
                        or not isinstance(
                            execution["process_creation_identity"], str
                        )
                        or not execution["process_creation_identity"].strip()
                        or execution["launch_token"] != lease["launch_token"]
                        or str(execution_sha256) != execution_fingerprint
                    ):
                        raise ControlStoreUnavailable(
                            "Resource Lease launch execution identity is invalid"
                        )

        if state == "starting" and authorization not in {"AVAILABLE", "CONSUMED"}:
            raise ControlStoreUnavailable(
                "starting Resource Lease has an invalid launch authority state"
            )
        if state in {"active", "released"} and authorization != "COMPLETED":
            raise ControlStoreUnavailable(
                f"{state} Resource Lease lacks completed launch authority"
            )
        if state in {"starting", "active", "unknown"}:
            if terminal_json is not None:
                raise ControlStoreUnavailable(
                    "non-terminal Resource Lease retains terminal evidence"
                )
            return
        if state not in {"released", "resolved"} or terminal_json is None:
            raise ControlStoreUnavailable(
                "terminal Resource Lease lacks terminal evidence"
            )
        self._validate_resource_terminal_evidence(
            lease,
            terminal_json,
            terminal_sha256,
            allowed_evidence_classes=(
                {"provider_terminal_result", "local_process_terminated"}
                if state == "released"
                else {
                    "provider_terminal_result",
                    "local_process_terminated",
                    "explicit_human_resolution",
                }
            ),
        )

    def _validate_resource_tables(self, connection: sqlite3.Connection) -> None:
        expected_objects = {
            ("table", "resource_configurations"): RESOURCE_CONFIGURATIONS_TABLE_SQL,
            ("table", "resource_sequences"): RESOURCE_SEQUENCES_TABLE_SQL,
            ("table", "resource_queue_entries"): RESOURCE_QUEUE_TABLE_SQL,
            ("table", "resource_leases"): RESOURCE_LEASES_TABLE_SQL,
            ("table", "resource_lease_resources"): RESOURCE_LEASE_RESOURCES_TABLE_SQL,
            ("table", "resource_fairness_cursors"): RESOURCE_FAIRNESS_CURSORS_TABLE_SQL,
            ("table", "resource_circuit_breakers"): RESOURCE_BREAKERS_TABLE_SQL,
            ("table", "resource_control_events"): RESOURCE_EVENTS_TABLE_SQL,
            ("index", "one_active_resource_configuration"): RESOURCE_CONFIGURATIONS_ACTIVE_INDEX_SQL,
            ("index", "resource_queue_by_state_enqueue"): RESOURCE_QUEUE_STATE_INDEX_SQL,
            ("index", "resource_lease_resources_by_class"): RESOURCE_LEASE_RESOURCES_INDEX_SQL,
        }
        actual_objects = {
            (str(row["type"]), str(row["name"])): str(row["sql"])
            for row in connection.execute(
                "SELECT type, name, sql FROM sqlite_master "
                "WHERE name LIKE 'resource_%' OR name='one_active_resource_configuration'"
            ).fetchall()
            if row["sql"] is not None
        }
        for identity, expected_sql in expected_objects.items():
            actual_sql = actual_objects.get(identity)
            if actual_sql is None or _normalized_sql(actual_sql) != _normalized_sql(
                expected_sql
            ):
                raise ControlStoreUnavailable(
                    f"Control Store resource schema object is absent or altered: {identity[1]}"
                )
        unexpected_objects = set(actual_objects) - set(expected_objects)
        if unexpected_objects:
            unexpected = sorted(name for _, name in unexpected_objects)
            raise ControlStoreUnavailable(
                "Control Store has unsupported resource schema objects: "
                + ", ".join(unexpected)
            )
        active = connection.execute(
            "SELECT * FROM resource_configurations WHERE state='ACTIVE'"
        ).fetchall()
        if len(active) != 1:
            raise ControlStoreUnavailable(
                "Control Store must have exactly one active Resource Admission Configuration"
            )
        configurations: dict[str, sqlite3.Row] = {}
        for row in connection.execute(
            "SELECT * FROM resource_configurations ORDER BY configuration_version"
        ).fetchall():
            try:
                stored = json.loads(str(row["configuration_json"]))
                self.contracts.validate(
                    "resource-admission-configuration", stored
                )
            except (json.JSONDecodeError, ContractError) as exc:
                raise ControlStoreUnavailable(
                    "Resource Admission Configuration authority is invalid"
                ) from exc
            canonical = canonical_json_bytes(stored).decode("utf-8")
            fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            if (
                str(row["configuration_json"]) != canonical
                or str(row["configuration_sha256"]) != fingerprint
                or str(row["configuration_id"]) != stored["configuration_id"]
                or str(row["schema_version"]) != stored["schema_version"]
                or int(row["configuration_version"])
                != int(stored["configuration_version"])
            ):
                raise ControlStoreUnavailable(
                    "Resource Admission Configuration fingerprint is invalid"
                )
            configurations[str(row["configuration_id"])] = row
        if int(active[0]["configuration_version"]) != max(
            int(row["configuration_version"]) for row in configurations.values()
        ):
            raise ControlStoreUnavailable(
                "active Resource Admission Configuration is not the highest version"
            )

        def validate_configuration_binding(
            *, identity: object, version: object, fingerprint: object, owner: str
        ) -> None:
            configuration = configurations.get(str(identity))
            if (
                configuration is None
                or int(configuration["configuration_version"]) != int(version)
                or str(configuration["configuration_sha256"]) != str(fingerprint)
            ):
                raise ControlStoreUnavailable(
                    f"{owner} Resource Admission Configuration binding is invalid"
                )

        queue_rows = connection.execute(
            "SELECT * FROM resource_queue_entries ORDER BY enqueue_seq"
        ).fetchall()
        queues_by_id = {str(row["queue_id"]): row for row in queue_rows}
        claim_rows = {
            str(row["task_id"]): row
            for row in connection.execute("SELECT * FROM task_claims").fetchall()
        }
        attempt_authorities: dict[str, tuple[str, int, str]] = {}
        for row in connection.execute(
            "SELECT a.attempt_id, a.task_id, a.claim_generation, "
            "aa.attempt_record_json FROM task_attempts a "
            "JOIN task_attempt_authorities aa ON aa.attempt_id=a.attempt_id"
        ).fetchall():
            try:
                attempt_record = json.loads(str(row["attempt_record_json"]))
                envelope_sha256 = str(attempt_record["task_envelope_sha256"])
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                raise ControlStoreUnavailable(
                    "Resource Queue Task Attempt authority is unreadable"
                ) from exc
            attempt_authorities[str(row["attempt_id"])] = (
                str(row["task_id"]),
                int(row["claim_generation"]),
                envelope_sha256,
            )
        lease_rows = connection.execute(
            "SELECT * FROM resource_leases ORDER BY admitted_seq"
        ).fetchall()
        leases_by_id = {str(row["lease_id"]): row for row in lease_rows}
        leases_by_queue: dict[str, list[sqlite3.Row]] = {}
        for lease in lease_rows:
            leases_by_queue.setdefault(str(lease["queue_id"]), []).append(lease)
            validate_configuration_binding(
                identity=lease["admission_configuration_id"],
                version=lease["admission_configuration_version"],
                fingerprint=lease["admission_configuration_sha256"],
                owner="Resource Lease",
            )
        active_reservations: list[set[str]] = []
        maximum_sequences: dict[str, int] = {
            name: 0 for name in RESOURCE_SEQUENCE_NAMES
        }
        for queue in queue_rows:
            queue_state = str(queue["state"])
            if queue_state not in {"QUEUED", "ADMITTED", "INVALIDATED"}:
                raise ControlStoreUnavailable(
                    "Resource Queue state is unsupported"
                )
            validate_configuration_binding(
                identity=queue["enqueue_configuration_id"],
                version=queue["enqueue_configuration_version"],
                fingerprint=queue["enqueue_configuration_sha256"],
                owner="Resource Queue",
            )
            blocked_binding = (
                queue["last_blocked_configuration_id"],
                queue["last_blocked_configuration_version"],
                queue["last_blocked_configuration_sha256"],
            )
            if any(value is None for value in blocked_binding):
                if any(value is not None for value in blocked_binding):
                    raise ControlStoreUnavailable(
                        "Resource Queue configuration-block binding is incomplete"
                    )
            else:
                validate_configuration_binding(
                    identity=blocked_binding[0],
                    version=blocked_binding[1],
                    fingerprint=blocked_binding[2],
                    owner="Resource Queue configuration-block",
                )
            required, _, _ = self._validated_resource_json(
                queue["required_resources_json"],
                "Resource Queue request",
            )
            if (
                not isinstance(required, list)
                or not required
                or required != sorted(required)
                or len(required) != len(set(required))
            ):
                raise ControlStoreUnavailable(
                    "Resource Queue request is not a stable unique set"
                )
            supported_resources = {
                "bilibili_download",
                "youtube_download",
                "whisper",
                "codex_semantic",
                "latex",
                "pdf_render",
                "visual_acceptance",
            }
            if any(resource not in supported_resources for resource in required):
                raise ControlStoreUnavailable(
                    "Resource Queue request names an unsupported Resource Class"
                )
            claim = claim_rows.get(str(queue["task_id"]))
            attempt_authority = attempt_authorities.get(str(queue["attempt_id"]))
            if (
                claim is None
                or str(claim["authority_id"]) != str(queue["run_id"])
                or attempt_authority is None
                or attempt_authority[0] != str(queue["task_id"])
                or attempt_authority[1] != int(queue["claim_generation"])
            ):
                raise ControlStoreUnavailable(
                    "Resource Queue request lacks its Task Claim authority"
                )
            expected_request_binding = self._resource_request_binding_sha256(
                task_id=str(queue["task_id"]),
                attempt_id=str(queue["attempt_id"]),
                claim_generation=int(queue["claim_generation"]),
                run_id=str(queue["run_id"]),
                envelope_sha256=attempt_authority[2],
                required_resources=tuple(str(resource) for resource in required),
                fairness_group_id=str(queue["fairness_group_id"]),
                batch_id=(
                    None if queue["batch_id"] is None else str(queue["batch_id"])
                ),
            )
            if str(queue["request_binding_sha256"]) != expected_request_binding:
                raise ControlStoreUnavailable(
                    "Resource Queue immutable request binding is invalid"
                )
            queue_leases = leases_by_queue.pop(str(queue["queue_id"]), [])
            if queue_state == "ADMITTED":
                if (
                    len(queue_leases) != 1
                    or queue["lease_id"] is None
                    or queue["admitted_seq"] is None
                ):
                    raise ControlStoreUnavailable(
                        "admitted Resource Queue lacks exactly one Lease"
                    )
                lease = queue_leases[0]
                if (
                    str(lease["lease_id"]) != str(queue["lease_id"])
                    or str(lease["task_id"]) != str(queue["task_id"])
                    or str(lease["attempt_id"]) != str(queue["attempt_id"])
                    or int(lease["claim_generation"])
                    != int(queue["claim_generation"])
                    or str(lease["launch_token"]) != str(queue["launch_token"])
                    or int(lease["admitted_seq"]) != int(queue["admitted_seq"])
                ):
                    raise ControlStoreUnavailable(
                        "Resource Queue and Lease identities disagree"
                    )
                normalized = {
                    str(row["resource_class"])
                    for row in connection.execute(
                        "SELECT resource_class FROM resource_lease_resources "
                        "WHERE lease_id=?",
                        (lease["lease_id"],),
                    ).fetchall()
                }
                if normalized != set(required) or len(normalized) != len(required):
                    raise ControlStoreUnavailable(
                        "Resource Lease normalized resources differ from its immutable request"
                    )
                self._validate_resource_lease_lifecycle(
                    lease,
                    tuple(str(resource) for resource in required),
                )
                maximum_sequences["admission"] = max(
                    maximum_sequences.get("admission", 0),
                    int(queue["admitted_seq"]),
                )
            elif queue_leases or queue["lease_id"] is not None or queue[
                "admitted_seq"
            ] is not None:
                raise ControlStoreUnavailable(
                    "non-admitted Resource Queue retains Lease authority"
                )
            reservation_state = str(queue["reservation_state"])
            reservation_seq = queue["reservation_seq"]
            if reservation_state == "NONE":
                if reservation_seq is not None:
                    raise ControlStoreUnavailable(
                        "Resource Queue has a reservation sequence without a reservation"
                    )
            else:
                if reservation_seq is None or int(reservation_seq) < 1:
                    raise ControlStoreUnavailable(
                        "Resource Queue reservation lacks its stable sequence"
                    )
                maximum_sequences["reservation"] = max(
                    maximum_sequences.get("reservation", 0), int(reservation_seq)
                )
            if (
                reservation_state in {"ACTIVE", "PENDING"}
                and queue_state != "QUEUED"
            ) or (
                reservation_state == "TERMINATED"
                and queue_state not in {"ADMITTED", "INVALIDATED"}
            ):
                raise ControlStoreUnavailable(
                    "Resource reservation lifecycle is invalid"
                )
            if reservation_state == "ACTIVE":
                required_set = set(required)
                if any(
                    not required_set.isdisjoint(existing)
                    for existing in active_reservations
                ):
                    raise ControlStoreUnavailable(
                        "active Draining Reservations overlap"
                    )
                active_reservations.append(required_set)
            maximum_sequences["enqueue"] = max(
                maximum_sequences.get("enqueue", 0), int(queue["enqueue_seq"])
            )
        if leases_by_queue:
            raise ControlStoreUnavailable(
                "Resource Lease lacks its admitted Queue authority"
            )
        event_counts: dict[tuple[str, str], int] = {}
        event_sequences: dict[tuple[str, str], list[int]] = {}
        latest_configuration_block: dict[str, sqlite3.Row] = {}
        latest_breaker_payload: dict[str, dict] = {}
        configuration_activation_counts: dict[str, int] = {}
        breaker_event_count = 0
        event_rows = connection.execute(
            "SELECT * FROM resource_control_events ORDER BY event_seq"
        ).fetchall()
        for event in event_rows:
            validate_configuration_binding(
                identity=event["configuration_id"],
                version=event["configuration_version"],
                fingerprint=event["configuration_sha256"],
                owner="Resource Control Event",
            )
            event_kind = str(event["event_kind"])
            if event_kind not in RESOURCE_EVENT_KINDS:
                raise ControlStoreUnavailable(
                    "Resource Control Event kind is unsupported"
                )
            payload, payload_json, _ = self._validated_resource_json(
                event["payload_json"],
                "Resource Control Event payload",
            )
            queue_id = None if event["queue_id"] is None else str(event["queue_id"])
            lease_id = None if event["lease_id"] is None else str(event["lease_id"])
            queue = None if queue_id is None else queues_by_id.get(queue_id)
            lease = None if lease_id is None else leases_by_id.get(lease_id)
            no_reference_kinds = {
                "configuration_activated",
                "breaker_opened",
                "breaker_closed",
            }
            queue_only_kinds = {
                "enqueued",
                "bypassed",
                "reservation_pending",
                "reservation_activated",
                "configuration_blocked",
                "invalidated_by_reclaim",
            }
            queue_and_lease_kinds = {
                "admitted",
                "released",
                "lease_unknown",
                "lease_resolved",
            }
            if event_kind in no_reference_kinds:
                if queue_id is not None or lease_id is not None:
                    raise ControlStoreUnavailable(
                        "Resource Control Event has forbidden queue or Lease references"
                    )
            elif event_kind in queue_only_kinds:
                if queue is None or lease_id is not None:
                    raise ControlStoreUnavailable(
                        "Resource Control Event queue reference is invalid"
                    )
            elif event_kind in queue_and_lease_kinds:
                if (
                    queue is None
                    or lease is None
                    or str(lease["queue_id"]) != queue_id
                    or str(queue["lease_id"]) != lease_id
                ):
                    raise ControlStoreUnavailable(
                        "Resource Control Event Queue and Lease identities disagree"
                    )
            if queue_id is not None:
                event_counts[(queue_id, event_kind)] = (
                    event_counts.get((queue_id, event_kind), 0) + 1
                )
                event_sequences.setdefault((queue_id, event_kind), []).append(
                    int(event["event_seq"])
                )

            if event_kind in {
                "enqueued",
                "admitted",
                "bypassed",
                "reservation_pending",
                "reservation_activated",
                "invalidated_by_reclaim",
            } and payload != {}:
                raise ControlStoreUnavailable(
                    "Resource Control Event empty payload contract is invalid"
                )
            if event_kind in {"enqueued", "invalidated_by_reclaim"}:
                if (
                    event["configuration_id"] != queue["enqueue_configuration_id"]
                    or int(event["configuration_version"])
                    != int(queue["enqueue_configuration_version"])
                    or event["configuration_sha256"]
                    != queue["enqueue_configuration_sha256"]
                ):
                    raise ControlStoreUnavailable(
                        "Resource Queue lifecycle Event configuration identity disagrees"
                    )
            if event_kind in {"admitted", "released", "lease_resolved"}:
                if (
                    event["configuration_id"]
                    != lease["admission_configuration_id"]
                    or int(event["configuration_version"])
                    != int(lease["admission_configuration_version"])
                    or event["configuration_sha256"]
                    != lease["admission_configuration_sha256"]
                ):
                    raise ControlStoreUnavailable(
                        "Resource Lease lifecycle Event configuration identity disagrees"
                    )
            if event_kind == "configuration_blocked":
                if payload != {"reason": "configuration_capacity"}:
                    raise ControlStoreUnavailable(
                        "Resource configuration-block Event payload is invalid"
                    )
                latest_configuration_block[queue_id] = event
            elif event_kind == "configuration_activated":
                configuration = configurations[str(event["configuration_id"])]
                if payload_json != str(configuration["configuration_json"]):
                    raise ControlStoreUnavailable(
                        "Resource configuration activation Event payload disagrees"
                    )
                configuration_activation_counts[str(event["configuration_id"])] = (
                    configuration_activation_counts.get(
                        str(event["configuration_id"]), 0
                    )
                    + 1
                )
            elif event_kind == "released":
                if payload_json != str(lease["terminal_evidence_json"]):
                    raise ControlStoreUnavailable(
                        "Resource release Event terminal evidence disagrees"
                    )
            elif event_kind == "lease_resolved":
                if payload_json != str(lease["terminal_evidence_json"]):
                    raise ControlStoreUnavailable(
                        "Resource resolution Event terminal evidence disagrees"
                    )
            elif event_kind == "lease_unknown":
                reconciliation_fields = {
                    "current_coordinator_session_id",
                    "lost_coordinator_session_id",
                    "prior_worker_id",
                    "attempt_id",
                    "claim_generation",
                }
                launch_failure_fields = {
                    "cause",
                    "attempt_id",
                    "claim_generation",
                    "failure_stage",
                }
                if not isinstance(payload, dict):
                    raise ControlStoreUnavailable(
                        "Resource unknown-Lease Event identity is invalid"
                    )
                if set(payload) == reconciliation_fields:
                    invalid_unknown_identity = (
                        payload["lost_coordinator_session_id"]
                        != lease["coordinator_session_id"]
                        or payload["prior_worker_id"] != lease["worker_id"]
                        or payload["attempt_id"] != lease["attempt_id"]
                        or int(payload["claim_generation"])
                        != int(lease["claim_generation"])
                        or not isinstance(
                            payload["current_coordinator_session_id"], str
                        )
                        or not payload["current_coordinator_session_id"].strip()
                    )
                elif set(payload) == launch_failure_fields:
                    invalid_unknown_identity = (
                        payload["cause"] != "launch_outcome_unconfirmed"
                        or payload["attempt_id"] != lease["attempt_id"]
                        or int(payload["claim_generation"])
                        != int(lease["claim_generation"])
                        or payload["failure_stage"]
                        not in RESOURCE_LAUNCH_FAILURE_STAGES
                    )
                else:
                    invalid_unknown_identity = True
                if invalid_unknown_identity:
                    raise ControlStoreUnavailable(
                        "Resource unknown-Lease Event identity is invalid"
                    )
            elif event_kind in {"breaker_opened", "breaker_closed"}:
                breaker_event_count += 1
                expected_state = (
                    "open" if event_kind == "breaker_opened" else "closed"
                )
                if (
                    not isinstance(payload, dict)
                    or set(payload)
                    != {
                        "breaker_key",
                        "resource_class",
                        "scope_kind",
                        "platform",
                        "state",
                        "reason",
                    }
                    or payload["resource_class"] not in RESOURCE_CLASSES
                    or payload["state"] != expected_state
                    or not isinstance(payload["reason"], str)
                    or not payload["reason"].strip()
                ):
                    raise ControlStoreUnavailable(
                        "Resource Circuit Breaker Event payload is invalid"
                    )
                if payload["platform"] is None:
                    expected_key = f"resource:{payload['resource_class']}"
                    expected_scope = "resource"
                else:
                    expected_key = (
                        f"platform:{payload['platform']}:"
                        f"{payload['resource_class']}"
                    )
                    expected_scope = "platform"
                    if (
                        payload["platform"] not in {"bilibili", "youtube"}
                        or payload["resource_class"]
                        != f"{payload['platform']}_download"
                    ):
                        raise ControlStoreUnavailable(
                            "Resource Circuit Breaker platform identity is invalid"
                        )
                if (
                    payload["breaker_key"] != expected_key
                    or payload["scope_kind"] != expected_scope
                ):
                    raise ControlStoreUnavailable(
                        "Resource Circuit Breaker Event key is invalid"
                    )
                latest_breaker_payload[str(payload["breaker_key"])] = payload
            maximum_sequences["event"] = int(event["event_seq"])

        for queue in queue_rows:
            queue_id = str(queue["queue_id"])
            if event_counts.get((queue_id, "enqueued"), 0) != 1:
                raise ControlStoreUnavailable(
                    "Resource Queue lacks exactly one enqueue Event"
                )
            admitted_events = event_counts.get((queue_id, "admitted"), 0)
            invalidated_events = event_counts.get(
                (queue_id, "invalidated_by_reclaim"), 0
            )
            if admitted_events != (1 if queue["state"] == "ADMITTED" else 0):
                raise ControlStoreUnavailable(
                    "Resource Queue admission Event coverage is invalid"
                )
            if invalidated_events != (
                1 if queue["state"] == "INVALIDATED" else 0
            ):
                raise ControlStoreUnavailable(
                    "Resource Queue invalidation Event coverage is invalid"
                )
            if int(queue["bypass_count"]) != event_counts.get(
                (queue_id, "bypassed"), 0
            ):
                raise ControlStoreUnavailable(
                    "Resource Queue bypass count disagrees with durable Events"
                )
            reservation_state = str(queue["reservation_state"])
            pending_events = event_sequences.get(
                (queue_id, "reservation_pending"), []
            )
            activated_events = event_sequences.get(
                (queue_id, "reservation_activated"), []
            )
            reservation_events_valid = (
                len(pending_events) <= 1
                and len(activated_events) <= 1
                and (
                    not pending_events
                    or not activated_events
                    or pending_events[0] < activated_events[0]
                )
            )
            if reservation_state == "NONE":
                reservation_events_valid = (
                    reservation_events_valid
                    and not pending_events
                    and not activated_events
                )
            elif reservation_state == "PENDING":
                reservation_events_valid = (
                    reservation_events_valid
                    and len(pending_events) == 1
                    and not activated_events
                )
            elif reservation_state == "ACTIVE":
                reservation_events_valid = (
                    reservation_events_valid
                    and len(activated_events) == 1
                )
            elif reservation_state == "TERMINATED":
                terminal_events = (
                    event_sequences.get((queue_id, "admitted"), [])
                    + event_sequences.get(
                        (queue_id, "invalidated_by_reclaim"), []
                    )
                )
                reservation_history = pending_events + activated_events
                reservation_events_valid = (
                    reservation_events_valid
                    and bool(reservation_history)
                    and len(terminal_events) == 1
                    and max(reservation_history) < terminal_events[0]
                )
            else:
                reservation_events_valid = False
            if not reservation_events_valid:
                raise ControlStoreUnavailable(
                    "Resource reservation Event lifecycle is invalid"
                )
            block = latest_configuration_block.get(queue_id)
            blocked_binding = (
                queue["last_blocked_configuration_id"],
                queue["last_blocked_configuration_version"],
                queue["last_blocked_configuration_sha256"],
            )
            if block is None:
                if any(value is not None for value in blocked_binding):
                    raise ControlStoreUnavailable(
                        "Resource Queue configuration-block Event is absent"
                    )
            elif blocked_binding != (
                block["configuration_id"],
                block["configuration_version"],
                block["configuration_sha256"],
            ):
                raise ControlStoreUnavailable(
                    "Resource Queue latest configuration-block Event disagrees"
                )

        for lease in lease_rows:
            queue_id = str(lease["queue_id"])
            state = str(lease["state"])
            if event_counts.get((queue_id, "released"), 0) != (
                1 if state == "released" else 0
            ):
                raise ControlStoreUnavailable(
                    "Resource Lease release Event coverage is invalid"
                )
            if event_counts.get((queue_id, "lease_resolved"), 0) != (
                1 if state == "resolved" else 0
            ):
                raise ControlStoreUnavailable(
                    "Resource Lease resolution Event coverage is invalid"
                )
            if event_counts.get((queue_id, "lease_unknown"), 0) != (
                1 if state in {"unknown", "resolved"} else 0
            ):
                raise ControlStoreUnavailable(
                    "Resource Lease unknown-state Event coverage is invalid"
                )

        ordered_configurations = sorted(
            configurations.values(), key=lambda row: int(row["configuration_version"])
        )
        for index, configuration in enumerate(ordered_configurations):
            expected = 0 if index == 0 else 1
            if configuration_activation_counts.get(
                str(configuration["configuration_id"]), 0
            ) != expected:
                raise ControlStoreUnavailable(
                    "Resource Configuration activation Event coverage is invalid"
                )

        cursor_rows = connection.execute(
            "SELECT * FROM resource_fairness_cursors"
        ).fetchall()
        cursor_sequences: set[int] = set()
        group_cursor_count = 0
        run_cursor_scopes: set[str] = set()
        known_groups = {str(queue["fairness_group_id"]) for queue in queue_rows}
        known_runs_by_group = {
            group: {
                str(queue["run_id"])
                for queue in queue_rows
                if str(queue["fairness_group_id"]) == group
            }
            for group in known_groups
        }
        for cursor in cursor_rows:
            scheduling_seq = int(cursor["scheduling_seq"])
            if scheduling_seq < 1 or scheduling_seq in cursor_sequences:
                raise ControlStoreUnavailable(
                    "Resource fairness cursor sequence is invalid"
                )
            cursor_sequences.add(scheduling_seq)
            level = str(cursor["level"])
            scope_id = str(cursor["scope_id"])
            cursor_value = str(cursor["cursor_value"])
            if level == "GROUP":
                group_cursor_count += 1
                if scope_id != "global" or cursor_value not in known_groups:
                    raise ControlStoreUnavailable(
                        "Resource GROUP fairness cursor identity is invalid"
                    )
            elif (
                level != "RUN"
                or scope_id not in known_groups
                or cursor_value not in known_runs_by_group[scope_id]
            ):
                raise ControlStoreUnavailable(
                    "Resource RUN fairness cursor identity is invalid"
                )
            else:
                run_cursor_scopes.add(scope_id)
            maximum_sequences["scheduling"] = max(
                maximum_sequences.get("scheduling", 0),
                scheduling_seq,
            )
        admitted_groups = {
            str(queue["fairness_group_id"])
            for queue in queue_rows
            if queue["state"] == "ADMITTED"
        }
        if group_cursor_count != (1 if admitted_groups else 0) or not admitted_groups.issubset(
            run_cursor_scopes
        ):
            raise ControlStoreUnavailable(
                "Resource fairness cursor coverage is invalid"
            )

        breaker_rows = connection.execute(
            "SELECT * FROM resource_circuit_breakers"
        ).fetchall()
        breaker_sequences: set[int] = set()
        for breaker in breaker_rows:
            resource_class = str(breaker["resource_class"])
            platform = None if breaker["platform"] is None else str(breaker["platform"])
            if platform is None:
                expected_key = f"resource:{resource_class}"
            else:
                expected_key = f"platform:{platform}:{resource_class}"
                if (
                    platform not in {"bilibili", "youtube"}
                    or resource_class != f"{platform}_download"
                ):
                    raise ControlStoreUnavailable(
                        "Resource Circuit Breaker platform binding is invalid"
                    )
            updated_seq = int(breaker["updated_seq"])
            latest = latest_breaker_payload.get(str(breaker["breaker_key"]))
            if (
                resource_class not in RESOURCE_CLASSES
                or str(breaker["breaker_key"]) != expected_key
                or not str(breaker["reason"] or "").strip()
                or updated_seq < 1
                or updated_seq in breaker_sequences
                or latest is None
                or latest["resource_class"] != resource_class
                or latest["platform"] != platform
                or latest["state"] != str(breaker["state"]).lower()
                or latest["reason"] != breaker["reason"]
            ):
                raise ControlStoreUnavailable(
                    "Resource Circuit Breaker durable identity is invalid"
                )
            breaker_sequences.add(updated_seq)
            maximum_sequences["breaker"] = max(
                maximum_sequences.get("breaker", 0),
                updated_seq,
            )
        sequence_values = {
            str(row["sequence_name"]): int(row["value"])
            for row in connection.execute(
                "SELECT * FROM resource_sequences"
            ).fetchall()
        }
        if set(sequence_values) != RESOURCE_SEQUENCE_NAMES:
            raise ControlStoreUnavailable(
                "Resource sequence authority names are not a closed set"
            )
        if breaker_event_count != sequence_values["breaker"]:
            raise ControlStoreUnavailable(
                "Resource Circuit Breaker Events do not cover its sequence authority"
            )
        # Fairness cursor rows intentionally overwrite prior positions. Their
        # durable proof is therefore the latest unique scheduling sequence and
        # maximum authority, unlike append-only Queue, Lease, Reservation, and
        # Event identities which must retain every value from 1 through N.
        for name, maximum in maximum_sequences.items():
            if sequence_values[name] != maximum:
                raise ControlStoreUnavailable(
                    f"Resource sequence disagrees with durable {name} authority"
                )
        append_only_identities = {
            "enqueue": {int(queue["enqueue_seq"]) for queue in queue_rows},
            "admission": {
                int(queue["admitted_seq"])
                for queue in queue_rows
                if queue["admitted_seq"] is not None
            },
            "reservation": {
                int(queue["reservation_seq"])
                for queue in queue_rows
                if queue["reservation_seq"] is not None
            },
            "event": {int(event["event_seq"]) for event in event_rows},
        }
        for name, identities in append_only_identities.items():
            ordered_identities = sorted(identities)
            if sequence_values[name] != len(ordered_identities) or any(
                identity != expected
                for expected, identity in enumerate(ordered_identities, start=1)
            ):
                raise ControlStoreUnavailable(
                    f"Resource {name} sequence identity is not append-only"
                )

    def activate_resource_configuration(
        self,
        *,
        configuration: dict,
        configuration_json: str,
        configuration_sha256: str,
        activated_at: str,
        resource_scheduler: Callable[[sqlite3.Connection, str], None],
    ) -> sqlite3.Row:
        with self._immediate() as connection:
            active = self._active_resource_configuration_row(connection)
            if active["configuration_id"] == configuration["configuration_id"]:
                if (
                    int(active["configuration_version"])
                    == int(configuration["configuration_version"])
                    and active["configuration_sha256"] == configuration_sha256
                    and active["configuration_json"] == configuration_json
                ):
                    return active
                raise KernelConflict(
                    "Resource Admission Configuration identity replay conflicts"
                )
            if int(configuration["configuration_version"]) <= int(
                active["configuration_version"]
            ):
                raise ContractError(
                    "Resource Admission Configuration version must increase monotonically"
                )
            connection.execute(
                "UPDATE resource_configurations SET state='RETIRED' "
                "WHERE configuration_id=? AND state='ACTIVE'",
                (active["configuration_id"],),
            )
            self._insert_resource_configuration(
                connection,
                configuration,
                configuration_json,
                configuration_sha256,
            )
            event_seq = self._next_resource_sequence(connection, "event")
            connection.execute(
                "INSERT INTO resource_control_events("
                "event_seq, event_kind, queue_id, lease_id, configuration_id, "
                "configuration_version, configuration_sha256, payload_json) "
                "VALUES (?, 'configuration_activated', NULL, NULL, ?, ?, ?, ?)",
                (
                    event_seq,
                    configuration["configuration_id"],
                    configuration["configuration_version"],
                    configuration_sha256,
                    configuration_json,
                ),
            )
            resource_scheduler(connection, activated_at)
            return self._active_resource_configuration_row(connection)

    @staticmethod
    def _create_task_tables_v4(connection: sqlite3.Connection) -> None:
        connection.execute(TASK_CLAIMS_TABLE_SQL)
        connection.execute(TASK_CLAIMS_AUTHORITY_STATE_INDEX_SQL)
        connection.execute(TASK_ATTEMPTS_TABLE_SQL)
        connection.execute(TASK_PROMOTION_TABLE_SQL)
        connection.execute(TASK_PROMOTION_INDEX_SQL)
        ControlStore._validate_task_tables(
            connection,
            completion_record_authority=False,
        )

    @staticmethod
    def _maintenance_index_is_valid(connection: sqlite3.Connection) -> bool:
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
            ("task_claims_by_authority_state_task",),
        ).fetchone()
        return row is not None and _normalized_sql(row[0]) == _normalized_sql(
            TASK_CLAIMS_AUTHORITY_STATE_INDEX_SQL
        )

    @staticmethod
    def _ensure_maintenance_indexes(connection: sqlite3.Connection) -> None:
        connection.execute(TASK_CLAIMS_AUTHORITY_STATE_INDEX_SQL)
        if not ControlStore._maintenance_index_is_valid(connection):
            raise ControlStoreUnavailable(
                "Control Store Task Claim maintenance index differs from authority"
            )

    @staticmethod
    def _validate_task_tables(
        connection: sqlite3.Connection,
        *,
        completion_record_authority: bool = True,
    ) -> None:
        attempt_columns = {
            "attempt_id", "task_id", "claim_generation", "attempt_path",
            "state", "completion_sha256",
        }
        expected = {
            "task_claims": {
                "task_id", "authority_kind", "authority_id", "envelope_sha256",
                "write_set_json", "state", "claim_generation", "attempt_id",
                "coordinator_session_id", "worker_id", "reclaim_reason", "updated_at",
            },
            "task_attempts": attempt_columns,
            "task_promotion_intents": {
                "intent_id", "run_id", "task_id", "attempt_id", "claim_generation",
                "expected_run_revision", "old_run_record_sha256",
                "replacement_run_record_sha256", "replacement_run_record_json",
                "outputs_json", "journal_sha256", "state", "intent_identity",
            },
        }
        if completion_record_authority:
            expected["task_attempt_authorities"] = {
                "attempt_id", "attempt_record_json", "attempt_record_sha256",
            }
            expected["task_completion_authorities"] = {
                "attempt_id", "completion_record_json",
            }
            expected["task_promotion_identity_versions"] = {
                "intent_id", "identity_version",
            }
        for table, expected_columns in expected.items():
            actual = {
                str(row[1])
                for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
            }
            if actual != expected_columns:
                raise ControlStoreUnavailable(
                    f"Control Store {table} table is incomplete"
                )
        indexes = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA index_list(task_promotion_intents)"
            ).fetchall()
        }
        if "one_nonterminal_task_promotion_per_run" not in indexes:
            raise ControlStoreUnavailable(
                "Control Store Run Promotion Slot index is missing"
            )
        expected_sql = {
            "task_claims": TASK_CLAIMS_TABLE_SQL,
            "task_attempts": TASK_ATTEMPTS_TABLE_SQL,
            "task_promotion_intents": TASK_PROMOTION_TABLE_SQL,
            "one_nonterminal_task_promotion_per_run": TASK_PROMOTION_INDEX_SQL,
        }
        if completion_record_authority:
            expected_sql["task_attempt_authorities"] = (
                TASK_ATTEMPT_AUTHORITIES_TABLE_SQL
            )
            expected_sql["task_completion_authorities"] = (
                TASK_COMPLETION_AUTHORITIES_TABLE_SQL
            )
            expected_sql["task_promotion_identity_versions"] = (
                TASK_PROMOTION_IDENTITY_VERSIONS_TABLE_SQL
            )
        for name, expected in expected_sql.items():
            row = connection.execute(
                "SELECT sql FROM sqlite_master WHERE name=?", (name,)
            ).fetchone()
            if row is None or _normalized_sql(row[0]) != _normalized_sql(expected):
                raise ControlStoreUnavailable(
                    f"Control Store SQL authority differs for {name}"
                )

    @staticmethod
    def _validate_task_claim_authority_table(
        connection: sqlite3.Connection,
    ) -> None:
        columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(task_claim_authorities)"
            ).fetchall()
        }
        if columns != {
            "task_id",
            "claim_record_json",
            "claim_record_sha256",
        }:
            raise ControlStoreUnavailable(
                "Control Store Task Claim authority table is incomplete"
            )
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='task_claim_authorities'"
        ).fetchone()
        if row is None or _normalized_sql(row[0]) != _normalized_sql(
            TASK_CLAIM_AUTHORITIES_TABLE_SQL
        ):
            raise ControlStoreUnavailable(
                "Control Store SQL authority differs for task_claim_authorities"
            )

    @staticmethod
    def _task_claim_authority_record(
        *,
        task_id: str,
        authority_kind: str,
        authority_id: str,
        envelope_sha256: str,
        write_set: tuple[str, ...],
    ) -> dict:
        return {
            "identity_version": "evidence-v1",
            "task_id": task_id,
            "authority_kind": authority_kind,
            "authority_id": authority_id,
            "task_root_path": f"workflow/tasks/{task_id}",
            "task_envelope_sha256": envelope_sha256,
            "write_set": list(write_set),
        }

    def _claim_authority_from_current_envelope(
        self,
        *,
        authority_id: str,
        task_id: str,
        envelope_sha256: str,
        write_set: tuple[str, ...],
    ) -> dict:
        connection = self._connect()
        try:
            binding = connection.execute(
                "SELECT output_path FROM run_bindings WHERE run_id=?",
                (authority_id,),
            ).fetchone()
        finally:
            connection.close()
        if binding is None:
            raise KernelConflict("Task Claim authority has no Run binding")
        run_root = Path(str(binding["output_path"])).resolve()
        task_root = run_root / "workflow" / "tasks" / task_id
        envelope_path = task_root / "task.json"
        try:
            task_root_info = task_root.lstat()
            envelope_info = envelope_path.lstat()
        except OSError as exc:
            raise KernelConflict("Task Claim Envelope is unavailable") from exc
        if (
            task_root.is_symlink()
            or not stat.S_ISDIR(task_root_info.st_mode)
            or getattr(task_root_info, "st_file_attributes", 0)
            & stat.FILE_ATTRIBUTE_REPARSE_POINT
            or envelope_path.is_symlink()
            or not stat.S_ISREG(envelope_info.st_mode)
            or getattr(envelope_info, "st_file_attributes", 0)
            & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise KernelConflict("Task Claim Envelope boundary is absent or linked")
        try:
            envelope = read_json(envelope_path)
            self.contracts.validate("subagent-task-envelope", envelope)
        except (OSError, json.JSONDecodeError, ContractError) as exc:
            raise KernelConflict("Task Claim Envelope contract is invalid") from exc
        authority_binding = envelope.get("authority_binding", {})
        if (
            envelope.get("task_id") != task_id
            or envelope.get("task_root_path") != f"workflow/tasks/{task_id}"
            or authority_binding.get("kind") != "kernel_run"
            or authority_binding.get("run_id") != authority_id
            or sha256_file(envelope_path) != envelope_sha256
            or envelope.get("write_set") != list(write_set)
        ):
            raise KernelConflict(
                "Task Claim static authority differs from its current Envelope"
            )
        return self._task_claim_authority_record(
            task_id=task_id,
            authority_kind="kernel_run",
            authority_id=authority_id,
            envelope_sha256=envelope_sha256,
            write_set=write_set,
        )

    @staticmethod
    def _insert_task_claim_authority(
        connection: sqlite3.Connection,
        record: dict,
        *,
        canonical: bytes | None = None,
        record_sha256: str | None = None,
    ) -> None:
        if canonical is None:
            canonical = canonical_json_bytes(record)
        if record_sha256 is None:
            record_sha256 = hashlib.sha256(canonical).hexdigest()
        connection.execute(
            "INSERT INTO task_claim_authorities("
            "task_id, claim_record_json, claim_record_sha256) VALUES (?, ?, ?)",
            (
                record["task_id"],
                canonical.decode("utf-8"),
                record_sha256,
            ),
        )

    def _validate_task_claim_authority_rows(
        self, connection: sqlite3.Connection
    ) -> None:
        orphan = connection.execute(
            "SELECT a.task_id FROM task_claim_authorities a "
            "LEFT JOIN task_claims c ON c.task_id=a.task_id "
            "WHERE c.task_id IS NULL LIMIT 1"
        ).fetchone()
        if orphan is not None:
            raise ControlStoreUnavailable(
                "Task Claim authority lacks a Claim projection"
            )
        rows = connection.execute(
            "SELECT c.*, a.claim_record_json, a.claim_record_sha256 "
            "FROM task_claims c LEFT JOIN task_claim_authorities a "
            "ON a.task_id=c.task_id ORDER BY c.task_id"
        ).fetchall()
        for row in rows:
            authority_json = row["claim_record_json"]
            authority_sha = row["claim_record_sha256"]
            if authority_json is None or authority_sha is None:
                raise ControlStoreUnavailable(
                    "Task Claim lacks immutable authority"
                )
            try:
                write_set = json.loads(str(row["write_set_json"]))
                record = json.loads(str(authority_json))
            except (TypeError, json.JSONDecodeError) as exc:
                raise ControlStoreUnavailable(
                    "Task Claim projection or authority is invalid"
                ) from exc
            if (
                not isinstance(write_set, list)
                or not write_set
                or any(not isinstance(path, str) or not path for path in write_set)
                or canonical_json_bytes(write_set).decode("utf-8").strip()
                != row["write_set_json"]
            ):
                raise ControlStoreUnavailable(
                    "Task Claim write set projection is invalid"
                )
            expected = self._task_claim_authority_record(
                task_id=str(row["task_id"]),
                authority_kind=str(row["authority_kind"]),
                authority_id=str(row["authority_id"]),
                envelope_sha256=str(row["envelope_sha256"]),
                write_set=tuple(write_set),
            )
            canonical = canonical_json_bytes(record)
            if (
                record != expected
                or canonical.decode("utf-8") != authority_json
                or hashlib.sha256(canonical).hexdigest() != authority_sha
            ):
                raise ControlStoreUnavailable(
                    "Task Claim immutable authority binding drifted"
                )
            current_attempt = connection.execute(
                "SELECT state FROM task_attempts WHERE task_id=? AND attempt_id=? "
                "AND claim_generation=?",
                (
                    row["task_id"],
                    row["attempt_id"],
                    row["claim_generation"],
                ),
            ).fetchone()
            if current_attempt is None:
                raise ControlStoreUnavailable(
                    "Task Claim current Attempt projection is absent"
                )
            promotions = connection.execute(
                "SELECT run_id, task_id, attempt_id, claim_generation, state "
                "FROM task_promotion_intents WHERE task_id=? ORDER BY intent_id",
                (row["task_id"],),
            ).fetchall()
            committed = [
                intent for intent in promotions if intent["state"] == "COMMITTED"
            ]
            non_aborted = [
                intent for intent in promotions if intent["state"] != "ABORTED"
            ]
            if any(
                intent["run_id"] != row["authority_id"]
                or intent["task_id"] != row["task_id"]
                or intent["attempt_id"] != row["attempt_id"]
                or int(intent["claim_generation"])
                != int(row["claim_generation"])
                for intent in promotions
                if intent["state"] != "ABORTED"
            ):
                raise ControlStoreUnavailable(
                    "Task Claim lifecycle disagrees with promotion authority"
                )
            if row["state"] == "ACTIVE":
                if current_attempt["state"] not in {
                    "CLAIMED",
                    "VALIDATED_WAITING_FOR_PROMOTION",
                } or committed:
                    raise ControlStoreUnavailable(
                        "active Task Claim lifecycle projection is invalid"
                    )
                if non_aborted and current_attempt["state"] != (
                    "VALIDATED_WAITING_FOR_PROMOTION"
                ):
                    raise ControlStoreUnavailable(
                        "Task Claim promotion lacks a validated current Attempt"
                    )
            elif (
                current_attempt["state"] != "COMMITTED_COMPLETE"
                or len(committed) != 1
            ):
                raise ControlStoreUnavailable(
                    "terminal Task Claim lacks one committed promotion authority"
                )
        active = [row for row in rows if row["state"] == "ACTIVE"]
        for index, left in enumerate(active):
            try:
                left_record = json.loads(str(left["claim_record_json"]))
            except (TypeError, json.JSONDecodeError) as exc:
                raise ControlStoreUnavailable(
                    "active Task Claim authority is invalid"
                ) from exc
            for right in active[index + 1 :]:
                if (
                    left["authority_kind"] != right["authority_kind"]
                    or left["authority_id"] != right["authority_id"]
                ):
                    continue
                try:
                    right_record = json.loads(str(right["claim_record_json"]))
                    right_write_set = tuple(right_record["write_set"])
                except (TypeError, KeyError, json.JSONDecodeError) as exc:
                    raise ControlStoreUnavailable(
                        "active Task Claim authority is invalid"
                    ) from exc
                left_write_set_json = canonical_json_bytes(
                    left_record["write_set"]
                ).decode("utf-8").strip()
                if self._write_sets_overlap(
                    left_write_set_json,
                    right_write_set,
                ):
                    raise ControlStoreUnavailable(
                        "Control Store has overlapping active Task Claims"
                    )

    def _validate_reclaim_history_before_mutation(
        self, connection: sqlite3.Connection
    ) -> None:
        version = int(
            connection.execute(
                "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
            ).fetchone()[0]
        )
        if version < 6:
            return
        self._validate_task_reclaim_transition_table(connection)
        self._validate_task_attempt_authority_rows(connection)
        self._validate_task_reclaim_transition_rows(connection)
        if version >= 7:
            self._validate_task_claim_authority_table(connection)
            self._validate_task_claim_authority_rows(connection)

    @staticmethod
    def _validate_task_reclaim_transition_table(
        connection: sqlite3.Connection,
    ) -> None:
        expected_columns = {
            "transition_id",
            "authority_id",
            "task_id",
            "prior_attempt_id",
            "replacement_attempt_id",
            "prior_claim_generation",
            "replacement_claim_generation",
            "recovery_reason",
            "prior_coordinator_session_id",
            "prior_worker_id",
            "replacement_coordinator_session_id",
            "replacement_worker_id",
            "reclaimed_at",
            "transition_record_json",
        }
        columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(task_reclaim_transitions)"
            ).fetchall()
        }
        if columns != expected_columns:
            raise ControlStoreUnavailable(
                "Control Store Task reclaim transition table is incomplete"
            )
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='task_reclaim_transitions'"
        ).fetchone()
        if row is None or _normalized_sql(row[0]) != _normalized_sql(
            TASK_RECLAIM_TRANSITIONS_TABLE_SQL
        ):
            raise ControlStoreUnavailable(
                "Control Store SQL authority differs for task_reclaim_transitions"
            )

    @staticmethod
    def _task_reclaim_transition_record(
        *,
        authority_id: str,
        task_id: str,
        prior_attempt_id: str,
        replacement_attempt_id: str,
        prior_claim_generation: int,
        replacement_claim_generation: int,
        recovery_reason: str,
        prior_coordinator_session_id: str,
        prior_worker_id: str,
        replacement_coordinator_session_id: str,
        replacement_worker_id: str,
        reclaimed_at: str,
    ) -> dict:
        return {
            "identity_version": "evidence-v1",
            "authority_id": authority_id,
            "task_id": task_id,
            "prior_attempt_id": prior_attempt_id,
            "replacement_attempt_id": replacement_attempt_id,
            "prior_claim_generation": prior_claim_generation,
            "replacement_claim_generation": replacement_claim_generation,
            "recovery_reason": recovery_reason,
            "prior_coordinator_session_id": prior_coordinator_session_id,
            "prior_worker_id": prior_worker_id,
            "replacement_coordinator_session_id": (
                replacement_coordinator_session_id
            ),
            "replacement_worker_id": replacement_worker_id,
            "reclaimed_at": reclaimed_at,
        }

    @staticmethod
    def _task_reclaim_transition_id(record: dict) -> str:
        return hashlib.sha256(canonical_json_bytes(record)).hexdigest()

    def _insert_task_reclaim_transition(
        self,
        connection: sqlite3.Connection,
        record: dict,
        *,
        canonical: str | None = None,
        transition_id: str | None = None,
    ) -> None:
        if canonical is None:
            canonical = canonical_json_bytes(record).decode("utf-8")
        if transition_id is None:
            transition_id = self._task_reclaim_transition_id(record)
        connection.execute(
            "INSERT INTO task_reclaim_transitions("
            "transition_id, authority_id, task_id, prior_attempt_id, "
            "replacement_attempt_id, prior_claim_generation, "
            "replacement_claim_generation, recovery_reason, "
            "prior_coordinator_session_id, prior_worker_id, "
            "replacement_coordinator_session_id, replacement_worker_id, "
            "reclaimed_at, transition_record_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                transition_id,
                record["authority_id"],
                record["task_id"],
                record["prior_attempt_id"],
                record["replacement_attempt_id"],
                record["prior_claim_generation"],
                record["replacement_claim_generation"],
                record["recovery_reason"],
                record["prior_coordinator_session_id"],
                record["prior_worker_id"],
                record["replacement_coordinator_session_id"],
                record["replacement_worker_id"],
                record["reclaimed_at"],
                canonical,
            ),
        )

    def _backfill_task_reclaim_transitions(
        self, connection: sqlite3.Connection
    ) -> None:
        claims = connection.execute(
            "SELECT * FROM task_claims ORDER BY task_id"
        ).fetchall()
        for claim in claims:
            generation = int(claim["claim_generation"])
            if generation > 2:
                raise ControlStoreUnavailable(
                    "Control Store v5 multiple-reclaim history cannot be recovered"
                )
            if generation == 1:
                continue
            reason = claim["reclaim_reason"]
            if not isinstance(reason, str) or not reason.strip():
                raise ControlStoreUnavailable(
                    "Control Store v5 reclaim reason is unavailable"
                )
            attempts = connection.execute(
                "SELECT a.*, aa.attempt_record_json FROM task_attempts a "
                "JOIN task_attempt_authorities aa ON aa.attempt_id=a.attempt_id "
                "WHERE a.task_id=? ORDER BY a.claim_generation",
                (claim["task_id"],),
            ).fetchall()
            if (
                len(attempts) != 2
                or [int(row["claim_generation"]) for row in attempts] != [1, 2]
                or attempts[1]["attempt_id"] != claim["attempt_id"]
            ):
                raise ControlStoreUnavailable(
                    "Control Store v5 single-reclaim Attempt chain is incomplete"
                )
            try:
                prior_record = json.loads(str(attempts[0]["attempt_record_json"]))
                replacement_record = json.loads(
                    str(attempts[1]["attempt_record_json"])
                )
            except json.JSONDecodeError as exc:
                raise ControlStoreUnavailable(
                    "Control Store v5 reclaim Attempt authority is invalid"
                ) from exc
            record = self._task_reclaim_transition_record(
                authority_id=str(claim["authority_id"]),
                task_id=str(claim["task_id"]),
                prior_attempt_id=str(attempts[0]["attempt_id"]),
                replacement_attempt_id=str(attempts[1]["attempt_id"]),
                prior_claim_generation=1,
                replacement_claim_generation=2,
                recovery_reason=reason,
                prior_coordinator_session_id=str(
                    prior_record.get("coordinator_session_id", "")
                ),
                prior_worker_id=str(prior_record.get("worker_id", "")),
                replacement_coordinator_session_id=str(
                    replacement_record.get("coordinator_session_id", "")
                ),
                replacement_worker_id=str(
                    replacement_record.get("worker_id", "")
                ),
                reclaimed_at=str(replacement_record.get("claimed_at", "")),
            )
            self._insert_task_reclaim_transition(connection, record)

    def _backfill_task_claim_authorities(
        self, connection: sqlite3.Connection
    ) -> None:
        claims = connection.execute(
            "SELECT c.*, b.output_path FROM task_claims c "
            "JOIN run_bindings b ON b.run_id=c.authority_id ORDER BY c.task_id"
        ).fetchall()
        claim_count = int(
            connection.execute("SELECT COUNT(*) FROM task_claims").fetchone()[0]
        )
        if len(claims) != claim_count:
            raise ControlStoreUnavailable(
                "Control Store v6 Claim lacks its Run binding"
            )
        for claim in claims:
            task_id = str(claim["task_id"])
            run_root = Path(str(claim["output_path"])).resolve()
            envelope_path = run_root / "workflow" / "tasks" / task_id / "task.json"
            try:
                info = envelope_path.lstat()
            except OSError as exc:
                raise ControlStoreUnavailable(
                    "Control Store v6 Claim Envelope is unavailable"
                ) from exc
            if (
                envelope_path.is_symlink()
                or not stat.S_ISREG(info.st_mode)
                or getattr(info, "st_file_attributes", 0)
                & stat.FILE_ATTRIBUTE_REPARSE_POINT
            ):
                raise ControlStoreUnavailable(
                    "Control Store v6 Claim Envelope is absent or linked"
                )
            try:
                envelope = read_json(envelope_path)
                self.contracts.validate("subagent-task-envelope", envelope)
            except (OSError, json.JSONDecodeError, ContractError) as exc:
                raise ControlStoreUnavailable(
                    "Control Store v6 Claim Envelope contract is invalid"
                ) from exc
            try:
                projected_write_set = json.loads(str(claim["write_set_json"]))
            except (TypeError, json.JSONDecodeError) as exc:
                raise ControlStoreUnavailable(
                    "Control Store v6 Claim write set is invalid"
                ) from exc
            envelope_sha = sha256_file(envelope_path)
            expected_root = f"workflow/tasks/{task_id}"
            authority_binding = envelope.get("authority_binding", {})
            if (
                envelope.get("task_id") != task_id
                or envelope.get("task_root_path") != expected_root
                or authority_binding.get("kind") != claim["authority_kind"]
                or authority_binding.get("run_id") != claim["authority_id"]
                or envelope_sha != claim["envelope_sha256"]
                or envelope.get("write_set") != projected_write_set
                or canonical_json_bytes(projected_write_set).decode("utf-8").strip()
                != claim["write_set_json"]
            ):
                raise ControlStoreUnavailable(
                    "Control Store v6 Claim cannot be losslessly bound to its Envelope"
                )
            record = self._task_claim_authority_record(
                task_id=task_id,
                authority_kind=str(claim["authority_kind"]),
                authority_id=str(claim["authority_id"]),
                envelope_sha256=envelope_sha,
                write_set=tuple(projected_write_set),
            )
            self._insert_task_claim_authority(connection, record)

    def _validate_task_reclaim_transition_rows(
        self, connection: sqlite3.Connection
    ) -> None:
        orphan = connection.execute(
            "SELECT t.transition_id FROM task_reclaim_transitions t "
            "LEFT JOIN task_claims c ON c.task_id=t.task_id "
            "LEFT JOIN run_bindings b ON b.run_id=t.authority_id "
            "LEFT JOIN task_attempts p ON p.attempt_id=t.prior_attempt_id "
            "AND p.task_id=t.task_id "
            "LEFT JOIN task_attempts r ON r.attempt_id=t.replacement_attempt_id "
            "AND r.task_id=t.task_id "
            "WHERE c.task_id IS NULL OR b.run_id IS NULL "
            "OR p.attempt_id IS NULL OR r.attempt_id IS NULL LIMIT 1"
        ).fetchone()
        if orphan is not None:
            raise ControlStoreUnavailable(
                "Task reclaim transition lacks its bound Run, Claim, or Attempt"
            )
        claims = connection.execute(
            "SELECT * FROM task_claims ORDER BY task_id"
        ).fetchall()
        for claim in claims:
            attempts = connection.execute(
                "SELECT a.*, aa.attempt_record_json, aa.attempt_record_sha256 "
                "FROM task_attempts a LEFT JOIN task_attempt_authorities aa "
                "ON aa.attempt_id=a.attempt_id WHERE a.task_id=? "
                "ORDER BY a.claim_generation",
                (claim["task_id"],),
            ).fetchall()
            claim_generation = int(claim["claim_generation"])
            expected_generations = list(range(1, claim_generation + 1))
            if (
                [int(row["claim_generation"]) for row in attempts]
                != expected_generations
                or not attempts
                or attempts[-1]["attempt_id"] != claim["attempt_id"]
            ):
                raise ControlStoreUnavailable(
                    "Task reclaim Attempt generation chain is incomplete"
                )
            attempt_records: list[dict] = []
            for attempt in attempts:
                try:
                    attempt_record = json.loads(
                        str(attempt["attempt_record_json"])
                    )
                except (TypeError, json.JSONDecodeError) as exc:
                    raise ControlStoreUnavailable(
                        "Task reclaim Attempt authority is invalid"
                    ) from exc
                canonical_attempt = canonical_json_bytes(attempt_record)
                if (
                    canonical_attempt.decode("utf-8")
                    != attempt["attempt_record_json"]
                    or hashlib.sha256(canonical_attempt).hexdigest()
                    != attempt["attempt_record_sha256"]
                    or attempt_record.get("task_id") != claim["task_id"]
                    or attempt_record.get("attempt_id") != attempt["attempt_id"]
                    or int(attempt_record.get("claim_generation", 0))
                    != int(attempt["claim_generation"])
                ):
                    raise ControlStoreUnavailable(
                        "Task reclaim Attempt authority binding is invalid"
                    )
                attempt_records.append(attempt_record)
            transitions = connection.execute(
                "SELECT * FROM task_reclaim_transitions WHERE task_id=? "
                "ORDER BY replacement_claim_generation",
                (claim["task_id"],),
            ).fetchall()
            if len(transitions) != claim_generation - 1:
                raise ControlStoreUnavailable(
                    "Task reclaim transition history coverage is incomplete"
                )
            if claim_generation == 1:
                if claim["reclaim_reason"] is not None:
                    raise ControlStoreUnavailable(
                        "initial Task Claim has an unexplained reclaim projection"
                    )
                current_record = attempt_records[0]
                if (
                    current_record.get("coordinator_session_id")
                    != claim["coordinator_session_id"]
                    or current_record.get("worker_id") != claim["worker_id"]
                    or current_record.get("claimed_at") != claim["updated_at"]
                ):
                    raise ControlStoreUnavailable(
                        "initial Task Claim projection identity drifted"
                    )
                continue
            for index, transition in enumerate(transitions):
                prior = attempts[index]
                replacement = attempts[index + 1]
                prior_record = attempt_records[index]
                replacement_record = attempt_records[index + 1]
                if prior["state"] != "ABANDONED":
                    raise ControlStoreUnavailable(
                        "Task reclaim prior Attempt is not abandoned"
                    )
                expected = self._task_reclaim_transition_record(
                    authority_id=str(claim["authority_id"]),
                    task_id=str(claim["task_id"]),
                    prior_attempt_id=str(prior["attempt_id"]),
                    replacement_attempt_id=str(replacement["attempt_id"]),
                    prior_claim_generation=int(prior["claim_generation"]),
                    replacement_claim_generation=int(
                        replacement["claim_generation"]
                    ),
                    recovery_reason=str(transition["recovery_reason"]),
                    prior_coordinator_session_id=str(
                        prior_record.get("coordinator_session_id", "")
                    ),
                    prior_worker_id=str(prior_record.get("worker_id", "")),
                    replacement_coordinator_session_id=str(
                        replacement_record.get("coordinator_session_id", "")
                    ),
                    replacement_worker_id=str(
                        replacement_record.get("worker_id", "")
                    ),
                    reclaimed_at=str(replacement_record.get("claimed_at", "")),
                )
                canonical = canonical_json_bytes(expected).decode("utf-8")
                try:
                    stored_record = json.loads(
                        str(transition["transition_record_json"])
                    )
                except (TypeError, json.JSONDecodeError) as exc:
                    raise ControlStoreUnavailable(
                        "Task reclaim transition record is invalid"
                    ) from exc
                projected = {
                    key: transition[key]
                    for key in (
                        "authority_id",
                        "task_id",
                        "prior_attempt_id",
                        "replacement_attempt_id",
                        "prior_claim_generation",
                        "replacement_claim_generation",
                        "recovery_reason",
                        "prior_coordinator_session_id",
                        "prior_worker_id",
                        "replacement_coordinator_session_id",
                        "replacement_worker_id",
                        "reclaimed_at",
                    )
                }
                expected_projection = {
                    key: value
                    for key, value in expected.items()
                    if key != "identity_version"
                }
                if (
                    projected != expected_projection
                    or stored_record != expected
                    or transition["transition_record_json"] != canonical
                    or transition["transition_id"]
                    != self._task_reclaim_transition_id(expected)
                ):
                    raise ControlStoreUnavailable(
                        "Task reclaim transition identity or binding drifted"
                    )
            latest = transitions[-1]
            latest_record = attempt_records[-1]
            if (
                claim["reclaim_reason"] != latest["recovery_reason"]
                or claim["coordinator_session_id"]
                != latest["replacement_coordinator_session_id"]
                or claim["worker_id"] != latest["replacement_worker_id"]
                or claim["updated_at"] != latest["reclaimed_at"]
                or latest_record.get("coordinator_session_id")
                != claim["coordinator_session_id"]
                or latest_record.get("worker_id") != claim["worker_id"]
                or latest_record.get("claimed_at") != claim["updated_at"]
            ):
                raise ControlStoreUnavailable(
                    "current Task Claim projection disagrees with reclaim history"
                )

    @staticmethod
    def _task_attempt_record(
        *,
        task_id: str,
        attempt_id: str,
        claim_generation: int,
        envelope_sha256: str,
        attempt_path: str,
        coordinator_session_id: str,
        worker_id: str,
        claimed_at: str,
    ) -> dict:
        return {
            "schema_name": "task-attempt",
            "schema_version": "1.0.0",
            "kernel_version": "2.0.0",
            "task_id": task_id,
            "attempt_id": attempt_id,
            "claim_generation": claim_generation,
            "task_envelope_sha256": envelope_sha256,
            "attempt_path": attempt_path,
            "coordinator_session_id": coordinator_session_id,
            "worker_id": worker_id,
            "claimed_at": claimed_at,
            "state": "claimed",
        }

    def _backfill_task_attempt_authorities(
        self, connection: sqlite3.Connection
    ) -> None:
        rows = connection.execute(
            "SELECT a.*, c.authority_id, c.envelope_sha256, "
            "c.attempt_id AS current_attempt_id, c.coordinator_session_id, "
            "c.worker_id, c.updated_at, b.output_path "
            "FROM task_attempts a JOIN task_claims c ON c.task_id=a.task_id "
            "JOIN run_bindings b ON b.run_id=c.authority_id"
        ).fetchall()
        for row in rows:
            if row["attempt_id"] != row["current_attempt_id"]:
                raise ControlStoreUnavailable(
                    "Control Store v4 historical Task Attempt lacks "
                    "authenticated full identity authority"
                )
            relative = PurePosixPath(str(row["attempt_path"]))
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or not relative.parts
                or relative.parts[0] != "workflow"
            ):
                raise ControlStoreUnavailable(
                    "Control Store v4 Task Attempt path is invalid"
                )
            run_root = Path(str(row["output_path"])).resolve()
            record_path = run_root.joinpath(*relative.parts) / "attempt.json"
            try:
                record_path.resolve(strict=False).relative_to(run_root)
            except ValueError as exc:
                raise ControlStoreUnavailable(
                    "Control Store v4 Task Attempt evidence escapes its Run"
                ) from exc
            if record_path.is_symlink():
                raise ControlStoreUnavailable(
                    "Control Store v4 Task Attempt evidence is linked"
                )
            if record_path.is_file():
                try:
                    record = read_json(record_path)
                    self.contracts.validate("task-attempt", record)
                except (OSError, json.JSONDecodeError, ContractError) as exc:
                    raise ControlStoreUnavailable(
                        "Control Store v4 Task Attempt evidence is invalid"
                    ) from exc
                canonical = canonical_json_bytes(record)
                expected = {
                    "task_id": str(row["task_id"]),
                    "attempt_id": str(row["attempt_id"]),
                    "claim_generation": int(row["claim_generation"]),
                    "task_envelope_sha256": str(row["envelope_sha256"]),
                    "attempt_path": str(row["attempt_path"]),
                    "state": "claimed",
                }
                if (
                    record_path.read_bytes() != canonical
                    or any(record.get(key) != value for key, value in expected.items())
                ):
                    raise ControlStoreUnavailable(
                        "Control Store v4 Task Attempt evidence binding is invalid"
                    )
                if (
                    record.get("coordinator_session_id")
                    != row["coordinator_session_id"]
                    or record.get("worker_id") != row["worker_id"]
                    or record.get("claimed_at") != row["updated_at"]
                ):
                    raise ControlStoreUnavailable(
                        "Control Store v4 current Task Attempt identity is invalid"
                    )
            elif (
                row["attempt_id"] == row["current_attempt_id"]
                and row["state"] == "CLAIMED"
            ):
                record = self._task_attempt_record(
                    task_id=str(row["task_id"]),
                    attempt_id=str(row["attempt_id"]),
                    claim_generation=int(row["claim_generation"]),
                    envelope_sha256=str(row["envelope_sha256"]),
                    attempt_path=str(row["attempt_path"]),
                    coordinator_session_id=str(row["coordinator_session_id"]),
                    worker_id=str(row["worker_id"]),
                    claimed_at=str(row["updated_at"]),
                )
                self.contracts.validate("task-attempt", record)
                canonical = canonical_json_bytes(record)
            else:
                raise ControlStoreUnavailable(
                    "Control Store v4 Task Attempt evidence is absent"
                )
            connection.execute(
                "INSERT INTO task_attempt_authorities("
                "attempt_id, attempt_record_json, attempt_record_sha256) "
                "VALUES (?, ?, ?)",
                (
                    row["attempt_id"],
                    canonical.decode("utf-8"),
                    hashlib.sha256(canonical).hexdigest(),
                ),
            )

    def _backfill_task_completion_authorities(
        self, connection: sqlite3.Connection
    ) -> None:
        rows = connection.execute(
            "SELECT a.*, c.envelope_sha256, c.authority_id, b.output_path "
            "FROM task_attempts a JOIN task_claims c ON c.task_id=a.task_id "
            "JOIN run_bindings b ON b.run_id=c.authority_id"
        ).fetchall()
        for row in rows:
            completion_sha = row["completion_sha256"]
            relative = PurePosixPath(str(row["attempt_path"]))
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or not relative.parts
                or relative.parts[0] != "workflow"
            ):
                raise ControlStoreUnavailable(
                    "Control Store v4 Task Attempt path is invalid"
                )
            run_root = Path(str(row["output_path"])).resolve()
            completion_path = run_root.joinpath(*relative.parts) / "completion.json"
            try:
                completion_path.resolve(strict=False).relative_to(run_root)
            except ValueError as exc:
                raise ControlStoreUnavailable(
                    "Control Store v4 Completion evidence escapes its Run"
                ) from exc
            if completion_sha is None:
                if completion_path.exists() or row["state"] in {
                    "VALIDATED_WAITING_FOR_PROMOTION",
                    "COMMITTED_COMPLETE",
                }:
                    raise ControlStoreUnavailable(
                        "Control Store v4 Completion authority is incomplete"
                    )
                continue
            if completion_path.is_symlink() or not completion_path.is_file():
                raise ControlStoreUnavailable(
                    "Control Store v4 Completion evidence is absent or linked"
                )
            try:
                completion = read_json(completion_path)
                self.contracts.validate("task-completion-record", completion)
            except (OSError, json.JSONDecodeError, ContractError) as exc:
                raise ControlStoreUnavailable(
                    "Control Store v4 Completion evidence is invalid"
                ) from exc
            canonical = canonical_json_bytes(completion)
            if (
                completion_path.read_bytes() != canonical
                or sha256_file(completion_path) != completion_sha
                or completion["task_id"] != row["task_id"]
                or completion["attempt_id"] != row["attempt_id"]
                or int(completion["claim_generation"])
                != int(row["claim_generation"])
                or completion["task_envelope_sha256"] != row["envelope_sha256"]
            ):
                raise ControlStoreUnavailable(
                    "Control Store v4 Completion evidence binding is invalid"
                )
            connection.execute(
                "INSERT INTO task_completion_authorities("
                "attempt_id, completion_record_json) VALUES (?, ?)",
                (row["attempt_id"], canonical.decode("utf-8")),
            )

    def _validate_task_attempt_authority_rows(
        self, connection: sqlite3.Connection
    ) -> None:
        rows = connection.execute(
            "SELECT a.*, c.envelope_sha256, "
            "c.attempt_id AS current_attempt_id, c.coordinator_session_id, "
            "c.worker_id, c.updated_at, aa.attempt_record_json, "
            "aa.attempt_record_sha256 FROM task_attempts a "
            "JOIN task_claims c ON c.task_id=a.task_id "
            "LEFT JOIN task_attempt_authorities aa "
            "ON aa.attempt_id=a.attempt_id"
        ).fetchall()
        for row in rows:
            authority_json = row["attempt_record_json"]
            authority_sha = row["attempt_record_sha256"]
            if authority_json is None or authority_sha is None:
                raise ControlStoreUnavailable(
                    "Task Attempt lacks immutable record authority"
                )
            try:
                record = json.loads(str(authority_json))
                self.contracts.validate("task-attempt", record)
            except (json.JSONDecodeError, ContractError) as exc:
                raise ControlStoreUnavailable(
                    "Task Attempt record authority is invalid"
                ) from exc
            canonical = canonical_json_bytes(record)
            expected = {
                "task_id": str(row["task_id"]),
                "attempt_id": str(row["attempt_id"]),
                "claim_generation": int(row["claim_generation"]),
                "task_envelope_sha256": str(row["envelope_sha256"]),
                "attempt_path": str(row["attempt_path"]),
                "state": "claimed",
            }
            if (
                canonical.decode("utf-8") != authority_json
                or hashlib.sha256(canonical).hexdigest() != authority_sha
                or any(record.get(key) != value for key, value in expected.items())
            ):
                raise ControlStoreUnavailable(
                    "Task Attempt record authority binding is invalid"
                )
            if row["attempt_id"] == row["current_attempt_id"] and (
                record.get("coordinator_session_id")
                != row["coordinator_session_id"]
                or record.get("worker_id") != row["worker_id"]
                or record.get("claimed_at") != row["updated_at"]
            ):
                raise ControlStoreUnavailable(
                    "current Task Attempt record authority identity drifted"
                )

    def _validate_task_completion_authority_rows(
        self, connection: sqlite3.Connection
    ) -> None:
        orphan = connection.execute(
            "SELECT ca.attempt_id FROM task_completion_authorities ca "
            "LEFT JOIN task_attempts a ON a.attempt_id=ca.attempt_id "
            "WHERE a.attempt_id IS NULL LIMIT 1"
        ).fetchone()
        if orphan is not None:
            raise ControlStoreUnavailable(
                "Task Completion authority lacks a Task Attempt"
            )
        rows = connection.execute(
            "SELECT a.attempt_id, a.task_id, a.claim_generation, a.state, "
            "a.completion_sha256, c.envelope_sha256, "
            "ca.completion_record_json FROM task_attempts a "
            "JOIN task_claims c ON c.task_id=a.task_id "
            "LEFT JOIN task_completion_authorities ca "
            "ON ca.attempt_id=a.attempt_id ORDER BY a.attempt_id"
        ).fetchall()
        for row in rows:
            completion_sha = row["completion_sha256"]
            authority_json = row["completion_record_json"]
            if (completion_sha is None) != (authority_json is None):
                raise ControlStoreUnavailable(
                    "Task Completion fingerprint and record authority coverage disagree"
                )
            if row["state"] in {
                "VALIDATED_WAITING_FOR_PROMOTION",
                "COMMITTED_COMPLETE",
            } and completion_sha is None:
                raise ControlStoreUnavailable(
                    "Task Attempt state requires a Completion authority"
                )
            if authority_json is None:
                continue
            try:
                record = json.loads(str(authority_json))
                self.contracts.validate("task-completion-record", record)
            except (TypeError, json.JSONDecodeError, ContractError) as exc:
                raise ControlStoreUnavailable(
                    "Task Completion record authority is invalid"
                ) from exc
            canonical = canonical_json_bytes(record)
            if (
                canonical.decode("utf-8") != authority_json
                or hashlib.sha256(canonical).hexdigest() != completion_sha
                or record.get("task_id") != row["task_id"]
                or record.get("attempt_id") != row["attempt_id"]
                or int(record.get("claim_generation", 0))
                != int(row["claim_generation"])
                or record.get("task_envelope_sha256") != row["envelope_sha256"]
            ):
                raise ControlStoreUnavailable(
                    "Task Completion record authority binding is invalid"
                )

    def _validate_task_promotion_identity_rows(
        self, connection: sqlite3.Connection
    ) -> None:
        orphan = connection.execute(
            "SELECT v.intent_id FROM task_promotion_identity_versions v "
            "LEFT JOIN task_promotion_intents i ON i.intent_id=v.intent_id "
            "WHERE i.intent_id IS NULL LIMIT 1"
        ).fetchone()
        if orphan is not None:
            raise ControlStoreUnavailable(
                "Task promotion identity version lacks an intent"
            )
        intents = connection.execute(
            "SELECT i.* FROM task_promotion_intents i "
            "LEFT JOIN task_promotion_identity_versions v "
            "ON v.intent_id=i.intent_id WHERE v.intent_id IS NOT NULL "
            "ORDER BY i.intent_id"
        ).fetchall()
        intent_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM task_promotion_intents"
            ).fetchone()[0]
        )
        if len(intents) != intent_count:
            raise ControlStoreUnavailable(
                "Task promotion intents and identity versions lack complete coverage"
            )
        for intent in intents:
            try:
                self._validate_task_promotion_intent(connection, intent)
            except ContractError as exc:
                raise ControlStoreUnavailable(
                    "Task promotion intent contract is invalid"
                ) from exc

    @staticmethod
    def _derive_legacy_task_promotion_intent_id(
        intent: sqlite3.Row, outputs: list[dict]
    ) -> str:
        if len(outputs) != 1 or not isinstance(outputs[0].get("sha256"), str):
            raise ControlStoreUnavailable(
                "legacy Task promotion outputs are invalid"
            )
        return hashlib.sha256(
            "\0".join(
                (
                    "task_artifact_promotion",
                    str(intent["run_id"]),
                    str(intent["task_id"]),
                    str(intent["attempt_id"]),
                    str(intent["claim_generation"]),
                    str(intent["expected_run_revision"]),
                    str(intent["old_run_record_sha256"]),
                    str(outputs[0]["sha256"]),
                )
            ).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _derive_legacy_task_promotion_row_identity(
        intent: sqlite3.Row, outputs_json: str
    ) -> str:
        return hashlib.sha256(
            "\0".join(
                (
                    "task_artifact_promotion",
                    str(intent["run_id"]),
                    str(intent["task_id"]),
                    str(intent["attempt_id"]),
                    str(intent["claim_generation"]),
                    str(intent["expected_run_revision"]),
                    str(intent["old_run_record_sha256"]),
                    str(intent["replacement_run_record_sha256"]),
                    hashlib.sha256(outputs_json.encode("utf-8")).hexdigest(),
                )
            ).encode("utf-8")
        ).hexdigest()

    def _migrate_task_promotion_identity_versions(
        self, connection: sqlite3.Connection
    ) -> None:
        intents = connection.execute(
            "SELECT * FROM task_promotion_intents ORDER BY intent_id"
        ).fetchall()
        for intent in intents:
            if intent["state"] != "COMMITTED":
                raise ControlStoreUnavailable(
                    "legacy non-COMMITTED Task promotion requires manual recovery"
                )
            try:
                outputs = json.loads(str(intent["outputs_json"]))
            except json.JSONDecodeError as exc:
                raise ControlStoreUnavailable(
                    "legacy Task promotion outputs are invalid"
                ) from exc
            outputs_json = canonical_json_bytes(outputs).decode("utf-8")
            try:
                replacement = json.loads(
                    str(intent["replacement_run_record_json"])
                )
            except json.JSONDecodeError as exc:
                raise ControlStoreUnavailable(
                    "legacy Task promotion replacement is invalid"
                ) from exc
            replacement_json = canonical_json_bytes(replacement).decode("utf-8")
            replacement_sha = hashlib.sha256(
                replacement_json.encode("utf-8")
            ).hexdigest()
            if (
                outputs_json != intent["outputs_json"]
                or replacement_json != intent["replacement_run_record_json"]
                or replacement_sha != intent["replacement_run_record_sha256"]
                or intent["intent_id"]
                != self._derive_legacy_task_promotion_intent_id(intent, outputs)
                or intent["intent_identity"]
                != self._derive_legacy_task_promotion_row_identity(
                    intent, outputs_json
                )
            ):
                raise ControlStoreUnavailable(
                    "legacy Task promotion identity is invalid"
                )
            claim = connection.execute(
                "SELECT * FROM task_claims WHERE task_id=?",
                (intent["task_id"],),
            ).fetchone()
            attempt = connection.execute(
                "SELECT a.*, ca.completion_record_json FROM task_attempts a "
                "JOIN task_completion_authorities ca ON ca.attempt_id=a.attempt_id "
                "WHERE a.attempt_id=?",
                (intent["attempt_id"],),
            ).fetchone()
            binding = connection.execute(
                "SELECT output_path FROM run_bindings WHERE run_id=?",
                (intent["run_id"],),
            ).fetchone()
            if (
                claim is None
                or attempt is None
                or binding is None
                or claim["state"] != "TERMINAL"
                or claim["attempt_id"] != intent["attempt_id"]
                or claim["authority_id"] != intent["run_id"]
                or attempt["state"] != "COMMITTED_COMPLETE"
                or attempt["completion_sha256"] is None
            ):
                raise ControlStoreUnavailable(
                    "legacy committed Task promotion lifecycle is invalid"
                )
            run_root = Path(str(binding["output_path"])).resolve()
            run_path = run_root / "workflow/run.json"
            envelope_path = (
                run_root / "workflow/tasks" / str(intent["task_id"]) / "task.json"
            )
            prompt_path = envelope_path.with_name("prompt.md")
            journal_path = run_root.joinpath(
                *PurePosixPath(str(attempt["attempt_path"])).parts
            ) / "p.json"
            if (
                run_path.is_symlink()
                or not run_path.is_file()
                or run_path.read_bytes() != replacement_json.encode("utf-8")
                or sha256_file(run_path) != replacement_sha
                or envelope_path.is_symlink()
                or not envelope_path.is_file()
                or sha256_file(envelope_path) != claim["envelope_sha256"]
                or prompt_path.is_symlink()
                or not prompt_path.is_file()
                or journal_path.is_symlink()
                or not journal_path.is_file()
                or sha256_file(journal_path) != intent["journal_sha256"]
            ):
                raise ControlStoreUnavailable(
                    "legacy committed Task promotion disk evidence is invalid"
                )
            try:
                self.contracts.validate_run_record(replacement)
                envelope = read_json(envelope_path)
                journal = read_json(journal_path)
                completion = json.loads(str(attempt["completion_record_json"]))
                self.contracts.validate("subagent-task-envelope", envelope)
                self.contracts.validate("task-promotion-journal", journal)
                self.contracts.validate("task-completion-record", completion)
            except (OSError, json.JSONDecodeError, ContractError) as exc:
                raise ControlStoreUnavailable(
                    "legacy committed Task promotion contracts are invalid"
                ) from exc
            if (
                replacement.get("last_mutation_intent_id") != intent["intent_id"]
                or envelope.get("task_id") != intent["task_id"]
                or envelope.get("authority_binding", {}).get("run_id")
                != intent["run_id"]
                or envelope.get("generated_prompt", {}).get("path")
                != f"workflow/tasks/{intent['task_id']}/prompt.md"
                or envelope.get("generated_prompt", {}).get("sha256")
                != sha256_file(prompt_path)
                or journal.get("intent_id") != intent["intent_id"]
                or journal.get("replacement_run_record_sha256")
                != replacement_sha
            ):
                raise ControlStoreUnavailable(
                    "legacy committed Task promotion evidence binding is invalid"
                )
            completion_outputs = completion.get("outputs")
            projected_outputs = [
                {
                    key: output[key]
                    for key in (
                        "logical_id",
                        "attempt_path",
                        "canonical_path",
                        "sha256",
                    )
                }
                for output in outputs
            ]
            if (
                completion_outputs != projected_outputs
                or journal.get("outputs") != outputs
                or journal.get("run_id") != intent["run_id"]
                or journal.get("task_id") != intent["task_id"]
                or journal.get("attempt_id") != intent["attempt_id"]
                or int(journal.get("claim_generation", 0))
                != int(intent["claim_generation"])
                or int(journal.get("expected_run_revision", 0))
                != int(intent["expected_run_revision"])
                or journal.get("prior_run_record_sha256")
                != intent["old_run_record_sha256"]
                or completion.get("task_id") != intent["task_id"]
                or completion.get("attempt_id") != intent["attempt_id"]
                or int(completion.get("claim_generation", 0))
                != int(intent["claim_generation"])
                or completion.get("task_envelope_sha256")
                != claim["envelope_sha256"]
                or completion.get("validated_run_record_sha256")
                != intent["old_run_record_sha256"]
            ):
                raise ControlStoreUnavailable(
                    "legacy Task promotion Completion or Journal authority disagrees"
                )
            for output in outputs:
                relative = PurePosixPath(str(output.get("canonical_path", "")))
                if relative.is_absolute() or ".." in relative.parts:
                    raise ControlStoreUnavailable(
                        "legacy Task promotion output path is invalid"
                    )
                canonical = run_root.joinpath(*relative.parts)
                try:
                    canonical.resolve(strict=False).relative_to(run_root)
                except ValueError as exc:
                    raise ControlStoreUnavailable(
                        "legacy Task promotion output escapes its Run"
                    ) from exc
                if (
                    canonical.is_symlink()
                    or not canonical.is_file()
                    or sha256_file(canonical) != output.get("sha256")
                ):
                    raise ControlStoreUnavailable(
                        "legacy committed Task promotion output is invalid"
                    )
                attempt_relative = PurePosixPath(str(output.get("attempt_path", "")))
                if (
                    attempt_relative.is_absolute()
                    or ".." in attempt_relative.parts
                    or not attempt_relative.parts
                ):
                    raise ControlStoreUnavailable(
                        "legacy Task promotion Attempt output path is invalid"
                    )
                candidate = journal_path.parent.joinpath(*attempt_relative.parts)
                try:
                    candidate.resolve(strict=False).relative_to(
                        journal_path.parent.resolve()
                    )
                except ValueError as exc:
                    raise ControlStoreUnavailable(
                        "legacy Task promotion Attempt output escapes its boundary"
                    ) from exc
                generation = replacement.get("artifact_generations", {}).get(
                    output.get("logical_id")
                )
                checkpoint = replacement.get("checkpoints", {}).get(
                    "source_acquisition_decision_ready"
                )
                if (
                    candidate.is_symlink()
                    or not candidate.is_file()
                    or sha256_file(candidate) != output.get("sha256")
                    or generation is None
                    or generation.get("path") != output.get("canonical_path")
                    or generation.get("sha256") != output.get("sha256")
                    or checkpoint is None
                    or checkpoint.get("status") != "current"
                    or checkpoint.get("evidence_sha256") != output.get("sha256")
                ):
                    raise ControlStoreUnavailable(
                        "legacy Task promotion Artifact Generation binding is invalid"
                    )
            connection.execute(
                "INSERT INTO task_promotion_identity_versions("
                "intent_id, identity_version) VALUES (?, 'legacy-v1')",
                (intent["intent_id"],),
            )

    def _migrate_run_state_mutation_identity_versions(
        self, connection: sqlite3.Connection
    ) -> None:
        rows = connection.execute(
            "SELECT * FROM run_state_mutation_intents"
        ).fetchall()
        for mutation in rows:
            replacement_json = str(mutation["replacement_run_record_json"])
            try:
                replacement = json.loads(replacement_json)
                self.contracts.validate_run_record(replacement)
            except (json.JSONDecodeError, ContractError) as exc:
                raise ControlStoreUnavailable(
                    "legacy run-state mutation replacement is invalid"
                ) from exc
            canonical = canonical_json_bytes(replacement).decode("utf-8")
            replacement_sha = hashlib.sha256(
                canonical.encode("utf-8")
            ).hexdigest()
            legacy_identity = self.derive_legacy_run_state_mutation_identity(
                operation=str(mutation["operation"]),
                run_id=str(mutation["run_id"]),
                expected_run_revision=int(mutation["expected_run_revision"]),
                old_run_record_sha256=str(mutation["old_run_record_sha256"]),
                predecessor_committed_sha256=str(
                    mutation["predecessor_committed_sha256"]
                ),
                replacement_run_record_sha256=replacement_sha,
            )
            if (
                mutation["operation"] != "source_drift_invalidation"
                or canonical != replacement_json
                or replacement_sha
                != mutation["replacement_run_record_sha256"]
                or mutation["old_run_record_sha256"]
                != mutation["predecessor_committed_sha256"]
                or replacement.get("run_id") != mutation["run_id"]
                or replacement.get("coordination_revision")
                != int(mutation["expected_run_revision"]) + 1
                or mutation["mutation_id"] != legacy_identity
                or mutation["mutation_identity"] != legacy_identity
                or (
                    replacement.get("schema_version") == "2.0.0"
                    and replacement.get("last_mutation_intent_id")
                    != legacy_identity
                )
            ):
                raise ControlStoreUnavailable(
                    "legacy run-state mutation identity cannot be authenticated"
                )
            connection.execute(
                "INSERT INTO run_state_mutation_identity_versions("
                "mutation_id, identity_version, row_identity) "
                "VALUES (?, 'legacy-v1', ?)",
                (legacy_identity, legacy_identity),
            )

    def _upgrade_migration_snapshot(
        self,
        planning_connection: sqlite3.Connection,
        resource_configuration: dict,
        resource_configuration_json: str,
        resource_configuration_sha256: str,
    ) -> None:
        expected_columns = {
            "expected_run_record_sha256",
            "canonical_platform",
            "canonical_item_id",
            "source_identity",
            "source_manifest_sha256",
        }
        try:
            with nullcontext(planning_connection) as connection:
                versions = self._migration_versions(connection)
                version = 0 if not versions else versions[-1]
                columns = {
                    str(row[1])
                    for row in connection.execute(
                        "PRAGMA table_info(initialization_intents)"
                    ).fetchall()
                }
                present = expected_columns & columns
                if version == 1:
                    if present:
                        raise ControlStoreUnavailable(
                            "Slice 1 v1 Control Store has a partial v2 intent migration"
                        )
                    for column in sorted(expected_columns):
                        connection.execute(
                            f"ALTER TABLE initialization_intents ADD COLUMN {column} TEXT"
                        )
                    connection.execute(
                        "INSERT INTO schema_migrations(version) VALUES (2)"
                    )
                    version = 2
                    present = set(expected_columns)
                if version == 2:
                    if present != expected_columns:
                        raise ControlStoreUnavailable(
                            "Control Store v2 intent identity columns are incomplete"
                        )
                    self._create_run_state_mutation_table(connection)
                    connection.execute(
                        "INSERT INTO schema_migrations(version) VALUES (3)"
                    )
                    version = 3
                if version == 3:
                    if present != expected_columns:
                        raise ControlStoreUnavailable(
                            "Control Store v3 intent identity columns are incomplete"
                        )
                    tables = {
                        str(row[0])
                        for row in connection.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        ).fetchall()
                    }
                    if "run_state_mutation_intents" not in tables:
                        raise ControlStoreUnavailable(
                            "Control Store v3 mutation intent table is missing"
                        )
                    self._validate_run_state_mutation_table(connection)
                    self._create_task_tables_v4(connection)
                    connection.execute(
                        "INSERT INTO schema_migrations(version) VALUES (4)"
                    )
                    version = 4
                if version == 4:
                    if present != expected_columns:
                        raise ControlStoreUnavailable(
                            "Control Store v4 intent identity columns are incomplete"
                        )
                    self._validate_run_state_mutation_table(connection)
                    tables = {
                        str(row[0])
                        for row in connection.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        ).fetchall()
                    }
                    if {
                        "run_state_mutation_identity_versions",
                        "task_attempt_authorities",
                        "task_completion_authorities",
                        "task_promotion_identity_versions",
                    } & tables:
                        raise ControlStoreUnavailable(
                            "Control Store v4 has a partial v5 Completion authority migration"
                        )
                    self._validate_task_tables(
                        connection,
                        completion_record_authority=False,
                    )
                    connection.execute(
                        RUN_STATE_MUTATION_IDENTITY_VERSIONS_TABLE_SQL
                    )
                    connection.execute(TASK_ATTEMPT_AUTHORITIES_TABLE_SQL)
                    connection.execute(TASK_COMPLETION_AUTHORITIES_TABLE_SQL)
                    connection.execute(
                        TASK_PROMOTION_IDENTITY_VERSIONS_TABLE_SQL
                    )
                    self._migrate_run_state_mutation_identity_versions(
                        connection
                    )
                    self._backfill_task_attempt_authorities(connection)
                    self._backfill_task_completion_authorities(connection)
                    self._migrate_task_promotion_identity_versions(connection)
                    self._validate_run_state_mutation_identity_table(connection)
                    self._validate_run_state_mutation_rows(connection)
                    self._validate_task_tables(connection)
                    self._validate_task_attempt_authority_rows(connection)
                    self._validate_task_completion_authority_rows(connection)
                    self._validate_task_promotion_identity_rows(connection)
                    connection.execute(
                        "INSERT INTO schema_migrations(version) VALUES (5)"
                    )
                    version = 5
                if version == 5:
                    if present != expected_columns:
                        raise ControlStoreUnavailable(
                            "Control Store v5 intent identity columns are incomplete"
                        )
                    self._validate_run_state_mutation_table(connection)
                    self._validate_run_state_mutation_identity_table(connection)
                    self._validate_run_state_mutation_rows(connection)
                    self._validate_task_tables(connection)
                    self._validate_task_attempt_authority_rows(connection)
                    self._validate_task_completion_authority_rows(connection)
                    self._validate_task_promotion_identity_rows(connection)
                    if self._migration_versions(connection) != [1, 2, 3, 4, 5]:
                        raise ControlStoreUnavailable(
                            "Control Store v5 migration ledger is incomplete"
                        )
                    tables = {
                        str(row[0])
                        for row in connection.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        ).fetchall()
                    }
                    if "task_reclaim_transitions" in tables:
                        raise ControlStoreUnavailable(
                            "Control Store v5 has a partial v6 reclaim migration"
                        )
                    connection.execute(TASK_RECLAIM_TRANSITIONS_TABLE_SQL)
                    self._backfill_task_reclaim_transitions(connection)
                    self._validate_task_reclaim_transition_table(connection)
                    self._validate_task_reclaim_transition_rows(connection)
                    connection.execute(
                        "INSERT INTO schema_migrations(version) VALUES (6)"
                    )
                    version = 6
                if version == 6:
                    if present != expected_columns:
                        raise ControlStoreUnavailable(
                            "Control Store v6 intent identity columns are incomplete"
                        )
                    self._validate_run_state_mutation_table(connection)
                    self._validate_run_state_mutation_identity_table(connection)
                    self._validate_run_state_mutation_rows(connection)
                    self._validate_task_tables(connection)
                    self._validate_task_attempt_authority_rows(connection)
                    self._validate_task_completion_authority_rows(connection)
                    self._validate_task_promotion_identity_rows(connection)
                    self._validate_task_reclaim_transition_table(connection)
                    self._validate_task_reclaim_transition_rows(connection)
                    if self._migration_versions(connection) != [1, 2, 3, 4, 5, 6]:
                        raise ControlStoreUnavailable(
                            "Control Store v6 migration ledger is incomplete"
                        )
                    tables = {
                        str(row[0])
                        for row in connection.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        ).fetchall()
                    }
                    if "task_claim_authorities" in tables:
                        raise ControlStoreUnavailable(
                            "Control Store v6 has a partial v7 Claim authority migration"
                        )
                    connection.execute(TASK_CLAIM_AUTHORITIES_TABLE_SQL)
                    self._backfill_task_claim_authorities(connection)
                    self._validate_task_claim_authority_table(connection)
                    self._validate_task_claim_authority_rows(connection)
                    connection.execute(
                        "INSERT INTO schema_migrations(version) VALUES (7)"
                    )
                    version = 7
                if version == 7:
                    if present != expected_columns:
                        raise ControlStoreUnavailable(
                            "Control Store v7 intent identity columns are incomplete"
                        )
                    self._validate_run_state_mutation_table(connection)
                    self._validate_run_state_mutation_identity_table(connection)
                    self._validate_run_state_mutation_rows(connection)
                    self._validate_task_tables(connection)
                    self._validate_task_attempt_authority_rows(connection)
                    self._validate_task_completion_authority_rows(connection)
                    self._validate_task_promotion_identity_rows(connection)
                    self._validate_task_reclaim_transition_table(connection)
                    self._validate_task_reclaim_transition_rows(connection)
                    self._validate_task_claim_authority_table(connection)
                    self._validate_task_claim_authority_rows(connection)
                    if self._migration_versions(connection) != [
                        1, 2, 3, 4, 5, 6, 7
                    ]:
                        raise ControlStoreUnavailable(
                            "Control Store v7 migration ledger is incomplete"
                        )
                    resource_tables = {
                        "resource_configurations",
                        "resource_sequences",
                        "resource_queue_entries",
                        "resource_leases",
                        "resource_lease_resources",
                        "resource_fairness_cursors",
                        "resource_circuit_breakers",
                        "resource_control_events",
                    }
                    tables = {
                        str(row[0])
                        for row in connection.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        ).fetchall()
                    }
                    if resource_tables & tables:
                        raise ControlStoreUnavailable(
                            "Control Store v7 has a partial v8 Resource Admission migration"
                        )
                    self._create_resource_tables(connection)
                    self._insert_resource_configuration(
                        connection,
                        resource_configuration,
                        resource_configuration_json,
                        resource_configuration_sha256,
                    )
                    connection.execute(
                        "INSERT INTO schema_migrations(version) VALUES (8)"
                    )
                    version = 8
                if version == 8:
                    self._validate_resource_tables(connection)
                else:
                    raise ControlStoreUnavailable(
                        f"unknown Control Store schema version: {version}"
                    )
        except ControlStoreUnavailable:
            raise
        except (sqlite3.Error, OSError) as exc:
            raise ControlStoreUnavailable(
                f"Control Store migration failed: {exc}"
            ) from exc

    @staticmethod
    def _migration_rows(
        connection: sqlite3.Connection,
        table: str,
        columns: tuple[str, ...],
        order_by: str,
    ) -> tuple[tuple[object, ...], ...]:
        selected = ", ".join(columns)
        return tuple(
            tuple(row)
            for row in connection.execute(
                f"SELECT {selected} FROM {table} ORDER BY {order_by}"
            ).fetchall()
        )

    def _prepare_migration_plan(
        self,
        connection: sqlite3.Connection,
    ) -> _MigrationPlan:
        resource_configuration, resource_json, resource_sha256 = (
            self._resource_configuration_identity()
        )
        versions = self._migration_versions(connection)
        source_version = 0 if not versions else versions[-1]
        snapshot = sqlite3.connect(":memory:", isolation_level=None)
        snapshot.row_factory = sqlite3.Row
        try:
            connection.backup(snapshot)
            snapshot.execute("PRAGMA foreign_keys=ON")
            snapshot.execute("PRAGMA trusted_schema=OFF")
            self._upgrade_migration_snapshot(
                snapshot,
                resource_configuration,
                resource_json,
                resource_sha256,
            )
            return _MigrationPlan(
                source_version=source_version,
                run_state_identity_rows=self._migration_rows(
                    snapshot,
                    "run_state_mutation_identity_versions",
                    ("mutation_id", "identity_version", "row_identity"),
                    "mutation_id",
                ),
                task_attempt_authority_rows=self._migration_rows(
                    snapshot,
                    "task_attempt_authorities",
                    (
                        "attempt_id",
                        "attempt_record_json",
                        "attempt_record_sha256",
                    ),
                    "attempt_id",
                ),
                task_completion_authority_rows=self._migration_rows(
                    snapshot,
                    "task_completion_authorities",
                    ("attempt_id", "completion_record_json"),
                    "attempt_id",
                ),
                task_promotion_identity_rows=self._migration_rows(
                    snapshot,
                    "task_promotion_identity_versions",
                    ("intent_id", "identity_version"),
                    "intent_id",
                ),
                task_reclaim_transition_rows=self._migration_rows(
                    snapshot,
                    "task_reclaim_transitions",
                    (
                        "transition_id",
                        "authority_id",
                        "task_id",
                        "prior_attempt_id",
                        "replacement_attempt_id",
                        "prior_claim_generation",
                        "replacement_claim_generation",
                        "recovery_reason",
                        "prior_coordinator_session_id",
                        "prior_worker_id",
                        "replacement_coordinator_session_id",
                        "replacement_worker_id",
                        "reclaimed_at",
                        "transition_record_json",
                    ),
                    "task_id, replacement_claim_generation",
                ),
                task_claim_authority_rows=self._migration_rows(
                    snapshot,
                    "task_claim_authorities",
                    ("task_id", "claim_record_json", "claim_record_sha256"),
                    "task_id",
                ),
                resource_configuration_json=resource_json,
                resource_configuration_sha256=resource_sha256,
            )
        finally:
            snapshot.close()

    @staticmethod
    def _assert_precomputed_row_count(
        connection: sqlite3.Connection,
        table: str,
        expected: int,
    ) -> None:
        actual = int(
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        )
        if actual != expected:
            raise ControlStoreUnavailable(
                f"Control Store migration row count differs for {table}"
            )

    def _apply_migration_plan(
        self,
        connection: sqlite3.Connection,
        plan: _MigrationPlan,
    ) -> None:
        versions = self._migration_versions(connection)
        version = 0 if not versions else versions[-1]
        if version != plan.source_version:
            raise ControlStoreUnavailable(
                "Control Store migration source snapshot changed before apply"
            )
        expected_columns = {
            "expected_run_record_sha256",
            "canonical_platform",
            "canonical_item_id",
            "source_identity",
            "source_manifest_sha256",
        }
        if version == 1:
            columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(initialization_intents)"
                ).fetchall()
            }
            if expected_columns & columns:
                raise ControlStoreUnavailable(
                    "Slice 1 v1 Control Store has a partial v2 intent migration"
                )
            for column in sorted(expected_columns):
                connection.execute(
                    f"ALTER TABLE initialization_intents ADD COLUMN {column} TEXT"
                )
            connection.execute("INSERT INTO schema_migrations(version) VALUES (2)")
            version = 2
        if version == 2:
            connection.execute(RUN_STATE_MUTATION_TABLE_SQL)
            connection.execute(RUN_STATE_MUTATION_INDEX_SQL)
            connection.execute("INSERT INTO schema_migrations(version) VALUES (3)")
            version = 3
        if version == 3:
            connection.execute(TASK_CLAIMS_TABLE_SQL)
            connection.execute(TASK_CLAIMS_AUTHORITY_STATE_INDEX_SQL)
            connection.execute(TASK_ATTEMPTS_TABLE_SQL)
            connection.execute(TASK_PROMOTION_TABLE_SQL)
            connection.execute(TASK_PROMOTION_INDEX_SQL)
            connection.execute("INSERT INTO schema_migrations(version) VALUES (4)")
            version = 4
        if version == 4:
            connection.execute(RUN_STATE_MUTATION_IDENTITY_VERSIONS_TABLE_SQL)
            connection.execute(TASK_ATTEMPT_AUTHORITIES_TABLE_SQL)
            connection.execute(TASK_COMPLETION_AUTHORITIES_TABLE_SQL)
            connection.execute(TASK_PROMOTION_IDENTITY_VERSIONS_TABLE_SQL)
            connection.executemany(
                "INSERT INTO run_state_mutation_identity_versions("
                "mutation_id, identity_version, row_identity) VALUES (?, ?, ?)",
                plan.run_state_identity_rows,
            )
            connection.executemany(
                "INSERT INTO task_attempt_authorities("
                "attempt_id, attempt_record_json, attempt_record_sha256) "
                "VALUES (?, ?, ?)",
                plan.task_attempt_authority_rows,
            )
            connection.executemany(
                "INSERT INTO task_completion_authorities("
                "attempt_id, completion_record_json) VALUES (?, ?)",
                plan.task_completion_authority_rows,
            )
            connection.executemany(
                "INSERT INTO task_promotion_identity_versions("
                "intent_id, identity_version) VALUES (?, ?)",
                plan.task_promotion_identity_rows,
            )
            self._assert_precomputed_row_count(
                connection,
                "run_state_mutation_identity_versions",
                len(plan.run_state_identity_rows),
            )
            self._assert_precomputed_row_count(
                connection,
                "task_attempt_authorities",
                len(plan.task_attempt_authority_rows),
            )
            self._assert_precomputed_row_count(
                connection,
                "task_completion_authorities",
                len(plan.task_completion_authority_rows),
            )
            self._assert_precomputed_row_count(
                connection,
                "task_promotion_identity_versions",
                len(plan.task_promotion_identity_rows),
            )
            connection.execute("INSERT INTO schema_migrations(version) VALUES (5)")
            version = 5
        if version == 5:
            connection.execute(TASK_RECLAIM_TRANSITIONS_TABLE_SQL)
            connection.executemany(
                "INSERT INTO task_reclaim_transitions("
                "transition_id, authority_id, task_id, prior_attempt_id, "
                "replacement_attempt_id, prior_claim_generation, "
                "replacement_claim_generation, recovery_reason, "
                "prior_coordinator_session_id, prior_worker_id, "
                "replacement_coordinator_session_id, replacement_worker_id, "
                "reclaimed_at, transition_record_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                plan.task_reclaim_transition_rows,
            )
            self._assert_precomputed_row_count(
                connection,
                "task_reclaim_transitions",
                len(plan.task_reclaim_transition_rows),
            )
            connection.execute("INSERT INTO schema_migrations(version) VALUES (6)")
            version = 6
        if version == 6:
            connection.execute(TASK_CLAIM_AUTHORITIES_TABLE_SQL)
            connection.executemany(
                "INSERT INTO task_claim_authorities("
                "task_id, claim_record_json, claim_record_sha256) "
                "VALUES (?, ?, ?)",
                plan.task_claim_authority_rows,
            )
            self._assert_precomputed_row_count(
                connection,
                "task_claim_authorities",
                len(plan.task_claim_authority_rows),
            )
            connection.execute("INSERT INTO schema_migrations(version) VALUES (7)")
            version = 7
        if version == 7:
            resource_configuration = json.loads(plan.resource_configuration_json)
            self.contracts.validate(
                "resource-admission-configuration", resource_configuration
            )
            self._create_resource_tables(connection)
            self._insert_resource_configuration(
                connection,
                resource_configuration,
                plan.resource_configuration_json,
                plan.resource_configuration_sha256,
            )
            connection.execute("INSERT INTO schema_migrations(version) VALUES (8)")
            version = 8
        self._ensure_maintenance_indexes(connection)
        if version != SCHEMA_VERSION or self._migration_versions(connection) != list(
            range(1, SCHEMA_VERSION + 1)
        ):
            raise ControlStoreUnavailable(
                f"unknown Control Store schema version: {version}"
            )

    def _validate_migrated_snapshot(self) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN")
            plan = self._prepare_migration_plan(connection)
            if plan.source_version != SCHEMA_VERSION:
                raise ControlStoreUnavailable(
                    "Control Store migration did not reach the current schema"
                )
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def _migrate_existing(self) -> None:
        for _attempt in range(SNAPSHOT_RETRY_LIMIT):
            connection = self._connect()
            try:
                connection.execute("BEGIN")
                snapshot_data_version = self._data_version(connection)
                plan = self._prepare_migration_plan(connection)
                maintenance_indexes_valid = self._maintenance_index_is_valid(
                    connection
                )
                connection.execute("COMMIT")
                if plan.source_version == SCHEMA_VERSION and maintenance_indexes_valid:
                    return
                self._assert_mutation_allowed()
                if not self._begin_immediate_if_snapshot_unchanged(
                    connection,
                    snapshot_data_version,
                ):
                    continue
                if plan.source_version == SCHEMA_VERSION:
                    self._ensure_maintenance_indexes(connection)
                else:
                    self._apply_migration_plan(connection, plan)
                connection.execute("COMMIT")
                self._validate_migrated_snapshot()
                return
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            finally:
                connection.close()
        raise ControlStoreUnavailable(
            "Control Store changed during migration preflight; bounded retry exhausted"
        )

    def quiesce_writers(self) -> None:
        """Wait for every pre-sentinel writer to finish under an exclusive lock."""
        if not self.recovery_sentinel_path.is_file():
            raise ControlStoreUnavailable(
                "Control Store quiescence requires persistent recovery authority"
            )
        connection = self._connect()
        try:
            connection.execute("BEGIN EXCLUSIVE")
            connection.execute("ROLLBACK")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def backup_to(self, destination: Path) -> None:
        """Publish a coherent file-backed snapshot through SQLite's backup API."""
        destination = destination.resolve()
        if destination.exists():
            raise ControlStoreUnavailable(
                "Control Store backup destination already exists"
            )
        source = self._connect()
        target: sqlite3.Connection | None = None
        try:
            target = sqlite3.connect(
                str(destination),
                timeout=BUSY_TIMEOUT_MS / 1000,
                isolation_level=None,
            )
            source.backup(target)
            if str(target.execute("PRAGMA quick_check").fetchone()[0]) != "ok":
                raise ControlStoreUnavailable(
                    "Control Store backup failed its offline integrity check"
                )
            if target.execute("PRAGMA foreign_key_check").fetchall():
                raise ControlStoreUnavailable(
                    "Control Store backup failed its offline foreign-key check"
                )
        except (sqlite3.Error, OSError) as exc:
            raise ControlStoreUnavailable(
                f"Control Store SQLite backup failed: {exc}"
            ) from exc
        finally:
            if target is not None:
                target.close()
            source.close()

    @classmethod
    def validate_backup_candidate(
        cls,
        workspace_root: Path,
        contracts: ContractRegistry,
        backup_dir: Path,
    ) -> ControlStoreHealth:
        """Run the live health validator against an independently selected package."""
        candidate = cls.__new__(cls)
        candidate._configure(workspace_root, contracts)
        candidate.control_dir = backup_dir.resolve()
        candidate.path = candidate.control_dir / "control.sqlite3"
        candidate.marker_path = candidate.control_dir / MARKER_NAME
        candidate.anchor_path = candidate.control_dir / "anchor.json"
        if (
            not candidate.path.is_file()
            or not candidate.marker_path.is_file()
            or not candidate.anchor_path.is_file()
        ):
            raise ControlStoreUnavailable(
                "Control Store backup candidate is missing required identity artifacts"
            )
        try:
            connection = candidate._connect_raw()
            try:
                version = int(
                    connection.execute(
                        "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
                    ).fetchone()[0]
                )
                versions = candidate._migration_versions(connection)
                maintenance_indexes_valid = candidate._maintenance_index_is_valid(
                    connection
                )
            finally:
                connection.close()
        except (sqlite3.Error, OSError) as exc:
            raise ControlStoreUnavailable(
                f"Control Store backup candidate cannot be inspected: {exc}"
            ) from exc
        if version != SCHEMA_VERSION or versions != list(
            range(1, SCHEMA_VERSION + 1)
        ):
            raise ControlStoreUnavailable(
                f"Control Store backup candidate schema mismatch: {version}"
            )
        if not maintenance_indexes_valid:
            raise ControlStoreUnavailable(
                "Control Store backup candidate maintenance index is invalid"
            )
        return candidate.check()

    def run_authority_ids(self) -> tuple[str, ...]:
        connection = self._connect()
        try:
            return tuple(
                str(row[0])
                for row in connection.execute(
                    "SELECT run_id FROM run_bindings ORDER BY run_id"
                ).fetchall()
            )
        finally:
            connection.close()

    def active_resource_configuration_identity(self) -> dict[str, object]:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT configuration_id, configuration_version, "
                "configuration_sha256 FROM resource_configurations "
                "WHERE state='ACTIVE'"
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise ControlStoreUnavailable(
                "Control Store has no active Resource Admission Configuration"
            )
        return {
            "configuration_id": str(row["configuration_id"]),
            "configuration_version": int(row["configuration_version"]),
            "configuration_sha256": str(row["configuration_sha256"]),
        }

    def check(self) -> ControlStoreHealth:
        self._validate_existing()
        try:
            connection = self._connect()
            try:
                pragmas = {
                    "journal_mode": connection.execute("PRAGMA journal_mode").fetchone()[0],
                    "synchronous": connection.execute("PRAGMA synchronous").fetchone()[0],
                    "foreign_keys": connection.execute("PRAGMA foreign_keys").fetchone()[0],
                    "trusted_schema": connection.execute("PRAGMA trusted_schema").fetchone()[0],
                    "busy_timeout": connection.execute("PRAGMA busy_timeout").fetchone()[0],
                }
                required = {
                    "journal_mode": "delete",
                    "synchronous": 3,
                    "foreign_keys": 1,
                    "trusted_schema": 0,
                    "busy_timeout": BUSY_TIMEOUT_MS,
                }
                if str(pragmas["journal_mode"]).lower() != required["journal_mode"]:
                    raise ControlStoreUnavailable("Control Store journal_mode is not DELETE")
                for name in ("synchronous", "foreign_keys", "trusted_schema", "busy_timeout"):
                    if int(pragmas[name]) != required[name]:
                        raise ControlStoreUnavailable(
                            f"Control Store effective PRAGMA mismatch: {name}={pragmas[name]}"
                        )
                quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
                foreign_key_rows = connection.execute("PRAGMA foreign_key_check").fetchall()
                if quick_check != "ok" or foreign_key_rows:
                    raise ControlStoreUnavailable("Control Store integrity check failed")
                tables = {
                    str(row[0])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                required_tables = {
                    "schema_migrations",
                    "control_store_metadata",
                    "run_bindings",
                    "initialization_intents",
                    "run_state_mutation_intents",
                    "run_state_mutation_identity_versions",
                    "task_claims",
                    "task_claim_authorities",
                    "task_attempts",
                    "task_attempt_authorities",
                    "task_completion_authorities",
                    "task_promotion_identity_versions",
                    "task_promotion_intents",
                    "task_reclaim_transitions",
                    "resource_configurations",
                    "resource_sequences",
                    "resource_queue_entries",
                    "resource_leases",
                    "resource_lease_resources",
                    "resource_fairness_cursors",
                    "resource_circuit_breakers",
                    "resource_control_events",
                }
                if not required_tables.issubset(tables):
                    raise ControlStoreUnavailable(
                        f"Control Store schema is incomplete: {sorted(required_tables - tables)}"
                    )
                conflicting_slots = connection.execute(
                    "SELECT run_id, COUNT(*) AS active_count FROM ("
                    "SELECT run_id FROM run_state_mutation_intents WHERE state='PREPARED' "
                    "UNION ALL "
                    "SELECT run_id FROM task_promotion_intents WHERE state IN "
                    "('PREPARED','FILES_PUBLISHED','RECORD_COMMITTED')) "
                    "GROUP BY run_id HAVING COUNT(*) > 1"
                ).fetchall()
                if conflicting_slots:
                    raise ControlStoreUnavailable(
                        "Control Store has multiple non-terminal Run Promotion Slot owners"
                    )
                version = int(
                    connection.execute(
                        "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
                    ).fetchone()[0]
                )
                if version != SCHEMA_VERSION:
                    raise ControlStoreUnavailable(
                        f"unknown Control Store schema version: {version}"
                    )
                self._validate_run_state_mutation_table(connection)
                self._validate_run_state_mutation_identity_table(connection)
                self._validate_run_state_mutation_rows(connection)
                self._validate_task_tables(connection)
                self._validate_task_attempt_authority_rows(connection)
                self._validate_task_completion_authority_rows(connection)
                self._validate_task_promotion_identity_rows(connection)
                self._validate_task_reclaim_transition_table(connection)
                self._validate_task_reclaim_transition_rows(connection)
                self._validate_task_claim_authority_table(connection)
                self._validate_task_claim_authority_rows(connection)
                self._validate_resource_tables(connection)
                if not self._maintenance_index_is_valid(connection):
                    raise ControlStoreUnavailable(
                        "Control Store Task Claim maintenance index is invalid"
                    )
                self._probe_lock_contention(connection)
            finally:
                connection.close()
        except (sqlite3.Error, OSError) as exc:
            raise ControlStoreUnavailable(f"Control Store health check failed: {exc}") from exc
        try:
            self._probe_atomic_replace()
        except OSError as exc:
            raise ControlStoreUnavailable(
                f"Control Store atomic replace probe failed: {exc}"
            ) from exc
        return ControlStoreHealth(
            status="ok",
            schema_version=SCHEMA_VERSION,
            pragmas=pragmas,
            quick_check=quick_check,
            path=self.path,
            lock_contention_checked=True,
            atomic_replace_checked=True,
        )

    def _probe_lock_contention(self, primary: sqlite3.Connection) -> None:
        secondary = self._connect_raw()
        try:
            secondary.execute(f"PRAGMA busy_timeout={LOCK_PROBE_TIMEOUT_MS}")
            primary.execute("BEGIN IMMEDIATE")
            started = time.monotonic()
            try:
                secondary.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as exc:
                elapsed_ms = (time.monotonic() - started) * 1000
                if "locked" not in str(exc).lower() or elapsed_ms > BUSY_TIMEOUT_MS:
                    raise ControlStoreUnavailable(
                        f"Control Store lock probe failed unexpectedly: {exc}"
                    ) from exc
            else:
                secondary.execute("ROLLBACK")
                raise ControlStoreUnavailable(
                    "Control Store second connection bypassed an immediate writer lock"
                )
            finally:
                primary.execute("ROLLBACK")
            secondary.execute("BEGIN IMMEDIATE")
            secondary.execute("ROLLBACK")
        finally:
            secondary.close()

    def _probe_atomic_replace(self) -> None:
        probe_root = (
            self.workspace_root.parent
            / "待删除"
            / "atomic staging"
            / self.store_id[:16]
            / uuid.uuid4().hex
        )
        probe_root.mkdir(parents=True, exist_ok=False)
        if os.stat(probe_root).st_dev != os.stat(self.control_dir).st_dev:
            raise ControlStoreUnavailable("atomic replace probe is not on the Control Store volume")
        source = probe_root / "replace-source"
        target = probe_root / "replace-target"
        with source.open("wb") as handle:
            handle.write(b"video-workflow-atomic-replace-v1\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(source, target)
        if source.exists() or target.read_bytes() != b"video-workflow-atomic-replace-v1\n":
            raise ControlStoreUnavailable("same-volume atomic replace probe failed")

    def binding_for_run(self, run_id: str) -> sqlite3.Row | None:
        connection = self._connect()
        try:
            return connection.execute(
                "SELECT * FROM run_bindings WHERE run_id=?", (run_id,)
            ).fetchone()
        finally:
            connection.close()

    def binding_for_path(self, output_path: Path) -> sqlite3.Row | None:
        normalized = normalized_physical_path(output_path)
        connection = self._connect()
        try:
            return connection.execute(
                "SELECT * FROM run_bindings WHERE normalized_path=?", (normalized,)
            ).fetchone()
        finally:
            connection.close()

    def bind_run(
        self, *, run_id: str, output_path: Path, initialization_intent_id: str
    ) -> None:
        normalized = normalized_physical_path(output_path)
        with self._immediate() as connection:
            by_run = connection.execute(
                "SELECT * FROM run_bindings WHERE run_id=?", (run_id,)
            ).fetchone()
            by_path = connection.execute(
                "SELECT * FROM run_bindings WHERE normalized_path=?", (normalized,)
            ).fetchone()
            if by_run:
                if (
                    by_run["normalized_path"] != normalized
                    or by_run["initialization_intent_id"] != initialization_intent_id
                ):
                    raise KernelConflict("run identity is already bound to different state")
                return
            if by_path:
                raise KernelConflict("output path is already bound to a different run")
            connection.execute(
                "INSERT INTO run_bindings(run_id, normalized_path, output_path, "
                "initialization_intent_id) VALUES (?, ?, ?, ?)",
                (run_id, normalized, str(output_path.resolve()), initialization_intent_id),
            )

    def prepare_initialization(
        self,
        *,
        run_id: str,
        output_path: Path,
        intent_id: str,
        staging_path: Path,
    ) -> str:
        normalized = normalized_physical_path(output_path)
        with self._immediate() as connection:
            existing_intent = connection.execute(
                "SELECT * FROM initialization_intents WHERE run_id=?",
                (run_id,),
            ).fetchone()
            existing_binding = connection.execute(
                "SELECT * FROM run_bindings WHERE run_id=?", (run_id,)
            ).fetchone()
            if existing_intent and existing_intent["state"] == "ABORTED":
                if existing_intent["intent_id"] != intent_id:
                    raise KernelConflict("aborted initialization identity changed on retry")
                if existing_binding is not None:
                    raise KernelConflict("aborted initialization retained an active binding")
                connection.execute(
                    "INSERT INTO run_bindings(run_id, normalized_path, output_path, "
                    "initialization_intent_id) VALUES (?, ?, ?, ?)",
                    (run_id, normalized, str(output_path.resolve()), intent_id),
                )
                connection.execute(
                    "UPDATE initialization_intents SET output_path=?, staging_path=?, "
                    "state='PREPARED', run_record_sha256=NULL WHERE intent_id=?",
                    (str(output_path.resolve()), str(staging_path.resolve()), intent_id),
                )
                return "PREPARED"
            if existing_intent:
                if (
                    existing_intent["intent_id"] != intent_id
                    or existing_binding is None
                    or existing_binding["normalized_path"] != normalized
                ):
                    raise KernelConflict("run initialization identity changed on retry")
                return str(existing_intent["state"])
            by_path = connection.execute(
                "SELECT run_id FROM run_bindings WHERE normalized_path=?", (normalized,)
            ).fetchone()
            if by_path:
                raise KernelConflict("output path is already bound to a different run")
            connection.execute(
                "INSERT INTO run_bindings(run_id, normalized_path, output_path, "
                "initialization_intent_id) VALUES (?, ?, ?, ?)",
                (run_id, normalized, str(output_path.resolve()), intent_id),
            )
            connection.execute(
                "INSERT INTO initialization_intents(intent_id, run_id, output_path, "
                "staging_path, state) VALUES (?, ?, ?, ?, 'PREPARED')",
                (intent_id, run_id, str(output_path.resolve()), str(staging_path.resolve())),
            )
            return "PREPARED"

    def intent_for_run(self, run_id: str) -> sqlite3.Row | None:
        connection = self._connect()
        try:
            return connection.execute(
                "SELECT * FROM initialization_intents WHERE run_id=?", (run_id,)
            ).fetchone()
        finally:
            connection.close()

    def bind_publication_expectations(
        self,
        intent_id: str,
        *,
        expected_run_record_sha256: str,
        canonical_platform: str,
        canonical_item_id: str,
        source_identity: str,
        source_manifest_sha256: str,
    ) -> None:
        values = (
            expected_run_record_sha256,
            canonical_platform,
            canonical_item_id,
            source_identity,
            source_manifest_sha256,
        )
        with self._immediate() as connection:
            cursor = connection.execute(
                "UPDATE initialization_intents SET "
                "expected_run_record_sha256=?, canonical_platform=?, "
                "canonical_item_id=?, source_identity=?, source_manifest_sha256=? "
                "WHERE intent_id=? AND state='PREPARED' "
                "AND expected_run_record_sha256 IS NULL "
                "AND canonical_platform IS NULL AND canonical_item_id IS NULL "
                "AND source_identity IS NULL AND source_manifest_sha256 IS NULL",
                (*values, intent_id),
            )
            if cursor.rowcount == 1:
                return
            row = connection.execute(
                "SELECT state, expected_run_record_sha256, canonical_platform, "
                "canonical_item_id, source_identity, source_manifest_sha256 "
                "FROM initialization_intents WHERE intent_id=?",
                (intent_id,),
            ).fetchone()
            if row is not None and row["state"] == "PREPARED" and tuple(row)[1:] == values:
                return
            raise KernelConflict(
                "initialization publication expectations changed or were bound late",
                data={"intent_id": intent_id},
            )

    def transition_intent(
        self,
        intent_id: str,
        *,
        expected_state: str,
        new_state: str,
        run_record_sha256: str | None = None,
    ) -> None:
        allowed = {
            ("PREPARED", "PUBLISHED"),
            ("PUBLISHED", "RECORD_COMMITTED"),
            ("RECORD_COMMITTED", "COMMITTED"),
        }
        if (expected_state, new_state) not in allowed:
            raise KernelConflict(
                "initialization intent transition is outside the state machine",
                data={
                    "expected_state": expected_state,
                    "new_state": new_state,
                },
            )
        with self._immediate() as connection:
            if (expected_state, new_state) == ("PREPARED", "PUBLISHED"):
                cursor = connection.execute(
                    "UPDATE initialization_intents SET state=?, "
                    "run_record_sha256=COALESCE(?, run_record_sha256) "
                    "WHERE intent_id=? AND state=? "
                    "AND expected_run_record_sha256 IS NOT NULL "
                    "AND canonical_platform IS NOT NULL "
                    "AND canonical_item_id IS NOT NULL "
                    "AND source_identity IS NOT NULL "
                    "AND source_manifest_sha256 IS NOT NULL",
                    (new_state, run_record_sha256, intent_id, expected_state),
                )
            else:
                cursor = connection.execute(
                    "UPDATE initialization_intents SET state=?, "
                    "run_record_sha256=COALESCE(?, run_record_sha256) "
                    "WHERE intent_id=? AND state=?",
                    (new_state, run_record_sha256, intent_id, expected_state),
                )
            if cursor.rowcount != 1:
                row = connection.execute(
                    "SELECT state FROM initialization_intents WHERE intent_id=?",
                    (intent_id,),
                ).fetchone()
                actual = None if row is None else str(row["state"])
                raise KernelConflict(
                    "initialization intent compare-and-swap failed",
                    data={
                        "intent_id": intent_id,
                        "expected_state": expected_state,
                        "actual_state": actual,
                        "new_state": new_state,
                    },
                )

    def abort_initialization(self, run_id: str) -> None:
        with self._immediate() as connection:
            cursor = connection.execute(
                "UPDATE initialization_intents SET state='ABORTED' "
                "WHERE run_id=? AND state='PREPARED'",
                (run_id,),
            )
            if cursor.rowcount != 1:
                row = connection.execute(
                    "SELECT state FROM initialization_intents WHERE run_id=?", (run_id,)
                ).fetchone()
                actual = None if row is None else str(row["state"])
                raise KernelConflict(
                    "only a PREPARED initialization can be aborted",
                    data={"run_id": run_id, "actual_state": actual},
                )
            connection.execute("DELETE FROM run_bindings WHERE run_id=?", (run_id,))

    @staticmethod
    def derive_legacy_run_state_mutation_identity(
        *,
        operation: str,
        run_id: str,
        expected_run_revision: int,
        old_run_record_sha256: str,
        predecessor_committed_sha256: str,
        replacement_run_record_sha256: str,
    ) -> str:
        payload = "\0".join(
            (
                operation,
                run_id,
                str(expected_run_revision),
                old_run_record_sha256,
                predecessor_committed_sha256,
                replacement_run_record_sha256,
            )
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def derive_run_state_mutation_row_identity(
        *, mutation_id: str, replacement_run_record_sha256: str
    ) -> str:
        payload = "\0".join(
            (
                "run-state-mutation-evidence-v2",
                mutation_id,
                replacement_run_record_sha256,
            )
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _validate_run_state_mutation_row(
        self, connection: sqlite3.Connection, mutation: sqlite3.Row
    ) -> dict:
        version = connection.execute(
            "SELECT identity_version, row_identity FROM "
            "run_state_mutation_identity_versions WHERE mutation_id=?",
            (mutation["mutation_id"],),
        ).fetchone()
        if version is None:
            raise ControlStoreUnavailable(
                "run-state mutation identity authority is absent"
            )
        replacement_json = str(mutation["replacement_run_record_json"])
        try:
            replacement = json.loads(replacement_json)
            self.contracts.validate_run_record(replacement)
        except (json.JSONDecodeError, ContractError) as exc:
            raise ControlStoreUnavailable(
                "run-state mutation replacement contract is invalid"
            ) from exc
        canonical = canonical_json_bytes(replacement).decode("utf-8")
        replacement_sha = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if (
            mutation["operation"] != "source_drift_invalidation"
            or canonical != replacement_json
            or replacement_sha != mutation["replacement_run_record_sha256"]
            or mutation["old_run_record_sha256"]
            != mutation["predecessor_committed_sha256"]
            or replacement.get("run_id") != mutation["run_id"]
            or replacement.get("coordination_revision")
            != int(mutation["expected_run_revision"]) + 1
        ):
            raise ControlStoreUnavailable(
                "run-state mutation replacement authority is invalid"
            )
        identity_version = str(version["identity_version"])
        if identity_version == "legacy-v1":
            expected_mutation_id = self.derive_legacy_run_state_mutation_identity(
                operation=str(mutation["operation"]),
                run_id=str(mutation["run_id"]),
                expected_run_revision=int(mutation["expected_run_revision"]),
                old_run_record_sha256=str(mutation["old_run_record_sha256"]),
                predecessor_committed_sha256=str(
                    mutation["predecessor_committed_sha256"]
                ),
                replacement_run_record_sha256=replacement_sha,
            )
            expected_row_identity = expected_mutation_id
        elif identity_version == "evidence-v2":
            expected_mutation_id = self.derive_run_state_mutation_id(
                run_id=str(mutation["run_id"]),
                expected_run_revision=int(mutation["expected_run_revision"]),
                old_run_record_sha256=str(mutation["old_run_record_sha256"]),
            )
            expected_row_identity = self.derive_run_state_mutation_row_identity(
                mutation_id=expected_mutation_id,
                replacement_run_record_sha256=replacement_sha,
            )
        else:
            raise ControlStoreUnavailable(
                "run-state mutation identity version is unsupported"
            )
        if (
            mutation["mutation_id"] != expected_mutation_id
            or mutation["mutation_identity"] != expected_mutation_id
            or version["row_identity"] != expected_row_identity
            or (
                replacement.get("schema_version") == "2.0.0"
                and replacement.get("last_mutation_intent_id")
                != expected_mutation_id
            )
        ):
            raise ControlStoreUnavailable(
                "run-state mutation versioned identity is invalid"
            )
        return replacement

    def _validate_run_state_mutation_rows(
        self, connection: sqlite3.Connection
    ) -> None:
        mutations = connection.execute(
            "SELECT * FROM run_state_mutation_intents"
        ).fetchall()
        identity_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM run_state_mutation_identity_versions"
            ).fetchone()[0]
        )
        if identity_count != len(mutations):
            raise ControlStoreUnavailable(
                "run-state mutation identity authority coverage is incomplete"
            )
        for mutation in mutations:
            self._validate_run_state_mutation_row(connection, mutation)

    def _current_run_record_sha(
        self, connection: sqlite3.Connection, run_id: str
    ) -> str | None:
        initialization = connection.execute(
            "SELECT run_record_sha256, expected_run_record_sha256 "
            "FROM initialization_intents WHERE run_id=? AND state='COMMITTED'",
            (run_id,),
        ).fetchone()
        if initialization is None:
            return None
        current = str(
            initialization["run_record_sha256"]
            or initialization["expected_run_record_sha256"]
        )
        expected_revision = 1
        run_state_rows = connection.execute(
            "SELECT * FROM run_state_mutation_intents "
            "WHERE run_id=? AND state='COMMITTED' "
            "ORDER BY expected_run_revision",
            (run_id,),
        ).fetchall()
        run_state_mutations = []
        for row in run_state_rows:
            self._validate_run_state_mutation_row(connection, row)
            run_state_mutations.append(
                {
                    "expected_run_revision": row["expected_run_revision"],
                    "predecessor_committed_sha256": row[
                        "predecessor_committed_sha256"
                    ],
                    "replacement_run_record_sha256": row[
                        "replacement_run_record_sha256"
                    ],
                }
            )
        task_rows = connection.execute(
            "SELECT * FROM task_promotion_intents "
            "WHERE run_id=? AND state='COMMITTED' ORDER BY expected_run_revision",
            (run_id,),
        ).fetchall()
        task_mutations = []
        for row in task_rows:
            self._validate_task_promotion_intent(connection, row)
            task_mutations.append(
                {
                    "expected_run_revision": row["expected_run_revision"],
                    "predecessor_committed_sha256": row["old_run_record_sha256"],
                    "replacement_run_record_sha256": row[
                        "replacement_run_record_sha256"
                    ],
                }
            )
        mutations = sorted(
            [*run_state_mutations, *task_mutations],
            key=lambda row: int(row["expected_run_revision"]),
        )
        for mutation in mutations:
            if (
                int(mutation["expected_run_revision"]) != expected_revision
                or mutation["predecessor_committed_sha256"] != current
            ):
                raise ControlStoreUnavailable(
                    "committed run-state mutation hash chain is invalid"
                )
            current = str(mutation["replacement_run_record_sha256"])
            expected_revision += 1
        return current

    @staticmethod
    def _next_run_revision(connection: sqlite3.Connection, run_id: str) -> int:
        revisions = [
            int(row[0])
            for table in ("run_state_mutation_intents", "task_promotion_intents")
            for row in connection.execute(
                f"SELECT expected_run_revision FROM {table} "
                "WHERE run_id=? AND state='COMMITTED'",
                (run_id,),
            ).fetchall()
        ]
        if len(revisions) != len(set(revisions)):
            raise ControlStoreUnavailable(
                "committed Run mutation revisions are ambiguous"
            )
        return 1 if not revisions else max(revisions) + 1

    @staticmethod
    def _assert_run_promotion_slot(
        connection: sqlite3.Connection,
        run_id: str,
        *,
        owner_kind: str | None = None,
        owner_id: str | None = None,
    ) -> None:
        slots = [
            ("source_drift", str(row[0]))
            for row in connection.execute(
                "SELECT mutation_id FROM run_state_mutation_intents "
                "WHERE run_id=? AND state='PREPARED'",
                (run_id,),
            ).fetchall()
        ]
        slots.extend(
            ("task_promotion", str(row[0]))
            for row in connection.execute(
                "SELECT intent_id FROM task_promotion_intents WHERE run_id=? "
                "AND state IN ('PREPARED','FILES_PUBLISHED','RECORD_COMMITTED')",
                (run_id,),
            ).fetchall()
        )
        allowed = [] if owner_kind is None else [(owner_kind, owner_id)]
        if slots != allowed:
            raise KernelConflict(
                "Run Promotion Slot is occupied by another mutation",
                data={"run_id": run_id, "active_slots": slots},
            )

    def current_run_record_sha(self, run_id: str) -> str | None:
        connection = self._connect()
        try:
            return self._current_run_record_sha(connection, run_id)
        finally:
            connection.close()

    @staticmethod
    def derive_run_state_mutation_id(
        *,
        run_id: str,
        expected_run_revision: int,
        old_run_record_sha256: str,
    ) -> str:
        identity_payload = "\0".join(
            (
                "source_drift_invalidation",
                run_id,
                str(expected_run_revision),
                old_run_record_sha256,
            )
        )
        return hashlib.sha256(identity_payload.encode("utf-8")).hexdigest()

    def prepare_run_state_mutation(
        self,
        *,
        run_id: str,
        expected_run_revision: int,
        old_run_record_sha256: str,
        replacement_run_record: dict,
    ) -> sqlite3.Row:
        """Durably prepare the Slice 1 source-drift invalidation Saga."""
        operation = "source_drift_invalidation"
        self.contracts.validate_run_record(replacement_run_record)
        if (
            replacement_run_record.get("run_id") != run_id
            or replacement_run_record.get("coordination_revision")
            != expected_run_revision + 1
        ):
            raise KernelConflict("run-state mutation replacement identity is invalid")
        replacement_json = canonical_json_bytes(replacement_run_record).decode("utf-8")
        replacement_sha = hashlib.sha256(replacement_json.encode("utf-8")).hexdigest()
        mutation_id = self.derive_run_state_mutation_id(
            run_id=run_id,
            expected_run_revision=expected_run_revision,
            old_run_record_sha256=old_run_record_sha256,
        )
        mutation_identity = mutation_id
        row_identity = self.derive_run_state_mutation_row_identity(
            mutation_id=mutation_id,
            replacement_run_record_sha256=replacement_sha,
        )
        if (
            replacement_run_record.get("schema_version") == "2.0.0"
            and replacement_run_record.get("last_mutation_intent_id")
            != mutation_id
        ):
            raise KernelConflict(
                "v2 run-state mutation replacement lacks its intent identity"
            )

        def plan_run_state_mutation(connection: sqlite3.Connection) -> str:
            predecessor = self._current_run_record_sha(connection, run_id)
            if predecessor is None:
                raise KernelConflict("run-state mutation has no committed predecessor")
            if old_run_record_sha256 != predecessor:
                raise ArtifactDrift(
                    "Run Record differs from its committed authority predecessor",
                    data={"drifted_paths": ["workflow/run.json"]},
                )
            chain_revision = self._next_run_revision(connection, run_id)
            if expected_run_revision != chain_revision:
                raise KernelConflict(
                    "run-state mutation expected revision is outside the committed chain"
                )
            existing = connection.execute(
                "SELECT * FROM run_state_mutation_intents WHERE mutation_identity=?",
                (mutation_identity,),
            ).fetchone()
            if existing is not None:
                self._validate_run_state_mutation_row(connection, existing)
                if (
                    existing["operation"] != operation
                    or existing["run_id"] != run_id
                    or int(existing["expected_run_revision"])
                    != expected_run_revision
                    or existing["old_run_record_sha256"]
                    != old_run_record_sha256
                    or existing["predecessor_committed_sha256"] != predecessor
                    or existing["replacement_run_record_sha256"]
                    != replacement_sha
                    or existing["replacement_run_record_json"]
                    != replacement_json
                ):
                    raise KernelConflict(
                        "conflicting run-state mutation replay changed its replacement"
                    )
                self._assert_run_promotion_slot(
                    connection,
                    run_id,
                    owner_kind="source_drift",
                    owner_id=mutation_id,
                )
                return "REPLAY"
            self._assert_run_promotion_slot(connection, run_id)
            return "INSERT"

        with self._planned_immediate(plan_run_state_mutation) as (
            connection,
            plan,
        ):
            if plan == "REPLAY":
                existing = connection.execute(
                    "SELECT * FROM run_state_mutation_intents "
                    "WHERE mutation_id=? AND mutation_identity=?",
                    (mutation_id, mutation_identity),
                ).fetchone()
                if existing is None:
                    raise KernelConflict(
                        "run-state mutation replay compare-and-swap failed"
                    )
                return existing
            try:
                connection.execute(
                    "INSERT INTO run_state_mutation_intents("
                    "mutation_id, operation, run_id, expected_run_revision, "
                    "old_run_record_sha256, predecessor_committed_sha256, "
                    "replacement_run_record_sha256, replacement_run_record_json, "
                    "state, mutation_identity) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PREPARED', ?)",
                    (
                        mutation_id,
                        operation,
                        run_id,
                        expected_run_revision,
                        old_run_record_sha256,
                        old_run_record_sha256,
                        replacement_sha,
                        replacement_json,
                        mutation_identity,
                    ),
                )
                connection.execute(
                    "INSERT INTO run_state_mutation_identity_versions("
                    "mutation_id, identity_version, row_identity) "
                    "VALUES (?, 'evidence-v2', ?)",
                    (mutation_id, row_identity),
                )
            except sqlite3.IntegrityError as exc:
                raise KernelConflict(
                    "run-state mutation compare-and-swap identity conflicts"
                ) from exc
            return connection.execute(
                "SELECT * FROM run_state_mutation_intents WHERE mutation_id=?",
                (mutation_id,),
            ).fetchone()

    def prepared_run_state_mutation(self, run_id: str) -> sqlite3.Row | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM run_state_mutation_intents "
                "WHERE run_id=? AND state='PREPARED'",
                (run_id,),
            ).fetchone()
            if row is not None:
                self._validate_prepared_run_state_mutation(connection, row)
            return row
        finally:
            connection.close()

    def _validate_prepared_run_state_mutation(
        self, connection: sqlite3.Connection, mutation: sqlite3.Row
    ) -> None:
        replacement = self._validate_run_state_mutation_row(
            connection, mutation
        )
        predecessor = self._current_run_record_sha(connection, mutation["run_id"])
        expected_revision = self._next_run_revision(connection, mutation["run_id"])
        if (
            mutation["operation"] != "source_drift_invalidation"
            or mutation["expected_run_revision"] != expected_revision
            or mutation["old_run_record_sha256"] != predecessor
            or mutation["predecessor_committed_sha256"] != predecessor
            or (
                replacement.get("schema_version") == "2.0.0"
                and replacement.get("last_mutation_intent_id")
                != mutation["mutation_id"]
            )
        ):
            raise ControlStoreUnavailable(
                "prepared run-state mutation authority evidence is invalid"
            )

    def commit_run_state_mutation(self, mutation_id: str) -> None:
        def plan_run_state_commit(connection: sqlite3.Connection) -> tuple[str, str]:
            mutation = connection.execute(
                "SELECT * FROM run_state_mutation_intents WHERE mutation_id=?",
                (mutation_id,),
            ).fetchone()
            if mutation is None:
                raise KernelConflict(
                    "run-state mutation commit authority is absent"
                )
            if mutation["state"] == "PREPARED":
                self._validate_prepared_run_state_mutation(
                    connection, mutation
                )
            else:
                self._validate_run_state_mutation_row(connection, mutation)
            if mutation["state"] == "COMMITTED" and self._current_run_record_sha(
                connection,
                str(mutation["run_id"]),
            ) is None:
                raise ControlStoreUnavailable(
                    "committed run-state mutation lacks a complete Run chain"
                )
            return str(mutation["state"]), str(mutation["mutation_identity"])

        with self._planned_immediate(plan_run_state_commit) as (
            connection,
            plan,
        ):
            state, mutation_identity = plan
            if state == "COMMITTED":
                return
            cursor = connection.execute(
                "UPDATE run_state_mutation_intents SET state='COMMITTED' "
                "WHERE mutation_id=? AND mutation_identity=? AND state='PREPARED'",
                (mutation_id, mutation_identity),
            )
            if cursor.rowcount == 1:
                return
            raise KernelConflict("run-state mutation commit compare-and-swap failed")

    @staticmethod
    def _write_sets_overlap(left_json: str, right: tuple[str, ...]) -> bool:
        try:
            left = tuple(json.loads(left_json))
        except (TypeError, json.JSONDecodeError) as exc:
            raise ControlStoreUnavailable("stored Task Claim write set is invalid") from exc
        for first in left:
            if not isinstance(first, str):
                raise ControlStoreUnavailable("stored Task Claim write set is invalid")
            for second in right:
                if (
                    first == second
                    or first.startswith(f"{second}/")
                    or second.startswith(f"{first}/")
                ):
                    return True
        return False

    @staticmethod
    def _next_resource_sequence(
        connection: sqlite3.Connection, sequence_name: str
    ) -> int:
        connection.execute(
            "INSERT INTO resource_sequences(sequence_name, value) VALUES (?, 0) "
            "ON CONFLICT(sequence_name) DO NOTHING",
            (sequence_name,),
        )
        connection.execute(
            "UPDATE resource_sequences SET value=value+1 WHERE sequence_name=?",
            (sequence_name,),
        )
        row = connection.execute(
            "SELECT value FROM resource_sequences WHERE sequence_name=?",
            (sequence_name,),
        ).fetchone()
        if row is None:
            raise ControlStoreUnavailable("Resource scheduler sequence authority is missing")
        return int(row[0])

    @staticmethod
    def _active_resource_configuration_row(
        connection: sqlite3.Connection,
    ) -> sqlite3.Row:
        rows = connection.execute(
            "SELECT * FROM resource_configurations WHERE state='ACTIVE'"
        ).fetchall()
        if len(rows) != 1:
            raise ControlStoreUnavailable(
                "Resource scheduler requires exactly one active configuration"
            )
        return rows[0]

    @staticmethod
    def _resource_capacities(configuration_row: sqlite3.Row) -> dict[str, int]:
        try:
            configuration = json.loads(str(configuration_row["configuration_json"]))
            return {
                str(item["resource_class"]): int(item["capacity"])
                for item in configuration["resources"]
            }
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ControlStoreUnavailable(
                "active Resource Admission Configuration is unreadable"
            ) from exc

    @staticmethod
    def _resource_request_binding_sha256(
        *,
        task_id: str,
        attempt_id: str,
        claim_generation: int,
        run_id: str,
        envelope_sha256: str,
        required_resources: tuple[str, ...],
        fairness_group_id: str,
        batch_id: str | None,
    ) -> str:
        binding = {
            "task_id": task_id,
            "attempt_id": attempt_id,
            "claim_generation": claim_generation,
            "run_id": run_id,
            "envelope_sha256": envelope_sha256,
            "required_resources": list(required_resources),
            "fairness_group_id": fairness_group_id,
            "batch_id": batch_id,
        }
        return hashlib.sha256(canonical_json_bytes(binding)).hexdigest()

    @staticmethod
    def _resource_usage(
        connection: sqlite3.Connection, resource_class: str
    ) -> int:
        return int(
            connection.execute(
                "SELECT COUNT(*) FROM resource_lease_resources lr "
                "JOIN resource_leases l ON l.lease_id=lr.lease_id "
                "WHERE lr.resource_class=? AND l.state IN ('starting','active','unknown')",
                (resource_class,),
            ).fetchone()[0]
        )

    @staticmethod
    def _resource_status_row(
        connection: sqlite3.Connection, task_id: str, attempt_id: str
    ) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT q.*, l.state AS lease_state, "
            "l.admission_configuration_id, l.admission_configuration_version, "
            "l.admission_configuration_sha256, l.launch_authorization_state, "
            "l.launch_required_resources_json, "
            "l.launch_required_resources_sha256, l.launch_execution_identity_json, "
            "l.launch_execution_identity_sha256, "
            "l.terminal_evidence_json, l.terminal_evidence_sha256, "
            "c.state AS current_claim_state, "
            "c.attempt_id AS current_claim_attempt_id, "
            "c.claim_generation AS current_claim_generation "
            "FROM resource_queue_entries q LEFT JOIN resource_leases l "
            "ON l.lease_id=q.lease_id "
            "LEFT JOIN task_claims c ON c.task_id=q.task_id "
            "WHERE q.task_id=? AND q.attempt_id=?",
            (task_id, attempt_id),
        ).fetchone()

    def resource_status(self, task_id: str, attempt_id: str) -> sqlite3.Row | None:
        connection = self._connect()
        try:
            return self._resource_status_row(connection, task_id, attempt_id)
        finally:
            connection.close()

    def resource_status_by_attempt(self, attempt_id: str) -> sqlite3.Row | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT task_id FROM resource_queue_entries WHERE attempt_id=?",
                (attempt_id,),
            ).fetchone()
            if row is None:
                return None
            return self._resource_status_row(
                connection, str(row["task_id"]), attempt_id
            )
        finally:
            connection.close()

    def authorize_resource_launch(
        self,
        *,
        attempt_id: str,
        claim_generation: int,
        required_resources: tuple[str, ...],
        updated_at: str,
    ) -> sqlite3.Row:
        if (
            not required_resources
            or tuple(sorted(required_resources)) != required_resources
            or len(required_resources) != len(set(required_resources))
        ):
            raise ContractError(
                "Resource launch request must be non-empty, unique, and stably sorted"
            )
        required_resources_json = canonical_json_bytes(
            list(required_resources)
        ).decode("utf-8")
        required_resources_sha256 = hashlib.sha256(
            required_resources_json.encode("utf-8")
        ).hexdigest()
        with self._immediate() as connection:
            queue = connection.execute(
                "SELECT q.*, l.state AS lease_state, "
                "l.launch_authorization_state FROM resource_queue_entries q "
                "LEFT JOIN resource_leases l ON l.lease_id=q.lease_id "
                "WHERE q.attempt_id=?",
                (attempt_id,),
            ).fetchone()
            if queue is None or queue["state"] != "ADMITTED" or queue["lease_id"] is None:
                raise ResourceAdmissionBlocked(
                    "Task Attempt has no complete admitted Resource Lease",
                    data={"attempt_id": attempt_id},
                )
            claim = connection.execute(
                "SELECT attempt_id, claim_generation, state FROM task_claims "
                "WHERE task_id=?",
                (queue["task_id"],),
            ).fetchone()
            if (
                claim is None
                or claim["state"] != "ACTIVE"
                or claim["attempt_id"] != attempt_id
                or int(claim["claim_generation"]) != claim_generation
            ):
                raise KernelConflict("Resource launch fencing compare-and-set failed")
            resources = tuple(
                str(row["resource_class"])
                for row in connection.execute(
                    "SELECT resource_class FROM resource_lease_resources "
                    "WHERE lease_id=? ORDER BY resource_class",
                    (queue["lease_id"],),
                ).fetchall()
            )
            if (
                resources != required_resources
                or str(queue["required_resources_json"]) != required_resources_json
            ):
                raise ResourceAdmissionBlocked(
                    "Resource launch request differs from the admitted immutable Resource set",
                    data={
                        "attempt_id": attempt_id,
                        "requested_resources": list(required_resources),
                        "lease_resources": list(resources),
                    },
                )
            if (
                queue["lease_state"] != "starting"
                or queue["launch_authorization_state"] != "AVAILABLE"
            ):
                raise ResourceAdmissionBlocked(
                    "Resource launch authorization is unavailable or already consumed",
                    data={
                        "attempt_id": attempt_id,
                        "lease_state": queue["lease_state"],
                        "launch_authorization_state": queue[
                            "launch_authorization_state"
                        ],
                    },
                )
            consumed = connection.execute(
                "UPDATE resource_leases SET launch_authorization_state='CONSUMED', "
                "launch_required_resources_json=?, "
                "launch_required_resources_sha256=?, "
                "launch_authorized_at=?, updated_at=? "
                "WHERE lease_id=? AND state='starting' "
                "AND launch_authorization_state='AVAILABLE'",
                (
                    required_resources_json,
                    required_resources_sha256,
                    updated_at,
                    updated_at,
                    queue["lease_id"],
                ),
            )
            if consumed.rowcount != 1:
                raise ResourceAdmissionBlocked(
                    "Resource launch authorization compare-and-set failed",
                    data={"attempt_id": attempt_id},
                )
            result = self._resource_status_row(
                connection, str(queue["task_id"]), attempt_id
            )
            if result is None:
                raise ControlStoreUnavailable("Resource status disappeared during launch")
            return result

    def confirm_resource_launch(
        self,
        *,
        attempt_id: str,
        claim_generation: int,
        launch_token: str,
        required_resources_sha256: str,
        launch_execution_identity_json: str | None,
        launch_execution_identity_sha256: str | None,
        updated_at: str,
    ) -> tuple[sqlite3.Row, bool]:
        with self._immediate() as connection:
            lease = connection.execute(
                "SELECT l.*, q.state AS queue_state FROM resource_leases l "
                "JOIN resource_queue_entries q ON q.queue_id=l.queue_id "
                "WHERE l.attempt_id=?",
                (attempt_id,),
            ).fetchone()
            if lease is None:
                raise ControlStoreUnavailable(
                    "Resource Lease disappeared during launch completion"
                )
            if claim_generation != int(lease["claim_generation"]):
                raise KernelConflict(
                    "Resource launch completion Lease generation compare-and-set failed"
                )
            if lease["state"] == "unknown":
                unknown_events = connection.execute(
                    "SELECT queue_id FROM resource_control_events "
                    "WHERE event_kind='lease_unknown' AND lease_id=? "
                    "ORDER BY event_seq",
                    (lease["lease_id"],),
                ).fetchall()
                if (
                    lease["queue_state"] != "ADMITTED"
                    or lease["launch_authorization_state"] != "CONSUMED"
                    or lease["launch_token"] != launch_token
                    or lease["launch_required_resources_sha256"]
                    != required_resources_sha256
                    or len(unknown_events) != 1
                    or unknown_events[0]["queue_id"] != lease["queue_id"]
                ):
                    raise KernelConflict(
                        "unknown Resource launch completion fencing compare-and-set failed"
                    )
                completed_unknown = connection.execute(
                    "UPDATE resource_leases SET "
                    "launch_authorization_state='COMPLETED', launch_completed_at=?, "
                    "launch_execution_identity_json=?, "
                    "launch_execution_identity_sha256=?, updated_at=? "
                    "WHERE lease_id=? AND attempt_id=? AND claim_generation=? "
                    "AND launch_token=? AND launch_required_resources_sha256=? "
                    "AND state='unknown' AND launch_authorization_state='CONSUMED'",
                    (
                        updated_at,
                        launch_execution_identity_json,
                        launch_execution_identity_sha256,
                        updated_at,
                        lease["lease_id"],
                        attempt_id,
                        claim_generation,
                        launch_token,
                        required_resources_sha256,
                    ),
                )
                if completed_unknown.rowcount != 1:
                    raise KernelConflict(
                        "unknown Resource launch completion compare-and-set failed"
                    )
                result = self._resource_status_row(
                    connection, str(lease["task_id"]), attempt_id
                )
                if result is None:
                    raise ControlStoreUnavailable(
                        "Resource status disappeared during unknown launch completion"
                    )
                return result, False
            claim = connection.execute(
                "SELECT attempt_id, claim_generation, state FROM task_claims "
                "WHERE task_id=?",
                (lease["task_id"],),
            ).fetchone()
            current_claim_matches = (
                claim is not None
                and claim["state"] == "ACTIVE"
                and claim["attempt_id"] == attempt_id
                and int(claim["claim_generation"]) == claim_generation
            )
            if not current_claim_matches:
                self._transition_resource_launch_unknown(
                    connection,
                    lease=lease,
                    launch_token=launch_token,
                    required_resources_sha256=required_resources_sha256,
                    failure_stage="claim_generation_fence",
                    updated_at=updated_at,
                    completion_observed=True,
                    launch_execution_identity_json=launch_execution_identity_json,
                    launch_execution_identity_sha256=launch_execution_identity_sha256,
                )
                result = self._resource_status_row(
                    connection, str(lease["task_id"]), attempt_id
                )
                if result is None:
                    raise ControlStoreUnavailable(
                        "Resource status disappeared during fenced launch completion"
                    )
                return result, False
            completed = connection.execute(
                "UPDATE resource_leases SET state='active', "
                "launch_authorization_state='COMPLETED', launch_completed_at=?, "
                "launch_execution_identity_json=?, "
                "launch_execution_identity_sha256=?, updated_at=? "
                "WHERE attempt_id=? AND launch_token=? "
                "AND launch_required_resources_sha256=? AND state='starting' "
                "AND launch_authorization_state='CONSUMED'",
                (
                    updated_at,
                    launch_execution_identity_json,
                    launch_execution_identity_sha256,
                    updated_at,
                    attempt_id,
                    launch_token,
                    required_resources_sha256,
                ),
            )
            if completed.rowcount != 1:
                raise ControlStoreUnavailable(
                    "Resource launch completion authority compare-and-set failed"
                )
            result = self._resource_status_row(
                connection, str(lease["task_id"]), attempt_id
            )
            if result is None:
                raise ControlStoreUnavailable(
                    "Resource status disappeared during launch completion"
                )
            return result, True

    def _transition_resource_launch_unknown(
        self,
        connection: sqlite3.Connection,
        *,
        lease: sqlite3.Row,
        launch_token: str,
        required_resources_sha256: str,
        failure_stage: str,
        updated_at: str,
        completion_observed: bool,
        launch_execution_identity_json: str | None,
        launch_execution_identity_sha256: str | None,
    ) -> None:
        payload_json = canonical_json_bytes(
            {
                "cause": "launch_outcome_unconfirmed",
                "attempt_id": str(lease["attempt_id"]),
                "claim_generation": int(lease["claim_generation"]),
                "failure_stage": failure_stage,
            }
        ).decode("utf-8")
        expected_authorization = "COMPLETED" if completion_observed else "CONSUMED"
        identity_matches = (
            lease["queue_state"] == "ADMITTED"
            and lease["launch_token"] == launch_token
            and lease["launch_required_resources_sha256"]
            == required_resources_sha256
        )
        existing_events = connection.execute(
            "SELECT * FROM resource_control_events "
            "WHERE event_kind='lease_unknown' AND lease_id=? "
            "ORDER BY event_seq",
            (lease["lease_id"],),
        ).fetchall()
        if lease["state"] == "unknown":
            if (
                identity_matches
                and lease["launch_authorization_state"] == expected_authorization
                and lease["launch_execution_identity_json"]
                == launch_execution_identity_json
                and lease["launch_execution_identity_sha256"]
                == launch_execution_identity_sha256
                and len(existing_events) == 1
                and existing_events[0]["queue_id"] == lease["queue_id"]
                and existing_events[0]["payload_json"] == payload_json
            ):
                return
            raise KernelConflict(
                "Resource launch unknown replay conflicts with durable authority"
            )
        if (
            not identity_matches
            or lease["state"] != "starting"
            or lease["launch_authorization_state"] != "CONSUMED"
            or existing_events
        ):
            raise KernelConflict(
                "Resource launch unknown fencing compare-and-set failed"
            )
        if completion_observed:
            changed = connection.execute(
                "UPDATE resource_leases SET state='unknown', "
                "launch_authorization_state='COMPLETED', launch_completed_at=?, "
                "launch_execution_identity_json=?, "
                "launch_execution_identity_sha256=?, updated_at=? "
                "WHERE lease_id=? AND attempt_id=? AND launch_token=? "
                "AND launch_required_resources_sha256=? "
                "AND state='starting' AND launch_authorization_state='CONSUMED'",
                (
                    updated_at,
                    launch_execution_identity_json,
                    launch_execution_identity_sha256,
                    updated_at,
                    lease["lease_id"],
                    lease["attempt_id"],
                    launch_token,
                    required_resources_sha256,
                ),
            )
        else:
            changed = connection.execute(
                "UPDATE resource_leases SET state='unknown', updated_at=? "
                "WHERE lease_id=? AND attempt_id=? AND launch_token=? "
                "AND launch_required_resources_sha256=? "
                "AND state='starting' AND launch_authorization_state='CONSUMED'",
                (
                    updated_at,
                    lease["lease_id"],
                    lease["attempt_id"],
                    launch_token,
                    required_resources_sha256,
                ),
            )
        if changed.rowcount != 1:
            raise KernelConflict("Resource launch unknown compare-and-set failed")
        configuration = self._active_resource_configuration_row(connection)
        event_seq = self._next_resource_sequence(connection, "event")
        connection.execute(
            "INSERT INTO resource_control_events("
            "event_seq, event_kind, queue_id, lease_id, configuration_id, "
            "configuration_version, configuration_sha256, payload_json) "
            "VALUES (?, 'lease_unknown', ?, ?, ?, ?, ?, ?)",
            (
                event_seq,
                lease["queue_id"],
                lease["lease_id"],
                configuration["configuration_id"],
                configuration["configuration_version"],
                configuration["configuration_sha256"],
                payload_json,
            ),
        )

    def mark_resource_launch_unknown(
        self,
        *,
        attempt_id: str,
        claim_generation: int,
        launch_token: str,
        required_resources_sha256: str,
        failure_stage: str,
        updated_at: str,
    ) -> None:
        if failure_stage not in RESOURCE_LAUNCH_FAILURE_STAGES:
            raise ContractError(
                "Resource launch unknown failure stage is unsupported"
            )
        with self._immediate() as connection:
            lease = connection.execute(
                "SELECT l.*, q.state AS queue_state FROM resource_leases l "
                "JOIN resource_queue_entries q ON q.queue_id=l.queue_id "
                "WHERE l.attempt_id=?",
                (attempt_id,),
            ).fetchone()
            if lease is None:
                raise ControlStoreUnavailable(
                    "Resource launch unknown transition has no Lease authority"
                )
            if claim_generation != int(lease["claim_generation"]):
                raise KernelConflict(
                    "Resource launch unknown Lease generation compare-and-set failed"
                )
            self._transition_resource_launch_unknown(
                connection,
                lease=lease,
                launch_token=launch_token,
                required_resources_sha256=required_resources_sha256,
                failure_stage=failure_stage,
                updated_at=updated_at,
                completion_observed=False,
                launch_execution_identity_json=None,
                launch_execution_identity_sha256=None,
            )

    def release_resource_lease(
        self,
        *,
        attempt_id: str,
        claim_generation: int,
        launch_token: str,
        terminal_evidence_json: str,
        terminal_evidence_sha256: str,
        released_at: str,
        resource_scheduler: Callable[[sqlite3.Connection, str], None],
    ) -> sqlite3.Row:
        with self._immediate() as connection:
            lease = connection.execute(
                "SELECT l.*, q.state AS queue_state FROM resource_leases l "
                "JOIN resource_queue_entries q ON q.queue_id=l.queue_id "
                "WHERE l.attempt_id=?",
                (attempt_id,),
            ).fetchone()
            if lease is None:
                raise ResourceAdmissionBlocked(
                    "Resource Lease release has no admitted authority",
                    data={"attempt_id": attempt_id},
                )
            self._validate_resource_terminal_evidence(
                lease,
                terminal_evidence_json,
                terminal_evidence_sha256,
                allowed_evidence_classes={
                    "provider_terminal_result",
                    "local_process_terminated",
                },
            )
            claim = connection.execute(
                "SELECT attempt_id, claim_generation, state FROM task_claims "
                "WHERE task_id=?",
                (lease["task_id"],),
            ).fetchone()
            if (
                claim is None
                or claim["state"] != "ACTIVE"
                or claim["attempt_id"] != attempt_id
                or int(claim["claim_generation"]) != claim_generation
                or int(lease["claim_generation"]) != claim_generation
            ):
                raise KernelConflict(
                    "Resource Lease release fencing compare-and-set failed"
                )
            if (
                lease["queue_state"] != "ADMITTED"
                or lease["state"] != "active"
                or lease["launch_authorization_state"] != "COMPLETED"
                or lease["launch_token"] != launch_token
            ):
                raise ResourceAdmissionBlocked(
                    "Resource Lease is not eligible for evidence-bearing release",
                    data={
                        "attempt_id": attempt_id,
                        "lease_state": lease["state"],
                    },
                )
            released = connection.execute(
                "UPDATE resource_leases SET state='released', "
                "terminal_evidence_json=?, terminal_evidence_sha256=?, updated_at=? "
                "WHERE lease_id=? AND state='active' "
                "AND launch_authorization_state='COMPLETED'",
                (
                    terminal_evidence_json,
                    terminal_evidence_sha256,
                    released_at,
                    lease["lease_id"],
                ),
            )
            if released.rowcount != 1:
                raise KernelConflict(
                    "Resource Lease release compare-and-set failed"
                )
            event_seq = self._next_resource_sequence(connection, "event")
            connection.execute(
                "INSERT INTO resource_control_events("
                "event_seq, event_kind, queue_id, lease_id, configuration_id, "
                "configuration_version, configuration_sha256, payload_json) "
                "VALUES (?, 'released', ?, ?, ?, ?, ?, ?)",
                (
                    event_seq,
                    lease["queue_id"],
                    lease["lease_id"],
                    lease["admission_configuration_id"],
                    lease["admission_configuration_version"],
                    lease["admission_configuration_sha256"],
                    terminal_evidence_json,
                ),
            )
            resource_scheduler(connection, released_at)
            result = self._resource_status_row(
                connection, str(lease["task_id"]), attempt_id
            )
            if result is None:
                raise ControlStoreUnavailable(
                    "Resource status disappeared during Lease release"
                )
            return result

    def reconcile_resource_leases_for_lost_sessions(
        self,
        *,
        current_coordinator_session_id: str,
        lost_coordinator_session_ids: tuple[str, ...],
        audit_payloads: dict[str, str],
        reconciled_at: str,
    ) -> list[str]:
        if current_coordinator_session_id in lost_coordinator_session_ids:
            raise ContractError(
                "current coordinator session cannot be declared lost"
            )
        with self._immediate() as connection:
            if not lost_coordinator_session_ids:
                return []
            placeholders = ",".join("?" for _ in lost_coordinator_session_ids)
            unresolved = connection.execute(
                "SELECT * FROM resource_leases WHERE state IN ('starting','active') "
                f"AND coordinator_session_id IN ({placeholders}) ORDER BY admitted_seq",
                lost_coordinator_session_ids,
            ).fetchall()
            configuration = self._active_resource_configuration_row(connection)
            transitioned: list[str] = []
            for lease in unresolved:
                payload_json = audit_payloads.get(str(lease["lease_id"]))
                if payload_json is None:
                    raise KernelConflict(
                        "Resource reconcile audit snapshot changed before commit"
                    )
                changed = connection.execute(
                    "UPDATE resource_leases SET state='unknown', updated_at=? "
                    "WHERE lease_id=? AND state IN ('starting','active')",
                    (reconciled_at, lease["lease_id"]),
                )
                if changed.rowcount != 1:
                    raise ControlStoreUnavailable(
                        "Resource Lease reconcile compare-and-set failed"
                    )
                transitioned.append(str(lease["lease_id"]))
                event_seq = self._next_resource_sequence(connection, "event")
                connection.execute(
                    "INSERT INTO resource_control_events("
                    "event_seq, event_kind, queue_id, lease_id, configuration_id, "
                    "configuration_version, configuration_sha256, payload_json) "
                    "VALUES (?, 'lease_unknown', ?, ?, ?, ?, ?, ?)",
                    (
                        event_seq,
                        lease["queue_id"],
                        lease["lease_id"],
                        configuration["configuration_id"],
                        configuration["configuration_version"],
                        configuration["configuration_sha256"],
                        payload_json,
                    ),
                )
            return transitioned

    def resource_leases_for_coordinator_sessions(
        self, coordinator_session_ids: tuple[str, ...]
    ) -> list[sqlite3.Row]:
        if not coordinator_session_ids:
            return []
        connection = self._connect()
        try:
            placeholders = ",".join("?" for _ in coordinator_session_ids)
            return connection.execute(
                "SELECT * FROM resource_leases WHERE state IN ('starting','active') "
                f"AND coordinator_session_id IN ({placeholders}) ORDER BY admitted_seq",
                coordinator_session_ids,
            ).fetchall()
        finally:
            connection.close()

    def unknown_resource_leases(self) -> list[sqlite3.Row]:
        connection = self._connect()
        try:
            return connection.execute(
                "SELECT * FROM resource_leases WHERE state='unknown' "
                "ORDER BY admitted_seq"
            ).fetchall()
        finally:
            connection.close()

    def nonterminal_resource_lease_coordinator_session_ids(self) -> tuple[str, ...]:
        connection = self._connect()
        try:
            return tuple(
                str(row[0])
                for row in connection.execute(
                    "SELECT DISTINCT coordinator_session_id FROM resource_leases "
                    "WHERE state IN ('starting','active') "
                    "ORDER BY coordinator_session_id"
                ).fetchall()
            )
        finally:
            connection.close()

    def resolve_unknown_resource_lease(
        self,
        *,
        lease_id: str,
        attempt_id: str,
        expected_claim_generation: int,
        resolution_evidence_json: str,
        resolution_evidence_sha256: str,
        resolved_at: str,
        resource_scheduler: Callable[[sqlite3.Connection, str], None],
    ) -> sqlite3.Row:
        with self._immediate() as connection:
            lease = connection.execute(
                "SELECT * FROM resource_leases WHERE lease_id=? AND attempt_id=?",
                (lease_id, attempt_id),
            ).fetchone()
            if lease is None:
                raise ResourceAdmissionBlocked(
                    "Resource resolution has no Lease authority",
                    data={"attempt_id": attempt_id},
                )
            if int(lease["claim_generation"]) != expected_claim_generation:
                raise KernelConflict(
                    "Resource resolution Lease generation compare-and-set failed"
                )
            if lease["state"] == "resolved":
                if (
                    lease["terminal_evidence_json"] == resolution_evidence_json
                    and lease["terminal_evidence_sha256"]
                    == resolution_evidence_sha256
                ):
                    result = self._resource_status_row(
                        connection, str(lease["task_id"]), attempt_id
                    )
                    if result is None:
                        raise ControlStoreUnavailable(
                            "resolved Resource status is absent"
                        )
                    return result
                raise KernelConflict(
                    "Resource resolution replay conflicts with terminal evidence"
                )
            if lease["state"] != "unknown":
                raise ResourceAdmissionBlocked(
                    "Resource Lease is not unknown and cannot be resolved",
                    data={"attempt_id": attempt_id, "lease_state": lease["state"]},
                )
            changed = connection.execute(
                "UPDATE resource_leases SET state='resolved', "
                "terminal_evidence_json=?, terminal_evidence_sha256=?, updated_at=? "
                "WHERE lease_id=? AND state='unknown'",
                (
                    resolution_evidence_json,
                    resolution_evidence_sha256,
                    resolved_at,
                    lease["lease_id"],
                ),
            )
            if changed.rowcount != 1:
                raise KernelConflict(
                    "Resource resolution compare-and-set failed"
                )
            event_seq = self._next_resource_sequence(connection, "event")
            connection.execute(
                "INSERT INTO resource_control_events("
                "event_seq, event_kind, queue_id, lease_id, configuration_id, "
                "configuration_version, configuration_sha256, payload_json) "
                "VALUES (?, 'lease_resolved', ?, ?, ?, ?, ?, ?)",
                (
                    event_seq,
                    lease["queue_id"],
                    lease["lease_id"],
                    lease["admission_configuration_id"],
                    lease["admission_configuration_version"],
                    lease["admission_configuration_sha256"],
                    resolution_evidence_json,
                ),
            )
            resource_scheduler(connection, resolved_at)
            result = self._resource_status_row(
                connection, str(lease["task_id"]), attempt_id
            )
            if result is None:
                raise ControlStoreUnavailable(
                    "Resource status disappeared during resolution"
                )
            return result

    def resource_scheduler_snapshot(self) -> dict[str, object]:
        connection = self._connect()
        try:
            connection.execute("BEGIN")
            cursors = connection.execute(
                "SELECT level, scope_id, cursor_value, scheduling_seq "
                "FROM resource_fairness_cursors ORDER BY level, scope_id"
            ).fetchall()
            sequences = connection.execute(
                "SELECT sequence_name, value FROM resource_sequences "
                "ORDER BY sequence_name"
            ).fetchall()
            reservations = connection.execute(
                "SELECT queue_id, task_id, attempt_id, reservation_state, "
                "reservation_seq, required_resources_json FROM resource_queue_entries "
                "WHERE reservation_state != 'NONE' ORDER BY reservation_seq"
            ).fetchall()
            events = connection.execute(
                "SELECT * FROM resource_control_events ORDER BY event_seq"
            ).fetchall()
            return {
                "cursors": [dict(row) for row in cursors],
                "sequences": {
                    str(row["sequence_name"]): int(row["value"])
                    for row in sequences
                },
                "reservations": [dict(row) for row in reservations],
                "events": [dict(row) for row in events],
            }
        finally:
            if connection.in_transaction:
                connection.rollback()
            connection.close()

    def resource_capacity_snapshot(self) -> dict[str, object]:
        connection = self._connect()
        try:
            connection.execute("BEGIN")
            configuration = self._active_resource_configuration_row(connection)
            capacities = self._resource_capacities(configuration)
            resources: dict[str, dict[str, object]] = {}
            for resource_class in sorted(capacities):
                capacity = capacities[resource_class]
                usage = self._resource_usage(connection, resource_class)
                if usage > capacity:
                    state = "overcommitted"
                elif usage == capacity:
                    state = "full"
                else:
                    state = "available"
                resources[resource_class] = {
                    "capacity": capacity,
                    "usage": usage,
                    "available": max(0, capacity - usage),
                    "state": state,
                }
            return {
                "configuration_id": str(configuration["configuration_id"]),
                "configuration_version": int(
                    configuration["configuration_version"]
                ),
                "configuration_sha256": str(
                    configuration["configuration_sha256"]
                ),
                "resources": resources,
            }
        finally:
            if connection.in_transaction:
                connection.rollback()
            connection.close()

    def set_resource_circuit_breaker(
        self,
        *,
        breaker_key: str,
        resource_class: str,
        platform: str | None,
        state: str,
        reason: str,
        payload_json: str,
        updated_at: str,
        resource_scheduler: Callable[[sqlite3.Connection, str], None],
    ) -> sqlite3.Row:
        with self._immediate() as connection:
            configuration = self._active_resource_configuration_row(connection)
            capacities = self._resource_capacities(configuration)
            if resource_class not in capacities:
                raise ContractError(
                    "Resource Circuit Breaker names an unknown Resource Class"
                )
            updated_seq = self._next_resource_sequence(connection, "breaker")
            connection.execute(
                "INSERT INTO resource_circuit_breakers("
                "breaker_key, resource_class, platform, state, reason, updated_seq) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(breaker_key) DO UPDATE SET "
                "resource_class=excluded.resource_class, platform=excluded.platform, "
                "state=excluded.state, reason=excluded.reason, "
                "updated_seq=excluded.updated_seq",
                (
                    breaker_key,
                    resource_class,
                    platform,
                    state,
                    reason,
                    updated_seq,
                ),
            )
            event_seq = self._next_resource_sequence(connection, "event")
            connection.execute(
                "INSERT INTO resource_control_events("
                "event_seq, event_kind, queue_id, lease_id, configuration_id, "
                "configuration_version, configuration_sha256, payload_json) "
                "VALUES (?, ?, NULL, NULL, ?, ?, ?, ?)",
                (
                    event_seq,
                    "breaker_opened" if state == "OPEN" else "breaker_closed",
                    configuration["configuration_id"],
                    configuration["configuration_version"],
                    configuration["configuration_sha256"],
                    payload_json,
                ),
            )
            if state == "CLOSED":
                resource_scheduler(connection, updated_at)
            return connection.execute(
                "SELECT * FROM resource_circuit_breakers WHERE breaker_key=?",
                (breaker_key,),
            ).fetchone()

    def resource_circuit_breaker_snapshot(self) -> list[dict[str, object]]:
        connection = self._connect()
        try:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM resource_circuit_breakers ORDER BY breaker_key"
                ).fetchall()
            ]
        finally:
            connection.close()

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
        required_resources: tuple[str, ...] | None = None,
        fairness_group_id: str | None = None,
        batch_id: str | None = None,
        resource_scheduler: Callable[[sqlite3.Connection, str], None] | None = None,
        fault_point: str | None = None,
    ) -> sqlite3.Row:
        write_set_json = canonical_json_bytes(list(write_set)).decode("utf-8").strip()
        claim_authority = self._claim_authority_from_current_envelope(
            authority_id=authority_id,
            task_id=task_id,
            envelope_sha256=envelope_sha256,
            write_set=write_set,
        )
        claim_authority_canonical = canonical_json_bytes(claim_authority)
        claim_authority_sha256 = hashlib.sha256(
            claim_authority_canonical
        ).hexdigest()
        generation = 1
        attempt_id = hashlib.sha256(
            f"task-attempt\0{task_id}\0{generation}".encode("utf-8")
        ).hexdigest()[:24]
        resource_request = None
        queue_id = None
        lease_candidate_id = None
        launch_token = None
        request_binding_sha256 = None
        if required_resources is not None:
            if not required_resources or tuple(sorted(required_resources)) != required_resources:
                raise ContractError(
                    "Resource Request must be non-empty, unique, and stably sorted"
                )
            if len(required_resources) != len(set(required_resources)):
                raise ContractError("Resource Request repeats a Resource Class")
            resource_request = canonical_json_bytes(list(required_resources)).decode(
                "utf-8"
            )
            queue_id = hashlib.sha256(
                f"resource-queue\0{task_id}\0{attempt_id}".encode("utf-8")
            ).hexdigest()[:32]
            lease_candidate_id = hashlib.sha256(
                f"resource-lease\0{queue_id}".encode("utf-8")
            ).hexdigest()[:32]
            launch_token = hashlib.sha256(
                f"resource-launch\0{queue_id}\0{claim_authority_sha256}".encode(
                    "utf-8"
                )
            ).hexdigest()
            request_binding_sha256 = self._resource_request_binding_sha256(
                task_id=task_id,
                attempt_id=attempt_id,
                claim_generation=generation,
                run_id=authority_id,
                envelope_sha256=envelope_sha256,
                required_resources=required_resources,
                fairness_group_id=fairness_group_id or authority_id,
                batch_id=batch_id,
            )
        attempt_record = self._task_attempt_record(
            task_id=task_id,
            attempt_id=attempt_id,
            claim_generation=generation,
            envelope_sha256=envelope_sha256,
            attempt_path=attempt_path,
            coordinator_session_id=coordinator_session_id,
            worker_id=worker_id,
            claimed_at=claimed_at,
        )
        self.contracts.validate("task-attempt", attempt_record)
        attempt_record_json = canonical_json_bytes(attempt_record).decode("utf-8")
        attempt_record_sha = hashlib.sha256(
            attempt_record_json.encode("utf-8")
        ).hexdigest()

        def plan_claim(connection: sqlite3.Connection) -> str:
            if self._current_run_record_sha(connection, authority_id) is None:
                raise KernelConflict("Task Claim authority has no committed Run Record")
            existing = connection.execute(
                "SELECT * FROM task_claims WHERE task_id=?", (task_id,)
            ).fetchone()
            if existing is not None:
                if (
                    existing["state"] == "ACTIVE"
                    and existing["authority_id"] == authority_id
                    and existing["envelope_sha256"] == envelope_sha256
                    and existing["write_set_json"] == write_set_json
                    and existing["coordinator_session_id"] == coordinator_session_id
                    and existing["worker_id"] == worker_id
                ):
                    if resource_request is not None:
                        queued = connection.execute(
                            "SELECT required_resources_json, fairness_group_id, batch_id, "
                            "request_binding_sha256 "
                            "FROM resource_queue_entries WHERE task_id=? AND attempt_id=?",
                            (task_id, attempt_id),
                        ).fetchone()
                        if (
                            queued is None
                            or queued["required_resources_json"] != resource_request
                            or queued["fairness_group_id"]
                            != (fairness_group_id or authority_id)
                            or queued["batch_id"] != batch_id
                            or queued["request_binding_sha256"]
                            != request_binding_sha256
                        ):
                            raise KernelConflict(
                                "conflicting Resource Request replay for Task Claim"
                            )
                    return "REPLAY"
                raise KernelConflict("logical task already has a current or terminal Claim")
            active = connection.execute(
                "SELECT task_id, write_set_json FROM task_claims "
                "WHERE authority_kind='kernel_run' AND authority_id=? AND state='ACTIVE'",
                (authority_id,),
            ).fetchall()
            for row in active:
                if self._write_sets_overlap(str(row["write_set_json"]), write_set):
                    raise KernelConflict(
                        "Task Claim write set overlaps an active Claim",
                        data={"conflicting_task_id": str(row["task_id"])},
                    )
            if resource_request is not None:
                configuration = self._active_resource_configuration_row(connection)
                capacities = self._resource_capacities(configuration)
                if any(resource not in capacities for resource in required_resources or ()):
                    raise ContractError("Resource Request names an unknown Resource Class")
                if any(capacities[resource] < 1 for resource in required_resources or ()):
                    raise ContractError(
                        "Resource Request exceeds configured Resource Class capacity"
                    )
            return "INSERT"

        with self._planned_immediate(plan_claim) as (connection, plan):
            if plan == "REPLAY":
                if resource_request is not None:
                    if resource_scheduler is None:
                        raise ControlStoreUnavailable(
                            "Resource Request requires a Resource Admission scheduler"
                        )
                    resource_scheduler(connection, claimed_at)
                return connection.execute(
                    "SELECT c.*, a.attempt_path, a.state AS attempt_state, "
                    "a.completion_sha256, ca.completion_record_json "
                    "FROM task_claims c JOIN task_attempts a "
                    "ON a.attempt_id=c.attempt_id "
                    "LEFT JOIN task_completion_authorities ca "
                    "ON ca.attempt_id=a.attempt_id WHERE c.task_id=?",
                    (task_id,),
                ).fetchone()
            try:
                connection.execute(
                    "INSERT INTO task_claims(task_id, authority_kind, authority_id, "
                    "envelope_sha256, write_set_json, state, claim_generation, attempt_id, "
                    "coordinator_session_id, worker_id, reclaim_reason, updated_at) "
                    "VALUES (?, 'kernel_run', ?, ?, ?, 'ACTIVE', ?, ?, ?, ?, NULL, ?)",
                    (
                        task_id, authority_id, envelope_sha256, write_set_json,
                        generation, attempt_id, coordinator_session_id, worker_id, claimed_at,
                    ),
                )
                self._insert_task_claim_authority(
                    connection,
                    claim_authority,
                    canonical=claim_authority_canonical,
                    record_sha256=claim_authority_sha256,
                )
                connection.execute(
                    "INSERT INTO task_attempts(attempt_id, task_id, claim_generation, "
                    "attempt_path, state, completion_sha256) "
                    "VALUES (?, ?, ?, ?, 'CLAIMED', NULL)",
                    (attempt_id, task_id, generation, attempt_path),
                )
                connection.execute(
                    "INSERT INTO task_attempt_authorities("
                    "attempt_id, attempt_record_json, attempt_record_sha256) "
                    "VALUES (?, ?, ?)",
                    (attempt_id, attempt_record_json, attempt_record_sha),
                )
                if resource_request is not None:
                    if fault_point == "after_claim_before_enqueue":
                        raise ResourceAdmissionFault(fault_point)
                    if (
                        queue_id is None
                        or lease_candidate_id is None
                        or launch_token is None
                        or request_binding_sha256 is None
                    ):
                        raise ControlStoreUnavailable(
                            "Resource Request identities were not prepared"
                        )
                    configuration = self._active_resource_configuration_row(connection)
                    enqueue_seq = self._next_resource_sequence(connection, "enqueue")
                    connection.execute(
                        "INSERT INTO resource_queue_entries("
                        "queue_id, task_id, attempt_id, run_id, fairness_group_id, "
                        "batch_id, enqueue_seq, required_resources_json, claim_generation, "
                        "enqueue_configuration_id, enqueue_configuration_version, "
                        "enqueue_configuration_sha256, request_binding_sha256, "
                        "state, bypass_count, "
                        "reservation_state, reservation_seq, lease_candidate_id, "
                        "launch_token, lease_id, admitted_seq) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'QUEUED', 0, "
                        "'NONE', NULL, ?, ?, NULL, NULL)",
                        (
                            queue_id,
                            task_id,
                            attempt_id,
                            authority_id,
                            fairness_group_id or authority_id,
                            batch_id,
                            enqueue_seq,
                            resource_request,
                            generation,
                            configuration["configuration_id"],
                            configuration["configuration_version"],
                            configuration["configuration_sha256"],
                            request_binding_sha256,
                            lease_candidate_id,
                            launch_token,
                        ),
                    )
                    event_seq = self._next_resource_sequence(connection, "event")
                    connection.execute(
                        "INSERT INTO resource_control_events("
                        "event_seq, event_kind, queue_id, lease_id, configuration_id, "
                        "configuration_version, configuration_sha256, payload_json) "
                        "VALUES (?, 'enqueued', ?, NULL, ?, ?, ?, ?)",
                        (
                            event_seq,
                            queue_id,
                            configuration["configuration_id"],
                            configuration["configuration_version"],
                            configuration["configuration_sha256"],
                            canonical_json_bytes({}).decode("utf-8"),
                        ),
                    )
                    if fault_point == "after_claim_enqueue_before_schedule":
                        raise ResourceAdmissionFault(fault_point)
                    if resource_scheduler is None:
                        raise ControlStoreUnavailable(
                            "Resource Request requires a Resource Admission scheduler"
                        )
                    resource_scheduler(connection, claimed_at)
                    if fault_point == "after_claim_schedule_before_commit":
                        raise ResourceAdmissionFault(fault_point)
            except sqlite3.IntegrityError as exc:
                raise KernelConflict("Task Claim compare-and-set failed") from exc
            return connection.execute(
                "SELECT c.*, a.attempt_path, a.state AS attempt_state, "
                "a.completion_sha256, ca.completion_record_json "
                "FROM task_claims c JOIN task_attempts a "
                "ON a.attempt_id=c.attempt_id "
                "LEFT JOIN task_completion_authorities ca "
                "ON ca.attempt_id=a.attempt_id WHERE c.task_id=?",
                (task_id,),
            ).fetchone()

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
        required_resources: tuple[str, ...] | None = None,
        fairness_group_id: str | None = None,
        batch_id: str | None = None,
        resource_scheduler: Callable[[sqlite3.Connection, str], None] | None = None,
        fault_point: str | None = None,
    ) -> sqlite3.Row:
        if not reason.strip():
            raise ContractError("Task reclaim requires a recovery reason")
        generation = expected_claim_generation + 1
        attempt_id = hashlib.sha256(
            f"task-attempt\0{task_id}\0{generation}".encode("utf-8")
        ).hexdigest()[:24]
        resource_request = None
        queue_id = None
        lease_candidate_id = None
        launch_token = None
        request_binding_sha256 = None
        if required_resources is not None:
            if (
                not required_resources
                or tuple(sorted(required_resources)) != required_resources
                or len(required_resources) != len(set(required_resources))
            ):
                raise ContractError(
                    "Resource Request must be non-empty, unique, and stably sorted"
                )
            resource_request = canonical_json_bytes(list(required_resources)).decode(
                "utf-8"
            )
            queue_id = hashlib.sha256(
                f"resource-queue\0{task_id}\0{attempt_id}".encode("utf-8")
            ).hexdigest()[:32]
            lease_candidate_id = hashlib.sha256(
                f"resource-lease\0{queue_id}".encode("utf-8")
            ).hexdigest()[:32]
        planned_claim = self.task_claim_for_task(task_id)
        if (
            resource_request is not None
            and planned_claim is not None
            and planned_claim["authority_id"] == authority_id
        ):
            request_binding_sha256 = self._resource_request_binding_sha256(
                task_id=task_id,
                attempt_id=attempt_id,
                claim_generation=generation,
                run_id=authority_id,
                envelope_sha256=str(planned_claim["envelope_sha256"]),
                required_resources=required_resources or (),
                fairness_group_id=fairness_group_id or authority_id,
                batch_id=batch_id,
            )
        attempt_record_json: str | None = None
        attempt_record_sha: str | None = None
        transition_record: dict | None = None
        transition_canonical: str | None = None
        transition_id: str | None = None
        if (
            planned_claim is not None
            and planned_claim["authority_id"] == authority_id
            and planned_claim["attempt_id"] == expected_attempt_id
            and int(planned_claim["claim_generation"])
            == expected_claim_generation
        ):
            attempt_record = self._task_attempt_record(
                task_id=task_id,
                attempt_id=attempt_id,
                claim_generation=generation,
                envelope_sha256=str(planned_claim["envelope_sha256"]),
                attempt_path=attempt_path,
                coordinator_session_id=coordinator_session_id,
                worker_id=worker_id,
                claimed_at=reclaimed_at,
            )
            self.contracts.validate("task-attempt", attempt_record)
            attempt_record_json = canonical_json_bytes(attempt_record).decode("utf-8")
            attempt_record_sha = hashlib.sha256(
                attempt_record_json.encode("utf-8")
            ).hexdigest()
            transition_record = self._task_reclaim_transition_record(
                authority_id=authority_id,
                task_id=task_id,
                prior_attempt_id=expected_attempt_id,
                replacement_attempt_id=attempt_id,
                prior_claim_generation=expected_claim_generation,
                replacement_claim_generation=generation,
                recovery_reason=reason,
                prior_coordinator_session_id=str(
                    planned_claim["coordinator_session_id"]
                ),
                prior_worker_id=str(planned_claim["worker_id"]),
                replacement_coordinator_session_id=coordinator_session_id,
                replacement_worker_id=worker_id,
                reclaimed_at=reclaimed_at,
            )
            transition_canonical = canonical_json_bytes(transition_record).decode(
                "utf-8"
            )
            transition_id = hashlib.sha256(
                transition_canonical.encode("utf-8")
            ).hexdigest()
            launch_token = hashlib.sha256(
                f"resource-launch\0{queue_id}\0{transition_id}".encode("utf-8")
            ).hexdigest()
        with self._immediate() as connection:
            claim = connection.execute(
                "SELECT * FROM task_claims WHERE task_id=?", (task_id,)
            ).fetchone()
            if claim is None or claim["state"] != "ACTIVE":
                raise KernelConflict("Task reclaim fencing compare-and-set failed")
            if (
                claim["authority_id"] == authority_id
                and claim["attempt_id"] == attempt_id
                and int(claim["claim_generation"]) == generation
            ):
                prior = connection.execute(
                    "SELECT state FROM task_attempts WHERE attempt_id=? AND task_id=?",
                    (expected_attempt_id, task_id),
                ).fetchone()
                replacement = connection.execute(
                    "SELECT * FROM task_attempts WHERE attempt_id=? AND task_id=?",
                    (attempt_id, task_id),
                ).fetchone()
                if (
                    claim["coordinator_session_id"] == coordinator_session_id
                    and claim["worker_id"] == worker_id
                    and claim["reclaim_reason"] == reason
                    and prior is not None
                    and prior["state"] == "ABANDONED"
                    and replacement is not None
                    and replacement["state"] == "CLAIMED"
                    and replacement["attempt_path"] == attempt_path
                    and int(replacement["claim_generation"]) == generation
                ):
                    if resource_request is not None:
                        queued = connection.execute(
                            "SELECT required_resources_json, fairness_group_id, batch_id, "
                            "request_binding_sha256 "
                            "FROM resource_queue_entries WHERE task_id=? AND attempt_id=?",
                            (task_id, attempt_id),
                        ).fetchone()
                        if (
                            queued is None
                            or queued["required_resources_json"] != resource_request
                            or queued["fairness_group_id"]
                            != (fairness_group_id or authority_id)
                            or queued["batch_id"] != batch_id
                            or queued["request_binding_sha256"]
                            != request_binding_sha256
                        ):
                            raise KernelConflict(
                                "conflicting Resource Request replay for Task reclaim"
                            )
                        if resource_scheduler is None:
                            raise ControlStoreUnavailable(
                                "Resource Request requires a Resource Admission scheduler"
                            )
                        resource_scheduler(connection, reclaimed_at)
                    return connection.execute(
                        "SELECT c.*, a.attempt_path, a.state AS attempt_state, "
                        "a.completion_sha256, ca.completion_record_json "
                        "FROM task_claims c JOIN task_attempts a "
                        "ON a.attempt_id=c.attempt_id "
                        "LEFT JOIN task_completion_authorities ca "
                        "ON ca.attempt_id=a.attempt_id WHERE c.task_id=?",
                        (task_id,),
                    ).fetchone()
                raise KernelConflict("conflicting Task reclaim replay")
            if (
                claim["authority_id"] != authority_id
                or claim["attempt_id"] != expected_attempt_id
                or int(claim["claim_generation"]) != expected_claim_generation
            ):
                raise KernelConflict("Task reclaim fencing compare-and-set failed")
            active_intent = connection.execute(
                "SELECT intent_id FROM task_promotion_intents "
                "WHERE task_id=? AND state IN ('PREPARED','FILES_PUBLISHED','RECORD_COMMITTED')",
                (task_id,),
            ).fetchone()
            if active_intent is not None:
                raise KernelConflict("Task reclaim is blocked by a non-terminal promotion")
            if (
                attempt_record_json is None
                or attempt_record_sha is None
                or transition_record is None
                or transition_canonical is None
                or transition_id is None
            ):
                raise KernelConflict(
                    "Task reclaim changed while its authenticated rows were prepared"
                )
            abandoned = connection.execute(
                "UPDATE task_attempts SET state='ABANDONED' "
                "WHERE attempt_id=? AND state IN ('CLAIMED','VALIDATED_WAITING_FOR_PROMOTION')",
                (expected_attempt_id,),
            )
            if abandoned.rowcount != 1:
                raise KernelConflict("Task reclaim prior Attempt is not replaceable")
            cursor = connection.execute(
                "UPDATE task_claims SET claim_generation=?, attempt_id=?, "
                "coordinator_session_id=?, worker_id=?, reclaim_reason=?, updated_at=? "
                "WHERE task_id=? AND state='ACTIVE' AND claim_generation=? AND attempt_id=?",
                (
                    generation, attempt_id, coordinator_session_id, worker_id, reason,
                    reclaimed_at, task_id, expected_claim_generation, expected_attempt_id,
                ),
            )
            if cursor.rowcount != 1:
                raise KernelConflict("Task reclaim fencing compare-and-set failed")
            connection.execute(
                "INSERT INTO task_attempts(attempt_id, task_id, claim_generation, "
                "attempt_path, state, completion_sha256) "
                "VALUES (?, ?, ?, ?, 'CLAIMED', NULL)",
                (attempt_id, task_id, generation, attempt_path),
            )
            connection.execute(
                "INSERT INTO task_attempt_authorities("
                "attempt_id, attempt_record_json, attempt_record_sha256) "
                "VALUES (?, ?, ?)",
                (attempt_id, attempt_record_json, attempt_record_sha),
            )
            self._insert_task_reclaim_transition(
                connection,
                transition_record,
                canonical=transition_canonical,
                transition_id=transition_id,
            )
            if resource_request is not None:
                if fault_point == "after_reclaim_before_enqueue":
                    raise ResourceAdmissionFault(fault_point)
                prior_queue = connection.execute(
                    "SELECT * FROM resource_queue_entries WHERE task_id=? "
                    "AND attempt_id=?",
                    (task_id, expected_attempt_id),
                ).fetchone()
                if prior_queue is not None and prior_queue["state"] == "QUEUED":
                    invalidated = connection.execute(
                        "UPDATE resource_queue_entries SET state='INVALIDATED', "
                        "reservation_state=CASE "
                        "WHEN reservation_state IN ('ACTIVE','PENDING') "
                        "THEN 'TERMINATED' ELSE reservation_state END WHERE queue_id=? "
                        "AND state='QUEUED'",
                        (prior_queue["queue_id"],),
                    )
                    if invalidated.rowcount != 1:
                        raise KernelConflict(
                            "Task reclaim Resource Queue invalidation compare-and-set failed"
                        )
                    event_seq = self._next_resource_sequence(connection, "event")
                    connection.execute(
                        "INSERT INTO resource_control_events("
                        "event_seq, event_kind, queue_id, lease_id, configuration_id, "
                        "configuration_version, configuration_sha256, payload_json) "
                        "VALUES (?, 'invalidated_by_reclaim', ?, NULL, ?, ?, ?, ?)",
                        (
                            event_seq,
                            prior_queue["queue_id"],
                            prior_queue["enqueue_configuration_id"],
                            prior_queue["enqueue_configuration_version"],
                            prior_queue["enqueue_configuration_sha256"],
                            canonical_json_bytes({}).decode("utf-8"),
                        ),
                    )
                if (
                    queue_id is None
                    or lease_candidate_id is None
                    or launch_token is None
                    or request_binding_sha256 is None
                ):
                    raise ControlStoreUnavailable(
                        "replacement Resource Request identities were not prepared"
                    )
                configuration = self._active_resource_configuration_row(connection)
                capacities = self._resource_capacities(configuration)
                if any(resource not in capacities for resource in required_resources or ()):
                    raise ContractError("Resource Request names an unknown Resource Class")
                enqueue_seq = self._next_resource_sequence(connection, "enqueue")
                connection.execute(
                    "INSERT INTO resource_queue_entries("
                    "queue_id, task_id, attempt_id, run_id, fairness_group_id, "
                    "batch_id, enqueue_seq, required_resources_json, claim_generation, "
                    "enqueue_configuration_id, enqueue_configuration_version, "
                    "enqueue_configuration_sha256, request_binding_sha256, "
                    "state, bypass_count, "
                    "reservation_state, reservation_seq, lease_candidate_id, "
                    "launch_token, lease_id, admitted_seq) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'QUEUED', 0, "
                    "'NONE', NULL, ?, ?, NULL, NULL)",
                    (
                        queue_id,
                        task_id,
                        attempt_id,
                        authority_id,
                        fairness_group_id or authority_id,
                        batch_id,
                        enqueue_seq,
                        resource_request,
                        generation,
                        configuration["configuration_id"],
                        configuration["configuration_version"],
                        configuration["configuration_sha256"],
                        request_binding_sha256,
                        lease_candidate_id,
                        launch_token,
                    ),
                )
                event_seq = self._next_resource_sequence(connection, "event")
                connection.execute(
                    "INSERT INTO resource_control_events("
                    "event_seq, event_kind, queue_id, lease_id, configuration_id, "
                    "configuration_version, configuration_sha256, payload_json) "
                    "VALUES (?, 'enqueued', ?, NULL, ?, ?, ?, ?)",
                    (
                        event_seq,
                        queue_id,
                        configuration["configuration_id"],
                        configuration["configuration_version"],
                        configuration["configuration_sha256"],
                        canonical_json_bytes({}).decode("utf-8"),
                    ),
                )
                if fault_point == "after_reclaim_enqueue_before_schedule":
                    raise ResourceAdmissionFault(fault_point)
                if resource_scheduler is None:
                    raise ControlStoreUnavailable(
                        "Resource Request requires a Resource Admission scheduler"
                    )
                resource_scheduler(connection, reclaimed_at)
                if fault_point == "after_reclaim_schedule_before_commit":
                    raise ResourceAdmissionFault(fault_point)
            return connection.execute(
                "SELECT c.*, a.attempt_path, a.state AS attempt_state, "
                "a.completion_sha256, ca.completion_record_json "
                "FROM task_claims c JOIN task_attempts a "
                "ON a.attempt_id=c.attempt_id "
                "LEFT JOIN task_completion_authorities ca "
                "ON ca.attempt_id=a.attempt_id WHERE c.task_id=?",
                (task_id,),
            ).fetchone()

    def task_claim_for_attempt(
        self, task_id: str, attempt_id: str
    ) -> sqlite3.Row | None:
        connection = self._connect()
        try:
            return connection.execute(
                "SELECT c.*, a.attempt_path, a.state AS attempt_state, "
                "a.completion_sha256, ca.completion_record_json "
                "FROM task_claims c JOIN task_attempts a "
                "ON a.task_id=c.task_id LEFT JOIN task_completion_authorities ca "
                "ON ca.attempt_id=a.attempt_id "
                "WHERE c.task_id=? AND a.attempt_id=?",
                (task_id, attempt_id),
            ).fetchone()
        finally:
            connection.close()

    def task_attempts_for_task(self, task_id: str) -> list[sqlite3.Row]:
        connection = self._connect()
        try:
            return connection.execute(
                "SELECT a.*, ca.completion_record_json, "
                "tp.journal_sha256 AS promotion_journal_sha256, "
                "tp.state AS promotion_state FROM task_attempts a "
                "LEFT JOIN task_completion_authorities ca "
                "ON ca.attempt_id=a.attempt_id "
                "LEFT JOIN task_promotion_intents tp "
                "ON tp.attempt_id=a.attempt_id WHERE a.task_id=? "
                "ORDER BY a.claim_generation",
                (task_id,),
            ).fetchall()
        finally:
            connection.close()

    def task_reclaim_history(self, task_id: str) -> list[dict]:
        connection = self._connect()
        try:
            self._validate_task_reclaim_transition_table(connection)
            self._validate_task_attempt_authority_rows(connection)
            self._validate_task_reclaim_transition_rows(connection)
            rows = connection.execute(
                "SELECT transition_record_json FROM task_reclaim_transitions "
                "WHERE task_id=? ORDER BY replacement_claim_generation",
                (task_id,),
            ).fetchall()
            return [json.loads(str(row["transition_record_json"])) for row in rows]
        finally:
            connection.close()

    def task_attempt_authority(self, attempt_id: str) -> sqlite3.Row | None:
        connection = self._connect()
        try:
            return connection.execute(
                "SELECT attempt_record_json, attempt_record_sha256 "
                "FROM task_attempt_authorities WHERE attempt_id=?",
                (attempt_id,),
            ).fetchone()
        finally:
            connection.close()

    def task_ids_for_authority(self, authority_id: str) -> set[str]:
        connection = self._connect()
        try:
            return {
                str(row[0])
                for row in connection.execute(
                    "SELECT task_id FROM task_claims WHERE authority_id=?",
                    (authority_id,),
                ).fetchall()
            }
        finally:
            connection.close()

    def task_claim_for_task(self, task_id: str) -> sqlite3.Row | None:
        connection = self._connect()
        try:
            return connection.execute(
                "SELECT * FROM task_claims WHERE task_id=?", (task_id,)
            ).fetchone()
        finally:
            connection.close()

    def active_task_claims(self) -> list[sqlite3.Row]:
        connection = self._connect()
        try:
            return connection.execute(
                "SELECT task_id, authority_id, attempt_id, claim_generation, "
                "coordinator_session_id, worker_id FROM task_claims "
                "WHERE state='ACTIVE' ORDER BY authority_id, task_id"
            ).fetchall()
        finally:
            connection.close()

    def prepare_task_completion(
        self,
        *,
        task_id: str,
        attempt_id: str,
        claim_generation: int,
        completion_record: dict,
    ) -> sqlite3.Row:
        self.contracts.validate("task-completion-record", completion_record)
        completion_json = canonical_json_bytes(completion_record).decode("utf-8")
        completion_sha256 = hashlib.sha256(
            completion_json.encode("utf-8")
        ).hexdigest()
        with self._immediate() as connection:
            claim = connection.execute(
                "SELECT state, attempt_id, claim_generation FROM task_claims WHERE task_id=?",
                (task_id,),
            ).fetchone()
            if (
                claim is None
                or claim["state"] != "ACTIVE"
                or claim["attempt_id"] != attempt_id
                or int(claim["claim_generation"]) != claim_generation
            ):
                raise KernelConflict("Task Completion preparation fencing token is stale")
            attempt = connection.execute(
                "SELECT a.*, ca.completion_record_json FROM task_attempts a "
                "LEFT JOIN task_completion_authorities ca "
                "ON ca.attempt_id=a.attempt_id WHERE a.attempt_id=?",
                (attempt_id,),
            ).fetchone()
            if attempt is None or attempt["state"] not in {
                "CLAIMED",
                "VALIDATED_WAITING_FOR_PROMOTION",
            }:
                raise KernelConflict("Task Attempt cannot prepare Completion evidence")
            if attempt["completion_record_json"] is not None:
                if (
                    attempt["completion_record_json"] != completion_json
                    or attempt["completion_sha256"] != completion_sha256
                ):
                    raise KernelConflict("Task Completion preparation changed on retry")
                return attempt
            if attempt["state"] != "CLAIMED":
                raise ControlStoreUnavailable(
                    "validated Task Attempt lacks prepared Completion authority"
                )
            connection.execute(
                "INSERT INTO task_completion_authorities("
                "attempt_id, completion_record_json) VALUES (?, ?)",
                (attempt_id, completion_json),
            )
            cursor = connection.execute(
                "UPDATE task_attempts SET completion_sha256=? "
                "WHERE attempt_id=? AND state='CLAIMED' AND completion_sha256 IS NULL",
                (completion_sha256, attempt_id),
            )
            if cursor.rowcount != 1:
                raise KernelConflict("Task Completion preparation compare-and-set failed")
            return connection.execute(
                "SELECT a.*, ca.completion_record_json FROM task_attempts a "
                "JOIN task_completion_authorities ca ON ca.attempt_id=a.attempt_id "
                "WHERE a.attempt_id=?",
                (attempt_id,),
            ).fetchone()

    def mark_task_validated(
        self,
        *,
        task_id: str,
        attempt_id: str,
        claim_generation: int,
        completion_sha256: str,
    ) -> None:
        with self._immediate() as connection:
            claim = connection.execute(
                "SELECT state, attempt_id, claim_generation FROM task_claims WHERE task_id=?",
                (task_id,),
            ).fetchone()
            if (
                claim is None
                or claim["state"] != "ACTIVE"
                or claim["attempt_id"] != attempt_id
                or int(claim["claim_generation"]) != claim_generation
            ):
                raise KernelConflict("Task Completion Gate fencing token is stale")
            attempt = connection.execute(
                "SELECT a.state, a.completion_sha256, ca.completion_record_json "
                "FROM task_attempts a LEFT JOIN task_completion_authorities ca "
                "ON ca.attempt_id=a.attempt_id WHERE a.attempt_id=?",
                (attempt_id,),
            ).fetchone()
            if attempt is None:
                raise KernelConflict("Task Attempt is absent from the Control Store")
            if attempt["state"] == "VALIDATED_WAITING_FOR_PROMOTION":
                if attempt["completion_sha256"] != completion_sha256:
                    raise KernelConflict("Task Completion evidence changed after validation")
                return
            if attempt["state"] != "CLAIMED":
                raise KernelConflict("Task Attempt cannot enter the Completion Gate")
            if attempt["completion_record_json"] is None:
                raise ControlStoreUnavailable(
                    "Task Completion evidence lacks a durable preparation"
                )
            cursor = connection.execute(
                "UPDATE task_attempts SET state='VALIDATED_WAITING_FOR_PROMOTION' "
                "WHERE attempt_id=? AND state='CLAIMED' AND completion_sha256=?",
                (attempt_id, completion_sha256),
            )
            if cursor.rowcount == 1:
                return
            current = connection.execute(
                "SELECT state, completion_sha256 FROM task_attempts "
                "WHERE attempt_id=?",
                (attempt_id,),
            ).fetchone()
            if (
                current is not None
                and current["state"] == "VALIDATED_WAITING_FOR_PROMOTION"
                and current["completion_sha256"] == completion_sha256
            ):
                return
            if (
                current is not None
                and current["completion_sha256"] != completion_sha256
            ):
                raise KernelConflict(
                    "Task Completion evidence differs from durable authority"
                )
            raise KernelConflict("Task Completion validation compare-and-set failed")

    @staticmethod
    def derive_task_promotion_intent_id(
        *,
        run_id: str,
        task_id: str,
        attempt_id: str,
        claim_generation: int,
        expected_run_revision: int,
        old_run_record_sha256: str,
        envelope_sha256: str,
        completion_sha256: str,
        outputs_json: str,
    ) -> str:
        outputs_sha256 = hashlib.sha256(outputs_json.encode("utf-8")).hexdigest()
        return hashlib.sha256(
            "\0".join(
                (
                    "task_artifact_promotion_v2",
                    run_id,
                    task_id,
                    attempt_id,
                    str(claim_generation),
                    str(expected_run_revision),
                    old_run_record_sha256,
                    envelope_sha256,
                    completion_sha256,
                    outputs_sha256,
                )
            ).encode("utf-8")
        ).hexdigest()

    def _validate_task_promotion_intent(
        self, connection: sqlite3.Connection, intent: sqlite3.Row
    ) -> None:
        try:
            replacement = json.loads(str(intent["replacement_run_record_json"]))
            outputs = json.loads(str(intent["outputs_json"]))
        except (TypeError, json.JSONDecodeError) as exc:
            raise ControlStoreUnavailable(
                "Task promotion intent contains invalid JSON"
            ) from exc
        replacement_json = canonical_json_bytes(replacement).decode("utf-8")
        outputs_json = canonical_json_bytes(outputs).decode("utf-8")
        if (
            replacement_json != intent["replacement_run_record_json"]
            or outputs_json != intent["outputs_json"]
        ):
            raise ControlStoreUnavailable(
                "Task promotion intent JSON is not canonical"
            )
        self.contracts.validate_run_record(replacement)
        claim = connection.execute(
            "SELECT * FROM task_claims WHERE task_id=?", (intent["task_id"],)
        ).fetchone()
        attempt = connection.execute(
            "SELECT a.*, ca.completion_record_json FROM task_attempts a "
            "LEFT JOIN task_completion_authorities ca ON ca.attempt_id=a.attempt_id "
            "WHERE a.attempt_id=?",
            (intent["attempt_id"],),
        ).fetchone()
        if (
            claim is None
            or attempt is None
            or claim["authority_id"] != intent["run_id"]
            or attempt["task_id"] != intent["task_id"]
            or int(attempt["claim_generation"]) != int(intent["claim_generation"])
            or attempt["completion_sha256"] is None
            or attempt["completion_record_json"] is None
        ):
            raise ControlStoreUnavailable(
                "Task promotion intent Claim or Completion binding is invalid"
            )
        state = str(intent["state"])
        if state == "COMMITTED":
            lifecycle_is_valid = (
                claim["state"] == "TERMINAL"
                and claim["attempt_id"] == intent["attempt_id"]
                and int(claim["claim_generation"])
                == int(intent["claim_generation"])
                and attempt["state"] == "COMMITTED_COMPLETE"
            )
        elif state == "ABORTED":
            lifecycle_is_valid = attempt["state"] in {
                "VALIDATED_WAITING_FOR_PROMOTION",
                "STALE",
                "ABANDONED",
                "FAILED",
            }
        else:
            lifecycle_is_valid = (
                claim["state"] == "ACTIVE"
                and claim["attempt_id"] == intent["attempt_id"]
                and int(claim["claim_generation"])
                == int(intent["claim_generation"])
                and attempt["state"] == "VALIDATED_WAITING_FOR_PROMOTION"
            )
        if not lifecycle_is_valid or (
            state in {"FILES_PUBLISHED", "RECORD_COMMITTED", "COMMITTED"}
            and intent["journal_sha256"] is None
        ):
            raise ControlStoreUnavailable(
                "Task promotion intent lifecycle authority is invalid"
            )
        replacement_sha = hashlib.sha256(
            replacement_json.encode("utf-8")
        ).hexdigest()
        version = connection.execute(
            "SELECT identity_version FROM task_promotion_identity_versions "
            "WHERE intent_id=?",
            (intent["intent_id"],),
        ).fetchone()
        if version is None:
            raise ControlStoreUnavailable(
                "Task promotion intent lacks an explicit identity version"
            )
        if version["identity_version"] == "legacy-v1":
            if intent["state"] != "COMMITTED":
                raise ControlStoreUnavailable(
                    "legacy Task promotion identity is non-terminal"
                )
            expected_id = self._derive_legacy_task_promotion_intent_id(
                intent, outputs
            )
            expected_identity = self._derive_legacy_task_promotion_row_identity(
                intent, outputs_json
            )
        elif version["identity_version"] == "evidence-v2":
            expected_id = self.derive_task_promotion_intent_id(
                run_id=str(intent["run_id"]),
                task_id=str(intent["task_id"]),
                attempt_id=str(intent["attempt_id"]),
                claim_generation=int(intent["claim_generation"]),
                expected_run_revision=int(intent["expected_run_revision"]),
                old_run_record_sha256=str(intent["old_run_record_sha256"]),
                envelope_sha256=str(claim["envelope_sha256"]),
                completion_sha256=str(attempt["completion_sha256"]),
                outputs_json=outputs_json,
            )
            outputs_sha = hashlib.sha256(outputs_json.encode("utf-8")).hexdigest()
            expected_identity = hashlib.sha256(
                "\0".join(
                    (
                        "task_promotion_intent_row_v2",
                        expected_id,
                        replacement_sha,
                        outputs_sha,
                        str(claim["envelope_sha256"]),
                        str(attempt["completion_sha256"]),
                    )
                ).encode("utf-8")
            ).hexdigest()
        else:
            raise ControlStoreUnavailable(
                "Task promotion intent identity version is unsupported"
            )
        if (
            intent["intent_id"] != expected_id
            or intent["intent_identity"] != expected_identity
            or intent["replacement_run_record_sha256"] != replacement_sha
            or replacement.get("run_id") != intent["run_id"]
            or replacement.get("coordination_revision")
            != int(intent["expected_run_revision"]) + 1
            or replacement.get("last_mutation_intent_id") != expected_id
        ):
            raise ControlStoreUnavailable(
                "Task promotion intent self-authentication failed"
            )

    def prepare_task_promotion(
        self,
        *,
        run_id: str,
        task_id: str,
        attempt_id: str,
        claim_generation: int,
        expected_run_revision: int,
        old_run_record_sha256: str,
        intent_id: str,
        replacement_run_record: dict,
        outputs: list[dict],
    ) -> sqlite3.Row:
        self.contracts.validate_run_record(replacement_run_record)
        replacement_json = canonical_json_bytes(replacement_run_record).decode("utf-8")
        replacement_sha = hashlib.sha256(replacement_json.encode("utf-8")).hexdigest()
        outputs_json = canonical_json_bytes(outputs).decode("utf-8")

        def plan_task_promotion(
            connection: sqlite3.Connection,
        ) -> tuple[str, str]:
            claim = connection.execute(
                "SELECT * FROM task_claims WHERE task_id=?", (task_id,)
            ).fetchone()
            attempt = connection.execute(
                "SELECT a.*, ca.completion_record_json FROM task_attempts a "
                "LEFT JOIN task_completion_authorities ca ON ca.attempt_id=a.attempt_id "
                "WHERE a.attempt_id=?",
                (attempt_id,),
            ).fetchone()
            if (
                claim is None
                or attempt is None
                or claim["state"] != "ACTIVE"
                or claim["authority_id"] != run_id
                or claim["attempt_id"] != attempt_id
                or int(claim["claim_generation"]) != claim_generation
                or attempt["state"] != "VALIDATED_WAITING_FOR_PROMOTION"
                or attempt["completion_sha256"] is None
                or attempt["completion_record_json"] is None
            ):
                raise KernelConflict("Task promotion fencing or Completion state is stale")
            expected_intent_id = self.derive_task_promotion_intent_id(
                run_id=run_id,
                task_id=task_id,
                attempt_id=attempt_id,
                claim_generation=claim_generation,
                expected_run_revision=expected_run_revision,
                old_run_record_sha256=old_run_record_sha256,
                envelope_sha256=str(claim["envelope_sha256"]),
                completion_sha256=str(attempt["completion_sha256"]),
                outputs_json=outputs_json,
            )
            outputs_sha = hashlib.sha256(outputs_json.encode("utf-8")).hexdigest()
            identity = hashlib.sha256(
                "\0".join(
                    (
                        "task_promotion_intent_row_v2",
                        expected_intent_id,
                        replacement_sha,
                        outputs_sha,
                        str(claim["envelope_sha256"]),
                        str(attempt["completion_sha256"]),
                    )
                ).encode("utf-8")
            ).hexdigest()
            if (
                intent_id != expected_intent_id
                or replacement_run_record.get("last_mutation_intent_id") != intent_id
            ):
                raise KernelConflict(
                    "Task promotion intent identity is not bound to immutable evidence"
                )
            existing = connection.execute(
                "SELECT * FROM task_promotion_intents WHERE intent_id=?",
                (intent_id,),
            ).fetchone()
            if existing is not None:
                self._validate_task_promotion_intent(connection, existing)
                self._assert_run_promotion_slot(
                    connection,
                    run_id,
                    owner_kind="task_promotion",
                    owner_id=intent_id,
                )
                return "REPLAY", identity
            predecessor = self._current_run_record_sha(connection, run_id)
            if predecessor != old_run_record_sha256:
                raise ArtifactDrift(
                    "Task promotion Run Record predecessor is stale",
                    data={"drifted_paths": ["workflow/run.json"]},
                )
            if self._next_run_revision(connection, run_id) != expected_run_revision:
                raise KernelConflict("Task promotion expected Run revision is stale")
            self._assert_run_promotion_slot(connection, run_id)
            return "INSERT", identity

        with self._planned_immediate(plan_task_promotion) as (
            connection,
            plan,
        ):
            action, identity = plan
            if action == "REPLAY":
                existing = connection.execute(
                    "SELECT * FROM task_promotion_intents "
                    "WHERE intent_id=? AND intent_identity=?",
                    (intent_id, identity),
                ).fetchone()
                if existing is None:
                    raise KernelConflict(
                        "Task promotion replay compare-and-swap failed"
                    )
                return existing
            try:
                connection.execute(
                    "INSERT INTO task_promotion_intents(intent_id, run_id, task_id, "
                    "attempt_id, claim_generation, expected_run_revision, "
                    "old_run_record_sha256, replacement_run_record_sha256, "
                    "replacement_run_record_json, outputs_json, journal_sha256, state, "
                    "intent_identity) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, "
                    "'PREPARED', ?)",
                    (
                        intent_id, run_id, task_id, attempt_id, claim_generation,
                        expected_run_revision, old_run_record_sha256, replacement_sha,
                        replacement_json, outputs_json, identity,
                    ),
                )
                connection.execute(
                    "INSERT INTO task_promotion_identity_versions("
                    "intent_id, identity_version) VALUES (?, 'evidence-v2')",
                    (intent_id,),
                )
            except sqlite3.IntegrityError as exc:
                raise KernelConflict("Run Promotion Slot compare-and-set failed") from exc
            inserted = connection.execute(
                "SELECT * FROM task_promotion_intents "
                "WHERE intent_id=? AND intent_identity=? AND state='PREPARED' "
                "AND journal_sha256 IS NULL",
                (intent_id, identity),
            ).fetchone()
            if inserted is None:
                raise KernelConflict("Task promotion insert compare-and-swap failed")
            return inserted

    def bind_task_promotion_journal(self, intent_id: str, journal_sha256: str) -> None:
        def plan_journal_binding(
            connection: sqlite3.Connection,
        ) -> tuple[str, str, str | None]:
            row = connection.execute(
                "SELECT * FROM task_promotion_intents WHERE intent_id=?",
                (intent_id,),
            ).fetchone()
            if row is None:
                raise KernelConflict("promotion journal binding requires a known intent")
            self._validate_task_promotion_intent(connection, row)
            if row["journal_sha256"] is not None:
                if row["journal_sha256"] != journal_sha256:
                    raise ArtifactDrift("promotion journal fingerprint changed")
                return (
                    "REPLAY",
                    str(row["intent_identity"]),
                    str(row["journal_sha256"]),
                )
            if row["state"] != "PREPARED":
                raise KernelConflict("promotion journal binding requires PREPARED intent")
            return "BIND", str(row["intent_identity"]), None

        with self._planned_immediate(plan_journal_binding) as (
            connection,
            plan,
        ):
            action, intent_identity, expected_journal = plan
            if action == "REPLAY":
                replay = connection.execute(
                    "SELECT 1 FROM task_promotion_intents WHERE intent_id=? "
                    "AND intent_identity=? AND journal_sha256=?",
                    (intent_id, intent_identity, expected_journal),
                ).fetchone()
                if replay is None:
                    raise KernelConflict(
                        "promotion journal replay compare-and-swap failed"
                    )
                return
            cursor = connection.execute(
                "UPDATE task_promotion_intents SET journal_sha256=? "
                "WHERE intent_id=? AND intent_identity=? AND state='PREPARED' "
                "AND journal_sha256 IS NULL",
                (journal_sha256, intent_id, intent_identity),
            )
            if cursor.rowcount != 1:
                raise KernelConflict(
                    "promotion journal binding compare-and-swap failed"
                )

    def transition_task_promotion(
        self, intent_id: str, *, expected_state: str, new_state: str
    ) -> None:
        allowed = {
            ("PREPARED", "FILES_PUBLISHED"),
            ("FILES_PUBLISHED", "RECORD_COMMITTED"),
        }
        if (expected_state, new_state) not in allowed:
            raise ContractError("invalid Task promotion intent transition")

        def plan_transition(
            connection: sqlite3.Connection,
        ) -> tuple[str, str, str]:
            intent = connection.execute(
                "SELECT * FROM task_promotion_intents WHERE intent_id=?",
                (intent_id,),
            ).fetchone()
            if intent is None:
                raise KernelConflict("Task promotion intent is absent")
            self._validate_task_promotion_intent(connection, intent)
            state = str(intent["state"])
            if state == new_state:
                return "REPLAY", str(intent["intent_identity"]), str(
                    intent["journal_sha256"]
                )
            if state != expected_state or intent["journal_sha256"] is None:
                raise KernelConflict(
                    "Task promotion intent transition compare-and-set failed"
                )
            return "TRANSITION", str(intent["intent_identity"]), str(
                intent["journal_sha256"]
            )

        with self._planned_immediate(plan_transition) as (connection, plan):
            action, intent_identity, journal_sha256 = plan
            if action == "REPLAY":
                replay = connection.execute(
                    "SELECT 1 FROM task_promotion_intents WHERE intent_id=? "
                    "AND intent_identity=? AND state=? AND journal_sha256=?",
                    (intent_id, intent_identity, new_state, journal_sha256),
                ).fetchone()
                if replay is None:
                    raise KernelConflict(
                        "Task promotion transition replay compare-and-swap failed"
                    )
                return
            cursor = connection.execute(
                "UPDATE task_promotion_intents SET state=? "
                "WHERE intent_id=? AND intent_identity=? AND state=? "
                "AND journal_sha256=?",
                (
                    new_state,
                    intent_id,
                    intent_identity,
                    expected_state,
                    journal_sha256,
                ),
            )
            if cursor.rowcount == 1:
                return
            raise KernelConflict("Task promotion intent transition compare-and-set failed")

    def commit_task_promotion(self, intent_id: str) -> None:
        def plan_commit(
            connection: sqlite3.Connection,
        ) -> tuple[str, str, str, str, str, int]:
            intent = connection.execute(
                "SELECT * FROM task_promotion_intents WHERE intent_id=?", (intent_id,)
            ).fetchone()
            if intent is None:
                raise KernelConflict("Task promotion intent is absent")
            self._validate_task_promotion_intent(connection, intent)
            if intent["state"] == "COMMITTED":
                if self._current_run_record_sha(
                    connection,
                    str(intent["run_id"]),
                ) is None:
                    raise ControlStoreUnavailable(
                        "committed Task promotion lacks a complete Run chain"
                    )
                return (
                    "REPLAY",
                    str(intent["intent_identity"]),
                    str(intent["journal_sha256"]),
                    str(intent["task_id"]),
                    str(intent["attempt_id"]),
                    int(intent["claim_generation"]),
                )
            if intent["state"] != "RECORD_COMMITTED":
                raise KernelConflict("Task promotion cannot commit before coordination marker")
            predecessor = self._current_run_record_sha(connection, intent["run_id"])
            if predecessor != intent["old_run_record_sha256"]:
                raise ControlStoreUnavailable(
                    "Task promotion predecessor changed before final commit"
                )
            claim = connection.execute(
                "SELECT * FROM task_claims WHERE task_id=?", (intent["task_id"],)
            ).fetchone()
            if (
                claim is None
                or claim["state"] != "ACTIVE"
                or claim["attempt_id"] != intent["attempt_id"]
                or int(claim["claim_generation"]) != int(intent["claim_generation"])
            ):
                raise KernelConflict("Task promotion final fencing compare-and-set failed")
            return (
                "COMMIT",
                str(intent["intent_identity"]),
                str(intent["journal_sha256"]),
                str(intent["task_id"]),
                str(intent["attempt_id"]),
                int(intent["claim_generation"]),
            )

        with self._planned_immediate(plan_commit) as (connection, plan):
            (
                action,
                intent_identity,
                journal_sha256,
                task_id,
                attempt_id,
                claim_generation,
            ) = plan
            if action == "REPLAY":
                replay = connection.execute(
                    "SELECT 1 FROM task_promotion_intents WHERE intent_id=? "
                    "AND intent_identity=? AND state='COMMITTED' "
                    "AND journal_sha256=?",
                    (intent_id, intent_identity, journal_sha256),
                ).fetchone()
                if replay is None:
                    raise KernelConflict(
                        "Task promotion commit replay compare-and-swap failed"
                    )
                return
            intent_cursor = connection.execute(
                "UPDATE task_promotion_intents SET state='COMMITTED' "
                "WHERE intent_id=? AND intent_identity=? "
                "AND state='RECORD_COMMITTED' AND journal_sha256=?",
                (intent_id, intent_identity, journal_sha256),
            )
            attempt_cursor = connection.execute(
                "UPDATE task_attempts SET state='COMMITTED_COMPLETE' "
                "WHERE attempt_id=? AND task_id=? AND claim_generation=? "
                "AND state='VALIDATED_WAITING_FOR_PROMOTION'",
                (attempt_id, task_id, claim_generation),
            )
            claim_cursor = connection.execute(
                "UPDATE task_claims SET state='TERMINAL' "
                "WHERE task_id=? AND state='ACTIVE' AND attempt_id=? AND claim_generation=?",
                (task_id, attempt_id, claim_generation),
            )
            if (
                intent_cursor.rowcount != 1
                or attempt_cursor.rowcount != 1
                or claim_cursor.rowcount != 1
            ):
                raise KernelConflict(
                    "Task promotion final compare-and-swap failed"
                )

    def abort_task_promotion(self, intent_id: str) -> None:
        def plan_abort(
            connection: sqlite3.Connection,
        ) -> tuple[str, str, str | None]:
            intent = connection.execute(
                "SELECT * FROM task_promotion_intents WHERE intent_id=?",
                (intent_id,),
            ).fetchone()
            if intent is None:
                raise KernelConflict("Task promotion intent is absent")
            self._validate_task_promotion_intent(connection, intent)
            if intent["state"] == "ABORTED":
                return (
                    "REPLAY",
                    str(intent["intent_identity"]),
                    None
                    if intent["journal_sha256"] is None
                    else str(intent["journal_sha256"]),
                )
            if intent["state"] != "PREPARED":
                raise KernelConflict(
                    "Task promotion can abort only before output publication"
                )
            return (
                "ABORT",
                str(intent["intent_identity"]),
                None
                if intent["journal_sha256"] is None
                else str(intent["journal_sha256"]),
            )

        with self._planned_immediate(plan_abort) as (connection, plan):
            action, intent_identity, journal_sha256 = plan
            if action == "REPLAY":
                replay = connection.execute(
                    "SELECT 1 FROM task_promotion_intents WHERE intent_id=? "
                    "AND intent_identity=? AND state='ABORTED' "
                    "AND journal_sha256 IS ?",
                    (intent_id, intent_identity, journal_sha256),
                ).fetchone()
                if replay is None:
                    raise KernelConflict(
                        "Task promotion abort replay compare-and-swap failed"
                    )
                return
            cursor = connection.execute(
                "UPDATE task_promotion_intents SET state='ABORTED' "
                "WHERE intent_id=? AND intent_identity=? AND state='PREPARED' "
                "AND journal_sha256 IS ?",
                (intent_id, intent_identity, journal_sha256),
            )
            if cursor.rowcount != 1:
                raise KernelConflict(
                    "Task promotion abort compare-and-swap failed"
                )

    def active_task_promotion(self, run_id: str) -> sqlite3.Row | None:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM task_promotion_intents WHERE run_id=? AND "
                "state IN ('PREPARED','FILES_PUBLISHED','RECORD_COMMITTED')",
                (run_id,),
            ).fetchall()
            if len(rows) > 1:
                raise ControlStoreUnavailable("Run has multiple non-terminal promotions")
            if not rows:
                return None
            self._validate_task_promotion_intent(connection, rows[0])
            return rows[0]
        finally:
            connection.close()

    def task_promotion_for_attempt(
        self, task_id: str, attempt_id: str
    ) -> sqlite3.Row | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM task_promotion_intents WHERE task_id=? AND attempt_id=? "
                "ORDER BY expected_run_revision DESC LIMIT 1",
                (task_id, attempt_id),
            ).fetchone()
            if row is not None:
                self._validate_task_promotion_intent(connection, row)
            return row
        finally:
            connection.close()

    def task_promotion_by_id(self, intent_id: str) -> sqlite3.Row | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM task_promotion_intents WHERE intent_id=?", (intent_id,)
            ).fetchone()
            if row is not None:
                self._validate_task_promotion_intent(connection, row)
            return row
        finally:
            connection.close()

    def task_promotion_identity_version(self, intent_id: str) -> str | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT identity_version FROM task_promotion_identity_versions "
                "WHERE intent_id=?",
                (intent_id,),
            ).fetchone()
            return None if row is None else str(row["identity_version"])
        finally:
            connection.close()
