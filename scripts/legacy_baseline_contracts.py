from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, cast


DEFINITION_SCHEMA_ID = "https://video2pdf.local/schemas/legacy-baseline-definition.v1.schema.json"
MANIFEST_SCHEMA_ID = "https://video2pdf.local/schemas/exit-evidence-manifest.v1.schema.json"
FINGERPRINT_ALGORITHM = "sha256-utf8-lf-v1"
REQUIRED_LEGACY_CATEGORIES = frozenset(
    {"pyramid", "compile", "acceptance", "delivery_guard", "batch"}
)
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


class ContractError(ValueError):
    pass


def fingerprint_utf8_lf(value: bytes, *, label: str = "bound evidence") -> str:
    """Return the canonical sha256-utf8-lf-v1 fingerprint for text bytes."""

    try:
        text = value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContractError(f"{label} must be UTF-8 text for {FINGERPRINT_ALGORITHM}") from exc
    canonical = text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _git_output_bytes(project_root: Path, arguments: list[str], label: str) -> bytes:
    process = subprocess.run(
        ["git", *arguments],
        cwd=project_root,
        capture_output=True,
    )
    if process.returncode != 0:
        detail = process.stderr.decode("utf-8", errors="replace").strip()
        raise ContractError(f"{label}: {detail or 'Git check failed'}")
    return process.stdout


def capture_clean_implementation_commit(project_root: Path) -> str:
    """Capture the exact clean Git HEAD whose checkout will be tested."""

    status = _git_output_bytes(
        project_root,
        ["status", "--porcelain=v1", "--untracked-files=all"],
        "cannot inspect implementation worktree",
    )
    if status:
        raise ContractError(
            "baseline collection requires a clean implementation HEAD; "
            "commit code, configuration, Schema, and tests before collecting evidence"
        )
    head = _git_output_bytes(
        project_root, ["rev-parse", "HEAD"], "cannot resolve implementation HEAD"
    ).decode("ascii", errors="strict").strip()
    if not COMMIT_RE.fullmatch(head):
        raise ContractError("implementation HEAD must resolve to a full lowercase Git commit SHA")
    return head


def _decode_git_path_list(raw: bytes, label: str) -> list[str]:
    try:
        return [item.decode("utf-8") for item in raw.split(b"\0") if item]
    except UnicodeDecodeError as exc:
        raise ContractError(f"{label} contains a non-UTF-8 project path") from exc


def _worktree_changed_paths(project_root: Path) -> set[str]:
    changed: set[str] = set()
    for arguments in (
        ["diff", "--name-only", "-z", "--no-renames"],
        ["diff", "--cached", "--name-only", "-z", "--no-renames"],
        ["ls-files", "--others", "--exclude-standard", "-z"],
    ):
        changed.update(
            _decode_git_path_list(
                _git_output_bytes(project_root, arguments, "cannot inspect evidence worktree"),
                "evidence worktree",
            )
        )
    return changed


def _commit_changed_paths(project_root: Path, commit: str, label: str) -> set[str]:
    return set(
        _decode_git_path_list(
            _git_output_bytes(
                project_root,
                [
                    "diff-tree",
                    "--root",
                    "--no-commit-id",
                    "--name-only",
                    "-r",
                    "-z",
                    "--no-renames",
                    commit,
                ],
                f"cannot inspect {label}",
            ),
            label,
        )
    )


def _declared_evidence_worktree_changes(
    project_root: Path, evidence_paths: list[str]
) -> set[str]:
    changed: set[str] = set()
    for path_value in evidence_paths:
        path = project_root / path_value
        if not path.is_file():
            continue
        tracked = _git_output_bytes(
            project_root,
            ["ls-files", "-z", "--", path_value],
            "cannot inspect declared evidence tracking state",
        )
        if not tracked:
            changed.add(path_value)
            continue
        head_blob = _git_output_bytes(
            project_root,
            ["rev-parse", f"HEAD:{path_value}"],
            "cannot inspect tracked evidence blob",
        ).decode("ascii", errors="strict").strip()
        worktree_blob = _git_output_bytes(
            project_root,
            ["hash-object", f"--path={path_value}", "--", path_value],
            "cannot fingerprint declared evidence worktree blob",
        ).decode("ascii", errors="strict").strip()
        if worktree_blob != head_blob:
            changed.add(path_value)
    return changed


