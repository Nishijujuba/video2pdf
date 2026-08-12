from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[2]

from tests.video_workflow._test_run import new_case_dir
from tests.video_workflow.test_issue13_delivery_lifecycle import (
    _acceptance_report,
    _guard_report,
)
from video2pdf_workflow_kernel.errors import ContractError
from video2pdf_workflow_kernel.guarded_delivery import (
    require_current_kernel_guarded_decision,
    validate_delivery_guard_report,
)


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CurrentKernelGuardedDecisionTests(unittest.TestCase):
    def _authority_graph(self) -> tuple[Path, Path, Path, Path]:
        return self._authority_graph_at_revision(report_revision=2, run_revision=2)

    def _authority_graph_at_revision(
        self, *, report_revision: int, run_revision: int
    ) -> tuple[Path, Path, Path, Path]:
        project_root = new_case_dir(
            self.id(), label="current-kernel-guarded-decision"
        )
        run_dir = project_root / "workspace" / "candidate-run"
        review_root = run_dir / "review" / "acceptance"
        run_id = "7" * 32
        session_id = "candidate-session"
        acceptance_path = review_root / "acceptance_report.json"
        guard_path = review_root / "delivery_guard_report.json"
        _write_json(
            acceptance_path,
            _acceptance_report(run_id, report_revision, "pass"),
        )
        _write_json(guard_path, _guard_report("pass"))

        video_path = review_root / "delivery_target.json"
        video = {
            "schema_name": "kernel-delivery-target",
            "schema_version": "1.0.0",
            "projection_kind": "video_target",
            "projection_revision": run_revision,
            "run_id": run_id,
            "run_revision": run_revision,
            "lifecycle_intent_id": "8" * 64,
            "video_output_dir": str(run_dir.resolve()),
            "stage": "accepted",
            "ownership": {"session_id": session_id, "generation": 1},
            "artifacts": {
                "final_pdf": None,
                "main_tex": None,
                "final_compile_report": None,
                "acceptance_report": {
                    "path": str(acceptance_path.resolve()),
                    "sha256": _sha256(acceptance_path),
                },
                "delivery_guard_report": None,
            },
            "global_gate_authority": {
                "path": str((project_root / "global-gate.json").resolve()),
                "generation": 1,
                "sha256": "9" * 64,
            },
        }
        _write_json(video_path, video)

        session_path = (
            project_root
            / ".codex"
            / "delivery-targets"
            / "sessions"
            / session_id
            / "current.json"
        )
        session = {
            "schema_name": "kernel-session-delivery-target",
            "schema_version": "1.0.0",
            "projection_kind": "session_target",
            "projection_revision": run_revision,
            "projection_path": str(session_path.resolve()),
            "session_id": session_id,
            "run_id": run_id,
            "run_revision": run_revision,
            "lifecycle_intent_id": "8" * 64,
            "stage": "accepted",
            "ownership_generation": 1,
            "owner_status": "active",
            "video_output_dir": str(run_dir.resolve()),
            "video_target": {
                "path": str(video_path.resolve()),
                "projection_revision": run_revision,
                "sha256": _sha256(video_path),
            },
        }
        _write_json(session_path, session)
        run = {
            "schema_name": "run-record",
            "schema_version": "4.0.0",
            "platform_adapter": "bilibili",
            "canonical_platform": "bilibili",
            "run_id": run_id,
            "coordination_revision": run_revision,
            "last_mutation_intent_id": "8" * 64,
            "output_path": str(run_dir.resolve()),
            "delivery": {
                "stage": "accepted",
                "ownership": {"session_id": session_id, "generation": 1},
                "projections": {
                    "video_target": {
                        "path": str(video_path.resolve()),
                        "projection_revision": run_revision,
                        "sha256": _sha256(video_path),
                    },
                    "session_target": {
                        "path": str(session_path.resolve()),
                        "projection_revision": run_revision,
                        "sha256": _sha256(session_path),
                    },
                },
            },
        }
        _write_json(run_dir / "workflow" / "run.json", run)
        return project_root, run_dir, acceptance_path, guard_path

    def test_self_declared_reports_without_provider_authority_fail_closed(self) -> None:
        project_root, run_dir, _acceptance, _guard = self._authority_graph()
        with patch(
            "video2pdf_workflow_kernel.guarded_delivery._load_active_delivery_guard"
        ) as loader:
            with self.assertRaises(ContractError):
                require_current_kernel_guarded_decision(
                    project_root=project_root,
                    run_dir=run_dir,
                )
        loader.assert_not_called()

    def test_provider_current_and_active_guard_fresh_return_exact_bindings(self) -> None:
        project_root, run_dir, acceptance_path, guard_path = self._authority_graph()
        session_path = next(
            (project_root / ".codex" / "delivery-targets" / "sessions").glob(
                "*/current.json"
            )
        )
        video_path = run_dir / "review" / "acceptance" / "delivery_target.json"
        target = SimpleNamespace(
            video_output_dir=run_dir.resolve(),
            current_target_path=session_path.resolve(),
            target_file=video_path.resolve(),
            acceptance_report_path=acceptance_path.resolve(),
            guard_report_path=guard_path.resolve(),
            stage="accepted",
        )
        active_guard = SimpleNamespace(
            resolve_delivery_target=lambda **_kwargs: target,
            guard_report_is_fresh=lambda value: value is target,
        )
        report = json.loads(acceptance_path.read_text(encoding="utf-8"))
        eligibility = Mock(
            return_value={
                "eligible": True,
                "delivery_authority": True,
                "report_sha256": report["report_sha256"],
            }
        )
        with (
            patch(
                "video2pdf_workflow_kernel.guarded_delivery.AcceptanceV2Provider",
                return_value=SimpleNamespace(guard_eligibility=eligibility),
            ),
            patch(
                "video2pdf_workflow_kernel.guarded_delivery.validate_acceptance_report",
                return_value=report,
            ),
            patch(
                "video2pdf_workflow_kernel.guarded_delivery._load_active_delivery_guard",
                return_value=active_guard,
            ),
        ):
            result = require_current_kernel_guarded_decision(
                project_root=project_root,
                run_dir=run_dir,
            )

        eligibility.assert_called_once_with(workspace_root=acceptance_path.parent)
        self.assertEqual(
            {"path": str(acceptance_path.resolve()), "sha256": _sha256(acceptance_path)},
            result["acceptance_report"],
        )
        self.assertEqual(
            {"path": str(guard_path.resolve()), "sha256": _sha256(guard_path)},
            result["delivery_guard_report"],
        )
        self.assertEqual(str(session_path.resolve()), result["session_target"]["path"])
        self.assertEqual(str(video_path.resolve()), result["video_target"]["path"])

    def test_guard_validator_accepts_an_explicit_ready_stage(self) -> None:
        case = new_case_dir(self.id(), label="guard-ready-stage")
        report = _guard_report("pass")
        report["stage"] = "ready_for_delivery"
        report_path = case / "delivery_guard_report.json"
        _write_json(report_path, report)

        validated = validate_delivery_guard_report(
            report_path=report_path,
            expected_stage="ready_for_delivery",
        )

        self.assertEqual("ready_for_delivery", validated["stage"])

    def test_accepted_run_validates_report_against_its_own_binding_revision(
        self,
    ) -> None:
        # Fixture graph: the Acceptance Report v2 is bound to the ready
        # predecessor (rev6) while the accepted Run has advanced to rev8
        # through committed Delivery Lifecycle successors.  The delivered
        # authority must validate the report against its own binding revision,
        # not the current Run revision.
        project_root, run_dir, acceptance_path, guard_path = (
            self._authority_graph_at_revision(report_revision=6, run_revision=8)
        )
        session_path = next(
            (project_root / ".codex" / "delivery-targets" / "sessions").glob(
                "*/current.json"
            )
        )
        video_path = run_dir / "review" / "acceptance" / "delivery_target.json"
        target = SimpleNamespace(
            video_output_dir=run_dir.resolve(),
            current_target_path=session_path.resolve(),
            target_file=video_path.resolve(),
            acceptance_report_path=acceptance_path.resolve(),
            guard_report_path=guard_path.resolve(),
            stage="accepted",
        )
        active_guard = SimpleNamespace(
            resolve_delivery_target=lambda **_kwargs: target,
            guard_report_is_fresh=lambda value: value is target,
        )
        report = json.loads(acceptance_path.read_text(encoding="utf-8"))
        eligibility = Mock(
            return_value={
                "eligible": True,
                "delivery_authority": True,
                "report_sha256": report["report_sha256"],
            }
        )
        validator = Mock(return_value=report)
        with (
            patch(
                "video2pdf_workflow_kernel.guarded_delivery.AcceptanceV2Provider",
                return_value=SimpleNamespace(guard_eligibility=eligibility),
            ),
            patch(
                "video2pdf_workflow_kernel.guarded_delivery.validate_acceptance_report",
                validator,
            ),
            patch(
                "video2pdf_workflow_kernel.guarded_delivery._load_active_delivery_guard",
                return_value=active_guard,
            ),
        ):
            result = require_current_kernel_guarded_decision(
                project_root=project_root,
                run_dir=run_dir,
            )

        self.assertEqual(
            {"path": str(acceptance_path.resolve()), "sha256": _sha256(acceptance_path)},
            result["acceptance_report"],
        )
        self.assertEqual("accepted", result["stage"])
        validator.assert_called_once_with(
            project_root=project_root,
            report_path=acceptance_path,
            run_id=report["run_binding"]["run_id"],
            coordination_revision=6,
        )


if __name__ == "__main__":
    unittest.main()
