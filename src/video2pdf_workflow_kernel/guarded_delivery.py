from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import sys
from typing import Any

from .acceptance_v2 import AcceptanceV2Provider
from .delivery_quality import DeliveryQualityRegistry
from .errors import ContractError, KernelError
from .utils import canonical_json_bytes, read_json, sha256_file


REQUIRED_GUARD_CONDITIONS = frozenset(
    {
        "target_resolved",
        "allowed_artifacts_manifest_loaded",
        "final_pdf_in_manifest",
        "final_compile_provenance_current",
        "acceptance_report_v2_authority_current",
        "rendered_page_evidence_current",
        "artifact_fingerprints_current",
    }
)


def _fingerprint_without(value: dict[str, Any], field: str) -> str:
    payload = {key: item for key, item in value.items() if key != field}
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def validate_acceptance_report(
    *,
    project_root: Path,
    report_path: Path,
    run_id: str,
    coordination_revision: int | None = None,
) -> dict[str, Any]:
    report = read_json(report_path.resolve())
    DeliveryQualityRegistry(project_root.resolve()).validate(
        "acceptance-report-v2", report
    )
    run_binding = report.get("run_binding")
    if (
        report.get("overall_status") != "pass"
        or report.get("routing_state") != "ready_for_delivery"
        or report.get("input_track") != "kernel"
        or not isinstance(run_binding, dict)
        or run_binding.get("run_id") != run_id
        or (
            coordination_revision is not None
            and run_binding.get("coordination_revision") != coordination_revision
        )
        or report.get("report_sha256")
        != _fingerprint_without(report, "report_sha256")
    ):
        raise ContractError(
            "Acceptance Report v2 is not a fingerprint-current passing Kernel decision"
        )
    return report


def validate_delivery_guard_report(
    *, report_path: Path, expected_stage: str = "accepted"
) -> dict[str, Any]:
    if expected_stage not in {"ready_for_delivery", "accepted"}:
        raise ContractError("Delivery Guard expected stage is unsupported")
    report = read_json(report_path.resolve())
    checked_conditions = report.get("checked_conditions")
    condition_statuses = (
        {
            item.get("condition"): item.get("status")
            for item in checked_conditions
            if isinstance(item, dict)
        }
        if isinstance(checked_conditions, list)
        else {}
    )
    fingerprints = report.get("artifact_fingerprints")
    fingerprints_valid = bool(fingerprints) and all(
        isinstance(item, dict)
        and set(item) == {"path", "sha256", "size_bytes", "size_chars"}
        and isinstance(item["path"], str)
        and isinstance(item["sha256"], str)
        and item["sha256"].startswith("sha256:")
        and len(item["sha256"]) == 71
        and isinstance(item["size_bytes"], int)
        and (item["size_chars"] is None or isinstance(item["size_chars"], int))
        for item in fingerprints
    )
    if (
        report.get("schema_version") != "1.0"
        or report.get("status") != "pass"
        or report.get("stage") != expected_stage
        or report.get("validated_by") != "delivery_guard.py"
        or report.get("acceptance_report_status") != "pass"
        or not fingerprints_valid
        or set(condition_statuses) != REQUIRED_GUARD_CONDITIONS
        or any(status != "pass" for status in condition_statuses.values())
    ):
        raise ContractError(
            "Delivery Guard Report is not a complete passing mechanical decision"
        )
    return report


