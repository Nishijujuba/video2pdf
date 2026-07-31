"""Git-bound execution source inventory for the project test runner."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
from typing import Any, Iterable, Mapping, Sequence

from scripts.project_test_results import (
    ResultIntegrityError,
    canonical_json_bytes,
    file_artifact_identity,
    read_file_snapshot,
    write_bytes_exclusive,
)


SOURCE_MANIFEST_SCHEMA_NAME = "video2pdf.project-test-execution-source"
SOURCE_MANIFEST_SCHEMA_VERSION = 1
SOURCE_MANIFEST_RELATIVE_PATH = Path("execution-source.json")
ALLOWED_OUTPUT_PREFIXES = ("待删除/",)
FIXED_EXECUTION_SOURCE_PATHS = (
    "config/test-suites.v1.json",
    "schemas/project-test-promotion-report.v2.schema.json",
    "scripts/persisted_command.py",
    "scripts/project_test_discovery.py",
    "scripts/project_test_external_root.py",
    "scripts/project_test_registry.py",
    "scripts/project_test_results.py",
    "scripts/project_test_run_identity.py",
    "scripts/project_test_scheduler.py",
    "scripts/project_test_source_provenance.py",
    "scripts/run_project_tests.py",
    "scripts/validate_project_test_promotion.py",
    "src/video2pdf_persisted_command/cli.py",
    "src/video2pdf_persisted_command/process_identity.py",
)
PROMOTION_AUTHORITY_SOURCE_PATHS = tuple(
    sorted(
        {
            *FIXED_EXECUTION_SOURCE_PATHS,
            "scripts/generate_project_test_promotion_v2_authority.py",
            "src/video2pdf_workflow_kernel/contracts.py",
            "src/video2pdf_workflow_kernel/control_store.py",
            "tests/video_workflow/test_contract_registry_cache.py",
            "tests/video_workflow/test_control_store_v9_fastpath.py",
        }
    )
)
EXECUTION_SOURCE_ROOTS = (
    ".agents",
    ".claude",
    ".codex",
    "agent_reports",
    "config",
    "delivery-quality",
    "docs",
    "evidence",
    "move-repro-ascii",
    "prompts",
    "requirements",
    "schemas",
    "scripts",
    "src",
    "tests",
    "AGENTS.md",
    "CLAUDE.md",
    "CONTEXT-MAP.md",
    ".gitattributes",
    ".gitignore",
    ".gitignore.txt",
    ".gitmodules",
    "pyproject.toml",
    "uv.lock",
)
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_GIT_OBJECT = re.compile(r"[0-9a-f]{40,64}\Z")


class SourceProvenanceError(RuntimeError):
    """Execution bytes cannot be bound to one clean Git tree."""


def _git(
    repo_root: Path,
    arguments: Sequence[str],
    *,
    text: bool = False,
    input_bytes: bytes | None = None,
) -> subprocess.CompletedProcess[Any]:
    environment = os.environ.copy()
    for name in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    ):
        environment.pop(name, None)
    try:
        return subprocess.run(
            ["git", *arguments],
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=text,
            encoding="utf-8" if text else None,
            input=(
                input_bytes.decode("utf-8")
                if text and input_bytes is not None
                else input_bytes
            ),
            env=environment,
        )
    except OSError as error:
        raise SourceProvenanceError("git executable is unavailable") from error


def _canonical_relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or path.is_absolute()
        or ".." in path.parts
        or path.as_posix() != value
    ):
        raise SourceProvenanceError(
            f"execution source path is not canonical: {value!r}"
        )
    return value


def assert_clean_execution_worktree(
    repo_root: Path,
    *,
    allowed_output_prefixes: Sequence[str] = ALLOWED_OUTPUT_PREFIXES,
) -> None:
    """Reject tracked drift and paths outside explicit output boundaries."""

    completed = _git(
        repo_root,
        ["status", "--porcelain=v1", "-z", "--untracked-files=all"],
    )
    if completed.returncode != 0:
        raise SourceProvenanceError("cannot inspect Git worktree cleanliness")
    allowed = tuple(
        _canonical_relative_path(prefix.rstrip("/")) + "/"
        for prefix in allowed_output_prefixes
    )
    dirty: list[str] = []
    records = completed.stdout.split(b"\0")
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        decoded = os.fsdecode(record)
        if len(decoded) < 4:
            raise SourceProvenanceError("Git returned malformed status output")
        status = decoded[:2]
        path = decoded[3:].replace("\\", "/")
        changed_paths = [path]
        if any(code in {"R", "C"} for code in status):
            if index >= len(records) or not records[index]:
                raise SourceProvenanceError(
                    "Git returned malformed rename/copy status output"
                )
            changed_paths.append(
                os.fsdecode(records[index]).replace("\\", "/")
            )
            index += 1
        if all(
            any(candidate.startswith(prefix) for prefix in allowed)
            for candidate in changed_paths
        ):
            continue
        dirty.append(f"{status} {' -> '.join(changed_paths)}")
    if dirty:
        raise SourceProvenanceError(
            "project test execution requires a clean Git worktree: "
            + ", ".join(sorted(dirty)[:10])
        )


def _commit_and_tree(repo_root: Path) -> tuple[str, str]:
    commit_result = _git(repo_root, ["rev-parse", "HEAD"], text=True)
    tree_result = _git(repo_root, ["rev-parse", "HEAD^{tree}"], text=True)
    commit = commit_result.stdout.strip()
    tree = tree_result.stdout.strip()
    if (
        commit_result.returncode != 0
        or tree_result.returncode != 0
        or _COMMIT.fullmatch(commit) is None
        or _GIT_OBJECT.fullmatch(tree) is None
    ):
        raise SourceProvenanceError("cannot resolve Git commit and tree")
    return commit, tree


def execution_source_paths(
    repo_root: Path,
    commit: str,
    test_module_paths: Iterable[str],
) -> tuple[str, ...]:
    tracked = _git(
        repo_root,
        [
            "ls-tree",
            "-r",
            "-z",
            "--name-only",
            commit,
            "--",
            *EXECUTION_SOURCE_ROOTS,
        ],
    )
    if tracked.returncode != 0:
        raise SourceProvenanceError(
            "cannot enumerate committed execution sources"
        )
    runtime_paths = {
        os.fsdecode(record)
        for record in tracked.stdout.split(b"\0")
        if record
    }
    paths = {
        _canonical_relative_path(path)
        for path in (
            *FIXED_EXECUTION_SOURCE_PATHS,
            *runtime_paths,
            *test_module_paths,
        )
    }
    return tuple(sorted(paths))


def planned_execution_source_paths(
    repo_root: Path,
    test_module_paths: Iterable[str],
) -> tuple[str, ...]:
    """Enumerate the HEAD-bound frozen paths before creating a run."""

    commit, _tree = _commit_and_tree(repo_root.resolve(strict=True))
    return execution_source_paths(repo_root, commit, test_module_paths)


def _commit_blob(
    repo_root: Path,
    commit: str,
    relative_path: str,
) -> tuple[str, bytes]:
    object_result = _git(
        repo_root,
        ["rev-parse", f"{commit}:{relative_path}"],
        text=True,
    )
    object_id = object_result.stdout.strip()
    if object_result.returncode != 0 or _GIT_OBJECT.fullmatch(object_id) is None:
        raise SourceProvenanceError(
            f"execution source is absent from commit: {relative_path}"
        )
    blob_result = _git(repo_root, ["cat-file", "blob", object_id])
    if blob_result.returncode != 0:
        raise SourceProvenanceError(
            f"cannot read committed execution source: {relative_path}"
        )
    return object_id, blob_result.stdout


def committed_source_fingerprints(
    repo_root: Path,
    commit: str,
    relative_paths: Iterable[str],
) -> dict[str, str]:
    """Return SHA-256 identities for canonical source blobs at one commit."""

    if _COMMIT.fullmatch(commit) is None:
        raise SourceProvenanceError("source authority commit is invalid")
    fingerprints: dict[str, str] = {}
    for relative_path in sorted(relative_paths):
        canonical_path = _canonical_relative_path(relative_path)
        if canonical_path in fingerprints:
            raise SourceProvenanceError(
                f"duplicate source authority path: {canonical_path}"
            )
        _object_id, content = _commit_blob(
            repo_root.resolve(strict=True),
            commit,
            canonical_path,
        )
        fingerprints[canonical_path] = hashlib.sha256(content).hexdigest()
    return fingerprints


def _filtered_blob_identity(
    repo_root: Path,
    relative_path: str,
    content: bytes,
) -> str:
    result = _git(
        repo_root,
        ["hash-object", "--path", relative_path, "--stdin"],
        input_bytes=content,
    )
    value = os.fsdecode(result.stdout).strip()
    if result.returncode != 0 or _GIT_OBJECT.fullmatch(value) is None:
        raise SourceProvenanceError(
            f"cannot normalize execution source: {relative_path}"
        )
    return value


def build_execution_source_manifest(
    repo_root: Path,
    test_module_paths: Iterable[str],
) -> dict[str, Any]:
    """Bind every executable contract byte to HEAD and its Git tree."""

    repo_root = repo_root.resolve(strict=True)
    assert_clean_execution_worktree(repo_root)
    commit, tree = _commit_and_tree(repo_root)
    entries = []
    for relative_path in execution_source_paths(
        repo_root,
        commit,
        test_module_paths,
    ):
        object_id, committed = _commit_blob(repo_root, commit, relative_path)
        try:
            _, working, _ = read_file_snapshot(repo_root / relative_path)
        except ResultIntegrityError as error:
            raise SourceProvenanceError(
                f"execution source is unreadable: {relative_path}"
            ) from error
        if _filtered_blob_identity(repo_root, relative_path, working) != object_id:
            raise SourceProvenanceError(
                f"dirty execution source differs from commit: {relative_path}"
            )
        entries.append(
            {
                "path": relative_path,
                "frozen_path": f"execution-source-files/{relative_path}",
                "git_blob": object_id,
                "committed_sha256": hashlib.sha256(committed).hexdigest(),
                "runtime_sha256": hashlib.sha256(working).hexdigest(),
                "runtime_size": len(working),
                "frozen_file_identity": None,
            }
        )
    return {
        "schema_name": SOURCE_MANIFEST_SCHEMA_NAME,
        "schema_version": SOURCE_MANIFEST_SCHEMA_VERSION,
        "commit": commit,
        "git_tree": tree,
        "git_authority": None,
        "entries": entries,
    }


def freeze_execution_source_files(
    repo_root: Path,
    run_dir: Path,
    manifest: Mapping[str, Any],
) -> None:
    """Persist exact execution-time bytes below the owned external run."""

    repo_root = repo_root.resolve(strict=True)
    for item in manifest.get("entries", []):
        if not isinstance(item, dict):
            raise SourceProvenanceError(
                "execution source inventory entry is invalid"
            )
        relative_path = _canonical_relative_path(item["path"])
        frozen_path = _canonical_relative_path(item["frozen_path"])
        try:
            _, content, _ = read_file_snapshot(repo_root / relative_path)
        except ResultIntegrityError as error:
            raise SourceProvenanceError(
                f"execution source is unreadable: {relative_path}"
            ) from error
        if (
            len(content) != item["runtime_size"]
            or hashlib.sha256(content).hexdigest()
            != item["runtime_sha256"]
        ):
            raise SourceProvenanceError(
                f"execution source drifted before freeze: {relative_path}"
            )
        destination = run_dir / frozen_path
        write_bytes_exclusive(destination, content)
        item["frozen_file_identity"] = file_artifact_identity(destination)


def _git_authority_value(
    git_authority_dir: Path,
    arguments: Sequence[str],
) -> str:
    completed = _git(
        git_authority_dir.parent,
        ["--git-dir", str(git_authority_dir), *arguments],
        text=True,
    )
    value = completed.stdout.strip()
    if completed.returncode != 0 or not value:
        raise SourceProvenanceError(
            "frozen Git authority is unavailable"
        )
    return value


def _git_config_entries(config_path: Path) -> dict[str, str]:
    completed = _git(
        config_path.parent,
        [
            "config",
            "--file",
            str(config_path),
            "--null",
            "--list",
        ],
    )
    if completed.returncode != 0:
        raise SourceProvenanceError(
            "frozen Git authority config is unreadable"
        )
    entries: dict[str, str] = {}
    try:
        records = [
            record
            for record in completed.stdout.split(b"\0")
            if record
        ]
        for record in records:
            key_raw, separator, value_raw = record.partition(b"\n")
            key = key_raw.decode("utf-8")
            value = value_raw.decode("utf-8")
            if not separator or key in entries:
                raise ValueError("duplicate or valueless config")
            entries[key] = value
    except (UnicodeError, ValueError) as error:
        raise SourceProvenanceError(
            "frozen Git authority config shape is invalid"
        ) from error
    return entries


def _validate_frozen_git_config_entries(
    entries: Mapping[str, str],
    *,
    repo_root: Path,
    execution_root: Path,
) -> None:
    """Enforce the exact seven-key frozen Git authority configuration."""

    expected = {
        "core.repositoryformatversion": "0",
        "core.filemode": "false",
        "core.bare": "false",
        "core.symlinks": "false",
        "core.ignorecase": "true",
        "core.worktree": str(execution_root.resolve(strict=True)),
        "remote.origin.url": str(repo_root.resolve(strict=True)),
    }
    if set(entries) != set(expected):
        raise SourceProvenanceError(
            "frozen Git authority config key set is invalid"
        )
    for key, expected_value in expected.items():
        value = entries[key]
        if key in {"core.worktree", "remote.origin.url"}:
            try:
                matches = (
                    Path(value).resolve(strict=True)
                    == Path(expected_value).resolve(strict=True)
                )
            except OSError:
                matches = False
        else:
            matches = value == expected_value
        if not matches:
            raise SourceProvenanceError(
                f"frozen Git authority config value is invalid: {key}"
            )


def create_frozen_git_authority(
    repo_root: Path,
    run_dir: Path,
    execution_root: Path,
    git_dir: Path,
    manifest: dict[str, Any],
) -> None:
    """Create and bind an independent-ref, shared-object Git authority."""

    try:
        resolved_git_dir = git_dir.resolve(strict=True)
    except OSError as error:
        raise SourceProvenanceError(
            "repository Git directory is unavailable"
        ) from error
    if not resolved_git_dir.is_dir():
        raise SourceProvenanceError(
            "repository Git directory is unavailable"
        )
    authority_dir = run_dir / "execution-git"
    completed = _git(
        repo_root,
        [
            "clone",
            "--shared",
            "--bare",
            "--quiet",
            str(repo_root),
            str(authority_dir),
        ],
    )
    if completed.returncode != 0:
        raise SourceProvenanceError(
            "cannot create frozen Git authority"
        )
    commit = manifest["commit"]
    for arguments in (
        ("update-ref", "refs/heads/execution-source", commit),
        ("symbolic-ref", "HEAD", "refs/heads/execution-source"),
        ("config", "core.bare", "false"),
        ("config", "core.worktree", str(execution_root.resolve(strict=True))),
    ):
        completed = _git(
            repo_root,
            ["--git-dir", str(authority_dir), *arguments],
        )
        if completed.returncode != 0:
            raise SourceProvenanceError(
                "cannot pin frozen Git authority"
            )
    git_link_path = execution_root / ".git"
    git_link_content = (
        f"gitdir: {authority_dir.resolve(strict=True).as_posix()}\n"
    ).encode("utf-8")
    write_bytes_exclusive(git_link_path, git_link_content)
    alternates_path = authority_dir / "objects" / "info" / "alternates"
    config_path = authority_dir / "config"
    try:
        _, alternates_content, alternates_identity = read_file_snapshot(
            alternates_path
        )
        _, config_content, config_identity = read_file_snapshot(config_path)
        _, observed_link, link_identity = read_file_snapshot(git_link_path)
    except ResultIntegrityError as error:
        raise SourceProvenanceError(
            "frozen Git authority identity is unavailable"
        ) from error
    if observed_link != git_link_content:
        raise SourceProvenanceError(
            "frozen Git link changed during creation"
        )
    head_commit = _git_authority_value(
        authority_dir,
        ["rev-parse", "HEAD"],
    )
    head_tree = _git_authority_value(
        authority_dir,
        ["rev-parse", "HEAD^{tree}"],
    )
    if head_commit != commit or head_tree != manifest["git_tree"]:
        raise SourceProvenanceError(
            "frozen Git authority does not match execution source"
        )
    manifest["git_authority"] = {
        "authority_path": "execution-git",
        "git_link_path": "execution-source-files/.git",
        "git_link_sha256": hashlib.sha256(git_link_content).hexdigest(),
        "git_link_file_identity": link_identity,
        "alternates_path": "execution-git/objects/info/alternates",
        "alternates_sha256": hashlib.sha256(alternates_content).hexdigest(),
        "alternates_file_identity": alternates_identity,
        "config_path": "execution-git/config",
        "config_sha256": hashlib.sha256(config_content).hexdigest(),
        "config_file_identity": config_identity,
        "head_commit": head_commit,
        "head_tree": head_tree,
        "source_git_dir": str(resolved_git_dir),
    }


def validate_execution_source_manifest(
    repo_root: Path,
    manifest: Mapping[str, Any],
    *,
    expected_test_module_paths: Iterable[str],
    require_worktree_match: bool,
    frozen_run_dir: Path | None = None,
) -> str:
    """Recompute every manifest entry from the declared commit tree."""

    repo_root = repo_root.resolve(strict=True)
    if set(manifest) != {
        "schema_name",
        "schema_version",
        "commit",
        "git_tree",
        "git_authority",
        "entries",
    }:
        raise SourceProvenanceError(
            "execution source manifest has missing or unknown fields"
        )
    commit = manifest.get("commit")
    tree = manifest.get("git_tree")
    entries = manifest.get("entries")
    git_authority = manifest.get("git_authority")
    if (
        manifest.get("schema_name") != SOURCE_MANIFEST_SCHEMA_NAME
        or manifest.get("schema_version") != SOURCE_MANIFEST_SCHEMA_VERSION
        or not isinstance(commit, str)
        or _COMMIT.fullmatch(commit) is None
        or not isinstance(tree, str)
        or _GIT_OBJECT.fullmatch(tree) is None
        or not isinstance(entries, list)
        or not isinstance(git_authority, dict)
    ):
        raise SourceProvenanceError("execution source manifest identity is invalid")
    tree_result = _git(repo_root, ["rev-parse", f"{commit}^{{tree}}"], text=True)
    if tree_result.returncode != 0 or tree_result.stdout.strip() != tree:
        raise SourceProvenanceError(
            "execution source manifest Git tree does not match commit"
        )
    git_dir_result = _git(
        repo_root,
        ["rev-parse", "--absolute-git-dir"],
        text=True,
    )
    if set(git_authority) != {
        "authority_path",
        "git_link_path",
        "git_link_sha256",
        "git_link_file_identity",
        "alternates_path",
        "alternates_sha256",
        "alternates_file_identity",
        "config_path",
        "config_sha256",
        "config_file_identity",
        "head_commit",
        "head_tree",
        "source_git_dir",
    }:
        raise SourceProvenanceError(
            "frozen Git authority manifest is invalid"
        )
    if any(
        not isinstance(git_authority.get(field), str)
        or not git_authority[field]
        for field in (
            "authority_path",
            "git_link_path",
            "alternates_path",
            "config_path",
            "head_commit",
            "head_tree",
            "source_git_dir",
        )
    ):
        raise SourceProvenanceError(
            "frozen Git authority binding is invalid"
        )
    authority_path = _canonical_relative_path(
        git_authority["authority_path"]
    )
    git_link_path = _canonical_relative_path(
        git_authority["git_link_path"]
    )
    alternates_path = _canonical_relative_path(
        git_authority["alternates_path"]
    )
    config_path = _canonical_relative_path(
        git_authority["config_path"]
    )
    if (
        authority_path != "execution-git"
        or git_link_path != "execution-source-files/.git"
        or alternates_path
        != "execution-git/objects/info/alternates"
        or config_path != "execution-git/config"
        or git_authority["head_commit"] != commit
        or git_authority["head_tree"] != tree
        or any(
            not isinstance(git_authority.get(field), str)
            or _SHA256.fullmatch(git_authority[field]) is None
            for field in (
                "git_link_sha256",
                "alternates_sha256",
                "config_sha256",
            )
        )
        or not isinstance(git_authority.get("source_git_dir"), str)
        or not git_authority["source_git_dir"]
        or git_dir_result.returncode != 0
        or os.path.normcase(
            os.path.abspath(git_authority["source_git_dir"])
        )
        != os.path.normcase(git_dir_result.stdout.strip())
    ):
        raise SourceProvenanceError(
            "frozen Git authority binding is invalid"
        )
    for field in (
        "git_link_file_identity",
        "alternates_file_identity",
        "config_file_identity",
    ):
        identity = git_authority.get(field)
        if (
            not isinstance(identity, dict)
            or set(identity)
            != {"device", "inode", "size", "mtime_ns", "ctime_ns"}
            or any(type(value) is not int or value < 0 for value in identity.values())
        ):
            raise SourceProvenanceError(
                "frozen Git authority file identity is invalid"
            )
    if frozen_run_dir is not None:
        authority_dir = frozen_run_dir / authority_path
        execution_root = frozen_run_dir / "execution-source-files"
        try:
            _, link_content, link_identity = read_file_snapshot(
                frozen_run_dir / git_link_path
            )
            _, alternates_content, alternates_identity = read_file_snapshot(
                frozen_run_dir / alternates_path
            )
            _, config_content, config_identity = read_file_snapshot(
                frozen_run_dir / config_path
            )
        except ResultIntegrityError as error:
            raise SourceProvenanceError(
                "frozen Git authority is unreadable"
            ) from error
        expected_link_content = (
            f"gitdir: {authority_dir.resolve(strict=True).as_posix()}\n"
        ).encode("utf-8")
        config_entries = _git_config_entries(
            frozen_run_dir / config_path
        )
        _validate_frozen_git_config_entries(
            config_entries,
            repo_root=repo_root,
            execution_root=execution_root,
        )
        common_dir_result = _git(
            repo_root,
            ["rev-parse", "--git-common-dir"],
            text=True,
        )
        common_dir = Path(common_dir_result.stdout.strip())
        if not common_dir.is_absolute():
            common_dir = repo_root / common_dir
        try:
            expected_object_dir = (
                common_dir.resolve(strict=True) / "objects"
            ).resolve(strict=True)
            alternates_lines = [
                line
                for line in alternates_content.decode("utf-8").splitlines()
                if line
            ]
            observed_object_dir = Path(alternates_lines[0]).resolve(
                strict=True
            )
            remote_origin = Path(
                config_entries["remote.origin.url"]
            ).resolve(strict=True)
        except (OSError, UnicodeError, IndexError, KeyError) as error:
            raise SourceProvenanceError(
                "frozen Git object authority is invalid"
            ) from error
        if (
            link_content != expected_link_content
            or hashlib.sha256(link_content).hexdigest()
            != git_authority["git_link_sha256"]
            or link_identity != git_authority["git_link_file_identity"]
            or hashlib.sha256(alternates_content).hexdigest()
            != git_authority["alternates_sha256"]
            or alternates_identity
            != git_authority["alternates_file_identity"]
            or hashlib.sha256(config_content).hexdigest()
            != git_authority["config_sha256"]
            or config_identity != git_authority["config_file_identity"]
            or len(alternates_lines) != 1
            or observed_object_dir != expected_object_dir
            or common_dir_result.returncode != 0
            or remote_origin != repo_root
            or _git_authority_value(
                authority_dir,
                ["config", "--get", "core.bare"],
            )
            != "false"
            or os.path.normcase(
                os.path.abspath(
                    _git_authority_value(
                        authority_dir,
                        ["config", "--get", "core.worktree"],
                    )
                )
            )
            != os.path.normcase(str(execution_root.resolve(strict=True)))
            or _git_authority_value(authority_dir, ["rev-parse", "HEAD"])
            != commit
            or _git_authority_value(
                authority_dir,
                ["rev-parse", "HEAD^{tree}"],
            )
            != tree
        ):
            raise SourceProvenanceError(
                "frozen Git authority differs from execution source"
            )
    expected_paths = execution_source_paths(
        repo_root,
        commit,
        expected_test_module_paths,
    )
    if [
        item.get("path") for item in entries if isinstance(item, dict)
    ] != list(expected_paths):
        raise SourceProvenanceError(
            "execution source inventory is incomplete or reordered"
        )
    for item in entries:
        if (
            not isinstance(item, dict)
            or set(item)
            != {
                "path",
                "frozen_path",
                "git_blob",
                "committed_sha256",
                "runtime_sha256",
                "runtime_size",
                "frozen_file_identity",
            }
            or not isinstance(item.get("path"), str)
            or not isinstance(item.get("frozen_path"), str)
            or not isinstance(item.get("git_blob"), str)
            or _GIT_OBJECT.fullmatch(item["git_blob"]) is None
            or not isinstance(item.get("committed_sha256"), str)
            or _SHA256.fullmatch(item["committed_sha256"]) is None
            or not isinstance(item.get("runtime_sha256"), str)
            or _SHA256.fullmatch(item["runtime_sha256"]) is None
            or type(item.get("runtime_size")) is not int
            or item["runtime_size"] < 0
            or not isinstance(item.get("frozen_file_identity"), dict)
            or set(item["frozen_file_identity"])
            != {"device", "inode", "size", "mtime_ns", "ctime_ns"}
            or any(
                type(value) is not int or value < 0
                for value in item["frozen_file_identity"].values()
            )
        ):
            raise SourceProvenanceError(
                "execution source inventory entry is invalid"
            )
        object_id, committed = _commit_blob(repo_root, commit, item["path"])
        if (
            object_id != item["git_blob"]
            or hashlib.sha256(committed).hexdigest()
            != item["committed_sha256"]
        ):
            raise SourceProvenanceError(
                f"execution source differs from commit tree: {item['path']}"
            )
        if require_worktree_match:
            try:
                _, working, _ = read_file_snapshot(
                    repo_root / item["path"]
                )
            except ResultIntegrityError as error:
                raise SourceProvenanceError(
                    f"execution source is unreadable: {item['path']}"
                ) from error
            if (
                _filtered_blob_identity(
                    repo_root,
                    item["path"],
                    working,
                )
                != object_id
                or len(working) != item["runtime_size"]
                or hashlib.sha256(working).hexdigest()
                != item["runtime_sha256"]
            ):
                raise SourceProvenanceError(
                    f"dirty execution source differs from commit: {item['path']}"
                )
        if frozen_run_dir is not None:
            frozen_path = _canonical_relative_path(item["frozen_path"])
            if frozen_path != f"execution-source-files/{item['path']}":
                raise SourceProvenanceError(
                    "frozen execution source path is invalid"
                )
            try:
                frozen_artifact_path = frozen_run_dir / frozen_path
                _, frozen, frozen_identity = read_file_snapshot(
                    frozen_artifact_path
                )
            except ResultIntegrityError as error:
                raise SourceProvenanceError(
                    f"frozen execution source is unreadable: {item['path']}"
                ) from error
            if (
                len(frozen) != item["runtime_size"]
                or hashlib.sha256(frozen).hexdigest()
                != item["runtime_sha256"]
                or _filtered_blob_identity(
                    repo_root,
                    item["path"],
                    frozen,
                )
                != object_id
                or frozen_identity != item["frozen_file_identity"]
            ):
                raise SourceProvenanceError(
                    f"frozen execution source is not committed: {item['path']}"
                )
    return hashlib.sha256(canonical_json_bytes(dict(manifest))).hexdigest()
