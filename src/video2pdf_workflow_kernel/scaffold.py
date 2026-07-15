from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath

from .contracts import ContractRegistry
from .errors import ContractError, PathBudgetError
from .utils import normalize_title, read_json, truncate_utf16, utf16_units


def load_scaffold(project_root: Path, contracts: ContractRegistry) -> dict:
    path = project_root / "schemas/video-workflow/v1/scaffold.v1.json"
    scaffold = read_json(path)
    contracts.validate("scaffold-contract", scaffold)
    return scaffold


def stable_title_hash(adapter_id: str, item_id: str, original_title: str) -> str:
    value = f"{adapter_id}\0{item_id}\0{original_title}".encode("utf-8")
    return hashlib.sha256(value).hexdigest()[:8]


def output_name(
    *,
    original_title: str,
    timestamp: str,
    adapter_id: str,
    item_id: str,
    max_units: int,
) -> str:
    title = normalize_title(original_title)
    suffix = f"_{timestamp}"
    candidate = f"{title}{suffix}"
    if utf16_units(candidate) <= max_units:
        return candidate
    stable_hash = stable_title_hash(adapter_id, item_id, original_title)
    fixed = f"_{stable_hash}{suffix}"
    prefix = truncate_utf16(title, max_units - utf16_units(fixed))
    if not prefix:
        raise PathBudgetError("output component budget cannot preserve required identity")
    return f"{prefix}{fixed}"


def max_reserved_path_units(output_path: Path, scaffold: dict) -> int:
    return max(
        utf16_units(output_path.joinpath(*PurePosixPath(relative).parts))
        for relative in scaffold["reserved_descendant_paths"]
    )


def validate_path_budget(output_path: Path, scaffold: dict) -> int:
    if utf16_units(output_path.name) > scaffold["max_output_component_utf16_units"]:
        raise PathBudgetError("output directory component exceeds UTF-16 budget")
    maximum = max_reserved_path_units(output_path, scaffold)
    if maximum > scaffold["max_absolute_path_utf16_units"]:
        raise PathBudgetError(
            f"workflow path requires {maximum} UTF-16 units; maximum is "
            f"{scaffold['max_absolute_path_utf16_units']}",
            data={
                "max_path_utf16_units": maximum,
                "workspace_root": str(output_path.parent),
                "candidate_output_path": str(output_path),
            },
        )
    return maximum


def create_scaffold(root: Path, scaffold: dict, run_id: str) -> dict:
    if root.exists():
        raise ContractError(f"staged run directory already exists: {root}")
    root.mkdir(parents=False, exist_ok=False)
    directories: list[dict[str, str]] = []
    for relative in scaffold["managed_directories"]:
        parts = PurePosixPath(relative).parts
        path = root.joinpath(*parts)
        path.mkdir(parents=False, exist_ok=False)
        directories.append({"path": relative, "created_by": "kernel:init-run"})
    return {
        "schema_name": "scaffold-ledger",
        "schema_version": "1.0.0",
        "kernel_version": "2.0.0",
        "run_id": run_id,
        "scaffold_version": scaffold["scaffold_version"],
        "directories": directories,
    }
