from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .errors import ContractError
from .utils import read_json


PROMPT_REGISTRY = Path("prompts/video-workflow/registry.v1.json")
ROLE_IDENTITY = "source-acquisition"
PLATFORM_IDENTITY = "fixture"
ROLE_PATH = "prompts/video-workflow/roles/source-acquisition.v1.md"
PLATFORM_PATH = "prompts/video-workflow/platforms/fixture.v1.md"


def _canonical_markdown(path: Path) -> bytes:
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ContractError(f"prompt source is unreadable UTF-8: {path}: {exc}") from exc
    if b"\r" in raw:
        raise ContractError(f"prompt source must use canonical LF: {path}")
    if not text.endswith("\n"):
        raise ContractError(f"prompt source must end with LF: {path}")
    return raw


def _entry(
    entries: Any, identity: str, expected_path: str, category: str
) -> dict[str, str]:
    if not isinstance(entries, list) or len(entries) != 1:
        raise ContractError(f"prompt registry must contain one Slice 2 {category}")
    raw = entries[0]
    if not isinstance(raw, dict) or set(raw) != {"identity", "version", "path"}:
        raise ContractError(f"prompt registry {category} entry is not closed")
    if (
        raw["identity"] != identity
        or raw["version"] != "1.0.0"
        or raw["path"] != expected_path
    ):
        raise ContractError(
            f"prompt registry {category} identity/version/path is unsupported"
        )
    if not isinstance(raw["path"], str):
        raise ContractError(f"prompt registry {category} path is invalid")
    return raw


def generate_source_acquisition_prompt(project_root: Path) -> tuple[bytes, dict[str, Any]]:
    registry_path = (project_root / PROMPT_REGISTRY).resolve()
    try:
        registry_path.relative_to(project_root.resolve())
    except ValueError as exc:
        raise ContractError("Video Workflow prompt registry escapes the project") from exc
    registry = read_json(registry_path)
    if not isinstance(registry, dict) or set(registry) != {
        "schema_name",
        "schema_version",
        "roles",
        "platforms",
    }:
        raise ContractError("Video Workflow prompt registry is not closed")
    if (
        registry["schema_name"] != "video-workflow-prompt-registry"
        or registry["schema_version"] != "1.0.0"
    ):
        raise ContractError("Video Workflow prompt registry version is unsupported")
    role = _entry(registry["roles"], ROLE_IDENTITY, ROLE_PATH, "role")
    platform = _entry(
        registry["platforms"], PLATFORM_IDENTITY, PLATFORM_PATH, "platform"
    )
    sources: list[tuple[dict[str, str], bytes]] = []
    for entry in (role, platform):
        relative = Path(entry["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ContractError("prompt source path escapes the project")
        path = (project_root / relative).resolve()
        try:
            path.relative_to(project_root.resolve())
        except ValueError as exc:
            raise ContractError("prompt source path escapes the project") from exc
        sources.append((entry, _canonical_markdown(path)))
    prompt = sources[0][1] + b"\n" + sources[1][1]
    provenance = {
        "sha256": hashlib.sha256(prompt).hexdigest(),
        "role_template": {
            **role,
            "sha256": hashlib.sha256(sources[0][1]).hexdigest(),
        },
        "platform_overlay": {
            **platform,
            "sha256": hashlib.sha256(sources[1][1]).hexdigest(),
        },
    }
    return prompt, provenance
