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
import unicodedata
from concurrent.futures import ThreadPoolExecutor

import fitz

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from video2pdf_workflow_kernel.guarded_compile import (  # noqa: E402
    GuardedCompileProvider,
    _MIKTEX_DURABLE_DIRECTORIES,
    _MIKTEX_RUNTIME_ROOTS,
)
from video2pdf_workflow_kernel.utils import (  # noqa: E402
    canonical_json_bytes,
    require_contained_path,
)


class AdapterError(RuntimeError):
    pass


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


def stage(manifest: dict[str, Any], staging: Path, policy: dict[str, Any]) -> tuple[
    list[dict[str, Any]], Path, dict[tuple[str, int, str], Path]
]:
    entries = manifest.get("entries")
    if not isinstance(entries, list) or not entries:
        raise AdapterError("compile manifest entries are missing")
    destinations: set[str] = set()
    result: list[dict[str, Any]] = []
    entry_tex: Path | None = None
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
        if relative.as_posix().casefold() == "main.tex":
            entry_tex = destination
        if relative.suffix.casefold() in {".png", ".jpg", ".jpeg"}:
            identity = (entry.get("logical_id"), entry.get("generation"), entry.get("sha256"))
            if not isinstance(identity[0], str) or not isinstance(identity[1], int):
                raise AdapterError("declared raster source identity is incomplete")
            raster_sources[identity] = destination
    if entry_tex is None:
        raise AdapterError("compile manifest has no main.tex entry")
    validator = GuardedCompileProvider(ROOT)
    allowed = {str(item).casefold() for item in policy.get("allowed_packages", [])}
    for path in staging.rglob("*"):
        if path.suffix.casefold() in {".tex", ".sty", ".cls"}:
            text = path.read_text(encoding="utf-8", errors="strict")
            try:
                validator.static_preflight_text(text)
                validator._validate_declared_references(text, destinations, allowed)
            except Exception as exc:
                raise AdapterError(str(exc)) from exc
    return result, entry_tex, raster_sources


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
) -> tuple[Path, Path, Path | None, dict[str, str], dict[str, int | str]]:
    engine = policy["engine"]
    miktex_process_guards = (
        ["--miktex-disable-maintenance", "--miktex-disable-diagnose"]
        if policy["policy_id"] == "miktex-xelatex-runtime"
        else []
    )
    command = [str(Path(engine["executable"]).resolve()), *map(str, engine.get("prefix_args", [])),
               *miktex_process_guards, "--disable-installer", "-no-shell-escape", "-recorder",
               "-synctex=1", "-interaction=nonstopmode", entry.name]
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
    source_map = staging / f"{entry.stem}.synctex.gz"
    if policy["policy_id"] == "miktex-xelatex-runtime" and not source_map.is_file():
        raise AdapterError("compiler source map is missing")
    return pdf, recorder, source_map if source_map.is_file() else None, environment, stderr_summary


def _synctex_source_location(
    tool: Path, pdf: Path, staging: Path, obj: dict[str, Any]
) -> dict[str, Any]:
    bbox = obj["bbox"]
    x = (float(bbox[0]) + float(bbox[2])) / 2
    y = (float(bbox[1]) + float(bbox[3])) / 2
    completed = subprocess.run(
        [str(tool), "edit", "-o", f"{obj['page']}:{x}:{y}:{pdf}", "-d", str(staging)],
        cwd=staging,
        text=True,
        encoding="utf-8",
        errors="strict",
        capture_output=True,
        check=False,
        timeout=30,
    )
    if completed.returncode != 0 or completed.stderr:
        raise AdapterError("compiler source map query failed")
    fields: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        if key in {"Input", "Line", "Column"} and key not in fields:
            fields[key] = value.strip()
    if not fields.get("Input") or not fields.get("Line"):
        raise AdapterError("compiler source map query is incomplete")
    return {
        "object_id": obj["object_id"],
        "source_path": str(Path(fields["Input"]).resolve()),
        "line": int(fields["Line"]),
        "column": int(fields.get("Column", "-1")),
        "query": {"page": obj["page"], "x": x, "y": y},
    }


