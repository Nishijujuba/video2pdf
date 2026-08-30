from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys
import unittest
import uuid


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tests.video_workflow._test_run import new_case_dir
from tests.video_workflow import test_single_section_production as single_section_fixture
from tests.video_workflow.test_issue13_run_initialization import (
    _run_start_cli_with_recording,
    _write_start_run_project,
)
from tests.video_workflow.test_precompile_quality import semantic_dependencies
from video2pdf_workflow_kernel import cli as kernel_cli
from video2pdf_workflow_kernel.errors import KernelError


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _real_figure_png() -> bytes:
    # A valid 1x1 PNG so the final compile adapter can rasterize the declared
    # figure source; the fixture's b"fixture-png" is not a readable image.
    import base64

    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
        "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
    )


def _canonical_sha(value: object) -> str:
    return hashlib.sha256(
        (
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
    ).hexdigest()


def _write_json(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )
    return path


def _compile_runtime_policy_fixture(run_dir: Path) -> Path:
    # production-advance persists the validated policy at the canonical
    # workflow/compile-runtime-policy.json path; final compile binds to it.
    return run_dir / "workflow" / "compile-runtime-policy.json"


class Issue13FinalEvidenceCliTests(unittest.TestCase):
    """Kernel final evidence is prepared only through public workflow seams."""

    def _cli(
        self, *arguments: str, cwd: Path = PROJECT_ROOT
    ) -> tuple[subprocess.CompletedProcess[str], dict]:
        del cwd
        command = arguments[0] if arguments else "unknown"
        try:
            parsed = kernel_cli._parser().parse_args(list(arguments))
            envelope = kernel_cli._execute(parsed, PROJECT_ROOT)
            returncode = 0
        except KernelError as exc:
            envelope = kernel_cli._error(command, exc)
            returncode = exc.exit_code
        stdout = json.dumps(envelope, ensure_ascii=False, sort_keys=True) + "\n"
        completed = subprocess.CompletedProcess(
            list(arguments), returncode, stdout, ""
        )
        return completed, envelope

    def _require_ok(self, *arguments: str) -> dict:
        completed, envelope = self._cli(*arguments)
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        return envelope

    def _attempt(self, run_dir: Path, task: dict, outputs: dict[str, bytes]) -> str:
        """Write only the worker-owned Task Attempt presented to the public CLI."""
        attempt_id = uuid.uuid4().hex[:24]
        attempt_dir = (
            run_dir
            / "workflow"
            / "tasks"
            / task["task_id"]
            / "attempts"
            / attempt_id
        )
        attempt_dir.mkdir(parents=True)
        for relative, payload in outputs.items():
            path = attempt_dir / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        attempt = {
            "schema_name": "production-task-attempt",
            "schema_version": "1.0.0",
            "kernel_version": "2.0.0",
            "task_id": task["task_id"],
            "attempt_id": attempt_id,
            "claim_generation": task["claim_generation"],
            "claim_token": task["claim_token"],
            "envelope_sha256": _sha256(
                run_dir / "workflow" / "tasks" / task["task_id"] / "envelope.json"
            ),
            "outputs": [
                {
                    "path": relative,
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
                for relative, payload in sorted(outputs.items())
            ],
        }
        (attempt_dir / "attempt.json").write_text(
            json.dumps(attempt, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        return attempt_id

    def _advance(self, run_dir: Path, task: dict, outputs: dict[str, bytes], *extra: str) -> dict:
        attempt_id = self._attempt(run_dir, task, outputs)
        return self._require_ok(
            "production-advance",
            "--run-dir",
            str(run_dir),
            "--task-id",
            task["task_id"],
            "--attempt-id",
            attempt_id,
            *extra,
        )

    def _source_ready_v4_run(self) -> tuple[Path, Path]:
        case_root = new_case_dir(self.id(), label="issue13-final-evidence")
        project_config, control_root, cookie = _write_start_run_project(case_root)
        recording = (
            PROJECT_ROOT
            / "tests"
            / "video_workflow"
            / "fixtures"
            / "providers"
            / "bilibili"
            / "fresh-download"
        )
        completed, initialized = _run_start_cli_with_recording(
            recording,
            "start-run",
            "--project-config",
            str(project_config),
            "--platform",
            "bilibili",
            "--source-url",
            "https://www.bilibili.com/video/BV1TEST00001/?p=1",
            "--session-id",
            "session-issue13-final-evidence",
            "--credential-ref",
            str(cookie),
        )
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        run_dir = Path(initialized["data"]["run_dir"])
        acquired = self._require_ok(
            "source-acquire",
            "--run-dir",
            str(run_dir),
            "--cookie-file",
            str(cookie),
            "--provider-recording",
            str(recording),
        )
        self.assertEqual("source_acquired", acquired["classification"])
        run = json.loads((run_dir / "workflow" / "run.json").read_text(encoding="utf-8"))
        self.assertEqual("4.0.0", run["schema_version"])
        self.assertEqual("current", run["checkpoints"]["source_ready"]["status"])
        return run_dir, control_root

    def _production_complete(
        self, run_dir: Path, *, second_blank_page: bool = False
    ) -> None:
        plan = self._require_ok("production-plan", "--run-dir", str(run_dir))
        outline = plan["data"]["runnable_tasks"][0]
        self._advance(
            run_dir,
            outline,
            {"outline.json": single_section_fixture.SingleSectionProductionTests._outline_payload()},
        )
        plan = self._require_ok("production-plan", "--run-dir", str(run_dir))
        outline_gate = plan["data"]["runnable_tasks"][0]
        self._advance(
            run_dir,
            outline_gate,
            {"pyramid-report.json": single_section_fixture.SingleSectionProductionTests._pyramid_payload(outline_gate)},
        )

        tasks = self._require_ok("production-plan", "--run-dir", str(run_dir))["data"]["runnable_tasks"]
        writer = next(task for task in tasks if task["role"] == "writer")
        figure = next(task for task in tasks if task["role"] == "figure")
        writer_result = json.dumps(
            {
                "schema_name": "writer-result",
                "schema_version": "1.0.0",
                "section_id": "section_01",
                "new_figure_candidates": [],
            },
            sort_keys=True,
        ).encode()
        section_source = b"\\section{Core claim}\n% FIGURE_SLOT:figure_01\n"
        if second_blank_page:
            section_source += b"% VIDEO2PDF_FIXTURE_SECOND_BLANK_PAGE\n"
        self._advance(
            run_dir,
            writer,
            {
                "section_01.tex": section_source,
                "writer-result.json": writer_result,
            },
        )
        # fixture_evolution: figure contribution is the target node; its
        # manifest fingerprint below is rematerialized from these exact bytes.
        # No downstream node is intentionally stale. The public
        # production-advance seam must first pass figure_manifest_binding.
        contribution = (
            b"\\begin{figure}[H]\n\\centering\n"
            b"\\includegraphics[width=0.76\\linewidth,height=0.34\\textheight,"
            b"keepaspectratio]{figures/figure_01}\n"
            b"\\caption{Bound evidence.}\n"
            b"\\par\\small Source (source\\_timestamp): 00:00:01\n\\end{figure}\n"
        )
        figure_manifest = json.dumps(
            {
                "schema_name": "figure-manifest",
                "schema_version": "1.0.0",
                "kernel_version": "2.0.0",
                "slot_id": "figure_01",
                "section_id": "section_01",
                "asset_path": "figures/figure_01.png",
                "asset_sha256": hashlib.sha256(_real_figure_png()).hexdigest(),
                "caption": "Bound evidence.",
                "source": {"kind": "source_timestamp", "value": "00:00:01"},
                "slot_contribution_path": "work/figures/figure_01.tex",
                "slot_contribution_sha256": hashlib.sha256(contribution).hexdigest(),
            },
            sort_keys=True,
        ).encode()
        self._advance(
            run_dir,
            figure,
            {
                "figure_01.png": _real_figure_png(),
                "figure-manifest.json": figure_manifest,
                "figure_01.tex": contribution,
            },
        )

        section_gate = self._require_ok("production-plan", "--run-dir", str(run_dir))["data"]["runnable_tasks"][0]
        self._advance(
            run_dir,
            section_gate,
            {"pyramid-report.json": single_section_fixture.SingleSectionProductionTests._pyramid_payload(section_gate)},
        )
        main_gate = self._require_ok("production-plan", "--run-dir", str(run_dir))["data"]["runnable_tasks"][0]
        attempt_id = self._attempt(
            run_dir,
            main_gate,
            {"pyramid-report.json": single_section_fixture.SingleSectionProductionTests._pyramid_payload(main_gate)},
        )
        from video2pdf_workflow_kernel.guarded_compile import runtime_policy_for_fixture

        policy = runtime_policy_for_fixture(
            run_dir=run_dir,
            engine_executable=Path(sys.executable),
            engine_prefix_args=[
                str(PROJECT_ROOT / "tests/video_workflow/fixtures/guarded-compile/fake_xelatex.py")
            ],
            system_fonts=[Path("C:/Windows/Fonts/arial.ttf")],
        )
        policy_path = run_dir / "workflow" / "fixture-compile-runtime-policy.json"
        policy_path.write_text(json.dumps(policy, sort_keys=True), encoding="utf-8")
        advanced = self._require_ok(
            "production-advance",
            "--run-dir",
            str(run_dir),
            "--task-id",
            main_gate["task_id"],
            "--attempt-id",
            attempt_id,
            "--compile-runtime-policy",
            str(policy_path),
        )
        final_plan = self._require_ok("production-plan", "--run-dir", str(run_dir))
        self.assertEqual("production_complete", final_plan["classification"])
        self.assertEqual("diagnostic_compile_ready", advanced["classification"])

    def _current_quality_evidence(
        self,
        run_dir: Path,
        *,
        delivery_glossary: dict[str, str] | None = None,
    ) -> dict[str, Path]:
        quality = run_dir / "review" / "quality"
        state = json.loads(
            (run_dir / "workflow" / "production-state.json").read_text(encoding="utf-8")
        )
        main_generation = state["artifacts"]["integrated_main"]
        main_tex = run_dir / main_generation["path"]
        support_specs = (
            ("local_class", "work/integration/course.cls", "local_class"),
            ("local_style", "work/integration/local.sty", "local_style"),
            ("bibliography", "work/integration/refs.bib", "bibliography"),
        )
        generation_artifacts = [
            {
                "logical_id": "integrated_main_tex",
                "generation": main_generation["generation"],
                "sha256": main_generation["sha256"],
            }
        ]
        for logical_id, _relative, _role in support_specs:
            artifact = state["artifacts"].get(logical_id)
            if artifact is None:
                continue
            generation_artifacts.append(
                {
                    "logical_id": logical_id,
                    "generation": artifact["generation"],
                    "sha256": artifact["sha256"],
                }
            )
        figure_asset = state["artifacts"].get("figure_asset_figure_01")
        if figure_asset is not None:
            generation_artifacts.append(
                {
                    "logical_id": "figure_asset_figure_01",
                    "generation": figure_asset["generation"],
                    "sha256": figure_asset["sha256"],
                }
            )
        section_artifact = state["artifacts"].get("integrated_section_01")
        if section_artifact is not None:
            generation_artifacts.append(
                {
                    "logical_id": "integrated_section_01",
                    "generation": section_artifact["generation"],
                    "sha256": section_artifact["sha256"],
                }
            )
        generations = {
            "schema_name": "precompile-artifact-generation-set",
            "schema_version": "1.0.0",
            "generation_set_id": "issue13-production-complete",
            "producer_ids": [main_generation["producer"]],
            "artifacts": generation_artifacts,
        }
        generations["generation_set_sha256"] = _canonical_sha(generations)
        item = {
            "item_id": "main.body",
            "kind": "paragraph",
            "semantic_region": "main",
            "language_profile_id": "zh-hans",
            "source_artifact_logical_id": "integrated_main_tex",
            "source_generation": main_generation["generation"],
            "source_sha256": main_generation["sha256"],
            "locator": "latex:document/body",
            "representation": "structured_text",
            "declared_text": "Core claim",
            "text_sha256": hashlib.sha256(b"Core claim").hexdigest(),
            "applicable_rule_ids": ["no_meta_writing_content"],
        }
        item["item_sha256"] = _canonical_sha(item)
        inventory = {
            "schema_name": "reader-facing-text-inventory",
            "schema_version": "1.0.0",
            "inventory_id": "issue13-current-reader-text",
            "language_profile_id": "zh-hans",
            "delivery_glossary": delivery_glossary,
            "generation_set_sha256": generations["generation_set_sha256"],
            "declared_surface": [{"region_id": "main.body", "kind": "paragraph"}],
            "items": [item],
            "coverage_ledger": [
                {"region_id": "main.body", "item_id": "main.body", "status": "covered"}
            ],
            "extractors": [
                {"extractor_id": "latex-reader-text-extractor", "extractor_sha256": "9" * 64}
            ],
        }
        inventory["reader_text_set_sha256"] = _canonical_sha(
            [
                {
                    "item_id": item["item_id"],
                    "kind": item["kind"],
                    "representation": item["representation"],
                    "text_sha256": item["text_sha256"],
                }
            ]
        )
        inventory["inventory_sha256"] = _canonical_sha(inventory)
        generation_path = _write_json(quality / "inputs" / "generations.json", generations)
        inventory_path = _write_json(quality / "inputs" / "inventory.json", inventory)
        dependencies_path = _write_json(
            quality / "inputs" / "semantic-dependencies.json", semantic_dependencies()
        )
        self._require_ok(
            "delivery-quality-precompile-prepare",
            "--workspace-root", str(quality),
            "--inventory", str(inventory_path),
            "--artifact-generations", str(generation_path),
            "--semantic-dependencies", str(dependencies_path),
            "--prepared-at", "2026-08-11T01:50:00Z",
        )
        for owner in (
            "source-faithfulness-reviewer",
            "writing-quality-reviewer",
            "pyramid-reviewer",
        ):
            skeleton_path = quality / "reviewers" / owner / "input" / "review-skeleton.json"
            skeleton = json.loads(skeleton_path.read_text(encoding="utf-8"))
            patch = {
                "schema_name": "precompile-judgment-patch",
                "schema_version": "1.0.0",
                "task_id": skeleton["task_id"],
                "owner": owner,
                "skeleton_sha256": skeleton["skeleton_sha256"],
                "generation_set_sha256": skeleton["generation_set_sha256"],
                "reviewer": {
                    "reviewer_id": f"independent-{owner}",
                    "runtime_sha256": "b" * 64,
                    "independent_from_generation_producers": True,
                },
                "results": [
                    {
                        "result_key": required["result_key"],
                        "decision": "pass",
                        "evidence_locator": f"artifact:{required['result_key']}",
                        "repair_write_set": [],
                    }
                    for required in skeleton["required_results"]
                ],
                "contract_gaps": [],
            }
            patch["patch_sha256"] = _canonical_sha(patch)
            patch_path = _write_json(quality / "inputs" / f"{owner}.patch.json", patch)
            self._require_ok(
                "delivery-quality-precompile-patch-commit",
                "--workspace-root", str(quality),
                "--owner", owner,
                "--patch", str(patch_path),
                "--committed-at", "2026-08-11T01:51:00Z",
            )
        self._require_ok(
            "delivery-quality-precompile-materialize",
            "--workspace-root", str(quality),
            "--provider-id", "precompile-quality-provider",
            "--provider-version", "1.0.0",
            "--materialized-at", "2026-08-11T01:52:00Z",
        )
        self._require_ok(
            "delivery-quality-seal",
            "--workspace-root", str(quality),
            "--sealed-at", "2026-08-11T01:53:00Z",
        )
        seal = json.loads((quality / "precompile-text-seal.json").read_text(encoding="utf-8"))
        runtime_policy_path = _compile_runtime_policy_fixture(run_dir)
        entries = [
            {
                "logical_id": "integrated_main_tex",
                "generation": main_generation["generation"],
                "sha256": main_generation["sha256"],
                "source_path": str(main_tex),
                "staging_path": "main.tex",
            }
        ]
        for logical_id, relative, _role in support_specs:
            artifact = state["artifacts"].get(logical_id)
            if artifact is None:
                continue
            entries.append(
                {
                    "logical_id": logical_id,
                    "generation": artifact["generation"],
                    "sha256": artifact["sha256"],
                    "source_path": str(run_dir / artifact["path"]),
                    "staging_path": PurePosixPath(relative).name,
                }
            )
        figure_asset = state["artifacts"].get("figure_asset_figure_01")
        if figure_asset is not None:
            figure_path = run_dir / figure_asset["path"]
            entries.append(
                {
                    "logical_id": "figure_asset_figure_01",
                    "generation": figure_asset["generation"],
                    "sha256": figure_asset["sha256"],
                    "source_path": str(figure_path),
                    "staging_path": "figures/figure_01.png",
                }
            )
        section_artifact = state["artifacts"].get("integrated_section_01")
        if section_artifact is not None:
            entries.append(
                {
                    "logical_id": "integrated_section_01",
                    "generation": section_artifact["generation"],
                    "sha256": section_artifact["sha256"],
                    "source_path": str(run_dir / section_artifact["path"]),
                    "staging_path": "section_01.tex",
                }
            )
        compile_manifest = {
            "schema_name": "final-compile-manifest",
            "schema_version": "1.0.0",
            "activation_status": "target_only",
            "mode": "final",
            "precompile_text_seal_sha256": seal["seal_sha256"],
            "entries": entries,
            "approved_runtime_inputs": [
                {
                    "path": "C:/Windows/Fonts/arial.ttf",
                    "sha256": _sha256(Path("C:/Windows/Fonts/arial.ttf")),
                    "classification": "registered_system_font",
                }
            ],
            "runtime_policy": {
                "path": str(runtime_policy_path.resolve()),
                "sha256": _sha256(runtime_policy_path),
            },
        }
        compile_manifest["manifest_sha256"] = _canonical_sha(compile_manifest)
        compile_manifest_path = _write_json(
            quality / "inputs" / "final-compile-manifest.json", compile_manifest
        )
        rendered_object = {
            "object_id": "page-1-text-1",
            "page": 1,
            "object_kind": "pdf_text_run",
            "bbox": [72.0, 60.17499923706055, 124.55799865722656, 75.28900146484375],
            "exact_utf8_text": "Core claim",
            "extractor_id": "pdf-text-v1",
            "evidence_locator": "page:1/block:1/line:1/span:1",
        }
        origin_edge = {
            "edge_id": "origin.main.body",
            "disposition": "sealed_origin",
            "sealed_item_id": "main.body",
            "sealed_text_utf8": "Core claim",
            "rendered_object_ids": ["page-1-text-1"],
            "recipe": "exact_utf8",
        }
        origin_plan = {
            "schema_name": "text-origin-plan",
            "schema_version": "1.0.0",
            "precompile_text_seal_sha256": seal["seal_sha256"],
            "sealed_items": [{"item_id": "main.body", "exact_utf8_text": "Core claim"}],
            "page_count": 1,
            "extractor_suite": [{"extractor_id": "pdf-text-v1", "extractor_sha256": "a" * 64}],
            "rendered_objects": [rendered_object],
            "edges": [origin_edge],
        }
        origin_plan["plan_sha256"] = _canonical_sha(origin_plan)
        origin_plan_path = _write_json(quality / "inputs" / "text-origin-plan.json", origin_plan)
        final_workspace = run_dir / "review" / "acceptance" / "final-evidence"
        self._require_ok(
            "delivery-quality-final-compile",
            "--input-track", "kernel",
            "--precompile-workspace-root", str(quality),
            "--compile-manifest", str(compile_manifest_path),
            "--compiler-adapter", str(PROJECT_ROOT / "scripts/guarded_final_compile_adapter.py"),
            "--runtime-policy", str(runtime_policy_path),
            "--workspace-root", str(final_workspace),
            "--compiled-at", "2026-08-11T01:54:00Z",
        )
        evidence = {
            "main_tex": main_tex,
            "precompile_quality_report": quality / "precompile-quality-report.json",
            "precompile_text_seal": quality / "precompile-text-seal.json",
            "final_compile_manifest": compile_manifest_path,
            "final_compile_report": final_workspace / "final-compile-report.json",
            "final_artifact_seal": final_workspace / "final-artifact-seal.json",
            "final_pdf": final_workspace / "adapter-output" / "final.pdf",
            "render_evidence_manifest": final_workspace / "render-evidence-manifest.json",
            "rendered_text_inventory": final_workspace / "adapter-output" / "rendered-text-object-inventory.json",
            "text_origin_manifest": final_workspace / "text-origin-manifest.json",
            "reconciliation": final_workspace / "rendered-text-reconciliation-report.json",
        }
        self._require_ok(
            "delivery-quality-rendered-text-reconcile",
            "--precompile-workspace-root", str(quality),
            "--compile-manifest", str(evidence["final_compile_manifest"]),
            "--compile-report", str(evidence["final_compile_report"]),
            "--final-artifact-seal", str(evidence["final_artifact_seal"]),
            "--final-pdf", str(evidence["final_pdf"]),
            "--render-evidence-manifest", str(evidence["render_evidence_manifest"]),
            "--rendered-text-inventory", str(evidence["rendered_text_inventory"]),
            "--text-origin-manifest", str(evidence["text_origin_manifest"]),
            "--output", str(evidence["reconciliation"]),
            "--reconciled-at", "2026-08-11T01:55:00Z",
        )
        allowed = {
            "criteria_file": "docs/acceptance/acceptance_criteria.v1.json",
            "review_output_dir": "review/acceptance",
            "final_artifacts": [
                {"role": "pdf", "path": evidence["final_pdf"].relative_to(run_dir).as_posix()},
                {"role": "tex", "path": main_tex.relative_to(run_dir).as_posix()},
            ],
            "forbidden_artifacts": [],
        }
        evidence["allowed_manifest"] = _write_json(
            run_dir / "review" / "acceptance" / "allowed_artifacts_manifest.json", allowed
        )
        return evidence

    def _invoke_prepare(
        self,
        run_dir: Path,
        control_root: Path,
        evidence: dict[str, Path],
        *,
        fault_point: str | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], dict]:
        gate_authority = control_root / "active_global_gate.json"
        arguments = [
            "delivery-final-evidence-prepare",
            "--run-dir",
            str(run_dir),
            "--final-pdf",
            str(evidence["final_pdf"]),
            "--main-tex",
            str(evidence["main_tex"]),
            "--final-compile-report",
            str(evidence["final_compile_report"]),
            "--final-compile-manifest",
            str(evidence["final_compile_manifest"]),
            "--precompile-quality-report",
            str(evidence["precompile_quality_report"]),
            "--precompile-text-seal",
            str(evidence["precompile_text_seal"]),
            "--final-artifact-seal",
            str(evidence["final_artifact_seal"]),
            "--rendered-text-reconciliation",
            str(evidence["reconciliation"]),
            "--render-evidence-manifest",
            str(evidence["render_evidence_manifest"]),
            "--rendered-text-inventory",
            str(evidence["rendered_text_inventory"]),
            "--text-origin-manifest",
            str(evidence["text_origin_manifest"]),
            "--global-gate-authority",
            str(gate_authority),
            "--allowed-manifest",
            str(evidence["allowed_manifest"]),
            "--prepared-at",
            "2026-08-11T02:00:00Z",
        ]
        if fault_point is not None:
            arguments.extend(["--fault-point", fault_point])
        return self._cli(*arguments)

    def test_public_cli_prepares_kernel_final_evidence_for_acceptance_v2(self) -> None:
        run_dir, control_root = self._source_ready_v4_run()
        self._production_complete(run_dir)
        evidence = self._current_quality_evidence(run_dir)

        completed, envelope = self._invoke_prepare(run_dir, control_root, evidence)

        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        self.assertEqual("delivery_final_evidence_prepared", envelope["classification"])
        binding = Path(envelope["data"]["input_binding_path"])
        authority = Path(envelope["data"]["final_quality_authority_path"])
        self.assertTrue(binding.is_file())
        self.assertTrue(authority.is_file())
        published = self._require_ok(
            "acceptance-final-authority-publish", "--input-binding", str(binding)
        )
        self.assertEqual("acceptance_v2_final_authority_published", published["classification"])
        prepared = self._require_ok(
            "acceptance-prepare",
            "--workspace-root",
            str(run_dir / "review" / "acceptance"),
            "--input-binding",
            str(binding),
            "--attempt-number",
            "1",
            "--prepared-at",
            "2026-08-11T02:01:00Z",
            "--coordinator-session",
            "session-issue13-final-evidence",
        )
        self.assertEqual("acceptance_v2_prepared", prepared["classification"])

    def test_final_pdf_drift_blocks_acceptance_consumption(self) -> None:
        run_dir, control_root = self._source_ready_v4_run()
        self._production_complete(run_dir)
        evidence = self._current_quality_evidence(run_dir)
        completed, envelope = self._invoke_prepare(run_dir, control_root, evidence)
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        binding = Path(envelope["data"]["input_binding_path"])
        final_pdf = Path(envelope["data"]["final_pdf_path"])
        final_pdf.write_bytes(final_pdf.read_bytes() + b"drift")

        rejected, rejection = self._cli(
            "acceptance-final-authority-publish", "--input-binding", str(binding)
        )

        self.assertNotEqual(0, rejected.returncode)
        self.assertEqual("artifact_drift", rejection["classification"])
        self.assertEqual("acceptance_final_pdf_stale", rejection["data"]["error_code"])

    def test_bound_compile_report_and_allowed_manifest_drift_fail_closed(self) -> None:
        run_dir, control_root = self._source_ready_v4_run()
        self._production_complete(run_dir)
        evidence = self._current_quality_evidence(run_dir)
        completed, envelope = self._invoke_prepare(run_dir, control_root, evidence)
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        binding = Path(envelope["data"]["input_binding_path"])

        for logical_id, path in (
            ("final_compile_report", evidence["final_compile_report"]),
            ("allowed_artifacts_manifest", evidence["allowed_manifest"]),
        ):
            with self.subTest(logical_id=logical_id):
                pristine = path.read_bytes()
                path.write_bytes(pristine + b" ")
                rejected, rejection = self._cli(
                    "acceptance-final-authority-publish",
                    "--input-binding",
                    str(binding),
                )
                self.assertNotEqual(0, rejected.returncode)
                self.assertEqual("acceptance_v2_rejected", rejection["classification"])
                self.assertEqual(
                    "acceptance_input_stale", rejection["data"]["error_code"]
                )
                path.write_bytes(pristine)

        published = self._require_ok(
            "acceptance-final-authority-publish", "--input-binding", str(binding)
        )
        self.assertEqual("acceptance_v2_final_authority_published", published["classification"])

    def test_allowed_manifest_accepts_one_glossary_and_rejects_duplicate_role(self) -> None:
        run_dir, control_root = self._source_ready_v4_run()
        self._production_complete(run_dir)
        glossary = _write_json(
            run_dir / "review" / "acceptance" / "delivery_glossary.json",
            {"schema_version": "delivery_glossary.v1", "terms": []},
        )
        glossary_identity = {
            "glossary_id": "issue13-governed-delivery-glossary",
            "path": glossary.relative_to(run_dir).as_posix(),
            "sha256": _sha256(glossary),
        }
        evidence = self._current_quality_evidence(
            run_dir, delivery_glossary=glossary_identity
        )
        allowed_path = evidence["allowed_manifest"]
        allowed = json.loads(allowed_path.read_text(encoding="utf-8"))
        glossary_entry = {
            "role": "delivery_glossary",
            "path": glossary.relative_to(run_dir).as_posix(),
        }
        allowed["final_artifacts"].append(glossary_entry)
        _write_json(allowed_path, allowed)

        completed, envelope = self._invoke_prepare(run_dir, control_root, evidence)
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        binding = json.loads(
            Path(envelope["data"]["input_binding_path"]).read_text(encoding="utf-8")
        )
        glossary_binding = next(
            item for item in binding["artifacts"] if item["logical_id"] == "delivery_glossary"
        )
        self.assertEqual(str(glossary.resolve()), glossary_binding["path"])
        self.assertEqual(_sha256(glossary), glossary_binding["sha256"])

        allowed["final_artifacts"].append(dict(glossary_entry))
        _write_json(allowed_path, allowed)
        rejected, rejection = self._invoke_prepare(run_dir, control_root, evidence)
        self.assertNotEqual(0, rejected.returncode)
        self.assertEqual("contract_invalid", rejection["classification"])
        self.assertEqual(
            "final_evidence_delivery_glossary_lineage_invalid",
            rejection["data"]["error_code"],
        )
        self.assertEqual(
            "delivery_glossary_lineage",
            rejection["data"]["first_failing_gate"],
        )
        allowed["final_artifacts"].pop()
        _write_json(allowed_path, allowed)

    def test_allowed_manifest_rejects_glossary_absent_from_quality_lineage(self) -> None:
        # scenario_id: allowed_manifest_glossary_without_quality_lineage
        # target_invariant: manifest glossary must be the glossary governed before sealing
        # mutation_seam: after Final Compile, before Final Evidence preparation
        # rematerialized_nodes: allowed_artifacts_manifest only
        # intentionally_stale_nodes: none
        # expected_first_gate: delivery_glossary_lineage
        # expected_error_code: final_evidence_delivery_glossary_lineage_invalid
        # scenario_class: single_contradiction
        run_dir, control_root = self._source_ready_v4_run()
        self._production_complete(run_dir)
        evidence = self._current_quality_evidence(run_dir)
        glossary = _write_json(
            run_dir / "review" / "acceptance" / "delivery_glossary.json",
            {"schema_version": "delivery_glossary.v1", "terms": []},
        )
        allowed_path = evidence["allowed_manifest"]
        allowed = json.loads(allowed_path.read_text(encoding="utf-8"))
        allowed["final_artifacts"].append(
            {
                "role": "delivery_glossary",
                "path": glossary.relative_to(run_dir).as_posix(),
            }
        )
        _write_json(allowed_path, allowed)

        rejected, rejection = self._invoke_prepare(run_dir, control_root, evidence)

        self.assertNotEqual(0, rejected.returncode)
        self.assertEqual("contract_invalid", rejection["classification"])
        self.assertEqual(
            "final_evidence_delivery_glossary_lineage_invalid",
            rejection["data"]["error_code"],
        )
        self.assertEqual(
            "delivery_glossary_lineage",
            rejection["data"]["first_failing_gate"],
        )

    def test_allowed_manifest_rejects_different_glossary_from_quality_lineage(self) -> None:
        # scenario_id: allowed_manifest_glossary_differs_from_quality_lineage
        # target_invariant: manifest path and hash must exact-match governed glossary identity
        # mutation_seam: after Final Compile, before Final Evidence preparation
        # rematerialized_nodes: allowed_artifacts_manifest only
        # intentionally_stale_nodes: none
        # expected_first_gate: delivery_glossary_lineage
        # expected_error_code: final_evidence_delivery_glossary_lineage_invalid
        # scenario_class: single_contradiction
        run_dir, control_root = self._source_ready_v4_run()
        self._production_complete(run_dir)
        governed_glossary = _write_json(
            run_dir / "review" / "acceptance" / "delivery_glossary.json",
            {"schema_version": "delivery_glossary.v1", "terms": []},
        )
        governed_identity = {
            "glossary_id": "issue13-governed-delivery-glossary",
            "path": governed_glossary.relative_to(run_dir).as_posix(),
            "sha256": _sha256(governed_glossary),
        }
        evidence = self._current_quality_evidence(
            run_dir, delivery_glossary=governed_identity
        )
        arbitrary_glossary = _write_json(
            run_dir / "review" / "acceptance" / "arbitrary_glossary.json",
            {"schema_version": "delivery_glossary.v1", "terms": []},
        )
        allowed_path = evidence["allowed_manifest"]
        allowed = json.loads(allowed_path.read_text(encoding="utf-8"))
        allowed["final_artifacts"].append(
            {
                "role": "delivery_glossary",
                "path": arbitrary_glossary.relative_to(run_dir).as_posix(),
            }
        )
        _write_json(allowed_path, allowed)

        rejected, rejection = self._invoke_prepare(run_dir, control_root, evidence)

        self.assertNotEqual(0, rejected.returncode)
        self.assertEqual("contract_invalid", rejection["classification"])
        self.assertEqual(
            "final_evidence_delivery_glossary_lineage_invalid",
            rejection["data"]["error_code"],
        )
        self.assertEqual(
            "delivery_glossary_lineage",
            rejection["data"]["first_failing_gate"],
        )

    def test_reader_inventory_replacement_breaks_governed_quality_lineage(self) -> None:
        # scenario_id: reader_inventory_sha_differs_from_sealed_quality_lineage
        # target_invariant: canonical inventory bytes must match report and seal SHA
        # mutation_seam: after Final Compile, before Final Evidence preparation
        # rematerialized_nodes: reader-facing-text-inventory only
        # intentionally_stale_nodes: precompile report and seal
        # expected_first_gate: delivery_glossary_lineage
        # expected_error_code: final_evidence_delivery_glossary_lineage_invalid
        # scenario_class: single_contradiction
        run_dir, control_root = self._source_ready_v4_run()
        self._production_complete(run_dir)
        evidence = self._current_quality_evidence(run_dir)
        inventory_path = (
            run_dir / "review" / "quality" / "reader-facing-text-inventory.json"
        )
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        inventory["inventory_id"] = "issue13-replaced-reader-text"
        inventory.pop("inventory_sha256", None)
        inventory["inventory_sha256"] = _canonical_sha(inventory)
        _write_json(inventory_path, inventory)

        rejected, rejection = self._invoke_prepare(run_dir, control_root, evidence)

        self.assertNotEqual(0, rejected.returncode)
        self.assertEqual("contract_invalid", rejection["classification"])
        self.assertEqual(
            {
                "first_failing_gate": "delivery_glossary_lineage",
                "error_code": "final_evidence_delivery_glossary_lineage_invalid",
            },
            {
                "first_failing_gate": rejection["data"].get("first_failing_gate"),
                "error_code": rejection["data"].get("error_code"),
            },
        )

    def test_after_binding_write_fault_restores_then_reprepares_committed_binding(
        self,
    ) -> None:
        # scenario_id: binding_written_before_intent_commit
        # target_invariant: an uncommitted binding cannot remain canonical
        # mutation_seam: after binding write and before COMMITTED
        # rematerialized_nodes: preceding canonical page set
        # intentionally_stale_nodes: input binding and final-quality authority
        # expected_first_gate: final_evidence_page_reconciliation
        # expected_error_code: final_evidence_page_publication_restored
        # scenario_class: single_contradiction
        run_dir, control_root = self._source_ready_v4_run()
        self._production_complete(run_dir)
        evidence = self._current_quality_evidence(run_dir)

        faulted, fault = self._invoke_prepare(
            run_dir,
            control_root,
            evidence,
            fault_point="after_binding_write",
        )
        self.assertEqual(60, faulted.returncode, faulted.stdout + faulted.stderr)
        self.assertEqual("injected_final_evidence_fault", fault["classification"])
        self.assertTrue((run_dir / "review/acceptance/input-binding.json").is_file())

        restored, rejection = self._invoke_prepare(run_dir, control_root, evidence)

        self.assertEqual(20, restored.returncode)
        self.assertEqual(
            {
                "first_failing_gate": "final_evidence_page_reconciliation",
                "error_code": "final_evidence_page_publication_restored",
            },
            {
                "first_failing_gate": rejection["data"].get("first_failing_gate"),
                "error_code": rejection["data"].get("error_code"),
            },
        )
        self.assertFalse((run_dir / "review/acceptance/input-binding.json").is_file())

        completed, replay = self._invoke_prepare(run_dir, control_root, evidence)

        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        self.assertFalse(replay["data"]["idempotent"])


def load_tests(
    loader: unittest.TestLoader,
    _standard_tests: unittest.TestSuite,
    _pattern: str | None,
) -> unittest.TestSuite:
    suite = loader.loadTestsFromTestCase(Issue13FinalEvidenceCliTests)
    if suite.countTestCases() != 8:
        raise AssertionError(
            f"Issue 13 Final Evidence discovery must contain exactly 8 tests; "
            f"found {suite.countTestCases()}"
        )
    return suite


if __name__ == "__main__":
    unittest.main()
