from __future__ import annotations

import json
import hashlib
from pathlib import Path
import sys
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


class MultiSectionProductionTests(unittest.TestCase):
    def setUp(self) -> None:
        from tests.video_workflow.test_source_publication_integration import (
            build_decision_ready_authority,
        )
        self.kernel, self.run_dir, _ = build_decision_ready_authority()
        self.kernel.finalize_production_source(
            self.run_dir, published_at="2026-07-21T12:00:00+08:00"
        )

    def _attempt(self, envelope: dict, outputs: dict[str, bytes]) -> str:
        attempt_id = uuid.uuid4().hex[:24]
        attempt_dir = (
            self.run_dir / "workflow/tasks" / envelope["task_id"] / "attempts" / attempt_id
        )
        attempt_dir.mkdir(parents=True)
        items = []
        for name, content in outputs.items():
            path = attempt_dir / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            items.append({"path": name, "sha256": hashlib.sha256(content).hexdigest()})
        record = {
            "schema_name": "production-task-attempt", "schema_version": "1.0.0",
            "kernel_version": "2.0.0", "task_id": envelope["task_id"],
            "attempt_id": attempt_id, "claim_generation": envelope["claim_generation"],
            "claim_token": envelope["claim_token"],
            "envelope_sha256": hashlib.sha256(
                (self.run_dir / "workflow/tasks" / envelope["task_id"] / "envelope.json").read_bytes()
            ).hexdigest(), "outputs": items,
        }
        (attempt_dir / "attempt.json").write_text(json.dumps(record), encoding="utf-8")
        return attempt_id

    @staticmethod
    def _pyramid_payload(envelope: dict) -> bytes:
        return json.dumps({
            "schema_name":"pyramid-evaluation-binding","schema_version":"1.0.0",
            "kernel_version":"2.0.0","target":envelope["pyramid_target"],
            "evaluation_context":envelope["evaluation_context"],"status":"pass",
        }, sort_keys=True).encode()

    def _release_parallel_tasks(self) -> list[dict]:
        outline = self.kernel.production_plan(self.run_dir)["runnable_tasks"][0]
        payload = (PROJECT_ROOT / "tests/video_workflow/fixtures/contracts/outline-contract.v2.valid.json").read_bytes()
        self.kernel.production_advance(self.run_dir, outline["task_id"], self._attempt(outline, {"outline.json":payload}))
        gate = self.kernel.production_plan(self.run_dir)["runnable_tasks"][0]
        self.kernel.production_advance(self.run_dir, gate["task_id"], self._attempt(gate, {"pyramid-report.json":self._pyramid_payload(gate)}))
        return self.kernel.production_plan(self.run_dir)["runnable_tasks"]

    @staticmethod
    def _writer_outputs(section_id: str, slots: list[str], candidates: list[dict] | None = None) -> dict[str, bytes]:
        body = f"\\section{{{section_id}}}\n" + "\n".join(f"% FIGURE_SLOT:{slot}" for slot in slots)
        result = {"schema_name":"writer-result","schema_version":"1.0.0","section_id":section_id,"new_figure_candidates":candidates or []}
        return {f"{section_id}.tex":body.encode(), "writer-result.json":json.dumps(result, sort_keys=True).encode()}

    @staticmethod
    def _figure_outputs(section_id: str, slot_id: str) -> dict[str, bytes]:
        asset = f"png-{slot_id}".encode()
        manifest = {"schema_name":"figure-manifest","schema_version":"2.0.0","kernel_version":"2.0.0","slot_id":slot_id,"section_id":section_id,"asset_path":f"figures/{slot_id}.png","asset_sha256":hashlib.sha256(asset).hexdigest(),"caption":f"Caption {slot_id}","source":{"kind":"source_timestamp","value":"00:01"},"slot_contribution_path":f"work/figures/{slot_id}.tex"}
        contribution = (f"\\begin{{figure}}\n\\centering\n\\includegraphics{{figures/{slot_id}}}\n\\caption{{Caption {slot_id}}}\n\\par\\small Source (source_timestamp): 00:01\n\\end{{figure}}\n").encode()
        manifest["slot_contribution_sha256"] = hashlib.sha256(contribution).hexdigest()
        return {f"{slot_id}.png":asset,"figure-manifest.json":json.dumps(manifest, sort_keys=True).encode(),f"{slot_id}.tex":contribution}

    def test_contracts_register_multi_section_shapes_and_reject_duplicate_identity(self) -> None:
        from video2pdf_workflow_kernel.contracts import ContractRegistry
        from video2pdf_workflow_kernel.errors import ContractError

        contracts = ContractRegistry(PROJECT_ROOT)
        valid = json.loads(
            (PROJECT_ROOT / "tests/video_workflow/fixtures/contracts/outline-contract.v2.valid.json")
            .read_text(encoding="utf-8")
        )
        contracts.validate("outline-contract", valid)
        invalid = dict(valid)
        invalid["sections"] = [valid["sections"][0], valid["sections"][0]]
        with self.assertRaises(ContractError):
            contracts.validate("outline-contract", invalid)

    def test_v2_state_and_integration_contracts_reject_malformed_nested_records(self) -> None:
        from video2pdf_workflow_kernel.contracts import ContractRegistry
        from video2pdf_workflow_kernel.errors import ContractError

        contracts = ContractRegistry(PROJECT_ROOT)
        state = json.loads(
            (PROJECT_ROOT / "tests/video_workflow/fixtures/contracts/production-state.v2.valid.json")
            .read_text(encoding="utf-8")
        )
        state["claims"]["writer-section-01"] = {
            "task_id": "a" * 32,
            "claim_generation": 1,
            "claim_token": "b" * 32,
            "status": "available",
        }
        state["claims"]["writer-section-01"]["claim_generation"] = 0
        with self.assertRaises(ContractError):
            contracts.validate("production-state", state)

        integration = json.loads(
            (PROJECT_ROOT / "tests/video_workflow/fixtures/contracts/integration-manifest.v2.valid.json")
            .read_text(encoding="utf-8")
        )
        integration["main"]["sha256"] = "not-a-sha256"
        with self.assertRaises(ContractError):
            contracts.validate("integration-manifest", integration)

    def test_three_sections_release_isolated_writers_and_required_figures_after_outline_barrier(self) -> None:
        tasks = self._release_parallel_tasks()
        self.assertEqual(6, len(tasks))
        self.assertEqual(3, sum(task["role"] == "writer" for task in tasks))
        self.assertEqual(3, sum(task["role"] == "figure" for task in tasks))
        self.assertEqual(6, len({task["task_id"] for task in tasks}))
        self.assertEqual(6, len({task["attempt_root"] for task in tasks}))
        for index, left in enumerate(tasks):
            for right in tasks[index + 1:]:
                self.assertTrue(set(left["write_set"]).isdisjoint(right["write_set"]))
        self.assertEqual(
            ["figure-required-figure-01", "figure-required-figure-02", "figure-required-figure-03",
             "writer-section-01", "writer-section-02", "writer-section-03"],
            [task["logical_task_key"] for task in tasks],
        )

    def test_disjoint_section_attempts_execute_concurrently_and_promote_serially(self) -> None:
        tasks = [task for task in self._release_parallel_tasks() if task["role"] == "writer"][:2]
        attempts = [(task, self._attempt(task, self._writer_outputs(task["section_id"], [f"figure_{task['section_id'][-2:]}"]))) for task in tasks]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda pair: self.kernel.production_advance(self.run_dir, pair[0]["task_id"], pair[1]), attempts))
        self.assertEqual(2, len(results))
        state = json.loads((self.run_dir / "workflow/production-state.json").read_text(encoding="utf-8"))
        self.assertEqual({task["logical_task_key"] for task in tasks}, set(state["promotion_sequence"][-2:]))
        self.assertTrue(all(result["promotion_sequence"] >= 1 for result in results))

    def test_overlapping_write_sets_are_rejected_before_plan_or_promotion(self) -> None:
        from video2pdf_workflow_kernel.errors import ContractError
        outline = self.kernel.production_plan(self.run_dir)["runnable_tasks"][0]
        value = json.loads((PROJECT_ROOT / "tests/video_workflow/fixtures/contracts/outline-contract.v2.valid.json").read_text())
        value["required_figure_slots"][1]["placement_marker"] = value["required_figure_slots"][0]["placement_marker"]
        before = json.loads((self.run_dir / "workflow/production-state.json").read_text())
        with self.assertRaises(ContractError):
            self.kernel.production_advance(self.run_dir, outline["task_id"], self._attempt(outline, {"outline.json":json.dumps(value).encode()}))
        after = json.loads((self.run_dir / "workflow/production-state.json").read_text())
        self.assertEqual(before["receipts"], after["receipts"])

    def test_required_figure_wave_runs_while_other_writers_remain_active(self) -> None:
        tasks = self._release_parallel_tasks()
        writer = next(task for task in tasks if task["logical_task_key"] == "writer-section-01")
        figure = next(task for task in tasks if task["logical_task_key"] == "figure-required-figure-01")
        start = Barrier(2)

        def run_worker(task: dict) -> dict:
            start.wait(timeout=10)
            outputs = (
                self._writer_outputs("section_01", ["figure_01"])
                if task["role"] == "writer"
                else self._figure_outputs("section_01", "figure_01")
            )
            return self.kernel.production_advance(
                self.run_dir, task["task_id"], self._attempt(task, outputs)
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run_worker, (writer, figure)))
        keys = {
            task["logical_task_key"]
            for result in results
            for task in result["runnable_tasks"]
        }
        self.assertIn("pyramid-section-section-01", keys)
        self.assertIn("writer-section-02", keys)
        state = json.loads((self.run_dir / "workflow/production-state.json").read_text())
        self.assertEqual("integrated", state["sections"]["section_01"]["status"])

    def test_required_candidate_over_budget_persists_and_exposes_section_block(self) -> None:
        from video2pdf_workflow_kernel.errors import ContractError

        writer = next(
            task
            for task in self._release_parallel_tasks()
            if task["logical_task_key"] == "writer-section-01"
        )
        candidates = [
            {
                "candidate_id": "diagram-a", "section_id": "section_01",
                "teaching_purpose": "Explain A",
                "placement_marker": "% FIGURE_SLOT:figure_01_incremental_01",
                "evidence": {"source_timestamp": "00:02"},
                "proposed_figure_type": "diagram",
                "prose_insufficiency_reason": "spatial relation", "priority": "required",
            },
            {
                "candidate_id": "diagram-b", "section_id": "section_01",
                "teaching_purpose": "Explain B",
                "placement_marker": "% FIGURE_SLOT:figure_01_incremental_02",
                "evidence": {"source_timestamp": "00:03"},
                "proposed_figure_type": "diagram",
                "prose_insufficiency_reason": "temporal relation", "priority": "required",
            },
        ]
        with self.assertRaisesRegex(ContractError, "required incremental figure budget exceeded"):
            self.kernel.production_advance(
                self.run_dir,
                writer["task_id"],
                self._attempt(
                    writer,
                    self._writer_outputs(
                        "section_01",
                        ["figure_01", "figure_01_incremental_01"],
                        candidates,
                    ),
                ),
            )

        state = json.loads((self.run_dir / "workflow/production-state.json").read_text())
        section = state["sections"]["section_01"]
        self.assertEqual("blocked", section["status"])
        self.assertEqual(
            [{
                "candidate_id": "diagram-b",
                "priority": "required",
                "reason": "per-section incremental figure budget exhausted",
            }],
            section["blocked_evidence"],
        )
        self.assertNotIn("writer-section-01", state["receipts"])
        plan = self.kernel.production_plan(self.run_dir)
        self.assertEqual(["section_01"], [item["section_id"] for item in plan["blocked_sections"]])
        self.assertNotIn(
            "writer-section-01",
            {task["logical_task_key"] for task in plan["runnable_tasks"]},
        )

    def test_writer_candidate_launches_one_deterministic_incremental_wave(self) -> None:
        tasks = self._release_parallel_tasks()
        writer = next(task for task in tasks if task["logical_task_key"] == "writer-section-01")
        candidate = {"candidate_id":"diagram-a","section_id":"section_01","teaching_purpose":"Explain A","placement_marker":"% FIGURE_SLOT:figure_01_incremental_01","evidence":{"source_timestamp":"00:02"},"proposed_figure_type":"diagram","prose_insufficiency_reason":"spatial relation","priority":"required"}
        result = self.kernel.production_advance(self.run_dir, writer["task_id"], self._attempt(writer, self._writer_outputs("section_01", ["figure_01","figure_01_incremental_01"], [candidate])))
        task = next(task for task in result["runnable_tasks"] if task["logical_task_key"] == "figure-incremental-figure-01-incremental-01")
        self.assertIn("writer_result_section_01", {item["logical_id"] for item in task["input_generations"]})
        state = json.loads((self.run_dir / "workflow/production-state.json").read_text())
        self.assertEqual(1, state["sections"]["section_01"]["incremental_wave_count"])

    def test_cross_section_candidate_is_rejected_before_promotion(self) -> None:
        from video2pdf_workflow_kernel.errors import ContractError
        writer = next(task for task in self._release_parallel_tasks() if task["logical_task_key"] == "writer-section-01")
        candidate = {"candidate_id":"cross","section_id":"section_02","teaching_purpose":"Wrong owner","placement_marker":"% FIGURE_SLOT:figure_01_incremental_01","evidence":{"source_timestamp":"00:02"},"proposed_figure_type":"diagram","prose_insufficiency_reason":"spatial","priority":"required"}
        with self.assertRaisesRegex(ContractError, "crosses section"):
            self.kernel.production_advance(self.run_dir, writer["task_id"], self._attempt(writer, self._writer_outputs("section_01", ["figure_01","figure_01_incremental_01"], [candidate])))
        state = json.loads((self.run_dir / "workflow/production-state.json").read_text())
        self.assertNotIn("writer-section-01", state["receipts"])

    def test_second_incremental_figure_wave_fails_closed_after_writer_supersede(self) -> None:
        from video2pdf_workflow_kernel.errors import ContractError
        tasks = self._release_parallel_tasks()
        writer = next(task for task in tasks if task["logical_task_key"] == "writer-section-01")
        first = {"candidate_id":"diagram-a","section_id":"section_01","teaching_purpose":"Explain A","placement_marker":"% FIGURE_SLOT:figure_01_incremental_01","evidence":{"source_timestamp":"00:02"},"proposed_figure_type":"diagram","prose_insufficiency_reason":"spatial","priority":"required"}
        result = self.kernel.production_advance(self.run_dir, writer["task_id"], self._attempt(writer, self._writer_outputs("section_01", ["figure_01","figure_01_incremental_01"], [first])))
        incremental = next(task for task in result["runnable_tasks"] if task["logical_task_key"] == "figure-incremental-figure-01-incremental-01")
        self.kernel.production_advance(self.run_dir, incremental["task_id"], self._attempt(incremental, self._figure_outputs("section_01", "figure_01_incremental_01")))
        replanned = self.kernel.production_plan(self.run_dir, supersede_task_id=writer["task_id"], expected_claim_generation=1)
        replacement = next(task for task in replanned["runnable_tasks"] if task["task_id"] == writer["task_id"])
        before = json.loads((self.run_dir / "workflow/production-state.json").read_text())
        second = dict(first, candidate_id="diagram-b")
        with self.assertRaisesRegex(ContractError, "budget exhausted"):
            self.kernel.production_advance(self.run_dir, replacement["task_id"], self._attempt(replacement, self._writer_outputs("section_01", ["figure_01","figure_01_incremental_01"], [second])))
        after = json.loads((self.run_dir / "workflow/production-state.json").read_text())
        self.assertEqual(1, after["sections"]["section_01"]["incremental_wave_count"])
        self.assertEqual(before["artifacts"], after["artifacts"])
        self.assertEqual(before["claims"], after["claims"])

    def _complete_to_main_integration(self, reverse: bool = False) -> tuple[bytes, dict]:
        tasks = self._release_parallel_tasks()
        if reverse:
            tasks = list(reversed(tasks))
        for task in tasks:
            if task["role"] == "writer":
                outputs = self._writer_outputs(task["section_id"], [f"figure_{task['section_id'][-2:]}"])
            else:
                outputs = self._figure_outputs(task["section_id"], task["slot_id"])
            self.kernel.production_advance(self.run_dir, task["task_id"], self._attempt(task, outputs))
        gates = [task for task in self.kernel.production_plan(self.run_dir)["runnable_tasks"] if task["role"] == "pyramid_section"]
        if reverse:
            gates.reverse()
        for gate in gates:
            self.kernel.production_advance(self.run_dir, gate["task_id"], self._attempt(gate, {"pyramid-report.json":self._pyramid_payload(gate)}))
        main = (self.run_dir / "work/integration/main.tex").read_bytes()
        manifest = json.loads((self.run_dir / "workflow/integration-manifest.json").read_text())
        return main, manifest

    def test_identical_inputs_with_reversed_completion_order_produce_identical_manifest(self) -> None:
        main_a, manifest_a = self._complete_to_main_integration(False)
        from tests.video_workflow.test_source_publication_integration import build_decision_ready_authority
        self.kernel, self.run_dir, _ = build_decision_ready_authority()
        self.kernel.finalize_production_source(self.run_dir, published_at="2026-07-21T12:00:00+08:00")
        main_b, manifest_b = self._complete_to_main_integration(True)
        self.assertEqual(main_a, main_b)
        self.assertEqual([item["logical_id"] for item in manifest_a["sections"]], [item["logical_id"] for item in manifest_b["sections"]])
        self.assertEqual([item["logical_id"] for item in manifest_a["figures"]], [item["logical_id"] for item in manifest_b["figures"]])
        self.assertEqual(
            [(item["path"], item["sha256"]) for item in manifest_a["sections"] + manifest_a["figures"]],
            [(item["path"], item["sha256"]) for item in manifest_b["sections"] + manifest_b["figures"]],
        )

    def test_branch_change_invalidates_only_transitive_dependants(self) -> None:
        self._complete_to_main_integration()
        state = json.loads((self.run_dir / "workflow/production-state.json").read_text())
        writer = state["claims"]["writer-section-01"]
        self.kernel.production_plan(self.run_dir, supersede_task_id=writer["task_id"], expected_claim_generation=writer["claim_generation"])
        changed = json.loads((self.run_dir / "workflow/production-state.json").read_text())
        self.assertNotIn("integrated_section_01", changed["artifacts"])
        self.assertNotIn("integrated_main", changed["artifacts"])
        self.assertIn("integrated_section_02", changed["artifacts"])
        self.assertIn("pyramid_section_02_report", changed["artifacts"])

    def test_writer_supersede_invalidates_incremental_figure_bound_to_old_result(self) -> None:
        tasks = self._release_parallel_tasks()
        writer = next(task for task in tasks if task["logical_task_key"] == "writer-section-01")
        candidate = {
            "candidate_id": "diagram-a", "section_id": "section_01",
            "teaching_purpose": "Explain A",
            "placement_marker": "% FIGURE_SLOT:figure_01_incremental_01",
            "evidence": {"source_timestamp": "00:02"},
            "proposed_figure_type": "diagram",
            "prose_insufficiency_reason": "spatial relation", "priority": "required",
        }
        advanced = self.kernel.production_advance(
            self.run_dir,
            writer["task_id"],
            self._attempt(
                writer,
                self._writer_outputs(
                    "section_01", ["figure_01", "figure_01_incremental_01"], [candidate]
                ),
            ),
        )
        incremental = next(
            task
            for task in advanced["runnable_tasks"]
            if task["logical_task_key"] == "figure-incremental-figure-01-incremental-01"
        )
        self.kernel.production_advance(
            self.run_dir,
            incremental["task_id"],
            self._attempt(
                incremental,
                self._figure_outputs("section_01", "figure_01_incremental_01"),
            ),
        )
        before = json.loads((self.run_dir / "workflow/production-state.json").read_text())
        old_figure_claim = before["claims"][incremental["logical_task_key"]]

        plan = self.kernel.production_plan(
            self.run_dir,
            supersede_task_id=writer["task_id"],
            expected_claim_generation=writer["claim_generation"],
        )
        changed = json.loads((self.run_dir / "workflow/production-state.json").read_text())
        self.assertNotIn(incremental["logical_task_key"], changed["completed_tasks"])
        self.assertNotIn(incremental["logical_task_key"], changed["receipts"])
        self.assertNotIn("figure_asset_figure_01_incremental_01", changed["artifacts"])
        self.assertNotIn("writer_result_section_01", changed["artifacts"])
        self.assertEqual(
            old_figure_claim["claim_generation"] + 1,
            changed["claims"][incremental["logical_task_key"]]["claim_generation"],
        )
        self.assertEqual(
            {"writer-section-01"},
            {
                task["logical_task_key"]
                for task in plan["runnable_tasks"]
                if task.get("section_id") == "section_01"
            },
        )

    def test_late_worker_cannot_overwrite_advanced_section_generation(self) -> None:
        from video2pdf_workflow_kernel.errors import KernelConflict
        tasks = self._release_parallel_tasks()
        writer = next(task for task in tasks if task["logical_task_key"] == "writer-section-01")
        late_attempt = self._attempt(writer, self._writer_outputs("section_01", ["figure_01"]))
        self.kernel.production_plan(self.run_dir, supersede_task_id=writer["task_id"], expected_claim_generation=1)
        before = json.loads((self.run_dir / "workflow/production-state.json").read_text())
        with self.assertRaises(KernelConflict):
            self.kernel.production_advance(self.run_dir, writer["task_id"], late_attempt)
        after = json.loads((self.run_dir / "workflow/production-state.json").read_text())
        self.assertEqual(before["artifacts"], after["artifacts"])


if __name__ == "__main__":
    unittest.main()
