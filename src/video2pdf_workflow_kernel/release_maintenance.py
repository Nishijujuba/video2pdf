from __future__ import annotations

import importlib.util
import json
from contextlib import contextmanager, nullcontext
from pathlib import Path
import re
import subprocess
import sys
from types import ModuleType
from typing import Any, Iterator

from .errors import ContractError
from .cutover_retirement import project_maintenance_fence
from .evidence import (
    EvidenceSupportError,
    clone_shared_repository,
    git_output,
)
from .global_gate_exit_evidence import (
    ExitEvidenceValidationError,
    validate_global_gate_exit_evidence,
)
from .release_profile import WorkflowReleaseProfile
from .utils import read_json, write_json_atomic


PROFILE_RELATIVE_PATH = Path("config/workflow-release-profile.v1.json")
EXPECTED_EVIDENCE_SLICES = {
    "bilibili": {"number": 12, "name": "bilibili-platform-kernel-cutover"},
    "youtube": {"number": 13, "name": "youtube-platform-kernel-cutover"},
    "batch": {"number": 14, "name": "batch-projection-cutover"},
}

_PUBLICATION_VALIDATOR_RUNNER = """\
from pathlib import Path
import inspect
import json
import os
import runpy
import sys

namespace = runpy.run_path(sys.argv[1])
validator = namespace["validate_manifest"]
if "verify_at" not in inspect.signature(validator).parameters:
    snapshot_root = Path(sys.argv[1]).resolve().parents[1]
    manifest_path = Path(sys.argv[2]).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    recorded_roots = []
    referenced_json = []

    def collect_records(node):
        if isinstance(node, dict):
            cwd = node.get("cwd")
            if isinstance(cwd, str) and Path(cwd).is_absolute():
                recorded_roots.append(Path(cwd))
            for key, value in node.items():
                if (
                    key == "path"
                    and isinstance(value, str)
                    and not Path(value).is_absolute()
                    and value.endswith(".json")
                ):
                    referenced_json.append(snapshot_root / value)
                collect_records(value)
        elif isinstance(node, list):
            for value in node:
                collect_records(value)

    collect_records(manifest)
    inspected = set()
    while referenced_json:
        record_path = referenced_json.pop()
        if record_path in inspected or not record_path.is_file():
            continue
        inspected.add(record_path)
        try:
            collect_records(json.loads(record_path.read_text(encoding="utf-8")))
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass

    global_validator = namespace.get("validate_global_gate_exit_evidence")
    if global_validator is not None:
        mirror_specs = global_validator.__globals__.get("MIRROR_SPECS", ())
        for check, spec in zip(manifest.get("mirror_checks", ()), mirror_specs):
            for field, relative in (
                ("source_path", spec[0]),
                ("mirror_path", spec[1]),
            ):
                recorded = check.get(field)
                if not isinstance(recorded, str) or not Path(recorded).is_absolute():
                    continue
                relative_parts = Path(relative).parts
                recorded_path = Path(recorded)
                if tuple(
                    os.path.normcase(part)
                    for part in recorded_path.parts[-len(relative_parts):]
                ) != tuple(os.path.normcase(part) for part in relative_parts):
                    print(
                        "INVALID: first_failing_gate=historical_evidence; "
                        "error_code=historical_evidence_location_inconsistent; "
                        "recorded mirror path contradicts publication authority",
                        file=sys.stderr,
                    )
                    raise SystemExit(1)
                root = recorded_path
                for _ in relative_parts:
                    root = root.parent
                recorded_roots.append(root)

    roots_by_identity = {
        os.path.normcase(os.path.normpath(str(root))): root
        for root in recorded_roots
    }
    if len(roots_by_identity) != 1:
        print(
            "INVALID: first_failing_gate=historical_evidence; "
            "error_code=historical_evidence_location_inconsistent; "
            "publication evidence does not bind one historical project root",
            file=sys.stderr,
        )
        raise SystemExit(1)
    original_root = next(iter(roots_by_identity.values()))

    def relocate(node):
        if isinstance(node, dict):
            return {key: relocate(value) for key, value in node.items()}
        if isinstance(node, list):
            return [relocate(value) for value in node]
        if not isinstance(node, str) or not Path(node).is_absolute():
            return node
        candidate = Path(node)
        try:
            relative = candidate.relative_to(original_root)
        except ValueError:
            return node
        return str(snapshot_root / relative)

    class RelocatingJson:
        def __getattr__(self, name):
            return getattr(json, name)

        @staticmethod
        def loads(value, *args, **kwargs):
            return relocate(json.loads(value, *args, **kwargs))

        @staticmethod
        def load(value, *args, **kwargs):
            return relocate(json.load(value, *args, **kwargs))

    relocating_json = RelocatingJson()
    namespace["json"] = relocating_json
    validator.__globals__["json"] = relocating_json
    namespace["main"].__globals__["json"] = relocating_json
    for module in tuple(sys.modules.values()):
        module_file = getattr(module, "__file__", None)
        if not isinstance(module_file, str):
            continue
        try:
            Path(module_file).resolve().relative_to(snapshot_root)
        except (OSError, ValueError):
            continue
        if getattr(module, "json", None) is json:
            module.json = relocating_json
    raise SystemExit(namespace["main"]([sys.argv[2]]))
try:
    validator(
        Path(sys.argv[2]).resolve(),
        schema_only=False,
        pre_publication=False,
        verify_at="publication",
    )
except namespace["EvidenceError"] as exc:
    print(
        "INVALID: "
        f"first_failing_gate={exc.first_failing_gate}; "
        f"error_code={exc.error_code}; {exc}",
        file=sys.stderr,
    )
    raise SystemExit(1)
print(f"VALID: {Path(sys.argv[2]).resolve()}")
"""


