from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import unittest
import uuid


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

CLI = PROJECT_ROOT / "scripts/video_workflow.py"
TEST_ROOT = PROJECT_ROOT / "workspace/待删除/vi"


def create_directory_link(link: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except OSError:
        if os.name != "nt":
            raise unittest.SkipTest("directory symlinks are unavailable")
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise unittest.SkipTest("directory junctions are unavailable")


class VerifiedSourceImportCliTests(unittest.TestCase):
    def _current_prior_package(self):
        from tests.video_workflow.test_source_publication_integration import (
            build_decision_ready_authority,
        )

        kernel, prior_run_dir, _ = build_decision_ready_authority()
        published = kernel.finalize_production_source(
            prior_run_dir,
            published_at="2026-07-18T12:00:00+08:00",
        )
        return kernel, prior_run_dir, published

    def _run_import(self, prior_run_dir: Path):
        workspace = TEST_ROOT / uuid.uuid4().hex / "workspace"
        completed = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                "-B",
                str(CLI),
                "source-import",
                "--workspace-root",
                str(workspace),
                "--prior-run-dir",
                str(prior_run_dir),
                "--task-start",
                "2026-07-18T14:00:00+08:00",
                "--request-id",
                f"verified-import-{uuid.uuid4().hex}",
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        payload = json.loads(completed.stdout)
        return completed, payload, workspace

    def test_public_source_import_creates_current_v2_package_without_semantic_task(self) -> None:
        from video2pdf_workflow_kernel.contracts import ContractRegistry
        from video2pdf_workflow_kernel.source_acquisition import derive_source_identity
        from video2pdf_workflow_kernel.utils import read_json, sha256_file

        _, prior_run_dir, prior_publication = self._current_prior_package()
        prior_record = read_json(prior_run_dir / "workflow/run.json")
        prior_manifest = read_json(prior_run_dir / "source/manifest.json")

        completed, payload, _ = self._run_import(prior_run_dir)

        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["classification"], "verified_source_imported")
        run_dir = Path(payload["data"]["run_dir"])
        record = read_json(run_dir / "workflow/run.json")
        manifest = read_json(run_dir / "source/manifest.json")
        inventory = read_json(
            run_dir / "work/source-acquisition/candidate-inventory.json"
        )

        contracts = ContractRegistry(PROJECT_ROOT)
        contracts.validate_run_record(record)
        contracts.validate("source-candidate-inventory", inventory)
        contracts.validate("source-manifest", manifest)
        self.assertEqual(record["schema_version"], "3.0.0")
        self.assertEqual(record["requested_source_acquisition_mode"], "verified_import")
        self.assertEqual(record["source_acquisition_mode"], "verified_import")
        self.assertEqual(record["source_state"], "ready")
        self.assertEqual(record["checkpoints"]["source_ready"]["status"], "current")
        self.assertEqual(
            record["checkpoint_dependencies"]["source_ready"],
            ["source_candidates_ready"],
        )
        self.assertNotIn("source_acquisition_decision_ready", record["checkpoints"])
        self.assertFalse(
            (run_dir / "work/source-acquisition/decision.skeleton.json").exists()
        )
        self.assertEqual(inventory["mode"], "verified_import")
        self.assertEqual(inventory["provider"]["kind"], "verified_import")
        self.assertEqual(inventory["authentication_classification"], "not_applicable")
        self.assertEqual(manifest["mode"], "verified_import")
        self.assertEqual(manifest["provenance"]["prior_run_id"], prior_record["run_id"])
        self.assertEqual(
            manifest["provenance"]["prior_source_manifest_sha256"],
            prior_publication.manifest_sha256,
        )
        self.assertNotEqual(record["run_id"], prior_record["run_id"])
        self.assertEqual(
            manifest["source_identity"],
            derive_source_identity(
                prior_manifest["canonical_platform"],
                prior_manifest["canonical_item_id"],
            ),
        )
        self.assertEqual(manifest["source_identity"], prior_manifest["source_identity"])
        self.assertEqual(manifest["source_version"], prior_manifest["source_version"])
        self.assertEqual(record["source_identity"], manifest["source_identity"])
        self.assertEqual(record["source_version"], manifest["source_version"])
        self.assertEqual(
            sha256_file(run_dir / "source/manifest.json"),
            record["artifact_generations"]["source_manifest"]["sha256"],
        )
        for artifact in manifest["artifacts"]:
            imported_path = run_dir.joinpath(*PurePosixPath(artifact["path"]).parts)
            prior_artifact = next(
                item
                for item in prior_manifest["artifacts"]
                if item["logical_id"] == artifact["logical_id"]
            )
            prior_path = prior_run_dir.joinpath(
                *PurePosixPath(prior_artifact["path"]).parts
            )
            self.assertNotEqual(imported_path.resolve(), prior_path.resolve())
            self.assertEqual(imported_path.read_bytes(), prior_path.read_bytes())

    def test_public_source_import_rejects_a_noncurrent_prior_package(self) -> None:
        from video2pdf_workflow_kernel.utils import read_json

        _, prior_run_dir, _ = self._current_prior_package()
        prior_manifest = read_json(prior_run_dir / "source/manifest.json")
        video = next(
            item for item in prior_manifest["artifacts"] if item["role"] == "video"
        )
        video_path = prior_run_dir.joinpath(*PurePosixPath(video["path"]).parts)
        video_path.write_bytes(video_path.read_bytes() + b"drift")

        completed, payload, workspace = self._run_import(prior_run_dir)

        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["classification"], "artifact_drift")
        self.assertFalse(any(workspace.glob("*/source/manifest.json")))

    def test_public_source_import_rejects_a_linked_prior_source_descendant(self) -> None:
        _, prior_run_dir, _ = self._current_prior_package()
        outside = TEST_ROOT / "outside" / uuid.uuid4().hex
        create_directory_link(prior_run_dir / "source/linked", outside)

        completed, payload, workspace = self._run_import(prior_run_dir)

        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["classification"], "artifact_drift")
        self.assertFalse(any(workspace.glob("*/source/manifest.json")))


if __name__ == "__main__":
    unittest.main()
