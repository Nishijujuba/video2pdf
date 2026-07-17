from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from video2pdf_workflow_kernel.contracts import ContractRegistry
from video2pdf_workflow_kernel.evidence import (
    EvidenceSupportError,
    fingerprint_implementation_changes,
    git_output,
    sha256_file,
)


SCHEMA_PATH = PROJECT_ROOT / "schemas/exit-evidence-manifest.v2.schema.json"
SLICE_CONFIGS = {
    1: {
        "base_commit": "96089b99c9ae63fff61107e1920fc3481ffc0802",
        "evidence_prefix": "evidence/slice-01/",
        "checkpoints": [{"name": "source_ready", "status": "current"}],
        "command_ids": [
            "slice0-regression",
            "slice1-contracts",
            "slice1-public-deep-tests",
            "slice1-review-hardening-tests",
            "slice1-gate4-saga-containment-tests",
            "slice1-gate7-review-repair-tests",
            "slice0-exit-evidence",
            "slice1-syntax",
            "slice1-diff-check",
        ],
        "result_kinds": ["positive", "negative", "recovery"],
    },
    2: {
        "base_commit": "904f46409b87aca96aeecf5cb0be4855c2cfdafa",
        "evidence_prefix": "evidence/slice-02/",
        "checkpoints": [
            {"name": "source_ready", "status": "current"},
            {"name": "source_acquisition_decision_ready", "status": "current"},
        ],
        "command_ids": [
            "slice0-regression",
            "slice2-contracts",
            "slice1-regression",
            "slice2-task-promotion",
            "slice2-task-promotion-hardening",
            "slice2-review-repairs",
            "slice2-control-store-transaction-scope",
            "slice1-exit-evidence",
            "slice2-syntax",
            "slice2-diff-check",
        ],
        "result_kinds": ["positive", "negative", "fencing", "recovery"],
        "required_fencing_results": ["late_and_superseded_workers_are_fenced"],
    },
}


class EvidenceError(ValueError):
    pass


def git(*arguments: str) -> str:
    try:
        return git_output(PROJECT_ROOT, *arguments)
    except EvidenceSupportError as exc:
        raise EvidenceError(str(exc)) from exc


def resolve_project_path(value: str) -> Path:
    root = PROJECT_ROOT.resolve()
    path = (root / value).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise EvidenceError(f"evidence path escapes project root: {value}") from exc
    return path


def changed_worktree_paths() -> set[str]:
    changed: set[str] = set()
    for arguments in (
        ("diff", "--name-only", "HEAD"),
        ("diff", "--cached", "--name-only", "HEAD"),
        ("ls-files", "--others", "--exclude-standard"),
    ):
        output = git(*arguments)
        changed.update(line for line in output.splitlines() if line)
    return changed


def commit_paths(commit: str) -> set[str]:
    parent_line = git("rev-list", "--parents", "-n", "1", commit).split()
    if len(parent_line) != 2:
        raise EvidenceError(f"commit must have exactly one parent: {commit}")
    return set(
        line
        for line in git(
            "diff-tree", "--no-commit-id", "--name-only", "-r", commit
        ).splitlines()
        if line
    )


