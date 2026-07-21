from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
from typing import Any
import uuid

from .contracts import ContractRegistry
from .errors import CompileDependencyGap, ContractError
from .utils import (
    canonical_json_bytes,
    read_json,
    require_contained_path,
    sha256_file,
    write_json_atomic,
)


_SHELL_ESCAPE = re.compile(r"\\(?:immediate\s*)?write18|\\ShellEscape|--shell-escape", re.IGNORECASE)
_DIRECT_REFERENCE = re.compile(
    r"\\(input|include|includegraphics|bibliography|documentclass|usepackage)"
    r"(?:\[[^\]]*\])?\{([^}]+)\}"
)
_GENERATED_SUFFIXES = frozenset(
    {".aux", ".toc", ".out", ".fls", ".log", ".xdv", ".bcf", ".run.xml"}
)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_FIXTURE_PREFIX = (
    _PROJECT_ROOT / "tests/video_workflow/fixtures/guarded-compile/fake_xelatex.py"
).resolve()
_FIXTURE_PACKAGE_INVENTORY = (
    _PROJECT_ROOT / "tests/video_workflow/fixtures/guarded-compile/package-inventory.json"
).resolve()
_MIKTEX_ENGINE = Path(r"D:\kits\MiKTex\miktex\bin\x64\xelatex.exe").resolve()
_MIKTEX_RUNTIME_ROOTS = (Path(r"D:\kits\MiKTex").resolve(),)
_TRUSTED_FONT_ROOTS = tuple(
    root.resolve()
    for root in (
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts",
        Path("/usr/share/fonts"),
        Path("/usr/local/share/fonts"),
    )
    if root.is_dir()
)


def _sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def runtime_policy_for_fixture(
    *,
    run_dir: Path,
    engine_executable: Path,
    engine_prefix_args: list[str],
    system_fonts: list[Path],
) -> dict[str, Any]:
    del run_dir
    executable = engine_executable.resolve()
    if executable != Path(sys.executable).resolve():
        raise ContractError("fixture Compile Runtime Policy requires the trusted Python engine")
    if [Path(value).resolve() for value in engine_prefix_args] != [_FIXTURE_PREFIX]:
        raise ContractError("fixture Compile Runtime Policy prefix is not registered")
    prefix_files = [Path(value).resolve() for value in engine_prefix_args if Path(value).is_file()]
    fonts = [font.resolve() for font in system_fonts]
    for font in fonts:
        if font.suffix.casefold() not in {".ttf", ".otf", ".ttc"} or not any(
            font == root or root in font.parents for root in _TRUSTED_FONT_ROOTS
        ):
            raise ContractError("fixture Compile Runtime Policy font is not a trusted system font")
    policy: dict[str, Any] = {
        "schema_name": "compile-runtime-policy",
        "schema_version": "1.0.0",
        "kernel_version": "2.0.0",
        "policy_id": "fixture-miktex-runtime",
        "policy_version": "1.0.0",
        "runtime_family": "miktex",
        "engine": {
            "name": "xelatex-fixture",
            "version": "fixture-1",
            "executable": str(executable),
            "sha256": _sha256_path(executable),
            "prefix_args": engine_prefix_args,
            "prefix_file_fingerprints": [
                {"path": str(path), "sha256": _sha256_path(path)} for path in prefix_files
            ],
        },
        "package_inventory": {
            "version": "fixture-1",
            "path": str(_FIXTURE_PACKAGE_INVENTORY),
            "sha256": _sha256_path(_FIXTURE_PACKAGE_INVENTORY),
        },
        "allowed_packages": ["fontspec", "graphicx"],
        "allowed_runtime_roots": [str(executable.parent)],
        "system_fonts": [
            {"path": str(font), "sha256": _sha256_path(font)} for font in fonts
        ],
        "automatic_package_install": False,
        "shell_escape": False,
        "dependency_discovery_policy_version": "recorder-closure-v1",
    }
    policy["policy_sha256"] = hashlib.sha256(canonical_json_bytes(policy)).hexdigest()
    return policy


