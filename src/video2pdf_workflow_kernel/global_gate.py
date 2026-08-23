from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
from typing import Any

from jsonschema import Draft202012Validator

from .delivery_quality import DeliveryQualityRegistry
from .evidence import EvidenceSupportError, git_output, sha256_git_blob
from .errors import AcceptanceV2Rejected, ContractError, ControlStoreUnavailable, GlobalGateFault
from .global_gate_exit_evidence import (
    ExitEvidenceValidationError,
    validate_global_gate_exit_evidence,
)
from .utils import canonical_json_bytes, read_json, sha256_file, write_json_atomic


GLOBAL_GATE_DB = "global-gate-control.sqlite3"
GLOBAL_GATE_SCHEMA_VERSION = 1
ATOMIC_MEMBERS = frozenset({
    "catalogs", "projections", "criteria_migration", "schemas", "providers",
    "validators", "hooks", "skills", "project_instructions", "mirrors", "tests",
    "activation_documentation",
})
EXIT_EVIDENCE_SCHEMA = Path(__file__).resolve().parents[2] / "schemas/exit-evidence-manifest.v2.schema.json"
GLOBAL_GATE_SLICE = {"number": 11, "name": "global-acceptance-v2-gate"}
QUALIFICATION_CONTRACT_SHA256 = "0e24ee82c2ff68124523546e5891c39227fe4268dbc581b74a319cdde22ef411"
ACTIVATION_FAULT_POINTS = frozenset({"after_intent", "after_authority_write", "after_control_commit"})
REQUIRED_ACCEPTANCE_QUALITY_INPUTS = frozenset({
    "precompile_quality_report", "precompile_text_seal", "rendered_text_reconciliation",
    "final_artifact_seal", "final_compile_manifest", "render_evidence_manifest",
    "rendered_text_object_inventory", "text_origin_manifest",
})
OPTIONAL_ACCEPTANCE_QUALITY_INPUTS = frozenset({"text_equivalence_report"})


def _fingerprint(value: dict[str, Any], field: str) -> str:
    return hashlib.sha256(canonical_json_bytes({key: item for key, item in value.items() if key != field})).hexdigest()


def _reject(message: str, gate: str, code: str, **data: Any) -> None:
    raise AcceptanceV2Rejected(message, data={"first_failing_gate": gate, "error_code": code, **data})


def _control_reject(message: str, code: str) -> None:
    raise ControlStoreUnavailable(
        message,
        data={"first_failing_gate": "control_store", "error_code": code},
    )


def _validate_policy_evidence(evidence: Any) -> dict[str, Any]:
    if not isinstance(evidence, dict):
        _reject("Global Gate Exit Evidence schema is invalid", "exit_evidence_schema", "global_gate_exit_evidence_invalid")
    schema = read_json(EXIT_EVIDENCE_SCHEMA)
    errors = sorted(Draft202012Validator(schema).iter_errors(evidence), key=lambda item: list(item.absolute_path))
    if errors:
        _reject(
            "Global Gate Exit Evidence schema is invalid",
            "exit_evidence_schema",
            "global_gate_exit_evidence_invalid",
            schema_path="/".join(str(item) for item in errors[0].absolute_path),
        )
    if evidence.get("slice") != GLOBAL_GATE_SLICE or evidence.get("overall_decision") != "pass":
        _reject("Global Gate Exit Evidence is not a passing cutover manifest", "exit_evidence_schema", "global_gate_exit_evidence_invalid")
    scope = evidence.get("activation_scope")
    if scope != {
        "kind": "active_global_gate",
        "runtime_authority_change": True,
        "components_activated": ["acceptance_report_v2", "delivery_quality_context"],
        "legacy_track_authority": "acceptance_report_v2",
        "platform_kernel_authority": "unchanged",
        "qualification_contract_sha256": QUALIFICATION_CONTRACT_SHA256,
    }:
        _reject("Global Gate activation scope is invalid", "activation_scope", "global_gate_activation_scope_invalid")
    if set(evidence.get("atomic_members", [])) != ATOMIC_MEMBERS:
        _reject("Global Gate atomic publication group is incomplete", "atomic_group", "global_gate_atomic_group_incomplete")
    statuses = evidence.get("atomic_member_status")
    if not isinstance(statuses, dict) or set(statuses) != ATOMIC_MEMBERS or any(statuses[member] != "active" for member in sorted(ATOMIC_MEMBERS)):
        failed = next((member for member in sorted(ATOMIC_MEMBERS) if not isinstance(statuses, dict) or statuses.get(member) != "active"), None)
        _reject("Global Gate atomic member is not active", "atomic_member_status", "global_gate_atomic_member_failed", member=failed)
    mirror_checks = evidence.get("mirror_checks")
    if not isinstance(mirror_checks, list) or not mirror_checks:
        _reject("Global Gate managed mirror evidence is incomplete", "mirror_checks", "global_gate_mirror_stale")
    for check in mirror_checks:
        if not isinstance(check, dict) or set(check) != {"source_path", "mirror_path", "source_sha256", "mirror_sha256", "status"}:
            _reject("Global Gate managed mirror evidence is invalid", "mirror_checks", "global_gate_mirror_stale")
        source = Path(check["source_path"])
        mirror = Path(check["mirror_path"])
        if (
            check["status"] != "equal"
            or check["source_sha256"] != check["mirror_sha256"]
            or not source.is_file()
            or not mirror.is_file()
            or sha256_file(source) != check["source_sha256"]
            or sha256_file(mirror) != check["mirror_sha256"]
        ):
            _reject("Global Gate managed mirrors are stale", "mirror_checks", "global_gate_mirror_stale", source_path=str(source), mirror_path=str(mirror))
    if evidence.get("policy_status") != "active_global_gate":
        _reject("Global Gate policy status is inactive", "policy_status", "global_gate_policy_inactive")
    commands = evidence.get("commands", [])
    if any(
        command.get("expected_exit_code") != 0
        or command.get("actual_exit_code") != 0
        or command.get("conforms") is not True
        for command in commands
    ):
        _reject("Global Gate qualification command failed", "atomic_group", "global_gate_atomic_member_failed")
    if any(item.get("blocking") for item in evidence.get("unresolved_exceptions", [])):
        _reject("Global Gate qualification has an unresolved exception", "contract_gap", "global_gate_contract_gap_not_rejected")
    result_pairs = {
        (result_id, result_kind)
        for result_kind, result_ids in evidence.get("results", {}).items()
        for result_id in result_ids
    }
    bindings = evidence.get("result_bindings", [])
    if hashlib.sha256(canonical_json_bytes(bindings)).hexdigest() != QUALIFICATION_CONTRACT_SHA256:
        _reject("Global Gate qualification contract is stale", "qualification_result_binding", "global_gate_qualification_contract_stale")
    binding_pairs = {(item.get("result_id"), item.get("result_kind")) for item in bindings}
    if len(bindings) != len(binding_pairs) or binding_pairs != result_pairs:
        _reject("Global Gate result bindings are incomplete", "qualification_result_coverage", "global_gate_results_incomplete")
    command_by_id = {item.get("test_id"): item for item in commands}
    for binding in bindings:
        command = command_by_id.get(binding.get("command_id"))
        if command is None or binding.get("test_target") not in command.get("command", []):
            _reject("Global Gate result lacks a public tracer", "qualification_result_binding", "global_gate_result_tracer_invalid", result=binding.get("result_id"))
    return evidence


