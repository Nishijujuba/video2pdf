from __future__ import annotations

from dataclasses import dataclass
import importlib.metadata
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import tomllib
from typing import Any, Iterator

from .artifact_plan import ARTIFACT_PLAN_BINDINGS

try:
    from jsonschema import Draft202012Validator, FormatChecker
    from jsonschema.exceptions import SchemaError, ValidationError
    from referencing import Registry, Resource
except ImportError as exc:  # pragma: no cover - exercised by startup environments
    raise RuntimeError(
        "Workflow Kernel requires the locked jsonschema runtime; install "
        "requirements/pylock.video-workflow-runtime.toml"
    ) from exc

from .errors import ContractError, UnknownContractVersion, UnresolvedSchemaReference
from .utils import canonical_json_bytes, read_json, sha256_bytes, sha256_file


JSONSCHEMA_VERSION = "4.26.0"
DRAFT = "https://json-schema.org/draft/2020-12/schema"
REGISTRY_RELATIVE_PATH = Path("schemas/video-workflow/registry.v1.json")
RUNTIME_INPUT = Path("requirements/video-workflow-runtime.in")
RUNTIME_LOCK = Path("requirements/pylock.video-workflow-runtime.toml")


@dataclass(frozen=True)
class ContractEntry:
    schema_name: str
    schema_version: str
    schema_id: str
    schema_path: Path
    kind: str
    positive_example: Path | None
    negative_example: Path | None
    invariants: tuple[str, ...]
    canonical_instance: Path | None


WINDOWS_DEVICE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{number}" for number in range(1, 10)}
    | {f"LPT{number}" for number in range(1, 10)}
)
KNOWN_INVARIANTS = frozenset(
    {
        "artifact-plan-paths-v1",
        "artifact-plan-paths-v2",
        "bootstrap-source-identity-v2",
        "control-store-identity-path-v1",
        "fixture-package-paths-v1",
        "run-record-freshness-v1",
        "run-record-freshness-v2",
        "run-record-freshness-v3",
        "scaffold-contract-directories-v1",
        "source-manifest-paths-and-fingerprints-v1",
        "source-candidate-inventory-v1",
        "source-decision-skeleton-v1",
        "source-judgment-patch-v2",
        "source-manifest-paths-and-fingerprints-v2",
        "source-publication-journal-paths-v1",
        "source-reopen-journal-bindings-v1",
        "source-credential-resolution-evidence-v1",
        "resource-admission-configuration-v1",
        "resource-lease-resolution-evidence-v1",
        "task-attempt-path-v1",
        "task-completion-bindings-v1",
        "task-envelope-bindings-v1",
        "task-envelope-bindings-v2",
        "task-envelope-bindings-v3",
        "task-promotion-journal-paths-v1",
    }
)


def _validate_project_relative_path(value: str, *, prefix: str | None = None) -> None:
    if not isinstance(value, str) or not value or "\\" in value or re.match(r"^[A-Za-z]:", value):
        raise ContractError(f"path is not a canonical project-relative path: {value!r}")
    pure = PurePosixPath(value)
    parts = pure.parts
    if (
        pure.is_absolute()
        or not parts
        or pure.as_posix() != value
        or any(part in {".", ".."} for part in parts)
    ):
        raise ContractError(f"path is not a canonical project-relative path: {value!r}")
    for part in parts:
        if part.endswith((" ", ".")) or ":" in part:
            raise ContractError(f"path contains an unsupported Windows component: {value!r}")
        stem = part.split(".", 1)[0].upper()
        if stem in WINDOWS_DEVICE_NAMES:
            raise ContractError(f"path contains a reserved Windows device name: {value!r}")
    if prefix is not None and (not parts or parts[0] != prefix):
        raise ContractError(f"path must stay under {prefix}/: {value!r}")


def _validate_canonical_absolute_path(value: str) -> None:
    if not isinstance(value, str) or not value:
        raise ContractError(f"path is not canonical absolute: {value!r}")
    if re.match(r"^[A-Za-z]:", value) or value.startswith("\\\\"):
        pure: PureWindowsPath | PurePosixPath = PureWindowsPath(value)
    else:
        pure = PurePosixPath(value)
    if not pure.is_absolute() or str(pure) != value:
        raise ContractError(f"path is not canonical absolute: {value!r}")
    for part in pure.parts[1:]:
        if part in {".", ".."} or part.endswith((" ", ".")):
            raise ContractError(f"absolute path contains a noncanonical component: {value!r}")
        stem = part.split(".", 1)[0].upper()
        if stem in WINDOWS_DEVICE_NAMES:
            raise ContractError(f"absolute path contains a reserved component: {value!r}")


def _expected_source_identity(canonical_platform: str, canonical_item_id: str) -> str:
    return sha256_bytes(
        canonical_json_bytes(
            {
                "canonical_item_id": canonical_item_id,
                "canonical_platform": canonical_platform,
            }
        )
    )


def _validate_source_identity(instance: dict[str, Any]) -> None:
    expected = _expected_source_identity(
        instance["canonical_platform"], instance["canonical_item_id"]
    )
    if instance["source_identity"] != expected:
        raise ContractError("Source Identity does not bind canonical platform and item")


def _validate_redacted_argv(argv: list[str]) -> None:
    for index, value in enumerate(argv):
        if "\n" in value or "\r" in value:
            raise ContractError("redacted command argument contains a line break")
        lowered = value.casefold()
        if "authorization:" in lowered or "cookie:" in lowered:
            raise ContractError("redacted command argument contains authentication material")
        if (
            lowered.startswith("--cookies=")
            and value != "--cookies=<localized-cookie-file>"
        ):
            raise ContractError("Cookie command argument is not redacted")
        if value == "--cookies":
            if (
                index + 1 >= len(argv)
                or argv[index + 1] != "<localized-cookie-file>"
            ):
                raise ContractError("Cookie command argument is not redacted")
        if lowered.startswith("--cookies-from-browser"):
            raise ContractError("Browser cookie extraction is outside the adapter contract")


def _validate_artifact_plan(instance: dict[str, Any]) -> None:
    expected = {
        binding.logical_id: (
            binding.path,
            binding.schema_name,
            binding.generator,
            binding.earliest_checkpoint,
        )
        for binding in ARTIFACT_PLAN_BINDINGS
    }
    logical_ids: set[str] = set()
    paths: set[str] = set()
    for artifact in instance["artifacts"]:
        logical_id = artifact["logical_id"]
        path = artifact["path"]
        _validate_project_relative_path(path)
        if logical_id in logical_ids or path in paths:
            raise ContractError("Artifact Plan logical identities and paths must be unique")
        actual = (
            path,
            artifact["schema_name"],
            artifact["generator"],
            artifact["earliest_checkpoint"],
        )
        if expected.get(logical_id) != actual:
            raise ContractError(f"Artifact Plan binding is invalid for {logical_id!r}")
        logical_ids.add(logical_id)
        paths.add(path)
    if logical_ids != set(expected):
        raise ContractError("Artifact Plan does not contain the exact Slice 1 artifact set")


