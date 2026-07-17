from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading
import unittest
import uuid


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from video2pdf_workflow_kernel.errors import (  # noqa: E402
    ContractError,
    ControlStoreUnavailable,
    KernelConflict,
    ResourceAdmissionBlocked,
    ResourceAdmissionFault,
)
from video2pdf_workflow_kernel.kernel import VideoWorkflowKernel  # noqa: E402
from video2pdf_workflow_kernel.utils import (  # noqa: E402
    canonical_json_bytes,
    read_json,
    write_json_atomic,
)


FIXTURE = PROJECT_ROOT / "tests/video_workflow/fixtures/source-ready-tracer"
TEST_RUNS = PROJECT_ROOT / "待删除/kernel-test-runs"
TASK_START = "2026-07-17T10:00:00+08:00"
CLI = PROJECT_ROOT / "scripts/video_workflow.py"


class RecordingLauncher:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, launch_token: str) -> str:
        self.calls.append(launch_token)
        return "started"


class ProcessIdentityLauncher(RecordingLauncher):
    def __call__(self, launch_token: str) -> dict:
        self.calls.append(launch_token)
        return {
            "status": "started",
            "process_identity": {
                "pid": 4321,
                "process_creation_identity": "process-created-2026-07-17T11:10:00Z",
                "launch_token": launch_token,
            },
        }


class RaisingLauncher(RecordingLauncher):
    def __call__(self, launch_token: str) -> str:
        self.calls.append(launch_token)
        raise RuntimeError("launcher outcome is unavailable")


class InvalidProcessIdentityLauncher(RecordingLauncher):
    def __call__(self, launch_token: str) -> dict:
        self.calls.append(launch_token)
        return {
            "status": "started",
            "process_identity": {
                "pid": 0,
                "process_creation_identity": "invalid-process-identity",
                "launch_token": launch_token,
            },
        }


class BlockingProcessIdentityLauncher(RecordingLauncher):
    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def __call__(self, launch_token: str) -> dict:
        self.calls.append(launch_token)
        self.entered.set()
        if not self.release.wait(timeout=20):
            raise RuntimeError("test launcher release barrier timed out")
        return {
            "status": "started",
            "process_identity": {
                "pid": 9876,
                "process_creation_identity": "process-created-claim-fence-race",
                "launch_token": launch_token,
            },
        }


class TrustedProviderVerifier:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, **identity: object) -> str:
        self.calls.append(identity)
        return f"provider-proof://{identity['provider']}/{identity['terminal_result_id']}"


class BoundProviderVerifier:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.terminal_results: dict[str, dict[str, object]] = {}

    def __call__(self, **identity: object) -> str:
        self.calls.append(identity)
        terminal_result_id = str(identity["terminal_result_id"])
        expected = self.terminal_results.get(terminal_result_id)
        observed = {
            "lease_id": identity["lease_id"],
            "attempt_id": identity["attempt_id"],
            "launch_token": identity["launch_token"],
            "declared_outcome": identity["declared_outcome"],
        }
        if expected != observed:
            raise RuntimeError("provider terminal result identity mismatch")
        return f"provider-proof://bound-provider/{terminal_result_id}"


class TrustedProcessInspector:
    def __init__(self, *, matching_process_absent: bool) -> None:
        self.matching_process_absent = matching_process_absent
        self.calls: list[dict] = []

    def __call__(self, **identity: object) -> str | None:
        self.calls.append(identity)
        if not self.matching_process_absent:
            return None
        return (
            "process-inspection://absent/"
            f"{identity['pid']}/{identity['process_creation_identity']}"
        )


class OneShotProviderVerifier:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, **identity: object) -> str:
        self.calls.append(identity)
        if len(self.calls) > 1:
            raise RuntimeError("provider verifier is no longer available")
        return (
            "provider-proof://one-shot/"
            f"{identity['provider']}/{identity['terminal_result_id']}"
        )


class OneShotProcessInspector:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, **identity: object) -> str:
        self.calls.append(identity)
        if len(self.calls) > 1:
            raise RuntimeError("local process inspector is no longer available")
        return (
            "process-inspection://one-shot/absent/"
            f"{identity['pid']}/{identity['process_creation_identity']}"
        )


class ResourceAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = TEST_RUNS / f"slice3-{uuid.uuid4().hex}" / "workspace"
        self.workspace.mkdir(parents=True)
        self.kernel = VideoWorkflowKernel(self.workspace)

    def prepare_and_claim(
        self,
        label: str,
        resources: tuple[str, ...],
        *,
        batch_id: str | None = None,
    ):
        traced = self.kernel.trace_source_ready(
            fixture=FIXTURE,
            task_start=TASK_START,
            request_id=f"{label}-{uuid.uuid4().hex}",
        )
        prepared = self.kernel.prepare_source_acquisition_task(
            traced.run_dir,
            logical_task_key=f"source-acquisition-{label}",
            prepared_at=TASK_START,
            required_resources=resources,
            batch_id=batch_id,
        )
        envelope = read_json(prepared.envelope_path)
        self.assertEqual(envelope["schema_version"], "2.0.0")
        self.assertEqual(envelope["resource_request"], list(resources))
        self.assertEqual(
            envelope["fairness_group_id"], batch_id or prepared.run_id
        )
        self.assertEqual(envelope.get("batch_id"), batch_id)
        claimed = self.kernel.claim_task(
            traced.run_dir,
            prepared.task_id,
            coordinator_session_id=f"coordinator-{label}",
            worker_id=f"worker-{label}",
        )
        return prepared, claimed

    def claim_prepared(self, prepared, label: str):
        return self.kernel.claim_task(
            prepared.run_dir,
            prepared.task_id,
            coordinator_session_id=f"coordinator-{label}",
            worker_id=f"worker-{label}",
        )

    def prepare_only(
        self,
        label: str,
        resources: tuple[str, ...],
        *,
        batch_id: str | None = None,
    ):
        traced = self.kernel.trace_source_ready(
            fixture=FIXTURE,
            task_start=TASK_START,
            request_id=f"{label}-{uuid.uuid4().hex}",
        )
        return self.kernel.prepare_source_acquisition_task(
            traced.run_dir,
            logical_task_key=f"source-acquisition-{label}",
            prepared_at=TASK_START,
            required_resources=resources,
            batch_id=batch_id,
        )

    def release_started(self, claimed, resource_class: str) -> None:
        launcher = RecordingLauncher()
        self.kernel.launch_admitted_task(
            claimed.attempt_id,
            claimed.claim_generation,
            (resource_class,),
            launcher,
        )
        self.trusted_provider_kernel().release_resource_lease(
            claimed.attempt_id,
            claimed.claim_generation,
            launcher.calls[0],
            terminal_evidence=self.provider_terminal_evidence(
                f"test-result-{claimed.attempt_id}",
                observed_at="2026-07-17T10:30:00+08:00",
            ),
        )

    def trusted_provider_kernel(self) -> VideoWorkflowKernel:
        return VideoWorkflowKernel(
            self.workspace,
            resource_provider_verifiers={
                "test-provider": TrustedProviderVerifier(),
            },
        )

    @staticmethod
    def provider_terminal_evidence(
        terminal_result_id: str,
        *,
        observed_at: str,
    ) -> dict[str, str]:
        return {
            "evidence_class": "provider_terminal_result",
            "provider": "test-provider",
            "terminal_result_id": terminal_result_id,
            "declared_outcome": "succeeded",
            "observed_at": observed_at,
        }

    @staticmethod
    def configuration(
        *,
        version: int,
        capacities: dict[str, int],
        bypass_threshold: int = 8,
    ) -> dict:
        resource_classes = (
            "bilibili_download",
            "youtube_download",
            "whisper",
            "codex_semantic",
            "latex",
            "pdf_render",
            "visual_acceptance",
        )
        return {
            "schema_name": "resource-admission-configuration",
            "schema_version": "1.0.0",
            "kernel_version": "2.0.0",
            "configuration_id": f"resource-admission-test-v{version}",
            "configuration_version": version,
            "bypass_threshold": bypass_threshold,
            "resources": [
                {
                    "resource_class": resource_class,
                    "capacity": capacities.get(resource_class, 1),
                }
                for resource_class in resource_classes
            ],
        }
    def test_multi_resource_request_is_atomic_and_launch_requires_admission(self) -> None:
        _, whisper = self.prepare_and_claim("whisper", ("whisper",))
        _, combined = self.prepare_and_claim(
            "combined", ("codex_semantic", "whisper")
        )
        _, codex = self.prepare_and_claim("codex", ("codex_semantic",))

        self.assertEqual(whisper.resource_admission.queue_state, "admitted")
        self.assertEqual(combined.resource_admission.queue_state, "queued")
        self.assertIsNone(combined.resource_admission.lease_id)
        self.assertFalse(combined.resource_admission.launch_eligible)
        self.assertEqual(codex.resource_admission.queue_state, "admitted")

        blocked_launcher = RecordingLauncher()
        with self.assertRaises(ResourceAdmissionBlocked):
            self.kernel.launch_admitted_task(
                combined.attempt_id,
                combined.claim_generation,
                ("codex_semantic", "whisper"),
                blocked_launcher,
            )
        self.assertEqual(blocked_launcher.calls, [])

        admitted_launcher = RecordingLauncher()
        result = self.kernel.launch_admitted_task(
            codex.attempt_id,
            codex.claim_generation,
            ("codex_semantic",),
            admitted_launcher,
        )
        self.assertEqual(result, "started")
        self.assertEqual(len(admitted_launcher.calls), 1)

    def test_reclaim_invalidates_queued_attempt_and_preserves_admitted_lease(self) -> None:
        _, holder = self.prepare_and_claim("holder", ("whisper",))
        _, queued = self.prepare_and_claim("queued", ("whisper",))

        replacement = self.kernel.reclaim_task(
            queued.run_dir,
            task_id=queued.task_id,
            expected_attempt_id=queued.attempt_id,
            expected_claim_generation=queued.claim_generation,
            coordinator_session_id="coordinator-queued-replacement",
            worker_id="worker-queued-replacement",
            reason="worker heartbeat expired",
        )

        old_status = self.kernel.resource_status(queued.task_id, queued.attempt_id)
        self.assertEqual(old_status.queue_state, "invalidated")
        self.assertIsNone(old_status.lease_id)
        self.assertEqual(replacement.resource_admission.queue_state, "queued")
        self.assertEqual(holder.resource_admission.queue_state, "admitted")

        _, admitted = self.prepare_and_claim("admitted-reclaim", ("latex",))
        prior_lease_id = admitted.resource_admission.lease_id
        admitted_replacement = self.kernel.reclaim_task(
            admitted.run_dir,
            task_id=admitted.task_id,
            expected_attempt_id=admitted.attempt_id,
            expected_claim_generation=admitted.claim_generation,
            coordinator_session_id="coordinator-admitted-replacement",
            worker_id="worker-admitted-replacement",
            reason="worker heartbeat expired",
        )

        preserved = self.kernel.resource_status(admitted.task_id, admitted.attempt_id)
        self.assertEqual(preserved.queue_state, "admitted")
        self.assertEqual(preserved.lease_id, prior_lease_id)
        self.assertEqual(preserved.lease_state, "starting")
        self.assertEqual(preserved.launch_authorization_state, "AVAILABLE")
        self.assertFalse(preserved.launch_eligible)
        self.assertEqual(admitted_replacement.resource_admission.queue_state, "queued")
        stale_launcher = RecordingLauncher()
        with self.assertRaises(KernelConflict):
            self.kernel.launch_admitted_task(
                admitted.attempt_id,
                admitted.claim_generation,
                ("latex",),
                stale_launcher,
            )
        self.assertEqual(stale_launcher.calls, [])

    def test_reclaim_advances_claim_and_queues_replacement_when_quota_is_zero(
        self,
    ) -> None:
        _, admitted = self.prepare_and_claim("zero-quota-reclaim", ("whisper",))
        prior_lease_id = admitted.resource_admission.lease_id
        self.kernel.activate_resource_configuration(
            self.configuration(version=2, capacities={"whisper": 0})
        )

        replacement = self.kernel.reclaim_task(
            admitted.run_dir,
            task_id=admitted.task_id,
            expected_attempt_id=admitted.attempt_id,
            expected_claim_generation=admitted.claim_generation,
            coordinator_session_id="coordinator-zero-quota-replacement",
            worker_id="worker-zero-quota-replacement",
            reason="worker heartbeat expired while quota was zero",
        )

        self.assertEqual(
            replacement.claim_generation,
            admitted.claim_generation + 1,
        )
        current_claim = self.kernel.task_claim_status(admitted.task_id)
        self.assertEqual(current_claim["attempt_id"], replacement.attempt_id)
        self.assertEqual(
            current_claim["claim_generation"],
            admitted.claim_generation + 1,
        )
        preserved = self.kernel.resource_status(
            admitted.task_id, admitted.attempt_id
        )
        self.assertEqual(preserved.queue_state, "admitted")
        self.assertEqual(preserved.lease_id, prior_lease_id)
        self.assertEqual(preserved.lease_state, "starting")
        self.assertEqual(preserved.launch_authorization_state, "AVAILABLE")
        self.assertFalse(preserved.launch_eligible)
        self.assertEqual(replacement.resource_admission.queue_state, "queued")
        self.assertIsNone(replacement.resource_admission.lease_id)
        self.assertEqual(
            self.kernel.resource_capacity_status()["resources"]["whisper"],
            {
                "capacity": 0,
                "usage": 1,
                "available": 0,
                "state": "overcommitted",
            },
        )
        stale_launcher = RecordingLauncher()
        with self.assertRaises(KernelConflict):
            self.kernel.launch_admitted_task(
                admitted.attempt_id,
                admitted.claim_generation,
                ("whisper",),
                stale_launcher,
            )
        self.assertEqual(stale_launcher.calls, [])

        self.kernel.activate_resource_configuration(
            self.configuration(version=3, capacities={"whisper": 1})
        )
        self.assertEqual(
            self.kernel.resource_status(
                replacement.task_id, replacement.attempt_id
            ).queue_state,
            "queued",
        )
        self.kernel.activate_resource_configuration(
            self.configuration(version=4, capacities={"whisper": 2})
        )
        admitted_replacement = self.kernel.resource_status(
            replacement.task_id, replacement.attempt_id
        )
        self.assertEqual(admitted_replacement.queue_state, "admitted")
        self.assertEqual(admitted_replacement.configuration_version, 4)
        self.assertEqual(
            self.kernel.resource_capacity_status()["resources"]["whisper"],
            {
                "capacity": 2,
                "usage": 2,
                "available": 0,
                "state": "full",
            },
        )

    def test_launch_authorization_is_resource_bound_and_single_use(self) -> None:
        resource_classes = (
            "bilibili_download",
            "youtube_download",
            "whisper",
            "codex_semantic",
            "latex",
            "pdf_render",
            "visual_acceptance",
        )
        for index, resource_class in enumerate(resource_classes):
            with self.subTest(resource_class=resource_class):
                _, claimed = self.prepare_and_claim(
                    f"launch-{resource_class.replace('_', '-')}", (resource_class,)
                )
                wrong_resource = resource_classes[(index + 1) % len(resource_classes)]
                launcher = RecordingLauncher()

                with self.assertRaises(ResourceAdmissionBlocked):
                    self.kernel.launch_admitted_task(
                        claimed.attempt_id,
                        claimed.claim_generation,
                        (wrong_resource,),
                        launcher,
                    )
                self.assertEqual(launcher.calls, [])

                self.assertEqual(
                    self.kernel.launch_admitted_task(
                        claimed.attempt_id,
                        claimed.claim_generation,
                        (resource_class,),
                        launcher,
                    ),
                    "started",
                )
                self.assertEqual(len(launcher.calls), 1)

                with self.assertRaises(ResourceAdmissionBlocked):
                    self.kernel.launch_admitted_task(
                        claimed.attempt_id,
                        claimed.claim_generation,
                        (resource_class,),
                        launcher,
                    )
                self.assertEqual(len(launcher.calls), 1)

    def test_multi_resource_launch_requires_the_exact_immutable_request(self) -> None:
        _, claimed = self.prepare_and_claim(
            "launch-exact-set", ("codex_semantic", "whisper")
        )
        subset_launcher = RecordingLauncher()
        with self.assertRaises(ResourceAdmissionBlocked):
            self.kernel.launch_admitted_task(
                claimed.attempt_id,
                claimed.claim_generation,
                ("whisper",),
                subset_launcher,
            )
        self.assertEqual(subset_launcher.calls, [])
        superset_launcher = RecordingLauncher()
        with self.assertRaises(ResourceAdmissionBlocked):
            self.kernel.launch_admitted_task(
                claimed.attempt_id,
                claimed.claim_generation,
                ("codex_semantic", "latex", "whisper"),
                superset_launcher,
            )
        self.assertEqual(superset_launcher.calls, [])
        exact_launcher = RecordingLauncher()
        self.assertEqual(
            self.kernel.launch_admitted_task(
                claimed.attempt_id,
                claimed.claim_generation,
                ("codex_semantic", "whisper"),
                exact_launcher,
            ),
            "started",
        )
        self.assertEqual(len(exact_launcher.calls), 1)

    def test_normal_release_rejects_unverified_provider_result_without_releasing_capacity(
        self,
    ) -> None:
        _, claimed = self.prepare_and_claim(
            "unverified-normal-release", ("whisper",)
        )
        launcher = RecordingLauncher()
        self.kernel.launch_admitted_task(
            claimed.attempt_id,
            claimed.claim_generation,
            ("whisper",),
            launcher,
        )

        with self.assertRaises(ContractError):
            self.kernel.release_resource_lease(
                claimed.attempt_id,
                claimed.claim_generation,
                launcher.calls[0],
                terminal_evidence={
                    "evidence_class": "provider_terminal_result",
                    "reference": "self-asserted-terminal-result",
                    "outcome": "succeeded",
                    "observed_at": "2026-07-17T11:45:00+08:00",
                },
            )

        retained = self.kernel.resource_status(claimed.task_id, claimed.attempt_id)
        self.assertEqual(retained.lease_state, "active")
        self.assertEqual(
            self.kernel.resource_capacity_status()["resources"]["whisper"],
            {"capacity": 1, "usage": 1, "available": 0, "state": "full"},
        )

    def test_normal_release_requires_provider_result_bound_to_lease_attempt_and_launch_authority(
        self,
    ) -> None:
        verifier = BoundProviderVerifier()
        self.kernel = VideoWorkflowKernel(
            self.workspace,
            resource_provider_verifiers={"bound-provider": verifier},
        )
        _, claimed = self.prepare_and_claim(
            "bound-provider-release", ("whisper",)
        )
        launcher = RecordingLauncher()
        self.kernel.launch_admitted_task(
            claimed.attempt_id,
            claimed.claim_generation,
            ("whisper",),
            launcher,
        )
        lease_id = str(claimed.resource_admission.lease_id)
        terminal_evidence = {
            "evidence_class": "provider_terminal_result",
            "provider": "bound-provider",
            "terminal_result_id": "provider-result-mismatched",
            "declared_outcome": "succeeded",
            "observed_at": "2026-07-17T11:50:00+08:00",
        }
        verifier.terminal_results["provider-result-mismatched"] = {
            "lease_id": "0" * 32,
            "attempt_id": "0" * 24,
            "launch_token": "0" * 64,
            "declared_outcome": "succeeded",
        }

        with self.assertRaises(ContractError):
            self.kernel.release_resource_lease(
                claimed.attempt_id,
                claimed.claim_generation,
                launcher.calls[0],
                terminal_evidence=terminal_evidence,
            )
        self.assertEqual(
            self.kernel.resource_status(
                claimed.task_id, claimed.attempt_id
            ).lease_state,
            "active",
        )
        self.assertEqual(
            self.kernel.resource_capacity_status()["resources"]["whisper"]["usage"],
            1,
        )

        terminal_evidence["terminal_result_id"] = "provider-result-bound"
        verifier.terminal_results["provider-result-bound"] = {
            "lease_id": lease_id,
            "attempt_id": claimed.attempt_id,
            "launch_token": launcher.calls[0],
            "declared_outcome": "succeeded",
        }
        with self.assertRaises(ResourceAdmissionBlocked):
            self.kernel.release_resource_lease(
                claimed.attempt_id,
                claimed.claim_generation,
                "f" * 64,
                terminal_evidence=terminal_evidence,
            )
        self.assertEqual(
            self.kernel.resource_capacity_status()["resources"]["whisper"]["usage"],
            1,
        )

        released = self.kernel.release_resource_lease(
            claimed.attempt_id,
            claimed.claim_generation,
            launcher.calls[0],
            terminal_evidence=terminal_evidence,
        )
        self.assertEqual(released.lease_state, "released")
        self.assertEqual(
            self.kernel.resource_capacity_status()["resources"]["whisper"]["usage"],
            0,
        )
        restarted = VideoWorkflowKernel(
            self.workspace,
            resource_provider_verifiers={"bound-provider": verifier},
        )
        self.assertEqual(
            restarted.resource_status(
                claimed.task_id, claimed.attempt_id
            ).lease_state,
            "released",
        )

    def test_normal_release_accepts_only_trusted_matching_local_process_proof(
        self,
    ) -> None:
        _, claimed = self.prepare_and_claim(
            "trusted-process-release", ("latex",)
        )
        launcher = ProcessIdentityLauncher()
        self.kernel.launch_admitted_task(
            claimed.attempt_id,
            claimed.claim_generation,
            ("latex",),
            launcher,
        )
        terminal_evidence = {
            "evidence_class": "local_process_terminated",
            "declared_outcome": "terminated",
            "pid": 4321,
            "process_creation_identity": "process-created-2026-07-17T11:10:00Z",
            "launch_token": launcher.calls[0],
            "observed_at": "2026-07-17T11:55:00+08:00",
        }

        with self.assertRaises(ContractError):
            self.kernel.release_resource_lease(
                claimed.attempt_id,
                claimed.claim_generation,
                launcher.calls[0],
                terminal_evidence=terminal_evidence,
            )
        self.assertEqual(
            self.kernel.resource_capacity_status()["resources"]["latex"]["usage"],
            1,
        )

        inspector = TrustedProcessInspector(matching_process_absent=True)
        trusted = VideoWorkflowKernel(
            self.workspace,
            local_process_inspector=inspector,
        )
        released = trusted.release_resource_lease(
            claimed.attempt_id,
            claimed.claim_generation,
            launcher.calls[0],
            terminal_evidence=terminal_evidence,
        )
        self.assertEqual(released.lease_state, "released")
        self.assertEqual(len(inspector.calls), 1)
        self.assertEqual(inspector.calls[0]["lease_id"], claimed.resource_admission.lease_id)
        self.assertEqual(inspector.calls[0]["attempt_id"], claimed.attempt_id)
        self.assertEqual(inspector.calls[0]["launch_token"], launcher.calls[0])
        self.assertEqual(
            trusted.resource_capacity_status()["resources"]["latex"]["usage"],
            0,
        )

    def test_crash_after_launch_authorization_cannot_replay_callback(self) -> None:
        _, claimed = self.prepare_and_claim("launch-crash", ("pdf_render",))
        launcher = RecordingLauncher()

        with self.assertRaises(ResourceAdmissionFault):
            self.kernel.launch_admitted_task(
                claimed.attempt_id,
                claimed.claim_generation,
                ("pdf_render",),
                launcher,
                fault_point="after_launch_authorized",
            )
        self.assertEqual(launcher.calls, [])
        stranded = self.kernel.resource_status(claimed.task_id, claimed.attempt_id)
        self.assertEqual(stranded.lease_state, "starting")
        self.assertFalse(stranded.launch_eligible)

        with self.assertRaises(ResourceAdmissionBlocked):
            self.kernel.launch_admitted_task(
                claimed.attempt_id,
                claimed.claim_generation,
                ("pdf_render",),
                launcher,
            )
        self.assertEqual(launcher.calls, [])

    def test_launch_completion_with_wrong_lease_generation_has_zero_mutation(
        self,
    ) -> None:
        _, claimed = self.prepare_and_claim("launch-generation-fence", ("pdf_render",))
        with self.assertRaises(ResourceAdmissionFault):
            self.kernel.launch_admitted_task(
                claimed.attempt_id,
                claimed.claim_generation,
                ("pdf_render",),
                RecordingLauncher(),
                fault_point="after_launch_authorized",
            )
        before = self.kernel.resource_status(claimed.task_id, claimed.attempt_id)
        before_events = self.kernel.resource_scheduler_status()["events"]
        required_resources_sha256 = hashlib.sha256(
            canonical_json_bytes(["pdf_render"])
        ).hexdigest()

        store = self.kernel._preflight_control_store()
        with self.assertRaises(KernelConflict):
            store.confirm_resource_launch(
                attempt_id=claimed.attempt_id,
                claim_generation=claimed.claim_generation + 1,
                launch_token=str(before.launch_token),
                required_resources_sha256=required_resources_sha256,
                launch_execution_identity_json=None,
                launch_execution_identity_sha256=None,
                updated_at="2026-07-17T11:30:00+08:00",
            )

        after = self.kernel.resource_status(claimed.task_id, claimed.attempt_id)
        self.assertEqual(after, before)
        self.assertEqual(
            self.kernel.resource_scheduler_status()["events"], before_events
        )
        self.assertEqual(after.lease_state, "starting")
        self.assertEqual(after.launch_authorization_state, "CONSUMED")

    def test_launcher_failure_paths_persist_one_audited_unknown_event_and_remain_fenced(
        self,
    ) -> None:
        cases = (
            (
                "exception",
                RaisingLauncher,
                RuntimeError,
                "launcher_exception",
                "pdf_render",
            ),
            (
                "invalid-identity",
                InvalidProcessIdentityLauncher,
                ContractError,
                "process_identity_validation",
                "visual_acceptance",
            ),
        )
        for index, (
            label,
            launcher_type,
            expected_error,
            failure_stage,
            resource_class,
        ) in enumerate(cases):
            with self.subTest(label=label):
                self.workspace = (
                    TEST_RUNS / f"lu-{index}-{uuid.uuid4().hex[:8]}" / "workspace"
                )
                self.workspace.mkdir(parents=True)
                self.kernel = VideoWorkflowKernel(self.workspace)
                _, claimed = self.prepare_and_claim(
                    f"lu-{index}", (resource_class,)
                )
                launcher = launcher_type()

                with self.assertRaises(expected_error):
                    self.kernel.launch_admitted_task(
                        claimed.attempt_id,
                        claimed.claim_generation,
                        (resource_class,),
                        launcher,
                    )
                self.assertEqual(len(launcher.calls), 1)
                unknown = self.kernel.resource_status(
                    claimed.task_id, claimed.attempt_id
                )
                self.assertEqual(unknown.lease_state, "unknown")
                self.assertEqual(unknown.launch_authorization_state, "CONSUMED")
                self.assertFalse(unknown.launch_eligible)
                capacity = self.kernel.resource_capacity_status()["resources"][
                    resource_class
                ]
                self.assertEqual(capacity["usage"], 1)
                self.assertEqual(capacity["state"], "full")

                events = [
                    event
                    for event in self.kernel.resource_scheduler_status()["events"]
                    if event["event_kind"] == "lease_unknown"
                    and event["lease_id"] == unknown.lease_id
                ]
                self.assertEqual(len(events), 1)
                self.assertEqual(
                    events[0]["payload"],
                    {
                        "cause": "launch_outcome_unconfirmed",
                        "attempt_id": claimed.attempt_id,
                        "claim_generation": claimed.claim_generation,
                        "failure_stage": failure_stage,
                    },
                )
                self.assertEqual(events[0]["configuration_id"], unknown.configuration_id)
                self.assertEqual(
                    events[0]["configuration_version"], unknown.configuration_version
                )
                self.assertEqual(
                    events[0]["configuration_sha256"], unknown.configuration_sha256
                )

                resource_fingerprint = hashlib.sha256(
                    canonical_json_bytes([resource_class])
                ).hexdigest()
                store = self.kernel._preflight_control_store()
                store.mark_resource_launch_unknown(
                    attempt_id=claimed.attempt_id,
                    claim_generation=claimed.claim_generation,
                    launch_token=launcher.calls[0],
                    required_resources_sha256=resource_fingerprint,
                    failure_stage=failure_stage,
                    updated_at="2026-07-17T11:40:00+08:00",
                )
                conflicting_stage = (
                    "process_identity_validation"
                    if failure_stage == "launcher_exception"
                    else "launcher_exception"
                )
                with self.assertRaises(KernelConflict):
                    store.mark_resource_launch_unknown(
                        attempt_id=claimed.attempt_id,
                        claim_generation=claimed.claim_generation,
                        launch_token=launcher.calls[0],
                        required_resources_sha256=resource_fingerprint,
                        failure_stage=conflicting_stage,
                        updated_at="2026-07-17T11:41:00+08:00",
                    )
                durable_events = self.kernel.resource_scheduler_status()["events"]
                for invalid_generation, invalid_token, invalid_fingerprint in (
                    (
                        claimed.claim_generation + 1,
                        launcher.calls[0],
                        resource_fingerprint,
                    ),
                    (
                        claimed.claim_generation,
                        "wrong-launch-token",
                        resource_fingerprint,
                    ),
                    (
                        claimed.claim_generation,
                        launcher.calls[0],
                        "0" * 64,
                    ),
                ):
                    with self.assertRaises(KernelConflict):
                        store.mark_resource_launch_unknown(
                            attempt_id=claimed.attempt_id,
                            claim_generation=invalid_generation,
                            launch_token=invalid_token,
                            required_resources_sha256=invalid_fingerprint,
                            failure_stage=failure_stage,
                            updated_at="2026-07-17T11:42:00+08:00",
                        )
                self.assertEqual(
                    self.kernel.resource_scheduler_status()["events"], durable_events
                )
                self.assertEqual(
                    self.kernel.resource_status(
                        claimed.task_id, claimed.attempt_id
                    ).lease_state,
                    "unknown",
                )

                checked = subprocess.run(
                    [
                        sys.executable,
                        "-X",
                        "utf8",
                        "-B",
                        str(CLI),
                        "control-store-check",
                        "--workspace-root",
                        str(self.workspace),
                    ],
                    cwd=PROJECT_ROOT,
                    text=True,
                    encoding="utf-8",
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(
                    checked.returncode,
                    0,
                    msg=f"stdout={checked.stdout}\nstderr={checked.stderr}",
                )
                self.assertEqual(json.loads(checked.stdout)["status"], "ok")

                restarted = VideoWorkflowKernel(self.workspace)
                self.assertEqual(
                    restarted.resource_status(
                        claimed.task_id, claimed.attempt_id
                    ).lease_state,
                    "unknown",
                )
                replay = RecordingLauncher()
                with self.assertRaises(ResourceAdmissionBlocked):
                    restarted.launch_admitted_task(
                        claimed.attempt_id,
                        claimed.claim_generation,
                        (resource_class,),
                        replay,
                    )
                self.assertEqual(replay.calls, [])
                self.assertEqual(
                    [
                        event
                        for event in restarted.resource_scheduler_status()["events"]
                        if event["event_kind"] == "lease_unknown"
                        and event["lease_id"] == unknown.lease_id
                    ],
                    events,
                )

    def test_stale_claim_after_blocked_launch_callback_commits_unknown_before_conflict(
        self,
    ) -> None:
        _, claimed = self.prepare_and_claim(
            "claim-fence-race", ("pdf_render",)
        )
        launcher = BlockingProcessIdentityLauncher()
        launch_errors: list[BaseException] = []

        def launch_generation_one() -> None:
            try:
                self.kernel.launch_admitted_task(
                    claimed.attempt_id,
                    claimed.claim_generation,
                    ("pdf_render",),
                    launcher,
                )
            except BaseException as exc:
                launch_errors.append(exc)

        launch_thread = threading.Thread(target=launch_generation_one)
        launch_thread.start()
        self.assertTrue(launcher.entered.wait(timeout=20))
        replacement = self.kernel.reclaim_task(
            claimed.run_dir,
            task_id=claimed.task_id,
            expected_attempt_id=claimed.attempt_id,
            expected_claim_generation=claimed.claim_generation,
            coordinator_session_id="coordinator-claim-fence-generation-two",
            worker_id="worker-claim-fence-generation-two",
            reason="generation one callback exceeded its ownership window",
        )
        self.assertEqual(replacement.claim_generation, claimed.claim_generation + 1)
        self.assertEqual(replacement.resource_admission.queue_state, "queued")

        launcher.release.set()
        launch_thread.join(timeout=20)
        self.assertFalse(launch_thread.is_alive())
        self.assertEqual(len(launch_errors), 1)
        self.assertIsInstance(launch_errors[0], KernelConflict)

        stale = self.kernel.resource_status(claimed.task_id, claimed.attempt_id)
        self.assertEqual(stale.lease_state, "unknown")
        self.assertEqual(stale.launch_authorization_state, "COMPLETED")
        self.assertFalse(stale.launch_eligible)
        capacity = self.kernel.resource_capacity_status()["resources"]["pdf_render"]
        self.assertEqual(capacity["usage"], 1)
        self.assertEqual(capacity["state"], "full")
        events = [
            event
            for event in self.kernel.resource_scheduler_status()["events"]
            if event["event_kind"] == "lease_unknown"
            and event["lease_id"] == stale.lease_id
        ]
        self.assertEqual(len(events), 1)
        self.assertEqual(
            events[0]["payload"],
            {
                "cause": "launch_outcome_unconfirmed",
                "attempt_id": claimed.attempt_id,
                "claim_generation": claimed.claim_generation,
                "failure_stage": "claim_generation_fence",
            },
        )

        restarted = VideoWorkflowKernel(self.workspace)
        self.assertEqual(
            restarted.resource_status(
                claimed.task_id, claimed.attempt_id
            ).lease_state,
            "unknown",
        )
        inspector = TrustedProcessInspector(matching_process_absent=True)
        verifier = VideoWorkflowKernel(
            self.workspace,
            local_process_inspector=inspector,
        )
        resolved = verifier.resource_resolve(
            str(stale.lease_id),
            claimed.attempt_id,
            claimed.claim_generation,
            resolution_evidence={
                "evidence_class": "local_process_terminated",
                "declared_outcome": "terminated",
                "pid": 9876,
                "process_creation_identity": "process-created-claim-fence-race",
                "launch_token": launcher.calls[0],
                "observed_at": "2026-07-17T12:10:00+08:00",
            },
        )
        self.assertEqual(resolved.lease_state, "resolved")
        self.assertEqual(len(inspector.calls), 1)

    def test_reconciled_inflight_launch_persists_identity_for_trusted_resolution(
        self,
    ) -> None:
        _, claimed = self.prepare_and_claim(
            "reconciled-inflight", ("pdf_render",)
        )
        launcher = BlockingProcessIdentityLauncher()
        launch_errors: list[BaseException] = []

        def launch_before_session_loss() -> None:
            try:
                self.kernel.launch_admitted_task(
                    claimed.attempt_id,
                    claimed.claim_generation,
                    ("pdf_render",),
                    launcher,
                )
            except BaseException as exc:
                launch_errors.append(exc)

        launch_thread = threading.Thread(target=launch_before_session_loss)
        launch_thread.start()
        self.assertTrue(launcher.entered.wait(timeout=20))

        reconciled = self.kernel.resource_reconcile(
            current_coordinator_session_id="coordinator-after-session-loss",
            lost_coordinator_session_ids=("coordinator-reconciled-inflight",),
        )
        self.assertEqual(
            reconciled["transitioned_lease_ids"],
            [claimed.resource_admission.lease_id],
        )

        launcher.release.set()
        launch_thread.join(timeout=20)
        self.assertFalse(launch_thread.is_alive())
        self.assertEqual(len(launch_errors), 1)
        self.assertIsInstance(launch_errors[0], KernelConflict)

        unknown = self.kernel.resource_status(claimed.task_id, claimed.attempt_id)
        self.assertEqual(unknown.lease_state, "unknown")
        self.assertEqual(unknown.launch_authorization_state, "COMPLETED")
        self.assertEqual(
            self.kernel.resource_capacity_status()["resources"]["pdf_render"],
            {"capacity": 1, "usage": 1, "available": 0, "state": "full"},
        )
        unknown_events = [
            event
            for event in self.kernel.resource_scheduler_status()["events"]
            if event["event_kind"] == "lease_unknown"
            and event["lease_id"] == unknown.lease_id
        ]
        self.assertEqual(len(unknown_events), 1)

        inspector = TrustedProcessInspector(matching_process_absent=True)
        resolver = VideoWorkflowKernel(
            self.workspace,
            local_process_inspector=inspector,
        )
        resolved = resolver.resource_resolve(
            str(unknown.lease_id),
            claimed.attempt_id,
            claimed.claim_generation,
            resolution_evidence={
                "evidence_class": "local_process_terminated",
                "declared_outcome": "terminated",
                "pid": 9876,
                "process_creation_identity": "process-created-claim-fence-race",
                "launch_token": launcher.calls[0],
                "observed_at": "2026-07-17T12:20:00+08:00",
            },
        )
        self.assertEqual(resolved.lease_state, "resolved")
        self.assertEqual(len(inspector.calls), 1)
        self.assertEqual(
            resolver.resource_capacity_status()["resources"]["pdf_render"]["usage"],
            0,
        )

    def test_claim_resource_transaction_faults_roll_back_without_partial_authority(
        self,
    ) -> None:
        fault_points = (
            "after_claim_before_enqueue",
            "after_claim_enqueue_before_schedule",
            "after_claim_schedule_before_commit",
        )
        for index, fault_point in enumerate(fault_points):
            with self.subTest(fault_point=fault_point):
                self.workspace = (
                    TEST_RUNS
                    / f"cf-{index}-{uuid.uuid4().hex[:8]}"
                    / "workspace"
                )
                self.workspace.mkdir(parents=True)
                self.kernel = VideoWorkflowKernel(self.workspace)
                prepared = self.prepare_only(f"cf-{index}", ("whisper",))
                baseline_scheduler = self.kernel.resource_scheduler_status()
                baseline_capacity = self.kernel.resource_capacity_status()

                with self.assertRaisesRegex(ResourceAdmissionFault, fault_point):
                    self.kernel.claim_task(
                        prepared.run_dir,
                        prepared.task_id,
                        coordinator_session_id=f"coordinator-{fault_point}",
                        worker_id=f"worker-{fault_point}",
                        fault_point=fault_point,
                    )

                restarted = VideoWorkflowKernel(self.workspace)
                self.assertIsNone(restarted.task_claim_status(prepared.task_id))
                self.assertEqual(
                    restarted.resource_scheduler_status(), baseline_scheduler
                )
                self.assertEqual(
                    restarted.resource_capacity_status(), baseline_capacity
                )

                claimed = restarted.claim_task(
                    prepared.run_dir,
                    prepared.task_id,
                    coordinator_session_id=f"coordinator-{fault_point}",
                    worker_id=f"worker-{fault_point}",
                )
                self.assertEqual(claimed.resource_admission.queue_state, "admitted")
                self.assertIsNotNone(claimed.resource_admission.lease_id)
                self.assertTrue(claimed.resource_admission.launch_eligible)
                VideoWorkflowKernel(self.workspace)

    def test_reclaim_resource_transaction_faults_roll_back_and_replay_cleanly(
        self,
    ) -> None:
        fault_points = (
            "after_reclaim_before_enqueue",
            "after_reclaim_enqueue_before_schedule",
            "after_reclaim_schedule_before_commit",
        )
        for index, fault_point in enumerate(fault_points):
            with self.subTest(fault_point=fault_point):
                self.workspace = (
                    TEST_RUNS
                    / f"rf-{index}-{uuid.uuid4().hex[:8]}"
                    / "workspace"
                )
                self.workspace.mkdir(parents=True)
                self.kernel = VideoWorkflowKernel(self.workspace)
                _, holder = self.prepare_and_claim(
                    f"rf-{index}-h", ("whisper",)
                )
                _, queued = self.prepare_and_claim(
                    f"rf-{index}-q", ("whisper",)
                )
                baseline_scheduler = self.kernel.resource_scheduler_status()
                baseline_capacity = self.kernel.resource_capacity_status()

                with self.assertRaisesRegex(ResourceAdmissionFault, fault_point):
                    self.kernel.reclaim_task(
                        queued.run_dir,
                        task_id=queued.task_id,
                        expected_attempt_id=queued.attempt_id,
                        expected_claim_generation=queued.claim_generation,
                        coordinator_session_id=f"coordinator-replacement-{fault_point}",
                        worker_id=f"worker-replacement-{fault_point}",
                        reason="fault injection recovery",
                        fault_point=fault_point,
                    )

                restarted = VideoWorkflowKernel(self.workspace)
                claim = restarted.task_claim_status(queued.task_id)
                self.assertEqual(claim["attempt_id"], queued.attempt_id)
                self.assertEqual(
                    claim["claim_generation"], queued.claim_generation
                )
                prior = restarted.resource_status(
                    queued.task_id, queued.attempt_id
                )
                self.assertEqual(prior.queue_state, "queued")
                self.assertIsNone(prior.lease_id)
                self.assertEqual(
                    restarted.resource_scheduler_status(), baseline_scheduler
                )
                self.assertEqual(
                    restarted.resource_capacity_status(), baseline_capacity
                )

                replacement = restarted.reclaim_task(
                    queued.run_dir,
                    task_id=queued.task_id,
                    expected_attempt_id=queued.attempt_id,
                    expected_claim_generation=queued.claim_generation,
                    coordinator_session_id=f"coordinator-replacement-{fault_point}",
                    worker_id=f"worker-replacement-{fault_point}",
                    reason="fault injection recovery",
                )
                self.assertEqual(
                    restarted.resource_status(
                        queued.task_id, queued.attempt_id
                    ).queue_state,
                    "invalidated",
                )
                self.assertEqual(
                    replacement.resource_admission.queue_state, "queued"
                )
                self.assertIsNone(replacement.resource_admission.lease_id)
                self.assertEqual(holder.resource_admission.queue_state, "admitted")
                VideoWorkflowKernel(self.workspace)

    def test_two_level_round_robin_is_deterministic_and_survives_restart(self) -> None:
        _, holder = self.prepare_and_claim(
            "fair-holder", ("whisper",), batch_id="00-holder"
        )
        batch_members = [
            self.prepare_and_claim(
                f"fair-batch-{index}",
                ("whisper",),
                batch_id="zz-batch",
            )[1]
            for index in range(2)
        ]
        _, standalone = self.prepare_and_claim("fair-standalone", ("whisper",))
        self.assertTrue(
            all(member.resource_admission.queue_state == "queued" for member in batch_members)
        )
        self.assertEqual(standalone.resource_admission.queue_state, "queued")

        self.release_started(holder, "whisper")
        standalone_status = self.kernel.resource_status(
            standalone.task_id, standalone.attempt_id
        )
        self.assertEqual(standalone_status.queue_state, "admitted")
        self.assertTrue(
            all(
                self.kernel.resource_status(member.task_id, member.attempt_id).queue_state
                == "queued"
                for member in batch_members
            )
        )

        self.release_started(standalone, "whisper")
        expected_first = min(batch_members, key=lambda member: member.run_id)
        first_status = self.kernel.resource_status(
            expected_first.task_id, expected_first.attempt_id
        )
        self.assertEqual(first_status.queue_state, "admitted")

        restarted = VideoWorkflowKernel(self.workspace)
        scheduler = restarted.resource_scheduler_status()
        self.assertEqual(scheduler["group_cursor"], "zz-batch")
        self.assertEqual(
            scheduler["run_cursors"]["zz-batch"], expected_first.run_id
        )

    def test_round_robin_cursor_keeps_position_after_served_identity_dequeues(self) -> None:
        _, group_holder = self.prepare_and_claim(
            "cursor-group-holder", ("latex",), batch_id="00-holder"
        )
        group_claims = {
            group: self.prepare_and_claim(
                f"cursor-{group}", ("latex",), batch_id=group
            )[1]
            for group in ("aa-group", "mm-group", "zz-group")
        }
        self.release_started(group_holder, "latex")
        self.assertEqual(
            self.kernel.resource_status(
                group_claims["aa-group"].task_id,
                group_claims["aa-group"].attempt_id,
            ).queue_state,
            "admitted",
        )
        self.release_started(group_claims["aa-group"], "latex")
        self.assertEqual(
            self.kernel.resource_status(
                group_claims["mm-group"].task_id,
                group_claims["mm-group"].attempt_id,
            ).queue_state,
            "admitted",
        )
        _, late_low_group = self.prepare_and_claim(
            "cursor-aa-late", ("latex",), batch_id="aa-group"
        )
        self.release_started(group_claims["mm-group"], "latex")
        self.assertEqual(
            self.kernel.resource_status(
                group_claims["zz-group"].task_id,
                group_claims["zz-group"].attempt_id,
            ).queue_state,
            "admitted",
        )
        self.assertEqual(
            self.kernel.resource_status(
                late_low_group.task_id, late_low_group.attempt_id
            ).queue_state,
            "queued",
        )

        _, run_holder = self.prepare_and_claim(
            "cursor-run-holder", ("pdf_render",), batch_id="00-holder-run"
        )
        prepared_members = [
            self.prepare_only(
                f"cursor-run-{index}",
                ("pdf_render",),
                batch_id="shared-batch",
            )
            for index in range(4)
        ]
        prepared_members.sort(key=lambda prepared: prepared.run_id)
        first = self.claim_prepared(prepared_members[1], "cursor-run-first")
        later_high = self.claim_prepared(prepared_members[3], "cursor-run-high")
        self.release_started(run_holder, "pdf_render")
        self.assertEqual(
            self.kernel.resource_status(first.task_id, first.attempt_id).queue_state,
            "admitted",
        )
        late_low_run = self.claim_prepared(
            prepared_members[0], "cursor-run-low-late"
        )
        self.release_started(first, "pdf_render")
        self.assertEqual(
            self.kernel.resource_status(
                later_high.task_id, later_high.attempt_id
            ).queue_state,
            "admitted",
        )
        self.assertEqual(
            self.kernel.resource_status(
                late_low_run.task_id, late_low_run.attempt_id
            ).queue_state,
            "queued",
        )

    def test_reserved_admission_advances_fairness_cursor_before_next_ordinary_turn(
        self,
    ) -> None:
        self.kernel.trace_source_ready(
            fixture=FIXTURE,
            task_start=TASK_START,
            request_id=f"reservation-cursor-bootstrap-{uuid.uuid4().hex}",
        )
        self.kernel.activate_resource_configuration(
            self.configuration(version=2, capacities={}, bypass_threshold=1)
        )
        _, holder = self.prepare_and_claim(
            "reservation-cursor-holder", ("whisper",), batch_id="00-holder"
        )
        _, reserved_a1 = self.prepare_and_claim(
            "reservation-cursor-a1", ("whisper",), batch_id="aa-group"
        )
        self.prepare_and_claim(
            "reservation-cursor-bypass", ("latex",), batch_id="zz-bypass"
        )
        self.assertEqual(
            self.kernel.resource_status(
                reserved_a1.task_id, reserved_a1.attempt_id
            ).reservation_state,
            "active",
        )
        _, ordinary_a2 = self.prepare_and_claim(
            "reservation-cursor-a2", ("whisper",), batch_id="aa-group"
        )
        _, ordinary_b = self.prepare_and_claim(
            "reservation-cursor-b", ("whisper",), batch_id="bb-group"
        )

        self.release_started(holder, "whisper")
        self.assertEqual(
            self.kernel.resource_status(
                reserved_a1.task_id, reserved_a1.attempt_id
            ).queue_state,
            "admitted",
        )
        scheduler = self.kernel.resource_scheduler_status()
        self.assertEqual(scheduler["group_cursor"], "aa-group")
        self.assertEqual(
            scheduler["run_cursors"]["aa-group"], reserved_a1.run_id
        )

        self.release_started(reserved_a1, "whisper")
        self.assertEqual(
            self.kernel.resource_status(
                ordinary_b.task_id, ordinary_b.attempt_id
            ).queue_state,
            "admitted",
        )
        self.assertEqual(
            self.kernel.resource_status(
                ordinary_a2.task_id, ordinary_a2.attempt_id
            ).queue_state,
            "queued",
        )

    def test_zero_quota_rejects_before_claim_or_queue_is_persisted(self) -> None:
        self.kernel.trace_source_ready(
            fixture=FIXTURE,
            task_start=TASK_START,
            request_id=f"zero-config-bootstrap-{uuid.uuid4().hex}",
        )
        self.kernel.activate_resource_configuration(
            self.configuration(version=2, capacities={"whisper": 0})
        )
        prepared = self.prepare_only("zero-whisper", ("whisper",))

        with self.assertRaises(ContractError):
            self.claim_prepared(prepared, "zero-whisper")
        self.assertIsNone(self.kernel.task_claim_status(prepared.task_id))
        with self.assertRaises(ControlStoreUnavailable):
            self.kernel.resource_status(prepared.task_id, "missing-attempt")

    def test_quota_downshift_drains_without_preemption_and_unrelated_work_runs(self) -> None:
        _, codex_one = self.prepare_and_claim(
            "drain-codex-one", ("codex_semantic",)
        )
        _, codex_two = self.prepare_and_claim(
            "drain-codex-two", ("codex_semantic",)
        )
        launchers = []
        for claimed in (codex_one, codex_two):
            launcher = RecordingLauncher()
            self.kernel.launch_admitted_task(
                claimed.attempt_id,
                claimed.claim_generation,
                ("codex_semantic",),
                launcher,
            )
            launchers.append(launcher)

        self.kernel.activate_resource_configuration(
            self.configuration(version=2, capacities={"codex_semantic": 1})
        )
        overcommitted = self.kernel.resource_capacity_status()["resources"][
            "codex_semantic"
        ]
        self.assertEqual(overcommitted["usage"], 2)
        self.assertEqual(overcommitted["state"], "overcommitted")
        _, codex_three = self.prepare_and_claim(
            "drain-codex-three", ("codex_semantic",)
        )
        _, latex = self.prepare_and_claim("drain-latex", ("latex",))
        self.assertEqual(codex_three.resource_admission.queue_state, "queued")
        self.assertEqual(latex.resource_admission.queue_state, "admitted")
        self.assertEqual(
            self.kernel.resource_status(
                codex_one.task_id, codex_one.attempt_id
            ).configuration_version,
            1,
        )

        releaser = self.trusted_provider_kernel()
        for index, claimed in enumerate((codex_one, codex_two)):
            releaser.release_resource_lease(
                claimed.attempt_id,
                claimed.claim_generation,
                launchers[index].calls[0],
                terminal_evidence=self.provider_terminal_evidence(
                    f"drain-result-{index}",
                    observed_at="2026-07-17T10:40:00+08:00",
                ),
            )
            expected = "queued" if index == 0 else "admitted"
            self.assertEqual(
                self.kernel.resource_status(
                    codex_three.task_id, codex_three.attempt_id
                ).queue_state,
                expected,
            )
            capacity = self.kernel.resource_capacity_status()["resources"][
                "codex_semantic"
            ]
            self.assertEqual(capacity["usage"], 1)
            self.assertEqual(capacity["state"], "full")

        admitted_v2 = self.kernel.resource_status(
            codex_three.task_id, codex_three.attempt_id
        )
        self.assertEqual(admitted_v2.configuration_version, 2)
        queue_events = [
            event
            for event in self.kernel.resource_scheduler_status()["events"]
            if event["queue_id"] == admitted_v2.queue_id
        ]
        self.assertEqual(
            [
                event["event_kind"]
                for event in queue_events
                if event["event_kind"] != "bypassed"
            ],
            ["enqueued", "configuration_blocked", "admitted"],
        )
        configuration_blocks = [
            event
            for event in queue_events
            if event["event_kind"] == "configuration_blocked"
        ]
        self.assertEqual(len(configuration_blocks), 1)
        self.assertEqual(configuration_blocks[0]["configuration_version"], 2)
        self.assertEqual(
            configuration_blocks[0]["configuration_sha256"],
            admitted_v2.configuration_sha256,
        )
        self.assertEqual(
            configuration_blocks[0]["payload"],
            {"reason": "configuration_capacity"},
        )
        self.release_started(codex_three, "codex_semantic")
        available = self.kernel.resource_capacity_status()["resources"][
            "codex_semantic"
        ]
        self.assertEqual(available["usage"], 0)
        self.assertEqual(available["state"], "available")

    def test_draining_reservations_are_disjoint_and_stably_ordered(self) -> None:
        self.kernel.trace_source_ready(
            fixture=FIXTURE,
            task_start=TASK_START,
            request_id=f"reservation-config-bootstrap-{uuid.uuid4().hex}",
        )
        self.kernel.activate_resource_configuration(
            self.configuration(
                version=2,
                capacities={"codex_semantic": 2},
                bypass_threshold=1,
            )
        )

        _, whisper_holder = self.prepare_and_claim(
            "reservation-whisper-holder", ("whisper",), batch_id="00-holder"
        )
        _, reservation_a = self.prepare_and_claim(
            "reservation-a",
            ("codex_semantic", "whisper"),
            batch_id="aa-starving",
        )
        _, codex_bypass = self.prepare_and_claim(
            "reservation-codex-bypass",
            ("codex_semantic",),
            batch_id="zz-bypass",
        )
        status_a = self.kernel.resource_status(
            reservation_a.task_id, reservation_a.attempt_id
        )
        self.assertEqual(status_a.bypass_count, 1)
        self.assertEqual(status_a.reservation_state, "active")

        _, latex_holder = self.prepare_and_claim(
            "reservation-latex-holder", ("latex",), batch_id="00-latex-holder"
        )
        _, reservation_b = self.prepare_and_claim(
            "reservation-b",
            ("codex_semantic", "latex"),
            batch_id="bb-starving",
        )
        _, unrelated_pdf = self.prepare_and_claim(
            "reservation-pdf-unrelated",
            ("pdf_render",),
            batch_id="zz-pdf-bypass",
        )
        status_b = self.kernel.resource_status(
            reservation_b.task_id, reservation_b.attempt_id
        )
        self.assertEqual(status_b.reservation_state, "pending")
        self.assertLess(status_a.reservation_seq, status_b.reservation_seq)
        self.assertEqual(unrelated_pdf.resource_admission.queue_state, "admitted")

        _, youtube_holder = self.prepare_and_claim(
            "reservation-youtube-holder",
            ("youtube_download",),
            batch_id="00-youtube-holder",
        )
        _, reservation_c = self.prepare_and_claim(
            "reservation-c",
            ("visual_acceptance", "youtube_download"),
            batch_id="cc-starving",
        )
        _, unrelated_bilibili = self.prepare_and_claim(
            "reservation-bilibili-unrelated",
            ("bilibili_download",),
            batch_id="zz-bilibili-bypass",
        )
        status_c = self.kernel.resource_status(
            reservation_c.task_id, reservation_c.attempt_id
        )
        self.assertEqual(status_c.reservation_state, "active")
        self.assertGreater(status_c.reservation_seq, status_b.reservation_seq)
        self.assertEqual(unrelated_bilibili.resource_admission.queue_state, "admitted")

        restarted = VideoWorkflowKernel(self.workspace)
        scheduler = restarted.resource_scheduler_status()
        active_sets = [set(item["resources"]) for item in scheduler["reservations"] if item["state"] == "active"]
        for index, left in enumerate(active_sets):
            for right in active_sets[index + 1 :]:
                self.assertTrue(left.isdisjoint(right))
        self.assertEqual(
            [item["reservation_seq"] for item in scheduler["reservations"]],
            sorted(item["reservation_seq"] for item in scheduler["reservations"]),
        )

        self.release_started(codex_bypass, "codex_semantic")
        _, overlapping_ordinary = self.prepare_and_claim(
            "reservation-overlapping-ordinary",
            ("codex_semantic",),
            batch_id="zz-overlapping-ordinary",
        )
        self.assertEqual(
            overlapping_ordinary.resource_admission.queue_state, "queued"
        )
        self.release_started(whisper_holder, "whisper")
        admitted_reserved = self.kernel.resource_status(
            reservation_a.task_id, reservation_a.attempt_id
        )
        self.assertEqual(admitted_reserved.queue_state, "admitted")
        self.assertEqual(admitted_reserved.reservation_state, "terminated")
        promoted_pending = self.kernel.resource_status(
            reservation_b.task_id, reservation_b.attempt_id
        )
        self.assertEqual(promoted_pending.reservation_state, "active")
        self.assertEqual(
            self.kernel.resource_status(
                overlapping_ordinary.task_id, overlapping_ordinary.attempt_id
            ).queue_state,
            "queued",
        )

        self.assertEqual(whisper_holder.resource_admission.queue_state, "admitted")
        self.assertEqual(codex_bypass.resource_admission.queue_state, "admitted")
        self.assertEqual(latex_holder.resource_admission.queue_state, "admitted")
        self.assertEqual(youtube_holder.resource_admission.queue_state, "admitted")

    def test_circuit_breaker_blocks_only_its_resource_class(self) -> None:
        self.kernel.trace_source_ready(
            fixture=FIXTURE,
            task_start=TASK_START,
            request_id=f"breaker-bootstrap-{uuid.uuid4().hex}",
        )
        opened = self.kernel.set_resource_circuit_breaker(
            "bilibili_download",
            state="open",
            reason="cookie rejected",
            platform="bilibili",
        )
        self.assertEqual(opened["state"], "open")
        self.assertEqual(opened["scope_kind"], "platform")
        latex_opened = self.kernel.set_resource_circuit_breaker(
            "latex",
            state="open",
            reason="MiKTeX unavailable",
        )
        self.assertEqual(latex_opened["scope_kind"], "resource")

        restarted = VideoWorkflowKernel(self.workspace)
        persisted_breakers = restarted.resource_circuit_breaker_status()
        self.assertEqual(
            {(item["resource_class"], item["scope_kind"], item["state"]) for item in persisted_breakers},
            {
                ("bilibili_download", "platform", "open"),
                ("latex", "resource", "open"),
            },
        )

        _, bilibili = self.prepare_and_claim(
            "breaker-bilibili", ("bilibili_download",)
        )
        _, youtube = self.prepare_and_claim(
            "breaker-youtube", ("youtube_download",)
        )
        _, semantic = self.prepare_and_claim(
            "breaker-semantic", ("codex_semantic",)
        )
        _, latex = self.prepare_and_claim("breaker-latex", ("latex",))
        self.assertEqual(bilibili.resource_admission.queue_state, "queued")
        self.assertEqual(youtube.resource_admission.queue_state, "admitted")
        self.assertEqual(semantic.resource_admission.queue_state, "admitted")
        self.assertEqual(latex.resource_admission.queue_state, "queued")

        closed = self.kernel.set_resource_circuit_breaker(
            "bilibili_download",
            state="closed",
            reason="refreshed cookie verified",
            platform="bilibili",
        )
        self.assertEqual(closed["state"], "closed")
        self.assertEqual(
            self.kernel.resource_status(
                bilibili.task_id, bilibili.attempt_id
            ).queue_state,
            "admitted",
        )
        self.kernel.set_resource_circuit_breaker(
            "latex",
            state="closed",
            reason="MiKTeX health check passed",
        )
        self.assertEqual(
            self.kernel.resource_status(latex.task_id, latex.attempt_id).queue_state,
            "admitted",
        )

    def test_temporarily_unschedulable_reservation_blocks_full_set_but_not_disjoint_resources(
        self,
    ) -> None:
        for mode in ("breaker", "zero-capacity"):
            with self.subTest(mode=mode):
                self.workspace = (
                    TEST_RUNS / f"ru-{mode[0]}-{uuid.uuid4().hex[:8]}" / "workspace"
                )
                self.workspace.mkdir(parents=True)
                self.kernel = VideoWorkflowKernel(self.workspace)
                self.kernel.trace_source_ready(
                    fixture=FIXTURE,
                    task_start=TASK_START,
                    request_id=f"ru-bootstrap-{mode}-{uuid.uuid4().hex}",
                )
                self.kernel.activate_resource_configuration(
                    self.configuration(
                        version=2,
                        capacities={"codex_semantic": 2},
                        bypass_threshold=1,
                    )
                )
                _, holder = self.prepare_and_claim(
                    f"ru-{mode[0]}-h",
                    ("bilibili_download",),
                    batch_id="00-holder",
                )
                _, reserved = self.prepare_and_claim(
                    f"ru-{mode[0]}-r",
                    ("bilibili_download", "codex_semantic"),
                    batch_id="aa-reserved",
                )
                _, bypass = self.prepare_and_claim(
                    f"ru-{mode[0]}-b",
                    ("codex_semantic",),
                    batch_id="zz-bypass",
                )
                before = self.kernel.resource_status(
                    reserved.task_id, reserved.attempt_id
                )
                self.assertEqual(before.reservation_state, "active")
                reservation_seq = before.reservation_seq

                if mode == "breaker":
                    self.kernel.set_resource_circuit_breaker(
                        "bilibili_download",
                        state="open",
                        reason="temporary cookie failure",
                        platform="bilibili",
                    )
                else:
                    self.kernel.activate_resource_configuration(
                        self.configuration(
                            version=3,
                            capacities={
                                "bilibili_download": 0,
                                "codex_semantic": 2,
                            },
                            bypass_threshold=1,
                        )
                    )

                _, overlapping = self.prepare_and_claim(
                    f"ru-{mode[0]}-overlapping", ("codex_semantic",)
                )
                self.assertEqual(
                    overlapping.resource_admission.queue_state,
                    "queued",
                )
                _, disjoint = self.prepare_and_claim(
                    f"ru-{mode[0]}-disjoint", ("pdf_render",)
                )
                self.assertEqual(disjoint.resource_admission.queue_state, "admitted")
                suspended = self.kernel.resource_status(
                    reserved.task_id, reserved.attempt_id
                )
                self.assertEqual(suspended.reservation_state, "active")
                self.assertEqual(suspended.reservation_seq, reservation_seq)

                if mode == "breaker":
                    self.kernel.set_resource_circuit_breaker(
                        "bilibili_download",
                        state="closed",
                        reason="cookie health verified",
                        platform="bilibili",
                    )
                else:
                    self.kernel.activate_resource_configuration(
                        self.configuration(
                            version=4,
                            capacities={
                                "bilibili_download": 1,
                                "codex_semantic": 2,
                            },
                            bypass_threshold=1,
                        )
                    )

                self.assertEqual(
                    self.kernel.resource_status(
                        overlapping.task_id, overlapping.attempt_id
                    ).queue_state,
                    "queued",
                )
                restored = self.kernel.resource_status(
                    reserved.task_id, reserved.attempt_id
                )
                self.assertEqual(restored.reservation_state, "active")
                self.assertEqual(restored.reservation_seq, reservation_seq)

                self.release_started(holder, "bilibili_download")
                admitted = self.kernel.resource_status(
                    reserved.task_id, reserved.attempt_id
                )
                self.assertEqual(admitted.queue_state, "admitted")
                self.assertEqual(admitted.reservation_state, "terminated")
                self.assertEqual(admitted.reservation_seq, reservation_seq)
                self.assertEqual(
                    self.kernel.resource_status(
                        overlapping.task_id, overlapping.attempt_id
                    ).queue_state,
                    "queued",
                )
                self.assertEqual(bypass.resource_admission.queue_state, "admitted")
                VideoWorkflowKernel(self.workspace)

    def test_resource_control_cli_exposes_public_operational_seams(self) -> None:
        self.kernel.trace_source_ready(
            fixture=FIXTURE,
            task_start=TASK_START,
            request_id=f"resource-cli-bootstrap-{uuid.uuid4().hex}",
        )

        def run_cli(*arguments: str, expected_exit_code: int = 0) -> dict:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-X",
                    "utf8",
                    "-B",
                    str(CLI),
                    *arguments,
                ],
                cwd=PROJECT_ROOT,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                completed.returncode,
                expected_exit_code,
                msg=f"stdout={completed.stdout}\nstderr={completed.stderr}",
            )
            envelope = json.loads(completed.stdout)
            self.kernel.contracts.validate("workflow-result", envelope)
            self.assertEqual(
                envelope["status"],
                "ok" if expected_exit_code == 0 else "error",
            )
            return envelope

        workspace_argument = str(self.workspace)
        scheduler = run_cli(
            "resource-scheduler-status",
            "--workspace-root",
            workspace_argument,
        )
        self.assertEqual(scheduler["command"], "resource-scheduler-status")
        self.assertIn("sequences", scheduler["data"])
        capacity = run_cli(
            "resource-capacity-status",
            "--workspace-root",
            workspace_argument,
        )
        self.assertIn("resources", capacity["data"])

        disposable = self.workspace / "待删除"
        disposable.mkdir(exist_ok=True)
        configuration_path = disposable / "resource-cli-configuration.json"
        write_json_atomic(
            configuration_path,
            self.configuration(version=2, capacities={"codex_semantic": 2}),
        )
        activated = run_cli(
            "resource-config-activate",
            "--workspace-root",
            workspace_argument,
            "--configuration",
            str(configuration_path),
        )
        self.assertEqual(activated["data"]["configuration_version"], 2)
        breaker = run_cli(
            "resource-breaker-set",
            "--workspace-root",
            workspace_argument,
            "--resource-class",
            "latex",
            "--state",
            "open",
            "--reason",
            "CLI contract test",
        )
        self.assertEqual(breaker["data"]["state"], "open")
        breaker_status = run_cli(
            "resource-breaker-status",
            "--workspace-root",
            workspace_argument,
        )
        self.assertEqual(
            breaker_status["data"]["breakers"][0]["resource_class"], "latex"
        )
        before_rejected_breaker = run_cli(
            "resource-scheduler-status",
            "--workspace-root",
            workspace_argument,
        )["data"]
        rejected = run_cli(
            "resource-breaker-set",
            "--workspace-root",
            workspace_argument,
            "--resource-class",
            "unknown_resource",
            "--state",
            "open",
            "--reason",
            "must fail before mutation",
            expected_exit_code=20,
        )
        self.assertEqual(rejected["classification"], "contract_invalid")
        self.assertEqual(
            run_cli(
                "resource-breaker-status",
                "--workspace-root",
                workspace_argument,
            )["data"],
            breaker_status["data"],
        )
        self.assertEqual(
            run_cli(
                "resource-scheduler-status",
                "--workspace-root",
                workspace_argument,
            )["data"],
            before_rejected_breaker,
        )

        _, claimed = self.prepare_and_claim("cli-resolve", ("whisper",))
        launcher = RecordingLauncher()
        self.kernel.launch_admitted_task(
            claimed.attempt_id,
            claimed.claim_generation,
            ("whisper",),
            launcher,
        )
        reconciled = run_cli(
            "resource-reconcile",
            "--workspace-root",
            workspace_argument,
            "--current-coordinator-session-id",
            "coordinator-cli-restart",
            "--lost-coordinator-session-id",
            "coordinator-cli-resolve",
        )
        self.assertEqual(
            reconciled["data"]["transitioned_lease_ids"],
            [claimed.resource_admission.lease_id],
        )
        resolution_path = disposable / "resource-cli-resolution.json"
        write_json_atomic(
            resolution_path,
            {
                "evidence_class": "explicit_human_resolution",
                "declared_outcome": "terminated",
                "reason": "operator verified the CLI test worker ended",
                "observed_termination_basis": "test launcher lifecycle ended",
                "coordinator_identity": "coordinator-cli-restart",
                "observed_at": "2026-07-17T12:00:00+08:00",
            },
        )
        resolved = run_cli(
            "resource-resolve",
            "--workspace-root",
            workspace_argument,
            "--lease-id",
            str(claimed.resource_admission.lease_id),
            "--attempt-id",
            claimed.attempt_id,
            "--expected-claim-generation",
            str(claimed.claim_generation),
            "--resolution-evidence",
            str(resolution_path),
        )
        self.assertEqual(resolved["data"]["lease_state"], "resolved")

    def test_new_reservation_blocks_later_same_pass_overlap(self) -> None:
        self.kernel.trace_source_ready(
            fixture=FIXTURE,
            task_start=TASK_START,
            request_id=f"same-pass-bootstrap-{uuid.uuid4().hex}",
        )
        self.kernel.activate_resource_configuration(
            self.configuration(
                version=2,
                capacities={"codex_semantic": 2},
                bypass_threshold=1,
            )
        )
        _, holder = self.prepare_and_claim(
            "same-pass-holder", ("whisper",), batch_id="00-holder"
        )
        self.kernel.set_resource_circuit_breaker(
            "codex_semantic", state="open", reason="stage queued candidates"
        )
        _, starving = self.prepare_and_claim(
            "same-pass-starving",
            ("codex_semantic", "whisper"),
            batch_id="aa-starving",
        )
        ordinary = [
            self.prepare_and_claim(
                f"same-pass-ordinary-{index}",
                ("codex_semantic",),
                batch_id=f"z{index}-ordinary",
            )[1]
            for index in range(2)
        ]
        self.kernel.set_resource_circuit_breaker(
            "codex_semantic", state="closed", reason="resume scheduler"
        )

        starving_status = self.kernel.resource_status(
            starving.task_id, starving.attempt_id
        )
        self.assertEqual(starving_status.reservation_state, "active")
        ordinary_states = [
            self.kernel.resource_status(item.task_id, item.attempt_id).queue_state
            for item in ordinary
        ]
        self.assertEqual(ordinary_states.count("admitted"), 1)
        self.assertEqual(ordinary_states.count("queued"), 1)
        self.assertEqual(holder.resource_admission.queue_state, "admitted")

    def test_unknown_lease_survives_reclaim_until_evidence_bearing_resolution(self) -> None:
        _, holder = self.prepare_and_claim("unknown-holder", ("whisper",))
        launcher = RecordingLauncher()
        self.kernel.launch_admitted_task(
            holder.attempt_id,
            holder.claim_generation,
            ("whisper",),
            launcher,
        )
        _, waiting = self.prepare_and_claim("unknown-waiting", ("whisper",))
        self.assertEqual(waiting.resource_admission.queue_state, "queued")

        restarted = VideoWorkflowKernel(self.workspace)
        healthy = restarted.resource_reconcile(
            current_coordinator_session_id="coordinator-unknown-holder",
            lost_coordinator_session_ids=(),
        )
        self.assertEqual(healthy["transitioned_lease_ids"], [])
        self.assertEqual(
            restarted.resource_status(holder.task_id, holder.attempt_id).lease_state,
            "active",
        )
        report = restarted.resource_reconcile(
            current_coordinator_session_id="coordinator-after-restart",
            lost_coordinator_session_ids=("coordinator-unknown-holder",),
        )
        self.assertIn(holder.resource_admission.lease_id, report["unknown_lease_ids"])
        unknown = restarted.resource_status(holder.task_id, holder.attempt_id)
        self.assertEqual(unknown.lease_state, "unknown")
        self.assertEqual(
            restarted.resource_capacity_status()["resources"]["whisper"],
            {"capacity": 1, "usage": 1, "available": 0, "state": "full"},
        )

        replacement = restarted.reclaim_task(
            holder.run_dir,
            task_id=holder.task_id,
            expected_attempt_id=holder.attempt_id,
            expected_claim_generation=holder.claim_generation,
            coordinator_session_id="coordinator-unknown-replacement",
            worker_id="worker-unknown-replacement",
            reason="coordinator restarted with unresolved worker ownership",
        )
        self.assertEqual(
            restarted.resource_status(holder.task_id, holder.attempt_id).lease_state,
            "unknown",
        )
        self.assertEqual(replacement.resource_admission.queue_state, "queued")
        with self.assertRaises(KernelConflict):
            self.trusted_provider_kernel().release_resource_lease(
                holder.attempt_id,
                holder.claim_generation,
                launcher.calls[0],
                terminal_evidence=self.provider_terminal_evidence(
                    "late-worker-result",
                    observed_at="2026-07-17T11:00:00+08:00",
                ),
            )

        with self.assertRaises(ContractError):
            restarted.resource_resolve(
                holder.resource_admission.lease_id,
                holder.attempt_id,
                holder.claim_generation,
                resolution_evidence={
                    "evidence_class": "local_process_terminated",
                    "pid": 1234,
                    "observed_at": "2026-07-17T11:01:00+08:00",
                },
            )

        resolution = {
            "evidence_class": "explicit_human_resolution",
            "declared_outcome": "terminated",
            "reason": "operator verified the worker process and provider request ended",
            "observed_termination_basis": "task manager process identity and provider console",
            "coordinator_identity": "coordinator-recovery-test",
            "observed_at": "2026-07-17T11:02:00+08:00",
        }
        resolved = restarted.resource_resolve(
            holder.resource_admission.lease_id,
            holder.attempt_id,
            holder.claim_generation,
            resolution_evidence=resolution,
        )
        self.assertEqual(resolved.lease_state, "resolved")
        admitted_after_resolution = [
            restarted.resource_status(item.task_id, item.attempt_id).queue_state
            for item in (waiting, replacement)
        ]
        self.assertEqual(admitted_after_resolution.count("admitted"), 1)
        self.assertEqual(admitted_after_resolution.count("queued"), 1)

        replay = restarted.resource_resolve(
            holder.resource_admission.lease_id,
            holder.attempt_id,
            holder.claim_generation,
            resolution_evidence=resolution,
        )
        self.assertEqual(replay.lease_state, "resolved")
        conflicting = dict(resolution)
        conflicting["reason"] = "different resolution claim"
        with self.assertRaises(KernelConflict):
            restarted.resource_resolve(
                holder.resource_admission.lease_id,
                holder.attempt_id,
                holder.claim_generation,
                resolution_evidence=conflicting,
            )

        with self.assertRaises(KernelConflict):
            restarted.resource_resolve(
                holder.resource_admission.lease_id,
                holder.attempt_id,
                holder.claim_generation + 1,
                resolution_evidence=resolution,
            )

    def test_provider_resolution_replay_reuses_persisted_proof_before_verifier(
        self,
    ) -> None:
        _, claimed = self.prepare_and_claim(
            "provider-resolution-replay", ("pdf_render",)
        )
        launcher = RecordingLauncher()
        self.kernel.launch_admitted_task(
            claimed.attempt_id,
            claimed.claim_generation,
            ("pdf_render",),
            launcher,
        )
        verifier = OneShotProviderVerifier()
        resolver = VideoWorkflowKernel(
            self.workspace,
            resource_provider_verifiers={"replay-provider": verifier},
        )
        resolver.resource_reconcile(
            current_coordinator_session_id="coordinator-provider-replay-restart",
            lost_coordinator_session_ids=(
                "coordinator-provider-resolution-replay",
            ),
        )
        resolution = {
            "evidence_class": "provider_terminal_result",
            "declared_outcome": "succeeded",
            "provider": "replay-provider",
            "terminal_result_id": "provider-replay-result",
            "observed_at": "2026-07-17T11:30:00+08:00",
        }

        first = resolver.resource_resolve(
            claimed.resource_admission.lease_id,
            claimed.attempt_id,
            claimed.claim_generation,
            resolution_evidence=resolution,
        )
        self.assertEqual(first.lease_state, "resolved")
        resolved_events = [
            event
            for event in resolver.resource_scheduler_status()["events"]
            if event["event_kind"] == "lease_resolved"
            and event["lease_id"] == claimed.resource_admission.lease_id
        ]
        self.assertEqual(len(resolved_events), 1)
        persisted_evidence = resolved_events[0]["payload"]

        replay = resolver.resource_resolve(
            claimed.resource_admission.lease_id,
            claimed.attempt_id,
            claimed.claim_generation,
            resolution_evidence=resolution,
        )
        self.assertEqual(replay.lease_state, "resolved")
        self.assertEqual(len(verifier.calls), 1)

        different_reference_verifier = TrustedProviderVerifier()
        different_reference_resolver = VideoWorkflowKernel(
            self.workspace,
            resource_provider_verifiers={
                "replay-provider": different_reference_verifier,
            },
        )
        replay_with_changed_verifier = different_reference_resolver.resource_resolve(
            claimed.resource_admission.lease_id,
            claimed.attempt_id,
            claimed.claim_generation,
            resolution_evidence=resolution,
        )
        self.assertEqual(replay_with_changed_verifier.lease_state, "resolved")
        self.assertEqual(different_reference_verifier.calls, [])

        changed_outcome = dict(resolution)
        changed_outcome["declared_outcome"] = "failed"
        with self.assertRaises(KernelConflict):
            different_reference_resolver.resource_resolve(
                claimed.resource_admission.lease_id,
                claimed.attempt_id,
                claimed.claim_generation,
                resolution_evidence=changed_outcome,
            )
        changed_provider = dict(resolution)
        changed_provider["provider"] = "different-provider"
        with self.assertRaises(KernelConflict):
            different_reference_resolver.resource_resolve(
                claimed.resource_admission.lease_id,
                claimed.attempt_id,
                claimed.claim_generation,
                resolution_evidence=changed_provider,
            )
        self.assertEqual(different_reference_verifier.calls, [])
        replay_events = [
            event
            for event in different_reference_resolver.resource_scheduler_status()[
                "events"
            ]
            if event["event_kind"] == "lease_resolved"
            and event["lease_id"] == claimed.resource_admission.lease_id
        ]
        self.assertEqual(len(replay_events), 1)
        self.assertEqual(replay_events[0]["payload"], persisted_evidence)

    def test_local_process_resolution_replay_reuses_persisted_proof_before_inspector(
        self,
    ) -> None:
        _, claimed = self.prepare_and_claim(
            "process-resolution-replay", ("latex",)
        )
        launcher = ProcessIdentityLauncher()
        self.kernel.launch_admitted_task(
            claimed.attempt_id,
            claimed.claim_generation,
            ("latex",),
            launcher,
        )
        inspector = OneShotProcessInspector()
        resolver = VideoWorkflowKernel(
            self.workspace,
            local_process_inspector=inspector,
        )
        resolver.resource_reconcile(
            current_coordinator_session_id="coordinator-process-replay-restart",
            lost_coordinator_session_ids=(
                "coordinator-process-resolution-replay",
            ),
        )
        resolution = {
            "evidence_class": "local_process_terminated",
            "declared_outcome": "terminated",
            "pid": 4321,
            "process_creation_identity": (
                "process-created-2026-07-17T11:10:00Z"
            ),
            "launch_token": launcher.calls[0],
            "observed_at": "2026-07-17T11:31:00+08:00",
        }

        first = resolver.resource_resolve(
            claimed.resource_admission.lease_id,
            claimed.attempt_id,
            claimed.claim_generation,
            resolution_evidence=resolution,
        )
        self.assertEqual(first.lease_state, "resolved")
        resolved_events = [
            event
            for event in resolver.resource_scheduler_status()["events"]
            if event["event_kind"] == "lease_resolved"
            and event["lease_id"] == claimed.resource_admission.lease_id
        ]
        self.assertEqual(len(resolved_events), 1)
        persisted_evidence = resolved_events[0]["payload"]

        replay = resolver.resource_resolve(
            claimed.resource_admission.lease_id,
            claimed.attempt_id,
            claimed.claim_generation,
            resolution_evidence=resolution,
        )
        self.assertEqual(replay.lease_state, "resolved")
        self.assertEqual(len(inspector.calls), 1)

        unavailable_inspector = VideoWorkflowKernel(self.workspace)
        replay_without_inspector = unavailable_inspector.resource_resolve(
            claimed.resource_admission.lease_id,
            claimed.attempt_id,
            claimed.claim_generation,
            resolution_evidence=resolution,
        )
        self.assertEqual(replay_without_inspector.lease_state, "resolved")
        conflicting_identity = dict(resolution)
        conflicting_identity["launch_token"] = "0" * 64
        with self.assertRaises(KernelConflict):
            unavailable_inspector.resource_resolve(
                claimed.resource_admission.lease_id,
                claimed.attempt_id,
                claimed.claim_generation,
                resolution_evidence=conflicting_identity,
            )
        replay_events = [
            event
            for event in unavailable_inspector.resource_scheduler_status()["events"]
            if event["event_kind"] == "lease_resolved"
            and event["lease_id"] == claimed.resource_admission.lease_id
        ]
        self.assertEqual(len(replay_events), 1)
        self.assertEqual(replay_events[0]["payload"], persisted_evidence)

    def test_process_and_provider_resolution_require_trusted_verifier_proof(self) -> None:
        _, process_claim = self.prepare_and_claim(
            "resolve-process", ("latex",)
        )
        process_launcher = ProcessIdentityLauncher()
        self.kernel.launch_admitted_task(
            process_claim.attempt_id,
            process_claim.claim_generation,
            ("latex",),
            process_launcher,
        )
        _, provider_claim = self.prepare_and_claim(
            "resolve-provider", ("pdf_render",)
        )
        provider_launcher = RecordingLauncher()
        self.kernel.launch_admitted_task(
            provider_claim.attempt_id,
            provider_claim.claim_generation,
            ("pdf_render",),
            provider_launcher,
        )
        provider_verifier = TrustedProviderVerifier()
        process_inspector = TrustedProcessInspector(matching_process_absent=True)
        restarted = VideoWorkflowKernel(
            self.workspace,
            resource_provider_verifiers={"fake-provider": provider_verifier},
            local_process_inspector=process_inspector,
        )
        restarted.resource_reconcile(
            current_coordinator_session_id="coordinator-resolution-restart",
            lost_coordinator_session_ids=(
                "coordinator-resolve-process",
                "coordinator-resolve-provider",
            ),
        )

        wrong_process = {
            "evidence_class": "local_process_terminated",
            "declared_outcome": "terminated",
            "pid": 4321,
            "process_creation_identity": "process-created-2026-07-17T11:10:00Z",
            "launch_token": "0" * 64,
            "observed_at": "2026-07-17T11:15:00+08:00",
        }
        with self.assertRaises(KernelConflict):
            restarted.resource_resolve(
                process_claim.resource_admission.lease_id,
                process_claim.attempt_id,
                process_claim.claim_generation,
                resolution_evidence=wrong_process,
            )
        matching_process = dict(wrong_process)
        matching_process["launch_token"] = process_launcher.calls[0]
        process_resolved = restarted.resource_resolve(
            process_claim.resource_admission.lease_id,
            process_claim.attempt_id,
            process_claim.claim_generation,
            resolution_evidence=matching_process,
        )
        self.assertEqual(process_resolved.lease_state, "resolved")
        self.assertEqual(
            process_inspector.calls,
            [
                {
                    "pid": 4321,
                    "process_creation_identity": "process-created-2026-07-17T11:10:00Z",
                    "launch_token": process_launcher.calls[0],
                    "lease_id": process_claim.resource_admission.lease_id,
                    "attempt_id": process_claim.attempt_id,
                }
            ],
        )

        without_verifier = VideoWorkflowKernel(self.workspace)
        with self.assertRaises(ContractError):
            without_verifier.resource_resolve(
                provider_claim.resource_admission.lease_id,
                provider_claim.attempt_id,
                provider_claim.claim_generation,
                resolution_evidence={
                    "evidence_class": "provider_terminal_result",
                    "declared_outcome": "succeeded",
                    "provider": "fake-provider",
                    "terminal_result_id": "provider-result-1",
                    "observed_at": "2026-07-17T11:16:00+08:00",
                },
            )
        provider_resolved = restarted.resource_resolve(
            provider_claim.resource_admission.lease_id,
            provider_claim.attempt_id,
            provider_claim.claim_generation,
            resolution_evidence={
                "evidence_class": "provider_terminal_result",
                "declared_outcome": "succeeded",
                "provider": "fake-provider",
                "terminal_result_id": "provider-result-1",
                "observed_at": "2026-07-17T11:16:00+08:00",
            },
        )
        self.assertEqual(provider_resolved.lease_state, "resolved")
        self.assertEqual(
            provider_verifier.calls,
            [
                {
                    "provider": "fake-provider",
                    "terminal_result_id": "provider-result-1",
                    "lease_id": provider_claim.resource_admission.lease_id,
                    "attempt_id": provider_claim.attempt_id,
                    "launch_token": provider_launcher.calls[0],
                    "declared_outcome": "succeeded",
                }
            ],
        )
        resolved_events = [
            event
            for event in restarted.resource_scheduler_status()["events"]
            if event["event_kind"] == "lease_resolved"
        ]
        proofs_by_lease = {
            event["lease_id"]: event["payload"]["evidence"]
            for event in resolved_events
        }
        self.assertEqual(
            proofs_by_lease[process_claim.resource_admission.lease_id][
                "inspection_proof_reference"
            ],
            (
                "process-inspection://absent/4321/"
                "process-created-2026-07-17T11:10:00Z"
            ),
        )
        self.assertEqual(
            proofs_by_lease[provider_claim.resource_admission.lease_id][
                "verification_proof_reference"
            ],
            "provider-proof://fake-provider/provider-result-1",
        )

    def test_local_process_resolution_stays_unknown_while_matching_process_runs(
        self,
    ) -> None:
        _, claimed = self.prepare_and_claim(
            "resolve-running-process", ("latex",)
        )
        launcher = ProcessIdentityLauncher()
        self.kernel.launch_admitted_task(
            claimed.attempt_id,
            claimed.claim_generation,
            ("latex",),
            launcher,
        )
        inspector = TrustedProcessInspector(matching_process_absent=False)
        restarted = VideoWorkflowKernel(
            self.workspace,
            local_process_inspector=inspector,
        )
        restarted.resource_reconcile(
            current_coordinator_session_id="coordinator-running-restart",
            lost_coordinator_session_ids=("coordinator-resolve-running-process",),
        )
        with self.assertRaises(KernelConflict):
            restarted.resource_resolve(
                claimed.resource_admission.lease_id,
                claimed.attempt_id,
                claimed.claim_generation,
                resolution_evidence={
                    "evidence_class": "local_process_terminated",
                    "declared_outcome": "terminated",
                    "pid": 4321,
                    "process_creation_identity": (
                        "process-created-2026-07-17T11:10:00Z"
                    ),
                    "launch_token": launcher.calls[0],
                    "observed_at": "2026-07-17T11:18:00+08:00",
                },
            )
        self.assertEqual(
            restarted.resource_status(claimed.task_id, claimed.attempt_id).lease_state,
            "unknown",
        )
        self.assertEqual(len(inspector.calls), 1)

    def test_resource_resolution_rejects_self_asserted_trust_booleans(self) -> None:
        _, provider_claim = self.prepare_and_claim(
            "resolve-self-asserted-provider", ("pdf_render",)
        )
        launcher = RecordingLauncher()
        self.kernel.launch_admitted_task(
            provider_claim.attempt_id,
            provider_claim.claim_generation,
            ("pdf_render",),
            launcher,
        )
        restarted = VideoWorkflowKernel(self.workspace)
        restarted.resource_reconcile(
            current_coordinator_session_id="coordinator-self-asserted-restart",
            lost_coordinator_session_ids=(
                "coordinator-resolve-self-asserted-provider",
            ),
        )
        with self.assertRaises(ContractError):
            restarted.resource_resolve(
                provider_claim.resource_admission.lease_id,
                provider_claim.attempt_id,
                provider_claim.claim_generation,
                resolution_evidence={
                    "evidence_class": "provider_terminal_result",
                    "declared_outcome": "succeeded",
                    "provider": "fake-provider",
                    "terminal_result_id": "self-asserted-result",
                    "authenticated": True,
                    "observed_at": "2026-07-17T11:20:00+08:00",
                },
            )


if __name__ == "__main__":
    unittest.main()
