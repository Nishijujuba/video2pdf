from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import time
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


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


class _PublicationAuthority:
    def __init__(self, run_sha256: str) -> None:
        self.run_sha256 = run_sha256
        self.row: dict | None = None

    @staticmethod
    def intent_id(record: dict, prior_sha256: str) -> str:
        from video2pdf_workflow_kernel.utils import canonical_json_bytes

        return hashlib.sha256(
            canonical_json_bytes(
                {
                    "operation": "source-publication-v1",
                    "run_id": record["run_id"],
                    "source_epoch": record["source_epoch"],
                    "expected_run_revision": record["coordination_revision"],
                    "old_run_record_sha256": prior_sha256,
                }
            )
        ).hexdigest()

    def prepare(
        self,
        record: dict,
        replacement: dict,
        manifest: dict,
        prior_sha256: str,
        manifest_sha256: str,
    ):
        from video2pdf_workflow_kernel.utils import canonical_json_bytes

        intent_id = self.intent_id(record, prior_sha256)
        replacement_sha = hashlib.sha256(canonical_json_bytes(replacement)).hexdigest()
        expected = {
            "intent_id": intent_id,
            "run_id": record["run_id"],
            "predecessor_committed_sha256": prior_sha256,
            "state": "PREPARED",
            "replacement_run_record_json": canonical_json_bytes(replacement).decode("utf-8"),
            "replacement_run_record_sha256": replacement_sha,
            "publication_journal_sha256": None,
        }
        if self.row is None:
            self.row = expected
        elif self.row != expected:
            raise AssertionError("publication replay changed durable authority")
        return self.row

    def bind_journal(self, intent_id: str, journal_sha256: str) -> None:
        assert self.row is not None and self.row["intent_id"] == intent_id
        prior = self.row["publication_journal_sha256"]
        if prior not in {None, journal_sha256}:
            raise AssertionError("journal binding drifted")
        self.row["publication_journal_sha256"] = journal_sha256

    def transition(self, intent_id: str, expected_state: str, new_state: str) -> None:
        assert self.row is not None and self.row["intent_id"] == intent_id
        if self.row["state"] == new_state:
            return
        if self.row["state"] != expected_state:
            raise AssertionError("publication state transition drifted")
        self.row["state"] = new_state

    def commit(self, intent_id: str) -> None:
        assert self.row is not None and self.row["intent_id"] == intent_id
        self.row["state"] = "COMMITTED"
        self.run_sha256 = self.row["replacement_run_record_sha256"]

    def current_run_record_sha(self, run_id: str) -> str:
        return self.run_sha256

    def active(self, run_id: str):
        if self.row is None or self.row["state"] == "COMMITTED":
            return None
        return self.row


