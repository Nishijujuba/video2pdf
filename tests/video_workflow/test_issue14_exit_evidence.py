from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scripts import collect_issue14_exit_evidence as collector
from scripts import issue14_exit_evidence_contract as contract
from scripts import validate_slice_exit_evidence as validator
from tests.video_workflow._test_run import new_case_dir
from tests.video_workflow.test_issue13_delivery_lifecycle import (
    _acceptance_report,
    _guard_report,
)
from video2pdf_workflow_kernel.evidence import fingerprint_implementation_changes


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


class Issue14ExitEvidenceTests(unittest.TestCase):
    def manifest(self) -> dict:
        sha = "1" * 64
        return {
            "$schema": "https://video2pdf.local/schemas/exit-evidence-manifest.v2.schema.json",
            "schema_version": 2,
            "kind": "video-workflow-exit-evidence",
            "fingerprint_algorithm": "sha256-raw-v1",
            "slice": {"number": contract.SLICE_NUMBER, "name": contract.SLICE_NAME},
            "slice_base_commit": contract.SLICE_BASE_COMMIT,
            "implementation_commit": "2" * 40,
            "evidence_paths": ["evidence/slice-13/exit-evidence-manifest.json", "evidence/slice-13/logs/tests.log"],
            "generated_at": "2026-08-12T00:00:00Z",
            "activation_scope": deepcopy(contract.ACTIVATION_SCOPE),
            "platform_statuses": deepcopy(contract.PLATFORM_STATUSES),
            "guarded_delivery_evidence": {
                "collection": {
                    "role": "guarded_delivery_collection",
                    "path": "evidence/slice-13/guarded-delivery/collection.json",
                    "sha256": sha,
                },
                "run_id": "14141414141414141414141414141414",
                "canonical_platform": "youtube",
                "delivery_stage": "delivered",
                "artifacts": [
                    {
                        "role": role,
                        "path": f"evidence/slice-13/guarded-delivery/{role}.json",
                        "sha256": sha,
                    }
                    for role in (
                        "run_record",
                        "source_manifest",
                        "acceptance_report_v2",
                        "delivery_guard_report",
                        "video_delivery_target",
                        "session_delivery_target",
                        "delivery_task_index",
                        "global_gate_authority",
                        "final_pdf",
                    )
                ],
                "qualification_run": {
                    "run_id": "14141414-1414-4414-8414-141414141414",
                    "command_record": {
                        "role": "persisted_command_record",
                        "path": "evidence/slice-13/guarded-delivery/qualification/command.json",
                        "sha256": sha,
                    },
                    "terminal_status": {
                        "role": "persisted_terminal_status",
                        "path": "evidence/slice-13/guarded-delivery/qualification/status.json",
                        "sha256": sha,
                    },
                    "exit_code": {
                        "role": "persisted_exit_code",
                        "path": "evidence/slice-13/guarded-delivery/qualification/exit-code.txt",
                        "sha256": sha,
                    },
                },
            },
            "atomic_members": list(contract.ATOMIC_MEMBERS),
            "atomic_member_status": deepcopy(contract.ATOMIC_MEMBER_STATUS),
            "commands": [
                {"test_id": command_id, "command": list(command), "expected_exit_code": exit_code, "actual_exit_code": exit_code, "log": {"role": "command_log", "path": f"evidence/slice-13/logs/{command_id}.log", "sha256": sha}, "conforms": True}
                for command_id, command, exit_code in contract.COMMANDS
            ],
            "expected_checkpoints": deepcopy(contract.EXPECTED_CHECKPOINTS),
            "fixtures": [{"role": role, "path": path, "sha256": sha} for role, path in contract.FIXTURE_SPECS],
            "results": deepcopy(contract.RESULTS),
            "result_bindings": deepcopy(contract.RESULT_BINDINGS),
            "artifact_fingerprints": [{"role": "implementation_artifact", "path": "scripts/issue14_exit_evidence_contract.py", "sha256": sha}],
            "unresolved_exceptions": [],
            "overall_decision": "pass",
        }

    def test_youtube_kernel_cutover_pass(self) -> None:
        fixture = json.loads((ROOT / "tests/video_workflow/fixtures/exit_evidence/slice13.valid.json").read_text(encoding="utf-8"))
        self.assertEqual(self.manifest()["platform_statuses"], fixture["manifest_authority"]["platform_statuses"])
        schema = json.loads((ROOT / "schemas/exit-evidence-manifest.v2.schema.json").read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(self.manifest())
        validator.validate_issue14_cutover(self.manifest())
        validator.validate_semantics(self.manifest())

    def test_bilibili_kernel_preserved(self) -> None:
        self.assertEqual({"bilibili": "active_kernel", "youtube": "active_kernel"}, self.manifest()["platform_statuses"])

    def test_bilibili_authority_change_rejected(self) -> None:
        # scenario_id=bilibili_legacy_restored; target_invariant=platform_statuses;
        # mutation_seam=platform_statuses.bilibili; rematerialized_nodes=none;
        # intentionally_stale_nodes=none; scenario_class=single_contradiction.
        scenario = json.loads((ROOT / "tests/video_workflow/fixtures/exit_evidence/slice13.bilibili-kernel.invalid.json").read_text(encoding="utf-8"))
        invalid = self.manifest()
        invalid.update(deepcopy(scenario["mutation"]))
        with self.assertRaises(validator.EvidenceError) as caught:
            validator.validate_issue14_cutover(invalid)
        self.assertEqual(scenario["expected_first_failing_gate"], caught.exception.first_failing_gate)
        self.assertEqual(scenario["expected_error_code"], caught.exception.error_code)

    def test_contract_and_collector_skeleton_close_fourteen_members(self) -> None:
        skeleton = collector.qualification_manifest_skeleton()
        self.assertEqual(14, len(contract.ATOMIC_MEMBERS))
        self.assertEqual(list(contract.ATOMIC_MEMBERS), skeleton["atomic_members"])
        self.assertEqual(contract.ACTIVATION_SCOPE, skeleton["activation_scope"])

    def _isolated_delivered_youtube_fixture(self) -> tuple[Path, Path, Path]:
        project = new_case_dir(self.id(), label="issue14-exit-evidence")
        run_dir = project / "workspace" / "Issue 14 YouTube Run_20260812_090000"
        review_dir = run_dir / "review" / "acceptance"
        workflow_dir = run_dir / "workflow"
        source_dir = run_dir / "source"
        session_id = "session-issue14-exit-evidence"
        run_id = "14141414141414141414141414141414"

        final_pdf = run_dir / "Issue 14 YouTube Run.pdf"
        final_pdf.parent.mkdir(parents=True, exist_ok=True)
        final_pdf.write_bytes(b"%PDF-1.7\n% isolated issue14 fixture\n")
        source_manifest = _write_json(
            source_dir / "source-manifest.json",
            {
                "schema_name": "source-manifest",
                "schema_version": "1.0.0",
                "run_id": run_id,
                "canonical_platform": "youtube",
                "canonical_item_id": "dQw4w9WgXcQ",
                "source_state": "ready",
            },
        )
        acceptance_report = _write_json(
            review_dir / "acceptance_report.json",
            _acceptance_report(run_id, 3, "pass"),
        )
        guard_report = _write_json(
            review_dir / "delivery_guard_report.json",
            _guard_report("pass"),
        )
        global_gate = _write_json(
            project / ".workflow-control" / "active_global_gate.json",
            {
                "schema_name": "global-gate-authority",
                "schema_version": "1.0.0",
                "active_global_gate": "acceptance_report_v2",
                "generation": 1,
            },
        )
        video_target = _write_json(
            review_dir / "delivery_target.json",
            {
                "schema_name": "kernel-delivery-target",
                "schema_version": "1.0.0",
                "projection_kind": "video_target",
                "projection_revision": 4,
                "run_id": run_id,
                "run_revision": 4,
                "lifecycle_intent_id": "a" * 64,
                "video_output_dir": str(run_dir.resolve()),
                "stage": "delivered",
                "ownership": {"session_id": session_id, "generation": 1},
                "artifacts": {
                    "final_pdf": {"path": str(final_pdf.resolve()), "sha256": _sha256(final_pdf)},
                    "main_tex": None,
                    "final_compile_report": None,
                    "acceptance_report": {"path": str(acceptance_report.resolve()), "sha256": _sha256(acceptance_report)},
                    "delivery_guard_report": {"path": str(guard_report.resolve()), "sha256": _sha256(guard_report)},
                },
                "global_gate_authority": {"path": str(global_gate.resolve()), "generation": 1, "sha256": _sha256(global_gate)},
            },
        )
        current_target = _write_json(
            project / ".codex" / "delivery-targets" / "sessions" / session_id / "current.json",
            {
                "schema_name": "kernel-session-delivery-target",
                "schema_version": "1.0.0",
                "projection_kind": "session_target",
                "projection_revision": 4,
                "projection_path": str((project / ".codex" / "delivery-targets" / "sessions" / session_id / "current.json").resolve()),
                "session_id": session_id,
                "run_id": run_id,
                "run_revision": 4,
                "lifecycle_intent_id": "a" * 64,
                "stage": "delivered",
                "ownership_generation": 1,
                "owner_status": "active",
                "video_output_dir": str(run_dir.resolve()),
                "video_target": {"path": str(video_target.resolve()), "projection_revision": 4, "sha256": _sha256(video_target)},
            },
        )
        task_index = _write_json(
            project / ".codex" / "delivery-targets" / "task-index.json",
            {
                "schema_name": "kernel-delivery-task-index",
                "schema_version": "1.0.0",
                "projection_kind": "task_index",
                "projection_revision": 4,
                "entries": [{
                    "run_id": run_id,
                    "canonical_platform": "youtube",
                    "video_output_dir": str(run_dir.resolve()),
                    "run_revision": 4,
                    "lifecycle_intent_id": "a" * 64,
                    "stage": "delivered",
                    "session_id": session_id,
                    "ownership_generation": 1,
                    "video_target": {"path": str(video_target.resolve()), "projection_revision": 4, "sha256": _sha256(video_target)},
                    "session_target": {"path": str(current_target.resolve()), "projection_revision": 4, "sha256": _sha256(current_target)},
                    "archive": None,
                }],
            },
        )
        _write_json(
            workflow_dir / "run.json",
            {
                "schema_name": "run-record",
                "schema_version": "4.0.0",
                "kernel_version": "2.0.0",
                "scaffold_version": "1.0.0",
                "run_id": run_id,
                "request_id": "issue-14-exit-evidence",
                "platform_adapter": "youtube",
                "adapter_contract_version": "1.0.0",
                "canonical_platform": "youtube",
                "canonical_item_id": "dQw4w9WgXcQ",
                "source_identity_scheme": "canonical-platform-item-v1",
                "source_identity": "b" * 64,
                "source_version_scheme": "source-content-v1",
                "source_version": "c" * 64,
                "original_title": "Issue 14 YouTube Run",
                "normalized_title": "Issue 14 YouTube Run",
                "task_start": "2026-08-12T09:00:00+08:00",
                "output_path": str(run_dir.resolve()),
                "deliverable_version": 1,
                "version_basis": "source_only",
                "requested_source_acquisition_mode": "fresh_download",
                "source_acquisition_mode": "fresh_download",
                "source_epoch": 1,
                "source_state": "ready",
                "source_blocker": None,
                "phase": "delivered",
                "initialization_intent_id": "initialize-issue-14-exit-evidence",
                "coordination_revision": 4,
                "last_mutation_intent_id": "a" * 64,
                "artifact_plan": "workflow/artifact-plan.json",
                "artifact_generations": {
                    "source_manifest": {"path": "source/source-manifest.json", "generation": 1, "sha256": _sha256(source_manifest), "producer": "kernel:source-acquisition", "committed_at": "2026-08-12T01:00:00Z", "source_epoch": 1},
                    "final_pdf": {"path": final_pdf.name, "generation": 1, "sha256": _sha256(final_pdf), "producer": "kernel:final-compile", "committed_at": "2026-08-12T02:00:00Z", "source_epoch": 1},
                },
                "checkpoint_dependencies": {"source_ready": [], "final_quality_ready": ["source_ready"]},
                "checkpoints": {
                    "source_ready": {"status": "current", "artifact_bindings": [{"logical_id": "source_manifest", "generation": 1, "sha256": _sha256(source_manifest)}], "prerequisite_bindings": [], "evidence_sha256": _sha256(source_manifest), "completed_at": "2026-08-12T01:00:00Z"},
                    "final_quality_ready": {"status": "current", "artifact_bindings": [{"logical_id": "final_pdf", "generation": 1, "sha256": _sha256(final_pdf)}], "prerequisite_bindings": [{"name": "source_ready", "evidence_sha256": _sha256(source_manifest)}], "evidence_sha256": _sha256(guard_report), "completed_at": "2026-08-12T02:00:00Z"},
                },
                "delivery": {
                    "stage": "delivered",
                    "ownership": {"session_id": session_id, "generation": 1},
                    "projections": {
                        "video_target": {"path": "review/acceptance/delivery_target.json", "projection_revision": 4, "sha256": _sha256(video_target)},
                        "session_target": {"path": str(current_target.resolve()), "projection_revision": 4, "sha256": _sha256(current_target)},
                        "task_index": {"path": str(task_index.resolve()), "projection_revision": 4, "sha256": _sha256(task_index)},
                        "archive": None,
                    },
                },
            },
        )

        qualification_run = project / "qualification-run"
        qualification_run.mkdir(parents=True)
        qualification_run_id = "14141414-1414-4414-8414-141414141414"
        qualification_argv = [sys.executable, "-X", "utf8", "-B", "-m", "unittest", "-v", "tests.video_workflow.test_issue14_exit_evidence"]
        _write_json(
            qualification_run / "command.json",
            {
                "schema_name": "persisted-command",
                "schema_version": "1.0.0",
                "run_id": qualification_run_id,
                "cwd": str(ROOT.resolve()),
                "argv": qualification_argv,
                "accepted_exit_codes": [0],
            },
        )
        _write_json(
            qualification_run / "status.json",
            {
                "schema_name": "persisted-command-status",
                "schema_version": "1.0.0",
                "run_id": qualification_run_id,
                "state": "succeeded",
                "exit_code": 0,
                "security": {"acceptance_evidence_eligible": True, "classification": "no_secret_detected"},
            },
        )
        (qualification_run / "exit-code.txt").write_text("0\n", encoding="utf-8")
        return run_dir, current_target, qualification_run

    def _refresh_delivery_binding_chain(self, run_dir: Path) -> None:
        run_path = run_dir / "workflow" / "run.json"
        video_path = run_dir / "review" / "acceptance" / "delivery_target.json"
        run_record = json.loads(run_path.read_text(encoding="utf-8"))
        session_path = Path(run_record["delivery"]["projections"]["session_target"]["path"])
        task_index_path = Path(run_record["delivery"]["projections"]["task_index"]["path"])

        video_target = json.loads(video_path.read_text(encoding="utf-8"))
        acceptance_path = Path(video_target["artifacts"]["acceptance_report"]["path"])
        guard_path = Path(video_target["artifacts"]["delivery_guard_report"]["path"])
        video_target["artifacts"]["acceptance_report"]["sha256"] = _sha256(acceptance_path)
        video_target["artifacts"]["delivery_guard_report"]["sha256"] = _sha256(guard_path)
        _write_json(video_path, video_target)

        session_target = json.loads(session_path.read_text(encoding="utf-8"))
        session_target["video_target"]["sha256"] = _sha256(video_path)
        _write_json(session_path, session_target)

        task_index = json.loads(task_index_path.read_text(encoding="utf-8"))
        entry = next(
            item for item in task_index["entries"]
            if item["run_id"] == run_record["run_id"]
        )
        entry["video_target"]["sha256"] = _sha256(video_path)
        entry["session_target"]["sha256"] = _sha256(session_path)
        _write_json(task_index_path, task_index)

        run_record["delivery"]["projections"]["video_target"]["sha256"] = _sha256(video_path)
        run_record["delivery"]["projections"]["session_target"]["sha256"] = _sha256(session_path)
        run_record["delivery"]["projections"]["task_index"]["sha256"] = _sha256(task_index_path)
        _write_json(run_path, run_record)

    def test_public_cli_collects_real_delivered_run_and_finalizes_schema_valid_manifest(self) -> None:
        run_dir, current_target, qualification_run = self._isolated_delivered_youtube_fixture()
        collection_path = run_dir.parent.parent / "collection.json"
        manifest_path = run_dir.parent.parent / "exit-evidence-manifest.json"

        collected = subprocess.run(
            [
                sys.executable, "-X", "utf8", "-B", "-m",
                "scripts.collect_issue14_exit_evidence", "collect",
                "--run-dir", str(run_dir),
                "--current-target", str(current_target),
                "--qualification-run-dir", str(qualification_run),
                "--output", str(collection_path),
            ],
            cwd=ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, collected.returncode, collected.stdout + collected.stderr)
        self.assertTrue(collection_path.is_file(), "collect must persist resumable evidence")
        collection = json.loads(collection_path.read_text(encoding="utf-8"))
        self.assertEqual("issue14-exit-evidence-collection", collection["schema_name"])
        self.assertEqual("14141414141414141414141414141414", collection["run_id"])
        self.assertEqual(
            {
                "run_record", "source_manifest", "acceptance_report_v2",
                "delivery_guard_report", "video_delivery_target",
                "session_delivery_target", "delivery_task_index",
                "global_gate_authority", "final_pdf",
            },
            set(collection["artifacts"]),
        )
        for binding in collection["artifacts"].values():
            self.assertEqual(_sha256(Path(binding["path"])), binding["sha256"])
        self.assertEqual("succeeded", collection["qualification_run"]["state"])
        self.assertTrue(collection["qualification_run"]["acceptance_evidence_eligible"])

        finalized = subprocess.run(
            [
                sys.executable, "-X", "utf8", "-B", "-m",
                "scripts.collect_issue14_exit_evidence", "finalize",
                "--collection", str(collection_path),
                "--manifest", str(manifest_path),
            ],
            cwd=ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        implementation_head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=True,
        ).stdout.strip()
        if implementation_head == contract.SLICE_BASE_COMMIT:
            self.assertEqual(2, finalized.returncode)
            self.assertIn("implementation change set is empty", finalized.stderr)
            self.assertFalse(manifest_path.exists())
            return
        self.assertEqual(0, finalized.returncode, finalized.stdout + finalized.stderr)
        schema = json.loads((ROOT / "schemas/exit-evidence-manifest.v2.schema.json").read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(manifest)
        self.assertEqual("delivered", manifest["guarded_delivery_evidence"]["delivery_stage"])

    def test_collect_rejects_succeeded_run_outside_closed_issue14_qualification(self) -> None:
        mutations = {
            "argv": lambda command: command.__setitem__(
                "argv", [*command["argv"], "tests.video_workflow.test_unrelated"]
            ),
            "cwd": lambda command: command.__setitem__("cwd", str(ROOT.parent.resolve())),
            "accepted_exit_codes": lambda command: command.__setitem__(
                "accepted_exit_codes", [0, 1]
            ),
        }
        for field, mutate in mutations.items():
            with self.subTest(field=field):
                run_dir, current_target, qualification_run = self._isolated_delivered_youtube_fixture()
                command_path = qualification_run / "command.json"
                command = json.loads(command_path.read_text(encoding="utf-8"))
                mutate(command)
                _write_json(command_path, command)
                output = run_dir.parents[1] / f"{field}-collection.json"

                with self.assertRaises(collector.CollectionError):
                    collector.collect(
                        run_dir=run_dir,
                        current_target=current_target,
                        qualification_run_dir=qualification_run,
                        output=output,
                    )
                self.assertFalse(output.exists())

    def test_finalize_fingerprints_complete_implementation_change_set(self) -> None:
        repository = new_case_dir(self.id(), label="issue14-fingerprint-repository")

        def git(*arguments: str) -> str:
            completed = subprocess.run(
                ["git", *arguments], cwd=repository, text=True, encoding="utf-8",
                capture_output=True, check=False,
            )
            self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
            return completed.stdout.strip()

        git("init")
        git("config", "user.email", "issue14-tests@example.invalid")
        git("config", "user.name", "Issue 14 Tests")
        for _role, relative in contract.FIXTURE_SPECS:
            path = repository / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}\n", encoding="utf-8")
        contract_path = repository / "scripts" / "issue14_exit_evidence_contract.py"
        contract_path.parent.mkdir(parents=True, exist_ok=True)
        contract_path.write_text("BASE = True\n", encoding="utf-8")
        git("add", ".")
        git("commit", "-m", "base")
        slice_base_commit = git("rev-parse", "HEAD")

        contract_path.write_text("BASE = True\nCUTOVER = True\n", encoding="utf-8")
        feature_path = repository / "src" / "video2pdf_workflow_kernel" / "platform_feature.py"
        feature_path.parent.mkdir(parents=True, exist_ok=True)
        feature_path.write_text("ACTIVE_PLATFORM = 'youtube'\n", encoding="utf-8")
        excluded = repository / contract.EVIDENCE_PREFIX / "preexisting-evidence.json"
        excluded.parent.mkdir(parents=True, exist_ok=True)
        excluded.write_text("{}\n", encoding="utf-8")
        git("add", ".")
        git("commit", "-m", "implementation")
        implementation_commit = git("rev-parse", "HEAD")

        runtime = repository / "runtime"
        artifact_bindings: dict[str, dict[str, str]] = {}
        for role in (
            "run_record", "source_manifest", "acceptance_report_v2",
            "delivery_guard_report", "video_delivery_target",
            "session_delivery_target", "delivery_task_index",
            "global_gate_authority", "final_pdf",
        ):
            path = runtime / f"{role}.bin"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((role + "\n").encode("utf-8"))
            artifact_bindings[role] = {"path": str(path.resolve()), "sha256": _sha256(path)}
        qualification_bindings: dict[str, dict[str, str]] = {}
        for role in ("command_record", "terminal_status", "exit_code_artifact"):
            path = runtime / "qualification" / f"{role}.bin"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((role + "\n").encode("utf-8"))
            qualification_bindings[role] = {"path": str(path.resolve()), "sha256": _sha256(path)}

        collection_path = repository / contract.EVIDENCE_PREFIX / "collection.json"
        manifest_path = repository / contract.EVIDENCE_PREFIX / "exit-evidence-manifest.json"
        _write_json(
            collection_path,
            {
                "schema_name": "issue14-exit-evidence-collection",
                "schema_version": "1.0.0",
                "run_id": "1" * 32,
                "canonical_platform": "youtube",
                "delivery_stage": "delivered",
                "artifacts": artifact_bindings,
                "qualification_run": {
                    "run_id": "14141414-1414-4414-8414-141414141414",
                    **qualification_bindings,
                },
            },
        )
        with (
            patch.object(collector, "PROJECT_ROOT", repository),
            patch.object(collector, "SLICE_BASE_COMMIT", slice_base_commit),
        ):
            manifest = collector.finalize(
                collection_path=collection_path,
                manifest_path=manifest_path,
            )
        expected = fingerprint_implementation_changes(
            repository,
            slice_base_commit,
            implementation_commit,
            excluded_prefixes=(contract.EVIDENCE_PREFIX,),
        )

        self.assertEqual(expected, manifest["artifact_fingerprints"])

    def test_collect_rejects_nonpassing_acceptance_or_delivery_guard(self) -> None:
        mutations = {
            "acceptance_report_v2": (
                "review/acceptance/acceptance_report.json",
                "overall_status",
                "fail",
            ),
            "delivery_guard_report": (
                "review/acceptance/delivery_guard_report.json",
                "status",
                "blocked",
            ),
        }
        for role, (relative_path, field, value) in mutations.items():
            with self.subTest(role=role):
                run_dir, current_target, qualification_run = self._isolated_delivered_youtube_fixture()
                artifact_path = run_dir / relative_path
                artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
                artifact[field] = value
                _write_json(artifact_path, artifact)
                self._refresh_delivery_binding_chain(run_dir)

                with self.assertRaises(collector.CollectionError):
                    collector.collect(
                        run_dir=run_dir,
                        current_target=current_target,
                        qualification_run_dir=qualification_run,
                        output=run_dir.parents[1] / f"{role}-collection.json",
                    )

    def test_platform_reconcile_rejects_evidence_or_global_gate_drift(self) -> None:
        from tests.video_workflow.test_issue14_platform_cutover import (
            Issue14PlatformCutoverTests,
            _run_cli as run_platform_cli,
        )

        for drift in ("exit_evidence", "global_gate"):
            with self.subTest(drift=drift):
                helper = Issue14PlatformCutoverTests(methodName="test_exact_youtube_activation_retry_is_idempotent")
                control_store_root, exit_evidence = helper._write_valid_cutover_manifest()
                interrupted = run_platform_cli(
                    "platform-kernel-activate",
                    "--platform", "youtube",
                    "--control-store-root", str(control_store_root),
                    "--exit-evidence", str(exit_evidence),
                    "--activated-at", "2026-08-12T00:00:00Z",
                    "--fault-point", "after_intent",
                )
                self.assertNotEqual(0, interrupted.returncode, interrupted.stdout)

                drift_path = (
                    exit_evidence
                    if drift == "exit_evidence"
                    else control_store_root / "active_global_gate.json"
                )
                drift_path.write_bytes(drift_path.read_bytes() + b" \n")
                reconciled = run_platform_cli(
                    "platform-kernel-reconcile",
                    "--platform", "youtube",
                    "--control-store-root", str(control_store_root),
                )

                self.assertNotEqual(0, reconciled.returncode, reconciled.stdout)
                with sqlite3.connect(control_store_root / "platform-kernel-control.sqlite3") as db:
                    committed = db.execute(
                        "SELECT COUNT(*) FROM platform_cutover_authority"
                    ).fetchone()[0]
                self.assertEqual(0, committed)

    def test_platform_activation_rejects_self_declared_guarded_delivery(self) -> None:
        # scenario_id=self_declared_guarded_delivery; target_invariant=guarded_delivery_evidence;
        # mutation_seam=guarded_delivery_evidence; rematerialized_nodes=none;
        # intentionally_stale_nodes=guarded_delivery_evidence; scenario_class=self_declaration.
        scenario = json.loads(
            (ROOT / "tests/video_workflow/fixtures/exit_evidence/slice13.self-declared.invalid.json").read_text(encoding="utf-8")
        )
        invalid = self.manifest()
        invalid.pop("guarded_delivery_evidence")
        with self.assertRaises(validator.EvidenceError) as caught:
            validator.validate_issue14_cutover(invalid)
        self.assertEqual(scenario["expected_first_failing_gate"], caught.exception.first_failing_gate)
        self.assertEqual(scenario["expected_error_code"], caught.exception.error_code)


if __name__ == "__main__":
    unittest.main()
