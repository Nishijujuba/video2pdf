from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from src.video2pdf_workflow_kernel.cli import main as workflow_main
import src.video2pdf_workflow_kernel.delivery_authority as delivery_authority_module
import src.video2pdf_workflow_kernel.delivery_lifecycle as delivery_lifecycle_module
from tests.video_workflow._test_run import new_case_dir
from tests.video_workflow.test_issue13_delivery_lifecycle import (
    _acceptance_report,
    _guard_report,
    _sha256,
    _write_json,
)
from tests.video_workflow.test_issue13_run_initialization import (
    PROJECT_ROOT,
    _run_start_cli_with_recording,
    _write_start_run_project,
)


def _run_cli(
    *arguments: str,
    acceptance_authority: bool = False,
    guard_authority: bool = False,
    guarded_delivery_calls: list[tuple[Path, Path]] | None = None,
) -> tuple[
    subprocess.CompletedProcess[str], dict
]:
    stdout = io.StringIO()

    def guard_eligibility(*, workspace_root: Path) -> dict[str, object]:
        report = json.loads(
            (workspace_root / "acceptance_report.json").read_text(encoding="utf-8")
        )
        return {
            "eligible": True,
            "delivery_authority": True,
            "report_sha256": report["report_sha256"],
        }

    def committed_successor(*, workspace_root: Path) -> dict[str, object]:
        run_path = workspace_root.parents[1] / "workflow" / "run.json"
        run = json.loads(run_path.read_text(encoding="utf-8"))
        return {
            "run_id": run["run_id"],
            "run_revision": run["coordination_revision"],
            "run_record_sha256": _sha256(run_path),
        }

    def guarded_delivery(*, project_root: Path, run_dir: Path) -> dict[str, object]:
        if guarded_delivery_calls is not None:
            guarded_delivery_calls.append((project_root, run_dir))
        guard_path = run_dir / "review" / "acceptance" / "delivery_guard_report.json"
        return {
            "run_id": json.loads(
                (run_dir / "workflow" / "run.json").read_text(encoding="utf-8")
            )["run_id"],
            "delivery_guard_report": {
                "path": str(guard_path.resolve()),
                "sha256": _sha256(guard_path),
            },
        }

    acceptance_eligibility_patch = patch.object(
        delivery_lifecycle_module.AcceptanceV2Provider,
        "guard_eligibility",
        side_effect=guard_eligibility,
    )
    acceptance_successor_patch = patch.object(
        delivery_lifecycle_module.AcceptanceV2Provider,
        "require_committed_delivery_successor",
        side_effect=committed_successor,
    )
    guard_authority_patch = patch.object(
        delivery_authority_module,
        "require_current_kernel_guarded_decision",
        side_effect=guarded_delivery,
    )
    with redirect_stdout(stdout):
        if acceptance_authority:
            with acceptance_eligibility_patch, acceptance_successor_patch:
                returncode = workflow_main(list(arguments))
        elif guard_authority:
            with guard_authority_patch:
                returncode = workflow_main(list(arguments))
        else:
            returncode = workflow_main(list(arguments))
    completed = subprocess.CompletedProcess(
        args=list(arguments),
        returncode=returncode,
        stdout=stdout.getvalue(),
        stderr="",
    )
    return completed, json.loads(completed.stdout)


