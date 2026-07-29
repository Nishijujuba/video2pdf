from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import unittest

from jsonschema import Draft202012Validator


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Slice5ExitEvidenceTests(unittest.TestCase):
    def test_slice5_schema_accepts_closed_shape_and_rejects_missing_recovery(self) -> None:
        schema = json.loads(
            (PROJECT_ROOT / "schemas/exit-evidence-manifest.v2.schema.json").read_text(encoding="utf-8")
        )
        validator = Draft202012Validator(schema)
        valid = json.loads(
            (PROJECT_ROOT / "tests/video_workflow/fixtures/exit_evidence_manifest.v2.slice5.valid.json").read_text(encoding="utf-8")
        )
        invalid = json.loads(
            (PROJECT_ROOT / "tests/video_workflow/fixtures/exit_evidence_manifest.v2.slice5.missing-closure.invalid.json").read_text(encoding="utf-8")
        )
        self.assertEqual([], list(validator.iter_errors(valid)))
        self.assertTrue(list(validator.iter_errors(invalid)))

    def test_slice5_contract_closes_commands_results_and_fixtures(self) -> None:
        contract = load_module(
            "slice5_contract", PROJECT_ROOT / "scripts/slice5_exit_evidence_contract.py"
        )
        self.assertEqual(5, contract.SLICE_NUMBER)
        self.assertEqual("single-section-production", contract.SLICE_NAME)
        self.assertEqual(
            ["slice5-contracts", "slice5-production", "slice5-full-video-workflow", "slice4-exit-evidence", "slice5-syntax", "slice5-diff-check"],
            [test_id for test_id, _ in contract.COMMANDS],
        )
        self.assertEqual(
            {item for values in contract.RESULTS.values() for item in values},
            {item["result_id"] for item in contract.RESULT_BINDINGS},
        )
        self.assertEqual(3, len(contract.FIXTURE_SPECS))
        self.assertTrue((PROJECT_ROOT / "scripts/collect_slice5_exit_evidence.py").is_file())

    def test_generic_validator_registers_slice5_authority(self) -> None:
        scripts = str(PROJECT_ROOT / "scripts")
        src = str(PROJECT_ROOT / "src")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        if src not in sys.path:
            sys.path.insert(0, src)
        validator = load_module(
            "slice_validator", PROJECT_ROOT / "scripts/validate_slice_exit_evidence.py"
        )
        config = validator.SLICE_CONFIGS[5]
        self.assertEqual(
            "7b33a2dcf8b19608943f12efd814907a69c35e8f",
            config["base_commit"],
        )
        self.assertEqual(
            ["positive", "negative", "recovery"], config["result_kinds"]
        )


if __name__ == "__main__":
    unittest.main()
