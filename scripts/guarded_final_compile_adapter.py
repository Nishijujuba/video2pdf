from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
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
from video2pdf_workflow_kernel.final_compile import (  # noqa: E402
    DISPLAY_MATH_DERIVATION,
    pdf_page_labels,
    registered_generator_identity,
    resolve_display_math_source,
    source_text_supports_rendered_text,
    validate_final_pdf_basename,
)
from video2pdf_workflow_kernel.errors import ContractError  # noqa: E402
from video2pdf_workflow_kernel.latex_generated_text import (  # noqa: E402
    TcolorboxInvocation,
    extract_tcolorbox_invocations,
    extract_tcolorbox_titles,
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
) -> tuple[
    Path,
    Path,
    Path | None,
    dict[str, str],
    dict[str, int | str],
    dict[Path, str],
]:
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
    stable_final_round_auxiliaries: dict[Path, str] = {}
    for round_index in range(3):
        toc_path = staging / f"{entry.stem}.toc"
        consumed_toc_sha = (
            sha(toc_path) if round_index == 2 and toc_path.is_file() else None
        )
        previous_state = _compile_output_state((pdf, log, recorder))
        completed = subprocess.run(command, cwd=staging, env=environment, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120, check=False)
        stderr_parts.append(completed.stderr)
        if completed.returncode != 0:
            raise AdapterError("guarded compile engine failed")
        if not all(path.is_file() for path in (pdf, log, recorder)):
            raise AdapterError("guarded compile omitted required output")
        _wait_for_completed_compile_output(pdf, log, recorder, previous_state)
        if round_index == 2 and toc_path.is_file():
            if consumed_toc_sha is None or sha(toc_path) != consumed_toc_sha:
                raise AdapterError(
                    "final compile table-of-contents input changed after consumption"
                )
            stable_final_round_auxiliaries[toc_path.resolve()] = consumed_toc_sha
    combined_stderr = b"".join(stderr_parts)
    stderr_summary = {
        "byte_length": len(combined_stderr),
        "sha256": hashlib.sha256(combined_stderr).hexdigest(),
    }
    source_map = staging / f"{entry.stem}.synctex.gz"
    if policy["policy_id"] == "miktex-xelatex-runtime" and not source_map.is_file():
        raise AdapterError("compiler source map is missing")
    return (
        pdf,
        recorder,
        source_map if source_map.is_file() else None,
        environment,
        stderr_summary,
        stable_final_round_auxiliaries,
    )


def _synctex_source_location(
    tool: Path,
    pdf: Path,
    staging: Path,
    obj: dict[str, Any],
    manifest_entries: list[dict[str, Any]],
    observed_declared_paths: set[Path],
    runtime_environment: dict[str, str],
) -> dict[str, Any] | None:
    bbox = obj["bbox"]
    x = (float(bbox[0]) + float(bbox[2])) / 2
    y = (float(bbox[1]) + float(bbox[3])) / 2
    query_timeout_seconds = 90
    try:
        completed = subprocess.run(
            [str(tool), "edit", "-o", f"{obj['page']}:{x}:{y}:{pdf}", "-d", str(staging)],
            cwd=staging,
            text=True,
            encoding="utf-8",
            errors="strict",
            capture_output=True,
            check=False,
            timeout=query_timeout_seconds,
            env=runtime_environment,
        )
    except subprocess.TimeoutExpired as exc:
        raise AdapterError(
            "compiler_source_map_query_timeout: "
            f"page={obj['page']}; x={x}; y={y}; "
            f"timeout_seconds={query_timeout_seconds}"
        ) from exc
    if completed.returncode != 0:
        raise AdapterError("compiler source map query failed")
    records: list[dict[str, str]] = []
    fields: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        if line.startswith("Output:") and fields:
            records.append(fields)
            fields = {}
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        if key in {"Input", "Line", "Column"} and key not in fields:
            fields[key] = value.strip()
    if fields:
        records.append(fields)
    candidates = [
        value
        for value in records
        if value.get("Input") and value.get("Line")
    ]
    if not candidates:
        raise AdapterError("compiler source map query is incomplete")
    toc_candidates = [
        value
        for value in candidates
        if Path(value["Input"]).resolve().suffix.casefold() == ".toc"
        and Path(value["Input"]).resolve().is_file()
    ]
    exact_candidates: list[dict[str, str]] = []
    for value in candidates:
        source_path = Path(value["Input"]).resolve()
        line_number = int(value["Line"])
        if not source_path.is_file():
            continue
        source_lines = source_path.read_text(encoding="utf-8").splitlines()
        if (
            1 <= line_number <= len(source_lines)
            and obj["exact_utf8_text"] in source_lines[line_number - 1]
        ):
            exact_candidates.append(value)
    resolved_candidates = {
        (value["Input"].casefold(), value["Line"], value.get("Column", "-1")): value
        for value in exact_candidates
    }
    if len(toc_candidates) == 1:
        fields = toc_candidates[0]
    elif len(resolved_candidates) == 1:
        fields = next(iter(resolved_candidates.values()))
    elif len(candidates) == 1:
        direct_path = Path(candidates[0]["Input"]).resolve()
        direct_line = int(candidates[0]["Line"])
        manifest_paths = {
            (staging / Path(value["staging_path"])).resolve()
            for value in manifest_entries
        }
        if (
            direct_path not in manifest_paths
            or direct_path not in observed_declared_paths
            or not direct_path.is_file()
        ):
            return None
        direct_lines = direct_path.read_text(encoding="utf-8").splitlines()
        if not 1 <= direct_line <= len(direct_lines):
            return None
        fields = candidates[0]
    else:
        return None
    source_path = Path(fields["Input"]).resolve()
    source_line = int(fields["Line"])
    source_column = int(fields.get("Column", "-1"))
    resolution = resolve_display_math_source(
        source_path,
        compiler_line=source_line,
        compiler_column=source_column,
        rendered_text=obj["exact_utf8_text"],
    )
    if source_path.suffix.casefold() == ".tex" and source_path.is_file():
        source_lines = source_path.read_text(encoding="utf-8").splitlines()
        if (
            1 <= source_line <= len(source_lines)
            and source_lines[source_line - 1].strip() == "$$"
        ):
            if resolution is None:
                raise AdapterError(
                    "compiler display-math source resolution is ambiguous or unsupported"
                )
            source_line = resolution["resolved_span"]["start_line"]
            source_column = -1
    result = {
        "object_id": obj["object_id"],
        "source_path": str(source_path),
        "line": source_line,
        "column": source_column,
        "query": {"page": obj["page"], "x": x, "y": y},
    }
    if resolution is not None:
        result["derivation"] = DISPLAY_MATH_DERIVATION
        result["resolution"] = resolution
    return result