def compiler_source_locations(
    *,
    policy: dict[str, Any],
    pdf: Path,
    staging: Path,
    objects: list[dict[str, Any]],
    entry: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    text_objects = [
        item for item in objects if item["object_kind"] == "pdf_text_run"
    ]
    if policy["policy_id"] == "fixture-miktex-runtime":
        locations = {
            item["object_id"]: {
                "object_id": item["object_id"],
                "source_path": str(entry.resolve()),
                "line": 1,
                "column": 1,
                "query": {"page": item["page"], "x": item["bbox"][0], "y": item["bbox"][1]},
            }
            for item in text_objects
        }
        return locations, {
            "provider_id": "fixture-compiler-source-map-v1",
            "provider_sha256": policy["engine"]["prefix_file_fingerprints"][0]["sha256"],
        }
    engine = Path(policy["engine"]["executable"]).resolve()
    tool = engine.with_name("synctex.exe")
    runtime_roots = [Path(value).resolve() for value in policy["allowed_runtime_roots"]]
    if (
        not tool.is_file()
        or not any(tool == root or root in tool.parents for root in runtime_roots)
    ):
        raise AdapterError("registered compiler source map extractor is unavailable")
    with ThreadPoolExecutor(max_workers=8) as executor:
        mapped = list(
            executor.map(
                lambda item: _synctex_source_location(tool, pdf, staging, item),
                text_objects,
            )
        )
    return {item["object_id"]: item for item in mapped}, {
        "provider_id": "synctex-reverse-map-v1",
        "provider_sha256": sha(tool),
        "tool_path": str(tool),
    }


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


def _normalized_layout_text(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).split())


def _object_with_fingerprints(value: dict[str, Any]) -> dict[str, Any]:
    value["text_sha256"] = hashlib.sha256(
        value["exact_utf8_text"].encode("utf-8")
    ).hexdigest()
    value["object_sha256"] = fingerprint(value, "object_sha256")
    return value


