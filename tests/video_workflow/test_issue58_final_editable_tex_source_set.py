from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
import uuid

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def canonical_sha(value: object, field: str) -> str:
    material = {key: item for key, item in value.items() if key != field}
    encoded = (
        json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from video2pdf_workflow_kernel.delivery_quality import DeliveryQualityRegistry
from video2pdf_workflow_kernel.contracts import ContractRegistry
from video2pdf_workflow_kernel.errors import CompileDependencyGap
from video2pdf_workflow_kernel.source_candidates import KERNEL_VERSION


class Issue58FinalEditableTexSourceSetTests(unittest.TestCase):
    """Final Compile exposes the exact editable TeX closure through public contracts."""

    def _isolated_authority_clone(self, tag: str) -> Path:
        """Clone the worktree into a detached fixture authority repository."""
        isolated_root = PROJECT_ROOT / f"待删除/i58{tag}" / uuid.uuid4().hex[:6]
        isolated_root.parent.mkdir(parents=True, exist_ok=True)
        for command in (
            (
                "git", "clone", "--no-checkout", "--shared",
                str(PROJECT_ROOT), str(isolated_root),
            ),
            ("git", "sparse-checkout", "init", "--cone"),
            (
                "git", "sparse-checkout", "set",
                "scripts", "src", "schemas", "delivery-quality", "prompts",
                "requirements", "docs/acceptance", "tests/video_workflow",
            ),
            ("git", "checkout", "--detach", "HEAD"),
        ):
            completed = subprocess.run(
                command,
                cwd=isolated_root.parent if command[1] == "clone" else isolated_root,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        for relative in (
            "scripts", "src", "schemas", "delivery-quality", "prompts",
            "requirements", "docs/acceptance", "tests/video_workflow",
        ):
            shutil.copytree(
                PROJECT_ROOT / relative,
                isolated_root / relative,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
        for command in (
            (
                "git", "add", "--", "scripts", "src", "schemas",
                "delivery-quality", "prompts", "requirements", "docs/acceptance",
                "tests/video_workflow",
            ),
            (
                "git", "-c", "user.name=Issue 58 Fixture",
                "-c", "user.email=issue58@example.invalid",
                "commit", "--allow-empty", "-m", "fixture authority",
            ),
        ):
            completed = subprocess.run(
                command,
                cwd=isolated_root,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        return isolated_root

    def test_public_contract_accepts_current_kernel_version_binding(self) -> None:
        artifact = json.loads(
            (
                PROJECT_ROOT
                / "tests/video_workflow/fixtures/contracts/final-editable-tex-source-set.valid.json"
            ).read_text(encoding="utf-8")
        )
        artifact["kernel_version"] = KERNEL_VERSION
        artifact["source_set_sha256"] = canonical_sha(
            artifact, "source_set_sha256"
        )

        ContractRegistry(PROJECT_ROOT).validate(
            "final-editable-tex-source-set", artifact
        )

    def test_public_contract_accepts_explicit_project_source_boundary(self) -> None:
        artifact = json.loads(
            (
                PROJECT_ROOT
                / "tests/video_workflow/fixtures/contracts/final-editable-tex-source-set.valid.json"
            ).read_text(encoding="utf-8")
        )
        artifact["project_root"] = "/project"
        artifact["members"][0]["path"] = "/project/main.tex"
        artifact["compile_evidence"]["consumed_project_tex_sources"][0][
            "path"
        ] = "/project/main.tex"
        artifact["source_set_sha256"] = canonical_sha(
            artifact, "source_set_sha256"
        )

        ContractRegistry(PROJECT_ROOT).validate(
            "final-editable-tex-source-set", artifact
        )

    def test_public_validator_rejects_non_tex_member_first(self) -> None:
        # scenario_id: non_tex_editable_member
        # target_invariant: every editable source-set member is TeX.
        # mutation_seam: change the sole member/evidence path suffix together.
        # rematerialized_nodes: compile evidence and source-set fingerprint.
        # deliberately_stale_nodes: none.
        # expected_first_failing_gate: final_editable_tex_source_set_project_membership.
        # expected_error_code: final_tex_member_not_tex.
        artifact = json.loads(
            (
                PROJECT_ROOT
                / "tests/video_workflow/fixtures/contracts/final-editable-tex-source-set.valid.json"
            ).read_text(encoding="utf-8")
        )
        artifact["members"][0]["path"] = "/project/main.md"
        artifact["compile_evidence"]["consumed_project_tex_sources"][0][
            "path"
        ] = "/project/main.md"
        artifact["source_set_sha256"] = canonical_sha(
            artifact, "source_set_sha256"
        )

        with self.assertRaises(CompileDependencyGap) as raised:
            ContractRegistry(PROJECT_ROOT).validate(
                "final-editable-tex-source-set", artifact
            )

        self.assertEqual(
            "final_editable_tex_source_set_project_membership",
            raised.exception.data["first_failing_gate"],
        )
        self.assertEqual(
            "final_tex_member_not_tex", raised.exception.data["error_code"]
        )

    def test_public_validator_rejects_member_outside_project_first(self) -> None:
        # scenario_id: editable_member_outside_project
        # target_invariant: every editable source path stays in project_root.
        # mutation_seam: move the sole member/evidence path outside that boundary.
        # rematerialized_nodes: compile evidence and source-set fingerprint.
        # deliberately_stale_nodes: none.
        # expected_first_failing_gate: final_editable_tex_source_set_project_membership.
        # expected_error_code: final_tex_member_outside_project.
        artifact = json.loads(
            (
                PROJECT_ROOT
                / "tests/video_workflow/fixtures/contracts/final-editable-tex-source-set.valid.json"
            ).read_text(encoding="utf-8")
        )
        artifact["members"][0]["path"] = "/other/main.tex"
        artifact["compile_evidence"]["consumed_project_tex_sources"][0][
            "path"
        ] = "/other/main.tex"
        artifact["source_set_sha256"] = canonical_sha(
            artifact, "source_set_sha256"
        )

        with self.assertRaises(CompileDependencyGap) as raised:
            ContractRegistry(PROJECT_ROOT).validate(
                "final-editable-tex-source-set", artifact
            )

        self.assertEqual(
            "final_editable_tex_source_set_project_membership",
            raised.exception.data["first_failing_gate"],
        )
        self.assertEqual(
            "final_tex_member_outside_project",
            raised.exception.data["error_code"],
        )

    def test_public_validator_rejects_duplicate_logical_identity_first(self) -> None:
        # scenario_id: duplicate_editable_logical_identity
        # target_invariant: source-set logical identities are unique.
        # mutation_seam: add one included source with the entrypoint logical_id.
        # rematerialized_nodes: compile evidence and source-set fingerprint.
        # deliberately_stale_nodes: none.
        # expected_first_failing_gate: final_editable_tex_source_set_identity.
        # expected_error_code: final_tex_logical_id_duplicate.
        artifact = json.loads(
            (
                PROJECT_ROOT
                / "tests/video_workflow/fixtures/contracts/final-editable-tex-source-set.valid.json"
            ).read_text(encoding="utf-8")
        )
        duplicate = {
            "logical_id": "integrated_main_tex",
            "generation": 2,
            "path": "/project/chapter.tex",
            "sha256": "7" * 64,
            "size": 8,
        }
        artifact["members"].append(
            {**duplicate, "role": "included_tex_source"}
        )
        artifact["compile_evidence"]["consumed_project_tex_sources"].append(
            duplicate
        )
        artifact["source_set_sha256"] = canonical_sha(
            artifact, "source_set_sha256"
        )

        with self.assertRaises(CompileDependencyGap) as raised:
            ContractRegistry(PROJECT_ROOT).validate(
                "final-editable-tex-source-set", artifact
            )

        self.assertEqual(
            "final_editable_tex_source_set_identity",
            raised.exception.data["first_failing_gate"],
        )
        self.assertEqual(
            "final_tex_logical_id_duplicate",
            raised.exception.data["error_code"],
        )

    def test_public_validator_rejects_duplicate_path_identity_first(self) -> None:
        # scenario_id: duplicate_editable_path_identity
        # target_invariant: source-set current paths are unique.
        # mutation_seam: add a distinct logical source at the entrypoint path.
        # rematerialized_nodes: compile evidence and source-set fingerprint.
        # deliberately_stale_nodes: none.
        # expected_first_failing_gate: final_editable_tex_source_set_identity.
        # expected_error_code: final_tex_path_duplicate.
        artifact = json.loads(
            (
                PROJECT_ROOT
                / "tests/video_workflow/fixtures/contracts/final-editable-tex-source-set.valid.json"
            ).read_text(encoding="utf-8")
        )
        duplicate = {
            "logical_id": "chapter",
            "generation": 2,
            "path": artifact["members"][0]["path"],
            "sha256": artifact["members"][0]["sha256"],
            "size": artifact["members"][0]["size"],
        }
        artifact["members"].append(
            {**duplicate, "role": "included_tex_source"}
        )
        artifact["compile_evidence"]["consumed_project_tex_sources"].append(
            duplicate
        )
        artifact["source_set_sha256"] = canonical_sha(
            artifact, "source_set_sha256"
        )

        with self.assertRaises(CompileDependencyGap) as raised:
            ContractRegistry(PROJECT_ROOT).validate(
                "final-editable-tex-source-set", artifact
            )

        self.assertEqual(
            "final_editable_tex_source_set_identity",
            raised.exception.data["first_failing_gate"],
        )
        self.assertEqual(
            "final_tex_path_duplicate", raised.exception.data["error_code"]
        )

    def test_final_compile_report_v1_remains_backward_compatible(self) -> None:
        report = json.loads(
            (PROJECT_ROOT / "delivery-quality/v1/final-compile-report.example.v1.json")
            .read_text(encoding="utf-8")
        )
        self.assertNotIn("final_editable_tex_source_set", report)
        report["report_sha256"] = canonical_sha(report, "report_sha256")

        DeliveryQualityRegistry(PROJECT_ROOT).validate("final-compile-report", report)

    def test_video_workflow_contract_accepts_standalone_monolithic_source_set(self) -> None:
        source = {
            "logical_id": "integrated_main_tex", "generation": 1,
            "path": "/project/main.tex", "sha256": "3" * 64, "size": 12,
        }
        artifact = {
            "schema_name": "final-editable-tex-source-set",
            "schema_version": "1.0.0",
            "kernel_version": KERNEL_VERSION,
            "project_root": "/project",
            "generation_set_sha256": "e" * 64,
            "final_pdf": {"path": "final.pdf", "sha256": "4" * 64, "size": 1},
            "compile_evidence": {
                "compile_manifest_sha256": "5" * 64,
                "recorder_path": "adapter-output/compile-recorder.fls",
                "recorder_sha256": "6" * 64,
                "dependency_closure_complete": True,
                "final_pdf": {"path": "final.pdf", "sha256": "4" * 64, "size": 1},
                "tex_entrypoint_logical_id": "integrated_main_tex",
                "consumed_project_tex_sources": [source],
            },
            "members": [{**source, "role": "tex_entrypoint"}],
        }
        artifact["source_set_sha256"] = canonical_sha(artifact, "source_set_sha256")

        ContractRegistry(PROJECT_ROOT).validate("final-editable-tex-source-set", artifact)

    def test_video_workflow_contract_rejects_omitted_consumed_tex_source(self) -> None:
        main = {
            "logical_id": "main", "generation": 2, "path": "/project/book.tex",
            "sha256": "1" * 64, "size": 9,
        }
        chapter = {
            "logical_id": "chapter", "generation": 3,
            "path": "/project/chapters/one.tex", "sha256": "2" * 64, "size": 7,
        }
        artifact = {
            "schema_name": "final-editable-tex-source-set", "schema_version": "1.0.0",
            "kernel_version": KERNEL_VERSION,
            "project_root": "/project",
            "generation_set_sha256": "e" * 64,
            "final_pdf": {"path": "book.pdf", "sha256": "4" * 64, "size": 1},
            "compile_evidence": {
                "compile_manifest_sha256": "5" * 64,
                "recorder_path": "adapter-output/book.fls", "recorder_sha256": "6" * 64,
                "dependency_closure_complete": True,
                "final_pdf": {"path": "book.pdf", "sha256": "4" * 64, "size": 1},
                "tex_entrypoint_logical_id": "main",
                "consumed_project_tex_sources": [main, chapter],
            },
            "members": [{**main, "role": "tex_entrypoint"}],
        }
        artifact["source_set_sha256"] = canonical_sha(artifact, "source_set_sha256")

        with self.assertRaises(CompileDependencyGap) as raised:
            ContractRegistry(PROJECT_ROOT).validate("final-editable-tex-source-set", artifact)

        self.assertEqual(
            "final_editable_tex_source_set_membership",
            raised.exception.data["first_failing_gate"],
        )
        self.assertEqual(
            "final_tex_dependency_evidence_mismatch",
            raised.exception.data["error_code"],
        )

    def test_public_final_compile_uses_explicit_book_entrypoint_evidence(self) -> None:
        from tests.video_workflow.test_rendered_text_reconciliation import (
            RenderedTextReconciliationCliTests,
            canonical_sha as report_fixture_sha,
            write_json,
        )

        fixture = RenderedTextReconciliationCliTests(
            "test_public_final_compile_invokes_adapter_and_produces_reconcilable_evidence"
        )
        root, paths = fixture.fixture(production_compile=True)
        manifest = json.loads(paths["compile_manifest"].read_text(encoding="utf-8"))
        entry = next(
            item for item in manifest["entries"]
            if item["staging_path"] == "main.tex"
        )
        entry["staging_path"] = "book.tex"
        manifest["manifest_sha256"] = report_fixture_sha(
            {
                key: value for key, value in manifest.items()
                if key != "manifest_sha256"
            }
        )
        write_json(paths["compile_manifest"], manifest)

        completed, envelope, _workspace = fixture.final_compile(
            root,
            paths,
            compiler_evidence_fixture=True,
            tex_entrypoint_logical_id=entry["logical_id"],
        )

        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        artifact = json.loads(
            Path(envelope["data"]["final_editable_tex_source_set_path"])
            .read_text(encoding="utf-8")
        )
        self.assertEqual(entry["logical_id"], artifact["members"][0]["logical_id"])
        self.assertEqual(
            str(Path(entry["source_path"]).resolve()),
            artifact["members"][0]["path"],
        )
        self.assertEqual("tex_entrypoint", artifact["members"][0]["role"])

    def test_public_final_compile_rejects_consumed_tex_missing_from_manifest(self) -> None:
        from tests.video_workflow.test_rendered_text_reconciliation import (
            RenderedTextReconciliationCliTests,
        )

        fixture = RenderedTextReconciliationCliTests(
            "test_public_final_compile_invokes_adapter_and_produces_reconcilable_evidence"
        )
        root, paths = fixture.fixture(production_compile=True)
        completed, envelope, workspace = fixture.final_compile(
            root, paths,
            plan_updates={"fixture_extra_consumed_tex": True},
            compiler_evidence_fixture=True,
        )

        self.assertEqual(40, completed.returncode, completed.stdout + completed.stderr)
        self.assertEqual(
            "final_editable_tex_source_set_membership",
            envelope["data"]["first_failing_gate"],
        )
        self.assertEqual(
            "final_tex_dependency_evidence_mismatch",
            envelope["data"]["error_code"],
        )
        self.assertFalse((workspace / "final-editable-tex-source-set.json").exists())

    def test_public_final_compile_reports_real_missing_include_stably(self) -> None:
        # scenario_id: real_missing_tex_include
        # target_invariant: compiler-observed missing TeX includes fail closed with
        # a stable Final Editable TeX Source Set classification.
        # mutation_seam: add one \input reference whose target does not exist.
        # rematerialized_nodes: main source identity, generation set, manifest,
        # seals, inventory, plan, and downstream bindings.
        # deliberately_stale_nodes: none.
        # expected_first_failing_gate: final_editable_tex_source_set_dependency_evidence.
        # expected_error_code: final_tex_include_missing.
        # scenario_class: single_contradiction.
        from tests.video_workflow._issue43_git_authority import (
            build_current_global_gate_authority,
        )
        from tests.video_workflow.test_rendered_text_reconciliation import (
            RenderedTextReconciliationCliTests,
        )
        from video2pdf_workflow_kernel.global_gate import GlobalGatePublisher

        fixture = RenderedTextReconciliationCliTests(
            "test_public_final_compile_invokes_adapter_and_produces_reconcilable_evidence"
        )
        source_root, source_paths = fixture.fixture(
            production_compile=True,
            main_tex_content="\\input{chapters/missing}\n",
            authority_root=PROJECT_ROOT / "待删除/i58m-source" / uuid.uuid4().hex[:6],
        )
        origins = json.loads(source_paths["origins"].read_text(encoding="utf-8"))
        rendered = json.loads(source_paths["rendered"].read_text(encoding="utf-8"))
        seal = json.loads(
            (
                source_paths["precompile_workspace"]
                / "precompile-text-seal.json"
            ).read_text(encoding="utf-8")
        )
        plan = {
            "schema_name": "text-origin-plan",
            "schema_version": "1.0.0",
            "precompile_text_seal_sha256": seal["seal_sha256"],
            "sealed_items": [
                {
                    "item_id": edge["sealed_item_id"],
                    "exact_utf8_text": edge["sealed_text_utf8"],
                }
                for edge in origins["edges"]
                if edge["disposition"] == "sealed_origin"
            ],
            "page_count": rendered["coverage"]["page_count"],
            "extractor_suite": rendered["extractor_suite"],
            "rendered_objects": [
                {
                    key: value
                    for key, value in item.items()
                    if key not in {"text_sha256", "object_sha256"}
                }
                for item in rendered["objects"]
            ],
            "edges": origins["edges"],
        }
        plan["plan_sha256"] = canonical_sha(plan, "plan_sha256")
        (source_root / "text-origin-plan.json").write_text(
            json.dumps(
                plan,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )

        isolated_root = self._isolated_authority_clone("m")

        control_store_root = isolated_root / "workspace"
        control_store_root.mkdir()
        authority_repository, exit_evidence = build_current_global_gate_authority(
            control_store_root,
            source_git_repository=isolated_root,
            authority_overlay_root=isolated_root,
        )
        GlobalGatePublisher(project_root=authority_repository).activate(
            control_store_root=control_store_root,
            exit_evidence=exit_evidence,
            activated_at="2026-08-25T13:30:00+08:00",
        )

        legacy_root = control_store_root / "v" / uuid.uuid4().hex[:6]
        shutil.copytree(source_root, legacy_root)
        legacy_quality = legacy_root / "precompile"
        legacy_manifest_path = legacy_root / "compile-manifest.json"
        legacy_origin_plan_path = legacy_root / "text-origin-plan.json"
        legacy_runtime_policy = legacy_quality / "legacy-runtime-policy.json"
        shutil.copy2(
            legacy_root / "workflow/compile-runtime-policy.json",
            legacy_runtime_policy,
        )
        legacy_policy = json.loads(legacy_runtime_policy.read_text(encoding="utf-8"))
        legacy_policy["engine"]["prefix_args"] = [
            str(isolated_root / Path(item).relative_to(PROJECT_ROOT))
            if Path(item).is_relative_to(PROJECT_ROOT)
            and not Path(item).is_relative_to(isolated_root)
            else item
            for item in legacy_policy["engine"]["prefix_args"]
        ]
        for identity in legacy_policy["engine"]["prefix_file_fingerprints"]:
            path = Path(identity["path"])
            if path.is_relative_to(PROJECT_ROOT) and not path.is_relative_to(
                isolated_root
            ):
                identity["path"] = str(isolated_root / path.relative_to(PROJECT_ROOT))
        inventory_path = Path(legacy_policy["package_inventory"]["path"])
        if inventory_path.is_relative_to(
            PROJECT_ROOT
        ) and not inventory_path.is_relative_to(isolated_root):
            legacy_policy["package_inventory"]["path"] = str(
                isolated_root / inventory_path.relative_to(PROJECT_ROOT)
            )
        legacy_policy["policy_sha256"] = canonical_sha(
            legacy_policy, "policy_sha256"
        )
        legacy_runtime_policy.write_text(
            json.dumps(
                legacy_policy,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        legacy_manifest = json.loads(legacy_manifest_path.read_text(encoding="utf-8"))
        for entry in legacy_manifest["entries"]:
            original = Path(entry["source_path"])
            entry["source_path"] = str(legacy_root / original.relative_to(source_root))
        legacy_manifest["runtime_policy"] = {
            "path": str(legacy_runtime_policy.resolve()),
            "sha256": hashlib.sha256(legacy_runtime_policy.read_bytes()).hexdigest(),
        }
        legacy_manifest["manifest_sha256"] = canonical_sha(
            legacy_manifest, "manifest_sha256"
        )
        legacy_manifest_path.write_text(
            json.dumps(
                legacy_manifest,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        retired_workflow = legacy_root / "待删除/kernel-workflow"
        retired_workflow.parent.mkdir(parents=True, exist_ok=True)
        (legacy_root / "workflow").rename(retired_workflow)

        workspace = legacy_root / "review/final"
        completed = subprocess.run(
            [
                sys.executable, "-X", "utf8", "-B",
                str(isolated_root / "scripts/video_workflow.py"),
                "delivery-quality-final-compile",
                "--input-track", "legacy",
                "--video-root", str(legacy_root),
                "--precompile-workspace-root", str(legacy_quality),
                "--compile-manifest", str(legacy_manifest_path),
                "--text-origin-plan", str(legacy_origin_plan_path),
                "--compiler-adapter", str(
                    isolated_root / "scripts/guarded_final_compile_adapter.py"
                ),
                "--runtime-policy", str(legacy_runtime_policy),
                "--workspace-root", str(workspace),
                "--compiled-at", "2026-08-25T13:30:00+08:00",
            ],
            cwd=isolated_root,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        envelope = json.loads(completed.stdout)
        self.assertEqual(40, completed.returncode, completed.stdout + completed.stderr)
        self.assertEqual(
            "final_editable_tex_source_set_dependency_evidence",
            envelope["data"]["first_failing_gate"],
        )
        self.assertEqual("final_tex_include_missing", envelope["data"]["error_code"])
        self.assertFalse((workspace / "final-editable-tex-source-set.json").exists())

    def test_public_final_compile_reports_adapter_missing_entrypoint_stably(self) -> None:
        from tests.video_workflow.test_rendered_text_reconciliation import (
            RenderedTextReconciliationCliTests,
        )

        fixture = RenderedTextReconciliationCliTests(
            "test_public_final_compile_invokes_adapter_and_produces_reconcilable_evidence"
        )
        root, paths = fixture.fixture(production_compile=True)
        completed, envelope, workspace = fixture.final_compile(
            root,
            paths,
            plan_updates={"fixture_entrypoint_missing_error": True},
            compiler_evidence_fixture=True,
        )

        self.assertEqual(40, completed.returncode, completed.stdout + completed.stderr)
        self.assertEqual(
            "final_editable_tex_source_set_entrypoint",
            envelope["data"]["first_failing_gate"],
        )
        self.assertEqual(
            "final_tex_entrypoint_missing", envelope["data"]["error_code"]
        )
        self.assertFalse((workspace / "final-editable-tex-source-set.json").exists())

    def test_public_final_compile_reports_adapter_ambiguous_entrypoint_stably(self) -> None:
        from tests.video_workflow.test_rendered_text_reconciliation import (
            RenderedTextReconciliationCliTests,
        )

        fixture = RenderedTextReconciliationCliTests(
            "test_public_final_compile_invokes_adapter_and_produces_reconcilable_evidence"
        )
        root, paths = fixture.fixture(production_compile=True)
        completed, envelope, workspace = fixture.final_compile(
            root,
            paths,
            plan_updates={"fixture_entrypoint_ambiguous_error": True},
            compiler_evidence_fixture=True,
        )

        self.assertEqual(40, completed.returncode, completed.stdout + completed.stderr)
        self.assertEqual(
            "final_editable_tex_source_set_entrypoint",
            envelope["data"]["first_failing_gate"],
        )
        self.assertEqual(
            "final_tex_entrypoint_ambiguous", envelope["data"]["error_code"]
        )
        self.assertFalse((workspace / "final-editable-tex-source-set.json").exists())

    def test_public_final_compile_rejects_incomplete_dependency_provenance(self) -> None:
        from tests.video_workflow.test_rendered_text_reconciliation import (
            RenderedTextReconciliationCliTests,
        )

        fixture = RenderedTextReconciliationCliTests(
            "test_public_final_compile_invokes_adapter_and_produces_reconcilable_evidence"
        )
        root, paths = fixture.fixture(production_compile=True)
        completed, envelope, workspace = fixture.final_compile(
            root, paths,
            plan_updates={"fixture_incomplete_provenance": True},
            compiler_evidence_fixture=True,
        )

        self.assertEqual(40, completed.returncode, completed.stdout + completed.stderr)
        self.assertEqual(
            "final_editable_tex_source_set_dependency_evidence",
            envelope["data"]["first_failing_gate"],
        )
        self.assertEqual(
            "final_tex_dependency_evidence_incomplete",
            envelope["data"]["error_code"],
        )
        self.assertFalse((workspace / "final-editable-tex-source-set.json").exists())

    def test_public_final_compile_keeps_non_tex_closure_failure_generic(self) -> None:
        # scenario_id: undeclared_non_tex_compile_input
        # target_invariant: Final Editable TeX Source Set classification applies
        # only to consumed project-local TeX sources.
        # mutation_seam: add one recorder-consumed undeclared .dat input.
        # rematerialized_nodes: recorder identity and compile provenance.
        # deliberately_stale_nodes: compile manifest (target contradiction).
        # expected_first_failing_gate: generic Final Compile dependency closure.
        # expected_error_code: absent (generic semantics).
        # scenario_class: single_contradiction.
        from tests.video_workflow.test_rendered_text_reconciliation import (
            RenderedTextReconciliationCliTests,
        )

        fixture = RenderedTextReconciliationCliTests(
            "test_public_final_compile_invokes_adapter_and_produces_reconcilable_evidence"
        )
        root, paths = fixture.fixture(production_compile=True)
        completed, envelope, _workspace = fixture.final_compile(
            root,
            paths,
            plan_updates={"fixture_extra_consumed_non_tex": True},
            compiler_evidence_fixture=True,
        )

        self.assertEqual(40, completed.returncode)
        self.assertEqual("compile_dependency_gap", envelope["classification"])
        self.assertNotIn("first_failing_gate", envelope["data"])
        self.assertNotIn("error_code", envelope["data"])
        self.assertFalse((_workspace / "final-editable-tex-source-set.json").exists())

    def test_public_final_compile_excludes_registered_runtime_tex_from_editable_set(self) -> None:
        from tests.video_workflow.test_rendered_text_reconciliation import (
            RenderedTextReconciliationCliTests,
            canonical_sha as fixture_sha,
            write_json,
        )

        fixture = RenderedTextReconciliationCliTests(
            "test_public_final_compile_invokes_adapter_and_produces_reconcilable_evidence"
        )
        root, paths = fixture.fixture(production_compile=True)
        runtime_tex = root / "registered-runtime/support.tex"
        runtime_tex.parent.mkdir(parents=True)
        runtime_tex.write_text("runtime support\n", encoding="utf-8")
        manifest = json.loads(paths["compile_manifest"].read_text(encoding="utf-8"))
        manifest["approved_runtime_inputs"].append(
            {
                "path": str(runtime_tex.resolve()),
                "sha256": hashlib.sha256(runtime_tex.read_bytes()).hexdigest(),
                "classification": "registered_runtime_dependency",
            }
        )
        manifest["manifest_sha256"] = fixture_sha(
            {
                key: value
                for key, value in manifest.items()
                if key != "manifest_sha256"
            }
        )
        write_json(paths["compile_manifest"], manifest)

        completed, envelope, _workspace = fixture.final_compile(
            root, paths, compiler_evidence_fixture=True
        )

        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        artifact = json.loads(
            Path(envelope["data"]["final_editable_tex_source_set_path"])
            .read_text(encoding="utf-8")
        )
        self.assertEqual(
            ["integrated_main_tex"],
            [member["logical_id"] for member in artifact["members"]],
        )
        self.assertNotIn(
            str(runtime_tex.resolve()),
            [
                source["path"]
                for source in artifact["compile_evidence"][
                    "consumed_project_tex_sources"
                ]
            ],
        )

    def test_public_final_compile_keeps_text_origin_provenance_failure_generic(self) -> None:
        # scenario_id: stale_text_origin_provenance
        # target_invariant: text-origin evidence keeps its existing Final Compile
        # classification outside the editable TeX source projection.
        # mutation_seam: replace only compile provenance text-origin-plan SHA.
        # rematerialized_nodes: compile provenance.
        # deliberately_stale_nodes: text-origin-plan binding (target contradiction).
        # expected_first_failing_gate: generic Final Compile provenance.
        # expected_error_code: absent (generic semantics).
        # scenario_class: single_contradiction.
        from tests.video_workflow.test_rendered_text_reconciliation import (
            RenderedTextReconciliationCliTests,
        )

        fixture = RenderedTextReconciliationCliTests(
            "test_public_final_compile_invokes_adapter_and_produces_reconcilable_evidence"
        )
        root, paths = fixture.fixture(production_compile=True)
        completed, envelope, workspace = fixture.final_compile(
            root,
            paths,
            plan_updates={"fixture_stale_provenance_text_origin": True},
            compiler_evidence_fixture=True,
        )

        self.assertEqual(40, completed.returncode)
        self.assertEqual("compile_dependency_gap", envelope["classification"])
        self.assertNotIn("first_failing_gate", envelope["data"])
        self.assertNotIn("error_code", envelope["data"])
        self.assertFalse((workspace / "final-editable-tex-source-set.json").exists())

    def test_public_final_compile_keeps_missing_runtime_input_generic(self) -> None:
        # scenario_id: approved_runtime_input_unobserved
        # target_invariant: runtime-input completeness remains owned by generic
        # Final Compile closure validation.
        # mutation_seam: omit only the approved runtime input from the recorder.
        # rematerialized_nodes: recorder identity and compile provenance.
        # deliberately_stale_nodes: recorder runtime closure (target contradiction).
        # expected_first_failing_gate: generic Final Compile dependency closure.
        # expected_error_code: absent (generic semantics).
        # scenario_class: single_contradiction.
        from tests.video_workflow.test_rendered_text_reconciliation import (
            RenderedTextReconciliationCliTests,
        )

        fixture = RenderedTextReconciliationCliTests(
            "test_public_final_compile_invokes_adapter_and_produces_reconcilable_evidence"
        )
        root, paths = fixture.fixture(production_compile=True)
        completed, envelope, workspace = fixture.final_compile(
            root,
            paths,
            plan_updates={"fixture_omit_runtime_recorder": True},
            compiler_evidence_fixture=True,
        )

        self.assertEqual(40, completed.returncode)
        self.assertEqual("compile_dependency_gap", envelope["classification"])
        self.assertNotIn("first_failing_gate", envelope["data"])
        self.assertNotIn("error_code", envelope["data"])
        self.assertFalse((workspace / "final-editable-tex-source-set.json").exists())

    def test_public_contract_accepts_monolithic_source_set(self) -> None:
        artifact = json.loads(
            (PROJECT_ROOT / "tests/video_workflow/fixtures/contracts/final-editable-tex-source-set.valid.json")
            .read_text(encoding="utf-8")
        )
        ContractRegistry(PROJECT_ROOT).validate("final-editable-tex-source-set", artifact)

    def test_public_final_compile_projects_split_tex_sources(self) -> None:
        from tests.video_workflow.test_rendered_text_reconciliation import (
            RenderedTextReconciliationCliTests,
        )
        fixture = RenderedTextReconciliationCliTests(
            "test_public_final_compile_invokes_adapter_and_produces_reconcilable_evidence"
        )
        root, paths = fixture.fixture(
            production_compile=True,
            additional_tex_sources=(
                ("integrated_section_01", 7, "chapter_01.tex", "Chapter one\n"),
            ),
        )
        completed, envelope, _workspace = fixture.final_compile(
            root, paths, compiler_evidence_fixture=True
        )

        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        source_set = json.loads(
            Path(envelope["data"]["final_editable_tex_source_set_path"])
            .read_text(encoding="utf-8")
        )
        self.assertEqual(
            [
                ("integrated_main_tex", "tex_entrypoint"),
                ("integrated_section_01", "included_tex_source"),
            ],
            [
                (member["logical_id"], member["role"])
                for member in source_set["members"]
            ],
        )

    def test_public_contract_projects_nested_relative_recorder_inputs(self) -> None:
        from tests.video_workflow.test_rendered_text_reconciliation import (
            RenderedTextReconciliationCliTests,
        )
        fixture = RenderedTextReconciliationCliTests(
            "test_public_final_compile_invokes_adapter_and_produces_reconcilable_evidence"
        )
        root, paths = fixture.fixture(
            production_compile=True,
            additional_tex_sources=(
                (
                    "integrated_nested_chapter",
                    3,
                    "chapters/chapter_01.tex",
                    "Nested chapter\n",
                ),
            ),
        )
        completed, envelope, _workspace = fixture.final_compile(
            root, paths, compiler_evidence_fixture=True
        )

        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        source_set = json.loads(
            Path(envelope["data"]["final_editable_tex_source_set_path"])
            .read_text(encoding="utf-8")
        )
        self.assertEqual(
            ["integrated_main_tex", "integrated_nested_chapter"],
            [member["logical_id"] for member in source_set["members"]],
        )
        self.assertEqual("chapter_01.tex", Path(source_set["members"][1]["path"]).name)

    def test_public_contract_binds_consumed_generated_snippet_generation(self) -> None:
        from tests.video_workflow.test_rendered_text_reconciliation import (
            RenderedTextReconciliationCliTests,
        )

        def compile_generation(snippet_generation: int) -> dict:
            fixture = RenderedTextReconciliationCliTests(
                "test_public_final_compile_invokes_adapter_and_produces_reconcilable_evidence"
            )
            root, paths = fixture.fixture(
                production_compile=True,
                additional_tex_sources=(
                    (
                        "generated_summary_tex",
                        snippet_generation,
                        "generated/summary.tex",
                        f"Generated version {snippet_generation}\n",
                    ),
                ),
            )
            completed, envelope, _workspace = fixture.final_compile(
                root, paths, compiler_evidence_fixture=True
            )
            self.assertEqual(
                0, completed.returncode, completed.stdout + completed.stderr
            )
            return json.loads(
                Path(envelope["data"]["final_editable_tex_source_set_path"])
                .read_text(encoding="utf-8")
            )

        first = compile_generation(1)
        second = compile_generation(2)

        self.assertEqual("included_tex_source", first["members"][1]["role"])
        self.assertEqual([1, 2], [first["members"][1]["generation"], second["members"][1]["generation"]])
        self.assertNotEqual(first["source_set_sha256"], second["source_set_sha256"])

    def test_public_validator_rejects_multiple_tex_entrypoints_first(self) -> None:
        # scenario_id: final_tex_multiple_entrypoints
        # target_invariant: one Final PDF has exactly one TeX Entry Point.
        # mutation_seam: change only a second member role to tex_entrypoint.
        # rematerialized_nodes: source_set_sha256.
        # deliberately_stale_nodes: none.
        # expected_first_failing_gate: final_editable_tex_source_set_entrypoint.
        # expected_error_code: final_tex_entrypoint_ambiguous.
        # scenario_class: single_contradiction.
        artifact = json.loads((PROJECT_ROOT / "tests/video_workflow/fixtures/contracts/final-editable-tex-source-set.valid.json").read_text(encoding="utf-8"))
        chapter = {
            "logical_id": "chapter_01",
            "generation": 1,
            "path": "/project/chapter_01.tex",
            "sha256": "a" * 64,
            "size": 1,
        }
        artifact["compile_evidence"]["consumed_project_tex_sources"].append(chapter)
        artifact["members"].append({**chapter, "role": "tex_entrypoint"})
        artifact["source_set_sha256"] = canonical_sha(artifact, "source_set_sha256")

        with self.assertRaises(CompileDependencyGap) as raised:
            ContractRegistry(PROJECT_ROOT).validate("final-editable-tex-source-set", artifact)

        self.assertEqual(
            "final_editable_tex_source_set_entrypoint",
            raised.exception.data["first_failing_gate"],
        )
        self.assertEqual(
            "final_tex_entrypoint_ambiguous",
            raised.exception.data["error_code"],
        )

    def test_public_validator_binds_entrypoint_role_to_compile_evidence(self) -> None:
        # scenario_id: final_tex_entrypoint_evidence_mismatch
        # target_invariant: the sole tex_entrypoint member is the entrypoint
        #   identified by current compile evidence.
        # mutation_seam: swap only the two valid member roles.
        # rematerialized_nodes: source_set_sha256.
        # deliberately_stale_nodes: none.
        # expected_first_gate: final_editable_tex_source_set_entrypoint_binding.
        # expected_error_code: final_tex_entrypoint_evidence_mismatch.
        # scenario_class: single_contradiction.
        artifact = json.loads(
            (
                PROJECT_ROOT
                / "tests/video_workflow/fixtures/contracts/final-editable-tex-source-set.valid.json"
            ).read_text(encoding="utf-8")
        )
        chapter = {
            "logical_id": "chapter_01",
            "generation": 1,
            "path": "/project/chapter_01.tex",
            "sha256": "a" * 64,
            "size": 1,
        }
        artifact["compile_evidence"]["tex_entrypoint_logical_id"] = (
            "integrated_main_tex"
        )
        artifact["compile_evidence"]["consumed_project_tex_sources"].append(chapter)
        artifact["members"][0]["role"] = "included_tex_source"
        artifact["members"].append({**chapter, "role": "tex_entrypoint"})
        artifact["source_set_sha256"] = canonical_sha(
            artifact, "source_set_sha256"
        )

        with self.assertRaises(CompileDependencyGap) as raised:
            ContractRegistry(PROJECT_ROOT).validate(
                "final-editable-tex-source-set", artifact
            )

        self.assertEqual(
            "final_editable_tex_source_set_entrypoint_binding",
            raised.exception.data["first_failing_gate"],
        )
        self.assertEqual(
            "final_tex_entrypoint_evidence_mismatch",
            raised.exception.data["error_code"],
        )

    def test_public_validator_rejects_stale_source_set_identity(self) -> None:
        # scenario_id: final_tex_source_set_identity_stale
        # target_invariant: source_set_sha256 binds the complete projection.
        # mutation_seam: change only source_set_sha256.
        # rematerialized_nodes: none.
        # deliberately_stale_nodes: source_set_sha256 (target contradiction).
        # expected_first_failing_gate: final_editable_tex_source_set_identity.
        # expected_error_code: final_tex_source_set_identity_stale.
        # scenario_class: single_contradiction.
        artifact = json.loads(
            (
                PROJECT_ROOT
                / "tests/video_workflow/fixtures/contracts/final-editable-tex-source-set.invalid.json"
            ).read_text(encoding="utf-8")
        )

        with self.assertRaises(CompileDependencyGap) as raised:
            ContractRegistry(PROJECT_ROOT).validate("final-editable-tex-source-set", artifact)

        self.assertEqual(
            "final_editable_tex_source_set_identity",
            raised.exception.data["first_failing_gate"],
        )
        self.assertEqual(
            "final_tex_source_set_identity_stale",
            raised.exception.data["error_code"],
        )

    def test_public_validator_rejects_member_absent_from_dependency_evidence(self) -> None:
        # scenario_id: final_tex_member_absent_from_dependency_evidence
        # target_invariant: every projected TeX member has current dependency evidence.
        # mutation_seam: add one valid included member without dependency evidence.
        # rematerialized_nodes: source_set_sha256.
        # deliberately_stale_nodes: dependency closure membership (target contradiction).
        # expected_first_failing_gate: final_editable_tex_source_set_membership.
        # expected_error_code: final_tex_dependency_evidence_mismatch.
        # scenario_class: single_contradiction.
        artifact = json.loads((PROJECT_ROOT / "tests/video_workflow/fixtures/contracts/final-editable-tex-source-set.valid.json").read_text(encoding="utf-8"))
        artifact["members"].append(
            {
                "logical_id": "unproven_chapter_tex",
                "generation": 1,
                "path": "/project/unproven.tex",
                "sha256": "8" * 64,
                "size": 1,
                "role": "included_tex_source",
            }
        )
        artifact["source_set_sha256"] = canonical_sha(artifact, "source_set_sha256")

        with self.assertRaises(CompileDependencyGap) as raised:
            ContractRegistry(PROJECT_ROOT).validate("final-editable-tex-source-set", artifact)

        self.assertEqual(
            "final_editable_tex_source_set_membership",
            raised.exception.data["first_failing_gate"],
        )
        self.assertEqual(
            "final_tex_dependency_evidence_mismatch",
            raised.exception.data["error_code"],
        )

    def test_public_validator_rejects_ambiguous_final_pdf_binding(self) -> None:
        # scenario_id: final_tex_pdf_binding_ambiguous
        # target_invariant: compile evidence and the projection identify the same
        # one Final PDF.
        # mutation_seam: replace only compile-evidence PDF identity.
        # rematerialized_nodes: source_set_sha256.
        # deliberately_stale_nodes: none.
        # expected_first_failing_gate: final_editable_tex_source_set_pdf_binding.
        # expected_error_code: final_tex_pdf_binding_ambiguous.
        # scenario_class: single_contradiction.
        artifact = json.loads(
            (
                PROJECT_ROOT
                / "tests/video_workflow/fixtures/contracts/final-editable-tex-source-set.valid.json"
            ).read_text(encoding="utf-8")
        )
        artifact["compile_evidence"]["final_pdf"] = {
            "path": "another-final.pdf",
            "sha256": "7" * 64,
            "size": 2,
        }
        artifact["source_set_sha256"] = canonical_sha(artifact, "source_set_sha256")

        with self.assertRaises(CompileDependencyGap) as raised:
            ContractRegistry(PROJECT_ROOT).validate(
                "final-editable-tex-source-set", artifact
            )

        self.assertEqual(
            "final_editable_tex_source_set_pdf_binding",
            raised.exception.data["first_failing_gate"],
        )
        self.assertEqual(
            "final_tex_pdf_binding_ambiguous",
            raised.exception.data["error_code"],
        )

    def test_public_final_compile_excludes_unused_tex_and_non_tex_inputs(self) -> None:
        from tests.video_workflow.test_rendered_text_reconciliation import (
            RenderedTextReconciliationCliTests,
        )

        fixture = RenderedTextReconciliationCliTests(
            "test_public_final_compile_invokes_adapter_and_produces_reconcilable_evidence"
        )
        root, paths = fixture.fixture(production_compile=True)
        (root / "scratch.tex").write_text("Abandoned draft\n", encoding="utf-8")
        completed, envelope, _workspace = fixture.final_compile(
            root, paths, compiler_evidence_fixture=True
        )

        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        source_set = json.loads(
            Path(envelope["data"]["final_editable_tex_source_set_path"]).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            ["integrated_main_tex"],
            [item["logical_id"] for item in source_set["members"]],
        )
        report = json.loads(
            Path(envelope["data"]["final_compile_report_path"]).read_text(
                encoding="utf-8"
            )
        )
        self.assertTrue(report["dependency_closure"]["runtime_inputs"])

    def test_public_final_compile_excludes_declared_but_unconsumed_tex(self) -> None:
        # scenario_id: declared_unconsumed_manifest_tex
        # target_invariant: a manifest-declared project TeX that current compile
        # dependency evidence shows was not consumed stays out of the editable
        # source set instead of being misclassified as a final source.
        # mutation_seam: declare one extra chapter in the manifest while the
        # compiler records only the entrypoint as consumed.
        # rematerialized_nodes: manifest identity and generation set.
        # deliberately_stale_nodes: none.
        # expected_outcome: final compile succeeds and projects the entrypoint only.
        # scenario_class: single_contradiction.
        from tests.video_workflow.test_rendered_text_reconciliation import (
            RenderedTextReconciliationCliTests,
        )

        fixture = RenderedTextReconciliationCliTests(
            "test_public_final_compile_invokes_adapter_and_produces_reconcilable_evidence"
        )
        root, paths = fixture.fixture(
            production_compile=True,
            additional_tex_sources=(
                ("unused_declared_chapter", 1, "unused/chapter.tex", "Unused\n"),
            ),
        )
        completed, envelope, _workspace = fixture.final_compile(
            root,
            paths,
            plan_updates={"fixture_unconsumed_declared_tex": True},
            compiler_evidence_fixture=True,
        )

        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        source_set = json.loads(
            Path(envelope["data"]["final_editable_tex_source_set_path"]).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            ["integrated_main_tex"],
            [member["logical_id"] for member in source_set["members"]],
        )

    def test_public_final_compile_keeps_same_output_directory_pdf_closures_independent(self) -> None:
        from tests.video_workflow.test_rendered_text_reconciliation import (
            RenderedTextReconciliationCliTests,
        )

        shared_video_output = (
            PROJECT_ROOT
            / "待删除/m"
            / uuid.uuid4().hex[:4]
        )
        shared_compile_output = shared_video_output / "finals"

        def compile_pdf(name: str) -> tuple[dict, Path, Path]:
            fixture = RenderedTextReconciliationCliTests(
                "test_public_final_compile_invokes_adapter_and_produces_reconcilable_evidence"
            )
            logical_id = f"{name}_chapter"
            root, paths = fixture.fixture(
                production_compile=True,
                additional_tex_sources=((logical_id, 1, f"chapters/{name}.tex", f"{name}\n"),),
                authority_root=shared_video_output / name[0],
            )
            completed, envelope, workspace = fixture.final_compile(
                root,
                paths,
                plan_updates={"fixture_pdf_salt": name},
                compiler_evidence_fixture=True,
                final_pdf_name=f"{name}.pdf",
                final_output_directory=shared_compile_output,
            )
            self.assertEqual(
                0, completed.returncode, completed.stdout + completed.stderr
            )
            source_set_path = Path(
                envelope["data"]["final_editable_tex_source_set_path"]
            )
            final_pdf_path = Path(envelope["data"]["final_pdf_path"])
            self.assertEqual(shared_compile_output, source_set_path.parent)
            self.assertEqual(shared_compile_output, final_pdf_path.parent)
            return (
                json.loads(source_set_path.read_text(encoding="utf-8")),
                final_pdf_path,
                source_set_path,
            )

        alpha_set, alpha_pdf, alpha_evidence = compile_pdf("alpha")
        beta_set, beta_pdf, beta_evidence = compile_pdf("beta")

        self.assertEqual(alpha_pdf.parent, beta_pdf.parent)
        self.assertNotEqual(alpha_pdf, beta_pdf)
        self.assertNotEqual(alpha_evidence, beta_evidence)
        self.assertNotEqual(
            alpha_set["final_pdf"]["sha256"],
            beta_set["final_pdf"]["sha256"],
        )
        self.assertEqual(
            {"integrated_main_tex", "alpha_chapter"},
            {item["logical_id"] for item in alpha_set["members"]},
        )
        self.assertEqual(
            {"integrated_main_tex", "beta_chapter"},
            {item["logical_id"] for item in beta_set["members"]},
        )
        self.assertNotEqual(alpha_set["source_set_sha256"], beta_set["source_set_sha256"])

    def test_public_kernel_and_run_record_free_legacy_artifacts_share_semantics(self) -> None:
        from tests.video_workflow._issue43_git_authority import (
            build_current_global_gate_authority,
        )
        from video2pdf_workflow_kernel.global_gate import GlobalGatePublisher

        isolated_root = self._isolated_authority_clone("r")

        kernel_handoff_path = isolated_root / "待删除/kernel-handoff.json"
        kernel_script = """
import json
from pathlib import Path
import uuid
from tests.video_workflow.test_rendered_text_reconciliation import RenderedTextReconciliationCliTests

project_root = Path.cwd()
fixture = RenderedTextReconciliationCliTests(
    "test_public_final_compile_invokes_adapter_and_produces_reconcilable_evidence"
)
kernel_root, kernel_paths = fixture.fixture(
    production_compile=True,
    additional_tex_sources=(("kernel_chapter", 1, "chapters/kernel.tex", "Kernel chapter\\n"),),
    main_tex_content="\\\\input{chapters/kernel.tex}\\n",
    authority_root=project_root / "待删除/i58k" / uuid.uuid4().hex[:6],
)
completed, envelope, _workspace = fixture.final_compile(kernel_root, kernel_paths)
handoff = project_root / "待删除/kernel-handoff.json"
handoff.parent.mkdir(parents=True, exist_ok=True)
handoff.write_text(
    json.dumps(
        {
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "kernel_root": str(kernel_root),
            "envelope": envelope,
        },
        ensure_ascii=False,
    ),
    encoding="utf-8",
)
"""
        kernel_process = subprocess.run(
            [sys.executable, "-X", "utf8", "-B", "-c", kernel_script],
            cwd=isolated_root,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(
            0, kernel_process.returncode, kernel_process.stdout + kernel_process.stderr
        )
        kernel_handoff = json.loads(kernel_handoff_path.read_text(encoding="utf-8"))
        self.assertEqual(
            0,
            kernel_handoff["returncode"],
            kernel_handoff["stdout"] + kernel_handoff["stderr"],
        )
        kernel_root = Path(kernel_handoff["kernel_root"])
        kernel_envelope = kernel_handoff["envelope"]

        control_store_root = isolated_root / "workspace"
        control_store_root.mkdir()
        authority_repository, exit_evidence = build_current_global_gate_authority(
            control_store_root,
            source_git_repository=isolated_root,
            authority_overlay_root=isolated_root,
        )
        GlobalGatePublisher(project_root=authority_repository).activate(
            control_store_root=control_store_root,
            exit_evidence=exit_evidence,
            activated_at="2026-08-25T13:30:00+08:00",
        )

        legacy_root = control_store_root / "v" / uuid.uuid4().hex[:6]
        shutil.copytree(kernel_root, legacy_root)
        legacy_quality = legacy_root / "precompile"
        legacy_manifest_path = legacy_root / "compile-manifest.json"
        legacy_origin_plan_path = legacy_root / "text-origin-plan.json"
        legacy_runtime_policy = legacy_quality / "legacy-runtime-policy.json"
        shutil.copy2(
            legacy_root / "workflow/compile-runtime-policy.json",
            legacy_runtime_policy,
        )
        legacy_policy = json.loads(legacy_runtime_policy.read_text(encoding="utf-8"))
        legacy_policy["engine"]["prefix_args"] = [
            str(isolated_root / Path(item).relative_to(PROJECT_ROOT))
            if Path(item).is_relative_to(PROJECT_ROOT)
            and not Path(item).is_relative_to(isolated_root)
            else item
            for item in legacy_policy["engine"]["prefix_args"]
        ]
        for identity in legacy_policy["engine"]["prefix_file_fingerprints"]:
            path = Path(identity["path"])
            if path.is_relative_to(PROJECT_ROOT) and not path.is_relative_to(
                isolated_root
            ):
                identity["path"] = str(isolated_root / path.relative_to(PROJECT_ROOT))
        inventory_path = Path(legacy_policy["package_inventory"]["path"])
        if inventory_path.is_relative_to(
            PROJECT_ROOT
        ) and not inventory_path.is_relative_to(isolated_root):
            legacy_policy["package_inventory"]["path"] = str(
                isolated_root / inventory_path.relative_to(PROJECT_ROOT)
            )
        legacy_policy["policy_sha256"] = canonical_sha(
            legacy_policy, "policy_sha256"
        )
        legacy_runtime_policy.write_text(
            json.dumps(
                legacy_policy,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        legacy_manifest = json.loads(legacy_manifest_path.read_text(encoding="utf-8"))
        for entry in legacy_manifest["entries"]:
            original = Path(entry["source_path"])
            entry["source_path"] = str(legacy_root / original.relative_to(kernel_root))
        legacy_manifest["runtime_policy"] = {
            "path": str(legacy_runtime_policy.resolve()),
            "sha256": hashlib.sha256(legacy_runtime_policy.read_bytes()).hexdigest(),
        }
        legacy_manifest["manifest_sha256"] = canonical_sha(
            legacy_manifest, "manifest_sha256"
        )
        legacy_manifest_path.write_text(
            json.dumps(
                legacy_manifest,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        retired_workflow = legacy_root / "待删除/kernel-workflow"
        retired_workflow.parent.mkdir(parents=True, exist_ok=True)
        (legacy_root / "workflow").rename(retired_workflow)
        self.assertFalse((legacy_root / "workflow/run.json").exists())

        legacy_workspace = legacy_root / "review/final"
        legacy_completed = subprocess.run(
            [
            sys.executable, "-X", "utf8", "-B",
            str(isolated_root / "scripts/video_workflow.py"),
            "delivery-quality-final-compile",
            "--input-track", "legacy",
            "--video-root", str(legacy_root),
            "--precompile-workspace-root", str(legacy_quality),
            "--compile-manifest", str(legacy_manifest_path),
            "--text-origin-plan", str(legacy_origin_plan_path),
            "--compiler-adapter", str(isolated_root / "scripts/guarded_final_compile_adapter.py"),
            "--runtime-policy", str(legacy_runtime_policy),
            "--workspace-root", str(legacy_workspace),
            "--compiled-at", "2026-08-25T13:30:00+08:00",
            ],
            cwd=isolated_root,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        legacy_envelope = json.loads(legacy_completed.stdout)
        self.assertEqual(
            0, legacy_completed.returncode, legacy_completed.stdout + legacy_completed.stderr
        )

        artifacts = [
            json.loads(
                Path(envelope["data"]["final_editable_tex_source_set_path"])
                .read_text(encoding="utf-8")
            )
            for envelope in (kernel_envelope, legacy_envelope)
        ]
        for artifact in artifacts:
            ContractRegistry(PROJECT_ROOT).validate(
                "final-editable-tex-source-set", artifact
            )
            self.assertEqual(artifact["final_pdf"], artifact["compile_evidence"]["final_pdf"])
            self.assertEqual(
                ["tex_entrypoint", "included_tex_source"],
                [member["role"] for member in artifact["members"]],
            )
            self.assertEqual(
                artifact["compile_evidence"]["consumed_project_tex_sources"],
                [
                    {key: value for key, value in member.items() if key != "role"}
                    for member in artifact["members"]
                ],
            )


if __name__ == "__main__":
    unittest.main()
