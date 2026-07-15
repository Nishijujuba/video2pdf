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
