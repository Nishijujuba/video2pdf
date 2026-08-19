from __future__ import annotations

import hashlib
import sqlite3
import sys
import unittest
from contextlib import contextmanager
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

    def test_batch_authority_commands_are_public(self) -> None:
        choices = kernel_cli._parser()._subparsers._group_actions[0].choices

        self.assertEqual(
            {
                command
                for command in choices
                if command
                in {
                    "batch-activate",
                    "batch-reconcile",
                    "batch-authority-check",
                }
            },
            {"batch-activate", "batch-reconcile", "batch-authority-check"},
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

    def test_batch_authority_commands_return_workflow_envelopes(self) -> None:
        authority = {
            "authority_path": "D:/workspace/active_batch.json",
            "authority_sha256": "a" * 64,
            "generation": 1,
            "current": True,
        }
        cases = (
            (
                [
                    "batch-activate",
                    "--control-store-root",
                    "D:/workspace",
                    "--exit-evidence",
                    "D:/repo/evidence/slice-14/exit-evidence-manifest.json",
                    "--activated-at",
                    "2026-08-19T00:00:00Z",
                ],
                "activate",
                "batch_authority_activated",
            ),
            (
                ["batch-reconcile", "--control-store-root", "D:/workspace"],
                "reconcile",
                "batch_authority_reconciled",
            ),
            (
                ["batch-authority-check", "--control-store-root", "D:/workspace"],
                "require_current",
                "batch_authority_current",
            ),
        )

        for argv, method_name, classification in cases:
            with self.subTest(command=argv[0]), patch.object(
                BatchCutoverPublisher, method_name, return_value=authority
            ):
                args = kernel_cli._parser().parse_args(argv)
                envelope = kernel_cli._execute(args, PROJECT_ROOT)

                self.assertEqual(envelope["classification"], classification)
                self.assertEqual(envelope["evidence_path"], authority["authority_path"])

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
