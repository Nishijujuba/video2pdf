from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3
import sys
import unittest
import uuid


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from video2pdf_workflow_kernel.errors import ControlStoreUnavailable  # noqa: E402
from video2pdf_workflow_kernel.kernel import VideoWorkflowKernel  # noqa: E402
from video2pdf_workflow_kernel.utils import canonical_json_bytes  # noqa: E402


FIXTURE = PROJECT_ROOT / "tests/video_workflow/fixtures/source-ready-tracer"
TEST_RUNS = PROJECT_ROOT / "待删除/kernel-test-runs"
TASK_START = "2026-07-17T12:00:00+08:00"
RESOURCE_V8_TABLES = (
    "resource_lease_resources",
    "resource_leases",
    "resource_control_events",
    "resource_circuit_breakers",
    "resource_fairness_cursors",
    "resource_queue_entries",
    "resource_sequences",
    "resource_configurations",
)


def trusted_provider_verifier(**identity: object) -> str:
    return f"provider-proof://integrity/{identity['terminal_result_id']}"


class ResourceControlStoreIntegrityTests(unittest.TestCase):
    def initialized_workspace(self, label: str) -> tuple[Path, VideoWorkflowKernel]:
        workspace = TEST_RUNS / f"s3m-{label}-{uuid.uuid4().hex[:10]}" / "workspace"
        workspace.mkdir(parents=True)
        kernel = VideoWorkflowKernel(
            workspace,
            resource_provider_verifiers={
                "integrity-provider": trusted_provider_verifier,
            },
        )
        kernel.trace_source_ready(
            fixture=FIXTURE,
            task_start=TASK_START,
            request_id=f"m-{uuid.uuid4().hex[:8]}",
        )
        return workspace, kernel

    def admitted_claim(self, label: str):
        workspace = (
            TEST_RUNS / f"s3i-{uuid.uuid4().hex[:10]}" / "workspace"
        )
        workspace.mkdir(parents=True)
        kernel = VideoWorkflowKernel(
            workspace,
            resource_provider_verifiers={
                "integrity-provider": trusted_provider_verifier,
            },
        )
        traced = kernel.trace_source_ready(
            fixture=FIXTURE,
            task_start=TASK_START,
            request_id=f"i-{uuid.uuid4().hex[:8]}",
        )
        prepared = kernel.prepare_source_acquisition_task(
            traced.run_dir,
            logical_task_key="integrity-task",
            prepared_at=TASK_START,
            required_resources=("whisper",),
        )
        claimed = kernel.claim_task(
            traced.run_dir,
            prepared.task_id,
            coordinator_session_id=f"coordinator-{label}",
            worker_id=f"worker-{label}",
        )
        self.assertEqual(claimed.resource_admission.queue_state, "admitted")
        return workspace, kernel, claimed

    def admitted_workspace(self, label: str) -> tuple[Path, VideoWorkflowKernel]:
        workspace, kernel, _ = self.admitted_claim(label)
        return workspace, kernel

    def active_reservation_workspace(
        self, label: str
    ) -> tuple[Path, VideoWorkflowKernel, object]:
        workspace, kernel = self.initialized_workspace(label)
        kernel.activate_resource_configuration(
            {
                "schema_name": "resource-admission-configuration",
                "schema_version": "1.0.0",
                "kernel_version": "2.0.0",
                "configuration_id": f"resource-reservation-{label}",
                "configuration_version": 2,
                "bypass_threshold": 1,
                "resources": [
                    {
                        "resource_class": resource_class,
                        "capacity": 2 if resource_class == "codex_semantic" else 1,
                    }
                    for resource_class in (
                        "bilibili_download",
                        "youtube_download",
                        "whisper",
                        "codex_semantic",
                        "latex",
                        "pdf_render",
                        "visual_acceptance",
                    )
                ],
            }
        )

        def claim(
            logical_key: str,
            resources: tuple[str, ...],
            batch_id: str,
        ):
            traced = kernel.trace_source_ready(
                fixture=FIXTURE,
                task_start=TASK_START,
                request_id=f"r-{uuid.uuid4().hex[:8]}",
            )
            prepared = kernel.prepare_source_acquisition_task(
                traced.run_dir,
                logical_task_key=logical_key,
                prepared_at=TASK_START,
                required_resources=resources,
                batch_id=batch_id,
            )
            return kernel.claim_task(
                traced.run_dir,
                prepared.task_id,
                coordinator_session_id=f"coordinator-{logical_key}",
                worker_id=f"worker-{logical_key}",
            )

        claim("reservation-holder", ("whisper",), "00-holder")
        reserved = claim(
            "reservation-target",
            ("codex_semantic", "whisper"),
            "aa-reserved",
        )
        claim("reservation-bypass", ("codex_semantic",), "zz-bypass")
        self.assertEqual(
            kernel.resource_status(
                reserved.task_id, reserved.attempt_id
            ).reservation_state,
            "active",
        )
        return workspace, kernel, reserved

    def test_missing_lease_resource_or_required_index_fails_closed(self) -> None:
        for tamper in ("lease-resource", "queue-index"):
            with self.subTest(tamper=tamper):
                workspace, _ = self.admitted_workspace(tamper)
                database = workspace / ".workflow-control/control.sqlite3"
                with sqlite3.connect(database) as connection:
                    if tamper == "lease-resource":
                        connection.execute("DELETE FROM resource_lease_resources")
                    else:
                        connection.execute(
                            "DROP INDEX resource_queue_by_state_enqueue"
                        )
                with self.assertRaises(ControlStoreUnavailable):
                    VideoWorkflowKernel(workspace)

    def test_configuration_and_queue_lease_tampering_fails_closed(self) -> None:
        for tamper in ("retired-config", "queue-fingerprint", "lease-request"):
            with self.subTest(tamper=tamper):
                workspace, kernel = self.admitted_workspace(tamper)
                if tamper == "retired-config":
                    kernel.activate_resource_configuration(
                        {
                            "schema_name": "resource-admission-configuration",
                            "schema_version": "1.0.0",
                            "kernel_version": "2.0.0",
                            "configuration_id": "resource-integrity-v2",
                            "configuration_version": 2,
                            "bypass_threshold": 8,
                            "resources": [
                                {"resource_class": item, "capacity": 1}
                                for item in (
                                    "bilibili_download",
                                    "youtube_download",
                                    "whisper",
                                    "codex_semantic",
                                    "latex",
                                    "pdf_render",
                                    "visual_acceptance",
                                )
                            ],
                        }
                    )
                database = workspace / ".workflow-control/control.sqlite3"
                with sqlite3.connect(database) as connection:
                    if tamper == "retired-config":
                        connection.execute(
                            "UPDATE resource_configurations SET configuration_sha256=? "
                            "WHERE state='RETIRED'",
                            ("0" * 64,),
                        )
                    elif tamper == "queue-fingerprint":
                        connection.execute(
                            "UPDATE resource_queue_entries SET "
                            "enqueue_configuration_sha256=?",
                            ("0" * 64,),
                        )
                    else:
                        connection.execute(
                            "UPDATE resource_queue_entries SET "
                            "required_resources_json='[\"latex\"]'"
                        )
                with self.assertRaises(ControlStoreUnavailable):
                    VideoWorkflowKernel(workspace)

    def test_queue_and_reservation_lifecycle_tampering_fails_closed(self) -> None:
        workspace, _ = self.admitted_workspace("cancelled-state")
        database = workspace / ".workflow-control/control.sqlite3"
        with sqlite3.connect(database) as connection:
            connection.execute("PRAGMA ignore_check_constraints=ON")
            connection.execute(
                "UPDATE resource_queue_entries SET state='CANCELLED'"
            )
        with self.assertRaisesRegex(
            ControlStoreUnavailable,
            "Resource Queue state is unsupported",
        ):
            VideoWorkflowKernel(workspace)

        workspace, _, reserved = self.active_reservation_workspace(
            "terminated-active"
        )
        database = workspace / ".workflow-control/control.sqlite3"
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE resource_queue_entries SET reservation_state='TERMINATED' "
                "WHERE attempt_id=?",
                (reserved.attempt_id,),
            )
        with self.assertRaisesRegex(
            ControlStoreUnavailable,
            "Resource reservation lifecycle is invalid",
        ):
            VideoWorkflowKernel(workspace)

    def test_queue_and_normalized_lease_double_tamper_breaks_request_binding(self) -> None:
        workspace, _ = self.admitted_workspace("double-request")
        database = workspace / ".workflow-control/control.sqlite3"
        tampered_request_json = canonical_json_bytes(["latex"]).decode("utf-8")
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE resource_queue_entries SET required_resources_json=?",
                (tampered_request_json,),
            )
            connection.execute(
                "UPDATE resource_lease_resources SET resource_class='latex'"
            )
        with self.assertRaisesRegex(
            ControlStoreUnavailable,
            "immutable request binding",
        ):
            VideoWorkflowKernel(workspace)

    def test_real_v7_store_migrates_atomically_to_complete_v8(self) -> None:
        workspace, kernel = self.initialized_workspace("real-v7")
        database = kernel.control_store.path
        with sqlite3.connect(database) as connection:
            connection.execute("PRAGMA foreign_keys=OFF")
            for table in RESOURCE_V8_TABLES:
                connection.execute(f"DROP TABLE IF EXISTS {table}")
            connection.execute("DELETE FROM schema_migrations WHERE version=8")

        migrated = VideoWorkflowKernel(workspace)
        self.assertEqual(migrated.control_store.check().schema_version, 8)
        with sqlite3.connect(database) as connection:
            versions = [
                row[0]
                for row in connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                ).fetchall()
            ]
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name LIKE 'resource_%'"
                ).fetchall()
            }
            active_configurations = connection.execute(
                "SELECT COUNT(*) FROM resource_configurations WHERE state='ACTIVE'"
            ).fetchone()[0]
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO resource_configurations("
                    "configuration_id, configuration_version, schema_version, "
                    "configuration_sha256, configuration_json, state) "
                    "VALUES ('duplicate-version', 1, '1.0.0', ?, '{}', 'RETIRED')",
                    ("f" * 64,),
                )
        self.assertEqual(versions, list(range(1, 9)))
        self.assertEqual(tables, set(RESOURCE_V8_TABLES))
        self.assertEqual(active_configurations, 1)

    def test_partial_v8_resource_migration_fails_closed_without_repairing_it(
        self,
    ) -> None:
        workspace, kernel = self.initialized_workspace("partial-v8")
        database = kernel.control_store.path
        with sqlite3.connect(database) as connection:
            connection.execute("PRAGMA foreign_keys=OFF")
            connection.execute("DROP TABLE resource_control_events")
            connection.execute("DELETE FROM schema_migrations WHERE version=8")

        with self.assertRaisesRegex(
            ControlStoreUnavailable,
            "partial v8 Resource Admission migration",
        ):
            VideoWorkflowKernel(workspace)
        with sqlite3.connect(database) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT MAX(version) FROM schema_migrations"
                ).fetchone()[0],
                7,
            )
            self.assertIsNone(
                connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name='resource_control_events'"
                ).fetchone()
            )

    def test_resource_schema_and_sequence_authorities_are_closed_and_exact(self) -> None:
        expectations = {
            "extra-object": "unsupported resource schema objects",
            "extra-sequence": "sequence authority names are not a closed set",
            "sequence-drift": "sequence disagrees with durable enqueue authority",
        }
        for tamper, expected in expectations.items():
            with self.subTest(tamper=tamper):
                workspace, _ = self.admitted_workspace(tamper)
                database = workspace / ".workflow-control/control.sqlite3"
                with sqlite3.connect(database) as connection:
                    if tamper == "extra-object":
                        connection.execute("CREATE TABLE resource_shadow(value TEXT)")
                    elif tamper == "extra-sequence":
                        connection.execute(
                            "INSERT INTO resource_sequences(sequence_name, value) "
                            "VALUES ('shadow', 0)"
                        )
                    else:
                        connection.execute(
                            "UPDATE resource_sequences SET value=value+1 "
                            "WHERE sequence_name='enqueue'"
                        )
                with self.assertRaisesRegex(ControlStoreUnavailable, expected):
                    VideoWorkflowKernel(workspace)

    def test_append_only_resource_sequence_gaps_fail_closed(self) -> None:
        for sequence_name in ("enqueue", "admission", "reservation", "event"):
            with self.subTest(sequence_name=sequence_name):
                if sequence_name == "reservation":
                    workspace, _, _ = self.active_reservation_workspace(
                        "reservation-gap"
                    )
                else:
                    workspace, _ = self.admitted_workspace(
                        f"{sequence_name}-gap"
                    )
                database = workspace / ".workflow-control/control.sqlite3"
                with sqlite3.connect(database) as connection:
                    if sequence_name == "enqueue":
                        connection.execute(
                            "UPDATE resource_queue_entries SET enqueue_seq=2"
                        )
                        connection.execute(
                            "UPDATE resource_sequences SET value=2 "
                            "WHERE sequence_name='enqueue'"
                        )
                    elif sequence_name == "admission":
                        connection.execute(
                            "UPDATE resource_queue_entries SET admitted_seq=2"
                        )
                        connection.execute(
                            "UPDATE resource_leases SET admitted_seq=2"
                        )
                        connection.execute(
                            "UPDATE resource_sequences SET value=2 "
                            "WHERE sequence_name='admission'"
                        )
                    elif sequence_name == "reservation":
                        connection.execute(
                            "UPDATE resource_queue_entries SET reservation_seq=2 "
                            "WHERE reservation_state='ACTIVE'"
                        )
                        connection.execute(
                            "UPDATE resource_sequences SET value=2 "
                            "WHERE sequence_name='reservation'"
                        )
                    else:
                        connection.execute(
                            "UPDATE resource_control_events SET event_seq=event_seq+100"
                        )
                        connection.execute(
                            "UPDATE resource_control_events SET event_seq=event_seq-99"
                        )
                        connection.execute(
                            "UPDATE resource_sequences SET value=value+1 "
                            "WHERE sequence_name='event'"
                        )
                with self.assertRaisesRegex(
                    ControlStoreUnavailable,
                    f"Resource {sequence_name} sequence identity is not append-only",
                ):
                    VideoWorkflowKernel(workspace)

        workspace, _ = self.admitted_workspace("enqueue-int64-max")
        database = workspace / ".workflow-control/control.sqlite3"
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE resource_sequences SET value=9223372036854775807 "
                "WHERE sequence_name='enqueue'"
            )
        with self.assertRaisesRegex(
            ControlStoreUnavailable,
            "Resource sequence disagrees with durable enqueue authority",
        ):
            VideoWorkflowKernel(workspace)

    def test_lease_launch_and_terminal_state_matrix_rejects_double_tamper(self) -> None:
        expectations = {
            "state": "active Resource Lease lacks completed launch authority",
            "launch-set": (
                "launch Resource set differs from normalized authority"
            ),
            "terminal-evidence": "Resource Lease terminal evidence is invalid",
        }
        for tamper, expected in expectations.items():
            with self.subTest(tamper=tamper):
                workspace, kernel, claimed = self.admitted_claim(tamper)
                launch_tokens: list[str] = []
                if tamper != "state":
                    kernel.launch_admitted_task(
                        claimed.attempt_id,
                        claimed.claim_generation,
                        ("whisper",),
                        lambda token: launch_tokens.append(token) or "started",
                    )
                if tamper == "terminal-evidence":
                    kernel.release_resource_lease(
                        claimed.attempt_id,
                        claimed.claim_generation,
                        launch_tokens[0],
                        terminal_evidence={
                            "evidence_class": "provider_terminal_result",
                            "provider": "integrity-provider",
                            "terminal_result_id": "integrity-terminal-result",
                            "declared_outcome": "succeeded",
                            "observed_at": "2026-07-17T12:30:00+08:00",
                        },
                    )
                database = workspace / ".workflow-control/control.sqlite3"
                with sqlite3.connect(database) as connection:
                    if tamper == "state":
                        connection.execute(
                            "UPDATE resource_leases SET state='active'"
                        )
                    elif tamper == "launch-set":
                        launch_json = canonical_json_bytes(["latex"]).decode("utf-8")
                        connection.execute(
                            "UPDATE resource_leases SET "
                            "launch_required_resources_json=?, "
                            "launch_required_resources_sha256=?",
                            (
                                launch_json,
                                hashlib.sha256(
                                    launch_json.encode("utf-8")
                                ).hexdigest(),
                            ),
                        )
                    else:
                        terminal_json = canonical_json_bytes(
                            {"forged": "terminal-evidence"}
                        ).decode("utf-8")
                        connection.execute(
                            "UPDATE resource_leases SET terminal_evidence_json=?, "
                            "terminal_evidence_sha256=?",
                            (
                                terminal_json,
                                hashlib.sha256(
                                    terminal_json.encode("utf-8")
                                ).hexdigest(),
                            ),
                        )
                with self.assertRaisesRegex(ControlStoreUnavailable, expected):
                    VideoWorkflowKernel(workspace)

    def test_event_cursor_and_breaker_identity_tampering_fails_closed(self) -> None:
        expectations = {
            "event-canonical": "payload JSON is not canonical",
            "event-reference": "Queue and Lease identities disagree",
            "cursor": "GROUP fairness cursor identity is invalid",
            "breaker": "Circuit Breaker durable identity is invalid",
        }
        for tamper, expected in expectations.items():
            with self.subTest(tamper=tamper):
                workspace, kernel = self.admitted_workspace(tamper)
                if tamper == "breaker":
                    kernel.set_resource_circuit_breaker(
                        "whisper",
                        state="open",
                        reason="integrity breaker",
                    )
                database = workspace / ".workflow-control/control.sqlite3"
                with sqlite3.connect(database) as connection:
                    if tamper == "event-canonical":
                        connection.execute(
                            "UPDATE resource_control_events SET payload_json='{ }' "
                            "WHERE event_kind='enqueued'"
                        )
                    elif tamper == "event-reference":
                        connection.execute(
                            "UPDATE resource_control_events SET lease_id=NULL "
                            "WHERE event_kind='admitted'"
                        )
                    elif tamper == "cursor":
                        connection.execute(
                            "UPDATE resource_fairness_cursors SET scope_id='forged' "
                            "WHERE level='GROUP'"
                        )
                    else:
                        connection.execute(
                            "UPDATE resource_circuit_breakers SET "
                            "breaker_key='resource:latex'"
                        )
                with self.assertRaisesRegex(ControlStoreUnavailable, expected):
                    VideoWorkflowKernel(workspace)


if __name__ == "__main__":
    unittest.main()
