from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import time
from typing import Any

import fitz

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from video2pdf_workflow_kernel.guarded_compile import (  # noqa: E402
    GuardedCompileProvider,
    _MIKTEX_DURABLE_DIRECTORIES,
    _MIKTEX_RUNTIME_ROOTS,
)
from video2pdf_workflow_kernel.errors import CompileDependencyGap  # noqa: E402
from video2pdf_workflow_kernel.utils import (  # noqa: E402
    canonical_json_bytes,
    require_contained_path,
)


class AdapterError(RuntimeError):
    def __init__(self, message: str, *, data: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.data = data or {}


COMPILE_OUTPUT_TIMEOUT_SECONDS = 300.0
COMPILE_OUTPUT_POLL_SECONDS = 0.25


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint(value: dict[str, Any], field: str) -> str:
    return hashlib.sha256(canonical_json_bytes({k: v for k, v in value.items() if k != field})).hexdigest()


def read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AdapterError(f"{label} is unreadable") from exc
    if not isinstance(value, dict):
        raise AdapterError(f"{label} is invalid")
    return value


def write_object(path: Path, value: dict[str, Any]) -> None:
    path.write_bytes(canonical_json_bytes(value))


def contained(path: Path, label: str, *, file: bool = True) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(ROOT)
    except ValueError as exc:
        raise AdapterError(f"{label} escapes project boundary") from exc
    if file and not resolved.is_file():
        raise AdapterError(f"{label} is missing")
    return resolved


def runtime_policy(path: Path, expected: object) -> dict[str, Any]:
    if sha(path) != expected:
        raise AdapterError("runtime policy identity is stale")
    policy = read_object(path, "runtime policy")
    try:
        GuardedCompileProvider(ROOT)._validate_runtime_policy(policy)
    except Exception as exc:
        raise AdapterError("runtime policy validation failed") from exc
    return policy


def stage(
    manifest: dict[str, Any],
    staging: Path,
    policy: dict[str, Any],
    tex_entrypoint_logical_id: str | None,
) -> tuple[
    list[dict[str, Any]], Path, dict[tuple[str, int, str], Path]
]:
    entries = manifest.get("entries")
    if not isinstance(entries, list) or not entries:
        raise AdapterError("compile manifest entries are missing")
    destinations: set[str] = set()
    result: list[dict[str, Any]] = []
    entrypoint_candidates: list[Path] = []
    raster_sources: dict[tuple[str, int, str], Path] = {}
    for entry in entries:
        source = contained(Path(str(entry.get("source_path", ""))), "compile source")
        if sha(source) != entry.get("sha256"):
            raise AdapterError("compile source identity is stale")
        relative = PurePosixPath(str(entry.get("staging_path", "")).replace("\\", "/"))
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            raise AdapterError("staging path escapes compile boundary")
        destination = (staging / Path(*relative.parts)).resolve()
        try:
            destination.relative_to(staging)
        except ValueError as exc:
            raise AdapterError("staging path escapes compile boundary") from exc
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        destinations.add(relative.as_posix().casefold())
        result.append({k: entry.get(k) for k in ("logical_id", "generation", "sha256")})
        is_explicit_entrypoint = (
            tex_entrypoint_logical_id is not None
            and entry.get("logical_id") == tex_entrypoint_logical_id
        )
        is_legacy_entrypoint = (
            tex_entrypoint_logical_id is None
            and relative.as_posix().casefold() == "main.tex"
        )
        if is_explicit_entrypoint or is_legacy_entrypoint:
            entrypoint_candidates.append(destination)
        if relative.suffix.casefold() in {".png", ".jpg", ".jpeg"}:
            identity = (entry.get("logical_id"), entry.get("generation"), entry.get("sha256"))
            if not isinstance(identity[0], str) or not isinstance(identity[1], int):
                raise AdapterError("declared raster source identity is incomplete")
            raster_sources[identity] = destination
    if len(entrypoint_candidates) != 1:
        raise AdapterError(
            "compile manifest entrypoint is missing or ambiguous",
            data={
                "first_failing_gate": "final_editable_tex_source_set_entrypoint",
                "error_code": (
                    "final_tex_entrypoint_missing"
                    if not entrypoint_candidates
                    else "final_tex_entrypoint_ambiguous"
                ),
            },
        )
    validator = GuardedCompileProvider(ROOT)
    allowed = {str(item).casefold() for item in policy.get("allowed_packages", [])}
    for path in staging.rglob("*"):
        if path.suffix.casefold() in {".tex", ".sty", ".cls"}:
            text = path.read_text(encoding="utf-8", errors="strict")
            try:
                validator.static_preflight_text(text)
                validator._validate_declared_references(text, destinations, allowed)
            except CompileDependencyGap as exc:
                data = (
                    {
                        "first_failing_gate": (
                            "final_editable_tex_source_set_dependency_evidence"
                        ),
                        "error_code": "final_tex_include_missing",
                    }
                    if exc.data.get("reference_command") in {"input", "include"}
                    else None
                )
                raise AdapterError(str(exc), data=data) from exc
            except Exception as exc:
                raise AdapterError(str(exc)) from exc
    return result, entrypoint_candidates[0], raster_sources


def _compile_output_state(paths: tuple[Path, ...]) -> tuple[tuple[int, int], ...] | None:
    try:
        return tuple((path.stat().st_size, path.stat().st_mtime_ns) for path in paths)
    except OSError:
        return None


def _wait_for_completed_compile_output(
    pdf: Path,
    log: Path,
    recorder: Path,
    previous_state: tuple[tuple[int, int], ...] | None,
) -> None:
    paths = (pdf, log, recorder)
    deadline = time.monotonic() + COMPILE_OUTPUT_TIMEOUT_SECONDS
    stable_state: tuple[tuple[int, int], ...] | None = None
    stable_observations = 0
    last_reason = "required output is missing"
    completion_marker = f"Output written on {pdf.name} ("

    while time.monotonic() < deadline:
        state = _compile_output_state(paths)
        if state is None:
            last_reason = "required output is missing"
        elif state == previous_state:
            last_reason = "outputs were not refreshed for this compile round"
        else:
            try:
                pdf_bytes = pdf.read_bytes()
                log_text = log.read_text(encoding="utf-8", errors="replace")
                recorder.read_bytes()
            except OSError:
                last_reason = "outputs are still being written"
            else:
                after_read_state = _compile_output_state(paths)
                if after_read_state != state:
                    last_reason = "outputs changed while being read"
                elif completion_marker not in log_text:
                    last_reason = "LaTeX log has no normal completion marker"
                else:
                    try:
                        with fitz.open(stream=pdf_bytes, filetype="pdf") as document:
                            page_count = document.page_count
                    except Exception:
                        last_reason = "compiled PDF is not yet readable"
                    else:
                        if page_count < 1:
                            last_reason = "compiled PDF has no pages"
                        elif state == stable_state:
                            stable_observations += 1
                            if stable_observations >= 2:
                                return
                        else:
                            stable_state = state
                            stable_observations = 1
        time.sleep(COMPILE_OUTPUT_POLL_SECONDS)

    raise AdapterError(
        "guarded compile output did not stabilize before timeout: " + last_reason
    )


def compile_pdf(
    staging: Path,
    entry: Path,
    policy: dict[str, Any],
) -> tuple[Path, Path, dict[str, str], dict[str, int | str]]:
    engine = policy["engine"]
    miktex_process_guards = (
        ["--miktex-disable-maintenance", "--miktex-disable-diagnose"]
        if policy["policy_id"] == "miktex-xelatex-runtime"
        else []
    )
    command = [str(Path(engine["executable"]).resolve()), *map(str, engine.get("prefix_args", [])),
               *miktex_process_guards, "--disable-installer", "-no-shell-escape", "-recorder",
               "-interaction=nonstopmode", entry.name]
    engine_temp = require_contained_path(
        staging / "engine-temp",
        staging,
        purpose="Final Compile engine temporary directory",
        error_type=AdapterError,
        leaf_kind="directory",
        allow_missing=True,
    )
    try:
        engine_temp.mkdir()
    except OSError as exc:
        raise AdapterError("Final Compile engine temporary directory is unavailable") from exc
    engine_temp = require_contained_path(
        engine_temp,
        staging,
        purpose="Final Compile engine temporary directory",
        error_type=AdapterError,
        leaf_kind="directory",
    )
    profile_root = require_contained_path(
        staging / "engine-profile",
        staging,
        purpose="Final Compile engine profile",
        error_type=AdapterError,
        leaf_kind="directory",
        allow_missing=True,
    )
    try:
        profile_root.mkdir()
    except OSError as exc:
        raise AdapterError("Final Compile engine profile is unavailable") from exc
    profile_root = require_contained_path(
        profile_root,
        staging,
        purpose="Final Compile engine profile",
        error_type=AdapterError,
        leaf_kind="directory",
    )
    environment = {
        "PYTHONUTF8": "1",
        "MIKTEX_ENABLE_INSTALLER": "0",
        "TEMP": str(engine_temp),
        "TMP": str(engine_temp),
        "USERNAME": "video2pdf",
        "USERDOMAIN": "LOCAL",
    }
    environment["VIDEO2PDF_FIXTURE_FONTS"] = os.pathsep.join(str(Path(item["path"]).resolve()) for item in policy["system_fonts"])
    for key, value in os.environ.items():
        normalized_key = key.upper()
        if normalized_key in {"SYSTEMROOT", "WINDIR"}:
            environment[normalized_key] = value
    runtime_roots = [Path(value).resolve() for value in policy["allowed_runtime_roots"]]
    for key, value in os.environ.items():
        normalized_key = key.upper()
        if not normalized_key.startswith("MIKTEX_"):
            continue
        paths = [Path(item).resolve() for item in value.split(os.pathsep) if item]
        if paths and all(any(path == root or root in path.parents for root in runtime_roots) for path in paths):
            environment[normalized_key] = value
    if policy["policy_id"] == "miktex-xelatex-runtime":
        environment.update({name: str(path) for name, path in _MIKTEX_DURABLE_DIRECTORIES.items()})
        environment["MIKTEX_COMMONINSTALL"] = str(_MIKTEX_RUNTIME_ROOTS[0])
        environment["MIKTEX_USERLOGDIRECTORY"] = str(profile_root)
        identity_profile = _MIKTEX_DURABLE_DIRECTORIES["MIKTEX_USERDATA"]
    else:
        governed_userdata = environment.get("MIKTEX_USERDATA")
        if governed_userdata and len(governed_userdata.split(os.pathsep)) == 1:
            identity_profile = Path(governed_userdata).resolve()
        else:
            environment.update({
                "MIKTEX_USERDATA": str(profile_root),
                "MIKTEX_USERCONFIG": str(profile_root),
                "MIKTEX_USERINSTALL": str(profile_root),
                "MIKTEX_USERLOGDIRECTORY": str(profile_root),
            })
            identity_profile = profile_root
    profile_drive = identity_profile.drive
    environment.update({
        "USERPROFILE": str(identity_profile),
        "HOME": str(identity_profile),
        "HOMEDRIVE": profile_drive,
        "HOMEPATH": str(identity_profile)[len(profile_drive):],
        "SYSTEMDRIVE": Path(environment["SYSTEMROOT"]).drive,
    })
    stderr_parts: list[bytes] = []
    pdf, log, recorder = (
        staging / f"{entry.stem}.pdf",
        staging / f"{entry.stem}.log",
        staging / f"{entry.stem}.fls",
    )
    for _ in range(3):
        previous_state = _compile_output_state((pdf, log, recorder))
        completed = subprocess.run(command, cwd=staging, env=environment, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120, check=False)
        stderr_parts.append(completed.stderr)
        if completed.returncode != 0:
            raise AdapterError("guarded compile engine failed")
        if not all(path.is_file() for path in (pdf, log, recorder)):
            raise AdapterError("guarded compile omitted required output")
        _wait_for_completed_compile_output(pdf, log, recorder, previous_state)
    combined_stderr = b"".join(stderr_parts)
    stderr_summary = {
        "byte_length": len(combined_stderr),
        "sha256": hashlib.sha256(combined_stderr).hexdigest(),
    }
    return pdf, recorder, environment, stderr_summary


def _pixmap_identity(pixmap: fitz.Pixmap) -> tuple[int, int, str]:
    normalized = pixmap
    if pixmap.alpha or pixmap.colorspace is None or pixmap.colorspace.n != 3:
        normalized = fitz.Pixmap(fitz.csRGB, pixmap)
    try:
        return normalized.width, normalized.height, hashlib.sha256(normalized.samples).hexdigest()
    finally:
        if normalized is not pixmap:
            normalized = None


def _same_bbox(actual: fitz.Rect, expected: object, tolerance: float = 0.5) -> bool:
    return (
        isinstance(expected, list)
        and len(expected) == 4
        and all(isinstance(value, (int, float)) for value in expected)
        and all(abs(actual[index] - float(expected[index])) <= tolerance for index in range(4))
    )


def render_and_extract(
    pdf: Path,
    output: Path,
    plan: dict[str, Any],
    raster_sources: dict[tuple[str, int, str], Path],
) -> list[dict[str, Any]]:
    expected_pages, suite = plan.get("page_count"), plan.get("extractor_suite")
    if not isinstance(expected_pages, int) or expected_pages < 1 or not isinstance(suite, list) or not suite:
        raise AdapterError("text origin plan is incomplete")
    pages = output / "rendered_pages"
    pages.mkdir()
    objects: list[dict[str, Any]] = []
    raster_plan = [item for item in plan.get("rendered_objects", [])
                   if isinstance(item, dict) and item.get("object_kind") == "declared_raster_text"]
    source_identities: dict[tuple[str, int, str], tuple[int, int, str]] = {}
    for identity, source in raster_sources.items():
        try:
            source_identities[identity] = _pixmap_identity(fitz.Pixmap(source))
        except Exception as exc:
            raise AdapterError("declared raster source is unreadable") from exc
    if raster_plan and not source_identities:
        raise AdapterError("declared raster source is absent from compile manifest")
    try:
        with fitz.open(pdf) as document:
            if document.page_count != expected_pages:
                raise AdapterError("compiled PDF page count contradicts text origin plan")
            sequence = 0
            for page_index, page in enumerate(document):
                page_number = page_index + 1
                page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False).save(pages / f"page_{page_number:03d}.png")
                for block_index, block in enumerate(page.get_text("dict").get("blocks", []), 1):
                    for line_index, line in enumerate(block.get("lines", []), 1):
                        for span_index, span in enumerate(line.get("spans", []), 1):
                            if not span.get("text"):
                                continue
                            sequence += 1
                            item = {"object_id": f"page-{page_number}-text-{sequence}", "page": page_number,
                                    "object_kind": "pdf_text_run", "bbox": list(span["bbox"]),
                                    "exact_utf8_text": span["text"], "extractor_id": suite[0]["extractor_id"],
                                    "evidence_locator": f"page:{page_number}/block:{block_index}/line:{line_index}/span:{span_index}"}
                            item["text_sha256"] = hashlib.sha256(item["exact_utf8_text"].encode("utf-8")).hexdigest()
                            item["object_sha256"] = fingerprint(item, "object_sha256")
                            objects.append(item)
                actual_images: list[tuple[fitz.Rect, tuple[int, int, str]]] = []
                for image in page.get_images(full=True):
                    xref, soft_mask_xref = image[0], image[1]
                    base = fitz.Pixmap(document, xref)
                    embedded = (
                        fitz.Pixmap(base, fitz.Pixmap(document, soft_mask_xref))
                        if soft_mask_xref
                        else base
                    )
                    identity = _pixmap_identity(embedded)
                    actual_images.extend((rect, identity) for rect in page.get_image_rects(xref))
                for declared in [item for item in raster_plan if item.get("page") == page_number]:
                    source_identity = (
                        declared.get("source_artifact_logical_id"),
                        declared.get("source_generation"),
                        declared.get("source_sha256"),
                    )
                    if (
                        not isinstance(source_identity[0], str)
                        or not isinstance(source_identity[1], int)
                        or not isinstance(source_identity[2], str)
                        or source_identity not in source_identities
                    ):
                        raise AdapterError("declared raster source identity is absent from compile manifest")
                    bbox_matches = [item for item in actual_images if _same_bbox(item[0], declared.get("bbox"))]
                    if not bbox_matches:
                        raise AdapterError("declared raster bbox does not match a PDF image")
                    if not any(identity == source_identities[source_identity] for _, identity in bbox_matches):
                        raise AdapterError("declared raster source identity does not match the embedded PDF image")
                    item = dict(declared)
                    item["text_sha256"] = hashlib.sha256(item["exact_utf8_text"].encode("utf-8")).hexdigest()
                    item["object_sha256"] = fingerprint(item, "object_sha256")
                    objects.append(item)
    except AdapterError:
        raise
    except Exception as exc:
        raise AdapterError("compiled PDF is unreadable") from exc
    projection = [{k: v for k, v in item.items() if k not in {"text_sha256", "object_sha256"}} for item in objects]
    if projection != plan.get("rendered_objects"):
        raise AdapterError("rendered text objects drift from text origin plan")
    return objects


