from __future__ import annotations

import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator

from scripts import slice10_exit_evidence_contract as contract


class Slice10ExitEvidenceContractTests(unittest.TestCase):
    def test_slice10_contract_is_target_only_and_binds_every_public_tracer(self) -> None:
        self.assertEqual(10, contract.SLICE_NUMBER)
        self.assertEqual(30, len(contract.QUALIFICATION_TEST_TARGETS))
        self.assertEqual(set(contract.QUALIFICATION_TEST_TARGETS), {item["test_target"] for item in contract.RESULT_BINDINGS})
        self.assertEqual({"positive", "negative", "recovery", "fencing"}, set(contract.RESULTS))
        self.assertEqual("legacy_delivery_authority", contract.EXPECTED_CHECKPOINTS[1]["name"])
        self.assertEqual("preserved", contract.EXPECTED_CHECKPOINTS[1]["status"])
        self.assertEqual(7, len(contract.FIXTURE_SPECS))

    def test_exit_evidence_v2_schema_admits_the_two_command_slice10_shape(self) -> None:
        project_root = Path(__file__).resolve().parents[2]
        schema = json.loads(
            (project_root / "schemas/exit-evidence-manifest.v2.schema.json").read_text(
                encoding="utf-8"
            )
        )
        sha256 = "1" * 64
        manifest = {
            "$schema": "https://video2pdf.local/schemas/exit-evidence-manifest.v2.schema.json",
            "schema_version": 2,
            "kind": "video-workflow-exit-evidence",
            "fingerprint_algorithm": "sha256-raw-v1",
            "slice": {"number": 10, "name": contract.SLICE_NAME},
            "slice_base_commit": contract.SLICE_BASE_COMMIT,
            "implementation_commit": "2" * 40,
            "evidence_paths": [
                "evidence/slice-10/exit-evidence-manifest.json",
                "evidence/slice-10/logs/contracts.log",
            ],
            "generated_at": "2026-08-02T00:00:00Z",
            "activation_scope": {
                "kind": "none",
                "runtime_authority_change": False,
                "components_activated": [],
                "legacy_track_authority": "preserved",
            },
            "commands": [
                {
                    "test_id": command_id,
                    "command": ["python", "-m", "unittest"],
                    "expected_exit_code": 0,
                    "actual_exit_code": 0,
                    "log": {
                        "role": "command_log",
                        "path": f"evidence/slice-10/logs/{command_id}.log",
                        "sha256": sha256,
                    },
                    "conforms": True,
                }
                for command_id in ("slice10-contracts", "slice10-acceptance-tests")
            ],
            "expected_checkpoints": contract.EXPECTED_CHECKPOINTS,
            "fixtures": [
                {"role": "contract", "path": contract.FIXTURE_SPECS[0][1], "sha256": sha256}
            ],
            "results": {
                "positive": ["pass"],
                "negative": ["fail_closed"],
                "recovery": ["recovered"],
                "fencing": ["fenced"],
            },
            "result_bindings": [
                {
                    "result_id": "pass",
                    "result_kind": "positive",
                    "command_id": "slice10-acceptance-tests",
                    "test_target": contract.QUALIFICATION_TEST_TARGETS[0],
                }
            ],
            "artifact_fingerprints": [
                {"role": "implementation_artifact", "path": "scripts/video_workflow.py", "sha256": sha256}
            ],
            "unresolved_exceptions": [],
            "overall_decision": "pass",
        }

        Draft202012Validator(schema).validate(manifest)


if __name__ == "__main__":
    unittest.main()
