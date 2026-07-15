from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any

from .evidence import sha256_file


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
