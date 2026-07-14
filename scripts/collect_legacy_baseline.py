from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, cast

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from legacy_baseline_contracts import (
    ContractError,
    DEFINITION_SCHEMA_ID,
    FINGERPRINT_ALGORITHM,
    MANIFEST_SCHEMA_ID,
    capture_clean_implementation_commit,
    expand_registered_runtime_command,
    fingerprint_utf8_lf,
    load_json_value,
    load_schema_object,
    validate_json_schema_instance,
    validate_prevalidated_exit_evidence_bindings,
    validate_prevalidated_exit_evidence_semantics,
    validate_prevalidated_legacy_baseline_semantics,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFINITION_SCHEMA = "legacy-baseline-definition.v1.schema.json"
MANIFEST_SCHEMA = "exit-evidence-manifest.v1.schema.json"
DISPOSABLE_RUN_TIME_NS_RE = re.compile(
    r"(?P<prefix>\{PROJECT_ROOT\}[\\/]待删除[\\/]"
    r"(?:kernel-test-runs|skill-tests)[\\/][^\\/\r\n]*?[-_])"
    r"\d{16,20}(?=[\\/\s]|$)"
)


def sha256_text(value: str) -> str:
    return fingerprint_utf8_lf(value.encode("utf-8"))


def write_text_atomic(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.tmp")
    temp_path.write_bytes(value.encode("utf-8"))
    os.replace(temp_path, path)


def project_relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    except (OSError, ValueError) as exc:
        raise ContractError(f"path must stay within the project root: {path}") from exc


def resolve_project_path(value: str) -> Path:
    path = (PROJECT_ROOT / value).resolve()
    project_relative(path)
    return path


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def compose_log(stdout: str, stderr: str) -> str:
    return (
        "===== STDOUT =====\n"
        f"{stdout.rstrip()}\n"
        "===== STDERR =====\n"
        f"{stderr.rstrip()}\n"
    )


def normalize_log(raw_log: str) -> str:
    value = raw_log.replace("\r\n", "\n").replace("\r", "\n")
    replacements = {
        str(PROJECT_ROOT.resolve()): "{PROJECT_ROOT}",
        PROJECT_ROOT.resolve().as_posix(): "{PROJECT_ROOT}",
        str(Path(sys.executable).resolve()): "{PYTHON}",
        Path(sys.executable).resolve().as_posix(): "{PYTHON}",
    }
    for source in sorted(replacements, key=len, reverse=True):
        value = value.replace(source, replacements[source])
    value = re.sub(r"(Ran \d+ tests? in )\d+(?:\.\d+)?s", r"\1<duration>s", value)
    value = DISPOSABLE_RUN_TIME_NS_RE.sub(r"\g<prefix>{TIME_NS}", value)
    value = re.sub(
        r"(?<!\d)\d{8}_\d{6}_\d{6}_[0-9a-f]{8}(?![0-9a-f])",
        "{COMPILE_RUN_ID}",
        value,
    )
    value = re.sub(
        r"(?<=[\\/])\d{6}_[0-9a-f]{6,8}(?=[\\/])",
        "{SHORT_ALIAS_ID}",
        value,
    )
    return value


def expand_command(declared: list[str]) -> list[str]:
    return expand_registered_runtime_command(
        declared, python_executable=sys.executable
    )


def run_command(entry: dict[str, Any], scope: str, log_dir: Path) -> dict[str, Any]:
    declared = list(entry["command"])
    executed = expand_command(declared)
    exit_code: int | None
    try:
        process = subprocess.run(
            executed,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=entry["timeout_seconds"],
        )
        stdout = process.stdout
        stderr = process.stderr
        exit_code = process.returncode
        actual_status = "pass" if exit_code == 0 else "fail"
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        stderr = f"{stderr.rstrip()}\nCOMMAND TIMED OUT".lstrip("\n")
        exit_code = None
        actual_status = "timeout"

    raw_log = compose_log(stdout, stderr)
    normalized_log = normalize_log(raw_log)
    raw_path = log_dir / f"{entry['test_id']}.raw.log"
    normalized_path = log_dir / f"{entry['test_id']}.log"
    write_text_atomic(raw_path, raw_log)
    write_text_atomic(normalized_path, normalized_log)
    normalized_sha = sha256_text(normalized_log)
    conforms = (
        actual_status == entry["expected_status"]
        and normalized_sha == entry["expected_log_sha256"]
    )
    return {
        "test_id": entry["test_id"],
        "scope": scope,
        "category": entry["category"],
        "declared_command": declared,
        "executed_command": executed,
        "timeout_seconds": entry["timeout_seconds"],
        "expected_status": entry["expected_status"],
        "actual_status": actual_status,
        "exit_code": exit_code,
        "expected_log_sha256": entry["expected_log_sha256"],
        "log": {
            "normalization_version": 1,
            "normalized_path": project_relative(normalized_path),
            "normalized_sha256": normalized_sha,
            "raw_path": project_relative(raw_path),
            "raw_sha256": sha256_text(raw_log),
        },
        "conforms": conforms,
    }


def collect_authority_evidence(guards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for guard in guards:
        path = resolve_project_path(guard["path"])
        raw = path.read_bytes()
        text = raw.decode("utf-8")
        missing = [item for item in guard["required_substrings"] if item not in text]
        evidence.append(
            {
                "path": project_relative(path),
                "sha256": fingerprint_utf8_lf(raw),
                "required_substrings": list(guard["required_substrings"]),
                "missing_substrings": missing,
                "conforms": not missing,
            }
        )
    return evidence


def fixture_fingerprints(definition_path: Path) -> list[dict[str, str]]:
    paths = [
        ("baseline_definition", definition_path),
        ("baseline_definition_schema", PROJECT_ROOT / "schemas" / DEFINITION_SCHEMA),
        ("exit_evidence_schema", PROJECT_ROOT / "schemas" / MANIFEST_SCHEMA),
        (
            "baseline_definition_positive_fixture",
            PROJECT_ROOT / "tests" / "video_workflow" / "fixtures" / "legacy_baseline_definition.valid.json",
        ),
        (
            "baseline_definition_negative_fixture",
            PROJECT_ROOT / "tests" / "video_workflow" / "fixtures" / "legacy_baseline_definition.invalid.json",
        ),
        (
            "exit_evidence_positive_fixture",
            PROJECT_ROOT / "tests" / "video_workflow" / "fixtures" / "exit_evidence_manifest.valid.json",
        ),
        (
            "exit_evidence_negative_fixture",
            PROJECT_ROOT / "tests" / "video_workflow" / "fixtures" / "exit_evidence_manifest.invalid.json",
        ),
    ]
    return [
        {
            "role": role,
            "path": project_relative(path),
            "sha256": fingerprint_utf8_lf(path.read_bytes()),
        }
        for role, path in paths
    ]


def declared_evidence_paths(
    output_path: Path, commands: list[dict[str, Any]]
) -> list[str]:
    paths = [project_relative(output_path)]
    for command in commands:
        paths.extend(
            [command["log"]["normalized_path"], command["log"]["raw_path"]]
        )
    return paths


def mismatch_exceptions(
    commands: list[dict[str, Any]], authority_evidence: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    exceptions: list[dict[str, Any]] = []
    for command in commands:
        if command["expected_status"] != command["actual_status"]:
            exceptions.append(
                {
                    "blocking": True,
                    "code": "unexpected_status",
                    "message": (
                        f"{command['test_id']}: expected {command['expected_status']}, "
                        f"actual {command['actual_status']}"
                    ),
                }
            )
        if command["expected_log_sha256"] != command["log"]["normalized_sha256"]:
            exceptions.append(
                {
                    "blocking": True,
                    "code": "unexpected_log_fingerprint",
                    "message": (
                        f"{command['test_id']}: expected {command['expected_log_sha256']}, "
                        f"actual {command['log']['normalized_sha256']}"
                    ),
                }
            )
    for evidence in authority_evidence:
        if not evidence["conforms"]:
            exceptions.append(
                {
                    "blocking": True,
                    "code": "runtime_authority_guard_failed",
                    "message": f"{evidence['path']}: missing required Legacy authority text",
                }
            )
    return exceptions


def collect(
    definition_path: Path,
    output_path: Path,
    log_dir: Path,
) -> dict[str, Any]:
    project_relative(definition_path)
    project_relative(output_path)
    project_relative(log_dir)
    definition_schema = load_schema_object(
        PROJECT_ROOT, DEFINITION_SCHEMA, DEFINITION_SCHEMA_ID
    )
    manifest_schema = load_schema_object(
        PROJECT_ROOT, MANIFEST_SCHEMA, MANIFEST_SCHEMA_ID
    )
    definition_value = load_json_value(definition_path)
    validate_json_schema_instance(
        definition_value, definition_schema, "legacy baseline definition"
    )
    definition = cast(dict[str, Any], definition_value)
    validate_prevalidated_legacy_baseline_semantics(definition)
    implementation_commit = capture_clean_implementation_commit(PROJECT_ROOT)
    # The result identity order is the definition-owned canonical execution
    # order. The slice verification invokes this collector's CLI tests, so it
    # remains first in the repository definition and runs while the
    # implementation checkout is still clean.
    command_entries = {
        entry["test_id"]: ("slice_verification", entry)
        for entry in definition["slice_verifications"]
    }
    command_entries.update(
        {
            entry["test_id"]: ("legacy_baseline", entry)
            for entry in definition["baselines"]
        }
    )
    commands = [
        run_command(command_entries[test_id][1], command_entries[test_id][0], log_dir)
        for test_id in definition["result_identities"]["positive"]
    ]
    authority_evidence = collect_authority_evidence(definition["authority_guards"])
    unresolved = mismatch_exceptions(commands, authority_evidence)
    overall_decision = "pass" if not unresolved else "fail"
    manifest = {
        "$schema": MANIFEST_SCHEMA_ID,
        "schema_version": 1,
        "kind": "video-workflow-exit-evidence",
        "fingerprint_algorithm": FINGERPRINT_ALGORITHM,
        "slice": {"number": 0, "name": "baseline-protection"},
        "implementation_commit": implementation_commit,
        "evidence_paths": declared_evidence_paths(output_path, commands),
        "generated_at": utc_now(),
        "activation_scope": {
            "kind": "none",
            "runtime_authority_change": False,
            "components_activated": [],
            "legacy_track_authority": "preserved",
        },
        "baseline_definition": {
            "path": project_relative(definition_path),
            "sha256": fingerprint_utf8_lf(definition_path.read_bytes()),
        },
        "commands": commands,
        "authority_evidence": authority_evidence,
        "expected_checkpoints": [],
        "fixtures": fixture_fingerprints(definition_path),
        "results": {
            "positive": list(definition["result_identities"]["positive"]),
            "negative": list(definition["result_identities"]["negative"]),
        },
        "unresolved_exceptions": unresolved,
        "overall_decision": overall_decision,
    }
    validate_json_schema_instance(manifest, manifest_schema, "exit evidence manifest")
    validate_prevalidated_exit_evidence_semantics(manifest)
    validate_prevalidated_exit_evidence_bindings(manifest, PROJECT_ROOT, output_path)
    write_text_atomic(output_path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect the repeatable Legacy workflow baseline and publish Slice 0 exit evidence."
    )
    parser.add_argument("--definition", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--log-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        manifest = collect(
            args.definition.resolve(),
            args.output.resolve(),
            args.log_dir.resolve(),
        )
    except (ContractError, OSError, UnicodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"Exit Evidence Manifest: {args.output.resolve()}")
    print(f"Overall decision: {manifest['overall_decision']}")
    return 0 if manifest["overall_decision"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
