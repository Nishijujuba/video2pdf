from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any

from jsonschema import Draft202012Validator, ValidationError

from .errors import (
    ContractError,
    ControlStoreUnavailable,
    KernelConflict,
    PlatformKernelFault,
)
from .evidence import EvidenceSupportError, git_output, sha256_git_blob
from .global_gate import GlobalGatePublisher
from .guarded_delivery import (
    validate_acceptance_report,
    validate_delivery_guard_report,
)
from .utils import canonical_json_bytes, read_json, sha256_file, write_json_atomic


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
                "state TEXT NOT NULL CHECK(state IN ('PREPARED','COMMITTED')))"
            )
            return connection
        except (OSError, sqlite3.DatabaseError) as exc:
            raise ControlStoreUnavailable(
                "Platform cutover control store is unavailable"
            ) from exc

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
                "intent_id,platform,evidence_sha256,authority_json,state) "
                "VALUES(?,?,?,?, 'PREPARED')",
                (
                    intent_id,
                    platform,
                    evidence_sha256,
                    __import__("json").dumps(
                        authority, sort_keys=True, separators=(",", ":")
                    ),
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
            _validate_evidence(read_json(evidence_path))
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
            connection.execute("COMMIT")
        return {
            "platform": platform,
            "generation": 1,
            "authority_path": str(authority_path),
            "authority_sha256": file_sha256,
            "authority_status": "current",
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
            pending = connection.execute(
                "SELECT COUNT(*) FROM platform_cutover_intents WHERE state='PREPARED'"
            ).fetchone()[0]
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