def validate_lineage(
    manifest: dict[str, Any], manifest_path: Path, *, pre_publication: bool
) -> None:
    implementation_commit = manifest["implementation_commit"]
    git("cat-file", "-e", f"{implementation_commit}^{{commit}}")
    allowed = set(manifest["evidence_paths"])
    manifest_relative = manifest_path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    if pre_publication:
        if git("rev-parse", "HEAD") != implementation_commit:
            raise EvidenceError(
                "pre-publication HEAD must equal implementation_commit"
            )
        changed = changed_worktree_paths()
        if not changed or manifest_relative not in changed:
            raise EvidenceError("pre-publication evidence changes are missing")
        forbidden = sorted(changed - allowed)
        if forbidden:
            raise EvidenceError(
                f"pre-publication worktree contains non-evidence changes: {forbidden}"
            )
        return

    head_blob = git("rev-parse", f"HEAD:{manifest_relative}")
    worktree_blob = git("hash-object", f"--path={manifest_relative}", "--", manifest_relative)
    if head_blob != worktree_blob:
        raise EvidenceError("current manifest differs from its committed HEAD blob")
    publication_commit: str | None = None
    for candidate in git("log", "--format=%H", "HEAD", "--", manifest_relative).splitlines():
        try:
            candidate_blob = git("rev-parse", f"{candidate}:{manifest_relative}")
        except EvidenceError:
            continue
        if candidate_blob == head_blob:
            publication_commit = candidate
            break
    if publication_commit is None:
        raise EvidenceError("cannot locate evidence publication commit")
    parents = git("rev-list", "--parents", "-n", "1", publication_commit).split()
    if len(parents) != 2 or parents[1] != implementation_commit:
        raise EvidenceError(
            "evidence publication must be the direct child of implementation_commit"
        )
    published = commit_paths(publication_commit)
    if published != allowed:
        raise EvidenceError(
            f"evidence publication paths differ from closed allowlist: {sorted(published ^ allowed)}"
        )
    if not (commit_paths(implementation_commit) - allowed):
        raise EvidenceError("implementation_commit cannot be evidence-only")


def slice_config(manifest: dict[str, Any]) -> dict[str, Any]:
    if "slice" not in manifest:
        base = manifest.get("slice_base_commit")
        matches = [
            config
            for config in SLICE_CONFIGS.values()
            if config["base_commit"] == base
        ]
        if len(matches) == 1:
            return matches[0]
        raise EvidenceError("Slice Exit Evidence authority cannot be inferred")
    number = manifest["slice"]["number"]
    try:
        return SLICE_CONFIGS[number]
    except KeyError as exc:
        raise EvidenceError(f"unsupported Slice Exit Evidence number: {number}") from exc


def validate_semantics(manifest: dict[str, Any]) -> None:
    commands = manifest["commands"]
    identities = [command["test_id"] for command in commands]
    if len(identities) != len(set(identities)):
        raise EvidenceError("command test_id values must be unique")
    expected_command_ids = slice_config(manifest)["command_ids"]
    if identities != expected_command_ids:
        raise EvidenceError(
            "Slice Exit Evidence commands differ from the registered closed test set"
        )
    for command in commands:
        derived = command["actual_exit_code"] == command["expected_exit_code"]
        if command["conforms"] != derived:
            raise EvidenceError(
                f"command conforms is stale: {command['test_id']}"
            )
    expected_checkpoints = slice_config(manifest)["checkpoints"]
    if manifest["expected_checkpoints"] != expected_checkpoints:
        raise EvidenceError(
            "Slice Exit Evidence checkpoints differ from the registered Slice authority"
        )
    config = slice_config(manifest)
    result_identities = [
        identity
        for values in manifest["results"].values()
        for identity in values
    ]
    if len(result_identities) != len(set(result_identities)):
        raise EvidenceError("result identities must be unique across evidence kinds")
    missing_fencing = set(config.get("required_fencing_results", [])) - set(
        manifest["results"].get("fencing", [])
    )
    if missing_fencing:
        raise EvidenceError(
            f"Slice fencing evidence is incomplete: {sorted(missing_fencing)}"
        )
    derived_pass = (
        all(command["conforms"] for command in commands)
        and all(manifest["results"][kind] for kind in config["result_kinds"])
        and not any(item["blocking"] for item in manifest["unresolved_exceptions"])
    )
    if (manifest["overall_decision"] == "pass") != derived_pass:
        raise EvidenceError("overall_decision differs from its evidence")
    if manifest["overall_decision"] == "pass" and manifest["unresolved_exceptions"]:
        raise EvidenceError("passing evidence cannot contain unresolved exceptions")


