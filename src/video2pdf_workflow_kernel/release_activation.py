from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any

from .contracts import ContractRegistry
from .cutover_retirement import (
    CutoverAuthorityRetirement,
    project_maintenance_fence,
    require_project_admission_open,
    tombstone_path,
)
from .errors import ContractError
from .release_maintenance import ReleaseMaintenance
from .utils import read_json, sha256_file, write_json_atomic


ACTIVATION_FILE = "workflow-admission-activation.v1.json"


def _reject(message: str, gate: str, code: str, **data: Any) -> None:
    raise ContractError(
        message,
        data={"first_failing_gate": gate, "error_code": code, **data},
    )


class WorkflowReleaseActivation:
    """Own coordinated release publication and ordinary-admission activation."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.contracts = ContractRegistry(self.project_root)
        self.maintenance = ReleaseMaintenance(self.project_root)
        self.retirement = CutoverAuthorityRetirement(self.project_root)

    def activate(
        self,
        *,
        project_config: Path,
        activated_at: str,
        global_gate_exit_evidence: Path,
        bilibili_exit_evidence: Path,
        youtube_exit_evidence: Path,
        batch_exit_evidence: Path,
    ) -> dict[str, Any]:
        config_path = project_config.resolve()
        config = self._load_project_config(config_path)
        configured_project_root = config_path.parent.parent.resolve()
        if configured_project_root != self.project_root:
            _reject(
                "Workflow project configuration does not belong to this runtime",
                "project_configuration",
                "workflow_project_configuration_root_mismatch",
            )
        control_root = self._resolve_project_path(
            configured_project_root,
            config["control_store_root"],
            "control_store_root",
        )
        workspace_root = self._resolve_project_path(
            configured_project_root,
            config["workspace_root"],
            "workspace_root",
        )
        profile_path = self._resolve_project_path(
            configured_project_root,
            config["release_profile"],
            "release_profile",
        )
        if control_root != workspace_root:
            _reject(
                "Coordinated activation requires one workspace and Control Store root",
                "project_configuration",
                "workflow_project_configuration_inconsistent",
            )
        if profile_path != self.maintenance.published_profile_path:
            _reject(
                "Workflow project configuration does not select the published Profile",
                "project_configuration",
                "workflow_release_profile_path_mismatch",
            )
        self._require_timestamp(activated_at)
        evidence = {
            "global_gate_exit_evidence": global_gate_exit_evidence,
            "bilibili_exit_evidence": bilibili_exit_evidence,
            "youtube_exit_evidence": youtube_exit_evidence,
            "batch_exit_evidence": batch_exit_evidence,
        }

        with project_maintenance_fence(control_root):
            publication = self.maintenance.publish(
                candidate_profile=profile_path,
                historical_release=True,
                **evidence,
            )
            audit = self.maintenance.audit(
                profile=profile_path,
                historical_release=True,
                **evidence,
            )
            profile = self.maintenance.require_for_admission(
                profile=profile_path,
                capability="batch",
            )
            for capability in ("bilibili", "youtube"):
                self.maintenance.require_for_admission(
                    profile=profile_path,
                    capability=capability,
                )
            retirement = self.retirement.retire(project_config=config_path)
            self._require_tombstone(
                control_root=control_root,
                profile_path=profile_path,
                profile=profile,
            )
            activation_path = profile_path.parent / ACTIVATION_FILE
            activation = self._activation_record(
                activation_path=activation_path,
                profile_path=profile_path,
                profile=profile,
                activated_at=self._ordered_activation_timestamp(
                    requested=activated_at,
                    retired_at=self._require_tombstone(
                        control_root=control_root,
                        profile_path=profile_path,
                        profile=profile,
                    )["retired_at"],
                ),
            )
            self.contracts.validate("workflow-admission-activation", activation)
            write_json_atomic(activation_path, activation)
            return {
                "profile_path": str(profile_path),
                "profile_sha256": activation["profile_sha256"],
                "release_id": profile["release_id"],
                "generation": activation["generation"],
                "activation_path": str(activation_path),
                "tombstone_path": retirement["tombstone_path"],
                "single_video_admission": "profile_backed",
                "batch_admission": "profile_backed",
                "archived_cutover_commands": True,
                "profile_publication": "published_and_audited",
                "historical_evidence": audit["historical_evidence"],
                "publication_release_id": publication["release_id"],
            }

    def require_current(
        self,
        *,
        profile_path: Path,
        profile: dict[str, Any],
        control_store_root: Path,
    ) -> dict[str, Any]:
        root = control_store_root.resolve()
        require_project_admission_open(root)
        self._require_tombstone(
            control_root=root,
            profile_path=profile_path,
            profile=profile,
        )
        activation_path = profile_path.resolve().parent / ACTIVATION_FILE
        try:
            activation = read_json(activation_path)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            _reject(
                f"Profile-backed ordinary admission is not activated: {exc}",
                "activation_fence",
                "profile_admission_not_active",
            )
        try:
            self.contracts.validate("workflow-admission-activation", activation)
        except ContractError as exc:
            _reject(
                str(exc),
                "activation_fence",
                "profile_admission_activation_invalid",
            )
        if (
            activation.get("release_id") != profile["release_id"]
            or activation.get("profile_sha256") != sha256_file(profile_path.resolve())
        ):
            _reject(
                "Profile-backed ordinary admission activation is stale or incompatible",
                "activation_fence",
                "profile_admission_activation_invalid",
            )
        require_project_admission_open(root)
        return activation

    def _require_tombstone(
        self,
        *,
        control_root: Path,
        profile_path: Path,
        profile: dict[str, Any],
    ) -> dict[str, Any]:
        path = tombstone_path(control_root)
        try:
            value = read_json(path)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            _reject(
                f"Cutover Authority Tombstone is unavailable or malformed: {exc}",
                "cutover_authority_tombstone",
                "cutover_authority_not_retired",
            )
        if (
            not isinstance(value, dict)
            or value.get("state") != "RETIRED"
            or value.get("release_id") != profile["release_id"]
            or value.get("contract_compatibility")
            != profile["contract_compatibility"]
            or value.get("profile_path") != str(profile_path.resolve())
        ):
            _reject(
                "Cutover Authority Tombstone conflicts with the selected Profile",
                "cutover_authority_tombstone",
                "cutover_authority_tombstone_invalid",
            )
        return value

    @staticmethod
    def _load_project_config(path: Path) -> dict[str, Any]:
        try:
            value = read_json(path)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            _reject(
                f"Workflow project configuration is unavailable or malformed: {exc}",
                "project_configuration",
                "workflow_project_configuration_invalid",
            )
        required = {
            "schema_name",
            "schema_version",
            "workspace_root",
            "control_store_root",
            "release_profile",
            "ordinary_run_platforms",
            "existing_directory_policy",
        }
        if (
            not isinstance(value, dict)
            or set(value) != required
            or value.get("schema_name") != "workflow-project-config"
            or value.get("schema_version") != "1.0.0"
            or value.get("existing_directory_policy")
            != "explicit_legacy_maintenance_only"
        ):
            _reject(
                "Workflow project configuration violates its v1 contract",
                "project_configuration",
                "workflow_project_configuration_invalid",
            )
        return value

    @staticmethod
    def _resolve_project_path(project_root: Path, raw: Any, field: str) -> Path:
        if not isinstance(raw, str) or not raw.strip() or Path(raw).is_absolute():
            _reject(
                f"Workflow project configuration field {field} is invalid",
                "project_configuration",
                "workflow_project_configuration_invalid",
            )
        resolved = (project_root / raw).resolve()
        if not resolved.is_relative_to(project_root):
            _reject(
                f"Workflow project configuration field {field} escapes the project",
                "project_configuration",
                "workflow_project_configuration_path_escape",
            )
        return resolved

    @staticmethod
    def _require_timestamp(value: str) -> None:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            _reject(
                "Activation timestamp must be ISO-8601",
                "activation_timestamp",
                "workflow_release_activation_timestamp_invalid",
            )
        if parsed.tzinfo is None:
            _reject(
                "Activation timestamp must include a timezone",
                "activation_timestamp",
                "workflow_release_activation_timestamp_invalid",
            )

    @staticmethod
    def _ordered_activation_timestamp(*, requested: str, retired_at: str) -> str:
        requested_value = datetime.fromisoformat(requested.replace("Z", "+00:00"))
        retired_value = datetime.fromisoformat(retired_at.replace("Z", "+00:00"))
        if requested_value >= retired_value:
            return requested
        return retired_at

    @staticmethod
    def _activation_record(
        *,
        activation_path: Path,
        profile_path: Path,
        profile: dict[str, Any],
        activated_at: str,
    ) -> dict[str, Any]:
        profile_sha = sha256_file(profile_path)
        generation = 1
        if activation_path.is_file():
            current = read_json(activation_path)
            if (
                isinstance(current, dict)
                and current.get("release_id") == profile["release_id"]
                and current.get("profile_sha256") == profile_sha
                and isinstance(current.get("activated_at"), str)
                and datetime.fromisoformat(
                    current["activated_at"].replace("Z", "+00:00")
                )
                >= datetime.fromisoformat(activated_at.replace("Z", "+00:00"))
            ):
                return current
            if isinstance(current, dict) and isinstance(current.get("generation"), int):
                generation = current["generation"] + 1
        return {
            "schema_name": "workflow-admission-activation",
            "schema_version": "1.0.0",
            "activation_status": "active_profile_admission",
            "release_id": profile["release_id"],
            "profile_sha256": profile_sha,
            "generation": generation,
            "activated_at": activated_at,
        }


__all__ = ["ACTIVATION_FILE", "WorkflowReleaseActivation"]
