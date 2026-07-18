from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest
import uuid


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tests.video_workflow.test_source_package import (  # noqa: E402
    build_inventory,
    persist_fresh_controls,
)
from video2pdf_workflow_kernel.contracts import ContractRegistry  # noqa: E402
from video2pdf_workflow_kernel.control_store import ControlStore  # noqa: E402
from video2pdf_workflow_kernel.errors import (  # noqa: E402
    ArtifactDrift,
    KernelConflict,
)
from video2pdf_workflow_kernel.kernel import VideoWorkflowKernel  # noqa: E402
from video2pdf_workflow_kernel.source_publication import (  # noqa: E402
    SourcePublicationFault,
)
from video2pdf_workflow_kernel.utils import (  # noqa: E402
    canonical_json_bytes,
    read_json,
    sha256_bytes,
    sha256_file,
)


TEST_ROOT = (
    PROJECT_ROOT
    / "workspace/待删除/kernel-test-runs/source-publication-integration"
)


class _SourcePublicationFixtureBuilder:
    def build_decision_ready_authority(
        self,
    ) -> tuple[VideoWorkflowKernel, Path, dict]:
        root = TEST_ROOT / uuid.uuid4().hex
        workspace = root / "workspace"
        run_dir = workspace / "production-source-run"
        (run_dir / "workflow").mkdir(parents=True, exist_ok=False)
        contracts = ContractRegistry(PROJECT_ROOT)

        run_id = uuid.uuid4().hex
        request_id = f"source-publication-integration-{run_id}"
        intent_id = f"initialize-{run_id}"
        inventory = build_inventory(run_dir)
        inventory["run_id"] = run_id
        inventory["acquisition_id"] = uuid.uuid4().hex
        inventory_path, skeleton_path, patch_path = persist_fresh_controls(
            run_dir, inventory
        )

        bootstrap = json.loads(
            (
                PROJECT_ROOT
                / "tests/video_workflow/fixtures/contracts/bootstrap-record.v2.valid.json"
            ).read_text(encoding="utf-8")
        )
        bootstrap.update(
            {
                "run_id": run_id,
                "request_id": request_id,
                "adapter": {
                    "id": "youtube",
                    "contract_version": "1.0.0",
                    "canonical_platform": "youtube",
                },
                "source_request": {
                    "kind": "fresh_download",
                    "canonical_locator": (
                        "https://www.youtube.com/watch?v=yt-test-001"
                    ),
                },
                "canonical_platform": "youtube",
                "canonical_item_id": inventory["canonical_item_id"],
                "source_identity": inventory["source_identity"],
                "original_title": inventory["source_metadata"]["original_title"],
                "probe_execution": {
                    "provider_kind": "recorded_fixture",
                    "command_argv_redacted": [
                        "python",
                        "-m",
                        "yt_dlp",
                        "--cookies",
                        "<localized-cookie-file>",
                        "https://www.youtube.com/watch?v=yt-test-001",
                    ],
                    "authentication_classification": "cookie_accepted",
                    "normalized_result_sha256": "1" * 64,
                    "resource_admission": None,
                },
            }
        )
        contracts.validate("bootstrap-record", bootstrap)
        bootstrap_path = run_dir / "待删除/bootstrap/probe.json"
        bootstrap_path.parent.mkdir(parents=True, exist_ok=False)
        bootstrap_path.write_bytes(canonical_json_bytes(bootstrap))

        record = json.loads(
            (
                PROJECT_ROOT
                / "tests/video_workflow/fixtures/contracts/run-record.v3.valid.json"
            ).read_text(encoding="utf-8")
        )
        record.update(
            {
                "run_id": run_id,
                "request_id": request_id,
                "platform_adapter": "youtube",
                "canonical_platform": "youtube",
                "canonical_item_id": inventory["canonical_item_id"],
                "source_identity": inventory["source_identity"],
                "source_version": None,
                "original_title": inventory["source_metadata"]["original_title"],
                "normalized_title": inventory["source_metadata"]["original_title"],
                "output_path": str(run_dir.resolve()),
                "source_state": "decision_ready",
                "phase": "source_acquisition",
                "initialization_intent_id": intent_id,
                "coordination_revision": 1,
                "last_mutation_intent_id": None,
            }
        )
        record["artifact_generations"].pop("source_manifest")
        record["checkpoints"].pop("source_ready")
        artifact_paths = {
            "bootstrap_record": "待删除/bootstrap/probe.json",
            "source_candidate_inventory": inventory_path,
            "source_acquisition_decision_skeleton": skeleton_path,
            "source_acquisition_decision": patch_path,
        }
        for logical_id, relative in artifact_paths.items():
            generation = record["artifact_generations"][logical_id]
            generation["sha256"] = sha256_file(run_dir / relative)
            generation["source_epoch"] = (
                0 if logical_id == "bootstrap_record" else 1
            )
        checkpoints = record["checkpoints"]
        for checkpoint in checkpoints.values():
            for binding in checkpoint["artifact_bindings"]:
                binding["sha256"] = record["artifact_generations"][
                    binding["logical_id"]
                ]["sha256"]
        checkpoints["run_initialized"]["evidence_sha256"] = record[
            "artifact_generations"
        ]["bootstrap_record"]["sha256"]
        checkpoints["source_candidates_ready"]["prerequisite_bindings"][0][
            "evidence_sha256"
        ] = checkpoints["run_initialized"]["evidence_sha256"]
        checkpoints["source_candidates_ready"]["evidence_sha256"] = record[
            "artifact_generations"
        ]["source_candidate_inventory"]["sha256"]
        checkpoints["source_acquisition_decision_ready"][
            "prerequisite_bindings"
        ][0]["evidence_sha256"] = checkpoints["source_candidates_ready"][
            "evidence_sha256"
        ]
        checkpoints["source_acquisition_decision_ready"][
            "evidence_sha256"
        ] = record["artifact_generations"]["source_acquisition_decision"][
            "sha256"
        ]
        contracts.validate_run_record(record)
        run_path = run_dir / "workflow/run.json"
        run_path.write_bytes(canonical_json_bytes(record))
        initial_sha = sha256_file(run_path)

        store = ControlStore.initialize(workspace, contracts)
        store.prepare_initialization(
            run_id=run_id,
            output_path=run_dir,
            intent_id=intent_id,
            staging_path=root / "initialization-staging",
        )
        store.bind_publication_expectations(
            intent_id,
            expected_run_record_sha256=initial_sha,
            canonical_platform="youtube",
            canonical_item_id=inventory["canonical_item_id"],
            source_identity=inventory["source_identity"],
            source_manifest_sha256=None,
        )
        store.transition_intent(
            intent_id,
            expected_state="PREPARED",
            new_state="PUBLISHED",
            run_record_sha256=initial_sha,
        )
        store.transition_intent(
            intent_id,
            expected_state="PUBLISHED",
            new_state="RECORD_COMMITTED",
        )
        store.transition_intent(
            intent_id,
            expected_state="RECORD_COMMITTED",
            new_state="COMMITTED",
        )
        return VideoWorkflowKernel(workspace), run_dir, record