def render_and_derive(
    pdf: Path,
    output: Path,
    inventory: dict[str, Any],
    raster_sources: dict[tuple[str, int, str], Path],
    *,
    policy: dict[str, Any],
    staging: Path,
    entry: Path,
    manifest_entries: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, str]], int]:
    items = inventory.get("items")
    if not isinstance(items, list) or not items:
        raise AdapterError("reader-facing text inventory is incomplete")
    extractor_sha = sha(Path(__file__).resolve())
    suite = [
        {"extractor_id": "pymupdf-text-v1", "extractor_sha256": extractor_sha},
        {"extractor_id": "pymupdf-raster-binding-v1", "extractor_sha256": extractor_sha},
    ]
    pages = output / "rendered_pages"
    pages.mkdir()
    objects: list[dict[str, Any]] = []
    source_identities: dict[tuple[str, int, str], tuple[int, int, str]] = {}
    for identity, source in raster_sources.items():
        try:
            source_identities[identity] = _pixmap_identity(fitz.Pixmap(source))
        except Exception as exc:
            raise AdapterError("declared raster source is unreadable") from exc
    raster_items = [
        item
        for item in items
        if isinstance(item, dict)
        and item.get("representation") == "authoritative_raster_text"
    ]
    if raster_items and not source_identities:
        raise AdapterError("declared raster source is absent from compile manifest")
    raster_object_ids: dict[str, list[str]] = {
        str(item.get("item_id")): [] for item in raster_items
    }
    try:
        with fitz.open(pdf) as document:
            sequence = 0
            raster_sequence = 0
            for page_index, page in enumerate(document):
                page_number = page_index + 1
                page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False).save(pages / f"page_{page_number:03d}.png")
                page_text_objects: list[dict[str, Any]] = []
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
                            materialized = _object_with_fingerprints(item)
                            objects.append(materialized)
                            page_text_objects.append(materialized)
                traced_text = "".join(
                    chr(character[0])
                    for span in page.get_texttrace()
                    for character in span.get("chars", [])
                )
                extracted_text = "".join(
                    item["exact_utf8_text"] for item in page_text_objects
                )
                if _normalized_layout_text(traced_text) != _normalized_layout_text(
                    extracted_text
                ):
                    raise AdapterError("PDF text extractor coverage is incomplete")
                annotations = page.annots()
                if annotations is not None:
                    for annotation_index, annotation in enumerate(annotations, 1):
                        content = annotation.info.get("content", "")
                        if not content:
                            continue
                        sequence += 1
                        objects.append(
                            _object_with_fingerprints(
                                {
                                    "object_id": f"page-{page_number}-annotation-{sequence}",
                                    "page": page_number,
                                    "object_kind": "text_annotation",
                                    "bbox": list(annotation.rect),
                                    "exact_utf8_text": content,
                                    "extractor_id": "pymupdf-text-v1",
                                    "evidence_locator": (
                                        f"page:{page_number}/annotation:{annotation_index}"
                                    ),
                                }
                            )
                        )
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
                for declared in raster_items:
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
                    for bbox, actual_identity in actual_images:
                        if actual_identity != source_identities[source_identity]:
                            continue
                        raster_sequence += 1
                        item = {
                            "object_id": f"page-{page_number}-raster-{raster_sequence}",
                            "page": page_number,
                            "object_kind": "declared_raster_text",
                            "bbox": list(bbox),
                            "exact_utf8_text": declared.get("declared_text", ""),
                            "extractor_id": "pymupdf-raster-binding-v1",
                            "evidence_locator": f"page:{page_number}/image:{raster_sequence}",
                            "source_artifact_logical_id": source_identity[0],
                            "source_generation": source_identity[1],
                            "source_sha256": source_identity[2],
                            "source_path": next(
                                path.name
                                for identity, path in raster_sources.items()
                                if identity == source_identity
                            ),
                        }
                        objects.append(_object_with_fingerprints(item))
                        raster_object_ids[str(declared.get("item_id"))].append(
                            item["object_id"]
                        )
            page_count = document.page_count
    except AdapterError:
        raise
    except Exception as exc:
        raise AdapterError("compiled PDF is unreadable") from exc
    locations, source_map_provider = compiler_source_locations(
        policy=policy,
        pdf=pdf,
        staging=staging,
        objects=objects,
        entry=entry,
    )
    entry_by_staged_path = {
        str((staging / Path(item["staging_path"])).resolve()).casefold(): item
        for item in manifest_entries
    }
    items_by_binding: dict[tuple[str, int, str], list[dict[str, Any]]] = {}
    for item in items:
        binding = (
            item.get("source_artifact_logical_id"),
            item.get("source_generation"),
            item.get("source_sha256"),
        )
        items_by_binding.setdefault(binding, []).append(item)
    grouped: dict[str, list[dict[str, Any]]] = {}
    used_objects: set[str] = set()
    mapped_items: set[str] = set()
    for object_id, location in locations.items():
        source_entry = entry_by_staged_path.get(location["source_path"].casefold())
        if source_entry is None:
            continue
        binding = (
            source_entry.get("logical_id"),
            source_entry.get("generation"),
            source_entry.get("sha256"),
        )
        candidates = items_by_binding.get(binding, [])
        if len(candidates) > 1:
            raise AdapterError("compiler source mapping is ambiguous")
        if not candidates:
            continue
        item_id = str(candidates[0]["item_id"])
        grouped.setdefault(item_id, []).append(location)
        used_objects.add(object_id)
    edges: list[dict[str, Any]] = []
    for item in items:
        item_id = str(item.get("item_id"))
        if item.get("representation") == "authoritative_raster_text":
            continue
        object_sources = grouped.get(item_id)
        if not object_sources:
            continue
        edges.append(
            {
                "edge_id": f"sealed.{len(edges) + 1}",
                "disposition": "sealed_origin",
                "sealed_item_id": item_id,
                "sealed_text_utf8": item.get("declared_text", ""),
                "rendered_object_ids": [
                    value["object_id"] for value in object_sources
                ],
                "recipe": "compiler_source_map",
                "source_mapping": {
                    "logical_id": item.get("source_artifact_logical_id"),
                    "generation": item.get("source_generation"),
                    "sha256": item.get("source_sha256"),
                    "method": "compiler_synctex_v1",
                    "provider": source_map_provider,
                    "object_sources": object_sources,
                },
            }
        )
        mapped_items.add(item_id)
    for item in raster_items:
        item_id = str(item.get("item_id"))
        object_ids = raster_object_ids[item_id]
        if not object_ids:
            raise AdapterError("declared raster text is absent from compiled PDF")
        edges.append(
            {
                "edge_id": f"sealed.{len(edges) + 1}",
                "disposition": "sealed_origin",
                "sealed_item_id": item_id,
                "sealed_text_utf8": item.get("declared_text", ""),
                "rendered_object_ids": object_ids,
                "recipe": "exact_utf8",
            }
        )
        used_objects.update(object_ids)
        mapped_items.add(item_id)
    missing_items = sorted(
        str(item.get("item_id"))
        for item in items
        if str(item.get("item_id")) not in mapped_items
    )
    if missing_items:
        raise AdapterError("sealed text origin coverage is incomplete")
    page_number_objects = [
        item
        for item in objects
        if item["object_id"] not in used_objects
        and item["object_id"] in locations
        and item["exact_utf8_text"].isdigit()
        and int(item["exact_utf8_text"]) == item["page"]
    ]
    if page_number_objects:
        numbers = [int(item["exact_utf8_text"]) for item in page_number_objects]
        if numbers == list(range(numbers[0], numbers[0] + len(numbers))):
            generator_contract = {
                "generator_id": "page-number-v1",
                "generator_version": "1.0.0",
                "kind": "page_number",
            }
            edges.append(
                {
                    "edge_id": f"generated.{len(edges) + 1}",
                    "disposition": "generated",
                    "rendered_object_ids": [
                        item["object_id"] for item in page_number_objects
                    ],
                    "recipe": "declared_generated",
                    "generator": {
                        **generator_contract,
                        "generator_sha256": hashlib.sha256(
                            canonical_json_bytes(generator_contract)
                        ).hexdigest(),
                        "inputs": {
                            "first_page_number": numbers[0],
                            "page_count": len(numbers),
                        },
                        "source_mapping": {
                            "method": "compiler_synctex_v1",
                            "provider": source_map_provider,
                            "object_sources": [
                                locations[item["object_id"]]
                                for item in page_number_objects
                            ],
                        },
                    },
                }
            )
            used_objects.update(
                item["object_id"] for item in page_number_objects
            )
    for item in objects:
        if item["object_id"] in used_objects:
            continue
        edges.append(
            {
                "edge_id": f"unexpected.{len(edges) + 1}",
                "disposition": "unexpected_addition",
                "rendered_object_ids": [item["object_id"]],
                "recipe": "exact_utf8",
            }
        )
    return objects, edges, suite, page_count


