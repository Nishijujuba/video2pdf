from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any

from jsonschema import Draft202012Validator, ValidationError

from .acceptance_v2 import AcceptanceV2Provider
from .errors import (
    ContractError,
    ControlStoreUnavailable,
    KernelConflict,
    PlatformKernelFault,
)
from .evidence import EvidenceSupportError, git_output, sha256_git_blob
from .global_gate import GlobalGatePublisher
from .guarded_delivery import (
    require_current_kernel_guarded_decision,
    validate_acceptance_report,
    validate_delivery_guard_report,
)
from .utils import (
    canonical_json_bytes,
    read_json,
    require_safe_path_segment,
    sha256_file,
    write_json_atomic,
)


PLATFORM_KERNEL_DB = "platform-kernel-control.sqlite3"
PLATFORM_AUTHORITY_DIR = "platform-authorities"
SUPPORTED_PLATFORM = "bilibili"
EXPECTED_SLICE = {
    "number": 12,
    "name": "bilibili-platform-kernel-cutover",
}
EXPECTED_ACTIVATION_SCOPE = {
    "kind": "platform_kernel_cutover",
    "runtime_authority_change": True,
    "components_activated": ["bilibili_platform_kernel"],
    "platform": "bilibili",
    "global_gate_authority": "unchanged",
    "qualification_contract_sha256": (
        "927022a0bcf5f626f4b9275928dce9de201775523ab1bf4c0c9b6803f0012461"
    ),
}
ATOMIC_MEMBERS = frozenset(
    {
        "bilibili_adapter",
        "kernel_run_authority",
        "task_ownership",
        "delivery_contracts",
        "delivery_lifecycle",
        "acceptance_v2_binding",
        "delivery_guard_binding",
        "hooks",
        "bilibili_skill",
        "project_instructions",
        "validators",
        "tests",
        "activation_documentation",
        "guarded_delivery_evidence",
    }
)
ACTIVATION_FAULT_POINTS = frozenset(
    {"after_intent", "after_authority_write", "after_control_commit"}
)
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _require_formal_exit_evidence(evidence_path: Path) -> None:
    """Bind platform authority to the repository's post-publication validator."""

    validator_path = PROJECT_ROOT / "scripts" / "validate_slice_exit_evidence.py"
    try:
        spec = importlib.util.spec_from_file_location(
            "video2pdf_issue13_exit_evidence_validator", validator_path
        )
        if spec is None or spec.loader is None:
            raise RuntimeError("validator module cannot be loaded")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.validate_manifest(
            evidence_path.resolve(), schema_only=False, pre_publication=False
        )
    except Exception as exc:
        raise ContractError(
            "Bilibili Platform Kernel requires current post-publication Exit Evidence",
            data={
                "first_failing_gate": "exit_evidence_lineage",
                "error_code": "bilibili_exit_evidence_lineage_invalid",
            },
        ) from exc


def _fingerprint(value: dict[str, Any], field: str) -> str:
    payload = {key: item for key, item in value.items() if key != field}
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _evidence_path(binding: Any, *, label: str, allow_absolute: bool) -> Path:
    if not isinstance(binding, dict):
        raise ContractError(f"Bilibili cutover {label} binding is absent")
    raw_path = binding.get("path")
    expected_sha = binding.get("sha256")
    if not isinstance(raw_path, str) or not isinstance(expected_sha, str):
        raise ContractError(f"Bilibili cutover {label} binding is invalid")
    candidate = Path(raw_path)
    if candidate.is_absolute():
        if not allow_absolute:
            raise ContractError(f"Bilibili cutover {label} path must be project-relative")
        path = candidate.resolve()
    else:
        path = (PROJECT_ROOT / candidate).resolve()
    if not path.is_relative_to(PROJECT_ROOT) or not path.is_file():
        raise ContractError(f"Bilibili cutover {label} escapes trusted evidence storage")
    if sha256_file(path) != expected_sha:
        raise ContractError(f"Bilibili cutover {label} fingerprint is stale")
    return path


def _validate_guarded_delivery(value: dict[str, Any]) -> None:
    guarded = value.get("guarded_delivery_evidence")
    expected_roles = {
        "run_record",
        "source_manifest",
        "acceptance_report_v2",
        "delivery_guard_report",
        "video_delivery_target",
        "session_delivery_target",
        "delivery_task_index",
        "global_gate_authority",
        "final_pdf",
    }
    if not isinstance(guarded, dict):
        raise ContractError(
            "Bilibili cutover lacks collected guarded-delivery evidence",
            data={
                "first_failing_gate": "guarded_delivery_evidence",
                "error_code": "guarded_delivery_evidence_missing",
            },
        )
    artifacts = guarded.get("artifacts")
    if not isinstance(artifacts, list):
        artifacts = []
    manifest_artifacts = {
        item.get("role"): item for item in artifacts if isinstance(item, dict)
    }
    if (
        guarded.get("canonical_platform") != "bilibili"
        or guarded.get("delivery_stage") != "delivered"
        or set(manifest_artifacts) != expected_roles
    ):
        raise ContractError(
            "Bilibili guarded-delivery evidence is incomplete",
            data={
                "first_failing_gate": "guarded_delivery_evidence",
                "error_code": "guarded_delivery_evidence_invalid",
            },
        )
    try:
        collection_path = _evidence_path(
            guarded.get("collection"),
            label="guarded-delivery collection",
            allow_absolute=False,
        )
        collection = read_json(collection_path)
        if (
            collection.get("schema_name") != "issue13-exit-evidence-collection"
            or collection.get("run_id") != guarded.get("run_id")
            or collection.get("canonical_platform") != "bilibili"
            or collection.get("delivery_stage") != "delivered"
        ):
            raise ContractError("Bilibili guarded-delivery collection identity is invalid")
        collected_artifacts = collection.get("artifacts")
        if not isinstance(collected_artifacts, dict) or set(collected_artifacts) != expected_roles:
            raise ContractError("Bilibili guarded-delivery collection artifact set is invalid")
        resolved_artifacts: dict[str, Path] = {}
        for role in expected_roles:
            manifest_path = _evidence_path(
                manifest_artifacts[role], label=role, allow_absolute=False
            )
            collected_path = _evidence_path(
                collected_artifacts[role], label=role, allow_absolute=True
            )
            if (
                manifest_path != collected_path
                or manifest_artifacts[role]["sha256"]
                != collected_artifacts[role]["sha256"]
            ):
                raise ContractError(
                    f"Bilibili guarded-delivery {role} differs from its collection"
                )
            resolved_artifacts[role] = collected_path
        run_id = guarded.get("run_id")
        validate_acceptance_report(
            project_root=PROJECT_ROOT,
            report_path=resolved_artifacts["acceptance_report_v2"],
            run_id=str(run_id),
        )
        validate_delivery_guard_report(
            report_path=resolved_artifacts["delivery_guard_report"]
        )
        manifest_qualification = guarded.get("qualification_run")
        collected_qualification = collection.get("qualification_run")
        if not isinstance(manifest_qualification, dict) or not isinstance(
            collected_qualification, dict
        ):
            raise ContractError("Bilibili qualification evidence is absent")
        if manifest_qualification.get("run_id") != collected_qualification.get("run_id"):
            raise ContractError("Bilibili qualification Run identity differs")
        for manifest_key, collected_key in (
            ("command_record", "command_record"),
            ("terminal_status", "terminal_status"),
            ("exit_code", "exit_code_artifact"),
        ):
            manifest_path = _evidence_path(
                manifest_qualification.get(manifest_key),
                label=f"qualification {manifest_key}",
                allow_absolute=False,
            )
            collected_path = _evidence_path(
                collected_qualification.get(collected_key),
                label=f"qualification {manifest_key}",
                allow_absolute=True,
            )
            if manifest_path != collected_path:
                raise ContractError("Bilibili qualification binding differs from collection")
        command = read_json(
            _evidence_path(
                collected_qualification["command_record"],
                label="qualification command",
                allow_absolute=True,
            )
        )
        status = read_json(
            _evidence_path(
                collected_qualification["terminal_status"],
                label="qualification status",
                allow_absolute=True,
            )
        )
        exit_code = int(
            _evidence_path(
                collected_qualification["exit_code_artifact"],
                label="qualification exit code",
                allow_absolute=True,
            ).read_text(encoding="utf-8").strip()
        )
        if (
            command.get("run_id") != manifest_qualification.get("run_id")
            or command.get("argv")
            != [
                sys.executable,
                "-X",
                "utf8",
                "-B",
                "-m",
                "unittest",
                "-v",
                "tests.video_workflow.test_issue13_exit_evidence",
            ]
            or command.get("cwd") != str(PROJECT_ROOT.resolve())
            or command.get("accepted_exit_codes") != [0]
            or status.get("run_id") != manifest_qualification.get("run_id")
            or status.get("state") != "succeeded"
            or status.get("exit_code") != 0
            or exit_code != 0
            or status.get("security", {}).get("acceptance_evidence_eligible") is not True
            or collected_qualification.get("acceptance_evidence_eligible") is not True
        ):
            raise ContractError("Bilibili qualification Run is not succeeded evidence")
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError, KeyError) as exc:
        raise ContractError("Bilibili guarded-delivery evidence cannot be decoded") from exc