class SourcePublicationTests(unittest.TestCase):
    def _decision_ready_run(self, label: str):
        from video2pdf_workflow_kernel.contracts import ContractRegistry
        from video2pdf_workflow_kernel.utils import (
            canonical_json_bytes,
            sha256_bytes,
            write_json_atomic,
        )

        run_dir = (
            PROJECT_ROOT
            / "待删除"
            / "source-publication-tests"
            / f"{label}-{time.time_ns()}"
        )
        (run_dir / "workflow").mkdir(parents=True)
        (run_dir / "source").mkdir()
        record = json.loads(
            (
                PROJECT_ROOT
                / "tests/video_workflow/fixtures/contracts/run-record.v3.valid.json"
            ).read_text(encoding="utf-8")
        )
        record["output_path"] = str(run_dir.resolve())
        record["source_version"] = None
        record["source_state"] = "decision_ready"
        record["phase"] = "source_acquisition"
        record["coordination_revision"] = 3
        record["last_mutation_intent_id"] = "f" * 64
        record["artifact_generations"].pop("source_manifest")
        record["checkpoints"].pop("source_ready")
        raw_by_logical_id = {
            "bootstrap_record": canonical_json_bytes({"kind": "bootstrap"}),
            "source_candidate_inventory": canonical_json_bytes({"kind": "inventory"}),
            "source_acquisition_decision_skeleton": canonical_json_bytes({"kind": "skeleton"}),
            "source_acquisition_decision": canonical_json_bytes({"kind": "decision"}),
        }
        for logical_id, generation in record["artifact_generations"].items():
            raw = raw_by_logical_id[logical_id]
            path = run_dir.joinpath(*PurePosixPath(generation["path"]).parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            generation["sha256"] = sha256_bytes(raw)
        initialized = record["checkpoints"]["run_initialized"]
        initialized["artifact_bindings"][0]["sha256"] = record[
            "artifact_generations"
        ]["bootstrap_record"]["sha256"]
        initialized["evidence_sha256"] = initialized["artifact_bindings"][0][
            "sha256"
        ]
        candidates = record["checkpoints"]["source_candidates_ready"]
        candidates["artifact_bindings"][0]["sha256"] = record[
            "artifact_generations"
        ]["source_candidate_inventory"]["sha256"]
        candidates["prerequisite_bindings"][0]["evidence_sha256"] = initialized[
            "evidence_sha256"
        ]
        candidates["evidence_sha256"] = candidates["artifact_bindings"][0][
            "sha256"
        ]
        decision = record["checkpoints"]["source_acquisition_decision_ready"]
        for binding in decision["artifact_bindings"]:
            binding["sha256"] = record["artifact_generations"][
                binding["logical_id"]
            ]["sha256"]
        decision["prerequisite_bindings"][0]["evidence_sha256"] = candidates[
            "evidence_sha256"
        ]
        decision["evidence_sha256"] = record["artifact_generations"][
            "source_acquisition_decision"
        ]["sha256"]
        contracts = ContractRegistry(PROJECT_ROOT)
        contracts.validate_run_record(record)
        write_json_atomic(run_dir / "workflow/run.json", record)
        return run_dir, record, contracts

    def _package(self, run_dir: Path, record: dict, intent_id: str, contracts):
        from video2pdf_workflow_kernel.source_package import MaterializedSourcePackage
        from video2pdf_workflow_kernel.utils import (
            canonical_json_bytes,
            sha256_bytes,
        )

        manifest = json.loads(
            (
                PROJECT_ROOT
                / "tests/video_workflow/fixtures/contracts/source-manifest.v2.valid.json"
            ).read_text(encoding="utf-8")
        )
        manifest["run_id"] = record["run_id"]
        manifest["source_epoch"] = record["source_epoch"]
        manifest["source_identity"] = record["source_identity"]
        manifest["source_version_basis"]["source_identity"] = record["source_identity"]
        manifest["provenance"]["candidate_inventory_sha256"] = record[
            "artifact_generations"
        ]["source_candidate_inventory"]["sha256"]
        manifest["provenance"]["decision_skeleton_sha256"] = record[
            "artifact_generations"
        ]["source_acquisition_decision_skeleton"]["sha256"]
        manifest["provenance"]["judgment_patch"]["sha256"] = record[
            "artifact_generations"
        ]["source_acquisition_decision"]["sha256"]
        raw_by_id = {
            "metadata": b'{"title":"recorded"}',
            "cover": b"recorded-cover",
            "video": b"recorded-video-with-audio",
            "subtitle_en": b"1\n00:00:00,000 --> 00:00:01,000\nHello\n",
        }
        by_id = {item["logical_id"]: item for item in manifest["artifacts"]}
        for logical_id, raw in raw_by_id.items():
            by_id[logical_id]["sha256"] = sha256_bytes(raw)
            by_id[logical_id]["size_bytes"] = len(raw)
        manifest["source_version_basis"]["artifacts"] = sorted(
            [
                {
                    key: item[key]
                    for key in (
                        "logical_id",
                        "role",
                        "media_type",
                        "sha256",
                        "size_bytes",
                        "language",
                        "subtitle_kind",
                        "technical_probe",
                    )
                }
                for item in manifest["artifacts"]
            ],
            key=lambda item: item["logical_id"],
        )
        manifest["source_version"] = sha256_bytes(
            canonical_json_bytes(manifest["source_version_basis"])
        )
        candidate_root = (
            run_dir
            / "work/source-acquisition/publications"
            / intent_id
            / "candidate/source"
        )
        artifact_paths = []
        for artifact in manifest["artifacts"]:
            relative = PurePosixPath(artifact["path"]).relative_to("source")
            path = candidate_root.joinpath(*relative.parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw_by_id[artifact["logical_id"]])
            artifact_paths.append(path)
        contracts.validate("source-manifest", manifest)
        manifest_bytes = canonical_json_bytes(manifest)
        manifest_path = candidate_root / "manifest.json"
        manifest_path.write_bytes(manifest_bytes)
        return MaterializedSourcePackage(
            manifest=manifest,
            manifest_bytes=manifest_bytes,
            manifest_sha256=sha256_bytes(manifest_bytes),
            manifest_path=manifest_path,
            artifact_paths=tuple(artifact_paths),
        )

    def test_publication_recovers_every_fault_and_commits_current_source(self) -> None:
        from video2pdf_workflow_kernel.source_publication import (
            SOURCE_PUBLICATION_FAULT_POINTS,
            SourcePublicationSaga,
        )
        from video2pdf_workflow_kernel.utils import read_json, sha256_file

        for fault_point in sorted(SOURCE_PUBLICATION_FAULT_POINTS):
            with self.subTest(fault_point=fault_point):
                run_dir, record, contracts = self._decision_ready_run(fault_point)
                prior_sha = sha256_file(run_dir / "workflow/run.json")
                authority = _PublicationAuthority(prior_sha)
                intent_id = authority.intent_id(record, prior_sha)
                package = self._package(run_dir, record, intent_id, contracts)
                saga = SourcePublicationSaga(
                    run_dir,
                    contracts=contracts,
                    authority=authority,
                )
                with self.assertRaisesRegex(Exception, fault_point):
                    saga.publish(package, fault_point=fault_point)

                result = SourcePublicationSaga(
                    run_dir,
                    contracts=contracts,
                    authority=authority,
                ).reconcile()
                current = read_json(run_dir / "workflow/run.json")
                self.assertEqual(current["source_state"], "ready")
                self.assertEqual(current["source_version"], package.manifest["source_version"])
                self.assertEqual(current["phase"], "source_ready")
                self.assertEqual(current["checkpoints"]["source_ready"]["status"], "current")
                self.assertEqual(
                    sha256_file(run_dir / "source/manifest.json"),
                    package.manifest_sha256,
                )
                self.assertEqual(result.intent_id, intent_id)
                self.assertEqual(authority.row["state"], "COMMITTED")

    def test_publication_rejects_canonical_parent_junction_before_external_write(
        self,
    ) -> None:
        from video2pdf_workflow_kernel.source_publication import SourcePublicationSaga
        from video2pdf_workflow_kernel.utils import sha256_file

        run_dir, record, contracts = self._decision_ready_run(
            "canonical-parent-junction"
        )
        prior_sha = sha256_file(run_dir / "workflow/run.json")
        authority = _PublicationAuthority(prior_sha)
        intent_id = authority.intent_id(record, prior_sha)
        package = self._package(run_dir, record, intent_id, contracts)
        outside = (
            PROJECT_ROOT
            / "workspace/待删除/kernel-test-runs/source-publication-outside"
            / f"{time.time_ns()}"
        )
        create_directory_link(run_dir / "source/media", outside)

        with self.assertRaisesRegex(Exception, "link|reparse|boundary"):
            SourcePublicationSaga(
                run_dir,
                contracts=contracts,
                authority=authority,
            ).publish(package)

        self.assertFalse((outside / "video.mp4").exists())

    def test_publication_rejects_preservation_parent_junction_before_move(
        self,
    ) -> None:
        from video2pdf_workflow_kernel.source_publication import SourcePublicationSaga
        from video2pdf_workflow_kernel.utils import sha256_file

        run_dir, record, contracts = self._decision_ready_run(
            "preservation-parent-junction"
        )
        prior_sha = sha256_file(run_dir / "workflow/run.json")
        authority = _PublicationAuthority(prior_sha)
        intent_id = authority.intent_id(record, prior_sha)
        package = self._package(run_dir, record, intent_id, contracts)
        canonical = run_dir / "source/media/video.mp4"
        canonical.parent.mkdir(parents=True)
        canonical.write_bytes(b"prior-video")
        outside = (
            PROJECT_ROOT
            / "workspace/待删除/kernel-test-runs/source-preservation-outside"
            / f"{time.time_ns()}"
        )
        preservation_parent = (
            run_dir
            / "待删除/source-publications"
            / intent_id
            / "previous/source/media"
        )
        preservation_parent.parent.mkdir(parents=True)
        create_directory_link(preservation_parent, outside)

        with self.assertRaisesRegex(Exception, "link|reparse|boundary"):
            SourcePublicationSaga(
                run_dir,
                contracts=contracts,
                authority=authority,
            ).publish(package)

        self.assertEqual(canonical.read_bytes(), b"prior-video")
        self.assertFalse((outside / "video.mp4").exists())

    def test_publication_rejects_write_staging_parent_junction_before_write(
        self,
    ) -> None:
        from video2pdf_workflow_kernel.source_publication import SourcePublicationSaga
        from video2pdf_workflow_kernel.utils import sha256_file

        run_dir, record, contracts = self._decision_ready_run(
            "write-staging-parent-junction"
        )
        prior_sha = sha256_file(run_dir / "workflow/run.json")
        authority = _PublicationAuthority(prior_sha)
        intent_id = authority.intent_id(record, prior_sha)
        package = self._package(run_dir, record, intent_id, contracts)
        outside = (
            PROJECT_ROOT
            / "workspace/待删除/kernel-test-runs/source-write-staging-outside"
            / f"{time.time_ns()}"
        )
        staging_parent = (
            run_dir
            / "待删除/source-publications"
            / intent_id
            / "writes/media"
        )
        staging_parent.parent.mkdir(parents=True)
        create_directory_link(staging_parent, outside)

        with self.assertRaisesRegex(Exception, "link|reparse"):
            SourcePublicationSaga(
                run_dir,
                contracts=contracts,
                authority=authority,
            ).publish(package)

        self.assertFalse((outside / "video.mp4").exists())

    def test_publication_rejects_hard_linked_canonical_file(self) -> None:
        from video2pdf_workflow_kernel.source_publication import SourcePublicationSaga
        from video2pdf_workflow_kernel.utils import sha256_file

        run_dir, record, contracts = self._decision_ready_run(
            "hard-linked-canonical"
        )
        prior_sha = sha256_file(run_dir / "workflow/run.json")
        authority = _PublicationAuthority(prior_sha)
        intent_id = authority.intent_id(record, prior_sha)
        package = self._package(run_dir, record, intent_id, contracts)
        outside = (
            PROJECT_ROOT
            / "workspace/待删除/kernel-test-runs/source-hard-link-outside"
            / f"{time.time_ns()}.mp4"
        )
        outside.parent.mkdir(parents=True, exist_ok=True)
        outside.write_bytes(b"prior-video")
        canonical = run_dir / "source/media/video.mp4"
        canonical.parent.mkdir(parents=True)
        os.link(outside, canonical)

        with self.assertRaisesRegex(Exception, "independent regular file"):
            SourcePublicationSaga(
                run_dir,
                contracts=contracts,
                authority=authority,
            ).publish(package)

        self.assertEqual(outside.read_bytes(), b"prior-video")
        self.assertTrue(canonical.exists())


if __name__ == "__main__":
    unittest.main()
