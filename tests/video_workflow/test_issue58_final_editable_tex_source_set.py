from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest import mock
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
from video2pdf_workflow_kernel.guarded_compile import (
    _MIKTEX_DURABLE_DIRECTORIES,
    _MIKTEX_ENGINE,
    _MIKTEX_RUNTIME_ROOTS,
)
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

    def _legacy_compile_environment(
        self,
        tag: str,
        source_root: Path,
        source_paths: dict[str, Path],
        *,
        plan: dict | None = None,
        real_miktex_runtime_files: list[Path] | None = None,
    ) -> tuple[Path, dict[str, Path]]:
        """Build the plan, isolated authority clone, and run-record-free Legacy
        root; returns (isolated_root, legacy_paths).

        ``plan`` replaces the origins-derived text-origin plan (used when the
        caller derives it from the real compiler output). When
        ``real_miktex_runtime_files`` is given, the precompile runtime policy
        is replaced by the registered MiKTeX policy bound to the exact
        recorder-observed runtime inputs, and the manifest approves them.
        """
        from tests.video_workflow._issue43_git_authority import (
            build_current_global_gate_authority,
        )
        from tests.video_workflow.test_rendered_text_reconciliation import (
            SYSTEM_FONT,
        )
        from video2pdf_workflow_kernel.global_gate import GlobalGatePublisher
        from video2pdf_workflow_kernel.guarded_compile import (
            runtime_policy_for_miktex,
        )

        origins = json.loads(source_paths["origins"].read_text(encoding="utf-8"))
        rendered = json.loads(source_paths["rendered"].read_text(encoding="utf-8"))
        seal = json.loads(
            (
                source_paths["precompile_workspace"]
                / "precompile-text-seal.json"
            ).read_text(encoding="utf-8")
        )
        if plan is None:
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

        isolated_root = self._isolated_authority_clone(tag)
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
        if real_miktex_runtime_files is not None:
            runtime_records = [
                {
                    "path": str(path.resolve()),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
                for path in sorted({path.resolve() for path in real_miktex_runtime_files}, key=str)
            ]
            inventory_path = legacy_root / "precompile/miktex-package-inventory.json"
            inventory_path.write_text(
                json.dumps(
                    {"schema_version": 1, "files": runtime_records},
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            legacy_runtime_policy.write_text(
                json.dumps(
                    runtime_policy_for_miktex(
                        package_inventory=inventory_path,
                        system_fonts=[SYSTEM_FONT],
                    ),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
            legacy_manifest["approved_runtime_inputs"] = [
                {**record, "classification": "registered_runtime_dependency"}
                for record in runtime_records
            ]
            legacy_manifest["runtime_policy"] = {
                "path": str(legacy_runtime_policy.resolve()),
                "sha256": hashlib.sha256(
                    legacy_runtime_policy.read_bytes()
                ).hexdigest(),
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
        return isolated_root, {
            "legacy_root": legacy_root,
            "legacy_quality": legacy_quality,
            "legacy_manifest_path": legacy_manifest_path,
            "legacy_origin_plan_path": legacy_origin_plan_path,
            "legacy_runtime_policy": legacy_runtime_policy,
            "workspace": legacy_root / "review/final",
        }

    def _run_legacy_final_compile(
        self,
        isolated_root: Path,
        legacy: dict[str, Path],
        *extra_arguments: str,
    ) -> tuple[subprocess.CompletedProcess[str], dict]:
        completed = subprocess.run(
            [
                sys.executable, "-X", "utf8", "-B",
                str(isolated_root / "scripts/video_workflow.py"),
                "delivery-quality-final-compile",
                "--input-track", "legacy",
                "--video-root", str(legacy["legacy_root"]),
                "--precompile-workspace-root", str(legacy["legacy_quality"]),
                "--compile-manifest", str(legacy["legacy_manifest_path"]),
                "--text-origin-plan", str(legacy["legacy_origin_plan_path"]),
                "--compiler-adapter", str(
                    isolated_root / "scripts/guarded_final_compile_adapter.py"
                ),
                "--runtime-policy", str(legacy["legacy_runtime_policy"]),
                "--workspace-root", str(legacy["workspace"]),
                "--compiled-at", "2026-08-25T13:30:00+08:00",
                *extra_arguments,
            ],
            cwd=isolated_root,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        return completed, json.loads(completed.stdout)

    def _probe_real_miktex(
        self,
        sources: dict[str, str],
    ) -> tuple[list[Path], Path]:
        """Compile project-local sources with the registered MiKTeX engine and
        return (recorder-observed runtime input files, probe root)."""
        probe = PROJECT_ROOT / "待删除/i58real" / uuid.uuid4().hex[:6]
        probe.mkdir(parents=True)
        (probe / "engine-temp").mkdir()
        (probe / "engine-profile").mkdir()
        for relative, content in sources.items():
            path = probe / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        profile = _MIKTEX_DURABLE_DIRECTORIES["MIKTEX_USERDATA"]
        system_root = os.environ.get("SYSTEMROOT", "C:/Windows")
        environment = {
            "PYTHONUTF8": "1",
            "MIKTEX_ENABLE_INSTALLER": "0",
            "TEMP": str(probe / "engine-temp"),
            "TMP": str(probe / "engine-temp"),
            "USERNAME": "video2pdf",
            "USERDOMAIN": "LOCAL",
            **_MIKTEX_DURABLE_DIRECTORIES,
            "MIKTEX_COMMONINSTALL": str(_MIKTEX_RUNTIME_ROOTS[0]),
            "MIKTEX_USERLOGDIRECTORY": str(probe / "engine-profile"),
            "USERPROFILE": str(profile),
            "HOME": str(profile),
            "HOMEDRIVE": Path(system_root).drive,
            "HOMEPATH": str(profile)[len(profile.drive):],
            "SYSTEMDRIVE": Path(system_root).drive,
            "SYSTEMROOT": system_root,
            "WINDIR": os.environ.get("WINDIR", "C:/Windows"),
        }
        completed = subprocess.run(
            [
                str(_MIKTEX_ENGINE),
                "--miktex-disable-maintenance",
                "--miktex-disable-diagnose",
                "--disable-installer",
                "-no-shell-escape",
                "-recorder",
                "-interaction=nonstopmode",
                "main.tex",
            ],
            cwd=probe,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=180,
            check=False,
        )
        self.assertEqual(
            0,
            completed.returncode,
            completed.stdout.decode("utf-8", "replace")
            + completed.stderr.decode("utf-8", "replace"),
        )
        runtime: list[Path] = []
        for line in (probe / "main.fls").read_text(
            encoding="utf-8", errors="strict"
        ).splitlines():
            if not line.startswith("INPUT "):
                continue
            observed = Path(line[6:])
            if not observed.is_absolute():
                observed = probe / observed
            observed = observed.resolve()
            try:
                relative = observed.relative_to(probe.resolve())
            except ValueError:
                runtime.append(observed)
                continue
            if str(relative).replace("\\", "/") in sources:
                continue
            suffix = "".join(observed.suffixes[-2:]).casefold()
            if suffix not in {
                ".aux", ".toc", ".out", ".log", ".xdv", ".bcf", ".run.xml"
            }:
                suffix = observed.suffix.casefold()
            if suffix not in {
                ".aux", ".toc", ".out", ".log", ".xdv", ".bcf", ".run.xml"
            }:
                runtime.append(observed)
        return runtime, probe

    def _real_plan_objects(
        self,
        pdf: Path,
    ) -> tuple[int, list[dict], list[dict]]:
        """Derive the text-origin plan pieces from the real compiled PDF.

        The enumeration walk mirrors the production adapter's
        render_and_extract so the plan objects match byte for byte; the last
        span of the single-page fixture document is the generated page number.
        """
        import fitz

        objects: list[dict] = []
        page_count = 0
        sequence = 0
        with fitz.open(pdf) as document:
            page_count = document.page_count
            for page_index, page in enumerate(document):
                page_number = page_index + 1
                for block_index, block in enumerate(
                    page.get_text("dict").get("blocks", []), 1
                ):
                    for line_index, line in enumerate(block.get("lines", []), 1):
                        for span_index, span in enumerate(
                            line.get("spans", []), 1
                        ):
                            if not span.get("text"):
                                continue
                            sequence += 1
                            objects.append(
                                {
                                    "object_id": f"page-{page_number}-text-{sequence}",
                                    "page": page_number,
                                    "object_kind": "pdf_text_run",
                                    "bbox": list(span["bbox"]),
                                    "exact_utf8_text": span["text"],
                                    "extractor_id": "pymupdf-text-v1",
                                    "evidence_locator": (
                                        f"page:{page_number}/block:{block_index}"
                                        f"/line:{line_index}/span:{span_index}"
                                    ),
                                }
                            )
        page_number_id = objects[-1]["object_id"] if objects else ""
        origin_ids = [
            item["object_id"] for item in objects if item["object_id"] != page_number_id
        ]
        self.assertEqual(
            "Core claim",
            "".join(
                str(item["exact_utf8_text"])
                for item in objects
                if item["object_id"] in origin_ids
            ),
        )
        self.assertEqual("1", objects[-1]["exact_utf8_text"])
        edges = [
            {
                "edge_id": "origin.main",
                "disposition": "sealed_origin",
                "sealed_item_id": "main.paragraph.001",
                "sealed_text_utf8": "Core claim",
                "rendered_object_ids": origin_ids,
                "recipe": "layout_whitespace",
            },
            {
                "edge_id": "generated.page-number",
                "disposition": "generated",
                "rendered_object_ids": [page_number_id],
                "recipe": "declared_generated",
                "generator": {
                    **registered_generator_identity("page-number-v1"),
                    "inputs": {
                        "first_page_number": 1,
                        "page_count": page_count,
                    },
                },
            },
        ]
        return page_count, objects, edges

    def _real_miktex_plan(
        self,
        seal_sha256: str,
        probe: Path,
    ) -> dict:
        page_count, objects, edges = self._real_plan_objects(probe / "main.pdf")
        plan = {
            "schema_name": "text-origin-plan",
            "schema_version": "1.0.0",
            "precompile_text_seal_sha256": seal_sha256,
            "sealed_items": [
                {
                    "item_id": "main.paragraph.001",
                    "exact_utf8_text": "Core claim",
                }
            ],
            "page_count": page_count,
            "extractor_suite": [
                {"extractor_id": "pymupdf-text-v1", "extractor_sha256": "a" * 64}
            ],
            "rendered_objects": objects,
            "edges": edges,
        }
        return plan

    def _public_targets(self, pdf_name: str) -> tuple[str, ...]:
        stem = Path(pdf_name).stem
        return (
            pdf_name,
            f"{stem}.render-evidence-manifest.json",
            f"{stem}.final-artifact-seal.json",
            f"{stem}.text-origin-manifest.json",
            f"{stem}.final-editable-tex-source-set.json",
            f"{stem}.compiler-adapter-identity.json",
            f"{stem}.final-compile-report.json",
        )

    def _run_fault_publication(
        self,
        fixture,
        root: Path,
        paths: dict[str, Path],
        *,
        pdf_name: str,
        workspace_key: str,
        patch_target: str,
        side_effect,
        expect_rollback_files: bool = True,
    ) -> None:
        """Run one named publication with a fault injected at the given seam,
        assert every public target is removed, and retry the same name."""
        from video2pdf_workflow_kernel import final_compile as final_compile_module

        finals = root / "finals"
        with mock.patch.object(
            final_compile_module, patch_target, side_effect=side_effect
        ):
            completed, envelope, _workspace = fixture.final_compile(
                root,
                paths,
                compiler_evidence_fixture=True,
                workspace_root=root / f"guarded-final-compile-{workspace_key}",
                final_pdf_name=pdf_name,
                final_output_directory=finals,
            )
        self.assertEqual(40, completed.returncode, completed.stdout + completed.stderr)
        self.assertEqual("compile_dependency_gap", envelope["classification"])
        for relative in self._public_targets(pdf_name):
            self.assertFalse((finals / relative).exists())
        rollback = root / f"待删除/final-compile-publish-{Path(pdf_name).stem}"
        self.assertTrue(rollback.is_dir())
        if expect_rollback_files:
            self.assertTrue(any(rollback.rglob("*")))

        completed, envelope, _workspace = fixture.final_compile(
            root,
            paths,
            plan_updates={"fixture_pdf_salt": pdf_name},
            compiler_evidence_fixture=True,
            workspace_root=root / f"guarded-final-compile-{workspace_key}-retry",
            final_pdf_name=pdf_name,
            final_output_directory=finals,
        )
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        self.assertTrue((finals / pdf_name).is_file())
        self.assertTrue(
            (finals / f"{Path(pdf_name).stem}.final-compile-report.json").is_file()
        )

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
        from tests.video_workflow.test_rendered_text_reconciliation import (
            RenderedTextReconciliationCliTests,
        )

        fixture = RenderedTextReconciliationCliTests(
            "test_public_final_compile_invokes_adapter_and_produces_reconcilable_evidence"
        )
        source_root, source_paths = fixture.fixture(
            production_compile=True,
            main_tex_content="\\input{chapters/missing}\n",
            authority_root=PROJECT_ROOT / "待删除/i58m-source" / uuid.uuid4().hex[:6],
        )
        isolated_root, legacy = self._legacy_compile_environment(
            "m", source_root, source_paths
        )
        completed, envelope = self._run_legacy_final_compile(isolated_root, legacy)
        self.assertEqual(40, completed.returncode, completed.stdout + completed.stderr)
        self.assertEqual(
            "final_editable_tex_source_set_dependency_evidence",
            envelope["data"]["first_failing_gate"],
        )
        self.assertEqual("final_tex_include_missing", envelope["data"]["error_code"])
        self.assertFalse(
            (legacy["workspace"] / "final-editable-tex-source-set.json").exists()
        )

    def test_public_final_compile_reports_missing_entrypoint_from_real_condition(self) -> None:
        # scenario_id: real_missing_explicit_entrypoint
        # target_invariant: an explicit entrypoint identity absent from the exact
        # sealed compile manifest fails closed at the public entrypoint gate.
        # mutation_seam: request one logical id that no manifest entry declares.
        # rematerialized_nodes: none (manifest and seals stay coherent).
        # deliberately_stale_nodes: none.
        # expected_first_failing_gate: final_editable_tex_source_set_entrypoint.
        # expected_error_code: final_tex_entrypoint_missing.
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
            tex_entrypoint_logical_id="absent_chapter_tex",
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

    def test_public_final_compile_accepts_zero_byte_consumed_tex(self) -> None:
        # target_invariant: a legitimate empty TeX fragment consumed by the
        # entrypoint remains a valid, delivered source-set member.
        # mutation_seam: declare and consume a zero-byte chapter file.
        # rematerialized_nodes: source identity, generation set, manifest, plan.
        # deliberately_stale_nodes: none.
        # expected_outcome: success with the empty member carrying size 0.
        from tests.video_workflow.test_rendered_text_reconciliation import (
            RenderedTextReconciliationCliTests,
        )

        fixture = RenderedTextReconciliationCliTests(
            "test_public_final_compile_invokes_adapter_and_produces_reconcilable_evidence"
        )
        root, paths = fixture.fixture(
            production_compile=True,
            main_tex_content="\\input{chapters/empty}\n",
            additional_tex_sources=(
                ("empty_chapter_tex", 7, "chapters/empty.tex", ""),
            ),
        )
        completed, envelope, _workspace = fixture.final_compile(
            root, paths, compiler_evidence_fixture=True
        )

        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        source_set = json.loads(
            Path(envelope["data"]["final_editable_tex_source_set_path"]).read_text(
                encoding="utf-8"
            )
        )
        empty = next(
            member
            for member in source_set["members"]
            if member["logical_id"] == "empty_chapter_tex"
        )
        self.assertEqual(0, empty["size"])

    def test_public_validator_treats_posix_case_sensitive_paths_as_distinct(self) -> None:
        # target_invariant: on POSIX-style project paths, path identity is
        # case-sensitive; Chapter.tex and chapter.tex are distinct members.
        main = {
            "logical_id": "integrated_main_tex", "generation": 1,
            "path": "/project/main.tex", "sha256": "3" * 64, "size": 12,
        }
        upper = {
            "logical_id": "chapter_upper", "generation": 1,
            "path": "/project/Chapter.tex", "sha256": "a" * 64, "size": 1,
        }
        lower = {
            "logical_id": "chapter_lower", "generation": 2,
            "path": "/project/chapter.tex", "sha256": "b" * 64, "size": 1,
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
                "consumed_project_tex_sources": [main, upper, lower],
            },
            "members": [
                {**main, "role": "tex_entrypoint"},
                {**upper, "role": "included_tex_source"},
                {**lower, "role": "included_tex_source"},
            ],
        }
        artifact["source_set_sha256"] = canonical_sha(artifact, "source_set_sha256")

        ContractRegistry(PROJECT_ROOT).validate("final-editable-tex-source-set", artifact)

    def test_public_final_compile_rejects_non_tex_entrypoint_stably(self) -> None:
        # target_invariant: a non-TeX artifact cannot be the TeX Entry Point.
        # mutation_seam: request an explicit entrypoint identity whose staging
        # path is a raster-style artifact.
        # rematerialized_nodes: none (manifest and seals stay coherent).
        # deliberately_stale_nodes: none.
        # expected_first_failing_gate: final_editable_tex_source_set_entrypoint.
        # expected_error_code: final_tex_entrypoint_not_tex.
        from tests.video_workflow.test_rendered_text_reconciliation import (
            RenderedTextReconciliationCliTests,
        )

        fixture = RenderedTextReconciliationCliTests(
            "test_public_final_compile_invokes_adapter_and_produces_reconcilable_evidence"
        )
        root, paths = fixture.fixture(
            production_compile=True,
            additional_tex_sources=(("cover_image", 1, "cover.png", "png\n"),),
        )
        completed, envelope, workspace = fixture.final_compile(
            root,
            paths,
            tex_entrypoint_logical_id="cover_image",
            compiler_evidence_fixture=True,
        )

        self.assertEqual(40, completed.returncode, completed.stdout + completed.stderr)
        self.assertEqual(
            "final_editable_tex_source_set_entrypoint",
            envelope["data"]["first_failing_gate"],
        )
        self.assertEqual(
            "final_tex_entrypoint_not_tex", envelope["data"]["error_code"]
        )
        self.assertFalse((workspace / "final-editable-tex-source-set.json").exists())

    def test_public_final_compile_reports_stale_tex_source_identity(self) -> None:
        # target_invariant: a declared final TeX source whose current bytes no
        # longer match its manifest identity fails with the stable member
        # source-identity code instead of a generic manifest error.
        # mutation_seam: mutate the entrypoint source file after the manifest
        # and sealed bindings were frozen.
        # rematerialized_nodes: none (manifest, seals, and plan stay frozen).
        # deliberately_stale_nodes: source file bytes (target contradiction).
        # expected_first_failing_gate: final_editable_tex_source_set_identity.
        # expected_error_code: final_tex_source_identity_stale.
        from tests.video_workflow.test_rendered_text_reconciliation import (
            RenderedTextReconciliationCliTests,
        )

        fixture = RenderedTextReconciliationCliTests(
            "test_public_final_compile_invokes_adapter_and_produces_reconcilable_evidence"
        )
        root, paths = fixture.fixture(production_compile=True)
        main_source = root / "integrated-main.tex"
        main_source.write_text(
            main_source.read_text(encoding="utf-8") + "\nchanged\n",
            encoding="utf-8",
        )
        completed, envelope, workspace = fixture.final_compile(
            root, paths, compiler_evidence_fixture=True
        )

        self.assertEqual(40, completed.returncode, completed.stdout + completed.stderr)
        self.assertEqual(
            "final_editable_tex_source_set_identity",
            envelope["data"]["first_failing_gate"],
        )
        self.assertEqual(
            "final_tex_source_identity_stale", envelope["data"]["error_code"]
        )
        self.assertFalse((workspace / "final-editable-tex-source-set.json").exists())

    def test_guarded_compile_reference_validation_is_directory_sensitive(self) -> None:
        # target_invariant: a directory-qualified \input reference cannot be
        # satisfied by an unrelated file with the same basename elsewhere.
        from video2pdf_workflow_kernel.guarded_compile import GuardedCompileProvider

        GuardedCompileProvider._validate_declared_references(
            "\\input{chapters/foo}\n", {"chapters/foo.tex"}, set()
        )
        with self.assertRaises(CompileDependencyGap):
            GuardedCompileProvider._validate_declared_references(
                "\\input{chapters/foo}\n", {"appendix/foo.tex"}, set()
            )
        GuardedCompileProvider._validate_declared_references(
            "\\input{foo}\n", {"chapters/foo.tex"}, set()
        )

    def test_public_named_pdf_publication_publishes_nothing_on_source_set_failure(self) -> None:
        # fault injection A: the PDF is produced but source-set validation fails;
        # the public named output must stay completely empty, and a retry with
        # the same name must succeed.
        from tests.video_workflow.test_rendered_text_reconciliation import (
            RenderedTextReconciliationCliTests,
        )

        fixture = RenderedTextReconciliationCliTests(
            "test_public_final_compile_invokes_adapter_and_produces_reconcilable_evidence"
        )
        root, paths = fixture.fixture(
            production_compile=True,
            authority_root=PROJECT_ROOT / "待删除/i58f" / uuid.uuid4().hex[:6],
        )
        finals = root / "finals"

        completed, envelope, _workspace = fixture.final_compile(
            root,
            paths,
            plan_updates={"fixture_extra_consumed_tex": True},
            compiler_evidence_fixture=True,
            workspace_root=root / "guarded-final-compile-fault-a",
            final_pdf_name="alpha.pdf",
            final_output_directory=finals,
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
        for relative in (
            "alpha.pdf",
            "alpha.final-editable-tex-source-set.json",
            "alpha.final-compile-report.json",
            "alpha.final-artifact-seal.json",
        ):
            self.assertFalse((finals / relative).exists())

        completed, envelope, _workspace = fixture.final_compile(
            root,
            paths,
            plan_updates={"fixture_pdf_salt": "alpha"},
            compiler_evidence_fixture=True,
            workspace_root=root / "guarded-final-compile-fault-a-retry",
            final_pdf_name="alpha.pdf",
            final_output_directory=finals,
        )
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        self.assertTrue((finals / "alpha.pdf").is_file())
        self.assertTrue(
            (finals / "alpha.final-editable-tex-source-set.json").is_file()
        )
        self.assertTrue((finals / "alpha.final-compile-report.json").is_file())

    def test_public_named_pdf_publication_publishes_nothing_before_report(self) -> None:
        # fault injection B: dependency provenance stays incomplete after the
        # compile evidence exists; nothing may reach the public directory and a
        # retry with the same name must succeed.
        from tests.video_workflow.test_rendered_text_reconciliation import (
            RenderedTextReconciliationCliTests,
        )

        fixture = RenderedTextReconciliationCliTests(
            "test_public_final_compile_invokes_adapter_and_produces_reconcilable_evidence"
        )
        root, paths = fixture.fixture(
            production_compile=True,
            authority_root=PROJECT_ROOT / "待删除/i58f" / uuid.uuid4().hex[:6],
        )
        finals = root / "finals"

        completed, envelope, _workspace = fixture.final_compile(
            root,
            paths,
            plan_updates={"fixture_incomplete_provenance": True},
            compiler_evidence_fixture=True,
            workspace_root=root / "guarded-final-compile-fault-b",
            final_pdf_name="beta.pdf",
            final_output_directory=finals,
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
        for relative in (
            "beta.pdf",
            "beta.final-editable-tex-source-set.json",
            "beta.final-compile-report.json",
            "beta.final-artifact-seal.json",
        ):
            self.assertFalse((finals / relative).exists())

        completed, envelope, _workspace = fixture.final_compile(
            root,
            paths,
            plan_updates={"fixture_pdf_salt": "beta"},
            compiler_evidence_fixture=True,
            workspace_root=root / "guarded-final-compile-fault-b-retry",
            final_pdf_name="beta.pdf",
            final_output_directory=finals,
        )
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        self.assertTrue((finals / "beta.pdf").is_file())
        self.assertTrue(
            (finals / "beta.final-editable-tex-source-set.json").is_file()
        )
        self.assertTrue((finals / "beta.final-compile-report.json").is_file())

    def test_public_final_compile_keeps_same_output_directory_pdf_closures_independent(self) -> None:
        from tests.video_workflow.test_rendered_text_reconciliation import (
            RenderedTextReconciliationCliTests,
        )

        fixture = RenderedTextReconciliationCliTests(
            "test_public_final_compile_invokes_adapter_and_produces_reconcilable_evidence"
        )
        root, paths = fixture.fixture(
            production_compile=True,
            additional_tex_sources=(
                ("alpha_chapter", 7, "chapters/alpha.tex", "Alpha\n"),
                ("beta_chapter", 8, "chapters/beta.tex", "Beta\n"),
            ),
            authority_root=PROJECT_ROOT / "待删除/m" / uuid.uuid4().hex[:4],
        )
        finals = root / "finals"

        def compile_pdf(name: str, recorded: list[str]) -> tuple[dict, Path, Path]:
            completed, envelope, _workspace = fixture.final_compile(
                root,
                paths,
                plan_updates={
                    "fixture_recorded_logical_ids": recorded,
                    "fixture_pdf_salt": name,
                },
                compiler_evidence_fixture=True,
                workspace_root=root / f"guarded-final-compile-{name}",
                final_pdf_name=f"{name}.pdf",
                final_output_directory=finals,
            )
            self.assertEqual(
                0, completed.returncode, completed.stdout + completed.stderr
            )
            source_set_path = Path(
                envelope["data"]["final_editable_tex_source_set_path"]
            )
            final_pdf_path = Path(envelope["data"]["final_pdf_path"])
            self.assertEqual(finals, source_set_path.parent)
            self.assertEqual(finals, final_pdf_path.parent)
            return (
                json.loads(source_set_path.read_text(encoding="utf-8")),
                final_pdf_path,
                source_set_path,
            )

        alpha_set, alpha_pdf, alpha_evidence = compile_pdf(
            "alpha", ["integrated_main_tex", "alpha_chapter"]
        )
        beta_set, beta_pdf, beta_evidence = compile_pdf(
            "beta", ["integrated_main_tex", "beta_chapter"]
        )

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

    def test_public_legacy_final_compile_rejects_named_output_outside_video_root(self) -> None:
        # target_invariant: Legacy named output stays inside the video root.
        # mutation_seam: request one named PDF outside the Legacy video root.
        # rematerialized_nodes: none.
        # deliberately_stale_nodes: none.
        # expected_command: fail closed before any directory or file is created.
        from tests.video_workflow.test_rendered_text_reconciliation import (
            RenderedTextReconciliationCliTests,
        )

        fixture = RenderedTextReconciliationCliTests(
            "test_public_final_compile_invokes_adapter_and_produces_reconcilable_evidence"
        )
        source_root, source_paths = fixture.fixture(
            production_compile=True,
            authority_root=PROJECT_ROOT / "待删除/i58e-source" / uuid.uuid4().hex[:6],
        )
        isolated_root, legacy = self._legacy_compile_environment(
            "e", source_root, source_paths
        )
        escaped = Path(legacy["legacy_root"]).parent / "other-video" / "finals"
        completed, envelope = self._run_legacy_final_compile(
            isolated_root,
            legacy,
            "--final-pdf-name", "escape.pdf",
            "--final-output-directory", str(escaped),
        )
        self.assertEqual(20, completed.returncode, completed.stdout + completed.stderr)
        self.assertEqual("contract_invalid", envelope["classification"])
        self.assertFalse(escaped.exists())

    @unittest.skipUnless(
        _MIKTEX_ENGINE.is_file(), "registered MiKTeX engine is unavailable"
    )
    def test_public_final_compile_projects_real_split_tex_recorder(self) -> None:
        # scenario_id: real_split_tex_closure
        # target_invariant: the real compiler recorder, not a synthetic manifest
        # listing, establishes a split-document source set.
        # mutation_seam: none (positive real compile).
        # expected_outcome: entrypoint plus the consumed root-level chapter.
        from tests.video_workflow.test_rendered_text_reconciliation import (
            RenderedTextReconciliationCliTests,
        )

        main_content = (
            "\\documentclass{article}\n\\begin{document}\n"
            "Core claim\n\\input{chapters/chapter_01}\n\\end{document}\n"
        )
        fixture = RenderedTextReconciliationCliTests(
            "test_public_final_compile_invokes_adapter_and_produces_reconcilable_evidence"
        )
        source_root, source_paths = fixture.fixture(
            production_compile=True,
            main_tex_content=main_content,
            additional_tex_sources=(
                (
                    "integrated_chapter_01",
                    7,
                    "chapters/chapter_01.tex",
                    "% Chapter one placeholder\n",
                ),
            ),
            authority_root=PROJECT_ROOT / "待删除/i58split-source" / uuid.uuid4().hex[:6],
        )
        sources = {
            "main.tex": main_content,
            "chapters/chapter_01.tex": "% Chapter one placeholder\n",
        }
        runtime_files, probe = self._probe_real_miktex(sources)
        seal = json.loads(
            (
                source_paths["precompile_workspace"] / "precompile-text-seal.json"
            ).read_text(encoding="utf-8")
        )
        plan = self._real_miktex_plan(seal["seal_sha256"], probe)
        isolated_root, legacy = self._legacy_compile_environment(
            "s",
            source_root,
            source_paths,
            plan=plan,
            real_miktex_runtime_files=runtime_files,
        )
        completed, envelope = self._run_legacy_final_compile(isolated_root, legacy)
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        artifact = json.loads(
            (legacy["workspace"] / "final-editable-tex-source-set.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            [
                ("integrated_main_tex", "tex_entrypoint"),
                ("integrated_chapter_01", "included_tex_source"),
            ],
            [(member["logical_id"], member["role"]) for member in artifact["members"]],
        )

    @unittest.skipUnless(
        _MIKTEX_ENGINE.is_file(), "registered MiKTeX engine is unavailable"
    )
    def test_public_final_compile_projects_real_nested_tex_recorder(self) -> None:
        # scenario_id: real_nested_tex_closure
        # target_invariant: a chapter that itself inputs a deeper project-local
        # TeX file keeps every transitively consumed member in the source set.
        # mutation_seam: none (positive real compile).
        # expected_outcome: entrypoint plus both nested included sources.
        from tests.video_workflow.test_rendered_text_reconciliation import (
            RenderedTextReconciliationCliTests,
        )

        main_content = (
            "\\documentclass{article}\n\\begin{document}\n"
            "Core claim\n\\input{chapters/chapter_01}\n\\end{document}\n"
        )
        fixture = RenderedTextReconciliationCliTests(
            "test_public_final_compile_invokes_adapter_and_produces_reconcilable_evidence"
        )
        source_root, source_paths = fixture.fixture(
            production_compile=True,
            main_tex_content=main_content,
            additional_tex_sources=(
                (
                    "integrated_chapter_01",
                    7,
                    "chapters/chapter_01.tex",
                    "\\input{chapters/deep}\n",
                ),
                (
                    "integrated_deep_tex",
                    6,
                    "chapters/deep.tex",
                    "% Deep paragraph placeholder\n",
                ),
            ),
            authority_root=PROJECT_ROOT / "待删除/i58nested-source" / uuid.uuid4().hex[:6],
        )
        sources = {
            "main.tex": main_content,
            "chapters/chapter_01.tex": "\\input{chapters/deep}\n",
            "chapters/deep.tex": "% Deep paragraph placeholder\n",
        }
        runtime_files, probe = self._probe_real_miktex(sources)
        seal = json.loads(
            (
                source_paths["precompile_workspace"] / "precompile-text-seal.json"
            ).read_text(encoding="utf-8")
        )
        plan = self._real_miktex_plan(seal["seal_sha256"], probe)
        isolated_root, legacy = self._legacy_compile_environment(
            "n",
            source_root,
            source_paths,
            plan=plan,
            real_miktex_runtime_files=runtime_files,
        )
        completed, envelope = self._run_legacy_final_compile(isolated_root, legacy)
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        artifact = json.loads(
            (legacy["workspace"] / "final-editable-tex-source-set.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            [
                ("integrated_main_tex", "tex_entrypoint"),
                ("integrated_chapter_01", "included_tex_source"),
                ("integrated_deep_tex", "included_tex_source"),
            ],
            [(member["logical_id"], member["role"]) for member in artifact["members"]],
        )

    @unittest.skipUnless(
        _MIKTEX_ENGINE.is_file(), "registered MiKTeX engine is unavailable"
    )
    def test_public_final_compile_projects_real_generated_tex_recorder(self) -> None:
        # scenario_id: real_generated_tex_closure
        # target_invariant: a consumed generated TeX snippet is delivered and
        # fingerprinted as an included source from the real recorder.
        # mutation_seam: none (positive real compile).
        # expected_outcome: entrypoint plus the consumed generated snippet.
        from tests.video_workflow.test_rendered_text_reconciliation import (
            RenderedTextReconciliationCliTests,
        )

        main_content = (
            "\\documentclass{article}\n\\begin{document}\n"
            "Core claim\n\\input{generated/summary}\n\\end{document}\n"
        )
        fixture = RenderedTextReconciliationCliTests(
            "test_public_final_compile_invokes_adapter_and_produces_reconcilable_evidence"
        )
        source_root, source_paths = fixture.fixture(
            production_compile=True,
            main_tex_content=main_content,
            additional_tex_sources=(
                (
                    "generated_summary_tex",
                    1,
                    "generated/summary.tex",
                    "% Generated summary placeholder\n",
                ),
            ),
            authority_root=PROJECT_ROOT / "待删除/i58gen-source" / uuid.uuid4().hex[:6],
        )
        sources = {
            "main.tex": main_content,
            "generated/summary.tex": "% Generated summary placeholder\n",
        }
        runtime_files, probe = self._probe_real_miktex(sources)
        seal = json.loads(
            (
                source_paths["precompile_workspace"] / "precompile-text-seal.json"
            ).read_text(encoding="utf-8")
        )
        plan = self._real_miktex_plan(seal["seal_sha256"], probe)
        isolated_root, legacy = self._legacy_compile_environment(
            "g",
            source_root,
            source_paths,
            plan=plan,
            real_miktex_runtime_files=runtime_files,
        )
        completed, envelope = self._run_legacy_final_compile(isolated_root, legacy)
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        artifact = json.loads(
            (legacy["workspace"] / "final-editable-tex-source-set.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            [
                ("integrated_main_tex", "tex_entrypoint"),
                ("generated_summary_tex", "included_tex_source"),
            ],
            [(member["logical_id"], member["role"]) for member in artifact["members"]],
        )


    def test_public_final_compile_ignores_missing_include_in_unconsumed_draft(self) -> None:
        # scenario_id: unconsumed_draft_missing_include
        # target_invariant: the entrypoint-rooted static preflight never fails
        # on an unconsumed staged draft; consumed-closure classification stays
        # with the real recorder evidence.
        # mutation_seam: declare one unused draft whose \input target is absent.
        # rematerialized_nodes: none (manifest and seals stay coherent).
        # expected_outcome: compile succeeds; the draft appears only because
        # the recorder-observed closure includes it.
        from tests.video_workflow.test_rendered_text_reconciliation import (
            RenderedTextReconciliationCliTests,
        )

        fixture = RenderedTextReconciliationCliTests(
            "test_public_final_compile_invokes_adapter_and_produces_reconcilable_evidence"
        )
        source_root, source_paths = fixture.fixture(
            production_compile=True,
            main_tex_content="\\input{chapters/good}\n",
            additional_tex_sources=(
                (
                    "integrated_good_chapter",
                    7,
                    "chapters/good.tex",
                    "Good chapter\n",
                ),
                (
                    "unused_draft_tex",
                    5,
                    "unused-draft.tex",
                    "\\input{abandoned/missing}\n",
                ),
            ),
            authority_root=PROJECT_ROOT / "待删除/i58d-source" / uuid.uuid4().hex[:6],
        )
        isolated_root, legacy = self._legacy_compile_environment(
            "d", source_root, source_paths
        )
        completed, envelope = self._run_legacy_final_compile(isolated_root, legacy)
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        artifact = json.loads(
            (legacy["workspace"] / "final-editable-tex-source-set.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            {"integrated_main_tex", "integrated_good_chapter", "unused_draft_tex"},
            {member["logical_id"] for member in artifact["members"]},
        )

    @unittest.skipUnless(
        _MIKTEX_ENGINE.is_file(), "registered MiKTeX engine is unavailable"
    )
    def test_public_real_xelatex_recorder_excludes_unconsumed_draft(self) -> None:
        # scenario_id: real_recorder_excludes_unconsumed_draft
        # target_invariant: the real XeLaTeX recorder, not a synthetic listing,
        #   establishes the consumed TeX closure; an unused draft with a
        #   missing include neither blocks the compile nor enters the set.
        # mutation_seam: none (positive real compile with an unused draft).
        # expected_outcome: entrypoint plus both transitive include members;
        #   unused-draft.tex stays absent from the recorder and the source set.
        from tests.video_workflow.test_rendered_text_reconciliation import (
            RenderedTextReconciliationCliTests,
        )

        main_content = (
            "\\documentclass{article}\n\\begin{document}\n"
            "Core claim\n\\input{chapters/used.tex}\n\\end{document}\n"
        )
        fixture = RenderedTextReconciliationCliTests(
            "test_public_final_compile_invokes_adapter_and_produces_reconcilable_evidence"
        )
        source_root, source_paths = fixture.fixture(
            production_compile=True,
            main_tex_content=main_content,
            additional_tex_sources=(
                (
                    "integrated_chapter_01",
                    7,
                    "chapters/used.tex",
                    "\\input{generated/summary}\n",
                ),
                (
                    "generated_summary_tex",
                    1,
                    "generated/summary.tex",
                    "% Generated summary placeholder\n",
                ),
                (
                    "unused_draft_tex",
                    5,
                    "unused-draft.tex",
                    "\\input{abandoned/missing}\n",
                ),
            ),
            authority_root=PROJECT_ROOT / "待删除/i58u-source" / uuid.uuid4().hex[:6],
        )
        sources = {
            "main.tex": main_content,
            "chapters/used.tex": "\\input{generated/summary}\n",
            "generated/summary.tex": "% Generated summary placeholder\n",
        }
        runtime_files, probe = self._probe_real_miktex(sources)
        seal = json.loads(
            (
                source_paths["precompile_workspace"] / "precompile-text-seal.json"
            ).read_text(encoding="utf-8")
        )
        plan = self._real_miktex_plan(seal["seal_sha256"], probe)
        isolated_root, legacy = self._legacy_compile_environment(
            "u",
            source_root,
            source_paths,
            plan=plan,
            real_miktex_runtime_files=runtime_files,
        )
        completed, envelope = self._run_legacy_final_compile(isolated_root, legacy)
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        artifact = json.loads(
            (legacy["workspace"] / "final-editable-tex-source-set.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            [
                ("integrated_main_tex", "tex_entrypoint"),
                ("integrated_chapter_01", "included_tex_source"),
                ("generated_summary_tex", "included_tex_source"),
            ],
            [(member["logical_id"], member["role"]) for member in artifact["members"]],
        )
        self.assertNotIn(
            "unused_draft_tex", [member["logical_id"] for member in artifact["members"]]
        )
        recorder = (
            legacy["workspace"] / "adapter-output/compile-recorder.fls"
        ).read_text(encoding="utf-8")
        self.assertIn("chapters\\used.tex", recorder)
        self.assertIn("generated\\summary.tex", recorder)
        self.assertNotIn("unused-draft.tex", recorder)

    def test_public_projector_excludes_unconsumed_draft_from_persisted_real_recorder(
        self,
    ) -> None:
        # scenario_id: persisted_real_recorder_closure
        # target_invariant: the projector consumes the persisted real XeLaTeX
        #   recorder evidence (captured on the registered MiKTeX engine) and
        #   excludes the manifest-declared unused draft from the source set.
        # mutation_seam: none (evidence replay over the captured recorder).
        from video2pdf_workflow_kernel.final_tex_source_set import (
            FinalEditableTexSourceSetProjector,
        )
        from video2pdf_workflow_kernel.utils import path_fold

        recorder_fixture = (
            PROJECT_ROOT
            / "tests/video_workflow/fixtures/guarded-compile/real-miktex-nested-recorder.fls"
        )
        recorder_lines = recorder_fixture.read_text(
            encoding="utf-8", errors="strict"
        ).splitlines()
        cwd = PROJECT_ROOT / "待删除/i58fls" / uuid.uuid4().hex[:6]
        cwd.mkdir(parents=True)
        source_contents = {
            "main.tex": (
                "\\documentclass{article}\n\\begin{document}\n"
                "Core claim\n\\input{chapters/used.tex}\n\\end{document}\n"
            ),
            "chapters/used.tex": "\\input{generated/summary}\n",
            "generated/summary.tex": "% Generated summary placeholder\n",
            "unused-draft.tex": "\\input{abandoned/missing}\n",
        }
        entries = []
        for staging_path, content in source_contents.items():
            source = cwd / "sources" / staging_path
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text(content, encoding="utf-8")
            entries.append(
                {
                    "logical_id": (
                        "integrated_main_tex"
                        if staging_path == "main.tex"
                        else (
                            "integrated_chapter_01"
                            if staging_path == "chapters/used.tex"
                            else (
                                "generated_summary_tex"
                                if staging_path == "generated/summary.tex"
                                else "unused_draft_tex"
                            )
                        )
                    ),
                    "generation": 1,
                    "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    "source_path": str(source),
                    "staging_path": staging_path,
                }
            )
        recorder_inputs: list[Path] = []
        registered_runtime: set[str] = set()
        for line in recorder_lines:
            if not line.startswith("INPUT "):
                continue
            observed = Path(line[6:])
            if not observed.is_absolute():
                observed = cwd / observed
            observed = observed.resolve()
            try:
                observed.relative_to(cwd.resolve())
                relative = str(observed.relative_to(cwd.resolve())).replace("\\", "/")
            except ValueError:
                registered_runtime.add(path_fold(str(observed)))
                continue
            if relative not in source_contents:
                registered_runtime.add(path_fold(str(observed)))
                continue
            recorder_inputs.append(observed)
        artifact = FinalEditableTexSourceSetProjector().project(
            compile_entries=entries,
            recorder_cwd=cwd,
            recorder_inputs=recorder_inputs,
            registered_runtime_input_paths=registered_runtime,
            project_root=cwd,
            entrypoint_staging_paths=["main.tex"],
            final_pdf={"path": "final.pdf", "sha256": "4" * 64, "size": 1},
            generation_set_sha256="e" * 64,
            compile_manifest_sha256="5" * 64,
            recorder_path="adapter-output/compile-recorder.fls",
            recorder_sha256="6" * 64,
        )
        self.assertEqual(
            [
                "integrated_main_tex",
                "integrated_chapter_01",
                "generated_summary_tex",
            ],
            [member["logical_id"] for member in artifact["members"]],
        )
        self.assertNotIn(
            "unused_draft_tex", [member["logical_id"] for member in artifact["members"]]
        )

    def test_public_final_compile_path_identity_is_platform_aware(self) -> None:
        # target_invariant: filesystem identity folds only Windows-style
        # path text; POSIX and relative TeX paths stay case-sensitive.
        from video2pdf_workflow_kernel.final_compile import _identity_key
        from video2pdf_workflow_kernel.utils import path_fold

        self.assertEqual(
            _identity_key(Path(r"C:\Work\Chapter.tex")),
            _identity_key(Path(r"c:\work\chapter.tex")),
        )
        self.assertEqual(
            _identity_key(Path(r"\\server\share\Main.tex")),
            _identity_key(Path(r"\\SERVER\SHARE\main.tex")),
        )
        self.assertEqual(
            path_fold("C:/Work/Chapter.tex"), path_fold("c:/work/chapter.tex")
        )
        self.assertNotEqual(
            path_fold("/project/Chapter.tex"), path_fold("/project/chapter.tex")
        )
        self.assertNotEqual(
            path_fold("chapters/Chapter.tex"), path_fold("chapters/chapter.tex")
        )

    @unittest.skipUnless(os.name == "nt", "Windows-only case-insensitive filesystem")
    def test_public_final_compile_folds_case_variant_runtime_input_on_windows(
        self,
    ) -> None:
        # target_invariant: a recorder-observed runtime path whose text case
        # differs from the approved declaration still matches on Windows
        # through the platform-aware identity fold.
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
            plan_updates={"fixture_runtime_input_case_variant": True},
            compiler_evidence_fixture=True,
        )

        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        self.assertEqual(
            "guarded_final_compile_complete", envelope["classification"]
        )

    def test_public_adapter_rejects_ambiguous_manifest_entrypoints_with_stable_code(
        self,
    ) -> None:
        # scenario_id: real_ambiguous_manifest_entrypoints
        # target_invariant: two manifest TeX entries staging the same path
        #   fail closed with the stable ambiguous-entrypoint classification.
        # mutation_seam: declare main_a and main_b at the same staging path.
        # rematerialized_nodes: none (manifest and plan stay coherent).
        # expected_first_failing_gate: final_editable_tex_source_set_entrypoint.
        # expected_error_code: final_tex_entrypoint_ambiguous.
        from tests.video_workflow.test_rendered_text_reconciliation import (
            write_json,
        )

        isolated_root = self._isolated_authority_clone("am")
        workspace = isolated_root / "待删除/i58amb" / uuid.uuid4().hex[:6]
        workspace.mkdir(parents=True)
        source = workspace / "integrated-main.tex"
        source.write_text("fixture\n", encoding="utf-8")
        source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
        fake = (
            isolated_root / "tests/video_workflow/fixtures/guarded-compile/fake_xelatex.py"
        )
        package_inventory = (
            isolated_root
            / "tests/video_workflow/fixtures/guarded-compile/package-inventory.json"
        )
        policy = {
            "schema_name": "compile-runtime-policy",
            "schema_version": "1.0.0",
            "kernel_version": "2.0.0",
            "policy_id": "fixture-miktex-runtime",
            "policy_version": "1.0.0",
            "runtime_family": "miktex",
            "engine": {
                "name": "xelatex-fixture",
                "version": "fixture-1",
                "executable": str(Path(sys.executable).resolve()),
                "sha256": hashlib.sha256(
                    Path(sys.executable).read_bytes()
                ).hexdigest(),
                "prefix_args": [str(fake.resolve())],
                "prefix_file_fingerprints": [
                    {
                        "path": str(fake.resolve()),
                        "sha256": hashlib.sha256(fake.read_bytes()).hexdigest(),
                    }
                ],
            },
            "package_inventory": {
                "version": "fixture-1",
                "path": str(package_inventory.resolve()),
                "sha256": hashlib.sha256(
                    package_inventory.read_bytes()
                ).hexdigest(),
            },
            "system_fonts": [],
            "allowed_packages": ["article"],
            "allowed_runtime_roots": [str(Path(sys.executable).resolve().parent)],
            "shell_escape": False,
            "automatic_package_install": False,
            "dependency_discovery_policy_version": "recorder-closure-v1",
        }
        policy["policy_sha256"] = canonical_sha(policy, "policy_sha256")
        policy_path = write_json(workspace / "runtime-policy.json", policy)
        secure_seal = "1" * 64
        manifest = {
            "schema_name": "final-compile-manifest",
            "schema_version": "1.0.0",
            "activation_status": "target_only",
            "mode": "final",
            "precompile_text_seal_sha256": secure_seal,
            "entries": [
                {
                    "logical_id": "main_a",
                    "generation": 1,
                    "sha256": source_sha256,
                    "source_path": str(source),
                    "staging_path": "main.tex",
                },
                {
                    "logical_id": "main_b",
                    "generation": 1,
                    "sha256": source_sha256,
                    "source_path": str(source),
                    "staging_path": "main.tex",
                },
            ],
            "approved_runtime_inputs": [],
            "runtime_policy": {
                "path": str(policy_path.resolve()),
                "sha256": hashlib.sha256(policy_path.read_bytes()).hexdigest(),
            },
        }
        manifest["manifest_sha256"] = canonical_sha(manifest, "manifest_sha256")
        manifest_path = write_json(workspace / "compile-manifest.json", manifest)
        plan = {
            "schema_name": "text-origin-plan",
            "schema_version": "1.0.0",
            "precompile_text_seal_sha256": secure_seal,
            "page_count": 1,
            "extractor_suite": [],
            "rendered_objects": [],
            "sealed_items": [],
            "edges": [],
        }
        plan["plan_sha256"] = canonical_sha(plan, "plan_sha256")
        plan_path = write_json(workspace / "text-origin-plan.json", plan)
        output = workspace / "adapter-output"
        request = {
            "schema_name": "guarded-final-compile-request",
            "schema_version": "1.0.0",
            "activation_status": "target_only",
            "precompile_text_seal_sha256": secure_seal,
            "compile_manifest_path": str(manifest_path.resolve()),
            "compile_manifest_sha256": manifest["manifest_sha256"],
            "text_origin_plan_path": str(plan_path.resolve()),
            "text_origin_plan_sha256": plan["plan_sha256"],
            "generation_set_sha256": "2" * 64,
            "compile_provider": {"provider_id": "fixture"},
            "compiled_at": "2026-08-25T13:30:00+08:00",
            "output_root": str(output.resolve()),
            "runtime_policy_path": str(policy_path.resolve()),
            "runtime_policy_sha256": hashlib.sha256(
                policy_path.read_bytes()
            ).hexdigest(),
        }
        request_path = write_json(workspace / "compile-request.json", request)
        completed = subprocess.run(
            [
                sys.executable,
                "-X", "utf8", "-B",
                str(isolated_root / "scripts/guarded_final_compile_adapter.py"),
                str(request_path),
            ],
            cwd=isolated_root,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(1, completed.returncode, completed.stdout + completed.stderr)
        self.assertIn(
            "compile manifest entrypoint is missing or ambiguous", completed.stderr
        )
        error = json.loads((output / "adapter-error.json").read_text(encoding="utf-8"))
        self.assertEqual("guarded-final-compile-error", error["schema_name"])
        self.assertEqual(
            "final_editable_tex_source_set_entrypoint", error["first_failing_gate"]
        )
        self.assertEqual("final_tex_entrypoint_ambiguous", error["error_code"])

    def test_public_named_pdf_publication_rolls_back_when_pdf_copy_fails(self) -> None:
        # fault injection: the PDF copy itself raises; nothing reaches the
        # public target set and the same name can be retried immediately.
        from tests.video_workflow.test_rendered_text_reconciliation import (
            RenderedTextReconciliationCliTests,
        )

        fixture = RenderedTextReconciliationCliTests(
            "test_public_final_compile_invokes_adapter_and_produces_reconcilable_evidence"
        )
        root, paths = fixture.fixture(
            production_compile=True,
            authority_root=PROJECT_ROOT / "待删除/i58t" / uuid.uuid4().hex[:6],
        )

        def fail_pdf_copy(temp: Path, source: Path) -> None:
            raise OSError("disk full")

        self._run_fault_publication(
            fixture,
            root,
            paths,
            pdf_name="alpha.pdf",
            workspace_key="pdf-copy",
            patch_target="_publish_copy",
            side_effect=fail_pdf_copy,
            expect_rollback_files=False,
        )

    def test_public_named_pdf_publication_rolls_back_after_pdf_commit(self) -> None:
        # fault injection: the PDF rename commits, then the first JSON rename
        # raises; the already-committed PDF is rolled back. This is the
        # reviewer scenario "PDF 复制成功后抛出异常".
        from tests.video_workflow.test_rendered_text_reconciliation import (
            RenderedTextReconciliationCliTests,
        )

        fixture = RenderedTextReconciliationCliTests(
            "test_public_final_compile_invokes_adapter_and_produces_reconcilable_evidence"
        )
        root, paths = fixture.fixture(
            production_compile=True,
            authority_root=PROJECT_ROOT / "待删除/i58t" / uuid.uuid4().hex[:6],
        )
        self._run_fault_publication(
            fixture,
            root,
            paths,
            pdf_name="beta.pdf",
            workspace_key="after-pdf-commit",
            patch_target="_publish_commit",
            side_effect=[None, OSError("commit interrupted")],
        )

    def test_public_named_pdf_publication_rolls_back_when_second_json_write_fails(
        self,
    ) -> None:
        # fault injection: the first JSON bytes write succeeds, then the
        # second raises. This is the reviewer scenario
        # "第一个 JSON 写入成功后抛出异常".
        from tests.video_workflow.test_rendered_text_reconciliation import (
            RenderedTextReconciliationCliTests,
        )

        fixture = RenderedTextReconciliationCliTests(
            "test_public_final_compile_invokes_adapter_and_produces_reconcilable_evidence"
        )
        root, paths = fixture.fixture(
            production_compile=True,
            authority_root=PROJECT_ROOT / "待删除/i58t" / uuid.uuid4().hex[:6],
        )
        self._run_fault_publication(
            fixture,
            root,
            paths,
            pdf_name="gamma.pdf",
            workspace_key="second-json-write",
            patch_target="_publish_bytes",
            side_effect=[None, OSError("write interrupted")],
        )

    def test_public_named_pdf_publication_rolls_back_before_report_write(self) -> None:
        # fault injection: every artifact through the adapter identity commits,
        # then the report rename raises. This is the reviewer scenario
        # "Source Set 写入成功、Report 写入前抛出异常".
        from tests.video_workflow.test_rendered_text_reconciliation import (
            RenderedTextReconciliationCliTests,
        )

        fixture = RenderedTextReconciliationCliTests(
            "test_public_final_compile_invokes_adapter_and_produces_reconcilable_evidence"
        )
        root, paths = fixture.fixture(
            production_compile=True,
            authority_root=PROJECT_ROOT / "待删除/i58t" / uuid.uuid4().hex[:6],
        )
        self._run_fault_publication(
            fixture,
            root,
            paths,
            pdf_name="delta.pdf",
            workspace_key="before-report",
            patch_target="_publish_commit",
            side_effect=[None] * 5 + [OSError("commit interrupted")],
        )

    def test_public_named_pdf_publication_rolls_back_when_report_write_fails(
        self,
    ) -> None:
        # fault injection: all five JSON content writes succeed, then the
        # report content write raises. This is the reviewer scenario
        # "Report 写入失败".
        from tests.video_workflow.test_rendered_text_reconciliation import (
            RenderedTextReconciliationCliTests,
        )

        fixture = RenderedTextReconciliationCliTests(
            "test_public_final_compile_invokes_adapter_and_produces_reconcilable_evidence"
        )
        root, paths = fixture.fixture(
            production_compile=True,
            authority_root=PROJECT_ROOT / "待删除/i58t" / uuid.uuid4().hex[:6],
        )
        self._run_fault_publication(
            fixture,
            root,
            paths,
            pdf_name="epsilon.pdf",
            workspace_key="report-write",
            patch_target="_publish_bytes",
            side_effect=[None] * 5 + [OSError("report write failed")],
        )


if __name__ == "__main__":
    unittest.main()
