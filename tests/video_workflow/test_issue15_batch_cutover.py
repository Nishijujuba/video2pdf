from __future__ import annotations

import hashlib
from contextlib import contextmanager, redirect_stdout
from io import StringIO
import json
import sqlite3
import sys
from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tests.video_workflow._test_run import new_case_dir
from video2pdf_workflow_kernel import cli as kernel_cli
from video2pdf_workflow_kernel.batch_authority import (
    BATCH_AUTHORITY_FILE,
    BATCH_CUTOVER_DB,
    BatchCutoverFault,
    BatchCutoverPublisher,
    _validate_post_publication,
)
from video2pdf_workflow_kernel.errors import (
    ContractError,
    ControlStoreUnavailable,
    KernelConflict,
)
from video2pdf_workflow_kernel import release_maintenance
from video2pdf_workflow_kernel.release_activation import WorkflowReleaseActivation
from video2pdf_workflow_kernel.utils import (
    canonical_json_bytes,
    read_json,
)


class Issue15BatchCutoverTests(unittest.TestCase):
    def _case(self, label: str) -> tuple[Path, Path]:
        root = new_case_dir(self.id(), label=label)
        evidence = root / "exit-evidence-manifest.json"
        evidence.write_text(
            '{"slice":{"number":14,"name":"batch-projection-cutover"}}',
            encoding="utf-8",
        )
        return root, evidence

    @staticmethod
    def _bindings(root: Path) -> tuple[dict, dict]:
        global_binding = {
            "path": str(root / "active_global_gate.json"),
            "file_sha256": "a" * 64,
            "generation": 1,
        }
        platform_bindings = {
            platform: {
                "platform": platform,
                "authority_path": str(root / f"{platform}.json"),
                "authority_sha256": character * 64,
                "generation": 1,
            }
            for platform, character in (("bilibili", "b"), ("youtube", "c"))
        }
        return global_binding, platform_bindings

    @contextmanager
    def _publisher_boundary(self, root: Path):
        global_binding, platform_bindings = self._bindings(root)
        with (
            patch(
                "video2pdf_workflow_kernel.batch_authority._validate_post_publication",
                return_value="d" * 40,
            ),
            patch(
                "video2pdf_workflow_kernel.batch_authority.GlobalGatePublisher.require_current",
                return_value=global_binding,
            ),
            patch(
                "video2pdf_workflow_kernel.batch_authority.BilibiliPlatformCutoverPublisher.require_current",
                side_effect=lambda *, platform, control_store_root: platform_bindings[platform],
            ),
        ):
            yield platform_bindings

    def _activate_case(self, label: str) -> tuple[Path, Path, BatchCutoverPublisher]:
        root, evidence = self._case(label)
        publisher = BatchCutoverPublisher(project_root=PROJECT_ROOT)
        with self._publisher_boundary(root):
            publisher.activate(
                control_store_root=root,
                exit_evidence=evidence,
                activated_at="2026-08-19T00:00:00Z",
            )
        return root, evidence, publisher

    def test_batch_and_release_maintenance_commands_are_public(self) -> None:
        choices = kernel_cli._parser()._subparsers._group_actions[0].choices

        self.assertEqual(
            {
                command
                for command in choices
                if command
                in {
                    "batch-activate",
                    "batch-authority-refresh",
                    "batch-reconcile",
                    "batch-authority-check",
                    "global-gate-activate",
                    "global-gate-reconcile",
                    "global-gate-policy-authority-refresh",
                    "platform-kernel-prepare",
                    "platform-kernel-candidate-activate",
                    "platform-kernel-activate",
                    "platform-kernel-reconcile",
                    "youtube-platform-authority-refresh",
                    "init-cutover-candidate",
                    "platform-kernel-candidate-reconcile",
                    "platform-kernel-candidate-rebind",
                    "release-profile-publish",
                    "release-profile-activate",
                    "release-audit",
                    "retire-cutover-authority",
                }
            },
            {
                "release-profile-publish",
                "release-profile-activate",
                "release-audit",
                "retire-cutover-authority",
            },
        )

    def test_missing_batch_authority_database_is_rejected_without_creating_it(self) -> None:
        root = new_case_dir(self.id(), label="missing-authority-database")

        with self.assertRaises(KernelConflict) as raised:
            BatchCutoverPublisher(project_root=PROJECT_ROOT).require_current(
                control_store_root=root
            )

        self.assertEqual(
            raised.exception.data.get("first_failing_gate"),
            "batch_cutover_authority",
        )
        self.assertEqual(
            raised.exception.data.get("error_code"),
            "batch_cutover_authority_stale",
        )
        self.assertFalse((root / BATCH_CUTOVER_DB).exists())

    def test_release_maintenance_and_batch_commands_return_workflow_envelopes(
        self,
    ) -> None:
        activation = {
            "activation_path": "D:/repo/config/workflow-admission-activation.v1.json",
            "profile_path": "D:/repo/config/workflow-release-profile.v1.json",
            "profile_sha256": "a" * 64,
            "release_id": "workflow-2.0",
            "generation": 1,
            "tombstone_path": "D:/workspace/.workflow-release-history/cutover-authority-tombstone.json",
            "single_video_admission": "profile_backed",
            "batch_admission": "profile_backed",
            "archived_cutover_commands": True,
            "profile_publication": "published_and_audited",
        }
        root = new_case_dir(self.id(), label="release-maintenance")
        candidate = root / "candidate-profile.json"
        candidate.write_bytes(
            (PROJECT_ROOT / "config/workflow-release-profile.v1.json").read_bytes()
        )
        published = root / "published-profile.json"
        evidence_arguments = [
            "--global-gate-exit-evidence", str(root / "global-gate.json"),
            "--bilibili-exit-evidence", str(root / "bilibili.json"),
            "--youtube-exit-evidence", str(root / "youtube.json"),
            "--batch-exit-evidence", str(root / "batch.json"),
        ]
        with patch.object(
            WorkflowReleaseActivation, "activate", return_value=activation
        ) as activate_release:
            args = kernel_cli._parser().parse_args(
                [
                    "release-profile-activate",
                    "--project-config", str(root / "workflow-project.v1.json"),
                    "--activated-at", "2026-08-28T00:00:00+08:00",
                    *evidence_arguments,
                ]
            )
            envelope = kernel_cli._execute(args, PROJECT_ROOT)
        self.assertEqual(
            envelope["classification"], "workflow_release_profile_activated"
        )
        self.assertEqual(envelope["evidence_path"], activation["activation_path"])
        self.assertEqual(activate_release.call_count, 1)
        validated_evidence = {
            capability: {"path": str(root / f"{capability}.json")}
            for capability in ("global_gate", "bilibili", "youtube", "batch")
        }

        with (
            patch.object(
                release_maintenance,
                "PROFILE_RELATIVE_PATH",
                published.relative_to(PROJECT_ROOT),
            ),
            patch.object(
                release_maintenance.ReleaseMaintenance,
                "_validate_release_package",
                return_value=validated_evidence,
            ) as validate_release_package,
            patch.object(
                release_maintenance.ReleaseMaintenance,
                "_validate_historical_release_package",
                return_value=validated_evidence,
            ) as validate_historical_release_package,
        ):
            stdout = StringIO()
            with redirect_stdout(stdout):
                published_exit = kernel_cli.main(
                    [
                        "release-profile-publish",
                        "--candidate-profile", str(candidate),
                        *evidence_arguments,
                    ]
                )
            published_result = json.loads(stdout.getvalue())
            self.assertEqual(published_exit, 0)
            self.assertEqual(
                published_result["classification"],
                "workflow_release_profile_published",
            )
            self.assertEqual(read_json(published), read_json(candidate))
            self.assertEqual(validate_release_package.call_count, 1)

            authoritative_bytes = published.read_bytes()
            stdout = StringIO()
            with redirect_stdout(stdout):
                failed_exit = kernel_cli.main(
                    [
                        "release-profile-publish",
                        "--candidate-profile",
                        str(
                            PROJECT_ROOT
                            / "tests/video_workflow/fixtures/contracts/workflow-release-profile.invalid.json"
                        ),
                        *evidence_arguments,
                    ]
                )
            failed_result = json.loads(stdout.getvalue())
            self.assertEqual(failed_exit, 20)
            self.assertEqual(failed_result["status"], "error")
            self.assertEqual(
                failed_result["data"]["error_code"],
                "workflow_release_profile_invalid",
            )
            self.assertEqual(published.read_bytes(), authoritative_bytes)
            self.assertEqual(validate_release_package.call_count, 1)

            runtime_authority = root / "runtime-authority.json"
            runtime_authority.write_text('{"unchanged":true}\n', encoding="utf-8")
            runtime_bytes = runtime_authority.read_bytes()
            stdout = StringIO()
            with redirect_stdout(stdout):
                audit_exit = kernel_cli.main(
                    [
                        "release-audit",
                        "--profile", str(published),
                        *evidence_arguments,
                    ]
                )
            audit_result = json.loads(stdout.getvalue())
            self.assertEqual(audit_exit, 0)
            self.assertEqual(
                audit_result["classification"],
                "workflow_release_audit_passed",
            )
            self.assertFalse(audit_result["data"]["profile_published"])
            self.assertFalse(audit_result["data"]["runtime_authority_changed"])
            self.assertEqual(published.read_bytes(), authoritative_bytes)
            self.assertEqual(runtime_authority.read_bytes(), runtime_bytes)
            self.assertEqual(validate_release_package.call_count, 1)
            self.assertEqual(validate_historical_release_package.call_count, 1)

        historical_validator = SimpleNamespace(
            EvidenceError=ValueError,
            validate_manifest=lambda *args, **kwargs: None,
        )
        with (
            patch(
                "video2pdf_workflow_kernel.global_gate_exit_evidence._validate_mirrors"
            ),
            patch(
                "video2pdf_workflow_kernel.global_gate_exit_evidence._validate_implementation"
            ),
            patch(
                "video2pdf_workflow_kernel.global_gate_exit_evidence._validate_bindings"
            ),
            patch.object(
                release_maintenance.ReleaseMaintenance,
                "_load_slice_validator",
                return_value=historical_validator,
            ),
        ):
            stdout = StringIO()
            with redirect_stdout(stdout):
                historical_audit_exit = kernel_cli.main(
                    [
                        "release-audit",
                        "--profile",
                        str(PROJECT_ROOT / "config/workflow-release-profile.v1.json"),
                        "--global-gate-exit-evidence",
                        str(PROJECT_ROOT / "evidence/global-gate/exit-evidence-manifest.json"),
                        "--bilibili-exit-evidence",
                        str(PROJECT_ROOT / "evidence/slice-12/exit-evidence-manifest.json"),
                        "--youtube-exit-evidence",
                        str(PROJECT_ROOT / "evidence/slice-13/exit-evidence-manifest.json"),
                        "--batch-exit-evidence",
                        str(PROJECT_ROOT / "evidence/slice-14/exit-evidence-manifest.json"),
                    ]
                )
        historical_audit_result = json.loads(stdout.getvalue())
        self.assertEqual(historical_audit_exit, 0)
        self.assertEqual(
            historical_audit_result["classification"],
            "workflow_release_audit_passed",
        )

    def test_activate_publishes_current_batch_authority(self) -> None:
        root, evidence = self._case("activate")
        with self._publisher_boundary(root):
            activated = BatchCutoverPublisher(project_root=PROJECT_ROOT).activate(
                control_store_root=root,
                exit_evidence=evidence,
                activated_at="2026-08-19T00:00:00Z",
            )
            current = BatchCutoverPublisher(project_root=PROJECT_ROOT).require_current(
                control_store_root=root
            )

        self.assertEqual(activated["authority_path"], str(root / "active_batch.json"))
        self.assertFalse(activated["idempotent"])
        self.assertEqual(current["authority_path"], activated["authority_path"])
        self.assertEqual(current["authority_sha256"], activated["authority_sha256"])
        self.assertTrue(current["current"])

    def test_reconcile_completes_interrupted_authority_publication(self) -> None:
        root, evidence = self._case("reconcile-authority-write")
        publisher = BatchCutoverPublisher(project_root=PROJECT_ROOT)

        with self._publisher_boundary(root):
            with self.assertRaises(BatchCutoverFault):
                publisher.activate(
                    control_store_root=root,
                    exit_evidence=evidence,
                    activated_at="2026-08-19T00:00:00Z",
                    fault_point="after_authority_write",
                )
            reconciled = publisher.reconcile(control_store_root=root)

        self.assertTrue(reconciled["reconciled"])
        self.assertTrue(reconciled["current"])
        self.assertEqual(reconciled["authority_path"], str(root / "active_batch.json"))

    def test_require_current_rejects_changed_platform_prerequisite(self) -> None:
        root, evidence = self._case("prerequisite")
        publisher = BatchCutoverPublisher(project_root=PROJECT_ROOT)

        with self._publisher_boundary(root) as platform_bindings:
            publisher.activate(
                control_store_root=root,
                exit_evidence=evidence,
                activated_at="2026-08-19T00:00:00Z",
            )
            platform_bindings["youtube"] = {
                **platform_bindings["youtube"],
                "authority_sha256": "e" * 64,
            }
            with self.assertRaisesRegex(
                KernelConflict, "prerequisite authority binding changed"
            ):
                publisher.require_current(control_store_root=root)

    def test_same_evidence_replay_is_idempotent(self) -> None:
        root, evidence = self._case("same-evidence-replay")
        publisher = BatchCutoverPublisher(project_root=PROJECT_ROOT)
        with self._publisher_boundary(root):
            first = publisher.activate(
                control_store_root=root,
                exit_evidence=evidence,
                activated_at="2026-08-19T00:00:00Z",
            )
            replay = publisher.activate(
                control_store_root=root,
                exit_evidence=evidence,
                activated_at="2026-08-19T00:01:00Z",
            )

        self.assertFalse(first["idempotent"])
        self.assertTrue(replay["idempotent"])
        self.assertEqual(replay["authority_sha256"], first["authority_sha256"])

    def test_refresh_advances_generation_and_rebinds_current_prerequisites(self) -> None:
        root, evidence = self._case("refresh")
        refreshed_evidence = root / "refreshed-exit-evidence-manifest.json"
        refreshed_evidence.write_text(
            '{"slice":{"number":14,"name":"batch-projection-cutover"},'
            '"refresh":true}',
            encoding="utf-8",
        )
        publisher = BatchCutoverPublisher(project_root=PROJECT_ROOT)
        with self._publisher_boundary(root) as platform_bindings:
            publisher.activate(
                control_store_root=root,
                exit_evidence=evidence,
                activated_at="2026-08-19T00:00:00Z",
            )
            platform_bindings["youtube"] = {
                **platform_bindings["youtube"],
                "authority_sha256": "e" * 64,
                "generation": 2,
            }
            refreshed = publisher.refresh_authority(
                control_store_root=root,
                exit_evidence=refreshed_evidence,
                expected_generation=1,
                refreshed_at="2026-08-20T00:00:00Z",
            )
            current = publisher.require_current(control_store_root=root)

        authority = read_json(root / BATCH_AUTHORITY_FILE)
        self.assertEqual(refreshed["generation"], 2)
        self.assertFalse(refreshed["idempotent"])
        self.assertEqual(current["generation"], 2)
        self.assertEqual(authority["refreshed_at"], "2026-08-20T00:00:00Z")
        self.assertEqual(
            authority["platform_authority_bindings"]["youtube"]["generation"], 2
        )

    def test_reconcile_completes_interrupted_authority_refresh(self) -> None:
        for fault_point in (
            "after_intent",
            "after_authority_write",
            "after_control_commit",
        ):
            with self.subTest(fault_point=fault_point):
                root, evidence = self._case(f"refresh-{fault_point}")
                refreshed_evidence = root / "refreshed-exit-evidence-manifest.json"
                refreshed_evidence.write_text(
                    '{"slice":{"number":14,"name":"batch-projection-cutover"},'
                    '"refresh":true}',
                    encoding="utf-8",
                )
                publisher = BatchCutoverPublisher(project_root=PROJECT_ROOT)
                with self._publisher_boundary(root):
                    publisher.activate(
                        control_store_root=root,
                        exit_evidence=evidence,
                        activated_at="2026-08-19T00:00:00Z",
                    )
                    with self.assertRaises(BatchCutoverFault):
                        publisher.refresh_authority(
                            control_store_root=root,
                            exit_evidence=refreshed_evidence,
                            expected_generation=1,
                            refreshed_at="2026-08-20T00:00:00Z",
                            fault_point=fault_point,
                        )
                    reconciled = publisher.reconcile(control_store_root=root)

                self.assertTrue(reconciled["reconciled"])
                self.assertTrue(reconciled["current"])
                self.assertEqual(reconciled["generation"], 2)

    def test_prepared_refresh_blocks_current_and_legacy_activate_until_reconcile(self) -> None:
        root, evidence = self._case("prepared-refresh-blocks-current")
        refreshed_evidence = root / "refreshed-exit-evidence-manifest.json"
        refreshed_evidence.write_text(
            '{"slice":{"number":14,"name":"batch-projection-cutover"},'
            '"refresh":true}',
            encoding="utf-8",
        )
        publisher = BatchCutoverPublisher(project_root=PROJECT_ROOT)
        with self._publisher_boundary(root):
            publisher.activate(
                control_store_root=root,
                exit_evidence=evidence,
                activated_at="2026-08-19T00:00:00Z",
            )
            with self.assertRaises(BatchCutoverFault):
                publisher.refresh_authority(
                    control_store_root=root,
                    exit_evidence=refreshed_evidence,
                    expected_generation=1,
                    refreshed_at="2026-08-20T00:00:00Z",
                    fault_point="after_intent",
                )
            with self.assertRaisesRegex(KernelConflict, "stale|incomplete"):
                publisher.require_current(control_store_root=root)
            with self.assertRaisesRegex(KernelConflict, "reconciliation"):
                publisher.activate(
                    control_store_root=root,
                    exit_evidence=evidence,
                    activated_at="2026-08-20T00:01:00Z",
                )

    def test_refresh_rejects_a_stale_expected_generation_before_publication(self) -> None:
        root, evidence = self._case("refresh-generation-fence")
        refreshed_evidence = root / "refreshed-exit-evidence-manifest.json"
        refreshed_evidence.write_text(
            '{"slice":{"number":14,"name":"batch-projection-cutover"},'
            '"refresh":true}',
            encoding="utf-8",
        )
        publisher = BatchCutoverPublisher(project_root=PROJECT_ROOT)
        with self._publisher_boundary(root):
            publisher.activate(
                control_store_root=root,
                exit_evidence=evidence,
                activated_at="2026-08-19T00:00:00Z",
            )
            original = (root / BATCH_AUTHORITY_FILE).read_bytes()
            with self.assertRaises(KernelConflict) as raised:
                publisher.refresh_authority(
                    control_store_root=root,
                    exit_evidence=refreshed_evidence,
                    expected_generation=2,
                    refreshed_at="2026-08-20T00:00:00Z",
                )

        self.assertEqual(
            raised.exception.data.get("error_code"),
            "batch_authority_refresh_fenced",
        )
        self.assertEqual((root / BATCH_AUTHORITY_FILE).read_bytes(), original)

    def test_exact_refresh_replay_is_idempotent(self) -> None:
        root, evidence = self._case("refresh-replay")
        refreshed_evidence = root / "refreshed-exit-evidence-manifest.json"
        refreshed_evidence.write_text(
            '{"slice":{"number":14,"name":"batch-projection-cutover"},'
            '"refresh":true}',
            encoding="utf-8",
        )
        publisher = BatchCutoverPublisher(project_root=PROJECT_ROOT)
        with self._publisher_boundary(root):
            publisher.activate(
                control_store_root=root,
                exit_evidence=evidence,
                activated_at="2026-08-19T00:00:00Z",
            )
            first = publisher.refresh_authority(
                control_store_root=root,
                exit_evidence=refreshed_evidence,
                expected_generation=1,
                refreshed_at="2026-08-20T00:00:00Z",
            )
            replay = publisher.refresh_authority(
                control_store_root=root,
                exit_evidence=refreshed_evidence,
                expected_generation=1,
                refreshed_at="2026-08-20T00:01:00Z",
            )

        self.assertFalse(first["idempotent"])
        self.assertTrue(replay["idempotent"])
        self.assertEqual(replay["generation"], 2)
        self.assertEqual(replay["authority_sha256"], first["authority_sha256"])

    def test_refresh_does_not_require_old_evidence_to_match_current_head(self) -> None:
        root, evidence = self._case("refresh-old-publication")
        refreshed_evidence = root / "refreshed-exit-evidence-manifest.json"
        refreshed_evidence.write_text(
            '{"slice":{"number":14,"name":"batch-projection-cutover"},'
            '"refresh":true}',
            encoding="utf-8",
        )
        global_binding, platform_bindings = self._bindings(root)
        refresh_started = False

        def validate_publication(*, evidence_path: Path, project_root: Path) -> str:
            self.assertEqual(project_root, PROJECT_ROOT)
            if evidence_path == evidence.resolve():
                if refresh_started:
                    self.fail(
                        "refresh revalidated historical evidence against current HEAD"
                    )
                return "d" * 40
            self.assertEqual(evidence_path, refreshed_evidence.resolve())
            return "e" * 40

        publisher = BatchCutoverPublisher(project_root=PROJECT_ROOT)
        with (
            patch(
                "video2pdf_workflow_kernel.batch_authority._validate_post_publication",
                side_effect=validate_publication,
            ),
            patch(
                "video2pdf_workflow_kernel.batch_authority.GlobalGatePublisher.require_current",
                return_value=global_binding,
            ),
            patch(
                "video2pdf_workflow_kernel.batch_authority.BilibiliPlatformCutoverPublisher.require_current",
                side_effect=lambda *, platform, control_store_root: platform_bindings[platform],
            ),
        ):
            publisher.activate(
                control_store_root=root,
                exit_evidence=evidence,
                activated_at="2026-08-19T00:00:00Z",
            )
            refresh_started = True
            refreshed = publisher.refresh_authority(
                control_store_root=root,
                exit_evidence=refreshed_evidence,
                expected_generation=1,
                refreshed_at="2026-08-20T00:00:00Z",
            )

        self.assertEqual(refreshed["generation"], 2)
        self.assertEqual(
            read_json(root / BATCH_AUTHORITY_FILE)["publication_commit"],
            "e" * 40,
        )

    def test_refresh_replay_rejects_prerequisite_drift(self) -> None:
        root, evidence = self._case("refresh-replay-prerequisite-drift")
        refreshed_evidence = root / "refreshed-exit-evidence-manifest.json"
        refreshed_evidence.write_text(
            '{"slice":{"number":14,"name":"batch-projection-cutover"},'
            '"refresh":true}',
            encoding="utf-8",
        )
        publisher = BatchCutoverPublisher(project_root=PROJECT_ROOT)
        with self._publisher_boundary(root) as platform_bindings:
            publisher.activate(
                control_store_root=root,
                exit_evidence=evidence,
                activated_at="2026-08-19T00:00:00Z",
            )
            publisher.refresh_authority(
                control_store_root=root,
                exit_evidence=refreshed_evidence,
                expected_generation=1,
                refreshed_at="2026-08-20T00:00:00Z",
            )
            platform_bindings["youtube"] = {
                **platform_bindings["youtube"],
                "authority_sha256": "f" * 64,
                "generation": 2,
            }
            with self.assertRaisesRegex(KernelConflict, "prerequisite"):
                publisher.refresh_authority(
                    control_store_root=root,
                    exit_evidence=refreshed_evidence,
                    expected_generation=1,
                    refreshed_at="2026-08-20T00:01:00Z",
                )

    def test_refresh_reconcile_normalizes_conflicting_authority_bytes(self) -> None:
        root, evidence = self._case("refresh-reconcile-conflicting-bytes")
        refreshed_evidence = root / "refreshed-exit-evidence-manifest.json"
        refreshed_evidence.write_text(
            '{"slice":{"number":14,"name":"batch-projection-cutover"},'
            '"refresh":true}',
            encoding="utf-8",
        )
        publisher = BatchCutoverPublisher(project_root=PROJECT_ROOT)
        with self._publisher_boundary(root):
            publisher.activate(
                control_store_root=root,
                exit_evidence=evidence,
                activated_at="2026-08-19T00:00:00Z",
            )
            with self.assertRaises(BatchCutoverFault):
                publisher.refresh_authority(
                    control_store_root=root,
                    exit_evidence=refreshed_evidence,
                    expected_generation=1,
                    refreshed_at="2026-08-20T00:00:00Z",
                    fault_point="after_intent",
                )
            (root / BATCH_AUTHORITY_FILE).write_text("{", encoding="utf-8")
            with self.assertRaisesRegex(KernelConflict, "bytes conflict"):
                publisher.reconcile(control_store_root=root)

    def test_refresh_reconcile_reproves_generation_and_prior_authority_sha(self) -> None:
        root, evidence = self._case("refresh-reconcile-cas")
        refreshed_evidence = root / "refreshed-exit-evidence-manifest.json"
        refreshed_evidence.write_text(
            '{"slice":{"number":14,"name":"batch-projection-cutover"},'
            '"refresh":true}',
            encoding="utf-8",
        )
        publisher = BatchCutoverPublisher(project_root=PROJECT_ROOT)
        with self._publisher_boundary(root):
            publisher.activate(
                control_store_root=root,
                exit_evidence=evidence,
                activated_at="2026-08-19T00:00:00Z",
            )
            prior_sha256 = hashlib.sha256(
                (root / BATCH_AUTHORITY_FILE).read_bytes()
            ).hexdigest()
            with self.assertRaises(BatchCutoverFault):
                publisher.refresh_authority(
                    control_store_root=root,
                    exit_evidence=refreshed_evidence,
                    expected_generation=1,
                    refreshed_at="2026-08-20T00:00:00Z",
                    fault_point="after_authority_write",
                )
            with sqlite3.connect(root / BATCH_CUTOVER_DB) as connection:
                intent = connection.execute(
                    "SELECT expected_generation,expected_authority_sha256 "
                    "FROM batch_authority_refresh_intents WHERE state='PREPARED'"
                ).fetchone()
                self.assertEqual(intent, (1, prior_sha256))
                connection.execute(
                    "UPDATE batch_cutover_authority SET authority_sha256=? "
                    "WHERE singleton=1",
                    ("f" * 64,),
                )
            with self.assertRaisesRegex(KernelConflict, "reconciliation fence"):
                publisher.reconcile(control_store_root=root)

    def test_current_refreshed_authority_rejects_resealed_invalid_refreshed_at(self) -> None:
        root, evidence = self._case("refresh-invalid-refreshed-at")
        refreshed_evidence = root / "refreshed-exit-evidence-manifest.json"
        refreshed_evidence.write_text(
            '{"slice":{"number":14,"name":"batch-projection-cutover"},'
            '"refresh":true}',
            encoding="utf-8",
        )
        publisher = BatchCutoverPublisher(project_root=PROJECT_ROOT)
        with self._publisher_boundary(root):
            publisher.activate(
                control_store_root=root,
                exit_evidence=evidence,
                activated_at="2026-08-19T00:00:00Z",
            )
            publisher.refresh_authority(
                control_store_root=root,
                exit_evidence=refreshed_evidence,
                expected_generation=1,
                refreshed_at="2026-08-20T00:00:00Z",
            )
            authority_path = root / BATCH_AUTHORITY_FILE
            authority = read_json(authority_path)
            authority["refreshed_at"] = "20 August 2026"
            unsigned = dict(authority)
            unsigned.pop("authority_sha256")
            authority["authority_sha256"] = hashlib.sha256(
                canonical_json_bytes(unsigned)
            ).hexdigest()
            authority_path.write_bytes(canonical_json_bytes(authority))
            outer_sha256 = hashlib.sha256(authority_path.read_bytes()).hexdigest()
            with sqlite3.connect(root / BATCH_CUTOVER_DB) as connection:
                connection.execute(
                    "UPDATE batch_cutover_authority SET authority_sha256=? "
                    "WHERE singleton=1",
                    (outer_sha256,),
                )
            with self.assertRaises(KernelConflict):
                publisher.require_current(control_store_root=root)

    def test_refresh_additively_upgrades_an_existing_activation_database(self) -> None:
        root, evidence = self._case("refresh-existing-activation-database")
        refreshed_evidence = root / "refreshed-exit-evidence-manifest.json"
        refreshed_evidence.write_text(
            '{"slice":{"number":14,"name":"batch-projection-cutover"},'
            '"refresh":true}',
            encoding="utf-8",
        )
        publisher = BatchCutoverPublisher(project_root=PROJECT_ROOT)
        with self._publisher_boundary(root):
            publisher.activate(
                control_store_root=root,
                exit_evidence=evidence,
                activated_at="2026-08-19T00:00:00Z",
            )
            with sqlite3.connect(root / BATCH_CUTOVER_DB) as connection:
                connection.execute("DROP TABLE batch_authority_refresh_intents")
            current = publisher.require_current(control_store_root=root)
            refreshed = publisher.refresh_authority(
                control_store_root=root,
                exit_evidence=refreshed_evidence,
                expected_generation=1,
                refreshed_at="2026-08-20T00:00:00Z",
            )

        self.assertEqual(current["generation"], 1)
        self.assertEqual(refreshed["generation"], 2)

    def test_refresh_accepts_new_canonical_evidence_at_the_same_path(self) -> None:
        root, evidence = self._case("refresh-same-evidence-path")
        publisher = BatchCutoverPublisher(project_root=PROJECT_ROOT)
        with self._publisher_boundary(root):
            publisher.activate(
                control_store_root=root,
                exit_evidence=evidence,
                activated_at="2026-08-19T00:00:00Z",
            )
            evidence.write_text(
                '{"slice":{"number":14,"name":"batch-projection-cutover"},'
                '"refresh":true}',
                encoding="utf-8",
            )
            refreshed = publisher.refresh_authority(
                control_store_root=root,
                exit_evidence=evidence,
                expected_generation=1,
                refreshed_at="2026-08-20T00:00:00Z",
            )

        self.assertEqual(refreshed["generation"], 2)
        self.assertEqual(
            read_json(root / BATCH_AUTHORITY_FILE)["exit_evidence_path"],
            str(evidence.resolve()),
        )

    def test_after_intent_prerequisite_drift_is_cancelled_then_retry_converges(self) -> None:
        root, evidence = self._case("refresh-prerequisite-drift-retry")
        refreshed_evidence = root / "refreshed-exit-evidence-manifest.json"
        refreshed_evidence.write_text(
            '{"slice":{"number":14,"name":"batch-projection-cutover"},'
            '"refresh":true}',
            encoding="utf-8",
        )
        publisher = BatchCutoverPublisher(project_root=PROJECT_ROOT)
        with self._publisher_boundary(root) as platform_bindings:
            publisher.activate(
                control_store_root=root,
                exit_evidence=evidence,
                activated_at="2026-08-19T00:00:00Z",
            )
            prior_authority = (root / BATCH_AUTHORITY_FILE).read_bytes()
            with self.assertRaises(BatchCutoverFault):
                publisher.refresh_authority(
                    control_store_root=root,
                    exit_evidence=refreshed_evidence,
                    expected_generation=1,
                    refreshed_at="2026-08-20T00:00:00Z",
                    fault_point="after_intent",
                )
            platform_bindings["youtube"] = {
                **platform_bindings["youtube"],
                "authority_sha256": "f" * 64,
                "generation": 2,
            }
            cancelled = publisher.reconcile(control_store_root=root)
            self.assertEqual(
                (root / BATCH_AUTHORITY_FILE).read_bytes(), prior_authority
            )
            retried = publisher.refresh_authority(
                control_store_root=root,
                exit_evidence=refreshed_evidence,
                expected_generation=1,
                refreshed_at="2026-08-20T00:01:00Z",
            )

        self.assertTrue(cancelled["cancelled"])
        self.assertEqual(cancelled["cancellation_reason"], "prerequisite_drift")
        self.assertFalse(cancelled["current"])
        self.assertEqual(retried["generation"], 2)
        self.assertFalse(retried["idempotent"])

    def test_after_intent_evidence_drift_is_cancelled_then_retry_converges(self) -> None:
        root, evidence = self._case("refresh-evidence-drift-retry")
        refreshed_evidence = root / "refreshed-exit-evidence-manifest.json"
        refreshed_evidence.write_text(
            '{"slice":{"number":14,"name":"batch-projection-cutover"},'
            '"refresh":1}',
            encoding="utf-8",
        )
        publisher = BatchCutoverPublisher(project_root=PROJECT_ROOT)
        with self._publisher_boundary(root):
            publisher.activate(
                control_store_root=root,
                exit_evidence=evidence,
                activated_at="2026-08-19T00:00:00Z",
            )
            prior_authority = (root / BATCH_AUTHORITY_FILE).read_bytes()
            with self.assertRaises(BatchCutoverFault):
                publisher.refresh_authority(
                    control_store_root=root,
                    exit_evidence=refreshed_evidence,
                    expected_generation=1,
                    refreshed_at="2026-08-20T00:00:00Z",
                    fault_point="after_intent",
                )
            refreshed_evidence.write_text(
                '{"slice":{"number":14,"name":"batch-projection-cutover"},'
                '"refresh":2}',
                encoding="utf-8",
            )
            cancelled = publisher.reconcile(control_store_root=root)
            self.assertEqual(
                (root / BATCH_AUTHORITY_FILE).read_bytes(), prior_authority
            )
            retried = publisher.refresh_authority(
                control_store_root=root,
                exit_evidence=refreshed_evidence,
                expected_generation=1,
                refreshed_at="2026-08-20T00:01:00Z",
            )

        self.assertTrue(cancelled["cancelled"])
        self.assertTrue(cancelled["reconciled"])
        self.assertEqual(cancelled["cancellation_reason"], "evidence_drift")
        self.assertEqual(retried["generation"], 2)

    def test_cancelled_refresh_and_reconcile_have_distinct_cli_classifications(self) -> None:
        choices = kernel_cli._parser()._subparsers._group_actions[0].choices
        self.assertNotIn("batch-authority-refresh", choices)
        self.assertNotIn("batch-reconcile", choices)

    def test_after_intent_publication_commit_drift_is_cancelled(self) -> None:
        root, evidence = self._case("refresh-publication-drift")
        refreshed_evidence = root / "refreshed-exit-evidence-manifest.json"
        refreshed_evidence.write_text(
            '{"slice":{"number":14,"name":"batch-projection-cutover"},'
            '"refresh":true}',
            encoding="utf-8",
        )
        publication_commit = "d" * 40
        publisher = BatchCutoverPublisher(project_root=PROJECT_ROOT)
        with self._publisher_boundary(root):
            publisher.activate(
                control_store_root=root,
                exit_evidence=evidence,
                activated_at="2026-08-19T00:00:00Z",
            )
            with self.assertRaises(BatchCutoverFault):
                publisher.refresh_authority(
                    control_store_root=root,
                    exit_evidence=refreshed_evidence,
                    expected_generation=1,
                    refreshed_at="2026-08-20T00:00:00Z",
                    fault_point="after_intent",
                )
            with patch(
                "video2pdf_workflow_kernel.batch_authority._validate_post_publication",
                return_value="e" * 40,
            ):
                cancelled = publisher.reconcile(control_store_root=root)

        self.assertEqual(publication_commit, "d" * 40)
        self.assertTrue(cancelled["cancelled"])
        self.assertEqual(
            cancelled["cancellation_reason"], "publication_commit_drift"
        )

    def test_normal_refresh_cancels_evidence_drift_and_allows_retry(self) -> None:
        root, evidence = self._case("refresh-normal-evidence-drift")
        refreshed_evidence = root / "refreshed-exit-evidence-manifest.json"
        refreshed_evidence.write_text(
            '{"slice":{"number":14,"name":"batch-projection-cutover"},'
            '"refresh":1}',
            encoding="utf-8",
        )
        publisher = BatchCutoverPublisher(project_root=PROJECT_ROOT)
        with self._publisher_boundary(root):
            publisher.activate(
                control_store_root=root,
                exit_evidence=evidence,
                activated_at="2026-08-19T00:00:00Z",
            )
            validation_calls = 0

            def drift_during_validation(
                *, evidence_path: Path, project_root: Path
            ) -> str:
                nonlocal validation_calls
                self.assertEqual(evidence_path, refreshed_evidence.resolve())
                self.assertEqual(project_root, PROJECT_ROOT)
                validation_calls += 1
                if validation_calls == 2:
                    refreshed_evidence.write_text(
                        '{"slice":{"number":14,'
                        '"name":"batch-projection-cutover"},"refresh":2}',
                        encoding="utf-8",
                    )
                return "d" * 40

            with patch(
                "video2pdf_workflow_kernel.batch_authority._validate_post_publication",
                side_effect=drift_during_validation,
            ):
                cancelled = publisher.refresh_authority(
                    control_store_root=root,
                    exit_evidence=refreshed_evidence,
                    expected_generation=1,
                    refreshed_at="2026-08-20T00:00:00Z",
                )
            retried = publisher.refresh_authority(
                control_store_root=root,
                exit_evidence=refreshed_evidence,
                expected_generation=1,
                refreshed_at="2026-08-20T00:01:00Z",
            )

        self.assertTrue(cancelled["cancelled"])
        self.assertFalse(cancelled["reconciled"])
        self.assertEqual(cancelled["cancellation_reason"], "evidence_drift")
        self.assertEqual(retried["generation"], 2)

    def test_reconcile_rejects_mixed_activation_and_refresh_intents(self) -> None:
        root, evidence = self._case("mixed-prepared-intents")
        refreshed_evidence = root / "refreshed-exit-evidence-manifest.json"
        refreshed_evidence.write_text(
            '{"slice":{"number":14,"name":"batch-projection-cutover"},'
            '"refresh":true}',
            encoding="utf-8",
        )
        publisher = BatchCutoverPublisher(project_root=PROJECT_ROOT)
        with self._publisher_boundary(root):
            publisher.activate(
                control_store_root=root,
                exit_evidence=evidence,
                activated_at="2026-08-19T00:00:00Z",
            )
            with self.assertRaises(BatchCutoverFault):
                publisher.refresh_authority(
                    control_store_root=root,
                    exit_evidence=refreshed_evidence,
                    expected_generation=1,
                    refreshed_at="2026-08-20T00:00:00Z",
                    fault_point="after_intent",
                )
            with sqlite3.connect(root / BATCH_CUTOVER_DB) as connection:
                connection.execute(
                    "INSERT INTO batch_cutover_intents("
                    "intent_id,expected_generation,evidence_sha256,state,"
                    "authority_sha256,authority_json,evidence_path,project_root,"
                    "publication_commit) VALUES(?,?,?,'PREPARED',?,?,?,?,?)",
                    (
                        "f" * 64,
                        1,
                        "a" * 64,
                        "b" * 64,
                        "{}",
                        str(evidence),
                        str(PROJECT_ROOT),
                        "d" * 40,
                    ),
                )
            with self.assertRaisesRegex(KernelConflict, "ambiguous"):
                publisher.reconcile(control_store_root=root)

    def test_refresh_rejects_non_positive_generation_through_the_cli_seam(self) -> None:
        choices = kernel_cli._parser()._subparsers._group_actions[0].choices
        self.assertNotIn("batch-authority-refresh", choices)

    def test_different_evidence_loses_singleton_activation_fence(self) -> None:
        root, evidence = self._case("different-evidence-fence")
        second_evidence = root / "different-exit-evidence-manifest.json"
        second_evidence.write_text(
            '{"slice":{"number":14,"name":"batch-projection-cutover"},"different":true}',
            encoding="utf-8",
        )
        publisher = BatchCutoverPublisher(project_root=PROJECT_ROOT)
        with self._publisher_boundary(root):
            publisher.activate(
                control_store_root=root,
                exit_evidence=evidence,
                activated_at="2026-08-19T00:00:00Z",
            )
            with self.assertRaisesRegex(KernelConflict, "activation fence"):
                publisher.activate(
                    control_store_root=root,
                    exit_evidence=second_evidence,
                    activated_at="2026-08-19T00:01:00Z",
                )

    def test_current_authority_rejects_authority_evidence_and_database_tamper(self) -> None:
        for tamper in ("authority", "evidence", "database"):
            with self.subTest(tamper=tamper):
                root, evidence, publisher = self._activate_case(f"tamper-{tamper}")
                authority_path = root / BATCH_AUTHORITY_FILE
                if tamper == "authority":
                    authority_path.write_text("{}", encoding="utf-8")
                elif tamper == "evidence":
                    evidence.write_text("{}", encoding="utf-8")
                else:
                    with sqlite3.connect(root / BATCH_CUTOVER_DB) as connection:
                        connection.execute(
                            "UPDATE batch_cutover_authority SET evidence_sha256=? "
                            "WHERE singleton=1",
                            ("f" * 64,),
                        )
                with self._publisher_boundary(root), self.assertRaises(KernelConflict):
                    publisher.require_current(control_store_root=root)

    def test_current_authority_normalizes_malformed_json_and_type_failures(self) -> None:
        for malformed in (b"{", b"[]"):
            with self.subTest(malformed=malformed):
                root, _evidence, publisher = self._activate_case(
                    f"malformed-authority-{len(malformed)}-{malformed[:1].hex()}"
                )
                authority_path = root / BATCH_AUTHORITY_FILE
                authority_path.write_bytes(malformed)
                outer_sha256 = hashlib.sha256(malformed).hexdigest()
                with sqlite3.connect(root / BATCH_CUTOVER_DB) as connection:
                    connection.execute(
                        "UPDATE batch_cutover_authority SET authority_sha256=? "
                        "WHERE singleton=1",
                        (outer_sha256,),
                    )

                with self._publisher_boundary(root), self.assertRaises(
                    KernelConflict
                ) as raised:
                    publisher.require_current(control_store_root=root)

                self.assertEqual(
                    raised.exception.data.get("first_failing_gate"),
                    "batch_cutover_authority",
                )
                self.assertEqual(
                    raised.exception.data.get("error_code"),
                    "batch_cutover_authority_conflict",
                )

    def test_current_authority_verifies_its_self_fingerprint(self) -> None:
        root, _evidence, publisher = self._activate_case("self-fingerprint")
        authority_path = root / BATCH_AUTHORITY_FILE
        authority = read_json(authority_path)
        authority["activated_at"] = "2026-08-19T00:02:00Z"
        authority_path.write_bytes(canonical_json_bytes(authority))
        outer_sha256 = hashlib.sha256(authority_path.read_bytes()).hexdigest()
        with sqlite3.connect(root / BATCH_CUTOVER_DB) as connection:
            connection.execute(
                "UPDATE batch_cutover_authority SET authority_sha256=? WHERE singleton=1",
                (outer_sha256,),
            )

        with self._publisher_boundary(root), self.assertRaisesRegex(
            KernelConflict, "content conflicts"
        ):
            publisher.require_current(control_store_root=root)

    def test_current_authority_rejects_resealed_invalid_activated_at(self) -> None:
        root, _evidence, publisher = self._activate_case("resealed-activated-at")
        authority_path = root / BATCH_AUTHORITY_FILE
        authority = read_json(authority_path)
        authority["activated_at"] = "19 August 2026"
        unsigned = dict(authority)
        unsigned.pop("authority_sha256")
        authority["authority_sha256"] = hashlib.sha256(
            canonical_json_bytes(unsigned)
        ).hexdigest()
        authority_path.write_bytes(canonical_json_bytes(authority))
        outer_sha256 = hashlib.sha256(authority_path.read_bytes()).hexdigest()
        with sqlite3.connect(root / BATCH_CUTOVER_DB) as connection:
            connection.execute(
                "UPDATE batch_cutover_authority SET authority_sha256=? WHERE singleton=1",
                (outer_sha256,),
            )

        with self._publisher_boundary(root), self.assertRaises(
            KernelConflict
        ) as raised:
            publisher.require_current(control_store_root=root)
        self.assertEqual(
            raised.exception.data.get("first_failing_gate"),
            "batch_cutover_authority",
        )
        self.assertEqual(
            raised.exception.data.get("error_code"),
            "batch_cutover_authority_conflict",
        )

    def test_reconcile_recovers_after_intent_and_after_control_commit(self) -> None:
        for fault_point in ("after_intent", "after_control_commit"):
            with self.subTest(fault_point=fault_point):
                root, evidence = self._case(f"reconcile-{fault_point}")
                publisher = BatchCutoverPublisher(project_root=PROJECT_ROOT)
                with self._publisher_boundary(root):
                    with self.assertRaises(BatchCutoverFault):
                        publisher.activate(
                            control_store_root=root,
                            exit_evidence=evidence,
                            activated_at="2026-08-19T00:00:00Z",
                            fault_point=fault_point,
                        )
                    reconciled = publisher.reconcile(control_store_root=root)

                self.assertTrue(reconciled["reconciled"])
                self.assertTrue(reconciled["current"])

    def test_reconcile_fails_closed_with_multiple_prepared_intents(self) -> None:
        root, evidence = self._case("multiple-prepared")
        publisher = BatchCutoverPublisher(project_root=PROJECT_ROOT)
        with self._publisher_boundary(root):
            with self.assertRaises(BatchCutoverFault):
                publisher.activate(
                    control_store_root=root,
                    exit_evidence=evidence,
                    activated_at="2026-08-19T00:00:00Z",
                    fault_point="after_intent",
                )
            with sqlite3.connect(root / BATCH_CUTOVER_DB) as connection:
                row = connection.execute(
                    "SELECT * FROM batch_cutover_intents WHERE state='PREPARED'"
                ).fetchone()
                self.assertIsNotNone(row)
                connection.execute(
                    "INSERT INTO batch_cutover_intents SELECT ?, expected_generation, "
                    "evidence_sha256, state, authority_sha256, authority_json, "
                    "evidence_path, project_root, publication_commit "
                    "FROM batch_cutover_intents WHERE intent_id=?",
                    ("f" * 64, row[0]),
                )
            with self.assertRaisesRegex(KernelConflict, "Multiple Batch cutover"):
                publisher.reconcile(control_store_root=root)

    def test_publisher_releases_sqlite_transaction_after_fault(self) -> None:
        root, evidence = self._case("transaction-close")
        publisher = BatchCutoverPublisher(project_root=PROJECT_ROOT)
        with self._publisher_boundary(root):
            with self.assertRaises(BatchCutoverFault):
                publisher.activate(
                    control_store_root=root,
                    exit_evidence=evidence,
                    activated_at="2026-08-19T00:00:00Z",
                    fault_point="after_intent",
                )
            with sqlite3.connect(root / BATCH_CUTOVER_DB, timeout=0.05) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("ROLLBACK")

    def test_invalid_activated_at_is_rejected_before_publication(self) -> None:
        root, evidence = self._case("invalid-activated-at")
        with self._publisher_boundary(root), self.assertRaisesRegex(
            ContractError, "activated_at"
        ):
            BatchCutoverPublisher(project_root=PROJECT_ROOT).activate(
                control_store_root=root,
                exit_evidence=evidence,
                activated_at="19 August 2026",
            )
        self.assertFalse((root / BATCH_AUTHORITY_FILE).exists())

    def test_real_validator_loader_rejects_non_slice14_and_prepublication_inputs(self) -> None:
        root = new_case_dir(self.id(), label="real-validator-loader")
        non_slice14 = root / "slice13.json"
        non_slice14.write_text(
            '{"slice":{"number":13,"name":"youtube-platform-kernel-cutover"}}',
            encoding="utf-8",
        )
        with self.assertRaises(ContractError) as non_slice_error:
            _validate_post_publication(
                evidence_path=non_slice14,
                project_root=PROJECT_ROOT,
            )
        self.assertEqual(
            non_slice_error.exception.data.get("error_code"),
            "batch_exit_evidence_slice_invalid",
        )

        prepublication = root / "slice14-prepublication.json"
        prepublication.write_text(
            (
                PROJECT_ROOT
                / "tests/video_workflow/fixtures/exit_evidence/slice14.valid.json"
            ).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        with self.assertRaises(ContractError) as prepublication_error:
            _validate_post_publication(
                evidence_path=prepublication,
                project_root=PROJECT_ROOT,
            )
        self.assertEqual(
            prepublication_error.exception.data.get("error_code"),
            "batch_exit_evidence_lineage_invalid",
        )

    def test_incompatible_database_fails_closed_without_leaking_sqlite_error(self) -> None:
        root = new_case_dir(self.id(), label="incompatible-db")
        with sqlite3.connect(root / BATCH_CUTOVER_DB) as connection:
            connection.execute("PRAGMA user_version=99")

        with self.assertRaises(ControlStoreUnavailable):
            BatchCutoverPublisher(project_root=PROJECT_ROOT).require_current(
                control_store_root=root
            )


if __name__ == "__main__":
    unittest.main()
