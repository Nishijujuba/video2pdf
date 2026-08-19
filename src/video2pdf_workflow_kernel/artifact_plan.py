from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ArtifactPlanBinding:
    logical_id: str
    path: str
    schema_name: str
    generator: str
    earliest_checkpoint: str


ARTIFACT_PLAN_BINDINGS: tuple[ArtifactPlanBinding, ...] = (
    ArtifactPlanBinding(
        "run_record",
        "workflow/run.json",
        "run-record",
        "kernel:init-run",
        "run_initialized",
    ),
    ArtifactPlanBinding(
        "artifact_plan",
        "workflow/artifact-plan.json",
        "artifact-plan",
        "kernel:init-run",
        "run_initialized",
    ),
    ArtifactPlanBinding(
        "bootstrap_record",
        "待删除/bootstrap/probe.json",
        "bootstrap-record",
        "kernel:bootstrap",
        "run_initialized",
    ),
    ArtifactPlanBinding(
        "scaffold_contract",
        "workflow/scaffold-contract.json",
        "scaffold-contract",
        "kernel:init-run",
        "run_initialized",
    ),
    ArtifactPlanBinding(
        "scaffold_ledger",
        "workflow/scaffold-ledger.json",
        "scaffold-ledger",
        "kernel:init-run",
        "run_initialized",
    ),
    ArtifactPlanBinding(
        "source_manifest",
        "source/manifest.json",
        "source-manifest",
        "kernel:verified-import",
        "source_ready",
    ),
)
