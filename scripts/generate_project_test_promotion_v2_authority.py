"""Recompute and verify immutable Promotion v2 authority artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any, Sequence
import uuid


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.project_test_results import canonical_json_bytes, sha256_file
from scripts.project_test_source_provenance import (
    PROMOTION_AUTHORITY_ARTIFACT_PATHS,
    PROMOTION_AUTHORITY_SOURCE_PATHS,
    PROMOTION_AUTHORITY_TRANSACTION_MARKER_RELATIVE_PATH,
    PROMOTION_EVIDENCE_ONLY_PATHS,
    SourceProvenanceError,
    assert_no_incomplete_promotion_authority_transaction,
    committed_source_fingerprints,
    require_live_authority_artifacts_match_commit,
    validate_evidence_only_commit_range,
)
from scripts.validate_project_test_promotion import (
    AUTHORIZED_DELTA_TEST_IDS,
    AUTHORIZED_DELTA_TEST_ID_SET_SHA256,
    AUTHORIZED_PRODUCTION_PATHS,
    AUTHORIZED_TEST_MODULES,
    BASELINE_TEST_COUNT,
    BASELINE_TEST_ID_SET_SHA256,
    CURRENT_TEST_COUNT,
    CURRENT_TEST_ID_SET_SHA256,
    FINAL_ISSUE9_DISCOVERY_PATH,
    FINAL_ISSUE9_DISCOVERY_SHA256,
    MIGRATION_REVIEW_RELATIVE_PATH,
    SUPERSET_AUTHORITY_RELATIVE_PATH,
    _test_module_inventory,
)


DEFAULT_DISCOVERY = Path(
    r"D:\tests\video2pdf\video-workflow"
    r"\20260728_021941_c2a92d99\discovery.json"
)
DEFAULT_BASELINE_DISCOVERY = FINAL_ISSUE9_DISCOVERY_PATH
DEFAULT_FOCUSED_RUN = Path(
    "待删除/long-running/"
    "issue27_stage94_control_store_focused_20260728_094829_d7342421"
)
DEFAULT_PROFILE_RUN = Path(
    "待删除/long-running/"
    "issue27_stage94_three_test_profile_r10_final_20260728_095420_16981990"
)
DEFAULT_PROFILE_RESULT = Path("待删除/profile/profile-results-r10-final.json")
SAFETY_RELATIVE_PATH = Path(
    "evidence/project-test-runner/optimization-safety-review.v1.json"
)
FINAL_ISSUE9_COMMIT = "5b4d76753da68713c8c2c009a77c4fa43b25373c"
MATERIALIZED_AUTHORITY_PATHS = frozenset(
    PROMOTION_AUTHORITY_ARTIFACT_PATHS
)


def _load_snapshot(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value, hashlib.sha256(raw).hexdigest()


def _relative_or_absolute(repo_root: Path, path: Path) -> str:
    resolved = path.resolve(strict=True)
    try:
        return resolved.relative_to(repo_root).as_posix()
    except ValueError:
        return str(resolved)


def _artifact(
    repo_root: Path,
    discovery_path: Path,
    baseline_discovery_path: Path,
) -> dict[str, Any]:
    discovery, _discovery_sha256 = _load_snapshot(discovery_path)
    current_ids = sorted(
        test_id
        for module in discovery.get("modules", [])
        for test_id in module.get("test_ids", [])
    )
    if (
        len(current_ids) != CURRENT_TEST_COUNT
        or hashlib.sha256(canonical_json_bytes(current_ids)).hexdigest()
        != CURRENT_TEST_ID_SET_SHA256
    ):
        raise ValueError("discovery is not the fixed 499-ID inventory")
    delta_ids = list(AUTHORIZED_DELTA_TEST_IDS)
    if (
        hashlib.sha256(canonical_json_bytes(delta_ids)).hexdigest()
        != AUTHORIZED_DELTA_TEST_ID_SET_SHA256
    ):
        raise ValueError("fixed authorized delta constant is inconsistent")
    baseline_discovery, baseline_discovery_sha256 = _load_snapshot(
        baseline_discovery_path
    )
    if (
        baseline_discovery_path != DEFAULT_BASELINE_DISCOVERY.resolve(
            strict=True
        )
        or baseline_discovery_sha256 != FINAL_ISSUE9_DISCOVERY_SHA256
    ):
        raise ValueError(
            "baseline discovery is not the original final Issue #9 artifact"
        )
    baseline_ids = sorted(
        test_id
        for module in baseline_discovery.get("modules", [])
        for test_id in module.get("test_ids", [])
    )
    if (
        len(baseline_ids) != BASELINE_TEST_COUNT
        or hashlib.sha256(canonical_json_bytes(baseline_ids)).hexdigest()
        != BASELINE_TEST_ID_SET_SHA256
    ):
        raise ValueError("baseline discovery is not the fixed 475-ID inventory")
    if sorted(set(current_ids) - set(delta_ids)) != baseline_ids:
        raise ValueError("499 discovery minus authorized 24 differs from baseline")
    migration_path = repo_root / MIGRATION_REVIEW_RELATIVE_PATH
    migration_sha256 = sha256_file(migration_path)
    return {
        "schema_name": (
            "video2pdf.project-test-promotion-superset-authority"
        ),
        "schema_version": 2,
        "issue": 27,
        "authority_sources": [
            {
                "path": source_path,
                "sha256": sha256_file(repo_root / source_path),
            }
            for source_path in PROMOTION_AUTHORITY_SOURCE_PATHS
        ],
        "baseline": {
            "commit": FINAL_ISSUE9_COMMIT,
            "test_count": BASELINE_TEST_COUNT,
            "test_id_set_sha256": BASELINE_TEST_ID_SET_SHA256,
            "test_ids": baseline_ids,
            "source_evidence_path": MIGRATION_REVIEW_RELATIVE_PATH.as_posix(),
            "source_evidence_sha256": migration_sha256,
        },
        "authorized_delta": {
            "authorization": "issue-27-option-b",
            "test_count": len(delta_ids),
            "test_id_set_sha256": AUTHORIZED_DELTA_TEST_ID_SET_SHA256,
            "test_ids": delta_ids,
            "modules": [
                {
                    "source_path": source_path,
                    "test_count": test_count,
                    "module_source_sha256": sha256_file(
                        repo_root / source_path
                    ),
                    "ast_assertion_call_count": _test_module_inventory(
                        repo_root / source_path
                    )[1],
                }
                for source_path, (
                    test_count,
                    _expected_assertions,
                ) in sorted(AUTHORIZED_TEST_MODULES.items())
            ],
            "production_paths": [
                {
                    "path": source_path,
                    "sha256": sha256_file(repo_root / source_path),
                }
                for source_path in sorted(AUTHORIZED_PRODUCTION_PATHS)
            ],
        },
        "derived_current": {
            "test_count": CURRENT_TEST_COUNT,
            "test_id_set_sha256": CURRENT_TEST_ID_SET_SHA256,
        },
        "semantic_review": {
            "path": MIGRATION_REVIEW_RELATIVE_PATH.as_posix(),
            "sha256": migration_sha256,
            "baseline_test_ids_removed_or_renamed": 0,
            "unauthorized_test_ids_added": 0,
            "unsafe_health_memo_present": False,
        },
    }


def _run_binding(repo_root: Path, run_dir: Path) -> dict[str, str]:
    resolved = (repo_root / run_dir).resolve(strict=True)
    return {
        "persisted_run_dir": _relative_or_absolute(repo_root, resolved),
        "status_path": _relative_or_absolute(repo_root, resolved / "status.json"),
        "status_sha256": sha256_file(resolved / "status.json"),
        "exit_code_path": _relative_or_absolute(
            repo_root, resolved / "exit-code.txt"
        ),
        "exit_code_sha256": sha256_file(resolved / "exit-code.txt"),
        "command_path": _relative_or_absolute(
            repo_root, resolved / "command.json"
        ),
        "command_sha256": sha256_file(resolved / "command.json"),
        "stderr_path": _relative_or_absolute(repo_root, resolved / "stderr.log"),
        "stderr_sha256": sha256_file(resolved / "stderr.log"),
    }


def _safety_artifact(
    repo_root: Path,
    focused_run: Path,
    profile_run: Path,
    profile_result: Path,
    implementation_commit: str,
) -> dict[str, Any]:
    focused = _run_binding(repo_root, focused_run)
    focused.update(
        {
            "test_count": 76,
            "required_selectors": [
                "tests.video_workflow.test_control_store_recovery",
                "tests.video_workflow.test_control_store_transaction_scope",
                "tests.video_workflow.test_control_store_v9_fastpath",
                "tests.video_workflow.test_resource_control_store_integrity",
                "tests.video_workflow.test_source_ready_hardening",
            ],
        }
    )
    profile_resolved = (repo_root / profile_result).resolve(strict=True)
    profile_run_resolved = (repo_root / profile_run).resolve(strict=True)
    return {
        "schema_name": "video2pdf.project-test-optimization-safety-review",
        "schema_version": 1,
        "issue": 27,
        "reviewed_source_commit": implementation_commit,
        "source_files": [
            {
                "path": source_path,
                "sha256": sha256_file(repo_root / source_path),
            }
            for source_path in sorted(AUTHORIZED_PRODUCTION_PATHS)
        ],
        "focused_run": focused,
        "health_profile": {
            "path": _relative_or_absolute(repo_root, profile_resolved),
            "sha256": sha256_file(profile_resolved),
            "persisted_status_path": _relative_or_absolute(
                repo_root, profile_run_resolved / "status.json"
            ),
            "persisted_status_sha256": sha256_file(
                profile_run_resolved / "status.json"
            ),
            "persisted_exit_code_path": _relative_or_absolute(
                repo_root, profile_run_resolved / "exit-code.txt"
            ),
            "persisted_exit_code_sha256": sha256_file(
                profile_run_resolved / "exit-code.txt"
            ),
        },
        "independent_reviews": [
            {
                "axis": axis,
                "status": "PASS",
                "path": f"待删除/review/stage95-{axis}-review.md",
                "sha256": sha256_file(
                    repo_root / f"待删除/review/stage95-{axis}-review.md"
                ),
            }
            for axis in ("spec", "standards")
        ],
    }


def _git_stdout(repo_root: Path, arguments: Sequence[str]) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        raise SystemExit(
            "cannot inspect repository state: "
            + (completed.stderr.strip() or "git command failed")
        )
    return completed.stdout


def _changed_worktree_paths(repo_root: Path) -> frozenset[str]:
    raw = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=repo_root,
        check=False,
        capture_output=True,
    )
    if raw.returncode != 0:
        raise SystemExit("cannot inspect materialization worktree state")
    records = raw.stdout.split(b"\0")
    changed: set[str] = set()
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        if len(record) < 4:
            raise SystemExit("materialization worktree status is malformed")
        status = record[:2]
        changed.add(os.fsdecode(record[3:]).replace("\\", "/"))
        if b"R" in status or b"C" in status:
            if index >= len(records) or not records[index]:
                raise SystemExit("materialization rename status is malformed")
            changed.add(os.fsdecode(records[index]).replace("\\", "/"))
            index += 1
    return frozenset(changed)


def _assert_materialization_boundary(repo_root: Path) -> None:
    try:
        assert_no_incomplete_promotion_authority_transaction(repo_root)
    except SourceProvenanceError as error:
        raise SystemExit(str(error)) from error
    if (
        not MATERIALIZED_AUTHORITY_PATHS
        or not MATERIALIZED_AUTHORITY_PATHS.issubset(
            PROMOTION_EVIDENCE_ONLY_PATHS
        )
        or "evidence/project-test-runner/promotion-report.json"
        in MATERIALIZED_AUTHORITY_PATHS
    ):
        raise SystemExit("materialization output allowlist is invalid")
    unexpected = frozenset(
        path
        for path in _changed_worktree_paths(repo_root)
        if path not in MATERIALIZED_AUTHORITY_PATHS
        and not path.startswith("待删除/authority-materialization-")
    )
    if unexpected:
        raise SystemExit(
            "materialization requires a clean worktree outside exact authority "
            "artifacts: " + ", ".join(sorted(unexpected))
        )


def _assert_safe_target(repo_root: Path, relative_path: Path) -> Path:
    canonical_relative = relative_path.as_posix()
    if canonical_relative not in MATERIALIZED_AUTHORITY_PATHS:
        raise SystemExit(
            f"refusing unexpected materialization path: {canonical_relative}"
        )
    target = repo_root / relative_path
    try:
        if target.exists() and (
            target.is_dir()
            or target.is_symlink()
            or target.stat(follow_symlinks=False).st_nlink != 1
        ):
            raise SystemExit(
                f"materialization target is not an ordinary private file: "
                f"{canonical_relative}"
            )
        for component in (target.parent, *target.parent.parents):
            if component == repo_root.parent:
                break
            value = component.stat(follow_symlinks=False)
            if component.is_symlink() or getattr(
                value,
                "st_file_attributes",
                0,
            ) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
                raise SystemExit(
                    "materialization target contains a reparse point: "
                    f"{canonical_relative}"
                )
            if component == repo_root:
                break
        if target.parent.resolve(strict=True) != (
            repo_root / relative_path.parent
        ).resolve(strict=True):
            raise SystemExit(
                f"materialization target escapes repository: "
                f"{canonical_relative}"
            )
    except OSError as error:
        raise SystemExit(
            f"materialization target boundary is unproved: {canonical_relative}"
        ) from error
    return target


def _materialization_quarantine(repo_root: Path) -> Path:
    quarantine = repo_root / "待删除"
    try:
        quarantine.mkdir(exist_ok=True)
        quarantine_stat = quarantine.stat(follow_symlinks=False)
        if (
            not quarantine.is_dir()
            or quarantine.is_symlink()
            or getattr(
                quarantine_stat,
                "st_file_attributes",
                0,
            )
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            or quarantine.resolve(strict=True)
            != (repo_root.resolve(strict=True) / "待删除")
        ):
            raise OSError("quarantine boundary is unsafe")
    except OSError as error:
        raise SystemExit(
            "cannot prove repository materialization quarantine boundary"
        ) from error
    return quarantine


def _unique_quarantine_path(
    repo_root: Path,
    destination: Path,
    kind: str,
) -> Path:
    quarantine = _materialization_quarantine(repo_root)
    for _attempt in range(8):
        quarantined = quarantine / (
            "authority-materialization-"
            f"{destination.name}-{uuid.uuid4().hex}.{kind}"
        )
        if not os.path.lexists(quarantined):
            return quarantined
    raise SystemExit("cannot allocate unique materialization quarantine path")


def _move_to_quarantine(
    repo_root: Path,
    source: Path,
    destination: Path,
    kind: str,
) -> Path:
    quarantined = _unique_quarantine_path(
        repo_root,
        destination,
        kind,
    )
    try:
        os.rename(source, quarantined)
    except OSError as error:
        raise SystemExit(
            "cannot preserve authority materialization artifact in repository "
            f"quarantine: {source}"
        ) from error
    return quarantined


def _copy_to_quarantine(
    repo_root: Path,
    source: Path,
    kind: str,
) -> Path:
    quarantined = _unique_quarantine_path(
        repo_root,
        source,
        kind,
    )
    try:
        with source.open("rb") as input_handle, quarantined.open("xb") as output:
            shutil.copyfileobj(input_handle, output)
            output.flush()
            os.fsync(output.fileno())
    except OSError as error:
        raise SystemExit(
            "cannot preserve displaced authority artifact in repository "
            f"quarantine: {source}"
        ) from error
    return quarantined


def _stage_materialization(
    repo_root: Path,
    destination: Path,
    content: bytes,
) -> Path:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as error:
        if temporary is not None and os.path.lexists(temporary):
            _move_to_quarantine(
                repo_root,
                temporary,
                destination,
                "temp",
            )
        raise SystemExit(
            f"cannot stage authority artifact: {destination}"
        ) from error
    return temporary


def _file_identity(path: Path) -> dict[str, Any]:
    content = path.read_bytes()
    return {
        "sha256": hashlib.sha256(content).hexdigest(),
        "size": len(content),
    }


def _content_identity(content: bytes) -> dict[str, Any]:
    return {
        "sha256": hashlib.sha256(content).hexdigest(),
        "size": len(content),
    }


def _relative_repo_path(repo_root: Path, path: Path) -> str:
    return path.relative_to(repo_root).as_posix()


def _acquire_materialization_marker(
    repo_root: Path,
    journal: dict[str, Any],
) -> Path:
    marker = (
        repo_root / PROMOTION_AUTHORITY_TRANSACTION_MARKER_RELATIVE_PATH
    )
    content = canonical_json_bytes(journal)
    created = False
    try:
        with marker.open("xb") as handle:
            created = True
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as error:
        if created and os.path.lexists(marker):
            _move_to_quarantine(
                repo_root,
                marker,
                marker,
                "invalid-marker",
            )
        raise SystemExit(
            "cannot acquire Promotion authority materialization transaction"
        ) from error
    return marker


def _write_materialization_journal(
    repo_root: Path,
    marker: Path,
    journal: dict[str, Any],
) -> None:
    temporary = _stage_materialization(
        repo_root,
        marker,
        canonical_json_bytes(journal),
    )
    try:
        _copy_to_quarantine(repo_root, marker, "journal-version")
        os.replace(temporary, marker)
    except OSError as error:
        if os.path.lexists(temporary):
            _move_to_quarantine(
                repo_root,
                temporary,
                marker,
                "journal-temp",
            )
        raise SystemExit(
            "cannot durably update Promotion authority transaction journal"
        ) from error


def _strict_json_object(path: Path) -> dict[str, Any]:
    def reject_duplicate_pairs(
        pairs: list[tuple[str, Any]],
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate journal field: {key}")
            result[key] = value
        return result

    try:
        raw = path.read_bytes()
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=reject_duplicate_pairs,
        )
    except (OSError, UnicodeError, ValueError) as error:
        raise SystemExit(
            "Promotion authority transaction journal is corrupt"
        ) from error
    if not isinstance(value, dict):
        raise SystemExit("Promotion authority transaction journal is invalid")
    return value


def _require_fields(
    value: dict[str, Any],
    expected: frozenset[str],
    label: str,
) -> None:
    if frozenset(value) != expected:
        raise SystemExit(f"{label} fields are invalid")


def _journal_path(
    repo_root: Path,
    declared: Any,
    label: str,
    *,
    expected: Path | None = None,
) -> Path:
    if (
        not isinstance(declared, str)
        or "\\" in declared
        or PurePosixPath(declared).is_absolute()
        or any(part in {"", ".", ".."} for part in PurePosixPath(declared).parts)
    ):
        raise SystemExit(f"{label} path is invalid")
    path = repo_root.joinpath(*PurePosixPath(declared).parts)
    if expected is not None and path != expected:
        raise SystemExit(f"{label} path identity is invalid")
    try:
        parent = path.parent.resolve(strict=True)
        expected_parent = repo_root.joinpath(
            *PurePosixPath(declared).parent.parts
        ).resolve(strict=True)
        if parent != expected_parent:
            raise OSError("path parent identity mismatch")
        for component in (path.parent, *path.parent.parents):
            if component == repo_root.parent:
                break
            metadata = component.stat(follow_symlinks=False)
            if component.is_symlink() or getattr(
                metadata,
                "st_file_attributes",
                0,
            ) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
                raise OSError("path contains reparse point")
            if component == repo_root:
                break
    except OSError as error:
        raise SystemExit(f"{label} path boundary is unproved") from error
    return path


def _ordinary_file_identity(
    path: Path,
    label: str,
) -> dict[str, Any]:
    try:
        metadata = path.stat(follow_symlinks=False)
        if (
            not path.is_file()
            or path.is_symlink()
            or metadata.st_nlink != 1
            or getattr(metadata, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        ):
            raise OSError("not an ordinary private file")
        return _file_identity(path)
    except OSError as error:
        raise SystemExit(f"{label} is missing or unsafe") from error


def _declared_identity(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SystemExit(f"{label} identity is invalid")
    _require_fields(value, frozenset({"sha256", "size"}), label)
    if (
        not isinstance(value["sha256"], str)
        or re.fullmatch(r"[0-9a-f]{64}", value["sha256"]) is None
        or type(value["size"]) is not int
        or value["size"] < 0
    ):
        raise SystemExit(f"{label} identity is invalid")
    return value


def _load_materialization_journal(
    repo_root: Path,
) -> tuple[Path, dict[str, Any], list[dict[str, Any]]]:
    marker = repo_root / PROMOTION_AUTHORITY_TRANSACTION_MARKER_RELATIVE_PATH
    _journal_path(
        repo_root,
        PROMOTION_AUTHORITY_TRANSACTION_MARKER_RELATIVE_PATH.as_posix(),
        "Promotion authority transaction journal",
        expected=marker,
    )
    _ordinary_file_identity(marker, "Promotion authority transaction journal")
    journal = _strict_json_object(marker)
    _require_fields(
        journal,
        frozenset(
            {
                "schema_name",
                "schema_version",
                "transaction_id",
                "phase",
                "artifacts",
            }
        ),
        "Promotion authority transaction journal",
    )
    if (
        journal["schema_name"]
        != "video2pdf.project-test-promotion-authority-transaction"
        or journal["schema_version"] != 2
        or not isinstance(journal["transaction_id"], str)
        or re.fullmatch(r"[0-9a-f]{32}", journal["transaction_id"]) is None
        or journal["phase"]
        not in {"publishing", "published", "recovering"}
        or not isinstance(journal["artifacts"], list)
        or len(journal["artifacts"]) != len(PROMOTION_AUTHORITY_ARTIFACT_PATHS)
    ):
        raise SystemExit("Promotion authority transaction journal is invalid")
    artifacts: list[dict[str, Any]] = []
    for index, expected_relative in enumerate(
        sorted(PROMOTION_AUTHORITY_ARTIFACT_PATHS)
    ):
        artifact = journal["artifacts"][index]
        if not isinstance(artifact, dict):
            raise SystemExit("Promotion authority journal artifact is invalid")
        _require_fields(
            artifact,
            frozenset(
                {
                    "canonical_path",
                    "staged_path",
                    "backup_path",
                    "recovery_stage_path",
                    "old",
                    "new",
                    "publication_state",
                }
            ),
            "Promotion authority journal artifact",
        )
        canonical = _journal_path(
            repo_root,
            artifact["canonical_path"],
            "canonical artifact",
            expected=repo_root / expected_relative,
        )
        staged = _journal_path(
            repo_root,
            artifact["staged_path"],
            "staged artifact",
        )
        recovery_stage = _journal_path(
            repo_root,
            artifact["recovery_stage_path"],
            "recovery staged artifact",
        )
        if (
            staged.parent != canonical.parent
            or not staged.name.startswith(f".{canonical.name}.")
            or not staged.name.endswith(".tmp")
            or recovery_stage.parent != canonical.parent
            or recovery_stage.name
            != f".{canonical.name}.{journal['transaction_id']}.recovery.tmp"
        ):
            raise SystemExit("Promotion authority staging identity is invalid")
        old = artifact["old"]
        if not isinstance(old, dict):
            raise SystemExit("Promotion authority old identity is invalid")
        _require_fields(
            old,
            frozenset({"exists", "sha256", "size"}),
            "Promotion authority old identity",
        )
        if type(old["exists"]) is not bool:
            raise SystemExit("Promotion authority old identity is invalid")
        if old["exists"]:
            _declared_identity(
                {"sha256": old["sha256"], "size": old["size"]},
                "Promotion authority old",
            )
            if not isinstance(artifact["backup_path"], str):
                raise SystemExit("Promotion authority backup path is invalid")
            backup = _journal_path(
                repo_root,
                artifact["backup_path"],
                "backup artifact",
            )
            if backup.parent != _materialization_quarantine(repo_root):
                raise SystemExit("Promotion authority backup boundary is invalid")
            if _ordinary_file_identity(
                backup, "Promotion authority backup"
            ) != {"sha256": old["sha256"], "size": old["size"]}:
                raise SystemExit("Promotion authority backup identity is invalid")
        else:
            if (
                old["sha256"] is not None
                or old["size"] is not None
                or artifact["backup_path"] is not None
            ):
                raise SystemExit("Promotion authority absent-old identity is invalid")
            backup = None
        new = _declared_identity(
            artifact["new"], "Promotion authority new"
        )
        if artifact["publication_state"] not in {"pending", "published"}:
            raise SystemExit("Promotion authority publication state is invalid")
        if os.path.lexists(staged):
            if _ordinary_file_identity(
                staged, "Promotion authority staged artifact"
            ) != new:
                raise SystemExit("Promotion authority staged identity is invalid")
        if os.path.lexists(recovery_stage):
            expected_old = (
                {"sha256": old["sha256"], "size": old["size"]}
                if old["exists"]
                else None
            )
            if (
                expected_old is None
                or _ordinary_file_identity(
                    recovery_stage,
                    "Promotion authority recovery stage",
                )
                != expected_old
            ):
                raise SystemExit(
                    "Promotion authority recovery stage identity is invalid"
                )
        artifacts.append(
            {
                "record": artifact,
                "canonical": canonical,
                "staged": staged,
                "backup": backup,
                "recovery_stage": recovery_stage,
                "old": old,
                "new": new,
            }
        )
    return marker, journal, artifacts


def _canonical_state(
    artifact: dict[str, Any],
    *,
    recovering: bool,
) -> str:
    canonical = artifact["canonical"]
    if not os.path.lexists(canonical):
        if not artifact["old"]["exists"] or recovering:
            return "absent"
        raise SystemExit("Promotion authority canonical artifact is missing")
    identity = _ordinary_file_identity(
        canonical, "Promotion authority canonical artifact"
    )
    if identity == artifact["new"]:
        return "new"
    old = artifact["old"]
    if old["exists"] and identity == {
        "sha256": old["sha256"],
        "size": old["size"],
    }:
        return "old"
    raise SystemExit("Promotion authority canonical artifact identity is invalid")


def _recover_materialization(repo_root: Path) -> str:
    marker_path = (
        repo_root / PROMOTION_AUTHORITY_TRANSACTION_MARKER_RELATIVE_PATH
    )
    if not os.path.lexists(marker_path):
        return "no-transaction"
    marker, journal, artifacts = _load_materialization_journal(repo_root)
    states = [
        _canonical_state(
            artifact,
            recovering=journal["phase"] == "recovering",
        )
        for artifact in artifacts
    ]
    if all(state == "new" for state in states):
        journal["phase"] = "published"
        for artifact in journal["artifacts"]:
            artifact["publication_state"] = "published"
        _write_materialization_journal(repo_root, marker, journal)
        for artifact in artifacts:
            if os.path.lexists(artifact["staged"]):
                _move_to_quarantine(
                    repo_root,
                    artifact["staged"],
                    artifact["canonical"],
                    "stage",
                )
            if os.path.lexists(artifact["recovery_stage"]):
                _move_to_quarantine(
                    repo_root,
                    artifact["recovery_stage"],
                    artifact["canonical"],
                    "recovery-stage",
                )
        _move_to_quarantine(repo_root, marker, marker, "marker")
        return "finished-new-set"

    journal["phase"] = "recovering"
    _write_materialization_journal(repo_root, marker, journal)
    for artifact, state in zip(artifacts, states, strict=True):
        canonical = artifact["canonical"]
        if state == "new":
            _move_to_quarantine(repo_root, canonical, canonical, "new")
        if artifact["old"]["exists"] and state != "old":
            recovery_stage = artifact["recovery_stage"]
            if not os.path.lexists(recovery_stage):
                original_bytes = artifact["backup"].read_bytes()
                try:
                    with recovery_stage.open("xb") as handle:
                        handle.write(original_bytes)
                        handle.flush()
                        os.fsync(handle.fileno())
                except OSError as error:
                    raise SystemExit(
                        "cannot stage verified Promotion authority backup"
                    ) from error
            os.replace(recovery_stage, canonical)
    for artifact in artifacts:
        expected_old = artifact["old"]
        if expected_old["exists"]:
            if _ordinary_file_identity(
                artifact["canonical"],
                "restored Promotion authority artifact",
            ) != {
                "sha256": expected_old["sha256"],
                "size": expected_old["size"],
            }:
                raise SystemExit("Promotion authority old set restoration failed")
        elif os.path.lexists(artifact["canonical"]):
            raise SystemExit("Promotion authority absent old artifact was recreated")
        if os.path.lexists(artifact["staged"]):
            _move_to_quarantine(
                repo_root,
                artifact["staged"],
                artifact["canonical"],
                "stage",
            )
        if os.path.lexists(artifact["recovery_stage"]):
            _move_to_quarantine(
                repo_root,
                artifact["recovery_stage"],
                artifact["canonical"],
                "recovery-stage",
            )
    _move_to_quarantine(repo_root, marker, marker, "marker")
    return "restored-old-set"


def _materialize(
    repo_root: Path,
    expected: dict[Path, dict[str, Any]],
) -> dict[str, str]:
    _assert_materialization_boundary(repo_root)
    if {
        relative_path.as_posix() for relative_path in expected
    } != MATERIALIZED_AUTHORITY_PATHS:
        raise SystemExit("materialization did not produce the exact authority set")
    encoded = {
        relative_path: canonical_json_bytes(value)
        for relative_path, value in expected.items()
    }
    targets = {
        relative_path: _assert_safe_target(repo_root, relative_path)
        for relative_path in encoded
    }
    transaction_paths = sorted(
        encoded,
        key=lambda value: value.as_posix(),
    )
    if all(
        targets[relative_path].exists()
        and targets[relative_path].read_bytes() == encoded[relative_path]
        for relative_path in transaction_paths
    ):
        return {
            relative_path.as_posix(): hashlib.sha256(content).hexdigest()
            for relative_path, content in encoded.items()
        }
    staged: dict[Path, Path] = {}
    backups: dict[Path, Path | None] = {}
    transaction_id = uuid.uuid4().hex
    marker: Path | None = None
    try:
        for relative_path in transaction_paths:
            staged[relative_path] = _stage_materialization(
                repo_root,
                targets[relative_path],
                encoded[relative_path],
            )
        for relative_path in transaction_paths:
            target = targets[relative_path]
            backups[relative_path] = (
                _copy_to_quarantine(repo_root, target, "original")
                if target.exists()
                else None
            )
        journal = {
            "schema_name": (
                "video2pdf.project-test-promotion-authority-transaction"
            ),
            "schema_version": 2,
            "transaction_id": transaction_id,
            "phase": "publishing",
            "artifacts": [
                {
                    "canonical_path": relative_path.as_posix(),
                    "staged_path": _relative_repo_path(
                        repo_root, staged[relative_path]
                    ),
                    "backup_path": (
                        _relative_repo_path(repo_root, backups[relative_path])
                        if backups[relative_path] is not None
                        else None
                    ),
                    "recovery_stage_path": _relative_repo_path(
                        repo_root,
                        targets[relative_path].parent
                        / (
                            f".{targets[relative_path].name}."
                            f"{transaction_id}.recovery.tmp"
                        ),
                    ),
                    "old": (
                        {
                            "exists": True,
                            **_file_identity(targets[relative_path]),
                        }
                        if targets[relative_path].exists()
                        else {
                            "exists": False,
                            "sha256": None,
                            "size": None,
                        }
                    ),
                    "new": _content_identity(encoded[relative_path]),
                    "publication_state": "pending",
                }
                for relative_path in transaction_paths
            ],
        }
        marker = _acquire_materialization_marker(repo_root, journal)
        for relative_path in transaction_paths:
            os.replace(staged[relative_path], targets[relative_path])
            artifact = next(
                item
                for item in journal["artifacts"]
                if item["canonical_path"] == relative_path.as_posix()
            )
            artifact["publication_state"] = "published"
            _write_materialization_journal(repo_root, marker, journal)
    except (OSError, SystemExit) as error:
        if marker is None:
            for relative_path, temporary in staged.items():
                if os.path.lexists(temporary):
                    _move_to_quarantine(
                        repo_root,
                        temporary,
                        targets[relative_path],
                        "stage",
                    )
        else:
            try:
                _recover_materialization(repo_root)
            except SystemExit as recovery_error:
                raise SystemExit(
                    "authority artifact set transaction failed and rollback is "
                    f"incomplete: {recovery_error}"
                ) from error
        raise SystemExit(
            "cannot materialize authority artifact set; canonical set was "
            "rolled back"
        ) from error
    journal["phase"] = "published"
    _write_materialization_journal(repo_root, marker, journal)
    _move_to_quarantine(repo_root, marker, marker, "marker")
    return {
        relative_path.as_posix(): hashlib.sha256(content).hexdigest()
        for relative_path, content in encoded.items()
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--discovery", type=Path, default=DEFAULT_DISCOVERY)
    parser.add_argument(
        "--baseline-discovery",
        type=Path,
        default=DEFAULT_BASELINE_DISCOVERY,
    )
    parser.add_argument(
        "--focused-run", type=Path, default=DEFAULT_FOCUSED_RUN
    )
    parser.add_argument("--profile-run", type=Path, default=DEFAULT_PROFILE_RUN)
    parser.add_argument(
        "--profile-result", type=Path, default=DEFAULT_PROFILE_RESULT
    )
    parser.add_argument("--reviewed-implementation-commit")
    parser.add_argument("--execution-evidence-commit")
    parser.add_argument(
        "--write",
        action="store_true",
        help=(
            "atomically materialize the two fixed authority artifacts at the "
            "parent of a future execution-evidence commit"
        ),
    )
    parser.add_argument(
        "--recover",
        action="store_true",
        help=(
            "recover the repository-owned incomplete authority transaction "
            "without recomputing authority inputs"
        ),
    )
    args = parser.parse_args(argv)
    repo_root = REPO_ROOT.resolve(strict=True)
    if args.write and args.recover:
        raise SystemExit("--write and --recover are mutually exclusive")
    if args.recover:
        outcome = _recover_materialization(repo_root)
        print(
            json.dumps(
                {
                    "valid": True,
                    "mode": "recovered",
                    "outcome": outcome,
                },
                sort_keys=True,
            )
        )
        return 0
    try:
        assert_no_incomplete_promotion_authority_transaction(repo_root)
    except SourceProvenanceError as error:
        raise SystemExit(str(error)) from error
    safety_path = repo_root / SAFETY_RELATIVE_PATH
    existing_safety, _safety_sha256 = _load_snapshot(safety_path)
    live_head = _git_stdout(repo_root, ["rev-parse", "HEAD"]).strip()
    if args.write and args.execution_evidence_commit is not None:
        raise SystemExit(
            "--write cannot bind --execution-evidence-commit before that "
            "future evidence commit exists"
        )
    if args.write and args.reviewed_implementation_commit is None:
        raise SystemExit(
            "--write requires --reviewed-implementation-commit explicitly"
        )
    reviewed_implementation_commit = (
        args.reviewed_implementation_commit
        or existing_safety.get("reviewed_source_commit")
    )
    execution_evidence_commit = (
        args.execution_evidence_commit or live_head
    )
    if not isinstance(reviewed_implementation_commit, str):
        raise SystemExit("optimization safety reviewed_source_commit is invalid")
    try:
        validate_evidence_only_commit_range(
            repo_root,
            reviewed_implementation_commit,
            execution_evidence_commit,
            label="reviewed implementation to execution evidence",
        )
    except SourceProvenanceError as error:
        raise SystemExit(str(error)) from error
    try:
        validate_evidence_only_commit_range(
            repo_root,
            execution_evidence_commit,
            live_head,
            label="execution evidence to generator-time live HEAD",
        )
    except SourceProvenanceError as error:
        raise SystemExit(str(error)) from error
    live_authority_sources = {
        path: sha256_file(repo_root / path)
        for path in PROMOTION_AUTHORITY_SOURCE_PATHS
    }
    try:
        reviewed_authority_sources = committed_source_fingerprints(
            repo_root,
            reviewed_implementation_commit,
            PROMOTION_AUTHORITY_SOURCE_PATHS,
        )
        evidence_authority_sources = committed_source_fingerprints(
            repo_root,
            execution_evidence_commit,
            PROMOTION_AUTHORITY_SOURCE_PATHS,
        )
    except SourceProvenanceError as error:
        raise SystemExit(
            f"implementation commit source authority is invalid: {error}"
        ) from error
    if (
        reviewed_authority_sources != live_authority_sources
        or evidence_authority_sources != live_authority_sources
    ):
        raise SystemExit(
            "validator-time authority sources differ from reviewed "
            "implementation or execution evidence commit"
        )
    expected = {
        SUPERSET_AUTHORITY_RELATIVE_PATH: _artifact(
            repo_root,
            args.discovery.resolve(strict=True),
            args.baseline_discovery.resolve(strict=True),
        ),
        SAFETY_RELATIVE_PATH: _safety_artifact(
            repo_root,
            args.focused_run,
            args.profile_run,
            args.profile_result,
            reviewed_implementation_commit,
        ),
    }
    if args.write:
        fingerprints = _materialize(repo_root, expected)
        print(
            json.dumps(
                {
                    "valid": True,
                    "mode": "materialized",
                    "artifacts": fingerprints,
                    "reviewed_implementation_commit": (
                        reviewed_implementation_commit
                    ),
                    "evidence_parent_commit": live_head,
                    "execution_evidence_commit": None,
                },
                sort_keys=True,
            )
        )
        return 0
    try:
        committed_fingerprints = require_live_authority_artifacts_match_commit(
            repo_root,
            execution_evidence_commit,
        )
    except SourceProvenanceError as error:
        raise SystemExit(str(error)) from error
    fingerprints: dict[str, str] = {}
    for relative_path, value in expected.items():
        expected_bytes = canonical_json_bytes(value)
        actual_path = repo_root / relative_path
        try:
            actual_bytes = actual_path.read_bytes()
        except OSError as error:
            raise SystemExit(f"authority artifact is missing: {relative_path}") from error
        if actual_bytes != expected_bytes:
            raise SystemExit(f"authority artifact is stale: {relative_path}")
        actual_sha256 = hashlib.sha256(actual_bytes).hexdigest()
        if committed_fingerprints[relative_path.as_posix()] != actual_sha256:
            raise SystemExit(
                "execution evidence commit authority artifact differs: "
                f"{relative_path}"
            )
        fingerprints[relative_path.as_posix()] = actual_sha256
    print(
        json.dumps(
            {
                "valid": True,
                "mode": "verified",
                "artifacts": fingerprints,
                "reviewed_implementation_commit": (
                    reviewed_implementation_commit
                ),
                "execution_evidence_commit": execution_evidence_commit,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