def runtime_policy_for_miktex(
    *,
    package_inventory: Path,
    system_fonts: list[Path],
) -> dict[str, Any]:
    """Build the registered production policy from exact local MiKTeX identities."""
    if not _MIKTEX_ENGINE.is_file():
        raise ContractError("registered MiKTeX XeLaTeX engine is unavailable")
    inventory = package_inventory.resolve()
    if not inventory.is_file():
        raise ContractError("registered MiKTeX package inventory is unavailable")
    fonts = [font.resolve() for font in system_fonts]
    for font in fonts:
        if font.suffix.casefold() not in {".ttf", ".otf", ".ttc"} or not any(
            font == root or root in font.parents for root in _TRUSTED_FONT_ROOTS
        ):
            raise ContractError("MiKTeX Runtime Policy font is not a trusted system font")
    policy: dict[str, Any] = {
        "schema_name": "compile-runtime-policy",
        "schema_version": "1.0.0",
        "kernel_version": "2.0.0",
        "policy_id": "miktex-xelatex-runtime",
        "policy_version": "1.0.0",
        "runtime_family": "miktex",
        "engine": {
            "name": "xelatex",
            "version": "miktex-registered",
            "executable": str(_MIKTEX_ENGINE),
            "sha256": _sha256_path(_MIKTEX_ENGINE),
            "prefix_args": [],
            "prefix_file_fingerprints": [],
        },
        "package_inventory": {
            "version": "miktex-exact-files-v1",
            "path": str(inventory),
            "sha256": _sha256_path(inventory),
        },
        "allowed_packages": ["fontspec", "graphicx"],
        "allowed_runtime_roots": [str(root) for root in _MIKTEX_RUNTIME_ROOTS],
        "system_fonts": [
            {"path": str(font), "sha256": _sha256_path(font)} for font in fonts
        ],
        "automatic_package_install": False,
        "shell_escape": False,
        "dependency_discovery_policy_version": "recorder-closure-v1",
    }
    policy["policy_sha256"] = hashlib.sha256(canonical_json_bytes(policy)).hexdigest()
    return policy