def _validate_artifact_plan_v2(instance: dict[str, Any]) -> None:
    expected = {
        "run_record": (
            "workflow/run.json", "run-record", "3.0.0", "kernel:init-run",
            (), "run_initialized", "always",
        ),
        "artifact_plan": (
            "workflow/artifact-plan.json", "artifact-plan", "2.0.0",
            "kernel:init-run", (), "run_initialized", "always",
        ),
        "bootstrap_record": (
            "待删除/bootstrap/probe.json", "bootstrap-record", "2.0.0",
            "kernel:bootstrap", (), "run_initialized", "always",
        ),
        "scaffold_contract": (
            "workflow/scaffold-contract.json", "scaffold-contract", "1.0.0",
            "kernel:init-run", (), "run_initialized", "always",
        ),
        "scaffold_ledger": (
            "workflow/scaffold-ledger.json", "scaffold-ledger", "1.0.0",
            "kernel:init-run", (), "run_initialized", "always",
        ),
        "source_candidate_inventory": (
            "work/source-acquisition/candidate-inventory.json",
            "source-candidate-inventory", "1.0.0", "kernel:source-candidates",
            (("bootstrap_record", "always"),), "source_candidates_ready", "always",
        ),
        "source_acquisition_decision_skeleton": (
            "work/source-acquisition/decision.skeleton.json",
            "source-acquisition-decision-skeleton", "1.0.0",
            "kernel:source-prepare",
            (("source_candidate_inventory", "always"),),
            "source_candidates_ready", "fresh_download",
        ),
        "source_acquisition_decision": (
            "workflow/source-acquisition-judgment-patch.json",
            "source-acquisition-judgment-patch", "2.0.0",
            "task:source-acquisition",
            (
                ("source_candidate_inventory", "always"),
                ("source_acquisition_decision_skeleton", "fresh_download"),
            ),
            "source_acquisition_decision_ready", "fresh_download",
        ),
        "source_transcription": (
            "work/source-acquisition/transcription.srt",
            "source-transcription-srt", "1.0.0",
            "task:whisper-transcription",
            (
                ("source_candidate_inventory", "always"),
                ("source_acquisition_decision", "fresh_download"),
            ),
            "source_acquisition_decision_ready", "whisper_requested",
        ),
        "source_manifest": (
            "source/manifest.json", "source-manifest", "2.0.0",
            "kernel:source-finalize",
            (
                ("source_candidate_inventory", "always"),
                ("source_acquisition_decision", "fresh_download"),
                ("source_transcription", "whisper_requested"),
            ),
            "source_ready", "always",
        ),
    }
    artifacts = instance["artifacts"]
    by_id = {artifact["logical_id"]: artifact for artifact in artifacts}
    if len(by_id) != len(artifacts) or set(by_id) != set(expected):
        raise ContractError("Artifact Plan v2 must contain each fixed artifact exactly once")
    paths: set[str] = set()
    for logical_id, artifact in by_id.items():
        path = artifact["path"]
        _validate_project_relative_path(path)
        if path in paths:
            raise ContractError("Artifact Plan v2 repeats a canonical path")
        paths.add(path)
        expected_path, schema_name, schema_version, generator, dependencies, earliest, condition = expected[logical_id]
        if path != expected_path:
            raise ContractError(f"Artifact Plan v2 path is invalid for {logical_id!r}")
        actual_dependencies = tuple(
            (item["logical_id"], item["when"]) for item in artifact["dependencies"]
        )
        actual = (
            artifact["schema_name"], artifact["schema_version"],
            artifact["generator"], actual_dependencies,
            artifact["earliest_checkpoint"], artifact["condition"],
        )
        wanted = (schema_name, schema_version, generator, dependencies, earliest, condition)
        if actual != wanted:
            raise ContractError(f"Artifact Plan v2 binding is invalid for {logical_id!r}")

    visited: set[str] = set()
    active: set[str] = set()

    def visit(logical_id: str) -> None:
        if logical_id in active:
            raise ContractError("Artifact Plan v2 dependency graph contains a cycle")
        if logical_id in visited:
            return
        active.add(logical_id)
        for dependency in by_id[logical_id]["dependencies"]:
            visit(dependency["logical_id"])
        active.remove(logical_id)
        visited.add(logical_id)

    for logical_id in by_id:
        visit(logical_id)


def _validate_source_manifest(instance: dict[str, Any]) -> None:
    logical_ids: set[str] = set()
    paths: set[str] = set()
    for artifact in instance["artifacts"]:
        logical_id = artifact["logical_id"]
        path = artifact["path"]
        _validate_project_relative_path(path, prefix="source")
        if logical_id in logical_ids or path in paths:
            raise ContractError("Source Manifest artifact identities and paths must be unique")
        logical_ids.add(logical_id)
        paths.add(path)


def _validate_bootstrap_record_v2(instance: dict[str, Any]) -> None:
    _validate_source_identity(instance)
    adapter = instance["adapter"]
    platform = instance["canonical_platform"]
    if adapter["id"] != platform or adapter["canonical_platform"] != platform:
        raise ContractError("Bootstrap Adapter does not bind its canonical platform")
    mode = instance["requested_source_acquisition_mode"]
    request = instance["source_request"]
    if request["kind"] != mode:
        raise ContractError("Bootstrap request kind differs from acquisition mode")
    execution = instance["probe_execution"]
    _validate_redacted_argv(execution["command_argv_redacted"])
    provider_kind = execution["provider_kind"]
    admission = execution["resource_admission"]
    if provider_kind == "deterministic_locator":
        item_id = instance["canonical_item_id"]
        if platform == "youtube":
            if re.fullmatch(r"[0-9A-Za-z_-]{11}", item_id) is None:
                raise ContractError(
                    "deterministic YouTube Bootstrap item identity is invalid"
                )
            canonical_locator = f"https://www.youtube.com/watch?v={item_id}"
            explicit_item_selector = None
        else:
            matched = re.fullmatch(r"(BV[0-9A-Za-z]{10}):p([1-9][0-9]*)", item_id)
            if matched is None:
                raise ContractError(
                    "deterministic Bilibili Bootstrap item identity is invalid"
                )
            canonical_locator = (
                f"https://www.bilibili.com/video/{matched.group(1)}/"
            )
            explicit_item_selector = f"p{matched.group(2)}"
        locator_evidence = {
            "canonical_platform": platform,
            "canonical_item_id": item_id,
            "canonical_url": canonical_locator,
            "original_title": instance["original_title"],
            "explicit_item_selector": explicit_item_selector,
        }
        if (
            admission is not None
            or execution["command_argv_redacted"] != []
            or execution["authentication_classification"] != "not_applicable"
            or instance["source_request"]["canonical_locator"]
            != canonical_locator
            or execution["normalized_result_sha256"]
            != sha256_bytes(canonical_json_bytes(locator_evidence))
            or instance["availability"]
            != {
                "status": "pending",
                "duration_seconds": None,
                "chapter_count": None,
                "subtitle_languages": [],
                "media_format_classes": [],
            }
        ):
            raise ContractError(
                "deterministic Bootstrap Locator contains execution evidence"
            )
    elif admission is not None:
        raise ContractError("offline Bootstrap Probe must not claim a live Resource Lease")
    elif provider_kind == "recorded_fixture" and not execution[
        "command_argv_redacted"
    ]:
        raise ContractError("recorded Bootstrap Probe lacks command evidence")
    if mode == "verified_import":
        if provider_kind != "verified_import":
            raise ContractError("Verified Import Bootstrap uses the wrong provider kind")
        if execution["authentication_classification"] != "not_applicable":
            raise ContractError("Verified Import Bootstrap has an authentication class")
    elif provider_kind == "verified_import":
        raise ContractError("fresh Bootstrap cannot use Verified Import provider evidence")


def _validate_candidate_inventory(instance: dict[str, Any]) -> None:
    _validate_source_identity(instance)
    platform = instance["canonical_platform"]
    if instance["adapter"]["id"] != platform:
        raise ContractError("Candidate Inventory Adapter differs from canonical platform")
    provider = instance["provider"]
    mode = instance["mode"]
    auth = instance["authentication_classification"]
    if auth not in {"not_applicable", "anonymous", "cookie_accepted"}:
        raise ContractError("completed Candidate Inventory has a failure auth class")
    recording_sha = provider["recording_sha256"]
    if (provider["kind"] == "recorded_fixture") != (recording_sha is not None):
        raise ContractError("recorded provider fingerprint binding is incomplete")
    if mode == "verified_import":
        if provider["kind"] != "verified_import" or auth != "not_applicable":
            raise ContractError("Verified Import Inventory has invalid provider provenance")
        binding = instance["import_binding"]
        if binding is None:
            raise ContractError("Verified Import Inventory lacks import validation")
        if binding["prior_source_identity"] != instance["source_identity"]:
            raise ContractError("Verified Import prior Source Identity differs")
    else:
        if provider["kind"] == "verified_import" or instance["import_binding"] is not None:
            raise ContractError("fresh Candidate Inventory contains import authority")
    candidate_root = PurePosixPath("work/source-acquisition/candidates")
    if mode == "fresh_download":
        candidate_root /= f"e{instance['source_epoch']}"
    tools = [item["name"] for item in provider["tool_versions"]]
    if len(tools) != len(set(tools)):
        raise ContractError("Candidate Inventory repeats a tool version identity")
    command_ids: set[str] = set()
    for command in instance["commands"]:
        if command["command_id"] in command_ids:
            raise ContractError("Candidate Inventory repeats a command identity")
        command_ids.add(command["command_id"])
        _validate_redacted_argv(command["command_argv_redacted"])
    candidate_ids: set[str] = set()
    paths: set[str] = set()
    roles: set[str] = set()
    for candidate in instance["candidates"]:
        candidate_id = candidate["candidate_id"]
        path = candidate["staged_path"]
        _validate_project_relative_path(path, prefix="work")
        try:
            relative_candidate_path = PurePosixPath(path).relative_to(candidate_root)
        except ValueError as exc:
            raise ContractError(
                "source candidate is outside its epoch staging boundary"
            ) from exc
        if not relative_candidate_path.parts:
            raise ContractError("source candidate is outside its staging boundary")
        if candidate_id in candidate_ids or path in paths:
            raise ContractError("Candidate Inventory identities and paths must be unique")
        candidate_ids.add(candidate_id)
        paths.add(path)
        role = candidate["role"]
        roles.add(role)
        if role in {"subtitle", "transcript"}:
            if candidate["language"] is None or candidate["subtitle_kind"] is None:
                raise ContractError("subtitle candidate lacks language or track kind")
        elif candidate["subtitle_kind"] is not None:
            raise ContractError("non-subtitle candidate declares a subtitle kind")
        expected_origin = (
            "verified_import" if mode == "verified_import" else "platform_download"
        )
        if candidate["origin"] != expected_origin:
            raise ContractError("candidate origin differs from Source Acquisition Mode")
    if not {"metadata", "cover", "video"}.issubset(roles):
        raise ContractError("Candidate Inventory lacks required original-source roles")
    policy = instance["policy_binding"]
    if (
        policy["content_classification"] == "language_learning"
        and policy["subtitle_language_priority"][0] != "en"
    ):
        raise ContractError("language-learning subtitle policy must prioritize English")


