"""Provider-owned single-section Production fixture with real MiKTeX closure."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from unittest.mock import patch
import uuid


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import tests.video_workflow.test_source_publication_integration as source_fixture  # noqa: E402
from video2pdf_workflow_kernel.content_production import ContentProduction  # noqa: E402
from video2pdf_workflow_kernel.guarded_compile import (  # noqa: E402
    GuardedCompileProvider,
    runtime_policy_for_miktex,
)
from video2pdf_workflow_kernel.kernel import VideoWorkflowKernel  # noqa: E402
from video2pdf_workflow_kernel.utils import (  # noqa: E402
    canonical_json_bytes,
    read_json,
    sha256_file,
)


RUNTIME_POLICY_ENV = "VIDEO2PDF_ISSUE135_RUNTIME_POLICY_JSON"
VALID_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _attempt(run_dir: Path, envelope: dict, outputs: dict[str, bytes]) -> str:
    """Write one worker-owned attempt; Production remains the promotion owner."""

    attempt_id = uuid.uuid4().hex[:24]
    attempt_dir = (
        run_dir
        / "workflow/tasks"
        / envelope["task_id"]
        / "attempts"
        / attempt_id
    )
    attempt_dir.mkdir(parents=True)
    for relative, payload in outputs.items():
        target = attempt_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    record = {
        "schema_name": "production-task-attempt",
        "schema_version": "1.0.0",
        "kernel_version": "2.0.0",
        "task_id": envelope["task_id"],
        "attempt_id": attempt_id,
        "claim_generation": envelope["claim_generation"],
        "claim_token": envelope["claim_token"],
        "envelope_sha256": sha256_file(
            run_dir
            / "workflow/tasks"
            / envelope["task_id"]
            / "envelope.json"
        ),
        "outputs": [
            {
                "path": relative,
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
            for relative, payload in sorted(outputs.items())
        ],
    }
    (attempt_dir / "attempt.json").write_bytes(canonical_json_bytes(record))
    return attempt_id


def _outline_payload() -> bytes:
    return canonical_json_bytes(
        {
            "schema_name": "outline-contract",
            "schema_version": "1.0.0",
            "kernel_version": "2.0.0",
            "article_title": "One guarded section",
            "terminology": [
                {
                    "term": "closure",
                    "definition": "declared inputs actually read",
                }
            ],
            "sections": [{"section_id": "section_01", "title": "Core claim"}],
            "required_figure_slots": [
                {
                    "slot_id": "figure_01",
                    "section_id": "section_01",
                    "teaching_purpose": "Show the dependency boundary",
                    "placement_marker": "% FIGURE_SLOT:figure_01",
                }
            ],
            "compile_support": {
                "document_class": "course",
                "class_content": (
                    "\\NeedsTeXFormat{LaTeX2e}\n"
                    "\\ProvidesClass{course}\n"
                    "\\LoadClass[11pt]{article}\n"
                ),
                "style_name": "local",
                "style_content": (
                    "\\ProvidesPackage{local}\n"
                    "\\RequirePackage{float}\n"
                ),
                "bibliography_name": "refs.bib",
                "bibliography_content": "@misc{fixture,title={Fixture}}\n",
            },
        }
    )


def _pyramid_payload(envelope: dict) -> bytes:
    return canonical_json_bytes(
        {
            "schema_name": "pyramid-evaluation-binding",
            "schema_version": "1.0.0",
            "kernel_version": "2.0.0",
            "target": envelope["pyramid_target"],
            "evaluation_context": envelope["evaluation_context"],
            "status": "pass",
        }
    )


def _valid_source_inventory_builder(original_builder):
    """Return a fixture builder that binds valid PNG bytes before publication."""

    def build(root: Path, *args, **kwargs) -> dict:
        inventory = original_builder(root, *args, **kwargs)
        cover = next(
            candidate
            for candidate in inventory["candidates"]
            if candidate["role"] == "cover"
        )
        candidate_root = Path(cover["staged_path"]).parent
        relative = (candidate_root / "cover.png").as_posix()
        target = root / relative
        target.write_bytes(VALID_PNG)
        cover.update(
            {
                "staged_path": relative,
                "sha256": hashlib.sha256(VALID_PNG).hexdigest(),
                "size_bytes": len(VALID_PNG),
                "media_type": "image/png",
            }
        )
        cover["technical_probe"]["codec_names"] = ["png"]
        return inventory

    return build


def _runtime_policy(run_dir: Path) -> dict:
    reference_value = os.environ.get(RUNTIME_POLICY_ENV)
    if not reference_value:
        raise RuntimeError(f"{RUNTIME_POLICY_ENV} is required")
    reference_path = Path(reference_value).resolve()
    if not reference_path.is_file():
        raise RuntimeError(f"runtime policy reference is unavailable: {reference_path}")
    reference = read_json(reference_path)
    if (
        reference.get("schema_name") != "compile-runtime-policy"
        or reference.get("policy_id") != "miktex-xelatex-runtime"
        or reference.get("runtime_family") != "miktex"
    ):
        raise RuntimeError("runtime policy reference is not the registered MiKTeX policy")
    inventory_binding = reference.get("package_inventory", {})
    inventory_source = Path(str(inventory_binding.get("path", ""))).resolve()
    if (
        not inventory_source.is_file()
        or sha256_file(inventory_source) != inventory_binding.get("sha256")
    ):
        raise RuntimeError("runtime policy package inventory binding is stale")
    font_bindings = reference.get("system_fonts")
    if not isinstance(font_bindings, list) or not font_bindings:
        raise RuntimeError("runtime policy reference has no registered system fonts")
    fonts: list[Path] = []
    for item in font_bindings:
        font = Path(str(item.get("path", ""))).resolve()
        if not font.is_file() or sha256_file(font) != item.get("sha256"):
            raise RuntimeError(f"runtime policy system font binding is stale: {font}")
        fonts.append(font)

    private_inventory = run_dir / "workflow/runtime/miktex-package-inventory.json"
    private_inventory.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(inventory_source, private_inventory)
    if sha256_file(private_inventory) != inventory_binding["sha256"]:
        raise RuntimeError("private runtime inventory copy changed bytes")
    policy = runtime_policy_for_miktex(
        package_inventory=private_inventory,
        system_fonts=fonts,
    )
    # This is a read-only preflight over the generated policy and exact local files.
    GuardedCompileProvider(run_dir)._validate_runtime_policy(policy)
    return policy


def _require_roles(plan: dict, expected: set[str]) -> list[dict]:
    tasks = plan.get("runnable_tasks", [])
    actual = {task.get("role") for task in tasks}
    if actual != expected:
        raise AssertionError(f"expected runnable roles {expected}, got {actual}")
    return tasks


def _advance(
    kernel: VideoWorkflowKernel,
    run_dir: Path,
    envelope: dict,
    outputs: dict[str, bytes],
    *,
    compile_runtime_policy: dict | None = None,
) -> dict:
    result = kernel.production_advance(
        run_dir,
        envelope["task_id"],
        _attempt(run_dir, envelope, outputs),
        compile_runtime_policy=compile_runtime_policy,
    )
    if result.get("classification") != "production_advanced" and not (
        envelope.get("role") == "pyramid_main"
        and result.get("classification") == "diagnostic_compile_ready"
    ):
        raise AssertionError(f"Production advance failed: {result}")
    return result


def complete_real_single_section_production(
    *, writer_text: bytes = b"Declared inputs establish closure."
) -> tuple[VideoWorkflowKernel, Path]:
    """Complete a private provider-owned Run through real diagnostic compilation.

    The environment supplies only a reference Runtime Policy. Its registered package
    inventory is copied byte-for-byte into this Run and a new policy is produced by
    ``runtime_policy_for_miktex``. No actual video Run or Production authority is read.
    """

    try:
        writer_text.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("writer_text must be UTF-8") from exc

    original_inventory_builder = source_fixture.build_inventory
    # The patch changes test input construction before candidate inventory and Run
    # authority publication. It does not replace a provider, predicate, or compiler.
    with patch.object(
        source_fixture,
        "build_inventory",
        _valid_source_inventory_builder(original_inventory_builder),
    ):
        kernel, run_dir, _ = source_fixture.build_decision_ready_authority()
    kernel.finalize_production_source(
        run_dir,
        published_at="2026-09-07T00:00:00+00:00",
    )

    outline = _require_roles(kernel.production_plan(run_dir), {"outline"})[0]
    _advance(kernel, run_dir, outline, {"outline.json": _outline_payload()})

    outline_gate = _require_roles(
        kernel.production_plan(run_dir), {"pyramid_outline"}
    )[0]
    _advance(
        kernel,
        run_dir,
        outline_gate,
        {"pyramid-report.json": _pyramid_payload(outline_gate)},
    )

    tasks = _require_roles(kernel.production_plan(run_dir), {"writer", "figure"})
    writer = next(task for task in tasks if task["role"] == "writer")
    figure = next(task for task in tasks if task["role"] == "figure")
    writer_result = canonical_json_bytes(
        {
            "schema_name": "writer-result",
            "schema_version": "1.0.0",
            "section_id": "section_01",
            "new_figure_candidates": [],
        }
    )
    _advance(
        kernel,
        run_dir,
        writer,
        {
            "section_01.tex": (
                b"\\section{Core claim}\n"
                + writer_text
                + b"\n% FIGURE_SLOT:figure_01\n"
            ),
            "writer-result.json": writer_result,
        },
    )

    figure_manifest = {
        "schema_name": "figure-manifest",
        "schema_version": "1.0.0",
        "kernel_version": "2.0.0",
        "slot_id": "figure_01",
        "section_id": "section_01",
        "asset_path": "figures/figure_01.png",
        "asset_sha256": hashlib.sha256(VALID_PNG).hexdigest(),
        "caption": "Declared and observed compile inputs.",
        "source": {"kind": "source_timestamp", "value": "00:00:01"},
        "slot_contribution_path": "work/figures/figure_01.tex",
    }
    contribution = ContentProduction._figure_contribution(figure_manifest)
    figure_manifest["slot_contribution_sha256"] = hashlib.sha256(
        contribution
    ).hexdigest()
    _advance(
        kernel,
        run_dir,
        figure,
        {
            "figure_01.png": VALID_PNG,
            "figure-manifest.json": canonical_json_bytes(figure_manifest),
            "figure_01.tex": contribution,
        },
    )

    section_gate = _require_roles(
        kernel.production_plan(run_dir), {"pyramid_section"}
    )[0]
    _advance(
        kernel,
        run_dir,
        section_gate,
        {"pyramid-report.json": _pyramid_payload(section_gate)},
    )

    main_gate = _require_roles(kernel.production_plan(run_dir), {"pyramid_main"})[0]
    result = _advance(
        kernel,
        run_dir,
        main_gate,
        {"pyramid-report.json": _pyramid_payload(main_gate)},
        compile_runtime_policy=_runtime_policy(run_dir),
    )
    report_path = Path(result["compile_report_path"])
    report = read_json(report_path)
    if (
        report.get("status") != "pass"
        or report.get("mode") != "diagnostic"
        or report.get("delivery_authority") is not False
        or report.get("dependency_closure", {}).get("complete") is not True
    ):
        raise AssertionError("real MiKTeX diagnostic compile did not close")
    return kernel, run_dir


__all__ = ["complete_real_single_section_production"]