def validate_prevalidated_prepublication_lineage(
    project_root: Path,
    implementation_commit: str,
    evidence_paths: list[str],
) -> None:
    """Bind unpublished evidence to the clean implementation HEAD."""

    _git_output_bytes(
        project_root,
        ["cat-file", "-e", f"{implementation_commit}^{{commit}}"],
        "implementation_commit does not resolve to a commit",
    )
    current_head = _git_output_bytes(
        project_root,
        ["rev-parse", "HEAD"],
        "cannot resolve pre-publication implementation HEAD",
    ).decode("ascii", errors="strict").strip()
    if current_head != implementation_commit:
        raise ContractError(
            "pre-publication validation requires HEAD to equal implementation_commit"
        )

    changed = _worktree_changed_paths(project_root)
    changed.update(
        _declared_evidence_worktree_changes(project_root, evidence_paths)
    )
    if not changed:
        raise ContractError(
            "pre-publication validation requires unpublished evidence changes"
        )
    forbidden = sorted(changed - set(evidence_paths))
    if forbidden:
        raise ContractError(
            "pre-publication worktree changed non-evidence path(s): "
            f"{forbidden}"
        )


def validate_prevalidated_postpublication_lineage(
    project_root: Path,
    implementation_commit: str,
    evidence_paths: list[str],
    manifest_path: Path,
) -> None:
    """Derive and prove the historical evidence publication commit."""

    allowed = set(evidence_paths)
    root = project_root.resolve()
    try:
        manifest_relative = manifest_path.resolve().relative_to(root).as_posix()
    except ValueError as exc:
        raise ContractError("manifest path must stay within the project root") from exc

    _git_output_bytes(
        project_root,
        ["cat-file", "-e", f"{implementation_commit}^{{commit}}"],
        "implementation_commit does not resolve to a commit",
    )
    head_blob = _git_output_bytes(
        project_root,
        ["rev-parse", f"HEAD:{manifest_relative}"],
        "post-publication manifest is not tracked at HEAD",
    ).decode("ascii", errors="strict").strip()
    worktree_blob = _git_output_bytes(
        project_root,
        ["hash-object", f"--path={manifest_relative}", "--", manifest_relative],
        "cannot fingerprint current manifest worktree blob",
    ).decode("ascii", errors="strict").strip()
    if worktree_blob != head_blob:
        raise ContractError(
            "current manifest does not match its tracked HEAD blob"
        )

    candidates = _git_output_bytes(
        project_root,
        ["log", "--format=%H", "HEAD", "--", manifest_relative],
        "cannot inspect manifest publication history",
    ).decode("ascii", errors="strict").splitlines()
    publication_commit: str | None = None
    for candidate in candidates:
        try:
            candidate_blob = _git_output_bytes(
                project_root,
                ["rev-parse", f"{candidate}:{manifest_relative}"],
                f"cannot inspect manifest blob at {candidate}",
            ).decode("ascii", errors="strict").strip()
        except ContractError:
            continue
        if candidate_blob == head_blob:
            publication_commit = candidate
            break
    if publication_commit is None:
        raise ContractError(
            "cannot locate evidence publication commit for current manifest blob"
        )

    ancestry = _git_output_bytes(
        project_root,
        ["rev-list", "--parents", "-n", "1", publication_commit],
        "cannot inspect evidence publication parent",
    ).decode("ascii", errors="strict").split()
    if len(ancestry) != 2:
        raise ContractError("evidence publication commit must have one direct parent")
    publication_parent = ancestry[1]
    if publication_parent != implementation_commit:
        raise ContractError(
            "evidence publication parent does not match implementation_commit"
        )

    publication_paths = _commit_changed_paths(
        project_root, publication_commit, "evidence publication commit"
    )
    if not publication_paths:
        raise ContractError("evidence publication commit changed no paths")
    forbidden_publication = sorted(publication_paths - allowed)
    if forbidden_publication:
        raise ContractError(
            "evidence publication changed non-evidence path(s): "
            f"{forbidden_publication}"
        )

    implementation_paths = _commit_changed_paths(
        project_root, implementation_commit, "implementation_commit"
    )
    if not (implementation_paths - allowed):
        raise ContractError(
            "implementation_commit cannot be an evidence-only commit"
        )


