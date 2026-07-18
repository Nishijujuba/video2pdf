from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Callable, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
SCRIPTS = PROJECT_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from slice4_exit_evidence_contract import (
    COMMANDS,
    EVIDENCE_PREFIX,
    EXPECTED_CHECKPOINTS,
    FAULT_POINT_BINDINGS,
    FAULT_POINTS,
    FIXTURE_SPECS,
    PLATFORM_SMOKE_SPECS,
    RESULT_BINDINGS,
    RESULTS,
    SLICE_BASE_COMMIT,
    SLICE_NAME,
    SLICE_NUMBER,
)
from video2pdf_workflow_kernel.evidence import (
    EvidenceSupportError,
    fingerprint_implementation_changes,
    git_output,
    sha256_file,
)
from video2pdf_workflow_kernel.contracts import ContractRegistry
from video2pdf_workflow_kernel.errors import ContractError
from video2pdf_workflow_kernel.source_acquisition import derive_source_identity


EVIDENCE_DIR = PROJECT_ROOT / "evidence/slice-04"
LOG_DIR = EVIDENCE_DIR / "logs"
SMOKE_DIR = EVIDENCE_DIR / "smokes"
MANIFEST_PATH = EVIDENCE_DIR / "exit-evidence-manifest.json"
EVIDENCE_REFRESH_ROOT = (
    PROJECT_ROOT / "workspace/待删除/exit-evidence-refresh/slice-04"
)
SMOKE_COMMAND_IDS = {spec["command_id"] for spec in PLATFORM_SMOKE_SPECS}


class SecretExposureError(ValueError):
    pass


def git(*arguments: str) -> str:
    try:
        return git_output(PROJECT_ROOT, *arguments)
    except EvidenceSupportError as exc:
        raise RuntimeError(str(exc)) from exc


def project_relative(path: Path) -> str:
    return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def assert_secret_free(
    path: Path, value: bytes, *, sensitive_values: tuple[bytes, ...]
) -> None:
    lower = value.lower()
    markers = (
        b"cookie:",
        b"set-cookie:",
        b"# netscape http cookie file",
        b"\ttrue\t/\t",
        b"\tfalse\t/\t",
    )
    if any(marker in lower for marker in markers):
        raise SecretExposureError(f"secret-like cookie material detected before write: {path}")
    if any(secret and secret in value for secret in sensitive_values):
        raise SecretExposureError(f"supplied secret detected before write: {path}")
    if re.search(rb"(?i)(?:[a-z]:[/\\]|/)[^\r\n\x00]{0,200}cookies?\.txt", value):
        raise SecretExposureError(f"cookie-file path detected before write: {path}")


def publish_evidence_blobs(
    pending: Mapping[Path, bytes],
    *,
    sensitive_values: tuple[bytes, ...] = (),
    writer: Callable[[Path, bytes], None] | None = None,
) -> None:
    # The complete pending set is inspected before directory creation, prior
    # evidence preservation, or the first output write.
    for path, value in pending.items():
        assert_secret_free(path, value, sensitive_values=sensitive_values)

    def write(path: Path, value: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)

    selected_writer = writer or write
    for path, value in pending.items():
        selected_writer(path, value)


def preserve_previous_evidence(pending_paths: set[Path]) -> None:
    existing = [path for path in pending_paths if path.exists()]
    if not existing:
        return
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    destination = EVIDENCE_REFRESH_ROOT / timestamp
    destination.mkdir(parents=True, exist_ok=False)
    for path in existing:
        target = destination / project_relative(path).replace("/", "__")
        os.replace(path, target)


