from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import unittest
import uuid


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from video2pdf_workflow_kernel.contracts import ContractRegistry  # noqa: E402
from video2pdf_workflow_kernel.control_store import (  # noqa: E402
    SCHEMA_VERSION,
    ControlStore,
)
from video2pdf_workflow_kernel.errors import (  # noqa: E402
    ArtifactDrift,
    ControlStoreUnavailable,
    KernelConflict,
)
from video2pdf_workflow_kernel.utils import canonical_json_bytes  # noqa: E402


RUN_FIXTURE = (
    PROJECT_ROOT / "tests/video_workflow/fixtures/contracts/run-record.v3.valid.json"
)
TEST_RUNS = PROJECT_ROOT / "待删除/source-publication-control-store-tests"


class SourcePublicationControlStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = TEST_RUNS / uuid.uuid4().hex[:12]
        self.workspace = self.root / "workspace"
        self.workspace.mkdir(parents=True)
        self.contracts = ContractRegistry(PROJECT_ROOT)
        self.store = ControlStore.initialize(self.workspace, self.contracts)
        self.run_id = uuid.uuid4().hex
        self.output_path = self.workspace / "production-source-run"
        self.intent_id = f"initialize-{self.run_id}"
        self.source_identity = (
            "0e0eb59773c2eddc5c2d40c4e43e6a35ca98372205c57545babd2a41ec50cc9d"
        )
        self.initial_run = self._pending_run()
        self.initial_sha = hashlib.sha256(
            canonical_json_bytes(self.initial_run)
        ).hexdigest()
        self.store.prepare_initialization(
            run_id=self.run_id,
            output_path=self.output_path,
            intent_id=self.intent_id,
            staging_path=self.root / "staging",
        )
        self.store.bind_publication_expectations(
            self.intent_id,
            expected_run_record_sha256=self.initial_sha,
            canonical_platform="bilibili",
            canonical_item_id="BV1Issue7001",
            source_identity=self.source_identity,
            source_manifest_sha256=None,
        )
        self.store.transition_intent(
            self.intent_id,
            expected_state="PREPARED",
            new_state="PUBLISHED",
            run_record_sha256=self.initial_sha,
        )
        self.store.transition_intent(
            self.intent_id,
            expected_state="PUBLISHED",
            new_state="RECORD_COMMITTED",
        )
        self.store.transition_intent(
            self.intent_id,
            expected_state="RECORD_COMMITTED",
            new_state="COMMITTED",
        )

    def _pending_run(self) -> dict:
        run = json.loads(RUN_FIXTURE.read_text(encoding="utf-8"))
        run.update(
            {
                "run_id": self.run_id,
                "request_id": f"source-publication-{self.run_id}",
                "output_path": str(self.output_path.resolve()),
                "initialization_intent_id": self.intent_id,
                "source_version": None,
                "source_state": "pending",
                "phase": "source_acquisition",
                "coordination_revision": 1,
                "last_mutation_intent_id": None,
                "artifact_generations": {
                    "bootstrap_record": run["artifact_generations"]["bootstrap_record"]
                },
                "checkpoints": {
                    "run_initialized": run["checkpoints"]["run_initialized"]
                },
            }
        )
        self.contracts.validate_run_record(run)
        return run

    def _replacement(self, *, source_epoch: int = 1) -> tuple[str, dict, str]:
        source_manifest_sha256 = "e" * 64
        source_version = "9" * 64
        intent_id = ControlStore.derive_source_publication_intent_id(
            run_id=self.run_id,
            source_epoch=source_epoch,
            expected_run_revision=1,
            old_run_record_sha256=self.initial_sha,
        )
        replacement = json.loads(RUN_FIXTURE.read_text(encoding="utf-8"))
        replacement.update(
            {
                "run_id": self.run_id,
                "request_id": f"source-publication-{self.run_id}",
                "output_path": str(self.output_path.resolve()),
                "initialization_intent_id": self.intent_id,
                "source_identity": self.source_identity,
                "source_version": source_version,
                "source_epoch": source_epoch,
                "coordination_revision": 2,
                "last_mutation_intent_id": intent_id,
            }
        )
        replacement["artifact_generations"]["source_manifest"][
            "sha256"
        ] = source_manifest_sha256
        for logical_id, generation in replacement["artifact_generations"].items():
            if logical_id != "bootstrap_record":
                generation["source_epoch"] = source_epoch
        replacement["checkpoints"]["source_ready"]["artifact_bindings"][-1][
            "sha256"
        ] = source_manifest_sha256
        replacement["checkpoints"]["source_ready"][
            "evidence_sha256"
        ] = source_manifest_sha256
        self.contracts.validate_run_record(replacement)
        return intent_id, replacement, source_manifest_sha256

    def _prepare(self):
        intent_id, replacement, manifest_sha = self._replacement()
        row = self.store.prepare_source_publication(
            run_id=self.run_id,
            source_epoch=1,
            expected_run_revision=1,
            old_run_record_sha256=self.initial_sha,
            replacement_run_record=replacement,
            source_manifest_sha256=manifest_sha,
            source_identity=self.source_identity,
            source_version="9" * 64,
        )
        self.assertEqual(row["intent_id"], intent_id)
        return intent_id, replacement, manifest_sha, row

    def test_new_database_is_v9_and_preparation_binds_every_authority(self) -> None:
        self.assertEqual(SCHEMA_VERSION, 9)
        self.assertEqual(self.store.check().schema_version, 9)
        intent_id, replacement, manifest_sha, row = self._prepare()
        replacement_sha = hashlib.sha256(
            canonical_json_bytes(replacement)
        ).hexdigest()
        self.assertEqual(row["state"], "PREPARED")
        self.assertEqual(row["source_epoch"], 1)
        self.assertEqual(row["expected_run_revision"], 1)
        self.assertEqual(row["predecessor_committed_sha256"], self.initial_sha)
        self.assertEqual(row["replacement_run_record_sha256"], replacement_sha)
        self.assertEqual(row["source_manifest_sha256"], manifest_sha)
        self.assertEqual(row["source_identity"], self.source_identity)
        self.assertEqual(row["source_version"], "9" * 64)
        self.assertEqual(
            row["intent_identity"],
            ControlStore.derive_source_publication_row_identity(
                intent_id=intent_id,
                replacement_run_record_sha256=replacement_sha,
                source_manifest_sha256=manifest_sha,
                source_identity=self.source_identity,
                source_version="9" * 64,
            ),
        )
        replay = self.store.prepare_source_publication(
            run_id=self.run_id,
            source_epoch=1,
            expected_run_revision=1,
            old_run_record_sha256=self.initial_sha,
            replacement_run_record=replacement,
            source_manifest_sha256=manifest_sha,
            source_identity=self.source_identity,
            source_version="9" * 64,
        )
        self.assertEqual(replay["intent_identity"], row["intent_identity"])

    def test_journal_binding_is_single_assignment_and_state_machine_commits_chain(
        self,
    ) -> None:
        intent_id, replacement, _, _ = self._prepare()
        journal_sha = "a" * 64
        self.store.bind_source_publication_journal(intent_id, journal_sha)
        self.store.bind_source_publication_journal(intent_id, journal_sha)
        with self.assertRaises(ArtifactDrift):
            self.store.bind_source_publication_journal(intent_id, "b" * 64)
        self.store.transition_source_publication(
            intent_id,
            expected_state="PREPARED",
            new_state="FILES_PUBLISHED",
        )
        self.store.transition_source_publication(
            intent_id,
            expected_state="FILES_PUBLISHED",
            new_state="RECORD_COMMITTED",
        )
        self.store.commit_source_publication(intent_id)
        self.store.commit_source_publication(intent_id)
        replacement_sha = hashlib.sha256(
            canonical_json_bytes(replacement)
        ).hexdigest()
        self.assertEqual(self.store.current_run_record_sha(self.run_id), replacement_sha)
        self.assertEqual(
            self.store.source_publication_by_id(intent_id)["state"], "COMMITTED"
        )

    def test_nonterminal_publication_owns_the_single_run_promotion_slot(self) -> None:
        intent_id, _, _, _ = self._prepare()
        with self.assertRaisesRegex(KernelConflict, "Promotion Slot"):
            self.store.prepare_source_publication(
                run_id=self.run_id,
                source_epoch=2,
                expected_run_revision=1,
                old_run_record_sha256=self.initial_sha,
                replacement_run_record=self._replacement(source_epoch=2)[1],
                source_manifest_sha256="e" * 64,
                source_identity=self.source_identity,
                source_version="9" * 64,
            )
        self.assertEqual(
            self.store.active_source_publication(self.run_id)["intent_id"], intent_id
        )
        mutation_id = self.store.derive_run_state_mutation_id(
            run_id=self.run_id,
            expected_run_revision=1,
            old_run_record_sha256=self.initial_sha,
        )
        stale_replacement = copy.deepcopy(self.initial_run)
        stale_replacement.update(
            {
                "source_state": "stale",
                "coordination_revision": 2,
                "last_mutation_intent_id": mutation_id,
            }
        )
        with self.assertRaisesRegex(KernelConflict, "Promotion Slot"):
            self.store.prepare_run_state_mutation(
                run_id=self.run_id,
                expected_run_revision=1,
                old_run_record_sha256=self.initial_sha,
                replacement_run_record=stale_replacement,
            )

    def test_abort_is_idempotent_only_before_files_are_published(self) -> None:
        intent_id, _, _, _ = self._prepare()
        self.store.abort_source_publication(intent_id)
        self.store.abort_source_publication(intent_id)
        self.assertIsNone(self.store.active_source_publication(self.run_id))

        second_id, replacement, manifest_sha = self._replacement(source_epoch=2)
        replacement["coordination_revision"] = 2
        replacement["last_mutation_intent_id"] = second_id
        second = self.store.prepare_source_publication(
            run_id=self.run_id,
            source_epoch=2,
            expected_run_revision=1,
            old_run_record_sha256=self.initial_sha,
            replacement_run_record=replacement,
            source_manifest_sha256=manifest_sha,
            source_identity=self.source_identity,
            source_version="9" * 64,
        )
        self.store.bind_source_publication_journal(second["intent_id"], "a" * 64)
        self.store.transition_source_publication(
            second["intent_id"],
            expected_state="PREPARED",
            new_state="FILES_PUBLISHED",
        )
        with self.assertRaises(KernelConflict):
            self.store.abort_source_publication(second["intent_id"])

    def test_tampered_publication_authority_fails_global_health_check(self) -> None:
        intent_id, _, _, _ = self._prepare()
        with sqlite3.connect(self.store.path) as connection:
            connection.execute(
                "UPDATE source_publication_intents SET source_version=? "
                "WHERE intent_id=?",
                ("8" * 64, intent_id),
            )
        with self.assertRaises(ControlStoreUnavailable):
            self.store.check()

    def test_unsupported_source_publication_schema_object_fails_closed(self) -> None:
        with sqlite3.connect(self.store.path) as connection:
            connection.execute(
                "CREATE TABLE source_publication_shadow(value TEXT)"
            )
        with self.assertRaisesRegex(
            ControlStoreUnavailable,
            "unsupported Source Publication schema objects",
        ):
            self.store.check()

    def test_missing_v9_publication_authority_blocks_unrelated_mutation(self) -> None:
        with sqlite3.connect(self.store.path) as connection:
            connection.execute("DROP TABLE source_publication_intents")
        with self.assertRaises(ControlStoreUnavailable):
            self.store.prepare_initialization(
                run_id=uuid.uuid4().hex,
                output_path=self.workspace / "unrelated-run",
                intent_id=f"initialize-{uuid.uuid4().hex}",
                staging_path=self.root / "unrelated-staging",
            )

    def test_real_v8_store_migrates_to_v9_and_partial_v9_fails_closed(self) -> None:
        database = self.store.path
        with sqlite3.connect(database) as connection:
            for index in (
                "one_nonterminal_source_publication_per_run",
                "one_source_publication_epoch_per_run",
                "one_source_publication_revision_per_run",
            ):
                connection.execute(f"DROP INDEX {index}")
            connection.execute("DROP TABLE source_publication_intents")
            connection.execute("DELETE FROM schema_migrations WHERE version=9")
        migrated = ControlStore(self.workspace, self.contracts)
        self.assertEqual(migrated.check().schema_version, 9)
        self.assertEqual(migrated.current_run_record_sha(self.run_id), self.initial_sha)

        with sqlite3.connect(database) as connection:
            connection.execute("DELETE FROM schema_migrations WHERE version=9")
        with self.assertRaisesRegex(
            ControlStoreUnavailable,
            "partial v9 Source Publication migration",
        ):
            ControlStore(self.workspace, self.contracts)


if __name__ == "__main__":
    unittest.main()
