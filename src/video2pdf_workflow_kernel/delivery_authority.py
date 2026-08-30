from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .cutover_retirement import tombstone_path
from .errors import ContractError
from .platform_kernel import BilibiliPlatformCutoverPublisher
from .release_activation import ACTIVATION_FILE, WorkflowReleaseActivation
from .release_maintenance import PROFILE_RELATIVE_PATH, ReleaseMaintenance
from .utils import read_json


class DeliveryTransitionAuthority:
    """Select the active platform authority boundary for delivery completion."""

    def __init__(self, repository_root: Path) -> None:
        self.repository_root = repository_root.resolve()

    def require_current(
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
        runtime_project_root = run_dir.resolve().parents[1]
        committed_tombstone = tombstone_path(root)
        profile_path = self._profile_path(
            tombstone=committed_tombstone,
            runtime_project_root=runtime_project_root,
            platform=platform,
        )
        activation_path = profile_path.parent / ACTIVATION_FILE

        if committed_tombstone.is_file() or activation_path.is_file():
            self._require_profile_authority(
                platform=platform,
                control_store_root=root,
                profile_path=profile_path,
            )
            return

        BilibiliPlatformCutoverPublisher().authorize_delivery_transition(
            platform=platform,
            control_store_root=root,
            run_dir=run_dir,
            run_id=run_id,
            to_stage=to_stage,
        )

    def _require_profile_authority(
        self,
        *,
        platform: str,
        control_store_root: Path,
        profile_path: Path,
    ) -> None:
        try:
            profile = ReleaseMaintenance(
                self.repository_root
            ).require_for_admission(
                profile=profile_path,
                capability=platform,
            )
            WorkflowReleaseActivation(self.repository_root).require_current(
                profile_path=profile_path,
                profile=profile,
                control_store_root=control_store_root,
            )
        except ContractError as exc:
            data: dict[str, Any] = dict(exc.data)
            data.update(
                {
                    "platform": platform,
                    "authority_boundary": "workflow_release_profile",
                }
            )
            raise ContractError(
                f"{platform} ordinary delivery transition requires current "
                f"Workflow Release Profile authority: {exc}",
                data=data,
            ) from exc

    @staticmethod
    def _profile_path(
        *,
        tombstone: Path,
        runtime_project_root: Path,
        platform: str,
    ) -> Path:
        if not tombstone.is_file():
            return runtime_project_root / PROFILE_RELATIVE_PATH
        try:
            value = read_json(tombstone)
            raw_path = value.get("profile_path")
            if not isinstance(raw_path, str) or not raw_path:
                raise ValueError("profile_path is absent")
            return Path(raw_path).resolve()
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise ContractError(
                f"{platform} ordinary delivery transition cannot resolve the "
                f"Workflow Release Profile from the Cutover Authority Tombstone: {exc}",
                data={
                    "first_failing_gate": "cutover_authority_tombstone",
                    "error_code": "cutover_authority_tombstone_invalid",
                    "platform": platform,
                    "authority_boundary": "workflow_release_profile",
                },
            ) from exc


__all__ = ["DeliveryTransitionAuthority"]
