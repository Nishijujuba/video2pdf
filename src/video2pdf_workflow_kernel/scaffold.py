from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath

from .contracts import ContractRegistry, _validate_project_relative_path
from .errors import ContractError, PathBudgetError
from .utils import normalize_title, truncate_utf16, utf16_units


def load_scaffold(project_root: Path, contracts: ContractRegistry) -> dict:
    return contracts.canonical_instance("scaffold-contract")


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
    collision_suffix: str = "",
) -> str:
    title = normalize_title(original_title)
    suffix = f"_{timestamp}{collision_suffix}"
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


def output_component_budget(workspace_root: Path, scaffold: dict) -> int:
    """Return the title-component budget that keeps every reserved path valid."""

    absolute_budget = int(scaffold["max_absolute_path_utf16_units"])
    # max_reserved_path_units(workspace_root, ...) already includes the separator
    # before each reserved descendant. One additional separator is required for
    # the output directory component inserted between those two path segments.
    available = absolute_budget - max_reserved_path_units(workspace_root, scaffold) - 1
    return min(int(scaffold["max_output_component_utf16_units"]), available)


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
    resolved_root = root.resolve()
    planned: list[tuple[str, Path]] = []
    for relative in scaffold["managed_directories"]:
        _validate_project_relative_path(relative)
        path = root.joinpath(*PurePosixPath(relative).parts)
        try:
            path.resolve().relative_to(resolved_root)
        except ValueError as exc:
            raise ContractError(
                f"managed scaffold directory escapes its root: {relative!r}"
            ) from exc
        planned.append((relative, path))
    root.mkdir(parents=False, exist_ok=False)
    directories: list[dict[str, str]] = []
    for relative, path in planned:
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