def _validate_decision_skeleton(instance: dict[str, Any]) -> None:
    _validate_project_relative_path(
        instance["candidate_inventory"]["path"], prefix="work"
    )
    allowed = instance["allowed_judgment"]
    choices = allowed["whisper_choices"]
    if "use_whisper" in choices and allowed["whisper_audio_candidate_id"] is None:
        raise ContractError("Decision Skeleton permits Whisper without an audio candidate")


def _validate_known_gaps(known_gaps: list[dict[str, Any]]) -> None:
    codes: set[str] = set()
    for gap in known_gaps:
        if gap["code"] in codes:
            raise ContractError("Judgment repeats a known-gap code")
        codes.add(gap["code"])
        previous_end = -1.0
        for affected in gap["affected_ranges"]:
            if affected["end_seconds"] <= affected["start_seconds"]:
                raise ContractError("known-gap time range is empty or reversed")
            if affected["start_seconds"] < previous_end:
                raise ContractError("known-gap time ranges overlap or are unsorted")
            previous_end = affected["end_seconds"]


def _validate_judgment_patch_v2(instance: dict[str, Any]) -> None:
    judgment = instance["judgment"]
    selection = judgment["selected_subtitle_candidate_id"]
    choice = judgment["whisper_fallback"]["choice"]
    if choice == "not_required" and selection is None:
        raise ContractError("not_required Whisper decision lacks a subtitle selection")
    if choice == "use_whisper" and selection is not None:
        raise ContractError("Whisper fallback cannot also select a subtitle candidate")
    if choice == "unavailable" and not judgment["known_gaps"]:
        raise ContractError("unavailable Whisper fallback lacks an explicit source gap")
    _validate_known_gaps(judgment["known_gaps"])


def _version_artifact(artifact: dict[str, Any]) -> dict[str, Any]:
    return {
        key: artifact[key]
        for key in (
            "logical_id",
            "role",
            "media_type",
            "sha256",
            "size_bytes",
            "language",
            "subtitle_kind",
            "technical_probe",
        )
    }


def _validate_source_manifest_v2(instance: dict[str, Any]) -> None:
    _validate_source_identity(instance)
    if instance["adapter"]["id"] != instance["canonical_platform"]:
        raise ContractError("Source Manifest Adapter differs from canonical platform")
    mode = instance["mode"]
    provenance = instance["provenance"]
    if provenance["kind"] != mode:
        raise ContractError("Source Manifest provenance differs from acquisition mode")
    logical_ids: set[str] = set()
    paths: set[str] = set()
    roles: set[str] = set()
    artifacts_by_id: dict[str, dict[str, Any]] = {}
    for artifact in instance["artifacts"]:
        logical_id = artifact["logical_id"]
        path = artifact["path"]
        _validate_project_relative_path(path, prefix="source")
        if logical_id in logical_ids or path in paths:
            raise ContractError("Source Manifest v2 identities and paths must be unique")
        logical_ids.add(logical_id)
        paths.add(path)
        roles.add(artifact["role"])
        artifacts_by_id[logical_id] = artifact
        if mode == "verified_import" and artifact["origin"] != "verified_import":
            raise ContractError("Verified Import Manifest contains a non-import origin")
        if artifact["role"] in {"subtitle", "transcript"}:
            if artifact["language"] is None or artifact["subtitle_kind"] is None:
                raise ContractError("Source subtitle artifact lacks language or track kind")
        elif artifact["subtitle_kind"] is not None:
            raise ContractError("non-subtitle Source artifact declares a track kind")
    if not {"metadata", "cover", "video"}.issubset(roles):
        raise ContractError("Source Manifest v2 lacks required package roles")
    selected = instance["selection"]["selected_subtitle_artifact_id"]
    if selected is not None:
        selected_artifact = artifacts_by_id.get(selected)
        if selected_artifact is None or selected_artifact["role"] not in {
            "subtitle", "transcript"
        }:
            raise ContractError("selected subtitle does not bind a Manifest artifact")
    if instance["selection"]["whisper_status"] == "used":
        if (
            selected is None
            or artifacts_by_id[selected]["origin"] != "whisper_transcription"
            or provenance["kind"] != "fresh_download"
        ):
            raise ContractError("Whisper selection lacks a fresh transcription artifact")
    if mode == "verified_import":
        if (
            provenance["prior_source_identity"] != instance["source_identity"]
            or provenance["prior_source_version"] != instance["source_version"]
        ):
            raise ContractError("Verified Import Manifest changed Source Identity or Version")
    expected_artifacts = sorted(
        (_version_artifact(item) for item in instance["artifacts"]),
        key=lambda item: item["logical_id"],
    )
    expected_basis = {
        "canonicalization": "video2pdf-canonical-json-v1",
        "source_identity": instance["source_identity"],
        "artifacts": expected_artifacts,
    }
    if instance["source_version_basis"] != expected_basis:
        raise ContractError("Source Version basis differs from Manifest artifacts")
    expected_version = sha256_bytes(canonical_json_bytes(expected_basis))
    if instance["source_version"] != expected_version:
        raise ContractError("Source Version fingerprint differs from its canonical basis")
    _validate_known_gaps(instance["known_gaps"])


def _validate_source_publication_journal(instance: dict[str, Any]) -> None:
    intent_id = instance["intent_id"]
    candidate_root = (
        f"work/source-acquisition/publications/{intent_id}/candidate/source"
    )
    preservation_root = (
        f"待删除/source-publications/{intent_id}/previous/source"
    )
    if instance["candidate_source_root"] != candidate_root:
        raise ContractError("Source publication candidate root differs from Intent identity")
    if instance["preservation_root"] != preservation_root:
        raise ContractError("Source publication preservation root differs from Intent identity")
    _validate_project_relative_path(candidate_root, prefix="work")
    _validate_project_relative_path(preservation_root, prefix="待删除")
    if instance["publication_kind"] == "initial_publish":
        if instance["prior_source_manifest_sha256"] is not None:
            raise ContractError("initial Source publication declares a prior Manifest")
    elif instance["prior_source_manifest_sha256"] is None:
        raise ContractError("Source reopen publication lacks its prior Manifest")
    logical_ids: set[str] = set()
    canonical_paths: set[str] = set()
    manifest_output: dict[str, Any] | None = None
    for output in instance["outputs"]:
        logical_id = output["logical_id"]
        candidate_path = output["candidate_path"]
        canonical_path = output["canonical_path"]
        preservation_path = output["preservation_path"]
        _validate_project_relative_path(candidate_path, prefix="work")
        _validate_project_relative_path(canonical_path, prefix="source")
        _validate_project_relative_path(preservation_path, prefix="待删除")
        if not candidate_path.startswith(f"{candidate_root}/"):
            raise ContractError("publication candidate is outside its Intent root")
        relative = candidate_path.removeprefix(f"{candidate_root}/")
        if canonical_path != f"source/{relative}":
            raise ContractError("publication candidate and canonical paths disagree")
        if preservation_path != f"{preservation_root}/{relative}":
            raise ContractError("publication preservation path disagrees with canonical path")
        if logical_id in logical_ids or canonical_path in canonical_paths:
            raise ContractError("Source publication journal repeats an output")
        logical_ids.add(logical_id)
        canonical_paths.add(canonical_path)
        if canonical_path == "source/manifest.json":
            manifest_output = output
    if (
        manifest_output is None
        or manifest_output["sha256"] != instance["replacement_source_manifest_sha256"]
    ):
        raise ContractError("Source publication journal does not bind replacement Manifest")


