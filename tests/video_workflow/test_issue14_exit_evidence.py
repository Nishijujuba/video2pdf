from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import unittest
from typing import Any
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
        """Real, recomputable Bilibili regression proof.

        Revalidates the committed slice-12 exit evidence end to end through the
        shared validator, which decodes the real acceptance report v2, delivery
        guard report, and persisted qualification run (validate_bindings'
        slice-12 guarded-delivery branch). The manifest passes post-publication
        validation today, so this regression test fails if any slice-12 evidence
        stops validating.
        """
        self.assertEqual(
            {"bilibili": "active_kernel", "youtube": "active_kernel"},
            self.manifest()["platform_statuses"],
        )
        manifest_path = ROOT / "evidence/slice-12/exit-evidence-manifest.json"
        validator.validate_manifest(
            manifest_path, schema_only=False, pre_publication=False
        )

    def test_bilibili_regression_fails_on_tampered_evidence(self) -> None:
        """Slice-12 regression tamper proof on a temp copy of real evidence.

        Copies the committed slice-12 manifest and every declared evidence file
        into a scratch root (never touching committed evidence), tampers one
        copy, and drives the validator against the tampered path. Tampering the
        acceptance report or delivery guard report must fail closed at
        ``guarded_delivery_evidence`` with ``guarded_delivery_decision_invalid``.
        """
        cases = (
            ("acceptance_report_v2", "overall_status", "fail"),
            ("delivery_guard_report", "status", "blocked"),
        )
        for role, field, value in cases:
            with self.subTest(role=role):
                scratch, manifest_copy, guarded = self._copy_slice12_evidence_for_tamper()
                artifact_binding = next(
                    item
                    for item in guarded["artifacts"]
                    if item["role"] == role
                )
                artifact_path = scratch / artifact_binding["path"]
                artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
                artifact[field] = value
                artifact_path.write_text(
                    json.dumps(artifact, ensure_ascii=False, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                manifest_copy = self._rewrite_manifest_binding(
                    manifest_copy, artifact_path
                )
                manifest_value = json.loads(
                    manifest_copy.read_text(encoding="utf-8")
                )
                with (
                    patch.object(validator, "PROJECT_ROOT", scratch),
                    patch.object(
                        validator,
                        "sha256_git_blob",
                        side_effect=self._fixture_git_blob_stub(manifest_copy),
                    ),
                    self.assertRaises(validator.EvidenceError) as caught,
                ):
                    validator.validate_bindings(manifest_value, manifest_copy)
                self.assertEqual(
                    "guarded_delivery_evidence", caught.exception.first_failing_gate
                )
                self.assertEqual(
                    "guarded_delivery_decision_invalid", caught.exception.error_code
                )

    def test_bilibili_regression_fails_on_tampered_qualification_run(self) -> None:
        """Slice-12 qualification command record mutation must fail closed."""
        scratch, manifest_copy, guarded = self._copy_slice12_evidence_for_tamper()
        command_path = scratch / guarded["qualification_run"]["command_record"]["path"]
        command = json.loads(command_path.read_text(encoding="utf-8"))
        command["argv"] = [*command["argv"], "tests.video_workflow.test_unrelated"]
        command_path.write_text(
            json.dumps(command, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        manifest_copy = self._rewrite_manifest_binding(manifest_copy, command_path)
        manifest_value = json.loads(manifest_copy.read_text(encoding="utf-8"))
        with (
            patch.object(validator, "PROJECT_ROOT", scratch),
            patch.object(
                validator,
                "sha256_git_blob",
                side_effect=self._fixture_git_blob_stub(manifest_copy),
            ),
            self.assertRaises(validator.EvidenceError) as caught,
        ):
            validator.validate_bindings(manifest_value, manifest_copy)
        self.assertEqual(
            "guarded_delivery_evidence", caught.exception.first_failing_gate
        )
        self.assertEqual(
            "guarded_delivery_qualification_identity_stale",
            caught.exception.error_code,
        )

    def test_bilibili_guarded_qualification_accepts_missing_absolute_cwd(self) -> None:
        """Blocker 2: slice-12 published evidence qualifies when its cwd
        directory no longer exists.

        The qualification command was recorded on the producing machine; after
        cross-machine relocation, or after the origin worktree is deleted, the
        recorded cwd directory is absent. The qualification check must treat
        cwd as syntactic identity (a non-empty absolute path) and must not
        require the directory to exist.
        """
        scratch, manifest_copy, guarded = self._copy_slice12_evidence_for_tamper()
        command_path = scratch / guarded["qualification_run"]["command_record"]["path"]
        command = json.loads(command_path.read_text(encoding="utf-8"))
        missing_cwd = str(scratch / "deleted-origin-worktree")
        self.assertFalse(Path(missing_cwd).exists())
        command["cwd"] = missing_cwd
        command_path.write_text(
            json.dumps(command, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        manifest_copy = self._rewrite_manifest_binding(manifest_copy, command_path)
        manifest_value = json.loads(manifest_copy.read_text(encoding="utf-8"))
        with (
            patch.object(validator, "PROJECT_ROOT", scratch),
            patch.object(
                validator,
                "sha256_git_blob",
                side_effect=self._fixture_git_blob_stub(manifest_copy),
            ),
        ):
            validator.validate_bindings(manifest_value, manifest_copy)

    def test_bilibili_guarded_qualification_rejects_syntactically_invalid_cwd(self) -> None:
        """Slice-12 qualification cwd must be a non-empty absolute path.

        The directory-existence check was replaced by a syntactic check; a
        relative recorded cwd is not a valid execution-environment identity and
        must still fail closed.
        """
        scratch, manifest_copy, guarded = self._copy_slice12_evidence_for_tamper()
        command_path = scratch / guarded["qualification_run"]["command_record"]["path"]
        command = json.loads(command_path.read_text(encoding="utf-8"))
        command["cwd"] = "relative/path"
        command_path.write_text(
            json.dumps(command, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        manifest_copy = self._rewrite_manifest_binding(manifest_copy, command_path)
        manifest_value = json.loads(manifest_copy.read_text(encoding="utf-8"))
        with (
            patch.object(validator, "PROJECT_ROOT", scratch),
            patch.object(
                validator,
                "sha256_git_blob",
                side_effect=self._fixture_git_blob_stub(manifest_copy),
            ),
            self.assertRaises(validator.EvidenceError) as caught,
        ):
            validator.validate_bindings(manifest_value, manifest_copy)
        self.assertEqual(
            "guarded_delivery_evidence", caught.exception.first_failing_gate
        )
        self.assertEqual(
            "guarded_delivery_qualification_identity_stale",
            caught.exception.error_code,
        )

    def _copy_slice12_evidence_for_tamper(self) -> tuple[Path, Path, dict]:
        """Copy the committed slice-12 manifest and evidence closure to scratch.

        Copies the delivery-quality registry authority too so acceptance
        validation can run against the patched root. Fixture git-blob
        fingerprinting is stubbed by the caller. Returns the scratch root, the
        manifest copy, and the copied manifest's guarded-delivery section.
        """
        source_manifest = ROOT / "evidence/slice-12/exit-evidence-manifest.json"
        source = json.loads(source_manifest.read_text(encoding="utf-8"))
        scratch = new_case_dir(self.id(), label="slice12-tamper-scratch")
        scratch_manifest = scratch / "evidence/slice-12/exit-evidence-manifest.json"
        scratch_manifest.parent.mkdir(parents=True)
        shutil.copy2(source_manifest, scratch_manifest)
        for relative in source["evidence_paths"]:
            src = ROOT / relative
            if not src.is_file():
                continue
            target = scratch / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
        for directory in ("schemas", "delivery-quality"):
            target = scratch / directory
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(ROOT / directory, target)
        copied = json.loads(scratch_manifest.read_text(encoding="utf-8"))
        return scratch, scratch_manifest, copied["guarded_delivery_evidence"]

    def _fixture_git_blob_stub(self, manifest_path: Path) -> Any:
        """Return a sha256_git_blob stub keyed by the copied manifest bindings.

        The copied evidence files (delivery projections, persisted runs) drift
        on disk from the committed blob that the manifest fingerprints anchor
        to, so the stub returns every declared binding's manifest sha256. This
        makes the anchored-fingerprint gate pass and lets tamper scenarios reach
        the guarded-delivery authority gates that own the mutation.
        """
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        declared: dict[str, str] = {}
        for item in manifest["fixtures"]:
            declared[item["path"]] = item["sha256"]

        def collect(group: object) -> None:
            if isinstance(group, dict) and isinstance(group.get("path"), str):
                declared[group["path"]] = group["sha256"]
                return
            if isinstance(group, list):
                for value in group:
                    collect(value)

        for command in manifest["commands"]:
            collect(command.get("log"))
            collect(command.get("persisted_run"))
        guarded = manifest.get("guarded_delivery_evidence")
        if isinstance(guarded, dict):
            collect(guarded.get("collection"))
            collect(guarded.get("artifacts"))
            collect(guarded.get("qualification_run"))

        def stub(project_root: Path, commit: str, path: str) -> str:
            return declared[path]

        return stub

    def _rewrite_manifest_binding(
        self, manifest_path: Path, artifact_path: Path
    ) -> Path:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        relative = artifact_path.relative_to(manifest_path.parents[2]).as_posix()
        for group in (
            manifest["guarded_delivery_evidence"].get("artifacts", []),
            [manifest["guarded_delivery_evidence"].get("collection")],
            [
                item
                for item in manifest["guarded_delivery_evidence"]
                .get("qualification_run", {})
                .values()
                if isinstance(item, dict)
            ],
        ):
            for item in group:
                if isinstance(item, dict) and item.get("path") == relative:
                    item["sha256"] = _sha256(artifact_path)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return manifest_path

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

    def _slice13_guarded_fixture(self, scratch: Path) -> tuple[Path, dict]:
        """Materialize a slice-13-shaped guarded manifest in a scratch root.

        Writes the guarded artifacts, two per-command persisted qualification
        runs, and command logs, then returns (manifest_path, manifest_value)
        with in-repo relative bindings. All artifacts and logs are declared in
        evidence_paths so the shared guarded-delivery helper can resolve them.
        """
        guarded_root = scratch / "guarded-delivery"
        persisted_root = scratch / "persisted"
        for directory in ("schemas", "delivery-quality"):
            target = scratch / directory
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(ROOT / directory, target)
        run_id = "14141414141414141414141414141414"
        qualification_run_id = "14141414-1414-4414-8414-141414141414"
        evidence_paths = [
            "exit-evidence-manifest.json",
            *[f"logs/{command_id}.log" for command_id, _, _ in contract.COMMANDS],
            *[
                f"persisted/{command_id}/{filename}"
                for command_id, _, _ in contract.COMMANDS
                for filename in ("command.json", "status.json", "exit-code.txt")
            ],
        ]

        def write_binding(relative: str, payload: bytes) -> dict[str, str]:
            path = scratch / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            return {"path": relative, "sha256": hashlib.sha256(payload).hexdigest()}

        artifacts = []
        for role in (
            "run_record", "source_manifest", "acceptance_report_v2",
            "delivery_guard_report", "video_delivery_target",
            "session_delivery_target", "delivery_task_index",
            "global_gate_authority", "final_pdf",
        ):
            if role == "acceptance_report_v2":
                payload = _acceptance_report(run_id, 3, "pass")
            elif role == "delivery_guard_report":
                payload = _guard_report("pass")
            else:
                payload = {"role": role}
            relative = f"guarded-delivery/{role}.json"
            binding = write_binding(
                relative,
                json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"),
            )
            artifacts.append({"role": role, **binding})

        commands = []
        persisted_run_id = qualification_run_id
        for index, (command_id, command, expected_exit) in enumerate(contract.COMMANDS, 1):
            run_id_for_command = f"14141414-1414-4414-8414-{index:012d}"
            command_record_payload = json.dumps(
                {
                    "schema_name": "persisted-command",
                    "schema_version": "1.0.0",
                    "run_id": run_id_for_command,
                    "cwd": str(scratch.resolve()),
                    "argv": list(command),
                    "accepted_exit_codes": [expected_exit],
                    "git_commit": "2" * 40,
                    "worktree_clean": True,
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8") + b"\n"
            status_payload = json.dumps(
                {
                    "schema_name": "persisted-command-status",
                    "schema_version": "1.0.0",
                    "run_id": run_id_for_command,
                    "state": "succeeded",
                    "exit_code": expected_exit,
                    "security": {
                        "acceptance_evidence_eligible": True,
                        "classification": "no_secret_detected",
                    },
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8") + b"\n"
            log_payload = (
                f"qualified command: {command_id}\n"
                f"EVIDENCE_IMPLEMENTATION_COMMIT: {'2' * 40}\n"
            ).encode("utf-8")
            source_log_payload = log_payload[: -(len("EVIDENCE_IMPLEMENTATION_COMMIT: " + "2" * 40 + "\n"))]
            persisted = {
                "run_id": run_id_for_command,
                "command_record": write_binding(
                    f"persisted/{command_id}/command.json", command_record_payload
                ),
                "terminal_status": write_binding(
                    f"persisted/{command_id}/status.json", status_payload
                ),
                "exit_code": write_binding(
                    f"persisted/{command_id}/exit-code.txt", f"{expected_exit}\n".encode("utf-8")
                ),
            }
            log_binding = write_binding(f"logs/{command_id}.log", log_payload)
            evidence_paths.extend(
                binding["path"]
                for binding in persisted.values()
                if isinstance(binding, dict) and isinstance(binding.get("path"), str)
            )
            commands.append(
                {
                    "test_id": command_id,
                    "command": list(command),
                    "expected_exit_code": expected_exit,
                    "actual_exit_code": expected_exit,
                    "source_log_sha256": hashlib.sha256(source_log_payload).hexdigest(),
                    "published_log_sha256": hashlib.sha256(log_payload).hexdigest(),
                    "log": {"role": "command_log", **log_binding},
                    "persisted_run": persisted,
                    "conforms": True,
                }
            )
        collection_binding = write_binding(
            "guarded-delivery/collection.json",
            json.dumps({"schema_name": "issue14-exit-evidence-collection"}).encode("utf-8"),
        )
        manifest = self.manifest()
        manifest["implementation_commit"] = "2" * 40
        manifest["evidence_paths"] = sorted(set(evidence_paths))
        manifest["guarded_delivery_evidence"] = {
            "collection": {"role": "guarded_delivery_collection", **collection_binding},
            "run_id": run_id,
            "canonical_platform": "youtube",
            "delivery_stage": "delivered",
            "artifacts": artifacts,
            "qualification_run": commands[1]["persisted_run"],
        }
        manifest["commands"] = commands
        manifest_path = scratch / "exit-evidence-manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return manifest_path, manifest

    def _slice13_tamper_cases(self) -> list[tuple[str, callable, str, str]]:
        """Return (label, mutation, expected_gate, expected_code) cases.

        Each mutation tampers the on-disk fixture files that the shared
        guarded-delivery helper decodes; the helper must fail closed at the
        expected gate. The mutation signature is ``mutation(scratch, manifest)``.
        """
        cases: list[tuple[str, callable, str, str]] = []

        def nonpassing_acceptance(scratch: Path, manifest: dict) -> None:
            path = scratch / "guarded-delivery/acceptance_report_v2.json"
            report = _acceptance_report("14141414141414141414141414141414", 3, "fail")
            path.write_text(
                json.dumps(report, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )

        cases.append(
            (
                "nonpassing_acceptance",
                nonpassing_acceptance,
                "guarded_delivery_evidence",
                "guarded_delivery_decision_invalid",
            )
        )

        def nonpassing_guard(scratch: Path, manifest: dict) -> None:
            path = scratch / "guarded-delivery/delivery_guard_report.json"
            guard = _guard_report("blocked")
            path.write_text(
                json.dumps(guard, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )

        cases.append(
            (
                "nonpassing_guard",
                nonpassing_guard,
                "guarded_delivery_evidence",
                "guarded_delivery_decision_invalid",
            )
        )

        def run_id_mismatch(scratch: Path, manifest: dict) -> None:
            path = scratch / "persisted/issue14-platform-cutover-tests/command.json"
            record = json.loads(path.read_text(encoding="utf-8"))
            record["run_id"] = "9" * 36
            path.write_text(
                json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )

        cases.append(
            (
                "run_id_mismatch",
                run_id_mismatch,
                "guarded_delivery_evidence",
                "guarded_delivery_qualification_failed",
            )
        )

        def commit_identity_mismatch(scratch: Path, manifest: dict) -> None:
            path = scratch / "persisted/issue14-platform-cutover-tests/command.json"
            record = json.loads(path.read_text(encoding="utf-8"))
            record["git_commit"] = "0" * 40
            path.write_text(
                json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )

        cases.append(
            (
                "commit_identity_mismatch",
                commit_identity_mismatch,
                "guarded_delivery_evidence",
                "guarded_delivery_qualification_identity_stale",
            )
        )

        def status_tamper(scratch: Path, manifest: dict) -> None:
            path = scratch / "persisted/issue14-platform-cutover-tests/status.json"
            record = json.loads(path.read_text(encoding="utf-8"))
            record["state"] = "failed"
            path.write_text(
                json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )

        cases.append(
            (
                "status_tamper",
                status_tamper,
                "guarded_delivery_evidence",
                "guarded_delivery_qualification_failed",
            )
        )

        def exit_code_tamper(scratch: Path, manifest: dict) -> None:
            path = scratch / "persisted/issue14-platform-cutover-tests/exit-code.txt"
            path.write_text("1\n", encoding="utf-8")

        cases.append(
            (
                "exit_code_tamper",
                exit_code_tamper,
                "guarded_delivery_evidence",
                "guarded_delivery_qualification_failed",
            )
        )

        def missing_log(scratch: Path, manifest: dict) -> None:
            manifest["commands"][1]["log"]["path"] = "logs/missing.log"

        cases.append(
            (
                "missing_log",
                missing_log,
                "guarded_delivery_evidence",
                "guarded_delivery_qualification_invalid",
            )
        )

        def cwd_relative(scratch: Path, manifest: dict) -> None:
            path = scratch / "persisted/issue14-platform-cutover-tests/command.json"
            record = json.loads(path.read_text(encoding="utf-8"))
            record["cwd"] = "relative/path"
            path.write_text(
                json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )

        cases.append(
            (
                "cwd_relative",
                cwd_relative,
                "guarded_delivery_evidence",
                "guarded_delivery_qualification_identity_stale",
            )
        )
        return cases

    def test_slice13_guarded_qualification_tamper_gates(self) -> None:
        """R4: slice-13 guarded-delivery tamper scenarios fail closed.

        Drives each mutation through the shared guarded-delivery qualification
        helper with a patched PROJECT_ROOT, asserting the stable
        first_failing_gate and error_code.
        """
        for label, mutate, expected_gate, expected_code in self._slice13_tamper_cases():
            with self.subTest(label=label):
                scratch = new_case_dir(self.id(), label=f"slice13-{label}")
                _manifest_path, manifest = self._slice13_guarded_fixture(scratch)
                mutate(scratch, manifest)
                with (
                    patch.object(validator, "PROJECT_ROOT", scratch),
                    self.assertRaises(validator.EvidenceError) as caught,
                ):
                    validator._validate_guarded_delivery_qualification(
                        manifest,
                        issue_commands=contract.COMMANDS,
                        issue_label="Issue #14",
                    )
                self.assertEqual(expected_gate, caught.exception.first_failing_gate)
                self.assertEqual(expected_code, caught.exception.error_code)

    def test_slice13_guarded_qualification_accepts_missing_absolute_cwd(self) -> None:
        """Blocker 2: a deleted-origin cwd still qualifies when absolute.

        The persisted qualification runs were recorded on the machine where
        the evidence was produced. After cross-machine relocation, or after the
        origin worktree is deleted, the recorded cwd directory no longer
        exists. Qualification must treat cwd as syntactic identity (a non-empty
        absolute path) and must not require the directory to exist.
        """
        scratch = new_case_dir(self.id(), label="slice13-missing-cwd")
        _manifest_path, manifest = self._slice13_guarded_fixture(scratch)
        missing_cwd = str(scratch / "deleted-origin-worktree")
        self.assertFalse(Path(missing_cwd).exists())
        for command_id, _, _ in contract.COMMANDS:
            path = scratch / f"persisted/{command_id}/command.json"
            record = json.loads(path.read_text(encoding="utf-8"))
            record["cwd"] = missing_cwd
            path.write_text(
                json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        with patch.object(validator, "PROJECT_ROOT", scratch):
            validator._validate_guarded_delivery_qualification(
                manifest,
                issue_commands=contract.COMMANDS,
                issue_label="Issue #14",
            )

    def test_slice13_guarded_qualification_pass(self) -> None:
        """R4: the shared helper accepts a valid slice-13 guarded manifest."""
        scratch = new_case_dir(self.id(), label="slice13-guarded-pass")
        _manifest_path, manifest = self._slice13_guarded_fixture(scratch)
        with patch.object(validator, "PROJECT_ROOT", scratch):
            validator._validate_guarded_delivery_qualification(
                manifest,
                issue_commands=contract.COMMANDS,
                issue_label="Issue #14",
            )

    def test_validate_bindings_ignores_persisted_run_metadata_string_fields(self) -> None:
        """validate_bindings must not crash on real collector output.

        The collector (1f3357bd) adds optional ``executable_sha256`` and
        ``python_version`` metadata string fields to every command's
        ``persisted_run``. validate_bindings collects persisted terminal
        artifacts by iterating persisted_run values; it must skip the string
        metadata fields (and any non-dict value) instead of indexing into them.
        """
        scratch = new_case_dir(self.id(), label="slice13-metadata-fields")
        manifest_path, manifest = self._slice13_guarded_fixture(scratch)
        for command in manifest["commands"]:
            command["persisted_run"]["executable_sha256"] = "0" * 64
            command["persisted_run"]["python_version"] = "3.12.4"
        guarded = manifest["guarded_delivery_evidence"]
        manifest["evidence_paths"] = sorted(
            set(manifest["evidence_paths"])
            | {guarded["collection"]["path"]}
            | {artifact["path"] for artifact in guarded["artifacts"]}
        )
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        with (
            patch.object(validator, "PROJECT_ROOT", scratch),
            patch.object(
                validator,
                "sha256_git_blob",
                side_effect=self._fixture_git_blob_stub(manifest_path),
            ),
        ):
            validator.validate_bindings(
                json.loads(manifest_path.read_text(encoding="utf-8")),
                manifest_path,
            )

    def test_contract_and_collector_skeleton_close_fourteen_members(self) -> None:
        skeleton = collector.qualification_manifest_skeleton()
        self.assertEqual(14, len(contract.ATOMIC_MEMBERS))
        self.assertEqual(list(contract.ATOMIC_MEMBERS), skeleton["atomic_members"])
        self.assertEqual(contract.ACTIVATION_SCOPE, skeleton["activation_scope"])

    def _isolated_delivered_youtube_fixture(
        self,
        *,
        project_root: Path | None = None,
        git_commit: str | None = None,
        worktree_clean: bool = True,
    ) -> tuple[Path, Path, dict[str, Path]]:
        project = project_root or new_case_dir(self.id(), label="issue14-exit-evidence")
        cwd = project_root or ROOT
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

        qualification_runs: dict[str, Path] = {}
        for index, (command_id, command_argv, expected_exit) in enumerate(contract.COMMANDS, 1):
            qualification_run = project / "qualification-run" / command_id
            qualification_run.mkdir(parents=True)
            qualification_run_id = f"14141414-1414-4414-8414-{index:012d}"
            resolved_commit = git_commit or subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=ROOT,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=True,
            ).stdout.strip()
            _write_json(
                qualification_run / "command.json",
                {
                    "schema_name": "persisted-command",
                    "schema_version": "1.0.0",
                    "run_id": qualification_run_id,
                    "cwd": str(cwd.resolve()),
                    "argv": list(command_argv),
                    "accepted_exit_codes": [expected_exit],
                    "git_commit": resolved_commit,
                    "worktree_clean": worktree_clean,
                },
            )
            _write_json(
                qualification_run / "status.json",
                {
                    "schema_name": "persisted-command-status",
                    "schema_version": "1.0.0",
                    "run_id": qualification_run_id,
                    "state": "succeeded",
                    "exit_code": expected_exit,
                    "security": {"acceptance_evidence_eligible": True, "classification": "no_secret_detected"},
                },
            )
            (qualification_run / "exit-code.txt").write_text(f"{expected_exit}\n", encoding="utf-8")
            (qualification_run / "command.log").write_text(
                f"qualified command: {command_id}\n", encoding="utf-8"
            )
            qualification_runs[command_id] = qualification_run
        return run_dir, current_target, qualification_runs

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
        run_dir, current_target, qualification_runs = self._isolated_delivered_youtube_fixture()
        collection_path = run_dir.parent.parent / "collection.json"
        manifest_path = run_dir.parent.parent / "exit-evidence-manifest.json"

        collected = subprocess.run(
            [
                sys.executable, "-X", "utf8", "-B", "-m",
                "scripts.collect_issue14_exit_evidence", "collect",
                "--run-dir", str(run_dir),
                "--current-target", str(current_target),
                "--qualification-run-dir", f"{contract.COMMANDS[0][0]}={qualification_runs[contract.COMMANDS[0][0]]}",
                "--qualification-run-dir", f"{contract.COMMANDS[1][0]}={qualification_runs[contract.COMMANDS[1][0]]}",
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
            self.assertFalse(Path(binding["path"]).is_absolute())
        self.assertEqual("succeeded", collection["qualification_run"]["state"])
        self.assertTrue(collection["qualification_run"]["acceptance_evidence_eligible"])
        self.assertEqual(
            {command_id for command_id, _, _ in contract.COMMANDS},
            set(collection["qualification_runs"]),
        )

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
        if finalized.returncode == 0:
            schema = json.loads((ROOT / "schemas/exit-evidence-manifest.v2.schema.json").read_text(encoding="utf-8"))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            Draft202012Validator(schema).validate(manifest)
            self.assertEqual("delivered", manifest["guarded_delivery_evidence"]["delivery_stage"])
            return
        # The finalization anchor gate fails closed when the current worktree
        # carries non-evidence changes (a dirty checkout) or HEAD has moved
        # past the qualification runs' execution-time commit.
        self.assertEqual(2, finalized.returncode, finalized.stdout + finalized.stderr)
        self.assertIn("finalize requires", finalized.stderr)
        self.assertFalse(manifest_path.exists())

    def test_collect_rejects_succeeded_run_outside_closed_issue14_qualification(self) -> None:
        mutations = {
            "argv": lambda command: command.__setitem__(
                "argv", [*command["argv"], "tests.video_workflow.test_unrelated"]
            ),
            "interpreter_role": lambda command: command.__setitem__(
                "argv", ["C:\\tools\\ruby.exe", *command["argv"][1:]]
            ),
            "accepted_exit_codes": lambda command: command.__setitem__(
                "accepted_exit_codes", [0, 1]
            ),
            "git_commit": lambda command: command.__setitem__("git_commit", "0" * 40),
            "worktree_clean": lambda command: command.__setitem__("worktree_clean", False),
        }
        for field, mutate in mutations.items():
            with self.subTest(field=field):
                run_dir, current_target, qualification_runs = self._isolated_delivered_youtube_fixture()
                command_path = qualification_runs[contract.COMMANDS[1][0]] / "command.json"
                command = json.loads(command_path.read_text(encoding="utf-8"))
                mutate(command)
                _write_json(command_path, command)
                output = run_dir.parents[1] / f"{field}-collection.json"

                with self.assertRaises(collector.CollectionError):
                    collector.collect(
                        run_dir=run_dir,
                        current_target=current_target,
                        qualification_runs=qualification_runs,
                        output=output,
                    )
                self.assertFalse(output.exists())

    def test_collect_rejects_missing_or_mismatched_qualification_run(self) -> None:
        run_dir, current_target, qualification_runs = self._isolated_delivered_youtube_fixture()
        output = run_dir.parents[1] / "missing-run-collection.json"
        with self.assertRaises(collector.CollectionError):
            collector.collect(
                run_dir=run_dir,
                current_target=current_target,
                qualification_runs={
                    contract.COMMANDS[0][0]: qualification_runs[contract.COMMANDS[0][0]],
                },
                output=output,
            )
        self.assertFalse(output.exists())

        other = new_case_dir(self.id(), label="issue14-mismatched-commit")
        run_dir2, current_target2, qualification_runs2 = self._isolated_delivered_youtube_fixture()
        swapped = dict(qualification_runs)
        swapped[contract.COMMANDS[1][0]] = other / "unrelated-run"
        swapped[contract.COMMANDS[1][0]].mkdir(parents=True)
        with self.assertRaises(collector.CollectionError):
            collector.collect(
                run_dir=run_dir2,
                current_target=current_target2,
                qualification_runs=swapped,
                output=output,
            )

    def _init_repository(self, label: str) -> tuple[Path, callable]:
        repository = new_case_dir(self.id(), label=label)

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
        git("config", "core.autocrlf", "false")
        return repository, git

    def _build_finalize_repository(self, label: str) -> tuple[Path, callable, str, str]:
        """Build a fresh git repo with a real slice-13-style qualification closure.

        Returns (repository, git, slice_base_commit, implementation_commit).
        The worktree at the implementation commit is clean except for the
        evidence closure written later by the caller.
        """
        repository, git = self._init_repository(label)
        (repository / ".gitignore").write_text("qualification-run/\nruntime/\n", encoding="utf-8")
        for _role, relative in contract.FIXTURE_SPECS:
            path = repository / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}\n", encoding="utf-8")
        for directory in ("schemas", "delivery-quality"):
            shutil.copytree(
                ROOT / directory,
                repository / directory,
                dirs_exist_ok=True,
            )
        contract_fixtures = repository / "tests/video_workflow/fixtures/contracts"
        contract_fixtures.mkdir(parents=True)
        shutil.copytree(
            ROOT / "tests/video_workflow/fixtures/contracts",
            contract_fixtures,
            dirs_exist_ok=True,
        )
        shutil.copytree(
            ROOT / "tests/video_workflow/fixtures/delivery-quality",
            repository / "tests/video_workflow/fixtures/delivery-quality",
            dirs_exist_ok=True,
        )
        requirements = repository / "requirements"
        requirements.mkdir(exist_ok=True)
        for name in ("video-workflow-runtime.in", "pylock.video-workflow-runtime.toml"):
            shutil.copy2(ROOT / "requirements" / name, requirements / name)
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
        return repository, git, slice_base_commit, implementation_commit

    def _synthetic_issue14_collection(
        self,
        repository: Path,
        *,
        implementation_commit: str,
    ) -> tuple[Path, dict[str, dict[str, str]]]:
        """Write a collection for a temp repository at its implementation commit.

        Qualification run records live under the gitignored ``runtime/`` tree so
        the finalization anchor only sees declared evidence changes.
        """
        artifact_bindings: dict[str, dict[str, str]] = {}
        for role in (
            "run_record", "source_manifest", "acceptance_report_v2",
            "delivery_guard_report", "video_delivery_target",
            "session_delivery_target", "delivery_task_index",
            "global_gate_authority", "final_pdf",
        ):
            path = repository / "runtime" / f"{role}.bin"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((role + "\n").encode("utf-8"))
            artifact_bindings[role] = {
                "path": path.relative_to(repository).as_posix(),
                "sha256": _sha256(path),
            }
        qualification_runs: dict[str, dict[str, Any]] = {}
        for index, (command_id, command, expected_exit) in enumerate(contract.COMMANDS, 1):
            run_dir = repository / "runtime" / "qualification" / command_id
            run_dir.mkdir(parents=True)
            run_id = f"14141414-1414-4414-8414-{index:012d}"
            command_path = run_dir / "command.json"
            command_path.write_text(
                json.dumps({
                    "schema_name": "persisted-command",
                    "schema_version": "1.0.0",
                    "run_id": run_id,
                    "cwd": str(repository.resolve()),
                    "argv": list(command),
                    "accepted_exit_codes": [expected_exit],
                    "git_commit": implementation_commit,
                    "worktree_clean": True,
                }, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            status_path = run_dir / "status.json"
            status_path.write_text(
                json.dumps({
                    "schema_name": "persisted-command-status",
                    "schema_version": "1.0.0",
                    "run_id": run_id,
                    "state": "succeeded",
                    "exit_code": expected_exit,
                    "security": {
                        "acceptance_evidence_eligible": True,
                        "classification": "no_secret_detected",
                    },
                }, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            exit_path = run_dir / "exit-code.txt"
            exit_path.write_text(f"{expected_exit}\n", encoding="utf-8")
            log_path = run_dir / "command.log"
            log_path.write_text(
                f"qualified command: {command_id}\n", encoding="utf-8"
            )
            qualification_runs[command_id] = {
                "run_id": run_id,
                "state": "succeeded",
                "exit_code": expected_exit,
                "acceptance_evidence_eligible": True,
                "git_commit": implementation_commit,
                "worktree_clean": True,
                "command_record": {
                    "path": command_path.relative_to(repository).as_posix(),
                    "sha256": _sha256(command_path),
                },
                "terminal_status": {
                    "path": status_path.relative_to(repository).as_posix(),
                    "sha256": _sha256(status_path),
                },
                "exit_code_artifact": {
                    "path": exit_path.relative_to(repository).as_posix(),
                    "sha256": _sha256(exit_path),
                },
                "log": {
                    "path": log_path.relative_to(repository).as_posix(),
                    "sha256": _sha256(log_path),
                },
            }
        primary = qualification_runs[contract.COMMANDS[1][0]]
        collection_path = repository / contract.EVIDENCE_PREFIX / "collection.json"
        _write_json(
            collection_path,
            {
                "schema_name": "issue14-exit-evidence-collection",
                "schema_version": "2.0.0",
                "run_id": "1" * 32,
                "canonical_platform": "youtube",
                "delivery_stage": "delivered",
                "implementation_commit": implementation_commit,
                "artifacts": artifact_bindings,
                "qualification_run": {
                    key: primary[key]
                    for key in ("run_id", "state", "exit_code", "acceptance_evidence_eligible")
                },
                "qualification_runs": qualification_runs,
            },
        )
        return collection_path, artifact_bindings

    def test_finalize_fingerprints_complete_implementation_change_set(self) -> None:
        repository, git, slice_base_commit, implementation_commit = (
            self._build_finalize_repository("issue14-fingerprint-repository")
        )
        collection_path, _artifact_bindings = self._synthetic_issue14_collection(
            repository, implementation_commit=implementation_commit
        )
        manifest_path = repository / contract.EVIDENCE_PREFIX / "exit-evidence-manifest.json"

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
        self.assertEqual(implementation_commit, manifest["implementation_commit"])

    def test_finalize_rejects_head_advanced_past_qualification_commit(self) -> None:
        """R2: finalize must fail when HEAD has moved past the runs' git_commit."""
        repository, git, _slice_base_commit, implementation_commit = (
            self._build_finalize_repository("issue14-head-advanced")
        )
        collection_path, _artifact_bindings = self._synthetic_issue14_collection(
            repository, implementation_commit=implementation_commit
        )
        manifest_path = repository / contract.EVIDENCE_PREFIX / "exit-evidence-manifest.json"

        later = repository / "src" / "video2pdf_workflow_kernel" / "later_change.py"
        later.parent.mkdir(parents=True, exist_ok=True)
        later.write_text("LATER = True\n", encoding="utf-8")
        git("add", ".")
        git("commit", "-m", "later implementation")
        self.assertNotEqual(git("rev-parse", "HEAD"), implementation_commit)

        with (
            patch.object(collector, "PROJECT_ROOT", repository),
            patch.object(collector, "SLICE_BASE_COMMIT", implementation_commit),
        ):
            with self.assertRaises(collector.CollectionError) as caught:
                collector.finalize(
                    collection_path=collection_path,
                    manifest_path=manifest_path,
                )
        self.assertIn("HEAD to equal", str(caught.exception))
        self.assertFalse(manifest_path.exists())

    def test_finalize_rejects_dirty_non_evidence_worktree(self) -> None:
        """R2: finalize must fail when non-evidence worktree changes exist."""
        repository, _git, _slice_base_commit, implementation_commit = (
            self._build_finalize_repository("issue14-dirty-worktree")
        )
        collection_path, _artifact_bindings = self._synthetic_issue14_collection(
            repository, implementation_commit=implementation_commit
        )
        manifest_path = repository / contract.EVIDENCE_PREFIX / "exit-evidence-manifest.json"
        non_evidence = repository / "src" / "video2pdf_workflow_kernel" / "drift.py"
        non_evidence.parent.mkdir(parents=True, exist_ok=True)
        non_evidence.write_text("DRIFT = True\n", encoding="utf-8")

        with (
            patch.object(collector, "PROJECT_ROOT", repository),
            patch.object(collector, "SLICE_BASE_COMMIT", implementation_commit),
        ):
            with self.assertRaises(collector.CollectionError) as caught:
                collector.finalize(
                    collection_path=collection_path,
                    manifest_path=manifest_path,
                )
        self.assertIn("non-evidence changes", str(caught.exception))
        self.assertFalse(manifest_path.exists())

    def test_collect_finalize_validate_round_trip_at_different_root(self) -> None:
        """R5: a slice-13-style manifest validates at a different repository root.

        Runs the full collect + finalize + validate cycle inside a fresh git
        repository: collect a delivered YouTube run and both persisted
        qualification runs, finalize against the patched root, then revalidate
        the produced manifest through the shared validator with a patched root.
        """
        repository, git, slice_base_commit, implementation_commit = (
            self._build_finalize_repository("issue14-round-trip")
        )
        run_dir, current_target, qualification_runs = (
            self._isolated_delivered_youtube_fixture(
                project_root=repository,
                git_commit=implementation_commit,
            )
        )
        collection_path = repository / contract.EVIDENCE_PREFIX / "collection.json"
        manifest_path = repository / contract.EVIDENCE_PREFIX / "exit-evidence-manifest.json"

        with (
            patch.object(collector, "PROJECT_ROOT", repository),
            patch.object(collector, "SLICE_BASE_COMMIT", slice_base_commit),
        ):
            collected = collector.collect(
                run_dir=run_dir,
                current_target=current_target,
                qualification_runs=qualification_runs,
                output=collection_path,
            )
            self.assertEqual(implementation_commit, collected["implementation_commit"])
            manifest = collector.finalize(
                collection_path=collection_path,
                manifest_path=manifest_path,
            )
        self.assertEqual(implementation_commit, manifest["implementation_commit"])
        # Blocker 3: the finalizer must LF-normalize the persisted (CRLF on
        # Windows) log before publishing, and bind both the normalized source
        # bytes and the published bytes. The published log must contain no
        # CRLF so its sha256 matches the committed blob in any checkout.
        for command in manifest["commands"]:
            self.assertIn("source_log_sha256", command)
            self.assertIn("published_log_sha256", command)
            published_bytes = (repository / command["log"]["path"]).read_bytes()
            self.assertNotIn(b"\r\n", published_bytes)
            self.assertEqual(
                hashlib.sha256(published_bytes).hexdigest(),
                command["published_log_sha256"],
            )
            marker_suffix = (
                f"EVIDENCE_IMPLEMENTATION_COMMIT: {implementation_commit}\n"
            ).encode("ascii")
            self.assertTrue(published_bytes.endswith(marker_suffix))
            self.assertEqual(
                hashlib.sha256(published_bytes[: -len(marker_suffix)]).hexdigest(),
                command["source_log_sha256"],
            )
        schema = json.loads(
            (ROOT / "schemas/exit-evidence-manifest.v2.schema.json").read_text(encoding="utf-8")
        )
        # The committed Schema pins the real repository's slice_base_commit, so
        # a fresh-root manifest must be validated against a Schema copy whose
        # slice-13 base-commit const matches the temporary repository.
        patched_schema = deepcopy(schema)
        for branch in patched_schema["oneOf"]:
            branch_props = branch.get("properties", {})
            branch_slice = branch_props.get("slice", {}).get("properties", {})
            if branch_slice.get("number", {}).get("const") == 13:
                branch_props["slice_base_commit"]["const"] = slice_base_commit
        Draft202012Validator(patched_schema).validate(manifest)

        patched_schema_path = repository.parent / "patched-exit-evidence-manifest.v2.schema.json"
        patched_schema_path.write_text(
            json.dumps(patched_schema, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        patched_configs = deepcopy(validator.SLICE_CONFIGS)
        patched_configs[13]["base_commit"] = slice_base_commit

        with (
            patch.object(validator, "PROJECT_ROOT", repository),
            patch.object(validator, "SCHEMA_PATH", patched_schema_path),
            patch.object(validator, "SLICE_CONFIGS", patched_configs),
        ):
            validator.validate_manifest(
                manifest_path, schema_only=False, pre_publication=True
            )

    def test_collect_rejects_absolute_or_escape_artifact_paths(self) -> None:
        """R5: collection and manifest entries reject absolute and escaping paths."""
        run_dir, current_target, qualification_runs = self._isolated_delivered_youtube_fixture()
        output = run_dir.parents[1] / "absolute-path-collection.json"
        collected = collector.collect(
            run_dir=run_dir,
            current_target=current_target,
            qualification_runs=qualification_runs,
            output=output,
        )
        outside = ROOT.parent / "escaped-artifact.json"
        outside.write_text("{}\n", encoding="utf-8")
        try:
            for mutation in (
                lambda c: c["artifacts"]["run_record"].__setitem__(
                    "path", str(outside.resolve())
                ),
                lambda c: c["qualification_runs"][
                    contract.COMMANDS[1][0]
                ]["command_record"].__setitem__(
                    "path", str(outside.resolve())
                ),
            ):
                with self.subTest():
                    tampered = deepcopy(collected)
                    mutation(tampered)
                    tampered_output = run_dir.parents[1] / "escape-collection.json"
                    with self.assertRaises(collector.CollectionError):
                        collector.finalize(
                            collection_path=_write_json(
                                tampered_output, tampered
                            ),
                            manifest_path=run_dir.parents[1] / "escape-manifest.json",
                        )
        finally:
            outside.unlink()

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
                run_dir, current_target, qualification_runs = self._isolated_delivered_youtube_fixture()
                artifact_path = run_dir / relative_path
                artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
                artifact[field] = value
                _write_json(artifact_path, artifact)
                self._refresh_delivery_binding_chain(run_dir)

                with self.assertRaises(collector.CollectionError):
                    collector.collect(
                        run_dir=run_dir,
                        current_target=current_target,
                        qualification_runs=qualification_runs,
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

    def test_published_slice13_evidence_validates_at_relocated_root_without_rebuild(self) -> None:
        """Blocker 2: published slice-13 evidence validates at a relocated root.

        Copies the committed ``evidence/slice-13/`` manifest and every declared
        evidence file (manifest, logs, persisted qualification records,
        guarded-delivery artifacts, schemas, delivery-quality authorities) into
        a fresh repository root, WITHOUT regenerating any manifest, command
        record, or log. The validator's slice-13 guarded-qualification semantic
        comparison must pass against the relocated root: command identity is
        semantic (argv[1:] plus interpreter role; cwd merely a syntactically
        valid absolute path), so a published command whose argv[0] and cwd
        point at the original machine and directory must not fail on
        relocation.
        """
        source_manifest = ROOT / "evidence/slice-13/exit-evidence-manifest.json"
        source = json.loads(source_manifest.read_text(encoding="utf-8"))
        scratch = new_case_dir(self.id(), label="slice13-relocated-root")
        scratch_manifest = scratch / "evidence/slice-13/exit-evidence-manifest.json"
        scratch_manifest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_manifest, scratch_manifest)
        for relative in source["evidence_paths"]:
            src = ROOT / relative
            if not src.is_file():
                continue
            target = scratch / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
        for directory in ("schemas", "delivery-quality"):
            target = scratch / directory
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(ROOT / directory, target)
        contracts = scratch / "tests/video_workflow/fixtures/contracts"
        if contracts.exists():
            shutil.rmtree(contracts)
        shutil.copytree(
            ROOT / "tests/video_workflow/fixtures/contracts", contracts
        )

        value = json.loads(scratch_manifest.read_text(encoding="utf-8"))
        # Cross-machine relocation of the *interpreter* too: the published
        # manifest recorded the publishing machine's argv[0], and this
        # validator runs against a different interpreter. Patch only
        # SLICE_CONFIGS[13] (never regenerating the evidence) so every closed
        # command's registered argv[0] points at another machine's python.
        # The semantic comparison must still pass: command[1:] matches the
        # contract exactly and the interpreter role is not machine-bound.
        patched_configs = deepcopy(validator.SLICE_CONFIGS)
        for expected_command in patched_configs[13]["commands"]:
            expected_command["command"][0] = "C:/other/machine/python.exe"
        with (
            patch.object(validator, "PROJECT_ROOT", scratch),
            patch.object(
                validator,
                "SCHEMA_PATH",
                scratch / "schemas/exit-evidence-manifest.v2.schema.json",
            ),
            patch.object(validator, "SLICE_CONFIGS", patched_configs),
        ):
            # The semantic comparison that previously bound the published
            # evidence to the original machine and directory must pass.
            validator.validate_semantics(value)
            validator._validate_guarded_delivery_qualification(
                value,
                issue_commands=contract.COMMANDS,
                issue_label="Issue #14",
            )
            validator._validate_slice13_evidence_paths(value)

    def test_finalize_rejects_collect_time_log_drift(self) -> None:
        """Blocker 3: finalize must reject a persisted log that drifted.

        ``collect`` hashes the original persisted ``command.log``; if that file
        changes between collect and finalize (the persisted log lives under
        待删除/ and is gitignored, so the worktree anchor cannot detect it),
        ``finalize`` must fail closed at the collect-time binding instead of
        publishing the drifted bytes.
        """
        run_dir, current_target, qualification_runs = (
            self._isolated_delivered_youtube_fixture()
        )
        collection_path = run_dir.parents[1] / "drift-collection.json"
        collector.collect(
            run_dir=run_dir,
            current_target=current_target,
            qualification_runs=qualification_runs,
            output=collection_path,
        )
        drifted = qualification_runs[contract.COMMANDS[1][0]] / "command.log"
        drifted.write_bytes(drifted.read_bytes() + b"drifted after collect\n")
        with self.assertRaises(collector.CollectionError) as caught:
            collector.finalize(
                collection_path=collection_path,
                manifest_path=run_dir.parents[1] / "drift-manifest.json",
            )
        self.assertIn("persisted_command_log", str(caught.exception))

    def test_validator_rejects_broken_log_source_chain(self) -> None:
        """Blocker 3: the validator rejects a broken collect->finalize chain.

        # scenario_id=broken_log_source_chain; target_invariant=published log source chain;
        # mutation_seam=published log bytes (line inserted before marker);
        # rematerialized_nodes=none; intentionally_stale_nodes=published_log_sha256;
        # expected_first_gate=guarded_delivery_evidence;
        # expected_error_code=command_log_source_chain_broken; scenario_class=single_contradiction.
        """
        for seam in ("source_log_sha256", "published_log_bytes"):
            with self.subTest(seam=seam):
                scratch = new_case_dir(self.id(), label=f"slice13-broken-chain-{seam}")
                manifest_path, manifest = self._slice13_guarded_fixture(scratch)
                target = manifest["commands"][0]
                if seam == "source_log_sha256":
                    target["source_log_sha256"] = "0" * 64
                else:
                    log_path = scratch / target["log"]["path"]
                    marker = (
                        f"EVIDENCE_IMPLEMENTATION_COMMIT: {'2' * 40}\n"
                    ).encode("ascii")
                    log_bytes = log_path.read_bytes()
                    log_path.write_bytes(
                        log_bytes[: -len(marker)]
                        + b"injected before marker\n"
                        + marker
                    )
                with (
                    patch.object(validator, "PROJECT_ROOT", scratch),
                    self.assertRaises(validator.EvidenceError) as caught,
                ):
                    validator._validate_guarded_delivery_qualification(
                        manifest,
                        issue_commands=contract.COMMANDS,
                        issue_label="Issue #14",
                    )
                self.assertEqual(
                    "guarded_delivery_evidence", caught.exception.first_failing_gate
                )
                self.assertEqual(
                    "command_log_source_chain_broken",
                    caught.exception.error_code,
                )


if __name__ == "__main__":
    unittest.main()
