from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from tests.video_workflow._test_run import new_case_dir
from tests.video_workflow.test_issue106_reader_text_continuation import (
    Issue106ReaderTextContinuationTests,
)
from tests.video_workflow.test_single_section_production import (
    SingleSectionProductionTests,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CLI = PROJECT_ROOT / "scripts" / "video_workflow.py"
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
OWNERS = (
    "source-faithfulness-reviewer",
    "writing-quality-reviewer",
    "pyramid-reviewer",
)


def canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def canonical_sha(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_bytes(value))
    return path


def run_cli(*arguments: str) -> tuple[subprocess.CompletedProcess[str], dict]:
    completed = subprocess.run(
        [sys.executable, "-X", "utf8", "-B", str(CLI), *arguments],
        cwd=PROJECT_ROOT,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    return completed, json.loads(completed.stdout)


class InventoryRefreshFixture:
    """A valid authority graph with one downstream generated-title contradiction.

    Authority inputs: Run Record, current Production files, and approved reference.
    Derived nodes: Artifact Generations -> inventory -> report -> Seal -> Final Compile
    Manifest -> retained failed command. Boundaries: predecessor Seal, persisted command,
    and Run-scoped refresh claim. The negative scenario mutates only the persisted
    command's predecessor argument and expects the failure-binding gate first.
    """

    def __init__(
        self, test_id: str, *, stale_predecessor_generation: bool = False
    ) -> None:
        self.root = new_case_dir(test_id, label="issue121-inventory-refresh")
        outline = json.loads(SingleSectionProductionTests._outline_payload())
        outline["compile_support"]["style_content"] = "\n".join(
            [
                r"\ProvidesPackage{local}",
                r"\newtcolorbox{importantbox}[1][]{title={核心结论},#1}",
                r"\newtcolorbox{knowledgebox}[1][]{title={背景知识},#1}",
                r"\newtcolorbox{warningbox}[1][]{title={边界与风险},#1}",
                r"\newtcolorbox{dialoguebox}[1][]{title={原声片段},#1}",
            ]
        ) + "\n"
        writer_text = "\n".join(
            [
                r"\begin{importantbox}",
                "A",
                r"\end{importantbox}",
                r"\begin{knowledgebox}",
                "B",
                r"\end{knowledgebox}",
                r"\begin{warningbox}",
                "C",
                r"\end{warningbox}",
                r"\begin{dialoguebox}[title={原声片段（00:01--00:02）}]",
                "D",
                r"\end{dialoguebox}",
            ]
        ).encode("utf-8")
        lifecycle = Issue106ReaderTextContinuationTests()
        with patch.object(
            SingleSectionProductionTests,
            "_outline_payload",
            return_value=canonical_bytes(outline),
        ):
            _kernel, self.run = lifecycle._complete_single_section_production(
                writer_text=writer_text
            )
        self.run_record = json.loads(
            (self.run / "workflow/run.json").read_text(encoding="utf-8")
        )
        self.style = self.run / "work/integration/local.sty"
        self.main = self.run / "work/integration/main.tex"
        self.section = self.run / "work/integration/section_01.tex"
        self.generations = self._generation_set()
        if stale_predecessor_generation:
            self.generations["artifacts"][0]["generation"] += 1
            self.generations["generation_set_sha256"] = canonical_sha(
                {
                    key: value
                    for key, value in self.generations.items()
                    if key != "generation_set_sha256"
                }
            )
        self.dependencies = self._dependencies()
        self.inventory = self._inventory()
        self.predecessor = self.run / "review/precompile/workspaces/predecessor"
        self._prepare_passing_predecessor()
        self.seal = json.loads(
            (self.predecessor / "precompile-text-seal.json").read_text(
                encoding="utf-8"
            )
        )
        self.attempt = self._write_predecessor_attempt()
        self.manifest = self._write_compile_manifest()
        self.failed_command = self._write_failed_command()
        self.successor = self.run / "review/precompile/workspaces/refreshed"

    def _generation_set(self) -> dict:
        current_manifest = json.loads(
            (self.run / "workflow/compile-manifest.json").read_text(
                encoding="utf-8"
            )
        )
        value = {
            "schema_name": "precompile-artifact-generation-set",
            "schema_version": "1.0.0",
            "generation_set_id": "issue121-current-production",
            "producer_ids": sorted(
                {item["producer"] for item in current_manifest["entries"]}
            ),
            "artifacts": [
                {
                    "logical_id": item["logical_id"],
                    "generation": item["generation"],
                    "sha256": item["sha256"],
                }
                for item in current_manifest["entries"]
            ],
        }
        value["generation_set_sha256"] = canonical_sha(value)
        return value

    @staticmethod
    def _dependencies() -> dict:
        value = {
            "schema_name": "precompile-semantic-dependencies",
            "schema_version": "1.0.0",
            "dependencies": [
                {
                    "owner": "source-faithfulness-reviewer",
                    "projection_id": "source-faithfulness-evaluation",
                    "projection_sha256": "3" * 64,
                    "required_scope_ids": ["source-correspondence"],
                    "provider_id": "source-faithfulness-provider",
                    "provider_sha256": "4" * 64,
                },
                {
                    "owner": "pyramid-reviewer",
                    "projection_id": "pyramid-evaluation",
                    "projection_sha256": "5" * 64,
                    "required_scope_ids": ["integrated-document"],
                    "provider_id": "pyramid-provider",
                    "provider_sha256": "6" * 64,
                },
            ],
        }
        value["dependencies_sha256"] = canonical_sha(value)
        return value

    def _inventory(self) -> dict:
        items = [
            {
                "item_id": "tex.integrated_section_01",
                "kind": "body_text",
                "semantic_region": "body",
                "language_profile_id": "zh-hans",
                "source_artifact_logical_id": "integrated_section_01",
                "source_generation": next(
                    item["generation"]
                    for item in self.generations["artifacts"]
                    if item["logical_id"] == "integrated_section_01"
                ),
                "source_sha256": sha256_file(self.section),
                "locator": "latex-source:work/integration/section_01.tex",
                "representation": "structured_text",
                "declared_text": self.section.read_text(encoding="utf-8"),
                "text_sha256": hashlib.sha256(
                    self.section.read_text(encoding="utf-8").encode("utf-8")
                ).hexdigest(),
                "applicable_rule_ids": ["no_meta_writing_content"],
            },
            {
                "item_id": "generated.local_style.box_titles",
                "kind": "generated_text",
                "semantic_region": "box_titles",
                "language_profile_id": "zh-hans",
                "source_artifact_logical_id": "local_style",
                "source_generation": 1,
                "source_sha256": sha256_file(self.style),
                "locator": "latex-generated:work/integration/local.sty/newtcolorbox-title",
                "representation": "declared_generated_text",
                "declaration_basis": "direct_inspection_of_current_local_style",
                "declared_text": "核心结论\n背景知识\n边界与风险\n原声片段",
                "text_sha256": hashlib.sha256(
                    "核心结论\n背景知识\n边界与风险\n原声片段".encode("utf-8")
                ).hexdigest(),
                "applicable_rule_ids": [
                    "no_meta_writing_content",
                    "core_term_first_use_readability",
                ],
            },
        ]
        for item in items:
            item["item_sha256"] = canonical_sha(item)
        value = {
            "schema_name": "reader-facing-text-inventory",
            "schema_version": "1.0.0",
            "inventory_id": "issue121-predecessor",
            "language_profile_id": "zh-hans",
            "delivery_glossary": None,
            "generation_set_sha256": self.generations["generation_set_sha256"],
            "declared_surface": [
                {"region_id": item["item_id"], "kind": item["kind"]}
                for item in items
            ],
            "items": items,
            "coverage_ledger": [
                {
                    "region_id": item["item_id"],
                    "item_id": item["item_id"],
                    "status": "covered",
                }
                for item in items
            ],
            "extractors": [
                {
                    "extractor_id": "latex-reader-text-extractor",
                    "extractor_sha256": "9" * 64,
                }
            ],
        }
        value["reader_text_set_sha256"] = canonical_sha(
            [
                {
                    "item_id": item["item_id"],
                    "kind": item["kind"],
                    "representation": item["representation"],
                    "text_sha256": item["text_sha256"],
                }
                for item in items
            ]
        )
        value["inventory_sha256"] = canonical_sha(value)
        return value

    def _prepare_passing_predecessor(self) -> None:
        inputs = self.root / "inputs"
        generation_path = write_json(inputs / "generations.json", self.generations)
        dependency_path = write_json(inputs / "dependencies.json", self.dependencies)
        inventory_path = write_json(inputs / "inventory.json", self.inventory)
        prepared, envelope = run_cli(
            "delivery-quality-precompile-prepare",
            "--workspace-root",
            str(self.predecessor),
            "--inventory",
            str(inventory_path),
            "--artifact-generations",
            str(generation_path),
            "--semantic-dependencies",
            str(dependency_path),
            "--prepared-at",
            "2026-09-06T00:00:00Z",
        )
        if prepared.returncode != 0:
            raise AssertionError(envelope)
        for owner in OWNERS:
            self.commit_patch(self.predecessor, owner)
        materialized, envelope = run_cli(
            "delivery-quality-precompile-materialize",
            "--workspace-root",
            str(self.predecessor),
            "--provider-id",
            "precompile-quality-provider",
            "--provider-version",
            "1.0.0",
            "--materialized-at",
            "2026-09-06T00:10:00Z",
        )
        if materialized.returncode != 0:
            raise AssertionError(envelope)
        sealed, envelope = run_cli(
            "delivery-quality-seal",
            "--workspace-root",
            str(self.predecessor),
            "--sealed-at",
            "2026-09-06T00:11:00Z",
        )
        if sealed.returncode != 0:
            raise AssertionError(envelope)

    def commit_patch(
        self, workspace: Path, owner: str, *, fail_first: bool = False
    ) -> None:
        skeleton_path = (
            workspace / "reviewers" / owner / "input/review-skeleton.json"
        )
        skeleton = json.loads(skeleton_path.read_text(encoding="utf-8"))
        results = []
        for index, required in enumerate(skeleton["required_results"]):
            failed = fail_first and index == 0
            result = {
                "result_key": required["result_key"],
                "decision": "fail" if failed else "pass",
                "evidence_locator": f"fixture:{required['result_key']}",
                "repair_write_set": ["work/integration/main.tex"] if failed else [],
            }
            if failed:
                result["violation_id"] = "fixture_violation"
            results.append(result)
        patch = {
            "schema_name": "precompile-judgment-patch",
            "schema_version": "1.0.0",
            "task_id": skeleton["task_id"],
            "owner": owner,
            "skeleton_sha256": skeleton["skeleton_sha256"],
            "generation_set_sha256": skeleton["generation_set_sha256"],
            "reviewer": {
                "reviewer_id": f"issue121-{owner}",
                "runtime_sha256": "b" * 64,
                "independent_from_generation_producers": True,
            },
            "results": results,
            "contract_gaps": [],
        }
        patch["patch_sha256"] = canonical_sha(patch)
        patch_path = write_json(
            workspace.parent / f"{workspace.name}-{owner}.patch.json", patch
        )
        completed, envelope = run_cli(
            "delivery-quality-precompile-patch-commit",
            "--workspace-root",
            str(workspace),
            "--owner",
            owner,
            "--patch",
            str(patch_path),
            "--committed-at",
            "2026-09-06T00:20:00Z",
        )
        if completed.returncode != 0:
            raise AssertionError(envelope)

    def _write_predecessor_attempt(self) -> dict:
        value = {
            "schema_name": "precompile-repair-attempt",
            "schema_version": "1.0.0",
            "activation_status": "target_only",
            "repair_attempt_number": 2,
            "prepared_at": "2026-09-06T00:00:00Z",
            "predecessor_failure_authority": {
                "kind": "semantic_failure_report",
                "path": str((self.root / "prior-failure.json").resolve()),
                "sha256": "a" * 64,
            },
            "predecessor_generation_set_sha256": "c" * 64,
            "repaired_generation_set_sha256": self.generations[
                "generation_set_sha256"
            ],
            "repaired_inventory_sha256": self.inventory["inventory_sha256"],
            "advanced_logical_ids": ["integrated_section_01"],
            "advanced_semantic_input_ids": [],
            "repair_routing": {},
            "failure_set": [],
            "disposition": None,
            "repair_bundle": None,
            "repair_sequence": 3,
            "promotion_input_bindings": None,
            "semantic_attempt_budget_consumed": True,
            "semantic_attempt_number": 2,
            "allowed_write_set": ["work/integration/main.tex"],
            "fresh_reviewer_task_ids": [],
            "predecessor_report_sha256": "a" * 64,
        }
        value["attempt_sha256"] = canonical_sha(value)
        write_json(self.predecessor / "repair-attempt.json", value)
        return value

    def _write_compile_manifest(self) -> Path:
        runtime_policy = self.run / "workflow/compile-runtime-policy.json"
        current_manifest = json.loads(
            (self.run / "workflow/compile-manifest.json").read_text(
                encoding="utf-8"
            )
        )
        predecessor_by_id = {
            item["logical_id"]: item for item in self.generations["artifacts"]
        }
        entries = [
            {
                "logical_id": item["logical_id"],
                "generation": predecessor_by_id[item["logical_id"]]["generation"],
                "sha256": predecessor_by_id[item["logical_id"]]["sha256"],
                "source_path": str((self.run / item["source_path"]).resolve()),
                "staging_path": item["staging_path"],
            }
            for item in current_manifest["entries"]
        ]
        value = {
            "schema_name": "final-compile-manifest",
            "schema_version": "1.0.0",
            "activation_status": "target_only",
            "mode": "final",
            "precompile_text_seal_sha256": self.seal["seal_sha256"],
            "entries": entries,
            "approved_runtime_inputs": [],
            "runtime_policy": {
                "path": str(runtime_policy.resolve()),
                "sha256": sha256_file(runtime_policy),
            },
        }
        value["manifest_sha256"] = canonical_sha(value)
        return write_json(self.run / "待删除/final-input/manifest.json", value)

    def _write_failed_command(self) -> Path:
        command_root = self.run / "待删除/long-running/final-compile-failed"
        final_workspace = self.run / "review/final-compile/failed"
        final_workspace.mkdir(parents=True)
        adapter_stderr = final_workspace / "adapter-stderr.log"
        adapter_stderr.write_text(
            "generated style title occurrence is absent or ambiguous: "
            "generated.local_style.box_titles\n",
            encoding="utf-8",
        )
        argv = [
            sys.executable,
            "-X",
            "utf8",
            "-B",
            str(CLI),
            "delivery-quality-final-compile",
            "--input-track",
            "kernel",
            "--precompile-workspace-root",
            str(self.predecessor.resolve()),
            "--compile-manifest",
            str(self.manifest.resolve()),
            "--workspace-root",
            str(final_workspace.resolve()),
        ]
        command = {
            "schema_name": "persisted-command",
            "schema_version": "1.0.0",
            "run_id": "2" * 32,
            "argv": argv,
            "accepted_exit_codes": [0],
        }
        status = {
            "schema_name": "persisted-command-status",
            "schema_version": "1.0.0",
            "run_id": command["run_id"],
            "state": "failed",
            "exit_code": 40,
            "security": {
                "classification": "no_secret_detected",
                "acceptance_evidence_eligible": True,
            },
        }
        result = {
            "schema_name": "workflow-result",
            "schema_version": "1.0.0",
            "kernel_version": "2.0.0",
            "command": "delivery-quality-final-compile",
            "status": "error",
            "classification": "compile_dependency_gap",
            "evidence_path": None,
            "data": {
                "message": "guarded Final Compile adapter failed",
                "first_failing_gate": "final_compile_adapter_execution",
                "error_code": "final_compile_adapter_failed",
                "exit_code": 1,
                "stderr_path": str(adapter_stderr.resolve()),
                "stdout_path": str((final_workspace / "adapter-stdout.log").resolve()),
            },
        }
        write_json(command_root / "command.json", command)
        write_json(command_root / "status.json", status)
        (command_root / "stdout.log").write_bytes(canonical_bytes(result))
        (command_root / "exit-code.txt").write_text("40\n", encoding="utf-8")
        return command_root

    def refresh_arguments(self, successor: Path | None = None) -> list[str]:
        return [
            "delivery-quality-precompile-inventory-refresh",
            "--run-dir",
            str(self.run),
            "--predecessor-workspace-root",
            str(self.predecessor),
            "--workspace-root",
            str(successor or self.successor),
            "--compile-manifest",
            str(self.manifest),
            "--failed-command-run-dir",
            str(self.failed_command),
            "--approval-reference",
            "https://github.com/Nishijujuba/video2pdf/issues/121",
            "--prepared-at",
            "2026-09-06T03:00:00Z",
        ]


class PrecompileInventoryRefreshCliTests(unittest.TestCase):
    def test_refresh_derives_used_generated_titles_preserves_lineage_and_dispatches_fresh_reviewers(
        self,
    ) -> None:
        fixture = InventoryRefreshFixture(self.id())
        predecessor_files = {
            path.relative_to(fixture.predecessor).as_posix(): path.read_bytes()
            for path in fixture.predecessor.rglob("*")
            if path.is_file()
        }

        completed, envelope = run_cli(*fixture.refresh_arguments())
        self.assertEqual(completed.returncode, 0, envelope)
        self.assertEqual(
            envelope["classification"], "precompile_inventory_refresh_prepared"
        )
        inventory = json.loads(
            (fixture.successor / "reader-facing-text-inventory.json").read_text(
                encoding="utf-8"
            )
        )
        generated = next(
            item
            for item in inventory["items"]
            if item["item_id"] == "generated.local_style.box_titles"
        )
        self.assertEqual(generated["declared_text"], "核心结论\n背景知识\n边界与风险")
        predecessor_inventory = fixture.inventory
        self.assertEqual(
            [item for item in inventory["items"] if item["representation"] != "declared_generated_text"],
            [item for item in predecessor_inventory["items"] if item["representation"] != "declared_generated_text"],
        )
        self.assertEqual(
            (fixture.successor / "artifact-generations.json").read_bytes(),
            (fixture.predecessor / "artifact-generations.json").read_bytes(),
        )
        self.assertEqual(
            (fixture.successor / "semantic-dependencies.json").read_bytes(),
            (fixture.predecessor / "semantic-dependencies.json").read_bytes(),
        )
        refresh = json.loads(
            (fixture.successor / "precompile-inventory-refresh.json").read_text(
                encoding="utf-8"
            )
        )
        attempt = json.loads(
            (fixture.successor / "repair-attempt.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            refresh["predecessor"]["seal_sha256"], fixture.seal["seal_sha256"]
        )
        self.assertEqual(
            refresh["predecessor"]["inventory_sha256"],
            fixture.inventory["inventory_sha256"],
        )
        self.assertEqual(
            refresh["predecessor"]["repair_attempt_sha256"],
            fixture.attempt["attempt_sha256"],
        )
        self.assertEqual(refresh["changed_generated_item_ids"], ["generated.local_style.box_titles"])
        self.assertEqual(attempt["repair_sequence"], 4)
        self.assertEqual(attempt["semantic_attempt_number"], 2)
        self.assertFalse(attempt["semantic_attempt_budget_consumed"])
        self.assertEqual(len(attempt["fresh_reviewer_task_ids"]), 3)
        custody = Path(refresh["downstream_failure_authority"]["custody_root"])
        self.assertEqual(
            {path.name for path in custody.iterdir() if path.is_file()},
            {
                "adapter-stderr.log",
                "command.json",
                "compile-manifest.json",
                "exit-code.txt",
                "status.json",
                "stdout.log",
            },
        )
        self.assertEqual(
            predecessor_files,
            {
                path.relative_to(fixture.predecessor).as_posix(): path.read_bytes()
                for path in fixture.predecessor.rglob("*")
                if path.is_file()
            },
        )

        replayed, replay_envelope = run_cli(*fixture.refresh_arguments())
        self.assertEqual(replayed.returncode, 0, replay_envelope)
        self.assertEqual(
            replay_envelope["data"]["refresh_sha256"], refresh["refresh_sha256"]
        )
        (custody / "status.json").write_text("{}\n", encoding="utf-8")
        stale_replay, stale_replay_envelope = run_cli(*fixture.refresh_arguments())
        self.assertEqual(stale_replay.returncode, 20, stale_replay_envelope)
        self.assertEqual(
            stale_replay_envelope["data"]["first_failing_gate"],
            "precompile_inventory_refresh_replay",
        )
        self.assertEqual(
            stale_replay_envelope["data"]["error_code"],
            "precompile_inventory_refresh_replay_custody_stale",
        )
        competing = fixture.run / "review/precompile/workspaces/competing"
        rejected, rejected_envelope = run_cli(
            *fixture.refresh_arguments(competing)
        )
        self.assertEqual(rejected.returncode, 20, rejected_envelope)
        self.assertEqual(
            rejected_envelope["data"]["first_failing_gate"],
            "precompile_inventory_refresh_successor_claim",
        )
        self.assertEqual(
            rejected_envelope["data"]["error_code"],
            "precompile_inventory_refresh_competing_successor",
        )
        self.assertFalse(competing.exists())

    def test_later_semantic_repair_continues_preserved_attempt_counter(self) -> None:
        fixture = InventoryRefreshFixture(self.id())
        completed, envelope = run_cli(*fixture.refresh_arguments())
        self.assertEqual(completed.returncode, 0, envelope)
        for owner in OWNERS:
            fixture.commit_patch(
                fixture.successor,
                owner,
                fail_first=owner == "writing-quality-reviewer",
            )
        materialized, materialized_envelope = run_cli(
            "delivery-quality-precompile-materialize",
            "--workspace-root",
            str(fixture.successor),
            "--provider-id",
            "precompile-quality-provider",
            "--provider-version",
            "1.0.0",
            "--materialized-at",
            "2026-09-06T03:10:00Z",
        )
        self.assertEqual(materialized.returncode, 0, materialized_envelope)

        from video2pdf_workflow_kernel.precompile_quality import (
            PrecompileQualityProvider,
        )

        generations = json.loads(
            (fixture.successor / "artifact-generations.json").read_text(
                encoding="utf-8"
            )
        )
        body_generation = next(
            item
            for item in generations["artifacts"]
            if item["logical_id"] == "integrated_section_01"
        )
        body_generation["generation"] += 1
        body_generation["sha256"] = "d" * 64
        generations["generation_set_id"] = "issue121-semantic-repair"
        generations["generation_set_sha256"] = canonical_sha(
            {key: value for key, value in generations.items() if key != "generation_set_sha256"}
        )
        inventory = json.loads(
            (fixture.successor / "reader-facing-text-inventory.json").read_text(
                encoding="utf-8"
            )
        )
        inventory["inventory_id"] = "issue121-semantic-repair"
        inventory["generation_set_sha256"] = generations["generation_set_sha256"]
        for item in inventory["items"]:
            if item["source_artifact_logical_id"] == body_generation["logical_id"]:
                item["source_generation"] = body_generation["generation"]
                item["source_sha256"] = body_generation["sha256"]
                item["declared_text"] += "\n修复"
                item["text_sha256"] = hashlib.sha256(
                    item["declared_text"].encode("utf-8")
                ).hexdigest()
            item["item_sha256"] = canonical_sha(
                {key: value for key, value in item.items() if key != "item_sha256"}
            )
        inventory["reader_text_set_sha256"] = canonical_sha(
            [
                {
                    "item_id": item["item_id"],
                    "kind": item["kind"],
                    "representation": item["representation"],
                    "text_sha256": item["text_sha256"],
                }
                for item in inventory["items"]
            ]
        )
        inventory["inventory_sha256"] = canonical_sha(
            {key: value for key, value in inventory.items() if key != "inventory_sha256"}
        )
        candidate_root = fixture.root / "semantic-repair-inputs"
        prepared = PrecompileQualityProvider(PROJECT_ROOT).prepare_repair(
            predecessor_workspace_root=fixture.successor,
            workspace_root=fixture.run / "review/precompile/workspaces/semantic-repair",
            inventory_path=write_json(candidate_root / "inventory.json", inventory),
            artifact_generations_path=write_json(candidate_root / "generations.json", generations),
            semantic_dependencies_path=fixture.successor / "semantic-dependencies.json",
            repair_attempt_number=3,
            prepared_at="2026-09-06T03:20:00Z",
            kernel_production_run_dir=fixture.run,
            repair_sequence=5,
        )
        ledger = json.loads(Path(prepared["repair_attempt_path"]).read_text(encoding="utf-8"))
        self.assertEqual(ledger["semantic_attempt_number"], 3)
        self.assertEqual(ledger["repair_sequence"], 5)

    def test_refresh_rejects_stale_successor_inventory_on_replay(self) -> None:
        """One contradiction: the published inventory body no longer matches its fingerprint."""
        fixture = InventoryRefreshFixture(self.id())
        completed, envelope = run_cli(*fixture.refresh_arguments())
        self.assertEqual(completed.returncode, 0, envelope)

        inventory_path = fixture.successor / "reader-facing-text-inventory.json"
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        inventory["inventory_id"] = "issue121-stale-successor"
        write_json(inventory_path, inventory)

        replayed, replay_envelope = run_cli(*fixture.refresh_arguments())
        self.assertEqual(replayed.returncode, 20, replay_envelope)
        self.assertEqual(
            replay_envelope["data"]["first_failing_gate"],
            "precompile_inventory_refresh_replay",
        )
        self.assertEqual(
            replay_envelope["data"]["error_code"],
            "precompile_inventory_refresh_replay_inventory_stale",
        )

    def test_refresh_rejects_predecessor_outside_current_production(self) -> None:
        """One contradiction: the sealed predecessor has one historical generation."""
        fixture = InventoryRefreshFixture(
            self.id(), stale_predecessor_generation=True
        )

        completed, envelope = run_cli(*fixture.refresh_arguments())
        self.assertEqual(completed.returncode, 20, envelope)
        self.assertEqual(
            envelope["data"]["first_failing_gate"],
            "precompile_inventory_refresh_current_production",
        )
        self.assertEqual(
            envelope["data"]["error_code"],
            "precompile_inventory_refresh_current_production_mismatch",
        )
        self.assertFalse(fixture.successor.exists())
        self.assertFalse(
            (fixture.run / "review/precompile/inventory-refresh").exists()
        )

    def test_refresh_rejects_incomplete_current_production(self) -> None:
        """One contradiction: current Production loses its compile-ready checkpoint."""
        fixture = InventoryRefreshFixture(self.id())
        state_path = fixture.run / "workflow/production-state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["checkpoints"]["draft_compile_ready"] = "pending"
        write_json(state_path, state)

        completed, envelope = run_cli(*fixture.refresh_arguments())
        self.assertEqual(completed.returncode, 20, envelope)
        self.assertEqual(
            envelope["data"]["first_failing_gate"],
            "precompile_inventory_refresh_current_production",
        )
        self.assertEqual(
            envelope["data"]["error_code"],
            "precompile_inventory_refresh_current_production_incomplete",
        )
        self.assertFalse(fixture.successor.exists())
        self.assertFalse(
            (fixture.run / "review/precompile/inventory-refresh").exists()
        )

    def test_refresh_rejects_stale_failure_binding_before_successor_publication(
        self,
    ) -> None:
        # scenario_id: issue121_stale_failed_command_predecessor
        # target_invariant: retained Final Compile command binds the current predecessor
        # mutation_seam: after persisted command publication
        # rematerialized_nodes: none; command.json is the sole declared contradiction
        # intentionally_stale_nodes: command predecessor argument
        # expected_first_gate/code: precompile_inventory_refresh_failure_binding /
        # precompile_inventory_refresh_failure_command_mismatch
        # scenario_class: single_contradiction
        fixture = InventoryRefreshFixture(self.id())
        command_path = fixture.failed_command / "command.json"
        command = json.loads(command_path.read_text(encoding="utf-8"))
        index = command["argv"].index("--precompile-workspace-root") + 1
        command["argv"][index] = str((fixture.run / "stale-predecessor").resolve())
        write_json(command_path, command)

        completed, envelope = run_cli(*fixture.refresh_arguments())
        self.assertEqual(completed.returncode, 20, envelope)
        self.assertEqual(
            envelope["data"]["first_failing_gate"],
            "precompile_inventory_refresh_failure_binding",
        )
        self.assertEqual(
            envelope["data"]["error_code"],
            "precompile_inventory_refresh_failure_command_mismatch",
        )
        self.assertFalse(fixture.successor.exists())
        self.assertFalse(
            (
                fixture.run
                / "review/precompile/inventory-refresh/claims"
                / f"{fixture.seal['seal_sha256']}.json"
            ).exists()
        )


if __name__ == "__main__":
    unittest.main()