class Issue95ProfileBackedDeliveryTests(unittest.TestCase):
    def _write_transition_evidence(
        self,
        run_dir: Path,
        *,
        from_stage: str,
        to_stage: str,
        artifacts: dict[str, Path],
    ) -> Path:
        run = json.loads(
            (run_dir / "workflow" / "run.json").read_text(encoding="utf-8")
        )
        video_target = json.loads(
            (
                run_dir / "review" / "acceptance" / "delivery_target.json"
            ).read_text(encoding="utf-8")
        )
        evidence = (
            run_dir
            / "review"
            / "acceptance"
            / f"delivery-transition-{from_stage}-{to_stage}.json"
        )
        _write_json(
            evidence,
            {
                "schema_name": "delivery-transition-evidence",
                "schema_version": "1.0.0",
                "run_id": run["run_id"],
                "from_stage": from_stage,
                "to_stage": to_stage,
                "artifacts": {
                    role: {"path": str(path), "sha256": _sha256(path)}
                    for role, path in artifacts.items()
                },
                "global_gate_authority": video_target["global_gate_authority"],
            },
        )
        return evidence

    def _transition(
        self,
        run_dir: Path,
        *,
        from_stage: str,
        to_stage: str,
        expected_revision: int,
        evidence: Path,
        acceptance_authority: bool = False,
        guard_authority: bool = False,
        guarded_delivery_calls: list[tuple[Path, Path]] | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], dict]:
        return _run_cli(
            "delivery-transition",
            "--run-dir",
            str(run_dir),
            "--from-stage",
            from_stage,
            "--to-stage",
            to_stage,
            "--session-id",
            "session-issue95",
            "--expected-run-revision",
            str(expected_revision),
            "--expected-ownership-generation",
            "1",
            "--evidence",
            str(evidence),
            "--transitioned-at",
            "2026-08-30T00:00:00Z",
            acceptance_authority=acceptance_authority,
            guard_authority=guard_authority,
            guarded_delivery_calls=guarded_delivery_calls,
        )

    def test_profile_backed_youtube_run_reaches_accepted_and_delivered_after_retirement(
        self,
    ) -> None:
        case_root = new_case_dir(self.id(), label="issue95-profile-delivery")
        project_config, workspace_root, credential = _write_start_run_project(
            case_root
        )
        started, start_envelope = _run_start_cli_with_recording(
            PROJECT_ROOT
            / "tests/video_workflow/fixtures/providers/youtube/fresh-download",
            "start-run",
            "--project-config",
            str(project_config),
            "--platform",
            "youtube",
            "--source-url",
            "https://www.youtube.com/watch?v=yt-test-001",
            "--session-id",
            "session-issue95",
            "--credential-ref",
            str(credential),
        )
        self.assertEqual(0, started.returncode, started.stdout + started.stderr)
        run_dir = Path(start_envelope["data"]["run_dir"])
        run_path = run_dir / "workflow" / "run.json"
        run_id = json.loads(run_path.read_text(encoding="utf-8"))["run_id"]

        final_pdf = run_dir / "article.pdf"
        final_pdf.write_bytes(b"%PDF-1.7\nissue95\n")
        main_tex = run_dir / "main.tex"
        main_tex.write_text("\\documentclass{article}\n", encoding="utf-8")
        compile_report = run_dir / "review" / "latex" / "compile_report.json"
        _write_json(compile_report, {"status": "pass"})
        render_manifest = (
            run_dir / "review" / "acceptance" / "rendered-pages.json"
        )
        _write_json(render_manifest, {"status": "pass", "page_count": 1})
        ready_evidence = self._write_transition_evidence(
            run_dir,
            from_stage="generating",
            to_stage="ready_for_delivery",
            artifacts={
                "final_pdf": final_pdf,
                "main_tex": main_tex,
                "final_compile_report": compile_report,
                "render_evidence_manifest": render_manifest,
            },
        )
        ready, ready_envelope = self._transition(
            run_dir,
            from_stage="generating",
            to_stage="ready_for_delivery",
            expected_revision=1,
            evidence=ready_evidence,
        )
        self.assertEqual(0, ready.returncode, ready.stdout + ready.stderr)
        self.assertEqual("ready_for_delivery", ready_envelope["data"]["stage"])

        acceptance_report = (
            run_dir / "review" / "acceptance" / "acceptance_report.json"
        )
        _write_json(acceptance_report, _acceptance_report(run_id, 2, "pass"))
        accepted_evidence = self._write_transition_evidence(
            run_dir,
            from_stage="ready_for_delivery",
            to_stage="accepted",
            artifacts={"acceptance_report": acceptance_report},
        )
        tombstone_path = (
            workspace_root
            / ".workflow-release-history"
            / "cutover-authority-tombstone.json"
        )
        original_tombstone = tombstone_path.read_bytes()
        tombstone_path.write_text("[]\n", encoding="utf-8")
        malformed, malformed_envelope = self._transition(
            run_dir,
            from_stage="ready_for_delivery",
            to_stage="accepted",
            expected_revision=2,
            evidence=accepted_evidence,
            acceptance_authority=True,
        )
        self.assertNotEqual(0, malformed.returncode)
        self.assertEqual(
            {
                "platform": "youtube",
                "authority_boundary": "workflow_release_profile",
                "first_failing_gate": "cutover_authority_tombstone",
                "error_code": "cutover_authority_tombstone_invalid",
            },
            {
                key: malformed_envelope["data"][key]
                for key in (
                    "platform",
                    "authority_boundary",
                    "first_failing_gate",
                    "error_code",
                )
            },
        )
        tombstone_path.write_bytes(original_tombstone)
        profile_path = project_config.parent / "workflow-release-profile.v1.json"
        activation_path = project_config.parent / "workflow-admission-activation.v1.json"
        original_profile = profile_path.read_bytes()
        original_activation = activation_path.read_bytes()
        inactive_profile = json.loads(original_profile)
        inactive_profile["capabilities"]["youtube"] = "inactive"
        _write_json(profile_path, inactive_profile)
        inactive_activation = json.loads(original_activation)
        inactive_activation["profile_sha256"] = _sha256(profile_path)
        _write_json(activation_path, inactive_activation)
        rejected, rejected_envelope = self._transition(
            run_dir,
            from_stage="ready_for_delivery",
            to_stage="accepted",
            expected_revision=2,
            evidence=accepted_evidence,
            acceptance_authority=True,
        )
        self.assertNotEqual(0, rejected.returncode)
        self.assertEqual(
            {
                "platform": "youtube",
                "authority_boundary": "workflow_release_profile",
                "first_failing_gate": "platform_activation",
                "error_code": "workflow_release_capability_inactive",
            },
            {
                key: rejected_envelope["data"][key]
                for key in (
                    "platform",
                    "authority_boundary",
                    "first_failing_gate",
                    "error_code",
                )
            },
        )
        profile_path.write_bytes(original_profile)
        activation_path.write_bytes(original_activation)
        accepted, accepted_envelope = self._transition(
            run_dir,
            from_stage="ready_for_delivery",
            to_stage="accepted",
            expected_revision=2,
            evidence=accepted_evidence,
            acceptance_authority=True,
        )
        self.assertEqual(0, accepted.returncode, accepted.stdout + accepted.stderr)
        self.assertEqual("accepted", accepted_envelope["data"]["stage"])

        guard_report = (
            run_dir / "review" / "acceptance" / "delivery_guard_report.json"
        )
        _write_json(guard_report, _guard_report("pass"))
        delivered_evidence = self._write_transition_evidence(
            run_dir,
            from_stage="accepted",
            to_stage="delivered",
            artifacts={"delivery_guard_report": guard_report},
        )
        stale_guard, stale_guard_envelope = self._transition(
            run_dir,
            from_stage="accepted",
            to_stage="delivered",
            expected_revision=3,
            evidence=delivered_evidence,
        )
        self.assertNotEqual(0, stale_guard.returncode)
        self.assertEqual(
            {
                "platform": "youtube",
                "authority_boundary": "delivery_guard",
            },
            {
                key: stale_guard_envelope["data"][key]
                for key in ("platform", "authority_boundary")
            },
        )
        guarded_delivery_calls: list[tuple[Path, Path]] = []
        delivered, delivered_envelope = self._transition(
            run_dir,
            from_stage="accepted",
            to_stage="delivered",
            expected_revision=3,
            evidence=delivered_evidence,
            guard_authority=True,
            guarded_delivery_calls=guarded_delivery_calls,
        )
        self.assertEqual(0, delivered.returncode, delivered.stdout + delivered.stderr)
        self.assertEqual("delivered", delivered_envelope["data"]["stage"])
        self.assertEqual(
            [(run_dir.resolve().parents[1], run_dir.resolve())],
            guarded_delivery_calls,
        )


if __name__ == "__main__":
    unittest.main()
