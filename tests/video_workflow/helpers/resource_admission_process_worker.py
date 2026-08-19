from __future__ import annotations

import os
from pathlib import Path
import sys
import time


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from video2pdf_workflow_kernel.kernel import VideoWorkflowKernel  # noqa: E402
from video2pdf_workflow_kernel.utils import read_json, write_json_atomic  # noqa: E402


def wait_for_start(path: Path) -> None:
    deadline = time.monotonic() + 30
    while not path.is_file():
        if time.monotonic() >= deadline:
            raise TimeoutError("multiprocess start barrier was not released")
        time.sleep(0.01)


def claim(payload: dict) -> dict:
    try:
        result = VideoWorkflowKernel(Path(payload["workspace"])).claim_task(
            Path(payload["run_dir"]),
            payload["task_id"],
            coordinator_session_id=f"coordinator-{payload['identity']}",
            worker_id=f"worker-{payload['identity']}",
        )
    except BaseException as exc:
        return {
            "status": "error",
            "identity": payload["identity"],
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    return {
        "status": "ok",
        "identity": payload["identity"],
        "task_id": result.task_id,
        "attempt_id": result.attempt_id,
        "queue_state": result.resource_admission.queue_state,
    }


def launch(payload: dict) -> dict:
    callbacks: list[str] = []

    def launcher(launch_token: str) -> str:
        callbacks.append(launch_token)
        return "started"

    try:
        result = VideoWorkflowKernel(Path(payload["workspace"])).launch_admitted_task(
            payload["attempt_id"],
            int(payload["claim_generation"]),
            tuple(payload["required_resources"]),
            launcher,
        )
    except BaseException as exc:
        return {
            "status": "error",
            "identity": payload["identity"],
            "error_type": type(exc).__name__,
            "error": str(exc),
            "callbacks": callbacks,
        }
    return {
        "status": "ok",
        "identity": payload["identity"],
        "result": result,
        "callbacks": callbacks,
    }


def hard_crash_launch(payload: dict) -> None:
    def launcher(launch_token: str) -> str:
        with open(payload["marker_path"], "xb") as stream:
            stream.write(launch_token.encode("ascii"))
            stream.flush()
            os.fsync(stream.fileno())
        os._exit(71)

    VideoWorkflowKernel(Path(payload["workspace"])).launch_admitted_task(
        payload["attempt_id"],
        int(payload["claim_generation"]),
        tuple(payload["required_resources"]),
        launcher,
    )


def main() -> int:
    if len(sys.argv) != 5:
        raise SystemExit(
            "usage: resource_admission_process_worker.py MODE INPUT RESULT START"
        )
    mode, input_path, result_path, start_path = sys.argv[1:]
    payload = read_json(Path(input_path))
    wait_for_start(Path(start_path))
    if mode == "hard-crash-launch":
        hard_crash_launch(payload)
        raise AssertionError("hard-crash launcher returned unexpectedly")
    if mode == "claim":
        result = claim(payload)
    elif mode == "launch":
        result = launch(payload)
    else:
        raise SystemExit(f"unknown worker mode: {mode}")
    write_json_atomic(Path(result_path), result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
