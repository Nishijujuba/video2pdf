"""Fail-closed, write-once result artifacts for the project test runner."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
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


def file_artifact_identity(path: Path) -> dict[str, int]:
    """Record stable opened-path identity and filesystem timestamps."""

    try:
        value = path.stat()
    except OSError as error:
        raise ResultIntegrityError(
            f"cannot observe artifact identity: {path}"
        ) from error
    return {
        "device": int(value.st_dev),
        "inode": int(value.st_ino),
        "size": int(value.st_size),
        "mtime_ns": int(value.st_mtime_ns),
        "ctime_ns": int(value.st_ctime_ns),
    }


def _reject_reparse_components(path: Path) -> None:
    for component in (path, *path.parents):
        if not component.exists():
            continue
        value = component.stat(follow_symlinks=False)
        attributes = getattr(value, "st_file_attributes", 0)
        if component.is_symlink() or attributes & getattr(
            stat,
            "FILE_ATTRIBUTE_REPARSE_POINT",
            0x400,
        ):
            raise ResultIntegrityError(
                f"artifact path contains a reparse point: {component}"
            )


def _windows_final_handle_path(file_descriptor: int) -> Path | None:
    if os.name != "nt":
        return None
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_final_path = kernel32.GetFinalPathNameByHandleW
    get_final_path.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    get_final_path.restype = wintypes.DWORD
    handle = wintypes.HANDLE(msvcrt.get_osfhandle(file_descriptor))
    required = get_final_path(handle, None, 0, 0)
    if required == 0:
        return None
    buffer = ctypes.create_unicode_buffer(required + 1)
    written = get_final_path(handle, buffer, len(buffer), 0)
    if written == 0 or written >= len(buffer):
        return None
    value = buffer.value
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return Path(value)


def read_file_snapshot(
    path: Path,
) -> tuple[Path, bytes, dict[str, int]]:
    """Read bytes and identity from one stable, canonical file handle."""

    try:
        lexical = Path(os.path.abspath(path))
        _reject_reparse_components(lexical)
        canonical = lexical.resolve(strict=True)
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(
            os,
            "O_NOINHERIT",
            0,
        )
        descriptor = os.open(lexical, flags)
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            before = os.fstat(handle.fileno())
            content = handle.read()
            after = os.fstat(handle.fileno())
            _reject_reparse_components(lexical)
            canonical_after = lexical.resolve(strict=True)
            path_stat = lexical.stat(follow_symlinks=False)
            final_handle_path = _windows_final_handle_path(handle.fileno())
    except ResultIntegrityError:
        raise
    except OSError as error:
        raise ResultIntegrityError(
            f"artifact path is missing or unreadable: {path}"
        ) from error
    stable = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    ) == (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    path_matches = (
        canonical_after == canonical
        and (
            path_stat.st_dev,
            path_stat.st_ino,
            path_stat.st_size,
        )
        == (
            after.st_dev,
            after.st_ino,
            after.st_size,
        )
    )
    if os.name == "nt":
        path_matches = path_matches and final_handle_path is not None and (
            os.path.normcase(os.path.abspath(final_handle_path))
            == os.path.normcase(str(canonical))
        )
    if not stable or len(content) != before.st_size:
        raise ResultIntegrityError(f"artifact changed while read: {path}")
    if not path_matches:
        raise ResultIntegrityError(
            f"artifact path identity is unproved: {path}"
        )
    return (
        canonical,
        content,
        {
            "device": int(after.st_dev),
            "inode": int(after.st_ino),
            "size": int(after.st_size),
            "mtime_ns": int(after.st_mtime_ns),
            "ctime_ns": int(after.st_ctime_ns),
        },
    )


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

    def reject_nonfinite_constant(value: str) -> None:
        raise ValueError(f"non-finite numeric constant: {value}")

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
        value = json.loads(
            raw.decode("utf-8"),
            parse_constant=reject_nonfinite_constant,
        )
    except (UnicodeDecodeError, ValueError) as error:
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