class GuardedCompileProvider:
    """Manifest-only diagnostic XeLaTeX provider with recorder closure checks."""

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir.resolve()

    @staticmethod
    def static_preflight_text(text: str) -> None:
        if _SHELL_ESCAPE.search(text):
            raise ContractError("LaTeX shell escape is forbidden")
        for _, reference in _DIRECT_REFERENCE.findall(text):
            for item in reference.split(","):
                candidate = item.strip().replace("\\", "/")
                if not candidate:
                    continue
                pure = PurePosixPath(candidate)
                if pure.is_absolute() or re.match(r"^[A-Za-z]:", candidate):
                    raise ContractError("LaTeX direct reference uses an absolute path")
                if ".." in pure.parts:
                    raise ContractError("LaTeX direct reference escapes the staging boundary")

    def validate_manifest_entry_path(self, source: str, staging: str) -> tuple[Path, PurePosixPath]:
        source_value = source.replace("\\", "/")
        source_pure = PurePosixPath(source_value)
        if source_pure.is_absolute() or re.match(r"^[A-Za-z]:", source_value):
            raise ContractError("Compile Manifest source path is absolute")
        if ".." in source_pure.parts:
            raise ContractError("Compile Manifest source path escapes the run")
        staging_value = staging.replace("\\", "/")
        staging_pure = PurePosixPath(staging_value)
        if staging_pure.is_absolute() or re.match(r"^[A-Za-z]:", staging_value):
            raise ContractError("Compile Manifest staging path is absolute")
        if ".." in staging_pure.parts:
            raise ContractError("Compile Manifest staging path escapes the attempt")
        source_path = Path(os.path.abspath(self.run_dir / Path(*source_pure.parts)))
        try:
            source_path.relative_to(self.run_dir)
        except ValueError as exc:
            raise ContractError("Compile Manifest source path escapes the run") from exc
        require_contained_path(
            source_path,
            self.run_dir,
            purpose="Compile Manifest source",
            error_type=ContractError,
            leaf_kind="file",
            require_single_link=True,
        )
        return source_path, staging_pure

    def _validate_runtime_policy(self, policy: dict[str, Any]) -> dict[str, str]:
        required = {
            "schema_name", "schema_version", "policy_id", "policy_version",
            "runtime_family", "engine", "package_inventory", "system_fonts",
            "allowed_packages", "allowed_runtime_roots",
            "automatic_package_install", "shell_escape",
            "dependency_discovery_policy_version", "policy_sha256",
        }
        if not required.issubset(policy):
            raise ContractError("Compile Runtime Policy is incomplete")
        if policy["runtime_family"] != "miktex":
            raise ContractError("Compile Runtime Policy must identify MiKTeX")
        if policy["policy_id"] not in {"fixture-miktex-runtime", "miktex-xelatex-runtime"}:
            raise ContractError("Compile Runtime Policy is not registered")
        if policy["automatic_package_install"] is not False:
            raise ContractError("automatic package installation must be disabled")
        if policy["shell_escape"] is not False:
            raise ContractError("shell escape must be disabled")
        unbound = dict(policy)
        expected = unbound.pop("policy_sha256")
        if hashlib.sha256(canonical_json_bytes(unbound)).hexdigest() != expected:
            raise ContractError("Compile Runtime Policy fingerprint is stale")
        engine = Path(policy["engine"]["executable"]).resolve()
        if not engine.is_file() or _sha256_path(engine) != policy["engine"]["sha256"]:
            raise ContractError("Compile Runtime Policy engine fingerprint is stale")
        runtime_roots = [Path(value).resolve() for value in policy["allowed_runtime_roots"]]
        for root in runtime_roots:
            if not root.is_dir() or root == Path(root.anchor) or len(root.parts) < 3:
                raise ContractError("Compile Runtime Policy contains an unsafe runtime root")
            for forbidden in (self.run_dir, _PROJECT_ROOT):
                if root == forbidden or root in forbidden.parents or forbidden in root.parents:
                    raise ContractError("Compile Runtime Policy root overlaps project authority")
        if not any(engine == root or root in engine.parents for root in runtime_roots):
            raise ContractError("Compile Runtime Policy engine is outside registered runtime roots")
        dependencies = [
            *policy["engine"].get("prefix_file_fingerprints", []),
            *policy["system_fonts"],
        ]
        for item in dependencies:
            path = Path(item["path"]).resolve()
            if not path.is_file() or _sha256_path(path) != item["sha256"]:
                raise ContractError("Compile Runtime Policy dependency fingerprint is stale")
        for item in policy["system_fonts"]:
            path = Path(item["path"]).resolve()
            if path.suffix.casefold() not in {".ttf", ".otf", ".ttc"} or not any(
                path == root or root in path.parents for root in _TRUSTED_FONT_ROOTS
            ):
                raise ContractError("Compile Runtime Policy font is outside trusted font roots")
        fixture_policy = policy["policy_id"] == "fixture-miktex-runtime"
        if fixture_policy:
            if engine != Path(sys.executable).resolve():
                raise ContractError("registered fixture engine identity changed")
            prefix_args = [Path(value).resolve() for value in policy["engine"]["prefix_args"]]
            if prefix_args != [_FIXTURE_PREFIX]:
                raise ContractError("registered fixture engine prefix changed")
        else:
            if engine != _MIKTEX_ENGINE or policy["engine"]["prefix_args"] != []:
                raise ContractError("registered MiKTeX engine identity changed")
            if runtime_roots != list(_MIKTEX_RUNTIME_ROOTS):
                raise ContractError("registered MiKTeX runtime roots changed")
        inventory_binding = policy["package_inventory"]
        inventory_path = Path(inventory_binding["path"]).resolve()
        if fixture_policy and inventory_path != _FIXTURE_PACKAGE_INVENTORY:
            raise ContractError("Compile package inventory is not registered")
        if _sha256_path(inventory_path) != inventory_binding["sha256"]:
            raise ContractError("Compile package inventory fingerprint is stale")
        inventory = read_json(inventory_path)
        if inventory.get("schema_version") != 1 or not isinstance(inventory.get("files"), list):
            raise ContractError("Compile package inventory shape is invalid")
        registered: dict[str, str] = {}
        for item in inventory["files"]:
            if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
                raise ContractError("Compile package inventory entry is invalid")
            path = Path(item["path"]).resolve()
            if not path.is_file() or _sha256_path(path) != item["sha256"]:
                raise ContractError("Compile package inventory entry drifted")
            if not any(path == root or root in path.parents for root in runtime_roots):
                raise ContractError("Compile package inventory entry escapes runtime roots")
            registered[str(path).casefold()] = item["sha256"]
        for item in policy["engine"].get("prefix_file_fingerprints", []):
            registered[str(Path(item["path"]).resolve()).casefold()] = item["sha256"]
        return registered

    def _authenticate_manifest(self, manifest: dict[str, Any]) -> None:
        run_record = read_json(self.run_dir / "workflow/run.json")
        state = read_json(self.run_dir / "workflow/production-state.json")
        contracts = ContractRegistry(_PROJECT_ROOT)
        contracts.validate("production-state", state)
        if manifest.get("run_id") != run_record.get("run_id") or state.get("run_id") != run_record.get("run_id"):
            raise ContractError("Compile Manifest belongs to another Run")
        integration = state.get("artifacts", {}).get("integration_manifest")
        if integration is None or manifest.get("integration_manifest_generation") != {
            "generation": integration.get("generation"),
            "sha256": integration.get("sha256"),
        }:
            raise CompileDependencyGap("Compile Manifest binds a stale Integration Manifest")
        integration_path = self.run_dir / integration["path"]
        if (
            not integration_path.is_file()
            or integration_path.stat().st_size != integration["size"]
            or _sha256_path(integration_path) != integration["sha256"]
        ):
            raise CompileDependencyGap("authoritative Integration Manifest drifted")
        integration_value = read_json(integration_path)
        contracts.validate("integration-manifest", integration_value)
        for named in [
            integration_value["main"],
            *integration_value["sections"],
            *integration_value["figures"],
        ]:
            authoritative = state.get("artifacts", {}).get(named["logical_id"])
            if authoritative is None or named != {
                "logical_id": named["logical_id"],
                **authoritative,
            }:
                raise CompileDependencyGap(
                    "Integration Manifest contains an uncommitted Artifact Generation"
                )
        source = run_record.get("artifact_generations", {}).get("source_manifest")
        if state.get("source_binding") != {
            "logical_id": "source_manifest",
            "generation": source.get("generation") if isinstance(source, dict) else None,
            "sha256": source.get("sha256") if isinstance(source, dict) else None,
        }:
            raise CompileDependencyGap("Production State binds a stale Source Manifest")
        if integration_value["source_binding"] != state["source_binding"]:
            raise CompileDependencyGap("Integration Manifest binds a stale Source Manifest")
        logical_ids: set[str] = set()
        for entry in manifest["entries"]:
            logical_id = entry["logical_id"]
            if logical_id in logical_ids:
                raise ContractError("Compile Manifest repeats a logical Artifact Generation")
            logical_ids.add(logical_id)
            authoritative = state.get("artifacts", {}).get(logical_id)
            expected = {
                key: authoritative.get(key) if isinstance(authoritative, dict) else None
                for key in ("generation", "sha256", "size", "producer", "path")
            }
            actual = {
                "generation": entry["generation"],
                "sha256": entry["sha256"],
                "size": entry["size"],
                "producer": entry["producer"],
                "path": entry["source_path"],
            }
            if actual != expected:
                raise CompileDependencyGap(
                    f"Compile Manifest entry is not a committed Artifact Generation: {logical_id}"
                )

    @staticmethod
    def _validate_declared_references(
        text: str,
        declared_destinations: set[str],
        allowed_packages: set[str],
    ) -> None:
        extension_by_command = {
            "input": ("", ".tex"),
            "include": ("", ".tex"),
            "includegraphics": ("", ".png", ".pdf", ".jpg", ".jpeg"),
            "bibliography": ("", ".bib"),
            "documentclass": ("", ".cls"),
            "usepackage": ("", ".sty"),
        }
        destination_names = {
            PurePosixPath(value).name.casefold() for value in declared_destinations
        }
        for command, references in _DIRECT_REFERENCE.findall(text):
            for reference in references.split(","):
                value = reference.strip().replace("\\", "/")
                if not value:
                    continue
                if command in {"documentclass", "usepackage"} and value.casefold() in allowed_packages:
                    continue
                candidates = {
                    f"{value}{extension}".casefold()
                    for extension in extension_by_command[command]
                    if extension == "" or not PurePosixPath(value).suffix
                }
                candidates.add(value.casefold())
                if not any(
                    candidate in declared_destinations
                    or PurePosixPath(candidate).name in destination_names
                    for candidate in candidates
                ):
                    raise CompileDependencyGap(
                        f"undeclared direct compile input: {value}"
                    )

    def compile(self, manifest_path: Path, runtime_policy: dict[str, Any]) -> dict[str, Any]:
        registered_runtime_inputs = self._validate_runtime_policy(runtime_policy)
        manifest_path = manifest_path.resolve()
        require_contained_path(
            manifest_path,
            self.run_dir,
            purpose="Compile Manifest",
            error_type=ContractError,
            leaf_kind="file",
            require_single_link=True,
        )
        manifest = read_json(manifest_path)
        if manifest.get("schema_name") != "compile-manifest" or manifest.get("mode") != "diagnostic":
            raise ContractError("Compile Manifest does not authorize diagnostic compilation")
        entries = manifest.get("entries")
        if not isinstance(entries, list) or not entries:
            raise ContractError("Compile Manifest entries are missing")
        if sum(item.get("role") == "entry_tex" for item in entries) != 1:
            raise ContractError("Compile Manifest must contain exactly one entry TeX")
        if manifest.get("runtime_policy_sha256") != runtime_policy["policy_sha256"]:
            raise ContractError("Compile Manifest Runtime Policy binding is stale")
        self._authenticate_manifest(manifest)

        attempt_dir = self.run_dir / "待删除" / "kernel-compile" / uuid.uuid4().hex
        staging = attempt_dir / "staging"
        staging.mkdir(parents=True, exist_ok=False)
        staged: dict[str, dict[str, Any]] = {}
        staged_paths: list[Path] = []
        source_identities: set[str] = set()
        destination_identities: set[str] = set()
        for entry in entries:
            source_path, relative = self.validate_manifest_entry_path(entry["source_path"], entry["staging_path"])
            if not source_path.is_file():
                raise CompileDependencyGap("declared compile input is missing")
            if _sha256_path(source_path) != entry["sha256"] or source_path.stat().st_size != entry["size"]:
                raise CompileDependencyGap("declared compile input fingerprint is stale")
            source_identity = str(source_path).casefold()
            destination_identity = relative.as_posix().casefold()
            if source_identity in source_identities or destination_identity in destination_identities:
                raise ContractError("Compile Manifest contains a source or staging collision")
            source_identities.add(source_identity)
            destination_identities.add(destination_identity)
            destination = staging / Path(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_path, destination)
            staged[str(destination.resolve()).casefold()] = entry
            staged_paths.append(destination.resolve())

        for path in staged_paths:
            if path.suffix.casefold() in {".tex", ".cls", ".sty"}:
                text = path.read_text(encoding="utf-8")
                self.static_preflight_text(text)
                self._validate_declared_references(
                    text,
                    destination_identities,
                    {value.casefold() for value in runtime_policy["allowed_packages"]},
                )

        entry = next(item for item in entries if item["role"] == "entry_tex")
        engine = runtime_policy["engine"]
        command = [
            engine["executable"], *engine.get("prefix_args", []),
            "--disable-installer", "-no-shell-escape", "-recorder",
            "-interaction=nonstopmode", entry["staging_path"],
        ]
        environment = dict(os.environ)
        environment["MIKTEX_ENABLE_INSTALLER"] = "0"
        environment["VIDEO2PDF_FIXTURE_FONTS"] = os.pathsep.join(
            item["path"] for item in runtime_policy["system_fonts"]
        )
        completed = subprocess.run(
            command, cwd=staging, env=environment, text=True, encoding="utf-8",
            errors="replace", capture_output=True, check=False, timeout=120,
        )
        if completed.returncode != 0:
            raise CompileDependencyGap(
                "guarded diagnostic compile failed",
                data={"exit_code": completed.returncode},
            )
        stem = PurePosixPath(entry["staging_path"]).stem
        recorder = staging / f"{stem}.fls"
        pdf = staging / f"{stem}.pdf"
        if not recorder.is_file() or not pdf.is_file():
            raise CompileDependencyGap("compile provider omitted recorder or PDF evidence")

        allowed_fonts = {
            str(Path(item["path"]).resolve()).casefold(): item
            for item in runtime_policy["system_fonts"]
        }
        observed: list[dict[str, Any]] = []
        gaps: list[str] = []
        for line in recorder.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.startswith("INPUT "):
                continue
            recorded = Path(line[6:])
            observed_path = (
                recorded.resolve()
                if recorded.is_absolute()
                else (staging / recorded).resolve()
            )
            if not observed_path.is_file():
                gaps.append(str(observed_path))
                continue
            identity = str(observed_path).casefold()
            if identity in staged:
                classification = "manifest_entry"
            elif identity in allowed_fonts:
                if _sha256_path(observed_path) != allowed_fonts[identity]["sha256"]:
                    gaps.append(str(observed_path))
                    continue
                classification = "registered_system_font"
            elif identity in registered_runtime_inputs:
                if _sha256_path(observed_path) != registered_runtime_inputs[identity]:
                    gaps.append(str(observed_path))
                    continue
                classification = "registered_runtime_dependency"
            else:
                try:
                    observed_path.relative_to(staging)
                    inside = True
                except ValueError:
                    inside = False
                if inside and observed_path.suffix.casefold() in _GENERATED_SUFFIXES:
                    classification = "attempt_generated_auxiliary"
                else:
                    gaps.append(str(observed_path))
                    continue
            observed.append({"path": str(observed_path), "classification": classification})
        if gaps:
            raise CompileDependencyGap(
                "recorder found undeclared compile inputs", data={"gaps": gaps}
            )

        output_dir = self.run_dir / "待删除" / "diagnostic-compile"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_pdf = output_dir / "main.pdf"
        shutil.copyfile(pdf, output_pdf)
        generated_outputs = []
        for generated_path in sorted(staging.rglob("*")):
            resolved = generated_path.resolve()
            if not generated_path.is_file() or str(resolved).casefold() in staged:
                continue
            generated_outputs.append(
                {
                    "path": generated_path.relative_to(staging).as_posix(),
                    "sha256": _sha256_path(generated_path),
                    "size": generated_path.stat().st_size,
                }
            )
        report = {
            "schema_name": "diagnostic-compile-report",
            "schema_version": "1.0.0",
            "kernel_version": "2.0.0",
            "mode": "diagnostic",
            "status": "pass",
            "delivery_authority": False,
            "compile_manifest_path": str(manifest_path.resolve()),
            "compile_manifest_sha256": _sha256_path(manifest_path),
            "runtime_policy_sha256": runtime_policy["policy_sha256"],
            "engine": {"name": engine["name"], "version": engine["version"], "sha256": engine["sha256"]},
            "invocation": {
                "automatic_package_install": False,
                "shell_escape": False,
                "recorder": True,
                "argv": command,
            },
            "executed_passes": [
                {"pass": 1, "exit_code": 0, "recorder_sha256": _sha256_path(recorder)}
            ],
            "generated_outputs": generated_outputs,
            "dependency_closure": {
                "complete": True,
                "inputs": observed,
                "recorder_sha256": _sha256_path(recorder),
            },
            "pdf": {
                "path": str(output_pdf),
                "sha256": _sha256_path(output_pdf),
                "size": output_pdf.stat().st_size,
            },
        }
        report_path = self.run_dir / "review/latex/diagnostic-compile-report.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(report_path, report)
        return {"report": report, "report_path": report_path, "pdf_path": output_pdf}
