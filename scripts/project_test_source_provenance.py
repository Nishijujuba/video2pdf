"""Git-bound execution source inventory for the project test runner."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import threading
from typing import Any, Iterable, Mapping, Sequence

from scripts.project_test_results import (
    ResultIntegrityError,
    canonical_json_bytes,
    file_artifact_identity,
    read_file_snapshot,
    write_bytes_exclusive,
)
from src.video2pdf_persisted_command.process_identity import (
    execution_identity_is_complete,
    process_execution_identity,
)


SOURCE_MANIFEST_SCHEMA_NAME = "video2pdf.project-test-execution-source"
SOURCE_MANIFEST_SCHEMA_VERSION = 1
SOURCE_MANIFEST_RELATIVE_PATH = Path("execution-source.json")
SOURCE_SNAPSHOT_SCHEMA_NAME = "video2pdf.project-test-source-snapshot"
SOURCE_SNAPSHOT_SCHEMA_VERSION = 1
SOURCE_SNAPSHOT_RELATIVE_PATH = Path("source-snapshot.json")
RUN_FINALIZATION_SCHEMA_NAME = "video2pdf.project-test-run-finalization"
RUN_FINALIZATION_SCHEMA_VERSION = 1
RUN_FINALIZATION_RELATIVE_PATH = Path("run-finalization.json")
TEST_RUN_SCHEMA_NAME = "video2pdf.project-test-run"
TEST_RUN_SCHEMA_VERSION = 2
TEST_RUN_V1_FIELDS = frozenset(
    {
        "schema_name",
        "schema_version",
        "command",
        "project",
        "commit",
        "registry_sha256",
        "discovery_sha256",
        "source_manifest_path",
        "source_manifest_sha256",
        "suite_ids",
        "run_dir",
        "project_marker_sha256",
        "persisted_run_id",
        "persisted_run_nonce",
        "persisted_target_identity",
        "persisted_supervisor_identity",
        "requested_jobs",
        "timings_from",
        "runner_identity",
        "discovery_process",
    }
)
TEST_RUN_V2_FIELDS = TEST_RUN_V1_FIELDS | frozenset(
    {
        "source_snapshot_path",
        "source_snapshot_id",
        "source_snapshot_sha256",
    }
)
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
PROMOTION_EVIDENCE_ONLY_PATHS = frozenset(
    {
        "evidence/project-test-runner/optimization-safety-review.v1.json",
        "evidence/project-test-runner/promotion-report.json",
        "evidence/project-test-runner/promotion-superset-authority.v2.json",
    }
)
PROMOTION_AUTHORITY_ARTIFACT_PATHS = (
    "evidence/project-test-runner/optimization-safety-review.v1.json",
    "evidence/project-test-runner/promotion-superset-authority.v2.json",
)
PROMOTION_AUTHORITY_TRANSACTION_MARKER_RELATIVE_PATH = Path(
    "evidence/project-test-runner/.promotion-authority-transaction.json"
)
_WORKER_AUTHORITY_CACHE: dict[
    tuple[Any, ...],
    tuple[str, str],
] = {}
_WORKER_AUTHORITY_CACHE_LOCK = threading.Lock()
_AUTHORITY_ASSIGNMENT_FIELDS = (
    "repo_root",
    "execution_root",
    "module_key",
    "suite_id",
    "source_path",
    "test_ids",
    "source_manifest_sha256",
    "source_snapshot_id",
    "source_snapshot_sha256",
    "module_inventory",
    "module_inventory_sha256",
    "source_sha256",
)
EXECUTION_SOURCE_ROOTS = (
    ".agents",
    ".claude",
    ".codex",
    "agent_reports",
    "config",
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


def assert_no_incomplete_promotion_authority_transaction(
    repo_root: Path,
) -> None:
    """Fail closed while the canonical Promotion authority set is in flight."""

    marker = repo_root / PROMOTION_AUTHORITY_TRANSACTION_MARKER_RELATIVE_PATH
    if os.path.lexists(marker):
        raise SourceProvenanceError(
            "incomplete Promotion authority materialization transaction exists"
        )


def require_live_authority_artifacts_match_commit(
    repo_root: Path,
    commit: str,
) -> dict[str, str]:
    """Require both live authority artifacts to equal their Git blobs at E."""

    assert_no_incomplete_promotion_authority_transaction(repo_root)
    fingerprints: dict[str, str] = {}
    for relative_path in PROMOTION_AUTHORITY_ARTIFACT_PATHS:
        completed = _git(
            repo_root,
            ["show", f"{commit}:{relative_path}"],
            text=False,
        )
        if completed.returncode != 0:
            raise SourceProvenanceError(
                "execution evidence commit Promotion authority artifact is "
                f"missing: {relative_path}"
            )
        try:
            live_bytes = (repo_root / relative_path).read_bytes()
        except OSError as error:
            raise SourceProvenanceError(
                f"live Promotion authority artifact is unreadable: {relative_path}"
            ) from error
        if live_bytes != completed.stdout:
            raise SourceProvenanceError(
                "execution evidence commit authority artifact differs: live "
                "Promotion authority artifacts differ from execution evidence "
                f"commit: {relative_path}"
            )
        fingerprints[relative_path] = hashlib.sha256(live_bytes).hexdigest()
    return fingerprints


def _module_inventory_entry(module: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "module_key": module["module_key"],
        "suite_id": module["suite_id"],
        "source_path": module["source_path"],
        "test_count": module["test_count"],
        "test_ids_sha256": hashlib.sha256(
            canonical_json_bytes(sorted(module["test_ids"]))
        ).hexdigest(),
    }


def module_inventory(
    modules: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Return the canonical discovered-module membership inventory."""

    return tuple(
        _module_inventory_entry(item)
        for item in sorted(
            modules,
            key=lambda value: (
                str(value["suite_id"]),
                str(value["source_path"]),
            ),
        )
    )