def _validate_source_reopen_journal(instance: dict[str, Any]) -> None:
    intent_id = instance["intent_id"]
    replacement = instance["replacement_run_record"]
    replacement_sha256 = sha256_bytes(canonical_json_bytes(replacement))
    if replacement_sha256 != instance["replacement_run_record_sha256"]:
        raise ContractError("Source Reopen replacement fingerprint is invalid")
    if instance["prior_run_record_sha256"] == replacement_sha256:
        raise ContractError("Source Reopen replacement did not change the Run Record")
    if replacement["run_id"] != instance["run_id"]:
        raise ContractError("Source Reopen replacement changed the Run identity")
    if replacement["last_mutation_intent_id"] != intent_id:
        raise ContractError("Source Reopen replacement lacks its Intent identity")
    if replacement["source_epoch"] != instance["prior_source_epoch"] + 1:
        raise ContractError("Source Reopen replacement Source Epoch is invalid")
    if (
        replacement["coordination_revision"]
        != instance["expected_run_revision"] + 1
    ):
        raise ContractError("Source Reopen replacement revision is invalid")
    if (
        replacement["source_state"] != "stale"
        or replacement["source_version"] is not None
        or replacement["source_blocker"] is not None
        or replacement["phase"] != "source_acquisition"
    ):
        raise ContractError("Source Reopen replacement retains current Source authority")
    source_generation = replacement["artifact_generations"].get("source_manifest")
    if (
        source_generation is None
        or source_generation["sha256"] != instance["source_manifest_sha256"]
        or source_generation["source_epoch"] != instance["prior_source_epoch"]
    ):
        raise ContractError("Source Reopen journal differs from its prior Manifest")

    expected_journal_path = f"待删除/source-reopens/{intent_id}/reopen.json"
    if instance["journal_path"] != expected_journal_path:
        raise ContractError("Source Reopen journal path differs from Intent identity")
    _validate_project_relative_path(instance["coordination_record_path"], prefix="workflow")
    _validate_project_relative_path(instance["journal_path"], prefix="待删除")

    preservation_specs = {
        "source_package": ("source_tree", "source", "source_manifest"),
        "source_candidates": (
            "candidate_tree",
            "work/source-acquisition/candidates",
            None,
        ),
        "source_candidate_inventory": (
            "control_file",
            "work/source-acquisition/candidate-inventory.json",
            "source_candidate_inventory",
        ),
        "source_acquisition_decision_skeleton": (
            "control_file",
            "work/source-acquisition/decision.skeleton.json",
            "source_acquisition_decision_skeleton",
        ),
        "source_transcription": (
            "control_file",
            "work/source-acquisition/transcription.srt",
            "source_transcription",
        ),
        "source_acquisition_decision": (
            "control_file",
            "workflow/source-acquisition-judgment-patch.json",
            "source_acquisition_decision",
        ),
    }
    logical_ids: set[str] = set()
    current_paths: set[str] = set()
    for preservation in instance["preservations"]:
        logical_id = preservation["logical_id"]
        kind, expected_current, generation_id = preservation_specs[logical_id]
        current_path = preservation["current_path"]
        preservation_path = preservation["preservation_path"]
        evidence_path = preservation["evidence_path"]
        expected_sha256 = preservation["expected_sha256"]
        if logical_id in logical_ids or current_path in current_paths:
            raise ContractError("Source Reopen journal repeats a preservation binding")
        logical_ids.add(logical_id)
        current_paths.add(current_path)
        if kind != preservation["kind"] or current_path != expected_current:
            raise ContractError("Source Reopen preservation path differs from its role")
        _validate_project_relative_path(current_path)
        _validate_project_relative_path(preservation_path, prefix="待删除")
        expected_preservation = (
            f"待删除/source-reopens/{intent_id}/previous/{current_path}"
        )
        if preservation_path != expected_preservation:
            raise ContractError("Source Reopen preservation escapes its Intent boundary")
        if evidence_path is not None:
            _validate_project_relative_path(evidence_path)
            external_candidate_inventory = (
                logical_id == "source_candidates"
                and evidence_path
                == "work/source-acquisition/candidate-inventory.json"
            )
            if (
                not external_candidate_inventory
                and evidence_path != current_path
                and not evidence_path.startswith(f"{current_path}/")
            ):
                raise ContractError("Source Reopen evidence path escapes its current path")
        if (evidence_path is None) != (expected_sha256 is None):
            raise ContractError("Source Reopen preservation evidence is incomplete")
        if kind == "control_file" and evidence_path != current_path:
            raise ContractError("Source Reopen control evidence does not bind its file")
        if generation_id is not None:
            generation = replacement["artifact_generations"].get(generation_id)
            if generation is None or generation["sha256"] != expected_sha256:
                raise ContractError(
                    "Source Reopen preservation differs from its Artifact Generation"
                )

    expected_preservations = {"source_package"}
    generations = replacement["artifact_generations"]
    if "source_candidate_inventory" in generations:
        expected_preservations.update(
            {"source_candidates", "source_candidate_inventory"}
        )
    expected_preservations.update(
        logical_id
        for logical_id in (
            "source_acquisition_decision_skeleton",
            "source_transcription",
            "source_acquisition_decision",
        )
        if logical_id in generations
    )
    if logical_ids != expected_preservations:
        raise ContractError(
            "Source Reopen journal omits a prior Source acquisition generation"
        )
    if "source_candidates" in logical_ids:
        candidate = next(
            item
            for item in instance["preservations"]
            if item["logical_id"] == "source_candidates"
        )
        inventory_generation = generations["source_candidate_inventory"]
        if (
            candidate["evidence_path"]
            != "work/source-acquisition/candidate-inventory.json"
            or candidate["expected_sha256"] != inventory_generation["sha256"]
        ):
            raise ContractError(
                "Source Reopen Candidate tree lacks its closed Inventory evidence"
            )

    source_preservation = next(
        (
            item
            for item in instance["preservations"]
            if item["logical_id"] == "source_package"
        ),
        None,
    )
    if (
        source_preservation is None
        or source_preservation["evidence_path"] != "source/manifest.json"
        or source_preservation["expected_sha256"]
        != instance["source_manifest_sha256"]
    ):
        raise ContractError("Source Reopen journal lacks its prior Source Package")


def _validate_fixture_package(instance: dict[str, Any]) -> None:
    logical_ids: set[str] = set()
    paths: set[str] = set()
    allowed_roots = {"metadata", "subtitles", "media", "cover"}
    for artifact in instance["artifacts"]:
        logical_id = artifact["logical_id"]
        path = artifact["path"]
        _validate_project_relative_path(path)
        if PurePosixPath(path).parts[0] not in allowed_roots:
            raise ContractError(f"fixture artifact path has an unapproved root: {path!r}")
        if logical_id in logical_ids or path in paths:
            raise ContractError("fixture artifact identities and paths must be unique")
        logical_ids.add(logical_id)
        paths.add(path)


def _validate_run_record(instance: dict[str, Any]) -> None:
    generation = instance["artifact_generations"]["source_manifest"]
    checkpoint = instance["checkpoints"]["source_ready"]
    _validate_project_relative_path(generation["path"], prefix="source")
    _validate_project_relative_path(instance["artifact_plan"])
    _validate_canonical_absolute_path(instance["output_path"])
    if checkpoint["artifact_generations"]["source_manifest"] != generation["generation"]:
        raise ContractError("source_ready generation does not bind to Source Manifest generation")
    if checkpoint["evidence_sha256"] != generation["sha256"]:
        raise ContractError("source_ready evidence fingerprint does not bind to Source Manifest")


def _validate_task_capable_run_record(instance: dict[str, Any]) -> None:
    source = instance["artifact_generations"]["source_manifest"]
    decision = instance["artifact_generations"]["source_acquisition_decision"]
    source_checkpoint = instance["checkpoints"]["source_ready"]
    decision_checkpoint = instance["checkpoints"]["source_acquisition_decision_ready"]
    _validate_project_relative_path(source["path"], prefix="source")
    _validate_project_relative_path(decision["path"], prefix="workflow")
    _validate_project_relative_path(instance["artifact_plan"])
    _validate_canonical_absolute_path(instance["output_path"])
    if source_checkpoint["artifact_generations"]["source_manifest"] != source["generation"]:
        raise ContractError("source_ready generation does not bind to Source Manifest generation")
    if source_checkpoint["evidence_sha256"] != source["sha256"]:
        raise ContractError("source_ready evidence fingerprint does not bind to Source Manifest")
    bound = decision_checkpoint["artifact_generations"]
    if (
        bound["source_manifest"] != source["generation"]
        or bound["source_acquisition_decision"] != decision["generation"]
    ):
        raise ContractError("task checkpoint does not bind current Artifact Generations")
    if decision_checkpoint["evidence_sha256"] != decision["sha256"]:
        raise ContractError("task checkpoint evidence does not bind promoted decision")
    if instance["checkpoint_dependencies"] != {
        "source_acquisition_decision_ready": ["source_ready"]
    }:
        raise ContractError("task checkpoint dependency graph is not canonical")


