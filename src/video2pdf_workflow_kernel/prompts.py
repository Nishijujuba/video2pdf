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
ROLE_PATHS = {
    ("source-acquisition", "1.0.0"): ROLE_PATH,
    ("source-acquisition", "2.0.0"): "prompts/video-workflow/roles/source-acquisition.v2.md",
}
PLATFORM_PATHS = {
    ("fixture", "1.0.0"): PLATFORM_PATH,
    ("bilibili", "1.0.0"): "prompts/video-workflow/platforms/bilibili.v1.md",
    ("youtube", "1.0.0"): "prompts/video-workflow/platforms/youtube.v1.md",
}


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
    entries: Any,
    identity: str,
    version: str,
    expected: dict[tuple[str, str], str] | str,
    category: str | None = None,
) -> dict[str, str]:
    if category is None:
        expected_path = version
        category = str(expected)
        version = "1.0.0"
        expected = {(identity, version): expected_path}
    if not isinstance(expected, dict):
        raise ContractError(f"prompt registry {category} expectation is invalid")
    if not isinstance(entries, list) or not entries:
        raise ContractError(f"prompt registry must contain versioned {category} entries")
    by_identity: dict[tuple[str, str], dict[str, str]] = {}
    for raw in entries:
        if (
            not isinstance(raw, dict)
            or set(raw) != {"identity", "version", "path"}
            or not all(isinstance(raw[key], str) for key in raw)
        ):
            raise ContractError(f"prompt registry {category} entry is not closed")
        key = (raw["identity"], raw["version"])
        if key in by_identity or key not in expected or raw["path"] != expected[key]:
            raise ContractError(
                f"prompt registry {category} identity/version/path is unsupported"
            )
        by_identity[key] = raw
    if set(by_identity) != set(expected):
        raise ContractError(f"prompt registry {category} entries are incomplete")
    try:
        return by_identity[(identity, version)]
    except KeyError as exc:
        raise ContractError(f"requested prompt {category} is unsupported") from exc


def generate_source_acquisition_prompt(
    project_root: Path,
    *,
    role_version: str = "1.0.0",
    platform: str = "fixture",
    platform_version: str = "1.0.0",
) -> tuple[bytes, dict[str, Any]]:
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
    role = _entry(
        registry["roles"],
        ROLE_IDENTITY,
        role_version,
        ROLE_PATHS,
        "role",
    )
    platform = _entry(
        registry["platforms"],
        platform,
        platform_version,
        PLATFORM_PATHS,
        "platform",
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
