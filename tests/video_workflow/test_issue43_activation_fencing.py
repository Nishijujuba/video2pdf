from __future__ import annotations

from pathlib import Path
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from tests.video_workflow._issue43_git_authority import (
    build_current_global_gate_authority,
)
from tests.video_workflow._test_run import new_case_dir
from video2pdf_workflow_kernel.errors import AcceptanceV2Rejected
from video2pdf_workflow_kernel.global_gate import GlobalGatePublisher


class Issue43ActivationFencingTests(unittest.TestCase):
    def test_distinct_valid_activation_authorities_reach_the_cas_fence(self) -> None:
        control_store_root = new_case_dir(f"{self.id()}-v2", label="issue43-activation-cas")
        first_source = new_case_dir(f"{self.id()}-v2-first", label="issue43-authority-source")
        second_source = new_case_dir(f"{self.id()}-v2-second", label="issue43-authority-source")
        first_repository, first_manifest = build_current_global_gate_authority(first_source)
        second_repository, second_manifest = build_current_global_gate_authority(second_source)

        first = GlobalGatePublisher(project_root=first_repository).activate(
            control_store_root=control_store_root,
            exit_evidence=first_manifest,
            activated_at="2026-08-03T00:00:00Z",
        )
        self.assertFalse(first["idempotent"])

        with self.assertRaises(AcceptanceV2Rejected) as raised:
            GlobalGatePublisher(project_root=second_repository).activate(
                control_store_root=control_store_root,
                exit_evidence=second_manifest,
                activated_at="2026-08-03T00:00:01Z",
            )
        self.assertEqual("activation_fencing", raised.exception.data["first_failing_gate"])
        self.assertEqual(
            "global_gate_authority_conflict",
            raised.exception.data["error_code"],
        )


if __name__ == "__main__":
    unittest.main()
