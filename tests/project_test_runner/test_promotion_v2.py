from __future__ import annotations

import hashlib
import json
import math
from contextlib import contextmanager
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest import mock

from jsonschema import Draft202012Validator

import scripts.validate_project_test_promotion as promotion_validator
from scripts.project_test_registry import load_registry
from scripts.project_test_results import (
    canonical_json_bytes,
    file_artifact_identity,
)
from scripts.project_test_source_provenance import (
    FIXED_EXECUTION_SOURCE_PATHS,
    PROMOTION_AUTHORITY_SOURCE_PATHS,
    SourceProvenanceError,
    _git_config_entries,
    _validate_frozen_git_config_entries,
    assert_clean_execution_worktree,
    build_execution_source_manifest,
    create_source_snapshot,
    create_frozen_git_authority,
    freeze_execution_source_files,
)
from scripts.validate_project_test_promotion import (
    AUTHORIZED_DELTA_TEST_ID_SET_SHA256,
    BASELINE_TEST_ID_SET_SHA256,
    CURRENT_TEST_ID_SET_SHA256,
    FINAL_ISSUE9_DISCOVERY_PATH,
    FINAL_ISSUE9_DISCOVERY_SHA256,
    PromotionValidationError,
    validate_promotion_report,
)
from tests.project_test_runner._fixture_root import new_fixture_dir


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATION = Path(
    "evidence/project-test-runner/test-path-migration-review.json"
)
AUTHORITY = Path(
    "evidence/project-test-runner/promotion-superset-authority.v2.json"
)
SELECTORS = [
    "tests.video_workflow.test_control_store_recovery",
    "tests.video_workflow.test_control_store_transaction_scope",
    "tests.video_workflow.test_control_store_v9_fastpath",
    "tests.video_workflow.test_resource_control_store_integrity",
    "tests.video_workflow.test_source_ready_hardening",
]


@contextmanager
def trusted_fixture_roots(repo: Path):
    with (
        mock.patch.object(
            promotion_validator,
            "CANONICAL_WORKTREE_ROOT",
            repo.resolve(),
        ),
        mock.patch.object(
            promotion_validator,
            "TRUSTED_EXTERNAL_RUN_ROOT",
            repo / "external" / "video2pdf" / "video-workflow",
        ),
        mock.patch.object(
            promotion_validator,
            "TRUSTED_PERSISTED_RUN_ROOT",
            repo,
        ),
    ):
        yield


def validate_promotion_report(repo: Path) -> dict:
    with trusted_fixture_roots(repo):
        return promotion_validator.validate_promotion_report(repo)


def write_json(path: Path, value: object) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = canonical_json_bytes(value)
    path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def write_bytes(path: Path, value: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)
    return hashlib.sha256(value).hexdigest()


def refresh_persisted_stdout_identity(run: dict) -> None:
    status_path = Path(run["persisted_status_path"])
    status = json.loads(status_path.read_text(encoding="utf-8"))
    stdout_path = Path(run["persisted_stdout_path"])
    status["artifact_identities"]["stdout"] = file_artifact_identity(
        stdout_path
    )
    status["log_sizes"]["stdout"] = stdout_path.stat().st_size
    run["persisted_status_sha256"] = write_json(status_path, status)