def _validate_run_record_v3(instance: dict[str, Any]) -> None:
    _validate_source_identity(instance)
    _validate_project_relative_path(instance["artifact_plan"], prefix="workflow")
    _validate_canonical_absolute_path(instance["output_path"])
    platform = instance["canonical_platform"]
    if instance["platform_adapter"] != platform:
        raise ContractError("Run Record Adapter differs from canonical platform")
    if (
        instance["requested_source_acquisition_mode"] == "fresh_download"
        and instance["source_acquisition_mode"] != "fresh_download"
    ):
        raise ContractError("fresh Source request cannot become Verified Import")
    blocker = instance["source_blocker"]
    if blocker is not None:
        expected_resource = f"{platform}_download"
        if (
            blocker["canonical_platform"] != platform
            or blocker["resource_class"] != expected_resource
        ):
            raise ContractError("Source blocker differs from Run platform")
        expected_breaker = (
            "open"
            if blocker["reason"] in {"cookie_rejected", "cookie_expired"}
            else "not_open"
        )
        if blocker["breaker_state"] != expected_breaker:
            raise ContractError("Source blocker reason differs from breaker state")

    expected_paths = {
        "bootstrap_record": ("待删除/bootstrap/probe.json", "待删除"),
        "source_candidate_inventory": (
            "work/source-acquisition/candidate-inventory.json", "work"
        ),
        "source_acquisition_decision_skeleton": (
            "work/source-acquisition/decision.skeleton.json", "work"
        ),
        "source_acquisition_decision": (
            "workflow/source-acquisition-judgment-patch.json", "workflow"
        ),
        "source_transcription": (
            "work/source-acquisition/transcription.srt", "work"
        ),
        "source_manifest": ("source/manifest.json", "source"),
    }
    generations = instance["artifact_generations"]
    for logical_id, generation in generations.items():
        path = generation["path"]
        if logical_id == "source_credential_resolution_evidence":
            _validate_project_relative_path(path, prefix="待删除")
            parts = PurePosixPath(path).parts
            if (
                len(parts) != 4
                or parts[:2] != ("待删除", "source-blocker-resolutions")
                or re.fullmatch(r"[0-9a-f]{64}", parts[2]) is None
                or parts[3] != "credential-evidence.json"
                or generation["producer"] != "kernel:source-blocker-resolve"
                or generation["source_epoch"] < 2
            ):
                raise ContractError(
                    "Run credential resolution evidence binding is invalid"
                )
            continue
        expected_path, prefix = expected_paths[logical_id]
        _validate_project_relative_path(path, prefix=prefix)
        if path != expected_path:
            raise ContractError(f"Run Artifact path is invalid for {logical_id!r}")

    expected_dependencies = {
        "run_initialized": [],
        "source_candidates_ready": ["run_initialized"],
        "source_acquisition_decision_ready": ["source_candidates_ready"],
        "source_ready": [
            "source_acquisition_decision_ready"
            if instance["source_acquisition_mode"] == "fresh_download"
            else "source_candidates_ready"
        ],
    }
    if instance["checkpoint_dependencies"] != expected_dependencies:
        raise ContractError("Run v3 checkpoint dependency graph is not canonical")

    checkpoints = instance["checkpoints"]
    for checkpoint_name, checkpoint in checkpoints.items():
        binding_ids: set[str] = set()
        for binding in checkpoint["artifact_bindings"]:
            logical_id = binding["logical_id"]
            if logical_id in binding_ids:
                raise ContractError("Run checkpoint repeats an Artifact binding")
            binding_ids.add(logical_id)
            generation = generations.get(logical_id)
            if generation is None:
                raise ContractError("Run checkpoint binds an unregistered Artifact")
            if checkpoint["status"] == "current" and (
                binding["generation"] != generation["generation"]
                or binding["sha256"] != generation["sha256"]
                or generation["source_epoch"] not in {0, instance["source_epoch"]}
            ):
                raise ContractError("current Run checkpoint binds a stale Artifact Generation")
        prerequisite_ids: set[str] = set()
        for binding in checkpoint["prerequisite_bindings"]:
            prerequisite = binding["checkpoint"]
            if prerequisite in prerequisite_ids:
                raise ContractError("Run checkpoint repeats a prerequisite binding")
            prerequisite_ids.add(prerequisite)
            prerequisite_checkpoint = checkpoints.get(prerequisite)
            if prerequisite_checkpoint is None:
                raise ContractError("Run checkpoint binds a missing prerequisite")
            if checkpoint["status"] == "current" and (
                prerequisite_checkpoint["status"] != "current"
                or binding["evidence_sha256"]
                != prerequisite_checkpoint["evidence_sha256"]
            ):
                raise ContractError("current Run checkpoint binds a stale prerequisite")
        if prerequisite_ids != set(expected_dependencies[checkpoint_name]):
            raise ContractError("Run checkpoint prerequisite bindings are incomplete")

    def is_current(name: str) -> bool:
        return name in checkpoints and checkpoints[name]["status"] == "current"

    if not is_current("run_initialized"):
        raise ContractError("Run v3 lacks a current initialization checkpoint")
    state = instance["source_state"]
    version = instance["source_version"]
    if state == "blocked_user_input":
        if blocker is None or version is not None:
            raise ContractError("blocked Source state lacks its user-input blocker")
    elif blocker is not None:
        raise ContractError("Source blocker exists outside blocked_user_input state")
    if state == "pending":
        if version is not None or is_current("source_candidates_ready"):
            raise ContractError("pending Source state has current candidate evidence")
    elif state == "candidates_ready":
        if version is not None or not is_current("source_candidates_ready"):
            raise ContractError("candidates_ready state lacks its checkpoint")
    elif state == "decision_ready":
        if (
            version is not None
            or instance["source_acquisition_mode"] != "fresh_download"
            or not is_current("source_acquisition_decision_ready")
        ):
            raise ContractError("decision_ready state lacks a fresh Decision checkpoint")
    elif state == "ready":
        if (
            version is None
            or instance["phase"] != "source_ready"
            or not is_current("source_ready")
            or "source_manifest" not in generations
        ):
            raise ContractError("ready Source state lacks current Manifest authority")
        if (
            instance["source_acquisition_mode"] == "fresh_download"
            and not is_current("source_acquisition_decision_ready")
        ):
            raise ContractError("fresh source_ready lacks current semantic Decision")
        if (
            instance["source_acquisition_mode"] == "verified_import"
            and is_current("source_acquisition_decision_ready")
        ):
            raise ContractError("Verified Import unexpectedly has semantic Decision authority")
    elif state == "stale" and (version is not None or is_current("source_ready")):
        raise ContractError("stale Source state retains current Source authority")
    if state != "ready" and instance["phase"] != "source_acquisition":
        raise ContractError("pre-source Run phase is not source_acquisition")


def _validate_task_envelope(instance: dict[str, Any]) -> None:
    task_id = instance["task_id"]
    expected_root = f"workflow/tasks/{task_id}"
    if instance["task_root_path"] != expected_root:
        raise ContractError("Task Envelope root does not bind its task identity")
    if instance["generated_prompt"]["path"] != f"{expected_root}/prompt.md":
        raise ContractError("Generated Task Prompt path does not bind its task identity")
    for value in instance["allowed_read_paths"]:
        _validate_project_relative_path(value, prefix="source")
    snapshot_paths: set[str] = set()
    for item in instance["protected_run_snapshot"]:
        _validate_project_relative_path(item["path"])
        if item["path"] in snapshot_paths:
            raise ContractError("Task Envelope protected Run snapshot repeats a path")
        if item["path"].startswith(f"{expected_root}/"):
            raise ContractError("Task Envelope snapshots its dynamic Task boundary")
        snapshot_paths.add(item["path"])
    for value in instance["write_set"]:
        _validate_project_relative_path(value, prefix="workflow")
    for output in instance["required_outputs"]:
        _validate_project_relative_path(output["attempt_relative_path"])
        _validate_project_relative_path(output["canonical_path"], prefix="workflow")
        if output["canonical_path"] not in instance["write_set"]:
            raise ContractError("required output is outside the declared write set")
        prior_generation = output["expected_prior_generation"]
        prior_sha = output["expected_prior_sha256"]
        if (prior_generation is None) != (prior_sha is None):
            raise ContractError("prior Artifact Generation identity is incomplete")
    for source in (
        instance["generated_prompt"]["role_template"],
        instance["generated_prompt"]["platform_overlay"],
    ):
        _validate_project_relative_path(source["path"], prefix="prompts")


def _validate_resource_admission_configuration(instance: dict[str, Any]) -> None:
    expected = {
        "bilibili_download",
        "youtube_download",
        "whisper",
        "codex_semantic",
        "latex",
        "pdf_render",
        "visual_acceptance",
    }
    actual = [item["resource_class"] for item in instance["resources"]]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ContractError(
            "Resource Admission Configuration must define every Resource Class exactly once"
        )


def _validate_task_envelope_v2(instance: dict[str, Any]) -> None:
    _validate_task_envelope(instance)
    resources = instance["resource_request"]
    if resources != sorted(resources):
        raise ContractError("Task Envelope Resource Request must use stable sorted order")
    run_id = instance["authority_binding"]["run_id"]
    batch_id = instance.get("batch_id")
    expected_group = batch_id if batch_id is not None else run_id
    if instance["fairness_group_id"] != expected_group:
        raise ContractError(
            "Task Envelope Fairness Group must equal its Batch or standalone Run identity"
        )


