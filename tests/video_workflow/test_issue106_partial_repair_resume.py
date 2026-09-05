from __future__ import annotations

import hashlib
from pathlib import Path
import unittest
from unittest.mock import patch


from tests.video_workflow.test_issue106_reader_text_continuation import (
    Issue106ReaderTextContinuationTests,
    fingerprint,
    write_json,
)
from video2pdf_workflow_kernel.errors import ContractError, ProductionFault
from video2pdf_workflow_kernel.precompile_repair_promotion import (
    PrecompileRepairPromotionProvider,
)
from video2pdf_workflow_kernel.utils import read_json


class _PromotionCaptured(RuntimeError):
    pass


class Issue106PartialRepairResumeTests(unittest.TestCase):
    def _genuine_promotion_case(self) -> tuple[
        PrecompileRepairPromotionProvider, dict[str, object]
    ]:
        captured: dict[str, object] = {}

        def capture(
            provider: PrecompileRepairPromotionProvider, **arguments: object
        ) -> dict[str, object]:
            captured["provider"] = provider
            captured["arguments"] = arguments
            raise _PromotionCaptured

        fixture = Issue106ReaderTextContinuationTests(
            "test_non_runtime_exact_replay_reuses_the_bound_workspace_read_only"
        )
        with patch.object(PrecompileRepairPromotionProvider, "promote", new=capture):
            with self.assertRaises(_PromotionCaptured):
                fixture._non_runtime_bound_repair_fixture()

        return captured["provider"], captured["arguments"]

    @staticmethod
    def _authorize_changed_outputs(
        arguments: dict[str, object], *, include_figure: bool
    ) -> None:
        failure_path = Path(arguments["repair_failure_authority_path"])
        failure = read_json(failure_path)
        allowed_write_set = [
            "work/writers/section_01.result.json",
            "work/writers/section_01.tex",
        ]
        if include_figure:
            allowed_write_set.extend(
                [
                    "work/figures/figure-manifest.json",
                    "work/figures/figure_01.tex",
                ]
            )
        failure["failure_set"][0]["repair_write_set"] = sorted(allowed_write_set)
        failure["report_sha256"] = fingerprint(failure, "report_sha256")
        write_json(failure_path, failure)

        bundle_path = Path(arguments["repair_bundle_path"])
        bundle = read_json(bundle_path)
        for payload_suffix in ["payload/writers/section_01.result.json"]:
            entry = next(
                item
                for item in bundle["derived_payload"]
                if item["path"].endswith(payload_suffix)
            )
            payload_path = Path(arguments["run_dir"]) / entry["path"]
            payload_path.write_bytes(payload_path.read_bytes() + b"\n")
            entry["sha256"] = hashlib.sha256(
                payload_path.read_bytes()
            ).hexdigest()
        if include_figure:
            contribution_entry = next(
                item
                for item in bundle["derived_payload"]
                if item["path"].endswith("payload/figures/figure_01.tex")
            )
            contribution_path = (
                Path(arguments["run_dir"]) / contribution_entry["path"]
            )
            contribution_path.write_bytes(
                contribution_path.read_bytes().replace(
                    b"Declared and observed compile inputs.",
                    b"Repaired declared and observed compile inputs.",
                )
            )
            contribution_entry["sha256"] = hashlib.sha256(
                contribution_path.read_bytes()
            ).hexdigest()
            manifest_entry = next(
                item
                for item in bundle["derived_payload"]
                if item["path"].endswith("payload/figures/figure_01.manifest.json")
            )
            manifest_path = Path(arguments["run_dir"]) / manifest_entry["path"]
            manifest = read_json(manifest_path)
            manifest["caption"] = "Repaired declared and observed compile inputs."
            manifest["slot_contribution_sha256"] = contribution_entry["sha256"]
            write_json(manifest_path, manifest)
            manifest_entry["sha256"] = hashlib.sha256(
                manifest_path.read_bytes()
            ).hexdigest()
        write_json(bundle_path, bundle)

    def test_public_retry_accepts_authorized_partial_remaining_write_set(self) -> None:
        provider, arguments = self._genuine_promotion_case()
        self._authorize_changed_outputs(arguments, include_figure=True)

        with self.assertRaises(ProductionFault):
            provider.promote(
                **arguments,
                fault_point="after_state_committed",
                fault_logical_task_key="writer-section-01",
            )

        with patch.object(
            provider,
            "_resume_production_repair",
            side_effect=RuntimeError("partial replay resumed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "partial replay resumed"):
                provider.promote(**arguments)

    def test_public_retry_accepts_empty_remaining_write_set(self) -> None:
        provider, arguments = self._genuine_promotion_case()
        self._authorize_changed_outputs(arguments, include_figure=False)

        with self.assertRaises(ProductionFault):
            provider.promote(
                **arguments,
                fault_point="after_state_committed",
                fault_logical_task_key="writer-section-01",
            )

        result = provider.promote(**arguments)

        self.assertEqual("precompile_repair_promoted", result["classification"])
        self.assertEqual(
            len(read_json(Path(arguments["repair_bundle_path"]))["task_order"]),
            result["promoted_task_count"],
        )

    def test_public_promotion_rejects_unauthorized_extra_producer_output(self) -> None:
        # scenario_id: issue106_unauthorized_extra_producer_output
        # target_invariant: remaining producer changes stay within failed-result authority
        # mutation_seam: repair bundle adds a changed writer result outside the allowed set
        # rematerialized_nodes: repair bundle entry SHA and bundle bytes
        # intentionally_stale_nodes: failed Precompile report authority
        # expected_first_gate: precompile_repair_allowed_write_set
        # expected_error_code: precompile_repair_write_set_mismatch
        # scenario_class: single_contradiction
        provider, arguments = self._genuine_promotion_case()
        bundle_path = Path(arguments["repair_bundle_path"])
        bundle = read_json(bundle_path)
        result_entry = next(
            entry
            for entry in bundle["derived_payload"]
            if entry["path"].endswith("payload/writers/section_01.result.json")
        )
        result_path = Path(arguments["run_dir"]) / result_entry["path"]
        result_path.write_bytes(result_path.read_bytes() + b"\n")
        result_entry["sha256"] = hashlib.sha256(result_path.read_bytes()).hexdigest()
        write_json(bundle_path, bundle)

        with self.assertRaises(ContractError) as raised:
            provider.promote(**arguments)

        self.assertEqual(
            "precompile_repair_allowed_write_set",
            raised.exception.data["first_failing_gate"],
        )
        self.assertEqual(
            "precompile_repair_write_set_mismatch",
            raised.exception.data["error_code"],
        )


if __name__ == "__main__":
    unittest.main()
