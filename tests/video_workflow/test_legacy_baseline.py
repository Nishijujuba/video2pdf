from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from legacy_baseline_contracts import ContractError, validate_json_schema_instance
import legacy_baseline_contracts


COLLECTOR = PROJECT_ROOT / "scripts" / "collect_legacy_baseline.py"
MANIFEST_VALIDATOR = PROJECT_ROOT / "scripts" / "validate_exit_evidence_manifest.py"
DEFINITION_VALIDATOR = PROJECT_ROOT / "scripts" / "validate_legacy_baseline_definition.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
REQUIRED_CATEGORIES = {"pyramid", "compile", "acceptance", "delivery_guard", "batch"}


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def stable_log_sha(stdout: str, stderr: str = "") -> str:
    normalized = (
        "===== STDOUT =====\n"
        f"{stdout.rstrip()}\n"
        "===== STDERR =====\n"
        f"{stderr.rstrip()}\n"
    )
    return sha256_text(normalized)


def run_git(repo: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()


class LegacyBaselineCliTests(unittest.TestCase):
    def setUp(self) -> None:
        trash_root = PROJECT_ROOT / "待删除" / "kernel-test-runs"
        trash_root.mkdir(parents=True, exist_ok=True)
        self.run_root = trash_root / f"legacy-baseline-{time.time_ns()}"
        self.run_root.mkdir(parents=True)
        self.commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout.strip()
        self.authority_path = self.run_root / "authority.txt"
        self.authority_path.write_text("Legacy Track remains authoritative.\n", encoding="utf-8")

    def command_entry(self, category: str, *, code: str | None = None, expected_stdout: str | None = None) -> dict:
        output = expected_stdout if expected_stdout is not None else f"{category}-pass\n"
        source = code if code is not None else f"print('{category}-pass')"
        return {
            "test_id": f"legacy-{category}",
            "category": category,
            "command": ["{python}", "-X", "utf8", "-B", "-c", source],
            "timeout_seconds": 30,
            "expected_status": "pass",
            "expected_log_sha256": stable_log_sha(output),
        }

    def write_definition(self, *, overrides: dict[str, dict] | None = None) -> Path:
        overrides = overrides or {}
        baselines = []
        for category in sorted(REQUIRED_CATEGORIES):
            entry = self.command_entry(category)
            entry.update(overrides.get(category, {}))
            baselines.append(entry)
        verification = {
            "test_id": "slice-00-contracts",
            "category": "slice_verification",
            "command": ["{python}", "-X", "utf8", "-B", "-c", "print('slice-contracts-pass')"],
            "timeout_seconds": 30,
            "expected_status": "pass",
            "expected_log_sha256": stable_log_sha("slice-contracts-pass\n"),
        }
        definition = {
            "$schema": "https://video2pdf.local/schemas/legacy-baseline-definition.v1.schema.json",
            "schema_version": 1,
            "kind": "legacy-workflow-baseline-definition",
            "normalization_version": 1,
            "baselines": baselines,
            "slice_verifications": [verification],
            "authority_guards": [
                {
                    "path": self.authority_path.relative_to(PROJECT_ROOT).as_posix(),
                    "required_substrings": ["Legacy Track remains authoritative."],
                }
            ],
        }
        path = self.run_root / "definition.json"
        path.write_text(json.dumps(definition, indent=2) + "\n", encoding="utf-8")
        return path

    def run_collector(
        self,
        definition: Path,
        *,
        suffix: str = "current",
        implementation_commit: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        command = [
                sys.executable,
                "-X",
                "utf8",
                "-B",
                str(COLLECTOR),
                "--definition",
                str(definition),
                "--output",
                str(self.run_root / f"manifest-{suffix}.json"),
                "--log-dir",
                str(self.run_root / f"logs-{suffix}"),
            ]
        if implementation_commit is not None:
            command.extend(["--implementation-commit", implementation_commit])
        return subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def read_manifest(self, suffix: str) -> dict:
        return json.loads((self.run_root / f"manifest-{suffix}.json").read_text(encoding="utf-8"))

    def test_collects_repeatable_five_category_baseline_and_valid_manifest(self) -> None:
        definition = self.write_definition()

        first = self.run_collector(definition, suffix="repeatable")
        first_manifest = self.read_manifest("repeatable")
        second = self.run_collector(definition, suffix="repeatable")
        second_manifest = self.read_manifest("repeatable")

        self.assertEqual(0, first.returncode, first.stderr)
        self.assertEqual(0, second.returncode, second.stderr)
        baseline_commands = [entry for entry in first_manifest["commands"] if entry["scope"] == "legacy_baseline"]
        self.assertEqual(REQUIRED_CATEGORIES, {entry["category"] for entry in baseline_commands})
        self.assertEqual(5, len(baseline_commands))
        self.assertTrue(all(entry["conforms"] for entry in first_manifest["commands"]))
        self.assertEqual("pass", first_manifest["overall_decision"])
        self.assertEqual("sha256-utf8-lf-v1", first_manifest["fingerprint_algorithm"])
        self.assertEqual(self.commit, first_manifest["implementation_commit"])
        expected_evidence_paths = {
            (self.run_root / "manifest-repeatable.json").relative_to(PROJECT_ROOT).as_posix(),
            *{
                command["log"][f"{kind}_path"]
                for command in first_manifest["commands"]
                for kind in ("normalized", "raw")
            },
        }
        self.assertEqual(expected_evidence_paths, set(first_manifest["evidence_paths"]))
        self.assertEqual(first_manifest["commands"], second_manifest["commands"])
        self.assertEqual(
            {
                "kind": "none",
                "runtime_authority_change": False,
                "components_activated": [],
                "legacy_track_authority": "preserved",
            },
            first_manifest["activation_scope"],
        )
        validated = subprocess.run(
            [sys.executable, "-X", "utf8", "-B", str(MANIFEST_VALIDATOR), str(self.run_root / "manifest-repeatable.json")],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(0, validated.returncode, validated.stderr)

    def test_unapproved_status_or_log_drift_blocks_completion(self) -> None:
        status_definition = self.write_definition(
            overrides={
                "batch": {
                    "command": ["{python}", "-X", "utf8", "-B", "-c", "raise SystemExit(7)"],
                    "expected_log_sha256": stable_log_sha(""),
                }
            }
        )
        status_result = self.run_collector(status_definition, suffix="status-drift")
        status_manifest = self.read_manifest("status-drift")

        self.assertNotEqual(0, status_result.returncode)
        failed_status = next(entry for entry in status_manifest["commands"] if entry["category"] == "batch")
        self.assertEqual("pass", failed_status["expected_status"])
        self.assertEqual("fail", failed_status["actual_status"])
        self.assertFalse(failed_status["conforms"])
        self.assertEqual("fail", status_manifest["overall_decision"])

        log_definition = self.write_definition(
            overrides={
                "pyramid": {
                    "command": ["{python}", "-X", "utf8", "-B", "-c", "print('pyramid-drift')"],
                }
            }
        )
        log_result = self.run_collector(log_definition, suffix="log-drift")
        log_manifest = self.read_manifest("log-drift")

        self.assertNotEqual(0, log_result.returncode)
        failed_log = next(entry for entry in log_manifest["commands"] if entry["category"] == "pyramid")
        self.assertEqual("pass", failed_log["actual_status"])
        self.assertNotEqual(failed_log["expected_log_sha256"], failed_log["log"]["normalized_sha256"])
        self.assertFalse(failed_log["conforms"])
        self.assertTrue(log_manifest["unresolved_exceptions"])

    def test_missing_legacy_category_is_rejected_before_execution(self) -> None:
        definition_path = self.write_definition()
        definition = json.loads(definition_path.read_text(encoding="utf-8"))
        definition["baselines"] = [entry for entry in definition["baselines"] if entry["category"] != "batch"]
        definition_path.write_text(json.dumps(definition, indent=2) + "\n", encoding="utf-8")

        result = self.run_collector(definition_path, suffix="missing-category")

        self.assertNotEqual(0, result.returncode)
        self.assertFalse((self.run_root / "manifest-missing-category.json").exists())
        self.assertIn("baselines", result.stderr)
        self.assertIn("at least 5", result.stderr)

    def test_collector_rejects_caller_supplied_ancestor_commit(self) -> None:
        definition_path = self.write_definition()
        ancestor = subprocess.run(
            ["git", "rev-parse", "HEAD~1"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout.strip()

        result = self.run_collector(
            definition_path,
            suffix="ancestor-injection",
            implementation_commit=ancestor,
        )

        self.assertNotEqual(0, result.returncode)
        self.assertFalse((self.run_root / "manifest-ancestor-injection.json").exists())
        self.assertIn("unrecognized arguments: --implementation-commit", result.stderr)

    def test_manifest_validator_rejects_tampered_bound_log(self) -> None:
        definition = self.write_definition()
        collected = self.run_collector(definition, suffix="tampered-binding")
        self.assertEqual(0, collected.returncode, collected.stderr)
        manifest_path = self.run_root / "manifest-tampered-binding.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        log_path = PROJECT_ROOT / manifest["commands"][0]["log"]["normalized_path"]
        lf_bytes = log_path.read_bytes()
        log_path.write_bytes(lf_bytes.replace(b"\n", b"\r\n"))

        newline_only_change = subprocess.run(
            [sys.executable, "-X", "utf8", "-B", str(MANIFEST_VALIDATOR), str(manifest_path)],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(0, newline_only_change.returncode, newline_only_change.stderr)

        log_path.write_bytes(log_path.read_bytes() + b"tampered\r\n")

        validated = subprocess.run(
            [sys.executable, "-X", "utf8", "-B", str(MANIFEST_VALIDATOR), str(manifest_path)],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

        self.assertNotEqual(0, validated.returncode)
        self.assertIn("fingerprint mismatch", validated.stderr)

    def test_normalization_removes_declared_run_identity_noise_only(self) -> None:
        spec = importlib.util.spec_from_file_location("collect_legacy_baseline", COLLECTOR)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader if spec else None)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        first = (
            "ERROR: idle timeout; report: "
            f"{PROJECT_ROOT}\\待删除\\skill-tests\\idle-timeout-1784043830963755900"
            "\\待删除\\latex-build\\20260714_234350_968754_9dd2f9a4\\compile_report.json\n"
            "alias: \\234350_fc6bf7\\main.tex\n"
        )
        second = (
            "ERROR: idle timeout; report: "
            f"{PROJECT_ROOT}\\待删除\\skill-tests\\idle-timeout-1784043940086707600"
            "\\待删除\\latex-build\\20260714_234540_092708_81b5e789\\compile_report.json\n"
            "alias: \\234540_edf1b1\\main.tex\n"
        )

        self.assertEqual(module.normalize_log(first), module.normalize_log(second))
        self.assertNotEqual(
            module.normalize_log(first),
            module.normalize_log(second.replace("idle timeout", "engine failure")),
        )

    def test_atomic_publish_failure_preserves_previous_evidence(self) -> None:
        spec = importlib.util.spec_from_file_location("collect_legacy_baseline", COLLECTOR)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader if spec else None)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        evidence_path = self.run_root / "atomic.json"
        evidence_path.write_text("previous evidence\n", encoding="utf-8")

        with mock.patch.object(module.os, "replace", side_effect=OSError("injected replace failure")):
            with self.assertRaisesRegex(OSError, "injected replace failure"):
                module.write_text_atomic(evidence_path, "new evidence\n")

        self.assertEqual("previous evidence\n", evidence_path.read_text(encoding="utf-8"))
        self.assertTrue(evidence_path.with_name("atomic.json.tmp").exists())


class LegacyBaselineContractFixtureTests(unittest.TestCase):
    def make_git_repo(self, name: str) -> Path:
        repo = PROJECT_ROOT / "待删除" / "kernel-test-runs" / f"{name}-{time.time_ns()}"
        repo.mkdir(parents=True)
        run_git(repo, "init")
        run_git(repo, "config", "user.email", "slice0-tests@example.invalid")
        run_git(repo, "config", "user.name", "Slice 0 Tests")
        (repo / "code.py").write_text("print('implementation')\n", encoding="utf-8")
        run_git(repo, "add", "code.py")
        run_git(repo, "commit", "-m", "implementation")
        return repo

    def run_validator(self, script: Path, fixture: str) -> subprocess.CompletedProcess[str]:
        extra = ["--schema-only"] if script == MANIFEST_VALIDATOR else []
        return subprocess.run(
            [sys.executable, "-X", "utf8", "-B", str(script), str(FIXTURES / fixture), *extra],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def test_positive_and_negative_schema_fixtures(self) -> None:
        cases = [
            (DEFINITION_VALIDATOR, "legacy_baseline_definition.valid.json", 0),
            (DEFINITION_VALIDATOR, "legacy_baseline_definition.invalid.json", 1),
            (MANIFEST_VALIDATOR, "exit_evidence_manifest.valid.json", 0),
            (MANIFEST_VALIDATOR, "exit_evidence_manifest.invalid.json", 1),
        ]
        for script, fixture, expected_returncode in cases:
            with self.subTest(fixture=fixture):
                result = self.run_validator(script, fixture)
                self.assertEqual(expected_returncode, result.returncode, result.stderr)

    def test_exit_manifest_schema_rejects_timeout_above_maximum(self) -> None:
        manifest = json.loads(
            (FIXTURES / "exit_evidence_manifest.valid.json").read_text(encoding="utf-8")
        )
        manifest["commands"][0]["timeout_seconds"] = 3601
        schema = json.loads(
            (PROJECT_ROOT / "schemas" / "exit-evidence-manifest.v1.schema.json").read_text(
                encoding="utf-8"
            )
        )
        with self.assertRaisesRegex(
            ContractError, r"commands\[0\]\.timeout_seconds.*3600"
        ):
            validate_json_schema_instance(manifest, schema, "exit evidence manifest")
        manifest_path = PROJECT_ROOT / "待删除" / "kernel-test-runs" / (
            f"schema-maximum-{time.time_ns()}.json"
        )
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

        result = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                "-B",
                str(MANIFEST_VALIDATOR),
                str(manifest_path),
                "--schema-only",
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("commands[0].timeout_seconds", result.stderr)
        self.assertIn("3600", result.stderr)

    def test_schema_only_routes_non_object_roots_to_schema_evaluator(self) -> None:
        for label, value in (("array", []), ("null", None)):
            with self.subTest(root=label):
                manifest_path = PROJECT_ROOT / "待删除" / "kernel-test-runs" / (
                    f"schema-root-{label}-{time.time_ns()}.json"
                )
                manifest_path.parent.mkdir(parents=True, exist_ok=True)
                manifest_path.write_text(
                    json.dumps(value) + "\n", encoding="utf-8"
                )

                result = subprocess.run(
                    [
                        sys.executable,
                        "-X",
                        "utf8",
                        "-B",
                        str(MANIFEST_VALIDATOR),
                        str(manifest_path),
                        "--schema-only",
                    ],
                    cwd=PROJECT_ROOT,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                )

                self.assertNotEqual(0, result.returncode)
                self.assertIn(
                    "exit evidence manifest must have JSON type ['object']",
                    result.stderr,
                )
                self.assertNotIn("contract root must be an object", result.stderr)

    def test_schema_only_accepts_structurally_valid_semantic_error_then_manual_rejects(self) -> None:
        manifest = json.loads(
            (FIXTURES / "exit_evidence_manifest.valid.json").read_text(encoding="utf-8")
        )
        manifest["commands"][0]["conforms"] = False
        schema = json.loads(
            (PROJECT_ROOT / "schemas" / "exit-evidence-manifest.v1.schema.json").read_text(
                encoding="utf-8"
            )
        )
        validate_json_schema_instance(manifest, schema, "exit evidence manifest")
        manifest_path = PROJECT_ROOT / "待删除" / "kernel-test-runs" / (
            f"schema-only-semantic-{time.time_ns()}.json"
        )
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

        schema_only = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                "-B",
                str(MANIFEST_VALIDATOR),
                str(manifest_path),
                "--schema-only",
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

        self.assertEqual(0, schema_only.returncode, schema_only.stderr)
        with self.assertRaisesRegex(ContractError, "conforms does not match"):
            legacy_baseline_contracts.validate_prevalidated_exit_evidence_semantics(manifest)

    def test_prevalidated_baseline_semantics_rejects_cross_entry_category_drift(self) -> None:
        definition = json.loads(
            (FIXTURES / "legacy_baseline_definition.valid.json").read_text(encoding="utf-8")
        )
        definition["baselines"][1]["category"] = definition["baselines"][0]["category"]
        schema = json.loads(
            (PROJECT_ROOT / "schemas" / "legacy-baseline-definition.v1.schema.json").read_text(
                encoding="utf-8"
            )
        )
        validate_json_schema_instance(definition, schema, "legacy baseline definition")

        with self.assertRaisesRegex(ContractError, "five Legacy categories"):
            legacy_baseline_contracts.validate_prevalidated_legacy_baseline_semantics(
                definition
            )

    def test_schema_compatibility_layer_fails_closed_on_unknown_keyword(self) -> None:
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "allOf": [{"type": "object"}],
        }

        with self.assertRaisesRegex(ContractError, "unsupported JSON Schema keyword.*allOf"):
            validate_json_schema_instance({}, schema, "test instance")

    def test_canonical_fingerprint_normalizes_newlines_and_rejects_non_utf8(self) -> None:
        expected = sha256_text("alpha\nbeta\n")

        self.assertEqual(
            expected,
            legacy_baseline_contracts.fingerprint_utf8_lf(b"alpha\r\nbeta\r"),
        )
        with self.assertRaisesRegex(ContractError, "must be UTF-8 text"):
            legacy_baseline_contracts.fingerprint_utf8_lf(b"\xff")

    def test_implementation_commit_is_captured_only_from_a_clean_head(self) -> None:
        repo = self.make_git_repo("clean-head")
        head = run_git(repo, "rev-parse", "HEAD")

        self.assertEqual(
            head,
            legacy_baseline_contracts.capture_clean_implementation_commit(repo),
        )

        (repo / "code.py").write_text("print('dirty')\n", encoding="utf-8")
        with self.assertRaisesRegex(ContractError, "clean implementation HEAD"):
            legacy_baseline_contracts.capture_clean_implementation_commit(repo)

    def test_evidence_lineage_allows_evidence_only_and_rejects_code_descendant(self) -> None:
        repo = self.make_git_repo("evidence-lineage")
        implementation_commit = run_git(repo, "rev-parse", "HEAD")
        evidence_path = repo / "evidence" / "result.json"
        evidence_path.parent.mkdir(parents=True)
        evidence_path.write_text('{"decision":"pass"}\n', encoding="utf-8")
        run_git(repo, "add", "evidence/result.json")
        run_git(repo, "commit", "-m", "evidence only")

        legacy_baseline_contracts.validate_prevalidated_evidence_lineage(
            repo,
            implementation_commit,
            ["evidence/result.json"],
        )

        (repo / "code.py").write_text("print('changed after evidence')\n", encoding="utf-8")
        run_git(repo, "add", "code.py")
        run_git(repo, "commit", "-m", "stale code descendant")
        with self.assertRaisesRegex(ContractError, "non-evidence path.*code.py"):
            legacy_baseline_contracts.validate_prevalidated_evidence_lineage(
                repo,
                implementation_commit,
                ["evidence/result.json"],
            )

    def test_repository_definition_covers_legacy_authority_without_cutover(self) -> None:
        definition_path = PROJECT_ROOT / "config" / "legacy-baseline.v1.json"
        definition = json.loads(definition_path.read_text(encoding="utf-8"))

        self.assertEqual(REQUIRED_CATEGORIES, {entry["category"] for entry in definition["baselines"]})
        guarded_paths = {entry["path"] for entry in definition["authority_guards"]}
        self.assertEqual(
            {"AGENTS.md", "CLAUDE.md", "docs/adr/video-workflow-kernel-2.0-decision-map.md"},
            guarded_paths,
        )
        decision_map = (PROJECT_ROOT / "docs" / "adr" / "video-workflow-kernel-2.0-decision-map.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("No component currently has `active_global_gate` or `active_kernel` status.", decision_map)


if __name__ == "__main__":
    unittest.main()
