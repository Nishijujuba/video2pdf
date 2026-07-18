from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tests.video_workflow.test_source_publication_integration import (  # noqa: E402
    build_decision_ready_authority,
)
from video2pdf_workflow_kernel.errors import ArtifactDrift  # noqa: E402
from video2pdf_workflow_kernel.utils import (  # noqa: E402
    read_json,
    write_json_atomic,
)


class ValidatedSourceGateTests(unittest.TestCase):
    @staticmethod
    def _ready_source():
        kernel, run_dir, _ = build_decision_ready_authority()
        kernel.finalize_production_source(
            run_dir,
            published_at="2026-07-18T12:00:00+08:00",
        )
        return kernel, run_dir

    @staticmethod
    def _first_artifact(run_dir: Path) -> Path:
        manifest = read_json(run_dir / "source/manifest.json")
        relative = PurePosixPath(manifest["artifacts"][0]["path"])
        return run_dir.joinpath(*relative.parts)

    def test_gate_rejects_published_artifact_hash_drift(self) -> None:
        kernel, run_dir = self._ready_source()
        self._first_artifact(run_dir).write_bytes(b"tampered-source-artifact")

        with self.assertRaises(ArtifactDrift):
            kernel.require_current_validated_source_package(run_dir)

    def test_gate_rejects_manifest_drift(self) -> None:
        kernel, run_dir = self._ready_source()
        manifest_path = run_dir / "source/manifest.json"
        manifest = read_json(manifest_path)
        manifest["known_gaps"].append("tampered after publication")
        write_json_atomic(manifest_path, manifest)

        with self.assertRaises(ArtifactDrift):
            kernel.require_current_validated_source_package(run_dir)

    def test_gate_rejects_an_extra_source_file(self) -> None:
        kernel, run_dir = self._ready_source()
        (run_dir / "source/undeclared.bin").write_bytes(b"undeclared")

        with self.assertRaises(ArtifactDrift):
            kernel.require_current_validated_source_package(run_dir)

    def test_gate_rejects_run_record_authority_drift(self) -> None:
        kernel, run_dir = self._ready_source()
        run_path = run_dir / "workflow/run.json"
        record = read_json(run_path)
        record["coordination_revision"] += 1
        write_json_atomic(run_path, record)

        with self.assertRaises(ArtifactDrift):
            kernel.require_current_validated_source_package(run_dir)

    def test_gate_rejects_a_hardlinked_source_artifact(self) -> None:
        kernel, run_dir = self._ready_source()
        artifact = self._first_artifact(run_dir)
        preservation = run_dir / "待删除/gate-hardlink" / artifact.name
        preservation.parent.mkdir(parents=True)
        os.replace(artifact, preservation)
        os.link(preservation, artifact)

        with self.assertRaises(ArtifactDrift):
            kernel.require_current_validated_source_package(run_dir)


if __name__ == "__main__":
    unittest.main()
