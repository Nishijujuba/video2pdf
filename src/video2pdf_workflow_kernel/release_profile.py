from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .contracts import ContractRegistry
from .errors import ContractError
from .utils import read_json


EXPECTED_CONTRACT_COMPATIBILITY = {
    "kernel": "2.0.0",
    "global_gate": "acceptance-report-v2",
    "bilibili_adapter": "1.0.0",
    "youtube_adapter": "1.0.0",
    "batch": "1.0.0",
}
PUBLISHED_PROFILE_RELATIVE_PATH = Path("config/workflow-release-profile.v1.json")


def _reject(message: str, gate: str, code: str, **data: Any) -> None:
    raise ContractError(
        message,
        data={"first_failing_gate": gate, "error_code": code, **data},
    )


class WorkflowReleaseProfile:
    """Validate the repository-owned post-release admission authority."""

    def __init__(self, project_root: Path) -> None:
        self.contracts = ContractRegistry(project_root.resolve())

    def load(self, path: Path) -> dict[str, Any]:
        try:
            value = read_json(path.resolve())
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            _reject(
                f"Workflow Release Profile is unavailable or malformed: {exc}",
                "release_profile_schema",
                "workflow_release_profile_invalid",
            )
        try:
            self.contracts.validate("workflow-release-profile", value)
        except ContractError as exc:
            _reject(
                str(exc),
                "release_profile_schema",
                "workflow_release_profile_invalid",
            )
        if value["contract_compatibility"] != EXPECTED_CONTRACT_COMPATIBILITY:
            _reject(
                "Workflow Release Profile is incompatible with the running contracts",
                "contract_compatibility",
                "workflow_release_profile_incompatible",
            )
        capabilities = value["capabilities"]
        if capabilities["global_gate"] != "active" and any(
            state == "active"
            for name, state in capabilities.items()
            if name != "global_gate"
        ):
            _reject(
                "Workflow Release Profile capabilities are incoherent",
                "capability_coherence",
                "workflow_release_profile_incoherent",
            )
        return value

    @staticmethod
    def require_active(
        profile: dict[str, Any], capability: str, *, gate: str
    ) -> None:
        if profile["capabilities"][capability] != "active":
            _reject(
                f"Workflow Release Profile capability is inactive: {capability}",
                gate,
                "workflow_release_capability_inactive",
                capability=capability,
            )