@dataclass(frozen=True)
class SourceBinding:
    """Supervisor-owned source authority passed intact into scheduling."""

    execution_root: Path
    source_manifest_sha256: str
    source_snapshot_id: str
    source_snapshot_sha256: str
    module_inventory: tuple[dict[str, Any], ...]
    source_sha256_by_path: Mapping[str, str]

    @property
    def module_inventory_sha256(self) -> str:
        return hashlib.sha256(
            canonical_json_bytes(list(self.module_inventory))
        ).hexdigest()

    def assignment_fields(
        self,
        module: Mapping[str, Any],
    ) -> dict[str, Any]:
        member = _module_inventory_entry(module)
        if member not in self.module_inventory:
            raise SourceProvenanceError(
                "scheduler module is absent from frozen module inventory"
            )
        source_sha256 = self.source_sha256_by_path.get(
            str(module["source_path"])
        )
        if source_sha256 is None:
            raise SourceProvenanceError(
                "scheduler module source fingerprint is missing"
            )
        return {
            "execution_root": str(self.execution_root),
            "source_manifest_sha256": self.source_manifest_sha256,
            "source_snapshot_id": self.source_snapshot_id,
            "source_snapshot_sha256": self.source_snapshot_sha256,
            "module_inventory_sha256": self.module_inventory_sha256,
            "module_inventory": list(self.module_inventory),
            "source_sha256": source_sha256,
        }


