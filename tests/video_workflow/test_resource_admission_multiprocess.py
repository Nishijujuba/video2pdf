from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import unittest
import uuid


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from video2pdf_workflow_kernel.errors import (  # noqa: E402
    ResourceAdmissionBlocked,
)
from video2pdf_workflow_kernel.kernel import VideoWorkflowKernel  # noqa: E402
from video2pdf_workflow_kernel.utils import read_json, write_json_atomic  # noqa: E402


FIXTURE = PROJECT_ROOT / "tests/video_workflow/fixtures/source-ready-tracer"
TEST_RUNS = PROJECT_ROOT / "待删除/kernel-test-runs"
TASK_START = "2026-07-17T13:00:00+08:00"
WORKER = (
    PROJECT_ROOT
    / "tests/video_workflow/helpers/resource_admission_process_worker.py"
)


class ResourceAdmissionMultiprocessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = (
            TEST_RUNS / f"slice3-mp-{uuid.uuid4().hex[:12]}" / "workspace"
        )
        self.workspace.mkdir(parents=True)
        self.kernel = VideoWorkflowKernel(self.workspace)

    def prepare(self, label: str, resources: tuple[str, ...]):
        traced = self.kernel.trace_source_ready(
            fixture=FIXTURE,
            task_start=TASK_START,
            request_id=f"mp-{label}-{uuid.uuid4().hex}",
        )
        return self.kernel.prepare_source_acquisition_task(
            traced.run_dir,
            logical_task_key=f"multiprocess-{label}",
            prepared_at=TASK_START,
            required_resources=resources,
        )

    def run_workers(
        self,
        mode: str,
        payloads: list[dict],
        *,
        expected_exit_code: int = 0,
    ) -> tuple[list[dict], list[subprocess.CompletedProcess[str]]]:
        operation = self.workspace / "待删除" / f"multiprocess-{uuid.uuid4().hex}"
        operation.mkdir(parents=True)
        start = operation / "start.signal"
        processes: list[tuple[subprocess.Popen[str], Path]] = []
        for index, payload in enumerate(payloads):
            input_path = operation / f"input-{index}.json"
            result_path = operation / f"result-{index}.json"
            write_json_atomic(input_path, payload)
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-X",
                    "utf8",
                    "-B",
                    str(WORKER),
                    mode,
                    str(input_path),
                    str(result_path),
                    str(start),
                ],
                cwd=PROJECT_ROOT,
                text=True,
                encoding="utf-8",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            processes.append((process, result_path))
        start.write_text("start\n", encoding="ascii")
        completed: list[subprocess.CompletedProcess[str]] = []
        for process, _ in processes:
            try:
                stdout, stderr = process.communicate(timeout=45)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate(timeout=10)
                self.fail(
                    "multiprocess worker timed out: "
                    f"stdout={stdout!r}; stderr={stderr!r}"
                )
            completed.append(
                subprocess.CompletedProcess(
                    process.args,
                    process.returncode,
                    stdout,
                    stderr,
                )
            )
        for result in completed:
            self.assertEqual(
                result.returncode,
                expected_exit_code,
                f"stdout={result.stdout!r}; stderr={result.stderr!r}",
            )
        results = [
            read_json(result_path)
            for _, result_path in processes
            if result_path.is_file()
        ]
        return results, completed

    def test_same_task_claim_race_has_exactly_one_owner(self) -> None:
        prepared = self.prepare("same-task", ("whisper",))
        messages, _ = self.run_workers(
            "claim",
            [
                {
                    "workspace": str(self.workspace),
                    "run_dir": str(prepared.run_dir),
                    "task_id": prepared.task_id,
                    "identity": identity,
                }
                for identity in ("same-a", "same-b")
            ],
        )
        self.assertEqual(sum(message["status"] == "ok" for message in messages), 1)
        self.assertEqual(
            sum(
                message["status"] == "error"
                and message["error_type"] == "KernelConflict"
                for message in messages
            ),
            1,
        )

    def test_multi_resource_quota_race_is_atomic_across_processes(self) -> None:
        resources = ("codex_semantic", "whisper")
        prepared = [
            self.prepare(f"quota-{identity}", resources)
            for identity in ("a", "b")
        ]
        messages, _ = self.run_workers(
            "claim",
            [
                {
                    "workspace": str(self.workspace),
                    "run_dir": str(item.run_dir),
                    "task_id": item.task_id,
                    "identity": identity,
                }
                for item, identity in zip(
                    prepared, ("quota-a", "quota-b"), strict=True
                )
            ],
        )
        self.assertTrue(
            all(message["status"] == "ok" for message in messages), messages
        )
        states = [message["queue_state"] for message in messages]
        self.assertEqual(states.count("admitted"), 1)
        self.assertEqual(states.count("queued"), 1)

        restarted = VideoWorkflowKernel(self.workspace)
        statuses = [
            restarted.resource_status(message["task_id"], message["attempt_id"])
            for message in messages
        ]
        self.assertEqual(sum(status.lease_id is not None for status in statuses), 1)
        self.assertEqual(sum(status.lease_id is None for status in statuses), 1)
        capacity = restarted.resource_capacity_status()["resources"]
        self.assertEqual(capacity["codex_semantic"]["usage"], 1)
        self.assertEqual(capacity["whisper"]["usage"], 1)
        scheduler = restarted.resource_scheduler_status()
        self.assertEqual(scheduler["sequences"]["enqueue"], 2)
        self.assertEqual(scheduler["sequences"]["admission"], 1)
        event_sequences = [event["event_seq"] for event in scheduler["events"]]
        self.assertEqual(event_sequences, sorted(set(event_sequences)))

    def test_launch_race_invokes_callback_exactly_once_across_processes(self) -> None:
        prepared = self.prepare(
            "launch-race", ("codex_semantic", "whisper")
        )
        claimed = self.kernel.claim_task(
            prepared.run_dir,
            prepared.task_id,
            coordinator_session_id="coordinator-launch-race",
            worker_id="worker-launch-race",
        )
        messages, _ = self.run_workers(
            "launch",
            [
                {
                    "workspace": str(self.workspace),
                    "attempt_id": claimed.attempt_id,
                    "claim_generation": claimed.claim_generation,
                    "required_resources": ["codex_semantic", "whisper"],
                    "identity": identity,
                }
                for identity in ("launch-a", "launch-b")
            ],
        )
        self.assertEqual(
            sum(len(message["callbacks"]) for message in messages), 1
        )
        self.assertEqual(sum(message["status"] == "ok" for message in messages), 1)
        self.assertEqual(
            sum(
                message["status"] == "error"
                and message["error_type"] == ResourceAdmissionBlocked.__name__
                for message in messages
            ),
            1,
        )

    def test_hard_crash_after_callback_cannot_replay_launch(self) -> None:
        prepared = self.prepare("launch-hard-crash", ("pdf_render",))
        claimed = self.kernel.claim_task(
            prepared.run_dir,
            prepared.task_id,
            coordinator_session_id="coordinator-launch-hard-crash",
            worker_id="worker-launch-hard-crash",
        )
        marker = self.workspace / "待删除" / "hard-crash-launch-token.txt"
        marker.parent.mkdir(parents=True, exist_ok=True)
        results, completed = self.run_workers(
            "hard-crash-launch",
            [
                {
                    "workspace": str(self.workspace),
                    "attempt_id": claimed.attempt_id,
                    "claim_generation": claimed.claim_generation,
                    "required_resources": ["pdf_render"],
                    "marker_path": str(marker),
                }
            ],
            expected_exit_code=71,
        )
        self.assertEqual(results, [])
        self.assertEqual(completed[0].returncode, 71)
        self.assertEqual(
            marker.read_text(encoding="ascii"),
            claimed.resource_admission.launch_token,
        )

        restarted = VideoWorkflowKernel(self.workspace)
        stranded = restarted.resource_status(claimed.task_id, claimed.attempt_id)
        self.assertEqual(stranded.lease_state, "starting")
        self.assertEqual(stranded.launch_authorization_state, "CONSUMED")
        self.assertFalse(stranded.launch_eligible)
        calls: list[str] = []
        with self.assertRaises(ResourceAdmissionBlocked):
            restarted.launch_admitted_task(
                claimed.attempt_id,
                claimed.claim_generation,
                ("pdf_render",),
                lambda launch_token: calls.append(launch_token) or "started",
            )
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
