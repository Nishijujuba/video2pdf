"""PROTOTYPE ONLY: pure startup-contract decision model for Issue 79."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal


Decision = Literal["start_run", "block", "route_legacy_maintenance"]


@dataclass(frozen=True)
class ProjectConfig:
    schema_valid: bool = True
    workspace_root: str = "workspace"
    control_store_root: str = "workspace"
    release_profile_path: str = "config/workflow-release-profile.v1.json"
    ordinary_run_platforms: tuple[str, ...] = ("bilibili", "youtube")


@dataclass(frozen=True)
class ReleaseProfile:
    present: bool = True
    schema_valid: bool = True
    contracts_compatible: bool = True
    global_gate_active: bool = True
    active_platforms: tuple[str, ...] = ("bilibili", "youtube")
    historical_exit_evidence_revalidated: bool = False


@dataclass(frozen=True)
class RuntimeState:
    control_store_state: Literal[
        "healthy", "absent_pristine", "identity_incomplete", "unproven_loss"
    ] = "healthy"
    output_path_available: bool = True
    unrelated_active_claims: int = 0
    existing_directory: bool = False


@dataclass(frozen=True)
class StartRequest:
    platform: str = "bilibili"
    source_url: str = "https://example.invalid/video"
    session_id: str = "session-issue-79"


@dataclass
class StartupDecision:
    decision: Decision
    first_failing_gate: str | None
    checks: list[dict[str, str]] = field(default_factory=list)
    effects: list[str] = field(default_factory=list)
    run_state: dict[str, object] | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def evaluate_startup(
    config: ProjectConfig,
    release: ReleaseProfile,
    runtime: RuntimeState,
    request: StartRequest,
) -> StartupDecision:
    """Evaluate the proposed public startup seam without performing I/O."""

    checks: list[dict[str, str]] = []

    def require(gate: str, condition: bool, detail: str) -> StartupDecision | None:
        checks.append({"gate": gate, "status": "pass" if condition else "fail", "detail": detail})
        if condition:
            return None
        return StartupDecision("block", gate, checks=checks)

    blocked = require("project_config", config.schema_valid, "project configuration is schema-valid")
    if blocked:
        return blocked
    blocked = require(
        "release_profile",
        release.present and release.schema_valid,
        "repository-owned Workflow Release Profile is present and schema-valid",
    )
    if blocked:
        return blocked
    blocked = require(
        "contract_compatibility",
        release.contracts_compatible,
        "running workflow contracts are compatible with the published Profile",
    )
    if blocked:
        return blocked
    blocked = require(
        "platform_activation",
        release.global_gate_active
        and request.platform in release.active_platforms
        and request.platform in config.ordinary_run_platforms,
        "requested platform and shared Global Gate are active",
    )
    if blocked:
        return blocked

    checks.append(
        {
            "gate": "historical_exit_evidence",
            "status": "skipped",
            "detail": "ordinary startup does not read or revalidate historical Exit Evidence",
        }
    )

    if runtime.existing_directory:
        checks.append(
            {
                "gate": "existing_directory_identity",
                "status": "route",
                "detail": "existing directories use explicit Legacy maintenance",
            }
        )
        return StartupDecision(
            "route_legacy_maintenance",
            None,
            checks=checks,
            effects=["no Kernel Run created", "no Legacy fallback inferred for a new request"],
        )

    store_usable = runtime.control_store_state in {"healthy", "absent_pristine"}
    blocked = require(
        "control_store_identity",
        store_usable,
        {
            "healthy": "existing Control Store identity and integrity are current",
            "absent_pristine": "workspace is eligible for strict first bootstrap",
            "identity_incomplete": "partial identity forbids automatic replacement",
            "unproven_loss": "unproven authority loss forbids reinitialization",
        }[runtime.control_store_state],
    )
    if blocked:
        return blocked
    blocked = require(
        "output_path_claim",
        runtime.output_path_available,
        "live Control Store grants the requested Run and output-path binding",
    )
    if blocked:
        return blocked

    effects = []
    if runtime.control_store_state == "absent_pristine":
        effects.append("initialize pristine Control Store")
    effects.extend(
        [
            "run Bootstrap Probe",
            "claim output path through live Control Store",
            "create one Kernel Run",
            "bind mandatory final-quality lifecycle",
        ]
    )
    return StartupDecision(
        "start_run",
        None,
        checks=checks,
        effects=effects,
        run_state={
            "platform": request.platform,
            "session_id": request.session_id,
            "phase": "source_acquisition",
            "delivery_stage": "generating",
            "unrelated_active_claims": runtime.unrelated_active_claims,
            "required_before_delivery": [
                "source_faithfulness",
                "writing_quality",
                "pyramid_validation",
                "final_compile_provenance",
                "acceptance_report_v2",
                "every_page_visual_review",
                "delivery_guard",
            ],
        },
    )

