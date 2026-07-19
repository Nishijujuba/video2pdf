from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import threading
import time
import unittest
import uuid
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from video2pdf_workflow_kernel.kernel import VideoWorkflowKernel  # noqa: E402
from video2pdf_workflow_kernel.control_store_recovery import (  # noqa: E402
    ControlStoreRecovery,
)
from video2pdf_workflow_kernel import control_store_recovery as recovery_module  # noqa: E402
from video2pdf_workflow_kernel.errors import (  # noqa: E402
    ContractError,
    ControlStoreUnavailable,
    InitializationFault,
)
from video2pdf_workflow_kernel.utils import (  # noqa: E402
    read_json,
    sha256_file,
    write_json_atomic,
)


FIXTURE = PROJECT_ROOT / "tests/video_workflow/fixtures/source-ready-tracer"
TEST_RUNS = PROJECT_ROOT / "待删除/kernel-test-runs"
TASK_START = "2026-07-17T01:02:03+08:00"
CLI = PROJECT_ROOT / "scripts/video_workflow.py"


def trusted_recovery_provider_verifier(**identity: object) -> str:
    return f"provider-proof://recovery-test/{identity['terminal_result_id']}"


class ControlStoreRecoveryTests(unittest.TestCase):
    def run_cli(self, *arguments: object) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                "-B",
                str(CLI),
                *(str(argument) for argument in arguments),
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
            check=False,
        )

    def new_workspace(self, label: str) -> Path:
        # Recovery labels describe the scenario and can be very long. Keep the
        # filesystem identity compact so non-path-budget tests remain within the
        # workflow's intentional 240 UTF-16-unit ceiling as reserved paths grow.
        root = TEST_RUNS / f"csr-{uuid.uuid4().hex[:8]}"
        workspace = root / "workspace"
        workspace.mkdir(parents=True, exist_ok=False)
        return workspace

    def traced_kernel(self, label: str) -> tuple[VideoWorkflowKernel, Path]:
        workspace = self.new_workspace(label)
        kernel = VideoWorkflowKernel(
            workspace,
            resource_provider_verifiers={
                "recovery-test-provider": trusted_recovery_provider_verifier,
            },
        )
        traced = kernel.trace_source_ready(
            fixture=FIXTURE,
            task_start=TASK_START,
            request_id=f"control-store-recovery-{label}-{uuid.uuid4().hex[:8]}",
        )
        return kernel, traced.run_dir

    def test_backup_uses_sqlite_backup_api_and_passes_integrity(self) -> None:
        kernel, run_dir = self.traced_kernel("backup-integrity")
        run_id = read_json(run_dir / "workflow/run.json")["run_id"]
        backup_dir = kernel.workspace_root.parent / "selected-backups" / "backup-a"
        writer_ready = threading.Event()
        release_writer = threading.Event()
        writer_errors: list[BaseException] = []

        def independent_writer() -> None:
            connection = sqlite3.connect(
                kernel.control_store.path,
                isolation_level=None,
                timeout=10,
            )
            try:
                connection.execute("PRAGMA busy_timeout=10000")
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT INTO control_store_metadata(key, value) VALUES (?, ?)",
                    ("backup-atomic-left", "committed-together"),
                )
                connection.execute(
                    "INSERT INTO control_store_metadata(key, value) VALUES (?, ?)",
                    ("backup-atomic-right", "committed-together"),
                )
                writer_ready.set()
                if not release_writer.wait(10):
                    raise AssertionError("backup test writer was not released")
                connection.execute("COMMIT")
            except BaseException as exc:
                writer_errors.append(exc)
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
            finally:
                connection.close()

        writer = threading.Thread(target=independent_writer, daemon=True)
        writer.start()
        self.assertTrue(writer_ready.wait(10))
        backup_result: dict[str, object] = {}
        backup_errors: list[BaseException] = []

        def take_backup() -> None:
            try:
                backup_result.update(
                    kernel.backup_control_store(
                        backup_dir,
                        backup_id="backup-a",
                        coordinator_session_id="coordinator-backup-a",
                        created_at="2026-07-17T02:00:00+08:00",
                    )
                )
            except BaseException as exc:
                backup_errors.append(exc)

        backup = threading.Thread(target=take_backup, daemon=True)
        backup.start()
        time.sleep(0.2)
        self.assertTrue(backup.is_alive())
        release_writer.set()
        writer.join(10)
        backup.join(30)
        self.assertFalse(writer.is_alive())
        self.assertFalse(backup.is_alive())
        self.assertEqual(writer_errors, [])
        self.assertEqual(backup_errors, [])
        result = backup_result

        manifest_path = Path(result["manifest_path"])
        manifest = read_json(manifest_path)
        self.assertEqual(result["classification"], "control_store_backup_complete")
        self.assertEqual(manifest["snapshot_method"], "sqlite_backup_api")
        self.assertEqual(manifest["backup_id"], "backup-a")
        self.assertEqual(manifest["run_authorities"], [run_id])
        self.assertEqual(
            manifest["candidate_integrity"],
            {
                "status": "ok",
                "quick_check": "ok",
                "foreign_keys": "ok",
                "exact_schema_and_semantics": "passed",
            },
        )
        self.assertEqual(
            manifest["artifacts"]["database"]["sha256"],
            sha256_file(backup_dir / "control.sqlite3"),
        )
        self.assertEqual(
            manifest["artifacts"]["marker"]["sha256"],
            sha256_file(backup_dir / "control-store.json"),
        )
        self.assertEqual(
            manifest["artifacts"]["anchor"]["sha256"],
            sha256_file(backup_dir / "anchor.json"),
        )
        with sqlite3.connect(
            f"file:{(backup_dir / 'control.sqlite3').as_posix()}?mode=ro",
            uri=True,
        ) as connection:
            self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM run_bindings").fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT MAX(version) FROM schema_migrations"
                ).fetchone()[0],
                manifest["control_store_schema_version"],
            )
            atomic_rows = connection.execute(
                "SELECT key, value FROM control_store_metadata "
                "WHERE key IN ('backup-atomic-left', 'backup-atomic-right') "
                "ORDER BY key"
            ).fetchall()
            self.assertEqual(
                atomic_rows,
                [
                    ("backup-atomic-left", "committed-together"),
                    ("backup-atomic-right", "committed-together"),
                ],
            )

        json.dumps(manifest, sort_keys=True)

    def test_restore_reconciles_every_run_through_public_kernel_authority(self) -> None:
        kernel, first_run_dir = self.traced_kernel("restore-public-authority")
        second = kernel.trace_source_ready(
            fixture=FIXTURE,
            task_start="2026-07-17T01:02:04+08:00",
            request_id=f"control-store-recovery-second-{uuid.uuid4().hex[:8]}",
        )
        expected_run_ids = sorted(
            [
                read_json(first_run_dir / "workflow/run.json")["run_id"],
                read_json(second.run_dir / "workflow/run.json")["run_id"],
            ]
        )
        with self.assertRaises(InitializationFault):
            kernel.trace_source_ready(
                fixture=FIXTURE,
                task_start="2026-07-17T01:02:07+08:00",
                request_id=f"control-store-recovery-prepared-{uuid.uuid4().hex[:8]}",
                fault_point="after_run_record_commit_marker",
            )
        recoverable_run_id = next(
            run_id
            for run_id in kernel.control_store.run_authority_ids()
            if run_id not in expected_run_ids
        )
        self.assertEqual(
            kernel.control_store.intent_for_run(recoverable_run_id)["state"],
            "PUBLISHED",
        )
        expected_run_ids = sorted([*expected_run_ids, recoverable_run_id])
        backup_dir = kernel.workspace_root.parent / "selected-backups" / "backup-b"
        kernel.backup_control_store(
            backup_dir,
            backup_id="backup-b",
            coordinator_session_id="coordinator-backup-b",
            created_at="2026-07-17T02:10:00+08:00",
        )
        connection = sqlite3.connect(kernel.control_store.path)
        try:
            connection.execute(
                "UPDATE control_store_metadata SET value='corrupt-live-store' "
                "WHERE key='store_id'"
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(ControlStoreUnavailable):
            VideoWorkflowKernel(kernel.workspace_root)

        reconciled: list[tuple[str, str]] = []
        original_reconcile = VideoWorkflowKernel.reconcile_authority

        def observed_reconcile(
            instance: VideoWorkflowKernel,
            kind: str,
            authority_id: str,
            **kwargs: object,
        ):
            reconciled.append((kind, authority_id))
            return original_reconcile(instance, kind, authority_id, **kwargs)

        with mock.patch.object(
            VideoWorkflowKernel,
            "reconcile_authority",
            new=observed_reconcile,
        ):
            result = ControlStoreRecovery(kernel.workspace_root).restore_selected(
                backup_dir,
                backup_id="backup-b",
                coordinator_session_id="coordinator-restore-b",
                restored_at="2026-07-17T02:20:00+08:00",
            )

        self.assertEqual(result["classification"], "control_store_restore_complete")
        self.assertEqual(
            reconciled,
            [("kernel_run", run_id) for run_id in expected_run_ids],
        )
        self.assertEqual(result["reconciled_authorities"], expected_run_ids)
        operation_dir = Path(result["operation_dir"])
        self.assertTrue((operation_dir / "prior/.workflow-control/control.sqlite3").is_file())
        self.assertTrue((operation_dir / "prior/anchor.json").is_file())
        self.assertEqual(read_json(operation_dir / "restore-state.json")["state"], "COMMITTED")
        self.assertFalse(
            (kernel.workspace_root / ".workflow-control-recovery.json").exists()
        )
        restored = VideoWorkflowKernel(kernel.workspace_root)
        self.assertEqual(restored.control_store.check().status, "ok")
        self.assertEqual(
            restored.control_store.intent_for_run(recoverable_run_id)["state"],
            "COMMITTED",
        )

        lease_kernel, lease_run_dir = self.traced_kernel(
            "restore-active-claim-lease"
        )
        prepared = lease_kernel.prepare_source_acquisition_task(
            lease_run_dir,
            logical_task_key="restore-active-claim-lease",
            prepared_at=TASK_START,
            required_resources=("whisper",),
        )
        claimed = lease_kernel.claim_task(
            lease_run_dir,
            prepared.task_id,
            coordinator_session_id="coordinator-before-restore",
            worker_id="worker-before-restore",
        )
        launch_tokens: list[str] = []

        def launcher(launch_token: str) -> str:
            launch_tokens.append(launch_token)
            return "started"

        lease_kernel.launch_admitted_task(
            claimed.attempt_id,
            claimed.claim_generation,
            ("whisper",),
            launcher,
        )
        lease_id = claimed.resource_admission.lease_id
        self.assertIsNotNone(lease_id)
        lease_backup = (
            lease_kernel.workspace_root.parent
            / "selected-backups"
            / "backup-active-claim"
        )
        lease_kernel.backup_control_store(
            lease_backup,
            backup_id="backup-active-claim",
            coordinator_session_id="coordinator-backup-active-claim",
            created_at="2026-07-17T02:21:00+08:00",
        )

        blocked = ControlStoreRecovery(lease_kernel.workspace_root).restore_selected(
            lease_backup,
            backup_id="backup-active-claim",
            coordinator_session_id="coordinator-restore-active-claim",
            restored_at="2026-07-17T02:22:00+08:00",
        )
        self.assertEqual(blocked["classification"], "control_store_restore_blocked")
        self.assertNotIn("orphan_report_path", blocked)
        blocked_report = read_json(Path(blocked["report_path"]))
        self.assertEqual(blocked_report["final_global_status"], "blocked")
        self.assertEqual(
            blocked_report["resource_recovery"]["lost_coordinator_session_ids"],
            ["coordinator-before-restore"],
        )
        self.assertEqual(
            blocked_report["resource_recovery"]["transitioned_lease_ids"],
            [lease_id],
        )
        self.assertIn(
            lease_id,
            blocked_report["resource_recovery"]["unknown_lease_ids"],
        )
        self.assertFalse(
            blocked_report["resource_recovery"]["capacity_released"]
        )
        self.assertEqual(
            blocked_report["resource_recovery"]["resource_usage"]["whisper"],
            1,
        )
        active_claim_gap = next(
            gap
            for gap in blocked_report["unresolved_gaps"]
            if gap["classification"] == "active_claim_requires_manual_recovery"
        )
        self.assertEqual(active_claim_gap["attempt_id"], claimed.attempt_id)
        self.assertEqual(active_claim_gap["claim_generation"], claimed.claim_generation)

        diagnostic = ControlStoreRecovery(
            lease_kernel.workspace_root
        ).diagnostic_status()
        self.assertEqual(
            Path(diagnostic["recovery_report_path"]),
            Path(blocked["report_path"]),
        )
        blocked_kernel = VideoWorkflowKernel(
            lease_kernel.workspace_root,
            resource_provider_verifiers={
                "recovery-test-provider": trusted_recovery_provider_verifier,
            },
        )
        lease_status = blocked_kernel.resource_status(
            claimed.task_id,
            claimed.attempt_id,
        )
        self.assertEqual(lease_status.lease_state, "unknown")
        self.assertEqual(lease_status.required_resources, ("whisper",))
        self.assertEqual(
            blocked_kernel.resource_capacity_status()["resources"]["whisper"],
            {"capacity": 1, "usage": 1, "available": 0, "state": "full"},
        )
        with self.assertRaises(ControlStoreUnavailable):
            blocked_kernel.release_resource_lease(
                claimed.attempt_id,
                claimed.claim_generation,
                launch_tokens[0],
                terminal_evidence={
                    "evidence_class": "provider_terminal_result",
                    "provider": "recovery-test-provider",
                    "terminal_result_id": "late-worker-result-after-restore",
                    "declared_outcome": "succeeded",
                    "observed_at": "2026-07-17T02:23:00+08:00",
                },
            )

        drift_kernel, drift_run_dir = self.traced_kernel(
            "restore-live-resource-drift"
        )
        drift_backup = (
            drift_kernel.workspace_root.parent
            / "selected-backups"
            / "backup-before-live-resource"
        )
        drift_kernel.backup_control_store(
            drift_backup,
            backup_id="backup-before-live-resource",
            coordinator_session_id="coordinator-backup-before-live-resource",
            created_at="2026-07-17T02:24:00+08:00",
        )
        prepared_after_backup = drift_kernel.prepare_source_acquisition_task(
            drift_run_dir,
            logical_task_key="restore-live-resource-created-after-backup",
            prepared_at=TASK_START,
            required_resources=("whisper",),
        )
        claimed_after_backup = drift_kernel.claim_task(
            drift_run_dir,
            prepared_after_backup.task_id,
            coordinator_session_id="coordinator-created-after-backup",
            worker_id="worker-created-after-backup",
        )
        drift_kernel.launch_admitted_task(
            claimed_after_backup.attempt_id,
            claimed_after_backup.claim_generation,
            ("whisper",),
            lambda _launch_token: "started",
        )
        live_lease_id = claimed_after_backup.resource_admission.lease_id
        self.assertIsNotNone(live_lease_id)
        self.assertEqual(
            drift_kernel.resource_capacity_status()["resources"]["whisper"]["usage"],
            1,
        )

        drift_result = ControlStoreRecovery(
            drift_kernel.workspace_root
        ).restore_selected(
            drift_backup,
            backup_id="backup-before-live-resource",
            coordinator_session_id="coordinator-restore-live-resource-drift",
            restored_at="2026-07-17T02:25:00+08:00",
        )
        self.assertEqual(
            drift_result["classification"],
            "control_store_restore_blocked",
        )
        drift_report = read_json(Path(drift_result["report_path"]))
        recovery = drift_report["resource_recovery"]
        self.assertTrue(recovery["conservative_capacity_unresolved"])
        self.assertEqual(
            recovery["selected_store_inventory"]["resource_usage"]["whisper"],
            0,
        )
        quarantined_inventory = recovery["quarantined_live_inventory"]
        self.assertEqual(quarantined_inventory["status"], "readable")
        self.assertEqual(quarantined_inventory["resource_usage"]["whisper"], 1)
        self.assertEqual(
            quarantined_inventory["active_claims"],
            [
                {
                    "task_id": claimed_after_backup.task_id,
                    "authority_id": read_json(
                        drift_run_dir / "workflow" / "run.json"
                    )["run_id"],
                    "attempt_id": claimed_after_backup.attempt_id,
                    "claim_generation": claimed_after_backup.claim_generation,
                    "coordinator_session_id": "coordinator-created-after-backup",
                    "worker_id": "worker-created-after-backup",
                }
            ],
        )
        self.assertEqual(
            quarantined_inventory["nonterminal_leases"],
            [
                {
                    "lease_id": live_lease_id,
                    "task_id": claimed_after_backup.task_id,
                    "attempt_id": claimed_after_backup.attempt_id,
                    "claim_generation": claimed_after_backup.claim_generation,
                    "coordinator_session_id": "coordinator-created-after-backup",
                    "worker_id": "worker-created-after-backup",
                    "state": "active",
                    "required_resources": ["whisper"],
                }
            ],
        )
        gap_classes = {
            gap["classification"] for gap in drift_report["unresolved_gaps"]
        }
        self.assertIn("quarantined_live_claim_not_in_selected_store", gap_classes)
        self.assertIn("quarantined_live_lease_not_in_selected_store", gap_classes)
        self.assertFalse(recovery["capacity_released"])
        self.assertEqual(
            VideoWorkflowKernel(
                drift_kernel.workspace_root
            ).resource_capacity_status()["resources"]["whisper"]["usage"],
            0,
        )

    def test_orphaned_filesystem_commit_blocks_global_mutation_after_restore(self) -> None:
        kernel, first_run_dir = self.traced_kernel("orphaned-filesystem-commit")
        first_run_id = read_json(first_run_dir / "workflow/run.json")["run_id"]
        backup_dir = kernel.workspace_root.parent / "selected-backups" / "backup-c"
        kernel.backup_control_store(
            backup_dir,
            backup_id="backup-c",
            coordinator_session_id="coordinator-backup-c",
            created_at="2026-07-17T02:30:00+08:00",
        )
        newer = kernel.trace_source_ready(
            fixture=FIXTURE,
            task_start="2026-07-17T01:02:05+08:00",
            request_id=f"control-store-recovery-newer-{uuid.uuid4().hex[:8]}",
        )
        newer_record_path = newer.run_dir / "workflow/run.json"
        newer_record = read_json(newer_record_path)
        newer_run_id = newer_record["run_id"]

        result = ControlStoreRecovery(kernel.workspace_root).restore_selected(
            backup_dir,
            backup_id="backup-c",
            coordinator_session_id="coordinator-restore-c",
            restored_at="2026-07-17T02:40:00+08:00",
        )

        self.assertEqual(result["classification"], "orphaned_filesystem_commit")
        orphan_report = read_json(Path(result["orphan_report_path"]))
        self.assertTrue(orphan_report["global_mutation_blocked"])
        self.assertEqual(
            [gap["authority_id"] for gap in orphan_report["gaps"]],
            [newer_run_id],
        )
        gap = orphan_report["gaps"][0]
        self.assertEqual(gap["coordination_record_path"], "workflow/run.json")
        self.assertEqual(gap["run_record_sha256"], sha256_file(newer_record_path))
        self.assertEqual(
            gap["coordination_revision"], newer_record["coordination_revision"]
        )
        self.assertEqual(
            gap["missing_intent_id"], newer_record["initialization_intent_id"]
        )
        self.assertEqual(gap["missing_intent_kind"], "initialization")
        self.assertEqual(
            gap["intent_authority_status"], "absent_from_selected_store"
        )
        self.assertEqual(gap["restored_intent_matches"], [])
        self.assertEqual(
            gap["artifact_generations"], newer_record["artifact_generations"]
        )
        source_generation = newer_record["artifact_generations"]["source_manifest"]
        self.assertEqual(
            gap["canonical_artifacts"],
            [
                {
                    "logical_id": "source_manifest",
                    "path": source_generation["path"],
                    "generation": source_generation["generation"],
                    "expected_sha256": source_generation["sha256"],
                    "actual_sha256": source_generation["sha256"],
                    "status": "matching",
                }
            ],
        )
        self.assertEqual(gap["staging_evidence_status"], "available")
        self.assertEqual(len(gap["staging_evidence"]), 1)
        self.assertEqual(
            gap["staging_evidence"][0]["evidence_kind"],
            "initialization_prepared_run",
        )
        self.assertTrue(
            gap["staging_evidence"][0]["path"].endswith(
                "/bootstrap/prepared-run.json"
            )
        )
        self.assertEqual(
            gap["staging_evidence"][0]["actual_sha256"],
            sha256_file(newer_record_path),
        )
        self.assertEqual(gap["staging_evidence"][0]["status"], "matching")
        self.assertEqual(gap["preservation_status"], "not_applicable")
        self.assertEqual(gap["preservation_evidence"], [])
        self.assertTrue(orphan_report["manual_recovery_required"])
        kernel.contracts.validate("orphaned-filesystem-commit-report", orphan_report)
        diagnostic = ControlStoreRecovery(kernel.workspace_root).diagnostic_status()
        self.assertEqual(diagnostic["state"], "BLOCKED")
        self.assertEqual(
            Path(diagnostic["orphan_report_path"]),
            Path(result["orphan_report_path"]),
        )

        blocked_kernel = VideoWorkflowKernel(kernel.workspace_root)
        self.assertEqual(blocked_kernel.control_store.check().status, "ok")
        self.assertIsNotNone(blocked_kernel.control_store.binding_for_run(first_run_id))
        self.assertIsNone(blocked_kernel.control_store.binding_for_run(newer_run_id))
        with self.assertRaisesRegex(
            ControlStoreUnavailable,
            "persistent recovery authority",
        ):
            blocked_kernel.trace_source_ready(
                fixture=FIXTURE,
                task_start="2026-07-17T01:02:06+08:00",
                request_id=f"control-store-recovery-blocked-{uuid.uuid4().hex[:8]}",
            )

    def test_subsequent_orphan_records_task_staging_and_preservation_evidence(self) -> None:
        kernel, run_dir = self.traced_kernel("subsequent-orphan-evidence")
        backup_dir = kernel.workspace_root.parent / "selected-backups" / "backup-d"
        kernel.backup_control_store(
            backup_dir,
            backup_id="backup-d",
            coordinator_session_id="coordinator-backup-d",
            created_at="2026-07-17T02:45:00+08:00",
        )

        prepared = kernel.prepare_source_acquisition_task(
            run_dir,
            logical_task_key="subsequent-orphan-source-acquisition",
            prepared_at="2026-07-17T02:46:00+08:00",
        )
        claimed = kernel.claim_task(
            run_dir,
            prepared.task_id,
            coordinator_session_id="coordinator-subsequent-orphan",
            worker_id="worker-subsequent-orphan",
        )
        launch_tokens: list[str] = []
        kernel.launch_admitted_task(
            claimed.attempt_id,
            claimed.claim_generation,
            ("codex_semantic",),
            lambda launch_token: launch_tokens.append(launch_token) or "started",
        )
        kernel.release_resource_lease(
            claimed.attempt_id,
            claimed.claim_generation,
            launch_tokens[0],
            terminal_evidence={
                "evidence_class": "provider_terminal_result",
                "provider": "recovery-test-provider",
                "terminal_result_id": f"subsequent-orphan-{claimed.attempt_id}",
                "declared_outcome": "succeeded",
                "observed_at": "2026-07-17T02:47:00+08:00",
            },
        )
        envelope = read_json(prepared.envelope_path)
        patch = {
            "schema_name": "source-acquisition-judgment-patch",
            "schema_version": "1.0.0",
            "kernel_version": "2.0.0",
            "task_id": prepared.task_id,
            "attempt_id": claimed.attempt_id,
            "task_envelope_sha256": sha256_file(prepared.envelope_path),
            "source_manifest_sha256": envelope["input_artifacts"][0]["sha256"],
            "judgment": {
                "selected_subtitle_track": "subtitle_en",
                "whisper_fallback": {
                    "choice": "not_required",
                    "rationale": "The immutable English subtitle fixture is usable.",
                },
                "known_gaps": [],
            },
        }
        attempt_output = claimed.attempt_dir / "o/p.json"
        attempt_output.parent.mkdir(parents=False, exist_ok=False)
        write_json_atomic(attempt_output, patch)
        kernel.complete_task(
            run_dir,
            task_id=prepared.task_id,
            attempt_id=claimed.attempt_id,
            claim_generation=claimed.claim_generation,
        )
        promoted = kernel.promote_task(
            run_dir,
            task_id=prepared.task_id,
            attempt_id=claimed.attempt_id,
            claim_generation=claimed.claim_generation,
        )
        promoted_record = read_json(run_dir / "workflow/run.json")
        self.assertEqual(
            promoted_record["last_mutation_intent_id"], promoted.intent_id
        )

        result = ControlStoreRecovery(kernel.workspace_root).restore_selected(
            backup_dir,
            backup_id="backup-d",
            coordinator_session_id="coordinator-restore-d",
            restored_at="2026-07-17T02:50:00+08:00",
        )
        self.assertEqual(result["classification"], "orphaned_filesystem_commit")
        orphan_report = read_json(Path(result["orphan_report_path"]))
        orphan_gaps = [
            gap
            for gap in orphan_report["gaps"]
            if gap["classification"] == "orphaned_filesystem_commit"
        ]
        self.assertEqual(len(orphan_gaps), 1)
        gap = orphan_gaps[0]
        self.assertEqual(gap["missing_intent_id"], promoted.intent_id)
        self.assertEqual(gap["missing_intent_kind"], "subsequent_mutation")
        self.assertEqual(
            gap["intent_authority_status"], "absent_from_selected_store"
        )
        self.assertEqual(gap["restored_intent_matches"], [])
        self.assertEqual(
            gap["artifact_generations"], promoted_record["artifact_generations"]
        )
        self.assertEqual(
            {artifact["logical_id"] for artifact in gap["canonical_artifacts"]},
            {"source_manifest", "source_acquisition_decision"},
        )
        self.assertTrue(
            all(
                artifact["status"] == "matching"
                and artifact["actual_sha256"] == artifact["expected_sha256"]
                for artifact in gap["canonical_artifacts"]
            )
        )
        self.assertEqual(gap["staging_evidence_status"], "available")
        self.assertEqual(
            {item["evidence_kind"] for item in gap["staging_evidence"]},
            {"task_promotion_journal", "task_attempt_output"},
        )
        self.assertEqual(gap["preservation_status"], "not_required")
        self.assertEqual(gap["preservation_evidence"], [])
        kernel.contracts.validate("orphaned-filesystem-commit-report", orphan_report)

    def test_missing_corrupt_and_schema_mismatched_backups_fail_closed(self) -> None:
        kernel, _run_dir = self.traced_kernel("invalid-backups")

        def live_fingerprints() -> tuple[str, str, str]:
            return (
                sha256_file(kernel.control_store.path),
                sha256_file(kernel.control_store.marker_path),
                sha256_file(kernel.control_store.anchor_path),
            )

        expected_live = live_fingerprints()
        missing = kernel.workspace_root.parent / "selected-backups" / "missing"
        with self.assertRaises(ControlStoreUnavailable):
            ControlStoreRecovery(kernel.workspace_root).restore_selected(
                missing,
                backup_id="missing",
                coordinator_session_id="coordinator-restore-missing",
                restored_at="2026-07-17T03:00:00+08:00",
            )
        self.assertEqual(live_fingerprints(), expected_live)
        self.assertFalse((kernel.workspace_root / ".workflow-control-recovery.json").exists())

        corrupt = kernel.workspace_root.parent / "selected-backups" / "backup-corrupt"
        kernel.backup_control_store(
            corrupt,
            backup_id="backup-corrupt",
            coordinator_session_id="coordinator-backup-corrupt",
            created_at="2026-07-17T03:01:00+08:00",
        )
        with (corrupt / "control.sqlite3").open("r+b") as handle:
            handle.seek(0)
            handle.write(b"broken sqlite header")
            handle.flush()
        with self.assertRaises(ControlStoreUnavailable):
            ControlStoreRecovery(kernel.workspace_root).restore_selected(
                corrupt,
                backup_id="backup-corrupt",
                coordinator_session_id="coordinator-restore-corrupt",
                restored_at="2026-07-17T03:02:00+08:00",
            )
        self.assertEqual(live_fingerprints(), expected_live)
        self.assertFalse((kernel.workspace_root / ".workflow-control-recovery.json").exists())

        mismatched = (
            kernel.workspace_root.parent / "selected-backups" / "backup-schema-mismatch"
        )
        kernel.backup_control_store(
            mismatched,
            backup_id="backup-schema-mismatch",
            coordinator_session_id="coordinator-backup-schema-mismatch",
            created_at="2026-07-17T03:03:00+08:00",
        )
        connection = sqlite3.connect(mismatched / "control.sqlite3")
        try:
            connection.execute("DELETE FROM schema_migrations WHERE version=9")
            connection.commit()
        finally:
            connection.close()
        manifest = read_json(mismatched / "backup-manifest.json")
        manifest["artifacts"]["database"]["sha256"] = sha256_file(
            mismatched / "control.sqlite3"
        )
        write_json_atomic(mismatched / "backup-manifest.json", manifest)
        with self.assertRaisesRegex(ControlStoreUnavailable, "schema mismatch"):
            ControlStoreRecovery(kernel.workspace_root).restore_selected(
                mismatched,
                backup_id="backup-schema-mismatch",
                coordinator_session_id="coordinator-restore-schema-mismatch",
                restored_at="2026-07-17T03:04:00+08:00",
            )
        self.assertEqual(live_fingerprints(), expected_live)
        self.assertFalse((kernel.workspace_root / ".workflow-control-recovery.json").exists())
        self.assertEqual(VideoWorkflowKernel(kernel.workspace_root).control_store.check().status, "ok")

        cli_kernel, _cli_run_dir = self.traced_kernel("restore-cli-early-dispatch")
        cli_backup = cli_kernel.workspace_root.parent / "selected-backups" / "backup-cli"
        backup_process = self.run_cli(
            "control-store-backup",
            "--workspace-root",
            cli_kernel.workspace_root,
            "--backup-dir",
            cli_backup,
            "--backup-id",
            "backup-cli",
            "--coordinator-session-id",
            "coordinator-backup-cli",
            "--created-at",
            "2026-07-17T03:20:00+08:00",
        )
        self.assertEqual(backup_process.returncode, 0, backup_process.stdout)
        backup_envelope = json.loads(backup_process.stdout)
        self.assertEqual(backup_envelope["status"], "ok")
        self.assertEqual(
            backup_envelope["classification"],
            "control_store_backup_complete",
        )
        cli_kernel.contracts.validate("workflow-result", backup_envelope)

        connection = sqlite3.connect(cli_kernel.control_store.path)
        try:
            connection.execute(
                "UPDATE control_store_metadata SET value='cli-corrupt-live-store' "
                "WHERE key='store_id'"
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(ControlStoreUnavailable):
            VideoWorkflowKernel(cli_kernel.workspace_root)

        restore_process = self.run_cli(
            "control-store-restore",
            "--workspace-root",
            cli_kernel.workspace_root,
            "--backup-dir",
            cli_backup,
            "--backup-id",
            "backup-cli",
            "--coordinator-session-id",
            "coordinator-restore-cli",
            "--restored-at",
            "2026-07-17T03:21:00+08:00",
        )
        self.assertEqual(restore_process.returncode, 0, restore_process.stdout)
        restore_envelope = json.loads(restore_process.stdout)
        self.assertEqual(restore_envelope["status"], "ok")
        self.assertEqual(
            restore_envelope["classification"],
            "control_store_restore_complete",
        )
        cli_kernel.contracts.validate("workflow-result", restore_envelope)
        self.assertEqual(
            VideoWorkflowKernel(cli_kernel.workspace_root).control_store.check().status,
            "ok",
        )
        status_process = self.run_cli(
            "control-store-recovery-status",
            "--workspace-root",
            cli_kernel.workspace_root,
        )
        self.assertEqual(status_process.returncode, 0, status_process.stdout)
        self.assertEqual(json.loads(status_process.stdout)["data"]["state"], "IDLE")

        blocked_cli_kernel, _blocked_cli_run = self.traced_kernel(
            "restore-cli-blocked"
        )
        blocked_cli_backup = (
            blocked_cli_kernel.workspace_root.parent
            / "selected-backups"
            / "backup-cli-blocked"
        )
        blocked_cli_kernel.backup_control_store(
            blocked_cli_backup,
            backup_id="backup-cli-blocked",
            coordinator_session_id="coordinator-backup-cli-blocked",
            created_at="2026-07-17T03:22:00+08:00",
        )
        blocked_cli_kernel.trace_source_ready(
            fixture=FIXTURE,
            task_start="2026-07-17T01:02:09+08:00",
            request_id=f"cli-orphan-{uuid.uuid4().hex[:8]}",
        )
        blocked_process = self.run_cli(
            "control-store-restore",
            "--workspace-root",
            blocked_cli_kernel.workspace_root,
            "--backup-dir",
            blocked_cli_backup,
            "--backup-id",
            "backup-cli-blocked",
            "--coordinator-session-id",
            "coordinator-restore-cli-blocked",
            "--restored-at",
            "2026-07-17T03:23:00+08:00",
        )
        self.assertEqual(
            blocked_process.returncode,
            ControlStoreUnavailable.exit_code,
            blocked_process.stdout,
        )
        blocked_envelope = json.loads(blocked_process.stdout)
        self.assertEqual(blocked_envelope["status"], "error")
        self.assertEqual(
            blocked_envelope["classification"],
            "control_store_unavailable",
        )
        self.assertEqual(
            blocked_envelope["data"]["classification"],
            "orphaned_filesystem_commit",
        )
        self.assertTrue(Path(blocked_envelope["evidence_path"]).is_file())
        blocked_cli_kernel.contracts.validate("workflow-result", blocked_envelope)

    def test_corrupt_live_store_uses_atomic_quarantine_and_persists_failure_evidence(
        self,
    ) -> None:
        kernel, _run_dir = self.traced_kernel("restore-corrupt-live-store")
        backup_dir = (
            kernel.workspace_root.parent
            / "selected-backups"
            / "backup-before-live-corruption"
        )
        kernel.backup_control_store(
            backup_dir,
            backup_id="backup-before-live-corruption",
            coordinator_session_id="coordinator-backup-before-live-corruption",
            created_at="2026-07-17T03:24:00+08:00",
        )
        with kernel.control_store.path.open("r+b") as handle:
            handle.seek(0)
            handle.write(b"destroyed sqlite header")
            handle.flush()

        process = self.run_cli(
            "control-store-restore",
            "--workspace-root",
            kernel.workspace_root,
            "--backup-dir",
            backup_dir,
            "--backup-id",
            "backup-before-live-corruption",
            "--coordinator-session-id",
            "coordinator-restore-corrupt-live",
            "--restored-at",
            "2026-07-17T03:25:00+08:00",
        )
        self.assertEqual(
            process.returncode,
            ControlStoreUnavailable.exit_code,
            process.stdout,
        )
        envelope = json.loads(process.stdout)
        self.assertEqual(envelope["status"], "error")
        self.assertEqual(envelope["classification"], "control_store_unavailable")
        evidence_path = Path(envelope["evidence_path"])
        self.assertTrue(evidence_path.is_file())
        kernel.contracts.validate("workflow-result", envelope)
        report = read_json(evidence_path)
        kernel.contracts.validate("control-store-recovery-report", report)
        self.assertEqual(report["final_global_status"], "blocked")
        self.assertEqual(
            report["quiescence"]["writer_lock"],
            "atomic_directory_quarantine",
        )
        recovery = report["resource_recovery"]
        self.assertTrue(recovery["conservative_capacity_unresolved"])
        self.assertEqual(
            recovery["quarantined_live_inventory"]["status"],
            "unreadable_or_invalid",
        )
        self.assertIsNone(
            recovery["quarantined_live_inventory"]["resource_usage"]
        )
        self.assertIn(
            "quarantined_live_resource_inventory_unreadable_or_invalid",
            {
                gap["classification"]
                for gap in report["unresolved_gaps"]
            },
        )
        operation_dir = Path(envelope["data"]["operation_dir"])
        self.assertTrue(
            (operation_dir / "prior/.workflow-control/control.sqlite3").is_file()
        )
        blocked_kernel = VideoWorkflowKernel(kernel.workspace_root)
        self.assertEqual(blocked_kernel.control_store.check().status, "ok")

    def test_restore_resume_recovers_every_persistent_state_boundary(self) -> None:
        boundaries = (
            "PREPARED",
            "OLD_MOVED",
            "NEW_PUBLISHED",
            "VALIDATED",
            "RECONCILING",
            "COMMITTED",
        )
        for index, boundary in enumerate(boundaries):
            with self.subTest(boundary=boundary):
                kernel, _run_dir = self.traced_kernel(
                    f"restore-resume-{boundary.casefold()}"
                )
                backup_dir = (
                    kernel.workspace_root.parent
                    / "selected-backups"
                    / f"backup-resume-{boundary.casefold()}"
                )
                backup_id = f"backup-resume-{boundary.casefold()}"
                kernel.backup_control_store(
                    backup_dir,
                    backup_id=backup_id,
                    coordinator_session_id=f"coordinator-backup-{boundary.casefold()}",
                    created_at=f"2026-07-17T04:{index:02d}:00+08:00",
                )
                recovery = ControlStoreRecovery(kernel.workspace_root)
                with self.assertRaises(recovery_module.RestoreInterruption):
                    recovery.restore_selected(
                        backup_dir,
                        backup_id=backup_id,
                        coordinator_session_id=(
                            f"coordinator-restore-{boundary.casefold()}"
                        ),
                        restored_at=f"2026-07-17T04:{index:02d}:30+08:00",
                        fault_point=f"after_{boundary.casefold()}",
                    )
                sentinel = read_json(
                    kernel.workspace_root / ".workflow-control-recovery.json"
                )
                self.assertEqual(sentinel["state"], boundary)
                operation_id = sentinel["operation_id"]
                operation_dir = (
                    kernel.workspace_root
                    / "待删除"
                    / "control-store-restores"
                    / operation_id
                )
                state_record = read_json(operation_dir / "restore-state.json")
                kernel.contracts.validate("control-store-restore-state", state_record)
                self.assertEqual(state_record["state"], boundary)

                resumed = ControlStoreRecovery(
                    kernel.workspace_root
                ).resume_restore(
                    operation_id=operation_id,
                    resumed_at=f"2026-07-17T05:{index:02d}:00+08:00",
                )
                self.assertEqual(
                    resumed["classification"],
                    "control_store_restore_complete",
                )
                self.assertFalse(
                    (kernel.workspace_root / ".workflow-control-recovery.json").exists()
                )
                self.assertEqual(
                    VideoWorkflowKernel(
                        kernel.workspace_root
                    ).control_store.check().status,
                    "ok",
                )

    def test_restore_resume_repairs_one_record_ahead_windows(self) -> None:
        cases = (
            ("state-write-ahead", "after_state_record_before_sentinel"),
            ("token-rotation-ahead", "after_old_moved"),
        )
        for index, (label, initial_fault) in enumerate(cases):
            with self.subTest(label=label):
                kernel, _run_dir = self.traced_kernel(label)
                backup_id = f"backup-{label}"
                backup_dir = (
                    kernel.workspace_root.parent
                    / "selected-backups"
                    / backup_id
                )
                kernel.backup_control_store(
                    backup_dir,
                    backup_id=backup_id,
                    coordinator_session_id=f"coordinator-backup-{label}",
                    created_at=f"2026-07-17T06:{index:02d}:00+08:00",
                )
                with self.assertRaises(recovery_module.RestoreInterruption):
                    ControlStoreRecovery(kernel.workspace_root).restore_selected(
                        backup_dir,
                        backup_id=backup_id,
                        coordinator_session_id=f"coordinator-restore-{label}",
                        restored_at=f"2026-07-17T06:{index:02d}:30+08:00",
                        fault_point=initial_fault,
                    )
                sentinel = read_json(
                    kernel.workspace_root / ".workflow-control-recovery.json"
                )
                operation_id = sentinel["operation_id"]
                if label == "token-rotation-ahead":
                    interrupted_resume = ControlStoreRecovery(kernel.workspace_root)
                    interrupted_resume._fault_point = (
                        "after_token_state_record_before_sentinel"
                    )
                    with self.assertRaises(recovery_module.RestoreInterruption):
                        interrupted_resume.resume_restore(
                            operation_id=operation_id,
                            resumed_at="2026-07-17T06:10:00+08:00",
                        )
                result = ControlStoreRecovery(
                    kernel.workspace_root
                ).resume_restore(
                    operation_id=operation_id,
                    resumed_at="2026-07-17T06:20:00+08:00",
                )
                self.assertEqual(
                    result["classification"],
                    "control_store_restore_complete",
                )

    def test_restore_resume_survives_partial_publication(self) -> None:
        kernel, _run_dir = self.traced_kernel("partial-publication")
        backup_id = "backup-partial-publication"
        backup_dir = kernel.workspace_root.parent / "selected-backups" / backup_id
        kernel.backup_control_store(
            backup_dir,
            backup_id=backup_id,
            coordinator_session_id="coordinator-backup-partial-publication",
            created_at="2026-07-17T06:30:00+08:00",
        )
        original_replace = recovery_module.os.replace

        def interrupt_after_database_move(source: object, destination: object) -> None:
            original_replace(source, destination)
            destination_path = Path(destination)
            if (
                destination_path.parent.name == "published-control"
                and destination_path.name == "control.sqlite3"
            ):
                raise recovery_module.RestoreInterruption(
                    "partial publication process boundary"
                )

        with mock.patch.object(
            recovery_module.os,
            "replace",
            side_effect=interrupt_after_database_move,
        ):
            with self.assertRaises(recovery_module.RestoreInterruption):
                ControlStoreRecovery(kernel.workspace_root).restore_selected(
                    backup_dir,
                    backup_id=backup_id,
                    coordinator_session_id=(
                        "coordinator-restore-partial-publication"
                    ),
                    restored_at="2026-07-17T06:31:00+08:00",
                )
        sentinel = read_json(
            kernel.workspace_root / ".workflow-control-recovery.json"
        )
        self.assertEqual(sentinel["state"], "OLD_MOVED")
        operation_dir = (
            kernel.workspace_root
            / "待删除"
            / "control-store-restores"
            / sentinel["operation_id"]
        )
        self.assertTrue(
            (operation_dir / "staging/published-control/control.sqlite3").is_file()
        )
        result = ControlStoreRecovery(kernel.workspace_root).resume_restore(
            operation_id=sentinel["operation_id"],
            resumed_at="2026-07-17T06:32:00+08:00",
        )
        self.assertEqual(result["classification"], "control_store_restore_complete")

    def test_restore_resume_cli_recovers_hard_exit_and_fences_concurrency(self) -> None:
        kernel, _run_dir = self.traced_kernel("hard-exit-resume")
        backup_id = "backup-hard-exit-resume"
        backup_dir = kernel.workspace_root.parent / "selected-backups" / backup_id
        kernel.backup_control_store(
            backup_dir,
            backup_id=backup_id,
            coordinator_session_id="coordinator-backup-hard-exit-resume",
            created_at="2026-07-17T06:40:00+08:00",
        )
        script = f"""
import os
from pathlib import Path
import sys
sys.path.insert(0, {str(SRC_ROOT)!r})
from video2pdf_workflow_kernel import control_store_recovery as module
original_replace = module.os.replace
def hard_exit_after_control_move(source, destination):
    original_replace(source, destination)
    target = Path(destination)
    if target.parent.name == 'prior' and target.name == '.workflow-control':
        os._exit(91)
module.os.replace = hard_exit_after_control_move
module.ControlStoreRecovery(Path({str(kernel.workspace_root)!r})).restore_selected(
    Path({str(backup_dir)!r}),
    backup_id={backup_id!r},
    coordinator_session_id='coordinator-restore-hard-exit-resume',
    restored_at='2026-07-17T06:41:00+08:00',
)
"""
        crashed = subprocess.run(
            [sys.executable, "-X", "utf8", "-B", "-c", script],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
            check=False,
        )
        self.assertEqual(crashed.returncode, 91, crashed.stderr)
        sentinel = read_json(
            kernel.workspace_root / ".workflow-control-recovery.json"
        )
        self.assertEqual(sentinel["state"], "PREPARED")
        operation_dir = (
            kernel.workspace_root
            / "待删除"
            / "control-store-restores"
            / sentinel["operation_id"]
        )
        self.assertTrue((operation_dir / "prior/.workflow-control").is_dir())
        self.assertTrue(kernel.control_store.anchor_path.is_file())

        recovery = ControlStoreRecovery(kernel.workspace_root)
        held_lock = recovery._acquire_restore_lock(operation_dir)
        try:
            fenced = self.run_cli(
                "control-store-restore-resume",
                "--workspace-root",
                kernel.workspace_root,
                "--operation-id",
                sentinel["operation_id"],
                "--resumed-at",
                "2026-07-17T06:42:00+08:00",
            )
        finally:
            recovery._release_restore_lock(held_lock)
        self.assertEqual(
            fenced.returncode,
            ControlStoreUnavailable.exit_code,
            fenced.stdout,
        )
        fenced_envelope = json.loads(fenced.stdout)
        self.assertEqual(fenced_envelope["status"], "error")
        self.assertEqual(
            fenced_envelope["classification"],
            "control_store_unavailable",
        )
        kernel.contracts.validate("workflow-result", fenced_envelope)

        resumed = self.run_cli(
            "control-store-restore-resume",
            "--workspace-root",
            kernel.workspace_root,
            "--operation-id",
            sentinel["operation_id"],
            "--resumed-at",
            "2026-07-17T06:43:00+08:00",
        )
        self.assertEqual(resumed.returncode, 0, resumed.stdout)
        envelope = json.loads(resumed.stdout)
        self.assertEqual(envelope["status"], "ok")
        self.assertEqual(
            envelope["classification"],
            "control_store_restore_complete",
        )
        self.assertTrue(Path(envelope["evidence_path"]).is_file())
        kernel.contracts.validate("workflow-result", envelope)

    def test_restore_resume_rejects_impossible_authority_and_report_drift(self) -> None:
        operation_dir = (
            self.new_workspace("invalid-lock")
            / "待删除/control-store-restores"
            / f"restore-{uuid.uuid4().hex}"
        )
        operation_dir.mkdir(parents=True, exist_ok=False)
        (operation_dir / "restore-operation.lock").mkdir()
        with self.assertRaisesRegex(
            ControlStoreUnavailable,
            "linked or non-file",
        ):
            ControlStoreRecovery(operation_dir.parents[2])._acquire_restore_lock(
                operation_dir
            )

        kernel, _run_dir = self.traced_kernel("report-drift")
        backup_id = "backup-report-drift"
        backup_dir = kernel.workspace_root.parent / "selected-backups" / backup_id
        kernel.backup_control_store(
            backup_dir,
            backup_id=backup_id,
            coordinator_session_id="coordinator-backup-report-drift",
            created_at="2026-07-17T06:50:00+08:00",
        )
        with self.assertRaises(recovery_module.RestoreInterruption):
            ControlStoreRecovery(kernel.workspace_root).restore_selected(
                backup_dir,
                backup_id=backup_id,
                coordinator_session_id="coordinator-restore-report-drift",
                restored_at="2026-07-17T06:51:00+08:00",
                fault_point="after_committed",
            )
        sentinel = read_json(
            kernel.workspace_root / ".workflow-control-recovery.json"
        )
        report_path = (
            kernel.workspace_root
            / ".workflow-control/control_store_recovery_report.json"
        )
        report = read_json(report_path)
        report["reported_at"] = "2026-07-17T06:51:30+08:00"
        write_json_atomic(report_path, report)
        with self.assertRaisesRegex(
            ControlStoreUnavailable,
            "fingerprint drifted",
        ):
            ControlStoreRecovery(kernel.workspace_root).resume_restore(
                operation_id=sentinel["operation_id"],
                resumed_at="2026-07-17T06:52:00+08:00",
            )

    @unittest.skipUnless(
        sys.platform == "win32",
        "Windows junction semantics are platform-specific",
    )
    def test_restore_resume_rejects_windows_junction_authority_boundaries(
        self,
    ) -> None:
        for junction_level in ("operation", "ancestor"):
            with self.subTest(junction_level=junction_level):
                workspace = self.new_workspace(f"junction-{junction_level}")
                operation_id = f"restore-{uuid.uuid4().hex}"
                restores_root = workspace / "待删除" / "control-store-restores"
                junction_target = (
                    workspace / "待删除" / f"junction-target-{junction_level}"
                )
                junction_target.mkdir(parents=True, exist_ok=False)
                if junction_level == "operation":
                    restores_root.mkdir(parents=True, exist_ok=False)
                    junction_path = restores_root / operation_id
                    operation_dir = junction_target
                else:
                    restores_root.parent.mkdir(parents=True, exist_ok=True)
                    junction_path = restores_root
                    operation_dir = junction_target / operation_id
                    operation_dir.mkdir(parents=False, exist_ok=False)
                created = subprocess.run(
                    [
                        "cmd.exe",
                        "/d",
                        "/c",
                        "mklink",
                        "/J",
                        str(junction_path),
                        str(junction_target),
                    ],
                    cwd=PROJECT_ROOT,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=30,
                    check=False,
                )
                self.assertEqual(
                    created.returncode,
                    0,
                    (created.stdout or "") + (created.stderr or ""),
                )
                if hasattr(junction_path, "is_junction"):
                    self.assertTrue(junction_path.is_junction())
                self.assertTrue(
                    getattr(
                        junction_path.lstat(),
                        "st_file_attributes",
                        0,
                    )
                    & stat.FILE_ATTRIBUTE_REPARSE_POINT
                )
                self.assertFalse(junction_path.is_symlink())
                with self.assertRaisesRegex(
                    ControlStoreUnavailable,
                    "reparse",
                ):
                    ControlStoreRecovery(workspace).resume_restore(
                        operation_id=operation_id,
                        resumed_at="2026-07-17T06:53:00+08:00",
                    )

    def test_restore_lock_rechecks_open_handle_path_identity(self) -> None:
        workspace = self.new_workspace("lock-identity")
        operation_dir = (
            workspace
            / "待删除"
            / "control-store-restores"
            / f"restore-{uuid.uuid4().hex}"
        )
        operation_dir.mkdir(parents=True, exist_ok=False)
        lock_path = operation_dir / "restore-operation.lock"
        replacement_path = operation_dir / "replacement-lock-target"
        lock_path.write_bytes(b"\0")
        replacement_path.write_bytes(b"\0")
        original_open = Path.open
        acquired: list[tuple[object, str]] = []

        def substituted_open(path: Path, *args: object, **kwargs: object):
            selected = replacement_path if path == lock_path else path
            return original_open(selected, *args, **kwargs)

        recovery = ControlStoreRecovery(workspace)
        try:
            with mock.patch.object(Path, "open", new=substituted_open):
                with self.assertRaisesRegex(
                    ControlStoreUnavailable,
                    "identity",
                ):
                    acquired.append(recovery._acquire_restore_lock(operation_dir))
        finally:
            for held_lock in acquired:
                recovery._release_restore_lock(held_lock)

    def test_first_restore_path_revalidates_committed_report_before_archive(
        self,
    ) -> None:
        kernel, _run_dir = self.traced_kernel("first-path-report-drift")
        backup_id = "backup-first-path-report-drift"
        backup_dir = kernel.workspace_root.parent / "selected-backups" / backup_id
        kernel.backup_control_store(
            backup_dir,
            backup_id=backup_id,
            coordinator_session_id="coordinator-backup-first-path-report-drift",
            created_at="2026-07-17T06:54:00+08:00",
        )
        recovery = ControlStoreRecovery(kernel.workspace_root)
        original_advance = recovery._advance_restore_state

        def drift_report_after_commit(
            state_record: dict[str, object],
            operation_dir: Path,
            sentinel: dict[str, object],
            state: str,
            recorded_at: str,
        ) -> None:
            original_advance(
                state_record,
                operation_dir,
                sentinel,
                state,
                recorded_at,
            )
            if state == "COMMITTED":
                report_path = Path(str(state_record["recovery_report_path"]))
                report = read_json(report_path)
                report["reported_at"] = "2026-07-17T06:54:30+08:00"
                write_json_atomic(report_path, report)

        with mock.patch.object(
            recovery,
            "_advance_restore_state",
            side_effect=drift_report_after_commit,
        ):
            with self.assertRaisesRegex(
                ControlStoreUnavailable,
                "fingerprint drifted",
            ):
                recovery.restore_selected(
                    backup_dir,
                    backup_id=backup_id,
                    coordinator_session_id=(
                        "coordinator-restore-first-path-report-drift"
                    ),
                    restored_at="2026-07-17T06:55:00+08:00",
                )

        active_sentinel_path = (
            kernel.workspace_root / ".workflow-control-recovery.json"
        )
        self.assertTrue(active_sentinel_path.is_file())
        sentinel = read_json(active_sentinel_path)
        operation_dir = Path(str(sentinel["state_path"])).parent
        state_record = read_json(operation_dir / "restore-state.json")
        self.assertEqual(state_record["state"], "COMMITTED")
        self.assertFalse((operation_dir / "sentinel.json").exists())
        self.assertEqual(
            sentinel["recovery_report_path"],
            state_record["recovery_report_path"],
        )
        self.assertEqual(
            sentinel["recovery_report_sha256"],
            state_record["recovery_report_sha256"],
        )

    def test_restore_resume_repairs_initial_sentinel_before_prepared_history(self) -> None:
        kernel, _run_dir = self.traced_kernel("initial-sentinel-crash")
        backup_id = "backup-initial-sentinel-crash"
        backup_dir = kernel.workspace_root.parent / "selected-backups" / backup_id
        kernel.backup_control_store(
            backup_dir,
            backup_id=backup_id,
            coordinator_session_id="coordinator-backup-initial-sentinel-crash",
            created_at="2026-07-17T07:00:00+08:00",
        )
        script = f"""
import os
from pathlib import Path
import sys
sys.path.insert(0, {str(SRC_ROOT)!r})
from video2pdf_workflow_kernel import control_store_recovery as module
original_advance = module.ControlStoreRecovery._advance_restore_state
def hard_exit_before_prepared_history(self, state_record, operation_dir, sentinel, state, recorded_at):
    if state == 'PREPARED':
        os._exit(91)
    return original_advance(self, state_record, operation_dir, sentinel, state, recorded_at)
module.ControlStoreRecovery._advance_restore_state = hard_exit_before_prepared_history
module.ControlStoreRecovery(Path({str(kernel.workspace_root)!r})).restore_selected(
    Path({str(backup_dir)!r}),
    backup_id={backup_id!r},
    coordinator_session_id='coordinator-restore-initial-sentinel-crash',
    restored_at='2026-07-17T07:01:00+08:00',
)
"""
        crashed = subprocess.run(
            [sys.executable, "-X", "utf8", "-B", "-c", script],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
            check=False,
        )
        self.assertEqual(crashed.returncode, 91, crashed.stderr)
        sentinel = read_json(
            kernel.workspace_root / ".workflow-control-recovery.json"
        )
        state = read_json(Path(sentinel["state_path"]))
        self.assertEqual((sentinel["state_revision"], state["state_revision"]), (0, 0))
        self.assertEqual(state["state_history"], [])

        resumed = self.run_cli(
            "control-store-restore-resume",
            "--workspace-root",
            kernel.workspace_root,
            "--operation-id",
            sentinel["operation_id"],
            "--resumed-at",
            "2026-07-17T07:02:00+08:00",
        )
        self.assertEqual(resumed.returncode, 0, resumed.stdout)
        envelope = json.loads(resumed.stdout)
        final_state = read_json(Path(state["workspace_path"]) / "待删除" / "control-store-restores" / sentinel["operation_id"] / "restore-state.json")
        self.assertEqual(final_state["state"], "COMMITTED")
        self.assertEqual(
            sha256_file(Path(final_state["recovery_report_path"])),
            final_state["recovery_report_sha256"],
        )
        self.assertEqual(Path(envelope["evidence_path"]), Path(final_state["recovery_report_path"]))

    def test_restore_resume_preserves_resource_transition_evidence_after_hard_exit(self) -> None:
        def active_lease_backup(label: str):
            kernel, run_dir = self.traced_kernel(label)
            prepared = kernel.prepare_source_acquisition_task(
                run_dir,
                logical_task_key=label,
                prepared_at=TASK_START,
                required_resources=("whisper",),
            )
            claimed = kernel.claim_task(
                run_dir,
                prepared.task_id,
                coordinator_session_id="coordinator-resource-transition",
                worker_id=f"worker-{label}",
            )
            kernel.launch_admitted_task(
                claimed.attempt_id,
                claimed.claim_generation,
                ("whisper",),
                lambda _launch_token: "started",
            )
            backup_id = f"backup-{label}"
            backup_dir = kernel.workspace_root.parent / "selected-backups" / backup_id
            kernel.backup_control_store(
                backup_dir,
                backup_id=backup_id,
                coordinator_session_id=f"coordinator-backup-{label}",
                created_at="2026-07-17T07:10:00+08:00",
            )
            return kernel, backup_dir, backup_id, claimed.resource_admission.lease_id

        reference_kernel, reference_backup, reference_id, reference_lease = (
            active_lease_backup("resource-transition-reference")
        )
        reference_result = ControlStoreRecovery(
            reference_kernel.workspace_root
        ).restore_selected(
            reference_backup,
            backup_id=reference_id,
            coordinator_session_id="coordinator-restore-resource-reference",
            restored_at="2026-07-17T07:11:00+08:00",
        )
        reference_report = read_json(Path(reference_result["report_path"]))

        kernel, backup_dir, backup_id, lease_id = active_lease_backup(
            "resource-transition-hard-exit"
        )
        script = f"""
import os
from pathlib import Path
import sys
sys.path.insert(0, {str(SRC_ROOT)!r})
from video2pdf_workflow_kernel.kernel import VideoWorkflowKernel
from video2pdf_workflow_kernel.control_store_recovery import ControlStoreRecovery
original_reconcile = VideoWorkflowKernel.resource_reconcile
def hard_exit_after_resource_commit(self, *args, **kwargs):
    original_reconcile(self, *args, **kwargs)
    os._exit(92)
VideoWorkflowKernel.resource_reconcile = hard_exit_after_resource_commit
ControlStoreRecovery(Path({str(kernel.workspace_root)!r})).restore_selected(
    Path({str(backup_dir)!r}),
    backup_id={backup_id!r},
    coordinator_session_id='coordinator-restore-resource-hard-exit',
    restored_at='2026-07-17T07:12:00+08:00',
)
"""
        crashed = subprocess.run(
            [sys.executable, "-X", "utf8", "-B", "-c", script],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
            check=False,
        )
        self.assertEqual(crashed.returncode, 92, crashed.stderr)
        sentinel = read_json(kernel.workspace_root / ".workflow-control-recovery.json")
        self.assertEqual(sentinel["state"], "RECONCILING")
        resumed = self.run_cli(
            "control-store-restore-resume",
            "--workspace-root",
            kernel.workspace_root,
            "--operation-id",
            sentinel["operation_id"],
            "--resumed-at",
            "2026-07-17T07:13:00+08:00",
        )
        self.assertEqual(resumed.returncode, ControlStoreUnavailable.exit_code, resumed.stdout)
        envelope = json.loads(resumed.stdout)
        report = read_json(Path(envelope["evidence_path"]))
        actual = report["resource_recovery"]
        reference = reference_report["resource_recovery"]
        self.assertEqual(actual["lost_coordinator_session_ids"], reference["lost_coordinator_session_ids"])
        self.assertEqual(actual["lost_coordinator_session_ids"], ["coordinator-resource-transition"])
        self.assertEqual(actual["transitioned_lease_ids"], [lease_id])
        self.assertEqual(reference["transitioned_lease_ids"], [reference_lease])
        self.assertEqual(actual["unknown_lease_ids"], [lease_id])
        self.assertEqual(reference["unknown_lease_ids"], [reference_lease])
        self.assertEqual(actual["capacity_released"], reference["capacity_released"])
        self.assertEqual(actual["resource_usage"], reference["resource_usage"])

    def test_restore_resume_repairs_blocked_state_ahead_and_preserves_report_bindings(self) -> None:
        kernel, _first_run = self.traced_kernel("blocked-state-ahead")
        backup_id = "backup-blocked-state-ahead"
        backup_dir = kernel.workspace_root.parent / "selected-backups" / backup_id
        kernel.backup_control_store(
            backup_dir,
            backup_id=backup_id,
            coordinator_session_id="coordinator-backup-blocked-state-ahead",
            created_at="2026-07-17T07:20:00+08:00",
        )
        kernel.trace_source_ready(
            fixture=FIXTURE,
            task_start="2026-07-17T07:20:30+08:00",
            request_id=f"blocked-state-ahead-newer-{uuid.uuid4().hex[:8]}",
        )
        script = f"""
import os
from pathlib import Path
import sys
sys.path.insert(0, {str(SRC_ROOT)!r})
from video2pdf_workflow_kernel import control_store_recovery as module
original_write = module.write_json_atomic
def hard_exit_after_blocked_state(path, value):
    original_write(path, value)
    if Path(path).name == 'restore-state.json' and value.get('state') == 'BLOCKED':
        os._exit(93)
module.write_json_atomic = hard_exit_after_blocked_state
module.ControlStoreRecovery(Path({str(kernel.workspace_root)!r})).restore_selected(
    Path({str(backup_dir)!r}),
    backup_id={backup_id!r},
    coordinator_session_id='coordinator-restore-blocked-state-ahead',
    restored_at='2026-07-17T07:21:00+08:00',
)
"""
        crashed = subprocess.run(
            [sys.executable, "-X", "utf8", "-B", "-c", script],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
            check=False,
        )
        self.assertEqual(crashed.returncode, 93, crashed.stderr)
        sentinel = read_json(kernel.workspace_root / ".workflow-control-recovery.json")
        state = read_json(Path(sentinel["state_path"]))
        self.assertEqual((sentinel["state"], state["state"]), ("RECONCILING", "BLOCKED"))
        resumed = self.run_cli(
            "control-store-restore-resume",
            "--workspace-root",
            kernel.workspace_root,
            "--operation-id",
            sentinel["operation_id"],
            "--resumed-at",
            "2026-07-17T07:22:00+08:00",
        )
        self.assertEqual(resumed.returncode, ControlStoreUnavailable.exit_code, resumed.stdout)
        envelope = json.loads(resumed.stdout)
        final_state = read_json(Path(sentinel["state_path"]))
        self.assertEqual(Path(envelope["evidence_path"]), Path(final_state["recovery_report_path"]))
        self.assertEqual(sha256_file(Path(final_state["recovery_report_path"])), final_state["recovery_report_sha256"])
        self.assertEqual(Path(envelope["data"]["orphan_report_path"]), Path(final_state["orphan_report_path"]))
        self.assertEqual(sha256_file(Path(final_state["orphan_report_path"])), final_state["orphan_report_sha256"])

    def test_restore_resume_rejects_non_file_active_sentinel_authority(self) -> None:
        kernel, _run_dir = self.traced_kernel("non-file-active-sentinel")
        backup_id = "backup-non-file-active-sentinel"
        backup_dir = kernel.workspace_root.parent / "selected-backups" / backup_id
        kernel.backup_control_store(
            backup_dir,
            backup_id=backup_id,
            coordinator_session_id="coordinator-backup-non-file-sentinel",
            created_at="2026-07-17T07:30:00+08:00",
        )
        completed = ControlStoreRecovery(kernel.workspace_root).restore_selected(
            backup_dir,
            backup_id=backup_id,
            coordinator_session_id="coordinator-restore-non-file-sentinel",
            restored_at="2026-07-17T07:31:00+08:00",
        )
        sentinel_path = kernel.workspace_root / ".workflow-control-recovery.json"
        sentinel_path.mkdir()
        with self.assertRaisesRegex(
            ControlStoreUnavailable,
            "linked or non-file",
        ):
            ControlStoreRecovery(kernel.workspace_root).resume_restore(
                operation_id=Path(completed["operation_dir"]).name,
                resumed_at="2026-07-17T07:32:00+08:00",
            )

    def test_recovery_contract_rejects_contradictory_resource_inventory(self) -> None:
        kernel, _run_dir = self.traced_kernel("resource-inventory-contract")
        valid_path = (
            PROJECT_ROOT
            / "tests/video_workflow/fixtures/contracts/control-store-recovery-report.valid.json"
        )
        valid = read_json(valid_path)
        contradictions = []

        readable_null = json.loads(json.dumps(valid))
        readable_null["resource_recovery"]["selected_store_inventory"]["resource_usage"] = None
        contradictions.append(readable_null)

        selected_unreadable = json.loads(json.dumps(valid))
        selected_unreadable["resource_recovery"]["selected_store_inventory"].update(
            {
                "status": "unreadable_or_invalid",
                "active_claims": [],
                "nonterminal_leases": [],
                "resource_usage": None,
            }
        )
        contradictions.append(selected_unreadable)

        selected_absent = json.loads(json.dumps(valid))
        selected_absent["resource_recovery"]["selected_store_inventory"]["status"] = "absent"
        contradictions.append(selected_absent)

        absent_nonzero = json.loads(json.dumps(valid))
        absent_inventory = absent_nonzero["resource_recovery"]["quarantined_live_inventory"]
        absent_inventory["status"] = "absent"
        absent_inventory["resource_usage"]["whisper"] = 1
        contradictions.append(absent_nonzero)

        unreadable_optimistic = json.loads(json.dumps(valid))
        unreadable_inventory = unreadable_optimistic["resource_recovery"]["quarantined_live_inventory"]
        unreadable_inventory.update(
            {
                "status": "unreadable_or_invalid",
                "active_claims": [],
                "nonterminal_leases": [],
                "resource_usage": None,
            }
        )
        contradictions.append(unreadable_optimistic)

        conservative_passing = json.loads(json.dumps(valid))
        conservative_passing["resource_recovery"][
            "conservative_capacity_unresolved"
        ] = True
        contradictions.append(conservative_passing)

        for contradiction in contradictions:
            with self.subTest(status=contradiction["resource_recovery"]["quarantined_live_inventory"]["status"]):
                with self.assertRaises(ContractError):
                    kernel.contracts.validate(
                        "control-store-recovery-report",
                        contradiction,
                    )

        committed_sentinel = read_json(
            PROJECT_ROOT
            / "tests/video_workflow/fixtures/contracts/"
            "control-store-restore-sentinel.valid.json"
        )
        committed_sentinel["state"] = "COMMITTED"
        with self.assertRaises(ContractError):
            kernel.contracts.validate(
                "control-store-restore-sentinel",
                committed_sentinel,
            )

    def test_restore_busy_quiescence_failure_does_not_quarantine_or_publish(
        self,
    ) -> None:
        for entry_path in ("first", "resume"):
            with self.subTest(entry_path=entry_path):
                kernel, _run_dir = self.traced_kernel(
                    f"busy-quiescence-{entry_path}"
                )
                backup_id = f"backup-busy-quiescence-{entry_path}"
                backup_dir = (
                    kernel.workspace_root.parent
                    / "selected-backups"
                    / backup_id
                )
                kernel.backup_control_store(
                    backup_dir,
                    backup_id=backup_id,
                    coordinator_session_id=(
                        f"coordinator-backup-busy-{entry_path}"
                    ),
                    created_at="2026-07-17T07:39:00+08:00",
                )
                connection = sqlite3.connect(kernel.control_store.path)
                try:
                    connection.execute(
                        "INSERT INTO control_store_metadata(key, value) "
                        "VALUES (?, ?)",
                        (f"busy-live-{entry_path}", "must-survive-contention"),
                    )
                    connection.commit()
                    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                finally:
                    connection.close()

                live_paths = (
                    kernel.control_store.path,
                    kernel.control_store.marker_path,
                    kernel.control_store.anchor_path,
                )
                live_identity = {
                    path: (
                        path.stat().st_dev,
                        path.stat().st_ino,
                        sha256_file(path),
                    )
                    for path in live_paths
                }
                backup_manifest = read_json(backup_dir / "backup-manifest.json")
                self.assertNotEqual(
                    live_identity[kernel.control_store.path][2],
                    backup_manifest["artifacts"]["database"]["sha256"],
                )

                recovery = ControlStoreRecovery(kernel.workspace_root)
                operation_id: str | None = None
                if entry_path == "resume":
                    with self.assertRaises(recovery_module.RestoreInterruption):
                        recovery.restore_selected(
                            backup_dir,
                            backup_id=backup_id,
                            coordinator_session_id=(
                                "coordinator-prepare-busy-resume"
                            ),
                            restored_at="2026-07-17T07:40:00+08:00",
                            fault_point="after_prepared",
                        )
                    operation_id = read_json(
                        kernel.workspace_root
                        / ".workflow-control-recovery.json"
                    )["operation_id"]

                original_connect = sqlite3.connect

                def busy_quiescence_connect(
                    database: object,
                    *args: object,
                    **kwargs: object,
                ):
                    if (
                        "mode=rw" in str(database)
                        and kernel.control_store.path.as_posix()
                        in str(database)
                        and kwargs.get("timeout") == 20
                    ):
                        raise sqlite3.OperationalError("database is locked")
                    return original_connect(database, *args, **kwargs)

                with mock.patch.object(
                    recovery_module.sqlite3,
                    "connect",
                    new=busy_quiescence_connect,
                ):
                    with self.assertRaisesRegex(
                        ControlStoreUnavailable,
                        "quiescence",
                    ) as blocked:
                        if entry_path == "first":
                            recovery.restore_selected(
                                backup_dir,
                                backup_id=backup_id,
                                coordinator_session_id=(
                                    "coordinator-first-busy-quiescence"
                                ),
                                restored_at="2026-07-17T07:40:00+08:00",
                            )
                        else:
                            recovery.resume_restore(
                                operation_id=str(operation_id),
                                resumed_at="2026-07-17T07:41:00+08:00",
                            )
                self.assertEqual(blocked.exception.exit_code, 50)

                sentinel_path = (
                    kernel.workspace_root / ".workflow-control-recovery.json"
                )
                self.assertTrue(sentinel_path.is_file())
                sentinel = read_json(sentinel_path)
                operation_id = str(sentinel["operation_id"])
                operation_dir = Path(str(sentinel["state_path"])).parent
                state_record = read_json(operation_dir / "restore-state.json")
                self.assertEqual(state_record["state"], "PREPARED")
                self.assertEqual(
                    recovery.diagnostic_status()["state"],
                    "PREPARED",
                )
                for path, expected_identity in live_identity.items():
                    self.assertTrue(path.is_file())
                    self.assertEqual(
                        (
                            path.stat().st_dev,
                            path.stat().st_ino,
                            sha256_file(path),
                        ),
                        expected_identity,
                    )
                self.assertFalse(
                    (operation_dir / "prior" / ".workflow-control").exists()
                )
                self.assertFalse((operation_dir / "prior" / "anchor.json").exists())
                self.assertTrue(
                    (operation_dir / "staging" / "candidate" / "control.sqlite3").is_file()
                )
                self.assertFalse(
                    (operation_dir / "staging" / "published-control").exists()
                )

                resumed = recovery.resume_restore(
                    operation_id=operation_id,
                    resumed_at="2026-07-17T07:42:00+08:00",
                )
                self.assertEqual(
                    resumed["classification"],
                    "control_store_restore_complete",
                )
                self.assertTrue((operation_dir / "sentinel.json").is_file())

    def test_restore_requires_quiescence_and_explicit_selected_backup(self) -> None:
        kernel, _run_dir = self.traced_kernel("restore-quiescence")
        backup_dir = kernel.workspace_root.parent / "selected-backups" / "backup-q"
        kernel.backup_control_store(
            backup_dir,
            backup_id="backup-q",
            coordinator_session_id="coordinator-backup-q",
            created_at="2026-07-17T03:10:00+08:00",
        )
        with self.assertRaises(ControlStoreUnavailable):
            ControlStoreRecovery(kernel.workspace_root).restore_selected(
                backup_dir,
                backup_id="another-backup",
                coordinator_session_id="coordinator-restore-q",
                restored_at="2026-07-17T03:11:00+08:00",
            )
        with self.assertRaisesRegex(
            ContractError,
            "outside the governed workspace",
        ):
            ControlStoreRecovery(kernel.workspace_root).restore_selected(
                kernel.workspace_root / "embedded-backup",
                backup_id="embedded-backup",
                coordinator_session_id="coordinator-restore-embedded",
                restored_at="2026-07-17T03:11:30+08:00",
            )
        self.assertFalse((kernel.workspace_root / ".workflow-control-recovery.json").exists())

        writer_ready = threading.Event()
        release_writer = threading.Event()
        writer_closed = threading.Event()
        writer_errors: list[BaseException] = []

        def writer() -> None:
            connection = sqlite3.connect(
                kernel.control_store.path,
                isolation_level=None,
                timeout=20,
            )
            try:
                connection.execute("PRAGMA busy_timeout=20000")
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT INTO control_store_metadata(key, value) VALUES (?, ?)",
                    ("restore-quiescence-writer", "committed-before-restore"),
                )
                writer_ready.set()
                if not release_writer.wait(20):
                    raise AssertionError("restore quiescence writer was not released")
                connection.execute("COMMIT")
            except BaseException as exc:
                writer_errors.append(exc)
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
            finally:
                connection.close()
                writer_closed.set()

        writer_thread = threading.Thread(target=writer, daemon=True)
        writer_thread.start()
        self.assertTrue(writer_ready.wait(10))

        archive_reached = threading.Event()
        allow_archive = threading.Event()
        original_replace = recovery_module.os.replace

        def observed_replace(source: object, destination: object) -> None:
            if (
                Path(source).resolve()
                == (kernel.workspace_root / ".workflow-control-recovery.json").resolve()
                and Path(destination).name == "sentinel.json"
                and "control-store-restores" in Path(destination).parts
            ):
                archive_reached.set()
                if not allow_archive.wait(20):
                    raise AssertionError("sentinel archive test window was not released")
            original_replace(source, destination)

        restore_result: dict[str, object] = {}
        restore_errors: list[BaseException] = []

        def restore() -> None:
            try:
                restore_result.update(
                    ControlStoreRecovery(kernel.workspace_root).restore_selected(
                        backup_dir,
                        backup_id="backup-q",
                        coordinator_session_id="coordinator-restore-q",
                        restored_at="2026-07-17T03:12:00+08:00",
                    )
                )
            except BaseException as exc:
                restore_errors.append(exc)

        with mock.patch.object(recovery_module.os, "replace", side_effect=observed_replace):
            restore_thread = threading.Thread(target=restore, daemon=True)
            restore_thread.start()
            sentinel = kernel.workspace_root / ".workflow-control-recovery.json"
            deadline = time.monotonic() + 15
            while not sentinel.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(sentinel.exists())
            self.assertTrue(restore_thread.is_alive())
            release_writer.set()
            self.assertTrue(writer_closed.wait(10))
            self.assertTrue(archive_reached.wait(30))
            report = read_json(
                kernel.workspace_root
                / ".workflow-control"
                / "control_store_recovery_report.json"
            )
            self.assertEqual(report["final_global_status"], "passed")
            self.assertTrue(report["unblock_requires_sentinel_absent"])
            self.assertTrue(sentinel.exists())
            still_blocked = VideoWorkflowKernel(kernel.workspace_root)
            with self.assertRaises(ControlStoreUnavailable):
                still_blocked.trace_source_ready(
                    fixture=FIXTURE,
                    task_start="2026-07-17T01:02:08+08:00",
                    request_id=f"sentinel-window-{uuid.uuid4().hex[:8]}",
                )
            allow_archive.set()
            restore_thread.join(30)

        writer_thread.join(10)
        self.assertEqual(writer_errors, [])
        self.assertEqual(restore_errors, [])
        self.assertEqual(
            restore_result["classification"],
            "control_store_restore_complete",
        )
        self.assertFalse((kernel.workspace_root / ".workflow-control-recovery.json").exists())
        self.assertEqual(VideoWorkflowKernel(kernel.workspace_root).control_store.check().status, "ok")


if __name__ == "__main__":
    unittest.main()
