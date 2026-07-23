"""Fail-closed, write-once result artifacts for the project test runner."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


class ResultIntegrityError(RuntimeError):
    """A result artifact is missing, mutable, malformed, or fingerprint-mismatched."""


def canonical_json_bytes(value: Any) -> bytes:
    """Encode stable UTF-8 JSON with one trailing newline."""

    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    except OSError as error:
        raise ResultIntegrityError(f"cannot fingerprint artifact: {path}") from error
    return digest.hexdigest()


def write_bytes_exclusive(destination: Path, content: bytes) -> str:
    """Create an artifact exactly once, fsync it, and return its SHA-256."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as error:
        raise ResultIntegrityError(
            f"artifact already exists: {destination}"
        ) from error
    except OSError as error:
        raise ResultIntegrityError(
            f"cannot write artifact: {destination}"
        ) from error
    return hashlib.sha256(content).hexdigest()


def write_json_exclusive(destination: Path, value: Any) -> str:
    """Create one canonical JSON artifact and return its SHA-256."""

    return write_bytes_exclusive(destination, canonical_json_bytes(value))


def read_module_result(
    path: Path,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    """Read a JSON result only when it exists and its optional hash matches."""

    try:
        raw = path.read_bytes()
    except FileNotFoundError as error:
        raise ResultIntegrityError(f"module result is missing: {path}") from error
    except OSError as error:
        raise ResultIntegrityError(f"cannot read module result: {path}") from error
    actual_sha256 = hashlib.sha256(raw).hexdigest()
    if expected_sha256 is not None and actual_sha256 != expected_sha256:
        raise ResultIntegrityError(
            f"module result fingerprint mismatch: {path}"
        )
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ResultIntegrityError(
            f"module result is invalid JSON: {path}"
        ) from error
    if not isinstance(value, dict):
        raise ResultIntegrityError(f"module result must be an object: {path}")
    return value


def verify_summary_artifacts(run_dir: Path, summary: dict[str, Any]) -> None:
    """Re-hash every result and raw log bound by a completed summary."""

    modules = summary.get("modules")
    if not isinstance(modules, list):
        raise ResultIntegrityError("summary modules must be an array")
    for module in modules:
        if not isinstance(module, dict) or not isinstance(
            module.get("module_key"), str
        ):
            raise ResultIntegrityError("summary module identity is invalid")
        key = module["module_key"]
        artifacts = (
            ("assignment_sha256", run_dir / "modules" / f"{key}.assignment.json"),
            ("result_sha256", run_dir / "modules" / f"{key}.result.json"),
            ("stdout_sha256", run_dir / "logs" / f"{key}.stdout.log"),
            ("stderr_sha256", run_dir / "logs" / f"{key}.stderr.log"),
        )
        for field, path in artifacts:
            expected = module.get(field)
            if expected is None:
                continue
            if not isinstance(expected, str) or len(expected) != 64:
                raise ResultIntegrityError(
                    f"summary artifact fingerprint is invalid: {field}"
                )
            actual = sha256_file(path)
            if actual != expected:
                raise ResultIntegrityError(
                    f"summary artifact fingerprint mismatch: {path}"
                )
