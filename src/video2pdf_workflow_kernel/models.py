from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BootstrapProbeResult:
    run_id: str
    request_id: str
    record_path: Path
    original_title: str
    task_start: str
    canonical_item_id: str
    fixture_manifest_sha256: str


@dataclass(frozen=True)
class ProductionBootstrapResult:
    run_id: str
    request_id: str
    record_path: Path
    original_title: str
    task_start: str
    canonical_platform: str
    canonical_item_id: str
    source_identity: str


@dataclass(frozen=True)
class DeterministicLocatorRequest:
    source_url: str
    original_title: str
    explicit_item_selector: str | None = None


@dataclass(frozen=True)
class ProductionInitializationResult:
    run_id: str
    run_dir: Path
    classification: str = "source_acquisition_pending"


@dataclass(frozen=True)
class TraceResult:
    run_id: str
    run_dir: Path
    classification: str
    max_path_utf16_units: int
    adapter_capabilities: tuple[str, ...]


@dataclass(frozen=True)
class ReconcileResult:
    run_id: str
    run_dir: Path
    outcome: str


@dataclass(frozen=True)
class ControlStoreHealth:
    status: str
    schema_version: int
    pragmas: dict[str, int | str]
    quick_check: str
    path: Path
    lock_contention_checked: bool
    atomic_replace_checked: bool


@dataclass(frozen=True)
class ResourceAdmissionState:
    queue_id: str
    task_id: str
    attempt_id: str
    run_id: str
    fairness_group_id: str
    batch_id: str | None
    claim_generation: int
    queue_state: str
    required_resources: tuple[str, ...]
    configuration_id: str
    configuration_version: int
    configuration_sha256: str
    lease_id: str | None
    lease_state: str | None
    launch_token: str | None
    launch_authorization_state: str | None
    launch_required_resources: tuple[str, ...] | None
    launch_eligible: bool
    bypass_count: int
    reservation_state: str
    reservation_seq: int | None


@dataclass(frozen=True)
class TaskPreparationResult:
    run_id: str
    run_dir: Path
    task_id: str
    task_dir: Path
    envelope_path: Path
    prompt_path: Path
    classification: str = "task_prepared"


@dataclass(frozen=True)
class TaskClaimResult:
    run_id: str
    run_dir: Path
    task_id: str
    attempt_id: str
    claim_generation: int
    attempt_dir: Path
    resource_admission: ResourceAdmissionState | None = None
    classification: str = "task_claimed"


@dataclass(frozen=True)
class TaskCompletionResult:
    run_id: str
    run_dir: Path
    task_id: str
    attempt_id: str
    claim_generation: int
    completion_path: Path
    classification: str = "validated_waiting_for_promotion"


@dataclass(frozen=True)
class TaskPromotionResult:
    run_id: str
    run_dir: Path
    task_id: str
    attempt_id: str
    claim_generation: int
    intent_id: str
    classification: str = "committed_complete"
