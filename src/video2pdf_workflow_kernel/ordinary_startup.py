from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any

from .contracts import ContractRegistry
from .control_store import ControlStore
from .cutover_retirement import project_admission_lease
from .errors import CliUsageError, ContractError, ControlStoreUnavailable
from .kernel import VideoWorkflowKernel
from .models import ProductionBootstrapResult
from .production_bootstrap import (
    bootstrap_bilibili_production_probe,
    bootstrap_youtube_production_probe,
)
from .release_activation import WorkflowReleaseActivation
from .release_maintenance import ReleaseMaintenance
from .global_gate import GlobalGatePublisher
from .utils import read_json, require_safe_path_segment


PROJECT_CONFIG_KEYS = {
    "schema_name",
    "schema_version",
    "workspace_root",
    "control_store_root",
    "release_profile",
    "ordinary_run_platforms",
    "existing_directory_policy",
}
SUPPORTED_PLATFORMS = {"bilibili", "youtube"}


def _reject(message: str, gate: str, code: str) -> None:
    raise ContractError(
        message,
        data={"first_failing_gate": gate, "error_code": code},
    )


class OrdinaryRunStartup:
    """Own the complete Profile-backed startup transaction for one video Run."""

    def __init__(self, runtime_project_root: Path) -> None:
        self.runtime_project_root = runtime_project_root.resolve()
        self.contracts = ContractRegistry(self.runtime_project_root)

    def start(
        self,
        *,
        project_config: Path,
        platform: str,
        source_url: str,
        session_id: str,
        credential_ref: Path | None = None,
    ) -> dict[str, Any]:
        require_safe_path_segment(
            session_id,
            purpose="Kernel delivery session identity",
            error_type=CliUsageError,
        )
        config_path = project_config.resolve()
        config = self._load_project_config(config_path)
        paths = self._resolve_project_paths(config_path, config)
        if paths["workspace_root"] != paths["control_store_root"]:
            _reject(
                "workflow project Control Store must own the configured workspace",
                "project_config",
                "workflow_project_control_store_binding_invalid",
            )
        if platform not in config["ordinary_run_platforms"]:
            _reject(
                "requested platform is not enabled by project configuration",
                "platform_activation",
                "ordinary_run_platform_not_configured",
            )
        with project_admission_lease(paths["control_store_root"]):
            profile = ReleaseMaintenance(
                self.runtime_project_root
            ).require_for_admission(
                profile=paths["release_profile"],
                capability=platform,
            )
            activation = WorkflowReleaseActivation(
                self.runtime_project_root
            ).require_current(
                profile_path=paths["release_profile"],
                profile=profile,
                control_store_root=paths["control_store_root"],
            )
            current_global_gate = GlobalGatePublisher().require_current(
                control_store_root=paths["control_store_root"]
            )
            store = self._open_control_store(
                workspace_root=paths["workspace_root"],
                control_store_root=paths["control_store_root"],
            )
            kernel = VideoWorkflowKernel(paths["workspace_root"])
            kernel.control_store = store
            request_id = hashlib.sha256(
                "\0".join(
                    (str(config_path), platform, source_url, session_id)
                ).encode("utf-8")
            ).hexdigest()[:32]
            probe = self._find_probe(
                kernel, request_id=request_id, platform=platform
            )
            if probe is None:
                task_start = (
                    datetime.now().astimezone().replace(microsecond=0).isoformat()
                )
                probe_arguments = {
                    "kernel": kernel,
                    "workspace_root": paths["workspace_root"],
                    "source_url": source_url,
                    "cookie_file": credential_ref,
                    "original_title": None,
                    "task_start": task_start,
                    "request_id": request_id,
                    "explicit_item_selector": None,
                    "provider_recording": None,
                    "provider_mode": "live",
                }
                if platform == "youtube":
                    probe = bootstrap_youtube_production_probe(**probe_arguments)
                else:
                    probe = bootstrap_bilibili_production_probe(**probe_arguments)
            initialized = kernel.initialize_production_source(
                probe,
                session_id=session_id,
                global_gate_binding={
                    "authority_path": current_global_gate["path"],
                    "authority_sha256": current_global_gate["file_sha256"],
                    "generation": current_global_gate["generation"],
                },
            )
            return {
                "run_id": initialized.run_id,
                "run_dir": str(initialized.run_dir),
                "platform": platform,
                "stage": "generating",
                "session_id": session_id,
                "release_id": profile["release_id"],
                "profile_path": str(paths["release_profile"]),
                "control_store_root": str(paths["control_store_root"]),
            }

    def _load_project_config(self, path: Path) -> dict[str, Any]:
        try:
            value = read_json(path)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            _reject(
                f"workflow project configuration is unavailable or malformed: {exc}",
                "project_config",
                "workflow_project_config_invalid",
            )
        if not isinstance(value, dict) or set(value) != PROJECT_CONFIG_KEYS:
            _reject(
                "workflow project configuration has unsupported or missing fields",
                "project_config",
                "workflow_project_config_invalid",
            )
        if (
            value.get("schema_name") != "workflow-project-config"
            or value.get("schema_version") != "1.0.0"
            or value.get("existing_directory_policy")
            != "explicit_legacy_maintenance_only"
        ):
            _reject(
                "workflow project configuration identity is incompatible",
                "project_config",
                "workflow_project_config_incompatible",
            )
        path_fields = (
            "workspace_root",
            "control_store_root",
            "release_profile",
        )
        if any(
            not isinstance(value.get(name), str) or not value[name].strip()
            for name in path_fields
        ):
            _reject(
                "workflow project configuration paths are invalid",
                "project_config",
                "workflow_project_config_invalid",
            )
        platforms = value.get("ordinary_run_platforms")
        if (
            not isinstance(platforms, list)
            or not platforms
            or len(platforms) != len(set(platforms))
            or any(item not in SUPPORTED_PLATFORMS for item in platforms)
        ):
            _reject(
                "workflow project configuration platforms are invalid",
                "project_config",
                "workflow_project_config_invalid",
            )
        return value

    @staticmethod
    def _resolve_project_paths(
        config_path: Path, config: dict[str, Any]
    ) -> dict[str, Path]:
        base = config_path.parent.parent
        return {
            key: (base / config[key]).resolve()
            for key in (
                "workspace_root",
                "control_store_root",
                "release_profile",
            )
        }

    def _find_probe(
        self,
        kernel: VideoWorkflowKernel,
        *,
        request_id: str,
        platform: str,
    ) -> ProductionBootstrapResult | None:
        matches: list[ProductionBootstrapResult] = []
        for path in kernel.bootstrap_root.glob("*/probe.json"):
            try:
                value = read_json(path)
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if value.get("request_id") != request_id:
                continue
            self.contracts.validate("bootstrap-record", value)
            if value.get("canonical_platform") != platform:
                _reject(
                    "persisted start-run Bootstrap platform conflicts with the request",
                    "bootstrap_identity",
                    "start_run_bootstrap_identity_conflict",
                )
            matches.append(
                ProductionBootstrapResult(
                    run_id=value["run_id"],
                    request_id=value["request_id"],
                    record_path=path.resolve(),
                    original_title=value["original_title"],
                    task_start=value["task_start"],
                    canonical_platform=value["canonical_platform"],
                    canonical_item_id=value["canonical_item_id"],
                    source_identity=value["source_identity"],
                )
            )
        if len(matches) > 1:
            _reject(
                "multiple Bootstrap records claim the same start-run request",
                "bootstrap_identity",
                "start_run_bootstrap_identity_ambiguous",
            )
        return matches[0] if matches else None

    def _open_control_store(
        self, *, workspace_root: Path, control_store_root: Path
    ) -> ControlStore:
        if ControlStore.identity_evidence_exists(control_store_root):
            store = ControlStore(control_store_root, self.contracts)
            store.check()
            return store
        if self._contains_prior_authority(workspace_root):
            raise ControlStoreUnavailable(
                "Fresh Control Store Initialization requires a pristine workspace",
                data={
                    "first_failing_gate": "control_store_identity",
                    "error_code": "fresh_control_store_not_pristine",
                },
            )
        return ControlStore.initialize(control_store_root, self.contracts)

    @staticmethod
    def _contains_prior_authority(root: Path) -> bool:
        if not root.exists():
            return False
        if not root.is_dir():
            return True
        delivery_targets = root.parent / ".codex" / "delivery-targets"
        if delivery_targets.is_file() or (
            delivery_targets.is_dir()
            and any(path.is_file() for path in delivery_targets.rglob("*"))
        ):
            return True
        try:
            governed_patterns = (
                "workflow/run.json",
                "workflow/scaffold-ledger.json",
                "待删除/bootstrap/prepared-run.json",
            )
            if any(any(root.rglob(pattern)) for pattern in governed_patterns):
                return True
            disposable_root = root.parent / "待删除"
            return any(
                path.is_file()
                for directory in (
                    "bootstrap",
                    "pipeline-bootstrap",
                    "kernel-initialization",
                )
                for path in (disposable_root / directory).rglob("*")
            )
        except OSError:
            return True
