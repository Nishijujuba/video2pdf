from __future__ import annotations

import json
import sqlite3
import unittest

from tests.video_workflow.test_issue13_whisper_source_cli import (
    PROJECT_ROOT,
    _run_public_cli,
)
from tests.video_workflow.test_production_source_tasks import (
    ProductionSourceTaskTests as _ProductionSourceTaskTests,
)


PROVIDER_RECORDINGS = {
    platform: PROJECT_ROOT
    / "tests"
    / "video_workflow"
    / "fixtures"
    / "providers"
    / platform
    / "fresh-download"
    for platform in ("bilibili", "youtube")
}


class SourceAcquireReconcileIdentityTests(unittest.TestCase):
    def test_terminal_proof_reconcile_preserves_platform_claim_identity(self) -> None:
        fixture = _ProductionSourceTaskTests()
        cases = (
            ("youtube", fixture._initialized_youtube_run),
            ("bilibili", fixture._initialized_bilibili_v4_run),
        )

        for platform, initialize in cases:
            with self.subTest(platform=platform):
                _kernel, run_dir = initialize()
                cookie_file = run_dir / "待删除" / "reconcile-test-cookie.txt"
                cookie_file.write_text(
                    "# Netscape HTTP Cookie File\n"
                    ".example.test\tTRUE\t/\tTRUE\t2147483647\tSID\trecorded\n",
                    encoding="utf-8",
                )
                faulted = _run_public_cli(
                    self.id() + f"-{platform}-faulted",
                    "source-acquire",
                    "--run-dir",
                    str(run_dir),
                    "--cookie-file",
                    str(cookie_file),
                    "--provider-recording",
                    str(PROVIDER_RECORDINGS[platform]),
                    "--fault-point",
                    "after_provider_terminal_proof_persisted",
                )
                self.assertNotEqual(
                    0, faulted.returncode, faulted.stdout + faulted.stderr
                )

                reconciled = _run_public_cli(
                    self.id() + f"-{platform}-reconciled",
                    "source-acquire-reconcile",
                    "--run-dir",
                    str(run_dir),
                )
                self.assertEqual(
                    0, reconciled.returncode, reconciled.stdout + reconciled.stderr
                )
                self.assertEqual(
                    1, json.loads(reconciled.stdout)["data"]["tasks_advanced"]
                )

                task = next(
                    json.loads(path.read_text(encoding="utf-8"))
                    for path in (run_dir / "workflow" / "tasks").glob("*/task.json")
                    if json.loads(path.read_text(encoding="utf-8"))["task_stage"]
                    == "provider_acquisition"
                )
                with sqlite3.connect(
                    run_dir.parent / ".workflow-control" / "control.sqlite3"
                ) as database:
                    claim = database.execute(
                        "SELECT claim_generation,attempt_id,coordinator_session_id,"
                        "worker_id FROM task_claims WHERE task_id=?",
                        (task["task_id"],),
                    ).fetchone()
                    self.assertIsNotNone(claim)
                    lease = database.execute(
                        "SELECT state,launch_authorization_state FROM resource_leases "
                        "WHERE attempt_id=?",
                        (claim[1],),
                    ).fetchone()

                self.assertEqual(2, claim[0])
                self.assertEqual(f"source-acquire-{platform}", claim[2])
                self.assertEqual(f"source-acquire-{platform}-provider", claim[3])
                self.assertEqual(("starting", "AVAILABLE"), lease)


if __name__ == "__main__":
    unittest.main()
