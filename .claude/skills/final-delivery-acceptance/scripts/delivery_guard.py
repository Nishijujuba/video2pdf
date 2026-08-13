#!/usr/bin/env python3
"""Mechanical Final Delivery Guard for video-to-PDF outputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

SRC_ROOT = Path(__file__).resolve().parents[4] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from video2pdf_workflow_kernel.acceptance_v2 import AcceptanceV2Provider
from video2pdf_workflow_kernel.errors import KernelError
from video2pdf_workflow_kernel.utils import (
    canonical_json_bytes,
    normalized_physical_path,
    write_json_atomic,
)

from validate_acceptance_report import (
    GateBlockedError,
    ValidationError as AcceptanceReportValidationError,
    compute_artifact_fingerprint,
    create_allowed_artifacts_manifest,
    validate_acceptance_report,
)


REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_CRITERIA = REPO_ROOT / "docs" / "acceptance" / "acceptance_criteria.v1.json"
DEFAULT_CURRENT_TARGET = REPO_ROOT / ".codex" / "delivery-targets" / "current.json"
DEFAULT_TASK_INDEX = REPO_ROOT / ".codex" / "delivery-targets" / "task-index.json"
SESSION_TARGETS_DIRNAME = "sessions"
ALLOWED_STAGES = {"generating", "ready_for_delivery", "accepted", "delivered", "blocked"}
GUARD_STAGES = {"ready_for_delivery", "accepted"}
OWNER_STATUSES = {"active", "blocked", "delivered", "abandoned", "superseded"}
HANDOFF_PREVIOUS_OWNER_STATUSES = {"abandoned", "superseded"}
COMPILE_REPORT_PRODUCER = "compile_latex_ascii.py"
COMPILE_REPORT_PRODUCER_CONTRACT = "latex_compile_guard.v1"
COMPILE_WRAPPER_RELATIVE = Path(".agents") / "skills" / "bilibili-render-pdf" / "scripts" / "compile_latex_ascii.py"
EXIT_PASS = 0
EXIT_INVALID = 1
EXIT_BLOCKED = 2


class GuardError(Exception):
    """Raised when the delivery guard must block delivery."""

    def __init__(self, message: str, *, first_failing_gate: str = "delivery_guard", error_code: str = "delivery_guard_failed") -> None:
        super().__init__(message)
        self.first_failing_gate = first_failing_gate
        self.error_code = error_code


class MissingTargetError(GuardError):
    """Raised when no active delivery target exists."""


@dataclass(frozen=True)
class DeliveryTarget:
    project_root: Path
    current_target_path: Path
    current_target: dict[str, Any]
    video_target: dict[str, Any]
    video_output_dir: Path
    target_file: Path
    final_pdf: Path
    main_tex: Path
    manifest_path: Path
    acceptance_report_path: Path
    guard_report_path: Path
    compile_report_path: Path
    global_gate_authority_path: Path
    global_gate_authority_sha256: str
    attempt_limit: int
    stage: str
    final_pdf_relative: str
    main_tex_relative: str
    manifest_relative: str
    acceptance_report_relative: str
    guard_report_relative: str
    compile_report_relative: str
    target_file_relative: str
    compile_provenance_required: bool
    legacy_existing_pdf: bool
    recompiled: bool
    kernel_authority: bool = False


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _path_under(base: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
    except ValueError:
        return False
    return True


def _repo_relative(project_root: Path, path: Path) -> str:
    return path.resolve().relative_to(project_root.resolve()).as_posix()


def _load_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        if label == "current target":
            raise MissingTargetError(f"current target not found: {path}") from exc
        raise GuardError(f"{label} not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise GuardError(f"{label} invalid JSON: {exc}") from exc


def _read_hook_input(stream: Any) -> dict[str, Any]:
    raw = stream.read()
    if not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GuardError(f"hook input invalid JSON: {exc}") from exc
    return _require_object(value, "hook input")


def _require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise GuardError(f"{label} must be an object")
    return value


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GuardError(f"{label} must be a non-empty string")
    return value


def _require_relative_path(value: Any, label: str, *, allow_dot: bool = False) -> str:
    raw = _require_string(value, label).replace("\\", "/")
    if allow_dot and raw == ".":
        return raw
    if raw.startswith("/") or _looks_windows_absolute(raw):
        raise GuardError(f"{label} must be a relative path")
    path = PurePosixPath(raw)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise GuardError(f"{label} must not contain empty, current, or parent path segments")
    return path.as_posix()


def _looks_windows_absolute(value: str) -> bool:
    return len(value) >= 3 and value[1] == ":" and value[2] in {"/", "\\"}


def _session_id_from_hook_input(hook_input: dict[str, Any]) -> str:
    return _validate_session_id(hook_input.get("session_id"), "hook input session_id")


def _validate_session_id(value: Any, label: str) -> str:
    session_id = _require_string(value, label).strip()
    if session_id in {".", ".."} or "/" in session_id or "\\" in session_id or ":" in session_id:
        raise GuardError(f"{label} must be a single safe path segment")
    return session_id


def _session_current_target_path(current_target_path: Path, session_id: str) -> Path:
    delivery_targets_dir = current_target_path.resolve().parent
    sessions_dir = delivery_targets_dir / SESSION_TARGETS_DIRNAME
    resolved = (sessions_dir / session_id / "current.json").resolve()
    if not _path_under(sessions_dir, resolved):
        raise GuardError("hook input session_id resolves outside the session target directory")
    return resolved


def _session_current_target_path_from_cli(current_target_path: Path, session_id: str) -> Path:
    resolved = current_target_path.resolve()
    if resolved.name == "current.json" and resolved.parent.parent.name == SESSION_TARGETS_DIRNAME:
        _validate_explicit_session_current_target_path(resolved, session_id)
        return resolved
    return _session_current_target_path(current_target_path, session_id)


def _validate_explicit_session_current_target_path(current_target_path: Path, session_id: str) -> None:
    path = current_target_path.resolve()
    if session_id in {".", ".."} or "/" in session_id or "\\" in session_id or ":" in session_id:
        raise GuardError("current target session_id must be a single safe path segment")
    if (
        path.name != "current.json"
        or path.parent.parent.name != SESSION_TARGETS_DIRNAME
        or path.parent.parent.parent.name != "delivery-targets"
    ):
        raise GuardError(
            "current target path must be under a delivery-targets/sessions/<session_id>/current.json tree"
        )
    if path.parent.name != session_id:
        raise GuardError("current target path session_id must match current target session_id")


def _resolve_project_path(project_root: Path, value: Any, label: str) -> Path:
    raw = _require_string(value, label)
    if Path(raw).is_absolute() or _looks_windows_absolute(raw):
        resolved = Path(raw).resolve()
    else:
        normalized = _require_relative_path(raw, label)
        resolved = (project_root / normalized).resolve()
    if not _path_under(project_root, resolved):
        raise GuardError(f"{label} escapes project boundary: {raw}")
    return resolved


def _resolve_video_path(video_output_dir: Path, value: Any, label: str) -> tuple[Path, str]:
    normalized = _require_relative_path(value, label)
    resolved = (video_output_dir / normalized).resolve()
    if not _path_under(video_output_dir, resolved):
        raise GuardError(f"{label} escapes video output directory: {value}")
    return resolved, normalized


def _validate_stage(value: Any, label: str) -> str:
    stage = _require_string(value, label)
    if stage not in ALLOWED_STAGES:
        raise GuardError(f"{label} is invalid: {stage}")
    return stage


def _validate_attempt_limit(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise GuardError("delivery_target.attempt_limit must be an integer")
    if value != 3:
        raise GuardError("delivery_target.attempt_limit must be 3")
    return value


def _validate_optional_bool(value: Any, label: str, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise GuardError(f"{label} must be a boolean")
    return value


def _validate_compile_provenance_policy(
    *,
    required: bool,
    legacy_existing_pdf: bool,
    recompiled: bool,
    recompiled_declared: bool,
) -> None:
    if not required and not (legacy_existing_pdf and recompiled_declared and not recompiled):
        raise GuardError(
            "delivery_target.compile_provenance_required may be false only for legacy_existing_pdf targets "
            "when recompiled is explicitly false"
        )
    if recompiled and not required:
        raise GuardError("recompiled delivery targets must require final compile provenance")


def _require_keys(value: dict[str, Any], keys: set[str], label: str) -> None:
    missing = keys - set(value)
    if missing:
        raise GuardError(f"{label} missing fields: {', '.join(sorted(missing))}")


def _validate_current_target_schema(
    current: dict[str, Any],
    *,
    expected_session_id: str | None = None,
    require_session_scope: bool = False,
) -> None:
    schema_version = current.get("schema_version")
    if require_session_scope or expected_session_id is not None:
        if schema_version != "1.1":
            raise GuardError("current target schema_version must be '1.1' for a session-scoped delivery target")
    if schema_version == "1.0":
        return
    if schema_version == "1.1":
        if current.get("scope") != "session":
            raise GuardError("current target scope must be 'session' for schema_version '1.1'")
        session_id = _require_string(current.get("session_id"), "current target session_id")
        if expected_session_id is not None and session_id != expected_session_id:
            raise GuardError("current target session_id does not match hook input session_id")
        return
    raise GuardError("current target schema_version must be '1.0' or '1.1'")


def resolve_delivery_target(
    *,
    project_root: Path,
    current_target_path: Path = DEFAULT_CURRENT_TARGET,
    require_session_scope: bool = False,
) -> DeliveryTarget:
    """Resolve and validate the active project and video delivery targets."""

    project_root = project_root.resolve()
    current_target_path = current_target_path.resolve()
    current = _require_object(_load_json(current_target_path, "current target"), "current target")
    if current.get("schema_name") == "kernel-session-delivery-target":
        return _resolve_kernel_delivery_target(
            project_root=project_root,
            current_target_path=current_target_path,
            current=current,
            require_session_scope=require_session_scope,
        )
    _require_keys(
        current,
        {"schema_version", "stage", "video_output_dir", "target_file", "source_skill", "updated_at"},
        "current target",
    )
    _validate_current_target_schema(current, require_session_scope=require_session_scope)
    if require_session_scope:
        session_id = _require_string(current.get("session_id"), "current target session_id")
        _validate_explicit_session_current_target_path(current_target_path, session_id)
    stage = _validate_stage(current["stage"], "current target stage")
    video_output_dir = _resolve_project_path(project_root, current["video_output_dir"], "current target video_output_dir")
    target_file = _resolve_project_path(project_root, current["target_file"], "current target target_file")
    if not _path_under(video_output_dir, target_file):
        raise GuardError("current target target_file must stay inside video_output_dir")

    video_target = _require_object(_load_json(target_file, "delivery target"), "delivery target")
    _require_keys(
        video_target,
        {
            "schema_version",
            "stage",
            "video_output_dir",
            "final_pdf",
            "main_tex",
            "allowed_artifacts_manifest",
            "acceptance_report",
            "delivery_guard_report",
            "global_gate_authority",
            "attempt_limit",
        },
        "delivery target",
    )
    if video_target["schema_version"] != "1.0":
        raise GuardError("delivery target schema_version must be '1.0'")
    video_stage = _validate_stage(video_target["stage"], "delivery target stage")
    if video_stage != stage:
        raise GuardError("current target stage and delivery target stage disagree")

    video_dir_value = _require_relative_path(video_target["video_output_dir"], "delivery_target.video_output_dir", allow_dot=True)
    if video_dir_value != ".":
        nested_video_dir = (video_output_dir / video_dir_value).resolve()
        if nested_video_dir != video_output_dir.resolve():
            raise GuardError("delivery_target.video_output_dir must resolve to the active video output directory")

    final_pdf, final_pdf_relative = _resolve_video_path(video_output_dir, video_target["final_pdf"], "delivery_target.final_pdf")
    main_tex, main_tex_relative = _resolve_video_path(video_output_dir, video_target["main_tex"], "delivery_target.main_tex")
    manifest_path, manifest_relative = _resolve_video_path(
        video_output_dir,
        video_target["allowed_artifacts_manifest"],
        "delivery_target.allowed_artifacts_manifest",
    )
    acceptance_report_path, acceptance_report_relative = _resolve_video_path(
        video_output_dir,
        video_target["acceptance_report"],
        "delivery_target.acceptance_report",
    )
    guard_report_path, guard_report_relative = _resolve_video_path(
        video_output_dir,
        video_target["delivery_guard_report"],
        "delivery_target.delivery_guard_report",
    )
    compile_report_path, compile_report_relative = _resolve_video_path(
        video_output_dir,
        video_target.get("compile_report", "review/latex/compile_report.json"),
        "delivery_target.compile_report",
    )
    gate_binding = _require_object(video_target["global_gate_authority"], "delivery_target.global_gate_authority")
    _require_keys(gate_binding, {"path", "sha256"}, "delivery_target.global_gate_authority")
    if set(gate_binding) != {"path", "sha256"}:
        raise GuardError("delivery_target.global_gate_authority contains unsupported fields")
    global_gate_authority_path = _resolve_project_path(
        project_root, gate_binding["path"], "delivery_target.global_gate_authority.path"
    )
    global_gate_authority_sha256 = _require_string(
        gate_binding["sha256"], "delivery_target.global_gate_authority.sha256"
    )
    if len(global_gate_authority_sha256) != 64 or any(character not in "0123456789abcdef" for character in global_gate_authority_sha256):
        raise GuardError("delivery_target.global_gate_authority.sha256 must be a lowercase SHA-256")
    compile_provenance_required = _validate_optional_bool(
        video_target.get("compile_provenance_required"),
        "delivery_target.compile_provenance_required",
        True,
    )
    legacy_existing_pdf = _validate_optional_bool(
        video_target.get("legacy_existing_pdf"),
        "delivery_target.legacy_existing_pdf",
        False,
    )
    recompiled = _validate_optional_bool(
        video_target.get("recompiled"),
        "delivery_target.recompiled",
        False,
    )
    _validate_compile_provenance_policy(
        required=compile_provenance_required,
        legacy_existing_pdf=legacy_existing_pdf,
        recompiled=recompiled,
        recompiled_declared="recompiled" in video_target,
    )
    attempt_limit = _validate_attempt_limit(video_target["attempt_limit"])

    return DeliveryTarget(
        project_root=project_root,
        current_target_path=current_target_path,
        current_target=current,
        video_target=video_target,
        video_output_dir=video_output_dir,
        target_file=target_file,
        final_pdf=final_pdf,
        main_tex=main_tex,
        manifest_path=manifest_path,
        acceptance_report_path=acceptance_report_path,
        guard_report_path=guard_report_path,
        compile_report_path=compile_report_path,
        global_gate_authority_path=global_gate_authority_path,
        global_gate_authority_sha256=global_gate_authority_sha256,
        attempt_limit=attempt_limit,
        stage=stage,
        final_pdf_relative=final_pdf_relative,
        main_tex_relative=main_tex_relative,
        manifest_relative=manifest_relative,
        acceptance_report_relative=acceptance_report_relative,
        guard_report_relative=guard_report_relative,
        compile_report_relative=compile_report_relative,
        target_file_relative=target_file.resolve().relative_to(video_output_dir.resolve()).as_posix(),
        compile_provenance_required=compile_provenance_required,
        legacy_existing_pdf=legacy_existing_pdf,
        recompiled=recompiled,
    )


def _resolve_kernel_delivery_target(
    *,
    project_root: Path,
    current_target_path: Path,
    current: dict[str, Any],
    require_session_scope: bool,
) -> DeliveryTarget:
    """Resolve committed Bilibili Kernel authority without publishing state."""

    _require_keys(
        current,
        {
            "schema_name", "schema_version", "projection_kind", "projection_revision",
            "projection_path", "session_id", "run_id", "run_revision",
            "lifecycle_intent_id", "stage", "ownership_generation", "owner_status",
            "video_output_dir", "video_target",
        },
        "Kernel session delivery target",
    )
    if current.get("schema_version") != "1.0.0" or current.get("projection_kind") != "session_target":
        raise GuardError("Kernel session delivery projection contract is invalid")
    session_id = _validate_session_id(current["session_id"], "Kernel session target session_id")
    if require_session_scope:
        _validate_explicit_session_current_target_path(current_target_path, session_id)
    if Path(_require_string(current["projection_path"], "Kernel session target projection_path")).resolve() != current_target_path:
        raise GuardError("Kernel session projection path is stale", first_failing_gate="kernel_projection", error_code="kernel_projection_stale")

    stage = _validate_stage(current["stage"], "Kernel session target stage")
    video_output_dir = _resolve_project_path(project_root, current["video_output_dir"], "Kernel video_output_dir")
    video_binding = _require_object(current["video_target"], "Kernel session target video_target")
    target_file = _resolve_project_path(project_root, video_binding.get("path"), "Kernel video target path")
    if not _path_under(video_output_dir, target_file):
        raise GuardError("Kernel video target escapes video output directory")
    video_target = _require_object(_load_json(target_file, "Kernel video target"), "Kernel video target")
    _require_keys(
        video_target,
        {
            "schema_name", "schema_version", "projection_kind", "projection_revision",
            "run_id", "run_revision", "lifecycle_intent_id", "video_output_dir",
            "stage", "ownership", "artifacts", "global_gate_authority",
        },
        "Kernel video delivery target",
    )
    if video_target.get("schema_name") != "kernel-delivery-target" or video_target.get("schema_version") != "1.0.0":
        raise GuardError("Kernel video delivery projection contract is invalid")

    run_path = video_output_dir / "workflow" / "run.json"
    run_record = _require_object(_load_json(run_path, "Kernel Run Record"), "Kernel Run Record")
    if run_record.get("schema_name") != "run-record" or run_record.get("schema_version") != "4.0.0":
        raise GuardError("Bilibili Kernel delivery requires Run Record v4")
    if run_record.get("canonical_platform") not in ("bilibili", "youtube") or run_record.get("platform_adapter") not in ("bilibili", "youtube"):
        raise GuardError("Kernel delivery authority is restricted to Bilibili or YouTube")

    run_id = _require_string(current["run_id"], "Kernel run_id")
    intent_id = _require_string(current["lifecycle_intent_id"], "Kernel lifecycle_intent_id")
    run_revision = current["run_revision"]
    ownership_generation = current["ownership_generation"]
    if not isinstance(run_revision, int) or isinstance(run_revision, bool) or run_revision < 1:
        raise GuardError("Kernel run_revision must be a positive integer")
    if not isinstance(ownership_generation, int) or isinstance(ownership_generation, bool) or ownership_generation < 1:
        raise GuardError("Kernel ownership_generation must be a positive integer")
    ownership = _require_object(video_target["ownership"], "Kernel video target ownership")
    identity_values = (
        run_record.get("run_id"), video_target.get("run_id"),
        run_record.get("last_mutation_intent_id"), video_target.get("lifecycle_intent_id"),
    )
    if identity_values != (run_id, run_id, intent_id, intent_id):
        raise GuardError("Kernel delivery authority identity is stale", first_failing_gate="kernel_authority", error_code="kernel_authority_stale")
    if any(value != run_revision for value in (run_record.get("coordination_revision"), video_target.get("run_revision"))):
        raise GuardError("Kernel delivery projection revision is stale", first_failing_gate="kernel_projection", error_code="kernel_projection_stale")
    if video_target.get("stage") != stage or run_record.get("delivery", {}).get("stage") != stage:
        raise GuardError("Kernel delivery projection stage is stale", first_failing_gate="kernel_projection", error_code="kernel_projection_stale")
    if ownership != {"session_id": session_id, "generation": ownership_generation} or run_record.get("delivery", {}).get("ownership") != ownership:
        raise GuardError("Kernel delivery ownership projection is stale", first_failing_gate="kernel_projection", error_code="kernel_projection_stale")

    try:
        successor_authority = AcceptanceV2Provider(
            SRC_ROOT.parent
        ).require_committed_delivery_successor(
            workspace_root=video_output_dir / "review" / "acceptance"
        )
    except KernelError as exc:
        data = getattr(exc, "data", {}) or {}
        raise GuardError(
            str(exc),
            first_failing_gate=data.get("first_failing_gate", "kernel_authority"),
            error_code=data.get(
                "error_code", "acceptance_delivery_successor_invalid"
            ),
        ) from exc
    if successor_authority != {
        "run_id": run_id,
        "run_revision": run_revision,
        "lifecycle_intent_id": intent_id,
        "run_record_sha256": _file_sha256(run_path),
    }:
        raise GuardError(
            "Provider-proven Delivery Lifecycle successor disagrees with projections",
            first_failing_gate="kernel_authority",
            error_code="kernel_authority_stale",
        )

    task_ref = _require_object(run_record["delivery"]["projections"]["task_index"], "Kernel Run task-index projection")
    task_index_path = _resolve_project_path(project_root, task_ref["path"], "Kernel task-index projection path")
    task_index = _require_object(_load_json(task_index_path, "Kernel task index"), "Kernel task index")
    _require_keys(
        task_index,
        {
            "schema_name",
            "schema_version",
            "projection_kind",
            "projection_revision",
            "entries",
        },
        "Kernel task index",
    )
    if (
        task_index.get("schema_name") != "kernel-delivery-task-index"
        or task_index.get("schema_version") != "1.0.0"
        or task_index.get("projection_kind") != "task_index"
        or not isinstance(task_index.get("projection_revision"), int)
    ):
        raise GuardError(
            "Kernel task-index projection contract is invalid",
            first_failing_gate="kernel_projection",
            error_code="kernel_projection_stale",
        )
    entries = task_index.get("entries")
    own_entries = (
        [
            item
            for item in entries
            if isinstance(item, dict) and item.get("run_id") == run_id
        ]
        if isinstance(entries, list)
        else []
    )
    if len(own_entries) != 1:
        raise GuardError("Kernel task-index projection is missing", first_failing_gate="kernel_projection", error_code="kernel_projection_stale")
    entry = own_entries[0]

    projections = run_record["delivery"]["projections"]
    actual_projections = {
        "video_target": (target_file, video_target.get("projection_revision")),
        "session_target": (current_target_path, current.get("projection_revision")),
    }
    for name, (path, revision) in actual_projections.items():
        reference = _require_object(projections[name], f"Kernel Run {name} projection")
        if reference.get("projection_revision") != revision or reference.get("sha256") != _file_sha256(path):
            raise GuardError("Kernel delivery projection is stale", first_failing_gate="kernel_projection", error_code="kernel_projection_stale")
    task_index_sha = _file_sha256(task_index_path)
    task_reference_revision = task_ref.get("projection_revision")
    acceptance_input = _require_object(
        _load_json(
            video_output_dir / "review" / "acceptance" / "input-binding.json",
            "Kernel Acceptance input binding",
        ),
        "Kernel Acceptance input binding",
    )
    acceptance_run = _require_object(
        acceptance_input.get("run"), "Kernel Acceptance Run binding"
    )
    delivery_control_root = Path(
        _require_string(
            acceptance_run.get("control_store_root"),
            "Kernel Acceptance Control Store root",
        )
    ).resolve()
    if (
        not isinstance(task_reference_revision, int)
        or task_reference_revision > task_index["projection_revision"]
        or not _kernel_task_index_publications_are_committed(
            control_store_root=delivery_control_root,
            task_index_path=task_index_path,
            bound_sha256=task_ref.get("sha256"),
            current_sha256=task_index_sha,
        )
    ):
        raise GuardError("Kernel delivery projection is stale", first_failing_gate="kernel_projection", error_code="kernel_projection_stale")
    if video_binding.get("projection_revision") != video_target.get("projection_revision") or video_binding.get("sha256") != _file_sha256(target_file):
        raise GuardError("Kernel session-to-video projection is stale", first_failing_gate="kernel_projection", error_code="kernel_projection_stale")
    if any(entry.get(field) != expected for field, expected in {
        "run_revision": run_revision, "lifecycle_intent_id": intent_id, "stage": stage,
        "session_id": session_id, "ownership_generation": ownership_generation,
    }.items()):
        raise GuardError("Kernel task-index entry is stale", first_failing_gate="kernel_projection", error_code="kernel_projection_stale")
    entry_video = _require_object(entry.get("video_target"), "Kernel task-index video target")
    entry_session = _require_object(entry.get("session_target"), "Kernel task-index session target")
    if (
        Path(_require_string(entry_video.get("path"), "Kernel task-index video path")).resolve()
        != target_file
        or entry_video.get("projection_revision")
        != video_target.get("projection_revision")
        or entry_video.get("sha256") != _file_sha256(target_file)
        or Path(_require_string(entry_session.get("path"), "Kernel task-index session path")).resolve()
        != current_target_path
        or entry_session.get("projection_revision")
        != current.get("projection_revision")
        or entry_session.get("sha256") != _file_sha256(current_target_path)
    ):
        raise GuardError("Kernel task-index entry is stale", first_failing_gate="kernel_projection", error_code="kernel_projection_stale")

    artifacts = _require_object(video_target["artifacts"], "Kernel delivery artifacts")
    resolved_artifacts: dict[str, Path] = {}
    for role in ("final_pdf", "main_tex", "final_compile_report", "acceptance_report"):
        binding = _require_object(artifacts.get(role), f"Kernel artifact {role}")
        path = _resolve_project_path(project_root, binding.get("path"), f"Kernel artifact {role} path")
        if not _path_under(video_output_dir, path) or binding.get("sha256") != _file_sha256(path):
            raise GuardError(f"Kernel artifact authority is stale: {role}")
        resolved_artifacts[role] = path
    gate_binding = _require_object(video_target["global_gate_authority"], "Kernel Global Gate authority")
    gate_path = _resolve_project_path(project_root, gate_binding.get("path"), "Kernel Global Gate authority path")
    gate_sha = _require_string(gate_binding.get("sha256"), "Kernel Global Gate authority sha256")

    manifest_path = video_output_dir / "review" / "acceptance" / "allowed_artifacts_manifest.json"
    guard_report_path = video_output_dir / "review" / "acceptance" / "delivery_guard_report.json"
    return DeliveryTarget(
        project_root=project_root, current_target_path=current_target_path, current_target=current,
        video_target=video_target, video_output_dir=video_output_dir, target_file=target_file,
        final_pdf=resolved_artifacts["final_pdf"], main_tex=resolved_artifacts["main_tex"],
        manifest_path=manifest_path, acceptance_report_path=resolved_artifacts["acceptance_report"],
        guard_report_path=guard_report_path, compile_report_path=resolved_artifacts["final_compile_report"],
        global_gate_authority_path=gate_path, global_gate_authority_sha256=gate_sha,
        attempt_limit=3, stage=stage,
        final_pdf_relative=resolved_artifacts["final_pdf"].relative_to(video_output_dir).as_posix(),
        main_tex_relative=resolved_artifacts["main_tex"].relative_to(video_output_dir).as_posix(),
        manifest_relative=manifest_path.relative_to(video_output_dir).as_posix(),
        acceptance_report_relative=resolved_artifacts["acceptance_report"].relative_to(video_output_dir).as_posix(),
        guard_report_relative=guard_report_path.relative_to(video_output_dir).as_posix(),
        compile_report_relative=resolved_artifacts["final_compile_report"].relative_to(video_output_dir).as_posix(),
        target_file_relative=target_file.relative_to(video_output_dir).as_posix(),
        compile_provenance_required=True, legacy_existing_pdf=False, recompiled=False,
        kernel_authority=True,
    )


def _kernel_task_index_publications_are_committed(
    *,
    control_store_root: Path,
    task_index_path: Path,
    bound_sha256: Any,
    current_sha256: str,
) -> bool:
    if not isinstance(bound_sha256, str):
        return False
    database_path = (
        control_store_root.resolve() / ".workflow-control" / "control.sqlite3"
    )
    normalized_path = normalized_physical_path(task_index_path)
    try:
        with sqlite3.connect(
            f"file:{database_path.as_posix()}?mode=ro", uri=True
        ) as connection:
            held = connection.execute(
                "SELECT COUNT(*) FROM projection_publication_slots "
                "WHERE normalized_path=? AND state='HELD'",
                (normalized_path,),
            ).fetchone()[0]
            committed = {
                str(row[0])
                for row in connection.execute(
                    "SELECT slots.proposed_sha256 "
                    "FROM projection_publication_slots AS slots "
                    "JOIN delivery_lifecycle_intents AS intents "
                    "ON intents.intent_id=slots.intent_id "
                    "WHERE slots.normalized_path=? "
                    "AND slots.proposed_state='present' "
                    "AND slots.state='RELEASED' "
                    "AND intents.state='COMMITTED'",
                    (normalized_path,),
                ).fetchall()
            }
    except (sqlite3.DatabaseError, OSError):
        return False
    return held == 0 and {bound_sha256, current_sha256}.issubset(committed)


def _condition(name: str, status: str, message: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"condition": name, "status": status}
    if message:
        result["message"] = message
    return result


def _load_manifest(target: DeliveryTarget) -> dict[str, Any]:
    manifest = _require_object(_load_json(target.manifest_path, "allowed artifacts manifest"), "allowed artifacts manifest")
    artifacts = manifest.get("final_artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise GuardError("allowed artifacts manifest has no final_artifacts")
    return manifest


def _manifest_paths(manifest: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for index, artifact in enumerate(manifest["final_artifacts"]):
        item = _require_object(artifact, f"manifest.final_artifacts[{index}]")
        path = _require_relative_path(item.get("path"), f"manifest.final_artifacts[{index}].path")
        paths.append(path)
    return paths


def _ensure_final_pdf_in_manifest(target: DeliveryTarget, manifest: dict[str, Any]) -> None:
    paths = set(_manifest_paths(manifest))
    if target.final_pdf_relative not in paths:
        raise GuardError("final PDF is absent from allowed_artifacts_manifest.json")


def _pdf_page_count(path: Path) -> int:
    try:
        import fitz
    except ImportError as exc:
        raise GuardError("PyMuPDF is required for delivery guard PDF page counting") from exc
    try:
        doc = fitz.open(path)
    except Exception as exc:
        raise GuardError(f"cannot open final PDF for page counting: {path}") from exc
    try:
        count = len(doc)
    finally:
        doc.close()
    if count < 1:
        raise GuardError("final PDF contains no pages")
    return count


def _ensure_rendered_page_coverage(target: DeliveryTarget) -> None:
    page_count = _pdf_page_count(target.final_pdf)
    rendered_dir = target.video_output_dir / "review" / "acceptance" / "rendered_pages"
    if not rendered_dir.exists():
        raise GuardError("rendered page evidence directory is missing")
    missing = [
        f"review/acceptance/rendered_pages/page_{page_number:04d}.png"
        for page_number in range(1, page_count + 1)
        if not (rendered_dir / f"page_{page_number:04d}.png").exists()
    ]
    if missing:
        raise GuardError(f"rendered page evidence is missing: {', '.join(missing)}")


def _require_compile_report_string(report: dict[str, Any], key: str) -> str:
    value = report.get(key)
    if not isinstance(value, str) or not value.strip():
        raise GuardError(f"malformed final compile report: {key} must be a non-empty string")
    return value


def _resolve_compile_report_absolute_path(value: str, label: str) -> Path:
    if not Path(value).is_absolute() and not _looks_windows_absolute(value):
        raise GuardError(f"malformed final compile report: {label} must be absolute")
    return Path(value).resolve()


def _compile_file_fingerprint(path: Path, label: str) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        raise GuardError(f"final compile report {label} path is missing: {path}")
    raw = path.read_bytes()
    return {
        "algorithm": "sha256",
        "sha256": hashlib.sha256(raw).hexdigest(),
        "size_bytes": len(raw),
    }


def _ensure_compile_fingerprint_current(report: dict[str, Any], path: Path, key: str) -> None:
    fingerprint = report.get(key)
    if not isinstance(fingerprint, dict):
        raise GuardError(f"malformed final compile report: {key} must be an object")
    current = _compile_file_fingerprint(path, key)
    algorithm = fingerprint.get("algorithm")
    if algorithm not in {None, "sha256"}:
        raise GuardError(f"malformed final compile report: {key}.algorithm must be sha256")
    sha256 = fingerprint.get("sha256")
    size_bytes = fingerprint.get("size_bytes")
    valid_hashes = {current["sha256"], f"sha256:{current['sha256']}"}
    if not isinstance(sha256, str) or not isinstance(size_bytes, int):
        raise GuardError(f"malformed final compile report: {key} must include sha256 and size_bytes")
    if sha256 not in valid_hashes or size_bytes != current["size_bytes"]:
        raise GuardError(f"final compile report {key} is stale")


def _argv_declares_final_mode(argv: list[str]) -> bool:
    for index, token in enumerate(argv):
        if token == "--mode" and index + 1 < len(argv) and argv[index + 1] == "final":
            return True
        if token == "--mode=final":
            return True
    return False


def _ensure_compile_report_producer(report: dict[str, Any], target: DeliveryTarget) -> None:
    producer = _require_string(report.get("producer"), "final compile report.producer")
    if producer != COMPILE_REPORT_PRODUCER:
        raise GuardError(f"final compile report producer must be '{COMPILE_REPORT_PRODUCER}', got {producer}")
    producer_contract = _require_string(report.get("producer_contract"), "final compile report.producer_contract")
    if producer_contract != COMPILE_REPORT_PRODUCER_CONTRACT:
        raise GuardError(
            "final compile report producer_contract must be "
            f"'{COMPILE_REPORT_PRODUCER_CONTRACT}', got {producer_contract}"
        )
    producer_mode = _require_string(report.get("producer_mode"), "final compile report.producer_mode")
    if producer_mode != "final":
        raise GuardError(f"final compile report producer_mode must be 'final', got {producer_mode}")

    expected_wrapper = (target.project_root / COMPILE_WRAPPER_RELATIVE).resolve()
    wrapper_script = _resolve_compile_report_absolute_path(
        _require_compile_report_string(report, "wrapper_script"),
        "wrapper_script",
    )
    if wrapper_script != expected_wrapper:
        raise GuardError("final compile report wrapper_script does not match the guarded compile wrapper")
    _ensure_compile_fingerprint_current(report, expected_wrapper, "wrapper_script_fingerprint")

    argv = report.get("argv")
    if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
        raise GuardError("malformed final compile report: argv must be a list of strings")
    if not _argv_declares_final_mode(argv):
        raise GuardError("final compile report argv must include --mode final")


def _ensure_kernel_compile_provenance(target: DeliveryTarget) -> None:
    """Prove the Kernel final-compile-report/1.0.0 contract for kernel targets."""

    report = _require_object(
        _load_json(target.compile_report_path, "final compile report"),
        "final compile report",
    )
    schema_name = _require_string(
        report.get("schema_name"), "final compile report.schema_name"
    )
    if schema_name != "final-compile-report":
        raise GuardError(
            f"final compile report schema_name must be 'final-compile-report', got {schema_name}"
        )
    schema_version = _require_string(
        report.get("schema_version"), "final compile report.schema_version"
    )
    if schema_version != "1.0.0":
        raise GuardError(
            f"final compile report schema_version must be '1.0.0', got {schema_version}"
        )
    mode = _require_string(report.get("mode"), "final compile report.mode")
    if mode != "final":
        raise GuardError(f"final compile report mode must be 'final', got {mode}")
    status = _require_string(report.get("status"), "final compile report.status")
    if status != "pass":
        raise GuardError(f"final compile report status must be 'pass', got {status}")
    if report.get("delivery_authority") is not False:
        raise GuardError("final compile report delivery_authority must be false")
    report_sha256 = _require_string(
        report.get("report_sha256"), "final compile report.report_sha256"
    )
    expected_sha256 = hashlib.sha256(
        canonical_json_bytes(
            {key: value for key, value in report.items() if key != "report_sha256"}
        )
    ).hexdigest()
    if report_sha256 != expected_sha256:
        raise GuardError("final compile report report_sha256 is stale")

    provider = _require_object(
        report.get("compiler_provider"), "final compile report.compiler_provider"
    )
    provider_id = _require_string(
        provider.get("provider_id"),
        "final compile report.compiler_provider.provider_id",
    )
    provider_sha256 = _require_string(
        provider.get("provider_sha256"),
        "final compile report.compiler_provider.provider_sha256",
    )
    expected_provider_sha256 = _file_sha256(
        REPO_ROOT / "src" / "video2pdf_workflow_kernel" / "final_compile.py"
    )
    if (
        provider_id != "guarded-final-compile-provider"
        or provider_sha256 != expected_provider_sha256
    ):
        raise GuardError(
            "final compile report compiler_provider does not match the guarded final compile provider"
        )

    pdf = _require_object(report.get("pdf"), "final compile report.pdf")
    pdf_path = Path(_require_string(pdf.get("path"), "final compile report.pdf.path"))
    resolved_pdf = (
        pdf_path
        if pdf_path.is_absolute()
        else target.compile_report_path.parent / pdf_path
    ).resolve()
    if resolved_pdf != target.final_pdf.resolve():
        raise GuardError("final compile report pdf does not match delivery_target.final_pdf")
    if _require_string(
        pdf.get("sha256"), "final compile report.pdf.sha256"
    ) != _file_sha256(target.final_pdf):
        raise GuardError("final compile report final_pdf_fingerprint is stale")
    if not isinstance(pdf.get("size"), int) or pdf["size"] != target.final_pdf.stat().st_size:
        raise GuardError("final compile report final_pdf size is stale")

    closure = _require_object(
        report.get("dependency_closure"), "final compile report.dependency_closure"
    )
    if closure.get("complete") is not True:
        raise GuardError("final compile report dependency_closure must be complete")
    inputs = closure.get("inputs")
    if not isinstance(inputs, list):
        raise GuardError("malformed final compile report: dependency_closure.inputs must be a list")
    main_tex_sha = _file_sha256(target.main_tex)
    bound_tex = any(
        isinstance(item, dict)
        and item.get("logical_id") == "integrated_main"
        and _require_string(
            item.get("sha256"), "final compile report dependency input sha256"
        )
        == main_tex_sha
        for item in inputs
    )
    if not bound_tex:
        raise GuardError("final compile report source_tex does not match delivery_target.main_tex")


def _ensure_compile_provenance(target: DeliveryTarget) -> None:
    if not target.compile_provenance_required:
        return
    if target.kernel_authority:
        _ensure_kernel_compile_provenance(target)
        return
    if not target.compile_report_path.exists():
        raise GuardError(f"final compile report is missing: {target.compile_report_relative}")
    report = _require_object(_load_json(target.compile_report_path, "final compile report"), "final compile report")
    schema_version = _require_string(report.get("schema_version"), "final compile report.schema_version")
    if schema_version != "latex_compile_report.v1":
        raise GuardError(f"final compile report schema_version must be 'latex_compile_report.v1', got {schema_version}")
    mode = _require_string(report.get("mode"), "final compile report.mode")
    if mode != "final":
        raise GuardError(f"final compile report mode must be 'final', got {mode}")
    status = _require_string(report.get("status"), "final compile report.status")
    if status != "passed":
        raise GuardError(f"final compile report status must be 'passed', got {status}")
    _ensure_compile_report_producer(report, target)
    report_final_pdf = _resolve_compile_report_absolute_path(
        _require_compile_report_string(report, "final_pdf"),
        "final_pdf",
    )
    if report_final_pdf != target.final_pdf.resolve():
        raise GuardError("final compile report final_pdf does not match delivery_target.final_pdf")
    _ensure_compile_fingerprint_current(report, target.final_pdf, "final_pdf_fingerprint")
    report_source_tex = _resolve_compile_report_absolute_path(
        _require_compile_report_string(report, "source_tex"),
        "source_tex",
    )
    if report_source_tex != target.main_tex.resolve():
        raise GuardError("final compile report source_tex does not match delivery_target.main_tex")
    _ensure_compile_fingerprint_current(report, target.main_tex, "source_tex_fingerprint")
    if "main_tex" in report:
        report_main_tex = _resolve_compile_report_absolute_path(
            _require_compile_report_string(report, "main_tex"),
            "main_tex",
        )
        if report_main_tex != target.main_tex.resolve():
            raise GuardError("final compile report main_tex does not match delivery_target.main_tex")


def _fingerprint_file(path: Path, relative_path: str) -> dict[str, Any]:
    if not path.exists():
        raise GuardError(f"guard artifact not found: {relative_path}")
    try:
        return compute_artifact_fingerprint(path, relative_path)
    except AcceptanceReportValidationError as exc:
        raise GuardError(str(exc)) from exc


def guard_fingerprints(target: DeliveryTarget, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    ordered_paths = [
        target.main_tex_relative,
        target.final_pdf_relative,
        target.manifest_relative,
        target.acceptance_report_relative,
        *([target.compile_report_relative] if target.compile_provenance_required else []),
        target.target_file_relative,
    ]
    ordered_paths.extend(_manifest_paths(manifest))
    seen: set[str] = set()
    fingerprints: list[dict[str, Any]] = []
    current_target_relative = _repo_relative(target.project_root, target.current_target_path)
    fingerprints.append(_fingerprint_file(target.current_target_path, current_target_relative))
    seen.add(current_target_relative)
    global_gate_relative = _repo_relative(target.project_root, target.global_gate_authority_path)
    fingerprints.append(_fingerprint_file(target.global_gate_authority_path, global_gate_relative))
    seen.add(global_gate_relative)
    for relative_path in ordered_paths:
        if relative_path in seen:
            continue
        seen.add(relative_path)
        fingerprints.append(_fingerprint_file(target.video_output_dir / relative_path, relative_path))
    return fingerprints


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_valid_video_output_dir(candidate: Path, pdf_path: Path) -> bool:
    if not candidate.is_dir() or not _path_under(candidate, pdf_path):
        return False
    if not (candidate / "待删除").exists():
        return False
    durable_identity = [
        candidate / "main.tex",
        candidate / "outline_contract.md",
        candidate / "review",
    ]
    return any(path.exists() for path in durable_identity) or any(candidate.glob("section_*.tex"))


def infer_video_output_dir(project_root: Path, pdf_path: Path, explicit_video_output_dir: Path | None) -> Path:
    pdf_path = pdf_path.resolve()
    if explicit_video_output_dir is not None:
        video_output_dir = _resolve_project_path(project_root, str(explicit_video_output_dir), "video_output_dir")
        if not video_output_dir.is_dir():
            raise GuardError(f"video_output_dir not found: {video_output_dir}")
        if not _path_under(video_output_dir, pdf_path):
            raise GuardError("PDF must be inside the explicit video_output_dir")
        return video_output_dir

    matches: list[Path] = []
    for candidate in [pdf_path.parent, *pdf_path.parents]:
        if candidate == project_root.resolve():
            break
        if not _path_under(project_root, candidate):
            break
        if _is_valid_video_output_dir(candidate, pdf_path):
            matches.append(candidate.resolve())
    unique_matches = []
    for match in matches:
        if match not in unique_matches:
            unique_matches.append(match)
    if not unique_matches:
        raise GuardError("old PDF repair requires an explicit video_output_dir when the PDF is isolated")
    if len(unique_matches) > 1:
        raise GuardError("old PDF repair is ambiguous; provide an explicit video_output_dir")
    return unique_matches[0]


def _choose_main_tex(video_output_dir: Path, pdf_path: Path) -> str:
    same_stem_tex = video_output_dir / f"{pdf_path.stem}.tex"
    if same_stem_tex.exists() and _path_under(video_output_dir, same_stem_tex):
        return same_stem_tex.relative_to(video_output_dir).as_posix()

    try:
        pdf_hash = _file_sha256(pdf_path)
        for sibling_pdf in sorted(video_output_dir.glob("*.pdf")):
            if sibling_pdf.resolve() == pdf_path.resolve():
                continue
            if sibling_pdf.stat().st_size == pdf_path.stat().st_size and _file_sha256(sibling_pdf) == pdf_hash:
                sibling_tex = sibling_pdf.with_suffix(".tex")
                if sibling_tex.exists():
                    return sibling_tex.relative_to(video_output_dir).as_posix()
    except OSError:
        pass

    main_tex = video_output_dir / "main.tex"
    if main_tex.exists():
        return "main.tex"
    tex_files = sorted(video_output_dir.glob("*.tex"))
    if tex_files:
        return tex_files[0].relative_to(video_output_dir).as_posix()
    raise GuardError("cannot prepare old PDF repair without a TeX source inside video_output_dir")


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _task_index_message(reason: str) -> str:
    return f"TASK INDEX BLOCKED: {reason}"


def _validate_owner_status(value: Any, label: str) -> str:
    owner_status = _require_string(value, label)
    if owner_status not in OWNER_STATUSES:
        raise GuardError(f"{label} is invalid: {owner_status}")
    return owner_status


def _resolve_task_index_path(project_root: Path, task_index_path: Path) -> Path:
    resolved = task_index_path.resolve()
    if not _path_under(project_root, resolved):
        raise GuardError(f"task index path escapes project boundary: {task_index_path}")
    return resolved


def _task_entry_paths(project_root: Path, task: dict[str, Any], label: str) -> tuple[Path, Path]:
    video_relative = _require_relative_path(task.get("video_output_dir"), f"{label}.video_output_dir")
    target_relative = _require_relative_path(task.get("target_file"), f"{label}.target_file")
    video_output_dir = (project_root / video_relative).resolve()
    target_file = (project_root / target_relative).resolve()
    if not _path_under(project_root, video_output_dir):
        raise GuardError(f"{label}.video_output_dir escapes project boundary")
    if not _path_under(project_root, target_file):
        raise GuardError(f"{label}.target_file escapes project boundary")
    if not _path_under(video_output_dir, target_file):
        raise GuardError(f"{label}.target_file must stay inside video_output_dir")
    return video_output_dir, target_file


def _validate_task_index(project_root: Path, index: dict[str, Any]) -> dict[str, Any]:
    if index.get("schema_version") != "1.0":
        raise GuardError("task-index schema_version must be '1.0'")
    tasks = index.get("tasks")
    if not isinstance(tasks, list):
        raise GuardError("task-index tasks must be a list")
    active_by_video_dir: dict[Path, str] = {}
    for task_number, item in enumerate(tasks):
        label = f"task-index.tasks[{task_number}]"
        task = _require_object(item, label)
        _require_keys(
            task,
            {
                "video_output_dir",
                "target_file",
                "owner_session_id",
                "owner_status",
                "last_session_id",
                "stage",
                "updated_at",
            },
            label,
        )
        video_output_dir, _target_file = _task_entry_paths(project_root, task, label)
        owner_session_id = _validate_session_id(task.get("owner_session_id"), f"{label}.owner_session_id")
        _validate_session_id(task.get("last_session_id"), f"{label}.last_session_id")
        if "continued_from_session_id" in task:
            _validate_session_id(task.get("continued_from_session_id"), f"{label}.continued_from_session_id")
        owner_status = _validate_owner_status(task.get("owner_status"), f"{label}.owner_status")
        _validate_stage(task.get("stage"), f"{label}.stage")
        _require_string(task.get("updated_at"), f"{label}.updated_at")
        if owner_status == "active":
            active_owner = active_by_video_dir.get(video_output_dir)
            if active_owner is not None:
                raise GuardError(
                    "task-index has multiple active owners for video_output_dir: "
                    f"{task['video_output_dir']} ({active_owner}, {owner_session_id})"
                )
            active_by_video_dir[video_output_dir] = owner_session_id
    return index


def _load_task_index(project_root: Path, task_index_path: Path) -> dict[str, Any]:
    task_index_path = _resolve_task_index_path(project_root, task_index_path)
    if not task_index_path.exists():
        return {"schema_version": "1.0", "tasks": []}
    index = _require_object(_load_json(task_index_path, "task index"), "task index")
    return _validate_task_index(project_root, index)


def _write_task_index(project_root: Path, task_index_path: Path, index: dict[str, Any]) -> None:
    task_index_path = _resolve_task_index_path(project_root, task_index_path)
    _validate_task_index(project_root, index)
    _write_json(task_index_path, index)


def _canonical_task_paths(
    project_root: Path,
    *,
    video_output_dir: Path,
    target_file: Path,
) -> tuple[Path, Path, str, str]:
    resolved_video_output_dir = _resolve_project_path(project_root, str(video_output_dir), "video_output_dir")
    resolved_target_file = _resolve_project_path(project_root, str(target_file), "target_file")
    if not _path_under(resolved_video_output_dir, resolved_target_file):
        raise GuardError("target_file must stay inside video_output_dir")
    return (
        resolved_video_output_dir,
        resolved_target_file,
        _repo_relative(project_root, resolved_video_output_dir),
        _repo_relative(project_root, resolved_target_file),
    )


def _active_task_for_video_dir(
    project_root: Path,
    index: dict[str, Any],
    video_output_dir: Path,
) -> dict[str, Any] | None:
    for item in index["tasks"]:
        task = _require_object(item, "task-index task")
        task_video_output_dir, _target_file = _task_entry_paths(project_root, task, "task-index task")
        if task_video_output_dir == video_output_dir.resolve() and task.get("owner_status") == "active":
            return task
    return None


def task_claim(
    *,
    project_root: Path,
    task_index_path: Path,
    session_id: str,
    video_output_dir: Path,
    target_file: Path,
    stage: str,
) -> tuple[int, str]:
    project_root = project_root.resolve()
    try:
        session_id = _validate_session_id(session_id, "session_id")
        stage = _validate_stage(stage, "stage")
        resolved_video_output_dir, _resolved_target_file, video_relative, target_relative = _canonical_task_paths(
            project_root,
            video_output_dir=video_output_dir,
            target_file=target_file,
        )
        index = _load_task_index(project_root, task_index_path)
        active_task = _active_task_for_video_dir(project_root, index, resolved_video_output_dir)
        if active_task is not None:
            if active_task["owner_session_id"] != session_id:
                raise GuardError(
                    "video_output_dir already has active owner_session_id "
                    f"{active_task['owner_session_id']}"
                )
            active_task["target_file"] = target_relative
            active_task["last_session_id"] = session_id
            active_task["stage"] = stage
            active_task["updated_at"] = _now_iso()
            _write_task_index(project_root, task_index_path, index)
            return EXIT_PASS, f"RESUMED: {_resolve_task_index_path(project_root, task_index_path)}"

        index["tasks"].append(
            {
                "video_output_dir": video_relative,
                "target_file": target_relative,
                "owner_session_id": session_id,
                "owner_status": "active",
                "last_session_id": session_id,
                "stage": stage,
                "updated_at": _now_iso(),
            }
        )
        _write_task_index(project_root, task_index_path, index)
        return EXIT_PASS, f"CLAIMED: {_resolve_task_index_path(project_root, task_index_path)}"
    except GuardError as exc:
        return EXIT_BLOCKED, _task_index_message(str(exc))


def task_handoff(
    *,
    project_root: Path,
    task_index_path: Path,
    from_session_id: str,
    to_session_id: str,
    video_output_dir: Path,
    target_file: Path,
    stage: str,
    previous_owner_status: str,
) -> tuple[int, str]:
    project_root = project_root.resolve()
    try:
        from_session_id = _validate_session_id(from_session_id, "from_session_id")
        to_session_id = _validate_session_id(to_session_id, "to_session_id")
        if from_session_id == to_session_id:
            raise GuardError("from_session_id and to_session_id must be different")
        stage = _validate_stage(stage, "stage")
        previous_owner_status = _validate_owner_status(previous_owner_status, "previous_owner_status")
        if previous_owner_status not in HANDOFF_PREVIOUS_OWNER_STATUSES:
            raise GuardError("previous_owner_status must be superseded or abandoned")
        resolved_video_output_dir, _resolved_target_file, video_relative, target_relative = _canonical_task_paths(
            project_root,
            video_output_dir=video_output_dir,
            target_file=target_file,
        )
        index = _load_task_index(project_root, task_index_path)
        active_task = _active_task_for_video_dir(project_root, index, resolved_video_output_dir)
        if active_task is None:
            raise GuardError("video_output_dir has no active owner to hand off")
        if active_task["owner_session_id"] != from_session_id:
            raise GuardError(
                "video_output_dir active owner_session_id is "
                f"{active_task['owner_session_id']}, not {from_session_id}"
            )

        now = _now_iso()
        active_task["owner_status"] = previous_owner_status
        active_task["last_session_id"] = to_session_id
        active_task["updated_at"] = now
        index["tasks"].append(
            {
                "video_output_dir": video_relative,
                "target_file": target_relative,
                "owner_session_id": to_session_id,
                "owner_status": "active",
                "last_session_id": to_session_id,
                "stage": stage,
                "updated_at": now,
                "continued_from_session_id": from_session_id,
            }
        )
        _write_task_index(project_root, task_index_path, index)
        return EXIT_PASS, f"HANDOFF: {_resolve_task_index_path(project_root, task_index_path)}"
    except GuardError as exc:
        return EXIT_BLOCKED, _task_index_message(str(exc))


def task_update(
    *,
    project_root: Path,
    task_index_path: Path,
    session_id: str,
    video_output_dir: Path,
    stage: str,
    owner_status: str,
) -> tuple[int, str]:
    project_root = project_root.resolve()
    try:
        session_id = _validate_session_id(session_id, "session_id")
        stage = _validate_stage(stage, "stage")
        owner_status = _validate_owner_status(owner_status, "owner_status")
        resolved_video_output_dir = _resolve_project_path(project_root, str(video_output_dir), "video_output_dir")
        index = _load_task_index(project_root, task_index_path)
        active_task = _active_task_for_video_dir(project_root, index, resolved_video_output_dir)
        if active_task is None:
            raise GuardError("video_output_dir has no active owner to update")
        if active_task["owner_session_id"] != session_id:
            raise GuardError(
                "video_output_dir active owner_session_id is "
                f"{active_task['owner_session_id']}, not {session_id}"
            )
        active_task["owner_status"] = owner_status
        active_task["last_session_id"] = session_id
        active_task["stage"] = stage
        active_task["updated_at"] = _now_iso()
        _write_task_index(project_root, task_index_path, index)
        return EXIT_PASS, f"UPDATED: {_resolve_task_index_path(project_root, task_index_path)}"
    except GuardError as exc:
        return EXIT_BLOCKED, _task_index_message(str(exc))


def _claimed_task_index(
    project_root: Path,
    task_index_path: Path,
    *,
    session_id: str,
    video_output_dir: Path,
    target_file: Path,
    stage: str,
) -> dict[str, Any]:
    session_id = _validate_session_id(session_id, "session_id")
    stage = _validate_stage(stage, "stage")
    resolved_video_output_dir, _resolved_target_file, video_relative, target_relative = _canonical_task_paths(
        project_root,
        video_output_dir=video_output_dir,
        target_file=target_file,
    )
    index = _load_task_index(project_root, task_index_path)
    active_task = _active_task_for_video_dir(project_root, index, resolved_video_output_dir)
    if active_task is not None:
        if active_task["owner_session_id"] != session_id:
            raise GuardError(
                "video_output_dir already has active owner_session_id "
                f"{active_task['owner_session_id']}"
            )
        active_task["target_file"] = target_relative
        active_task["last_session_id"] = session_id
        active_task["stage"] = stage
        active_task["updated_at"] = _now_iso()
        return index

    index["tasks"].append(
        {
            "video_output_dir": video_relative,
            "target_file": target_relative,
            "owner_session_id": session_id,
            "owner_status": "active",
            "last_session_id": session_id,
            "stage": stage,
            "updated_at": _now_iso(),
        }
    )
    _validate_task_index(project_root, index)
    return index


def _owned_task_index(
    project_root: Path,
    task_index_path: Path,
    *,
    session_id: str,
    video_output_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    session_id = _validate_session_id(session_id, "session_id")
    resolved_video_output_dir = _resolve_project_path(project_root, str(video_output_dir), "video_output_dir")
    index = _load_task_index(project_root, task_index_path)
    active_task = _active_task_for_video_dir(project_root, index, resolved_video_output_dir)
    if active_task is None:
        raise GuardError("video_output_dir has no active owner to update")
    if active_task["owner_session_id"] != session_id:
        raise GuardError(
            "video_output_dir active owner_session_id is "
            f"{active_task['owner_session_id']}, not {session_id}"
        )
    return index, active_task


def prepare_old_pdf(
    *,
    project_root: Path,
    current_target_path: Path,
    task_index_path: Path,
    session_id: str,
    criteria_path: Path,
    pdf_path: Path,
    explicit_video_output_dir: Path | None,
) -> tuple[int, str]:
    project_root = project_root.resolve()
    try:
        session_id = _validate_session_id(session_id, "session_id")
        pdf_path = _resolve_project_path(project_root, str(pdf_path), "pdf")
        video_output_dir = infer_video_output_dir(project_root, pdf_path, explicit_video_output_dir)
        run_path = video_output_dir / "workflow" / "run.json"
        if run_path.is_file():
            try:
                run_record = json.loads(run_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise GuardError(
                    "video output Run authority is unreadable"
                ) from exc
            if (
                isinstance(run_record, dict)
                and run_record.get("schema_name") == "run-record"
                and run_record.get("schema_version") == "4.0.0"
                and run_record.get("canonical_platform") in ("bilibili", "youtube")
            ):
                raise GuardError(
                    "Bilibili Kernel Run rejects Legacy old-pdf-prepare mutation; "
                    "use the Workflow CLI delivery lifecycle"
                )
        final_pdf_relative = pdf_path.relative_to(video_output_dir).as_posix()
        main_tex_relative = _choose_main_tex(video_output_dir, pdf_path)
        acceptance_dir = video_output_dir / "review" / "acceptance"
        target_path = acceptance_dir / "delivery_target.json"
        session_target_path = _session_current_target_path_from_cli(current_target_path, session_id)
        claimed_index = _claimed_task_index(
            project_root,
            task_index_path,
            session_id=session_id,
            video_output_dir=video_output_dir,
            target_file=target_path,
            stage="ready_for_delivery",
        )
        manifest_path = create_allowed_artifacts_manifest(
            video_output_dir,
            criteria_path,
            [("tex", main_tex_relative), ("pdf", final_pdf_relative)],
        )
        target = {
            "schema_version": "1.0",
            "stage": "ready_for_delivery",
            "video_output_dir": ".",
            "final_pdf": final_pdf_relative,
            "main_tex": main_tex_relative,
            "allowed_artifacts_manifest": manifest_path.relative_to(video_output_dir).as_posix(),
            "acceptance_report": "review/acceptance/acceptance_report.json",
            "delivery_guard_report": "review/acceptance/delivery_guard_report.json",
            "compile_provenance_required": False,
            "legacy_existing_pdf": True,
            "recompiled": False,
            "attempt_limit": 3,
        }
        _write_json(target_path, target)
        now = _now_iso()
        current = {
            "schema_version": "1.1",
            "scope": "session",
            "session_id": session_id,
            "stage": "ready_for_delivery",
            "video_output_dir": video_output_dir.relative_to(project_root).as_posix(),
            "target_file": target_path.relative_to(project_root).as_posix(),
            "source_skill": "final-delivery-acceptance-old-pdf-repair",
            "started_at": now,
            "updated_at": now,
        }
        _write_json(session_target_path, current)
        _write_task_index(project_root, task_index_path, claimed_index)
        return EXIT_PASS, f"PREPARED: {target_path}"
    except (GuardError, AcceptanceReportValidationError) as exc:
        return EXIT_BLOCKED, _blocking_message(str(exc), None)


def _video_relative_changed_file(video_output_dir: Path, value: str) -> str:
    raw = _require_string(value, "changed file")
    if Path(raw).is_absolute() or _looks_windows_absolute(raw):
        resolved = Path(raw).resolve()
        if not _path_under(video_output_dir, resolved):
            raise GuardError(f"changed file escapes video output directory: {raw}")
        return resolved.relative_to(video_output_dir.resolve()).as_posix()
    normalized = _require_relative_path(raw, "changed file")
    resolved = (video_output_dir / normalized).resolve()
    if not _path_under(video_output_dir, resolved):
        raise GuardError(f"changed file escapes video output directory: {raw}")
    return normalized


def _copy_if_exists(source: Path, destination: Path) -> None:
    if source.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _repair_brief_from_report(report: dict[str, Any], attempt_number: int, changed_files: list[str]) -> str:
    failed_criteria = report.get("failed_criteria", [])
    criterion_results = [
        result
        for result in report.get("criterion_results", [])
        if isinstance(result, dict) and result.get("criterion_id") in failed_criteria
    ]
    payload = {
        "attempt": f"attempt_{attempt_number:02d}",
        "failed_criteria": failed_criteria,
        "failed_criterion_results": criterion_results,
        "visual_scan_evidence": report.get("visual_scan_evidence"),
        "changed_files": changed_files,
    }
    return (
        f"# Repair Brief attempt_{attempt_number:02d}\n\n"
        "Repair subagents may inspect and modify only files inside this video output directory.\n\n"
        "```json\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n"
        "```\n"
    )


def _manual_brief(acceptance_dir: Path, attempt_limit: int) -> str:
    attempts = [f"attempt_{number:02d}" for number in range(1, attempt_limit + 1)]
    return (
        "# Manual Repair Brief\n\n"
        "Automatic bounded repair reached the attempt limit. Delivery remains blocked until a human or a fresh repair "
        "subagent run resolves the failed criteria and a fresh Acceptance Reviewer plus delivery guard pass succeeds.\n\n"
        f"Attempt evidence: {', '.join(attempts)}\n"
        f"Review directory: {acceptance_dir.as_posix()}\n"
    )


def record_failed_attempt(
    *,
    project_root: Path,
    current_target_path: Path,
    task_index_path: Path,
    session_id: str,
    video_output_dir: Path,
    attempt_number: int,
    changed_files: list[str],
) -> tuple[int, str]:
    project_root = project_root.resolve()
    try:
        session_id = _validate_session_id(session_id, "session_id")
        if attempt_number < 1:
            raise GuardError("attempt_number must be at least 1")
        video_output_dir = _resolve_project_path(project_root, str(video_output_dir), "video_output_dir")
        acceptance_dir = video_output_dir / "review" / "acceptance"
        target_path = acceptance_dir / "delivery_target.json"
        session_target_path = _session_current_target_path_from_cli(current_target_path, session_id)
        current = _require_object(_load_json(session_target_path, "current target"), "current target")
        _validate_current_target_schema(current, expected_session_id=session_id, require_session_scope=True)
        _validate_explicit_session_current_target_path(session_target_path, session_id)
        current_video_dir = _resolve_project_path(
            project_root,
            current.get("video_output_dir"),
            "current target video_output_dir",
        )
        current_target_file = _resolve_project_path(
            project_root,
            current.get("target_file"),
            "current target target_file",
        )
        if current_video_dir != video_output_dir:
            raise GuardError("current target video_output_dir must match record-failed-attempt video_output_dir")
        if current_target_file != target_path.resolve():
            raise GuardError("current target target_file must match video delivery_target.json")
        task_index, active_task = _owned_task_index(
            project_root,
            task_index_path,
            session_id=session_id,
            video_output_dir=video_output_dir,
        )
        target = _require_object(_load_json(target_path, "delivery target"), "delivery target")
        attempt_limit = _validate_attempt_limit(target.get("attempt_limit"))
        if attempt_number > attempt_limit:
            raise GuardError("attempt_number exceeds attempt_limit")
        report_path = acceptance_dir / "acceptance_report.json"
        report = _require_object(_load_json(report_path, "acceptance report"), "acceptance report")
        if report.get("overall_status") != "fail":
            raise GuardError("record-failed-attempt requires a failed acceptance report")
        normalized_changed_files = [_video_relative_changed_file(video_output_dir, item) for item in changed_files]

        attempt_dir = acceptance_dir / "attempts" / f"attempt_{attempt_number:02d}"
        attempt_dir.mkdir(parents=True, exist_ok=True)
        _copy_if_exists(report_path, attempt_dir / "acceptance_report.json")
        _copy_if_exists(acceptance_dir / "acceptance_summary.md", attempt_dir / "acceptance_summary.md")
        (attempt_dir / "repair_brief.md").write_text(
            _repair_brief_from_report(report, attempt_number, normalized_changed_files),
            encoding="utf-8",
        )
        _write_json(
            attempt_dir / "changed_files.json",
            {
                "schema_version": "1.0",
                "attempt": f"attempt_{attempt_number:02d}",
                "recorded_at": _now_iso(),
                "changed_files": normalized_changed_files,
            },
        )

        if attempt_number == attempt_limit:
            active_task["owner_status"] = "blocked"
            active_task["last_session_id"] = session_id
            active_task["stage"] = "blocked"
            active_task["updated_at"] = _now_iso()
            _validate_task_index(project_root, task_index)
            target["stage"] = "blocked"
            _write_json(target_path, target)
            (acceptance_dir / "manual_repair_brief.md").write_text(
                _manual_brief(acceptance_dir, attempt_limit),
                encoding="utf-8",
            )
            current["stage"] = "blocked"
            current["updated_at"] = _now_iso()
            _write_json(session_target_path, current)
            _write_task_index(project_root, task_index_path, task_index)

        return EXIT_PASS, f"RECORDED: {attempt_dir}"
    except GuardError as exc:
        return EXIT_BLOCKED, _blocking_message(str(exc), None)


def _unique_archive_path(archive_dir: Path, session_id: str) -> Path:
    safe_stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    archive_path = archive_dir / f"current-{session_id}-{safe_stamp}.json"
    counter = 1
    while archive_path.exists():
        archive_path = archive_dir / f"current-{session_id}-{safe_stamp}-{counter}.json"
        counter += 1
    return archive_path


def _replace_path_with_retry(source: Path, target: Path, *, attempts: int = 10, delay_seconds: float = 0.1) -> OSError | None:
    last_error: OSError | None = None
    for _ in range(attempts):
        try:
            source.replace(target)
            return None
        except OSError as exc:
            last_error = exc
            time.sleep(delay_seconds)
    return last_error


def clear_target(
    *,
    project_root: Path,
    current_target_path: Path,
    video_output_dir: Path | None,
    task_index_path: Path | None = None,
    session_id: str | None = None,
) -> tuple[int, str]:
    project_root = project_root.resolve()
    current_target_path = current_target_path.resolve()
    try:
        if session_id is not None:
            session_id = _validate_session_id(session_id, "session_id")
            session_target_path = _session_current_target_path_from_cli(current_target_path, session_id)
            if not _path_under(project_root, session_target_path):
                raise GuardError("current target path escapes project boundary")
            if not session_target_path.exists():
                return EXIT_PASS, "No active session delivery target to clear."
            if task_index_path is None:
                raise GuardError("clear-target requires task-index for session-scoped targets")
            current = _require_object(_load_json(session_target_path, "current target"), "current target")
            _validate_current_target_schema(current, expected_session_id=session_id, require_session_scope=True)
            _validate_explicit_session_current_target_path(session_target_path, session_id)
            current_video_dir = _resolve_project_path(
                project_root,
                current.get("video_output_dir"),
                "current target video_output_dir",
            )
            current_target_file = _resolve_project_path(
                project_root,
                current.get("target_file"),
                "current target target_file",
            )
            if not _path_under(current_video_dir, current_target_file):
                raise GuardError("current target target_file must stay inside video_output_dir")
            if video_output_dir is not None:
                resolved_video_dir = _resolve_project_path(project_root, str(video_output_dir), "video_output_dir")
                if resolved_video_dir != current_video_dir:
                    raise GuardError("video_output_dir must match current target video_output_dir")
            else:
                resolved_video_dir = current_video_dir
            if not resolved_video_dir.is_dir():
                raise GuardError(f"video_output_dir not found: {resolved_video_dir}")

            task_index, active_task = _owned_task_index(
                project_root,
                task_index_path,
                session_id=session_id,
                video_output_dir=resolved_video_dir,
            )
            _task_video_dir, task_target_file = _task_entry_paths(project_root, active_task, "task-index active task")
            if task_target_file != current_target_file:
                raise GuardError("task-index target_file must match current target target_file")

            now = _now_iso()
            current["stage"] = "delivered"
            current["updated_at"] = now
            current["cleared_by"] = "delivery_guard.py clear-target"
            session_target_path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
            archive_dir = resolved_video_dir / "待删除" / "delivery-targets" / "sessions"
            archive_dir.mkdir(parents=True, exist_ok=True)
            archive_path = _unique_archive_path(archive_dir, session_id)
            archive_error = _replace_path_with_retry(session_target_path, archive_path)
            if archive_error is not None:
                raise GuardError(f"cannot archive session delivery target: {archive_error}")

            active_task["owner_status"] = "delivered"
            active_task["last_session_id"] = session_id
            active_task["stage"] = "delivered"
            active_task["updated_at"] = now
            _write_task_index(project_root, task_index_path, task_index)
            return EXIT_PASS, f"CLEARED: {archive_path}"

        if not current_target_path.exists():
            return EXIT_PASS, "No active delivery target to clear."
        current = _require_object(_load_json(current_target_path, "current target"), "current target")
        if video_output_dir is not None:
            resolved_video_dir = _resolve_project_path(project_root, str(video_output_dir), "video_output_dir")
        else:
            resolved_video_dir = _resolve_project_path(
                project_root,
                current.get("video_output_dir"),
                "current target video_output_dir",
            )
        if not resolved_video_dir.is_dir():
            raise GuardError(f"video_output_dir not found: {resolved_video_dir}")
        current["stage"] = "delivered"
        current["updated_at"] = _now_iso()
        current["cleared_by"] = "delivery_guard.py clear-target"
        current_target_path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        archive_dir = resolved_video_dir / "待删除" / "delivery-targets"
        archive_dir.mkdir(parents=True, exist_ok=True)
        safe_stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        archive_path = archive_dir / f"current-{safe_stamp}.json"
        archive_path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        last_error = _replace_path_with_retry(current_target_path, archive_path, attempts=5)
        if last_error is not None:
            return EXIT_PASS, f"CLEARED: {archive_path}; active target retained at delivered stage because archive move was unavailable"
        return EXIT_PASS, f"CLEARED: {archive_path}"
    except GuardError as exc:
        return EXIT_BLOCKED, _blocking_message(str(exc), None)


def _write_guard_report(
    target: DeliveryTarget,
    *,
    status: str,
    acceptance_report_status: str | None,
    fingerprints: list[dict[str, Any]],
    checked_conditions: list[dict[str, Any]],
    blocking_message: str | None,
    first_failing_gate: str | None = None,
    error_code: str | None = None,
) -> None:
    target.guard_report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": "1.0",
        "status": status,
        "checked_at": _now_iso(),
        "stage": target.stage,
        "video_output_dir": _repo_relative(target.project_root, target.video_output_dir),
        "final_pdf": target.final_pdf_relative,
        "validated_by": "delivery_guard.py",
        "acceptance_report_status": acceptance_report_status,
        "artifact_fingerprints": fingerprints,
        "checked_conditions": checked_conditions,
        "blocking_message": blocking_message,
        "first_failing_gate": first_failing_gate,
        "error_code": error_code,
    }
    write_json_atomic(target.guard_report_path, report)


def _load_acceptance_status(path: Path) -> str | None:
    try:
        report = _require_object(_load_json(path, "acceptance report"), "acceptance report")
    except GuardError:
        return None
    status = report.get("overall_status")
    return status if isinstance(status, str) else None


def _validate_active_acceptance_authority(target: DeliveryTarget) -> dict[str, Any]:
    """Require the complete provider-owned v2 publication and current Global Gate."""

    if any(key in target.video_target for key in ("acceptance_report_v1", "legacy_acceptance_report", "acceptance_authorities")):
        raise GuardError(
            "Dual Acceptance Report authority cannot authorize the active delivery gate",
            first_failing_gate="acceptance_authority",
            error_code="acceptance_dual_authority_rejected",
        )
    report = _require_object(_load_json(target.acceptance_report_path, "acceptance report"), "acceptance report")
    if report.get("schema_name") != "acceptance-report-v2" or report.get("schema_version") != "2.0.0":
        raise GuardError(
            "Acceptance Report v1 or an unsupported report version cannot authorize delivery",
            first_failing_gate="acceptance_authority",
            error_code="acceptance_report_v1_rejected",
        )
    if any(key in report for key in ("translated_from", "compatibility_translation", "source_schema_version")):
        raise GuardError(
            "Compatibility translation cannot authorize the active delivery gate",
            first_failing_gate="acceptance_authority",
            error_code="acceptance_compatibility_translation_rejected",
        )
    if not target.global_gate_authority_path.is_file() or _file_sha256(target.global_gate_authority_path) != target.global_gate_authority_sha256:
        raise GuardError(
            "delivery_target Global Gate authority binding is stale",
            first_failing_gate="global_gate_authority",
            error_code="global_gate_authority_stale",
        )
    try:
        acceptance_provider = AcceptanceV2Provider(SRC_ROOT.parent)
        current_gate = acceptance_provider.require_current_global_gate(
            control_store_root=target.global_gate_authority_path.parent
        )
        eligibility = acceptance_provider.guard_eligibility(
            workspace_root=target.acceptance_report_path.parent
        )
    except KernelError as exc:
        data = getattr(exc, "data", {}) or {}
        raise GuardError(
            str(exc),
            first_failing_gate=data.get("first_failing_gate", "acceptance_v2_authority"),
            error_code=data.get("error_code", "acceptance_v2_authority_stale"),
        ) from exc
    except (sqlite3.DatabaseError, OSError) as exc:
        raise GuardError(
            "Acceptance Report v2 control store is unavailable or corrupt",
            first_failing_gate="control_store",
            error_code="acceptance_v2_control_store_unavailable",
        ) from exc
    if (
        Path(current_gate["path"]).resolve() != target.global_gate_authority_path
        or current_gate["file_sha256"] != target.global_gate_authority_sha256
    ):
        raise GuardError(
            "delivery_target Global Gate authority disagrees with committed control state",
            first_failing_gate="global_gate_authority",
            error_code="global_gate_authority_stale",
        )
    failed_check = next(
        (name for name, passed in eligibility.get("mechanical_checks", {}).items() if not passed),
        None,
    )
    if not eligibility.get("eligible") or failed_check is not None:
        failed_check = failed_check or "acceptance_v2_authority"
        raise GuardError(
            f"Acceptance Report v2 authority is ineligible: {failed_check}",
            first_failing_gate=failed_check,
            error_code=f"acceptance_v2_{failed_check}_stale",
        )
    return eligibility


def run_check(*, project_root: Path, current_target_path: Path, criteria_path: Path) -> tuple[int, str]:
    checked_conditions: list[dict[str, Any]] = []
    target: DeliveryTarget | None = None
    acceptance_status: str | None = None
    fingerprints: list[dict[str, Any]] = []
    try:
        target = resolve_delivery_target(
            project_root=project_root,
            current_target_path=current_target_path,
            require_session_scope=True,
        )
        checked_conditions.append(_condition("target_resolved", "pass"))
        if target.stage not in GUARD_STAGES:
            raise GuardError(f"delivery guard check requires ready_for_delivery or accepted stage, got {target.stage}")

        manifest = _load_manifest(target)
        checked_conditions.append(_condition("allowed_artifacts_manifest_loaded", "pass"))
        _ensure_final_pdf_in_manifest(target, manifest)
        checked_conditions.append(_condition("final_pdf_in_manifest", "pass"))
        _ensure_compile_provenance(target)
        checked_conditions.append(_condition("final_compile_provenance_current", "pass"))

        _validate_active_acceptance_authority(target)
        acceptance_status = _load_acceptance_status(target.acceptance_report_path)
        checked_conditions.append(_condition("acceptance_report_v2_authority_current", "pass"))
        _ensure_rendered_page_coverage(target)
        checked_conditions.append(_condition("rendered_page_evidence_current", "pass"))
        fingerprints = guard_fingerprints(target, manifest)
        checked_conditions.append(_condition("artifact_fingerprints_current", "pass"))
        _write_guard_report(
            target,
            status="pass",
            acceptance_report_status=acceptance_status,
            fingerprints=fingerprints,
            checked_conditions=checked_conditions,
            blocking_message=None,
        )
        return EXIT_PASS, f"PASS: {target.guard_report_path}"
    except (GuardError, GateBlockedError, AcceptanceReportValidationError) as exc:
        message = _blocking_message(str(exc), target)
        if target is not None and not target.kernel_authority:
            if not checked_conditions or checked_conditions[-1]["status"] == "pass":
                checked_conditions.append(_condition("delivery_guard", "fail", str(exc)))
            acceptance_status = acceptance_status or _load_acceptance_status(target.acceptance_report_path)
            _write_guard_report(
                target,
                status="fail",
                acceptance_report_status=acceptance_status,
                fingerprints=fingerprints,
                checked_conditions=checked_conditions,
                blocking_message=message,
                first_failing_gate=getattr(exc, "first_failing_gate", "delivery_guard"),
                error_code=getattr(exc, "error_code", "delivery_guard_failed"),
            )
        return EXIT_BLOCKED, message


def guard_report_is_fresh(target: DeliveryTarget) -> bool:
    """Return whether an existing passing guard report matches current artifacts."""

    try:
        report = _require_object(_load_json(target.guard_report_path, "delivery guard report"), "delivery guard report")
        if report.get("schema_version") != "1.0":
            return False
        if report.get("status") != "pass":
            return False
        if report.get("stage") != target.stage:
            return False
        if report.get("final_pdf") != target.final_pdf_relative:
            return False
        manifest = _load_manifest(target)
        current_fingerprints = guard_fingerprints(target, manifest)
        return report.get("artifact_fingerprints") == current_fingerprints
    except GuardError:
        return False


def run_hook_stop(
    *,
    project_root: Path,
    current_target_path: Path,
    criteria_path: Path,
    hook_input: dict[str, Any],
) -> tuple[int, str]:
    """Implement the project-local Stop hook decision."""

    project_root = project_root.resolve()
    try:
        session_id = _session_id_from_hook_input(hook_input)
        current_target_path = _session_current_target_path(current_target_path, session_id)
        current = _require_object(_load_json(current_target_path, "current target"), "current target")
        _require_keys(current, {"schema_version", "stage"}, "current target")
        _validate_current_target_schema(current, expected_session_id=session_id)
        stage = _validate_stage(current["stage"], "current target stage")
    except MissingTargetError:
        return EXIT_PASS, "No active delivery target; Final Delivery Guard allows this response."
    except GuardError as exc:
        return EXIT_BLOCKED, _blocking_message(str(exc), None)

    if stage == "generating":
        return EXIT_PASS, "Final Delivery Guard allows stage generating; final delivery is not active."
    if stage == "delivered":
        return EXIT_PASS, "Final Delivery Guard allows stale delivered session target; render workflow should archive session state."
    if stage == "blocked":
        target = _try_resolve_target(project_root, current_target_path)
        reason = "target stage is blocked; inspect review/acceptance/manual_repair_brief.md or attempts evidence"
        return EXIT_BLOCKED, _blocking_message(reason, target)

    if stage in GUARD_STAGES:
        try:
            target = resolve_delivery_target(project_root=project_root, current_target_path=current_target_path)
        except GuardError as exc:
            return EXIT_BLOCKED, _blocking_message(str(exc), None)
        if guard_report_is_fresh(target):
            try:
                _validate_active_acceptance_authority(target)
            except GuardError:
                return run_check(
                    project_root=project_root,
                    current_target_path=current_target_path,
                    criteria_path=criteria_path,
                )
            return EXIT_PASS, f"Final Delivery Guard found a fresh passing guard report: {target.guard_report_path}"
        return run_check(project_root=project_root, current_target_path=current_target_path, criteria_path=criteria_path)

    return EXIT_BLOCKED, _blocking_message(f"unsupported target stage: {stage}", None)


def _try_resolve_target(project_root: Path, current_target_path: Path) -> DeliveryTarget | None:
    try:
        return resolve_delivery_target(project_root=project_root, current_target_path=current_target_path)
    except GuardError:
        return None


def _blocking_message(reason: str, target: DeliveryTarget | None) -> str:
    video = _repo_relative(target.project_root, target.video_output_dir) if target else "<unknown video output dir>"
    return (
        "Final Delivery Guard blocked delivery. Use a separate Acceptance Reviewer subagent and repair subagents "
        f"to run the Final Delivery Acceptance workflow for {video}. "
        "Do not deliver this PDF until delivery_guard.py records a fresh pass. "
        f"Reason: {reason}"
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check Final Delivery Guard state.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(subparser: argparse.ArgumentParser, *, current_target_default: Path | None = DEFAULT_CURRENT_TARGET) -> None:
        subparser.add_argument("--project-root", type=Path, default=REPO_ROOT)
        subparser.add_argument("--current-target", type=Path, default=current_target_default)
        subparser.add_argument("--criteria", type=Path, default=DEFAULT_CRITERIA)

    def add_task_common(subparser: argparse.ArgumentParser) -> None:
        add_common(subparser)
        subparser.add_argument("--task-index", type=Path, default=DEFAULT_TASK_INDEX)

    check_parser = subparsers.add_parser("check", help="Validate the active target and write delivery_guard_report.json.")
    add_common(check_parser, current_target_default=None)

    hook_parser = subparsers.add_parser("hook-stop", help="Run the lightweight Stop hook delivery decision.")
    add_common(hook_parser)

    old_pdf_parser = subparsers.add_parser("old-pdf-prepare", help="Prepare a bounded old-PDF repair target.")
    old_pdf_parser.add_argument("pdf", type=Path)
    old_pdf_parser.add_argument("--session-id", required=True)
    old_pdf_parser.add_argument("--video-output-dir", type=Path)
    add_task_common(old_pdf_parser)

    attempt_parser = subparsers.add_parser("record-failed-attempt", help="Archive a failed acceptance attempt.")
    attempt_parser.add_argument("--session-id", required=True)
    attempt_parser.add_argument("--video-output-dir", type=Path, required=True)
    attempt_parser.add_argument("--attempt-number", type=int, required=True)
    attempt_parser.add_argument("--changed-file", action="append", default=[])
    add_task_common(attempt_parser)

    clear_parser = subparsers.add_parser("clear-target", help="Archive and clear the active project delivery target.")
    clear_parser.add_argument("--session-id")
    clear_parser.add_argument("--video-output-dir", type=Path)
    add_task_common(clear_parser)

    task_claim_parser = subparsers.add_parser("task-claim", help="Claim or resume delivery task ownership.")
    task_claim_parser.add_argument("--session-id", required=True)
    task_claim_parser.add_argument("--video-output-dir", type=Path, required=True)
    task_claim_parser.add_argument("--target-file", type=Path, required=True)
    task_claim_parser.add_argument("--stage", required=True)
    add_task_common(task_claim_parser)

    task_handoff_parser = subparsers.add_parser("task-handoff", help="Transfer delivery task ownership.")
    task_handoff_parser.add_argument("--from-session-id", required=True)
    task_handoff_parser.add_argument("--to-session-id", required=True)
    task_handoff_parser.add_argument("--video-output-dir", type=Path, required=True)
    task_handoff_parser.add_argument("--target-file", type=Path, required=True)
    task_handoff_parser.add_argument("--stage", required=True)
    task_handoff_parser.add_argument("--previous-owner-status", required=True)
    add_task_common(task_handoff_parser)

    task_update_parser = subparsers.add_parser("task-update", help="Update active delivery task ownership state.")
    task_update_parser.add_argument("--session-id", required=True)
    task_update_parser.add_argument("--video-output-dir", type=Path, required=True)
    task_update_parser.add_argument("--stage", required=True)
    task_update_parser.add_argument("--owner-status", required=True)
    add_task_common(task_update_parser)
    return parser.parse_args()


def _kernel_mutation_is_forbidden(current_target_path: Path | None) -> bool:
    if current_target_path is None or not current_target_path.is_file():
        return False
    try:
        current = json.loads(current_target_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(current, dict) and current.get("schema_name") == "kernel-session-delivery-target"


def main() -> int:
    args = _parse_args()
    if args.command in {"task-claim", "task-update", "clear-target", "task-handoff"} and _kernel_mutation_is_forbidden(args.current_target):
        print(
            _blocking_message("Kernel delivery authority is read-only; use the Workflow CLI delivery lifecycle", None),
            file=sys.stderr,
        )
        return EXIT_BLOCKED
    if args.command == "check":
        if args.current_target is None:
            message = _blocking_message(
                "delivery_guard.py check requires --current-target "
                ".codex/delivery-targets/sessions/<session_id>/current.json",
                None,
            )
            print(message, file=sys.stderr)
            return EXIT_BLOCKED
        code, message = run_check(
            project_root=args.project_root,
            current_target_path=args.current_target,
            criteria_path=args.criteria,
        )
        stream = sys.stdout if code == EXIT_PASS else sys.stderr
        print(message, file=stream)
        return code
    if args.command == "hook-stop":
        try:
            hook_input = _read_hook_input(sys.stdin)
            code, message = run_hook_stop(
                project_root=args.project_root,
                current_target_path=args.current_target,
                criteria_path=args.criteria,
                hook_input=hook_input,
            )
        except GuardError as exc:
            code, message = EXIT_BLOCKED, _blocking_message(str(exc), None)
        stream = sys.stdout if code == EXIT_PASS else sys.stderr
        print(message, file=stream)
        return code
    if args.command == "old-pdf-prepare":
        code, message = prepare_old_pdf(
            project_root=args.project_root,
            current_target_path=args.current_target,
            task_index_path=args.task_index,
            session_id=args.session_id,
            criteria_path=args.criteria,
            pdf_path=args.pdf,
            explicit_video_output_dir=args.video_output_dir,
        )
        stream = sys.stdout if code == EXIT_PASS else sys.stderr
        print(message, file=stream)
        return code
    if args.command == "record-failed-attempt":
        code, message = record_failed_attempt(
            project_root=args.project_root,
            current_target_path=args.current_target,
            task_index_path=args.task_index,
            session_id=args.session_id,
            video_output_dir=args.video_output_dir,
            attempt_number=args.attempt_number,
            changed_files=args.changed_file,
        )
        stream = sys.stdout if code == EXIT_PASS else sys.stderr
        print(message, file=stream)
        return code
    if args.command == "clear-target":
        code, message = clear_target(
            project_root=args.project_root,
            current_target_path=args.current_target,
            video_output_dir=args.video_output_dir,
            task_index_path=args.task_index,
            session_id=args.session_id,
        )
        stream = sys.stdout if code == EXIT_PASS else sys.stderr
        print(message, file=stream)
        return code
    if args.command == "task-claim":
        code, message = task_claim(
            project_root=args.project_root,
            task_index_path=args.task_index,
            session_id=args.session_id,
            video_output_dir=args.video_output_dir,
            target_file=args.target_file,
            stage=args.stage,
        )
        stream = sys.stdout if code == EXIT_PASS else sys.stderr
        print(message, file=stream)
        return code
    if args.command == "task-handoff":
        code, message = task_handoff(
            project_root=args.project_root,
            task_index_path=args.task_index,
            from_session_id=args.from_session_id,
            to_session_id=args.to_session_id,
            video_output_dir=args.video_output_dir,
            target_file=args.target_file,
            stage=args.stage,
            previous_owner_status=args.previous_owner_status,
        )
        stream = sys.stdout if code == EXIT_PASS else sys.stderr
        print(message, file=stream)
        return code
    if args.command == "task-update":
        code, message = task_update(
            project_root=args.project_root,
            task_index_path=args.task_index,
            session_id=args.session_id,
            video_output_dir=args.video_output_dir,
            stage=args.stage,
            owner_status=args.owner_status,
        )
        stream = sys.stdout if code == EXIT_PASS else sys.stderr
        print(message, file=stream)
        return code
    return EXIT_INVALID


if __name__ == "__main__":
    raise SystemExit(main())
