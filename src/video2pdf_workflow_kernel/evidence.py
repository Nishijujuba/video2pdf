from __future__ import annotations

import hashlib
import io
from pathlib import Path, PurePosixPath
import subprocess
import tarfile
from typing import Iterable


class EvidenceSupportError(RuntimeError):
    """Low-level evidence operation failure for caller-specific classification."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _run_git(project_root: Path, arguments: tuple[str, ...]) -> bytes:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=project_root,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        message = completed.stderr.decode("utf-8", errors="replace").strip()
        raise EvidenceSupportError(
            f"git {' '.join(arguments)} failed: {message or 'git command failed'}"
        )
    return completed.stdout


def git_output(project_root: Path, *arguments: str) -> str:
    try:
        return _run_git(project_root, arguments).decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise EvidenceSupportError(
            f"git {' '.join(arguments)} returned non-UTF-8 text"
        ) from exc


def sha256_git_blob(project_root: Path, commit: str, path: str) -> str:
    canonical_path = _canonical_git_path(path)
    raw = _run_git(
        project_root,
        ("cat-file", "blob", f"{commit}:{canonical_path}"),
    )
    return hashlib.sha256(raw).hexdigest()


def _canonical_git_path(value: str) -> str:
    relative = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or relative.is_absolute()
        or relative.as_posix() != value
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise EvidenceSupportError(
            f"implementation change has a noncanonical project path: {value!r}"
        )
    return value


def implementation_change_paths(
    project_root: Path,
    slice_base_commit: str,
    implementation_commit: str,
    *,
    excluded_prefixes: Iterable[str] = (),
) -> tuple[str, ...]:
    git_output(project_root, "cat-file", "-e", f"{slice_base_commit}^{{commit}}")
    git_output(project_root, "cat-file", "-e", f"{implementation_commit}^{{commit}}")
    raw = _run_git(
        project_root,
        (
            "diff",
            "--name-status",
            "--no-renames",
            "-z",
            f"{slice_base_commit}...{implementation_commit}",
            "--",
        ),
    )
    tokens = raw.split(b"\0")
    if tokens and tokens[-1] == b"":
        tokens.pop()
    if len(tokens) % 2:
        raise EvidenceSupportError("git diff --name-status returned an incomplete record")
    excluded = tuple(excluded_prefixes)
    paths: list[str] = []
    for index in range(0, len(tokens), 2):
        try:
            status = tokens[index].decode("ascii")
            path = _canonical_git_path(tokens[index + 1].decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise EvidenceSupportError(
                "implementation change contains an unsupported path encoding"
            ) from exc
        if path in excluded or any(path.startswith(prefix) for prefix in excluded):
            continue
        if status == "D":
            raise EvidenceSupportError(
                f"deleted implementation path cannot be fingerprinted: {path}"
            )
        if status not in {"A", "M"}:
            raise EvidenceSupportError(
                f"unsupported implementation change status {status!r}: {path}"
            )
        paths.append(path)
    if len(paths) != len(set(paths)):
        raise EvidenceSupportError("implementation change set contains duplicate paths")
    return tuple(sorted(paths))


def fingerprint_implementation_changes(
    project_root: Path,
    slice_base_commit: str,
    implementation_commit: str,
    *,
    excluded_prefixes: Iterable[str] = (),
) -> list[dict[str, str]]:
    paths = implementation_change_paths(
        project_root,
        slice_base_commit,
        implementation_commit,
        excluded_prefixes=excluded_prefixes,
    )
    if not paths:
        raise EvidenceSupportError("implementation change set is empty")
    archive = _run_git(
        project_root,
        ("archive", "--format=tar", implementation_commit, "--", *paths),
    )
    result: list[dict[str, str]] = []
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as handle:
            for path in paths:
                try:
                    member = handle.getmember(path)
                except KeyError as exc:
                    raise EvidenceSupportError(
                        f"implementation path is absent from its Git commit: {path}"
                    ) from exc
                if not member.isfile() or member.issym() or member.islnk():
                    raise EvidenceSupportError(
                        f"implementation path is not a regular Git file: {path}"
                    )
                extracted = handle.extractfile(member)
                if extracted is None:
                    raise EvidenceSupportError(
                        f"implementation Git file cannot be read: {path}"
                    )
                result.append(
                    {
                        "role": "implementation_artifact",
                        "path": path,
                        "sha256": hashlib.sha256(extracted.read()).hexdigest(),
                    }
                )
    except tarfile.TarError as exc:
        raise EvidenceSupportError("Git implementation archive is invalid") from exc
    return result