def load_json_value(path: Path) -> Any:
    """Load a JSON value without imposing an instance shape before Schema."""

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ContractError(f"contract file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ContractError(f"contract is invalid JSON: {path}: {exc}") from exc


def load_schema_object(
    project_root: Path, filename: str, expected_id: str
) -> dict[str, Any]:
    """Load a registered Schema document and require its own object shape."""

    schema_path = project_root / "schemas" / filename
    schema = load_json_value(schema_path)
    if not isinstance(schema, dict):
        raise ContractError(f"schema document root must be an object: {schema_path}")
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        raise ContractError(f"schema draft identity mismatch: {schema_path}")
    if schema.get("$id") != expected_id:
        raise ContractError(f"schema $id mismatch: {schema_path}")
    return schema


# Slice 1 / Issue #4 owns the standards-based jsonschema runtime and its lock.
# Slice 0 still needs the Schema file to be the structural authority, so this
# compatibility evaluator implements exactly the vocabulary used by the two
# Slice 0 schemas and fails closed when that vocabulary changes.
_SUPPORTED_SCHEMA_KEYWORDS = frozenset(
    {
        "$schema",
        "$id",
        "$defs",
        "$ref",
        "title",
        "description",
        "type",
        "required",
        "properties",
        "additionalProperties",
        "const",
        "enum",
        "minLength",
        "pattern",
        "minimum",
        "maximum",
        "minItems",
        "maxItems",
        "uniqueItems",
        "prefixItems",
        "items",
    }
)
_SUPPORTED_JSON_TYPES = frozenset(
    {"null", "boolean", "object", "array", "number", "integer", "string"}
)


def _schema_error(path: str, detail: str) -> ContractError:
    return ContractError(f"{path} {detail}")


def _require_schema_object(value: Any, path: str) -> dict[str, Any] | bool:
    if isinstance(value, bool) or isinstance(value, dict):
        return value
    raise _schema_error(path, "must be an object or boolean JSON Schema")


def _check_schema_vocabulary(schema: Any, path: str = "$") -> None:
    schema = _require_schema_object(schema, path)
    if isinstance(schema, bool):
        return
    unsupported = sorted(set(schema) - _SUPPORTED_SCHEMA_KEYWORDS)
    if unsupported:
        raise _schema_error(path, f"uses unsupported JSON Schema keyword(s): {unsupported}")

    for keyword in ("$defs", "properties"):
        value = schema.get(keyword)
        if value is None:
            continue
        if not isinstance(value, dict):
            raise _schema_error(f"{path}.{keyword}", "must be an object")
        for name, child in value.items():
            _check_schema_vocabulary(child, f"{path}.{keyword}.{name}")

    prefix_items = schema.get("prefixItems")
    if prefix_items is not None:
        if not isinstance(prefix_items, list):
            raise _schema_error(f"{path}.prefixItems", "must be an array")
        for index, child in enumerate(prefix_items):
            _check_schema_vocabulary(child, f"{path}.prefixItems[{index}]")

    for keyword in ("items", "additionalProperties"):
        child = schema.get(keyword)
        if child is not None:
            _check_schema_vocabulary(child, f"{path}.{keyword}")

    type_value = schema.get("type")
    if type_value is not None:
        types = [type_value] if isinstance(type_value, str) else type_value
        if (
            not isinstance(types, list)
            or not types
            or any(not isinstance(item, str) or item not in _SUPPORTED_JSON_TYPES for item in types)
        ):
            raise _schema_error(f"{path}.type", "contains an unsupported JSON type")

    required = schema.get("required")
    if required is not None and (
        not isinstance(required, list) or any(not isinstance(item, str) for item in required)
    ):
        raise _schema_error(f"{path}.required", "must be an array of strings")

    enum = schema.get("enum")
    if enum is not None and (not isinstance(enum, list) or not enum):
        raise _schema_error(f"{path}.enum", "must be a non-empty array")

    for keyword in ("minLength", "minItems", "maxItems"):
        constraint = schema.get(keyword)
        if constraint is not None and (
            isinstance(constraint, bool) or not isinstance(constraint, int) or constraint < 0
        ):
            raise _schema_error(f"{path}.{keyword}", "must be a non-negative integer")
    for keyword in ("minimum", "maximum"):
        constraint = schema.get(keyword)
        if constraint is not None and (
            isinstance(constraint, bool) or not isinstance(constraint, (int, float))
        ):
            raise _schema_error(f"{path}.{keyword}", "must be a number")
    if "uniqueItems" in schema and not isinstance(schema["uniqueItems"], bool):
        raise _schema_error(f"{path}.uniqueItems", "must be a boolean")
    if "pattern" in schema:
        pattern = schema["pattern"]
        if not isinstance(pattern, str):
            raise _schema_error(f"{path}.pattern", "must be a string")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise _schema_error(f"{path}.pattern", f"is invalid: {exc}") from exc


def _json_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _json_equal(a, b) for a, b in zip(left, right, strict=True)
        )
    if isinstance(left, dict):
        return set(left) == set(right) and all(_json_equal(left[key], right[key]) for key in left)
    return left == right


