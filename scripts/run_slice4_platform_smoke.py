from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ALLOWED_CREDENTIAL_PROFILES = {
    "bilibili-project-cookie": "bilibili",
    "youtube-project-cookie": "youtube",
}


class SmokeContractError(ValueError):
    pass


def project_argument(path: Path) -> str:
    value = path.as_posix()
    if path.is_absolute():
        try:
            value = path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
        except ValueError as exc:
            raise SmokeContractError("smoke path escapes the project root") from exc
    return value


def build_product_cli_command(
    spec_path: Path, credential_profile: str, work_root: Path
) -> tuple[str, ...]:
    if credential_profile not in ALLOWED_CREDENTIAL_PROFILES:
        raise SmokeContractError("unknown credential profile")
    return (
        sys.executable,
        "-X",
        "utf8",
        "-B",
        "scripts/video_workflow.py",
        "source-live-smoke",
        "--spec",
        project_argument(spec_path),
        "--credential-profile",
        credential_profile,
        "--work-root",
        project_argument(work_root),
    )


def assert_secret_free_bytes(
    value: bytes, *, sensitive_values: tuple[bytes, ...] = ()
) -> None:
    lower = value.lower()
    forbidden_markers = (
        b"cookie:",
        b"# netscape http cookie file",
        b"\t.true\t/\t",
        b"\tfalse\t/\t",
    )
    if any(marker in lower for marker in forbidden_markers):
        raise SmokeContractError("product smoke output contains authentication material")
    if any(secret and secret in value for secret in sensitive_values):
        raise SmokeContractError("product smoke output contains a supplied secret")
    if re.search(rb"(?i)(?:[a-z]:[/\\]|/)[^\r\n\x00]{0,200}cookies?\.txt", value):
        raise SmokeContractError("product smoke output contains a cookie-file path")


def validate_report(report: Any, expected_platform: str) -> dict[str, Any]:
    if not isinstance(report, dict):
        raise SmokeContractError("product smoke output must be one JSON object")
    required = {
        "platform",
        "adapter_id",
        "adapter_contract_version",
        "provider_kind",
        "run_id",
        "command_argv_redacted",
        "authentication_classification",
        "tool_versions",
        "target_checkpoint",
        "source_manifest",
        "runtime_policy_sha256",
        "recorded_at",
    }
    if set(report) != required:
        raise SmokeContractError("product smoke output differs from the closed report shape")
    if (
        report["platform"] != expected_platform
        or report["adapter_id"] != expected_platform
        or report["provider_kind"] != "live"
    ):
        raise SmokeContractError("product smoke output has inconsistent platform authority")
    argv = report["command_argv_redacted"]
    if not isinstance(argv, list) or argv.count("<COOKIE_FILE>") != 1:
        raise SmokeContractError("product smoke command must contain one cookie placeholder")
    serialized = json.dumps(report, ensure_ascii=False, sort_keys=True).encode("utf-8")
    assert_secret_free_bytes(serialized)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Delegate one live Slice 4 platform smoke to the product CLI."
    )
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--credential-profile", required=True)
    parser.add_argument("--work-root", required=True, type=Path)
    args = parser.parse_args(argv or sys.argv[1:])
    try:
        platform = ALLOWED_CREDENTIAL_PROFILES[args.credential_profile]
        command = build_product_cli_command(
            args.spec, args.credential_profile, args.work_root
        )
        completed = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            capture_output=True,
            check=False,
        )
        assert_secret_free_bytes(completed.stdout)
        assert_secret_free_bytes(completed.stderr)
        if completed.returncode != 0:
            raise SmokeContractError(
                f"product smoke CLI failed with exit code {completed.returncode}"
            )
        report = validate_report(json.loads(completed.stdout), platform)
    except (
        KeyError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        SmokeContractError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