def validate_evidence_only_commit_range(
    repo_root: Path,
    ancestor: str,
    descendant: str,
    *,
    label: str,
) -> None:
    """Reject any non-evidence path touched by any commit in a range."""

    ancestry = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=repo_root,
        check=False,
        capture_output=True,
    )
    if ancestry.returncode != 0:
        raise SourceProvenanceError(
            f"{label} must be an ancestor/equal commit relation"
        )
    commits = subprocess.run(
        ["git", "rev-list", "--reverse", f"{ancestor}..{descendant}"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if commits.returncode != 0:
        raise SourceProvenanceError(
            f"{label} evidence-only commit range cannot be inspected"
        )
    unexpected: set[str] = set()
    for commit in commits.stdout.splitlines():
        changed = subprocess.run(
            [
                "git",
                "diff-tree",
                "--root",
                "-m",
                "--no-commit-id",
                "--name-only",
                "-r",
                "-z",
                commit,
            ],
            cwd=repo_root,
            check=False,
            capture_output=True,
        )
        if changed.returncode != 0:
            raise SourceProvenanceError(
                f"{label} evidence-only commit cannot be inspected: {commit}"
            )
        unexpected.update(
            os.fsdecode(path).replace("\\", "/")
            for path in changed.stdout.split(b"\0")
            if path
            and os.fsdecode(path).replace("\\", "/")
            not in PROMOTION_EVIDENCE_ONLY_PATHS
        )
    if unexpected:
        raise SourceProvenanceError(
            f"{label} contains non-evidence paths: "
            + ", ".join(sorted(unexpected))
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


def create_synthetic_source_artifacts(
    repo_root: Path,
    owned_project_root: Path,
    run_dir: Path,
    *,
    registry_sha256: str,
    modules: Sequence[Mapping[str, Any]],
) -> tuple[Path, str, Path, dict[str, Any], str]:
    """Publish a small source set through the production authority chain."""

    source_paths = ("config/test-suites.v1.json",)
    repo_root = repo_root.resolve(strict=True)
    commit, tree = _commit_and_tree(repo_root)
    source_manifest = _build_execution_source_manifest_for_paths(
        repo_root,
        commit,
        tree,
        source_paths,
    )
    freeze_execution_source_files(repo_root, run_dir, source_manifest)
    execution_root = run_dir / "execution-source-files"
    git_dir_result = _git(
        repo_root,
        ["rev-parse", "--absolute-git-dir"],
        text=True,
    )
    if git_dir_result.returncode != 0:
        raise SourceProvenanceError(
            "repository Git directory is unavailable"
        )
    create_frozen_git_authority(
        repo_root,
        run_dir,
        execution_root,
        Path(git_dir_result.stdout.strip()),
        source_manifest,
    )
    source_manifest_path = run_dir / SOURCE_MANIFEST_RELATIVE_PATH
    source_manifest_sha256 = write_bytes_exclusive(
        source_manifest_path,
        canonical_json_bytes(source_manifest),
    )
    runner_identity = process_execution_identity(os.getpid())
    if not execution_identity_is_complete(runner_identity):
        raise SourceProvenanceError(
            "synthetic source snapshot runner identity is unavailable"
        )
    try:
        project_marker_sha256 = hashlib.sha256(
            (owned_project_root / "project.json").read_bytes()
        ).hexdigest()
    except OSError as error:
        raise SourceProvenanceError(
            "synthetic source project marker is unreadable"
        ) from error
    source_snapshot, source_snapshot_sha256 = (
        _create_source_snapshot_for_paths(
            repo_root,
            run_dir,
            execution_root,
            source_manifest_path=source_manifest_path,
            source_manifest_sha256=source_manifest_sha256,
            source_manifest=source_manifest,
            expected_test_module_paths=(),
            project={
                "project_key": "video2pdf",
                "repository": "Nishijujuba/video2pdf",
            },
            registry_sha256=registry_sha256,
            project_marker_sha256=project_marker_sha256,
            persisted_run_id=None,
            persisted_run_nonce=None,
            runner_identity=runner_identity,
            modules=modules,
            expected_source_paths=source_paths,
        )
    )
    source_snapshot_path = run_dir / SOURCE_SNAPSHOT_RELATIVE_PATH
    validated_sha256 = _validate_execution_source_manifest_for_paths(
        repo_root,
        source_manifest,
        require_worktree_match=True,
        frozen_run_dir=run_dir,
        expected_source_paths=source_paths,
    )
    if validated_sha256 != source_manifest_sha256:
        raise SourceProvenanceError(
            "synthetic source authority changed after snapshot publication"
        )
    return (
        source_manifest_path,
        source_manifest_sha256,
        source_snapshot_path,
        source_snapshot,
        source_snapshot_sha256,
    )


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
    return _build_execution_source_manifest_for_paths(
        repo_root,
        commit,
        tree,
        execution_source_paths(repo_root, commit, test_module_paths),
    )


def _build_execution_source_manifest_for_paths(
    repo_root: Path,
    commit: str,
    tree: str,
    source_paths: Iterable[str],
) -> dict[str, Any]:
    """Build a manifest for a fixed caller-owned internal source set."""

    entries = []
    planned_paths = tuple(
        sorted({_canonical_relative_path(path) for path in source_paths})
    )
    for relative_path in planned_paths:
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


def _validate_frozen_git_authority(
    repo_root: Path,
    frozen_run_dir: Path | None,
    *,
    commit: str,
    tree: str,
    git_authority: Mapping[str, Any],
) -> Path | None:
    """Validate frozen Git metadata and its constant-size authority files."""

    try:
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
        try:
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
        except SourceProvenanceError as error:
            raise SourceProvenanceError(
                "frozen Git authority path is invalid"
            ) from error
        git_dir_result = _git(
            repo_root,
            ["rev-parse", "--absolute-git-dir"],
            text=True,
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
                or any(
                    type(value) is not int or value < 0
                    for value in identity.values()
                )
            ):
                raise SourceProvenanceError(
                    "frozen Git authority file identity is invalid"
                )
        if frozen_run_dir is None:
            return None
        resolved_run_dir = frozen_run_dir.resolve(strict=True)
        authority_dir = (resolved_run_dir / authority_path).resolve(strict=True)
        execution_root = (
            resolved_run_dir / "execution-source-files"
        ).resolve(strict=True)
        for resolved_path in (
            authority_dir,
            (resolved_run_dir / git_link_path).resolve(strict=True),
            (resolved_run_dir / alternates_path).resolve(strict=True),
            (resolved_run_dir / config_path).resolve(strict=True),
        ):
            if not resolved_path.is_relative_to(resolved_run_dir):
                raise SourceProvenanceError(
                    "frozen Git authority path escapes run directory"
                )
        _, link_content, link_identity = read_file_snapshot(
            resolved_run_dir / git_link_path
        )
        _, alternates_content, alternates_identity = read_file_snapshot(
            resolved_run_dir / alternates_path
        )
        _, config_content, config_identity = read_file_snapshot(
            resolved_run_dir / config_path
        )
        expected_link_content = (
            f"gitdir: {authority_dir.as_posix()}\n"
        ).encode("utf-8")
        config_entries = _git_config_entries(
            resolved_run_dir / config_path
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
        expected_object_dir = (
            common_dir.resolve(strict=True) / "objects"
        ).resolve(strict=True)
        alternates_lines = [
            line
            for line in alternates_content.decode("utf-8").splitlines()
            if line
        ]
        observed_object_dir = Path(alternates_lines[0]).resolve(strict=True)
        remote_origin = Path(
            config_entries["remote.origin.url"]
        ).resolve(strict=True)
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
            != os.path.normcase(str(execution_root))
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
        return authority_dir
    except SourceProvenanceError:
        raise
    except (
        IndexError,
        KeyError,
        OSError,
        ResultIntegrityError,
        TypeError,
        UnicodeError,
        ValueError,
    ) as error:
        raise SourceProvenanceError(
            "frozen Git authority is invalid"
        ) from error


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
    commit = manifest.get("commit")
    if not isinstance(commit, str) or _COMMIT.fullmatch(commit) is None:
        raise SourceProvenanceError("execution source manifest identity is invalid")
    return _validate_execution_source_manifest_for_paths(
        repo_root,
        manifest,
        require_worktree_match=require_worktree_match,
        frozen_run_dir=frozen_run_dir,
        expected_source_paths=execution_source_paths(
            repo_root,
            commit,
            expected_test_module_paths,
        ),
    )


def _validate_execution_source_manifest_for_paths(
    repo_root: Path,
    manifest: Mapping[str, Any],
    *,
    require_worktree_match: bool,
    frozen_run_dir: Path | None,
    expected_source_paths: Iterable[str],
) -> str:
    """Validate a manifest against an internal fixed source path set."""

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
    _validate_frozen_git_authority(
        repo_root,
        frozen_run_dir,
        commit=commit,
        tree=tree,
        git_authority=git_authority,
    )
    expected_paths = tuple(
        sorted(
            {
                _canonical_relative_path(path)
                for path in expected_source_paths
            }
        )
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


def create_source_snapshot(
    repo_root: Path,
    run_dir: Path,
    execution_root: Path,
    *,
    source_manifest_path: Path,
    source_manifest_sha256: str,
    source_manifest: Mapping[str, Any],
    expected_test_module_paths: Iterable[str],
    project: Mapping[str, Any],
    registry_sha256: str,
    project_marker_sha256: str,
    persisted_run_id: str | None,
    persisted_run_nonce: str | None,
    runner_identity: Mapping[str, Any],
    modules: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], str]:
    """Fully validate frozen source once and publish its scheduling authority."""

    module_paths = tuple(sorted(set(expected_test_module_paths)))
    commit = source_manifest.get("commit")
    if not isinstance(commit, str) or _COMMIT.fullmatch(commit) is None:
        raise SourceProvenanceError("execution source manifest identity is invalid")
    return _create_source_snapshot_for_paths(
        repo_root,
        run_dir,
        execution_root,
        source_manifest_path=source_manifest_path,
        source_manifest_sha256=source_manifest_sha256,
        source_manifest=source_manifest,
        expected_test_module_paths=module_paths,
        project=project,
        registry_sha256=registry_sha256,
        project_marker_sha256=project_marker_sha256,
        persisted_run_id=persisted_run_id,
        persisted_run_nonce=persisted_run_nonce,
        runner_identity=runner_identity,
        modules=modules,
        expected_source_paths=execution_source_paths(
            repo_root.resolve(strict=True),
            commit,
            module_paths,
        ),
    )


def _create_source_snapshot_for_paths(
    repo_root: Path,
    run_dir: Path,
    execution_root: Path,
    *,
    source_manifest_path: Path,
    source_manifest_sha256: str,
    source_manifest: Mapping[str, Any],
    expected_test_module_paths: Iterable[str],
    project: Mapping[str, Any],
    registry_sha256: str,
    project_marker_sha256: str,
    persisted_run_id: str | None,
    persisted_run_nonce: str | None,
    runner_identity: Mapping[str, Any],
    modules: Sequence[Mapping[str, Any]],
    expected_source_paths: Iterable[str],
) -> tuple[dict[str, Any], str]:
    """Create a snapshot from an internal fixed source path set."""

    module_paths = tuple(sorted(set(expected_test_module_paths)))
    validated_manifest_sha256 = _validate_execution_source_manifest_for_paths(
        repo_root,
        source_manifest,
        require_worktree_match=True,
        frozen_run_dir=run_dir,
        expected_source_paths=expected_source_paths,
    )
    if validated_manifest_sha256 != source_manifest_sha256:
        raise SourceProvenanceError(
            "execution source manifest fingerprint changed before scheduling"
        )
    entry_inventory = [
        {
            "path": item["path"],
            "git_blob": item["git_blob"],
            "runtime_sha256": item["runtime_sha256"],
            "runtime_size": item["runtime_size"],
        }
        for item in source_manifest["entries"]
    ]
    discovered_module_inventory = module_inventory(modules)
    payload = {
        "schema_name": SOURCE_SNAPSHOT_SCHEMA_NAME,
        "schema_version": SOURCE_SNAPSHOT_SCHEMA_VERSION,
        "run_dir": str(run_dir.resolve(strict=True)),
        "execution_root": str(execution_root.resolve(strict=True)),
        "persisted_run_id": persisted_run_id,
        "persisted_run_nonce": persisted_run_nonce,
        "runner_identity": dict(runner_identity),
        "project": dict(project),
        "registry_sha256": registry_sha256,
        "project_marker_sha256": project_marker_sha256,
        "source_manifest_path": str(source_manifest_path.resolve(strict=True)),
        "source_manifest_sha256": source_manifest_sha256,
        "commit": source_manifest["commit"],
        "git_tree": source_manifest["git_tree"],
        "entry_inventory": {
            "count": len(entry_inventory),
            "sha256": hashlib.sha256(
                canonical_json_bytes(entry_inventory)
            ).hexdigest(),
        },
        "module_inventory": {
            "count": len(discovered_module_inventory),
            "sha256": hashlib.sha256(
                canonical_json_bytes(list(discovered_module_inventory))
            ).hexdigest(),
        },
        "prevalidation": {
            "result": "passed",
            "source_manifest_sha256": validated_manifest_sha256,
        },
    }
    source_snapshot_id = hashlib.sha256(
        canonical_json_bytes(payload)
    ).hexdigest()
    snapshot = {
        **payload,
        "source_snapshot_id": source_snapshot_id,
    }
    snapshot_sha256 = write_bytes_exclusive(
        run_dir / SOURCE_SNAPSHOT_RELATIVE_PATH,
        canonical_json_bytes(snapshot),
    )
    return snapshot, snapshot_sha256


def _validate_source_snapshot_binding_uncached(
    run_dir: Path,
    assignment: Mapping[str, Any],
) -> tuple[str, str]:
    """Validate one immutable snapshot record and one assigned frozen module."""

    try:
        _, test_run_content, _ = read_file_snapshot(
            run_dir / "test-run.json"
        )
        test_run = json.loads(test_run_content.decode("utf-8"))
        _, manifest_content, _ = read_file_snapshot(
            run_dir / SOURCE_MANIFEST_RELATIVE_PATH
        )
        manifest = json.loads(manifest_content.decode("utf-8"))
        _, snapshot_content, _ = read_file_snapshot(
            run_dir / SOURCE_SNAPSHOT_RELATIVE_PATH
        )
        snapshot = json.loads(snapshot_content.decode("utf-8"))
    except (ResultIntegrityError, OSError, UnicodeError, ValueError) as error:
        raise SourceProvenanceError(
            "worker source snapshot is unreadable"
        ) from error
    if (
        not isinstance(test_run, dict)
        or set(test_run) != TEST_RUN_V2_FIELDS
        or canonical_json_bytes(test_run) != test_run_content
        or test_run.get("schema_name") != TEST_RUN_SCHEMA_NAME
        or type(test_run.get("schema_version")) is not int
        or test_run.get("schema_version") != TEST_RUN_SCHEMA_VERSION
        or not isinstance(manifest, dict)
        or set(manifest)
        != {
            "schema_name",
            "schema_version",
            "commit",
            "git_tree",
            "git_authority",
            "entries",
        }
        or canonical_json_bytes(manifest) != manifest_content
        or manifest.get("schema_name") != SOURCE_MANIFEST_SCHEMA_NAME
        or manifest.get("schema_version") != SOURCE_MANIFEST_SCHEMA_VERSION
    ):
        raise SourceProvenanceError(
            "worker source authority schema is invalid"
        )
    if (
        not isinstance(snapshot, dict)
        or set(snapshot)
        != {
            "schema_name",
            "schema_version",
            "run_dir",
            "execution_root",
            "persisted_run_id",
            "persisted_run_nonce",
            "runner_identity",
            "project",
            "registry_sha256",
            "project_marker_sha256",
            "source_manifest_path",
            "source_manifest_sha256",
            "commit",
            "git_tree",
            "entry_inventory",
            "module_inventory",
            "prevalidation",
            "source_snapshot_id",
        }
        or canonical_json_bytes(snapshot) != snapshot_content
        or snapshot.get("schema_name") != SOURCE_SNAPSHOT_SCHEMA_NAME
        or snapshot.get("schema_version") != SOURCE_SNAPSHOT_SCHEMA_VERSION
    ):
        raise SourceProvenanceError("worker source snapshot is invalid")
    try:
        repo_root = Path(assignment["repo_root"]).resolve(strict=True)
        live_commit, live_tree = _commit_and_tree(repo_root)
        _, live_registry_content, _ = read_file_snapshot(
            repo_root / "config/test-suites.v1.json"
        )
        live_registry_sha256 = hashlib.sha256(
            live_registry_content
        ).hexdigest()
        _, project_marker_content, _ = read_file_snapshot(
            run_dir.parent.parent / "project.json"
        )
        project_marker_sha256 = hashlib.sha256(
            project_marker_content
        ).hexdigest()
    except (
        KeyError,
        OSError,
        ResultIntegrityError,
        TypeError,
        ValueError,
    ) as error:
        raise SourceProvenanceError(
            "worker live source authority is unavailable"
        ) from error
    expected_manifest_path = (
        run_dir / SOURCE_MANIFEST_RELATIVE_PATH
    ).resolve(strict=True)
    expected_snapshot_path = (
        run_dir / SOURCE_SNAPSHOT_RELATIVE_PATH
    ).resolve(strict=True)
    snapshot_runner_identity = snapshot.get("runner_identity")
    live_runner_identity = (
        process_execution_identity(snapshot_runner_identity.get("pid"))
        if isinstance(snapshot_runner_identity, dict)
        and type(snapshot_runner_identity.get("pid")) is int
        else None
    )
    if (
        Path(test_run["run_dir"]).resolve(strict=True)
        != run_dir.resolve(strict=True)
        or Path(test_run["source_manifest_path"]).resolve(strict=True)
        != expected_manifest_path
        or Path(test_run["source_snapshot_path"]).resolve(strict=True)
        != expected_snapshot_path
        or hashlib.sha256(manifest_content).hexdigest()
        != test_run["source_manifest_sha256"]
        or hashlib.sha256(snapshot_content).hexdigest()
        != test_run["source_snapshot_sha256"]
        or snapshot.get("source_manifest_path")
        != str(expected_manifest_path)
        or snapshot.get("source_manifest_sha256")
        != test_run["source_manifest_sha256"]
        or test_run.get("project") != snapshot.get("project")
        or test_run.get("project")
        != {
            "project_key": "video2pdf",
            "repository": "Nishijujuba/video2pdf",
        }
        or test_run.get("commit") != live_commit
        or manifest.get("commit") != live_commit
        or snapshot.get("commit") != live_commit
        or manifest.get("git_tree") != live_tree
        or snapshot.get("git_tree") != live_tree
        or test_run.get("registry_sha256") != live_registry_sha256
        or snapshot.get("registry_sha256") != live_registry_sha256
        or test_run.get("project_marker_sha256")
        != project_marker_sha256
        or snapshot.get("project_marker_sha256")
        != project_marker_sha256
        or test_run.get("persisted_run_id")
        != snapshot.get("persisted_run_id")
        or test_run.get("persisted_run_nonce")
        != snapshot.get("persisted_run_nonce")
        or test_run.get("runner_identity")
        != snapshot.get("runner_identity")
        or not execution_identity_is_complete(live_runner_identity)
        or live_runner_identity != snapshot_runner_identity
    ):
        raise SourceProvenanceError(
            "worker source authority binding is invalid"
        )
    snapshot_sha256 = hashlib.sha256(snapshot_content).hexdigest()
    source_snapshot_id = snapshot.get("source_snapshot_id")
    payload = {
        key: value
        for key, value in snapshot.items()
        if key != "source_snapshot_id"
    }
    if (
        not isinstance(source_snapshot_id, str)
        or _SHA256.fullmatch(source_snapshot_id) is None
        or hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
        != source_snapshot_id
        or assignment.get("source_snapshot_id") != source_snapshot_id
        or assignment.get("source_snapshot_sha256") != snapshot_sha256
    ):
        raise SourceProvenanceError(
            "worker source snapshot identity is invalid"
        )
    canonical_execution_root = (
        run_dir / "execution-source-files"
    ).resolve(strict=True)
    if (
        snapshot.get("run_dir") != str(run_dir.resolve(strict=True))
        or snapshot.get("execution_root") != str(canonical_execution_root)
        or assignment.get("execution_root") != str(canonical_execution_root)
        or assignment.get("source_manifest_sha256")
        not in (None, snapshot.get("source_manifest_sha256"))
        or assignment.get("module_inventory_sha256")
        != snapshot.get("module_inventory", {}).get("sha256")
    ):
        raise SourceProvenanceError(
            "worker source snapshot binding is invalid"
        )
    runner_identity = snapshot.get("runner_identity")
    project = snapshot.get("project")
    entry_inventory = snapshot.get("entry_inventory")
    module_inventory = snapshot.get("module_inventory")
    prevalidation = snapshot.get("prevalidation")
    if (
        not execution_identity_is_complete(runner_identity)
        or project
        != {
            "project_key": "video2pdf",
            "repository": "Nishijujuba/video2pdf",
        }
        or not isinstance(entry_inventory, dict)
        or set(entry_inventory) != {"count", "sha256"}
        or type(entry_inventory.get("count")) is not int
        or entry_inventory["count"] < 1
        or not isinstance(entry_inventory.get("sha256"), str)
        or _SHA256.fullmatch(entry_inventory["sha256"]) is None
        or not isinstance(module_inventory, dict)
        or set(module_inventory) != {"count", "sha256"}
        or type(module_inventory.get("count")) is not int
        or module_inventory["count"] < 1
        or not isinstance(module_inventory.get("sha256"), str)
        or _SHA256.fullmatch(module_inventory["sha256"]) is None
        or not isinstance(prevalidation, dict)
        or set(prevalidation) != {"result", "source_manifest_sha256"}
        or prevalidation.get("result") != "passed"
        or prevalidation.get("source_manifest_sha256")
        != snapshot.get("source_manifest_sha256")
        or any(
            not isinstance(snapshot.get(field), str)
            or _SHA256.fullmatch(snapshot[field]) is None
            for field in (
                "registry_sha256",
                "project_marker_sha256",
                "source_manifest_sha256",
            )
        )
        or not isinstance(snapshot.get("commit"), str)
        or _COMMIT.fullmatch(snapshot["commit"]) is None
        or not isinstance(snapshot.get("git_tree"), str)
        or _GIT_OBJECT.fullmatch(snapshot["git_tree"]) is None
    ):
        raise SourceProvenanceError(
            "worker source snapshot nested authority is invalid"
        )
    source_path = assignment.get("source_path")
    source_sha256 = assignment.get("source_sha256")
    assigned_test_ids = assignment.get("test_ids")
    assigned_inventory = assignment.get("module_inventory")
    if (
        not isinstance(source_path, str)
        or not isinstance(source_sha256, str)
        or _SHA256.fullmatch(source_sha256) is None
        or not isinstance(assigned_test_ids, list)
        or any(not isinstance(test_id, str) for test_id in assigned_test_ids)
        or not isinstance(assigned_inventory, list)
    ):
        raise SourceProvenanceError(
            "worker assigned module binding is invalid"
        )
    if (
        len(assigned_inventory) != module_inventory["count"]
        or hashlib.sha256(
            canonical_json_bytes(assigned_inventory)
        ).hexdigest()
        != module_inventory["sha256"]
    ):
        raise SourceProvenanceError(
            "worker module inventory membership proof is invalid"
        )
    assigned_member = {
        "module_key": assignment.get("module_key"),
        "suite_id": assignment.get("suite_id"),
        "source_path": source_path,
        "test_count": len(assigned_test_ids),
        "test_ids_sha256": hashlib.sha256(
            canonical_json_bytes(sorted(assigned_test_ids))
        ).hexdigest(),
    }
    if assigned_member not in assigned_inventory:
        raise SourceProvenanceError(
            "worker module inventory membership proof is invalid"
        )
    source_path = _canonical_relative_path(source_path)
    entries = manifest.get("entries")
    authority = manifest.get("git_authority")
    if (
        not isinstance(entries, list)
        or not isinstance(authority, dict)
        or set(authority)
        != {
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
        }
    ):
        raise SourceProvenanceError(
            "worker frozen Git authority is invalid"
        )
    authority_dir = _validate_frozen_git_authority(
        repo_root,
        run_dir,
        commit=live_commit,
        tree=live_tree,
        git_authority=authority,
    )
    entry_inventory_values = []
    selected_entry = None
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
                "worker source manifest entry is invalid"
            )
        try:
            item_path = _canonical_relative_path(item["path"])
            item_frozen_path = _canonical_relative_path(item["frozen_path"])
        except SourceProvenanceError as error:
            raise SourceProvenanceError(
                "worker source manifest entry path is invalid"
            ) from error
        if (
            item_path != item["path"]
            or item_frozen_path
            != f"execution-source-files/{item_path}"
        ):
            raise SourceProvenanceError(
                "worker source manifest entry path is invalid"
            )
        entry_inventory_values.append(
            {
                "path": item["path"],
                "git_blob": item["git_blob"],
                "runtime_sha256": item["runtime_sha256"],
                "runtime_size": item["runtime_size"],
            }
        )
        if item.get("path") == source_path:
            selected_entry = item
    if (
        selected_entry is None
        or snapshot.get("entry_inventory")
        != {
            "count": len(entry_inventory_values),
            "sha256": hashlib.sha256(
                canonical_json_bytes(entry_inventory_values)
            ).hexdigest(),
        }
        or authority.get("head_commit") != live_commit
        or authority.get("head_tree") != live_tree
    ):
        raise SourceProvenanceError(
            "worker source manifest inventory is invalid"
        )
    try:
        _, source_content, source_identity = read_file_snapshot(
            run_dir / "execution-source-files" / source_path
        )
    except ResultIntegrityError as error:
        raise SourceProvenanceError(
            "worker assigned module is unreadable"
        ) from error
    blob_result = _git(
        repo_root,
        [
            "--git-dir",
            str(authority_dir),
            "cat-file",
            "blob",
            selected_entry["git_blob"],
        ],
    )
    if (
        hashlib.sha256(source_content).hexdigest() != source_sha256
        or selected_entry.get("frozen_path")
        != f"execution-source-files/{source_path}"
        or selected_entry.get("runtime_sha256") != source_sha256
        or selected_entry.get("runtime_size") != len(source_content)
        or selected_entry.get("frozen_file_identity") != source_identity
        or blob_result.returncode != 0
        or hashlib.sha256(blob_result.stdout).hexdigest()
        != selected_entry.get("committed_sha256")
        or blob_result.stdout != source_content
    ):
        raise SourceProvenanceError(
            "worker assigned module fingerprint mismatch"
        )
    return source_snapshot_id, snapshot_sha256


def _worker_authority_cache_key(
    run_dir: Path,
    assignment: Mapping[str, Any],
) -> tuple[Any, ...]:
    """Bind a worker cache entry to every small mutable authority artifact."""

    try:
        resolved_run_dir = run_dir.resolve(strict=True)
        source_path_value = assignment.get("source_path")
        if not isinstance(source_path_value, str):
            raise ValueError("assigned source path is invalid")
        source_path = _canonical_relative_path(source_path_value)
        assignment_sha256 = hashlib.sha256(
            canonical_json_bytes(
                authority_assignment_projection(assignment)
            )
        ).hexdigest()
        artifact_paths = (
            "test-run.json",
            SOURCE_MANIFEST_RELATIVE_PATH.as_posix(),
            SOURCE_SNAPSHOT_RELATIVE_PATH.as_posix(),
            "execution-source-files/.git",
            "execution-git/config",
            "execution-git/objects/info/alternates",
            "execution-git/HEAD",
            "execution-git/refs/heads/execution-source",
            f"execution-source-files/{source_path}",
        )
        artifact_keys = []
        artifact_content: dict[str, bytes] = {}
        for relative_path in artifact_paths:
            _, content, identity = read_file_snapshot(
                resolved_run_dir / relative_path
            )
            artifact_content[relative_path] = content
            artifact_keys.append(
                (
                    relative_path,
                    hashlib.sha256(content).hexdigest(),
                    tuple(sorted(identity.items())),
                )
            )
        authority_identity = file_artifact_identity(
            resolved_run_dir / "execution-git"
        )
        repo_root = Path(assignment["repo_root"]).resolve(strict=True)
        live_commit, live_tree = _commit_and_tree(repo_root)
        git_dir_result = _git(
            repo_root,
            ["rev-parse", "--absolute-git-dir"],
            text=True,
        )
        common_dir_result = _git(
            repo_root,
            ["rev-parse", "--git-common-dir"],
            text=True,
        )
        if git_dir_result.returncode != 0 or common_dir_result.returncode != 0:
            raise ValueError("live Git authority is unavailable")
        git_dir = Path(git_dir_result.stdout.strip()).resolve(strict=True)
        common_dir = Path(common_dir_result.stdout.strip())
        if not common_dir.is_absolute():
            common_dir = repo_root / common_dir
        common_dir = common_dir.resolve(strict=True)
        _, live_git_config_content, live_git_config_identity = (
            read_file_snapshot(common_dir / "config")
        )
        _, live_head_content, live_head_identity = read_file_snapshot(
            git_dir / "HEAD"
        )
        _, registry_content, registry_identity = read_file_snapshot(
            repo_root / "config/test-suites.v1.json"
        )
        _, marker_content, marker_identity = read_file_snapshot(
            resolved_run_dir.parent.parent / "project.json"
        )
        snapshot = json.loads(
            artifact_content[
                SOURCE_SNAPSHOT_RELATIVE_PATH.as_posix()
            ].decode("utf-8")
        )
        snapshot_runner_identity = snapshot.get("runner_identity")
        runner_identity = (
            process_execution_identity(snapshot_runner_identity.get("pid"))
            if isinstance(snapshot_runner_identity, dict)
            and type(snapshot_runner_identity.get("pid")) is int
            else None
        )
        live_inputs = (
            live_commit,
            live_tree,
            str(git_dir),
            tuple(sorted(file_artifact_identity(git_dir).items())),
            str(common_dir),
            tuple(sorted(file_artifact_identity(common_dir).items())),
            tuple(
                sorted(
                    file_artifact_identity(common_dir / "objects").items()
                )
            ),
            hashlib.sha256(live_git_config_content).hexdigest(),
            tuple(sorted(live_git_config_identity.items())),
            hashlib.sha256(live_head_content).hexdigest(),
            tuple(sorted(live_head_identity.items())),
            hashlib.sha256(registry_content).hexdigest(),
            tuple(sorted(registry_identity.items())),
            hashlib.sha256(marker_content).hexdigest(),
            tuple(sorted(marker_identity.items())),
            hashlib.sha256(
                canonical_json_bytes(runner_identity)
            ).hexdigest(),
        )
    except (
        KeyError,
        OSError,
        ResultIntegrityError,
        TypeError,
        UnicodeError,
        ValueError,
    ) as error:
        raise SourceProvenanceError(
            "worker source authority cache identity is unavailable"
        ) from error
    return (
        os.getpid(),
        str(resolved_run_dir),
        assignment.get("module_key"),
        assignment_sha256,
        tuple(sorted(authority_identity.items())),
        tuple(artifact_keys),
        live_inputs,
    )


def authority_assignment_projection(
    assignment: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the sole cache identity projection for worker authority fields."""

    return {
        field: assignment.get(field)
        for field in _AUTHORITY_ASSIGNMENT_FIELDS
    }


def validate_source_snapshot_binding(
    run_dir: Path,
    assignment: Mapping[str, Any],
) -> tuple[str, str]:
    """Validate one worker authority once for each immutable artifact identity."""

    with _WORKER_AUTHORITY_CACHE_LOCK:
        pre_validation_key = _worker_authority_cache_key(
            run_dir,
            assignment,
        )
        cached = _WORKER_AUTHORITY_CACHE.get(pre_validation_key)
        if cached is not None:
            return cached
        validated = _validate_source_snapshot_binding_uncached(
            run_dir,
            assignment,
        )
        post_validation_key = _worker_authority_cache_key(
            run_dir,
            assignment,
        )
        if post_validation_key != pre_validation_key:
            raise SourceProvenanceError(
                "worker source authority changed during validation"
            )
        _WORKER_AUTHORITY_CACHE[pre_validation_key] = validated
        return validated


def finalize_source_snapshot(
    repo_root: Path,
    run_dir: Path,
    *,
    source_snapshot: Mapping[str, Any],
    source_snapshot_sha256: str,
    source_manifest: Mapping[str, Any],
    expected_test_module_paths: Iterable[str],
    scheduler_success: bool,
    scheduler_failure_kind: str | None,
    summary_sha256: str | None,
) -> tuple[dict[str, Any], str]:
    """Revalidate the complete frozen authority before terminal success."""

    postvalidation_detail = None
    try:
        postvalidated_manifest_sha256 = validate_execution_source_manifest(
            repo_root,
            source_manifest,
            expected_test_module_paths=expected_test_module_paths,
            require_worktree_match=True,
            frozen_run_dir=run_dir,
        )
        if (
            postvalidated_manifest_sha256
            == source_snapshot["source_manifest_sha256"]
        ):
            postvalidation_result = "passed"
        else:
            postvalidation_result = "failed"
            postvalidation_detail = (
                "execution source manifest fingerprint changed after run"
            )
    except SourceProvenanceError as error:
        postvalidated_manifest_sha256 = None
        postvalidation_result = "failed"
        postvalidation_detail = str(error)
    if postvalidation_result == "failed":
        success = False
        failure_kind = "source_postrun_failure"
    elif not scheduler_success:
        success = False
        failure_kind = scheduler_failure_kind
    else:
        success = True
        failure_kind = None
    finalization = {
        "schema_name": RUN_FINALIZATION_SCHEMA_NAME,
        "schema_version": RUN_FINALIZATION_SCHEMA_VERSION,
        "source_snapshot_id": source_snapshot["source_snapshot_id"],
        "source_snapshot_sha256": source_snapshot_sha256,
        "source_manifest_sha256": source_snapshot["source_manifest_sha256"],
        "summary_sha256": summary_sha256,
        "scheduler_success": scheduler_success,
        "scheduler_failure_kind": scheduler_failure_kind,
        "postvalidation": {
            "result": postvalidation_result,
            "source_manifest_sha256": postvalidated_manifest_sha256,
            "detail": postvalidation_detail,
        },
        "success": success,
        "failure_kind": failure_kind,
    }
    finalization_sha256 = write_bytes_exclusive(
        run_dir / RUN_FINALIZATION_RELATIVE_PATH,
        canonical_json_bytes(finalization),
    )
    return finalization, finalization_sha256