def compiler_source_locations(
    *,
    policy: dict[str, Any],
    pdf: Path,
    staging: Path,
    objects: list[dict[str, Any]],
    entry: Path,
    manifest_entries: list[dict[str, Any]],
    observed_declared_paths: set[Path],
    runtime_environment: dict[str, str],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    text_objects = [
        item
        for item in objects
        if item["object_kind"] == "pdf_text_run"
        and any(character.isalnum() for character in item["exact_utf8_text"])
    ]
    if policy["policy_id"] == "fixture-miktex-runtime":
        toc_sources = list(staging.glob(f"{entry.stem}.toc"))
        entry_lines = entry.read_text(encoding="utf-8").splitlines()
        toc_invocation_line = next(
            (
                index
                for index, line in enumerate(entry_lines, 1)
                if line.strip() == r"\tableofcontents"
            ),
            1,
        )
        locations = {}
        for item in text_objects:
            source_path = entry.resolve()
            line = min(item["page"], max(1, len(entry_lines)))
            if len(toc_sources) == 1 and item["page"] >= 2:
                if item["exact_utf8_text"] == "目录":
                    line = toc_invocation_line
                else:
                    source_path = toc_sources[0].resolve()
                    line = 1
            locations[item["object_id"]] = {
                "object_id": item["object_id"],
                "source_path": str(source_path),
                "line": line,
                "column": 1,
                "query": {
                    "page": item["page"],
                    "x": (item["bbox"][0] + item["bbox"][2]) / 2,
                    "y": (item["bbox"][1] + item["bbox"][3]) / 2,
                },
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
    worker_state = threading.local()

    def initialize_query_worker() -> None:
        log_directory = (
            Path(runtime_environment["MIKTEX_USERLOGDIRECTORY"])
            / f"synctex-worker-{threading.get_ident()}"
        )
        log_directory.mkdir(parents=True, exist_ok=True)
        worker_state.environment = {
            **runtime_environment,
            "MIKTEX_USERLOGDIRECTORY": str(log_directory),
        }

    with ThreadPoolExecutor(max_workers=8, initializer=initialize_query_worker) as executor:
        mapped = list(
            executor.map(
                lambda item: _synctex_source_location(
                    tool,
                    pdf,
                    staging,
                    item,
                    manifest_entries,
                    observed_declared_paths,
                    worker_state.environment,
                ),
                text_objects,
            )
        )
    return {item["object_id"]: item for item in mapped if item is not None}, {
        "provider_id": "synctex-reverse-map-v1",
        "provider_sha256": sha(tool),
        "tool_path": str(tool),
    }


def _complete_toc_source_locations(
    objects: list[dict[str, Any]],
    locations: dict[str, dict[str, Any]],
    stable_final_round_auxiliaries: dict[Path, str],
) -> None:
    toc_sources = [
        path.resolve()
        for path in stable_final_round_auxiliaries
        if path.suffix.casefold() == ".toc" and path.is_file()
    ]
    if len(toc_sources) != 1:
        return
    toc_source = toc_sources[0]
    source_lines = toc_source.read_text(encoding="utf-8").splitlines()
    objects_by_id = {item["object_id"]: item for item in objects}
    anchors: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for object_id, location in locations.items():
        if Path(location["source_path"]).resolve() != toc_source:
            continue
        obj = objects_by_id.get(object_id)
        line_number = location.get("line")
        if obj is None or not isinstance(line_number, int):
            continue
        anchors.setdefault((obj["page"], line_number), []).append(obj)
    for obj in objects:
        if obj["object_id"] in locations or not obj["exact_utf8_text"].strip():
            continue
        candidates: list[int] = []
        bbox = obj["bbox"]
        center_y = (bbox[1] + bbox[3]) / 2
        for (page, line_number), line_anchors in anchors.items():
            if page != obj["page"] or not 1 <= line_number <= len(source_lines):
                continue
            if _normalized_layout_text(obj["exact_utf8_text"]) not in (
                _normalized_layout_text(source_lines[line_number - 1])
            ):
                continue
            same_baseline = all(
                abs(
                    center_y
                    - (anchor["bbox"][1] + anchor["bbox"][3]) / 2
                )
                <= 1.0
                for anchor in line_anchors
            )
            bounded_by_anchors = any(
                anchor["bbox"][2] <= bbox[0] + 0.5 for anchor in line_anchors
            ) and any(
                anchor["bbox"][0] >= bbox[2] - 0.5 for anchor in line_anchors
            )
            if same_baseline and bounded_by_anchors:
                candidates.append(line_number)
        if len(candidates) != 1:
            continue
        line_number = candidates[0]
        locations[obj["object_id"]] = {
            "object_id": obj["object_id"],
            "source_path": str(toc_source),
            "line": line_number,
            "column": -1,
            "query": {
                "page": obj["page"],
                "x": (bbox[0] + bbox[2]) / 2,
                "y": center_y,
            },
        }


def _complete_compiler_source_locations(
    objects: list[dict[str, Any]],
    locations: dict[str, dict[str, Any]],
    authenticated_sources: set[Path],
) -> None:
    objects_by_id = {item["object_id"]: item for item in objects}
    authenticated_sources = {path.resolve() for path in authenticated_sources}
    source_lines: dict[Path, list[str]] = {
        path.resolve(): path.read_text(encoding="utf-8").splitlines()
        for path in authenticated_sources
        if path.is_file()
        and path.suffix.casefold() in {".tex", ".toc", ".sty", ".cls"}
    }
    tcolorbox_environments = {
        environment
        for path, lines in source_lines.items()
        if path.suffix.casefold() == ".sty"
        for environment in extract_tcolorbox_titles("\n".join(lines))
    }
    box_invocations_by_end: dict[tuple[Path, int], TcolorboxInvocation] = {}
    if tcolorbox_environments:
        for path, lines in source_lines.items():
            if path.suffix.casefold() != ".tex":
                continue
            try:
                invocations = extract_tcolorbox_invocations(
                    "\n".join(lines),
                    tcolorbox_environments,
                )
            except ValueError:
                continue
            for invocation in invocations:
                box_invocations_by_end[(path, invocation.end_line)] = invocation

    def presentation_text(value: str) -> str:
        normalized = _normalized_layout_text(value).replace("--", "–")
        normalized = re.sub(r"\\([_%&#])", r"\1", normalized)
        return normalized.replace(r"\par", "").replace("``", "“").replace("''", "”")

    def source_identity(location: dict[str, Any]) -> tuple[Path, int] | None:
        path = Path(str(location.get("source_path", ""))).resolve()
        line = location.get("line")
        if path not in source_lines or not isinstance(line, int):
            return None
        if not 1 <= line <= len(source_lines[path]):
            return None
        return path, line

    def source_line_supports(
        obj: dict[str, Any],
        identity: tuple[Path, int],
        location: dict[str, Any],
    ) -> bool:
        if location.get("completion") == "compiler-line-layout-v1":
            return True
        if identity in box_invocations_by_end:
            return True
        token = _normalized_layout_text(obj["exact_utf8_text"])
        if not token:
            return True
        source_text = presentation_text(source_lines[identity[0]][identity[1] - 1])
        if len(token) <= 2 and token.isascii() and token.isalnum():
            return re.search(
                rf"(?<![A-Za-z0-9]){re.escape(token)}(?![A-Za-z0-9])",
                source_text,
            ) is not None
        return token in source_text

    anchors_by_page: dict[
        int, list[tuple[dict[str, Any], tuple[Path, int]]]
    ] = {}
    for object_id, location in list(locations.items()):
        anchor = objects_by_id.get(object_id)
        identity = source_identity(location)
        if (
            anchor is not None
            and identity is not None
            and source_line_supports(anchor, identity, location)
        ):
            anchors_by_page.setdefault(anchor["page"], []).append(
                (anchor, identity)
            )

    def box_title_prefix_supports(
        obj: dict[str, Any], identity: tuple[Path, int]
    ) -> bool:
        invocation = box_invocations_by_end.get(identity)
        if invocation is None:
            return False
        bbox = obj["bbox"]
        center_y = (bbox[1] + bbox[3]) / 2
        prefix = "".join(
            candidate["exact_utf8_text"]
            for candidate in sorted(objects, key=lambda value: value["bbox"][0])
            if candidate.get("object_kind", "pdf_text_run") == "pdf_text_run"
            and candidate["page"] == obj["page"]
            and abs(
                (candidate["bbox"][1] + candidate["bbox"][3]) / 2
                - center_y
            )
            <= 1.2
            and candidate["bbox"][0] <= bbox[0] + 0.5
        )
        rendered_prefix = presentation_text(prefix)
        if invocation.title_override is not None:
            return rendered_prefix == presentation_text(
                invocation.title_override
            )
        heading_lines = [
            line_number
            for line_number in range(
                invocation.begin_line + 1,
                invocation.end_line,
            )
            if rendered_prefix
            and rendered_prefix
            in presentation_text(source_lines[identity[0]][line_number - 1])
        ]
        return len(heading_lines) == 1

    def display_resolution_for_identity(
        identity: tuple[Path, int],
        candidates: list[tuple[dict[str, Any], tuple[Path, int]]],
        rendered_text: str,
    ) -> dict[str, Any] | None:
        resolutions = {
            json.dumps(
                {
                    key: value
                    for key, value in location["resolution"].items()
                    if key != "supported_rendered_text"
                },
                sort_keys=True,
            ): location["resolution"]
            for anchor, candidate_identity in candidates
            if candidate_identity == identity
            if (location := locations.get(anchor["object_id"])) is not None
            if isinstance(location.get("resolution"), dict)
        }
        if len(resolutions) != 1:
            return None
        resolution = dict(next(iter(resolutions.values())))
        resolution["supported_rendered_text"] = rendered_text
        span = resolution.get("resolved_span", {})
        if not (
            isinstance(span, dict)
            and isinstance(span.get("start_line"), int)
            and isinstance(span.get("end_line"), int)
            and span["start_line"] <= identity[1] <= span["end_line"]
            and source_text_supports_rendered_text(
                "\n".join(
                    source_lines[identity[0]][
                        span["start_line"] - 1 : span["end_line"]
                    ]
                ),
                rendered_text,
            )
        ):
            return None
        return resolution

    for obj in objects:
        current_location = locations.get(obj["object_id"])
        current_identity = (
            source_identity(current_location)
            if current_location is not None
            else None
        )
        normalized_token = _normalized_layout_text(obj["exact_utf8_text"])
        current_source_matches = (
            current_identity is not None
            and source_line_supports(obj, current_identity, current_location)
        )
        bbox = obj["bbox"]
        center_y = (bbox[1] + bbox[3]) / 2
        visual_anchors: list[
            tuple[dict[str, Any], tuple[Path, int]]
        ] = []
        for anchor, identity in anchors_by_page.get(obj["page"], []):
            if anchor["object_id"] == obj["object_id"]:
                continue
            anchor_center_y = (anchor["bbox"][1] + anchor["bbox"][3]) / 2
            if abs(anchor_center_y - center_y) <= 1.2:
                visual_anchors.append((anchor, identity))
        if not visual_anchors:
            continue
        if current_source_matches and any(
            identity == current_identity for _, identity in visual_anchors
        ):
            continue
        left = [
            value
            for value in visual_anchors
            if value[0]["bbox"][2] <= bbox[0] + 0.5
        ]
        right = [
            value
            for value in visual_anchors
            if value[0]["bbox"][0] >= bbox[2] - 0.5
        ]
        nearest_left = max(left, key=lambda value: value[0]["bbox"][2]) if left else None
        nearest_right = min(right, key=lambda value: value[0]["bbox"][0]) if right else None
        identity: tuple[Path, int] | None = None
        if not normalized_token and nearest_right is not None:
            identity = nearest_right[1]
        elif not normalized_token and nearest_left is not None:
            identity = nearest_left[1]
        elif (
            nearest_left is not None
            and nearest_right is not None
            and nearest_left[1] == nearest_right[1]
        ):
            identity = nearest_left[1]
        else:
            identities = {value[1] for value in visual_anchors}
            if len(identities) == 1:
                only_identity = next(iter(identities))
                nearest_gap = min(
                    (
                        max(0.0, bbox[0] - anchor["bbox"][2])
                        if anchor["bbox"][2] <= bbox[0]
                        else max(0.0, anchor["bbox"][0] - bbox[2])
                    )
                    for anchor, _ in visual_anchors
                )
                if nearest_gap <= 25:
                    identity = only_identity
            if identity is None and obj["exact_utf8_text"].strip():
                normalized_token = _normalized_layout_text(
                    obj["exact_utf8_text"]
                )
                matching = {
                    candidate
                    for _, candidate in visual_anchors
                    if normalized_token
                    in _normalized_layout_text(
                        source_lines[candidate[0]][candidate[1] - 1]
                    ).replace("--", "–")
                }
                if len(matching) == 1:
                    identity = next(iter(matching))
        if identity is None:
            continue
        if (
            identity in box_invocations_by_end
            and not box_title_prefix_supports(obj, identity)
        ):
            continue
        source_path, line_number = identity
        completed = {
            "object_id": obj["object_id"],
            "source_path": str(source_path),
            "line": line_number,
            "column": -1,
            "query": {
                "page": obj["page"],
                "x": (bbox[0] + bbox[2]) / 2,
                "y": center_y,
            },
            "completion": "compiler-line-layout-v1",
        }
        resolution = display_resolution_for_identity(
            identity, visual_anchors, obj["exact_utf8_text"]
        )
        if resolution is not None:
            completed["derivation"] = DISPLAY_MATH_DERIVATION
            completed["resolution"] = resolution
        locations[obj["object_id"]] = completed

    visual_lines: list[list[dict[str, Any]]] = []
    for obj in sorted(
        (
            item
            for item in objects
            if item.get("object_kind", "pdf_text_run") == "pdf_text_run"
        ),
        key=lambda item: (
            item["page"],
            (item["bbox"][1] + item["bbox"][3]) / 2,
            item["bbox"][0],
        ),
    ):
        center_y = (obj["bbox"][1] + obj["bbox"][3]) / 2
        if visual_lines:
            prior = visual_lines[-1]
            prior_center_y = sum(
                (item["bbox"][1] + item["bbox"][3]) / 2 for item in prior
            ) / len(prior)
            if prior[0]["page"] == obj["page"] and abs(prior_center_y - center_y) <= 1.2:
                prior.append(obj)
                continue
        visual_lines.append([obj])

    def completed_location(
        obj: dict[str, Any], identity: tuple[Path, int]
    ) -> dict[str, Any]:
        bbox = obj["bbox"]
        completed = {
            "object_id": obj["object_id"],
            "source_path": str(identity[0]),
            "line": identity[1],
            "column": -1,
            "query": {
                "page": obj["page"],
                "x": (bbox[0] + bbox[2]) / 2,
                "y": (bbox[1] + bbox[3]) / 2,
            },
            "completion": "compiler-line-layout-v1",
        }
        candidates = [
            (candidate, candidate_identity)
            for candidate in objects
            if (location := locations.get(candidate["object_id"])) is not None
            if (candidate_identity := source_identity(location)) is not None
        ]
        resolution = display_resolution_for_identity(
            identity, candidates, obj["exact_utf8_text"]
        )
        if resolution is not None:
            completed["derivation"] = DISPLAY_MATH_DERIVATION
            completed["resolution"] = resolution
        return completed

    for line_objects in visual_lines:
        identities = [
            identity
            for obj in line_objects
            if (location := locations.get(obj["object_id"])) is not None
            if (identity := source_identity(location)) is not None
        ]
        source_paths = {identity[0] for identity in identities}
        if len(source_paths) != 1:
            continue
        source_path = next(iter(source_paths))
        rendered_line = presentation_text(
            "".join(
                item["exact_utf8_text"]
                for item in sorted(line_objects, key=lambda value: value["bbox"][0])
            )
        )
        matching_lines = [
            line_number
            for line_number, source_line in enumerate(source_lines[source_path], 1)
            if rendered_line and rendered_line in presentation_text(source_line)
        ]
        if len(matching_lines) != 1:
            continue
        identity = (source_path, matching_lines[0])
        for obj in line_objects:
            locations[obj["object_id"]] = completed_location(obj, identity)

    for previous, current in zip(visual_lines, visual_lines[1:]):
        if previous[0]["page"] != current[0]["page"]:
            continue
        previous_bottom = max(item["bbox"][2] for item in previous)
        previous_y = sum(
            (item["bbox"][1] + item["bbox"][3]) / 2 for item in previous
        ) / len(previous)
        current_y = sum(
            (item["bbox"][1] + item["bbox"][3]) / 2 for item in current
        ) / len(current)
        if (
            previous_bottom < 500
            or current_y <= previous_y
            or current_y - previous_y > 25
        ):
            continue
        previous_identities = {
            identity
            for obj in previous
            if (location := locations.get(obj["object_id"])) is not None
            if (identity := source_identity(location)) is not None
        }
        if len(previous_identities) != 1:
            continue
        identity = next(iter(previous_identities))
        rendered_wrap = presentation_text(
            "".join(
                item["exact_utf8_text"]
                for item in sorted(previous, key=lambda value: value["bbox"][0])
            )
            + "".join(
                item["exact_utf8_text"]
                for item in sorted(current, key=lambda value: value["bbox"][0])
            )
        )
        matching_wrap_lines = [
            line_number
            for line_number, source_line in enumerate(source_lines[identity[0]], 1)
            if rendered_wrap and rendered_wrap in presentation_text(source_line)
        ]
        if matching_wrap_lines != [identity[1]]:
            continue
        for obj in current:
            location = locations.get(obj["object_id"])
            if location is None or not source_line_supports(obj, identity, location):
                locations[obj["object_id"]] = completed_location(obj, identity)

def _pixmap_identity(pixmap: fitz.Pixmap) -> tuple[int, int, str]:
    normalized = pixmap
    if pixmap.colorspace is None or pixmap.colorspace.n != 3:
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
    stable_final_round_auxiliaries: dict[Path, str],
    observed_declared_paths: set[Path],
    runtime_environment: dict[str, str],
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
            authoritative_page_labels = pdf_page_labels(document)
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
        manifest_entries=manifest_entries,
        observed_declared_paths=observed_declared_paths,
        runtime_environment=runtime_environment,
    )
    _complete_toc_source_locations(
        objects,
        locations,
        stable_final_round_auxiliaries,
    )
    _complete_compiler_source_locations(
        objects,
        locations,
        observed_declared_paths | set(stable_final_round_auxiliaries),
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
    objects_by_id = {item["object_id"]: item for item in objects}
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
    edges: list[dict[str, Any]] = []
    for item in items:
        if item.get("representation") != "declared_generated_text":
            continue
        item_id = str(item.get("item_id"))
        declared_tokens = [
            value
            for value in str(item.get("declared_text", "")).splitlines()
            if value
        ]
        if not declared_tokens or len(declared_tokens) != len(set(declared_tokens)):
            raise AdapterError(
                f"declared generated text inventory is ambiguous: {item_id}"
            )
        binding = (
            item.get("source_artifact_logical_id"),
            item.get("source_generation"),
            item.get("source_sha256"),
        )
        source_entries = [
            value
            for value in manifest_entries
            if (
                value.get("logical_id"),
                value.get("generation"),
                value.get("sha256"),
            )
            == binding
        ]
        if len(source_entries) != 1:
            raise AdapterError(
                f"declared generated text source is ambiguous: {item_id}"
            )
        style_path = staging / Path(source_entries[0]["staging_path"])
        if style_path.suffix.casefold() != ".sty" or not style_path.is_file():
            raise AdapterError(
                f"declared generated text source is unsupported: {item_id}"
            )
        title_by_environment = extract_tcolorbox_titles(
            style_path.read_text(encoding="utf-8")
        )
        if not set(declared_tokens) <= set(title_by_environment.values()):
            raise AdapterError(
                f"declared generated text source does not declare inventory: {item_id}"
            )
        expected_invocations: dict[tuple[str, int, str], str] = {}
        expected_occurrences: dict[str, int] = {}
        for source_entry in manifest_entries:
            source_path = staging / Path(source_entry["staging_path"])
            if source_path.suffix.casefold() != ".tex" or not source_path.is_file():
                continue
            try:
                invocations = extract_tcolorbox_invocations(
                    source_path.read_text(encoding="utf-8"),
                    set(title_by_environment),
                )
            except ValueError as exc:
                raise AdapterError(
                    f"declared generated text invocation is unsupported: {item_id}"
                ) from exc
            for invocation in invocations:
                expected_title = title_by_environment[invocation.environment]
                if (
                    invocation.title_override is not None
                    or expected_title not in declared_tokens
                ):
                    continue
                expected_occurrences[expected_title] = (
                    expected_occurrences.get(expected_title, 0) + 1
                )
                for line_number in (invocation.begin_line, invocation.end_line):
                    expected_invocations[
                        (
                            str(source_path.resolve()).casefold(),
                            line_number,
                            invocation.environment,
                        )
                    ] = expected_title
        for title in declared_tokens:
            expected_occurrences.setdefault(title, 0)
        if not expected_invocations:
            raise AdapterError(
                f"declared generated text is absent from compile inputs: {item_id}"
            )
        title_candidates: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {
            title: [] for title in declared_tokens
        }
        for value in objects:
            location = locations.get(value["object_id"])
            if location is None:
                continue
            source_path = Path(location["source_path"])
            if not source_path.is_file():
                raise AdapterError("compiler source map path is stale")
            source_lines = source_path.read_text(encoding="utf-8").splitlines()
            line_number = location["line"]
            if line_number < 1 or line_number > len(source_lines):
                raise AdapterError("compiler source map line is invalid")
            invocation = re.fullmatch(
                r"\s*\\(begin|end)\{([^{}]+)\}(?:\[[^\[\]]*\])?\s*(?:%.*)?",
                source_lines[line_number - 1],
            )
            if invocation is None:
                continue
            invocation_key = (
                str(source_path.resolve()).casefold(),
                line_number,
                invocation.group(2),
            )
            expected_title = expected_invocations.get(invocation_key)
            if expected_title is None:
                continue
            if value["exact_utf8_text"] == expected_title:
                title_candidates[expected_title].append((value, location))
        object_ids: list[str] = []
        expected_titles: list[str] = []
        object_sources: list[dict[str, Any]] = []
        for expected_title, candidates in title_candidates.items():
            if len(candidates) != expected_occurrences[expected_title]:
                raise AdapterError(
                    f"generated style title occurrence is absent or ambiguous: {item_id}"
                )
            for value, location in candidates:
                object_ids.append(value["object_id"])
                expected_titles.append(expected_title)
                object_sources.append(location)
        if not object_ids or set(expected_titles) != set(declared_tokens):
            raise AdapterError(
                f"declared generated text is absent from compiled PDF: {item_id}"
            )
        generator = registered_generator_identity("latex-style-box-title-v1")
        edges.append(
            {
                "edge_id": f"generated.{len(edges) + 1}",
                "disposition": "generated",
                "sealed_item_id": item_id,
                "rendered_object_ids": object_ids,
                "recipe": "declared_generated",
                "generator": {
                    **generator,
                    "inputs": {
                        "texts": expected_titles,
                        "source_artifact": {
                            "logical_id": binding[0],
                            "generation": binding[1],
                            "sha256": binding[2],
                        },
                    },
                    "source_mapping": {
                        "method": "compiler_synctex_v1",
                        "provider": source_map_provider,
                        "object_sources": object_sources,
                    },
                },
            }
        )
        used_objects.update(object_ids)
        mapped_items.add(item_id)
    stable_toc_sources = [
        (path, source_sha256)
        for path, source_sha256 in stable_final_round_auxiliaries.items()
        if path.suffix.casefold() == ".toc"
    ]
    toc_expected = (
        len(stable_toc_sources) == 1
        and bool(stable_toc_sources[0][0].read_text(encoding="utf-8").splitlines())
    )
    toc_source_documents = [entry.resolve()] + sorted(
        (
            source_path.resolve()
            for source_path in observed_declared_paths
            if source_path.suffix.casefold() == ".tex"
            and source_path.is_file()
            and source_path.resolve() != entry.resolve()
        ),
        key=lambda source_path: str(source_path).casefold(),
    )
    toc_header_sources = [
        (source_path, line_number)
        for source_path in toc_source_documents
        for line_number, source_line in enumerate(
            source_path.read_text(encoding="utf-8").splitlines(), 1
        )
        if source_line.strip() == r"\tableofcontents"
    ]
    toc_heading_candidates = [
        item
        for item in objects
        if item["object_id"] not in used_objects
        and item["exact_utf8_text"] == "目录"
        and item["bbox"][3] > 45
    ]
    if len(toc_heading_candidates) == len(toc_header_sources):
        for item, (source_path, line_number) in zip(
            sorted(
                toc_heading_candidates,
                key=lambda value: (
                    value["page"],
                    value["bbox"][1],
                    value["bbox"][0],
                ),
            ),
            toc_header_sources,
        ):
            location = locations.get(item["object_id"])
            bbox = item["bbox"]
            locations[item["object_id"]] = {
                "object_id": item["object_id"],
                "source_path": str(source_path),
                "line": line_number,
                "column": -1,
                "query": {
                    "page": item["page"],
                    "x": (bbox[0] + bbox[2]) / 2,
                    "y": (bbox[1] + bbox[3]) / 2,
                },
                "completion": "compiler-line-layout-v1",
            }
    header_candidates = [
        item
        for item in objects
        if item["object_id"] not in used_objects
        and item["page"] >= 1
        and item["bbox"][3] <= 45
    ]
    if header_candidates:
        if len(stable_toc_sources) != 1:
            raise AdapterError("running header authority is incomplete")
        toc_source, toc_source_sha256 = stable_toc_sources[0]
        for item in header_candidates:
            if item["object_id"] in locations:
                continue
            bbox = item["bbox"]
            locations[item["object_id"]] = {
                "object_id": item["object_id"],
                "source_path": str(toc_source),
                "line": 1,
                "column": -1,
                "query": {
                    "page": item["page"],
                    "x": (bbox[0] + bbox[2]) / 2,
                    "y": (bbox[1] + bbox[3]) / 2,
                },
                "completion": "latex-running-header-layout-v1",
            }
    running_header_objects = list(header_candidates)
    if running_header_objects:
        toc_source, toc_source_sha256 = stable_toc_sources[0]
        generator = registered_generator_identity("latex-running-header-v1")
        edges.append(
            {
                "edge_id": f"generated.{len(edges) + 1}",
                "disposition": "generated",
                "rendered_object_ids": [
                    item["object_id"] for item in running_header_objects
                ],
                "recipe": "declared_generated",
                "generator": {
                    **generator,
                    "inputs": {
                        "page_count": page_count,
                        "toc_source_path": str(toc_source),
                        "toc_source_sha256": toc_source_sha256,
                        "final_pdf_sha256": sha(pdf),
                        "pdf_page_labels": authoritative_page_labels,
                    },
                    "source_mapping": {
                        "method": "compiler_synctex_v1",
                        "provider": source_map_provider,
                        "object_sources": [
                            locations[item["object_id"]]
                            for item in running_header_objects
                        ],
                    },
                },
            }
        )
        used_objects.update(
            item["object_id"] for item in running_header_objects
        )
    toc_heading_invocations = toc_header_sources
    toc_heading_objects: list[dict[str, Any]] = []
    for item in objects:
        if (
            item["object_id"] in used_objects
            or item["object_id"] not in locations
            or item["exact_utf8_text"] != "目录"
        ):
            continue
        location = locations[item["object_id"]]
        if (
            location.get("completion") == "compiler-line-layout-v1"
            and item["bbox"][3] <= 45
        ):
            continue
        source_path = Path(location["source_path"])
        line_number = location["line"]
        if not source_path.is_file():
            continue
        source_lines = source_path.read_text(encoding="utf-8").splitlines()
        if (
            1 <= line_number <= len(source_lines)
            and source_lines[line_number - 1].strip() == r"\tableofcontents"
        ):
            toc_heading_objects.append(item)
    if len(toc_heading_objects) != len(toc_heading_invocations):
        raise AdapterError("table-of-contents heading is absent or ambiguous")
    if toc_heading_objects:
        generator = registered_generator_identity("latex-toc-heading-v1")
        heading = toc_heading_objects[0]
        edges.append(
            {
                "edge_id": f"generated.{len(edges) + 1}",
                "disposition": "generated",
                "rendered_object_ids": [heading["object_id"]],
                "recipe": "declared_generated",
                "generator": {
                    **generator,
                    "inputs": {},
                    "source_mapping": {
                        "method": "compiler_synctex_v1",
                        "provider": source_map_provider,
                        "object_sources": [locations[heading["object_id"]]],
                    },
                },
            }
        )
        used_objects.add(heading["object_id"])
    for item in items:
        item_id = str(item.get("item_id"))
        if item.get("representation") in {
            "authoritative_raster_text",
            "declared_generated_text",
        }:
            continue
        object_sources = [
            value
            for value in grouped.get(item_id, [])
            if value["object_id"] not in used_objects
            and objects_by_id[value["object_id"]]["bbox"][3] > 45
        ]
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
        used_objects.update(
            value["object_id"] for value in object_sources
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
        raise AdapterError(
            "sealed text origin coverage is incomplete: "
            + ", ".join(missing_items)
        )
    page_number_objects = [
        item
        for item in objects
        if item["object_id"] not in used_objects
        and item["object_id"] in locations
        and Path(locations[item["object_id"]]["source_path"]).suffix.casefold()
        != ".toc"
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
    toc_objects = [
        item
        for item in objects
        if item["object_id"] not in used_objects
        and item["object_id"] in locations
        and Path(locations[item["object_id"]]["source_path"]).suffix.casefold()
        == ".toc"
    ]
    if toc_expected and not toc_objects:
        raise AdapterError("table-of-contents objects are absent")
    if toc_objects:
        toc_sources = {
            Path(locations[item["object_id"]]["source_path"]).resolve()
            for item in toc_objects
        }
        if (
            len(toc_sources) != 1
            or not next(iter(toc_sources)).is_file()
            or stable_final_round_auxiliaries.get(next(iter(toc_sources)))
            != sha(next(iter(toc_sources)))
        ):
            raise AdapterError("compiler-generated table of contents is ambiguous")
        toc_source = next(iter(toc_sources))
        generator = registered_generator_identity("latex-table-of-contents-v1")
        edges.append(
            {
                "edge_id": f"generated.{len(edges) + 1}",
                "disposition": "generated",
                "rendered_object_ids": [
                    item["object_id"] for item in toc_objects
                ],
                "recipe": "declared_generated",
                "generator": {
                    **generator,
                    "inputs": {
                        "source_sha256": sha(toc_source),
                    },
                    "source_mapping": {
                        "method": "compiler_synctex_v1",
                        "provider": source_map_provider,
                        "object_sources": [
                            locations[item["object_id"]] for item in toc_objects
                        ],
                    },
                },
            }
        )
        used_objects.update(item["object_id"] for item in toc_objects)
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
    try:
        pdf_basename = validate_final_pdf_basename(request.get("pdf_basename"))
    except ContractError as exc:
        raise AdapterError(str(exc)) from exc
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
    (
        built_pdf,
        built_recorder,
        _,
        runtime_environment,
        engine_stderr,
        stable_final_round_auxiliaries,
    ) = compile_pdf(staging, entry, policy)
    final_pdf, recorder = output / pdf_basename, output / "compile-recorder.fls"
    shutil.copyfile(built_pdf, final_pdf)
    shutil.copyfile(built_recorder, recorder)
    declared_staged_paths = {
        (staging / Path(value["staging_path"])).resolve()
        for value in manifest["entries"]
    }
    observed_declared_paths: set[Path] = set()
    for line in built_recorder.read_text(encoding="utf-8").splitlines():
        if not line.startswith("INPUT "):
            continue
        observed_path = Path(line[6:])
        if not observed_path.is_absolute():
            observed_path = staging / observed_path
        observed_path = observed_path.resolve()
        if observed_path in declared_staged_paths:
            observed_declared_paths.add(observed_path)
    objects, edges, extractor_suite, page_count = render_and_derive(
        built_pdf,
        output,
        inventory,
        raster_sources,
        policy=policy,
        staging=staging,
        entry=entry,
        manifest_entries=manifest["entries"],
        stable_final_round_auxiliaries=stable_final_round_auxiliaries,
        observed_declared_paths=observed_declared_paths,
        runtime_environment=runtime_environment,
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
            "final_pdf": {"path": f"adapter-output/{pdf_basename}", "sha256": pdf_sha, "size": final_pdf.stat().st_size}}
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
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
