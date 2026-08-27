from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import stat
from typing import Any, Iterator

from .contracts import ContractRegistry
from .control_store import ControlStore
from .errors import ContractError, KernelConflict
from .release_maintenance import ReleaseMaintenance
from .utils import read_json, sha256_file, write_json_atomic


HISTORY_DIR = Path(".workflow-release-history")
TOMBSTONE_FILE = "cutover-authority-tombstone.json"
FENCE_FILE = "cutover-authority-retirement.lock"

_JSON_SURFACES = (
    Path("active_global_gate.json"),
    Path("active_global_gate_policy.json"),
    Path("platform-authorities/bilibili.json"),
    Path("platform-authorities/youtube.json"),
    Path("active_batch.json"),
)
_DATABASES = {
    Path("global-gate-control.sqlite3"): {
        "gate_authority",
        "gate_intents",
        "gate_policy_authority",
        "gate_policy_refresh_intents",
    },
    Path("platform-kernel-control.sqlite3"): {
        "platform_cutover_authority",
        "platform_cutover_intents",
        "platform_authority_refresh_intents",
        "platform_cutover_candidates",
    },
    Path("batch-cutover-control.sqlite3"): {
        "batch_cutover_authority",
        "batch_cutover_intents",
        "batch_authority_refresh_intents",
    },
}
_SIDECARS = ("-wal", "-shm", "-journal")
_MUTATOR_FENCE_HELD: ContextVar[bool] = ContextVar(
    "cutover_mutator_fence_held", default=False
)


def tombstone_path(control_store_root: Path) -> Path:
    return control_store_root.resolve() / HISTORY_DIR / TOMBSTONE_FILE


def _lock_byte(handle: Any, *, blocking: bool) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
        msvcrt.locking(handle.fileno(), mode, 1)
    else:
        import fcntl

        mode = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
        fcntl.flock(handle.fileno(), mode)


def _unlock_byte(handle: Any) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _fence_file_lock(
    root: Path,
    *,
    message: str,
    error_code: str,
    error_type: type[ContractError] | type[KernelConflict],
) -> Iterator[Path]:
    history = root / HISTORY_DIR
    history.mkdir(parents=True, exist_ok=True)
    path = history / FENCE_FILE
    with path.open("a+b") as handle:
        if path.stat().st_size == 0:
            handle.write(b"0")
            handle.flush()
        try:
            _lock_byte(handle, blocking=False)
        except OSError as exc:
            raise error_type(
                message,
                data={
                    "first_failing_gate": "retirement_fence",
                    "error_code": error_code,
                },
            ) from exc
        try:
            yield path
        finally:
            _unlock_byte(handle)


@contextmanager
def _exclusive_retirement_fence(root: Path) -> Iterator[Path]:
    with _fence_file_lock(
        root,
        message="Another cutover-authority retirement owns the exclusive fence",
        error_code="cutover_authority_retirement_in_progress",
        error_type=KernelConflict,
    ) as path:
        yield path


def reject_retired_cutover_mutation(control_store_root: Path) -> None:
    root = control_store_root.resolve()
    tombstone = tombstone_path(root)
    if tombstone.is_file():
        raise ContractError(
            "Cutover authority has been retired",
            data={
                "first_failing_gate": "cutover_authority_tombstone",
                "error_code": "cutover_authority_retired",
                "tombstone_path": str(tombstone),
            },
        )
    if _MUTATOR_FENCE_HELD.get():
        return
    lock_path = root / HISTORY_DIR / FENCE_FILE
    if not lock_path.is_file():
        return
    with lock_path.open("r+b") as handle:
        try:
            _lock_byte(handle, blocking=False)
        except OSError as exc:
            raise ContractError(
                "Cutover authority retirement is in progress",
                data={
                    "first_failing_gate": "retirement_fence",
                    "error_code": "cutover_authority_retirement_in_progress",
                },
            ) from exc
        else:
            _unlock_byte(handle)