def _contained_file(root: Path, candidate: Path, *, gate: str, code: str) -> Path:
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        _reject("Legacy Acceptance input path escapes the video output directory", gate, code, path=str(resolved))
    if not resolved.is_file():
        _reject("Legacy Acceptance input is missing", gate, code, path=str(resolved))
    return resolved


class LegacyAcceptanceProvider:
    """Adopts current Legacy final evidence without inventing workflow authority."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()

    def adopt(
        self, *, video_output_dir: Path, final_pdf: Path, main_tex: Path,
        allowed_artifacts_manifest: Path, compile_report: Path, criteria: Path,
        dimension_map: Path, rendered_pages_manifest: Path, quality_inputs_manifest: Path,
        control_store_root: Path, adopted_at: str,
        output: Path | None = None,
    ) -> dict[str, Any]:
        root = video_output_dir.resolve()
        if not root.is_dir():
            _reject("Legacy video output directory is unavailable", "video_output_authority", "legacy_video_output_unavailable")
        local = {
            "final_pdf": _contained_file(root, final_pdf, gate="path_boundary", code="legacy_input_path_escape"),
            "main_tex": _contained_file(root, main_tex, gate="path_boundary", code="legacy_input_path_escape"),
            "allowed_manifest": _contained_file(root, allowed_artifacts_manifest, gate="path_boundary", code="legacy_input_path_escape"),
            "compile_report": _contained_file(root, compile_report, gate="path_boundary", code="legacy_input_path_escape"),
            "dimension_map": _contained_file(root, dimension_map, gate="path_boundary", code="legacy_input_path_escape"),
            "pages_manifest": _contained_file(root, rendered_pages_manifest, gate="path_boundary", code="legacy_input_path_escape"),
            "quality_manifest": _contained_file(root, quality_inputs_manifest, gate="path_boundary", code="legacy_input_path_escape"),
        }
        criteria_path = criteria.resolve()
        if not criteria_path.is_file() or not criteria_path.is_relative_to(self.project_root):
            _reject("Acceptance criteria authority is unavailable", "policy_binding", "legacy_criteria_unavailable")

        quality_registry = DeliveryQualityRegistry(self.project_root)
        compile_value = read_json(local["compile_report"])
        modern_compile = (
            isinstance(compile_value, dict)
            and compile_value.get("schema_name") == "final-compile-report"
        )
        if modern_compile:
            try:
                quality_registry.validate("final-compile-report", compile_value)
            except ContractError:
                _reject("Legacy final compile provenance is absent, stale, or unsupported", "compile_provenance", "legacy_compile_provenance_invalid")
        elif not isinstance(compile_value, dict) or (
            compile_value.get("schema_version") != "latex_compile_report.v1"
            or compile_value.get("mode") != "final"
            or compile_value.get("status") != "passed"
            or compile_value.get("producer") != "compile_latex_ascii.py"
            or compile_value.get("producer_contract") != "latex_compile_guard.v1"
            or Path(compile_value.get("final_pdf", "")).resolve() != local["final_pdf"]
            or Path(compile_value.get("main_tex", compile_value.get("source_tex", ""))).resolve() != local["main_tex"]
            or compile_value.get("final_pdf_fingerprint", {}).get("sha256") != sha256_file(local["final_pdf"])
            or compile_value.get("source_tex_fingerprint", {}).get("sha256") != sha256_file(local["main_tex"])
        ):
            _reject("Legacy final compile provenance is absent, stale, or unsupported", "compile_provenance", "legacy_compile_provenance_invalid")

        allowed = read_json(local["allowed_manifest"])
        declared = {
            (item.get("role"), item.get("path"), item.get("sha256"))
            for item in allowed.get("final_artifacts", []) if isinstance(item, dict)
        }
        expected = {
            ("pdf", local["final_pdf"].relative_to(root).as_posix(), sha256_file(local["final_pdf"])),
            ("tex", local["main_tex"].relative_to(root).as_posix(), sha256_file(local["main_tex"])),
        }
        if not expected <= declared:
            _reject("Allowed-artifact manifest does not bind current final artifacts", "allowed_manifest", "legacy_allowed_manifest_stale")

        rendered = read_json(local["pages_manifest"])
        pages = rendered.get("pages", []) if isinstance(rendered, dict) else []
        if rendered.get("final_pdf_sha256") != sha256_file(local["final_pdf"]) or rendered.get("page_count") != len(pages) or [p.get("page") for p in pages] != list(range(1, len(pages) + 1)) or not pages:
            _reject("Rendered-page manifest lacks complete current coverage", "rendered_page_coverage", "legacy_rendered_page_coverage_invalid")
        normalized_pages = []
        for page in pages:
            path = _contained_file(root, Path(page.get("path", "")), gate="rendered_page_freshness", code="legacy_rendered_page_stale")
            if sha256_file(path) != page.get("sha256"):
                _reject("Rendered page is stale", "rendered_page_freshness", "legacy_rendered_page_stale", page=page.get("page"))
            normalized_pages.append({"page": page["page"], "path": str(path), "sha256": page["sha256"]})

        quality_manifest_value = read_json(local["quality_manifest"])
        quality_inputs = quality_manifest_value.get("quality_inputs") if isinstance(quality_manifest_value, dict) else None
        if (
            not isinstance(quality_manifest_value, dict)
            or quality_manifest_value.get("schema_name") != "legacy-quality-inputs-manifest"
            or quality_manifest_value.get("schema_version") != "1.0.0"
            or not isinstance(quality_inputs, dict)
            or frozenset(quality_inputs) not in {
                REQUIRED_ACCEPTANCE_QUALITY_INPUTS,
                REQUIRED_ACCEPTANCE_QUALITY_INPUTS | OPTIONAL_ACCEPTANCE_QUALITY_INPUTS,
            }
        ):
            _reject("Legacy quality input membership is incomplete", "quality_input_membership", "legacy_quality_input_incomplete")
        normalized_quality_inputs: dict[str, dict[str, str]] = {}
        quality_schemas = {
            "precompile_quality_report": "precompile-quality-report",
            "precompile_text_seal": "precompile-text-seal",
            "rendered_text_reconciliation": "rendered-text-reconciliation-report",
            "final_artifact_seal": "final-artifact-seal",
            "final_compile_manifest": "final-compile-manifest",
            "render_evidence_manifest": "render-evidence-manifest",
            "rendered_text_object_inventory": "rendered-text-object-inventory",
            "text_origin_manifest": "text-origin-manifest",
            "text_equivalence_report": "text-equivalence-report",
        }
        quality_values: dict[str, dict[str, Any]] = {}
        for logical_id in sorted(quality_inputs):
            item = quality_inputs[logical_id]
            if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
                _reject("Legacy quality input binding is unsupported", "quality_input_contract", "legacy_quality_input_contract_invalid", logical_id=logical_id)
            path = _contained_file(root, Path(item.get("path", "")), gate="quality_input_freshness", code="legacy_quality_input_stale")
            if sha256_file(path) != item.get("sha256"):
                _reject("Legacy quality input fingerprint is stale", "quality_input_freshness", "legacy_quality_input_stale", logical_id=logical_id)
            quality_value = read_json(path)
            try:
                quality_registry.validate(quality_schemas[logical_id], quality_value)
            except (ContractError, KeyError):
                _reject("Legacy quality input contract is unsupported", "quality_input_contract", "legacy_quality_input_contract_invalid", logical_id=logical_id)
            normalized_quality_inputs[logical_id] = {"path": str(path), "sha256": item["sha256"]}
            quality_values[logical_id] = quality_value

        if modern_compile:
            pdf = compile_value["pdf"]
            report_pdf = (local["compile_report"].parent / pdf["path"]).resolve()
            report_root = local["compile_report"].parent.resolve()
            compile_manifest = quality_values["final_compile_manifest"]
            final_seal = quality_values["final_artifact_seal"]
            render_evidence = quality_values["render_evidence_manifest"]
            rendered_inventory = quality_values["rendered_text_object_inventory"]
            text_origin = quality_values["text_origin_manifest"]
            main_entries = [
                entry for entry in compile_manifest["entries"]
                if Path(entry["staging_path"]).name.casefold() == "main.tex"
            ]
            manifest_inputs = {
                (entry["logical_id"], entry["generation"], entry["sha256"])
                for entry in compile_manifest["entries"]
            }
            report_inputs = {
                (entry["logical_id"], entry["generation"], entry["sha256"])
                for entry in compile_value["dependency_closure"]["inputs"]
            }
            dependency_closure = compile_value["dependency_closure"]
            registered_adapter = (
                self.project_root / "scripts/guarded_final_compile_adapter.py"
            ).resolve()
            adapter_identity = compile_value["compile_adapter"]
            try:
                head = git_output(self.project_root, "rev-parse", "HEAD")
                adapter_head_sha256 = sha256_git_blob(
                    self.project_root,
                    head,
                    "scripts/guarded_final_compile_adapter.py",
                )
            except EvidenceSupportError:
                adapter_head_sha256 = None
            adapter_is_current = (
                Path(adapter_identity["adapter_path"]).resolve() == registered_adapter
                and registered_adapter.is_file()
                and adapter_head_sha256 is not None
                and sha256_file(registered_adapter) == adapter_head_sha256
                and adapter_identity["adapter_sha256"] == adapter_head_sha256
                and adapter_identity["protocol_version"]
                == "guarded-final-compile-v1"
            )
            recorder_path_value = dependency_closure["recorder_path"]
            recorder_is_current = False
            if "\\" not in recorder_path_value and not Path(recorder_path_value).is_absolute():
                recorder_path = (report_root / recorder_path_value).resolve()
                recorder_is_current = (
                    recorder_path.is_relative_to(report_root)
                    and recorder_path.is_file()
                    and sha256_file(recorder_path)
                    == dependency_closure["recorder_sha256"]
                )
            approved_runtime_inputs = compile_manifest["approved_runtime_inputs"]
            reported_runtime_inputs = dependency_closure["runtime_inputs"]
            runtime_inputs_are_current = (
                reported_runtime_inputs == approved_runtime_inputs
                and all(
                    Path(item["path"]).is_absolute()
                    and Path(item["path"]).resolve().is_file()
                    and sha256_file(Path(item["path"]).resolve()) == item["sha256"]
                    for item in reported_runtime_inputs
                )
            )
            generated_inputs_are_current = True
            for item in dependency_closure["generated_inputs"]:
                generated_path = Path(item["path"]).resolve()
                if (
                    not Path(item["path"]).is_absolute()
                    or not generated_path.is_relative_to(report_root)
                    or not generated_path.is_file()
                    or sha256_file(generated_path) != item["sha256"]
                ):
                    generated_inputs_are_current = False
                    break
            report_fingerprint_is_stale = (
                compile_value["report_sha256"] != _fingerprint(compile_value, "report_sha256")
            )
            pdf_is_current = (
                report_pdf == local["final_pdf"]
                and pdf["sha256"] == sha256_file(local["final_pdf"])
                and pdf["size"] == local["final_pdf"].stat().st_size
            )
            main_tex_is_current = (
                len(main_entries) == 1
                and Path(main_entries[0]["source_path"]).resolve() == local["main_tex"]
                and main_entries[0]["sha256"] == sha256_file(local["main_tex"])
            )
            compile_manifest_is_current = (
                manifest_inputs == report_inputs
                and compile_manifest["manifest_sha256"] == _fingerprint(compile_manifest, "manifest_sha256")
                and compile_value["compile_manifest_sha256"] == compile_manifest["manifest_sha256"]
                and compile_value["precompile_text_seal_sha256"] == compile_manifest["precompile_text_seal_sha256"]
            )
            final_seal_is_current = (
                final_seal["seal_sha256"] == _fingerprint(final_seal, "seal_sha256")
                and compile_value["final_artifact_seal_sha256"] == final_seal["seal_sha256"]
                and final_seal["precompile_text_seal_sha256"] == compile_value["precompile_text_seal_sha256"]
                and final_seal["compile_manifest_sha256"] == compile_manifest["manifest_sha256"]
                and final_seal["final_pdf"] == pdf
                and final_seal["compile_provider"] == compile_value["compiler_provider"]
            )
            render_evidence_is_current = (
                render_evidence["manifest_sha256"] == _fingerprint(render_evidence, "manifest_sha256")
                and compile_value["render_evidence_manifest_sha256"] == render_evidence["manifest_sha256"]
                and render_evidence["final_pdf_sha256"] == pdf["sha256"]
            )
            rendered_inventory_is_current = (
                rendered_inventory["inventory_sha256"] == _fingerprint(rendered_inventory, "inventory_sha256")
                and compile_value["rendered_text_inventory_sha256"] == rendered_inventory["inventory_sha256"]
                and rendered_inventory["final_pdf_sha256"] == pdf["sha256"]
            )
            text_origin_is_current = (
                text_origin["manifest_sha256"] == _fingerprint(text_origin, "manifest_sha256")
                and compile_value["text_origin_manifest_sha256"] == text_origin["manifest_sha256"]
                and text_origin["precompile_text_seal_sha256"] == compile_value["precompile_text_seal_sha256"]
                and text_origin["final_artifact_seal_sha256"] == final_seal["seal_sha256"]
                and text_origin["rendered_text_inventory_sha256"] == rendered_inventory["inventory_sha256"]
            )
            relational_checks = {
                "report_fingerprint": not report_fingerprint_is_stale,
                "compile_adapter": adapter_is_current,
                "compile_recorder": recorder_is_current,
                "runtime_inputs": runtime_inputs_are_current,
                "generated_inputs": generated_inputs_are_current,
                "pdf": pdf_is_current,
                "main_tex": main_tex_is_current,
                "compile_manifest": compile_manifest_is_current,
                "final_artifact_seal": final_seal_is_current,
                "render_evidence_manifest": render_evidence_is_current,
                "rendered_text_object_inventory": rendered_inventory_is_current,
                "text_origin_manifest": text_origin_is_current,
            }
            failed_relations = [name for name, current in relational_checks.items() if not current]
            if failed_relations:
                _reject(
                    "Legacy final compile provenance is absent, stale, or unsupported",
                    "compile_provenance", "legacy_compile_provenance_invalid",
                    failed_relations=failed_relations,
                )

        artifacts = []
        for logical_id, path in (("final_pdf", local["final_pdf"]), ("main_tex", local["main_tex"])):
            digest = sha256_file(path)
            artifacts.append({"logical_id": logical_id, "path": str(path), "size": path.stat().st_size,
                              "sha256": digest, "generation_id": hashlib.sha256(f"{logical_id}\0{digest}".encode()).hexdigest()})
        gate_binding = GlobalGatePublisher().require_current(control_store_root=control_store_root)
        value = {
            "schema_name": "legacy-acceptance-input-set", "schema_version": "1.0.0",
            "activation_status": "active_global_gate", "input_track": "legacy",
            "input_set_id": hashlib.sha256(f"{root}\0{adopted_at}".encode()).hexdigest()[:32],
            "video_output_dir": str(root), "artifacts": artifacts,
            "quality_inputs_manifest": {"path": str(local["quality_manifest"]), "sha256": sha256_file(local["quality_manifest"])},
            "quality_inputs": normalized_quality_inputs,
            "allowed_artifacts_manifest": {"path": str(local["allowed_manifest"]), "sha256": sha256_file(local["allowed_manifest"])},
            "compile_provenance": {"path": str(local["compile_report"]), "sha256": sha256_file(local["compile_report"]), "classification": "guarded_final_compile"},
            "acceptance_criteria": {"path": str(criteria_path), "sha256": sha256_file(criteria_path)},
            "acceptance_dimension_map": {"path": str(local["dimension_map"]), "sha256": sha256_file(local["dimension_map"])},
            "rendered_pages": {"manifest_path": str(local["pages_manifest"]), "manifest_sha256": sha256_file(local["pages_manifest"]), "page_count": len(normalized_pages), "pages": normalized_pages},
            "provider": {"provider_id": "legacy-acceptance-adoption-provider", "provider_version": "1.0.0", "provider_sha256": sha256_file(Path(__file__))},
            "invocation": {"explicit_video_output_dir": str(root), "selection_policy": "explicit_paths_only"},
            "adopted_at": adopted_at,
            "global_gate_authority": gate_binding,
        }
        value["input_set_sha256"] = _fingerprint(value, "input_set_sha256")
        target = (output or root / "review/acceptance/legacy_input_set.json").resolve()
        if not target.is_relative_to(root):
            _reject("Legacy input-set output escapes the video directory", "path_boundary", "legacy_output_path_escape")
        target.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(target, value)
        return {"input_set_path": str(target), "input_set_sha256": value["input_set_sha256"], "input_track": "legacy", "activation_status": "active_global_gate"}


class GlobalGatePublisher:
    """Crash-safe CAS publication for the sole active global delivery gate."""

    def __init__(self, *, project_root: Path | None = None) -> None:
        self.project_root = (
            project_root.resolve()
            if project_root is not None
            else Path(__file__).resolve().parents[2]
        )

    def _connect(self, root: Path) -> sqlite3.Connection:
        if not root.is_dir():
            _control_reject("Global Gate control-store root is unavailable", "global_gate_control_store_unavailable")
        try:
            connection = sqlite3.connect(root / GLOBAL_GATE_DB, timeout=0.05, isolation_level=None)
            connection.row_factory = sqlite3.Row
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                connection.close()
                _control_reject("Global Gate control store is corrupt", "global_gate_control_store_corrupt")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in {0, GLOBAL_GATE_SCHEMA_VERSION}:
                connection.close()
                _control_reject("Global Gate control store schema is incompatible", "global_gate_control_store_incompatible")
            connection.execute(f"PRAGMA user_version={GLOBAL_GATE_SCHEMA_VERSION}")
            connection.execute("CREATE TABLE IF NOT EXISTS gate_authority (singleton INTEGER PRIMARY KEY CHECK(singleton=1), generation INTEGER NOT NULL, evidence_sha256 TEXT NOT NULL, authority_sha256 TEXT NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS gate_intents (intent_id TEXT PRIMARY KEY, expected_generation INTEGER NOT NULL, evidence_sha256 TEXT NOT NULL, state TEXT NOT NULL, authority_sha256 TEXT, authority_json TEXT, evidence_path TEXT, project_root TEXT, publication_commit TEXT)")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS gate_policy_authority ("
                "singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
                "generation INTEGER NOT NULL, evidence_sha256 TEXT NOT NULL, "
                "authority_sha256 TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS gate_policy_refresh_intents ("
                "intent_id TEXT PRIMARY KEY, expected_generation INTEGER NOT NULL, "
                "evidence_sha256 TEXT NOT NULL, state TEXT NOT NULL "
                "CHECK(state IN ('PREPARED','COMMITTED')), authority_json TEXT NOT NULL, "
                "evidence_path TEXT NOT NULL, project_root TEXT NOT NULL, "
                "publication_commit TEXT NOT NULL)"
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(gate_intents)")}
            migrations = {
                "authority_json": "TEXT",
                "evidence_path": "TEXT",
                "project_root": "TEXT",
                "publication_commit": "TEXT",
            }
            for column, declaration in migrations.items():
                if column not in columns:
                    connection.execute(f"ALTER TABLE gate_intents ADD COLUMN {column} {declaration}")
            return connection
        except ControlStoreUnavailable:
            raise
        except sqlite3.DatabaseError as exc:
            code = "global_gate_control_store_corrupt" if "not a database" in str(exc).casefold() else "global_gate_control_store_locked"
            raise ControlStoreUnavailable("Global Gate control store cannot be opened", data={"first_failing_gate": "control_store", "error_code": code}) from exc
        except OSError as exc:
            raise ControlStoreUnavailable("Global Gate control store is unavailable", data={"first_failing_gate": "control_store", "error_code": "global_gate_control_store_unavailable"}) from exc

    def _validate_publication_identity(
        self, *, evidence_path: Path, project_root: Path,
        expected_sha256: str | None = None,
        expected_publication_commit: str | None = None,
    ) -> tuple[Any, str]:
        try:
            head_before = git_output(project_root, "rev-parse", "HEAD")
            validated = validate_global_gate_exit_evidence(
                evidence_path,
                project_root=project_root,
            )
            head_after = git_output(project_root, "rev-parse", "HEAD")
        except ExitEvidenceValidationError as exc:
            _reject(str(exc), exc.first_failing_gate, exc.error_code)
        except EvidenceSupportError as exc:
            _reject(
                str(exc),
                "implementation_currentness",
                "evidence_publication_not_current",
            )
        if head_before != head_after:
            _reject(
                "Global Gate evidence publication changed during validation",
                "implementation_currentness",
                "evidence_publication_not_current",
            )
        if (
            expected_sha256 is not None
            and validated.sha256 != expected_sha256
        ) or (
            expected_publication_commit is not None
            and head_after != expected_publication_commit
        ):
            _reject(
                "Global Gate evidence publication no longer matches its prepared intent",
                "implementation_currentness",
                "evidence_publication_not_current",
            )
        return validated, head_after

    def activate(self, *, control_store_root: Path, exit_evidence: Path, activated_at: str, fault_point: str | None = None) -> dict[str, Any]:
        root = control_store_root.resolve()
        evidence_path = exit_evidence.resolve()
        validated, publication_commit = self._validate_publication_identity(
            evidence_path=evidence_path,
            project_root=self.project_root,
        )
        evidence_sha = validated.sha256
        authority_path = root / "active_global_gate.json"
        intent_id = hashlib.sha256((evidence_sha + "\0global_acceptance_v2").encode()).hexdigest()
        authority = {"schema_name": "global-gate-authority", "schema_version": "1.0.0", "generation": 1,
                     "active_global_gate": "acceptance_report_v2", "acceptance_report_schema_version": "2.0.0",
                     "legacy_acceptance_authority": "legacy_acceptance_input_set_v1", "platform_kernel_authority": "unchanged",
                     "exit_evidence_path": str(evidence_path), "exit_evidence_sha256": evidence_sha, "activated_at": activated_at}
        authority["authority_sha256"] = _fingerprint(authority, "authority_sha256")
        with self._connect(root) as control:
            try:
                control.execute("BEGIN IMMEDIATE")
                current = control.execute("SELECT * FROM gate_authority WHERE singleton=1").fetchone()
                if current is not None:
                    if current["evidence_sha256"] != evidence_sha:
                        control.execute("ROLLBACK")
                        _reject("A different Global Gate authority already won the CAS", "activation_fencing", "global_gate_authority_conflict")
                    authority = read_json(authority_path) if authority_path.is_file() else None
                    if not authority or sha256_file(authority_path) != current["authority_sha256"]:
                        control.execute("ROLLBACK")
                        _reject("Committed Global Gate authority bytes are stale", "activation_reconcile", "global_gate_authority_stale")
                    control.execute("COMMIT")
                    return {"authority_path": str(authority_path), "authority_sha256": current["authority_sha256"], "generation": current["generation"], "idempotent": True}
                pending = control.execute("SELECT * FROM gate_intents WHERE state='PREPARED'").fetchall()
                if pending:
                    control.execute("ROLLBACK")
                    _reject("An interrupted Global Gate publication requires reconciliation", "activation_reconcile", "global_gate_reconcile_required")
                control.execute(
                    "INSERT INTO gate_intents(intent_id,expected_generation,evidence_sha256,state,authority_sha256,authority_json,evidence_path,project_root,publication_commit) VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        intent_id, 0, evidence_sha, "PREPARED", authority["authority_sha256"],
                        json.dumps(authority, sort_keys=True, separators=(",", ":")),
                        str(evidence_path), str(self.project_root), publication_commit,
                    ),
                )
                control.execute("COMMIT")
            except sqlite3.OperationalError as exc:
                try:
                    control.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise ControlStoreUnavailable("Global Gate control store is unavailable or locked", data={"first_failing_gate": "control_store", "error_code": "global_gate_control_store_locked"}) from exc
        if fault_point == "after_intent":
            raise GlobalGateFault(fault_point)
        self._validate_publication_identity(
            evidence_path=evidence_path,
            project_root=self.project_root,
            expected_sha256=evidence_sha,
            expected_publication_commit=publication_commit,
        )
        write_json_atomic(authority_path, authority)
        if fault_point == "after_authority_write":
            raise GlobalGateFault(fault_point)
        file_sha = sha256_file(authority_path)
        with self._connect(root) as control:
            try:
                control.execute("BEGIN IMMEDIATE")
                self._validate_publication_identity(
                    evidence_path=evidence_path,
                    project_root=self.project_root,
                    expected_sha256=evidence_sha,
                    expected_publication_commit=publication_commit,
                )
                control.execute("INSERT INTO gate_authority(singleton,generation,evidence_sha256,authority_sha256) VALUES(1,1,?,?)", (evidence_sha, file_sha))
                control.execute("UPDATE gate_intents SET state='COMMITTED',authority_sha256=? WHERE intent_id=?", (file_sha, intent_id))
                control.execute("COMMIT")
            except sqlite3.OperationalError as exc:
                try:
                    control.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise ControlStoreUnavailable("Global Gate control store is unavailable or locked", data={"first_failing_gate": "control_store", "error_code": "global_gate_control_store_locked"}) from exc
        if fault_point == "after_control_commit":
            raise GlobalGateFault(fault_point)
        return {"authority_path": str(authority_path), "authority_sha256": file_sha, "generation": 1, "idempotent": False}

    def require_current(self, *, control_store_root: Path) -> dict[str, Any]:
        root = control_store_root.resolve()
        authority_path = root / "active_global_gate.json"
        with self._connect(root) as control:
            row = control.execute("SELECT * FROM gate_authority WHERE singleton=1").fetchone()
            pending = control.execute("SELECT COUNT(*) FROM gate_intents WHERE state!='COMMITTED'").fetchone()[0]
        if row is None or pending or not authority_path.is_file() or sha256_file(authority_path) != row["authority_sha256"]:
            _reject("Global Gate authority is absent, stale, or has an incomplete publication", "global_gate_authority", "global_gate_authority_stale")
        value = read_json(authority_path)
        if (
            value.get("active_global_gate") != "acceptance_report_v2"
            or value.get("platform_kernel_authority") != "unchanged"
            or value.get("generation") != row["generation"]
            or value.get("exit_evidence_sha256") != row["evidence_sha256"]
            or value.get("authority_sha256") != _fingerprint(value, "authority_sha256")
        ):
            _reject("Global Gate authority content conflicts with committed control state", "global_gate_authority", "global_gate_authority_conflict")
        return {
            "control_store_root": str(root), "path": str(authority_path),
            "file_sha256": row["authority_sha256"], "authority_sha256": value["authority_sha256"],
            "exit_evidence_sha256": row["evidence_sha256"], "generation": row["generation"],
        }

    def reconcile(self, *, control_store_root: Path) -> dict[str, Any]:
        root = control_store_root.resolve()
        authority_path = root / "active_global_gate.json"
        with self._connect(root) as control:
            policy_pending = control.execute(
                "SELECT intent_id FROM gate_policy_refresh_intents "
                "WHERE state='PREPARED'"
            ).fetchall()
            committed_policy = control.execute(
                "SELECT * FROM gate_policy_authority WHERE singleton=1"
            ).fetchone()
        if len(policy_pending) > 1:
            _reject(
                "Multiple Global Gate policy refreshes require operator disposition",
                "policy_authority_reconcile",
                "global_gate_policy_refresh_ambiguous",
            )
        if policy_pending:
            return self._reconcile_policy_authority_refresh(
                control_store_root=root,
                intent_id=policy_pending[0]["intent_id"],
            )
        if committed_policy is not None:
            current = self.require_current(control_store_root=root)
            _, _ = self._validate_policy_authority(
                root=root,
                current=current,
                row=committed_policy,
            )
            return {
                "authority_path": str(root / "active_global_gate_policy.json"),
                "authority_sha256": committed_policy["authority_sha256"],
                "generation": committed_policy["generation"],
                "base_global_gate": current,
                "reconciled": True,
            }
        with self._connect(root) as control:
            control.execute("BEGIN IMMEDIATE")
            current = control.execute("SELECT * FROM gate_authority WHERE singleton=1").fetchone()
            pending = control.execute("SELECT * FROM gate_intents WHERE state='PREPARED'").fetchall()
            if len(pending) > 1:
                control.execute("ROLLBACK")
                _reject("Multiple activation publications require operator disposition", "activation_reconcile", "global_gate_reconcile_ambiguous")
            if pending:
                intent = pending[0]
                authority = json.loads(intent["authority_json"])
                if not intent["evidence_path"] or not intent["project_root"] or not intent["publication_commit"]:
                    control.execute("ROLLBACK")
                    _reject("Interrupted Global Gate publication lacks validation identity", "activation_reconcile", "global_gate_reconcile_ambiguous")
                evidence_path = Path(intent["evidence_path"])
                project_root = Path(intent["project_root"])
                self._validate_publication_identity(
                    evidence_path=evidence_path,
                    project_root=project_root,
                    expected_sha256=intent["evidence_sha256"],
                    expected_publication_commit=intent["publication_commit"],
                )
                if not authority_path.is_file():
                    write_json_atomic(authority_path, authority)
                if read_json(authority_path) != authority:
                    control.execute("ROLLBACK")
                    _reject("Interrupted Global Gate authority bytes conflict", "activation_reconcile", "global_gate_authority_stale")
                file_sha = sha256_file(authority_path)
                if current is None:
                    control.execute("INSERT INTO gate_authority(singleton,generation,evidence_sha256,authority_sha256) VALUES(1,1,?,?)", (intent["evidence_sha256"], file_sha))
                elif current["evidence_sha256"] != intent["evidence_sha256"] or current["authority_sha256"] != file_sha:
                    control.execute("ROLLBACK")
                    _reject("Interrupted publication lost its activation fence", "activation_fencing", "global_gate_authority_conflict")
                self._validate_publication_identity(
                    evidence_path=evidence_path,
                    project_root=project_root,
                    expected_sha256=intent["evidence_sha256"],
                    expected_publication_commit=intent["publication_commit"],
                )
                control.execute("UPDATE gate_intents SET state='COMMITTED',authority_sha256=? WHERE intent_id=?", (file_sha, intent["intent_id"]))
            control.execute("COMMIT")
        current_value = self.require_current(control_store_root=root)
        return {"authority_path": current_value["path"], "authority_sha256": current_value["file_sha256"], "generation": current_value["generation"], "reconciled": True}

    @staticmethod
    def _policy_binding(current: dict[str, Any]) -> dict[str, Any]:
        return {
            "path": current["path"],
            "file_sha256": current["file_sha256"],
            "authority_sha256": current["authority_sha256"],
            "generation": current["generation"],
        }

    def _policy_authority_matches_committed_state(
        self,
        *,
        authority: dict[str, Any],
        current: dict[str, Any],
        row: sqlite3.Row,
    ) -> bool:
        return (
            authority.get("schema_name") == "global-gate-policy-authority"
            and authority.get("schema_version") == "1.0.0"
            and authority.get("generation") == row["generation"]
            and authority.get("exit_evidence_sha256") == row["evidence_sha256"]
            and authority.get("base_global_gate") == self._policy_binding(current)
            and authority.get("authority_sha256")
            == _fingerprint(authority, "authority_sha256")
        )

    def _validate_policy_authority(
        self,
        *,
        root: Path,
        current: dict[str, Any],
        row: sqlite3.Row,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        authority_path = root / "active_global_gate_policy.json"
        if not authority_path.is_file() or sha256_file(authority_path) != row["authority_sha256"]:
            _reject(
                "Global Gate policy authority bytes are absent or stale",
                "policy_authority",
                "global_gate_policy_authority_stale",
            )
        authority = read_json(authority_path)
        if not self._policy_authority_matches_committed_state(
            authority=authority,
            current=current,
            row=row,
        ):
            _reject(
                "Global Gate policy authority conflicts with committed control state",
                "policy_authority",
                "global_gate_policy_authority_conflict",
            )
        evidence_path = Path(str(authority.get("exit_evidence_path", ""))).resolve()
        validated, publication_commit = self._validate_publication_identity(
            evidence_path=evidence_path,
            project_root=self.project_root,
            expected_sha256=row["evidence_sha256"],
            expected_publication_commit=authority.get("publication_commit"),
        )
        if publication_commit != authority.get("publication_commit"):
            _reject(
                "Global Gate policy evidence publication is stale",
                "policy_authority",
                "global_gate_policy_exit_evidence_stale",
            )
        return authority, validated.value

    def refresh_policy_authority(
        self,
        *,
        control_store_root: Path,
        exit_evidence: Path,
        expected_generation: int,
        refreshed_at: str,
        fault_point: str | None = None,
    ) -> dict[str, Any]:
        """Advance policy evidence while preserving the stable delivery authority."""

        root = control_store_root.resolve()
        current = self.require_current(control_store_root=root)
        stable_authority_path = Path(current["path"])
        stable_bytes = stable_authority_path.read_bytes()
        evidence_path = exit_evidence.resolve()
        validated, publication_commit = self._validate_publication_identity(
            evidence_path=evidence_path,
            project_root=self.project_root,
        )
        evidence_sha256 = validated.sha256
        policy_path = root / "active_global_gate_policy.json"

        with self._connect(root) as control:
            try:
                control.execute("BEGIN IMMEDIATE")
                row = control.execute(
                    "SELECT * FROM gate_policy_authority WHERE singleton=1"
                ).fetchone()
                pending = control.execute(
                    "SELECT COUNT(*) FROM gate_policy_refresh_intents WHERE state='PREPARED'"
                ).fetchone()[0]
                committed = control.execute(
                    "SELECT authority_json FROM gate_policy_refresh_intents "
                    "WHERE expected_generation=? AND evidence_sha256=? AND state='COMMITTED'",
                    (expected_generation, evidence_sha256),
                ).fetchone()
                if pending:
                    control.execute("ROLLBACK")
                    _reject(
                        "Interrupted Global Gate policy refresh requires reconciliation",
                        "policy_authority_reconcile",
                        "global_gate_policy_reconcile_required",
                    )
                actual_generation = 0 if row is None else int(row["generation"])
                if (
                    committed is not None
                    and row is not None
                    and actual_generation == expected_generation + 1
                    and row["evidence_sha256"] == evidence_sha256
                    and policy_path.is_file()
                    and sha256_file(policy_path) == row["authority_sha256"]
                    and read_json(policy_path) == json.loads(committed["authority_json"])
                ):
                    control.execute("COMMIT")
                    return {
                        "authority_path": str(policy_path),
                        "authority_sha256": row["authority_sha256"],
                        "generation": actual_generation,
                        "base_global_gate": current,
                        "idempotent": True,
                    }
                if actual_generation != expected_generation:
                    control.execute("ROLLBACK")
                    _reject(
                        "Global Gate policy refresh expected generation is stale",
                        "policy_authority",
                        "global_gate_policy_refresh_fenced",
                        expected_generation=expected_generation,
                        actual_generation=actual_generation,
                    )
                if row is not None and row["evidence_sha256"] == evidence_sha256:
                    control.execute("COMMIT")
                    return {
                        "authority_path": str(policy_path),
                        "authority_sha256": row["authority_sha256"],
                        "generation": actual_generation,
                        "base_global_gate": current,
                        "idempotent": True,
                    }
                generation = actual_generation + 1
                authority = {
                    "schema_name": "global-gate-policy-authority",
                    "schema_version": "1.0.0",
                    "generation": generation,
                    "base_global_gate": self._policy_binding(current),
                    "exit_evidence_path": str(evidence_path),
                    "exit_evidence_sha256": evidence_sha256,
                    "publication_commit": publication_commit,
                    "implementation_commit": validated.value["implementation_commit"],
                    "refreshed_at": refreshed_at,
                }
                authority["authority_sha256"] = _fingerprint(authority, "authority_sha256")
                intent_id = hashlib.sha256(
                    f"{generation}\0{evidence_sha256}\0global_gate_policy".encode("utf-8")
                ).hexdigest()
                control.execute(
                    "INSERT INTO gate_policy_refresh_intents("
                    "intent_id,expected_generation,evidence_sha256,state,authority_json,"
                    "evidence_path,project_root,publication_commit) "
                    "VALUES(?,?,?,'PREPARED',?,?,?,?)",
                    (
                        intent_id,
                        actual_generation,
                        evidence_sha256,
                        json.dumps(authority, sort_keys=True, separators=(",", ":")),
                        str(evidence_path),
                        str(self.project_root),
                        publication_commit,
                    ),
                )
                control.execute("COMMIT")
            except sqlite3.OperationalError as exc:
                try:
                    control.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise ControlStoreUnavailable(
                    "Global Gate control store is unavailable or locked",
                    data={
                        "first_failing_gate": "control_store",
                        "error_code": "global_gate_control_store_locked",
                    },
                ) from exc

        if fault_point == "after_intent":
            raise GlobalGateFault(fault_point)
        self._validate_publication_identity(
            evidence_path=evidence_path,
            project_root=self.project_root,
            expected_sha256=evidence_sha256,
            expected_publication_commit=publication_commit,
        )
        if stable_authority_path.read_bytes() != stable_bytes:
            _reject(
                "Stable Global Gate authority changed during policy refresh",
                "policy_authority",
                "global_gate_base_authority_drift",
            )
        write_json_atomic(policy_path, authority)
        if fault_point == "after_authority_write":
            raise GlobalGateFault(fault_point)
        result = self._commit_policy_authority_refresh(
            root=root,
            intent_id=intent_id,
            stable_bytes=stable_bytes,
        )
        if fault_point == "after_control_commit":
            raise GlobalGateFault(fault_point)
        result["idempotent"] = False
        return result

    def _commit_policy_authority_refresh(
        self, *, root: Path, intent_id: str, stable_bytes: bytes
    ) -> dict[str, Any]:
        policy_path = root / "active_global_gate_policy.json"
        with self._connect(root) as control:
            try:
                control.execute("BEGIN IMMEDIATE")
                intent = control.execute(
                    "SELECT * FROM gate_policy_refresh_intents "
                    "WHERE intent_id=? AND state='PREPARED'",
                    (intent_id,),
                ).fetchone()
                row = control.execute(
                    "SELECT * FROM gate_policy_authority WHERE singleton=1"
                ).fetchone()
                if intent is None or (0 if row is None else int(row["generation"])) != int(intent["expected_generation"]):
                    control.execute("ROLLBACK")
                    _reject(
                        "Global Gate policy refresh lost its generation fence",
                        "policy_authority",
                        "global_gate_policy_refresh_fenced",
                    )
                stable_authority_path = root / "active_global_gate.json"
                stable_row = control.execute(
                    "SELECT * FROM gate_authority WHERE singleton=1"
                ).fetchone()
                pending_activation = control.execute(
                    "SELECT COUNT(*) FROM gate_intents WHERE state!='COMMITTED'"
                ).fetchone()[0]
                if (
                    stable_row is None
                    or pending_activation
                    or not stable_authority_path.is_file()
                    or sha256_file(stable_authority_path) != stable_row["authority_sha256"]
                    or stable_authority_path.read_bytes() != stable_bytes
                ):
                    control.execute("ROLLBACK")
                    _reject(
                        "Stable Global Gate authority changed during policy refresh",
                        "policy_authority",
                        "global_gate_base_authority_drift",
                    )
                stable_authority = read_json(stable_authority_path)
                if (
                    stable_authority.get("active_global_gate") != "acceptance_report_v2"
                    or stable_authority.get("platform_kernel_authority") != "unchanged"
                    or stable_authority.get("generation") != stable_row["generation"]
                    or stable_authority.get("exit_evidence_sha256") != stable_row["evidence_sha256"]
                    or stable_authority.get("authority_sha256")
                    != _fingerprint(stable_authority, "authority_sha256")
                ):
                    control.execute("ROLLBACK")
                    _reject(
                        "Stable Global Gate authority conflicts with committed control state",
                        "policy_authority",
                        "global_gate_base_authority_drift",
                    )
                current = {
                    "control_store_root": str(root),
                    "path": str(stable_authority_path),
                    "file_sha256": stable_row["authority_sha256"],
                    "authority_sha256": stable_authority["authority_sha256"],
                    "exit_evidence_sha256": stable_row["evidence_sha256"],
                    "generation": stable_row["generation"],
                }
                self._validate_publication_identity(
                    evidence_path=Path(intent["evidence_path"]),
                    project_root=Path(intent["project_root"]),
                    expected_sha256=intent["evidence_sha256"],
                    expected_publication_commit=intent["publication_commit"],
                )
                authority = json.loads(intent["authority_json"])
                if not policy_path.is_file() or read_json(policy_path) != authority:
                    control.execute("ROLLBACK")
                    _reject(
                        "Global Gate policy authority bytes conflict with prepared intent",
                        "policy_authority_reconcile",
                        "global_gate_policy_authority_stale",
                    )
                file_sha256 = sha256_file(policy_path)
                generation = int(intent["expected_generation"]) + 1
                if row is None:
                    control.execute(
                        "INSERT INTO gate_policy_authority(singleton,generation,evidence_sha256,authority_sha256) VALUES(1,?,?,?)",
                        (generation, intent["evidence_sha256"], file_sha256),
                    )
                else:
                    changed = control.execute(
                        "UPDATE gate_policy_authority SET generation=?,evidence_sha256=?,authority_sha256=? "
                        "WHERE singleton=1 AND generation=? AND authority_sha256=?",
                        (
                            generation,
                            intent["evidence_sha256"],
                            file_sha256,
                            intent["expected_generation"],
                            row["authority_sha256"],
                        ),
                    ).rowcount
                    if changed != 1:
                        control.execute("ROLLBACK")
                        _reject(
                            "Global Gate policy refresh lost its generation fence",
                            "policy_authority",
                            "global_gate_policy_refresh_fenced",
                        )
                control.execute(
                    "UPDATE gate_policy_refresh_intents SET state='COMMITTED' "
                    "WHERE intent_id=? AND state='PREPARED'",
                    (intent_id,),
                )
                control.execute("COMMIT")
            except sqlite3.OperationalError as exc:
                try:
                    control.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise ControlStoreUnavailable(
                    "Global Gate control store is unavailable or locked",
                    data={
                        "first_failing_gate": "control_store",
                        "error_code": "global_gate_control_store_locked",
                    },
                ) from exc
        return {
            "authority_path": str(policy_path),
            "authority_sha256": file_sha256,
            "generation": generation,
            "base_global_gate": current,
            "reconciled": True,
        }

    def _reconcile_policy_authority_refresh(
        self, *, control_store_root: Path, intent_id: str
    ) -> dict[str, Any]:
        root = control_store_root.resolve()
        current = self.require_current(control_store_root=root)
        stable_bytes = Path(current["path"]).read_bytes()
        with self._connect(root) as control:
            intent = control.execute(
                "SELECT * FROM gate_policy_refresh_intents "
                "WHERE intent_id=? AND state='PREPARED'",
                (intent_id,),
            ).fetchone()
            committed = control.execute(
                "SELECT * FROM gate_policy_authority WHERE singleton=1"
            ).fetchone()
        if intent is None:
            _reject(
                "Global Gate policy refresh intent is unavailable",
                "policy_authority_reconcile",
                "global_gate_policy_refresh_ambiguous",
            )
        self._validate_publication_identity(
            evidence_path=Path(intent["evidence_path"]),
            project_root=Path(intent["project_root"]),
            expected_sha256=intent["evidence_sha256"],
            expected_publication_commit=intent["publication_commit"],
        )
        policy_path = root / "active_global_gate_policy.json"
        authority = json.loads(intent["authority_json"])
        if not policy_path.is_file():
            write_json_atomic(policy_path, authority)
        else:
            existing_authority = read_json(policy_path)
            if existing_authority != authority:
                previous_is_exact_committed_authority = (
                    committed is not None
                    and int(committed["generation"])
                    == int(intent["expected_generation"])
                    and sha256_file(policy_path) == committed["authority_sha256"]
                    and self._policy_authority_matches_committed_state(
                        authority=existing_authority,
                        current=current,
                        row=committed,
                    )
                )
                if not previous_is_exact_committed_authority:
                    _reject(
                        "Interrupted Global Gate policy authority bytes conflict",
                        "policy_authority_reconcile",
                        "global_gate_policy_authority_stale",
                    )
                write_json_atomic(policy_path, authority)
        result = self._commit_policy_authority_refresh(
            root=root,
            intent_id=intent_id,
            stable_bytes=stable_bytes,
        )
        result["reconciled"] = True
        return result

    def check_policy(self, *, control_store_root: Path) -> dict[str, Any]:
        root = control_store_root.resolve()
        current = self.require_current(control_store_root=root)
        with self._connect(root) as control:
            policy = control.execute(
                "SELECT * FROM gate_policy_authority WHERE singleton=1"
            ).fetchone()
            pending = control.execute(
                "SELECT COUNT(*) FROM gate_policy_refresh_intents WHERE state='PREPARED'"
            ).fetchone()[0]
        if pending:
            _reject(
                "Global Gate policy authority has an incomplete publication",
                "policy_authority_reconcile",
                "global_gate_policy_reconcile_required",
            )
        if policy is not None:
            policy_authority, evidence = self._validate_policy_authority(
                root=root,
                current=current,
                row=policy,
            )
            policy_binding = {
                "path": str(root / "active_global_gate_policy.json"),
                "file_sha256": policy["authority_sha256"],
                "authority_sha256": policy_authority["authority_sha256"],
                "exit_evidence_sha256": policy["evidence_sha256"],
                "generation": policy["generation"],
            }
        else:
            policy_authority = None
            policy_binding = None
            authority = read_json(Path(current["path"]))
            evidence_path = Path(authority["exit_evidence_path"])
            if not evidence_path.is_file() or sha256_file(evidence_path) != current["exit_evidence_sha256"]:
                _reject("Current Global Gate Exit Evidence is stale", "global_gate_authority", "global_gate_exit_evidence_stale")
            evidence = _validate_policy_evidence(read_json(evidence_path))
        return {
            "current": True,
            "policy_status": evidence["policy_status"],
            "active_atomic_members": sorted(evidence["atomic_member_status"]),
            "mirror_checks": evidence["mirror_checks"],
            "global_gate_authority": current,
            "policy_authority": policy_binding,
            "results": evidence["results"],
        }
