from __future__ import annotations

import hashlib
import io
from pathlib import Path, PurePosixPath
import subprocess
import os
import shutil
import sys
import tarfile
from typing import Iterable


class EvidenceSupportError(RuntimeError):
    """Low-level evidence operation failure for caller-specific classification."""


_CANONICAL_ARCHIVE_CONFIG = ("-c", "core.autocrlf=true")


def _trusted_git_executable() -> Path:
    if os.name == "nt":
        approved_roots = (
            Path("D:/kits/Git").resolve(),
            Path("C:/Program Files/Git").resolve(),
            Path("C:/Program Files (x86)/Git").resolve(),
        )
        candidates = tuple(root / "cmd/git.exe" for root in approved_roots)
    else:
        candidates = tuple(
            Path(value)
            for value in (
                "/usr/bin/git",
                "/usr/local/bin/git",
                "/opt/homebrew/bin/git",
            )
        )
        approved_roots = tuple(candidate.parent.resolve() for candidate in candidates)
    discovered = shutil.which("git")
    if discovered:
        candidates = (*candidates, Path(discovered))
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        if resolved.is_file() and any(
            resolved == root or root in resolved.parents for root in approved_roots
        ):
            return resolved
    raise EvidenceSupportError(
        f"approved Git executable is unavailable for {sys.platform}"
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _run_git(project_root: Path, arguments: tuple[str, ...]) -> bytes:
    root = project_root.resolve()
    git_executable = _trusted_git_executable()
    environment = {
        key: os.environ[key]
        for key in ("SYSTEMROOT", "WINDIR")
        if key in os.environ
    }
    authority = subprocess.run(
        [str(git_executable), "-C", str(root), "rev-parse", "--path-format=absolute",
         "--show-toplevel", "--git-dir", "--git-common-dir"],
        cwd=root, capture_output=True, check=False, env=environment,
    )
    lines = authority.stdout.decode("utf-8", errors="replace").splitlines()
    if authority.returncode != 0 or len(lines) != 3 or Path(lines[0]).resolve() != root:
        raise EvidenceSupportError("project_root is not the anchored Git worktree")
    git_dir, common_dir = Path(lines[1]).resolve(), Path(lines[2]).resolve()
    if not git_dir.is_dir() or not common_dir.is_dir():
        raise EvidenceSupportError("anchored Git repository authority is missing")
    completed = subprocess.run(
        [str(git_executable), f"--git-dir={git_dir}", f"--work-tree={root}", *arguments],
        cwd=root,
        capture_output=True,
        check=False,
        env=environment,
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
    paths, _ = _implementation_change_authority(
        project_root,
        slice_base_commit,
        implementation_commit,
        excluded_prefixes=excluded_prefixes,
    )
    return paths


def _implementation_change_authority(
    project_root: Path,
    slice_base_commit: str,
    implementation_commit: str,
    *,
    excluded_prefixes: Iterable[str] = (),
) -> tuple[tuple[str, ...], tuple[dict[str, str | None], ...]]:
    git_output(project_root, "cat-file", "-e", f"{slice_base_commit}^{{commit}}")
    git_output(project_root, "cat-file", "-e", f"{implementation_commit}^{{commit}}")
    raw = _run_git(
        project_root,
        (
            "diff",
            "--name-status",
            "--find-renames",
            "-z",
            f"{slice_base_commit}...{implementation_commit}",
            "--",
        ),
    )
    tokens = raw.split(b"\0")
    if tokens and tokens[-1] == b"":
        tokens.pop()
    excluded = tuple(excluded_prefixes)
    paths: list[str] = []
    tombstones: list[dict[str, str | None]] = []

    def is_excluded(path: str) -> bool:
        return path in excluded or any(path.startswith(prefix) for prefix in excluded)

    index = 0
    while index < len(tokens):
        try:
            status = tokens[index].decode("ascii")
            index += 1
            if index >= len(tokens):
                raise EvidenceSupportError(
                    "git diff --name-status returned an incomplete record"
                )
            source_path = _canonical_git_path(tokens[index].decode("utf-8"))
            index += 1
            target_path: str | None = None
            if status.startswith("R"):
                if index >= len(tokens):
                    raise EvidenceSupportError(
                        "git diff --name-status returned an incomplete rename record"
                    )
                target_path = _canonical_git_path(tokens[index].decode("utf-8"))
                index += 1
        except UnicodeDecodeError as exc:
            raise EvidenceSupportError(
                "implementation change contains an unsupported path encoding"
            ) from exc

        if status in {"A", "M"}:
            if not is_excluded(source_path):
                paths.append(source_path)
            continue
        if status == "D":
            if not is_excluded(source_path):
                tombstones.append(
                    {
                        "role": "implementation_tombstone",
                        "path": source_path,
                        "base_sha256": sha256_git_blob(
                            project_root, slice_base_commit, source_path
                        ),
                        "change": "deleted",
                        "target_path": None,
                    }
                )
            continue
        if status.startswith("R") and status[1:].isdigit() and target_path is not None:
            source_excluded = is_excluded(source_path)
            target_excluded = is_excluded(target_path)
            if not target_excluded:
                paths.append(target_path)
            if not source_excluded:
                tombstones.append(
                    {
                        "role": "implementation_tombstone",
                        "path": source_path,
                        "base_sha256": sha256_git_blob(
                            project_root, slice_base_commit, source_path
                        ),
                        "change": "renamed" if not target_excluded else "deleted",
                        "target_path": target_path if not target_excluded else None,
                    }
                )
            continue
        raise EvidenceSupportError(
            f"unsupported implementation change status {status!r}: {source_path}"
        )

    if len(paths) != len(set(paths)):
        raise EvidenceSupportError("implementation change set contains duplicate paths")
    tombstone_paths = [item["path"] for item in tombstones]
    if len(tombstone_paths) != len(set(tombstone_paths)):
        raise EvidenceSupportError(
            "implementation change tombstones contain duplicate paths"
        )
    if not paths and not tombstones:
        raise EvidenceSupportError("implementation change set is empty")
    return (
        tuple(sorted(paths)),
        tuple(sorted(tombstones, key=lambda item: str(item["path"]))),
    )


def implementation_change_tombstones(
    project_root: Path,
    slice_base_commit: str,
    implementation_commit: str,
    *,
    excluded_prefixes: Iterable[str] = (),
) -> list[dict[str, str | None]]:
    _, tombstones = _implementation_change_authority(
        project_root,
        slice_base_commit,
        implementation_commit,
        excluded_prefixes=excluded_prefixes,
    )
    return [dict(item) for item in tombstones]


def sha256_git_archive(project_root: Path, commit: str, path: str) -> str:
    """Hash a committed file exactly as the evidence collector fingerprints it.

    Historical manifests bind Git archive bytes produced with CRLF conversion
    for text files without an explicit ``eol=lf`` attribute.  Pin that archive
    policy so repository-local ``core.autocrlf`` cannot change verification.
    """

    raw = _run_git(
        project_root,
        (
            *_CANONICAL_ARCHIVE_CONFIG,
            "archive",
            "--format=tar",
            commit,
            "--",
            path,
        ),
    )
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as handle:
            member = handle.getmember(path)
            if not member.isfile() or member.issym() or member.islnk():
                raise EvidenceSupportError(
                    f"implementation path is not a regular Git file: {path}"
                )
            extracted = handle.extractfile(member)
            if extracted is None:
                raise EvidenceSupportError(
                    f"implementation Git file cannot be read: {path}"
                )
            return hashlib.sha256(extracted.read()).hexdigest()
    except tarfile.TarError as exc:
        raise EvidenceSupportError("Git implementation archive is invalid") from exc


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
        return []
    batches: list[list[str]] = []
    batch: list[str] = []
    argument_size = 0
    for path in paths:
        path_size = len(path.encode("utf-8")) + 3
        if batch and argument_size + path_size > 6000:
            batches.append(batch)
            batch = []
            argument_size = 0
        batch.append(path)
        argument_size += path_size
    if batch:
        batches.append(batch)

    fingerprints: dict[str, str] = {}
    for selected in batches:
        tree_raw = _run_git(
            project_root,
            ("ls-tree", "-z", implementation_commit, "--", *selected),
        )
        tree_entries: dict[str, tuple[str, str, str]] = {}
        for record in tree_raw.split(b"\0"):
            if not record:
                continue
            try:
                authority, raw_path = record.split(b"\t", 1)
                mode, object_type, object_id = authority.decode("ascii").split(" ")
                tree_path = _canonical_git_path(raw_path.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as exc:
                raise EvidenceSupportError(
                    "implementation Git tree returned an invalid record"
                ) from exc
            tree_entries[tree_path] = (mode, object_type, object_id)
        archive = _run_git(
            project_root,
            (
                *_CANONICAL_ARCHIVE_CONFIG,
                "archive",
                "--format=tar",
                implementation_commit,
                "--",
                *selected,
            ),
        )
        try:
            with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as handle:
                for path in selected:
                    entry = tree_entries.get(path)
                    if entry is None:
                        raise EvidenceSupportError(
                            f"implementation path is absent from its Git commit: {path}"
                        )
                    mode, object_type, object_id = entry
                    if mode == "160000" and object_type == "commit":
                        fingerprints[path] = hashlib.sha256(
                            f"gitlink {object_id}\n".encode("ascii")
                        ).hexdigest()
                        continue
                    if object_type != "blob":
                        raise EvidenceSupportError(
                            f"implementation path has unsupported Git type {object_type}: {path}"
                        )
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
                    fingerprints[path] = hashlib.sha256(extracted.read()).hexdigest()
        except tarfile.TarError as exc:
            raise EvidenceSupportError("Git implementation archive is invalid") from exc
    return [
        {
            "role": "implementation_artifact",
            "path": path,
            "sha256": fingerprints[path],
        }
        for path in paths
    ]