def validate_implementation_artifacts(manifest: dict[str, Any]) -> None:
    slice_base_commit = manifest["slice_base_commit"]
    implementation_commit = manifest["implementation_commit"]
    config = slice_config(manifest)
    if slice_base_commit != config["base_commit"]:
        raise EvidenceError("Slice base commit differs from its fixed authority")
    git("merge-base", "--is-ancestor", slice_base_commit, implementation_commit)
    try:
        expected = fingerprint_implementation_changes(
            PROJECT_ROOT,
            slice_base_commit,
            implementation_commit,
            excluded_prefixes=(config["evidence_prefix"],),
        )
    except EvidenceSupportError as exc:
        raise EvidenceError(str(exc)) from exc
    provided = manifest["artifact_fingerprints"]
    provided_paths = [item["path"] for item in provided]
    if len(provided_paths) != len(set(provided_paths)):
        raise EvidenceError("complete implementation change set has duplicate paths")
    expected_by_path = {item["path"]: item for item in expected}
    provided_by_path = {item["path"]: item for item in provided}
    if set(provided_by_path) != set(expected_by_path):
        missing = sorted(set(expected_by_path) - set(provided_by_path))
        extra = sorted(set(provided_by_path) - set(expected_by_path))
        raise EvidenceError(
            "artifact_fingerprints must equal the complete implementation change set: "
            f"missing={missing}, extra={extra}"
        )
    for path, expected_item in expected_by_path.items():
        provided_item = provided_by_path[path]
        if provided_item != expected_item:
            raise EvidenceError(
                "complete implementation change set fingerprint differs for "
                f"{path}: expected {expected_item}, got {provided_item}"
            )


def validate_bindings(manifest: dict[str, Any], manifest_path: Path) -> None:
    manifest_relative = manifest_path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    log_paths = {command["log"]["path"] for command in manifest["commands"]}
    expected_evidence_paths = {manifest_relative, *log_paths}
    if set(manifest["evidence_paths"]) != expected_evidence_paths:
        raise EvidenceError("evidence_paths must be exactly manifest plus command logs")
    seen: set[str] = set()
    bound = [command["log"] for command in manifest["commands"]]
    bound.extend(manifest["fixtures"])
    for item in bound:
        path = resolve_project_path(item["path"])
        identity = str(path).casefold()
        if identity in seen:
            raise EvidenceError(f"fingerprinted path is duplicated: {item['path']}")
        seen.add(identity)
        if not path.is_file():
            raise EvidenceError(f"fingerprinted path does not exist: {item['path']}")
        actual = sha256_file(path)
        if actual != item["sha256"]:
            raise EvidenceError(
                f"fingerprint mismatch for {item['path']}: expected {item['sha256']}, got {actual}"
            )


def validate_manifest(
    manifest_path: Path, *, schema_only: bool, pre_publication: bool
) -> None:
    ContractRegistry(PROJECT_ROOT).check()
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise EvidenceError(f"Exit Evidence v2 Schema is invalid: {exc.message}") from exc
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    try:
        Draft202012Validator(schema).validate(value)
    except ValidationError as exc:
        path = "/".join(str(part) for part in exc.absolute_path) or "$"
        raise EvidenceError(f"Schema validation failed at {path}: {exc.message}") from exc
    if schema_only:
        return
    validate_semantics(value)
    validate_bindings(value, manifest_path)
    validate_implementation_artifacts(value)
    validate_lineage(value, manifest_path, pre_publication=pre_publication)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate generic Slice Exit Evidence v2.")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--schema-only", action="store_true")
    parser.add_argument("--pre-publication", action="store_true")
    args = parser.parse_args(argv or sys.argv[1:])
    try:
        validate_manifest(
            args.manifest.resolve(),
            schema_only=args.schema_only,
            pre_publication=args.pre_publication,
        )
    except (EvidenceError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    print(f"VALID: {args.manifest.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
