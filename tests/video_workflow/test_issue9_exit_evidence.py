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
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

class Slice6ExitEvidenceTests(unittest.TestCase):
    def test_slice6_schema_accepts_closed_shape_and_rejects_missing_determinism(self) -> None:
        validator = Draft202012Validator(json.loads((PROJECT_ROOT / "schemas/exit-evidence-manifest.v2.schema.json").read_text()))
        valid = json.loads((PROJECT_ROOT / "tests/video_workflow/fixtures/exit_evidence_manifest.v2.slice6.valid.json").read_text())
        invalid = json.loads((PROJECT_ROOT / "tests/video_workflow/fixtures/exit_evidence_manifest.v2.slice6.missing-determinism.invalid.json").read_text())
        self.assertEqual([], list(validator.iter_errors(valid)))
        self.assertTrue(list(validator.iter_errors(invalid)))

    def test_slice6_contract_closes_commands_results_and_fixtures(self) -> None:
        contract = load_module("slice6_contract", PROJECT_ROOT / "scripts/slice6_exit_evidence_contract.py")
        self.assertEqual((6, "multi-section-production"), (contract.SLICE_NUMBER, contract.SLICE_NAME))
        self.assertEqual(["slice6-contracts","slice6-production","slice6-full-video-workflow","slice5-exit-evidence","slice6-syntax","slice6-diff-check"], [item[0] for item in contract.COMMANDS])
        self.assertEqual({item for values in contract.RESULTS.values() for item in values}, {item["result_id"] for item in contract.RESULT_BINDINGS})
        self.assertEqual({"positive","negative","fencing","restart","recovery"}, set(contract.RESULTS))
        command_by_id = {command_id: command for command_id, command in contract.COMMANDS}
        self.assertTrue(
            all(
                binding["test_target"] in command_by_id[binding["command_id"]]
                for binding in contract.RESULT_BINDINGS
            )
        )
        self.assertTrue((PROJECT_ROOT / "scripts/collect_slice6_exit_evidence.py").is_file())

    def test_generic_validator_registers_slice6_authority(self) -> None:
        for path in (PROJECT_ROOT / "scripts", PROJECT_ROOT / "src"):
            if str(path) not in sys.path: sys.path.insert(0, str(path))
        validator = load_module("slice_validator6", PROJECT_ROOT / "scripts/validate_slice_exit_evidence.py")
        self.assertEqual("6f8241ddb4bd725d3b584dd1c403ed59dda32219", validator.SLICE_CONFIGS[6]["base_commit"])
        self.assertEqual(["positive","negative","fencing","restart","recovery"], validator.SLICE_CONFIGS[6]["result_kinds"])

if __name__ == "__main__": unittest.main()
