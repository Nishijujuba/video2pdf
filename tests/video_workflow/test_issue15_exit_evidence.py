from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scripts import issue15_exit_evidence_contract as contract
from scripts import collect_issue15_exit_evidence as collector
from scripts import validate_slice_exit_evidence as validator
from tests.video_workflow._test_run import new_case_dir


SCHEMA_PATH = PROJECT_ROOT / "schemas" / "exit-evidence-manifest.v2.schema.json"


def _write_json(path: Path, value: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Issue15ExitEvidenceTests(unittest.TestCase):
    def _lineage_repository(self, label: str) -> tuple[Path, dict, Path, callable]:
        repository = new_case_dir(self.id(), label=label)

        def git(*args: str) -> str:
            return subprocess.run(
                ["git", *args],
                cwd=repository,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=True,
            ).stdout.strip()

        git("init")
        git("config", "user.email", "issue15@example.invalid")
        git("config", "user.name", "Issue 15 Test")
        git("config", "core.autocrlf", "false")
        (repository / "README.md").write_text("slice 14 fixture\n", encoding="utf-8")
        git("add", ".")
        git("commit", "-m", "base")
        implementation = repository / "src/issue15.py"
        implementation.parent.mkdir(parents=True, exist_ok=True)
        implementation.write_text("BATCH_ACTIVE = True\n", encoding="utf-8")
        git("add", ".")
        git("commit", "-m", "implementation")
        implementation_commit = git("rev-parse", "HEAD")
        input_path = repository / "evidence/slice-14/input.json"
        _write_json(input_path, {"qualified": True})
        manifest_path = repository / "evidence/slice-14/exit-evidence-manifest.json"
        manifest = {
            "implementation_commit": implementation_commit,
            "evidence_paths": [
                "evidence/slice-14/exit-evidence-manifest.json",
                "evidence/slice-14/input.json",
            ],
        }
        _write_json(manifest_path, manifest)
        return repository, manifest, manifest_path, git

    def test_slice14_lineage_accepts_pre_and_post_publication(self) -> None:
        repository, manifest, manifest_path, git = self._lineage_repository("slice14-lineage")
        with patch.object(validator, "PROJECT_ROOT", repository):
            validator.validate_lineage(manifest, manifest_path, pre_publication=True)
        git("add", ".")
        git("commit", "-m", "publish slice 14 evidence")
        with patch.object(validator, "PROJECT_ROOT", repository):
            validator.validate_lineage(manifest, manifest_path, pre_publication=False)

    def test_slice14_prepublication_rejects_dirty_non_evidence_path(self) -> None:
        repository, manifest, manifest_path, _git = self._lineage_repository("slice14-dirty")
        (repository / "src/drift.py").write_text("DRIFT = True\n", encoding="utf-8")
        with (
            patch.object(validator, "PROJECT_ROOT", repository),
            self.assertRaisesRegex(validator.EvidenceError, "non-evidence changes"),
        ):
            validator.validate_lineage(manifest, manifest_path, pre_publication=True)

    def test_slice14_postpublication_rejects_extra_committed_path(self) -> None:
        repository, manifest, manifest_path, git = self._lineage_repository("slice14-extra-path")
        (repository / "unexpected.txt").write_text("unexpected\n", encoding="utf-8")
        git("add", ".")
        git("commit", "-m", "publish evidence with extra path")
        with (
            patch.object(validator, "PROJECT_ROOT", repository),
            self.assertRaisesRegex(validator.EvidenceError, "closed allowlist"),
        ):
            validator.validate_lineage(manifest, manifest_path, pre_publication=False)

    def test_slice14_lineage_is_relocatable_after_publication(self) -> None:
        repository, manifest, _manifest_path, git = self._lineage_repository("slice14-relocation-source")
        git("add", ".")
        git("commit", "-m", "publish slice 14 evidence")
        relocated = repository.parent / f"{repository.name}-relocated"
        subprocess.run(
            ["git", "-c", "core.autocrlf=false", "clone", str(repository), str(relocated)],
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(relocated), "config", "core.autocrlf", "false"],
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=True,
        )
        relocated_manifest = relocated / "evidence/slice-14/exit-evidence-manifest.json"
        with patch.object(validator, "PROJECT_ROOT", relocated):
            validator.validate_lineage(manifest, relocated_manifest, pre_publication=False)

    def _materialized_binding_manifest(self) -> tuple[Path, dict, Path]:
        scratch = new_case_dir(self.id(), label="slice14-bindings")
        value = self._batch_semantic_fixture()
        implementation_commit = value["implementation_commit"]
        batch_record = json.loads(
            (PROJECT_ROOT / "tests/video_workflow/fixtures/contracts/batch-record.valid.json").read_text(
                encoding="utf-8"
            )
        )
        projection = deepcopy(batch_record["projections"][0]["item_projection"])
        batch_path = _write_json(scratch / "batch-record.json", batch_record)
        projection_path = _write_json(scratch / "projection-1.json", projection)
        authority_path = _write_json(
            scratch / "authority-evidence.json",
            {
                "duplicate_run_rejected": True,
                "pdf_existence_success_rejected": True,
                "per_video_mutation_rejected": True,
                "fairness_group_is_batch_id": True,
                "auth_breaker_delegated_to_resource_admission": True,
            },
        )
        value["batch_evidence"] = {
            "batch_record_contract_sha256": _sha256(PROJECT_ROOT / "schemas/video-workflow/v5/batch-record.v1.schema.json"),
            "batch_item_projection_contract_sha256": _sha256(PROJECT_ROOT / "schemas/video-workflow/v5/batch-item-projection.v1.schema.json"),
            "batch_record": {"role": "batch_record_evidence", "path": batch_path.relative_to(PROJECT_ROOT).as_posix(), "sha256": _sha256(batch_path)},
            "projections": [{
                "item_index": projection["item_index"],
                "run_id": projection["run_id"],
                "delivery_stage": projection["delivery_outcome"]["delivery_stage"],
                "guarded_delivered": projection["delivery_outcome"]["guarded_delivered"],
                "artifact": {"role": "batch_item_projection", "path": projection_path.relative_to(PROJECT_ROOT).as_posix(), "sha256": _sha256(projection_path)},
            }],
            "batch_guarded_delivered_count": 1,
            "negative_evidence": {
                "artifact": {"role": "batch_authority_evidence", "path": authority_path.relative_to(PROJECT_ROOT).as_posix(), "sha256": _sha256(authority_path)},
                "duplicate_run_rejected": True,
                "pdf_existence_success_rejected": True,
                "per_video_mutation_rejected": True,
                "fairness_group_is_batch_id": True,
                "auth_breaker_delegated_to_resource_admission": True,
            },
            "fairness_group_id": batch_record["batch_id"],
        }
        evidence_paths: set[str] = set()
        for index, command in enumerate(value["commands"], 1):
            command_id = command["test_id"]
            run_dir = scratch / "persisted" / command_id
            run_id = f"15151515-1515-4515-8515-{index:012d}"
            command_path = _write_json(run_dir / "command.json", {
                "run_id": run_id,
                "cwd": str(PROJECT_ROOT),
                "argv": command["command"],
                "accepted_exit_codes": [0],
                "git_commit": implementation_commit,
                "worktree_clean": True,
            })
            status_path = _write_json(run_dir / "status.json", {
                "run_id": run_id,
                "state": "succeeded",
                "exit_code": 0,
                "security": {"acceptance_evidence_eligible": True},
            })
            exit_path = run_dir / "exit-code.txt"
            exit_path.write_text("0\n", encoding="utf-8")
            log_path = scratch / "logs" / f"{command_id}.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(f"qualified\nEVIDENCE_IMPLEMENTATION_COMMIT: {implementation_commit}\n", encoding="utf-8")
            command["log"] = {"role": "command_log", "path": log_path.relative_to(PROJECT_ROOT).as_posix(), "sha256": _sha256(log_path)}
            command["persisted_run"] = {
                "run_id": run_id,
                "command_record": {"role": "persisted_command_record", "path": command_path.relative_to(PROJECT_ROOT).as_posix(), "sha256": _sha256(command_path)},
                "terminal_status": {"role": "persisted_terminal_status", "path": status_path.relative_to(PROJECT_ROOT).as_posix(), "sha256": _sha256(status_path)},
                "exit_code": {"role": "persisted_exit_code", "path": exit_path.relative_to(PROJECT_ROOT).as_posix(), "sha256": _sha256(exit_path)},
            }
            evidence_paths.add(command["log"]["path"])
            evidence_paths.update(item["path"] for item in command["persisted_run"].values() if isinstance(item, dict))
        # This focused binding fixture isolates evidence membership from the
        # separate committed-fixture gate exercised by the round-trip tests.
        value["fixtures"] = []
        manifest_path = scratch / "exit-evidence-manifest.json"
        evidence_paths.add(manifest_path.relative_to(PROJECT_ROOT).as_posix())
        value["evidence_paths"] = sorted(evidence_paths)
        _write_json(manifest_path, value)
        return manifest_path, value, projection_path

    def test_batch_artifacts_are_mandatory_evidence_paths(self) -> None:
        manifest_path, value, _projection_path = self._materialized_binding_manifest()
        with self.assertRaisesRegex(validator.EvidenceError, "evidence_paths"):
            validator.validate_bindings(value, manifest_path)

    def test_batch_projection_tampering_breaks_bound_sha(self) -> None:
        # scenario_id=projection_bytes_tampered; target_invariant=projection SHA binding;
        # mutation_seam=projection file after manifest binding; rematerialized_nodes=none;
        # intentionally_stale_nodes=projection artifact SHA; expected_first_gate=bindings;
        # expected_error_code=artifact_sha_mismatch; scenario_class=single_contradiction.
        manifest_path, value, projection_path = self._materialized_binding_manifest()
        batch = value["batch_evidence"]
        value["evidence_paths"] = sorted({
            *value["evidence_paths"],
            batch["batch_record"]["path"],
            batch["negative_evidence"]["artifact"]["path"],
            *[entry["artifact"]["path"] for entry in batch["projections"]],
        })
        _write_json(manifest_path, value)
        projection_path.write_bytes(projection_path.read_bytes() + b"\n")
        with self.assertRaisesRegex(validator.EvidenceError, "fingerprint mismatch"):
            validator.validate_bindings(value, manifest_path)

    def test_batch_validator_rejects_projection_summary_outside_batch_mapping(self) -> None:
        # scenario_id=projection_summary_foreign_run; target_invariant=Batch run mapping;
        # mutation_seam=manifest projection.run_id; rematerialized_nodes=manifest;
        # intentionally_stale_nodes=none; expected_first_gate=batch_evidence;
        # expected_error_code=projection_batch_mismatch; scenario_class=single_contradiction.
        _manifest_path, value, _projection_path = self._materialized_binding_manifest()
        value["batch_evidence"]["projections"][0]["run_id"] = "f" * 32
        with self.assertRaisesRegex(validator.EvidenceError, "does not belong"):
            validator.validate_batch_exit_evidence(value, project_root=PROJECT_ROOT)

    def test_slice14_validates_each_persisted_qualification_without_guarded_delivery_block(self) -> None:
        _manifest_path, value, _projection_path = self._materialized_binding_manifest()
        validator._validate_slice13_guarded_qualification(
            value,
            issue_commands=contract.COMMANDS,
            issue_label="Issue #15",
        )

    def _batch_semantic_fixture(self) -> dict:
        value = json.loads(
            (PROJECT_ROOT / "tests/video_workflow/fixtures/exit_evidence/slice14.valid.json").read_text(
                encoding="utf-8"
            )
        )
        value["slice_base_commit"] = contract.SLICE_BASE_COMMIT
        value["activation_scope"] = deepcopy(contract.ACTIVATION_SCOPE)
        value["results"] = deepcopy(contract.RESULTS)
        value["result_bindings"] = deepcopy(contract.RESULT_BINDINGS)
        command_by_id = {item["test_id"]: item for item in value["commands"]}
        value["commands"] = []
        for command_id, argv, expected_exit in contract.COMMANDS:
            item = command_by_id[command_id]
            item["command"] = list(argv)
            item["expected_exit_code"] = expected_exit
            item["actual_exit_code"] = expected_exit
            value["commands"].append(item)
        value["mirror_checks"] = [
            {
                "source_path": source,
                "mirror_path": mirror,
                "source_sha256": _sha256(PROJECT_ROOT / source),
                "mirror_sha256": _sha256(PROJECT_ROOT / mirror),
                "status": "equal",
            }
            for source, mirror in contract.MIRROR_SPECS
        ]
        return value

    def test_batch_mirror_checks_are_repo_relative_and_relocatable(self) -> None:
        _manifest_path, value, _projection_path = self._materialized_binding_manifest()
        validated = validator.validate_batch_exit_evidence(
            value,
            project_root=PROJECT_ROOT,
        )
        self.assertEqual(
            validated["mirror_checks"][0]["source_path"],
            ".agents/skills/bilibili-batch-render-pdf/SKILL.md",
        )

    def _collection_inputs(self) -> tuple[Path, Path, Path, dict[str, Path]]:
        scratch = new_case_dir(self.id(), label="issue15-collection")
        batch_record = json.loads(
            (PROJECT_ROOT / "tests/video_workflow/fixtures/contracts/batch-record.valid.json").read_text(
                encoding="utf-8"
            )
        )
        projection = deepcopy(batch_record["projections"][0]["item_projection"])
        batch_path = _write_json(scratch / "batch-record.json", batch_record)
        projections_dir = scratch / "projections"
        _write_json(projections_dir / "item-1.json", projection)
        authority_path = _write_json(
            scratch / "authority-evidence.json",
            {
                "duplicate_run_rejected": True,
                "pdf_existence_success_rejected": True,
                "per_video_mutation_rejected": True,
                "fairness_group_is_batch_id": True,
                "auth_breaker_delegated_to_resource_admission": True,
            },
        )
        qualification_runs: dict[str, Path] = {}
        commit = "a" * 40
        for index, (command_id, argv, expected_exit) in enumerate(contract.COMMANDS, 1):
            run_dir = scratch / "qualification" / command_id
            run_id = f"15151515-1515-4515-8515-{index:012d}"
            _write_json(
                run_dir / "command.json",
                {
                    "run_id": run_id,
                    "cwd": str(PROJECT_ROOT),
                    "argv": list(argv),
                    "accepted_exit_codes": [expected_exit],
                    "git_commit": commit,
                    "worktree_clean": True,
                },
            )
            _write_json(
                run_dir / "status.json",
                {
                    "run_id": run_id,
                    "state": "succeeded",
                    "exit_code": expected_exit,
                    "security": {"acceptance_evidence_eligible": True},
                },
            )
            (run_dir / "exit-code.txt").write_text(f"{expected_exit}\n", encoding="utf-8")
            (run_dir / "command.log").write_text(f"qualified {command_id}\n", encoding="utf-8")
            qualification_runs[command_id] = run_dir
        return batch_path, projections_dir, authority_path, qualification_runs

    def test_collect_binds_batch_inputs_and_resource_authority(self) -> None:
        batch_path, projections_dir, authority_path, qualification_runs = self._collection_inputs()
        output = batch_path.parent / "collection.json"
        value = collector.collect(
            batch_record_path=batch_path,
            projections_dir=projections_dir,
            negative_evidence_path=authority_path,
            fairness_group_id="0123456789abcdef0123456789abcdef",
            qualification_runs=qualification_runs,
            output=output,
        )
        projection = value["batch_evidence"]["projections"][0]
        self.assertEqual(projection["artifact"]["path"], projections_dir.joinpath("item-1.json").relative_to(PROJECT_ROOT).as_posix())
        self.assertEqual(projection["artifact"]["sha256"], _sha256(projections_dir / "item-1.json"))
        authority = value["batch_evidence"]["negative_evidence"]
        self.assertEqual(authority["artifact"]["path"], authority_path.relative_to(PROJECT_ROOT).as_posix())
        self.assertTrue(authority["fairness_group_is_batch_id"])
        self.assertTrue(authority["auth_breaker_delegated_to_resource_admission"])

    def test_collect_rejects_projection_from_another_batch(self) -> None:
        # scenario_id=foreign_projection; target_invariant=projection Batch ownership;
        # mutation_seam=projection.batch_id; rematerialized_nodes=projection file;
        # intentionally_stale_nodes=none; expected_first_gate=batch_evidence;
        # expected_error_code=projection_batch_mismatch; scenario_class=single_contradiction.
        batch_path, projections_dir, authority_path, qualification_runs = self._collection_inputs()
        projection_path = projections_dir / "item-1.json"
        projection = json.loads(projection_path.read_text(encoding="utf-8"))
        projection["batch_id"] = "f" * 32
        _write_json(projection_path, projection)
        with self.assertRaisesRegex(collector.CollectionError, "does not belong to the Batch Record"):
            collector.collect(
                batch_record_path=batch_path,
                projections_dir=projections_dir,
                negative_evidence_path=authority_path,
                fairness_group_id="0123456789abcdef0123456789abcdef",
                qualification_runs=qualification_runs,
                output=batch_path.parent / "foreign-collection.json",
            )
    def test_slice14_contract_constants_are_pinned(self) -> None:
        self.assertEqual(contract.SLICE_NUMBER, 14)
        self.assertEqual(contract.SLICE_NAME, "batch-projection-cutover")
        self.assertEqual(
            contract.PLATFORM_STATUSES,
            {"bilibili": "active_kernel", "youtube": "active_kernel"},
        )
        self.assertEqual(len(contract.ATOMIC_MEMBERS), 14)
        self.assertEqual(contract.ACTIVATION_SCOPE["kind"], "batch_cutover")
        self.assertTrue(contract.ACTIVATION_SCOPE["runtime_authority_change"])
        kinds = {spec[1] for spec in contract.RESULT_SPECS}
        self.assertEqual(
            kinds, {"positive", "negative", "recovery", "fencing", "fairness"}
        )

    def test_closed_qualification_covers_every_issue15_module_and_resource_authority(self) -> None:
        command_targets = {
            argument
            for _command_id, argv, _expected_exit in contract.COMMANDS
            for argument in argv
            if argument.startswith("tests.video_workflow.test_issue15_")
        }
        self.assertEqual(
            command_targets,
            {
                "tests.video_workflow.test_issue15_batch_authority",
                "tests.video_workflow.test_issue15_batch_cli",
                "tests.video_workflow.test_issue15_batch_contracts",
                "tests.video_workflow.test_issue15_batch_policy_docs",
                "tests.video_workflow.test_issue15_batch_projection",
                "tests.video_workflow.test_issue15_control_store_batch",
                "tests.video_workflow.test_issue15_exit_evidence",
            },
        )
        bindings = {item[0]: item for item in contract.RESULT_SPECS}
        self.assertEqual(
            bindings["fairness_group_is_batch_id"][2],
            "tests.video_workflow.test_issue15_batch_authority.test_fairness_group_id_is_batch_id",
        )
        self.assertEqual(
            bindings["auth_breaker_uses_resource_admission"][2],
            "tests.video_workflow.test_issue15_batch_authority.test_auth_breaker_flows_through_resource_admission",
        )

    def test_slice14_positive_fixture_validates_against_schema(self) -> None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        fixture = json.loads(
            (
                PROJECT_ROOT
                / "tests"
                / "video_workflow"
                / "fixtures"
                / "exit_evidence"
                / "slice14.valid.json"
            ).read_text(encoding="utf-8")
        )
        Draft202012Validator(schema).validate(fixture)
        self.assertEqual(fixture["slice"]["number"], 14)
        self.assertEqual(fixture["overall_decision"], "pass")

    def test_slice14_schema_requires_batch_evidence(self) -> None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        fixture = json.loads(
            (PROJECT_ROOT / "tests/video_workflow/fixtures/exit_evidence/slice14.valid.json").read_text(
                encoding="utf-8"
            )
        )
        fixture.pop("batch_evidence")
        errors = list(Draft202012Validator(schema).iter_errors(fixture))
        self.assertTrue(errors)

    def test_slice14_invalid_fixture_rejected_by_schema(self) -> None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        fixture = json.loads(
            (
                PROJECT_ROOT
                / "tests"
                / "video_workflow"
                / "fixtures"
                / "exit_evidence"
                / "slice14.invalid.json"
            ).read_text(encoding="utf-8")
        )
        errors = list(Draft202012Validator(schema).iter_errors(fixture))
        self.assertTrue(errors)

    def test_slice14_fixture_cannot_bypass_common_binding_and_lineage_gates(self) -> None:
        """A schema fixture carries no materialized files or Git publication authority."""
        manifest_path, value, _projection_path = self._materialized_binding_manifest()
        value["fixtures"] = [
            {"role": role, "path": path, "sha256": _sha256(PROJECT_ROOT / path)}
            for role, path in contract.FIXTURE_SPECS
        ]
        _write_json(manifest_path, value)
        with (
            patch.object(validator.ContractRegistry, "check", return_value=None),
            self.assertRaisesRegex(validator.EvidenceError, "evidence_paths"),
        ):
            validator.validate_manifest(
                manifest_path,
                schema_only=False,
                pre_publication=True,
            )

    def test_slice14_invalid_fixture_fails_dispatch(self) -> None:
        manifest_path = (
            PROJECT_ROOT
            / "tests"
            / "video_workflow"
            / "fixtures"
            / "exit_evidence"
            / "slice14.invalid.json"
        )
        result = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                "-B",
                str(PROJECT_ROOT / "scripts" / "validate_slice_exit_evidence.py"),
                "--pre-publication",
                str(manifest_path),
            ],
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