def run_commands(implementation_commit: str) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []
    fault_points_by_command: dict[str, list[str]] = {}
    for binding in FAULT_POINT_BINDINGS:
        fault_points_by_command.setdefault(binding["command_id"], []).append(
            binding["fault_point"]
        )
    for test_id, command in COMMANDS:
        completed = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            capture_output=True,
            check=False,
        )
        stdout = completed.stdout.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        stderr = completed.stderr.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        smoke_report = None
        if test_id in SMOKE_COMMAND_IDS and completed.returncode == 0:
            smoke_report = json.loads(stdout)
        raw = (
            stdout
            + stderr
            + f"\nEVIDENCE_IMPLEMENTATION_COMMIT: {implementation_commit}\n".encode(
                "ascii"
            )
            + b"".join(
                f"EVIDENCE_FAULT_POINT: {point}\n".encode("ascii")
                for point in fault_points_by_command.get(test_id, [])
            )
        )
        captured.append(
            {
                "test_id": test_id,
                "command": list(command),
                "expected_exit_code": 0,
                "actual_exit_code": completed.returncode,
                "raw": raw,
                "smoke_report": smoke_report,
            }
        )
    return captured


def implementation_artifacts(
    slice_base_commit: str, implementation_commit: str
) -> list[dict[str, str]]:
    try:
        return fingerprint_implementation_changes(
            PROJECT_ROOT,
            slice_base_commit,
            implementation_commit,
            excluded_prefixes=(EVIDENCE_PREFIX,),
        )
    except EvidenceSupportError as exc:
        raise RuntimeError(str(exc)) from exc


def resolve_source_manifest(
    report: dict[str, Any], *, expected_platform: str
) -> tuple[bytes, dict[str, str]]:
    binding = report.get("source_manifest")
    if not isinstance(binding, dict):
        raise RuntimeError("platform smoke omitted the Source Manifest binding")
    raw_path = binding.get("path")
    if not isinstance(raw_path, str):
        raise RuntimeError("platform smoke Source Manifest path is invalid")
    path = (PROJECT_ROOT / raw_path).resolve()
    try:
        path.relative_to(PROJECT_ROOT.resolve())
    except ValueError as exc:
        raise RuntimeError("platform smoke Source Manifest escapes the project root") from exc
    raw = path.read_bytes()
    actual = sha256_bytes(raw)
    if binding.get("sha256") != actual:
        raise RuntimeError("platform smoke Source Manifest fingerprint is stale")
    try:
        manifest = json.loads(raw)
        ContractRegistry(PROJECT_ROOT).validate("source-manifest", manifest)
    except (UnicodeError, json.JSONDecodeError, ContractError) as exc:
        raise RuntimeError("platform smoke Source Manifest is contract-invalid") from exc
    canonical_platform = manifest["canonical_platform"]
    canonical_item_id = manifest["canonical_item_id"]
    source_identity = manifest["source_identity"]
    source_version = manifest["source_version"]
    if canonical_platform != expected_platform:
        raise RuntimeError("platform smoke Source Manifest platform is stale")
    if source_identity != derive_source_identity(canonical_platform, canonical_item_id):
        raise RuntimeError("platform smoke Source Manifest identity is not canonical")
    if (
        binding.get("source_identity") != source_identity
        or binding.get("source_version") != source_version
    ):
        raise RuntimeError("platform smoke report binding differs from its Source Manifest")
    return raw, {
        "canonical_platform": canonical_platform,
        "canonical_item_id": canonical_item_id,
        "source_identity": source_identity,
        "source_version": source_version,
    }