def run(request_path: Path) -> None:
    request = read_object(contained(request_path, "compile request"), "compile request")
    if (request.get("schema_name"), request.get("schema_version")) != ("guarded-final-compile-request", "1.0.0"):
        raise AdapterError("compile request protocol is unsupported")
    manifest_path = contained(Path(str(request.get("compile_manifest_path", ""))), "compile manifest")
    manifest = read_object(manifest_path, "compile manifest")
    if manifest.get("manifest_sha256") != request.get("compile_manifest_sha256") or fingerprint(manifest, "manifest_sha256") != manifest.get("manifest_sha256"):
        raise AdapterError("compile manifest identity is stale")
    if manifest.get("mode") != "final" or manifest.get("precompile_text_seal_sha256") != request.get("precompile_text_seal_sha256"):
        raise AdapterError("compile manifest authority is stale")
    plan_path = contained(Path(str(request.get("text_origin_plan_path", ""))), "text origin plan")
    plan = read_object(plan_path, "text origin plan")
    if plan.get("plan_sha256") != request.get("text_origin_plan_sha256") or fingerprint(plan, "plan_sha256") != plan.get("plan_sha256"):
        raise AdapterError("text origin plan identity is stale")
    if plan.get("precompile_text_seal_sha256") != request.get("precompile_text_seal_sha256"):
        raise AdapterError("text origin plan authority is stale")
    policy_path = contained(Path(str(request.get("runtime_policy_path", ""))), "runtime policy")
    policy_binding = manifest.get("runtime_policy")
    if (not isinstance(policy_binding, dict)
            or Path(str(policy_binding.get("path", ""))).resolve() != policy_path
            or policy_binding.get("sha256") != request.get("runtime_policy_sha256")):
        raise AdapterError("compile manifest runtime policy binding is stale")
    policy = runtime_policy(policy_path, request.get("runtime_policy_sha256"))
    output = contained(Path(str(request.get("output_root", ""))), "adapter output", file=False)
    if output.exists() and any(output.iterdir()):
        raise AdapterError("adapter output must be empty")
    output.mkdir(parents=True, exist_ok=True)
    staging = output / "compiler-staging"
    staging.mkdir()
    tex_entrypoint_logical_id = request.get("tex_entrypoint_logical_id")
    if tex_entrypoint_logical_id is not None and (
        not isinstance(tex_entrypoint_logical_id, str)
        or not tex_entrypoint_logical_id
    ):
        raise AdapterError("compile request TeX entrypoint identity is invalid")
    inputs, entry, raster_sources = stage(
        manifest,
        staging,
        policy,
        tex_entrypoint_logical_id,
    )
    built_pdf, built_recorder, runtime_environment, engine_stderr = compile_pdf(
        staging, entry, policy
    )
    final_pdf, recorder = output / "final.pdf", output / "compile-recorder.fls"
    shutil.copyfile(built_pdf, final_pdf)
    shutil.copyfile(built_recorder, recorder)
    objects = render_and_extract(final_pdf, output, plan, raster_sources)
    pdf_sha = sha(final_pdf)
    rendered = {"schema_name": "rendered-text-object-inventory", "schema_version": "1.0.0",
                "activation_status": request.get("activation_status"), "final_pdf_sha256": pdf_sha,
                "extractor_suite": plan["extractor_suite"],
                "coverage": {"page_count": plan["page_count"], "pages_scanned": list(range(1, plan["page_count"] + 1)),
                             "content_streams_complete": True, "annotations_complete": True,
                             "form_xobjects_complete": True, "declared_raster_text_complete": True}, "objects": objects}
    rendered["inventory_sha256"] = fingerprint(rendered, "inventory_sha256")
    write_object(output / "rendered-text-object-inventory.json", rendered)
    seal = {"schema_name": "final-artifact-seal", "schema_version": "1.0.0",
            "activation_status": request.get("activation_status"), "sealed_at": request.get("compiled_at"),
            "precompile_text_seal_sha256": request.get("precompile_text_seal_sha256"),
            "generation_set_sha256": request.get("generation_set_sha256"),
            "compile_manifest_sha256": request.get("compile_manifest_sha256"), "compile_provider": request.get("compile_provider"),
            "final_pdf": {"path": "adapter-output/final.pdf", "sha256": pdf_sha, "size": final_pdf.stat().st_size}}
    seal["seal_sha256"] = fingerprint(seal, "seal_sha256")
    write_object(output / "final-artifact-seal.json", seal)
    write_object(output / "text-origin-trace.json", {"schema_name": "text-origin-trace", "schema_version": "1.0.0",
                 "activation_status": request.get("activation_status"), "text_origin_plan_sha256": plan["plan_sha256"],
                 "final_artifact_seal_sha256": seal["seal_sha256"], "edges": plan["edges"]})
    write_object(output / "compile-provenance.json", {"schema_name": "compile-provenance", "schema_version": "1.0.0",
                 "compile_manifest_sha256": manifest["manifest_sha256"], "text_origin_plan_sha256": plan["plan_sha256"],
                 "final_artifact_seal_sha256": seal["seal_sha256"],
                 "invocation": {"recorder": True, "shell_escape": False, "automatic_package_install": False},
                 "engine_stderr": engine_stderr,
                 "runtime_environment": runtime_environment,
                 "recorder_cwd": str(staging), "dependency_closure": {"complete": True,
                 "recorder_path": "compile-recorder.fls", "recorder_sha256": sha(recorder), "inputs": inputs,
                 "runtime_inputs": manifest.get("approved_runtime_inputs", [])}})


def main() -> int:
    if len(sys.argv) != 2:
        print("guarded final compile requires exactly one request", file=sys.stderr)
        return 2
    try:
        run(Path(sys.argv[1]))
    except AdapterError as exc:
        if exc.data:
            try:
                request = read_object(
                    contained(Path(sys.argv[1]), "compile request"),
                    "compile request",
                )
                output = contained(
                    Path(str(request.get("output_root", ""))),
                    "adapter output",
                    file=False,
                )
                output.mkdir(parents=True, exist_ok=True)
                write_object(
                    output / "adapter-error.json",
                    {
                        "schema_name": "guarded-final-compile-error",
                        "schema_version": "1.0.0",
                        **exc.data,
                    },
                )
            except AdapterError:
                pass
        print(str(exc), file=sys.stderr)
        return 1
    except Exception:
        print("guarded final compile failed closed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
