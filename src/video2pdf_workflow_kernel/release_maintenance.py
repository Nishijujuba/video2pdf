from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

from .errors import ContractError
from .global_gate_exit_evidence import (
    ExitEvidenceValidationError,
    validate_global_gate_exit_evidence,
)
from .release_profile import WorkflowReleaseProfile
from .utils import read_json, write_json_atomic


PROFILE_RELATIVE_PATH = Path("config/workflow-release-profile.v1.json")
EXPECTED_EVIDENCE_SLICES = {
    "bilibili": {"number": 12, "name": "bilibili-platform-kernel-cutover"},
    "youtube": {"number": 13, "name": "youtube-platform-kernel-cutover"},
    "batch": {"number": 14, "name": "batch-projection-cutover"},
}


def _reject(message: str, gate: str, code: str) -> None:
    raise ContractError(
        message,
        data={"first_failing_gate": gate, "error_code": code},
    )


class ReleaseMaintenance:
    """Own complete historical validation and atomic release-Profile publication."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.profiles = WorkflowReleaseProfile(self.project_root)

    @property
    def published_profile_path(self) -> Path:
        return self.project_root / PROFILE_RELATIVE_PATH

    def require_for_admission(
        self, *, profile: Path, capability: str
    ) -> dict[str, Any]:
        """Validate ordinary capability without reading historical evidence."""

        value = self.profiles.load(profile.resolve())
        if capability not in {"bilibili", "youtube", "batch"}:
            _reject(
                "Workflow Release Profile capability is unsupported",
                "platform_activation",
                "workflow_release_capability_unsupported",
            )
        if (
            value["capabilities"]["global_gate"] != "active"
            or value["capabilities"][capability] != "active"
        ):
            _reject(
                "Workflow Release Profile capability is inactive",
                "platform_activation",
                "workflow_release_capability_inactive",
            )
        return value

    def publish(
        self,
        *,
        candidate_profile: Path,
        global_gate_exit_evidence: Path,
        bilibili_exit_evidence: Path,
        youtube_exit_evidence: Path,
        batch_exit_evidence: Path,
    ) -> dict[str, Any]:
        profile_path = candidate_profile.resolve()
        profile, evidence = self._validate(
            profile=profile_path,
            global_gate=global_gate_exit_evidence,
            bilibili=bilibili_exit_evidence,
            youtube=youtube_exit_evidence,
            batch=batch_exit_evidence,
        )

        output = self.published_profile_path
        output.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(output, profile)
        return {
            "profile_path": str(output),
            "release_id": profile["release_id"],
            "capabilities": profile["capabilities"],
            "historical_evidence": evidence,
            "runtime_activation_changed": False,
        }

    def audit(
        self,
        *,
        profile: Path,
        global_gate_exit_evidence: Path,
        bilibili_exit_evidence: Path,
        youtube_exit_evidence: Path,
        batch_exit_evidence: Path,
    ) -> dict[str, Any]:
        profile_path = profile.resolve()
        value, evidence = self._validate(
            profile=profile_path,
            global_gate=global_gate_exit_evidence,
            bilibili=bilibili_exit_evidence,
            youtube=youtube_exit_evidence,
            batch=batch_exit_evidence,
        )
        return {
            "profile_path": str(profile_path),
            "release_id": value["release_id"],
            "capabilities": value["capabilities"],
            "historical_evidence": evidence,
            "profile_published": False,
            "runtime_authority_changed": False,
        }

    def _validate(
        self,
        *,
        profile: Path,
        **evidence_paths: Path,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        value = self.profiles.load(profile)
        evidence = self._validate_release_package(**evidence_paths)
        return value, evidence

    def _validate_release_package(self, **paths: Path) -> dict[str, Any]:
        global_gate = paths.pop("global_gate").resolve()
        try:
            validated_global_gate = validate_global_gate_exit_evidence(
                global_gate,
                project_root=self.project_root,
                require_current_publication=False,
            )
        except ExitEvidenceValidationError as exc:
            _reject(str(exc), exc.first_failing_gate, exc.error_code)

        validator = self._load_slice_validator()
        evidence = {
            "global_gate": {
                "path": str(validated_global_gate.path),
            }
        }
        for capability in ("bilibili", "youtube", "batch"):
            path = paths[capability].resolve()
            self._require_evidence_slice(path, capability)
            try:
                validator.validate_manifest(
                    path,
                    schema_only=False,
                    pre_publication=False,
                )
            except validator.EvidenceError as exc:
                _reject(str(exc), exc.first_failing_gate, exc.error_code)
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                _reject(
                    f"{capability} Exit Evidence is unavailable: {exc}",
                    "exit_evidence_schema",
                    f"{capability}_exit_evidence_invalid",
                )
            evidence[capability] = {
                "path": str(path),
            }
        return evidence

    def _load_slice_validator(self) -> ModuleType:
        validator_path = self.project_root / "scripts/validate_slice_exit_evidence.py"
        try:
            spec = importlib.util.spec_from_file_location(
                "video2pdf_release_maintenance_exit_evidence_validator",
                validator_path,
            )
            if spec is None or spec.loader is None:
                raise RuntimeError("validator module cannot be loaded")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
        except Exception as exc:
            _reject(
                f"release Exit Evidence validator is unavailable: {exc}",
                "exit_evidence_validator",
                "release_exit_evidence_validator_unavailable",
            )

    def _require_evidence_slice(self, path: Path, capability: str) -> None:
        try:
            value = read_json(path)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            _reject(
                f"{capability} Exit Evidence is unavailable or malformed: {exc}",
                "exit_evidence_schema",
                f"{capability}_exit_evidence_invalid",
            )
        if value.get("slice") != EXPECTED_EVIDENCE_SLICES[capability]:
            _reject(
                f"{capability} Exit Evidence has the wrong release identity",
                "exit_evidence_identity",
                f"{capability}_exit_evidence_identity_invalid",
            )
