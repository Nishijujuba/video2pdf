from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ContractError
from .release_activation import WorkflowReleaseActivation
from .release_profile import WorkflowReleaseProfile
from .utils import read_json


_REQUIRED_PROJECT_CONFIGURATION = {
    "schema_name",
    "schema_version",
    "workspace_root",
    "control_store_root",
    "release_profile",
    "ordinary_run_platforms",
    "existing_directory_policy",
}
def _reject(message: str, gate: str, code: str, **data: Any) -> None:
    raise ContractError(
        message,
        data={"first_failing_gate": gate, "error_code": code, **data},
    )


@dataclass(frozen=True)
class BatchAdmission:
    workspace_root: Path
    control_store_root: Path
    profile_path: Path
    release_id: str
    profile_sha256: str
    activation_generation: int
    capabilities: tuple[tuple[str, str], ...]


class OrdinaryAdmission:
    """Own project configuration and Profile checks for ordinary work."""

    def __init__(self, code_project_root: Path) -> None:
        self.code_project_root = code_project_root.resolve()
        self.profiles = WorkflowReleaseProfile(self.code_project_root)
        self.activation = WorkflowReleaseActivation(self.code_project_root)

    def require_batch(self, project_config: Path, platform: str) -> BatchAdmission:
        config_path = project_config.resolve()
        config = self._load_project_config(config_path)
        if platform not in config["ordinary_run_platforms"]:
            _reject(
                f"Project configuration does not support ordinary {platform} Runs",
                "project_configuration",
                "workflow_project_platform_unsupported",
                platform=platform,
            )
        project_root = config_path.parent.parent.resolve()
        workspace_root = self._resolve_project_path(
            project_root, config["workspace_root"], "workspace_root"
        )
        control_store_root = self._resolve_project_path(
            project_root,
            config["control_store_root"],
            "control_store_root",
        )
        profile_path = self._resolve_project_path(
            project_root,
            config["release_profile"],
            "release_profile",
        )
        profile = self.profiles.load(profile_path)
        self.profiles.require_active(
            profile, "global_gate", gate="global_gate_capability"
        )
        self.profiles.require_active(
            profile, platform, gate="platform_capability"
        )
        self.profiles.require_active(profile, "batch", gate="batch_capability")
        activation = self.activation.require_current(
            profile_path=profile_path,
            profile=profile,
            control_store_root=control_store_root,
        )
        return BatchAdmission(
            workspace_root=workspace_root,
            control_store_root=control_store_root,
            profile_path=profile_path,
            release_id=profile["release_id"],
            profile_sha256=activation["profile_sha256"],
            activation_generation=activation["generation"],
            capabilities=tuple(sorted(profile["capabilities"].items())),
        )

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
        if not isinstance(value, dict):
            _reject(
                "Workflow project configuration must be a JSON object",
                "project_configuration",
                "workflow_project_configuration_invalid",
            )
        keys = set(value)
        if (
            not _REQUIRED_PROJECT_CONFIGURATION.issubset(keys)
            or keys != _REQUIRED_PROJECT_CONFIGURATION
            or value.get("schema_name") != "workflow-project-config"
            or value.get("schema_version") != "1.0.0"
            or value.get("existing_directory_policy")
            != "explicit_legacy_maintenance_only"
            or any(
                not isinstance(value.get(name), str) or not value[name]
                for name in ("workspace_root", "control_store_root", "release_profile")
            )
            or not isinstance(value.get("ordinary_run_platforms"), list)
            or not value["ordinary_run_platforms"]
            or any(
                platform not in {"bilibili", "youtube"}
                for platform in value["ordinary_run_platforms"]
            )
            or len(set(value["ordinary_run_platforms"]))
            != len(value["ordinary_run_platforms"])
        ):
            _reject(
                "Workflow project configuration violates its v1 contract",
                "project_configuration",
                "workflow_project_configuration_invalid",
            )
        return value

    @staticmethod
    def _resolve_project_path(
        project_root: Path, raw: str, field: str
    ) -> Path:
        relative = Path(raw)
        if relative.is_absolute():
            _reject(
                f"Workflow project configuration {field} must be relative",
                "project_configuration",
                "workflow_project_configuration_path_escape",
                field=field,
            )
        resolved = (project_root / relative).resolve()
        if not resolved.is_relative_to(project_root):
            _reject(
                f"Workflow project configuration {field} escapes the project root",
                "project_configuration",
                "workflow_project_configuration_path_escape",
                field=field,
            )
        return resolved
