from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from video2pdf_workflow_kernel.contracts import ContractRegistry  # noqa: E402
from video2pdf_workflow_kernel.errors import (  # noqa: E402
    ArtifactDrift,
    ContractError,
    UnknownContractVersion,
)
from video2pdf_workflow_kernel.source_acquisition import (  # noqa: E402
    SourceReopenFault,
    SourceReopenSaga,
)
from tests.video_workflow import test_source_publication_integration  # noqa: E402
from video2pdf_workflow_kernel.utils import (  # noqa: E402
    canonical_json_bytes,
    read_json,
    write_json_atomic,
)


class SourceReopenJournalContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contracts = ContractRegistry(PROJECT_ROOT)

    def _ready_source_run(self, label: str) -> Path:
        kernel, run_dir, _ = (
            test_source_publication_integration.build_decision_ready_authority()
        )
        kernel.finalize_production_source(
            run_dir,
            published_at="2026-07-18T12:00:00+08:00",
        )
        return run_dir

    def _prepared_journal(self, label: str) -> tuple[Path, Path]:
        run_dir = self._ready_source_run(label)
        saga = SourceReopenSaga(run_dir, contracts=self.contracts)
        with self.assertRaisesRegex(SourceReopenFault, "after_reopen_prepared"):
            saga.reopen(
                reason="replace stale production evidence",
                validated_record=read_json(run_dir / "workflow/run.json"),
                fault_point="after_reopen_prepared",
            )
        journals = tuple((run_dir / "待删除/source-reopens").glob("*/reopen.json"))
        self.assertEqual(len(journals), 1)
        return run_dir, journals[0]

    def test_registered_journal_contract_validates_prepared_and_committed_states(
        self,
    ) -> None:
        run_dir, journal_path = self._prepared_journal("registered")
        prepared = read_json(journal_path)

        self.contracts.validate("source-reopen-journal", prepared)
        self.assertEqual(prepared["kernel_version"], "2.0.0")
        self.assertEqual(
            prepared["journal_path"],
            f"待删除/source-reopens/{prepared['intent_id']}/reopen.json",
        )
        self.assertEqual(prepared["coordination_record_path"], "workflow/run.json")
        self.assertEqual(
            {item["logical_id"] for item in prepared["preservations"]},
            {
                "source_package",
                "source_candidates",
                "source_candidate_inventory",
                "source_acquisition_decision_skeleton",
                "source_acquisition_decision",
            },
        )

        SourceReopenSaga(run_dir, contracts=self.contracts).reconcile()
        committed = read_json(journal_path)
        self.contracts.validate("source-reopen-journal", committed)
        self.assertEqual(committed["state"], "COMMITTED")

    def test_malformed_and_unknown_version_journals_fail_recovery_closed(self) -> None:
        for label, mutate, error in (
            (
                "malformed",
                lambda value: value.pop("kernel_version"),
                ContractError,
            ),
            (
                "unknown-version",
                lambda value: value.__setitem__("schema_version", "9.0.0"),
                UnknownContractVersion,
            ),
        ):
            with self.subTest(label=label):
                run_dir, journal_path = self._prepared_journal(label)
                journal = read_json(journal_path)
                mutate(journal)
                write_json_atomic(journal_path, journal)

                with self.assertRaises(error):
                    SourceReopenSaga(
                        run_dir,
                        contracts=self.contracts,
                    ).reconcile()

    def test_preservation_path_tamper_fails_recovery_closed(self) -> None:
        run_dir, journal_path = self._prepared_journal("path-tamper")
        journal = read_json(journal_path)
        journal["preservations"][0]["preservation_path"] = (
            "待删除/source-reopens/" + "f" * 64 + "/previous/source"
        )
        write_json_atomic(journal_path, journal)

        with self.assertRaises(ContractError):
            SourceReopenSaga(run_dir, contracts=self.contracts).reconcile()

    def test_batch_preflight_rejects_candidate_drift_before_any_move(self) -> None:
        run_dir, journal_path = self._prepared_journal("batch-preflight")
        inventory = read_json(
            run_dir / "work/source-acquisition/candidate-inventory.json"
        )
        candidate = run_dir.joinpath(
            *Path(inventory["candidates"][0]["staged_path"]).parts
        )
        candidate.write_bytes(candidate.read_bytes() + b"drift")

        with self.assertRaisesRegex(ArtifactDrift, "Candidate fingerprint"):
            SourceReopenSaga(run_dir, contracts=self.contracts).reconcile()

        journal = read_json(journal_path)
        self.assertEqual(journal["state"], "PREPARED")
        for item in journal["preservations"]:
            current = run_dir.joinpath(*Path(item["current_path"]).parts)
            preserved = run_dir.joinpath(*Path(item["preservation_path"]).parts)
            self.assertTrue(current.exists())
            self.assertFalse(preserved.exists())

    def test_replacement_hash_and_identity_relations_fail_recovery_closed(self) -> None:
        for label, rebind_hash in (
            ("hash-drift", False),
            ("run-identity-drift", True),
        ):
            with self.subTest(label=label):
                run_dir, journal_path = self._prepared_journal(label)
                journal = read_json(journal_path)
                if rebind_hash:
                    journal["replacement_run_record"]["run_id"] = "f" * 32
                    journal["replacement_run_record_sha256"] = hashlib.sha256(
                        canonical_json_bytes(journal["replacement_run_record"])
                    ).hexdigest()
                else:
                    journal["replacement_run_record"]["source_epoch"] += 1
                write_json_atomic(journal_path, journal)

                with self.assertRaises(ContractError):
                    SourceReopenSaga(
                        run_dir,
                        contracts=self.contracts,
                    ).reconcile()


if __name__ == "__main__":
    unittest.main()
