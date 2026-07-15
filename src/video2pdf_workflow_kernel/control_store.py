from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Iterator

from .errors import ControlStoreUnavailable, KernelConflict
from .models import ControlStoreHealth
from .utils import normalized_physical_path


SCHEMA_VERSION = 1
BUSY_TIMEOUT_MS = 5000


class ControlStore:
    """Cross-run transaction authority for Slice 1 bindings and init intents."""

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()
        raw = str(self.workspace_root)
        if raw.startswith("\\\\"):
            raise ControlStoreUnavailable("UNC workspace roots are unsupported")
        self.control_dir = self.workspace_root / ".workflow-control"
        self.control_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.control_dir / "control.sqlite3"
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=BUSY_TIMEOUT_MS / 1000,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
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

    def _migrate(self) -> None:
        with self._immediate() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
            row = connection.execute(
                "SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations"
            ).fetchone()
            version = int(row["version"])
            if version > SCHEMA_VERSION:
                raise ControlStoreUnavailable(
                    f"unknown Control Store schema version: {version}"
                )
            if version < 1:
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
                    "run_record_sha256 TEXT)"
                )
                connection.execute("INSERT INTO schema_migrations(version) VALUES (1)")

    def check(self) -> ControlStoreHealth:
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
                }
                if str(pragmas["journal_mode"]).lower() != required["journal_mode"]:
                    raise ControlStoreUnavailable("Control Store journal_mode is not DELETE")
                for name in ("synchronous", "foreign_keys", "trusted_schema"):
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
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("ROLLBACK")
            finally:
                connection.close()
        except (sqlite3.Error, OSError) as exc:
            raise ControlStoreUnavailable(f"Control Store health check failed: {exc}") from exc
        return ControlStoreHealth(
            status="ok",
            schema_version=SCHEMA_VERSION,
            pragmas=pragmas,
            quick_check=quick_check,
            path=self.path,
        )

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

    def set_intent_state(
        self, intent_id: str, state: str, *, run_record_sha256: str | None = None
    ) -> None:
        with self._immediate() as connection:
            row = connection.execute(
                "SELECT state FROM initialization_intents WHERE intent_id=?", (intent_id,)
            ).fetchone()
            if row is None:
                raise KernelConflict("initialization intent does not exist")
            connection.execute(
                "UPDATE initialization_intents SET state=?, "
                "run_record_sha256=COALESCE(?, run_record_sha256) WHERE intent_id=?",
                (state, run_record_sha256, intent_id),
            )

    def abort_initialization(self, run_id: str) -> None:
        with self._immediate() as connection:
            connection.execute(
                "UPDATE initialization_intents SET state='ABORTED' WHERE run_id=?",
                (run_id,),
            )
            connection.execute("DELETE FROM run_bindings WHERE run_id=?", (run_id,))