def _matches_json_type(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "number":
        return not isinstance(value, bool) and isinstance(value, (int, float))
    if expected == "integer":
        return not isinstance(value, bool) and isinstance(value, int)
    if expected == "string":
        return isinstance(value, str)
    raise _schema_error("schema.type", f"is unsupported: {expected!r}")


def _resolve_local_ref(root_schema: dict[str, Any], reference: Any, path: str) -> Any:
    if not isinstance(reference, str) or not reference.startswith("#/"):
        raise _schema_error(path, "must be a local JSON Pointer reference")
    current: Any = root_schema
    for raw_token in reference[2:].split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or token not in current:
            raise _schema_error(path, f"cannot resolve {reference!r}")
        current = current[token]
    return current


def _validate_schema_instance(
    value: Any,
    schema: Any,
    root_schema: dict[str, Any],
    instance_path: str,
) -> None:
    if isinstance(schema, bool):
        if not schema:
            raise _schema_error(instance_path, "is rejected by a false JSON Schema")
        return
    if "$ref" in schema:
        target = _resolve_local_ref(root_schema, schema["$ref"], f"{instance_path}.$ref")
        _validate_schema_instance(value, target, root_schema, instance_path)

    type_value = schema.get("type")
    if type_value is not None:
        expected_types = [type_value] if isinstance(type_value, str) else type_value
        if not any(_matches_json_type(value, expected) for expected in expected_types):
            raise _schema_error(instance_path, f"must have JSON type {expected_types}")
    if "const" in schema and not _json_equal(value, schema["const"]):
        raise _schema_error(instance_path, f"must equal schema const {schema['const']!r}")
    if "enum" in schema and not any(_json_equal(value, item) for item in schema["enum"]):
        raise _schema_error(instance_path, f"must be one of {schema['enum']!r}")

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise _schema_error(instance_path, f"must have length at least {schema['minLength']}")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            raise _schema_error(instance_path, f"must match schema pattern {schema['pattern']!r}")

    if not isinstance(value, bool) and isinstance(value, (int, float)):
        if "minimum" in schema and value < schema["minimum"]:
            raise _schema_error(instance_path, f"must be at least {schema['minimum']} (schema minimum)")
        if "maximum" in schema and value > schema["maximum"]:
            raise _schema_error(instance_path, f"must be at most {schema['maximum']} (schema maximum)")

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise _schema_error(instance_path, f"must contain at least {schema['minItems']} item(s)")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise _schema_error(instance_path, f"must contain at most {schema['maxItems']} item(s)")
        if schema.get("uniqueItems"):
            for index, item in enumerate(value):
                if any(_json_equal(item, previous) for previous in value[:index]):
                    raise _schema_error(instance_path, "must contain unique items")
        prefix_items = schema.get("prefixItems", [])
        for index, item_schema in enumerate(prefix_items):
            if index < len(value):
                _validate_schema_instance(value[index], item_schema, root_schema, f"{instance_path}[{index}]")
        if "items" in schema:
            for index in range(len(prefix_items), len(value)):
                _validate_schema_instance(
                    value[index], schema["items"], root_schema, f"{instance_path}[{index}]"
                )

    if isinstance(value, dict):
        required = schema.get("required", [])
        missing = [name for name in required if name not in value]
        if missing:
            raise _schema_error(instance_path, f"is missing required properties {missing}")
        properties = schema.get("properties", {})
        for name, property_schema in properties.items():
            if name in value:
                _validate_schema_instance(
                    value[name], property_schema, root_schema, f"{instance_path}.{name}"
                )
        extras = [name for name in value if name not in properties]
        additional = schema.get("additionalProperties", True)
        if additional is False and extras:
            raise _schema_error(instance_path, f"contains additional properties {extras}")
        if additional is not True and additional is not False:
            for name in extras:
                _validate_schema_instance(
                    value[name], additional, root_schema, f"{instance_path}.{name}"
                )


def validate_json_schema_instance(value: Any, schema: dict[str, Any], label: str) -> None:
    """Validate through the bounded Slice 0 Schema compatibility layer.

    Issue #4 replaces this function with the locked standards-based runtime.
    Cross-artifact and derived-value invariants remain separate validators.
    """

    _check_schema_vocabulary(schema)
    _validate_schema_instance(value, schema, schema, label)


def validate_prevalidated_legacy_baseline_semantics(value: dict[str, Any]) -> None:
    """Validate semantic invariants after the definition Schema has passed.

    The caller must first validate ``value`` against the registered Legacy
    baseline definition Schema. This function intentionally assumes structural
    validity and owns only relationships the Schema does not express.
    """

    baselines = value["baselines"]
    categories = [entry["category"] for entry in baselines]
    test_ids = [entry["test_id"] for entry in baselines]
    category_set = set(categories)
    if category_set != REQUIRED_LEGACY_CATEGORIES or len(categories) != len(category_set):
        missing = sorted(REQUIRED_LEGACY_CATEGORIES - category_set)
        extra = sorted(category_set - REQUIRED_LEGACY_CATEGORIES)
        raise ContractError(
            "baselines must cover the five Legacy categories; "
            f"missing={missing}; extra={extra}"
        )

    verifications = value["slice_verifications"]
    for index, entry in enumerate(verifications):
        if entry["category"] != "slice_verification":
            raise ContractError(
                f"slice_verifications[{index}].category must be 'slice_verification'"
            )
        test_ids.append(entry["test_id"])
    if len(test_ids) != len(set(test_ids)):
        raise ContractError("all baseline and verification test_id values must be unique")

    result_identities = value["result_identities"]
    positive_identities = result_identities["positive"]
    if set(positive_identities) != set(test_ids):
        missing = sorted(set(test_ids) - set(positive_identities))
        unexpected = sorted(set(positive_identities) - set(test_ids))
        raise ContractError(
            "positive result identities must exactly match baseline and verification "
            f"test_id values; missing={missing}; unexpected={unexpected}"
        )
    overlap = sorted(set(positive_identities) & set(result_identities["negative"]))
    if overlap:
        raise ContractError(
            "positive and negative result identities must be disjoint; "
            f"overlap={overlap}"
        )

    guard_paths = [guard["path"] for guard in value["authority_guards"]]
    if len(guard_paths) != len(set(guard_paths)):
        raise ContractError("authority guard paths must be unique")

    fixture_contracts = value["fixture_contracts"]
    fixture_roles = [fixture["role"] for fixture in fixture_contracts]
    if len(fixture_roles) != len(set(fixture_roles)):
        raise ContractError("fixture contract roles must be unique")
    fixture_paths = [fixture["path"] for fixture in fixture_contracts]
    if len(fixture_paths) != len(set(fixture_paths)):
        raise ContractError("fixture contract paths must be unique")
    for index, fixture in enumerate(fixture_contracts):
        kind = fixture["validation_kind"]
        expected = fixture["expected_validity"]
        if kind == "fingerprint_only" and expected != "not_applicable":
            raise ContractError(
                f"fixture_contracts[{index}].expected_validity must be "
                "'not_applicable' for fingerprint_only"
            )
        if kind != "fingerprint_only" and expected not in {"valid", "invalid"}:
            raise ContractError(
                f"fixture_contracts[{index}].expected_validity must declare "
                "'valid' or 'invalid' for Schema validation"
            )


def _validate_prevalidated_command_evidence_semantics(
    value: dict[str, Any], label: str
) -> None:
    category = value["category"]
    if value["scope"] == "legacy_baseline" and category not in REQUIRED_LEGACY_CATEGORIES:
        raise ContractError(f"{label}.category is not a Legacy baseline category")
    if value["scope"] == "slice_verification" and category != "slice_verification":
        raise ContractError(f"{label}.category must be 'slice_verification'")
    exit_code = value["exit_code"]
    derived_status = (
        "timeout" if exit_code is None else "pass" if exit_code == 0 else "fail"
    )
    if value["actual_status"] != derived_status:
        raise ContractError(f"{label}.actual_status does not match exit_code")
    log = value["log"]
    derived = (
        value["expected_status"] == value["actual_status"]
        and value["expected_log_sha256"] == log["normalized_sha256"]
    )
    if value["conforms"] != derived:
        raise ContractError(f"{label}.conforms does not match expected/actual evidence")


def expand_registered_runtime_command(
    declared_command: list[str], *, python_executable: str
) -> list[str]:
    """Expand only the registered ``{python}`` token in a declared command."""

    return [
        python_executable if token == "{python}" else token
        for token in declared_command
    ]


def validate_prevalidated_manifest_definition_bindings(
    value: dict[str, Any],
    definition: dict[str, Any],
    *,
    python_executable: str,
) -> None:
    """Bind manifest command and result evidence to its validated definition.

    Both objects must first pass their registered Schemas. Definition semantics
    must also establish unique command identities before this cross-artifact
    invariant is called.
    """

    expected_authority = [
        {
            "path": guard["path"],
            "required_substrings": guard["required_substrings"],
        }
        for guard in definition["authority_guards"]
    ]
    actual_authority = [
        {
            "path": evidence["path"],
            "required_substrings": evidence["required_substrings"],
        }
        for evidence in value["authority_evidence"]
    ]
    if actual_authority != expected_authority:
        raise ContractError(
            "authority evidence inventory does not match baseline definition"
        )

    fixture_fields = (
        "role",
        "path",
        "validation_kind",
        "expected_validity",
    )
    expected_fixtures = [
        {field: fixture[field] for field in fixture_fields}
        for fixture in definition["fixture_contracts"]
    ]
    actual_fixtures = [
        {field: fixture[field] for field in fixture_fields}
        for fixture in value["fixtures"]
    ]
    if actual_fixtures != expected_fixtures:
        raise ContractError("fixture inventory does not match baseline definition")

    definition_commands: dict[str, tuple[str, dict[str, Any]]] = {}
    for scope, key in (
        ("slice_verification", "slice_verifications"),
        ("legacy_baseline", "baselines"),
    ):
        for entry in definition[key]:
            definition_commands[entry["test_id"]] = (scope, entry)

    expected_test_ids = definition["result_identities"]["positive"]
    actual_test_ids = [command["test_id"] for command in value["commands"]]
    if actual_test_ids != expected_test_ids:
        raise ContractError(
            "command test identities do not match baseline definition; "
            f"expected={expected_test_ids}; actual={actual_test_ids}"
        )

    for index, command in enumerate(value["commands"]):
        label = f"commands[{index}]"
        expected_scope, entry = definition_commands[command["test_id"]]
        bindings = (
            ("scope", expected_scope),
            ("category", entry["category"]),
            ("declared_command", entry["command"]),
            ("timeout_seconds", entry["timeout_seconds"]),
            ("expected_status", entry["expected_status"]),
            ("expected_log_sha256", entry["expected_log_sha256"]),
        )
        for field, expected in bindings:
            if command[field] != expected:
                raise ContractError(
                    f"{label}.{field} does not match baseline definition"
                )
        expected_executed = expand_registered_runtime_command(
            entry["command"], python_executable=python_executable
        )
        if command["executed_command"] != expected_executed:
            raise ContractError(
                f"{label}.executed_command does not match runtime expansion"
            )

    result_identities = definition["result_identities"]
    if value["results"]["positive"] != result_identities["positive"]:
        raise ContractError("positive results do not match baseline definition")
    if value["results"]["negative"] != result_identities["negative"]:
        raise ContractError("negative results do not match baseline definition")


def validate_prevalidated_exit_evidence_semantics(value: dict[str, Any]) -> None:
    """Validate semantic invariants after the manifest Schema has passed.

    The caller must first validate ``value`` against the registered Exit
    Evidence Manifest Schema. This function intentionally assumes structural
    validity and owns only derived values and cross-field relationships.
    """

    commands = value["commands"]
    test_ids: list[str] = []
    baseline_categories: list[str] = []
    for index, command in enumerate(commands):
        _validate_prevalidated_command_evidence_semantics(
            command, f"commands[{index}]"
        )
        test_ids.append(command["test_id"])
        if command["scope"] == "legacy_baseline":
            baseline_categories.append(command["category"])
    if len(test_ids) != len(set(test_ids)):
        raise ContractError("commands test_id values must be unique")
    if len(baseline_categories) != 5 or set(baseline_categories) != REQUIRED_LEGACY_CATEGORIES:
        raise ContractError(
            "commands must contain the five Legacy baseline categories exactly once"
        )

    authority = value["authority_evidence"]
    for index, item in enumerate(authority):
        label = f"authority_evidence[{index}]"
        if item["conforms"] != (not item["missing_substrings"]):
            raise ContractError(f"{label}.conforms does not match missing_substrings")

    unresolved = value["unresolved_exceptions"]
    derived_pass = (
        all(item["conforms"] for item in commands)
        and all(item["conforms"] for item in authority)
        and not any(item["blocking"] for item in unresolved)
    )
    if (value["overall_decision"] == "pass") != derived_pass:
        raise ContractError(
            "overall_decision does not match command, authority, and exception evidence"
        )
    if value["overall_decision"] == "pass" and unresolved:
        raise ContractError("a passing manifest cannot contain unresolved exceptions")


def _resolve_bound_path(project_root: Path, value: str, label: str) -> Path:
    root = project_root.resolve()
    path = (root / value).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ContractError(f"{label} escapes the project root") from exc
    return path


def _read_fingerprint_bound_file(
    project_root: Path, path_value: str, sha_value: str, label: str
) -> tuple[Path, bytes]:
    path = _resolve_bound_path(project_root, path_value, f"{label}.path")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ContractError(f"{label} cannot be read: {path}: {exc}") from exc
    actual_sha = fingerprint_utf8_lf(raw, label=label)
    if actual_sha != sha_value:
        raise ContractError(
            f"{label} fingerprint mismatch: expected {sha_value}, actual {actual_sha}: {path}"
        )
    return path, raw


def validate_prevalidated_declared_fixture_validity(
    fixtures: list[dict[str, Any]], project_root: Path
) -> None:
    """Verify definition-bound fixture fingerprints and declared Schema validity."""

    schemas = {
        "legacy_baseline_definition_schema": load_schema_object(
            project_root,
            "legacy-baseline-definition.v1.schema.json",
            DEFINITION_SCHEMA_ID,
        ),
        "exit_evidence_manifest_schema": load_schema_object(
            project_root,
            "exit-evidence-manifest.v1.schema.json",
            MANIFEST_SCHEMA_ID,
        ),
    }
    for schema in schemas.values():
        _check_schema_vocabulary(schema)

    for index, fixture in enumerate(fixtures):
        label = f"fixtures[{index}]"
        _, raw = _read_fingerprint_bound_file(
            project_root, fixture["path"], fixture["sha256"], label
        )
        kind = fixture["validation_kind"]
        if kind == "fingerprint_only":
            continue
        try:
            fixture_value = json.loads(raw.decode("utf-8"))
            schema = schemas[kind]
            _validate_schema_instance(
                fixture_value,
                schema,
                schema,
                f"{label} fixture",
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ContractError):
            actual_validity = "invalid"
        else:
            actual_validity = "valid"
        if actual_validity != fixture["expected_validity"]:
            raise ContractError(
                "fixture validity does not match baseline definition: "
                f"{fixture['role']}: expected {fixture['expected_validity']}, "
                f"actual {actual_validity}"
            )


def validate_prevalidated_exit_evidence_bindings(
    value: dict[str, Any],
    project_root: Path,
    manifest_path: Path,
    *,
    pre_publication: bool = False,
) -> None:
    """Validate cross-artifact bindings after the manifest Schema has passed.

    The caller must first validate ``value`` against the registered Exit
    Evidence Manifest Schema. This function owns only artifact fingerprints,
    filesystem boundaries, Git lineage, and expected-versus-actual bindings.
    """

    implementation_commit = value["implementation_commit"]
    root = project_root.resolve()
    try:
        manifest_relative = manifest_path.resolve().relative_to(root).as_posix()
    except ValueError as exc:
        raise ContractError("manifest path must stay within the project root") from exc
    declared_evidence_paths = set(value["evidence_paths"])
    log_evidence_paths = {
        command["log"][f"{log_kind}_path"]
        for command in value["commands"]
        for log_kind in ("normalized", "raw")
    }
    expected_evidence_paths = {manifest_relative} | log_evidence_paths
    if declared_evidence_paths != expected_evidence_paths:
        missing = sorted(expected_evidence_paths - declared_evidence_paths)
        unexpected = sorted(declared_evidence_paths - expected_evidence_paths)
        raise ContractError(
            "evidence_paths must exactly declare the manifest and command logs; "
            f"missing={missing}; unexpected={unexpected}"
        )
    if pre_publication:
        validate_prevalidated_prepublication_lineage(
            project_root, implementation_commit, value["evidence_paths"]
        )
    else:
        validate_prevalidated_postpublication_lineage(
            project_root,
            implementation_commit,
            value["evidence_paths"],
            manifest_path,
        )

    definition_binding = value["baseline_definition"]
    definition_path, _ = _read_fingerprint_bound_file(
        project_root,
        definition_binding["path"],
        definition_binding["sha256"],
        "baseline_definition",
    )
    definition_value = load_json_value(definition_path)
    definition_schema = load_schema_object(
        project_root,
        "legacy-baseline-definition.v1.schema.json",
        DEFINITION_SCHEMA_ID,
    )
    validate_json_schema_instance(
        definition_value, definition_schema, "legacy baseline definition"
    )
    definition = cast(dict[str, Any], definition_value)
    validate_prevalidated_legacy_baseline_semantics(definition)
    validate_prevalidated_manifest_definition_bindings(
        value,
        definition,
        python_executable=sys.executable,
    )

    bound_paths: set[str] = set()
    for index, command in enumerate(value["commands"]):
        for log_kind in ("normalized", "raw"):
            label = f"commands[{index}].log.{log_kind}"
            path_value = command["log"][f"{log_kind}_path"]
            sha_value = command["log"][f"{log_kind}_sha256"]
            path, _ = _read_fingerprint_bound_file(project_root, path_value, sha_value, label)
            canonical_path = str(path).casefold()
            if canonical_path in bound_paths:
                raise ContractError(f"{label} reuses a log path already bound by another entry")
            bound_paths.add(canonical_path)

    for index, evidence in enumerate(value["authority_evidence"]):
        label = f"authority_evidence[{index}]"
        _, raw = _read_fingerprint_bound_file(
            project_root, evidence["path"], evidence["sha256"], label
        )
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ContractError(f"{label} must be UTF-8 text") from exc
        actual_missing = [
            item for item in evidence["required_substrings"] if item not in text
        ]
        if actual_missing != evidence["missing_substrings"]:
            raise ContractError(f"{label}.missing_substrings is stale")

    validate_prevalidated_declared_fixture_validity(
        value["fixtures"], project_root
    )