def _validate_evidence(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError("Bilibili cutover Exit Evidence must be an object")
    if (
        value.get("kind") != "video-workflow-exit-evidence"
        or value.get("schema_version") != 2
        or value.get("slice") != EXPECTED_SLICE
        or value.get("overall_decision") != "pass"
        or value.get("platform_statuses")
        != {"bilibili": "active_kernel", "youtube": "active_legacy"}
    ):
        raise ContractError("Bilibili cutover Exit Evidence identity is invalid")
    scope = value.get("activation_scope")
    if not isinstance(scope, dict):
        raise ContractError("Bilibili cutover activation scope is invalid")
    comparable_scope = {
        key: scope.get(key) for key in EXPECTED_ACTIVATION_SCOPE
    }
    if comparable_scope != EXPECTED_ACTIVATION_SCOPE:
        raise ContractError(
            "Bilibili cutover activation scope is invalid",
            data={
                "first_failing_gate": "activation_scope",
                "error_code": "bilibili_activation_scope_invalid",
            },
        )
    if set(value.get("atomic_members", [])) != ATOMIC_MEMBERS:
        raise ContractError("Bilibili cutover atomic member set is incomplete")
    statuses = value.get("atomic_member_status")
    if (
        not isinstance(statuses, dict)
        or set(statuses) != ATOMIC_MEMBERS
        or any(statuses[member] != "active" for member in ATOMIC_MEMBERS)
    ):
        raise ContractError(
            "Bilibili cutover atomic member is inactive",
            data={
                "first_failing_gate": "atomic_member_status",
                "error_code": "bilibili_cutover_atomic_member_failed",
            },
        )
    _validate_guarded_delivery(value)
    implementation_commit = value.get("implementation_commit")
    fingerprints = value.get("artifact_fingerprints")
    try:
        if not isinstance(implementation_commit, str):
            raise EvidenceSupportError("implementation commit is absent")
        git_output(
            PROJECT_ROOT,
            "cat-file",
            "-e",
            f"{implementation_commit}^{{commit}}",
        )
        if not isinstance(fingerprints, list) or not fingerprints:
            raise EvidenceSupportError("implementation fingerprints are absent")
        for item in fingerprints:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("path"), str)
                or not isinstance(item.get("sha256"), str)
                or sha256_git_blob(
                    PROJECT_ROOT,
                    implementation_commit,
                    item["path"],
                )
                != item["sha256"]
            ):
                raise EvidenceSupportError(
                    "implementation fingerprint differs from its committed blob"
                )
    except EvidenceSupportError as exc:
        raise ContractError(
            "Bilibili cutover implementation lineage is invalid",
            data={
                "first_failing_gate": "implementation_artifacts",
                "error_code": "bilibili_implementation_lineage_invalid",
            },
        ) from exc
    schema_path = (
        PROJECT_ROOT
        / "schemas"
        / "exit-evidence-manifest.v2.schema.json"
    )
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(value)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise ContractError(
            "Bilibili cutover Exit Evidence is schema-invalid",
            data={
                "first_failing_gate": "exit_evidence_contract",
                "error_code": "bilibili_exit_evidence_schema_invalid",
            },
        ) from exc
    return value