def _validate_task_envelope_v3(instance: dict[str, Any]) -> None:
    task_id = instance["task_id"]
    task_root = f"workflow/tasks/{task_id}"
    if instance["task_root_path"] != task_root:
        raise ContractError("Task Envelope v3 root does not bind its Task identity")
    run_id = instance["authority_binding"]["run_id"]
    expected_task_id = sha256_bytes(
        canonical_json_bytes(
            {
                "run_id": run_id,
                "source_epoch": instance["source_epoch"],
                "task_stage": instance["task_stage"],
                "logical_task_key": instance["logical_task_key"],
            }
        )
    )[:32]
    if task_id != expected_task_id:
        raise ContractError(
            "Task Envelope v3 identity does not bind its Run, Source Epoch, and stage"
        )
    expected_group = instance.get("batch_id", run_id)
    if instance["fairness_group_id"] != expected_group:
        raise ContractError("Task Envelope v3 Fairness Group is not canonical")
    snapshots: set[str] = set()
    for snapshot in instance["protected_run_snapshot"]:
        path = snapshot["path"]
        _validate_project_relative_path(path)
        if path in snapshots or path.startswith(f"{task_root}/"):
            raise ContractError("Task Envelope v3 protected snapshot is invalid")
        snapshots.add(path)
    if "workflow/run.json" not in snapshots:
        raise ContractError("Task Envelope v3 does not protect its Run Record")
    for value in instance["allowed_read_paths"]:
        _validate_project_relative_path(value)
    for value in instance["write_set"]:
        _validate_project_relative_path(value)
    output_ids: set[str] = set()
    output_paths: set[str] = set()
    for output in instance["required_outputs"]:
        _validate_project_relative_path(output["attempt_relative_path"])
        _validate_project_relative_path(output["canonical_path"])
        if (
            output["logical_id"] in output_ids
            or output["canonical_path"] in output_paths
            or output["canonical_path"] not in instance["write_set"]
        ):
            raise ContractError("Task Envelope v3 output boundary is invalid")
        output_ids.add(output["logical_id"])
        output_paths.add(output["canonical_path"])
        prior_generation = output["expected_prior_generation"]
        prior_sha = output["expected_prior_sha256"]
        if (prior_generation is None) != (prior_sha is None):
            raise ContractError("Task Envelope v3 prior generation binding is incomplete")

    stage = instance["task_stage"]
    platform = instance["platform"]
    skeleton_path = "work/source-acquisition/decision.skeleton.json"
    if stage == "provider_acquisition":
        candidate_root = (
            f"work/source-acquisition/candidates/e{instance['source_epoch']}"
        )
        expected_inputs = {
            "bootstrap_record": "待删除/bootstrap/probe.json",
        }
        expected_reads = {"待删除/bootstrap/probe.json"}
        expected_writes = {
            "work/source-acquisition/candidate-inventory.json",
            skeleton_path,
            candidate_root,
        }
        expected_outputs = {
            "source_candidate_inventory": (
                "o/candidate-inventory.json",
                "work/source-acquisition/candidate-inventory.json",
                "source-candidate-inventory",
                "1.0.0",
            ),
            "source_acquisition_decision_skeleton": (
                "o/decision.skeleton.json",
                skeleton_path,
                "source-acquisition-decision-skeleton",
                "1.0.0",
            ),
        }
        if instance["authority_binding"]["target_checkpoint"] != "source_candidates_ready":
            raise ContractError("provider Task targets the wrong checkpoint")
        if instance["generated_prompt"] is not None:
            raise ContractError("mechanical provider Task unexpectedly has an Agent prompt")
        if instance["bounded_semantic_fields"]:
            raise ContractError("mechanical provider Task declares semantic fields")
        if instance["whisper_audio_candidate"] is not None:
            raise ContractError("provider Task unexpectedly binds a Whisper candidate")
        if instance["resource_request"] != [f"{platform}_download"]:
            raise ContractError("provider Task lacks its platform download Resource")
    elif stage == "semantic_judgment":
        expected_inputs = {
            "source_candidate_inventory": "work/source-acquisition/candidate-inventory.json",
            "source_acquisition_decision_skeleton": skeleton_path,
        }
        expected_reads = set(expected_inputs.values())
        expected_writes = {"workflow/source-acquisition-judgment-patch.json"}
        expected_outputs = {
            "source_acquisition_decision": (
                "o/p.json",
                "workflow/source-acquisition-judgment-patch.json",
                "source-acquisition-judgment-patch",
                "2.0.0",
            )
        }
        if (
            instance["authority_binding"]["target_checkpoint"]
            != "source_acquisition_decision_ready"
        ):
            raise ContractError("semantic Source Task targets the wrong checkpoint")
        prompt = instance["generated_prompt"]
        if prompt is None or prompt["path"] != f"{task_root}/prompt.md":
            raise ContractError("semantic Source Task lacks its generated prompt")
        role = prompt["role_template"]
        overlay = prompt["platform_overlay"]
        if (
            role["identity"] != "source-acquisition"
            or role["version"] != "2.0.0"
            or role["path"]
            != "prompts/video-workflow/roles/source-acquisition.v2.md"
        ):
            raise ContractError("semantic Source Task role template binding is invalid")
        if (
            overlay["identity"] != platform
            or overlay["version"] != "1.0.0"
            or overlay["path"]
            != f"prompts/video-workflow/platforms/{platform}.v1.md"
        ):
            raise ContractError("semantic Source Task platform overlay binding is invalid")
        if instance["bounded_semantic_fields"] != [
            "selected_subtitle_candidate_id",
            "subtitle_selection_rationale",
            "whisper_fallback.choice",
            "whisper_fallback.rationale",
            "known_gaps",
        ]:
            raise ContractError("semantic Source Task writable field set is invalid")
        if instance["resource_request"] != ["codex_semantic"]:
            raise ContractError("semantic Source Task has the wrong Resource request")
        if instance["whisper_audio_candidate"] is not None:
            raise ContractError("semantic Source Task unexpectedly binds a Whisper candidate")
    else:
        decision_path = "workflow/source-acquisition-judgment-patch.json"
        transcription_path = "work/source-acquisition/transcription.srt"
        expected_inputs = {
            "source_candidate_inventory": "work/source-acquisition/candidate-inventory.json",
            "source_acquisition_decision_skeleton": skeleton_path,
            "source_acquisition_decision": decision_path,
        }
        audio = instance["whisper_audio_candidate"]
        if audio is None:
            raise ContractError("Whisper Task lacks its selected audio candidate")
        audio_path = audio["staged_path"]
        _validate_project_relative_path(audio_path, prefix="work")
        expected_audio_root = (
            f"work/source-acquisition/candidates/e{instance['source_epoch']}/"
        )
        if not audio_path.startswith(expected_audio_root):
            raise ContractError("Whisper Task audio candidate is outside candidate staging")
        expected_reads = {*expected_inputs.values(), audio_path}
        expected_writes = {transcription_path}
        expected_outputs = {
            "source_transcription": (
                "o/transcription.srt",
                transcription_path,
                "source-transcription-srt",
                "1.0.0",
            )
        }
        if (
            instance["authority_binding"]["target_checkpoint"]
            != "source_acquisition_decision_ready"
        ):
            raise ContractError("Whisper Task targets the wrong checkpoint")
        if instance["generated_prompt"] is not None:
            raise ContractError("Whisper Task unexpectedly has an Agent prompt")
        if instance["bounded_semantic_fields"]:
            raise ContractError("Whisper Task declares semantic fields")
        if instance["resource_request"] != ["whisper"]:
            raise ContractError("Whisper Task lacks the Whisper Resource")

    actual_inputs = {item["logical_id"]: item["path"] for item in instance["input_artifacts"]}
    if len(actual_inputs) != len(instance["input_artifacts"]) or actual_inputs != expected_inputs:
        raise ContractError("Task Envelope v3 input bindings are invalid for its stage")
    if set(instance["allowed_read_paths"]) != expected_reads:
        raise ContractError("Task Envelope v3 read boundary is invalid for its stage")
    if set(instance["write_set"]) != expected_writes:
        raise ContractError("Task Envelope v3 write boundary is invalid for its stage")
    actual_outputs = {
        item["logical_id"]: (
            item["attempt_relative_path"],
            item["canonical_path"],
            item["schema_name"],
            item["schema_version"],
        )
        for item in instance["required_outputs"]
    }
    if actual_outputs != expected_outputs:
        raise ContractError("Task Envelope v3 outputs are invalid for its stage")


def _validate_resource_lease_resolution_evidence(instance: dict[str, Any]) -> None:
    evidence_class = instance["evidence_class"]
    evidence = instance["evidence"]
    expected_keys = {
        "local_process_terminated": {
            "pid",
            "process_creation_identity",
            "launch_token",
            "inspection_proof_reference",
        },
        "provider_terminal_result": {
            "provider",
            "terminal_result_id",
            "verification_proof_reference",
        },
        "explicit_human_resolution": {
            "reason",
            "observed_termination_basis",
            "coordinator_identity",
        },
    }
    allowed_outcomes = {
        "local_process_terminated": {"terminated"},
        "provider_terminal_result": {"succeeded", "failed", "cancelled"},
        "explicit_human_resolution": {"terminated"},
    }
    if set(evidence) != expected_keys[evidence_class]:
        raise ContractError(
            "Resource Lease resolution evidence shape disagrees with its class"
        )
    if instance["declared_outcome"] not in allowed_outcomes[evidence_class]:
        raise ContractError(
            "Resource Lease resolution outcome disagrees with its evidence class"
        )


