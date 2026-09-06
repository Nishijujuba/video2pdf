from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import sys
import unittest

from tests.video_workflow import test_acceptance_v2 as acceptance_tests
from tests.video_workflow import test_issue100_final_evidence_page_publication as issue100_tests
from tests.video_workflow import test_issue13_candidate_confirmation as candidate_tests
from tests.video_workflow import test_issue13_final_evidence_cli as final_evidence_tests
from tests.video_workflow import test_single_section_production as production_tests
from tests.video_workflow.test_precompile_quality import semantic_dependencies
from video2pdf_workflow_kernel.acceptance_v2 import (
    changed_artifact_logical_ids,
    require_exact_changed_artifact_logical_ids,
)
from video2pdf_workflow_kernel.errors import AcceptanceV2Rejected


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SYSTEM_FONT = Path("C:/Windows/Fonts/arial.ttf")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_sha(value: object) -> str:
    return hashlib.sha256(
        (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    ).hexdigest()


def _write_json(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return path


def _complete_production(
    fixture: final_evidence_tests.Issue13FinalEvidenceCliTests,
    run_dir: Path,
) -> None:
    plan = fixture._require_ok("production-plan", "--run-dir", str(run_dir))
    outline_payload = json.loads(
        production_tests.SingleSectionProductionTests._outline_payload().decode("utf-8")
    )
    # The basename is valid Outline compile support and Production places its
    # stem in main.tex's generated bibliography command. The registered fake
    # engine then models a stable multi-pass TOC without a source rewrite.
    outline_payload["compile_support"]["bibliography_name"] = (
        "refs_VIDEO2PDF_FIXTURE_STABLE_TOC.bib"
    )
    fixture._advance(
        run_dir,
        plan["data"]["runnable_tasks"][0],
        {"outline.json": json.dumps(outline_payload, sort_keys=True).encode("utf-8")},
    )
    plan = fixture._require_ok("production-plan", "--run-dir", str(run_dir))
    outline_gate = plan["data"]["runnable_tasks"][0]
    fixture._advance(
        run_dir,
        outline_gate,
        {"pyramid-report.json": production_tests.SingleSectionProductionTests._pyramid_payload(outline_gate)},
    )
    tasks = fixture._require_ok("production-plan", "--run-dir", str(run_dir))["data"]["runnable_tasks"]
    writer = next(task for task in tasks if task["role"] == "writer")
    figure = next(task for task in tasks if task["role"] == "figure")
    writer_result = json.dumps(
        {"schema_name": "writer-result", "schema_version": "1.0.0", "section_id": "section_01", "new_figure_candidates": []},
        sort_keys=True,
    ).encode("utf-8")
    fixture._advance(
        run_dir,
        writer,
        {"section_01.tex": b"\\section{Core claim}\n% FIGURE_SLOT:figure_01\n", "writer-result.json": writer_result},
    )
    contribution = (
        b"\\begin{figure}[H]\n\\centering\n"
        b"\\includegraphics[width=0.76\\linewidth,height=0.34\\textheight,keepaspectratio]{figures/figure_01}\n"
        b"\\caption{Bound evidence.}\n"
        b"\\par\\small Source (source\\_timestamp): 00:00:01\n\\end{figure}\n"
    )
    image = final_evidence_tests._real_figure_png()
    figure_manifest = json.dumps(
        {
            "schema_name": "figure-manifest",
            "schema_version": "1.0.0",
            "kernel_version": "2.0.0",
            "slot_id": "figure_01",
            "section_id": "section_01",
            "asset_path": "figures/figure_01.png",
            "asset_sha256": hashlib.sha256(image).hexdigest(),
            "caption": "Bound evidence.",
            "source": {"kind": "source_timestamp", "value": "00:00:01"},
            "slot_contribution_path": "work/figures/figure_01.tex",
            "slot_contribution_sha256": hashlib.sha256(contribution).hexdigest(),
        },
        sort_keys=True,
    ).encode("utf-8")
    fixture._advance(
        run_dir,
        figure,
        {"figure_01.png": image, "figure-manifest.json": figure_manifest, "figure_01.tex": contribution},
    )
    section_gate = fixture._require_ok("production-plan", "--run-dir", str(run_dir))["data"]["runnable_tasks"][0]
    fixture._advance(
        run_dir,
        section_gate,
        {"pyramid-report.json": production_tests.SingleSectionProductionTests._pyramid_payload(section_gate)},
    )
    main_gate = fixture._require_ok("production-plan", "--run-dir", str(run_dir))["data"]["runnable_tasks"][0]
    attempt_id = fixture._attempt(
        run_dir,
        main_gate,
        {"pyramid-report.json": production_tests.SingleSectionProductionTests._pyramid_payload(main_gate)},
    )
    from video2pdf_workflow_kernel.guarded_compile import runtime_policy_for_fixture

    policy = runtime_policy_for_fixture(
        run_dir=run_dir,
        engine_executable=Path(sys.executable),
        engine_prefix_args=[str(PROJECT_ROOT / "tests/video_workflow/fixtures/guarded-compile/fake_xelatex.py")],
        system_fonts=[SYSTEM_FONT],
    )
    policy_path = run_dir / "workflow" / "fixture-compile-runtime-policy.json"
    _write_json(policy_path, policy)
    fixture._require_ok(
        "production-advance",
        "--run-dir", str(run_dir),
        "--task-id", main_gate["task_id"],
        "--attempt-id", attempt_id,
        "--compile-runtime-policy", str(policy_path),
    )
    completed = fixture._require_ok("production-plan", "--run-dir", str(run_dir))
    fixture.assertEqual("production_complete", completed["classification"])


def _quality_evidence(
    fixture: final_evidence_tests.Issue13FinalEvidenceCliTests,
    run_dir: Path,
    attempt: int,
) -> dict[str, Path]:
    """Materialize one complete PRE -> Final Compile -> RTR generation."""
    quality = run_dir / "review" / "quality" / f"issue131-attempt-{attempt:02d}"
    final_workspace = run_dir / "review" / "acceptance" / f"final-evidence-attempt-{attempt:02d}"
    state = json.loads((run_dir / "workflow" / "production-state.json").read_text(encoding="utf-8"))
    main_generation = state["artifacts"]["integrated_main"]
    main_tex = run_dir / main_generation["path"]
    artifact_specs = [
        ("integrated_main_tex", main_generation, main_tex, "main.tex"),
    ]
    for logical_id, default_staging_path in (
        ("local_class", "course.cls"),
        ("local_style", "local.sty"),
        ("bibliography", None),
        ("figure_asset_figure_01", "figures/figure_01.png"),
        ("integrated_section_01", "section_01.tex"),
    ):
        generation = state["artifacts"].get(logical_id)
        if generation is not None:
            staging_path = (
                Path(generation["path"]).name
                if default_staging_path is None
                else default_staging_path
            )
            artifact_specs.append((logical_id, generation, run_dir / generation["path"], staging_path))
    generations = {
        "schema_name": "precompile-artifact-generation-set",
        "schema_version": "1.0.0",
        "generation_set_id": f"issue131-production-attempt-{attempt:02d}",
        "producer_ids": [main_generation["producer"]],
        "artifacts": [
            {"logical_id": logical_id, "generation": generation["generation"], "sha256": generation["sha256"]}
            for logical_id, generation, _path, _staging in artifact_specs
        ],
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
        "declared_text": "\\section{Core claim}",
        "text_sha256": hashlib.sha256(b"\\section{Core claim}").hexdigest(),
        "applicable_rule_ids": ["no_meta_writing_content"],
    }
    item["item_sha256"] = _canonical_sha(item)
    inventory = {
        "schema_name": "reader-facing-text-inventory",
        "schema_version": "1.0.0",
        "inventory_id": f"issue131-reader-text-attempt-{attempt:02d}",
        "language_profile_id": "zh-hans",
        "delivery_glossary": None,
        "generation_set_sha256": generations["generation_set_sha256"],
        "declared_surface": [{"region_id": "main.body", "kind": "paragraph"}],
        "items": [item],
        "coverage_ledger": [{"region_id": "main.body", "item_id": "main.body", "status": "covered"}],
        "extractors": [{"extractor_id": "latex-reader-text-extractor", "extractor_sha256": "9" * 64}],
    }
    inventory["reader_text_set_sha256"] = _canonical_sha(
        [{"item_id": item["item_id"], "kind": item["kind"], "representation": item["representation"], "text_sha256": item["text_sha256"]}]
    )
    inventory["inventory_sha256"] = _canonical_sha(inventory)
    generation_path = _write_json(quality / "inputs" / "generations.json", generations)
    inventory_path = _write_json(quality / "inputs" / "inventory.json", inventory)
    dependencies_path = _write_json(quality / "inputs" / "semantic-dependencies.json", semantic_dependencies())
    minute = 10 * attempt
    fixture._require_ok(
        "delivery-quality-precompile-prepare",
        "--workspace-root", str(quality),
        "--inventory", str(inventory_path),
        "--artifact-generations", str(generation_path),
        "--semantic-dependencies", str(dependencies_path),
        "--prepared-at", f"2026-09-07T01:{minute:02d}:00Z",
    )
    for owner in ("source-faithfulness-reviewer", "writing-quality-reviewer", "pyramid-reviewer"):
        skeleton_path = quality / "reviewers" / owner / "input" / "review-skeleton.json"
        skeleton = json.loads(skeleton_path.read_text(encoding="utf-8"))
        patch = {
            "schema_name": "precompile-judgment-patch",
            "schema_version": "1.0.0",
            "task_id": skeleton["task_id"],
            "owner": owner,
            "skeleton_sha256": skeleton["skeleton_sha256"],
            "generation_set_sha256": skeleton["generation_set_sha256"],
            "reviewer": {"reviewer_id": f"independent-{owner}", "runtime_sha256": "b" * 64, "independent_from_generation_producers": True},
            "results": [
                {"result_key": required["result_key"], "decision": "pass", "evidence_locator": f"artifact:{required['result_key']}", "repair_write_set": []}
                for required in skeleton["required_results"]
            ],
            "contract_gaps": [],
        }
        patch["patch_sha256"] = _canonical_sha(patch)
        patch_path = _write_json(quality / "inputs" / f"{owner}.patch.json", patch)
        fixture._require_ok(
            "delivery-quality-precompile-patch-commit",
            "--workspace-root", str(quality),
            "--owner", owner,
            "--patch", str(patch_path),
            "--committed-at", f"2026-09-07T01:{minute + 1:02d}:00Z",
        )
    fixture._require_ok(
        "delivery-quality-precompile-materialize",
        "--workspace-root", str(quality),
        "--provider-id", "precompile-quality-provider",
        "--provider-version", "1.0.0",
        "--materialized-at", f"2026-09-07T01:{minute + 2:02d}:00Z",
    )
    fixture._require_ok(
        "delivery-quality-seal",
        "--workspace-root", str(quality),
        "--sealed-at", f"2026-09-07T01:{minute + 3:02d}:00Z",
    )
    seal_path = quality / "precompile-text-seal.json"
    seal = json.loads(seal_path.read_text(encoding="utf-8"))
    runtime_policy_path = run_dir / "workflow" / "compile-runtime-policy.json"
    compile_manifest = {
        "schema_name": "final-compile-manifest",
        "schema_version": "1.0.0",
        "activation_status": "target_only",
        "mode": "final",
        "precompile_text_seal_sha256": seal["seal_sha256"],
        "entries": [
            {
                "logical_id": logical_id,
                "generation": generation["generation"],
                "sha256": generation["sha256"],
                "source_path": str(path),
                "staging_path": PurePosixPath(staging).as_posix(),
            }
            for logical_id, generation, path, staging in artifact_specs
        ],
        "approved_runtime_inputs": [
            {"path": str(SYSTEM_FONT), "sha256": _sha256(SYSTEM_FONT), "classification": "registered_system_font"}
        ],
        "runtime_policy": {"path": str(runtime_policy_path.resolve()), "sha256": _sha256(runtime_policy_path)},
    }
    compile_manifest["manifest_sha256"] = _canonical_sha(compile_manifest)
    compile_manifest_path = _write_json(quality / "inputs" / "final-compile-manifest.json", compile_manifest)
    fixture._require_ok(
        "delivery-quality-final-compile",
        "--input-track", "kernel",
        "--precompile-workspace-root", str(quality),
        "--compile-manifest", str(compile_manifest_path),
        "--compiler-adapter", str(PROJECT_ROOT / "scripts/guarded_final_compile_adapter.py"),
        "--runtime-policy", str(runtime_policy_path),
        "--workspace-root", str(final_workspace),
        "--compiled-at", f"2026-09-07T01:{minute + 4:02d}:00Z",
    )
    evidence = {
        "main_tex": main_tex,
        "precompile_quality_report": quality / "precompile-quality-report.json",
        "precompile_text_seal": seal_path,
        "final_compile_manifest": compile_manifest_path,
        "final_compile_report": final_workspace / "final-compile-report.json",
        "final_artifact_seal": final_workspace / "final-artifact-seal.json",
        "final_pdf": final_workspace / "adapter-output" / "final.pdf",
        "render_evidence_manifest": final_workspace / "render-evidence-manifest.json",
        "rendered_text_inventory": final_workspace / "adapter-output" / "rendered-text-object-inventory.json",
        "text_origin_manifest": final_workspace / "text-origin-manifest.json",
        "reconciliation": final_workspace / "rendered-text-reconciliation-report.json",
    }
    fixture._require_ok(
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
        "--reconciled-at", f"2026-09-07T01:{minute + 5:02d}:00Z",
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


def _transition(
    fixture: final_evidence_tests.Issue13FinalEvidenceCliTests,
    transition_fixture: candidate_tests.Issue13CandidateConfirmationTests,
    run_dir: Path,
    from_stage: str,
    to_stage: str,
    artifacts: dict[str, Path],
    at: str,
) -> None:
    evidence_path = transition_fixture._transition_evidence(
        run_dir, from_stage=from_stage, to_stage=to_stage, artifacts=artifacts
    )
    run = json.loads((run_dir / "workflow" / "run.json").read_text(encoding="utf-8"))
    fixture._require_ok(
        "delivery-transition",
        "--run-dir", str(run_dir),
        "--from-stage", from_stage,
        "--to-stage", to_stage,
        "--session-id", "session-issue100",
        "--expected-run-revision", str(run["coordination_revision"]),
        "--expected-ownership-generation", str(run["delivery"]["ownership"]["generation"]),
        "--evidence", str(evidence_path),
        "--transitioned-at", at,
    )


def _materialized_failed_acceptance(
    test: unittest.TestCase,
) -> dict[str, object]:
    """Build the public coherent failed authority shared by Issue #131 cases."""
    fixture = issue100_tests.Issue100FinalEvidencePagePublicationTests(
        methodName="test_final_evidence_publishes_pages_before_reviewer_dispatch"
    )._fixture()
    run_dir, control_root = fixture._source_ready_v4_run()
    _complete_production(fixture, run_dir)
    transition_fixture = candidate_tests.Issue13CandidateConfirmationTests(
        methodName="test_candidate_activation_rejects_generating_candidate"
    )
    evidence_1 = _quality_evidence(fixture, run_dir, 1)
    _transition(
        fixture,
        transition_fixture,
        run_dir,
        "generating",
        "ready_for_delivery",
        {key: evidence_1[key] for key in ("final_pdf", "main_tex", "final_compile_report", "render_evidence_manifest")},
        "2026-09-07T02:00:00Z",
    )
    prepared_command, prepared_1 = fixture._invoke_prepare(run_dir, control_root, evidence_1)
    test.assertEqual(0, prepared_command.returncode, prepared_command.stdout + prepared_command.stderr)
    binding_path_1 = Path(prepared_1["data"]["input_binding_path"])
    test.assertEqual(run_dir / "review" / "acceptance" / "input-binding.json", binding_path_1)
    fixture._require_ok("acceptance-final-authority-publish", "--input-binding", str(binding_path_1))
    acceptance_root = run_dir / "review" / "acceptance"
    prepared_acceptance = fixture._require_ok(
        "acceptance-prepare",
        "--workspace-root", str(acceptance_root),
        "--input-binding", str(binding_path_1),
        "--attempt-number", "1",
        "--prepared-at", "2026-09-07T02:01:00Z",
        "--coordinator-session", "session-issue100",
    )
    execution_1 = Path(prepared_acceptance["data"]["execution_root"])
    acceptance_fixture = acceptance_tests.AcceptanceV2CliTests(
        methodName="test_repair_requires_fresh_artifact_generation_and_bounds_three_failures"
    )
    patch_path = acceptance_fixture.patch(acceptance_root, decision="fail")
    patch = json.loads(patch_path.read_text(encoding="utf-8"))
    patch.pop("patch_sha256")
    _write_json(patch_path, patch)
    fixture._require_ok(
        "acceptance-patch-commit",
        "--workspace-root", str(acceptance_root),
        "--dimension", "visual_quality",
        "--patch", str(patch_path),
        "--committed-at", "2026-09-07T02:02:00Z",
    )
    materialized = fixture._require_ok(
        "acceptance-materialize",
        "--workspace-root", str(acceptance_root),
        "--provider-id", "acceptance-v2-provider",
        "--provider-version", "1.0.0",
        "--materialized-at", "2026-09-07T02:03:00Z",
    )
    test.assertEqual("fail", materialized["data"]["overall_status"])
    test.assertEqual("repair_required", materialized["data"]["routing_state"])
    return {
        "fixture": fixture,
        "run_dir": run_dir,
        "control_root": control_root,
        "transition_fixture": transition_fixture,
        "evidence_1": evidence_1,
        "binding_path_1": binding_path_1,
        "acceptance_root": acceptance_root,
        "execution_1": execution_1,
        "acceptance_fixture": acceptance_fixture,
    }


def _fresh_successor_inputs(test: unittest.TestCase) -> dict[str, object]:
    """Advance one coherent failed authority to fresh public successor inputs."""
    case = _materialized_failed_acceptance(test)
    fixture = case["fixture"]
    run_dir = case["run_dir"]
    transition_fixture = case["transition_fixture"]
    acceptance_root = case["acceptance_root"]
    execution_1 = case["execution_1"]
    evidence_1 = case["evidence_1"]
    immutable_binding_1 = execution_1 / "input-binding.json"
    predecessor = json.loads(immutable_binding_1.read_text(encoding="utf-8"))
    retained_paths = [
        immutable_binding_1,
        next(execution_1.glob("reports/*/acceptance_report.json")),
        next(execution_1.glob("reports/*/attempt-record.json")),
        next(execution_1.glob("reports/*/repair-ledger.json")),
    ]
    prior_page_bytes = {
        path.name: path.read_bytes()
        for path in (acceptance_root / "rendered_pages").glob("page_*.png")
    }
    _transition(
        fixture,
        transition_fixture,
        run_dir,
        "ready_for_delivery",
        "generating",
        {"acceptance_report": acceptance_root / "acceptance_report.json"},
        "2026-09-07T02:04:00Z",
    )
    state = json.loads((run_dir / "workflow" / "production-state.json").read_text(encoding="utf-8"))
    writer_claim = state["claims"]["writer-section-01"]
    replacement = fixture._require_ok(
        "production-plan",
        "--run-dir", str(run_dir),
        "--supersede-task-id", writer_claim["task_id"],
        "--expected-claim-generation", str(writer_claim["claim_generation"]),
    )["data"]["runnable_tasks"][0]
    writer_result = json.dumps(
        {"schema_name": "writer-result", "schema_version": "1.0.0", "section_id": "section_01", "new_figure_candidates": []},
        sort_keys=True,
    ).encode("utf-8")
    fixture._advance(
        run_dir,
        replacement,
        {
            "section_01.tex": b"\\section{Core claim}\nDisclosure pagination repaired.\n% FIGURE_SLOT:figure_01\n",
            "writer-result.json": writer_result,
        },
    )
    stale_section_gate = fixture._require_ok(
        "production-plan", "--run-dir", str(run_dir)
    )["data"]["runnable_tasks"][0]
    section_gate = fixture._require_ok(
        "production-plan",
        "--run-dir", str(run_dir),
        "--supersede-task-id", stale_section_gate["task_id"],
        "--expected-claim-generation", str(stale_section_gate["claim_generation"]),
    )["data"]["runnable_tasks"][0]
    fixture._advance(
        run_dir,
        section_gate,
        {"pyramid-report.json": production_tests.SingleSectionProductionTests._pyramid_payload(section_gate)},
    )
    stale_main_gate = fixture._require_ok(
        "production-plan", "--run-dir", str(run_dir)
    )["data"]["runnable_tasks"][0]
    main_gate = fixture._require_ok(
        "production-plan",
        "--run-dir", str(run_dir),
        "--supersede-task-id", stale_main_gate["task_id"],
        "--expected-claim-generation", str(stale_main_gate["claim_generation"]),
    )["data"]["runnable_tasks"][0]
    main_attempt = fixture._attempt(
        run_dir,
        main_gate,
        {"pyramid-report.json": production_tests.SingleSectionProductionTests._pyramid_payload(main_gate)},
    )
    fixture._require_ok(
        "production-advance",
        "--run-dir", str(run_dir),
        "--task-id", main_gate["task_id"],
        "--attempt-id", main_attempt,
        "--compile-runtime-policy", str(run_dir / "workflow" / "compile-runtime-policy.json"),
    )
    test.assertEqual(
        "production_complete",
        fixture._require_ok("production-plan", "--run-dir", str(run_dir))["classification"],
    )
    evidence_2 = _quality_evidence(fixture, run_dir, 2)
    _transition(
        fixture,
        transition_fixture,
        run_dir,
        "generating",
        "ready_for_delivery",
        {key: evidence_2[key] for key in ("final_pdf", "main_tex", "final_compile_report", "render_evidence_manifest")},
        "2026-09-07T02:10:00Z",
    )
    return {
        **case,
        "predecessor": predecessor,
        "predecessor_seal": json.loads(evidence_1["precompile_text_seal"].read_text(encoding="utf-8")),
        "retained_paths": retained_paths,
        "retained_bytes": {path: path.read_bytes() for path in retained_paths},
        "prior_page_bytes": prior_page_bytes,
        "evidence_2": evidence_2,
    }


class Issue131AcceptanceSuccessorLineageTests(unittest.TestCase):
    def test_repair_rejects_predecessor_outside_immutable_execution_authority(self) -> None:
        case = _fresh_successor_inputs(self)
        fixture = case["fixture"]
        run_dir = case["run_dir"]
        control_root = case["control_root"]
        evidence_2 = case["evidence_2"]
        acceptance_root = case["acceptance_root"]
        execution_1 = case["execution_1"]
        successor_command, prepared_2 = fixture._invoke_prepare(
            run_dir, control_root, evidence_2
        )
        self.assertEqual(
            0,
            successor_command.returncode,
            successor_command.stdout + successor_command.stderr,
        )
        binding_path_2 = Path(prepared_2["data"]["input_binding_path"])
        fixture._require_ok(
            "acceptance-final-authority-publish", "--input-binding", str(binding_path_2)
        )
        outside = acceptance_root.parent / "unowned-execution"
        shutil.copytree(execution_1, outside)
        retained_paths = [path for path in execution_1.rglob("*") if path.is_file()]
        retained_bytes = {path: path.read_bytes() for path in retained_paths}
        current = json.loads((acceptance_root / "current.json").read_text(encoding="utf-8"))
        _write_json(
            acceptance_root / "current.json",
            {**current, "execution_root": str(outside.resolve())},
        )

        rejected, error = fixture._cli(
            "acceptance-repair-prepare",
            "--workspace-root", str(acceptance_root),
            "--input-binding", str(binding_path_2),
            "--prepared-at", "2026-09-07T02:11:00Z",
            "--coordinator-session", "session-issue100",
        )
        self.assertEqual(30, rejected.returncode)
        self.assertEqual(
            {
                "first_failing_gate": "repair_admission",
                "error_code": "acceptance_repair_history_invalid",
            },
            {
                "first_failing_gate": error["data"]["first_failing_gate"],
                "error_code": error["data"]["error_code"],
            },
        )
        self.assertEqual(retained_bytes, {path: path.read_bytes() for path in retained_paths})
        _write_json(acceptance_root / "current.json", current)
        admitted, result = fixture._cli(
            "acceptance-repair-prepare",
            "--workspace-root", str(acceptance_root),
            "--input-binding", str(binding_path_2),
            "--prepared-at", "2026-09-07T02:11:00Z",
            "--coordinator-session", "session-issue100",
        )
        self.assertEqual(0, admitted.returncode, admitted.stdout + admitted.stderr)
        prepared_execution = json.loads(
            (Path(result["data"]["execution_root"]) / "execution.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(2, prepared_execution["attempt_number"])

    def test_complete_record_change_set_covers_add_remove_and_metadata_change(self) -> None:
        predecessor = [
            {"logical_id": "final_pdf", "path": "old/final.pdf", "sha256": "1" * 64},
            {"logical_id": "delivery_glossary", "path": "old/glossary.json", "sha256": "2" * 64},
            {"logical_id": "main_tex", "path": "old/main.tex", "sha256": "3" * 64},
        ]
        successor = [
            {"logical_id": "final_pdf", "path": "new/final.pdf", "sha256": "1" * 64},
            {"logical_id": "allowed_artifacts_manifest", "path": "new/allowed.json", "sha256": "4" * 64},
            {"logical_id": "main_tex", "path": "old/main.tex", "sha256": "3" * 64},
        ]

        self.assertEqual(
            frozenset({"final_pdf", "delivery_glossary", "allowed_artifacts_manifest"}),
            changed_artifact_logical_ids(predecessor, successor),
        )

    def test_repair_rejects_omitted_and_invented_changed_artifact_ids(self) -> None:
        predecessor = [
            {"logical_id": "final_pdf", "path": "old/final.pdf", "sha256": "1" * 64},
            {"logical_id": "delivery_glossary", "path": "old/glossary.json", "sha256": "2" * 64},
        ]
        successor = [
            {"logical_id": "final_pdf", "path": "new/final.pdf", "sha256": "1" * 64},
        ]

        for declared in (["final_pdf"], ["final_pdf", "delivery_glossary", "invented"]):
            with self.subTest(declared=declared), self.assertRaises(AcceptanceV2Rejected) as caught:
                require_exact_changed_artifact_logical_ids(declared, predecessor, successor)
            self.assertEqual("repair_generation", caught.exception.data["first_failing_gate"])
            self.assertEqual(
                "acceptance_changed_generation_ids_mismatch",
                caught.exception.data["error_code"],
            )

    def test_public_canonical_successor_enters_semantic_attempt_two_with_exact_lineage(self) -> None:
        # Fixture graph: Production -> PRE/Seal -> Final Compile/RTR -> Final
        # Evidence -> immutable failed Acceptance execution -> fresh Production
        # closure -> fresh PRE/Seal/Final Compile/RTR -> canonical successor.
        # Boundary: attempt 1 materialization. Observation: repair_generation /
        # acceptance_repair_generation_unchanged on current production code.
        case = _fresh_successor_inputs(self)
        fixture = case["fixture"]
        run_dir = case["run_dir"]
        control_root = case["control_root"]
        evidence_2 = case["evidence_2"]
        acceptance_root = case["acceptance_root"]
        binding_path_1 = case["binding_path_1"]
        execution_1 = case["execution_1"]
        acceptance_fixture = case["acceptance_fixture"]
        predecessor = case["predecessor"]
        predecessor_seal = case["predecessor_seal"]
        retained_paths = case["retained_paths"]
        retained_bytes = case["retained_bytes"]
        prior_page_bytes = case["prior_page_bytes"]
        interrupted, fault = fixture._invoke_prepare(
            run_dir,
            control_root,
            evidence_2,
            fault_point="after_pages_published",
        )
        self.assertEqual(60, interrupted.returncode)
        self.assertEqual("injected_final_evidence_fault", fault["classification"])
        successor_page_manifest = json.loads(
            evidence_2["render_evidence_manifest"].read_text(encoding="utf-8")
        )
        predecessor_pages = [item["page"] for item in predecessor["rendered_pages"]]
        successor_pages = [item["page"] for item in successor_page_manifest["pages"]]
        interrupted_page_bytes = {
            path.name: path.read_bytes()
            for path in (acceptance_root / "rendered_pages").glob("page_*.png")
        }
        self.assertEqual(predecessor_pages, successor_pages)
        self.assertEqual(
            {f"page_{page:04d}.png" for page in predecessor_pages},
            set(interrupted_page_bytes),
        )
        self.assertEqual(prior_page_bytes, interrupted_page_bytes)
        restored, restoration = fixture._invoke_prepare(run_dir, control_root, evidence_2)
        self.assertEqual(20, restored.returncode)
        self.assertEqual(
            {
                "first_failing_gate": "final_evidence_page_reconciliation",
                "error_code": "final_evidence_page_publication_restored",
            },
            {
                "first_failing_gate": restoration["data"]["first_failing_gate"],
                "error_code": restoration["data"]["error_code"],
            },
        )
        self.assertEqual(retained_bytes, {path: path.read_bytes() for path in retained_paths})
        restored_page_bytes = {
            path.name: path.read_bytes()
            for path in (acceptance_root / "rendered_pages").glob("page_*.png")
        }
        failed_page_roots = list(
            (run_dir / "待删除" / "final-evidence-publications").glob(
                "revision-*/failed/rendered_pages"
            )
        )
        self.assertEqual(1, len(failed_page_roots))
        failed_page_bytes = {
            path.name: path.read_bytes()
            for path in failed_page_roots[0].glob("page_*.png")
        }
        self.assertEqual(prior_page_bytes, restored_page_bytes)
        self.assertEqual(interrupted_page_bytes, failed_page_bytes)
        successor_command, prepared_2 = fixture._invoke_prepare(run_dir, control_root, evidence_2)
        self.assertEqual(0, successor_command.returncode, successor_command.stdout + successor_command.stderr)
        binding_path_2 = Path(prepared_2["data"]["input_binding_path"])
        self.assertEqual(binding_path_1, binding_path_2)
        successor = json.loads(binding_path_2.read_text(encoding="utf-8"))
        expected_changed = sorted(
            logical_id
            for logical_id in ({item["logical_id"] for item in predecessor["artifacts"]} | {item["logical_id"] for item in successor["artifacts"]})
            if next((item for item in predecessor["artifacts"] if item["logical_id"] == logical_id), None)
            != next((item for item in successor["artifacts"] if item["logical_id"] == logical_id), None)
        )
        fixture._require_ok("acceptance-final-authority-publish", "--input-binding", str(binding_path_2))
        repaired_command, repaired = fixture._cli(
            "acceptance-repair-prepare",
            "--workspace-root", str(acceptance_root),
            "--input-binding", str(binding_path_2),
            "--prepared-at", "2026-09-07T02:11:00Z",
            "--coordinator-session", "session-issue100",
        )
        self.assertEqual(0, repaired_command.returncode, repaired_command.stdout + repaired_command.stderr)
        current = json.loads((acceptance_root / "current.json").read_text(encoding="utf-8"))
        current_execution = json.loads((Path(current["execution_root"]) / "execution.json").read_text(encoding="utf-8"))
        self.assertEqual(2, current_execution["attempt_number"])
        self.assertEqual(predecessor_seal["generation_set_sha256"], successor["run"]["predecessor_generation_set_sha256"])
        self.assertEqual(expected_changed, successor["run"]["changed_generation_ids"])
        self.assertEqual(retained_bytes, {path: path.read_bytes() for path in retained_paths})
        self.assertEqual(1, len(json.loads((acceptance_root / "repair-ledger.json").read_text(encoding="utf-8"))["semantic_attempts"]))
        archived_pages = list((run_dir / "待删除" / "final-evidence-publications").glob("revision-*/previous/rendered_pages/page_*.png"))
        self.assertTrue(archived_pages)
        self.assertTrue(any(path.read_bytes() in prior_page_bytes.values() for path in archived_pages))
        patch_path_2 = acceptance_fixture.patch(acceptance_root)
        fixture._require_ok(
            "acceptance-patch-commit",
            "--workspace-root", str(acceptance_root),
            "--dimension", "visual_quality",
            "--patch", str(patch_path_2),
            "--committed-at", "2026-09-07T02:12:00Z",
        )
        materialized_2 = fixture._require_ok(
            "acceptance-materialize",
            "--workspace-root", str(acceptance_root),
            "--provider-id", "acceptance-v2-provider",
            "--provider-version", "1.0.0",
            "--materialized-at", "2026-09-07T02:13:00Z",
        )
        self.assertEqual("pass", materialized_2["data"]["overall_status"])
        successor_binding_bytes = binding_path_2.read_bytes()
        successor_revision = successor["run"]["acceptance_revision"]
        current_before_repeat = (acceptance_root / "current.json").read_bytes()
        execution_before_repeat = (Path(current["execution_root"]) / "execution.json").read_bytes()
        repeated_command, repeated = fixture._invoke_prepare(run_dir, control_root, evidence_2)
        self.assertEqual(0, repeated_command.returncode, repeated_command.stdout + repeated_command.stderr)
        self.assertTrue(repeated["data"]["idempotent"])
        self.assertEqual(str(binding_path_2), repeated["data"]["input_binding_path"])
        repeated_binding = json.loads(binding_path_2.read_text(encoding="utf-8"))
        self.assertEqual(successor_revision, repeated_binding["run"]["acceptance_revision"])
        self.assertEqual(successor_binding_bytes, binding_path_2.read_bytes())
        self.assertEqual(current_before_repeat, (acceptance_root / "current.json").read_bytes())
        self.assertEqual(execution_before_repeat, (Path(current["execution_root"]) / "execution.json").read_bytes())
        run = json.loads((run_dir / "workflow" / "run.json").read_text(encoding="utf-8"))
        bound_command, _ = fixture._cli(
            "delivery-acceptance-bind",
            "--run-dir", str(run_dir),
            "--session-id", "session-issue100",
            "--acceptance-report", str(acceptance_root / "acceptance_report.json"),
            "--expected-run-revision", str(run["coordination_revision"]),
            "--expected-ownership-generation", str(run["delivery"]["ownership"]["generation"]),
            "--bound-at", "2026-09-07T02:14:00Z",
        )
        self.assertEqual(0, bound_command.returncode, bound_command.stdout + bound_command.stderr)


if __name__ == "__main__":
    unittest.main()
