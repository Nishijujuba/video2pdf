"""Recompute and verify immutable Promotion v2 authority artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.project_test_results import canonical_json_bytes, sha256_file
from scripts.project_test_source_provenance import (
    PROMOTION_AUTHORITY_SOURCE_PATHS,
    SourceProvenanceError,
    committed_source_fingerprints,
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
    args = parser.parse_args(argv)
    repo_root = REPO_ROOT.resolve(strict=True)
    safety_path = repo_root / SAFETY_RELATIVE_PATH
    existing_safety, _safety_sha256 = _load_snapshot(safety_path)
    reviewed_implementation_commit = (
        args.reviewed_implementation_commit
        or existing_safety.get("reviewed_source_commit")
    )
    execution_evidence_commit = (
        args.execution_evidence_commit or reviewed_implementation_commit
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
    live_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()
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
        fingerprints[relative_path.as_posix()] = hashlib.sha256(
            actual_bytes
        ).hexdigest()
    print(
        json.dumps(
            {
                "valid": True,
                "artifacts": fingerprints,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
