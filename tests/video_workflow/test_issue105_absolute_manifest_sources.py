from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from tests.video_workflow import test_issue105_content_repair_handoff as issue105_fixture
from video2pdf_workflow_kernel import cli
from video2pdf_workflow_kernel.utils import canonical_json_bytes


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Issue105AbsoluteManifestSourceTests(unittest.TestCase):
    def test_public_seal_handoff_absolutizes_production_relative_sources(self) -> None:
        fixture = issue105_fixture.Issue105ContentRepairHandoffTests(
            "test_seal_fault_replay_supersedes_once_and_replays_current_manifest"
        )
        run, workspace, journal, generations = fixture._supersession_fixture()

        # Content Production records Run-relative source paths in its diagnostic
        # Compile Manifest. Materialize those exact producer identities so this
        # positive graph differs from Production only at the Seal handoff boundary.
        compile_manifest_path = run / "workflow/compile-manifest.json"
        compile_manifest = json.loads(compile_manifest_path.read_text(encoding="utf-8"))
        artifacts_by_id = {
            artifact["logical_id"]: artifact for artifact in generations["artifacts"]
        }
        for entry in compile_manifest["entries"]:
            self.assertFalse(Path(entry["source_path"]).is_absolute())
            source = run / entry["source_path"]
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(f"Production source: {entry['logical_id']}\n".encode())
            entry["sha256"] = _sha256(source)
            artifacts_by_id[entry["logical_id"]]["sha256"] = entry["sha256"]
        compile_manifest_path.write_bytes(canonical_json_bytes(compile_manifest))

        generations["generation_set_sha256"] = issue105_fixture.fingerprint(
            generations, "generation_set_sha256"
        )
        generation_path = workspace / "artifact-generations.json"
        generation_path.write_bytes(canonical_json_bytes(generations))
        handoff = journal["content_repair_handoff"]
        handoff["promotion"]["generation_set_sha256"] = generations[
            "generation_set_sha256"
        ]
        handoff["promotion"]["generation_set_file_sha256"] = _sha256(generation_path)
        handoff["handoff_sha256"] = issue105_fixture.fingerprint(
            handoff, "handoff_sha256"
        )
        journal["journal_sha256"] = issue105_fixture.fingerprint(
            journal, "journal_sha256"
        )
        issue105_fixture.write_json(
            run / "workflow/runtime-refresh-active.json", journal
        )

        policy_path = run / "workflow/compile-runtime-policy.json"
        authority = {
            "runtime_policy_path": str(policy_path.resolve()),
            "runtime_policy_sha256": _sha256(policy_path),
        }
        precompile = {
            "classification": "precompile_seal_reused",
            "seal_sha256": "7" * 64,
            "artifact_generations": generations["artifacts"],
        }
        seal_result = {
            "seal_id": "8" * 32,
            "seal_sha256": precompile["seal_sha256"],
            "decision_origin": "fresh_evaluation",
            "seal_path": str((workspace / "precompile-text-seal.json").resolve()),
            "activation_status": "target_only",
        }

        stdout = io.StringIO()
        with (
            patch(
                "video2pdf_workflow_kernel.cli.PrecompileQualityProvider.seal",
                return_value=seal_result,
            ),
            patch(
                "video2pdf_workflow_kernel.runtime_refresh.PrecompileQualityProvider.assess_current_seal",
                return_value=precompile,
            ),
            patch(
                "video2pdf_workflow_kernel.content_production.ContentProduction.require_current_diagnostic_compile_authority",
                return_value=authority,
            ),
            redirect_stdout(stdout),
        ):
            exit_code = cli.main(
                [
                    "delivery-quality-seal",
                    "--workspace-root",
                    str(workspace),
                    "--sealed-at",
                    "2026-09-06T00:00:00Z",
                ]
            )

        self.assertEqual(0, exit_code, stdout.getvalue())
        envelope = json.loads(stdout.getvalue())
        manifest_path = Path(
            envelope["data"]["runtime_refresh_handoff"][
                "successor_final_compile_manifest_path"
            ]
        )
        final_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for entry in final_manifest["entries"]:
            source = Path(entry["source_path"])
            self.assertTrue(source.is_absolute())
            self.assertTrue(source.is_relative_to(run))
            self.assertTrue(source.is_file())
            self.assertEqual(entry["sha256"], _sha256(source))


if __name__ == "__main__":
    unittest.main()