def read_stdout_records(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def write_stdout_records(path: Path, records: list[dict]) -> str:
    return write_bytes(
        path,
        b"".join(canonical_json_bytes(record) for record in records),
    )


def module_assignment(discovery: dict) -> list[dict]:
    result = [
        {
            "suite_id": item["suite_id"],
            "source_path": item["source_path"],
            "test_ids": sorted(item["test_ids"]),
        }
        for item in discovery["modules"]
    ]
    result.sort(key=lambda item: (item["suite_id"], item["source_path"]))
    return result


class PromotionReportV2Tests(unittest.TestCase):
    def test_promotion_authority_source_closed_set_is_explicit_and_live(
        self,
    ) -> None:
        required = {
            "scripts/run_project_tests.py",
            "scripts/project_test_scheduler.py",
            "scripts/validate_project_test_promotion.py",
            "scripts/generate_project_test_promotion_v2_authority.py",
            "schemas/project-test-promotion-report.v2.schema.json",
            "tests/video_workflow/test_contract_registry_cache.py",
            "tests/video_workflow/test_control_store_v9_fastpath.py",
            "src/video2pdf_workflow_kernel/contracts.py",
            "src/video2pdf_workflow_kernel/control_store.py",
        }
        self.assertEqual(
            tuple(sorted(set(PROMOTION_AUTHORITY_SOURCE_PATHS))),
            PROMOTION_AUTHORITY_SOURCE_PATHS,
        )
        self.assertTrue(
            required.issubset(PROMOTION_AUTHORITY_SOURCE_PATHS)
        )
        authority = json.loads(
            (PROJECT_ROOT / AUTHORITY).read_text(encoding="utf-8")
        )
        declared = {
            item["path"]: item["sha256"]
            for item in authority["authority_sources"]
        }
        self.assertEqual(
            set(PROMOTION_AUTHORITY_SOURCE_PATHS),
            set(declared),
        )
        self.assertEqual(
            declared,
            {
                path: hashlib.sha256(
                    (PROJECT_ROOT / path).read_bytes()
                ).hexdigest()
                for path in PROMOTION_AUTHORITY_SOURCE_PATHS
            },
        )

    def test_preflight_rejects_each_authority_byte_drift_category(
        self,
    ) -> None:
        repo, _report = self.make_report()
        paths = {
            "source": "scripts/project_test_source_provenance.py",
            "schema": "schemas/project-test-promotion-report.v2.schema.json",
            "registry": "config/test-suites.v1.json",
            "test_module": (
                "tests/video_workflow/test_control_store_v9_fastpath.py"
            ),
        }
        for category, relative in paths.items():
            with self.subTest(category=category):
                path = repo / relative
                original = path.read_bytes()
                path.write_bytes(original + b"\n")
                try:
                    with self.assertRaisesRegex(
                        SourceProvenanceError,
                        "clean Git worktree",
                    ):
                        assert_clean_execution_worktree(repo)
                finally:
                    path.write_bytes(original)

    def test_clean_gate_rejects_rename_from_source_into_allowed_output(
        self,
    ) -> None:
        repo = new_fixture_dir("clean-gate-rename")
        source = repo / "scripts/authority.py"
        destination = repo / "待删除/authority.py"
        source.parent.mkdir(parents=True)
        destination.parent.mkdir()
        source.write_text("AUTHORITY = True\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(
            ["git", "add", "."],
            cwd=repo,
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "-q",
                "-m",
                "fixture",
            ],
            cwd=repo,
            check=True,
        )
        subprocess.run(
            [
                "git",
                "mv",
                "scripts/authority.py",
                "待删除/authority.py",
            ],
            cwd=repo,
            check=True,
        )
        with self.assertRaisesRegex(
            SourceProvenanceError,
            "clean Git worktree",
        ):
            assert_clean_execution_worktree(repo)

    def test_clean_gate_accepts_unchanged_authority_copy_inside_allowed_output(
        self,
    ) -> None:
        repo = new_fixture_dir("clean-gate-unchanged-copy")
        for relative in FIXED_EXECUTION_SOURCE_PATHS:
            target = repo / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if relative == "config/test-suites.v1.json":
                continue
            shutil.copy2(PROJECT_ROOT / relative, target)
        authority_path = repo / "tests/authority/test_authority.py"
        authority_path.parent.mkdir(parents=True)
        authority_path.write_text("AUTHORITY = True\n", encoding="utf-8")
        write_json(
            repo / "config/test-suites.v1.json",
            {
                "schema_name": "video2pdf.project-test-suites",
                "schema_version": 1,
                "project": {
                    "project_key": "video2pdf",
                    "repository": "Nishijujuba/video2pdf",
                },
                "suites": [
                    {
                        "suite_id": "authority",
                        "suite_key": "authority",
                        "roots": [
                            {
                                "path": "tests/authority",
                                "pattern": "test_*.py",
                            }
                        ],
                    }
                ],
                "mirrors": [],
            },
        )
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "-q",
                "-m",
                "fixture",
            ],
            cwd=repo,
            check=True,
        )
        original_bytes = authority_path.read_bytes()
        copy_path = repo / "待删除/test_authority.py"
        copy_path.parent.mkdir()
        shutil.copy2(authority_path, copy_path)

        registry = load_registry(
            repo,
            Path("config/test-suites.v1.json"),
        )
        registered_paths = registry.registered_test_files()
        assert_clean_execution_worktree(repo)
        manifest = build_execution_source_manifest(repo, registered_paths)
        manifest_paths = [entry["path"] for entry in manifest["entries"]]

        self.assertEqual(original_bytes, authority_path.read_bytes())
        self.assertEqual(original_bytes, copy_path.read_bytes())
        self.assertEqual(
            ("tests/authority/test_authority.py",),
            registered_paths,
        )
        self.assertNotIn(str(copy_path.parent), sys.path)
        self.assertEqual(
            ["tests/authority/test_authority.py"],
            [
                path
                for path in manifest_paths
                if path.endswith("test_authority.py")
            ],
        )

        untracked_source = repo / "scripts/untracked_authority.py"
        untracked_source.write_bytes(original_bytes)
        with self.assertRaisesRegex(
            SourceProvenanceError,
            "clean Git worktree",
        ):
            assert_clean_execution_worktree(repo)

    def test_frozen_git_config_rejects_each_seven_key_attack(self) -> None:
        fixture = new_fixture_dir("promotion-v2-git-config")
        repo = fixture / "repo"
        execution_root = fixture / "execution"
        repo.mkdir()
        execution_root.mkdir()
        base = {
            "core.repositoryformatversion": "0",
            "core.filemode": "false",
            "core.bare": "false",
            "core.symlinks": "false",
            "core.ignorecase": "true",
            "core.worktree": str(execution_root),
            "remote.origin.url": str(repo),
        }

        def render(
            entries: dict[str, str],
            duplicate_key: str | None = None,
        ) -> bytes:
            core = [
                f"\t{key.split('.', 1)[1]} = {value}"
                for key, value in entries.items()
                if key.startswith("core.")
            ]
            remote = [
                f"\turl = {entries['remote.origin.url']}"
            ] if "remote.origin.url" in entries else []
            lines = ["[core]", *core]
            if remote:
                lines.extend(['[remote "origin"]', *remote])
            if duplicate_key is not None:
                section, name = duplicate_key.split(".", 1)
                if section == "remote":
                    lines.extend(
                        [
                            '[remote "origin"]',
                            f"\t{name.split('.', 1)[1]} = {base[duplicate_key]}",
                        ]
                    )
                else:
                    lines.extend(
                        [
                            f"[{section}]",
                            f"\t{name} = {base[duplicate_key]}",
                        ]
                    )
            return ("\n".join(lines) + "\n").encode("utf-8")

        config_path = fixture / "config"
        for key in base:
            for attack in ("missing", "duplicate", "value"):
                with self.subTest(key=key, attack=attack):
                    entries = dict(base)
                    duplicate_key = None
                    if attack == "missing":
                        entries.pop(key)
                    elif attack == "duplicate":
                        duplicate_key = key
                    else:
                        entries[key] = "invalid-stage109-value"
                    config_path.write_bytes(
                        render(entries, duplicate_key)
                    )
                    with self.assertRaises(SourceProvenanceError):
                        parsed = _git_config_entries(config_path)
                        _validate_frozen_git_config_entries(
                            parsed,
                            repo_root=repo,
                            execution_root=execution_root,
                        )

    def test_runner_binding_accepts_direct_target_or_launcher_child_only(
        self,
    ) -> None:
        target = {
            "pid": 101,
            "process_creation_identity": "windows-filetime:1001",
        }
        direct = dict(target)
        launcher_child = {
            "pid": 202,
            "process_creation_identity": "windows-filetime:2002",
            "parent_pid": 101,
            "parent_process_creation_identity": "windows-filetime:1001",
        }
        unrelated = {
            **launcher_child,
            "parent_process_creation_identity": "windows-filetime:9999",
        }

        self.assertTrue(
            promotion_validator._runner_is_bound_to_persisted_target(
                direct,
                target,
            )
        )
        self.assertTrue(
            promotion_validator._runner_is_bound_to_persisted_target(
                launcher_child,
                target,
            )
        )
        self.assertFalse(
            promotion_validator._runner_is_bound_to_persisted_target(
                unrelated,
                target,
            )
        )

    def make_report(
        self,
        *,
        commit_source_overrides: dict[str, bytes] | None = None,
    ) -> tuple[Path, dict]:
        repo = new_fixture_dir("promotion-v2")
        for relative in (
            *PROMOTION_AUTHORITY_SOURCE_PATHS,
            "config/test-suites.v1.json",
            "src/video2pdf_workflow_kernel/contracts.py",
            "src/video2pdf_workflow_kernel/control_store.py",
            "tests/video_workflow/test_contract_registry_cache.py",
            "tests/video_workflow/test_control_store_v9_fastpath.py",
            MIGRATION.as_posix(),
            AUTHORITY.as_posix(),
        ):
            target = repo / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(PROJECT_ROOT / relative, target)
        for relative in (
            "tests/persisted_command/test_stub.py",
            "scripts/test_stub.py",
            "tests/project_test_runner/test_stub.py",
            ".agents/skills/stub/test_stub.py",
            ".claude/skills/stub/test_stub.py",
        ):
            target = repo / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("# Registry fixture.\n", encoding="utf-8")
        authority = json.loads((repo / AUTHORITY).read_text(encoding="utf-8"))
        authority["authority_sources"] = [
            {
                "path": relative,
                "sha256": hashlib.sha256(
                    (repo / relative).read_bytes()
                ).hexdigest(),
            }
            for relative in PROMOTION_AUTHORITY_SOURCE_PATHS
        ]
        write_json(repo / AUTHORITY, authority)
        baseline_ids = authority["baseline"]["test_ids"]
        delta_ids = authority["authorized_delta"]["test_ids"]
        current_ids = sorted(baseline_ids + delta_ids)
        self.assertEqual(499, len(current_ids))

        baseline_dir = repo / "baseline"
        baseline_status = {
            "schema_name": "persisted-command-status",
            "schema_version": "1.0.0",
            "state": "succeeded",
            "exit_code": 0,
            "elapsed_seconds": 4849.187,
            "security": {
                "classification": "no_secret_detected",
                "acceptance_evidence_eligible": True,
            },
        }
        baseline_status_path = baseline_dir / "status.json"
        baseline_exit_path = baseline_dir / "exit-code.txt"
        baseline_status_sha = write_json(
            baseline_status_path, baseline_status
        )
        baseline_exit_sha = write_bytes(baseline_exit_path, b"0\n")

        focused_dir = repo / "focused"
        focused_status_path = focused_dir / "status.json"
        focused_exit_path = focused_dir / "exit-code.txt"
        focused_command_path = focused_dir / "command.json"
        focused_stderr_path = focused_dir / "stderr.log"
        focused_status_sha = write_json(
            focused_status_path,
            {
                "schema_name": "persisted-command-status",
                "schema_version": "1.0.0",
                "state": "succeeded",
                "exit_code": 0,
                "elapsed_seconds": 1.0,
                "security": {
                    "classification": "no_secret_detected",
                    "acceptance_evidence_eligible": True,
                },
            },
        )
        focused_exit_sha = write_bytes(focused_exit_path, b"0\n")
        focused_command_sha = write_json(
            focused_command_path,
            {
                "schema_name": "persisted-command",
                "schema_version": "1.0.0",
                "accepted_exit_codes": [0],
                "cwd": str(repo),
                "argv": ["python", "-m", "unittest", *SELECTORS],
            },
        )
        focused_stderr_sha = write_bytes(
            focused_stderr_path,
            b"Ran 76 tests in 1.0s\n\nOK\n",
        )
        profile_dir = repo / "profile"
        profile_path = profile_dir / "result.json"
        profile_status_path = profile_dir / "status.json"
        profile_exit_path = profile_dir / "exit-code.txt"
        profile_sha = write_json(
            profile_path,
            {
                "success": True,
                "tests_run": 3,
                "control_store_check_classification": {
                    "full_checks": 807,
                    "memo_hits": 0,
                },
                "timed_calls": {
                    "control_store_lock_probe": {"count": 807}
                },
            },
        )
        profile_status_sha = write_json(
            profile_status_path,
            {
                "schema_name": "persisted-command-status",
                "schema_version": "1.0.0",
                "state": "succeeded",
                "exit_code": 0,
                "elapsed_seconds": 1.0,
                "security": {
                    "classification": "no_secret_detected",
                    "acceptance_evidence_eligible": True,
                },
            },
        )
        profile_exit_sha = write_bytes(profile_exit_path, b"0\n")
        reviews = []
        for axis in ("spec", "standards"):
            path = repo / f"reviews/{axis}.md"
            sha = write_bytes(path, f"{axis}: PASS\n".encode())
            reviews.append(
                {
                    "axis": axis,
                    "status": "PASS",
                    "path": str(path.relative_to(repo)).replace("\\", "/"),
                    "sha256": sha,
                }
            )
        safety_path = repo / (
            "evidence/project-test-runner/"
            "optimization-safety-review.v1.json"
        )
        safety_sha = write_json(
            safety_path,
            {
                "schema_name": (
                    "video2pdf.project-test-optimization-safety-review"
                ),
                "schema_version": 1,
                "issue": 27,
                "reviewed_source_commit": "c" * 40,
                "source_files": [
                    {
                        "path": relative,
                        "sha256": hashlib.sha256(
                            (repo / relative).read_bytes()
                        ).hexdigest(),
                    }
                    for relative in (
                        "src/video2pdf_workflow_kernel/contracts.py",
                        "src/video2pdf_workflow_kernel/control_store.py",
                    )
                ],
                "focused_run": {
                    "persisted_run_dir": str(focused_dir),
                    "status_path": str(focused_status_path),
                    "status_sha256": focused_status_sha,
                    "exit_code_path": str(focused_exit_path),
                    "exit_code_sha256": focused_exit_sha,
                    "command_path": str(focused_command_path),
                    "command_sha256": focused_command_sha,
                    "stderr_path": str(focused_stderr_path),
                    "stderr_sha256": focused_stderr_sha,
                    "test_count": 76,
                    "required_selectors": SELECTORS,
                },
                "health_profile": {
                    "path": str(profile_path),
                    "sha256": profile_sha,
                    "persisted_status_path": str(profile_status_path),
                    "persisted_status_sha256": profile_status_sha,
                    "persisted_exit_code_path": str(profile_exit_path),
                    "persisted_exit_code_sha256": profile_exit_sha,
                },
                "independent_reviews": reviews,
            },
        )

        registry_sha = hashlib.sha256(
            (repo / "config/test-suites.v1.json").read_bytes()
        ).hexdigest()
        runner_sha = hashlib.sha256(
            (repo / "scripts/run_project_tests.py").read_bytes()
        ).hexdigest()
        scheduler_sha = hashlib.sha256(
            (repo / "scripts/project_test_scheduler.py").read_bytes()
        ).hexdigest()
        groups: dict[str, list[str]] = {}
        for test_id in current_ids:
            groups.setdefault(test_id.split(".", 1)[0], []).append(test_id)
        for name in groups:
            source = repo / f"tests/video_workflow/{name}.py"
            if not source.exists():
                source.write_text("# Registry authority fixture.\n", encoding="utf-8")
        restored_live_sources = {
            relative: (repo / relative).read_bytes()
            for relative in (commit_source_overrides or {})
        }
        for relative, content in (commit_source_overrides or {}).items():
            (repo / relative).write_bytes(content)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(
            ["git", "config", "user.name", "Stage 107 Fixture"],
            cwd=repo,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "stage107@example.invalid"],
            cwd=repo,
            check=True,
        )
        subprocess.run(
            ["git", "config", "core.autocrlf", "false"],
            cwd=repo,
            check=True,
        )
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "fixture"],
            cwd=repo,
            check=True,
        )
        implementation_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout.strip()
        source_manifest = build_execution_source_manifest(
            repo,
            [
                f"tests/video_workflow/{name}.py"
                for name in sorted(groups)
            ],
        )

        def execution_identity(
            pid: int,
            creation: int,
            parent_pid: int,
            parent_creation: int,
        ) -> dict:
            identity = {
                "pid": pid,
                "process_creation_identity": f"windows-filetime:{creation}",
                "executable_path": r"C:\Python\python.exe",
                "executable_file_identity": {
                    "device": 1,
                    "inode": 10_000 + pid,
                    "size": 1024,
                    "mtime_ns": 1_342_968_800_000_000_000,
                },
                "parent_pid": parent_pid,
                "parent_process_creation_identity": (
                    f"windows-filetime:{parent_creation}"
                ),
            }
            identity["observation_sha256"] = hashlib.sha256(
                canonical_json_bytes(identity)
            ).hexdigest()
            return identity
        baseline_inventory_path = FINAL_ISSUE9_DISCOVERY_PATH
        self.assertTrue(baseline_inventory_path.is_file())
        baseline_inventory_sha = hashlib.sha256(
            baseline_inventory_path.read_bytes()
        ).hexdigest()
        self.assertEqual(
            FINAL_ISSUE9_DISCOVERY_SHA256, baseline_inventory_sha
        )
        runs = []
        run_fingerprint_inputs = []
        semantic_sha = ""
        assignment_sha = ""
        external_root = repo / "external"
        project_root = external_root / "video2pdf"
        write_json(
            project_root / "project.json",
            {
                "schema_name": "external-test-project",
                "schema_version": "1.0.0",
                "project_key": "video2pdf",
                "repository": "Nishijujuba/video2pdf",
                "remote_identity": "github.com/Nishijujuba/video2pdf",
            },
        )
        for index in (1, 2):
            persisted_run_nonce = f"{index:x}" * 64
            created_at = f"2026-07-28T05:00:0{index}.000+00:00"
            finished_at = f"2026-07-28T05:16:4{index}.000+00:00"
            updated_at = f"2026-07-28T05:16:4{index}.001+00:00"
            run_id = (
                f"{index}{index}{index}{index}{index}{index}{index}{index}"
                f"-1111-4111-8111-{index}" + "1" * 11
            )
            run_dir = (
                project_root
                / "video-workflow"
                / f"20260728_04000{index}_{index:08x}"
            )
            source_manifest_path = run_dir / "execution-source.json"
            freeze_execution_source_files(repo, run_dir, source_manifest)
            create_frozen_git_authority(
                repo,
                run_dir,
                run_dir / "execution-source-files",
                repo / ".git",
                source_manifest,
            )
            source_manifest_sha = write_json(
                source_manifest_path,
                source_manifest,
            )
            supervisor_creation = 134296884000000000 + index
            runner_creation = 134296885000000000 + index
            supervisor_identity = execution_identity(
                8000 + index,
                supervisor_creation,
                5000 + index,
                134296882000000000 + index,
            )
            runner_identity = execution_identity(
                7000 + index,
                runner_creation,
                supervisor_identity["pid"],
                supervisor_creation,
            )
            discovery_launcher_identity = execution_identity(
                6000 + index,
                134296883000000000 + index,
                runner_identity["pid"],
                runner_creation,
            )
            discovery_self_identity = execution_identity(
                6100 + index,
                134296883500000000 + index,
                discovery_launcher_identity["pid"],
                134296883000000000 + index,
            )
            modules = [
                {
                    "module_key": hashlib.sha256(
                        (
                            "video-workflow\0"
                            f"tests/video_workflow/{name}.py"
                        ).encode("utf-8")
                    ).hexdigest()[:12],
                    "root_path": "tests/video_workflow",
                    "source_path": f"tests/video_workflow/{name}.py",
                    "suite_id": "video-workflow",
                    "test_count": len(ids),
                    "test_ids": sorted(ids),
                }
                for name, ids in sorted(groups.items())
            ]
            discovery_command = [
                discovery_launcher_identity["executable_path"],
                "-X",
                "utf8",
                "-B",
                "-m",
                "scripts.project_test_discovery",
                "--repo-root",
                str(run_dir / "execution-source-files"),
                "--registry",
                str(
                    run_dir
                    / "execution-source-files"
                    / "config/test-suites.v1.json"
                ),
                "--destination",
                str(run_dir / "discovery.json"),
                "--commit",
                implementation_commit,
                "--launcher-binding-stdin",
                "--suite",
                "video-workflow",
            ]
            discovery_process = {
                "relationship": "launcher_child",
                "command": discovery_command,
                "launcher_identity": discovery_launcher_identity,
                "self_identity": discovery_self_identity,
            }
            discovery = {
                "schema_name": "video2pdf.project-test-discovery",
                "schema_version": 1,
                "project": {
                    "project_key": "video2pdf",
                    "repository": "Nishijujuba/video2pdf",
                },
                "commit": implementation_commit,
                "registry_path": "config/test-suites.v1.json",
                "suite_ids": ["video-workflow"],
                "discovery_arguments": {
                    "suite_ids": ["video-workflow"]
                },
                "suites": [
                    {
                        "suite_id": "video-workflow",
                        "suite_key": "video-workflow",
                        "roots": [
                            {
                                "path": "tests/video_workflow",
                                "pattern": "test_*.py",
                            }
                        ],
                    }
                ],
                "total_count": 499,
                "duplicate_test_ids": [],
                "test_id_set_sha256": CURRENT_TEST_ID_SET_SHA256,
                "registry_sha256": registry_sha,
                "modules": modules,
                "discovery_process": discovery_process,
            }
            source_snapshot, source_snapshot_sha = create_source_snapshot(
                repo,
                run_dir,
                run_dir / "execution-source-files",
                source_manifest_path=source_manifest_path,
                source_manifest_sha256=source_manifest_sha,
                source_manifest=source_manifest,
                expected_test_module_paths=[
                    module["source_path"] for module in modules
                ],
                project=discovery["project"],
                registry_sha256=registry_sha,
                project_marker_sha256=hashlib.sha256(
                    (project_root / "project.json").read_bytes()
                ).hexdigest(),
                persisted_run_id=run_id,
                persisted_run_nonce=persisted_run_nonce,
                runner_identity=runner_identity,
                modules=modules,
            )
            outcomes = [
                {"test_id": test_id, "status": "passed"}
                for test_id in current_ids
            ]
            summary_modules = [
                {
                    "module_key": module["module_key"],
                    "suite_id": module["suite_id"],
                    "source_path": module["source_path"],
                    "test_ids": module["test_ids"],
                    "executions": [
                        {"test_id": test_id, "status": "passed"}
                        for test_id in module["test_ids"]
                    ],
                    "failure_kind": None,
                    "detail": None,
                    "exit_code": 0,
                    "assignment_sha256": "",
                    "result_sha256": "",
                    "stdout_sha256": "",
                    "stderr_sha256": "",
                }
                for module in modules
            ]
            events = []
            sequence = 0
            for module in modules:
                sequence += 1
                events.append(
                    {
                        "sequence": sequence,
                        "event": "queued",
                        "module_key": module["module_key"],
                        "suite_id": module["suite_id"],
                        "source_path": module["source_path"],
                        "time_unix_ns": index * 1_000_000 + sequence,
                    }
                )
            for module, summary_module in zip(modules, summary_modules):
                key = module["module_key"]
                worker_launch_nonce = hashlib.sha256(
                    f"{index}:{key}:worker".encode("utf-8")
                ).hexdigest()
                worker_pid = (
                    index * 10_000 + int(key[:6], 16) % 8_000 + 1
                )
                launcher_creation = (
                    134296885500000000
                    + index * 1_000_000
                    + int(key[:8], 16)
                )
                worker_launcher_identity = execution_identity(
                    worker_pid + 100_000,
                    launcher_creation,
                    runner_identity["pid"],
                    runner_creation,
                )
                worker_identity = execution_identity(
                    worker_pid,
                    (
                        134296886000000000
                        + index * 1_000_000
                        + int(key[:8], 16)
                    ),
                    worker_launcher_identity["pid"],
                    launcher_creation,
                )
                assignment = {
                    "schema_name": (
                        "video2pdf.project-test-module-assignment"
                    ),
                    "schema_version": 2,
                    "repo_root": str(repo),
                    "execution_root": str(
                        run_dir / "execution-source-files"
                    ),
                    "module_key": key,
                    "suite_id": module["suite_id"],
                    "source_path": module["source_path"],
                    "test_ids": module["test_ids"],
                    "worker_launch_nonce": worker_launch_nonce,
                    "source_manifest_sha256": source_manifest_sha,
                    "source_snapshot_id": source_snapshot[
                        "source_snapshot_id"
                    ],
                    "source_snapshot_sha256": source_snapshot_sha,
                    "module_inventory_sha256": source_snapshot[
                        "module_inventory"
                    ]["sha256"],
                    "source_sha256": next(
                        item["runtime_sha256"]
                        for item in source_manifest["entries"]
                        if item["path"] == module["source_path"]
                    ),
                }
                summary_module["assignment_sha256"] = write_json(
                    run_dir / "modules" / f"{key}.assignment.json",
                    assignment,
                )
                result = {
                    "schema_name": "video2pdf.project-test-module-result",
                    "schema_version": 2,
                    "module_key": key,
                    "suite_id": module["suite_id"],
                    "source_path": module["source_path"],
                    "assigned_test_ids": module["test_ids"],
                    "worker_launch_nonce": worker_launch_nonce,
                    "source_manifest_sha256": source_manifest_sha,
                    "source_snapshot_id": source_snapshot[
                        "source_snapshot_id"
                    ],
                    "source_snapshot_sha256": source_snapshot_sha,
                    "worker_identity": worker_identity,
                    "executions": [
                        {
                            "test_id": test_id,
                            "status": "passed",
                            "duration_seconds": 0.001,
                        }
                        for test_id in module["test_ids"]
                    ],
                    "failure_kind": None,
                    "exit_code": 0,
                    "duration_seconds": 0.01,
                }
                summary_module["result_sha256"] = write_json(
                    run_dir / "modules" / f"{key}.result.json",
                    result,
                )
                summary_module["stdout_sha256"] = write_bytes(
                    run_dir / "logs" / f"{key}.stdout.log", b""
                )
                summary_module["stderr_sha256"] = write_bytes(
                    run_dir / "logs" / f"{key}.stderr.log",
                    f"{module['source_path']}: OK\n".encode(),
                )
                summary_module["worker_launch_nonce"] = worker_launch_nonce
                summary_module["worker_identity"] = worker_identity
                summary_module["worker_launcher_identity"] = (
                    worker_launcher_identity
                )
                summary_module["source_manifest_sha256"] = (
                    source_manifest_sha
                )
                summary_module["source_snapshot_id"] = source_snapshot[
                    "source_snapshot_id"
                ]
                summary_module["source_snapshot_sha256"] = source_snapshot_sha
                summary_module["artifact_identities"] = {
                    "assignment": file_artifact_identity(
                        run_dir / "modules" / f"{key}.assignment.json"
                    ),
                    "result": file_artifact_identity(
                        run_dir / "modules" / f"{key}.result.json"
                    ),
                    "stdout": file_artifact_identity(
                        run_dir / "logs" / f"{key}.stdout.log"
                    ),
                    "stderr": file_artifact_identity(
                        run_dir / "logs" / f"{key}.stderr.log"
                    ),
                }
            for offset in range(0, len(modules), 4):
                wave = modules[offset : offset + 4]
                for module in wave:
                    summary_module = next(
                        item
                        for item in summary_modules
                        if item["module_key"] == module["module_key"]
                    )
                    sequence += 1
                    events.append(
                        {
                            "sequence": sequence,
                            "event": "started",
                            "module_key": module["module_key"],
                            "suite_id": module["suite_id"],
                            "source_path": module["source_path"],
                            "time_unix_ns": index * 1_000_000 + sequence,
                            "worker_launcher_identity": summary_module[
                                "worker_launcher_identity"
                            ],
                            "worker_launch_nonce": summary_module[
                                "worker_launch_nonce"
                            ],
                            "source_manifest_sha256": source_manifest_sha,
                            "artifact_identities": {
                                "assignment": summary_module[
                                    "artifact_identities"
                                ]["assignment"]
                            },
                        }
                    )
                for module in wave:
                    summary_module = next(
                        item
                        for item in summary_modules
                        if item["module_key"] == module["module_key"]
                    )
                    sequence += 1
                    events.append(
                        {
                            "sequence": sequence,
                            "event": "completed",
                            "module_key": module["module_key"],
                            "suite_id": module["suite_id"],
                            "source_path": module["source_path"],
                            "time_unix_ns": index * 1_000_000 + sequence,
                            "failure_kind": None,
                            "exit_code": 0,
                            "worker_identity": summary_module[
                                "worker_identity"
                            ],
                            "worker_launcher_identity": summary_module[
                                "worker_launcher_identity"
                            ],
                            "worker_launch_nonce": summary_module[
                                "worker_launch_nonce"
                            ],
                            "source_manifest_sha256": source_manifest_sha,
                            "artifact_identities": summary_module[
                                "artifact_identities"
                            ],
                        }
                    )
            summary = {
                "schema_name": "video2pdf.project-test-summary",
                "schema_version": 2,
                "project": {
                    "project_key": "video2pdf",
                    "repository": "Nishijujuba/video2pdf",
                },
                "commit": implementation_commit,
                "suite_ids": ["video-workflow"],
                "requested_jobs": 4,
                "observed_peak_concurrency": 4,
                "success": True,
                "failure_kind": None,
                "source_snapshot_id": source_snapshot[
                    "source_snapshot_id"
                ],
                "source_snapshot_sha256": source_snapshot_sha,
                "coverage": {
                    "discovered": 499,
                    "assigned": 499,
                    "started": 499,
                    "terminal": 499,
                    "module_count": len(modules),
                    "executed_test_ids": 499,
                    "missing_test_ids": [],
                    "duplicate_test_ids": [],
                    "unassigned_test_ids": [],
                    "multiply_executed_test_ids": [],
                },
                "modules": summary_modules,
            }
            discovery_path = run_dir / "discovery.json"
            summary_path = run_dir / "summary.json"
            discovery_sha = write_json(discovery_path, discovery)
            summary_sha = write_json(summary_path, summary)
            for event in events:
                event["source_snapshot_id"] = source_snapshot[
                    "source_snapshot_id"
                ]
                event["source_snapshot_sha256"] = source_snapshot_sha
            events_bytes = b"".join(
                canonical_json_bytes(event) for event in events
            )
            events_sha = write_bytes(
                run_dir / "events.jsonl", events_bytes
            )
            timings_sha = write_json(
                run_dir / "timings.json",
                {
                    "schema_name": "video2pdf.project-test-timings",
                    "schema_version": 2,
                    "project": {
                        "project_key": "video2pdf",
                        "repository": "Nishijujuba/video2pdf",
                    },
                    "commit": implementation_commit,
                    "suite_ids": ["video-workflow"],
                    "modules": [
                        {
                            "module_key": module["module_key"],
                            "source_path": module["source_path"],
                            "duration_seconds": 0.01,
                        }
                        for module in modules
                    ],
                },
            )
            run_finalization_sha = write_json(
                run_dir / "run-finalization.json",
                {
                    "schema_name": (
                        "video2pdf.project-test-run-finalization"
                    ),
                    "schema_version": 1,
                    "source_snapshot_id": source_snapshot[
                        "source_snapshot_id"
                    ],
                    "source_snapshot_sha256": source_snapshot_sha,
                    "source_manifest_sha256": source_manifest_sha,
                    "summary_sha256": summary_sha,
                    "scheduler_success": True,
                    "scheduler_failure_kind": None,
                    "postvalidation": {
                        "result": "passed",
                        "source_manifest_sha256": source_manifest_sha,
                        "detail": None,
                    },
                    "success": True,
                    "failure_kind": None,
                },
            )
            test_run_sha = write_json(
                run_dir / "test-run.json",
                {
                    "schema_name": "video2pdf.project-test-run",
                    "schema_version": 2,
                    "command": "run",
                    "project": {
                        "project_key": "video2pdf",
                        "repository": "Nishijujuba/video2pdf",
                    },
                    "commit": implementation_commit,
                    "registry_sha256": registry_sha,
                    "discovery_sha256": discovery_sha,
                    "source_manifest_path": str(source_manifest_path),
                    "source_manifest_sha256": source_manifest_sha,
                    "source_snapshot_path": str(
                        run_dir / "source-snapshot.json"
                    ),
                    "source_snapshot_id": source_snapshot[
                        "source_snapshot_id"
                    ],
                    "source_snapshot_sha256": source_snapshot_sha,
                    "suite_ids": ["video-workflow"],
                    "run_dir": str(run_dir),
                    "project_marker_sha256": hashlib.sha256(
                        (project_root / "project.json").read_bytes()
                    ).hexdigest(),
                    "persisted_run_id": run_id,
                    "persisted_run_nonce": persisted_run_nonce,
                    "persisted_target_identity": runner_identity,
                    "persisted_supervisor_identity": supervisor_identity,
                    "requested_jobs": 4,
                    "timings_from": None,
                    "runner_identity": runner_identity,
                    "discovery_process": {
                        **discovery_process,
                        "exit_code": 0,
                    },
                },
            )
            assignment_sha = hashlib.sha256(
                canonical_json_bytes(module_assignment(discovery))
            ).hexdigest()
            semantic_sha = hashlib.sha256(
                canonical_json_bytes(outcomes)
            ).hexdigest()
            persisted_dir = (
                repo
                / (
                    "promotion_v2_"
                    f"20260728_05000{index}_{run_id[:8]}"
                )
            )
            status_path = persisted_dir / "status.json"
            exit_path = persisted_dir / "exit-code.txt"
            command_path = persisted_dir / "command.json"
            stdout_path = persisted_dir / "stdout.log"
            stderr_path = persisted_dir / "stderr.log"
            merged_log_path = persisted_dir / "command.log"
            supervisor_launch_path = (
                persisted_dir / "supervisor-identity.json"
            )
            write_json(
                supervisor_launch_path,
                {
                    "schema_name": (
                        "persisted-command-supervisor-identity"
                    ),
                    "schema_version": "1.0.0",
                    "run_id": run_id,
                    "recorded_at": created_at,
                    "supervisor_pid": supervisor_identity["pid"],
                    "execution_identity": supervisor_identity,
                },
            )
            scheduling_record = {
                "event": "project_test_scheduling_started",
                "run_dir": str(run_dir),
                "discovery_sha256": discovery_sha,
                "total_count": 499,
                "discovery_process": {
                    **discovery_process,
                    "exit_code": 0,
                },
            }
            completion_record = {
                    "event": "project_test_run_complete",
                    "success": True,
                    "failure_kind": None,
                    "run_dir": str(run_dir),
                    "discovery_sha256": discovery_sha,
                    "summary_sha256": summary_sha,
                    "discovery_process": {
                        **discovery_process,
                        "exit_code": 0,
                    },
            }
            stdout_bytes = (
                canonical_json_bytes(scheduling_record)
                + canonical_json_bytes(completion_record)
            )
            stdout_sha = write_bytes(stdout_path, stdout_bytes)
            write_bytes(stderr_path, b"")
            write_bytes(
                merged_log_path,
                f"[stdout {len(stdout_bytes)}]\n".encode("ascii")
                + stdout_bytes,
            )
            status_sha = write_json(
                status_path,
                {
                    "schema_name": "persisted-command-status",
                    "schema_version": "1.0.0",
                    "run_id": run_id,
                    "run_nonce": persisted_run_nonce,
                    "state": "succeeded",
                    "exit_code": 0,
                    "elapsed_seconds": 1000.0 + index,
                    "started_at": created_at,
                    "finished_at": finished_at,
                    "updated_at": updated_at,
                    "heartbeat_at": updated_at,
                    "latest_output_at": finished_at,
                    "log_sizes": {
                        "stdout": stdout_path.stat().st_size,
                        "stderr": stderr_path.stat().st_size,
                        "merged": merged_log_path.stat().st_size,
                    },
                    "child_pid": 7000 + index,
                    "supervisor_pid": 8000 + index,
                    "target_identity": runner_identity,
                    "supervisor_identity": supervisor_identity,
                    "security": {
                        "classification": "no_secret_detected",
                        "acceptance_evidence_eligible": True,
                    },
                },
            )
            exit_sha = write_bytes(exit_path, b"0\n")
            command_sha = write_json(
                command_path,
                {
                    "schema_name": "persisted-command",
                    "schema_version": "1.0.0",
                    "run_id": run_id,
                    "run_nonce": persisted_run_nonce,
                    "task_name": "promotion_v2",
                    "normalized_task_name": "promotion_v2",
                    "created_at": created_at,
                    "accepted_exit_codes": [0],
                    "cwd": str(repo),
                    "argv": [
                        "python.exe",
                        "-X",
                        "utf8",
                        "-B",
                        str(repo / "scripts/run_project_tests.py"),
                        "run",
                        "--suite",
                        "video-workflow",
                        "--jobs",
                        "4",
                        "--test-root",
                        str(external_root),
                    ],
                },
            )
            persisted_status = json.loads(
                status_path.read_text(encoding="utf-8")
            )
            persisted_status["artifact_identities"] = {
                "command": file_artifact_identity(command_path),
                "supervisor_launch": file_artifact_identity(
                    supervisor_launch_path
                ),
                "stdout": file_artifact_identity(stdout_path),
                "stderr": file_artifact_identity(stderr_path),
                "merged": file_artifact_identity(merged_log_path),
                "exit_code": file_artifact_identity(exit_path),
            }
            status_sha = write_json(status_path, persisted_status)
            runs.append(
                {
                    "run_dir": str(run_dir),
                    "discovery_path": str(discovery_path),
                    "discovery_sha256": discovery_sha,
                    "summary_path": str(summary_path),
                    "summary_sha256": summary_sha,
                    "persisted_run_dir": str(persisted_dir),
                    "persisted_status_path": str(status_path),
                    "persisted_status_sha256": status_sha,
                    "persisted_exit_code_path": str(exit_path),
                    "persisted_exit_code_sha256": exit_sha,
                    "persisted_command_path": str(command_path),
                    "persisted_command_sha256": command_sha,
                    "persisted_stdout_path": str(stdout_path),
                    "persisted_stdout_sha256": stdout_sha,
                    "semantic_outcomes_sha256": semantic_sha,
                    "module_assignment_sha256": assignment_sha,
                    "registry_sha256": registry_sha,
                }
            )
            marker_sha = hashlib.sha256(
                (project_root / "project.json").read_bytes()
            ).hexdigest()
            run_fingerprint_inputs.append(
                {
                    "marker_sha256": marker_sha,
                    "test_run_sha256": test_run_sha,
                    "events_sha256": events_sha,
                    "timings_sha256": timings_sha,
                    "persisted_run_id": run_id,
                    "target_process_identity": (
                        f"windows-filetime:13429688500000000{index}"
                    ),
                    "persisted_run_nonce": persisted_run_nonce,
                    "source_manifest_sha256": source_manifest_sha,
                    "source_snapshot_id": source_snapshot[
                        "source_snapshot_id"
                    ],
                    "source_snapshot_sha256": source_snapshot_sha,
                    "run_finalization_sha256": run_finalization_sha,
                    "worker_identity_lineage_sha256": hashlib.sha256(
                        canonical_json_bytes(
                            [
                                {
                                    "module_key": item["module_key"],
                                    "worker_identity": item[
                                        "worker_identity"
                                    ],
                                    "worker_launcher_identity": item[
                                        "worker_launcher_identity"
                                    ],
                                    "worker_launch_nonce": item[
                                        "worker_launch_nonce"
                                    ],
                                }
                                for item in sorted(
                                    summary_modules,
                                    key=lambda value: value["module_key"],
                                )
                            ]
                        )
                    ).hexdigest(),
                }
            )
        safety = json.loads(safety_path.read_text(encoding="utf-8"))
        safety["reviewed_source_commit"] = implementation_commit
        safety_sha = write_json(safety_path, safety)
        migration_sha = hashlib.sha256((repo / MIGRATION).read_bytes()).hexdigest()
        authority_sha = hashlib.sha256((repo / AUTHORITY).read_bytes()).hexdigest()
        report = {
            "schema_name": "video2pdf.project-test-promotion-report",
            "schema_version": 2,
            "issue": 27,
            "historical_performance_baseline": {
                "implementation_commit": (
                    "18f78fad0be5a66d2da6250dc268bc8de81fdbcc"
                ),
                "test_count": 474,
                "result": "OK",
                "test_duration_seconds": 4847.218,
                "persisted_elapsed_seconds": 4849.187,
                "persisted_run_dir": str(baseline_dir),
                "persisted_status_path": str(baseline_status_path),
                "persisted_status_sha256": baseline_status_sha,
                "persisted_exit_code_path": str(baseline_exit_path),
                "persisted_exit_code_sha256": baseline_exit_sha,
            },
            "final_issue9_closed_set": {
                "commit": authority["baseline"]["commit"],
                "test_count": 475,
                "test_id_set_sha256": BASELINE_TEST_ID_SET_SHA256,
                "test_ids": baseline_ids,
                "evidence_path": MIGRATION.as_posix(),
                "evidence_sha256": migration_sha,
                "inventory_path": str(baseline_inventory_path),
                "inventory_sha256": baseline_inventory_sha,
            },
            "superset_authority": {
                "path": AUTHORITY.as_posix(),
                "sha256": authority_sha,
            },
            "implementation": {
                "reviewed_implementation_commit": implementation_commit,
                "execution_evidence_commit": implementation_commit,
                "authority_sources": authority["authority_sources"],
                "registry_path": "config/test-suites.v1.json",
                "registry_sha256": registry_sha,
                "runner_path": "scripts/run_project_tests.py",
                "runner_sha256": runner_sha,
                "scheduler_path": "scripts/project_test_scheduler.py",
                "scheduler_sha256": scheduler_sha,
            },
            "promotion_closed_set": {
                "suite_ids": ["video-workflow"],
                "test_count": 499,
                "test_id_set_sha256": CURRENT_TEST_ID_SET_SHA256,
                "baseline_test_count": 475,
                "baseline_test_id_set_sha256": BASELINE_TEST_ID_SET_SHA256,
                "added_test_count": 24,
                "added_test_id_set_sha256": AUTHORIZED_DELTA_TEST_ID_SET_SHA256,
                "removed_test_count": 0,
                "renamed_test_count": 0,
            },
            "parallel_runs": runs,
            "semantic_parity": {
                "passed": True,
                "test_id_set_sha256": CURRENT_TEST_ID_SET_SHA256,
                "semantic_outcomes_sha256": semantic_sha,
                "module_assignment_sha256": assignment_sha,
                "ignored_fields": [
                    "timestamps",
                    "pids",
                    "durations",
                    "completion_order",
                ],
            },
            "migration_review": {
                "path": MIGRATION.as_posix(),
                "sha256": migration_sha,
                "passed": True,
            },
            "optimization_safety_review": {
                "path": str(safety_path.relative_to(repo)).replace("\\", "/"),
                "sha256": safety_sha,
                "passed": True,
            },
            "performance": {
                "maximum_elapsed_seconds": 1800,
                "passed": True,
            },
            "authorization_model": {
                "decision_semantics": (
                    "local-fail-closed-non-cryptographic-v1"
                ),
                "cryptographic_provenance": False,
                "path_identity_required": True,
                "unproved_path_identity_authorizes": False,
                "persisted_record_contract": (
                    "persisted-command-v1.0.0-current-success-shape"
                ),
                "execution_source_contract": (
                    "clean-git-tree-plus-frozen-runtime-bytes-v1"
                ),
                "process_identity_contract": (
                    "pid-creation-executable-parent-file-identity-v1"
                ),
                "local_attacker_limit": (
                    "an arbitrary local writer can forge the worktree, frozen "
                    "source, and every unsigned evidence artifact"
                ),
            },
            "cutover_authorized": True,
        }
        report["promotion_fingerprint"] = hashlib.sha256(
            canonical_json_bytes(
                {
                    "schema_version": 2,
                    "authorization_model": report["authorization_model"],
                    "reviewed_implementation_commit": implementation_commit,
                    "execution_evidence_commit": implementation_commit,
                    "historical_baseline_commit": (
                        "18f78fad0be5a66d2da6250dc268bc8de81fdbcc"
                    ),
                    "historical_status_sha256": baseline_status_sha,
                    "historical_exit_code_sha256": baseline_exit_sha,
                    "final_issue9_commit": authority["baseline"]["commit"],
                    "final_issue9_inventory_sha256": (
                        baseline_inventory_sha
                    ),
                    "baseline_test_id_set_sha256": (
                        BASELINE_TEST_ID_SET_SHA256
                    ),
                    "authorized_delta_test_id_set_sha256": (
                        AUTHORIZED_DELTA_TEST_ID_SET_SHA256
                    ),
                    "current_test_id_set_sha256": CURRENT_TEST_ID_SET_SHA256,
                    "superset_authority_sha256": authority_sha,
                    "authority_sources": authority["authority_sources"],
                    "registry_sha256": registry_sha,
                    "runner_sha256": runner_sha,
                    "scheduler_sha256": scheduler_sha,
                    "parallel_runs": [
                        {
                            **{
                                key: run[key]
                                for key in (
                                    "discovery_sha256",
                                    "summary_sha256",
                                    "persisted_status_sha256",
                                    "persisted_exit_code_sha256",
                                    "persisted_command_sha256",
                                    "persisted_stdout_sha256",
                                    "semantic_outcomes_sha256",
                                    "module_assignment_sha256",
                                )
                            },
                            **run_fingerprint_inputs[index],
                        }
                        for index, run in enumerate(runs)
                    ],
                    "migration_review_sha256": migration_sha,
                    "optimization_safety_review_sha256": safety_sha,
                }
            )
        ).hexdigest()
        for relative, content in restored_live_sources.items():
            (repo / relative).write_bytes(content)
        write_json(
            repo / "evidence/project-test-runner/promotion-report.json",
            report,
        )
        return repo, report

    def test_v2_rejects_commit_and_frozen_authority_source_from_other_bytes(
        self,
    ) -> None:
        for relative in (
            "tests/video_workflow/test_contract_registry_cache.py",
            "src/video2pdf_workflow_kernel/contracts.py",
        ):
            with self.subTest(path=relative):
                approved = (PROJECT_ROOT / relative).read_bytes()
                repo, _report = self.make_report(
                    commit_source_overrides={
                        relative: approved
                        + b"\n# source bytes from an unreviewed commit\n",
                    },
                )
                with self.assertRaisesRegex(
                    PromotionValidationError,
                    "authority source|implementation commit|reviewed source",
                ):
                    validate_promotion_report(repo)

    def test_v2_rejects_safety_review_for_another_commit(self) -> None:
        repo, report = self.make_report()
        safety_path = (
            repo
            / "evidence/project-test-runner/"
            "optimization-safety-review.v1.json"
        )
        safety = json.loads(safety_path.read_text(encoding="utf-8"))
        safety["reviewed_source_commit"] = "d" * 40
        report["optimization_safety_review"]["sha256"] = write_json(
            safety_path,
            safety,
        )
        write_json(
            repo / "evidence/project-test-runner/promotion-report.json",
            report,
        )
        with self.assertRaisesRegex(
            PromotionValidationError,
            "optimization safety review identity",
        ):
            validate_promotion_report(repo)

    def test_valid_v2_report_proves_exact_475_plus_24_and_two_runs(self) -> None:
        repo, report = self.make_report()
        schema = json.loads(
            (
                repo / "schemas/project-test-promotion-report.v2.schema.json"
            ).read_text(encoding="utf-8")
        )
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(report)
        result = validate_promotion_report(repo)
        self.assertTrue(result["valid"])
        self.assertEqual(2, result["schema_version"])
        self.assertEqual(499, result["test_count"])

    def test_dual_commit_range_allows_evidence_only_descendants(self) -> None:
        repo = new_fixture_dir("promotion-dual-commit-range")
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(
            ["git", "config", "user.name", "Dual Commit Fixture"],
            cwd=repo,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "dual@example.invalid"],
            cwd=repo,
            check=True,
        )
        implementation = repo / "implementation.py"
        implementation.write_text("VALUE = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "implementation"],
            cwd=repo,
            check=True,
        )
        reviewed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout.strip()
        evidence = (
            repo
            / "evidence/project-test-runner/"
            "optimization-safety-review.v1.json"
        )
        evidence.parent.mkdir(parents=True)
        evidence.write_text("{}\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "evidence"],
            cwd=repo,
            check=True,
        )
        execution = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout.strip()

        promotion_validator._validate_evidence_only_commit_range(
            repo,
            reviewed,
            execution,
            label="fixture",
        )

        implementation.write_text("VALUE = 2\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "unreviewed source"],
            cwd=repo,
            check=True,
        )
        unreviewed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout.strip()
        with self.assertRaisesRegex(
            PromotionValidationError,
            "non-evidence paths",
        ):
            promotion_validator._validate_evidence_only_commit_range(
                repo,
                execution,
                unreviewed,
                label="fixture",
            )

    def test_v2_rejects_each_missing_current_command_field(self) -> None:
        for field in ("created_at", "normalized_task_name", "task_name"):
            with self.subTest(field=field):
                repo, report = self.make_report()
                run = report["parallel_runs"][0]
                path = Path(run["persisted_command_path"])
                command = json.loads(path.read_text(encoding="utf-8"))
                command.pop(field)
                run["persisted_command_sha256"] = write_json(path, command)
                write_json(
                    repo
                    / "evidence/project-test-runner/promotion-report.json",
                    report,
                )
                with self.assertRaisesRegex(
                    PromotionValidationError, f"missing field: {field}"
                ):
                    validate_promotion_report(repo)

    def test_v2_rejects_each_missing_current_success_status_field(
        self,
    ) -> None:
        for field in (
            "finished_at",
            "heartbeat_at",
            "latest_output_at",
            "log_sizes",
            "started_at",
            "updated_at",
        ):
            with self.subTest(field=field):
                repo, report = self.make_report()
                run = report["parallel_runs"][0]
                path = Path(run["persisted_status_path"])
                status = json.loads(path.read_text(encoding="utf-8"))
                status.pop(field)
                run["persisted_status_sha256"] = write_json(path, status)
                write_json(
                    repo
                    / "evidence/project-test-runner/promotion-report.json",
                    report,
                )
                with self.assertRaisesRegex(
                    PromotionValidationError, f"missing field: {field}"
                ):
                    validate_promotion_report(repo)

    def test_v2_rejects_fake_current_command_values(self) -> None:
        mutations = {
            "created_at": lambda value: value.__setitem__(
                "created_at", "2026-07-28T05:00:01.000000+00:00"
            ),
            "task_name": lambda value: value.__setitem__("task_name", 7),
            "run_id": lambda value: value.__setitem__("run_id", None),
            "run_nonce": lambda value: value.__setitem__("run_nonce", False),
            "normalized_task_name": lambda value: value.__setitem__(
                "normalized_task_name", "forged"
            ),
            "self_consistent_forged_task_identity": lambda value: value.update(
                {
                    "task_name": "promotion",
                    "normalized_task_name": "promotion",
                }
            ),
        }
        for field, mutate in mutations.items():
            with self.subTest(field=field):
                repo, report = self.make_report()
                run = report["parallel_runs"][0]
                path = Path(run["persisted_command_path"])
                command = json.loads(path.read_text(encoding="utf-8"))
                mutate(command)
                run["persisted_command_sha256"] = write_json(path, command)
                write_json(
                    repo
                    / "evidence/project-test-runner/promotion-report.json",
                    report,
                )
                with self.assertRaisesRegex(
                    PromotionValidationError,
                    "timestamp|task identity|run identity",
                ):
                    validate_promotion_report(repo)

    def test_current_run_directory_identity_allows_runner_unicode_shape(
        self,
    ) -> None:
        self.assertTrue(
            promotion_validator._persisted_run_directory_matches(
                "测试 Task_20260728_050001_11111111",
                "测试 Task",
                "11111111-1111-4111-8111-111111111111",
            )
        )

    def test_v2_rejects_fake_current_success_status_values(self) -> None:
        mutations = {
            "finished_at": lambda value: value.__setitem__(
                "finished_at", "2026-07-28T05:16:41.000000+00:00"
            ),
            "heartbeat_at": lambda value: value.__setitem__(
                "heartbeat_at", 1
            ),
            "latest_output_at": lambda value: value.__setitem__(
                "latest_output_at", False
            ),
            "exit_code": lambda value: value.__setitem__("exit_code", False),
            "log_sizes": lambda value: value["log_sizes"].__setitem__(
                "stdout", True
            ),
            "started_at": lambda value: value.__setitem__("started_at", None),
            "updated_at": lambda value: value.__setitem__(
                "updated_at", "2026-07-28T05:16:41"
            ),
        }
        for field, mutate in mutations.items():
            with self.subTest(field=field):
                repo, report = self.make_report()
                run = report["parallel_runs"][0]
                path = Path(run["persisted_status_path"])
                status = json.loads(path.read_text(encoding="utf-8"))
                mutate(status)
                run["persisted_status_sha256"] = write_json(path, status)
                write_json(
                    repo
                    / "evidence/project-test-runner/promotion-report.json",
                    report,
                )
                with self.assertRaisesRegex(
                    PromotionValidationError,
                    "timestamp|UTC offset|log_sizes|exit_code",
                ):
                    validate_promotion_report(repo)

    def test_v2_rejects_impossible_current_success_timeline(self) -> None:
        mutations = {
            "started-does-not-match-created": lambda value: value.__setitem__(
                "started_at", "2026-07-28T05:00:00.999+00:00"
            ),
            "finished-before-started": lambda value: value.__setitem__(
                "finished_at", "2026-07-28T04:59:59.999+00:00"
            ),
            "latest-before-started": lambda value: value.__setitem__(
                "latest_output_at", "2026-07-28T04:59:59.999+00:00"
            ),
            "latest-after-updated": lambda value: value.__setitem__(
                "latest_output_at", "2026-07-28T05:17:00.000+00:00"
            ),
            "latest-after-finished": lambda value: value.update(
                {
                    "latest_output_at": "2026-07-28T05:16:41.001+00:00",
                    "updated_at": "2026-07-28T05:16:41.002+00:00",
                    "heartbeat_at": "2026-07-28T05:16:41.002+00:00",
                }
            ),
            "missing-latest-for-nonempty-log": lambda value: value.__setitem__(
                "latest_output_at", None
            ),
            "heartbeat-does-not-match-updated": (
                lambda value: value.__setitem__(
                    "heartbeat_at", "2026-07-28T05:16:42.000+00:00"
                )
            ),
            "updated-before-finished": lambda value: value.__setitem__(
                "updated_at", "2026-07-28T05:16:39.000+00:00"
            ),
        }
        for mutation, mutate in mutations.items():
            with self.subTest(mutation=mutation):
                repo, report = self.make_report()
                run = report["parallel_runs"][0]
                path = Path(run["persisted_status_path"])
                status = json.loads(path.read_text(encoding="utf-8"))
                mutate(status)
                run["persisted_status_sha256"] = write_json(path, status)
                write_json(
                    repo
                    / "evidence/project-test-runner/promotion-report.json",
                    report,
                )
                with self.assertRaisesRegex(
                    PromotionValidationError, "time|timeline|created"
                ):
                    validate_promotion_report(repo)

    def test_v2_rejects_each_persisted_log_size_mismatch(self) -> None:
        for field in ("stdout", "stderr", "merged"):
            with self.subTest(field=field):
                repo, report = self.make_report()
                run = report["parallel_runs"][0]
                path = Path(run["persisted_status_path"])
                status = json.loads(path.read_text(encoding="utf-8"))
                status["log_sizes"][field] += 1
                run["persisted_status_sha256"] = write_json(path, status)
                write_json(
                    repo
                    / "evidence/project-test-runner/promotion-report.json",
                    report,
                )
                with self.assertRaisesRegex(
                    PromotionValidationError, "log_sizes"
                ):
                    validate_promotion_report(repo)

    def test_v2_requires_opened_identity_for_each_unbound_log(self) -> None:
        original = promotion_validator._path_identity_matches_open_handle
        for filename in ("stderr.log", "command.log"):
            with self.subTest(filename=filename):
                repo, _report = self.make_report()

                def fail_selected_log(
                    lexical_path, canonical_before, file_descriptor, handle_stat
                ):
                    if lexical_path.name == filename:
                        return False
                    return original(
                        lexical_path,
                        canonical_before,
                        file_descriptor,
                        handle_stat,
                    )

                with mock.patch.object(
                    promotion_validator,
                    "_path_identity_matches_open_handle",
                    side_effect=fail_selected_log,
                ), self.assertRaisesRegex(
                    PromotionValidationError,
                    "path identity is unproved",
                ):
                    validate_promotion_report(repo)

    def test_v2_rejects_synthetic_runs_outside_canonical_trust_roots(
        self,
    ) -> None:
        repo, _report = self.make_report()
        with self.assertRaisesRegex(
            PromotionValidationError,
            "canonical .* root|trusted .* root",
        ):
            promotion_validator.validate_promotion_report(repo)

    def test_v2_rejects_one_worker_identity_reused_for_every_module(
        self,
    ) -> None:
        repo, report = self.make_report()
        run = report["parallel_runs"][0]
        shared_identity = {
            "pid": 4242,
            "process_creation_identity": "windows-filetime:134296899999999999",
        }
        summary_path = Path(run["summary_path"])
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        for module in summary["modules"]:
            module["worker_identity"] = shared_identity
            result_path = (
                Path(run["run_dir"])
                / "modules"
                / f"{module['module_key']}.result.json"
            )
            result = json.loads(result_path.read_text(encoding="utf-8"))
            result["worker_identity"] = shared_identity
            module["result_sha256"] = write_json(result_path, result)
        run["summary_sha256"] = write_json(summary_path, summary)
        events_path = Path(run["run_dir"]) / "events.jsonl"
        events = [
            json.loads(line)
            for line in events_path.read_text(encoding="utf-8").splitlines()
        ]
        for event in events:
            if event["event"] in {"started", "completed"}:
                event["pid"] = shared_identity["pid"]
                event["process_creation_identity"] = shared_identity[
                    "process_creation_identity"
                ]
        write_bytes(
            events_path,
            b"".join(canonical_json_bytes(event) for event in events),
        )
        stdout_path = Path(run["persisted_stdout_path"])
        stdout_records = read_stdout_records(stdout_path)
        completion = stdout_records[-1]
        completion["summary_sha256"] = run["summary_sha256"]
        run["persisted_stdout_sha256"] = write_stdout_records(
            stdout_path, stdout_records
        )
        refresh_persisted_stdout_identity(run)
        with self.assertRaisesRegex(
            PromotionValidationError,
            "worker identity|independent process",
        ):
            with trusted_fixture_roots(repo):
                promotion_validator._validate_parallel_run(
                repo,
                run,
                report["promotion_closed_set"],
                report["implementation"]["execution_evidence_commit"],
                1800.0,
                expected_test_ids=sorted(
                    report["final_issue9_closed_set"]["test_ids"]
                    + json.loads(
                        (repo / AUTHORITY).read_text(encoding="utf-8")
                    )["authorized_delta"]["test_ids"]
                ),
                    expected_registry_sha256=report["implementation"][
                        "registry_sha256"
                    ],
                )

    def test_v2_rejects_consistent_runner_identity_substitution(self) -> None:
        repo, report = self.make_report()
        run = report["parallel_runs"][0]
        test_run_path = Path(run["run_dir"]) / "test-run.json"
        test_run = json.loads(test_run_path.read_text(encoding="utf-8"))
        self.assertNotEqual(
            test_run["persisted_target_identity"]["pid"],
            919191,
        )
        test_run["runner_identity"]["pid"] = 919191
        test_run["runner_identity"]["process_creation_identity"] = (
            "windows-filetime:134296899191919191"
        )
        write_json(test_run_path, test_run)
        expected_ids = sorted(
            report["final_issue9_closed_set"]["test_ids"]
            + json.loads(
                (repo / AUTHORITY).read_text(encoding="utf-8")
            )["authorized_delta"]["test_ids"]
        )
        with self.assertRaisesRegex(
            PromotionValidationError,
            "test-run identity|runner.*persisted target|process identity",
        ):
            with trusted_fixture_roots(repo):
                promotion_validator._validate_parallel_run(
                    repo,
                    run,
                    report["promotion_closed_set"],
                    report["implementation"]["execution_evidence_commit"],
                    1800.0,
                    expected_test_ids=expected_ids,
                    expected_registry_sha256=report["implementation"][
                        "registry_sha256"
                    ],
                )

    def test_v2_rejects_coherent_process_identity_replacements(
        self,
    ) -> None:
        repo, report = self.make_report()
        run = report["parallel_runs"][0]
        expected_ids = sorted(
            report["final_issue9_closed_set"]["test_ids"]
            + json.loads(
                (repo / AUTHORITY).read_text(encoding="utf-8")
            )["authorized_delta"]["test_ids"]
        )

        def replace_identity(
            original: dict,
            *,
            pid: int,
            creation: int,
            parent: dict | None = None,
        ) -> dict:
            changed = json.loads(json.dumps(original))
            changed["pid"] = pid
            changed["process_creation_identity"] = (
                f"windows-filetime:{creation}"
            )
            changed["executable_file_identity"]["inode"] += pid
            if parent is not None:
                changed["parent_pid"] = parent["pid"]
                changed["parent_process_creation_identity"] = parent[
                    "process_creation_identity"
                ]
            changed["observation_sha256"] = hashlib.sha256(
                canonical_json_bytes(
                    {
                        key: value
                        for key, value in changed.items()
                        if key != "observation_sha256"
                    }
                )
            ).hexdigest()
            return changed

        def assert_parallel_rejected() -> None:
            with self.assertRaisesRegex(
                PromotionValidationError,
                "identity|discovery|worker|stdout|completion",
            ):
                with trusted_fixture_roots(repo):
                    promotion_validator._validate_parallel_run(
                        repo,
                        run,
                        report["promotion_closed_set"],
                        report["implementation"]["execution_evidence_commit"],
                        1800.0,
                        expected_test_ids=expected_ids,
                        expected_registry_sha256=report["implementation"][
                            "registry_sha256"
                        ],
                    )

        test_run_path = Path(run["run_dir"]) / "test-run.json"
        original_test_run = test_run_path.read_bytes()
        test_run = json.loads(original_test_run.decode("utf-8"))
        test_run["runner_identity"] = replace_identity(
            test_run["runner_identity"],
            pid=911_001,
            creation=134296899110010001,
        )
        write_json(test_run_path, test_run)
        assert_parallel_rejected()
        test_run_path.write_bytes(original_test_run)

        summary_path = Path(run["summary_path"])
        original_summary = summary_path.read_bytes()
        original_summary_sha = run["summary_sha256"]
        summary = json.loads(original_summary.decode("utf-8"))
        first = summary["modules"][0]
        first["worker_identity"] = replace_identity(
            first["worker_identity"],
            pid=922_001,
            creation=134296899220010001,
            parent=first["worker_launcher_identity"],
        )
        run["summary_sha256"] = write_json(summary_path, summary)
        assert_parallel_rejected()
        summary_path.write_bytes(original_summary)
        run["summary_sha256"] = original_summary_sha

        for field, pid in (
            ("launcher_identity", 932_001),
            ("self_identity", 932_002),
        ):
            with self.subTest(discovery_replacement=field):
                test_run = json.loads(original_test_run.decode("utf-8"))
                process = test_run["discovery_process"]
                process[field] = replace_identity(
                    process[field],
                    pid=pid,
                    creation=134296899320000000 + pid,
                )
                write_json(test_run_path, test_run)
                assert_parallel_rejected()
                test_run_path.write_bytes(original_test_run)

        test_run = json.loads(original_test_run.decode("utf-8"))
        process = test_run["discovery_process"]
        new_launcher = replace_identity(
            process["launcher_identity"],
            pid=933_001,
            creation=134296899330010001,
            parent=test_run["runner_identity"],
        )
        new_self = replace_identity(
            process["self_identity"],
            pid=933_002,
            creation=134296899330010002,
            parent=new_launcher,
        )
        process["launcher_identity"] = new_launcher
        process["self_identity"] = new_self
        write_json(test_run_path, test_run)
        assert_parallel_rejected()
        test_run_path.write_bytes(original_test_run)

    def test_v2_rejects_unique_worker_pid_creation_substitution_without_full_identity(
        self,
    ) -> None:
        repo, report = self.make_report()
        run = report["parallel_runs"][0]
        summary_path = Path(run["summary_path"])
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        identities = {}
        for index, module in enumerate(summary["modules"], start=1):
            changed = dict(module["worker_identity"])
            changed["pid"] = 900_000 + index
            changed["process_creation_identity"] = (
                f"windows-filetime:{134296899000000000 + index}"
            )
            identities[module["module_key"]] = changed
            module["worker_identity"] = changed
            result_path = (
                Path(run["run_dir"])
                / "modules"
                / f"{module['module_key']}.result.json"
            )
            result = json.loads(result_path.read_text(encoding="utf-8"))
            result["worker_identity"] = changed
            module["result_sha256"] = write_json(result_path, result)
        run["summary_sha256"] = write_json(summary_path, summary)
        events_path = Path(run["run_dir"]) / "events.jsonl"
        events = [
            json.loads(line)
            for line in events_path.read_text(encoding="utf-8").splitlines()
        ]
        for event in events:
            if event["event"] == "completed":
                event["worker_identity"] = identities[event["module_key"]]
        write_bytes(
            events_path,
            b"".join(canonical_json_bytes(event) for event in events),
        )
        stdout_path = Path(run["persisted_stdout_path"])
        stdout_records = read_stdout_records(stdout_path)
        completion = stdout_records[-1]
        completion["summary_sha256"] = run["summary_sha256"]
        run["persisted_stdout_sha256"] = write_stdout_records(
            stdout_path, stdout_records
        )
        expected_ids = sorted(
            report["final_issue9_closed_set"]["test_ids"]
            + json.loads(
                (repo / AUTHORITY).read_text(encoding="utf-8")
            )["authorized_delta"]["test_ids"]
        )
        with self.assertRaisesRegex(
            PromotionValidationError,
            "identity",
        ):
            with trusted_fixture_roots(repo):
                promotion_validator._validate_parallel_run(
                    repo,
                    run,
                    report["promotion_closed_set"],
                    report["implementation"]["execution_evidence_commit"],
                    1800.0,
                    expected_test_ids=expected_ids,
                    expected_registry_sha256=report["implementation"][
                        "registry_sha256"
                    ],
                )

    def test_v2_rejects_same_head_with_dirty_executed_runner_bytes(self) -> None:
        repo, report = self.make_report()
        run = report["parallel_runs"][0]
        runner_path = repo / report["implementation"]["runner_path"]
        runner_path.write_bytes(
            runner_path.read_bytes() + b"\n# dirty execution probe\n"
        )
        report["implementation"]["runner_sha256"] = hashlib.sha256(
            runner_path.read_bytes()
        ).hexdigest()
        source_manifest_path = Path(run["run_dir"]) / "execution-source.json"
        source_manifest = json.loads(
            source_manifest_path.read_text(encoding="utf-8")
        )
        runner_entry = next(
            item
            for item in source_manifest["entries"]
            if item["path"] == report["implementation"]["runner_path"]
        )
        runner_entry["runtime_sha256"] = report["implementation"][
            "runner_sha256"
        ]
        runner_entry["runtime_size"] = runner_path.stat().st_size
        (
            Path(run["run_dir"]) / runner_entry["frozen_path"]
        ).write_bytes(runner_path.read_bytes())
        source_manifest_sha = write_json(
            source_manifest_path,
            source_manifest,
        )
        test_run_path = Path(run["run_dir"]) / "test-run.json"
        test_run = json.loads(test_run_path.read_text(encoding="utf-8"))
        test_run["source_manifest_sha256"] = source_manifest_sha
        write_json(test_run_path, test_run)
        expected_ids = sorted(
            report["final_issue9_closed_set"]["test_ids"]
            + json.loads(
                (repo / AUTHORITY).read_text(encoding="utf-8")
            )["authorized_delta"]["test_ids"]
        )
        with self.assertRaisesRegex(
            PromotionValidationError,
            "source|commit|dirty",
        ):
            with trusted_fixture_roots(repo):
                promotion_validator._validate_parallel_run(
                    repo,
                    run,
                    report["promotion_closed_set"],
                    report["implementation"]["execution_evidence_commit"],
                    1800.0,
                    expected_test_ids=expected_ids,
                    expected_registry_sha256=report["implementation"][
                        "registry_sha256"
                    ],
                )

    def test_v2_rejects_post_run_live_runner_or_scheduler_drift(
        self,
    ) -> None:
        for path_field, sha_field in (
            ("runner_path", "runner_sha256"),
            ("scheduler_path", "scheduler_sha256"),
        ):
            with self.subTest(source=path_field):
                repo, report = self.make_report()
                source_path = repo / report["implementation"][path_field]
                source_path.write_bytes(
                    source_path.read_bytes() + b"\n# post-run drift\n"
                )
                report["implementation"][sha_field] = hashlib.sha256(
                    source_path.read_bytes()
                ).hexdigest()
                write_json(
                    repo
                    / "evidence/project-test-runner/promotion-report.json",
                    report,
                )

                with self.assertRaisesRegex(
                    PromotionValidationError,
                    "Promotion authority source",
                ):
                    validate_promotion_report(repo)

    def test_v2_rejects_frozen_git_authority_link_tampering(self) -> None:
        repo, report = self.make_report()
        run = report["parallel_runs"][0]
        git_link = Path(run["run_dir"]) / "execution-source-files" / ".git"
        git_link.write_text(
            "gitdir: D:/untrusted/synthetic.git\n",
            encoding="utf-8",
        )
        source_manifest_path = (
            Path(run["run_dir"]) / "execution-source.json"
        )
        source_manifest = json.loads(
            source_manifest_path.read_text(encoding="utf-8")
        )
        source_manifest["git_authority"]["git_link_sha256"] = (
            hashlib.sha256(git_link.read_bytes()).hexdigest()
        )
        source_manifest["git_authority"]["git_link_file_identity"] = (
            file_artifact_identity(git_link)
        )
        source_manifest_sha = write_json(
            source_manifest_path,
            source_manifest,
        )
        test_run_path = Path(run["run_dir"]) / "test-run.json"
        test_run = json.loads(test_run_path.read_text(encoding="utf-8"))
        test_run["source_manifest_sha256"] = source_manifest_sha
        write_json(test_run_path, test_run)
        expected_ids = sorted(
            report["final_issue9_closed_set"]["test_ids"]
            + json.loads(
                (repo / AUTHORITY).read_text(encoding="utf-8")
            )["authorized_delta"]["test_ids"]
        )

        with self.assertRaisesRegex(
            PromotionValidationError,
            "Git authority|source",
        ):
            with trusted_fixture_roots(repo):
                promotion_validator._validate_parallel_run(
                    repo,
                    run,
                    report["promotion_closed_set"],
                    report["implementation"]["execution_evidence_commit"],
                    1800.0,
                    expected_test_ids=expected_ids,
                    expected_registry_sha256=report["implementation"][
                        "registry_sha256"
                    ],
                )

    def test_v2_rejects_coherent_git_authority_config_tampering(self) -> None:
        repo, report = self.make_report()
        run = report["parallel_runs"][0]
        config_path = Path(run["run_dir"]) / "execution-git" / "config"
        config_path.write_bytes(
            config_path.read_bytes()
            + b"\n[include]\n\tpath = D:/untrusted/injected.config\n"
        )
        source_manifest_path = (
            Path(run["run_dir"]) / "execution-source.json"
        )
        source_manifest = json.loads(
            source_manifest_path.read_text(encoding="utf-8")
        )
        source_manifest["git_authority"]["config_sha256"] = (
            hashlib.sha256(config_path.read_bytes()).hexdigest()
        )
        source_manifest["git_authority"]["config_file_identity"] = (
            file_artifact_identity(config_path)
        )
        source_manifest_sha = write_json(
            source_manifest_path,
            source_manifest,
        )
        test_run_path = Path(run["run_dir"]) / "test-run.json"
        test_run = json.loads(test_run_path.read_text(encoding="utf-8"))
        test_run["source_manifest_sha256"] = source_manifest_sha
        write_json(test_run_path, test_run)
        expected_ids = sorted(
            report["final_issue9_closed_set"]["test_ids"]
            + json.loads(
                (repo / AUTHORITY).read_text(encoding="utf-8")
            )["authorized_delta"]["test_ids"]
        )

        with self.assertRaisesRegex(
            PromotionValidationError,
            "Git authority|config|source",
        ):
            with trusted_fixture_roots(repo):
                promotion_validator._validate_parallel_run(
                    repo,
                    run,
                    report["promotion_closed_set"],
                    report["implementation"]["execution_evidence_commit"],
                    1800.0,
                    expected_test_ids=expected_ids,
                    expected_registry_sha256=report["implementation"][
                        "registry_sha256"
                    ],
                )

    def test_v2_rejects_unknown_fields_recursively_in_discovery_suites(
        self,
    ) -> None:
        repo, report = self.make_report()
        run = report["parallel_runs"][0]
        discovery_path = Path(run["discovery_path"])
        discovery = json.loads(discovery_path.read_text(encoding="utf-8"))
        discovery["suites"][0]["unexpected_nested_field"] = True
        run["discovery_sha256"] = write_json(discovery_path, discovery)
        test_run_path = Path(run["run_dir"]) / "test-run.json"
        test_run = json.loads(test_run_path.read_text(encoding="utf-8"))
        test_run["discovery_sha256"] = run["discovery_sha256"]
        write_json(test_run_path, test_run)
        stdout_path = Path(run["persisted_stdout_path"])
        stdout_records = read_stdout_records(stdout_path)
        completion = stdout_records[-1]
        completion["discovery_sha256"] = run["discovery_sha256"]
        run["persisted_stdout_sha256"] = write_stdout_records(
            stdout_path, stdout_records
        )
        refresh_persisted_stdout_identity(run)
        with self.assertRaisesRegex(PromotionValidationError, "unknown field"):
            with trusted_fixture_roots(repo):
                promotion_validator._validate_parallel_run(
                repo,
                run,
                report["promotion_closed_set"],
                report["implementation"]["execution_evidence_commit"],
                1800.0,
                expected_test_ids=sorted(
                    report["final_issue9_closed_set"]["test_ids"]
                    + json.loads(
                        (repo / AUTHORITY).read_text(encoding="utf-8")
                    )["authorized_delta"]["test_ids"]
                ),
                    expected_registry_sha256=report["implementation"][
                        "registry_sha256"
                    ],
                )

    def test_v2_file_snapshot_fails_closed_when_path_identity_is_unproved(
        self,
    ) -> None:
        repo, _report = self.make_report()
        artifact = repo / "snapshot-race.json"
        write_json(artifact, {"value": "before"})
        with mock.patch.object(
            promotion_validator,
            "_path_identity_matches_open_handle",
            return_value=False,
            create=True,
        ):
            with self.assertRaisesRegex(
                PromotionValidationError,
                "path identity|authorization unavailable",
            ):
                promotion_validator._read_file_snapshot(
                    artifact,
                    "racing artifact",
                )

    def test_v2_binds_original_issue9_discovery_inventory(self) -> None:
        repo, report = self.make_report()
        inventory_path = repo / "forged-issue9-discovery.json"
        shutil.copy2(FINAL_ISSUE9_DISCOVERY_PATH, inventory_path)
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        inventory["modules"][0]["test_ids"][0] = (
            "forged.BaselineTests.test_replacement"
        )
        report["final_issue9_closed_set"]["inventory_path"] = str(
            inventory_path
        )
        report["final_issue9_closed_set"]["inventory_sha256"] = write_json(
            inventory_path, inventory
        )
        write_json(
            repo / "evidence/project-test-runner/promotion-report.json",
            report,
        )
        with self.assertRaisesRegex(
            PromotionValidationError, "original discovery"
        ):
            validate_promotion_report(repo)

    def test_v2_rejects_contained_substitute_artifact_paths(self) -> None:
        for field, canonical_name in (
            ("discovery", "discovery.json"),
            ("summary", "summary.json"),
            ("persisted_status", "status.json"),
            ("persisted_command", "command.json"),
            ("persisted_exit_code", "exit-code.txt"),
            ("persisted_stdout", "stdout.log"),
        ):
            with self.subTest(field=field):
                repo, report = self.make_report()
                run = report["parallel_runs"][0]
                path_field = f"{field}_path"
                sha_field = f"{field}_sha256"
                original = Path(run[path_field])
                substitute = original.with_name(f"substitute-{canonical_name}")
                substitute.write_bytes(original.read_bytes())
                run[path_field] = str(substitute)
                run[sha_field] = hashlib.sha256(
                    substitute.read_bytes()
                ).hexdigest()
                write_json(
                    repo
                    / "evidence/project-test-runner/promotion-report.json",
                    report,
                )
                with self.assertRaisesRegex(
                    PromotionValidationError, "inside|canonical"
                ):
                    validate_promotion_report(repo)

    def test_v2_rejects_run_id_or_process_identity_tampering(self) -> None:
        for artifact, field in (
            ("status", "run_id"),
            ("status", "target_identity"),
            ("command", "run_id"),
        ):
            with self.subTest(artifact=artifact, field=field):
                repo, report = self.make_report()
                run = report["parallel_runs"][0]
                path_field = f"persisted_{artifact}_path"
                sha_field = f"persisted_{artifact}_sha256"
                path = Path(run[path_field])
                value = json.loads(path.read_text(encoding="utf-8"))
                if field == "target_identity":
                    value[field]["process_creation_identity"] += "-forged"
                else:
                    value[field] = (
                        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
                    )
                run[sha_field] = write_json(path, value)
                write_json(
                    repo
                    / "evidence/project-test-runner/promotion-report.json",
                    report,
                )
                with self.assertRaises(PromotionValidationError):
                    validate_promotion_report(repo)

    def test_v2_rejects_cloned_external_and_persisted_trees(self) -> None:
        repo, report = self.make_report()
        first = report["parallel_runs"][0]
        second = report["parallel_runs"][1]
        first_run_dir = Path(first["run_dir"])
        cloned_run_dir = first_run_dir.with_name(
            "20260728_040003_00000003"
        )
        shutil.copytree(first_run_dir, cloned_run_dir)
        cloned_test_run_path = cloned_run_dir / "test-run.json"
        cloned_test_run = json.loads(
            cloned_test_run_path.read_text(encoding="utf-8")
        )
        cloned_test_run["run_dir"] = str(cloned_run_dir)
        write_json(cloned_test_run_path, cloned_test_run)
        cloned_persisted_dir = Path(first["persisted_run_dir"]).with_name(
            "promotion_v2_20260728_050003_11111111"
        )
        shutil.copytree(Path(first["persisted_run_dir"]), cloned_persisted_dir)
        second.update(
            {
                "run_dir": str(cloned_run_dir),
                "discovery_path": str(cloned_run_dir / "discovery.json"),
                "discovery_sha256": first["discovery_sha256"],
                "summary_path": str(cloned_run_dir / "summary.json"),
                "summary_sha256": first["summary_sha256"],
                "persisted_run_dir": str(cloned_persisted_dir),
                "persisted_status_path": str(
                    cloned_persisted_dir / "status.json"
                ),
                "persisted_status_sha256": first[
                    "persisted_status_sha256"
                ],
                "persisted_exit_code_path": str(
                    cloned_persisted_dir / "exit-code.txt"
                ),
                "persisted_exit_code_sha256": first[
                    "persisted_exit_code_sha256"
                ],
                "persisted_command_path": str(
                    cloned_persisted_dir / "command.json"
                ),
                "persisted_command_sha256": first[
                    "persisted_command_sha256"
                ],
                "persisted_stdout_path": str(
                    cloned_persisted_dir / "stdout.log"
                ),
                "persisted_stdout_sha256": first[
                    "persisted_stdout_sha256"
                ],
                "semantic_outcomes_sha256": first[
                    "semantic_outcomes_sha256"
                ],
                "module_assignment_sha256": first[
                    "module_assignment_sha256"
                ],
                "registry_sha256": first["registry_sha256"],
            }
        )
        write_json(
            repo / "evidence/project-test-runner/promotion-report.json",
            report,
        )
        with self.assertRaisesRegex(
            PromotionValidationError, "identity|completion"
        ):
            validate_promotion_report(repo)

    def test_v2_rejects_summary_success_forged_over_worker_failure(self) -> None:
        repo, report = self.make_report()
        run = report["parallel_runs"][0]
        summary_path = Path(run["summary_path"])
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["modules"][0]["exit_code"] = 1
        summary["modules"][0]["failure_kind"] = None
        run["summary_sha256"] = write_json(summary_path, summary)
        stdout_path = Path(run["persisted_stdout_path"])
        stdout_records = read_stdout_records(stdout_path)
        completion = next(
            record
            for record in stdout_records
            if record.get("event") == "project_test_run_complete"
        )
        completion["summary_sha256"] = run["summary_sha256"]
        run["persisted_stdout_sha256"] = write_stdout_records(
            stdout_path, stdout_records
        )
        refresh_persisted_stdout_identity(run)
        write_json(
            repo / "evidence/project-test-runner/promotion-report.json",
            report,
        )
        with self.assertRaisesRegex(
            PromotionValidationError, "summary module"
        ):
            validate_promotion_report(repo)

    def test_v2_rejects_consistent_cross_module_reassignment(self) -> None:
        repo, report = self.make_report()
        assignment_sha = ""
        for run in report["parallel_runs"]:
            discovery_path = Path(run["discovery_path"])
            discovery = json.loads(
                discovery_path.read_text(encoding="utf-8")
            )
            first, second = discovery["modules"][:2]
            first["test_ids"][0], second["test_ids"][0] = (
                second["test_ids"][0],
                first["test_ids"][0],
            )
            first["test_ids"].sort()
            second["test_ids"].sort()
            run["discovery_sha256"] = write_json(
                discovery_path, discovery
            )
            assignment_sha = hashlib.sha256(
                canonical_json_bytes(module_assignment(discovery))
            ).hexdigest()
            run["module_assignment_sha256"] = assignment_sha
            test_run_path = Path(run["run_dir"]) / "test-run.json"
            test_run = json.loads(test_run_path.read_text(encoding="utf-8"))
            test_run["discovery_sha256"] = run["discovery_sha256"]
            write_json(test_run_path, test_run)
            summary_path = Path(run["summary_path"])
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            by_key = {
                item["module_key"]: item for item in summary["modules"]
            }
            for discovered in (first, second):
                item = by_key[discovered["module_key"]]
                item["test_ids"] = discovered["test_ids"]
                item["executions"] = [
                    {"test_id": test_id, "status": "passed"}
                    for test_id in discovered["test_ids"]
                ]
            run["summary_sha256"] = write_json(summary_path, summary)
            stdout_path = Path(run["persisted_stdout_path"])
            stdout_records = read_stdout_records(stdout_path)
            for stdout_record in stdout_records:
                if "discovery_sha256" in stdout_record:
                    stdout_record["discovery_sha256"] = run[
                        "discovery_sha256"
                    ]
            stdout_records[-1]["summary_sha256"] = run["summary_sha256"]
            run["persisted_stdout_sha256"] = write_stdout_records(
                stdout_path,
                stdout_records,
            )
            refresh_persisted_stdout_identity(run)
        report["semantic_parity"]["module_assignment_sha256"] = (
            assignment_sha
        )
        write_json(
            repo / "evidence/project-test-runner/promotion-report.json",
            report,
        )
        with self.assertRaisesRegex(
            PromotionValidationError, "Registry-authoritative"
        ):
            validate_promotion_report(repo)

    def test_v2_consumes_the_same_bytes_it_fingerprints(self) -> None:
        repo, _report = self.make_report()
        from scripts import validate_project_test_promotion as validator

        original = validator.sha256_file
        calls = []

        def legacy_split_read_probe(path: Path) -> str:
            calls.append(Path(path))
            return original(path)

        with mock.patch.object(
            validator, "sha256_file", side_effect=legacy_split_read_probe
        ):
            result = validate_promotion_report(repo)
        self.assertTrue(result["valid"])
        bound_names = {
            "discovery.json",
            "summary.json",
            "command.json",
            "status.json",
            "exit-code.txt",
            "stdout.log",
        }
        self.assertFalse(any(path.name in bound_names for path in calls))

    def test_v2_rejects_windows_device_path_alias(self) -> None:
        repo, report = self.make_report()
        run = report["parallel_runs"][0]
        run["discovery_path"] = "\\\\?\\" + run["discovery_path"]
        write_json(
            repo / "evidence/project-test-runner/promotion-report.json",
            report,
        )
        with self.assertRaisesRegex(
            PromotionValidationError,
            "canonical|missing|path identity|authorization unavailable",
        ):
            validate_promotion_report(repo)

    def test_v2_rejects_worker_event_timing_or_test_run_tampering(self) -> None:
        for artifact in (
            "marker",
            "assignment",
            "result",
            "stdout",
            "stderr",
            "events",
            "timings",
            "test-run",
        ):
            with self.subTest(artifact=artifact):
                repo, report = self.make_report()
                run = report["parallel_runs"][0]
                run_dir = Path(run["run_dir"])
                summary = json.loads(
                    Path(run["summary_path"]).read_text(encoding="utf-8")
                )
                module = summary["modules"][0]
                key = module["module_key"]
                paths = {
                    "marker": run_dir.parents[1] / "project.json",
                    "assignment": (
                        run_dir / "modules" / f"{key}.assignment.json"
                    ),
                    "result": run_dir / "modules" / f"{key}.result.json",
                    "stdout": run_dir / "logs" / f"{key}.stdout.log",
                    "stderr": run_dir / "logs" / f"{key}.stderr.log",
                    "events": run_dir / "events.jsonl",
                    "timings": run_dir / "timings.json",
                    "test-run": run_dir / "test-run.json",
                }
                path = paths[artifact]
                path.write_bytes(path.read_bytes() + b" ")
                write_json(
                    repo
                    / "evidence/project-test-runner/promotion-report.json",
                    report,
                )
                with self.assertRaises(PromotionValidationError):
                    validate_promotion_report(repo)

    def test_v2_rejects_removed_or_renamed_baseline_id(self) -> None:
        repo, report = self.make_report()
        report["final_issue9_closed_set"]["test_ids"][-1] = (
            "renamed.BaselineTests.test_replacement"
        )
        write_json(
            repo / "evidence/project-test-runner/promotion-report.json",
            report,
        )
        with self.assertRaises(PromotionValidationError):
            validate_promotion_report(repo)

    def test_v2_rejects_reused_parallel_or_persisted_run_directory(self) -> None:
        repo, report = self.make_report()
        report["parallel_runs"][1]["run_dir"] = report["parallel_runs"][0][
            "run_dir"
        ]
        report["parallel_runs"][1]["persisted_run_dir"] = report[
            "parallel_runs"
        ][0]["persisted_run_dir"]
        write_json(
            repo / "evidence/project-test-runner/promotion-report.json",
            report,
        )
        with self.assertRaises(PromotionValidationError):
            validate_promotion_report(repo)

    def test_v2_rejects_1800_001_and_accepts_1800_000(self) -> None:
        for elapsed, passes in (
            (1800.0, True),
            (1800.001, False),
            (1838.234, False),
        ):
            with self.subTest(elapsed=elapsed):
                repo, report = self.make_report()
                run = report["parallel_runs"][0]
                status_path = Path(run["persisted_status_path"])
                status = json.loads(status_path.read_text(encoding="utf-8"))
                status["elapsed_seconds"] = elapsed
                run["persisted_status_sha256"] = write_json(
                    status_path, status
                )
                write_json(
                    repo
                    / "evidence/project-test-runner/promotion-report.json",
                    report,
                )
                if passes:
                    with self.assertRaisesRegex(
                        PromotionValidationError,
                        "promotion fingerprint mismatch",
                    ):
                        validate_promotion_report(repo)
                else:
                    with self.assertRaisesRegex(
                        PromotionValidationError, "performance gate"
                    ):
                        validate_promotion_report(repo)

    def test_v2_rejects_nonfinite_or_negative_elapsed(self) -> None:
        for elapsed in (-0.001, math.nan, math.inf, -math.inf):
            with self.subTest(elapsed=elapsed):
                repo, report = self.make_report()
                run = report["parallel_runs"][0]
                status_path = Path(run["persisted_status_path"])
                status = json.loads(status_path.read_text(encoding="utf-8"))
                status["elapsed_seconds"] = elapsed
                run["persisted_status_sha256"] = write_json(
                    status_path, status
                )
                write_json(
                    repo
                    / "evidence/project-test-runner/promotion-report.json",
                    report,
                )
                with self.assertRaisesRegex(
                    PromotionValidationError,
                    "elapsed_seconds is invalid|performance gate",
                ):
                    validate_promotion_report(repo)

    def test_v2_unknown_nested_field_fails_closed(self) -> None:
        repo, report = self.make_report()
        report["implementation"]["unexpected"] = True
        write_json(
            repo / "evidence/project-test-runner/promotion-report.json",
            report,
        )
        with self.assertRaisesRegex(
            PromotionValidationError, "schema validation failed"
        ):
            validate_promotion_report(repo)

    def test_v2_unauthorized_safety_path_fails_closed(self) -> None:
        repo, report = self.make_report()
        report["optimization_safety_review"]["path"] = (
            "evidence/project-test-runner/copied-safety.json"
        )
        write_json(
            repo / "evidence/project-test-runner/promotion-report.json",
            report,
        )
        with self.assertRaisesRegex(
            PromotionValidationError, "fixed authority"
        ):
            validate_promotion_report(repo)

    def test_v2_recomputes_authority_instead_of_trusting_summary(self) -> None:
        repo, report = self.make_report()
        authority_path = repo / AUTHORITY
        authority = json.loads(authority_path.read_text(encoding="utf-8"))
        authority["authorized_delta"]["test_ids"][-1] = (
            "unauthorized.ExtraTests.test_extra"
        )
        authority["authorized_delta"]["test_ids"].sort()
        report["superset_authority"]["sha256"] = write_json(
            authority_path, authority
        )
        write_json(
            repo / "evidence/project-test-runner/promotion-report.json",
            report,
        )
        with self.assertRaises(PromotionValidationError):
            validate_promotion_report(repo)

    def test_v2_rejects_registry_or_semantic_outcome_drift(self) -> None:
        repo, report = self.make_report()
        report["parallel_runs"][1]["registry_sha256"] = "f" * 64
        write_json(
            repo / "evidence/project-test-runner/promotion-report.json",
            report,
        )
        with self.assertRaisesRegex(PromotionValidationError, "Registry"):
            validate_promotion_report(repo)

        repo, report = self.make_report()
        run = report["parallel_runs"][1]
        summary_path = Path(run["summary_path"])
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["modules"][0]["executions"][0]["status"] = "skipped"
        run["summary_sha256"] = write_json(summary_path, summary)
        stdout_path = Path(run["persisted_stdout_path"])
        stdout_records = read_stdout_records(stdout_path)
        completion = next(
            record
            for record in stdout_records
            if record.get("event") == "project_test_run_complete"
        )
        completion["summary_sha256"] = run["summary_sha256"]
        run["persisted_stdout_sha256"] = write_stdout_records(
            stdout_path, stdout_records
        )
        refresh_persisted_stdout_identity(run)
        write_json(
            repo / "evidence/project-test-runner/promotion-report.json",
            report,
        )
        with self.assertRaisesRegex(
            PromotionValidationError, "non-passing semantic outcome"
        ):
            validate_promotion_report(repo)

    def test_v2_health_memo_positive_profile_blocks_cutover(self) -> None:
        repo, report = self.make_report()
        safety_path = (
            repo
            / "evidence/project-test-runner/"
            "optimization-safety-review.v1.json"
        )
        safety = json.loads(safety_path.read_text(encoding="utf-8"))
        profile_path = repo / "profile/result.json"
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        profile["control_store_check_classification"]["memo_hits"] = 1
        safety["health_profile"]["sha256"] = write_json(
            profile_path, profile
        )
        report["optimization_safety_review"]["sha256"] = write_json(
            safety_path, safety
        )
        write_json(
            repo / "evidence/project-test-runner/promotion-report.json",
            report,
        )
        with self.assertRaisesRegex(PromotionValidationError, "memo"):
            validate_promotion_report(repo)

    def test_v2_safety_persisted_versions_and_fields_fail_closed(
        self,
    ) -> None:
        for mutation in (
            "focused-command-unknown",
            "focused-status-version",
            "profile-status-unknown",
        ):
            with self.subTest(mutation=mutation):
                repo, report = self.make_report()
                safety_path = (
                    repo
                    / "evidence/project-test-runner/"
                    "optimization-safety-review.v1.json"
                )
                safety = json.loads(
                    safety_path.read_text(encoding="utf-8")
                )
                if mutation == "focused-command-unknown":
                    path = repo / "focused/command.json"
                    value = json.loads(path.read_text(encoding="utf-8"))
                    value["unexpected"] = True
                    safety["focused_run"]["command_sha256"] = write_json(
                        path, value
                    )
                elif mutation == "focused-status-version":
                    path = repo / "focused/status.json"
                    value = json.loads(path.read_text(encoding="utf-8"))
                    value["schema_version"] = "9.9.9"
                    safety["focused_run"]["status_sha256"] = write_json(
                        path, value
                    )
                else:
                    path = repo / "profile/status.json"
                    value = json.loads(path.read_text(encoding="utf-8"))
                    value["unexpected"] = True
                    safety["health_profile"][
                        "persisted_status_sha256"
                    ] = write_json(path, value)
                report["optimization_safety_review"]["sha256"] = write_json(
                    safety_path, safety
                )
                write_json(
                    repo
                    / "evidence/project-test-runner/promotion-report.json",
                    report,
                )
                with self.assertRaises(PromotionValidationError):
                    validate_promotion_report(repo)

    def test_v2_rejects_delta_cardinality_overlap_and_stale_sources(
        self,
    ) -> None:
        for mutation in ("23", "duplicate", "overlap"):
            with self.subTest(mutation=mutation):
                repo, report = self.make_report()
                authority_path = repo / AUTHORITY
                authority = json.loads(
                    authority_path.read_text(encoding="utf-8")
                )
                delta = authority["authorized_delta"]["test_ids"]
                if mutation == "23":
                    delta.pop()
                elif mutation == "duplicate":
                    delta[-1] = delta[-2]
                    delta.sort()
                else:
                    delta[-1] = authority["baseline"]["test_ids"][0]
                    delta.sort()
                report["superset_authority"]["sha256"] = write_json(
                    authority_path, authority
                )
                write_json(
                    repo
                    / "evidence/project-test-runner/promotion-report.json",
                    report,
                )
                with self.assertRaises(PromotionValidationError):
                    validate_promotion_report(repo)
        for relative_path in (
            "tests/video_workflow/test_contract_registry_cache.py",
            "src/video2pdf_workflow_kernel/control_store.py",
        ):
            with self.subTest(stale_source=relative_path):
                repo, _report = self.make_report()
                path = repo / relative_path
                path.write_bytes(path.read_bytes() + b"\n# drift\n")
                with self.assertRaisesRegex(
                    PromotionValidationError, "stale|fingerprint"
                ):
                    validate_promotion_report(repo)

    def test_v2_rejects_raw_persisted_and_scheduler_tampering(self) -> None:
        for mutation in (
            "status",
            "status-version",
            "status-unknown",
            "security",
            "eligible",
            "exit",
            "command",
            "command-version",
            "command-unknown",
            "stdout",
            "jobs",
            "peak",
            "coverage",
            "invalid-discovery-json",
            "discovery-version",
            "discovery-unknown",
            "summary-version",
            "summary-unknown",
        ):
            with self.subTest(mutation=mutation):
                repo, report = self.make_report()
                run = report["parallel_runs"][0]
                if mutation in {
                    "status",
                    "status-version",
                    "status-unknown",
                    "security",
                    "eligible",
                }:
                    path = Path(run["persisted_status_path"])
                    value = json.loads(path.read_text(encoding="utf-8"))
                    if mutation == "status":
                        value["state"] = "failed"
                    elif mutation == "status-version":
                        value["schema_version"] = "9.9.9"
                    elif mutation == "status-unknown":
                        value["unexpected"] = True
                    elif mutation == "security":
                        value["security"]["classification"] = (
                            "potential_secret_detected"
                        )
                    else:
                        value["security"][
                            "acceptance_evidence_eligible"
                        ] = False
                    run["persisted_status_sha256"] = write_json(path, value)
                elif mutation == "exit":
                    path = Path(run["persisted_exit_code_path"])
                    run["persisted_exit_code_sha256"] = write_bytes(
                        path, b"1\n"
                    )
                elif mutation in {
                    "command",
                    "command-version",
                    "command-unknown",
                }:
                    path = Path(run["persisted_command_path"])
                    value = json.loads(path.read_text(encoding="utf-8"))
                    if mutation == "command":
                        value["argv"][
                            value["argv"].index("--jobs") + 1
                        ] = "3"
                    elif mutation == "command-version":
                        value["schema_version"] = "9.9.9"
                    else:
                        value["unexpected"] = True
                    run["persisted_command_sha256"] = write_json(path, value)
                elif mutation == "stdout":
                    path = Path(run["persisted_stdout_path"])
                    run["persisted_stdout_sha256"] = write_bytes(
                        path, b'{"event":"unrelated"}\n'
                    )
                elif mutation == "invalid-discovery-json":
                    path = Path(run["discovery_path"])
                    run["discovery_sha256"] = write_bytes(path, b"{")
                elif mutation in {
                    "discovery-version",
                    "discovery-unknown",
                }:
                    path = Path(run["discovery_path"])
                    value = json.loads(path.read_text(encoding="utf-8"))
                    if mutation == "discovery-version":
                        value["schema_version"] = 9
                    else:
                        value["unexpected"] = True
                    run["discovery_sha256"] = write_json(path, value)
                else:
                    summary_path = Path(run["summary_path"])
                    summary = json.loads(
                        summary_path.read_text(encoding="utf-8")
                    )
                    if mutation == "jobs":
                        summary["requested_jobs"] = 3
                    elif mutation == "peak":
                        summary["observed_peak_concurrency"] = 3
                    elif mutation == "summary-version":
                        summary["schema_version"] = 9
                    elif mutation == "summary-unknown":
                        summary["unexpected"] = True
                    else:
                        summary["coverage"]["missing_test_ids"] = [
                            summary["modules"][0]["test_ids"][0]
                        ]
                    run["summary_sha256"] = write_json(
                        summary_path, summary
                    )
                    stdout_path = Path(run["persisted_stdout_path"])
                    run["persisted_stdout_sha256"] = write_bytes(
                        stdout_path,
                        canonical_json_bytes(
                            {
                                "event": "project_test_run_complete",
                                "success": True,
                                "failure_kind": None,
                                "run_dir": run["run_dir"],
                                "discovery_sha256": run[
                                    "discovery_sha256"
                                ],
                                "summary_sha256": run["summary_sha256"],
                            }
                        ),
                    )
                write_json(
                    repo
                    / "evidence/project-test-runner/promotion-report.json",
                    report,
                )
                with self.assertRaises(PromotionValidationError):
                    validate_promotion_report(repo)

    def test_v2_promotion_fingerprint_is_mandatory_and_complete(self) -> None:
        repo, report = self.make_report()
        report["promotion_fingerprint"] = "0" * 64
        write_json(
            repo / "evidence/project-test-runner/promotion-report.json",
            report,
        )
        with self.assertRaisesRegex(
            PromotionValidationError, "promotion fingerprint mismatch"
        ):
            validate_promotion_report(repo)


if __name__ == "__main__":
    unittest.main()
