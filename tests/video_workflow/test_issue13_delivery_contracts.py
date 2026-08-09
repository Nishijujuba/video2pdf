from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from video2pdf_workflow_kernel.contracts import ContractRegistry
from video2pdf_workflow_kernel.errors import ContractError


FIXTURES = PROJECT_ROOT / "tests" / "video_workflow" / "fixtures" / "contracts"
CLI = PROJECT_ROOT / "scripts" / "video_workflow.py"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class Issue13DeliveryContractTests(unittest.TestCase):
    def test_public_registry_and_contracts_check_expose_delivery_contracts(self) -> None:
        contracts = ContractRegistry(PROJECT_ROOT)
        checked = contracts.check()

        expected_versions = {
            "run-record@4.0.0",
            "kernel-delivery-target@1.0.0",
            "kernel-session-delivery-target@1.0.0",
            "kernel-delivery-task-index@1.0.0",
            "kernel-delivery-target-archive@1.0.0",
        }
        self.assertTrue(expected_versions.issubset(set(checked["registered_contract_versions"])))

        contracts.validate(
            "run-record",
            read_json(FIXTURES / "run-record.v3.valid.json"),
        )
        run_v4 = read_json(FIXTURES / "run-record.v4.valid.json")
        contracts.validate("run-record", run_v4)
        missing_delivery = copy.deepcopy(run_v4)
        missing_delivery.pop("delivery")
        with self.assertRaises(ContractError):
            contracts.validate("run-record", missing_delivery)

        completed = subprocess.run(
            [sys.executable, "-X", "utf8", "-B", str(CLI), "contracts-check"],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        envelope = json.loads(completed.stdout)
        self.assertTrue(
            expected_versions.issubset(
                set(envelope["data"]["registered_contract_versions"])
            )
        )

    def test_run_v4_delivery_cross_field_semantics_fail_closed(self) -> None:
        contracts = ContractRegistry(PROJECT_ROOT)
        run_v4 = read_json(FIXTURES / "run-record.v4.valid.json")

        wrong_owner = copy.deepcopy(run_v4)
        wrong_owner["delivery"]["ownership"]["session_id"] = "other-session"
        with self.assertRaisesRegex(ContractError, "owner session"):
            contracts.validate("run-record", wrong_owner)

        archived_while_generating = copy.deepcopy(run_v4)
        archived_while_generating["delivery"]["projections"]["session_target"] = None
        archived_while_generating["delivery"]["projections"]["archive"] = {
            "path": "D:\\workspace\\.codex\\delivery-targets\\archive\\session-13\\intent.json",
            "projection_revision": 1,
            "sha256": "e" * 64,
        }
        with self.assertRaisesRegex(ContractError, "archived Run delivery"):
            contracts.validate("run-record", archived_while_generating)

        zero_revision = copy.deepcopy(run_v4)
        zero_revision["delivery"]["projections"]["video_target"][
            "projection_revision"
        ] = 0
        with self.assertRaises(ContractError):
            contracts.validate("run-record", zero_revision)

    def test_projection_contracts_enforce_stage_owner_and_revision_semantics(self) -> None:
        contracts = ContractRegistry(PROJECT_ROOT)
        fixture_by_contract = {
            "kernel-delivery-target": "kernel-delivery-target.valid.json",
            "kernel-session-delivery-target": (
                "kernel-session-delivery-target.valid.json"
            ),
            "kernel-delivery-task-index": "kernel-delivery-task-index.valid.json",
            "kernel-delivery-target-archive": (
                "kernel-delivery-target-archive.valid.json"
            ),
        }
        for contract, fixture in fixture_by_contract.items():
            with self.subTest(contract=contract):
                contracts.validate(contract, read_json(FIXTURES / fixture))

        wrong_session_owner = read_json(
            FIXTURES / "kernel-session-delivery-target.valid.json"
        )
        wrong_session_owner["session_id"] = "other-session"
        with self.assertRaisesRegex(ContractError, "owner session"):
            contracts.validate(
                "kernel-session-delivery-target", wrong_session_owner
            )

        missing_acceptance = read_json(
            FIXTURES / "kernel-delivery-target.valid.json"
        )
        missing_acceptance["stage"] = "accepted"
        with self.assertRaisesRegex(ContractError, "required artifact"):
            contracts.validate("kernel-delivery-target", missing_acceptance)

        zero_index_revision = read_json(
            FIXTURES / "kernel-delivery-task-index.valid.json"
        )
        zero_index_revision["projection_revision"] = 0
        with self.assertRaises(ContractError):
            contracts.validate("kernel-delivery-task-index", zero_index_revision)

    def test_delivery_projection_session_ids_match_runtime_path_segment_policy(self) -> None:
        contracts = ContractRegistry(PROJECT_ROOT)
        fixtures = {
            "kernel-delivery-target": (
                "kernel-delivery-target.valid.json",
                lambda value, session_id: value["ownership"].__setitem__(
                    "session_id", session_id
                ),
            ),
            "kernel-delivery-task-index": (
                "kernel-delivery-task-index.valid.json",
                lambda value, session_id: value["entries"][0].__setitem__(
                    "session_id", session_id
                ),
            ),
            "kernel-delivery-target-archive": (
                "kernel-delivery-target-archive.valid.json",
                lambda value, session_id: value.__setitem__(
                    "session_id", session_id
                ),
            ),
        }
        invalid_session_ids = (
            "../escape",
            "C:\\absolute",
            "CON",
            "session.",
            "session ",
            "s" * 129,
        )

        for contract, (fixture, set_session_id) in fixtures.items():
            for session_id in invalid_session_ids:
                with self.subTest(contract=contract, session_id=session_id):
                    value = read_json(FIXTURES / fixture)
                    set_session_id(value, session_id)
                    with self.assertRaises(ContractError):
                        contracts.validate(contract, value)


if __name__ == "__main__":
    unittest.main()