class BilibiliPlatformCutoverPublisher:
    """Owns the independent authority transfer for new Bilibili Kernel Runs."""

    def _connect(self, root: Path) -> sqlite3.Connection:
        if not root.is_dir():
            raise ControlStoreUnavailable(
                "Platform cutover control-store root is unavailable"
            )
        try:
            connection = sqlite3.connect(
                root / PLATFORM_KERNEL_DB,
                timeout=0.05,
                isolation_level=None,
            )
            connection.row_factory = sqlite3.Row
            connection.execute(
                "CREATE TABLE IF NOT EXISTS platform_cutover_authority ("
                "platform TEXT PRIMARY KEY, generation INTEGER NOT NULL, "
                "evidence_sha256 TEXT NOT NULL, authority_sha256 TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS platform_cutover_intents ("
                "intent_id TEXT PRIMARY KEY, platform TEXT NOT NULL UNIQUE, "
                "evidence_sha256 TEXT NOT NULL, authority_json TEXT NOT NULL, "
                "candidate_snapshot_sha256 TEXT, "
                "state TEXT NOT NULL CHECK(state IN ('PREPARED','COMMITTED')))"
            )
            intent_columns = {
                str(column["name"])
                for column in connection.execute(
                    "PRAGMA table_info(platform_cutover_intents)"
                ).fetchall()
            }
            if "candidate_snapshot_sha256" not in intent_columns:
                connection.execute(
                    "ALTER TABLE platform_cutover_intents "
                    "ADD COLUMN candidate_snapshot_sha256 TEXT"
                )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS platform_cutover_candidates ("
                "platform TEXT PRIMARY KEY, candidate_run_id TEXT NOT NULL, "
                "source_identity TEXT NOT NULL, session_id TEXT NOT NULL, "
                "global_gate_sha256 TEXT NOT NULL, implementation_commit TEXT NOT NULL, "
                "probe_sha256 TEXT NOT NULL, candidate_json TEXT NOT NULL, "
                "state TEXT NOT NULL CHECK(state IN "
                "('PREPARED','INITIALIZING','INITIALIZED','PROVISIONAL','CONFIRMED')))"
            )
            candidate_table = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' "
                "AND name='platform_cutover_candidates'"
            ).fetchone()
            if candidate_table is not None and "'INITIALIZING'" not in str(
                candidate_table["sql"]
            ):
                connection.execute("BEGIN EXCLUSIVE")
                try:
                    connection.execute(
                        "ALTER TABLE platform_cutover_candidates "
                        "RENAME TO platform_cutover_candidates_v1"
                    )
                    connection.execute(
                        "CREATE TABLE platform_cutover_candidates ("
                        "platform TEXT PRIMARY KEY, candidate_run_id TEXT NOT NULL, "
                        "source_identity TEXT NOT NULL, session_id TEXT NOT NULL, "
                        "global_gate_sha256 TEXT NOT NULL, implementation_commit TEXT NOT NULL, "
                        "probe_sha256 TEXT NOT NULL, candidate_json TEXT NOT NULL, "
                        "state TEXT NOT NULL CHECK(state IN "
                        "('PREPARED','INITIALIZING','INITIALIZED','PROVISIONAL','CONFIRMED')))"
                    )
                    connection.execute(
                        "INSERT INTO platform_cutover_candidates SELECT * "
                        "FROM platform_cutover_candidates_v1"
                    )
                    connection.execute("DROP TABLE platform_cutover_candidates_v1")
                    connection.execute("COMMIT")
                except Exception:
                    connection.execute("ROLLBACK")
                    raise
            return connection
        except (OSError, sqlite3.DatabaseError) as exc:
            raise ControlStoreUnavailable(
                "Platform cutover control store is unavailable"
            ) from exc

    @staticmethod
    def _candidate_snapshot(row: sqlite3.Row) -> dict[str, Any]:
        try:
            candidate = json.loads(row["candidate_json"])
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise KernelConflict(
                "Bilibili candidate snapshot cannot be decoded",
                data={
                    "first_failing_gate": "platform_kernel_candidate",
                    "error_code": "bilibili_candidate_snapshot_invalid",
                },
            ) from exc
        if (
            not isinstance(candidate, dict)
            or candidate.get("state") != row["state"]
            or candidate.get("candidate_run_id") != row["candidate_run_id"]
            or candidate.get("source_identity") != row["source_identity"]
            or candidate.get("candidate_session_id") != row["session_id"]
            or candidate.get("implementation_commit") != row["implementation_commit"]
            or candidate.get("probe_sha256") != row["probe_sha256"]
            or not isinstance(candidate.get("global_gate_binding"), dict)
            or candidate["global_gate_binding"].get("authority_sha256")
            != row["global_gate_sha256"]
        ):
            raise KernelConflict(
                "Bilibili candidate SQL and JSON states differ",
                data={
                    "first_failing_gate": "platform_kernel_candidate",
                    "error_code": "bilibili_candidate_state_inconsistent",
                },
            )
        return candidate

    @classmethod
    def _confirmation_snapshot_fingerprint(
        cls, row: sqlite3.Row, evidence: dict[str, Any]
    ) -> str:
        candidate = cls._candidate_snapshot(row)
        guarded = evidence.get("guarded_delivery_evidence")
        artifacts = guarded.get("artifacts") if isinstance(guarded, dict) else None
        if not isinstance(artifacts, list) or len(artifacts) != 9:
            raise KernelConflict(
                "Bilibili candidate confirmation snapshot is incomplete"
            )
        current_artifacts = []
        for item in sorted(artifacts, key=lambda value: str(value.get("role", ""))):
            if not isinstance(item, dict):
                raise KernelConflict(
                    "Bilibili candidate confirmation snapshot is invalid"
                )
            raw_path = Path(str(item.get("path", "")))
            path = (
                raw_path.resolve()
                if raw_path.is_absolute()
                else (PROJECT_ROOT / raw_path).resolve()
            )
            if not path.is_file():
                raise KernelConflict(
                    "Bilibili candidate confirmation artifact is unavailable"
                )
            current_artifacts.append(
                {
                    "role": item.get("role"),
                    "path": str(path),
                    "sha256": sha256_file(path),
                }
            )
        return hashlib.sha256(
            canonical_json_bytes(
                {
                    "candidate": candidate,
                    "run_id": guarded.get("run_id"),
                    "artifacts": current_artifacts,
                }
            )
        ).hexdigest()

    def prepare_candidate(
        self,
        *,
        platform: str,
        control_store_root: Path,
        implementation_commit: str,
        candidate_probe: Path,
        candidate_session_id: str,
        prepared_at: str,
    ) -> dict[str, Any]:
        """Durably bind the single pre-confirmation Run without activating it."""

        if platform != SUPPORTED_PLATFORM:
            raise ContractError("Only a Bilibili cutover candidate can be prepared")
        session_id = require_safe_path_segment(
            candidate_session_id,
            purpose="cutover candidate session_id",
            error_type=ContractError,
        )
        probe_path = candidate_probe.resolve()
        if not probe_path.is_file():
            raise ContractError("Bilibili cutover candidate probe is unavailable")
        probe = read_json(probe_path)
        adapter = probe.get("adapter")
        if (
            probe.get("schema_name") != "bootstrap-record"
            or probe.get("schema_version") != "2.0.0"
            or probe.get("status") != "probe_complete"
            or not isinstance(adapter, dict)
            or adapter.get("canonical_platform") != platform
            or probe.get("canonical_platform") != platform
            or not isinstance(probe.get("run_id"), str)
            or len(probe["run_id"]) != 32
            or not isinstance(probe.get("source_identity"), str)
            or len(probe["source_identity"]) != 64
        ):
            raise ContractError("Bilibili cutover candidate probe identity is invalid")
        try:
            git_output(
                PROJECT_ROOT,
                "cat-file",
                "-e",
                f"{implementation_commit}^{{commit}}",
            )
            if git_output(PROJECT_ROOT, "rev-parse", "HEAD") != implementation_commit:
                raise EvidenceSupportError(
                    "cutover candidate implementation commit is not current HEAD"
                )
        except EvidenceSupportError as exc:
            raise ContractError(
                "Bilibili cutover candidate implementation lineage is invalid",
                data={
                    "first_failing_gate": "implementation_artifacts",
                    "error_code": "bilibili_candidate_implementation_invalid",
                },
            ) from exc

        root = control_store_root.resolve()
        global_gate = GlobalGatePublisher().require_current(control_store_root=root)
        candidate = {
            "schema_name": "platform-kernel-cutover-candidate",
            "schema_version": "1.0.0",
            "platform": platform,
            "candidate_run_id": probe["run_id"],
            "source_identity": probe["source_identity"],
            "candidate_session_id": session_id,
            "global_gate_binding": {
                "activation_status": "active_global_gate",
                "authority_path": global_gate["path"],
                "authority_sha256": global_gate["file_sha256"],
                "generation": global_gate["generation"],
            },
            "implementation_commit": implementation_commit,
            "probe_path": str(probe_path),
            "probe_sha256": sha256_file(probe_path),
            "prepared_at": prepared_at,
            "state": "PREPARED",
        }
        encoded = json.dumps(candidate, sort_keys=True, separators=(",", ":"))
        reprepared = False
        with self._connect(root) as connection:
            connection.execute("BEGIN IMMEDIATE")
            active = connection.execute(
                "SELECT 1 FROM platform_cutover_authority WHERE platform=?",
                (platform,),
            ).fetchone()
            if active is not None:
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    "Bilibili Platform Kernel is already confirmed",
                    data={
                        "first_failing_gate": "platform_kernel_authority",
                        "error_code": "bilibili_platform_authority_already_confirmed",
                    },
                )
            existing = connection.execute(
                "SELECT * FROM platform_cutover_candidates "
                "WHERE platform=?",
                (platform,),
            ).fetchone()
            if existing is not None:
                existing_candidate = self._candidate_snapshot(existing)
                if existing["state"] != "PREPARED":
                    connection.execute("ROLLBACK")
                    raise KernelConflict(
                        "A different Bilibili cutover candidate is already prepared"
                    )
                if existing["candidate_json"] == encoded:
                    connection.execute("COMMIT")
                    idempotent = True
                else:
                    rebound_candidate = dict(existing_candidate)
                    rebound_candidate["implementation_commit"] = implementation_commit
                    rebound_candidate["prepared_at"] = prepared_at
                    if (
                        existing["implementation_commit"] == implementation_commit
                        or "workspace_root" in existing_candidate
                        or "candidate_run_dir" in existing_candidate
                        or rebound_candidate != candidate
                    ):
                        connection.execute("ROLLBACK")
                        raise KernelConflict(
                            "A different Bilibili cutover candidate is already prepared"
                        )
                    updated = connection.execute(
                        "UPDATE platform_cutover_candidates SET "
                        "implementation_commit=?,candidate_json=? "
                        "WHERE platform=? AND state='PREPARED' "
                        "AND implementation_commit=? AND candidate_json=?",
                        (
                            implementation_commit,
                            encoded,
                            platform,
                            existing["implementation_commit"],
                            existing["candidate_json"],
                        ),
                    )
                    if updated.rowcount != 1:
                        connection.execute("ROLLBACK")
                        raise KernelConflict(
                            "Bilibili cutover candidate changed during preparation"
                        )
                    connection.execute("COMMIT")
                    idempotent = False
                    reprepared = True
            else:
                connection.execute(
                    "INSERT INTO platform_cutover_candidates("
                    "platform,candidate_run_id,source_identity,session_id,"
                    "global_gate_sha256,implementation_commit,probe_sha256,"
                    "candidate_json,state) VALUES(?,?,?,?,?,?,?,?, 'PREPARED')",
                    (
                        platform,
                        candidate["candidate_run_id"],
                        candidate["source_identity"],
                        session_id,
                        candidate["global_gate_binding"]["authority_sha256"],
                        implementation_commit,
                        candidate["probe_sha256"],
                        encoded,
                    ),
                )
                connection.execute("COMMIT")
                idempotent = False
        return {
            "authority_status": "prepared_candidate",
            "candidate_run_id": candidate["candidate_run_id"],
            "candidate_session_id": session_id,
            "source_identity": candidate["source_identity"],
            "global_gate_binding": candidate["global_gate_binding"],
            "implementation_commit": implementation_commit,
            "platform_statuses": {
                "bilibili": "active_legacy",
                "youtube": "active_legacy",
            },
            "idempotent": idempotent,
            "reprepared": reprepared,
        }

    def require_prepared_candidate(
        self,
        *,
        platform: str,
        control_store_root: Path,
        candidate_probe: Path,
        candidate_session_id: str,
    ) -> dict[str, Any]:
        if platform != SUPPORTED_PLATFORM:
            raise ContractError("Only a Bilibili cutover candidate can initialize")
        root = control_store_root.resolve()
        with self._connect(root) as connection:
            row = connection.execute(
                "SELECT * FROM platform_cutover_candidates WHERE platform=?",
                (platform,),
            ).fetchone()
        if row is None or row["state"] not in {"PREPARED", "INITIALIZING", "INITIALIZED"}:
            raise KernelConflict(
                "Bilibili cutover candidate is absent or already confirmed",
                data={
                    "first_failing_gate": "platform_kernel_candidate",
                    "error_code": "bilibili_cutover_candidate_unavailable",
                },
            )
        probe_path = candidate_probe.resolve()
        if not probe_path.is_file():
            raise KernelConflict(
                "Bilibili cutover candidate probe is unavailable",
                data={
                    "first_failing_gate": "platform_kernel_candidate",
                    "error_code": "bilibili_cutover_candidate_binding_mismatch",
                },
            )
        probe = read_json(probe_path)
        candidate = self._candidate_snapshot(row)
        global_gate = candidate["global_gate_binding"]
        global_gate_path = Path(global_gate["authority_path"]).resolve()
        current_global_gate = GlobalGatePublisher().require_current(
            control_store_root=root
        )
        if (
            row["candidate_run_id"] != probe.get("run_id")
            or row["source_identity"] != probe.get("source_identity")
            or row["session_id"] != candidate_session_id
            or row["probe_sha256"] != sha256_file(probe_path)
            or row["implementation_commit"]
            != git_output(PROJECT_ROOT, "rev-parse", "HEAD")
            or not global_gate_path.is_relative_to(root)
            or not global_gate_path.is_file()
            or row["global_gate_sha256"] != sha256_file(global_gate_path)
            or global_gate["authority_path"] != current_global_gate["path"]
            or global_gate["authority_sha256"]
            != current_global_gate["file_sha256"]
            or global_gate["generation"] != current_global_gate["generation"]
        ):
            raise KernelConflict(
                "Bilibili cutover candidate binding differs from its prepared authority",
                data={
                    "first_failing_gate": "platform_kernel_candidate",
                    "error_code": "bilibili_cutover_candidate_binding_mismatch",
                },
            )
        return candidate

    def begin_candidate_initialization(
        self,
        *,
        platform: str,
        control_store_root: Path,
        candidate_probe: Path,
        candidate_session_id: str,
        workspace_root: Path,
    ) -> dict[str, Any]:
        candidate = self.require_prepared_candidate(
            platform=platform,
            control_store_root=control_store_root,
            candidate_probe=candidate_probe,
            candidate_session_id=candidate_session_id,
        )
        root = control_store_root.resolve()
        workspace = workspace_root.resolve()
        candidate["workspace_root"] = str(workspace)
        candidate["state"] = "INITIALIZING"
        encoded = json.dumps(candidate, sort_keys=True, separators=(",", ":"))
        with self._connect(root) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM platform_cutover_candidates WHERE platform=?",
                (platform,),
            ).fetchone()
            if row is None:
                connection.execute("ROLLBACK")
                raise KernelConflict("Bilibili cutover candidate disappeared")
            current = self._candidate_snapshot(row)
            if row["state"] != "PREPARED":
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    "Bilibili cutover candidate initialization is already owned",
                    data={
                        "first_failing_gate": "platform_kernel_candidate",
                        "error_code": "bilibili_candidate_initialization_in_progress",
                    },
                )
            if current["candidate_run_id"] != candidate["candidate_run_id"]:
                connection.execute("ROLLBACK")
                raise KernelConflict("Bilibili cutover candidate identity changed")
            connection.execute(
                "UPDATE platform_cutover_candidates "
                "SET candidate_json=?,state='INITIALIZING' "
                "WHERE platform=? AND state='PREPARED'",
                (encoded, platform),
            )
            connection.execute("COMMIT")
        return candidate

    def record_candidate_initialized(
        self, *, platform: str, control_store_root: Path, candidate_run_dir: Path
    ) -> None:
        root = control_store_root.resolve()
        run_dir = candidate_run_dir.resolve()
        run_path = run_dir / "workflow" / "run.json"
        if not run_path.is_file():
            raise KernelConflict("Bilibili cutover candidate Run Record is unavailable")
        run = read_json(run_path)
        with self._connect(root) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM platform_cutover_candidates WHERE platform=?",
                (platform,),
            ).fetchone()
            if (
                row is None
                or row["state"] not in {"INITIALIZING", "INITIALIZED"}
                or run.get("schema_version") != "4.0.0"
                or run.get("canonical_platform") != platform
                or run.get("run_id") != row["candidate_run_id"]
                or Path(str(run.get("output_path", ""))).resolve() != run_dir
            ):
                connection.execute("ROLLBACK")
                raise KernelConflict("Bilibili cutover candidate lost its durable fence")
            candidate = self._candidate_snapshot(row)
            prior_run_dir = candidate.get("candidate_run_dir")
            if prior_run_dir is not None and Path(prior_run_dir).resolve() != run_dir:
                connection.execute("ROLLBACK")
                raise KernelConflict("Bilibili cutover candidate Run binding changed")
            candidate["candidate_run_dir"] = str(run_dir)
            candidate["state"] = "INITIALIZED"
            connection.execute(
                "UPDATE platform_cutover_candidates "
                "SET candidate_json=?,state='INITIALIZED' WHERE platform=? "
                "AND state IN ('INITIALIZING','INITIALIZED')",
                (
                    json.dumps(candidate, sort_keys=True, separators=(",", ":")),
                    platform,
                ),
            )
            connection.execute("COMMIT")

    def rollback_unstarted_candidate_initialization(
        self,
        *,
        platform: str,
        control_store_root: Path,
        candidate_run_id: str,
        workspace_root: Path,
    ) -> None:
        root = control_store_root.resolve()
        workspace = workspace_root.resolve()
        with self._connect(root) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM platform_cutover_candidates WHERE platform=?",
                (platform,),
            ).fetchone()
            if row is None or row["state"] != "INITIALIZING":
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    "Bilibili candidate initialization rollback lost its fence"
                )
            candidate = self._candidate_snapshot(row)
            if (
                row["candidate_run_id"] != candidate_run_id
                or Path(str(candidate.get("workspace_root", ""))).resolve()
                != workspace
            ):
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    "Bilibili candidate initialization rollback binding changed"
                )
            candidate.pop("workspace_root", None)
            candidate.pop("candidate_run_dir", None)
            candidate["state"] = "PREPARED"
            changed = connection.execute(
                "UPDATE platform_cutover_candidates SET candidate_json=?,state='PREPARED' "
                "WHERE platform=? AND candidate_run_id=? AND state='INITIALIZING'",
                (
                    json.dumps(candidate, sort_keys=True, separators=(",", ":")),
                    platform,
                    candidate_run_id,
                ),
            ).rowcount
            if changed != 1:
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    "Bilibili candidate initialization rollback lost its CAS fence"
                )
            connection.execute("COMMIT")

    @staticmethod
    def _projection_path(
        binding: Any, *, base: Path, allowed_root: Path, label: str
    ) -> Path:
        if not isinstance(binding, dict):
            raise KernelConflict(f"Bilibili candidate {label} binding is absent")
        raw = binding.get("path")
        expected = binding.get("sha256")
        if not isinstance(raw, str) or not isinstance(expected, str):
            raise KernelConflict(f"Bilibili candidate {label} binding is invalid")
        candidate = Path(raw)
        path = (base / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
        if (
            not path.is_relative_to(allowed_root.resolve())
            or not path.is_file()
            or sha256_file(path) != expected
        ):
            if not path.is_relative_to(allowed_root.resolve()):
                raise KernelConflict(
                    f"Bilibili candidate {label} escapes its projection root",
                    data={
                        "first_failing_gate": "path_boundary",
                        "error_code": "bilibili_candidate_projection_escape",
                    },
                )
            raise KernelConflict(f"Bilibili candidate {label} binding is stale")
        return path

    def _current_candidate_run(
        self,
        *,
        root: Path,
        row: sqlite3.Row,
        expected_stage: str,
    ) -> tuple[Path, dict[str, Any], dict[str, Any]]:
        candidate = self._candidate_snapshot(row)
        declared = candidate.get("candidate_run_dir")
        if not isinstance(declared, str):
            raise KernelConflict("Bilibili candidate Run binding is absent")
        run_dir = Path(declared).resolve()
        run_path = run_dir / "workflow" / "run.json"
        if not run_path.is_file():
            raise KernelConflict("Bilibili candidate Run Record is unavailable")
        run = read_json(run_path)
        delivery = run.get("delivery")
        if (
            run.get("schema_version") != "4.0.0"
            or run.get("canonical_platform") != SUPPORTED_PLATFORM
            or run.get("run_id") != row["candidate_run_id"]
            or Path(str(run.get("output_path", ""))).resolve() != run_dir
            or not isinstance(delivery, dict)
            or delivery.get("stage") != expected_stage
        ):
            raise KernelConflict(
                "Bilibili candidate Run is not at the required delivery stage",
                data={
                    "first_failing_gate": "platform_kernel_candidate",
                    "error_code": "bilibili_candidate_not_ready_for_activation",
                },
            )
        if run.get("source_identity") != row["source_identity"]:
            raise KernelConflict(
                "Bilibili candidate source identity differs from preparation",
                data={
                    "first_failing_gate": "platform_kernel_candidate",
                    "error_code": "bilibili_candidate_source_binding_mismatch",
                },
            )
        ownership = delivery.get("ownership")
        if (
            not isinstance(ownership, dict)
            or ownership.get("session_id") != row["session_id"]
        ):
            raise KernelConflict(
                "Bilibili candidate session differs from preparation",
                data={
                    "first_failing_gate": "platform_kernel_candidate",
                    "error_code": "bilibili_candidate_session_binding_mismatch",
                },
            )
        projections = delivery.get("projections")
        if not isinstance(projections, dict):
            raise KernelConflict("Bilibili candidate delivery projections are absent")
        video_path = self._projection_path(
            projections.get("video_target"),
            base=run_dir,
            allowed_root=run_dir,
            label="video target",
        )
        project_root = run_dir.parents[1]
        session_path = self._projection_path(
            projections.get("session_target"),
            base=run_dir,
            allowed_root=project_root,
            label="session target",
        )
        index_path = self._projection_path(
            projections.get("task_index"),
            base=run_dir,
            allowed_root=project_root,
            label="task index",
        )
        video = read_json(video_path)
        session = read_json(session_path)
        index = read_json(index_path)
        run_id = run["run_id"]
        matching = [
            item for item in index.get("entries", []) if item.get("run_id") == run_id
        ]
        if (
            video.get("run_id") != run_id
            or video.get("stage") != expected_stage
            or session.get("run_id") != run_id
            or session.get("stage") != expected_stage
            or len(matching) != 1
            or matching[0].get("stage") != expected_stage
        ):
            raise KernelConflict("Bilibili candidate delivery projections are not current")
        gate = video.get("global_gate_authority")
        current_gate = GlobalGatePublisher().require_current(control_store_root=root)
        if (
            not isinstance(gate, dict)
            or gate.get("path") != current_gate["path"]
            or gate.get("generation") != current_gate["generation"]
            or gate.get("sha256") != current_gate["file_sha256"]
        ):
            raise KernelConflict("Bilibili candidate Global Gate binding is stale")
        return run_dir, run, video

    def activate_candidate(
        self,
        *,
        platform: str,
        control_store_root: Path,
        candidate_run_dir: Path,
        activated_at: str,
    ) -> dict[str, Any]:
        if platform != SUPPORTED_PLATFORM:
            raise ContractError("Only a Bilibili cutover candidate can activate")
        root = control_store_root.resolve()
        with self._connect(root) as connection:
            row = connection.execute(
                "SELECT * FROM platform_cutover_candidates WHERE platform=?",
                (platform,),
            ).fetchone()
        if row is None or row["state"] not in {"INITIALIZED", "PROVISIONAL"}:
            raise KernelConflict(
                "Bilibili cutover candidate is not initialized",
                data={
                    "first_failing_gate": "platform_kernel_candidate",
                    "error_code": "bilibili_cutover_candidate_unavailable",
                },
            )
        if row["implementation_commit"] != git_output(
            PROJECT_ROOT, "rev-parse", "HEAD"
        ):
            raise KernelConflict(
                "Bilibili candidate implementation is no longer current",
                data={
                    "first_failing_gate": "implementation_artifacts",
                    "error_code": "bilibili_candidate_implementation_stale",
                },
            )
        self._candidate_snapshot(row)
        run_dir, run, video = self._current_candidate_run(
            root=root, row=row, expected_stage="ready_for_delivery"
        )
        if run_dir != candidate_run_dir.resolve():
            raise KernelConflict("Bilibili candidate activation targets another Run")
        artifacts = video.get("artifacts")
        acceptance_binding = (
            artifacts.get("acceptance_report") if isinstance(artifacts, dict) else None
        )
        guard_binding = (
            artifacts.get("delivery_guard_report") if isinstance(artifacts, dict) else None
        )
        acceptance_path = run_dir / "review" / "acceptance" / "acceptance_report.json"
        if (
            not isinstance(acceptance_binding, dict)
            or guard_binding is not None
            or Path(str(acceptance_binding.get("path", ""))).resolve()
            != acceptance_path.resolve()
            or not acceptance_path.is_file()
            or acceptance_binding.get("sha256") != sha256_file(acceptance_path)
        ):
            raise KernelConflict(
                "Bilibili candidate guarded decision is not bound to the ready target",
                data={
                    "first_failing_gate": "platform_kernel_candidate",
                    "error_code": "bilibili_candidate_guarded_decision_unbound",
                },
            )
        try:
            report = validate_acceptance_report(
                project_root=PROJECT_ROOT,
                report_path=acceptance_path,
                run_id=run["run_id"],
                coordination_revision=read_json(acceptance_path)["run_binding"][
                    "coordination_revision"
                ],
            )
            eligibility = AcceptanceV2Provider(PROJECT_ROOT).guard_eligibility(
                workspace_root=acceptance_path.parent
            )
            if (
                eligibility.get("eligible") is not True
                or eligibility.get("delivery_authority") is not True
                or eligibility.get("report_sha256") != report.get("report_sha256")
            ):
                raise ContractError(
                    "Acceptance Report v2 lacks committed provider authority"
                )
        except (ContractError, KernelConflict, OSError, KeyError, TypeError, ValueError) as exc:
            raise KernelConflict(
                "Bilibili candidate lacks a passing Acceptance decision",
                data={
                    "first_failing_gate": "platform_kernel_candidate",
                    "error_code": "bilibili_candidate_guarded_decision_invalid",
                },
            ) from exc
        candidate = json.loads(row["candidate_json"])
        candidate.update(
            {
                "state": "PROVISIONAL",
                "provisional_activated_at": activated_at,
                "acceptance_report_sha256": sha256_file(acceptance_path),
            }
        )
        encoded = json.dumps(candidate, sort_keys=True, separators=(",", ":"))
        with self._connect(root) as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT * FROM platform_cutover_candidates "
                "WHERE platform=?",
                (platform,),
            ).fetchone()
            if current is None or current["state"] not in {"INITIALIZED", "PROVISIONAL"}:
                connection.execute("ROLLBACK")
                raise KernelConflict("Bilibili candidate activation lost its fence")
            self._candidate_snapshot(current)
            if current["state"] == "PROVISIONAL" and current["candidate_json"] != encoded:
                connection.execute("ROLLBACK")
                raise KernelConflict("Bilibili candidate provisional authority conflicts")
            connection.execute(
                "UPDATE platform_cutover_candidates "
                "SET candidate_json=?,state='PROVISIONAL' WHERE platform=?",
                (encoded, platform),
            )
            connection.execute("COMMIT")
        return {
            "platform": platform,
            "cutover_state": "PROVISIONAL",
            "candidate_run_id": run["run_id"],
            "candidate_run_dir": str(run_dir),
            "platform_statuses": {
                "bilibili": "active_legacy",
                "youtube": "active_legacy",
            },
        }

    def authorize_delivery_transition(
        self,
        *,
        platform: str,
        control_store_root: Path,
        run_dir: Path,
        run_id: str,
        to_stage: str,
    ) -> None:
        if to_stage not in {"accepted", "delivered"}:
            return
        root = control_store_root.resolve()
        if not (root / PLATFORM_KERNEL_DB).is_file():
            raise KernelConflict(
                "Bilibili delivery transition lacks Platform Kernel authority",
                data={
                    "first_failing_gate": "platform_kernel_authority",
                    "error_code": "bilibili_platform_authority_stale",
                },
            )
        with self._connect(root) as connection:
            active = connection.execute(
                "SELECT 1 FROM platform_cutover_authority WHERE platform=?",
                (platform,),
            ).fetchone()
            candidate = connection.execute(
                "SELECT * FROM platform_cutover_candidates WHERE platform=?",
                (platform,),
            ).fetchone()
        if active is not None:
            self.require_current(platform=platform, control_store_root=root)
            return
        if candidate is not None and candidate["state"] == "PROVISIONAL":
            value = self._candidate_snapshot(candidate)
            if (
                candidate["candidate_run_id"] == run_id
                and Path(str(value.get("candidate_run_dir", ""))).resolve()
                == run_dir.resolve()
            ):
                if to_stage == "delivered":
                    try:
                        guarded = require_current_kernel_guarded_decision(
                            project_root=run_dir.resolve().parents[1],
                            run_dir=run_dir.resolve(),
                        )
                    except (ContractError, OSError, KeyError, TypeError, ValueError) as exc:
                        raise KernelConflict(
                            "Bilibili candidate guarded delivery authority is stale",
                            data={
                                "first_failing_gate": "platform_kernel_candidate",
                                "error_code": "bilibili_candidate_guarded_decision_stale",
                            },
                        ) from exc
                    if (
                        guarded.get("run_id") != run_id
                        or guarded.get("acceptance_report", {}).get("sha256")
                        != value.get("acceptance_report_sha256")
                    ):
                        raise KernelConflict(
                            "Bilibili candidate guarded delivery authority is stale",
                            data={
                                "first_failing_gate": "platform_kernel_candidate",
                                "error_code": "bilibili_candidate_guarded_decision_stale",
                            },
                        )
                return
        raise KernelConflict(
            "Bilibili delivery transition lacks confirmed or provisional authority",
            data={
                "first_failing_gate": "platform_kernel_candidate",
                "error_code": "bilibili_candidate_delivery_not_authorized",
            },
        )

    def reject_candidate_handoff(
        self,
        *,
        platform: str,
        control_store_root: Path,
        run_id: str,
    ) -> None:
        root = control_store_root.resolve()
        if not (root / PLATFORM_KERNEL_DB).is_file():
            return
        with self._connect(root) as connection:
            candidate = connection.execute(
                "SELECT * FROM platform_cutover_candidates WHERE platform=?",
                (platform,),
            ).fetchone()
        if candidate is None:
            return
        self._candidate_snapshot(candidate)
        if (
            candidate["candidate_run_id"] == run_id
            and candidate["state"] != "CONFIRMED"
        ):
            raise KernelConflict(
                "Bilibili cutover candidate cannot be handed off before confirmation",
                data={
                    "first_failing_gate": "platform_kernel_candidate",
                    "error_code": "bilibili_candidate_handoff_forbidden",
                },
            )

    def _require_confirmable_candidate(
        self,
        *,
        root: Path,
        evidence: dict[str, Any],
        candidate_row: sqlite3.Row | None = None,
    ) -> sqlite3.Row:
        row = candidate_row
        if row is None:
            with self._connect(root) as connection:
                row = connection.execute(
                    "SELECT * FROM platform_cutover_candidates WHERE platform=?",
                    (SUPPORTED_PLATFORM,),
                ).fetchone()
        guarded = evidence.get("guarded_delivery_evidence")
        guarded_run_id = guarded.get("run_id") if isinstance(guarded, dict) else None
        if row is None or row["state"] not in {"PROVISIONAL", "CONFIRMED"}:
            raise KernelConflict(
                "Bilibili activation requires one provisional candidate",
                data={
                    "first_failing_gate": "platform_kernel_candidate",
                    "error_code": "bilibili_provisional_candidate_absent",
                },
            )
        if evidence.get("implementation_commit") != row["implementation_commit"]:
            raise KernelConflict(
                "Bilibili activation evidence differs from the candidate implementation",
                data={
                    "first_failing_gate": "implementation_artifacts",
                    "error_code": "bilibili_candidate_implementation_evidence_mismatch",
                },
            )
        run_dir, run, video = self._current_candidate_run(
            root=root, row=row, expected_stage="delivered"
        )
        if guarded_run_id != row["candidate_run_id"] or run["run_id"] != guarded_run_id:
            raise KernelConflict(
                "Bilibili guarded delivery differs from the delivered candidate",
                data={
                    "first_failing_gate": "guarded_delivery_candidate_binding",
                    "error_code": (
                        "bilibili_guarded_run_differs_from_delivered_candidate"
                    ),
                },
            )
        manifest_artifacts = {
            item.get("role"): item
            for item in guarded.get("artifacts", [])
            if isinstance(item, dict)
        }
        source = run.get("artifact_generations", {}).get("source_manifest")
        projections = run["delivery"]["projections"]
        expected_paths = {
            "run_record": run_dir / "workflow" / "run.json",
            "source_manifest": run_dir / "source" / "manifest.json",
            "acceptance_report_v2": Path(
                str(video.get("artifacts", {}).get("acceptance_report", {}).get("path", ""))
            ),
            "delivery_guard_report": Path(
                str(video.get("artifacts", {}).get("delivery_guard_report", {}).get("path", ""))
            ),
            "video_delivery_target": self._projection_path(
                projections["video_target"],
                base=run_dir,
                allowed_root=run_dir,
                label="video target",
            ),
            "session_delivery_target": self._projection_path(
                projections["session_target"],
                base=run_dir,
                allowed_root=run_dir.parents[1],
                label="session target",
            ),
            "delivery_task_index": self._projection_path(
                projections["task_index"],
                base=run_dir,
                allowed_root=run_dir.parents[1],
                label="task index",
            ),
            "global_gate_authority": Path(
                str(video.get("global_gate_authority", {}).get("path", ""))
            ),
            "final_pdf": Path(
                str(video.get("artifacts", {}).get("final_pdf", {}).get("path", ""))
            ),
        }
        if (
            not isinstance(source, dict)
            or not expected_paths["source_manifest"].is_file()
            or source.get("sha256")
            != sha256_file(expected_paths["source_manifest"])
        ):
            raise KernelConflict(
                "Bilibili candidate source manifest authority is absent or stale",
                data={
                    "first_failing_gate": "guarded_delivery_candidate_binding",
                    "error_code": "bilibili_candidate_source_manifest_stale",
                },
            )
        for role, expected_path in expected_paths.items():
            binding = manifest_artifacts.get(role)
            resolved = expected_path.resolve()
            if (
                not isinstance(binding, dict)
                or not resolved.is_file()
                or (
                    Path(str(binding.get("path", ""))).resolve()
                    if Path(str(binding.get("path", ""))).is_absolute()
                    else (PROJECT_ROOT / Path(str(binding.get("path", "")))).resolve()
                )
                != resolved
                or binding.get("sha256") != sha256_file(resolved)
            ):
                raise KernelConflict(
                    f"Bilibili guarded delivery role differs from candidate: {role}",
                    data={
                        "first_failing_gate": "guarded_delivery_candidate_binding",
                        "error_code": "bilibili_guarded_candidate_role_mismatch",
                        "role": role,
                    },
                )
        return row

    def activate(
        self,
        *,
        platform: str,
        control_store_root: Path,
        exit_evidence: Path,
        activated_at: str,
        fault_point: str | None = None,
    ) -> dict[str, Any]:
        if platform != SUPPORTED_PLATFORM:
            raise ContractError("Only the Bilibili Platform Kernel Cutover is active")
        evidence_path = exit_evidence.resolve()
        if not evidence_path.is_file():
            raise ContractError("Bilibili cutover Exit Evidence is unavailable")
        evidence = _validate_evidence(read_json(evidence_path))
        _require_formal_exit_evidence(evidence_path)
        evidence_sha256 = sha256_file(evidence_path)
        root = control_store_root.resolve()
        global_gate = GlobalGatePublisher().require_current(
            control_store_root=root
        )
        confirmable_candidate = self._require_confirmable_candidate(
            root=root, evidence=evidence
        )
        candidate_snapshot_sha256 = self._confirmation_snapshot_fingerprint(
            confirmable_candidate, evidence
        )
        binding = {
            "activation_status": "active_global_gate",
            "authority_path": global_gate["path"],
            "authority_sha256": global_gate["file_sha256"],
            "generation": global_gate["generation"],
        }
        authority_path = root / PLATFORM_AUTHORITY_DIR / f"{platform}.json"
        authority_path.parent.mkdir(parents=True, exist_ok=True)
        authority = {
            "schema_name": "platform-kernel-authority",
            "schema_version": "1.0.0",
            "platform": platform,
            "generation": 1,
            "authority_status": "active_kernel",
            "new_run_authority": "video_workflow_kernel_v2",
            "existing_run_authority": "legacy_preserved",
            "acceptance_authority": "active_global_gate",
            "global_gate_binding": binding,
            "exit_evidence_path": str(evidence_path),
            "exit_evidence_sha256": evidence_sha256,
            "activated_at": activated_at,
        }
        authority["authority_sha256"] = _fingerprint(
            authority, "authority_sha256"
        )
        intent_id = hashlib.sha256(
            (platform + "\0" + evidence_sha256).encode("utf-8")
        ).hexdigest()
        with self._connect(root) as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT * FROM platform_cutover_authority WHERE platform=?",
                (platform,),
            ).fetchone()
            if current is not None:
                if current["evidence_sha256"] != evidence_sha256:
                    connection.execute("ROLLBACK")
                    raise KernelConflict(
                        "A different Bilibili Platform Kernel authority already exists"
                    )
                if (
                    not authority_path.is_file()
                    or sha256_file(authority_path) != current["authority_sha256"]
                ):
                    connection.execute("ROLLBACK")
                    raise KernelConflict(
                        "Committed Bilibili Platform Kernel authority is stale"
                    )
                connection.execute("COMMIT")
                return {
                    "platform": platform,
                    "generation": int(current["generation"]),
                    "authority_path": str(authority_path),
                    "authority_sha256": current["authority_sha256"],
                    "platform_statuses": {
                        "bilibili": "active_kernel",
                        "youtube": "active_legacy",
                    },
                    "cutover_state": "CONFIRMED",
                    "idempotent": True,
                }
            pending = connection.execute(
                "SELECT * FROM platform_cutover_intents WHERE state='PREPARED'"
            ).fetchall()
            if pending:
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    "An interrupted Bilibili Platform Kernel publication requires reconciliation"
                )
            connection.execute(
                "INSERT INTO platform_cutover_intents("
                "intent_id,platform,evidence_sha256,authority_json,"
                "candidate_snapshot_sha256,state) "
                "VALUES(?,?,?,?,?, 'PREPARED')",
                (
                    intent_id,
                    platform,
                    evidence_sha256,
                    __import__("json").dumps(
                        authority, sort_keys=True, separators=(",", ":")
                    ),
                    candidate_snapshot_sha256,
                ),
            )
            connection.execute("COMMIT")
        if fault_point == "after_intent":
            raise PlatformKernelFault(fault_point)
        write_json_atomic(authority_path, authority)
        if fault_point == "after_authority_write":
            raise PlatformKernelFault(fault_point)
        file_sha256 = sha256_file(authority_path)
        with self._connect(root) as connection:
            connection.execute("BEGIN IMMEDIATE")
            candidate = connection.execute(
                "SELECT * FROM platform_cutover_candidates "
                "WHERE platform=?",
                (platform,),
            ).fetchone()
            intent = connection.execute(
                "SELECT * FROM platform_cutover_intents WHERE intent_id=? "
                "AND state='PREPARED'",
                (intent_id,),
            ).fetchone()
            if candidate is None or (
                candidate["state"] != "PROVISIONAL"
                or candidate["candidate_run_id"]
                != evidence["guarded_delivery_evidence"]["run_id"]
                or intent is None
                or intent["candidate_snapshot_sha256"]
                != self._confirmation_snapshot_fingerprint(candidate, evidence)
            ):
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    "Bilibili activation evidence differs from its prepared candidate"
                )
            connection.execute(
                "INSERT INTO platform_cutover_authority("
                "platform,generation,evidence_sha256,authority_sha256) "
                "VALUES(?,?,?,?)",
                (platform, 1, evidence_sha256, file_sha256),
            )
            connection.execute(
                "UPDATE platform_cutover_intents SET state='COMMITTED' "
                "WHERE intent_id=?",
                (intent_id,),
            )
            confirmed_candidate = json.loads(candidate["candidate_json"])
            confirmed_candidate["state"] = "CONFIRMED"
            connection.execute(
                "UPDATE platform_cutover_candidates "
                "SET state='CONFIRMED',candidate_json=? "
                "WHERE platform=? AND state='PROVISIONAL'",
                (
                    json.dumps(
                        confirmed_candidate, sort_keys=True, separators=(",", ":")
                    ),
                    platform,
                ),
            )
            connection.execute("COMMIT")
        if fault_point == "after_control_commit":
            raise PlatformKernelFault(fault_point)
        return {
            "platform": platform,
            "generation": 1,
            "authority_path": str(authority_path),
            "authority_sha256": file_sha256,
            "platform_statuses": {
                "bilibili": "active_kernel",
                "youtube": "active_legacy",
            },
            "cutover_state": "CONFIRMED",
            "idempotent": False,
        }

    def reconcile(
        self, *, platform: str, control_store_root: Path
    ) -> dict[str, Any]:
        if platform != SUPPORTED_PLATFORM:
            raise ContractError("Only the Bilibili Platform Kernel Cutover is active")
        root = control_store_root.resolve()
        authority_path = root / PLATFORM_AUTHORITY_DIR / f"{platform}.json"
        with self._connect(root) as connection:
            connection.execute("BEGIN IMMEDIATE")
            pending = connection.execute(
                "SELECT * FROM platform_cutover_intents WHERE state='PREPARED'"
            ).fetchall()
            if len(pending) != 1:
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    "Bilibili Platform Kernel reconciliation requires one prepared intent"
                )
            intent = pending[0]
            authority = __import__("json").loads(intent["authority_json"])
            evidence_path = Path(str(authority.get("exit_evidence_path", ""))).resolve()
            if (
                not evidence_path.is_relative_to(PROJECT_ROOT)
                or not evidence_path.is_file()
                or sha256_file(evidence_path) != intent["evidence_sha256"]
            ):
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    "Interrupted Bilibili Platform Kernel Exit Evidence drifted"
                )
            evidence = _validate_evidence(read_json(evidence_path))
            _require_formal_exit_evidence(evidence_path)
            global_gate_binding = authority.get("global_gate_binding")
            if not isinstance(global_gate_binding, dict):
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    "Interrupted Bilibili Platform Kernel Global Gate binding is absent"
                )
            global_gate_path = Path(
                str(global_gate_binding.get("authority_path", ""))
            ).resolve()
            if (
                not global_gate_path.is_relative_to(root)
                or not global_gate_path.is_file()
                or sha256_file(global_gate_path)
                != global_gate_binding.get("authority_sha256")
            ):
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    "Interrupted Bilibili Platform Kernel Global Gate drifted"
                )
            if authority_path.is_file() and read_json(authority_path) != authority:
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    "Interrupted Bilibili Platform Kernel authority bytes conflict"
                )
            if not authority_path.is_file():
                authority_path.parent.mkdir(parents=True, exist_ok=True)
                write_json_atomic(authority_path, authority)
            file_sha256 = sha256_file(authority_path)
            candidate = connection.execute(
                "SELECT * FROM platform_cutover_candidates WHERE platform=?",
                (platform,),
            ).fetchone()
            if candidate is None:
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    "Bilibili provisional candidate is absent during reconciliation",
                    data={
                        "first_failing_gate": "platform_kernel_candidate",
                        "error_code": "bilibili_provisional_candidate_absent",
                    },
                )
            if (
                candidate["state"] != "PROVISIONAL"
                or candidate["candidate_run_id"]
                != evidence["guarded_delivery_evidence"]["run_id"]
            ):
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    "Interrupted activation candidate snapshot drifted",
                    data={
                        "first_failing_gate": "platform_kernel_candidate",
                        "error_code": "bilibili_candidate_confirmation_snapshot_drift",
                    },
                )
            current_snapshot_sha256 = self._confirmation_snapshot_fingerprint(
                candidate, evidence
            )
            prepared_snapshot_sha256 = intent["candidate_snapshot_sha256"]
            if prepared_snapshot_sha256 is None:
                self._require_confirmable_candidate(
                    root=root, evidence=evidence, candidate_row=candidate
                )
                changed = connection.execute(
                    "UPDATE platform_cutover_intents "
                    "SET candidate_snapshot_sha256=? "
                    "WHERE intent_id=? AND state='PREPARED' "
                    "AND candidate_snapshot_sha256 IS NULL",
                    (current_snapshot_sha256, intent["intent_id"]),
                ).rowcount
                if changed != 1:
                    connection.execute("ROLLBACK")
                    raise KernelConflict(
                        "Interrupted activation snapshot backfill lost its CAS fence"
                    )
                prepared_snapshot_sha256 = current_snapshot_sha256
            if prepared_snapshot_sha256 != current_snapshot_sha256:
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    "Interrupted activation candidate snapshot drifted",
                    data={
                        "first_failing_gate": "platform_kernel_candidate",
                        "error_code": "bilibili_candidate_confirmation_snapshot_drift",
                    },
                )
            current = connection.execute(
                "SELECT * FROM platform_cutover_authority WHERE platform=?",
                (platform,),
            ).fetchone()
            if current is None:
                connection.execute(
                    "INSERT INTO platform_cutover_authority("
                    "platform,generation,evidence_sha256,authority_sha256) "
                    "VALUES(?,?,?,?)",
                    (platform, 1, intent["evidence_sha256"], file_sha256),
                )
            elif (
                current["evidence_sha256"] != intent["evidence_sha256"]
                or current["authority_sha256"] != file_sha256
            ):
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    "Interrupted Bilibili Platform Kernel publication lost its fence"
                )
            connection.execute(
                "UPDATE platform_cutover_intents SET state='COMMITTED' "
                "WHERE intent_id=?",
                (intent["intent_id"],),
            )
            confirmed_candidate = self._candidate_snapshot(candidate)
            self._current_candidate_run(
                root=root, row=candidate, expected_stage="delivered"
            )
            confirmed_candidate["state"] = "CONFIRMED"
            connection.execute(
                "UPDATE platform_cutover_candidates "
                "SET state='CONFIRMED',candidate_json=? WHERE platform=?",
                (
                    json.dumps(
                        confirmed_candidate,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    platform,
                ),
            )
            connection.execute("COMMIT")
        return {
            "platform": platform,
            "generation": 1,
            "authority_path": str(authority_path),
            "authority_sha256": file_sha256,
            "authority_status": "current",
            "cutover_state": "CONFIRMED",
            "current": True,
            "reconciled": True,
        }

    def require_current(
        self, *, platform: str, control_store_root: Path
    ) -> dict[str, Any]:
        if platform != SUPPORTED_PLATFORM:
            raise ContractError("Only the Bilibili Platform Kernel Cutover is active")
        root = control_store_root.resolve()
        authority_path = root / PLATFORM_AUTHORITY_DIR / f"{platform}.json"
        with self._connect(root) as connection:
            current = connection.execute(
                "SELECT * FROM platform_cutover_authority WHERE platform=?",
                (platform,),
            ).fetchone()
            candidate = connection.execute(
                "SELECT state FROM platform_cutover_candidates WHERE platform=?",
                (platform,),
            ).fetchone()
            pending = connection.execute(
                "SELECT COUNT(*) FROM platform_cutover_intents WHERE state='PREPARED'"
            ).fetchone()[0]
        if current is None and candidate is not None and candidate["state"] != "CONFIRMED":
            raise KernelConflict(
                "Bilibili Platform Kernel candidate awaits confirmation",
                data={
                    "first_failing_gate": "platform_kernel_authority",
                    "error_code": "bilibili_platform_authority_pending_confirmation",
                },
            )
        if (
            current is None
            or pending
            or not authority_path.is_file()
            or sha256_file(authority_path) != current["authority_sha256"]
        ):
            raise KernelConflict(
                "Bilibili Platform Kernel authority is absent, stale, or incomplete",
                data={
                    "first_failing_gate": "platform_kernel_authority",
                    "error_code": "bilibili_platform_authority_stale",
                },
            )
        authority = read_json(authority_path)
        evidence_path = Path(authority.get("exit_evidence_path", ""))
        if (
            authority.get("platform") != platform
            or authority.get("authority_status") != "active_kernel"
            or authority.get("generation") != current["generation"]
            or authority.get("exit_evidence_sha256") != current["evidence_sha256"]
            or authority.get("authority_sha256")
            != _fingerprint(authority, "authority_sha256")
            or not evidence_path.is_file()
            or sha256_file(evidence_path) != current["evidence_sha256"]
        ):
            raise KernelConflict(
                "Bilibili Platform Kernel authority content conflicts with control state",
                data={
                    "first_failing_gate": "platform_kernel_authority",
                    "error_code": "bilibili_platform_authority_conflict",
                },
            )
        evidence = _validate_evidence(read_json(evidence_path))
        _require_formal_exit_evidence(evidence_path)
        return {
            "platform": platform,
            "generation": int(current["generation"]),
            "authority_path": str(authority_path),
            "authority_sha256": current["authority_sha256"],
            "exit_evidence_sha256": current["evidence_sha256"],
            "platform_statuses": evidence["platform_statuses"],
            "current": True,
        }

    def check_policy(
        self, *, platform: str, control_store_root: Path
    ) -> dict[str, Any]:
        current = self.require_current(
            platform=platform, control_store_root=control_store_root
        )
        return {
            "current": True,
            "platform_statuses": current["platform_statuses"],
            "platform_kernel_authority": current,
        }


__all__ = ["ACTIVATION_FAULT_POINTS", "BilibiliPlatformCutoverPublisher"]