def _validate_source_credential_resolution_evidence(
    instance: dict[str, Any],
) -> None:
    platform = instance["canonical_platform"]
    expected_resource_class = f"{platform}_download"
    expected_breaker_key = f"platform:{platform}:{expected_resource_class}"
    if (
        instance["resource_class"] != expected_resource_class
        or instance["breaker_key"] != expected_breaker_key
    ):
        raise ContractError(
            "Source credential resolution evidence has inconsistent breaker scope"
        )
    expected_redacted_argv = [
        "<python-executable>",
        "-m",
        "yt_dlp",
        "--cookies",
        "<localized-cookie-file>",
        "--simulate",
        "<canonical-source-url>",
    ]
    if instance["probe_command_argv_redacted"] != expected_redacted_argv:
        raise ContractError(
            "Source credential resolution evidence must use the closed secret-free "
            "provider probe argv"
        )


def _validate_task_attempt(instance: dict[str, Any]) -> None:
    expected = (
        f"workflow/tasks/{instance['task_id']}/attempts/{instance['attempt_id']}"
    )
    _validate_project_relative_path(instance["attempt_path"], prefix="workflow")
    if instance["attempt_path"] != expected:
        raise ContractError("Task Attempt path does not bind task and attempt identities")


def _validate_task_completion(instance: dict[str, Any]) -> None:
    logical_ids: set[str] = set()
    canonical_paths: set[str] = set()
    for output in instance["outputs"]:
        _validate_project_relative_path(output["attempt_path"])
        _validate_project_relative_path(output["canonical_path"])
        if PurePosixPath(output["canonical_path"]).parts[0] not in {
            "work",
            "workflow",
        }:
            raise ContractError(
                "Task Completion canonical output lacks a Task-owned prefix"
            )
        if output["logical_id"] in logical_ids or output["canonical_path"] in canonical_paths:
            raise ContractError("Task Completion outputs are not unique")
        logical_ids.add(output["logical_id"])
        canonical_paths.add(output["canonical_path"])


def _validate_task_promotion_journal(instance: dict[str, Any]) -> None:
    prefix = (
        f"待删除/task-promotions/{instance['task_id']}/"
        f"g{instance['claim_generation']:08d}"
    )
    canonical_paths: set[str] = set()
    for output in instance["outputs"]:
        _validate_project_relative_path(output["attempt_path"])
        _validate_project_relative_path(output["canonical_path"])
        if PurePosixPath(output["canonical_path"]).parts[0] not in {
            "work",
            "workflow",
        }:
            raise ContractError(
                "promotion canonical output lacks a Task-owned prefix"
            )
        _validate_project_relative_path(output["preservation_path"], prefix="待删除")
        if not output["preservation_path"].startswith(f"{prefix}/previous/"):
            raise ContractError("promotion preservation path is outside its intent boundary")
        if output["canonical_path"] in canonical_paths:
            raise ContractError("promotion journal repeats a canonical output path")
        canonical_paths.add(output["canonical_path"])


def _validate_scaffold_contract(instance: dict[str, Any]) -> None:
    for value in instance["managed_directories"]:
        _validate_project_relative_path(value)
    for value in instance["reserved_descendant_paths"]:
        _validate_project_relative_path(value)


INVARIANT_VALIDATORS = {
    "artifact-plan-paths-v1": _validate_artifact_plan,
    "artifact-plan-paths-v2": _validate_artifact_plan_v2,
    "bootstrap-source-identity-v2": _validate_bootstrap_record_v2,
    "control-store-identity-path-v1": lambda value: _validate_canonical_absolute_path(
        value["workspace_path"]
    ),
    "fixture-package-paths-v1": _validate_fixture_package,
    "run-record-freshness-v1": _validate_run_record,
    "run-record-freshness-v2": _validate_task_capable_run_record,
    "run-record-freshness-v3": _validate_run_record_v3,
    "resource-admission-configuration-v1": _validate_resource_admission_configuration,
    "resource-lease-resolution-evidence-v1": _validate_resource_lease_resolution_evidence,
    "scaffold-contract-directories-v1": _validate_scaffold_contract,
    "source-manifest-paths-and-fingerprints-v1": _validate_source_manifest,
    "source-candidate-inventory-v1": _validate_candidate_inventory,
    "source-decision-skeleton-v1": _validate_decision_skeleton,
    "source-judgment-patch-v2": _validate_judgment_patch_v2,
    "source-manifest-paths-and-fingerprints-v2": _validate_source_manifest_v2,
    "source-publication-journal-paths-v1": _validate_source_publication_journal,
    "source-reopen-journal-bindings-v1": _validate_source_reopen_journal,
    "source-credential-resolution-evidence-v1": (
        _validate_source_credential_resolution_evidence
    ),
    "task-attempt-path-v1": _validate_task_attempt,
    "task-completion-bindings-v1": _validate_task_completion,
    "task-envelope-bindings-v1": _validate_task_envelope,
    "task-envelope-bindings-v2": _validate_task_envelope_v2,
    "task-envelope-bindings-v3": _validate_task_envelope_v3,
    "task-promotion-journal-paths-v1": _validate_task_promotion_journal,
}