def main() -> int:
    status = git("status", "--porcelain=v1", "--untracked-files=all")
    if status:
        print(
            "ERROR: Slice 4 evidence collection requires a clean implementation HEAD",
            file=sys.stderr,
        )
        return 2
    implementation_commit = git("rev-parse", "HEAD")
    git("merge-base", "--is-ancestor", SLICE_BASE_COMMIT, implementation_commit)
    try:
        captured = run_commands(implementation_commit)
        pending: dict[Path, bytes] = {}
        command_evidence: list[dict[str, Any]] = []
        captured_by_id: dict[str, dict[str, Any]] = {}
        for item in captured:
            test_id = item["test_id"]
            log_path = LOG_DIR / f"{test_id}.log"
            pending[log_path] = item["raw"]
            evidence = {
                "test_id": test_id,
                "command": item["command"],
                "expected_exit_code": item["expected_exit_code"],
                "actual_exit_code": item["actual_exit_code"],
                "log": {
                    "role": "command_log",
                    "path": project_relative(log_path),
                    "sha256": sha256_bytes(item["raw"]),
                },
                "conforms": item["actual_exit_code"] == item["expected_exit_code"],
            }
            command_evidence.append(evidence)
            captured_by_id[test_id] = {**item, "evidence": evidence}

        platform_smokes: list[dict[str, Any]] = []
        for spec in PLATFORM_SMOKE_SPECS:
            captured_smoke = captured_by_id[spec["command_id"]]
            report = captured_smoke["smoke_report"]
            if not isinstance(report, dict):
                raise RuntimeError(f"platform smoke report is missing: {spec['platform']}")
            source_raw, source_identity = resolve_source_manifest(
                report, expected_platform=spec["platform"]
            )
            source_path = PROJECT_ROOT / spec["source_manifest_path"]
            pending[source_path] = source_raw
            smoke = {
                **report,
                "command_id": spec["command_id"],
                "source_manifest": {
                    "path": spec["source_manifest_path"],
                    "sha256": sha256_bytes(source_raw),
                    **source_identity,
                },
                "sanitized_log": {
                    "path": spec["sanitized_log_path"],
                    "sha256": captured_smoke["evidence"]["log"]["sha256"],
                    "no_secret_scan": "pass",
                },
            }
            smoke["target_checkpoint"] = {
                "name": "source_ready",
                "status": "current",
                "evidence_sha256": sha256_bytes(source_raw),
            }
            platform_smokes.append(smoke)

        fixtures = [
            {
                "role": role,
                "path": relative_path,
                "sha256": sha256_file(PROJECT_ROOT / relative_path),
            }
            for role, relative_path in FIXTURE_SPECS
        ]
        decision = (
            "pass" if all(item["conforms"] for item in command_evidence) else "fail"
        )
        manifest = {
            "$schema": "https://video2pdf.local/schemas/exit-evidence-manifest.v2.schema.json",
            "schema_version": 2,
            "kind": "video-workflow-exit-evidence",
            "fingerprint_algorithm": "sha256-raw-v1",
            "slice": {"number": SLICE_NUMBER, "name": SLICE_NAME},
            "slice_base_commit": SLICE_BASE_COMMIT,
            "implementation_commit": implementation_commit,
            "evidence_paths": [
                project_relative(MANIFEST_PATH),
                *[item["log"]["path"] for item in command_evidence],
                *[spec["source_manifest_path"] for spec in PLATFORM_SMOKE_SPECS],
            ],
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "activation_scope": {
                "kind": "none",
                "runtime_authority_change": False,
                "components_activated": [],
                "legacy_track_authority": "preserved",
            },
            "commands": command_evidence,
            "expected_checkpoints": EXPECTED_CHECKPOINTS,
            "fixtures": fixtures,
            "results": RESULTS,
            "result_bindings": RESULT_BINDINGS,
            "fault_points": list(FAULT_POINTS),
            "platform_smokes": platform_smokes,
            "artifact_fingerprints": implementation_artifacts(
                SLICE_BASE_COMMIT, implementation_commit
            ),
            "unresolved_exceptions": [],
            "overall_decision": decision,
        }
        pending[MANIFEST_PATH] = (
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
        # Preserve happens only after publish_evidence_blobs has proved the full
        # set is secret-free. A closure keeps that ordering explicit.
        scanned: dict[Path, bytes] = {}

        def stage(path: Path, value: bytes) -> None:
            scanned[path] = value

        publish_evidence_blobs(pending, writer=stage)
        preserve_previous_evidence(set(pending))
        publish_evidence_blobs(scanned)
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        RuntimeError,
        SecretExposureError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(MANIFEST_PATH)
    print(
        "NEXT: run validate_slice_exit_evidence.py with --pre-publication before the evidence-only child commit"
    )
    return 0 if decision == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