def _load_active_delivery_guard(project_root: Path) -> Any:
    """Load the active read-only Guard implementation from its canonical surface."""

    guard_path = (
        project_root.resolve()
        / ".agents"
        / "skills"
        / "final-delivery-acceptance"
        / "scripts"
        / "delivery_guard.py"
    )
    if not guard_path.is_file():
        raise ContractError("Active Delivery Guard implementation is unavailable")
    module_name = (
        "video2pdf_active_delivery_guard_"
        + hashlib.sha256(str(guard_path).encode("utf-8")).hexdigest()[:16]
    )
    spec = importlib.util.spec_from_file_location(module_name, guard_path)
    if spec is None or spec.loader is None:
        raise ContractError("Active Delivery Guard implementation cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    guard_script_dir = str(guard_path.parent)
    added_guard_script_dir = guard_script_dir not in sys.path
    if added_guard_script_dir:
        sys.path.insert(0, guard_script_dir)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        sys.modules.pop(module_name, None)
        raise ContractError("Active Delivery Guard implementation cannot be loaded") from exc
    finally:
        if added_guard_script_dir:
            try:
                sys.path.remove(guard_script_dir)
            except ValueError:
                pass
    for name in (
        "resolve_delivery_target",
        "guard_report_is_fresh",
        "guard_fingerprints",
    ):
        if not callable(getattr(module, name, None)):
            raise ContractError("Active Delivery Guard read-only API is incomplete")
    return module


def _bound_file(
    binding: Any,
    *,
    base: Path,
    allowed_root: Path,
    label: str,
) -> Path:
    if not isinstance(binding, dict) or set(binding) < {"path", "sha256"}:
        raise ContractError(f"Kernel guarded decision {label} binding is invalid")
    raw = binding.get("path")
    expected_sha = binding.get("sha256")
    if not isinstance(raw, str) or not isinstance(expected_sha, str):
        raise ContractError(f"Kernel guarded decision {label} binding is invalid")
    candidate = Path(raw)
    path = candidate.resolve() if candidate.is_absolute() else (base / candidate).resolve()
    if (
        not path.is_relative_to(allowed_root.resolve())
        or not path.is_file()
        or sha256_file(path) != expected_sha
    ):
        raise ContractError(f"Kernel guarded decision {label} binding is stale")
    return path


def _require_kernel_projection_graph(
    *, project_root: Path, run_dir: Path, expected_stage: str
) -> dict[str, Any]:
    project = project_root.resolve()
    run_root = run_dir.resolve()
    if expected_stage not in {"accepted", "delivered"}:
        raise ContractError("Kernel guarded decision stage is unsupported")
    if not run_root.is_relative_to(project):
        raise ContractError("Kernel guarded decision Run escapes the project root")
    run_path = run_root / "workflow" / "run.json"
    if not run_path.is_file():
        raise ContractError("Kernel guarded decision Run Record is unavailable")
    run = read_json(run_path)
    delivery = run.get("delivery")
    if (
        run.get("schema_name") != "run-record"
        or run.get("schema_version") != "4.0.0"
        or run.get("canonical_platform") not in ("bilibili", "youtube")
        or run.get("platform_adapter") not in ("bilibili", "youtube")
        or Path(str(run.get("output_path", ""))).resolve() != run_root
        or not isinstance(delivery, dict)
        or delivery.get("stage") != expected_stage
    ):
        raise ContractError(
            f"Kernel guarded decision requires a {expected_stage} platform Run v4"
        )
    projections = delivery.get("projections")
    if not isinstance(projections, dict):
        raise ContractError("Kernel guarded decision projections are absent")
    video_path = _bound_file(
        projections.get("video_target"),
        base=run_root,
        allowed_root=run_root,
        label="video target",
    )
    session_path = _bound_file(
        projections.get("session_target"),
        base=project,
        allowed_root=project,
        label="session target",
    )
    video = read_json(video_path)
    session = read_json(session_path)
    run_id = run.get("run_id")
    run_revision = run.get("coordination_revision")
    ownership = delivery.get("ownership")
    if (
        not isinstance(run_id, str)
        or video.get("run_id") != run_id
        or session.get("run_id") != run_id
        or video.get("stage") != expected_stage
        or session.get("stage") != expected_stage
        or video.get("run_revision") != run_revision
        or session.get("run_revision") != run_revision
        or video.get("ownership") != ownership
        or not isinstance(ownership, dict)
        or session.get("session_id") != ownership.get("session_id")
        or session.get("ownership_generation") != ownership.get("generation")
        or session.get("owner_status") != "active"
    ):
        raise ContractError("Kernel guarded decision projection identity is stale")
    session_video = session.get("video_target")
    if (
        not isinstance(session_video, dict)
        or Path(str(session_video.get("path", ""))).resolve() != video_path
        or session_video.get("projection_revision")
        != video.get("projection_revision")
        or session_video.get("sha256") != sha256_file(video_path)
    ):
        raise ContractError("Kernel guarded decision session-to-video binding is stale")
    artifacts = video.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ContractError("Kernel guarded decision artifact bindings are absent")
    return {
        "project": project,
        "run_root": run_root,
        "run": run,
        "delivery": delivery,
        "run_id": run_id,
        "video_path": video_path,
        "session_path": session_path,
        "artifacts": artifacts,
    }


def require_current_kernel_guarded_decision(
    *,
    project_root: Path,
    run_dir: Path,
) -> dict[str, Any]:
    """Require the provider-committed Acceptance and fresh Guard for an accepted Run.

    This is the read-only accepted-to-delivered authority boundary.  Candidate
    activation at ``ready_for_delivery`` requires the Acceptance provider only;
    a Guard cannot be current until the accepted projection exists.
    """

    graph = _require_kernel_projection_graph(
        project_root=project_root, run_dir=run_dir, expected_stage="accepted"
    )
    project = graph["project"]
    run_root = graph["run_root"]
    run_id = graph["run_id"]
    video_path = graph["video_path"]
    session_path = graph["session_path"]
    artifacts = graph["artifacts"]
    acceptance_path = _bound_file(
        artifacts.get("acceptance_report"),
        base=run_root,
        allowed_root=run_root,
        label="Acceptance Report v2",
    )
    # At the accepted predecessor the Guard has been written at its canonical
    # path, while the video-target artifact slot is committed by the succeeding
    # delivered transition.  The active Guard resolver below is the authority
    # that binds this canonical pre-transition path.
    guard_path = (
        run_root / "review" / "acceptance" / "delivery_guard_report.json"
    ).resolve()
    if not guard_path.is_file():
        raise ContractError("Kernel guarded decision Delivery Guard Report is absent")

    try:
        eligibility = AcceptanceV2Provider(project).guard_eligibility(
            workspace_root=acceptance_path.parent
        )
    except (KernelError, OSError, KeyError, TypeError, ValueError) as exc:
        raise ContractError(
            "Kernel guarded decision lacks committed Acceptance provider authority"
        ) from exc
    report = read_json(acceptance_path)
    if (
        eligibility.get("eligible") is not True
        or eligibility.get("delivery_authority") is not True
        or eligibility.get("report_sha256") != report.get("report_sha256")
    ):
        raise ContractError(
            "Kernel guarded decision lacks committed Acceptance provider authority"
        )
    validate_acceptance_report(
        project_root=project,
        report_path=acceptance_path,
        run_id=run_id,
        coordination_revision=read_json(acceptance_path)["run_binding"][
            "coordination_revision"
        ],
    )

    active_guard = _load_active_delivery_guard(project)
    try:
        target = active_guard.resolve_delivery_target(
            project_root=project,
            current_target_path=session_path,
            require_session_scope=True,
        )
        guard_fresh = active_guard.guard_report_is_fresh(target)
    except Exception as exc:
        raise ContractError("Kernel guarded decision cannot resolve active Guard authority") from exc
    if (
        target.video_output_dir.resolve() != run_root
        or target.current_target_path.resolve() != session_path
        or target.target_file.resolve() != video_path
        or target.acceptance_report_path.resolve() != acceptance_path
        or target.guard_report_path.resolve() != guard_path
        or target.stage != "accepted"
        or guard_fresh is not True
    ):
        raise ContractError("Kernel guarded decision Delivery Guard authority is stale")
    validate_delivery_guard_report(
        report_path=guard_path,
        expected_stage="accepted",
    )
    return {
        "run_id": run_id,
        "stage": "accepted",
        "acceptance_report": {
            "path": str(acceptance_path),
            "sha256": sha256_file(acceptance_path),
        },
        "delivery_guard_report": {
            "path": str(guard_path),
            "sha256": sha256_file(guard_path),
        },
        "video_target": {
            "path": str(video_path),
            "sha256": sha256_file(video_path),
        },
        "session_target": {
            "path": str(session_path),
            "sha256": sha256_file(session_path),
        },
    }


def _fingerprints_by_path(fingerprints: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(fingerprints, list):
        raise ContractError("Kernel delivered decision Guard fingerprints are invalid")
    indexed: dict[str, dict[str, Any]] = {}
    for fingerprint in fingerprints:
        if not isinstance(fingerprint, dict):
            raise ContractError(
                "Kernel delivered decision Guard fingerprints are invalid"
            )
        path = fingerprint.get("path")
        if not isinstance(path, str) or not path or path in indexed:
            raise ContractError(
                "Kernel delivered decision Guard fingerprints are invalid"
            )
        indexed[path] = fingerprint
    return indexed


def require_current_kernel_delivered_decision(
    *, project_root: Path, run_dir: Path
) -> dict[str, Any]:
    """Require current delivered projections and current guarded artifacts.

    The accepted Guard report necessarily fingerprints the accepted projection
    predecessor.  A delivered lifecycle transition advances the Run, video,
    and session projections.  The active resolver proves that successor, while
    this seam revalidates every delivery artifact that must remain byte-current.
    """

    graph = _require_kernel_projection_graph(
        project_root=project_root, run_dir=run_dir, expected_stage="delivered"
    )
    project = graph["project"]
    run_root = graph["run_root"]
    video_path = graph["video_path"]
    session_path = graph["session_path"]
    artifacts = graph["artifacts"]
    bound = {
        role: _bound_file(
            artifacts.get(role),
            base=run_root,
            allowed_root=run_root,
            label=role.replace("_", " "),
        )
        for role in (
            "final_pdf",
            "main_tex",
            "final_compile_report",
            "acceptance_report",
            "delivery_guard_report",
        )
    }
    active_guard = _load_active_delivery_guard(project)
    try:
        target = active_guard.resolve_delivery_target(
            project_root=project,
            current_target_path=session_path,
            require_session_scope=True,
        )
        guard_fresh = active_guard.guard_report_is_fresh(target)
    except Exception as exc:
        raise ContractError(
            "Kernel delivered decision cannot resolve active Guard authority"
        ) from exc
    expected_target_paths = {
        "final_pdf": target.final_pdf.resolve(),
        "main_tex": target.main_tex.resolve(),
        "final_compile_report": target.compile_report_path.resolve(),
        "acceptance_report": target.acceptance_report_path.resolve(),
        "delivery_guard_report": target.guard_report_path.resolve(),
    }
    if (
        target.video_output_dir.resolve() != run_root
        or target.current_target_path.resolve() != session_path
        or target.target_file.resolve() != video_path
        or target.stage != "delivered"
        or any(bound[role] != path for role, path in expected_target_paths.items())
    ):
        raise ContractError("Kernel delivered decision authority is stale")
    report = validate_delivery_guard_report(
        report_path=bound["delivery_guard_report"], expected_stage="accepted"
    )
    if guard_fresh is not True:
        try:
            manifest = read_json(target.manifest_path)
            current_fingerprints = active_guard.guard_fingerprints(target, manifest)
            guarded = _fingerprints_by_path(report.get("artifact_fingerprints"))
            current = _fingerprints_by_path(current_fingerprints)
        except ContractError:
            raise
        except Exception as exc:
            raise ContractError(
                "Kernel delivered decision cannot revalidate Guard artifacts"
            ) from exc

        # The delivered transition advances the two lifecycle projections after
        # the accepted Guard report is written.  Every other Guard-managed member
        # remains immutable and must match the active Guard's complete evidence
        # closure, including the allowed-artifact manifest, rendered pages, the
        # Global Gate authority, and any future manifest members.
        mutable_projection_paths = {
            target.current_target_path.resolve()
            .relative_to(project)
            .as_posix(),
            target.target_file.resolve().relative_to(run_root).as_posix(),
        }
        guarded = {
            path: fingerprint
            for path, fingerprint in guarded.items()
            if path not in mutable_projection_paths
        }
        current = {
            path: fingerprint
            for path, fingerprint in current.items()
            if path not in mutable_projection_paths
        }
        if guarded != current:
            raise ContractError(
                "Kernel delivered decision Delivery Guard artifacts are stale"
            )
    return {
        "run_id": graph["run_id"],
        "stage": "delivered",
        **{
            role: {"path": str(path), "sha256": sha256_file(path)}
            for role, path in bound.items()
        },
        "video_target": {
            "path": str(video_path),
            "sha256": sha256_file(video_path),
        },
        "session_target": {
            "path": str(session_path),
            "sha256": sha256_file(session_path),
        },
    }
