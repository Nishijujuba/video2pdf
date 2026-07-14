from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
import re
from typing import Any


DEFINITION_SCHEMA_ID = "https://video2pdf.local/schemas/legacy-baseline-definition.v1.schema.json"
MANIFEST_SCHEMA_ID = "https://video2pdf.local/schemas/exit-evidence-manifest.v1.schema.json"
REQUIRED_LEGACY_CATEGORIES = frozenset(
    {"pyramid", "compile", "acceptance", "delivery_guard", "batch"}
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
TEST_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,79}$")


class ContractError(ValueError):
    pass


def load_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ContractError(f"contract file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ContractError(f"contract is invalid JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContractError(f"contract root must be an object: {path}")
    return value


def load_schema_contract(project_root: Path, filename: str, expected_id: str) -> dict[str, Any]:
    schema_path = project_root / "schemas" / filename
    schema = load_json_object(schema_path)
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        raise ContractError(f"schema draft identity mismatch: {schema_path}")
    if schema.get("$id") != expected_id:
        raise ContractError(f"schema $id mismatch: {schema_path}")
    return schema


def _require_exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    if missing or unexpected:
        details: list[str] = []
        if missing:
            details.append(f"missing {missing}")
        if unexpected:
            details.append(f"unexpected {unexpected}")
        raise ContractError(f"{label} fields invalid: {'; '.join(details)}")


def _require_string(value: Any, label: str, *, nonempty: bool = True) -> str:
    if not isinstance(value, str) or (nonempty and not value):
        raise ContractError(f"{label} must be a non-empty string")
    return value


def _require_bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise ContractError(f"{label} must be a boolean")
    return value


def _require_int(value: Any, label: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(f"{label} must be an integer")
    if minimum is not None and value < minimum:
        raise ContractError(f"{label} must be at least {minimum}")
    return value


def _require_list(value: Any, label: str, *, minimum: int = 0) -> list[Any]:
    if not isinstance(value, list) or len(value) < minimum:
        raise ContractError(f"{label} must be an array with at least {minimum} item(s)")
    return value


def _require_sha256(value: Any, label: str) -> str:
    text = _require_string(value, label)
    if not SHA256_RE.fullmatch(text):
        raise ContractError(f"{label} must be a lowercase SHA-256 fingerprint")
    return text


def _require_commit(value: Any, label: str) -> str:
    text = _require_string(value, label)
    if not COMMIT_RE.fullmatch(text):
        raise ContractError(f"{label} must be a full lowercase Git commit SHA")
    return text


def _require_relative_path(value: Any, label: str) -> str:
    text = _require_string(value, label)
    if "\\" in text:
        raise ContractError(f"{label} must use forward slashes")
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts or text.startswith("/"):
        raise ContractError(f"{label} must stay within the project root")
    return text


def _validate_command_definition(value: Any, label: str, *, expected_category: str | None = None) -> None:
    if not isinstance(value, dict):
        raise ContractError(f"{label} must be an object")
    _require_exact_keys(
        value,
        {"test_id", "category", "command", "timeout_seconds", "expected_status", "expected_log_sha256"},
        label,
    )
    test_id = _require_string(value["test_id"], f"{label}.test_id")
    if not TEST_ID_RE.fullmatch(test_id):
        raise ContractError(f"{label}.test_id has an invalid format")
    category = _require_string(value["category"], f"{label}.category")
    if expected_category is not None and category != expected_category:
        raise ContractError(f"{label}.category must be {expected_category!r}")
    command = _require_list(value["command"], f"{label}.command", minimum=2)
    if any(not isinstance(token, str) or not token or "\x00" in token for token in command):
        raise ContractError(f"{label}.command must contain non-empty string tokens")
    if command[0] != "{python}":
        raise ContractError(f"{label}.command must use the registered {{python}} runtime token")
    timeout = _require_int(value["timeout_seconds"], f"{label}.timeout_seconds", minimum=1)
    if timeout > 3600:
        raise ContractError(f"{label}.timeout_seconds must not exceed 3600")
    if value["expected_status"] not in {"pass", "fail"}:
        raise ContractError(f"{label}.expected_status must be 'pass' or 'fail'")
    _require_sha256(value["expected_log_sha256"], f"{label}.expected_log_sha256")


def validate_legacy_baseline_definition(value: dict[str, Any]) -> None:
    _require_exact_keys(
        value,
        {
            "$schema",
            "schema_version",
            "kind",
            "normalization_version",
            "baselines",
            "slice_verifications",
            "authority_guards",
        },
        "legacy baseline definition",
    )
    if value["$schema"] != DEFINITION_SCHEMA_ID:
        raise ContractError("legacy baseline definition $schema is unsupported")
    if value["schema_version"] != 1 or value["kind"] != "legacy-workflow-baseline-definition":
        raise ContractError("legacy baseline definition identity is unsupported")
    if value["normalization_version"] != 1:
        raise ContractError("legacy baseline normalization_version is unsupported")

    baselines = _require_list(value["baselines"], "baselines")
    categories: list[str] = []
    test_ids: list[str] = []
    for index, entry in enumerate(baselines):
        _validate_command_definition(entry, f"baselines[{index}]")
        categories.append(entry["category"])
        test_ids.append(entry["test_id"])
    category_set = set(categories)
    if (
        len(baselines) != 5
        or category_set != REQUIRED_LEGACY_CATEGORIES
        or len(categories) != len(category_set)
    ):
        missing = sorted(REQUIRED_LEGACY_CATEGORIES - category_set)
        extra = sorted(category_set - REQUIRED_LEGACY_CATEGORIES)
        raise ContractError(f"baselines must cover the five Legacy categories; missing={missing}; extra={extra}")

    verifications = _require_list(value["slice_verifications"], "slice_verifications", minimum=1)
    for index, entry in enumerate(verifications):
        _validate_command_definition(entry, f"slice_verifications[{index}]", expected_category="slice_verification")
        test_ids.append(entry["test_id"])
    if len(test_ids) != len(set(test_ids)):
        raise ContractError("all baseline and verification test_id values must be unique")

    guards = _require_list(value["authority_guards"], "authority_guards", minimum=1)
    guard_paths: list[str] = []
    for index, guard in enumerate(guards):
        label = f"authority_guards[{index}]"
        if not isinstance(guard, dict):
            raise ContractError(f"{label} must be an object")
        _require_exact_keys(guard, {"path", "required_substrings"}, label)
        guard_paths.append(_require_relative_path(guard["path"], f"{label}.path"))
        substrings = _require_list(guard["required_substrings"], f"{label}.required_substrings", minimum=1)
        if any(not isinstance(item, str) or not item for item in substrings):
            raise ContractError(f"{label}.required_substrings must contain non-empty strings")
        if len(substrings) != len(set(substrings)):
            raise ContractError(f"{label}.required_substrings must be unique")
    if len(guard_paths) != len(set(guard_paths)):
        raise ContractError("authority guard paths must be unique")


def _validate_command_evidence(value: Any, label: str) -> None:
    if not isinstance(value, dict):
        raise ContractError(f"{label} must be an object")
    _require_exact_keys(
        value,
        {
            "test_id",
            "scope",
            "category",
            "declared_command",
            "executed_command",
            "timeout_seconds",
            "expected_status",
            "actual_status",
            "exit_code",
            "expected_log_sha256",
            "log",
            "conforms",
        },
        label,
    )
    test_id = _require_string(value["test_id"], f"{label}.test_id")
    if not TEST_ID_RE.fullmatch(test_id):
        raise ContractError(f"{label}.test_id has an invalid format")
    if value["scope"] not in {"legacy_baseline", "slice_verification"}:
        raise ContractError(f"{label}.scope is unsupported")
    category = _require_string(value["category"], f"{label}.category")
    if value["scope"] == "legacy_baseline" and category not in REQUIRED_LEGACY_CATEGORIES:
        raise ContractError(f"{label}.category is not a Legacy baseline category")
    if value["scope"] == "slice_verification" and category != "slice_verification":
        raise ContractError(f"{label}.category must be 'slice_verification'")
    for command_field in ("declared_command", "executed_command"):
        command = _require_list(value[command_field], f"{label}.{command_field}", minimum=2)
        if any(not isinstance(token, str) or not token for token in command):
            raise ContractError(f"{label}.{command_field} must contain non-empty strings")
    _require_int(value["timeout_seconds"], f"{label}.timeout_seconds", minimum=1)
    if value["expected_status"] not in {"pass", "fail"}:
        raise ContractError(f"{label}.expected_status is unsupported")
    if value["actual_status"] not in {"pass", "fail", "timeout"}:
        raise ContractError(f"{label}.actual_status is unsupported")
    if value["exit_code"] is not None:
        _require_int(value["exit_code"], f"{label}.exit_code")
    _require_sha256(value["expected_log_sha256"], f"{label}.expected_log_sha256")

    log = value["log"]
    if not isinstance(log, dict):
        raise ContractError(f"{label}.log must be an object")
    _require_exact_keys(
        log,
        {"normalization_version", "normalized_path", "normalized_sha256", "raw_path", "raw_sha256"},
        f"{label}.log",
    )
    if log["normalization_version"] != 1:
        raise ContractError(f"{label}.log.normalization_version is unsupported")
    _require_relative_path(log["normalized_path"], f"{label}.log.normalized_path")
    _require_sha256(log["normalized_sha256"], f"{label}.log.normalized_sha256")
    _require_relative_path(log["raw_path"], f"{label}.log.raw_path")
    _require_sha256(log["raw_sha256"], f"{label}.log.raw_sha256")
    conforms = _require_bool(value["conforms"], f"{label}.conforms")
    derived = (
        value["expected_status"] == value["actual_status"]
        and value["expected_log_sha256"] == log["normalized_sha256"]
    )
    if conforms != derived:
        raise ContractError(f"{label}.conforms does not match expected/actual evidence")


def validate_exit_evidence_manifest(value: dict[str, Any]) -> None:
    _require_exact_keys(
        value,
        {
            "$schema",
            "schema_version",
            "kind",
            "slice",
            "implementation_commit",
            "evidence_head",
            "generated_at",
            "activation_scope",
            "baseline_definition",
            "commands",
            "authority_evidence",
            "expected_checkpoints",
            "fixtures",
            "results",
            "unresolved_exceptions",
            "overall_decision",
        },
        "exit evidence manifest",
    )
    if value["$schema"] != MANIFEST_SCHEMA_ID:
        raise ContractError("exit evidence manifest $schema is unsupported")
    if value["schema_version"] != 1 or value["kind"] != "video-workflow-exit-evidence":
        raise ContractError("exit evidence manifest identity is unsupported")

    slice_value = value["slice"]
    if not isinstance(slice_value, dict):
        raise ContractError("slice must be an object")
    _require_exact_keys(slice_value, {"number", "name"}, "slice")
    if slice_value != {"number": 0, "name": "baseline-protection"}:
        raise ContractError("exit evidence manifest must identify Slice 0 baseline protection")
    _require_commit(value["implementation_commit"], "implementation_commit")
    _require_commit(value["evidence_head"], "evidence_head")
    generated_at = _require_string(value["generated_at"], "generated_at")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", generated_at):
        raise ContractError("generated_at must be a UTC second-resolution timestamp")

    activation = value["activation_scope"]
    if not isinstance(activation, dict):
        raise ContractError("activation_scope must be an object")
    expected_activation = {
        "kind": "none",
        "runtime_authority_change": False,
        "components_activated": [],
        "legacy_track_authority": "preserved",
    }
    if activation != expected_activation:
        raise ContractError("Slice 0 must preserve Legacy Track authority without activation")

    definition = value["baseline_definition"]
    if not isinstance(definition, dict):
        raise ContractError("baseline_definition must be an object")
    _require_exact_keys(definition, {"path", "sha256"}, "baseline_definition")
    _require_relative_path(definition["path"], "baseline_definition.path")
    _require_sha256(definition["sha256"], "baseline_definition.sha256")

    commands = _require_list(value["commands"], "commands", minimum=6)
    test_ids: list[str] = []
    baseline_categories: list[str] = []
    for index, command in enumerate(commands):
        _validate_command_evidence(command, f"commands[{index}]")
        test_ids.append(command["test_id"])
        if command["scope"] == "legacy_baseline":
            baseline_categories.append(command["category"])
    if len(test_ids) != len(set(test_ids)):
        raise ContractError("commands test_id values must be unique")
    if len(baseline_categories) != 5 or set(baseline_categories) != REQUIRED_LEGACY_CATEGORIES:
        raise ContractError("commands must contain the five Legacy baseline categories exactly once")

    authority = _require_list(value["authority_evidence"], "authority_evidence", minimum=1)
    for index, item in enumerate(authority):
        label = f"authority_evidence[{index}]"
        if not isinstance(item, dict):
            raise ContractError(f"{label} must be an object")
        _require_exact_keys(
            item,
            {"path", "sha256", "required_substrings", "missing_substrings", "conforms"},
            label,
        )
        _require_relative_path(item["path"], f"{label}.path")
        _require_sha256(item["sha256"], f"{label}.sha256")
        for field in ("required_substrings", "missing_substrings"):
            strings = _require_list(item[field], f"{label}.{field}")
            if any(not isinstance(entry, str) or not entry for entry in strings):
                raise ContractError(f"{label}.{field} must contain non-empty strings")
        conforms = _require_bool(item["conforms"], f"{label}.conforms")
        if conforms != (not item["missing_substrings"]):
            raise ContractError(f"{label}.conforms does not match missing_substrings")

    checkpoints = _require_list(value["expected_checkpoints"], "expected_checkpoints")
    if any(not isinstance(item, str) or not item for item in checkpoints):
        raise ContractError("expected_checkpoints must contain non-empty strings")
    if checkpoints:
        raise ContractError("Slice 0 must not create or claim a Kernel Workflow Checkpoint")

    fixtures = _require_list(value["fixtures"], "fixtures", minimum=3)
    for index, item in enumerate(fixtures):
        label = f"fixtures[{index}]"
        if not isinstance(item, dict):
            raise ContractError(f"{label} must be an object")
        _require_exact_keys(item, {"role", "path", "sha256"}, label)
        _require_string(item["role"], f"{label}.role")
        _require_relative_path(item["path"], f"{label}.path")
        _require_sha256(item["sha256"], f"{label}.sha256")

    results = value["results"]
    if not isinstance(results, dict):
        raise ContractError("results must be an object")
    _require_exact_keys(results, {"positive", "negative"}, "results")
    for field in ("positive", "negative"):
        entries = _require_list(results[field], f"results.{field}")
        if any(not isinstance(item, str) or not item for item in entries):
            raise ContractError(f"results.{field} must contain non-empty strings")

    unresolved = _require_list(value["unresolved_exceptions"], "unresolved_exceptions")
    if any(not isinstance(item, dict) for item in unresolved):
        raise ContractError("unresolved_exceptions must contain objects")
    for index, item in enumerate(unresolved):
        _require_exact_keys(item, {"blocking", "code", "message"}, f"unresolved_exceptions[{index}]")
        _require_bool(item["blocking"], f"unresolved_exceptions[{index}].blocking")
        _require_string(item["code"], f"unresolved_exceptions[{index}].code")
        _require_string(item["message"], f"unresolved_exceptions[{index}].message")

    if value["overall_decision"] not in {"pass", "fail"}:
        raise ContractError("overall_decision must be 'pass' or 'fail'")
    derived_pass = (
        all(item["conforms"] for item in commands)
        and all(item["conforms"] for item in authority)
        and not any(item["blocking"] for item in unresolved)
    )
    if (value["overall_decision"] == "pass") != derived_pass:
        raise ContractError("overall_decision does not match command, authority, and exception evidence")
    if value["overall_decision"] == "pass" and unresolved:
        raise ContractError("a passing manifest cannot contain unresolved exceptions")