@contextmanager
def cutover_mutation_fence(control_store_root: Path) -> Iterator[None]:
    """Hold the retirement fence for one complete old-command mutation."""

    root = control_store_root.resolve()
    if not root.is_dir():
        yield
        return
    with _fence_file_lock(
        root,
        message="Cutover authority retirement is in progress",
        error_code="cutover_authority_retirement_in_progress",
        error_type=ContractError,
    ):
        token = _MUTATOR_FENCE_HELD.set(True)
        try:
            reject_retired_cutover_mutation(root)
            yield
        finally:
            _MUTATOR_FENCE_HELD.reset(token)


def _resolve_project_path(project_root: Path, raw: Any, field: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ContractError(
            f"Workflow project configuration field {field} is invalid",
            data={
                "first_failing_gate": "project_configuration",
                "error_code": "workflow_project_configuration_invalid",
            },
        )
    path = Path(raw)
    resolved = path.resolve() if path.is_absolute() else (project_root / path).resolve()
    if not resolved.is_relative_to(project_root):
        raise ContractError(
            f"Workflow project configuration field {field} escapes the project",
            data={
                "first_failing_gate": "project_configuration",
                "error_code": "workflow_project_configuration_path_escape",
            },
        )
    return resolved


class CutoverAuthorityRetirement:
    """Retire every machine-local release-cutover authority as one lifecycle."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.contracts = ContractRegistry(self.project_root)

    def retire(self, *, project_config: Path) -> dict[str, Any]:
        config = self._load_project_config(project_config)
        root = _resolve_project_path(
            self.project_root, config["control_store_root"], "control_store_root"
        )
        workspace = _resolve_project_path(
            self.project_root, config["workspace_root"], "workspace_root"
        )
        if root != workspace:
            self._reject(
                "Retired cutover authority and the live Control Store must share the configured workspace root",
                "project_configuration",
                "workflow_project_configuration_inconsistent",
            )
        profile_path = _resolve_project_path(
            self.project_root, config["release_profile"], "release_profile"
        )
        profile = ReleaseMaintenance(self.project_root)._validate_profile(profile_path)

        with _exclusive_retirement_fence(root):
            committed = tombstone_path(root)
            if committed.is_file():
                return self._validate_committed(
                    root=root, profile=profile, profile_path=profile_path
                )

            health = ControlStore(root, self.contracts).check()
            migration_id = f"{profile['release_id']}-cutover-retirement"
            bundle = root / HISTORY_DIR / "retired-cutover-authority" / migration_id
            record_path = bundle / "retirement-record.json"
            original = bundle / "original"
            try:
                if record_path.is_file():
                    record = self._load_prepared_record(
                        record_path=record_path,
                        profile=profile,
                        profile_path=profile_path,
                    )
                else:
                    inventory, dispositions, limitations = self._inventory(root)
                    self._validate_capabilities(profile, inventory)
                    record = {
                        "schema_name": "cutover-authority-retirement-record",
                        "schema_version": "1.0.0",
                        "migration_id": migration_id,
                        "state": "PREPARED",
                        "profile": {
                            "path": str(profile_path),
                            "release_id": profile["release_id"],
                            "contract_compatibility": profile["contract_compatibility"],
                        },
                        "inventory": inventory,
                        "dispositions": dispositions,
                        "historical_evidence": limitations,
                        "live_control_store": {
                            "path": str(health.path),
                            "status": health.status,
                            "schema_version": health.schema_version,
                        },
                        "prepared_at": self._now(),
                    }
                    bundle.mkdir(parents=True, exist_ok=True)
                    write_json_atomic(record_path, record)

                self._move_inventory(record, original)
                self._validate_archive(record, original)
                self._require_active_paths_absent(record)
                if record["state"] == "PREPARED":
                    record["state"] = "RETIRED"
                    record["retired_at"] = self._now()
                    write_json_atomic(record_path, record)
                elif not isinstance(record.get("retired_at"), str):
                    self._reject(
                        "Retired migration record has no completion time",
                        "retirement_resume",
                        "cutover_retirement_conflict",
                    )
                self._make_read_only(bundle)
                tombstone = {
                    "schema_name": "cutover-authority-tombstone",
                    "schema_version": "1.0.0",
                    "migration_id": migration_id,
                    "state": "RETIRED",
                    "release_id": profile["release_id"],
                    "contract_compatibility": profile["contract_compatibility"],
                    "profile_path": str(profile_path),
                    "audit_bundle_path": str(bundle),
                    "capabilities_retired": sorted(
                        name
                        for name, state in profile["capabilities"].items()
                        if state == "active"
                    ),
                    "disposition_counts": self._disposition_counts(record),
                    "historical_limitation_count": sum(
                        item["availability"] == "unavailable_at_migration"
                        for item in record["historical_evidence"]
                    ),
                    "retired_at": record["retired_at"],
                }
                write_json_atomic(committed, tombstone)
                return {
                    "tombstone_path": str(committed),
                    "audit_bundle_path": str(bundle),
                    "retirement_record_path": str(record_path),
                    "migration_id": migration_id,
                    "state": "RETIRED",
                    "idempotent": False,
                    "live_control_store_unchanged": True,
                }
            except (ContractError, KernelConflict):
                raise
            except Exception as exc:
                self._write_manual_brief(bundle, exc)
                raise KernelConflict(
                    "Retired cutover authority requires manual migration",
                    data={
                        "first_failing_gate": "retired_state_inventory",
                        "error_code": "cutover_authority_manual_migration_required",
                        "manual_migration_brief": str(bundle / "manual-migration-brief.md"),
                    },
                ) from exc

    def _load_project_config(self, path: Path) -> dict[str, Any]:
        resolved = path.resolve()
        if not resolved.is_relative_to(self.project_root):
            self._reject(
                "Workflow project configuration escapes the project",
                "project_configuration",
                "workflow_project_configuration_path_escape",
            )
        try:
            value = read_json(resolved)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            self._reject(
                f"Workflow project configuration is unavailable or malformed: {exc}",
                "project_configuration",
                "workflow_project_configuration_invalid",
            )
        if (
            not isinstance(value, dict)
            or value.get("schema_name") != "workflow-project-config"
            or value.get("schema_version") != "1.0.0"
            or not all(
                key in value
                for key in ("workspace_root", "control_store_root", "release_profile")
            )
        ):
            self._reject(
                "Workflow project configuration is incompatible",
                "project_configuration",
                "workflow_project_configuration_invalid",
            )
        return value

    def _inventory(
        self, root: Path
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, str]]]:
        inventory: list[dict[str, Any]] = []
        dispositions: list[dict[str, Any]] = []
        evidence_paths: set[str] = set()
        database_rows: dict[str, list[dict[str, Any]]] = {}

        for relative in _JSON_SURFACES:
            path = root / relative
            item: dict[str, Any] = {
                "relative_path": relative.as_posix(),
                "kind": "json",
                "present": path.is_file(),
            }
            if path.is_file():
                value = read_json(path)
                if not isinstance(value, dict):
                    raise ValueError(f"retired JSON is not an object: {relative}")
                item["sha256"] = sha256_file(path)
                item["schema_name"] = value.get("schema_name")
                self._collect_evidence_paths(value, evidence_paths)
            inventory.append(item)

        for relative, required_tables in _DATABASES.items():
            path = root / relative
            family = [relative]
            family.extend(
                Path(relative.as_posix() + suffix)
                for suffix in _SIDECARS
                if (root / Path(relative.as_posix() + suffix)).is_file()
            )
            database_item: dict[str, Any] = {
                "relative_path": relative.as_posix(),
                "kind": "sqlite",
                "present": path.is_file(),
                "family": [part.as_posix() for part in family],
            }
            if path.is_file():
                rows, tables = self._read_database(path)
                missing = sorted(required_tables - tables)
                if missing:
                    raise ValueError(
                        f"retired database {relative} lacks tables: {missing}"
                    )
                database_item["tables"] = sorted(tables)
                database_rows[relative.as_posix()] = rows
                for row in rows:
                    self._collect_evidence_paths(row, evidence_paths)
                    disposition = self._classify_row(relative, row)
                    if disposition is not None:
                        dispositions.append(disposition)
            elif len(family) > 1:
                raise ValueError(
                    f"orphaned SQLite sidecar lacks its main database: {relative}"
                )
            inventory.append(database_item)

        self._validate_projection_consistency(root, inventory, database_rows)
        limitations = [
            {
                "path": raw,
                "availability": (
                    "available_at_migration"
                    if Path(raw).is_file()
                    else "unavailable_at_migration"
                ),
            }
            for raw in sorted(evidence_paths)
        ]
        return inventory, dispositions, limitations

    @staticmethod
    def _read_database(path: Path) -> tuple[list[dict[str, Any]], set[str]]:
        uri = f"file:{path.as_posix()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        try:
            connection.row_factory = sqlite3.Row
            if str(connection.execute("PRAGMA quick_check").fetchone()[0]) != "ok":
                raise ValueError(f"retired database is corrupt: {path}")
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            rows: list[dict[str, Any]] = []
            for table in sorted(tables):
                if table.startswith("sqlite_"):
                    continue
                for row in connection.execute(f'SELECT * FROM "{table}"').fetchall():
                    value = dict(row)
                    value["_table"] = table
                    rows.append(value)
            return rows, tables
        finally:
            connection.close()

    @staticmethod
    def _collect_evidence_paths(value: Any, output: set[str]) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key.endswith("evidence_path") and isinstance(child, str) and child:
                    output.add(str(Path(child).resolve()))
                elif isinstance(child, str) and key.endswith("_json"):
                    try:
                        CutoverAuthorityRetirement._collect_evidence_paths(
                            json.loads(child), output
                        )
                    except json.JSONDecodeError:
                        continue
                else:
                    CutoverAuthorityRetirement._collect_evidence_paths(child, output)
        elif isinstance(value, list):
            for child in value:
                CutoverAuthorityRetirement._collect_evidence_paths(child, output)

    @staticmethod
    def _classify_row(relative: Path, row: dict[str, Any]) -> dict[str, Any] | None:
        table = str(row["_table"])
        identity = str(
            row.get("intent_id")
            or row.get("candidate_run_id")
            or row.get("platform")
            or row.get("singleton")
        )
        if table.endswith("intents"):
            if bool(row.get("cancelled", 0)):
                disposition = "archived_cancelled_publication"
            elif row.get("state") == "PREPARED":
                disposition = "abandoned_by_retirement"
            elif row.get("state") == "COMMITTED":
                disposition = "archived_committed_publication"
            else:
                raise ValueError(f"unknown retired intent state in {table}")
        elif table == "platform_cutover_candidates":
            state = row.get("state")
            if state == "CONFIRMED":
                disposition = "archived_confirmed_candidate"
            elif state in {"PREPARED", "INITIALIZING", "INITIALIZED", "PROVISIONAL"}:
                disposition = "abandoned_candidate_role"
            else:
                raise ValueError("unknown platform candidate state")
        elif table in {
            "gate_authority",
            "gate_policy_authority",
            "platform_cutover_authority",
            "batch_cutover_authority",
        }:
            disposition = "archived_completed_release_state"
        else:
            return None
        result = {
            "database": relative.as_posix(),
            "table": table,
            "identity": identity,
            "original_state": row.get("state"),
            "disposition": disposition,
        }
        if table == "platform_cutover_candidates":
            result["run_disposition"] = "retained_video_workflow_run"
            result["candidate_run_id"] = row.get("candidate_run_id")
        return result

    @staticmethod
    def _validate_projection_consistency(
        root: Path,
        inventory: list[dict[str, Any]],
        rows_by_database: dict[str, list[dict[str, Any]]],
    ) -> None:
        json_items = {
            item["relative_path"]: item for item in inventory if item["kind"] == "json"
        }
        bindings = (
            (
                "global-gate-control.sqlite3",
                "gate_authority",
                "gate_intents",
                "active_global_gate.json",
                None,
            ),
            (
                "global-gate-control.sqlite3",
                "gate_policy_authority",
                "gate_policy_refresh_intents",
                "active_global_gate_policy.json",
                None,
            ),
            (
                "platform-kernel-control.sqlite3",
                "platform_cutover_authority",
                ("platform_cutover_intents", "platform_authority_refresh_intents"),
                "platform-authorities/bilibili.json",
                "bilibili",
            ),
            (
                "platform-kernel-control.sqlite3",
                "platform_cutover_authority",
                ("platform_cutover_intents", "platform_authority_refresh_intents"),
                "platform-authorities/youtube.json",
                "youtube",
            ),
            (
                "batch-cutover-control.sqlite3",
                "batch_cutover_authority",
                ("batch_cutover_intents", "batch_authority_refresh_intents"),
                "active_batch.json",
                None,
            ),
        )
        for database, table, intent_tables, json_path, platform in bindings:
            authority_rows = [
                row
                for row in rows_by_database.get(database, [])
                if row["_table"] == table
                and (platform is None or row.get("platform") == platform)
            ]
            item = json_items[json_path]
            prepared_rows = [
                row
                for row in rows_by_database.get(database, [])
                if row["_table"]
                in (
                    {intent_tables}
                    if isinstance(intent_tables, str)
                    else set(intent_tables)
                )
                and row.get("state") == "PREPARED"
                and not bool(row.get("cancelled", 0))
                and (platform is None or row.get("platform") == platform)
            ]
            if authority_rows and not item["present"]:
                raise ValueError(f"committed retired authority lacks {json_path}")
            if not item["present"]:
                continue
            projection_path = root / json_path
            matches_committed = bool(authority_rows) and authority_rows[0].get(
                "authority_sha256"
            ) == sha256_file(projection_path)
            projection = read_json(projection_path)
            matches_prepared = any(
                isinstance(row.get("authority_json"), str)
                and projection == json.loads(str(row["authority_json"]))
                for row in prepared_rows
            )
            if not matches_committed and not matches_prepared:
                raise ValueError(f"retired projection conflicts with {database}: {json_path}")

    @staticmethod
    def _validate_capabilities(profile: dict[str, Any], inventory: list[dict[str, Any]]) -> None:
        present = {item["relative_path"] for item in inventory if item["present"]}
        required: set[str] = set()
        if "active_global_gate.json" in present or "global-gate-control.sqlite3" in present:
            required.add("global_gate")
        if "platform-authorities/bilibili.json" in present:
            required.add("bilibili")
        if "platform-authorities/youtube.json" in present:
            required.add("youtube")
        if "active_batch.json" in present or "batch-cutover-control.sqlite3" in present:
            required.add("batch")
        inactive = sorted(
            name for name in required if profile["capabilities"].get(name) != "active"
        )
        if inactive:
            raise ContractError(
                f"Workflow Release Profile does not activate retired capabilities: {inactive}",
                data={
                    "first_failing_gate": "capability_consistency",
                    "error_code": "cutover_retirement_capability_conflict",
                },
            )

    def _load_prepared_record(
        self, *, record_path: Path, profile: dict[str, Any], profile_path: Path
    ) -> dict[str, Any]:
        record = read_json(record_path)
        if (
            not isinstance(record, dict)
            or record.get("state") not in {"PREPARED", "RETIRED"}
            or record.get("profile", {}).get("path") != str(profile_path)
            or record.get("profile", {}).get("release_id") != profile["release_id"]
            or record.get("profile", {}).get("contract_compatibility")
            != profile["contract_compatibility"]
        ):
            self._reject(
                "Prepared cutover retirement conflicts with the selected Profile",
                "retirement_resume",
                "cutover_retirement_conflict",
            )
        return record

    @staticmethod
    def _move_inventory(record: dict[str, Any], original: Path) -> None:
        root = Path(record["live_control_store"]["path"]).parent.parent
        for item in record["inventory"]:
            paths = item.get("family", [item["relative_path"]])
            for raw in paths:
                source = root / raw
                target = original / raw
                if source.is_file() and target.exists():
                    raise ValueError(f"retired path exists in active and archive locations: {raw}")
                if source.is_file():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    source.replace(target)
                elif item["present"] and raw == item["relative_path"] and not target.is_file():
                    raise ValueError(f"inventoried retired path disappeared: {raw}")

    def _validate_archive(self, record: dict[str, Any], original: Path) -> None:
        for item in record["inventory"]:
            if not item["present"]:
                continue
            path = original / item["relative_path"]
            if not path.is_file():
                raise ValueError(f"archived retired path is absent: {item['relative_path']}")
            if item["kind"] == "json":
                if not isinstance(read_json(path), dict):
                    raise ValueError(f"archived JSON is invalid: {item['relative_path']}")
            else:
                self._read_database(path)

    @staticmethod
    def _require_active_paths_absent(record: dict[str, Any]) -> None:
        root = Path(record["live_control_store"]["path"]).parent.parent
        remaining = []
        for item in record["inventory"]:
            for raw in item.get("family", [item["relative_path"]]):
                if (root / raw).exists():
                    remaining.append(raw)
        if remaining:
            raise ValueError(f"retired active paths remain: {sorted(remaining)}")

    @staticmethod
    def _make_read_only(bundle: Path) -> None:
        for path in bundle.rglob("*"):
            if path.is_file():
                path.chmod(stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)

    def _validate_committed(
        self, *, root: Path, profile: dict[str, Any], profile_path: Path
    ) -> dict[str, Any]:
        path = tombstone_path(root)
        value = read_json(path)
        if (
            not isinstance(value, dict)
            or value.get("state") != "RETIRED"
            or value.get("release_id") != profile["release_id"]
            or value.get("contract_compatibility") != profile["contract_compatibility"]
            or value.get("profile_path") != str(profile_path)
        ):
            self._reject(
                "Cutover Authority Tombstone conflicts with the selected Profile",
                "retirement_resume",
                "cutover_retirement_conflict",
            )
        bundle = Path(str(value.get("audit_bundle_path", "")))
        record_path = bundle / "retirement-record.json"
        if not record_path.is_file():
            self._reject(
                "Cutover retirement audit bundle is unavailable",
                "archive_readability",
                "cutover_retirement_archive_unavailable",
            )
        try:
            record = read_json(record_path)
            active_paths = [
                raw
                for item in record["inventory"]
                for raw in item.get("family", [item["relative_path"]])
                if (root / raw).exists()
            ]
            if active_paths:
                raise KernelConflict(
                    "Retired cutover authority was recreated after Tombstone publication",
                    data={
                        "first_failing_gate": "retirement_resume",
                        "error_code": "retired_authority_resurrected",
                        "active_paths": sorted(active_paths),
                    },
                )
            self._validate_archive(record, bundle / "original")
        except KernelConflict:
            raise
        except (OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
            raise KernelConflict(
                "Cutover retirement audit bundle cannot be validated",
                data={
                    "first_failing_gate": "archive_readability",
                    "error_code": "cutover_retirement_archive_unreadable",
                },
            ) from exc
        return {
            "tombstone_path": str(path),
            "audit_bundle_path": str(bundle),
            "retirement_record_path": str(record_path),
            "migration_id": value["migration_id"],
            "state": "RETIRED",
            "idempotent": True,
            "live_control_store_unchanged": True,
        }

    @staticmethod
    def _disposition_counts(record: dict[str, Any]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in record["dispositions"]:
            disposition = item["disposition"]
            counts[disposition] = counts.get(disposition, 0) + 1
        return counts

    @staticmethod
    def _write_manual_brief(bundle: Path, exc: Exception) -> None:
        bundle.mkdir(parents=True, exist_ok=True)
        brief = bundle / "manual-migration-brief.md"
        brief.write_text(
            "# Manual cutover-authority migration required\n\n"
            f"Automatic retirement stopped before Tombstone publication.\n\nReason: {exc}\n",
            encoding="utf-8",
        )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _reject(message: str, gate: str, code: str) -> None:
        raise ContractError(
            message, data={"first_failing_gate": gate, "error_code": code}
        )


__all__ = [
    "CutoverAuthorityRetirement",
    "cutover_mutation_fence",
    "reject_retired_cutover_mutation",
    "tombstone_path",
]
