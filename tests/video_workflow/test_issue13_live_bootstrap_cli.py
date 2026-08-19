from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROVIDER_FIXTURE = (
    PROJECT_ROOT
    / "tests"
    / "video_workflow"
    / "fixtures"
    / "providers"
    / "bilibili"
    / "fresh-download"
)
LEGACY_FIXTURE = (
    PROJECT_ROOT / "tests" / "video_workflow" / "fixtures" / "source-ready-tracer"
)
SOURCE_URL = "https://www.bilibili.com/video/BV1Gp3s69EZb/?p=1"
CANONICAL_ITEM_ID = "BV1Gp3s69EZb:p1"
SOURCE_IDENTITY = "0a0c056f01eb6f2a9467216dc44e80f92cb3ba8ece746e8e7cf4b61dce1e227a"
ORIGINAL_TITLE = "Issue 13 real Bilibili cutover candidate"

from tests.video_workflow._test_run import child_environment, new_case_dir


def _recorded_candidate_provider(case_root: Path) -> Path:
    recording_root = case_root / "recorded-provider"
    shutil.copytree(PROVIDER_FIXTURE, recording_root)

    metadata_path = recording_root / "stdout" / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata.update(
        {
            "id": "BV1Gp3s69EZb_p1",
            "bvid": "BV1Gp3s69EZb",
            "page": 1,
            "title": ORIGINAL_TITLE,
            "webpage_url": SOURCE_URL,
        }
    )
    metadata_bytes = (
        json.dumps(metadata, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    metadata_path.write_bytes(metadata_bytes)

    manifest_path = recording_root / "recording.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for command in manifest["commands"]:
        if command["argv"][-1].startswith("https://www.bilibili.com/video/"):
            command["argv"][-1] = SOURCE_URL
        if command["operation"] == "metadata_probe":
            command["stdout"]["sha256"] = hashlib.sha256(metadata_bytes).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return recording_root


class Issue13LiveBootstrapCliTests(unittest.TestCase):
    def test_public_cli_replays_a_real_bilibili_p1_bootstrap_without_cookie_leak(
        self,
    ) -> None:
        case_root = new_case_dir(self.id(), label="issue13-live-bootstrap-cli")
        candidate_project_root = case_root / "candidate-project"
        workspace_root = candidate_project_root / "workspace"
        workspace_root.mkdir(parents=True)
        recording_root = _recorded_candidate_provider(case_root)
        cookie_path = case_root / "private" / "source-cookie.txt"
        cookie_path.parent.mkdir()
        cookie_bytes = (
            b"# Netscape HTTP Cookie File\n"
            b".bilibili.com\tTRUE\t/\tTRUE\t2147483647\tSESSDATA\tissue13-secret\n"
        )
        cookie_path.write_bytes(cookie_bytes)

        completed = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                "-B",
                str(PROJECT_ROOT / "scripts" / "video_workflow.py"),
                "bootstrap-probe",
                "--workspace-root",
                str(workspace_root),
                "--platform",
                "bilibili",
                "--source-url",
                SOURCE_URL,
                "--cookie-file",
                str(cookie_path),
                "--provider-recording",
                str(recording_root),
                "--task-start",
                "2026-08-11T09:00:00+08:00",
                "--request-id",
                "issue13-real-candidate-bootstrap",
            ],
            cwd=PROJECT_ROOT,
            env=child_environment(self.id() + "-cli"),
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )

        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        envelope = json.loads(completed.stdout)
        self.assertEqual("probe_complete", envelope["classification"])
        self.assertEqual(
            {
                "canonical_item_id": CANONICAL_ITEM_ID,
                "source_identity": SOURCE_IDENTITY,
                "original_title": ORIGINAL_TITLE,
            },
            {
                key: envelope["data"][key]
                for key in (
                    "canonical_item_id",
                    "source_identity",
                    "original_title",
                )
            },
        )

        record_path = Path(envelope["data"]["probe_record"])
        record = json.loads(record_path.read_text(encoding="utf-8"))
        self.assertEqual("bootstrap-record", record["schema_name"])
        self.assertEqual("2.0.0", record["schema_version"])
        self.assertEqual("bilibili", record["canonical_platform"])
        self.assertEqual(CANONICAL_ITEM_ID, record["canonical_item_id"])
        self.assertEqual(SOURCE_IDENTITY, record["source_identity"])
        self.assertEqual(ORIGINAL_TITLE, record["original_title"])
        self.assertEqual(SOURCE_URL, record["source_request"]["canonical_locator"])
        self.assertEqual(
            "recorded_fixture", record["probe_execution"]["provider_kind"]
        )

        disposable_root = candidate_project_root / "待删除"
        self.assertTrue(record_path.is_relative_to(disposable_root))
        localized_cookies = [
            path
            for path in disposable_root.rglob("*")
            if path.is_file() and path.read_bytes() == cookie_bytes
        ]
        self.assertEqual(1, len(localized_cookies))
        self.assertNotEqual(cookie_path.resolve(), localized_cookies[0].resolve())

        persisted_text = json.dumps(record, ensure_ascii=False, sort_keys=True)
        observable_text = completed.stdout + completed.stderr + persisted_text
        self.assertNotIn(str(cookie_path), observable_text)
        self.assertNotIn("issue13-secret", observable_text)
        self.assertIn(
            "<localized-cookie-file>",
            record["probe_execution"]["command_argv_redacted"],
        )

    def test_public_cli_creates_a_pending_deterministic_bilibili_p1_bootstrap(
        self,
    ) -> None:
        case_root = new_case_dir(self.id(), label="issue13-deterministic-bootstrap")
        candidate_project_root = case_root / "candidate-project"
        workspace_root = candidate_project_root / "workspace"
        workspace_root.mkdir(parents=True)

        completed = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                "-B",
                str(PROJECT_ROOT / "scripts" / "video_workflow.py"),
                "bootstrap-probe",
                "--workspace-root",
                str(workspace_root),
                "--platform",
                "bilibili",
                "--source-url",
                "https://www.bilibili.com/video/BV1Gp3s69EZb/",
                "--explicit-item-selector",
                "p1",
                "--original-title",
                ORIGINAL_TITLE,
                "--task-start",
                "2026-08-11T09:00:00+08:00",
                "--request-id",
                "issue13-deterministic-candidate-bootstrap",
            ],
            cwd=PROJECT_ROOT,
            env=child_environment(self.id() + "-cli"),
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )

        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        envelope = json.loads(completed.stdout)
        self.assertEqual("probe_complete", envelope["classification"])
        self.assertEqual(CANONICAL_ITEM_ID, envelope["data"]["canonical_item_id"])
        self.assertEqual(SOURCE_IDENTITY, envelope["data"]["source_identity"])
        self.assertEqual(ORIGINAL_TITLE, envelope["data"]["original_title"])

        record = json.loads(
            Path(envelope["data"]["probe_record"]).read_text(encoding="utf-8")
        )
        self.assertEqual("2.0.0", record["schema_version"])
        self.assertEqual(
            "deterministic_locator", record["probe_execution"]["provider_kind"]
        )
        self.assertEqual("pending", record["availability"]["status"])
        self.assertIsNone(record["availability"]["duration_seconds"])
        self.assertEqual([], record["probe_execution"]["command_argv_redacted"])
        self.assertEqual(
            "https://www.bilibili.com/video/BV1Gp3s69EZb/",
            record["source_request"]["canonical_locator"],
        )
        self.assertFalse(
            (candidate_project_root / "待删除" / "bootstrap" / "credentials").exists()
        )
        self.assertFalse(
            (
                candidate_project_root
                / "待删除"
                / "bootstrap"
                / "provider-attempts"
            ).exists()
        )

    def test_public_cli_rejects_mixed_bootstrap_modes(self) -> None:
        case_root = new_case_dir(self.id(), label="issue13-bootstrap-mode-errors")
        cookie_path = case_root / "cookie.txt"
        cookie_path.write_text(
            "# Netscape HTTP Cookie File\n"
            ".bilibili.com\tTRUE\t/\tTRUE\t2147483647\tSESSDATA\trecorded\n",
            encoding="utf-8",
        )
        common = [
            sys.executable,
            "-X",
            "utf8",
            "-B",
            str(PROJECT_ROOT / "scripts" / "video_workflow.py"),
            "bootstrap-probe",
            "--task-start",
            "2026-08-11T09:00:00+08:00",
            "--request-id",
            "issue13-bootstrap-mode-errors",
        ]
        cases = {
            "fixture-with-platform": (
                [
                    "--workspace-root",
                    str(case_root / "fixture-workspace"),
                    "--fixture",
                    str(LEGACY_FIXTURE),
                    "--platform",
                    "bilibili",
                ],
                "usage_error",
            ),
            "deterministic-with-cookie": (
                [
                    "--workspace-root",
                    str(case_root / "deterministic-workspace"),
                    "--platform",
                    "bilibili",
                    "--source-url",
                    SOURCE_URL,
                    "--original-title",
                    ORIGINAL_TITLE,
                    "--cookie-file",
                    str(cookie_path),
                ],
                "contract_invalid",
            ),
            "recorded-without-cookie": (
                [
                    "--workspace-root",
                    str(case_root / "recorded-workspace"),
                    "--platform",
                    "bilibili",
                    "--source-url",
                    SOURCE_URL,
                    "--provider-recording",
                    str(PROVIDER_FIXTURE),
                ],
                "contract_invalid",
            ),
        }

        for label, (arguments, expected_classification) in cases.items():
            with self.subTest(label=label):
                completed = subprocess.run(
                    [*common, *arguments],
                    cwd=PROJECT_ROOT,
                    env=child_environment(self.id() + "-" + label),
                    text=True,
                    encoding="utf-8",
                    capture_output=True,
                    check=False,
                )
                self.assertNotEqual(0, completed.returncode)
                envelope = json.loads(completed.stdout)
                self.assertEqual(expected_classification, envelope["classification"])


if __name__ == "__main__":
    unittest.main()