def build_decision_ready_authority() -> tuple[VideoWorkflowKernel, Path, dict]:
    """Build a decision-ready v3 Run bound to a real file-backed authority."""

    return _SourcePublicationFixtureBuilder().build_decision_ready_authority()


class SourcePublicationIntegrationTests(unittest.TestCase):
    def test_kernel_finalizer_and_reconciler_commit_real_v9_publication(self) -> None:
        kernel, run_dir, prior = build_decision_ready_authority()

        with self.assertRaisesRegex(
            SourcePublicationFault,
            "after_source_publication_intent_prepared",
        ):
            kernel.finalize_production_source(
                run_dir,
                published_at="2026-07-18T12:00:00+08:00",
                fault_point="after_source_publication_intent_prepared",
            )

        active = kernel.control_store.active_source_publication(prior["run_id"])
        self.assertIsNotNone(active)
        self.assertEqual(active["state"], "PREPARED")
        self.assertIsNone(active["journal_sha256"])

        reconciled = kernel.reconcile_run(run_dir)
        current = read_json(run_dir / "workflow/run.json")
        manifest = read_json(run_dir / "source/manifest.json")
        intent_id = current["last_mutation_intent_id"]

        self.assertEqual(reconciled.outcome, "new_state_complete")
        self.assertEqual(current["coordination_revision"], 2)
        self.assertEqual(current["source_state"], "ready")
        self.assertEqual(current["phase"], "source_ready")
        self.assertEqual(current["source_version"], manifest["source_version"])
        self.assertEqual(
            current["artifact_generations"]["source_manifest"]["sha256"],
            sha256_file(run_dir / "source/manifest.json"),
        )
        committed = kernel.control_store.source_publication_by_id(intent_id)
        self.assertEqual(committed["state"], "COMMITTED")
        self.assertEqual(
            kernel.control_store.current_run_record_sha(prior["run_id"]),
            sha256_file(run_dir / "workflow/run.json"),
        )

        replay = kernel.finalize_production_source(
            run_dir,
            published_at="2026-07-18T12:00:00+08:00",
        )
        self.assertEqual(replay.intent_id, intent_id)
        self.assertEqual(replay.source_version, current["source_version"])
        self.assertEqual(
            replay.manifest_sha256,
            sha256_bytes(canonical_json_bytes(manifest)),
        )

        stale_identity = deepcopy(current)
        stale_identity["coordination_revision"] += 1
        with self.assertRaisesRegex(
            KernelConflict,
            "lacks its intent identity",
        ):
            kernel.control_store.prepare_run_state_mutation(
                run_id=current["run_id"],
                expected_run_revision=current["coordination_revision"],
                old_run_record_sha256=sha256_file(
                    run_dir / "workflow/run.json"
                ),
                replacement_run_record=stale_identity,
            )

        journal_path = (
            run_dir
            / "work/source-acquisition/source-publication-journal.json"
        )
        tampered_journal = read_json(journal_path)
        tampered_journal["prior_run_record_sha256"] = "0" * 64
        journal_path.write_bytes(canonical_json_bytes(tampered_journal))
        with self.assertRaisesRegex(
            ArtifactDrift,
            "committed publication authority",
        ):
            kernel.finalize_production_source(
                run_dir,
                published_at="2026-07-18T12:00:00+08:00",
            )


if __name__ == "__main__":
    unittest.main()
