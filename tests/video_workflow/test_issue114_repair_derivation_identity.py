from __future__ import annotations

from pathlib import Path
import unittest

from tests.video_workflow.test_issue113_semantic_input_repair import (
    Issue113SemanticInputRepairTests,
)
from video2pdf_workflow_kernel.errors import ContractError
from video2pdf_workflow_kernel.utils import read_json


class RepairDerivationOperationIdentityTests(unittest.TestCase):
    def _case(self) -> tuple[object, dict[str, object], Path, Path]:
        fixture = Issue113SemanticInputRepairTests()
        provider, arguments = fixture._semantic_only_case()
        semantic_inventory = Path(arguments["inventory_path"])
        semantic_dependencies = Path(arguments["semantic_dependencies_path"])
        predecessor = Path(arguments["predecessor_workspace_root"])
        arguments["inventory_path"] = predecessor / "reader-facing-text-inventory.json"
        arguments["semantic_dependencies_path"] = (
            predecessor / "semantic-dependencies.json"
        )
        return provider, arguments, semantic_inventory, semantic_dependencies

    def test_public_promotion_separates_partial_derivations_by_every_byte_input(
        self,
    ) -> None:
        # Authority input: prepared_at plus candidate inventory/dependency bytes.
        # Derived nodes: operation id -> successor generations/inventory/dependencies
        # -> visual provenance. Boundary: immutable production-repair-promotions root.
        # The first request is coherent and intentionally reaches only the existing
        # no-change gate. Each second request changes exactly one derivation input.
        for scenario in ("prepared_at", "inventory", "semantic_dependencies"):
            with self.subTest(scenario=scenario):
                provider, arguments, semantic_inventory, semantic_dependencies = (
                    self._case()
                )
                promotions_root = (
                    Path(arguments["run_dir"])
                    / "review/precompile/production-repair-promotions"
                )
                with self.assertRaises(ContractError) as initial:
                    provider.promote(**arguments)
                self.assertEqual(
                    "precompile_repair_evaluation_inputs_unchanged",
                    initial.exception.data["error_code"],
                )
                initial_operations = {path.name for path in promotions_root.iterdir()}
                self.assertEqual(1, len(initial_operations))

                expected_success = scenario != "prepared_at"
                if scenario == "prepared_at":
                    arguments["prepared_at"] = "2026-09-06T00:03:01Z"
                elif scenario == "inventory":
                    arguments["inventory_path"] = semantic_inventory
                else:
                    arguments["semantic_dependencies_path"] = semantic_dependencies

                if expected_success:
                    result = provider.promote(**arguments)
                    self.assertIn(
                        result["classification"],
                        {
                            "precompile_repair_promoted",
                            "precompile_repair_already_promoted",
                        },
                    )
                else:
                    with self.assertRaises(ContractError) as repeated_no_change:
                        provider.promote(**arguments)
                    self.assertEqual(
                        "precompile_repair_evaluation_inputs_unchanged",
                        repeated_no_change.exception.data["error_code"],
                    )
                self.assertEqual(
                    2,
                    len({path.name for path in promotions_root.iterdir()}),
                )

    def test_public_promotion_exact_replay_reuses_the_bound_derivation(self) -> None:
        fixture = Issue113SemanticInputRepairTests()
        provider, arguments = fixture._semantic_only_case()
        first = provider.promote(**arguments)
        promotions_root = (
            Path(arguments["run_dir"])
            / "review/precompile/production-repair-promotions"
        )
        operation_bytes = {
            path.relative_to(promotions_root): path.read_bytes()
            for path in promotions_root.rglob("*")
            if path.is_file()
        }

        replayed = provider.promote(**arguments)

        self.assertEqual("precompile_repair_already_promoted", replayed["classification"])
        self.assertEqual(first["repair_attempt_path"], replayed["repair_attempt_path"])
        self.assertEqual(
            operation_bytes,
            {
                path.relative_to(promotions_root): path.read_bytes()
                for path in promotions_root.rglob("*")
                if path.is_file()
            },
        )


if __name__ == "__main__":
    unittest.main()
