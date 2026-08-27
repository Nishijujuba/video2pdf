from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from .errors import (
    ContractError,
    ControlStoreUnavailable,
    KernelConflict,
    KernelError,
)
from .evidence import git_output
from .global_gate import GlobalGatePublisher
from .platform_kernel import BilibiliPlatformCutoverPublisher
from .utils import canonical_json_bytes, read_json, sha256_file, write_json_atomic

BATCH_CUTOVER_DB = "batch-cutover-control.sqlite3"
BATCH_AUTHORITY_FILE = "active_batch.json"
BATCH_CUTOVER_SCHEMA_VERSION = 1
ACTIVATION_FAULT_POINTS = frozenset(
    {"after_intent", "after_authority_write", "after_control_commit"}
)


class BatchCutoverFault(KernelError):
    classification = "injected_batch_cutover_fault"
    exit_code = 60

    def __init__(self, fault_point: str) -> None:
        super().__init__(
            f"injected Batch cutover fault at {fault_point}",
            data={"fault_point": fault_point},
        )


def _fingerprint(value: dict[str, Any], field: str) -> str:
    payload = dict(value)
    payload.pop(field, None)
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _validate_post_publication(*, evidence_path: Path, project_root: Path) -> str:
    validator_path = project_root / "scripts" / "validate_slice_exit_evidence.py"
    try:
        head_before = git_output(project_root, "rev-parse", "HEAD")
        spec = importlib.util.spec_from_file_location(
            "video2pdf_batch_exit_evidence_validator", validator_path
        )
        if spec is None or spec.loader is None:
            raise RuntimeError("validator module cannot be loaded")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception as exc:
        raise ContractError(
            "Batch cutover requires current post-publication Slice 14 Exit Evidence",
            data={
                "first_failing_gate": "exit_evidence_lineage",
                "error_code": "batch_exit_evidence_lineage_invalid",
            },
        ) from exc
    evidence = read_json(evidence_path)
    if evidence.get("slice") != {
        "number": 14,
        "name": "batch-projection-cutover",
    }:
        raise ContractError(
            "Batch cutover requires Slice 14 Exit Evidence",
            data={
                "first_failing_gate": "exit_evidence_identity",
                "error_code": "batch_exit_evidence_slice_invalid",
            },
        )
    try:
        module.validate_manifest(
            evidence_path.resolve(), schema_only=False, pre_publication=False
        )
        head_after = git_output(project_root, "rev-parse", "HEAD")
    except Exception as exc:
        raise ContractError(
            "Batch cutover requires current post-publication Slice 14 Exit Evidence",
            data={
                "first_failing_gate": "exit_evidence_lineage",
                "error_code": "batch_exit_evidence_lineage_invalid",
            },
        ) from exc
    if head_before != head_after:
        raise ContractError(
            "Batch cutover evidence publication changed during validation",
            data={
                "first_failing_gate": "implementation_currentness",
                "error_code": "batch_evidence_publication_not_current",
            },
        )
    return head_after


def _validate_activated_at(value: str) -> None:
    if not isinstance(value, str) or not value:
        raise ContractError("Batch cutover activated_at must be an ISO 8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError(
            "Batch cutover activated_at must be an ISO 8601 timestamp"
        ) from exc
    if parsed.tzinfo is None:
        raise ContractError(
            "Batch cutover activated_at must include a timezone offset"
        )


