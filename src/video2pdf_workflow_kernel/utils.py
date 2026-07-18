from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Literal

from .evidence import sha256_file
from .errors import KernelError


def utf16_units(value: str | Path) -> int:
    return len(str(value).encode("utf-16-le")) // 2


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def write_json_atomic(path: Path, value: Any) -> str:
    data = canonical_json_bytes(value)
    temp = path.with_name(f".{path.name}.kernel-new")
    with temp.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)
    return sha256_bytes(data)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_title(value: str) -> str:
    chars: list[str] = []
    for char in value:
        if char.isalnum() or char in {" ", "_"}:
            chars.append(char)
        else:
            chars.append("_")
    normalized = "".join(chars)
    normalized = re.sub(r" +", " ", normalized)
    normalized = re.sub(r"_+", "_", normalized)
    normalized = normalized.strip(" _.")
    return normalized or "video"


def truncate_utf16(value: str, units: int) -> str:
    result: list[str] = []
    used = 0
    for char in value:
        width = utf16_units(char)
        if used + width > units:
            break
        result.append(char)
        used += width
    return "".join(result).rstrip(" _.")


def normalized_physical_path(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path)).casefold()


def require_contained_path(
    path: Path,
    boundary: Path,
    *,
    purpose: str,
    error_type: type[KernelError],
    leaf_kind: Literal["any", "directory", "file"] = "any",
    allow_missing: bool = False,
    require_single_link: bool = False,
) -> Path:
    """Fail closed when a path escapes or traverses any filesystem link.

    The check is lexical and physical. Every existing component from the declared
    boundary through the leaf is inspected with ``lstat`` so a symlink, Windows
    junction, or other reparse point is rejected even when it resolves back into
    the boundary. Missing leaves are permitted only for preflighted writes.
    """

    lexical_boundary = Path(os.path.abspath(boundary))
    lexical_path = Path(os.path.abspath(path))
    try:
        relative = lexical_path.relative_to(lexical_boundary)
    except ValueError as exc:
        raise error_type(f"{purpose} escapes its declared boundary") from exc

    current = lexical_boundary
    chain = [lexical_boundary]
    for part in relative.parts:
        current = current / part
        chain.append(current)
    leaf_info = None
    for index, candidate in enumerate(chain):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise error_type(f"{purpose} path metadata is unavailable") from exc
        is_link = stat.S_ISLNK(info.st_mode)
        is_reparse = bool(
            getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        )
        if is_link or is_reparse:
            raise error_type(f"{purpose} traverses a link or reparse point")
        if index < len(chain) - 1 and not stat.S_ISDIR(info.st_mode):
            raise error_type(f"{purpose} ancestor is not a directory")
        if index == len(chain) - 1:
            leaf_info = info

    try:
        resolved_boundary = lexical_boundary.resolve(strict=not allow_missing)
        resolved_path = lexical_path.resolve(strict=not allow_missing)
        resolved_path.relative_to(resolved_boundary)
    except (OSError, ValueError) as exc:
        raise error_type(
            f"{purpose} is unavailable or resolves outside its boundary"
        ) from exc

    if leaf_info is None:
        if allow_missing:
            return lexical_path
        raise error_type(f"{purpose} is missing")
    if leaf_kind == "file" and not stat.S_ISREG(leaf_info.st_mode):
        raise error_type(f"{purpose} is not a regular file")
    if leaf_kind == "directory" and not stat.S_ISDIR(leaf_info.st_mode):
        raise error_type(f"{purpose} is not a directory")
    if require_single_link and leaf_kind == "file" and leaf_info.st_nlink != 1:
        raise error_type(f"{purpose} is not an independent regular file")
    return lexical_path
