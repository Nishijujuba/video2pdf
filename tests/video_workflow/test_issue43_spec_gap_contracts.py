from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

from jsonschema import Draft202012Validator

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from scripts import issue43_exit_evidence_contract as contract
from tests.video_workflow._issue43_git_authority import (
    build_current_global_gate_authority,
)
from tests.video_workflow._test_run import new_case_dir
from video2pdf_workflow_kernel.global_gate_exit_evidence import (
    ExitEvidenceValidationError,
    MIRROR_SPECS as RUNTIME_MIRROR_SPECS,
    validate_global_gate_exit_evidence,
)

class Issue43SpecGapContractTests(unittest.TestCase):
    def test_exit_evidence_binds_delivery_guard_runtime_script_mirrors(self) -> None:
        required = {
            (
                ".agents/skills/final-delivery-acceptance/scripts/delivery_guard.py",
                ".claude/skills/final-delivery-acceptance/scripts/delivery_guard.py",
            )
        }
        self.assertTrue(required <= set(contract.MIRROR_SPECS))
        self.assertEqual(contract.MIRROR_SPECS, RUNTIME_MIRROR_SPECS)

    def test_incomplete_delivery_guard_runtime_mirror_evidence_fails_closed(self) -> None:
        # scenario_id: delivery_guard_runtime_mirror_omitted
        # authority: closed MIRROR_SPECS -> boundary: Exit Evidence validation
        # mutation: omit one mirror check; rematerialized: none; stale: mirror_checks
        # expected_first_gate: mirror_checks
        # expected_error_code: global_gate_mirror_stale
        root = new_case_dir(f"{self.id()}-runtime-script-v2", label="issue43-mirror-omitted")
        repository, manifest_path = build_current_global_gate_authority(root)
        damaged = json.loads(manifest_path.read_text(encoding="utf-8"))
        damaged["mirror_checks"] = damaged["mirror_checks"][:-1]
        damaged_path = repository / "damaged-mirror-evidence.json"
        damaged_path.write_text(
            json.dumps(damaged, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        with self.assertRaises(ExitEvidenceValidationError) as raised:
            validate_global_gate_exit_evidence(
                damaged_path,
                project_root=repository,
            )
        self.assertEqual("mirror_checks", raised.exception.first_failing_gate)
        self.assertEqual("global_gate_mirror_stale", raised.exception.error_code)

    def test_global_gate_authority_is_required_by_input_and_report_contracts(self) -> None:
        cases = (
            (
                "acceptance-v2-input-binding",
                PROJECT_ROOT / "schemas/delivery-quality/v1/acceptance-v2-input-binding.v1.schema.json",
                PROJECT_ROOT / "delivery-quality/v1/acceptance-v2-input-binding.example.v1.json",
            ),
            (
                "acceptance-report-v2",
                PROJECT_ROOT / "schemas/delivery-quality/v1/acceptance-report-v2.v1.schema.json",
                PROJECT_ROOT / "delivery-quality/v1/acceptance-report-v2.example.v1.json",
            ),
        )
        for schema_name, schema_path, positive_path in cases:
            with self.subTest(
                scenario_id=f"{schema_name}-missing-global-gate-authority",
                expected_first_gate="delivery_quality_schema_validation",
                expected_error_code="contract_invalid",
            ):
                schema = json.loads(schema_path.read_text(encoding="utf-8"))
                validator = Draft202012Validator(schema)
                positive = json.loads(positive_path.read_text(encoding="utf-8"))
                validator.validate(positive)
                negative = deepcopy(positive)
                negative.pop("global_gate_authority")
                errors = list(validator.iter_errors(negative))
                self.assertTrue(errors)
                self.assertIn("global_gate_authority", errors[0].message)


if __name__ == "__main__":
    unittest.main()
