from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import time
from typing import Iterator
import uuid

from .contracts import ContractRegistry
from .errors import ArtifactDrift, ContractError, ControlStoreUnavailable, KernelConflict
from .models import ControlStoreHealth
from .utils import (
    canonical_json_bytes,
    normalized_physical_path,
    read_json,
    sha256_file,
    write_json_atomic,
)


SCHEMA_VERSION = 5
BUSY_TIMEOUT_MS = 5000
LOCK_PROBE_TIMEOUT_MS = 100
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


def _normalized_sql(value: str | None) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", "", value).casefold().replace("ifnotexists", "")


class ControlStore:
    """Cross-run transaction authority for Kernel bindings and mutation intents."""

    def __init__(self, workspace_root: Path, contracts: ContractRegistry) -> None:
        self._configure(workspace_root, contracts)
        self._validate_existing()

    def _configure(self, workspace_root: Path, contracts: ContractRegistry) -> None:
        self.workspace_root = workspace_root.resolve()
        self.contracts = contracts
        raw = str(self.workspace_root)
        if raw.startswith("\\\\"):
            raise ControlStoreUnavailable("UNC workspace roots are unsupported")
        self.control_dir = self.workspace_root / ".workflow-control"
        self.path = self.control_dir / "control.sqlite3"
        self.marker_path = self.control_dir / MARKER_NAME
        self.store_id = hashlib.sha256(
            f"video-workflow-control-store-v1\0{normalized_physical_path(self.workspace_root)}".encode(
                "utf-8"
            )
        ).hexdigest()
        self.anchor_dir = self.workspace_root.parent / ".video-workflow-control-anchors"
        self.anchor_path = self.anchor_dir / f"{self.store_id}.json"

    @classmethod
    def identity_evidence_exists(cls, workspace_root: Path) -> bool:
        workspace = workspace_root.resolve()
        store_id = hashlib.sha256(
            f"video-workflow-control-store-v1\0{normalized_physical_path(workspace)}".encode(
                "utf-8"
            )
        ).hexdigest()
        control_dir = workspace / ".workflow-control"
        anchor = workspace.parent / ".video-workflow-control-anchors" / f"{store_id}.json"
        return (
            anchor.exists()
            or control_dir.exists()
            and any(control_dir.iterdir())
        )

    @classmethod
    def initialize(
        cls, workspace_root: Path, contracts: ContractRegistry
    ) -> "ControlStore":
        store = cls.__new__(cls)
        store._configure(workspace_root, contracts)
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
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=EXTRA")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        return connection

    @contextmanager
    def _immediate(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def _create_database(self) -> None:
        if self.path.exists():
            raise ControlStoreUnavailable("refusing to initialize over an existing database")
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
            self._create_task_tables(connection)
            connection.execute(
                "INSERT INTO control_store_metadata(key, value) VALUES ('store_id', ?)",
                (self.store_id,),
            )
            connection.execute("INSERT INTO schema_migrations(version) VALUES (1)")
            connection.execute("INSERT INTO schema_migrations(version) VALUES (2)")
            connection.execute("INSERT INTO schema_migrations(version) VALUES (3)")
            connection.execute("INSERT INTO schema_migrations(version) VALUES (4)")
            connection.execute("INSERT INTO schema_migrations(version) VALUES (5)")
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
        self._migrate_existing()
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
    def _create_task_tables(connection: sqlite3.Connection) -> None:
        connection.execute(TASK_CLAIMS_TABLE_SQL)
        connection.execute(TASK_ATTEMPTS_TABLE_SQL)
        connection.execute(TASK_ATTEMPT_AUTHORITIES_TABLE_SQL)
        connection.execute(TASK_COMPLETION_AUTHORITIES_TABLE_SQL)
        connection.execute(TASK_PROMOTION_TABLE_SQL)
        connection.execute(TASK_PROMOTION_INDEX_SQL)
        connection.execute(TASK_PROMOTION_IDENTITY_VERSIONS_TABLE_SQL)
        ControlStore._validate_task_tables(connection)

    @staticmethod
    def _create_task_tables_v4(connection: sqlite3.Connection) -> None:
        connection.execute(TASK_CLAIMS_TABLE_SQL)
        connection.execute(TASK_ATTEMPTS_TABLE_SQL)
        connection.execute(TASK_PROMOTION_TABLE_SQL)
        connection.execute(TASK_PROMOTION_INDEX_SQL)
        ControlStore._validate_task_tables(
            connection,
            completion_record_authority=False,
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
                if row["attempt_id"] == row["current_attempt_id"] and (
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

    def _migrate_existing(self) -> None:
        expected_columns = {
            "expected_run_record_sha256",
            "canonical_platform",
            "canonical_item_id",
            "source_identity",
            "source_manifest_sha256",
        }
        try:
            with self._immediate() as connection:
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
                    connection.execute(TASK_ATTEMPT_AUTHORITIES_TABLE_SQL)
                    connection.execute(TASK_COMPLETION_AUTHORITIES_TABLE_SQL)
                    connection.execute(
                        TASK_PROMOTION_IDENTITY_VERSIONS_TABLE_SQL
                    )
                    self._backfill_task_attempt_authorities(connection)
                    self._backfill_task_completion_authorities(connection)
                    self._migrate_task_promotion_identity_versions(connection)
                    self._validate_task_tables(connection)
                    self._validate_task_attempt_authority_rows(connection)
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
                    self._validate_task_tables(connection)
                    self._validate_task_attempt_authority_rows(connection)
                    if self._migration_versions(connection) != [1, 2, 3, 4, 5]:
                        raise ControlStoreUnavailable(
                            "Control Store v5 migration ledger is incomplete"
                        )
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
                    "task_claims",
                    "task_attempts",
                    "task_attempt_authorities",
                    "task_completion_authorities",
                    "task_promotion_identity_versions",
                    "task_promotion_intents",
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
                self._validate_task_tables(connection)
                self._validate_task_attempt_authority_rows(connection)
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
        run_state_mutations = connection.execute(
            "SELECT expected_run_revision, predecessor_committed_sha256, "
            "replacement_run_record_sha256 FROM run_state_mutation_intents "
            "WHERE run_id=? AND state='COMMITTED' "
            "ORDER BY expected_run_revision",
            (run_id,),
        ).fetchall()
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
        with self._immediate() as connection:
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
            mutation_id = self.derive_run_state_mutation_id(
                run_id=run_id,
                expected_run_revision=expected_run_revision,
                old_run_record_sha256=old_run_record_sha256,
            )
            mutation_identity = mutation_id
            if (
                replacement_run_record.get("schema_version") == "2.0.0"
                and replacement_run_record.get("last_mutation_intent_id")
                != mutation_id
            ):
                raise KernelConflict(
                    "v2 run-state mutation replacement lacks its intent identity"
                )
            existing = connection.execute(
                "SELECT * FROM run_state_mutation_intents WHERE mutation_identity=?",
                (mutation_identity,),
            ).fetchone()
            if existing is not None:
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
                return existing
            self._assert_run_promotion_slot(connection, run_id)
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
                        predecessor,
                        replacement_sha,
                        replacement_json,
                        mutation_identity,
                    ),
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
        predecessor = self._current_run_record_sha(connection, mutation["run_id"])
        expected_revision = self._next_run_revision(connection, mutation["run_id"])
        replacement_json = str(mutation["replacement_run_record_json"])
        replacement_sha = hashlib.sha256(replacement_json.encode("utf-8")).hexdigest()
        identity = self.derive_run_state_mutation_id(
            run_id=str(mutation["run_id"]),
            expected_run_revision=int(mutation["expected_run_revision"]),
            old_run_record_sha256=str(mutation["old_run_record_sha256"]),
        )
        try:
            replacement = json.loads(replacement_json)
        except json.JSONDecodeError as exc:
            raise ControlStoreUnavailable(
                "prepared run-state mutation replacement JSON is invalid"
            ) from exc
        if (
            mutation["operation"] != "source_drift_invalidation"
            or mutation["expected_run_revision"] != expected_revision
            or mutation["old_run_record_sha256"] != predecessor
            or mutation["predecessor_committed_sha256"] != predecessor
            or replacement_sha != mutation["replacement_run_record_sha256"]
            or identity != mutation["mutation_identity"]
            or mutation["mutation_id"] != identity
            or (
                replacement.get("schema_version") == "2.0.0"
                and replacement.get("last_mutation_intent_id") != identity
            )
        ):
            raise ControlStoreUnavailable(
                "prepared run-state mutation authority evidence is invalid"
            )

    def commit_run_state_mutation(self, mutation_id: str) -> None:
        with self._immediate() as connection:
            cursor = connection.execute(
                "UPDATE run_state_mutation_intents SET state='COMMITTED' "
                "WHERE mutation_id=? AND state='PREPARED'",
                (mutation_id,),
            )
            if cursor.rowcount == 1:
                return
            row = connection.execute(
                "SELECT state FROM run_state_mutation_intents WHERE mutation_id=?",
                (mutation_id,),
            ).fetchone()
            if row is not None and row["state"] == "COMMITTED":
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
    ) -> sqlite3.Row:
        write_set_json = canonical_json_bytes(list(write_set)).decode("utf-8").strip()
        with self._immediate() as connection:
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
                    return connection.execute(
                        "SELECT c.*, a.attempt_path, a.state AS attempt_state, "
                        "a.completion_sha256, ca.completion_record_json "
                        "FROM task_claims c JOIN task_attempts a "
                        "ON a.attempt_id=c.attempt_id "
                        "LEFT JOIN task_completion_authorities ca "
                        "ON ca.attempt_id=a.attempt_id WHERE c.task_id=?",
                        (task_id,),
                    ).fetchone()
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
            generation = 1
            attempt_id = hashlib.sha256(
                f"task-attempt\0{task_id}\0{generation}".encode("utf-8")
            ).hexdigest()[:24]
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
    ) -> sqlite3.Row:
        if not reason.strip():
            raise ContractError("Task reclaim requires a recovery reason")
        generation = expected_claim_generation + 1
        attempt_id = hashlib.sha256(
            f"task-attempt\0{task_id}\0{generation}".encode("utf-8")
        ).hexdigest()[:24]
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
            abandoned = connection.execute(
                "UPDATE task_attempts SET state='ABANDONED' "
                "WHERE attempt_id=? AND state IN ('CLAIMED','VALIDATED_WAITING_FOR_PROMOTION')",
                (expected_attempt_id,),
            )
            if abandoned.rowcount != 1:
                raise KernelConflict("Task reclaim prior Attempt is not replaceable")
            attempt_record = self._task_attempt_record(
                task_id=task_id,
                attempt_id=attempt_id,
                claim_generation=generation,
                envelope_sha256=str(claim["envelope_sha256"]),
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
            connection.execute(
                "UPDATE task_attempts SET state='VALIDATED_WAITING_FOR_PROMOTION' "
                "WHERE attempt_id=? AND state='CLAIMED' AND completion_sha256=?",
                (attempt_id, completion_sha256),
            )

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
            or claim["attempt_id"] != intent["attempt_id"]
            or int(claim["claim_generation"]) != int(intent["claim_generation"])
            or attempt["task_id"] != intent["task_id"]
            or int(attempt["claim_generation"]) != int(intent["claim_generation"])
            or attempt["completion_sha256"] is None
            or attempt["completion_record_json"] is None
        ):
            raise ControlStoreUnavailable(
                "Task promotion intent Claim or Completion binding is invalid"
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
        with self._immediate() as connection:
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
                return existing
            predecessor = self._current_run_record_sha(connection, run_id)
            if predecessor != old_run_record_sha256:
                raise ArtifactDrift(
                    "Task promotion Run Record predecessor is stale",
                    data={"drifted_paths": ["workflow/run.json"]},
                )
            if self._next_run_revision(connection, run_id) != expected_run_revision:
                raise KernelConflict("Task promotion expected Run revision is stale")
            self._assert_run_promotion_slot(connection, run_id)
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
                "SELECT * FROM task_promotion_intents WHERE intent_id=?", (intent_id,)
            ).fetchone()
            self._validate_task_promotion_intent(connection, inserted)
            return inserted

    def bind_task_promotion_journal(self, intent_id: str, journal_sha256: str) -> None:
        with self._immediate() as connection:
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
                return
            if row["state"] != "PREPARED":
                raise KernelConflict("promotion journal binding requires PREPARED intent")
            connection.execute(
                "UPDATE task_promotion_intents SET journal_sha256=? "
                "WHERE intent_id=? AND state='PREPARED' AND journal_sha256 IS NULL",
                (journal_sha256, intent_id),
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
        with self._immediate() as connection:
            intent = connection.execute(
                "SELECT * FROM task_promotion_intents WHERE intent_id=?",
                (intent_id,),
            ).fetchone()
            if intent is None:
                raise KernelConflict("Task promotion intent is absent")
            self._validate_task_promotion_intent(connection, intent)
            cursor = connection.execute(
                "UPDATE task_promotion_intents SET state=? "
                "WHERE intent_id=? AND state=? AND journal_sha256 IS NOT NULL",
                (new_state, intent_id, expected_state),
            )
            if cursor.rowcount == 1:
                return
            row = connection.execute(
                "SELECT state FROM task_promotion_intents WHERE intent_id=?",
                (intent_id,),
            ).fetchone()
            if row is not None and row["state"] == new_state:
                return
            raise KernelConflict("Task promotion intent transition compare-and-set failed")

    def commit_task_promotion(self, intent_id: str) -> None:
        with self._immediate() as connection:
            intent = connection.execute(
                "SELECT * FROM task_promotion_intents WHERE intent_id=?", (intent_id,)
            ).fetchone()
            if intent is None:
                raise KernelConflict("Task promotion intent is absent")
            self._validate_task_promotion_intent(connection, intent)
            if intent["state"] == "COMMITTED":
                return
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
            connection.execute(
                "UPDATE task_promotion_intents SET state='COMMITTED' "
                "WHERE intent_id=? AND state='RECORD_COMMITTED'",
                (intent_id,),
            )
            connection.execute(
                "UPDATE task_attempts SET state='COMMITTED_COMPLETE' "
                "WHERE attempt_id=? AND state='VALIDATED_WAITING_FOR_PROMOTION'",
                (intent["attempt_id"],),
            )
            connection.execute(
                "UPDATE task_claims SET state='TERMINAL' "
                "WHERE task_id=? AND state='ACTIVE' AND attempt_id=? AND claim_generation=?",
                (intent["task_id"], intent["attempt_id"], intent["claim_generation"]),
            )

    def abort_task_promotion(self, intent_id: str) -> None:
        with self._immediate() as connection:
            intent = connection.execute(
                "SELECT * FROM task_promotion_intents WHERE intent_id=?",
                (intent_id,),
            ).fetchone()
            if intent is None:
                raise KernelConflict("Task promotion intent is absent")
            self._validate_task_promotion_intent(connection, intent)
            if intent["state"] == "ABORTED":
                return
            if intent["state"] != "PREPARED":
                raise KernelConflict(
                    "Task promotion can abort only before output publication"
                )
            connection.execute(
                "UPDATE task_promotion_intents SET state='ABORTED' "
                "WHERE intent_id=? AND state='PREPARED'",
                (intent_id,),
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