def run(request_path: Path) -> None:
    request = read_object(contained(request_path, "compile request"), "compile request")
    if (request.get("schema_name"), request.get("schema_version")) != ("guarded-final-compile-request", "2.0.0"):
        raise AdapterError("compile request protocol is unsupported")
    execution_path = contained(
        Path(str(request.get("execution_state_path", ""))),
        "Final Compile execution state",
    )
    execution = read_object(execution_path, "Final Compile execution state")
    if (
        execution.get("operation_id") != request.get("operation_id")
        or execution.get("state") != "launch_pending"
        or execution.get("execution_sha256")
        != fingerprint(execution, "execution_sha256")
    ):
        raise AdapterError("Final Compile execution identity is stale")
    execution["state"] = "running"
    execution["adapter_pid"] = os.getpid()
    execution["execution_sha256"] = fingerprint(execution, "execution_sha256")
    write_object(execution_path, execution)
    manifest_path = contained(Path(str(request.get("compile_manifest_path", ""))), "compile manifest")
    manifest = read_object(manifest_path, "compile manifest")
    if manifest.get("manifest_sha256") != request.get("compile_manifest_sha256") or fingerprint(manifest, "manifest_sha256") != manifest.get("manifest_sha256"):
        raise AdapterError("compile manifest identity is stale")
    if manifest.get("mode") != "final" or manifest.get("precompile_text_seal_sha256") != request.get("precompile_text_seal_sha256"):
        raise AdapterError("compile manifest authority is stale")
    inventory_path = contained(
        Path(str(request.get("reader_facing_text_inventory_path", ""))),
        "reader-facing text inventory",
    )
    inventory = read_object(inventory_path, "reader-facing text inventory")
    if (
        inventory.get("inventory_sha256")
        != request.get("reader_facing_text_inventory_sha256")
        or fingerprint(inventory, "inventory_sha256")
        != inventory.get("inventory_sha256")
    ):
        raise AdapterError("reader-facing text inventory identity is stale")
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
    inputs, entry, raster_sources = stage(manifest, staging, policy)
    built_pdf, built_recorder, _, runtime_environment, engine_stderr = compile_pdf(
        staging, entry, policy
    )
    final_pdf, recorder = output / "final.pdf", output / "compile-recorder.fls"
    shutil.copyfile(built_pdf, final_pdf)
    shutil.copyfile(built_recorder, recorder)
    objects, edges, extractor_suite, page_count = render_and_derive(
        built_pdf,
        output,
        inventory,
        raster_sources,
        policy=policy,
        staging=staging,
        entry=entry,
        manifest_entries=manifest["entries"],
    )
    pdf_sha = sha(final_pdf)
    rendered = {"schema_name": "rendered-text-object-inventory", "schema_version": "1.0.0",
                "activation_status": request.get("activation_status"), "final_pdf_sha256": pdf_sha,
                "extractor_suite": extractor_suite,
                "coverage": {"page_count": page_count, "pages_scanned": list(range(1, page_count + 1)),
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
    write_object(output / "text-origin-trace.json", {"schema_name": "text-origin-trace", "schema_version": "2.0.0",
                 "activation_status": request.get("activation_status"),
                 "reader_facing_text_inventory_sha256": inventory["inventory_sha256"],
                 "final_artifact_seal_sha256": seal["seal_sha256"], "edges": edges})
    write_object(output / "compile-provenance.json", {"schema_name": "compile-provenance", "schema_version": "1.0.0",
                 "compile_manifest_sha256": manifest["manifest_sha256"],
                 "reader_facing_text_inventory_sha256": inventory["inventory_sha256"],
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
        print(str(exc), file=sys.stderr)
        return 1
    except Exception:
        print("guarded final compile failed closed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
