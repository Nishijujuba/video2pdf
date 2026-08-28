from pathlib import Path
import hashlib
import json
import os
import sys
import unittest
import uuid


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from video2pdf_workflow_kernel.cli import _parser
from video2pdf_workflow_kernel.delivery_quality import DeliveryQualityRegistry
from video2pdf_workflow_kernel.final_compile import (
    GuardedFinalCompileProvider,
    _validate_derived_text_origin_evidence,
    registered_generator_identity,
)
from video2pdf_workflow_kernel.errors import ContractError

from tests.video_workflow import test_guarded_final_compile_adapter as final_compile_fixture
from tests.video_workflow import test_precompile_quality as precompile_fixture


class GovernedTextOriginFinalCompileTests(unittest.TestCase):
    def _inventory_bound_adapter_fixture(
        self, declared_text: str = "Core claim"
    ) -> tuple[object, Path, dict]:
        fixture = final_compile_fixture.GuardedFinalCompileAdapterTests(
            methodName="test_public_adapter_compiles_and_derives_complete_evidence"
        )
        fixture.setUp()
        manifest = json.loads(fixture.manifest.read_text(encoding="utf-8"))
        source = manifest["entries"][0]
        item = {
            "item_id": "main",
            "declared_text": declared_text,
            "text_sha256": hashlib.sha256(declared_text.encode("utf-8")).hexdigest(),
            "representation": "structured_text",
            "source_artifact_logical_id": source["logical_id"],
            "source_generation": source["generation"],
            "source_sha256": source["sha256"],
        }
        item["item_sha256"] = final_compile_fixture.fingerprint(item, "item_sha256")
        inventory = {
            "schema_name": "reader-facing-text-inventory",
            "schema_version": "1.0.0",
            "items": [item],
        }
        inventory["inventory_sha256"] = final_compile_fixture.fingerprint(
            inventory, "inventory_sha256"
        )
        inventory_path = fixture.root / "reader-facing-text-inventory.json"
        inventory_path.write_bytes(final_compile_fixture.canonical_bytes(inventory))
        request = json.loads(fixture.request.read_text(encoding="utf-8"))
        request["schema_version"] = "2.0.0"
        request.pop("text_origin_plan_path")
        request.pop("text_origin_plan_sha256")
        request["reader_facing_text_inventory_path"] = str(inventory_path)
        request["reader_facing_text_inventory_sha256"] = inventory[
            "inventory_sha256"
        ]
        operation_id = f"issue92-adapter-{uuid.uuid4().hex}"
        execution = {
            "schema_name": "final-compile-execution",
            "schema_version": "1.0.0",
            "operation_id": operation_id,
            "state": "launch_pending",
            "adapter_pid": None,
            "exit_code": None,
        }
        execution["execution_sha256"] = final_compile_fixture.fingerprint(
            execution, "execution_sha256"
        )
        execution_path = fixture.root / "final-compile-execution.json"
        execution_path.write_bytes(final_compile_fixture.canonical_bytes(execution))
        request["operation_id"] = operation_id
        request["execution_state_path"] = str(execution_path)
        fixture.request.write_bytes(final_compile_fixture.canonical_bytes(request))
        return fixture, inventory_path, inventory

    def _rematerialize_inventory_source_binding(
        self, fixture: object, inventory_path: Path
    ) -> dict:
        manifest = json.loads(fixture.manifest.read_text(encoding="utf-8"))
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        inventory["items"][0]["source_sha256"] = manifest["entries"][0]["sha256"]
        inventory["items"][0]["item_sha256"] = final_compile_fixture.fingerprint(
            inventory["items"][0], "item_sha256"
        )
        inventory["inventory_sha256"] = final_compile_fixture.fingerprint(
            inventory, "inventory_sha256"
        )
        inventory_path.write_bytes(final_compile_fixture.canonical_bytes(inventory))
        request = json.loads(fixture.request.read_text(encoding="utf-8"))
        request["reader_facing_text_inventory_sha256"] = inventory[
            "inventory_sha256"
        ]
        fixture.request.write_bytes(final_compile_fixture.canonical_bytes(request))
        return inventory

    def _bind_declared_generated_style(
        self,
        fixture: object,
        inventory_path: Path,
        declared_text: str,
    ) -> None:
        style = fixture.root / "source/video2pdfnotes.sty"
        declared_tokens = declared_text.splitlines()
        style.write_text(
            "".join(
                f"\\newtcolorbox{{box{index}}}{{title={value}}}\n"
                for index, value in enumerate(declared_tokens, 1)
            ),
            encoding="utf-8",
        )
        manifest = json.loads(fixture.manifest.read_text(encoding="utf-8"))
        fixture.source.write_text("\\end{box1}\n", encoding="utf-8")
        manifest["entries"][0]["sha256"] = hashlib.sha256(
            fixture.source.read_bytes()
        ).hexdigest()
        manifest["entries"].append(
            {
                "logical_id": "local_style",
                "generation": 1,
                "sha256": hashlib.sha256(style.read_bytes()).hexdigest(),
                "source_path": str(style),
                "staging_path": "video2pdfnotes.sty",
            }
        )
        manifest["manifest_sha256"] = final_compile_fixture.fingerprint(
            manifest, "manifest_sha256"
        )
        fixture.manifest.write_bytes(final_compile_fixture.canonical_bytes(manifest))
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        item = inventory["items"][0]
        item.update(
            {
                "declared_text": declared_text,
                "text_sha256": hashlib.sha256(
                    declared_text.encode("utf-8")
                ).hexdigest(),
                "representation": "declared_generated_text",
                "source_artifact_logical_id": "local_style",
                "source_generation": 1,
                "source_sha256": manifest["entries"][-1]["sha256"],
            }
        )
        item["item_sha256"] = final_compile_fixture.fingerprint(
            item, "item_sha256"
        )
        inventory["inventory_sha256"] = final_compile_fixture.fingerprint(
            inventory, "inventory_sha256"
        )
        inventory_path.write_bytes(final_compile_fixture.canonical_bytes(inventory))
        request = json.loads(fixture.request.read_text(encoding="utf-8"))
        request["compile_manifest_sha256"] = manifest["manifest_sha256"]
        request["reader_facing_text_inventory_sha256"] = inventory[
            "inventory_sha256"
        ]
        fixture.request.write_bytes(final_compile_fixture.canonical_bytes(request))

    def test_public_final_compile_does_not_accept_postcompile_origin_plan(self) -> None:
        command = _parser()._subparsers._group_actions[0].choices[
            "delivery-quality-final-compile"
        ]

        option_strings = {
            option
            for action in command._actions
            for option in action.option_strings
        }

        self.assertNotIn("--text-origin-plan", option_strings)

    def test_production_adapter_derives_objects_and_origins_after_compile(self) -> None:
        fixture, _, _ = self._inventory_bound_adapter_fixture()

        completed = fixture._run()

        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        rendered = json.loads(
            (fixture.output / "rendered-text-object-inventory.json").read_text(
                encoding="utf-8"
            )
        )
        trace = json.loads(
            (fixture.output / "text-origin-trace.json").read_text(encoding="utf-8")
        )
        self.assertEqual(1, rendered["coverage"]["page_count"])
        self.assertEqual("Core claim", rendered["objects"][0]["exact_utf8_text"])
        self.assertEqual("sealed_origin", trace["edges"][0]["disposition"])
        self.assertEqual([rendered["objects"][0]["object_id"]], trace["edges"][0]["rendered_object_ids"])

    def test_adapter_rejects_stale_inventory_before_compile(self) -> None:
        fixture, _, _ = self._inventory_bound_adapter_fixture()
        request = json.loads(fixture.request.read_text(encoding="utf-8"))
        request["reader_facing_text_inventory_sha256"] = "0" * 64
        fixture.request.write_bytes(final_compile_fixture.canonical_bytes(request))

        completed = fixture._run()

        self.assertEqual(1, completed.returncode)
        self.assertIn("inventory identity is stale", completed.stderr)
        self.assertFalse(fixture.output.exists())

    def test_adapter_fails_closed_when_sealed_item_has_no_rendered_origin(self) -> None:
        fixture, inventory_path, inventory = self._inventory_bound_adapter_fixture()
        inventory["items"][0]["source_artifact_logical_id"] = "absent-source"
        inventory["items"][0]["item_sha256"] = final_compile_fixture.fingerprint(
            inventory["items"][0], "item_sha256"
        )
        inventory["inventory_sha256"] = final_compile_fixture.fingerprint(
            inventory, "inventory_sha256"
        )
        inventory_path.write_bytes(final_compile_fixture.canonical_bytes(inventory))
        request = json.loads(fixture.request.read_text(encoding="utf-8"))
        request["reader_facing_text_inventory_sha256"] = inventory[
            "inventory_sha256"
        ]
        fixture.request.write_bytes(final_compile_fixture.canonical_bytes(request))

        completed = fixture._run()

        self.assertEqual(1, completed.returncode)
        self.assertIn("origin coverage is incomplete", completed.stderr)
        self.assertFalse((fixture.output / "final-artifact-seal.json").exists())

    def test_declared_generated_style_text_uses_registered_generator(self) -> None:
        fixture, inventory_path, _ = self._inventory_bound_adapter_fixture()
        self._bind_declared_generated_style(
            fixture, inventory_path, "Core claim"
        )

        completed = fixture._run()

        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        trace = json.loads(
            (fixture.output / "text-origin-trace.json").read_text(encoding="utf-8")
        )
        self.assertEqual("generated", trace["edges"][0]["disposition"])
        self.assertEqual("declared_generated", trace["edges"][0]["recipe"])
        self.assertEqual("main", trace["edges"][0]["sealed_item_id"])
        self.assertEqual(
            "latex-style-box-title-v1",
            trace["edges"][0]["generator"]["generator_id"],
        )

    def test_declared_generated_style_text_fails_when_a_token_is_absent(self) -> None:
        fixture, inventory_path, _ = self._inventory_bound_adapter_fixture()
        self._bind_declared_generated_style(
            fixture, inventory_path, "Core claim\nMissing title"
        )

        completed = fixture._run()

        self.assertEqual(1, completed.returncode)
        self.assertIn("declared generated text is absent", completed.stderr)
        self.assertFalse((fixture.output / "final-artifact-seal.json").exists())

    def test_declared_style_generator_preserves_repeated_outputs(self) -> None:
        generator = registered_generator_identity("latex-style-box-title-v1")
        rendered_objects = [
            {
                "object_id": f"generated-{index}",
                "page": 1,
                "object_kind": "pdf_text_run",
                "bbox": [index, 0, index + 1, 1],
                "exact_utf8_text": "Core claim",
                "extractor_id": "fixture",
                "evidence_locator": f"page:1/object:{index}",
            }
            for index in range(2)
        ]
        evidence = {
            "page_count": 1,
            "extractor_suite": [
                {"extractor_id": "fixture", "extractor_sha256": "1" * 64}
            ],
            "rendered_objects": rendered_objects,
            "edges": [
                {
                    "edge_id": "generated-style",
                    "disposition": "generated",
                    "sealed_item_id": "style-item",
                    "rendered_object_ids": [
                        value["object_id"] for value in rendered_objects
                    ],
                    "recipe": "declared_generated",
                    "generator": {
                        **generator,
                        "inputs": {
                            "texts": ["Core claim", "Core claim"],
                            "source_artifact": {
                                "logical_id": "local_style",
                                "generation": 1,
                                "sha256": "2" * 64,
                            },
                        },
                        "source_mapping": {
                            "method": "compiler_synctex_v1",
                            "provider": {
                                "provider_id": "fixture-compiler-source-map-v1",
                                "provider_sha256": "3" * 64,
                            },
                            "object_sources": [
                                {
                                    "object_id": value["object_id"],
                                    "source_path": "C:/fixture/main.tex",
                                    "line": index + 1,
                                    "column": 1,
                                    "query": {
                                        "page": 1,
                                        "x": index + 0.5,
                                        "y": 0.5,
                                    },
                                }
                                for index, value in enumerate(rendered_objects)
                            ],
                        },
                    },
                }
            ],
            "sealed_items": [
                {
                    "item_id": "style-item",
                    "exact_utf8_text": "Core claim",
                    "representation": "declared_generated_text",
                    "source_artifact_logical_id": "local_style",
                    "source_generation": 1,
                    "source_sha256": "2" * 64,
                }
            ],
        }

        _validate_derived_text_origin_evidence(evidence)

        mismatched = json.loads(json.dumps(evidence))
        mismatched["edges"][0]["generator"]["inputs"]["texts"] = [
            "Different title",
            "Different title",
        ]
        for value in mismatched["rendered_objects"]:
            value["exact_utf8_text"] = "Different title"
        with self.assertRaisesRegex(
            ContractError, "generated origin is incomplete"
        ):
            _validate_derived_text_origin_evidence(mismatched)

        drifted_query = json.loads(json.dumps(evidence))
        drifted_query["edges"][0]["generator"]["source_mapping"][
            "object_sources"
        ][0]["query"]["x"] = 999
        with self.assertRaisesRegex(
            ContractError, "generated origin is incomplete"
        ):
            _validate_derived_text_origin_evidence(drifted_query)

        duplicated_source = json.loads(json.dumps(evidence))
        duplicated_source["edges"][0]["generator"]["source_mapping"][
            "object_sources"
        ].append(
            dict(
                duplicated_source["edges"][0]["generator"]["source_mapping"][
                    "object_sources"
                ][0]
            )
        )
        with self.assertRaisesRegex(
            ContractError, "generated origin is incomplete"
        ):
            _validate_derived_text_origin_evidence(duplicated_source)

    def test_adapter_fails_closed_on_ambiguous_sealed_origin(self) -> None:
        fixture, inventory_path, inventory = self._inventory_bound_adapter_fixture()
        duplicate = dict(inventory["items"][0])
        duplicate["item_id"] = "duplicate"
        duplicate["item_sha256"] = final_compile_fixture.fingerprint(
            duplicate, "item_sha256"
        )
        inventory["items"].append(duplicate)
        inventory["inventory_sha256"] = final_compile_fixture.fingerprint(
            inventory, "inventory_sha256"
        )
        inventory_path.write_bytes(final_compile_fixture.canonical_bytes(inventory))
        request = json.loads(fixture.request.read_text(encoding="utf-8"))
        request["reader_facing_text_inventory_sha256"] = inventory[
            "inventory_sha256"
        ]
        fixture.request.write_bytes(final_compile_fixture.canonical_bytes(request))

        completed = fixture._run()

        self.assertEqual(1, completed.returncode)
        self.assertIn("mapping is ambiguous", completed.stderr)

    def test_adapter_reports_unexpected_visible_annotation(self) -> None:
        # Single contradiction: an engine-created annotation has no governed source edge.
        # AdapterError currently exposes a stable message rather than a structured gate code.
        fixture, inventory_path, _ = self._inventory_bound_adapter_fixture()
        fixture._write_engine_directive("VIDEO2PDF_FIXTURE_UNEXPECTED_ANNOTATION")
        self._rematerialize_inventory_source_binding(fixture, inventory_path)

        completed = fixture._run()

        self.assertEqual(0, completed.returncode, completed.stderr)
        trace = json.loads(
            (fixture.output / "text-origin-trace.json").read_text(encoding="utf-8")
        )
        unexpected = [
            edge
            for edge in trace["edges"]
            if edge["disposition"] == "unexpected_addition"
        ]
        self.assertEqual(1, len(unexpected))

    def test_unregistered_text_extractor_fails_closed(self) -> None:
        evidence = {
            "page_count": 1,
            "extractor_suite": [
                {"extractor_id": "fixture", "extractor_sha256": "invalid"}
            ],
            "rendered_objects": [],
            "edges": [],
            "sealed_items": [],
        }

        with self.assertRaisesRegex(ContractError, "extractor suite is invalid"):
            _validate_derived_text_origin_evidence(evidence)

    def test_public_provider_rejects_adapter_drift(self) -> None:
        with self.assertRaisesRegex(ContractError, "registered Final Compile adapter"):
            GuardedFinalCompileProvider(PROJECT_ROOT)._validate_adapter_authority(
                final_compile_fixture.FAKE_ENGINE
            )

    def test_unsupported_generated_text_fails_closed(self) -> None:
        evidence = {
            "page_count": 1,
            "extractor_suite": [
                {"extractor_id": "fixture", "extractor_sha256": "1" * 64}
            ],
            "rendered_objects": [
                {
                    "object_id": "sealed-object",
                    "page": 1,
                    "object_kind": "pdf_text_run",
                    "bbox": [0, 0, 1, 1],
                    "exact_utf8_text": "sealed",
                    "extractor_id": "fixture",
                    "evidence_locator": "page:1/object:1",
                },
                {
                    "object_id": "generated-object",
                    "page": 1,
                    "object_kind": "pdf_text_run",
                    "bbox": [1, 1, 2, 2],
                    "exact_utf8_text": "generated",
                    "extractor_id": "fixture",
                    "evidence_locator": "page:1/object:2",
                },
            ],
            "edges": [
                {
                    "edge_id": "sealed",
                    "disposition": "sealed_origin",
                    "sealed_item_id": "sealed-item",
                    "sealed_text_utf8": "sealed",
                    "rendered_object_ids": ["sealed-object"],
                    "recipe": "exact_utf8",
                },
                {
                    "edge_id": "generated",
                    "disposition": "generated",
                    "rendered_object_ids": ["generated-object"],
                    "recipe": "declared_generated",
                    "generator": {
                        "generator_id": "unsupported-generator",
                        "generator_version": "1.0.0",
                        "generator_sha256": "2" * 64,
                        "kind": "unsupported",
                        "inputs": {},
                    },
                },
            ],
            "sealed_items": [
                {"item_id": "sealed-item", "exact_utf8_text": "sealed"}
            ],
        }

        with self.assertRaisesRegex(ContractError, "generated origin is incomplete"):
            _validate_derived_text_origin_evidence(evidence)

    def test_current_delivery_quality_registry_accepts_v2_compile_contract(self) -> None:
        registry = DeliveryQualityRegistry(PROJECT_ROOT)

        registry.check()
        registry.validate(
            "final-compile-report",
            json.loads(
                (PROJECT_ROOT / "delivery-quality/v1/final-compile-report.example.v1.json")
                .read_text(encoding="utf-8")
            ),
        )

    def test_precompile_report_projects_only_registered_reviewer_identity(self) -> None:
        fixture = precompile_fixture.PrecompileQualityCliTests(
            methodName="test_independent_complete_patches_materialize_pass_and_create_initial_seal"
        )
        workspace, completed, _ = fixture.prepare_case()
        self.assertEqual(0, completed.returncode, completed.stderr)
        for owner in (
            "source-faithfulness-reviewer",
            "writing-quality-reviewer",
            "pyramid-reviewer",
        ):
            committed, _ = fixture.commit_patch(
                workspace,
                owner,
                reviewer_runtime_descriptor="openai:gpt-5.6-sol;reasoning=medium",
            )
            self.assertEqual(0, committed.returncode, committed.stderr)
        materialized, _ = precompile_fixture.run_cli(
            "delivery-quality-precompile-materialize",
            "--workspace-root",
            str(workspace),
            "--provider-id",
            "precompile-quality-provider",
            "--provider-version",
            "1.0.0",
            "--materialized-at",
            "2026-08-28T08:12:00Z",
        )
        self.assertEqual(0, materialized.returncode, materialized.stderr)
        report = json.loads(
            (workspace / "precompile-quality-report.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertTrue(
            all(
                set(item["reviewer"])
                == {
                    "reviewer_id",
                    "runtime_sha256",
                    "independent_from_generation_producers",
                }
                for item in report["owner_reports"]
            )
        )

    def test_public_provider_compiles_without_operator_authored_postcompile_facts(self) -> None:
        fixture = final_compile_fixture.GuardedFinalCompileProviderAuthorityTests(
            methodName="test_public_final_compile_allows_unread_governance_entries"
        )
        fixture.setUp()

        workspace = fixture._run_public_final_compile_fixture()

        report = json.loads(
            (workspace / "final-compile-report.json").read_text(encoding="utf-8")
        )
        self.assertEqual("guarded-final-compile-v2", report["compile_adapter"]["protocol_version"])
        self.assertIn("reader_facing_text_inventory_sha256", report)
        self.assertNotIn("text_origin_plan_sha256", report)

    def test_completed_replay_rejects_drifted_rendered_page(self) -> None:
        fixture = final_compile_fixture.GuardedFinalCompileProviderAuthorityTests(
            methodName="test_public_final_compile_allows_unread_governance_entries"
        )
        fixture.setUp()
        workspace = fixture._run_public_final_compile_fixture()
        report = json.loads(
            (workspace / "final-compile-report.json").read_text(encoding="utf-8")
        )
        operation = json.loads(
            (workspace / "final-compile-operation.json").read_text(encoding="utf-8")
        )
        page = workspace / report["pdf"]["path"]
        rendered_page = workspace / "adapter-output/rendered_pages/page_001.png"
        self.assertTrue(page.is_file())
        with rendered_page.open("ab") as stream:
            stream.write(b"issue92-drift")

        with self.assertRaisesRegex(ContractError, "rendered page is stale"):
            GuardedFinalCompileProvider(PROJECT_ROOT)._validate_completed_replay(
                root=workspace,
                report=report,
                operation=operation,
            )

    def test_interrupted_publication_has_one_deterministic_archival_path(self) -> None:
        operation_id = f"issue92-interrupted-{uuid.uuid4().hex}"
        root = final_compile_fixture.TEST_RUNS / operation_id
        root.mkdir(parents=True)
        operation = {
            "schema_name": "final-compile-operation",
            "schema_version": "1.0.0",
            "operation_id": operation_id,
        }
        operation["operation_sha256"] = final_compile_fixture.fingerprint(
            operation, "operation_sha256"
        )
        (root / "final-compile-operation.json").write_bytes(
            final_compile_fixture.canonical_bytes(operation)
        )
        (root / "partial-evidence.json").write_text("{}\n", encoding="utf-8")

        result = GuardedFinalCompileProvider(PROJECT_ROOT).reconcile_interrupted(
            workspace_root=root
        )

        archive = Path(result["archive_path"])
        self.assertFalse(root.exists())
        self.assertTrue((archive / "partial-evidence.json").is_file())
        self.assertEqual(
            "final_compile_interrupted_archived", result["classification"]
        )

    def test_reconcile_refuses_to_move_a_live_final_compile_process(self) -> None:
        operation_id = f"issue92-live-{uuid.uuid4().hex}"
        root = final_compile_fixture.TEST_RUNS / operation_id
        root.mkdir(parents=True)
        operation = {
            "schema_name": "final-compile-operation",
            "schema_version": "1.0.0",
            "operation_id": operation_id,
        }
        operation["operation_sha256"] = final_compile_fixture.fingerprint(
            operation, "operation_sha256"
        )
        (root / "final-compile-operation.json").write_bytes(
            final_compile_fixture.canonical_bytes(operation)
        )
        execution = {
            "schema_name": "final-compile-execution",
            "schema_version": "1.0.0",
            "operation_id": operation_id,
            "state": "running",
            "adapter_pid": os.getpid(),
            "exit_code": None,
        }
        execution["execution_sha256"] = final_compile_fixture.fingerprint(
            execution, "execution_sha256"
        )
        (root / "final-compile-execution.json").write_bytes(
            final_compile_fixture.canonical_bytes(execution)
        )

        with self.assertRaisesRegex(ContractError, "still running"):
            GuardedFinalCompileProvider(PROJECT_ROOT).reconcile_interrupted(
                workspace_root=root
            )
        self.assertTrue(root.is_dir())


if __name__ == "__main__":
    unittest.main()