def _reject(message: str, gate: str, code: str) -> None:
    raise ContractError(
        message,
        data={"first_failing_gate": gate, "error_code": code},
    )


class ReleaseMaintenance:
    """Own complete historical validation and atomic release-Profile publication."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.profiles = WorkflowReleaseProfile(self.project_root)

    @property
    def published_profile_path(self) -> Path:
        return self.project_root / PROFILE_RELATIVE_PATH

    def require_for_admission(
        self, *, profile: Path, capability: str
    ) -> dict[str, Any]:
        """Validate ordinary capability without reading historical evidence."""

        value = self.profiles.load(profile.resolve())
        if capability not in {"bilibili", "youtube", "batch"}:
            _reject(
                "Workflow Release Profile capability is unsupported",
                "platform_activation",
                "workflow_release_capability_unsupported",
            )
        if (
            value["capabilities"]["global_gate"] != "active"
            or value["capabilities"][capability] != "active"
        ):
            _reject(
                "Workflow Release Profile capability is inactive",
                "platform_activation",
                "workflow_release_capability_inactive",
            )
        return value

    def publish(
        self,
        *,
        candidate_profile: Path,
        global_gate_exit_evidence: Path,
        bilibili_exit_evidence: Path,
        youtube_exit_evidence: Path,
        batch_exit_evidence: Path,
        control_store_root: Path | None = None,
        historical_release: bool = False,
    ) -> dict[str, Any]:
        fence = (
            nullcontext()
            if control_store_root is None
            else project_maintenance_fence(control_store_root)
        )
        with fence:
            profile_path = candidate_profile.resolve()
            profile, evidence = self._validate(
                profile=profile_path,
                historical_release=historical_release,
                global_gate=global_gate_exit_evidence,
                bilibili=bilibili_exit_evidence,
                youtube=youtube_exit_evidence,
                batch=batch_exit_evidence,
            )

            output = self.published_profile_path
            activation_path = output.parent / "workflow-admission-activation.v1.json"
            if (
                control_store_root is not None
                and activation_path.is_file()
                and output.is_file()
                and read_json(output) != profile
            ):
                _reject(
                    "A current Profile can only change through coordinated activation",
                    "project_maintenance_fence",
                    "workflow_release_activation_required",
            )
            output.parent.mkdir(parents=True, exist_ok=True)
            if not output.is_file() or read_json(output) != profile:
                write_json_atomic(output, profile)
            return {
                "profile_path": str(output),
                "release_id": profile["release_id"],
                "capabilities": profile["capabilities"],
                "historical_evidence": evidence,
                "runtime_activation_changed": False,
            }

    def audit(
        self,
        *,
        profile: Path,
        global_gate_exit_evidence: Path,
        bilibili_exit_evidence: Path,
        youtube_exit_evidence: Path,
        batch_exit_evidence: Path,
        historical_release: bool = False,
    ) -> dict[str, Any]:
        profile_path = profile.resolve()
        value, evidence = self._validate(
            profile=profile_path,
            historical_release=historical_release,
            global_gate=global_gate_exit_evidence,
            bilibili=bilibili_exit_evidence,
            youtube=youtube_exit_evidence,
            batch=batch_exit_evidence,
        )
        return {
            "profile_path": str(profile_path),
            "release_id": value["release_id"],
            "capabilities": value["capabilities"],
            "historical_evidence": evidence,
            "profile_published": False,
            "runtime_authority_changed": False,
        }

    def _validate(
        self,
        *,
        profile: Path,
        historical_release: bool = False,
        **evidence_paths: Path,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        value = self.profiles.load(profile)
        evidence = (
            self._validate_historical_release_package(**evidence_paths)
            if historical_release
            else self._validate_release_package(**evidence_paths)
        )
        return value, evidence

    def _validate_historical_release_package(
        self, **paths: Path
    ) -> dict[str, Any]:
        """Audit the complete release package against its publication tree.

        The package is validated with the same full validator set as candidate
        publication, executed inside a retained repository snapshot of each
        publication commit: every file-content gate (schema registry, schema,
        slice constants, mirror files, command logs, persisted terminal
        artifacts, guarded-delivery reports) reads the publication state,
        never the drifting worktree. A valid historical package therefore
        cannot be rejected because current files moved or evolved, and an
        incomplete or tampered manifest cannot pass on shallow git checks
        alone.
        """

        evidence: dict[str, Any] = {}
        for capability in ("global_gate", "bilibili", "youtube", "batch"):
            path = paths[capability].resolve()
            if not path.is_relative_to(self.project_root):
                _reject(
                    f"{capability} Exit Evidence escapes the project",
                    "evidence_paths",
                    f"{capability}_exit_evidence_invalid",
                )
            try:
                value = read_json(path)
                if not isinstance(value, dict):
                    raise TypeError("Exit Evidence must be a JSON object")
                relative = path.relative_to(self.project_root).as_posix()
                head_blob = git_output(
                    self.project_root, "rev-parse", f"HEAD:{relative}"
                )
                worktree_blob = git_output(
                    self.project_root,
                    "hash-object",
                    f"--path={relative}",
                    "--",
                    relative,
                )
                publication = git_output(
                    self.project_root,
                    "log",
                    "-1",
                    "--format=%H",
                    "HEAD",
                    "--",
                    relative,
                )
            except (
                OSError,
                UnicodeError,
                json.JSONDecodeError,
                EvidenceSupportError,
                TypeError,
            ) as exc:
                _reject(
                    f"{capability} historical Exit Evidence is invalid: {exc}",
                    "historical_evidence",
                    f"{capability}_exit_evidence_invalid",
                )
            if head_blob != worktree_blob:
                _reject(
                    f"{capability} historical Exit Evidence is uncommitted or dirty",
                    "historical_evidence",
                    f"{capability}_exit_evidence_invalid",
                )
            implementation_commit = value.get("implementation_commit")
            if not isinstance(implementation_commit, str):
                _reject(
                    f"{capability} historical Exit Evidence lacks an implementation commit",
                    "exit_evidence_schema",
                    f"{capability}_exit_evidence_invalid",
                )
            evidence[capability] = {
                "path": str(path),
                "publication_commit": publication,
                "implementation_commit": implementation_commit,
            }

        for capability, item in evidence.items():
            relative = Path(item["path"]).relative_to(self.project_root).as_posix()
            with self._publication_snapshot(
                item["publication_commit"],
                capability,
            ) as snapshot:
                target = snapshot / relative
                validator_script = snapshot / "scripts" / "validate_slice_exit_evidence.py"
                try:
                    completed = subprocess.run(
                        [
                            sys.executable,
                            "-X",
                            "utf8",
                            "-B",
                            "-c",
                            _PUBLICATION_VALIDATOR_RUNNER,
                            str(validator_script),
                            str(target),
                        ],
                        cwd=snapshot,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        timeout=600,
                    )
                except (OSError, subprocess.TimeoutExpired) as exc:
                    _reject(
                        f"{capability} historical publication validator is unavailable: {exc}",
                        "historical_evidence",
                        f"{capability}_exit_evidence_invalid",
                    )
                if completed.returncode == 0:
                    self._require_clean_snapshot(snapshot, capability)
                    continue
                gate, code = self._parse_validator_failure(completed.stderr)
                if gate is None or code is None:
                    _reject(
                        f"{capability} historical release package is invalid",
                        "historical_evidence",
                        f"{capability}_exit_evidence_invalid",
                    )
                _reject(
                    f"{capability} historical release package is invalid: "
                    f"{completed.stderr.strip()[:400]}",
                    gate,
                    code,
                )
        return evidence

    @staticmethod
    def _parse_validator_failure(stderr: str) -> tuple[str | None, str | None]:
        """Extract the stable gate/code pair from the validator CLI failure line."""
        text = stderr.replace("\n", " ")
        match = re.search(
            r"first_failing_gate=([^;]+); error_code=([^;]+)",
            text,
        )
        if match is None:
            return None, None
        return match.group(1).strip(), match.group(2).strip()

    @contextmanager
    def _publication_snapshot(
        self,
        publication: str,
        capability: str,
    ) -> Iterator[Path]:
        """Reusable repository snapshot of one publication commit.

        The snapshot is a local shared clone of the immutable publication commit, so
        the publication-era validator (scripts/validate_slice_exit_evidence.py
        as published in that tree) observes the publication state for every
        check, including contract registries, slice constants, mirror files,
        persisted qualification artifacts, and guarded-delivery reports.
        Snapshots remain under the repository 待删除 staging area for manual
        cleanup and are never registered as linked worktrees of the source
        repository.
        """
        if not re.fullmatch(r"[0-9a-f]{40}", publication):
            _reject(
                f"{capability} historical publication identity is invalid",
                "historical_evidence",
                f"{capability}_exit_evidence_invalid",
            )
        snapshot_root = self.project_root / "待删除" / "release-audit-snapshots"
        snapshot_dir = snapshot_root / publication
        snapshot_root.mkdir(parents=True, exist_ok=True)
        if not snapshot_dir.exists():
            try:
                clone_shared_repository(self.project_root, snapshot_dir)
                git_output(snapshot_dir, "config", "core.autocrlf", "false")
                git_output(snapshot_dir, "config", "core.longpaths", "true")
                git_output(snapshot_dir, "checkout", "--detach", publication)
            except EvidenceSupportError as exc:
                _reject(
                    f"{capability} historical publication snapshot is unavailable: {exc}",
                    "historical_evidence",
                    f"{capability}_exit_evidence_invalid",
                )
        else:
            self._require_clean_snapshot(snapshot_dir, capability)
            sparse_file = snapshot_dir / ".git" / "info" / "sparse-checkout"
            if sparse_file.is_file():
                try:
                    git_output(snapshot_dir, "sparse-checkout", "disable")
                except EvidenceSupportError as exc:
                    _reject(
                        f"{capability} historical publication snapshot cannot materialize: {exc}",
                        "historical_evidence",
                        f"{capability}_exit_evidence_invalid",
                    )
        try:
            snapshot_head = git_output(snapshot_dir, "rev-parse", "HEAD")
        except EvidenceSupportError as exc:
            _reject(
                f"{capability} historical publication snapshot is invalid: {exc}",
                "historical_evidence",
                f"{capability}_exit_evidence_invalid",
            )
        if snapshot_head != publication:
            _reject(
                f"{capability} historical publication snapshot has the wrong commit",
                "historical_evidence",
                f"{capability}_exit_evidence_invalid",
            )
        self._require_clean_snapshot(snapshot_dir, capability)
        yield snapshot_dir

    def _require_clean_snapshot(self, snapshot: Path, capability: str) -> None:
        try:
            status = git_output(
                snapshot, "status", "--porcelain", "--untracked-files=all"
            )
        except EvidenceSupportError as exc:
            _reject(
                f"{capability} historical publication snapshot cannot be inspected: {exc}",
                "historical_evidence",
                f"{capability}_exit_evidence_invalid",
            )
        if status:
            _reject(
                f"{capability} historical publication snapshot is dirty",
                "historical_evidence",
                f"{capability}_exit_evidence_invalid",
            )

    def _validate_release_package(self, **paths: Path) -> dict[str, Any]:
        global_gate = paths.pop("global_gate").resolve()
        try:
            validated_global_gate = validate_global_gate_exit_evidence(
                global_gate,
                project_root=self.project_root,
                purpose="candidate_publication",
            )
        except ExitEvidenceValidationError as exc:
            _reject(str(exc), exc.first_failing_gate, exc.error_code)

        validator = self._load_slice_validator()
        evidence = {
            "global_gate": {
                "path": str(validated_global_gate.path),
            }
        }
        for capability in ("bilibili", "youtube", "batch"):
            path = paths[capability].resolve()
            self._require_evidence_slice(path, capability)
            try:
                validator.validate_manifest(
                    path,
                    schema_only=False,
                    pre_publication=False,
                )
            except validator.EvidenceError as exc:
                _reject(str(exc), exc.first_failing_gate, exc.error_code)
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                _reject(
                    f"{capability} Exit Evidence is unavailable: {exc}",
                    "exit_evidence_schema",
                    f"{capability}_exit_evidence_invalid",
                )
            evidence[capability] = {
                "path": str(path),
            }
        return evidence

    def _load_slice_validator(self) -> ModuleType:
        validator_path = self.project_root / "scripts/validate_slice_exit_evidence.py"
        try:
            spec = importlib.util.spec_from_file_location(
                "video2pdf_release_maintenance_exit_evidence_validator",
                validator_path,
            )
            if spec is None or spec.loader is None:
                raise RuntimeError("validator module cannot be loaded")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
        except Exception as exc:
            _reject(
                f"release Exit Evidence validator is unavailable: {exc}",
                "exit_evidence_validator",
                "release_exit_evidence_validator_unavailable",
            )

    def _require_evidence_slice(self, path: Path, capability: str) -> None:
        try:
            value = read_json(path)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            _reject(
                f"{capability} Exit Evidence is unavailable or malformed: {exc}",
                "exit_evidence_schema",
                f"{capability}_exit_evidence_invalid",
            )
        self._require_evidence_identity(value, capability)

    @staticmethod
    def _require_evidence_identity(value: dict[str, Any], capability: str) -> None:
        if value.get("slice") != EXPECTED_EVIDENCE_SLICES[capability]:
            _reject(
                f"{capability} Exit Evidence has the wrong release identity",
                "exit_evidence_identity",
                f"{capability}_exit_evidence_identity_invalid",
            )
