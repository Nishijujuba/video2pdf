from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Iterator
import uuid

from .contracts import ContractRegistry
from .errors import ContractError, ControlStoreUnavailable, KernelConflict
from .models import ControlStoreHealth
from .utils import normalized_physical_path, read_json, write_json_atomic


SCHEMA_VERSION = 2
BUSY_TIMEOUT_MS = 5000
LOCK_PROBE_TIMEOUT_MS = 100
MARKER_NAME = "control-store.json"
DATABASE_RELPATH = ".workflow-control/control.sqlite3"


class ControlStore:
    """Cross-run transaction authority for Slice 1 bindings and init intents."""

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
            connection.execute(
                "INSERT INTO control_store_metadata(key, value) VALUES ('store_id', ?)",
                (self.store_id,),
            )
            connection.execute("INSERT INTO schema_migrations(version) VALUES (1)")
            connection.execute("INSERT INTO schema_migrations(version) VALUES (2)")
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
                version = int(
                    connection.execute(
                        "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
                    ).fetchone()[0]
                )
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
                elif version == 2:
                    if present != expected_columns:
                        raise ControlStoreUnavailable(
                            "Control Store v2 intent identity columns are incomplete"
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
                }
                if not required_tables.issubset(tables):
                    raise ControlStoreUnavailable(
                        f"Control Store schema is incomplete: {sorted(required_tables - tables)}"
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
