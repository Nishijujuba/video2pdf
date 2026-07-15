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