def _validate_refreshed_at(value: str) -> None:
    if not isinstance(value, str) or not value:
        raise ContractError(
            "Batch authority refreshed_at must be an ISO 8601 timestamp"
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError(
            "Batch authority refreshed_at must be an ISO 8601 timestamp"
        ) from exc
    if parsed.tzinfo is None:
        raise ContractError(
            "Batch authority refreshed_at must include a timezone offset"
        )


class BatchCutoverPublisher:
    """Crash-safe publication for the singleton new-Batch runtime authority."""

    def __init__(self, *, project_root: Path | None = None) -> None:
        self.project_root = (
            project_root.resolve()
            if project_root is not None
            else Path(__file__).resolve().parents[2]
        )

    @staticmethod
    @contextmanager
    def _connect(
        root: Path, *, initialize: bool = False
    ) -> Iterator[sqlite3.Connection]:
        from .cutover_retirement import reject_retired_cutover_mutation

        reject_retired_cutover_mutation(root)
        if not root.is_dir():
            raise ControlStoreUnavailable(
                "Batch cutover control-store root is unavailable"
            )
        connection: sqlite3.Connection | None = None
        try:
            database_path = root / BATCH_CUTOVER_DB
            database_target = (
                database_path
                if initialize
                else f"{database_path.resolve().as_uri()}?mode=ro"
            )
            connection = sqlite3.connect(
                database_target,
                timeout=0.05,
                isolation_level=None,
                uri=not initialize,
            )
            connection.row_factory = sqlite3.Row
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ControlStoreUnavailable(
                    "Batch cutover control store is corrupt"
                )
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            accepted_versions = (
                {0, BATCH_CUTOVER_SCHEMA_VERSION}
                if initialize
                else {BATCH_CUTOVER_SCHEMA_VERSION}
            )
            if version not in accepted_versions:
                raise ControlStoreUnavailable(
                    "Batch cutover control store schema is incompatible"
                )
            if initialize:
                connection.execute(
                    f"PRAGMA user_version={BATCH_CUTOVER_SCHEMA_VERSION}"
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS batch_cutover_authority ("
                    "singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
                    "generation INTEGER NOT NULL, evidence_sha256 TEXT NOT NULL, "
                    "authority_sha256 TEXT NOT NULL, publication_commit TEXT NOT NULL)"
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS batch_cutover_intents ("
                    "intent_id TEXT PRIMARY KEY, expected_generation INTEGER NOT NULL, "
                    "evidence_sha256 TEXT NOT NULL, state TEXT NOT NULL "
                    "CHECK(state IN ('PREPARED','COMMITTED')), "
                    "authority_sha256 TEXT NOT NULL, authority_json TEXT NOT NULL, "
                    "evidence_path TEXT NOT NULL, project_root TEXT NOT NULL, "
                    "publication_commit TEXT NOT NULL)"
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS batch_authority_refresh_intents ("
                    "intent_id TEXT PRIMARY KEY, expected_generation INTEGER NOT NULL, "
                    "expected_authority_sha256 TEXT NOT NULL, "
                    "evidence_sha256 TEXT NOT NULL, state TEXT NOT NULL "
                    "CHECK(state IN ('PREPARED','COMMITTED')), "
                    "authority_json TEXT NOT NULL, prior_authority_json TEXT, "
                    "cancelled INTEGER NOT NULL DEFAULT 0 CHECK(cancelled IN (0,1)), "
                    "cancellation_reason TEXT, evidence_path TEXT NOT NULL, "
                    "project_root TEXT NOT NULL, publication_commit TEXT NOT NULL)"
                )
                refresh_columns = {
                    str(column["name"])
                    for column in connection.execute(
                        "PRAGMA table_info(batch_authority_refresh_intents)"
                    ).fetchall()
                }
                if "prior_authority_json" not in refresh_columns:
                    connection.execute(
                        "ALTER TABLE batch_authority_refresh_intents "
                        "ADD COLUMN prior_authority_json TEXT"
                    )
                if "cancelled" not in refresh_columns:
                    connection.execute(
                        "ALTER TABLE batch_authority_refresh_intents "
                        "ADD COLUMN cancelled INTEGER NOT NULL DEFAULT 0 "
                        "CHECK(cancelled IN (0,1))"
                    )
                if "cancellation_reason" not in refresh_columns:
                    connection.execute(
                        "ALTER TABLE batch_authority_refresh_intents "
                        "ADD COLUMN cancellation_reason TEXT"
                    )
            yield connection
        except ControlStoreUnavailable:
            raise
        except (sqlite3.Error, OSError) as exc:
            raise ControlStoreUnavailable(
                "Batch cutover control store cannot be opened"
            ) from exc
        finally:
            if connection is not None:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                connection.close()

    @staticmethod
    def _normalize_global_binding(value: dict[str, Any]) -> dict[str, Any]:
        return {
            "authority_path": str(value["path"]),
            "authority_sha256": str(value["file_sha256"]),
            "generation": int(value["generation"]),
        }

    @staticmethod
    def _normalize_platform_binding(value: dict[str, Any]) -> dict[str, Any]:
        return {
            "platform": str(value["platform"]),
            "authority_path": str(value["authority_path"]),
            "authority_sha256": str(value["authority_sha256"]),
            "generation": int(value["generation"]),
        }

    def _require_prerequisites(self, root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
        global_current = GlobalGatePublisher(
            project_root=self.project_root
        ).require_current(control_store_root=root)
        publisher = BilibiliPlatformCutoverPublisher()
        platforms = {
            platform: self._normalize_platform_binding(
                publisher.require_current(
                    platform=platform,
                    control_store_root=root,
                )
            )
            for platform in ("bilibili", "youtube")
        }
        return self._normalize_global_binding(global_current), platforms

    def activate(
        self,
        *,
        control_store_root: Path,
        exit_evidence: Path,
        activated_at: str,
        fault_point: str | None = None,
    ) -> dict[str, Any]:
        if fault_point is not None and fault_point not in ACTIVATION_FAULT_POINTS:
            raise ContractError(f"unsupported Batch cutover fault point: {fault_point}")
        _validate_activated_at(activated_at)
        root = control_store_root.resolve()
        evidence_path = exit_evidence.resolve()
        if not evidence_path.is_file():
            raise ContractError("Batch cutover Exit Evidence is unavailable")
        publication_commit = _validate_post_publication(
            evidence_path=evidence_path,
            project_root=self.project_root,
        )
        global_binding, platform_bindings = self._require_prerequisites(root)
        evidence_sha256 = sha256_file(evidence_path)
        authority_path = root / BATCH_AUTHORITY_FILE
        authority = {
            "schema_name": "batch-cutover-authority",
            "schema_version": "1.0.0",
            "generation": 1,
            "authority_status": "active_batch",
            "new_batch_authority": "batch_projection_v1",
            "legacy_batch_authority": "legacy_preserved",
            "global_gate_authority": "unchanged",
            "platform_kernel_authority": "unchanged",
            "global_gate_binding": global_binding,
            "platform_authority_bindings": platform_bindings,
            "exit_evidence_path": str(evidence_path),
            "exit_evidence_sha256": evidence_sha256,
            "publication_commit": publication_commit,
            "activated_at": activated_at,
        }
        authority["authority_sha256"] = _fingerprint(
            authority, "authority_sha256"
        )
        authority_json = canonical_json_bytes(authority).decode("utf-8")
        intent_id = hashlib.sha256(
            ("batch\0" + evidence_sha256).encode("utf-8")
        ).hexdigest()

        with self._connect(root, initialize=True) as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT * FROM batch_cutover_authority WHERE singleton=1"
            ).fetchone()
            pending = int(
                connection.execute(
                    "SELECT COUNT(*) FROM batch_cutover_intents "
                    "WHERE state='PREPARED'"
                ).fetchone()[0]
            )
            pending += int(
                connection.execute(
                    "SELECT COUNT(*) FROM batch_authority_refresh_intents "
                    "WHERE state='PREPARED' AND cancelled=0"
                ).fetchone()[0]
            )
            if current is not None:
                if pending:
                    connection.execute("ROLLBACK")
                    raise KernelConflict(
                        "Interrupted Batch cutover publication requires reconciliation"
                    )
                if current["evidence_sha256"] != evidence_sha256:
                    connection.execute("ROLLBACK")
                    raise KernelConflict(
                        "A different Batch cutover authority already won the activation fence"
                    )
                if (
                    not authority_path.is_file()
                    or sha256_file(authority_path) != current["authority_sha256"]
                ):
                    connection.execute("ROLLBACK")
                    raise KernelConflict("Committed Batch cutover authority is stale")
                connection.execute("COMMIT")
                result = self.require_current(control_store_root=root)
                return {**result, "idempotent": True}
            if pending:
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    "Interrupted Batch cutover publication requires reconciliation"
                )
            connection.execute(
                "INSERT INTO batch_cutover_intents("
                "intent_id,expected_generation,evidence_sha256,state,"
                "authority_sha256,authority_json,evidence_path,project_root,"
                "publication_commit) VALUES(?,?,?,'PREPARED',?,?,?,?,?)",
                (
                    intent_id,
                    0,
                    evidence_sha256,
                    authority["authority_sha256"],
                    authority_json,
                    str(evidence_path),
                    str(self.project_root),
                    publication_commit,
                ),
            )
            connection.execute("COMMIT")

        if fault_point == "after_intent":
            raise BatchCutoverFault(fault_point)
        if (
            _validate_post_publication(
                evidence_path=evidence_path,
                project_root=self.project_root,
            )
            != publication_commit
        ):
            raise ContractError("Batch cutover publication commit changed")
        write_json_atomic(authority_path, authority)
        if fault_point == "after_authority_write":
            raise BatchCutoverFault(fault_point)
        file_sha256 = sha256_file(authority_path)
        if (
            _validate_post_publication(
                evidence_path=evidence_path,
                project_root=self.project_root,
            )
            != publication_commit
        ):
            raise ContractError("Batch cutover publication commit changed")
        with self._connect(root, initialize=True) as connection:
            connection.execute("BEGIN IMMEDIATE")
            intent = connection.execute(
                "SELECT * FROM batch_cutover_intents WHERE intent_id=? "
                "AND state='PREPARED'",
                (intent_id,),
            ).fetchone()
            current = connection.execute(
                "SELECT * FROM batch_cutover_authority WHERE singleton=1"
            ).fetchone()
            if intent is None or current is not None:
                connection.execute("ROLLBACK")
                raise KernelConflict("Batch cutover activation fence was lost")
            connection.execute(
                "INSERT INTO batch_cutover_authority("
                "singleton,generation,evidence_sha256,authority_sha256,"
                "publication_commit) VALUES(1,1,?,?,?)",
                (evidence_sha256, file_sha256, publication_commit),
            )
            connection.execute(
                "UPDATE batch_cutover_intents SET state='COMMITTED',"
                "authority_sha256=? WHERE intent_id=?",
                (file_sha256, intent_id),
            )
            connection.execute("COMMIT")
        if fault_point == "after_control_commit":
            raise BatchCutoverFault(fault_point)
        return {
            "authority_path": str(authority_path),
            "authority_sha256": file_sha256,
            "generation": 1,
            "current": True,
            "idempotent": False,
        }

    def refresh_authority(
        self,
        *,
        control_store_root: Path,
        exit_evidence: Path,
        expected_generation: int,
        refreshed_at: str,
        fault_point: str | None = None,
    ) -> dict[str, Any]:
        """Advance active Batch authority to a newer evidence generation."""

        if (
            isinstance(expected_generation, bool)
            or not isinstance(expected_generation, int)
            or expected_generation < 1
        ):
            raise ContractError(
                "Batch authority refresh expected_generation must be at least 1"
            )
        if fault_point is not None and fault_point not in ACTIVATION_FAULT_POINTS:
            raise ContractError(
                f"unsupported Batch authority refresh fault point: {fault_point}"
            )
        _validate_refreshed_at(refreshed_at)
        root = control_store_root.resolve()
        evidence_path = exit_evidence.resolve()
        if not evidence_path.is_file():
            raise ContractError("Batch authority refresh Exit Evidence is unavailable")
        publication_commit = _validate_post_publication(
            evidence_path=evidence_path,
            project_root=self.project_root,
        )
        evidence_sha256 = sha256_file(evidence_path)
        global_binding, platform_bindings = self._require_prerequisites(root)
        authority_path = root / BATCH_AUTHORITY_FILE

        with self._connect(root, initialize=True) as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT * FROM batch_cutover_authority WHERE singleton=1"
            ).fetchone()
            pending = int(
                connection.execute(
                    "SELECT COUNT(*) FROM batch_authority_refresh_intents "
                    "WHERE state='PREPARED' AND cancelled=0"
                ).fetchone()[0]
            )
            committed = connection.execute(
                "SELECT authority_json FROM batch_authority_refresh_intents "
                "WHERE expected_generation=? AND evidence_sha256=? "
                "AND state='COMMITTED'",
                (expected_generation, evidence_sha256),
            ).fetchone()
            if current is None or not authority_path.is_file():
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    "Batch authority refresh requires a current active authority"
                )
            if pending:
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    "Interrupted Batch authority refresh requires reconciliation"
                )
            if sha256_file(authority_path) != current["authority_sha256"]:
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    "Batch authority refresh requires current authority bytes"
                )
            try:
                current_authority = read_json(authority_path)
                if not isinstance(current_authority, dict):
                    raise TypeError("Batch authority must be an object")
                current_conflicts = (
                    current_authority.get("schema_name")
                    != "batch-cutover-authority"
                    or current_authority.get("schema_version") != "1.0.0"
                    or current_authority.get("generation") != current["generation"]
                    or current_authority.get("authority_status") != "active_batch"
                    or current_authority.get("authority_sha256")
                    != _fingerprint(current_authority, "authority_sha256")
                    or current_authority.get("exit_evidence_sha256")
                    != current["evidence_sha256"]
                    or current_authority.get("publication_commit")
                    != current["publication_commit"]
                )
            except (OSError, UnicodeError, ValueError, TypeError) as exc:
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    "Batch authority refresh found conflicting authority bytes"
                ) from exc
            if current_conflicts:
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    "Batch authority refresh found conflicting authority bytes"
                )
            prerequisites_current = (
                current_authority.get("global_gate_binding") == global_binding
                and current_authority.get("platform_authority_bindings")
                == platform_bindings
            )
            if (
                committed is not None
                and int(current["generation"]) == expected_generation + 1
                and current["evidence_sha256"] == evidence_sha256
                and current_authority == json.loads(committed["authority_json"])
            ):
                if not prerequisites_current:
                    connection.execute("ROLLBACK")
                    raise KernelConflict(
                        "Batch authority refresh replay found prerequisite drift"
                    )
                connection.execute("COMMIT")
                return {
                    "authority_path": str(authority_path),
                    "authority_sha256": str(current["authority_sha256"]),
                    "generation": int(current["generation"]),
                    "current": True,
                    "idempotent": True,
                }
            if int(current["generation"]) != expected_generation:
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    "Batch authority refresh expected generation is stale",
                    data={
                        "first_failing_gate": "batch_cutover_authority",
                        "error_code": "batch_authority_refresh_fenced",
                    },
                )
            if current["evidence_sha256"] == evidence_sha256:
                if not prerequisites_current:
                    connection.execute("ROLLBACK")
                    raise KernelConflict(
                        "Batch authority refresh requires new evidence after "
                        "prerequisite drift"
                    )
                connection.execute("COMMIT")
                return {
                    "authority_path": str(authority_path),
                    "authority_sha256": str(current["authority_sha256"]),
                    "generation": int(current["generation"]),
                    "current": True,
                    "idempotent": True,
                }
            generation = expected_generation + 1
            authority = dict(current_authority)
            authority.update(
                {
                    "generation": generation,
                    "global_gate_binding": global_binding,
                    "platform_authority_bindings": platform_bindings,
                    "exit_evidence_path": str(evidence_path),
                    "exit_evidence_sha256": evidence_sha256,
                    "publication_commit": publication_commit,
                    "refreshed_at": refreshed_at,
                }
            )
            authority["authority_sha256"] = _fingerprint(
                authority, "authority_sha256"
            )
            authority_json = canonical_json_bytes(authority).decode("utf-8")
            prior_authority_json = canonical_json_bytes(current_authority).decode(
                "utf-8"
            )
            intent_id = hashlib.sha256(
                (
                    "batch-refresh\0"
                    + str(generation)
                    + "\0"
                    + evidence_sha256
                    + "\0"
                    + authority["authority_sha256"]
                ).encode("utf-8")
            ).hexdigest()
            connection.execute(
                "INSERT INTO batch_authority_refresh_intents("
                "intent_id,expected_generation,expected_authority_sha256,"
                "evidence_sha256,state,authority_json,prior_authority_json,"
                "evidence_path,project_root,publication_commit) "
                "VALUES(?,?,?,?,'PREPARED',?,?,?,?,?)",
                (
                    intent_id,
                    expected_generation,
                    current["authority_sha256"],
                    evidence_sha256,
                    authority_json,
                    prior_authority_json,
                    str(evidence_path),
                    str(self.project_root),
                    publication_commit,
                ),
            )
            connection.execute("COMMIT")

        if fault_point == "after_intent":
            raise BatchCutoverFault(fault_point)
        if not evidence_path.is_file() or sha256_file(evidence_path) != evidence_sha256:
            return self._cancel_authority_refresh(
                control_store_root=root,
                intent_id=intent_id,
                reason="evidence_drift",
            )
        try:
            current_publication_commit = _validate_post_publication(
                evidence_path=evidence_path,
                project_root=self.project_root,
            )
        except ContractError:
            return self._cancel_authority_refresh(
                control_store_root=root,
                intent_id=intent_id,
                reason="publication_validation_failed",
            )
        if current_publication_commit != publication_commit:
            return self._cancel_authority_refresh(
                control_store_root=root,
                intent_id=intent_id,
                reason="publication_commit_drift",
            )
        current_global, current_platforms = self._require_prerequisites(root)
        if (
            current_global != global_binding
            or current_platforms != platform_bindings
        ):
            return self._cancel_authority_refresh(
                control_store_root=root,
                intent_id=intent_id,
                reason="prerequisite_drift",
            )
        write_json_atomic(authority_path, authority)
        if fault_point == "after_authority_write":
            raise BatchCutoverFault(fault_point)
        file_sha256 = sha256_file(authority_path)
        if not evidence_path.is_file() or sha256_file(evidence_path) != evidence_sha256:
            return self._cancel_authority_refresh(
                control_store_root=root,
                intent_id=intent_id,
                reason="evidence_drift",
            )
        try:
            current_publication_commit = _validate_post_publication(
                evidence_path=evidence_path,
                project_root=self.project_root,
            )
        except ContractError:
            return self._cancel_authority_refresh(
                control_store_root=root,
                intent_id=intent_id,
                reason="publication_validation_failed",
            )
        if current_publication_commit != publication_commit:
            return self._cancel_authority_refresh(
                control_store_root=root,
                intent_id=intent_id,
                reason="publication_commit_drift",
            )
        current_global, current_platforms = self._require_prerequisites(root)
        if current_global != global_binding or current_platforms != platform_bindings:
            return self._cancel_authority_refresh(
                control_store_root=root,
                intent_id=intent_id,
                reason="prerequisite_drift",
            )
        with self._connect(root, initialize=True) as connection:
            connection.execute("BEGIN IMMEDIATE")
            intent = connection.execute(
                "SELECT * FROM batch_authority_refresh_intents "
                "WHERE intent_id=? AND state='PREPARED' AND cancelled=0",
                (intent_id,),
            ).fetchone()
            if intent is None:
                connection.execute("ROLLBACK")
                raise KernelConflict("Batch authority refresh lost its prepared intent")
            if (
                not evidence_path.is_file()
                or sha256_file(evidence_path) != evidence_sha256
            ):
                return self._cancel_prepared_authority_refresh(
                    connection=connection,
                    intent=intent,
                    current=connection.execute(
                        "SELECT * FROM batch_cutover_authority WHERE singleton=1"
                    ).fetchone(),
                    authority_path=authority_path,
                    reason="evidence_drift",
                    reconciled=False,
                )
            try:
                current_publication_commit = _validate_post_publication(
                    evidence_path=evidence_path,
                    project_root=self.project_root,
                )
            except ContractError:
                return self._cancel_prepared_authority_refresh(
                    connection=connection,
                    intent=intent,
                    current=connection.execute(
                        "SELECT * FROM batch_cutover_authority WHERE singleton=1"
                    ).fetchone(),
                    authority_path=authority_path,
                    reason="publication_validation_failed",
                    reconciled=False,
                )
            if current_publication_commit != publication_commit:
                return self._cancel_prepared_authority_refresh(
                    connection=connection,
                    intent=intent,
                    current=connection.execute(
                        "SELECT * FROM batch_cutover_authority WHERE singleton=1"
                    ).fetchone(),
                    authority_path=authority_path,
                    reason="publication_commit_drift",
                    reconciled=False,
                )
            current_global, current_platforms = self._require_prerequisites(root)
            if (
                current_global != global_binding
                or current_platforms != platform_bindings
            ):
                return self._cancel_prepared_authority_refresh(
                    connection=connection,
                    intent=intent,
                    current=connection.execute(
                        "SELECT * FROM batch_cutover_authority WHERE singleton=1"
                    ).fetchone(),
                    authority_path=authority_path,
                    reason="prerequisite_drift",
                    reconciled=False,
                )
            changed = connection.execute(
                "UPDATE batch_cutover_authority SET generation=?,evidence_sha256=?,"
                "authority_sha256=?,publication_commit=? "
                "WHERE singleton=1 AND generation=? AND authority_sha256=?",
                (
                    generation,
                    evidence_sha256,
                    file_sha256,
                    publication_commit,
                    expected_generation,
                    intent["expected_authority_sha256"],
                ),
            ).rowcount
            if changed != 1:
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    "Batch authority refresh lost its generation fence"
                )
            connection.execute(
                "UPDATE batch_authority_refresh_intents SET state='COMMITTED' "
                "WHERE intent_id=? AND state='PREPARED'",
                (intent_id,),
            )
            connection.execute("COMMIT")
        if fault_point == "after_control_commit":
            raise BatchCutoverFault(fault_point)
        return {
            "authority_path": str(authority_path),
            "authority_sha256": file_sha256,
            "generation": generation,
            "current": True,
            "idempotent": False,
        }

    def _cancel_prepared_authority_refresh(
        self,
        *,
        connection: sqlite3.Connection,
        intent: sqlite3.Row,
        current: sqlite3.Row | None,
        authority_path: Path,
        reason: str,
        reconciled: bool,
    ) -> dict[str, Any]:
        if (
            current is None
            or int(current["generation"]) != int(intent["expected_generation"])
            or current["authority_sha256"]
            != intent["expected_authority_sha256"]
        ):
            connection.execute("ROLLBACK")
            raise KernelConflict(
                "Batch authority refresh cancellation lost its generation fence"
            )
        prior_json = intent["prior_authority_json"]
        if prior_json is None:
            connection.execute("ROLLBACK")
            raise KernelConflict(
                "Batch authority refresh cancellation requires its prior authority"
            )
        try:
            authority = json.loads(str(intent["authority_json"]))
            prior_authority = json.loads(str(prior_json))
            if not isinstance(authority, dict) or not isinstance(
                prior_authority, dict
            ):
                raise TypeError("Batch authority intent values must be objects")
            prior_bytes = canonical_json_bytes(prior_authority)
            if hashlib.sha256(prior_bytes).hexdigest() != intent[
                "expected_authority_sha256"
            ]:
                raise ValueError("prior Batch authority SHA conflicts")
            existing_authority = (
                read_json(authority_path) if authority_path.is_file() else None
            )
            if existing_authority not in (prior_authority, authority, None):
                raise ValueError("Batch authority bytes belong to another writer")
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            connection.execute("ROLLBACK")
            raise KernelConflict(
                "Interrupted Batch authority refresh bytes conflict"
            ) from exc
        if existing_authority != prior_authority:
            write_json_atomic(authority_path, prior_authority)
        changed = connection.execute(
            "UPDATE batch_authority_refresh_intents "
            "SET cancelled=1,cancellation_reason=? "
            "WHERE intent_id=? AND state='PREPARED' AND cancelled=0",
            (reason, intent["intent_id"]),
        ).rowcount
        if changed != 1:
            connection.execute("ROLLBACK")
            raise KernelConflict(
                "Batch authority refresh cancellation lost its intent fence"
            )
        connection.execute("COMMIT")
        return {
            "authority_path": str(authority_path),
            "authority_sha256": str(intent["expected_authority_sha256"]),
            "generation": int(intent["expected_generation"]),
            "current": False,
            "reconciled": reconciled,
            "cancelled": True,
            "cancellation_reason": reason,
        }

    def _cancel_authority_refresh(
        self,
        *,
        control_store_root: Path,
        intent_id: str,
        reason: str,
    ) -> dict[str, Any]:
        root = control_store_root.resolve()
        with self._connect(root, initialize=True) as connection:
            connection.execute("BEGIN IMMEDIATE")
            intent = connection.execute(
                "SELECT * FROM batch_authority_refresh_intents "
                "WHERE intent_id=? AND state='PREPARED' AND cancelled=0",
                (intent_id,),
            ).fetchone()
            current = connection.execute(
                "SELECT * FROM batch_cutover_authority WHERE singleton=1"
            ).fetchone()
            if intent is None:
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    "Batch authority refresh cancellation lost its prepared intent"
                )
            return self._cancel_prepared_authority_refresh(
                connection=connection,
                intent=intent,
                current=current,
                authority_path=root / BATCH_AUTHORITY_FILE,
                reason=reason,
                reconciled=False,
            )

    def _reconcile_authority_refresh(
        self,
        *,
        control_store_root: Path,
        intent_id: str,
    ) -> dict[str, Any]:
        root = control_store_root.resolve()
        authority_path = root / BATCH_AUTHORITY_FILE
        with self._connect(root, initialize=True) as connection:
            connection.execute("BEGIN IMMEDIATE")
            intent = connection.execute(
                "SELECT * FROM batch_authority_refresh_intents "
                "WHERE intent_id=? AND state='PREPARED' AND cancelled=0",
                (intent_id,),
            ).fetchone()
            current = connection.execute(
                "SELECT * FROM batch_cutover_authority WHERE singleton=1"
            ).fetchone()
            if (
                intent is None
                or current is None
                or int(current["generation"])
                != int(intent["expected_generation"])
                or current["authority_sha256"]
                != intent["expected_authority_sha256"]
            ):
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    "Batch authority refresh lost its reconciliation fence"
                )
            evidence_path = Path(str(intent["evidence_path"]))
            project_root = Path(str(intent["project_root"]))
            if not evidence_path.is_file() or sha256_file(evidence_path) != intent[
                "evidence_sha256"
            ]:
                return self._cancel_prepared_authority_refresh(
                    connection=connection,
                    intent=intent,
                    current=current,
                    authority_path=authority_path,
                    reason="evidence_drift",
                    reconciled=True,
                )
            try:
                current_publication_commit = _validate_post_publication(
                    evidence_path=evidence_path,
                    project_root=project_root,
                )
            except ContractError:
                return self._cancel_prepared_authority_refresh(
                    connection=connection,
                    intent=intent,
                    current=current,
                    authority_path=authority_path,
                    reason="publication_validation_failed",
                    reconciled=True,
                )
            if current_publication_commit != intent["publication_commit"]:
                return self._cancel_prepared_authority_refresh(
                    connection=connection,
                    intent=intent,
                    current=current,
                    authority_path=authority_path,
                    reason="publication_commit_drift",
                    reconciled=True,
                )
            authority = json.loads(str(intent["authority_json"]))
            global_binding, platform_bindings = self._require_prerequisites(root)
            if (
                authority.get("global_gate_binding") != global_binding
                or authority.get("platform_authority_bindings")
                != platform_bindings
            ):
                return self._cancel_prepared_authority_refresh(
                    connection=connection,
                    intent=intent,
                    current=current,
                    authority_path=authority_path,
                    reason="prerequisite_drift",
                    reconciled=True,
                )
            existing_authority: dict[str, Any] | None = None
            if authority_path.is_file():
                existing_sha256 = sha256_file(authority_path)
                try:
                    existing_value = read_json(authority_path)
                    if not isinstance(existing_value, dict):
                        raise TypeError("Batch authority must be an object")
                    existing_authority = existing_value
                except (OSError, UnicodeError, ValueError, TypeError) as exc:
                    connection.execute("ROLLBACK")
                    raise KernelConflict(
                        "Interrupted Batch authority refresh bytes conflict"
                    ) from exc
                if (
                    existing_sha256 != intent["expected_authority_sha256"]
                    and existing_authority != authority
                ):
                    connection.execute("ROLLBACK")
                    raise KernelConflict(
                        "Interrupted Batch authority refresh bytes conflict"
                    )
            if existing_authority != authority:
                write_json_atomic(authority_path, authority)
            file_sha256 = sha256_file(authority_path)
            generation = int(intent["expected_generation"]) + 1
            changed = connection.execute(
                "UPDATE batch_cutover_authority SET generation=?,evidence_sha256=?,"
                "authority_sha256=?,publication_commit=? "
                "WHERE singleton=1 AND generation=? AND authority_sha256=?",
                (
                    generation,
                    intent["evidence_sha256"],
                    file_sha256,
                    intent["publication_commit"],
                    intent["expected_generation"],
                    intent["expected_authority_sha256"],
                ),
            ).rowcount
            if changed != 1:
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    "Batch authority refresh lost its generation fence"
                )
            connection.execute(
                "UPDATE batch_authority_refresh_intents SET state='COMMITTED' "
                "WHERE intent_id=? AND state='PREPARED'",
                (intent_id,),
            )
            connection.execute("COMMIT")
        result = self.require_current(control_store_root=root)
        return {**result, "reconciled": True}

    def require_current(self, *, control_store_root: Path) -> dict[str, Any]:
        root = control_store_root.resolve()
        authority_path = root / BATCH_AUTHORITY_FILE
        if not root.is_dir():
            raise ControlStoreUnavailable(
                "Batch cutover control-store root is unavailable"
            )
        if not (root / BATCH_CUTOVER_DB).is_file():
            raise KernelConflict(
                "Batch cutover authority is absent, stale, or incomplete",
                data={
                    "first_failing_gate": "batch_cutover_authority",
                    "error_code": "batch_cutover_authority_stale",
                },
            )
        with self._connect(root) as connection:
            current = connection.execute(
                "SELECT * FROM batch_cutover_authority WHERE singleton=1"
            ).fetchone()
            pending = int(
                connection.execute(
                    "SELECT COUNT(*) FROM batch_cutover_intents "
                    "WHERE state!='COMMITTED'"
                ).fetchone()[0]
            )
            refresh_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='batch_authority_refresh_intents'"
            ).fetchone()
            if refresh_table is not None:
                pending += int(
                    connection.execute(
                        "SELECT COUNT(*) FROM batch_authority_refresh_intents "
                        "WHERE state!='COMMITTED' AND cancelled=0"
                    ).fetchone()[0]
                )
        if (
            current is None
            or pending
            or not authority_path.is_file()
            or sha256_file(authority_path) != current["authority_sha256"]
        ):
            raise KernelConflict(
                "Batch cutover authority is absent, stale, or incomplete",
                data={
                    "first_failing_gate": "batch_cutover_authority",
                    "error_code": "batch_cutover_authority_stale",
                },
            )
        try:
            authority = read_json(authority_path)
            if not isinstance(authority, dict):
                raise TypeError("Batch cutover authority must be an object")
            _validate_activated_at(authority.get("activated_at"))
            if int(current["generation"]) > 1:
                _validate_refreshed_at(authority.get("refreshed_at"))
            evidence_path = Path(str(authority.get("exit_evidence_path", "")))
            content_conflicts = (
                authority.get("schema_name") != "batch-cutover-authority"
                or authority.get("schema_version") != "1.0.0"
                or authority.get("generation") != current["generation"]
                or authority.get("authority_status") != "active_batch"
                or authority.get("new_batch_authority") != "batch_projection_v1"
                or authority.get("legacy_batch_authority") != "legacy_preserved"
                or authority.get("global_gate_authority") != "unchanged"
                or authority.get("platform_kernel_authority") != "unchanged"
                or authority.get("exit_evidence_sha256")
                != current["evidence_sha256"]
                or authority.get("publication_commit")
                != current["publication_commit"]
                or authority.get("authority_sha256")
                != _fingerprint(authority, "authority_sha256")
                or not evidence_path.is_file()
                or sha256_file(evidence_path) != current["evidence_sha256"]
            )
        except (ContractError, OSError, UnicodeError, ValueError, TypeError) as exc:
            raise KernelConflict(
                "Batch cutover authority content conflicts with committed control state",
                data={
                    "first_failing_gate": "batch_cutover_authority",
                    "error_code": "batch_cutover_authority_conflict",
                },
            ) from exc
        if content_conflicts:
            raise KernelConflict(
                "Batch cutover authority content conflicts with committed control state",
                data={
                    "first_failing_gate": "batch_cutover_authority",
                    "error_code": "batch_cutover_authority_conflict",
                },
            )
        if (
            _validate_post_publication(
                evidence_path=evidence_path,
                project_root=self.project_root,
            )
            != current["publication_commit"]
        ):
            raise KernelConflict("Batch cutover evidence publication is stale")
        global_binding, platform_bindings = self._require_prerequisites(root)
        if (
            authority.get("global_gate_binding") != global_binding
            or authority.get("platform_authority_bindings") != platform_bindings
        ):
            raise KernelConflict(
                "Batch cutover prerequisite authority binding changed",
                data={
                    "first_failing_gate": "batch_cutover_prerequisites",
                    "error_code": "batch_cutover_prerequisite_stale",
                },
            )
        return {
            "authority_path": str(authority_path),
            "authority_sha256": str(current["authority_sha256"]),
            "exit_evidence_sha256": str(current["evidence_sha256"]),
            "generation": int(current["generation"]),
            "publication_commit": str(current["publication_commit"]),
            "global_gate_binding": global_binding,
            "platform_authority_bindings": platform_bindings,
            "current": True,
        }

    def reconcile(self, *, control_store_root: Path) -> dict[str, Any]:
        root = control_store_root.resolve()
        authority_path = root / BATCH_AUTHORITY_FILE
        with self._connect(root, initialize=True) as connection:
            refresh_pending = connection.execute(
                "SELECT * FROM batch_authority_refresh_intents "
                "WHERE state='PREPARED' AND cancelled=0"
            ).fetchall()
            current = connection.execute(
                "SELECT * FROM batch_cutover_authority WHERE singleton=1"
            ).fetchone()
            pending = connection.execute(
                "SELECT * FROM batch_cutover_intents WHERE state='PREPARED'"
            ).fetchall()
        if refresh_pending and pending:
            raise KernelConflict(
                "Batch cutover reconciliation is ambiguous across activation "
                "and refresh intents"
            )
        if len(refresh_pending) > 1:
            raise KernelConflict(
                "Multiple Batch authority refreshes require operator disposition"
            )
        if refresh_pending:
            return self._reconcile_authority_refresh(
                control_store_root=root,
                intent_id=str(refresh_pending[0]["intent_id"]),
            )
        if not pending:
            if current is None:
                raise KernelConflict(
                    "Batch cutover reconciliation requires one prepared intent"
                )
            result = self.require_current(control_store_root=root)
            return {**result, "reconciled": True}
        if len(pending) != 1:
            raise KernelConflict(
                "Multiple Batch cutover publications require operator disposition"
            )
        intent = pending[0]
        evidence_path = Path(str(intent["evidence_path"]))
        if (
            not evidence_path.is_file()
            or sha256_file(evidence_path) != intent["evidence_sha256"]
            or _validate_post_publication(
                evidence_path=evidence_path,
                project_root=Path(str(intent["project_root"])),
            )
            != intent["publication_commit"]
        ):
            raise KernelConflict("Interrupted Batch cutover Exit Evidence drifted")
        authority = json.loads(str(intent["authority_json"]))
        global_binding, platform_bindings = self._require_prerequisites(root)
        if (
            authority.get("global_gate_binding") != global_binding
            or authority.get("platform_authority_bindings") != platform_bindings
        ):
            raise KernelConflict(
                "Interrupted Batch cutover prerequisite authority changed"
            )
        if authority_path.is_file() and read_json(authority_path) != authority:
            raise KernelConflict(
                "Interrupted Batch cutover authority bytes conflict"
            )
        if not authority_path.is_file():
            write_json_atomic(authority_path, authority)
        file_sha256 = sha256_file(authority_path)
        with self._connect(root, initialize=True) as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT * FROM batch_cutover_authority WHERE singleton=1"
            ).fetchone()
            prepared = connection.execute(
                "SELECT * FROM batch_cutover_intents WHERE intent_id=? "
                "AND state='PREPARED'",
                (intent["intent_id"],),
            ).fetchone()
            if prepared is None:
                connection.execute("ROLLBACK")
                raise KernelConflict("Batch cutover reconciliation lost its intent")
            if current is None:
                connection.execute(
                    "INSERT INTO batch_cutover_authority("
                    "singleton,generation,evidence_sha256,authority_sha256,"
                    "publication_commit) VALUES(1,1,?,?,?)",
                    (
                        intent["evidence_sha256"],
                        file_sha256,
                        intent["publication_commit"],
                    ),
                )
            elif (
                current["evidence_sha256"] != intent["evidence_sha256"]
                or current["authority_sha256"] != file_sha256
            ):
                connection.execute("ROLLBACK")
                raise KernelConflict("Batch cutover reconciliation lost its fence")
            connection.execute(
                "UPDATE batch_cutover_intents SET state='COMMITTED',"
                "authority_sha256=? WHERE intent_id=?",
                (file_sha256, intent["intent_id"]),
            )
            connection.execute("COMMIT")
        result = self.require_current(control_store_root=root)
        return {**result, "reconciled": True}


__all__ = [
    "ACTIVATION_FAULT_POINTS",
    "BATCH_AUTHORITY_FILE",
    "BATCH_CUTOVER_DB",
    "BatchCutoverFault",
    "BatchCutoverPublisher",
]