def _walk_refs(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        reference = value.get("$ref")
        if isinstance(reference, str):
            yield reference
        for child in value.values():
            yield from _walk_refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_refs(child)


class ContractRegistry:
    """Closed JSON Schema registry; structural field authority stays in Schema."""

    def __init__(self, project_root: Path, registry_path: Path | None = None) -> None:
        self.project_root = project_root.resolve()
        self.registry_path = (registry_path or self.project_root / REGISTRY_RELATIVE_PATH).resolve()
        self._canonical = read_json(self.project_root / REGISTRY_RELATIVE_PATH)
        self._manifest = read_json(self.registry_path)
        if self._manifest != self._canonical:
            raise ContractError(
                "alternate registry authority metadata differs from the canonical registry"
            )
        self.entries = self._load_entries()
        self.schemas: dict[tuple[str, str], dict[str, Any]] = {}
        self._registry: Registry | None = None

    def _load_entries(self) -> tuple[ContractEntry, ...]:
        if not isinstance(self._manifest, dict):
            raise ContractError("Kernel Schema Registry root must be an object")
        if self._manifest.get("schema_name") != "kernel-schema-registry":
            raise ContractError("Kernel Schema Registry identity is invalid")
        if self._manifest.get("schema_version") != "1.0.0":
            raise UnknownContractVersion("unknown Kernel Schema Registry version")
        contracts = self._manifest.get("contracts")
        if not isinstance(contracts, list) or not contracts:
            raise ContractError("Kernel Schema Registry contracts must be a non-empty array")
        canonical_entries = {
            (item["schema_name"], item["schema_version"]): item
            for item in self._canonical["contracts"]
        }
        if len(canonical_entries) != len(self._canonical["contracts"]):
            raise ContractError("canonical registry repeats a contract name/version")
        known_versions = set(canonical_entries)
        entries: list[ContractEntry] = []
        seen_versions: set[tuple[str, str]] = set()
        seen_ids: set[str] = set()
        for raw in contracts:
            if not isinstance(raw, dict):
                raise ContractError("registry contract entry must be an object")
            name = raw.get("schema_name")
            version = raw.get("schema_version")
            identity = (name, version)
            if identity not in known_versions:
                raise UnknownContractVersion(
                    f"unknown registered contract version: {name!r} {version!r}"
                )
            schema_id = raw.get("schema_id")
            if identity in seen_versions or schema_id in seen_ids:
                raise ContractError(
                    "registry contract name/version and schema identities must be unique"
                )
            seen_versions.add(identity)
            seen_ids.add(schema_id)
            raw_path = Path(str(raw.get("schema_path", "")))
            schema_path = raw_path if raw_path.is_absolute() else self.project_root / raw_path
            positive = raw.get("positive_example")
            negative = raw.get("negative_example")
            invariants = raw.get("invariants", [])
            canonical_instance = raw.get("canonical_instance")
            expected_canonical_instance = canonical_entries[identity].get(
                "canonical_instance"
            )
            if canonical_instance != expected_canonical_instance:
                raise ContractError(
                    f"registry canonical instance changed for {name!r}"
                )
            if (
                not isinstance(invariants, list)
                or any(not isinstance(value, str) for value in invariants)
                or len(set(invariants)) != len(invariants)
            ):
                raise ContractError(f"registry invariants are invalid for {name!r}")
            unknown_invariants = sorted(set(invariants) - KNOWN_INVARIANTS)
            if unknown_invariants:
                raise ContractError(
                    f"registry declares unknown contract invariants: {unknown_invariants}"
                )
            entries.append(
                ContractEntry(
                    schema_name=name,
                    schema_version=version,
                    schema_id=schema_id,
                    schema_path=schema_path.resolve(),
                    kind=str(raw.get("kind")),
                    positive_example=(self.project_root / positive).resolve() if positive else None,
                    negative_example=(self.project_root / negative).resolve() if negative else None,
                    invariants=tuple(invariants),
                    canonical_instance=(
                        (self.project_root / canonical_instance).resolve()
                        if canonical_instance
                        else None
                    ),
                )
            )
        if seen_versions != known_versions:
            raise ContractError(
                "registry must contain the exact canonical contract version set: "
                f"missing={sorted(known_versions - seen_versions)}, "
                f"extra={sorted(seen_versions - known_versions)}"
            )
        return tuple(entries)

    def check(self) -> dict[str, Any]:
        runtime = self._prepare_registry()
        installed = runtime["jsonschema_version"]

        positive_count = 0
        negative_count = 0
        for entry in self.entries:
            if entry.kind != "contract":
                continue
            if entry.positive_example is None or entry.negative_example is None:
                raise ContractError(f"contract examples missing for {entry.schema_name}")
            self.validate(entry.schema_name, read_json(entry.positive_example))
            positive_count += 1
            try:
                self.validate(entry.schema_name, read_json(entry.negative_example))
            except ContractError:
                negative_count += 1
            else:
                raise ContractError(
                    f"negative contract example unexpectedly passed: {entry.negative_example}"
                )
        return {
            "jsonschema_version": installed,
            "json_schema_draft": DRAFT,
            "contract_count": positive_count,
            "supporting_schema_count": sum(e.kind == "supporting_schema" for e in self.entries),
            "positive_examples_validated": positive_count,
            "negative_examples_rejected": negative_count,
            "registry_path": str(self.registry_path),
            "registry_complete": True,
            "registered_schema_names": sorted(
                {entry.schema_name for entry in self.entries}
            ),
            "registered_contract_versions": sorted(
                f"{entry.schema_name}@{entry.schema_version}" for entry in self.entries
            ),
            "runtime_lock": runtime,
        }

    def _check_locked_runtime(self) -> dict[str, Any]:
        input_path = self.project_root / RUNTIME_INPUT
        lock_path = self.project_root / RUNTIME_LOCK
        direct_lines = {
            line.strip()
            for line in input_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        if direct_lines != {f"jsonschema=={JSONSCHEMA_VERSION}"}:
            raise ContractError("runtime input must contain only the exact jsonschema pin")
        with lock_path.open("rb") as handle:
            lock = tomllib.load(handle)
        if lock.get("lock-version") != "1.0" or lock.get("created-by") != "uv":
            raise ContractError("runtime lock is not the expected uv PEP 751 lock")
        packages = lock.get("packages")
        if not isinstance(packages, list) or not packages:
            raise ContractError("runtime lock contains no packages")
        locked: dict[str, str] = {}
        for package in packages:
            name = package.get("name")
            version = package.get("version")
            if not isinstance(name, str) or not isinstance(version, str) or name in locked:
                raise ContractError("runtime lock package identities must be unique strings")
            artifacts = []
            if isinstance(package.get("sdist"), dict):
                artifacts.append(package["sdist"])
            if isinstance(package.get("wheels"), list):
                artifacts.extend(package["wheels"])
            if not artifacts or any(
                not isinstance(artifact.get("hashes"), dict)
                or not isinstance(artifact["hashes"].get("sha256"), str)
                or len(artifact["hashes"]["sha256"]) != 64
                for artifact in artifacts
            ):
                raise ContractError(f"runtime lock package lacks full SHA-256 hashes: {name}")
            locked[name] = version
        if locked.get("jsonschema") != JSONSCHEMA_VERSION:
            raise ContractError("runtime lock jsonschema version differs from the direct pin")
        installed: dict[str, str] = {}
        for name, expected in locked.items():
            try:
                actual = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError as exc:
                raise ContractError(f"locked runtime package is unavailable: {name}") from exc
            if actual != expected:
                raise ContractError(
                    f"locked runtime package mismatch: {name}: expected {expected}, got {actual}"
                )
            installed[name] = actual
        return {
            "jsonschema_version": installed["jsonschema"],
            "locked_packages": installed,
            "lock_path": str(lock_path),
            "lock_sha256": sha256_file(lock_path),
        }

    def validate(self, schema_name: str, instance: Any) -> None:
        if self._registry is None:
            self._prepare_registry()
        if not isinstance(instance, dict):
            raise ContractError(f"{schema_name} contract root must be an object")
        actual_version = instance.get("schema_version")
        entry = next(
            (
                item
                for item in self.entries
                if item.schema_name == schema_name
                and item.schema_version == actual_version
            ),
            None,
        )
        if entry is None:
            versions = {
                item.schema_version
                for item in self.entries
                if item.schema_name == schema_name
            }
            if not versions:
                raise UnknownContractVersion(f"unregistered contract: {schema_name}")
            raise UnknownContractVersion(
                f"unknown {schema_name} schema_version: {actual_version!r}"
            )
        validator = Draft202012Validator(
            self.schemas[(schema_name, entry.schema_version)],
            registry=self._registry,
            format_checker=FormatChecker(),
        )
        try:
            validator.validate(instance)
        except ValidationError as exc:
            path = "/".join(str(part) for part in exc.absolute_path) or "$"
            raise ContractError(f"{schema_name} instance invalid at {path}: {exc.message}") from exc
        for invariant in entry.invariants:
            INVARIANT_VALIDATORS[invariant](instance)
            if invariant == "scaffold-contract-directories-v1":
                if entry.canonical_instance is None:
                    raise ContractError(
                        "scaffold contract lacks its registered canonical instance"
                    )
                canonical = read_json(entry.canonical_instance)
                if instance != canonical:
                    raise ContractError(
                        "scaffold contract differs from its registered canonical instance"
                    )

    def validate_run_record(self, instance: Any) -> None:
        """Validate either registered Run Record generation without guessing fields."""
        if not isinstance(instance, dict):
            raise ContractError("Run Record root must be an object")
        self.validate("run-record", instance)

    def canonical_instance(
        self, schema_name: str, schema_version: str | None = None
    ) -> Any:
        candidates = [
            item
            for item in self.entries
            if item.schema_name == schema_name
            and (schema_version is None or item.schema_version == schema_version)
            and item.canonical_instance is not None
        ]
        entry = candidates[0] if len(candidates) == 1 else None
        if entry is None or entry.canonical_instance is None:
            raise ContractError(f"contract has no canonical instance: {schema_name}")
        value = read_json(entry.canonical_instance)
        self.validate(schema_name, value)
        return value

    def _prepare_registry(self) -> dict[str, Any]:
        """Prepare every registry entry through the same closed, locked path."""
        if self._registry is not None:
            return self._check_locked_runtime()
        runtime = self._check_locked_runtime()
        registered_ids = {entry.schema_id for entry in self.entries}
        resources: list[tuple[str, Resource]] = []
        for entry in self.entries:
            try:
                schema = read_json(entry.schema_path)
            except (FileNotFoundError, json.JSONDecodeError) as exc:
                raise ContractError(f"cannot load schema: {entry.schema_path}: {exc}") from exc
            if not isinstance(schema, dict):
                raise ContractError(f"schema root must be an object: {entry.schema_path}")
            if schema.get("$schema") != DRAFT:
                raise ContractError(f"schema draft mismatch: {entry.schema_path}")
            if schema.get("$id") != entry.schema_id:
                raise ContractError(f"schema identity mismatch: {entry.schema_path}")
            try:
                Draft202012Validator.check_schema(schema)
            except SchemaError as exc:
                raise ContractError(
                    f"invalid Draft 2020-12 schema {entry.schema_id}: {exc.message}"
                ) from exc
            for reference in _walk_refs(schema):
                target = reference.split("#", 1)[0]
                if target and target not in registered_ids:
                    raise UnresolvedSchemaReference(
                        f"unregistered schema reference {reference!r} in {entry.schema_id}"
                    )
            self.schemas[(entry.schema_name, entry.schema_version)] = schema
            resources.append((entry.schema_id, Resource.from_contents(schema)))
        self._registry = Registry().with_resources(resources)
        registered_paths = {entry.schema_path for entry in self.entries}
        disk_paths = {
            path.resolve()
            for path in (self.project_root / "schemas/video-workflow").glob(
                "v*/*.schema.json"
            )
        }
        if registered_paths != disk_paths:
            missing = sorted(str(path) for path in disk_paths - registered_paths)
            extra = sorted(str(path) for path in registered_paths - disk_paths)
            raise ContractError(
                f"registry completeness mismatch: missing={missing}, extra={extra}"
            )
        return runtime
