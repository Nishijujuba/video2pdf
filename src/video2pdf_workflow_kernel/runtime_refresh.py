from __future__ import annotations

import hashlib
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any

from .content_production import ContentProduction
from .delivery_quality import DeliveryQualityRegistry
from .errors import ContractError, RuntimeRefreshFault
from .kernel import VideoWorkflowKernel
from .precompile_quality import PrecompileQualityProvider
from .utils import (
    canonical_json_bytes,
    read_json,
    require_contained_path,
    sha256_file,
    write_json_atomic,
)


RUNTIME_REFRESH_FAULT_POINTS = {
    "before_production_state_publish",
    "after_diagnostic_publish",
}

CONTENT_REPAIR_HANDOFF_FAULT_POINTS = {"after_seal_before_runtime_supersession"}
def _fingerprint(value: dict[str, Any], field: str) -> str:
    return hashlib.sha256(
        canonical_json_bytes({key: item for key, item in value.items() if key != field})
    ).hexdigest()


def _require_fingerprint(value: dict[str, Any], field: str, label: str) -> None:
    if value.get(field) != _fingerprint(value, field):
        raise ContractError(f"{label} fingerprint is stale")


class CompileRuntimeRefreshProvider:
    """Derive, diagnose, and bind one resumable Compile Runtime successor."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.quality = DeliveryQualityRegistry(self.project_root)

    def refresh(
        self,
        *,
        run_dir: Path,
        refreshed_at: str,
        precompile_workspace_root: Path | None = None,
        final_compile_manifest_path: Path | None = None,
        fault_point: str | None = None,
    ) -> dict[str, Any]:
        if fault_point is not None and fault_point not in RUNTIME_REFRESH_FAULT_POINTS:
            raise ContractError(f"unsupported Compile Runtime refresh fault point: {fault_point}")
        run = run_dir.resolve()
        production = ContentProduction(VideoWorkflowKernel(run.parent))
        active_path = run / "workflow/runtime-refresh-active.json"
        existing = read_json(active_path) if active_path.is_file() else None
        if existing is not None and existing.get("state") == "superseded_by_content_repair":
            self.validate_retained_content_repair_closure(
                run_dir=run, journal=existing
            )
            handoff = existing["content_repair_handoff"]
            if (
                existing.get("refreshed_at") != refreshed_at
                or (
                    precompile_workspace_root is not None
                    and precompile_workspace_root.resolve()
                    != Path(handoff["promotion"]["workspace_root"])
                )
                or (
                    final_compile_manifest_path is not None
                    and final_compile_manifest_path.resolve()
                    != Path(handoff["predecessor_final_compile_manifest_path"])
                )
            ):
                raise ContractError(
                    "superseded Compile Runtime refresh replay arguments changed"
                )
            return self._result(existing)
        if (
            existing is not None
            and existing.get("state") == "committed"
            and existing.get("refreshed_at") != refreshed_at
        ):
            _require_fingerprint(
                existing, "journal_sha256", "Compile Runtime refresh journal"
            )
            self._validate_committed(
                existing, production, run, allow_runtime_input_drift=True
            )
            existing = None

        if existing is None:
            authority = production.require_current_diagnostic_compile_authority(run)
            old_policy_path = Path(authority["runtime_policy_path"])
            old_policy = read_json(old_policy_path)
            old_report_path = run / "review/latex/diagnostic-compile-report.json"
            old_report = read_json(old_report_path)
            if final_compile_manifest_path is not None:
                self._preflight_predecessor_manifest(
                    final_compile_manifest_path,
                    expected_runtime_policy_sha256=sha256_file(old_policy_path),
                )
            operation_material = {
                "run_id": authority["run_id"],
                "refreshed_at": refreshed_at,
                "predecessor_runtime_policy_sha256": sha256_file(old_policy_path),
                "predecessor_diagnostic_report_sha256": sha256_file(old_report_path),
            }
            operation_id = hashlib.sha256(canonical_json_bytes(operation_material)).hexdigest()[:32]
            operation_root = run / "待删除/runtime-refresh" / operation_id
            operation_root.mkdir(parents=True, exist_ok=True)
            predecessor_root = operation_root / "predecessor"
            predecessor_root.mkdir(parents=True, exist_ok=True)
            predecessor_paths = {
                "runtime_policy": old_policy_path,
                "compile_manifest": run / "workflow/compile-manifest.json",
                "diagnostic_report": old_report_path,
                "diagnostic_pdf": run / "待删除/diagnostic-compile/main.pdf",
                "production_state": run / "workflow/production-state.json",
            }
            predecessor_evidence: dict[str, dict[str, str]] = {}
            for name, source in predecessor_paths.items():
                target = predecessor_root / source.name
                shutil.copyfile(source, target)
                predecessor_evidence[name] = {
                    "source_path": str(source.resolve()),
                    "archive_path": str(target.resolve()),
                    "sha256": sha256_file(target),
                }
            runtime_inputs = self._runtime_inputs(old_report)
            inventory = {
                "schema_version": 1,
                "files": [
                    {"path": str(path), "sha256": sha256_file(path)}
                    for path in runtime_inputs
                ],
            }
            inventory_path = operation_root / "runtime-inventory.json"
            write_json_atomic(inventory_path, inventory)
            successor = dict(old_policy)
            successor["package_inventory"] = {
                "version": f"recorder-successor-{operation_id[:12]}",
                "path": str(inventory_path.resolve()),
                "sha256": sha256_file(inventory_path),
            }
            successor["policy_sha256"] = _fingerprint(successor, "policy_sha256")
            successor_path = operation_root / "compile-runtime-policy.json"
            write_json_atomic(successor_path, successor)
            drifted = self._drifted_inventory_entries(old_policy)
            journal = {
                "schema_name": "compile-runtime-refresh",
                "schema_version": "1.0.0",
                "operation_id": operation_id,
                "run_id": authority["run_id"],
                "state": "prepared",
                "refreshed_at": refreshed_at,
                "run_dir": str(run),
                "predecessor_runtime_policy_path": str(old_policy_path),
                "predecessor_runtime_policy_sha256": sha256_file(old_policy_path),
                "successor_runtime_policy_path": str(successor_path),
                "successor_runtime_policy_sha256": sha256_file(successor_path),
                "runtime_inventory_path": str(inventory_path),
                "drifted_inputs": drifted,
                "predecessor_evidence": predecessor_evidence,
            }
            journal["journal_sha256"] = _fingerprint(journal, "journal_sha256")
            write_json_atomic(operation_root / "journal.json", journal)
            write_json_atomic(active_path, journal)
        else:
            journal = existing
            _require_fingerprint(journal, "journal_sha256", "Compile Runtime refresh journal")
            if journal.get("refreshed_at") != refreshed_at or Path(journal.get("run_dir", "")).resolve() != run:
                raise ContractError("Compile Runtime refresh replay arguments changed")
            expected_operation_id = hashlib.sha256(
                canonical_json_bytes(
                    {
                        "run_id": journal.get("run_id"),
                        "refreshed_at": journal["refreshed_at"],
                        "predecessor_runtime_policy_sha256": journal[
                            "predecessor_runtime_policy_sha256"
                        ],
                        "predecessor_diagnostic_report_sha256": journal[
                            "predecessor_evidence"
                        ]["diagnostic_report"]["sha256"],
                    }
                )
            ).hexdigest()[:32]
            if journal.get("operation_id") != expected_operation_id:
                raise ContractError("Compile Runtime refresh operation identity is stale")
            operation_root = run / "待删除/runtime-refresh" / journal["operation_id"]
            operation_journal_path = operation_root / "journal.json"
            if (
                not operation_journal_path.is_file()
                or read_json(operation_journal_path) != journal
            ):
                write_json_atomic(operation_journal_path, journal)
            successor_path = Path(journal["successor_runtime_policy_path"])
            if not successor_path.is_file():
                raise ContractError("Compile Runtime successor policy is unavailable")
            successor = read_json(successor_path)
            if sha256_file(successor_path) != journal["successor_runtime_policy_sha256"]:
                raise ContractError("Compile Runtime successor policy drifted")
            if journal["state"] == "committed":
                self._validate_committed(journal, production, run)
                if (
                    precompile_workspace_root is None
                    or final_compile_manifest_path is None
                    or precompile_workspace_root.resolve()
                    != Path(journal["precompile_workspace_root"])
                    or final_compile_manifest_path.resolve()
                    != Path(
                        journal["predecessor_evidence"]["final_compile_manifest"][
                            "source_path"
                        ]
                    )
                ):
                    raise ContractError(
                        "committed Compile Runtime refresh replay arguments changed"
                    )
                return self._result(journal)

        if journal["state"] == "prepared":
            self._restore_predecessor_production(journal)
            try:
                result = production.refresh_diagnostic_compile(
                    run, successor, fault_point=fault_point
                )
            except Exception:
                self._restore_predecessor_production(journal)
                raise
            report = read_json(Path(result["compile_report_path"]))
            observed_runtime = {
                str(Path(item["path"]).resolve()).casefold()
                for item in report["dependency_closure"]["inputs"]
                if item["classification"] == "registered_runtime_dependency"
            }
            registered = {
                str(Path(item["path"]).resolve()).casefold()
                for item in read_json(Path(journal["runtime_inventory_path"]))["files"]
            }
            if not observed_runtime.issubset(registered):
                raise ContractError("successor diagnostic compile discovered unregistered runtime inputs")
            journal["state"] = "diagnostic_published"
            journal["successor_diagnostic_report_path"] = result["compile_report_path"]
            journal["successor_diagnostic_report_sha256"] = sha256_file(
                Path(result["compile_report_path"])
            )
            journal["canonical_runtime_policy_path"] = str(
                (run / "workflow/compile-runtime-policy.json").resolve()
            )
            journal["canonical_runtime_policy_sha256"] = sha256_file(
                run / "workflow/compile-runtime-policy.json"
            )
            journal["journal_sha256"] = _fingerprint(journal, "journal_sha256")
            write_json_atomic(operation_root / "journal.json", journal)
            write_json_atomic(active_path, journal)
            if fault_point == "after_diagnostic_publish":
                raise RuntimeRefreshFault(fault_point)

        precompile = self._validate_precompile(precompile_workspace_root)
        if precompile["classification"] != "precompile_seal_reused":
            journal["state"] = "precompile_refresh_required"
            journal["precompile"] = precompile
            journal["journal_sha256"] = _fingerprint(journal, "journal_sha256")
            write_json_atomic(operation_root / "journal.json", journal)
            write_json_atomic(active_path, journal)
            return self._result(journal)

        if final_compile_manifest_path is None:
            raise ContractError("current Final Compile Manifest is required after Precompile reuse")
        self._preflight_predecessor_manifest(
            final_compile_manifest_path,
            expected_runtime_policy_sha256=journal[
                "predecessor_runtime_policy_sha256"
            ],
        )
        predecessor_manifest_source = final_compile_manifest_path.resolve()
        report = read_json(Path(journal["successor_diagnostic_report_path"]))
        successor_manifest = dict(predecessor_manifest)
        generation_bindings = {
            (
                item["logical_id"],
                item["generation"],
                item["sha256"],
            )
            for item in precompile["artifact_generations"]
        }
        predecessor_bindings = {
            (item["logical_id"], item["generation"], item["sha256"])
            for item in predecessor_manifest["entries"]
        }
        if generation_bindings != predecessor_bindings:
            raise ContractError(
                "Precompile successor content differs from the predecessor Final Compile inputs"
            )
        predecessor_manifest_archive = operation_root / "predecessor/final-compile-manifest.json"
        if predecessor_manifest_archive.is_file():
            if sha256_file(predecessor_manifest_archive) != sha256_file(
                predecessor_manifest_source
            ):
                raise ContractError("predecessor Final Compile Manifest changed during refresh")
        else:
            shutil.copyfile(predecessor_manifest_source, predecessor_manifest_archive)
        journal["predecessor_evidence"]["final_compile_manifest"] = {
            "source_path": str(predecessor_manifest_source),
            "archive_path": str(predecessor_manifest_archive.resolve()),
            "sha256": sha256_file(predecessor_manifest_archive),
        }
        successor_manifest["precompile_text_seal_sha256"] = precompile["seal_sha256"]
        successor_manifest["runtime_policy"] = {
            "path": str((run / "workflow/compile-runtime-policy.json").resolve()),
            "sha256": sha256_file(run / "workflow/compile-runtime-policy.json"),
        }
        policy = read_json(run / "workflow/compile-runtime-policy.json")
        successor_manifest["approved_runtime_inputs"] = (
            self._approved_runtime_inputs(report, policy)
        )
        successor_manifest["manifest_sha256"] = _fingerprint(
            successor_manifest, "manifest_sha256"
        )
        self.quality.validate("final-compile-manifest", successor_manifest)
        successor_manifest_path = operation_root / "final-compile-manifest.json"
        write_json_atomic(successor_manifest_path, successor_manifest)
        journal["state"] = "committed"
        journal["precompile"] = precompile
        journal["precompile_workspace_root"] = precompile["workspace_root"]
        journal["successor_final_compile_manifest_path"] = str(successor_manifest_path)
        journal["successor_final_compile_manifest_sha256"] = sha256_file(
            successor_manifest_path
        )
        journal["journal_sha256"] = _fingerprint(journal, "journal_sha256")
        write_json_atomic(operation_root / "journal.json", journal)
        write_json_atomic(active_path, journal)
        return self._result(journal)

    def prepare_content_repair_handoff(
        self,
        *,
        run_dir: Path,
        repair_bundle_path: Path,
        predecessor_final_compile_manifest_path: Path,
        expected_operation_id: str,
    ) -> dict[str, Any]:
        """Persist the one authorized bridge from a pending runtime refresh to repair."""
        run = run_dir.resolve()
        active_path = run / "workflow/runtime-refresh-active.json"
        if not active_path.is_file():
            raise ContractError(
                "content repair handoff requires an active Compile Runtime refresh",
                data={"first_failing_gate": "content_repair_runtime_state", "error_code": "runtime_refresh_handoff_missing"},
            )
        journal = read_json(active_path)
        _require_fingerprint(journal, "journal_sha256", "Compile Runtime refresh journal")
        if journal.get("operation_id") != expected_operation_id:
            raise ContractError(
                "content repair handoff names another Compile Runtime refresh",
                data={"first_failing_gate": "content_repair_runtime_state", "error_code": "runtime_refresh_handoff_operation_mismatch"},
            )
        existing = journal.get("content_repair_handoff")
        if journal.get("state") == "superseded_by_content_repair" and isinstance(existing, dict):
            self.validate_retained_content_repair_closure(
                run_dir=run, journal=journal
            )
            if (
                existing.get("repair_bundle_path") != str(repair_bundle_path.resolve())
                or existing.get("repair_bundle_sha256")
                != sha256_file(repair_bundle_path.resolve())
                or existing.get("predecessor_final_compile_manifest_path")
                != str(predecessor_final_compile_manifest_path.resolve())
                or existing.get("predecessor_final_compile_manifest_sha256")
                != sha256_file(predecessor_final_compile_manifest_path.resolve())
            ):
                raise ContractError("content repair handoff replay identity changed")
            return existing
        if journal.get("state") != "precompile_refresh_required":
            raise ContractError(
                "content repair handoff requires precompile_refresh_required",
                data={"first_failing_gate": "content_repair_runtime_state", "error_code": "runtime_refresh_handoff_state_invalid"},
            )

        canonical_policy_path = run / "workflow/compile-runtime-policy.json"
        canonical_policy_sha256 = sha256_file(canonical_policy_path)
        if (
            canonical_policy_sha256 != journal.get("canonical_runtime_policy_sha256")
            or canonical_policy_sha256 != journal.get("successor_runtime_policy_sha256")
        ):
            raise ContractError(
                "content repair handoff Runtime Policy is stale",
                data={"first_failing_gate": "content_repair_runtime_policy", "error_code": "runtime_refresh_handoff_policy_stale"},
            )
        bundle = read_json(repair_bundle_path.resolve())
        bundled_policy_path = repair_bundle_path.resolve().parent / "payload/compile-runtime-policy.json"
        policy_entries = [
            item for item in bundle.get("derived_payload", [])
            if item.get("path", "").replace("\\", "/").endswith(
                "/payload/compile-runtime-policy.json"
            )
            and (run / str(item.get("path", ""))).resolve() == bundled_policy_path
        ]
        if (
            len(policy_entries) != 1
            or not bundled_policy_path.is_file()
            or sha256_file(bundled_policy_path) != policy_entries[0].get("sha256")
            or sha256_file(bundled_policy_path) != canonical_policy_sha256
        ):
            raise ContractError(
                "content repair bundle lacks the current Runtime Policy binding",
                data={"first_failing_gate": "content_repair_bundle_policy", "error_code": "runtime_refresh_handoff_bundle_policy_mismatch"},
            )
        self._preflight_predecessor_manifest(
            predecessor_final_compile_manifest_path,
            expected_runtime_policy_sha256=journal["predecessor_runtime_policy_sha256"],
        )
        if isinstance(existing, dict):
            _require_fingerprint(existing, "handoff_sha256", "content repair handoff")
            if (
                existing.get("repair_bundle_path") != str(repair_bundle_path.resolve())
                or existing.get("repair_bundle_sha256") != sha256_file(repair_bundle_path.resolve())
                or existing.get("predecessor_final_compile_manifest_path")
                != str(predecessor_final_compile_manifest_path.resolve())
                or existing.get("predecessor_final_compile_manifest_sha256")
                != sha256_file(predecessor_final_compile_manifest_path.resolve())
                or existing.get("runtime_policy_sha256") != canonical_policy_sha256
            ):
                raise ContractError("content repair handoff replay identity changed")
            return existing
        operation_root = run / "待删除/runtime-refresh" / expected_operation_id
        archive_path = operation_root / "content-repair/precompile-refresh-required-journal.json"
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        archived_bytes = canonical_json_bytes(journal)
        if archive_path.exists() and archive_path.read_bytes() != archived_bytes:
            raise ContractError("retained precompile-refresh-required journal conflicts")
        if not archive_path.exists():
            archive_path.write_bytes(archived_bytes)
        handoff = {
            "schema_name": "runtime-refresh-content-repair-handoff",
            "schema_version": "1.0.0",
            "state": "prepared",
            "runtime_refresh_operation_id": expected_operation_id,
            "runtime_refresh_journal_archive_path": str(archive_path.resolve()),
            "runtime_policy_sha256": canonical_policy_sha256,
            "repair_bundle_path": str(repair_bundle_path.resolve()),
            "repair_bundle_sha256": sha256_file(repair_bundle_path.resolve()),
            "predecessor_final_compile_manifest_path": str(predecessor_final_compile_manifest_path.resolve()),
            "predecessor_final_compile_manifest_sha256": sha256_file(predecessor_final_compile_manifest_path.resolve()),
        }
        handoff["handoff_sha256"] = _fingerprint(handoff, "handoff_sha256")
        journal["content_repair_handoff"] = handoff
        journal["journal_sha256"] = _fingerprint(journal, "journal_sha256")
        write_json_atomic(active_path, journal)
        return handoff

    def preflight_content_repair_promotion_refresh(
        self,
        *,
        run_dir: Path,
        expected_operation_id: str,
        repair_bundle_path: Path,
        disposition_path: Path,
        predecessor_contract_gap_brief_path: Path,
        successor_workspace_root: Path,
        actual_write_set: list[str] | None = None,
    ) -> dict[str, Any]:
        run = run_dir.resolve()
        approved_path = require_contained_path(
            disposition_path.resolve(),
            run,
            purpose="content repair human disposition",
            error_type=ContractError,
            leaf_kind="file",
            require_single_link=True,
        )
        disposition = read_json(approved_path)
        _require_fingerprint(
            disposition, "disposition_sha256", "content repair human disposition"
        )
        if disposition.get("schema_version") == "2.0.0":
            return self.preflight_repair_continuation(
                run_dir=run,
                expected_operation_id=expected_operation_id,
                repair_bundle_path=repair_bundle_path,
                failure_authority_path=predecessor_contract_gap_brief_path,
                disposition_path=approved_path,
                successor_workspace_root=successor_workspace_root,
                actual_write_set=actual_write_set,
            )
        raise ContractError(
            "legacy content repair disposition is read-only replay evidence",
            data={
                "first_failing_gate": "content_repair_continuation_disposition",
                "error_code": "runtime_refresh_legacy_disposition_read_only",
            },
        )

    def preflight_repair_continuation(
        self,
        *,
        run_dir: Path,
        expected_operation_id: str,
        repair_bundle_path: Path,
        failure_authority_path: Path,
        successor_workspace_root: Path,
        actual_write_set: list[str],
        disposition_path: Path | None = None,
    ) -> dict[str, Any]:
        run = run_dir.resolve()
        journal = read_json(run / "workflow/runtime-refresh-active.json")
        _require_fingerprint(journal, "journal_sha256", "Compile Runtime refresh journal")
        handoff = journal.get("content_repair_handoff")
        if (
            journal.get("state") != "precompile_refresh_required"
            or journal.get("operation_id") != expected_operation_id
            or not isinstance(handoff, dict)
            or handoff.get("state") != "promotion_ready"
        ):
            raise ContractError(
                "content repair continuation requires the current promotion-ready handoff",
                data={
                    "first_failing_gate": "content_repair_continuation_state",
                    "error_code": "runtime_refresh_continuation_state_invalid",
                },
            )
        _require_fingerprint(handoff, "handoff_sha256", "content repair handoff")
        predecessor_promotion = handoff.get("promotion")
        if not isinstance(predecessor_promotion, dict):
            raise ContractError("content repair continuation predecessor is missing")
        predecessor_workspace = require_contained_path(
            Path(predecessor_promotion["workspace_root"]),
            run,
            purpose="content repair continuation predecessor workspace",
            error_type=ContractError,
            leaf_kind="directory",
        )
        existing_refresh = handoff.get("promotion_refresh")
        current_failure_authority = failure_authority_path.resolve()
        if (
            existing_refresh is not None
            and current_failure_authority.parent != predecessor_workspace
        ):
            if predecessor_promotion.get("workspace_root") != str(
                successor_workspace_root.resolve()
            ):
                raise ContractError(
                    "content repair continuation already owns another successor",
                    data={
                        "first_failing_gate": "content_repair_continuation_successor",
                        "error_code": "runtime_refresh_continuation_competing_successor",
                    },
                )
            supplied_disposition_sha256 = (
                read_json(disposition_path.resolve()).get("disposition_sha256")
                if disposition_path is not None
                else None
            )
            supplied_authority = read_json(failure_authority_path.resolve())
            supplied_authority_sha256 = supplied_authority.get(
                "brief_sha256", supplied_authority.get("report_sha256")
            )
            recorded_authority_sha256 = existing_refresh.get(
                "authorization_sha256",
                existing_refresh.get(
                    "disposition_sha256",
                    existing_refresh.get(
                        "failure_authority_sha256",
                        existing_refresh.get(
                            "predecessor_contract_gap_brief_sha256"
                        ),
                    ),
                ),
            )
            recorded_authority_path = existing_refresh.get(
                "failure_authority_path",
                existing_refresh.get("predecessor_contract_gap_brief_path"),
            )
            recorded_bundle_path = existing_refresh.get("repair_bundle_path")
            if (
                recorded_authority_sha256
                != (supplied_disposition_sha256 or supplied_authority_sha256)
                or (
                    recorded_bundle_path is not None
                    and recorded_bundle_path != str(repair_bundle_path.resolve())
                )
                or existing_refresh.get("repair_bundle_sha256")
                != sha256_file(repair_bundle_path.resolve())
                or recorded_authority_path != str(current_failure_authority)
            ):
                raise ContractError(
                    "content repair continuation replay disposition changed",
                    data={
                        "first_failing_gate": "content_repair_continuation_successor",
                        "error_code": "runtime_refresh_continuation_competing_authority",
                    },
                )
            return existing_refresh
        if predecessor_workspace == successor_workspace_root.resolve():
            raise ContractError(
                "content repair continuation requires a fresh workspace",
                data={
                    "first_failing_gate": "content_repair_continuation_successor",
                    "error_code": "runtime_refresh_continuation_workspace_reused",
                },
            )
        if (predecessor_workspace / "precompile-text-seal.json").exists():
            raise ContractError(
                "sealed Precompile authority cannot start a repair continuation",
                data={
                    "first_failing_gate": "content_repair_continuation_predecessor",
                    "error_code": "runtime_refresh_continuation_predecessor_sealed",
                },
            )
        authority_path = require_contained_path(
            failure_authority_path.resolve(),
            predecessor_workspace,
            purpose="content repair continuation failure authority",
            error_type=ContractError,
            leaf_kind="file",
            require_single_link=True,
        )
        generation_path = require_contained_path(
            Path(predecessor_promotion["generation_set_path"]),
            run,
            purpose="content repair continuation predecessor generations",
            error_type=ContractError,
            leaf_kind="file",
            require_single_link=True,
        )
        generations = read_json(generation_path)
        _require_fingerprint(
            generations,
            "generation_set_sha256",
            "content repair continuation predecessor generations",
        )
        bundle_path = require_contained_path(
            repair_bundle_path.resolve(),
            run,
            purpose="content repair continuation bundle",
            error_type=ContractError,
            leaf_kind="file",
            require_single_link=True,
        )
        repair_authority = PrecompileQualityProvider(
            self.project_root
        ).preflight_repair_authority(
            predecessor_workspace_root=predecessor_workspace,
            failure_authority_path=authority_path,
            repair_bundle_path=bundle_path,
            actual_write_set=actual_write_set,
            disposition_path=disposition_path,
        )
        authority = read_json(authority_path)
        predecessor_inventory = read_json(Path(predecessor_promotion["inventory_path"]))
        if (
            authority.get("generation_set_sha256")
            != generations.get("generation_set_sha256")
            or authority.get("inventory_sha256")
            != predecessor_inventory.get("inventory_sha256")
            or generations.get("generation_set_sha256")
            != predecessor_promotion.get("generation_set_sha256")
            or sha256_file(generation_path)
            != predecessor_promotion.get("generation_set_file_sha256")
        ):
            raise ContractError(
                "content repair continuation predecessor generation drifted",
                data={
                    "first_failing_gate": "content_repair_continuation_predecessor",
                    "error_code": "runtime_refresh_continuation_generation_drift",
                },
            )
        predecessor_sequence = repair_authority["predecessor_sequence"]
        disposition = (
            read_json(disposition_path.resolve())
            if disposition_path is not None
            else None
        )
        if repair_authority["kind"] == "contract_gap_brief" and (
            disposition.get("runtime_refresh_operation_id") != expected_operation_id
            or disposition.get("runtime_policy_sha256")
            != handoff.get("runtime_policy_sha256")
            or disposition.get("predecessor_sequence") != predecessor_sequence
        ):
            raise ContractError(
                "content repair continuation disposition is absent or stale",
                data={
                    "first_failing_gate": "content_repair_continuation_disposition",
                    "error_code": "runtime_refresh_continuation_disposition_invalid",
                },
            )
        if (
            sha256_file(run / "workflow/compile-runtime-policy.json")
            != handoff.get("runtime_policy_sha256")
        ):
            raise ContractError(
                "content repair continuation Runtime Policy drifted",
                data={
                    "first_failing_gate": "content_repair_continuation_predecessor",
                    "error_code": "runtime_refresh_continuation_policy_drift",
                },
            )
        authorization_sha256 = (
            disposition["disposition_sha256"]
            if disposition is not None
            else repair_authority["sha256"]
        )
        return {
            "decision": (
                disposition["decision"]
                if disposition is not None
                else "semantic_failure_repair"
            ),
            "approved_at": disposition.get("approved_at") if disposition else None,
            "approval_reference": (
                disposition.get("approval_reference") if disposition else None
            ),
            "authorization_sha256": authorization_sha256,
            "disposition_path": (
                str(disposition_path.resolve()) if disposition_path else None
            ),
            "disposition_sha256": (
                disposition["disposition_sha256"] if disposition else None
            ),
            "failure_authority_kind": repair_authority["kind"],
            "failure_authority_path": str(authority_path),
            "failure_authority_sha256": repair_authority["sha256"],
            "authorized_contract_gap_ids": sorted(
                item.get("gap_id") for item in authority.get("contract_gaps", [])
            ),
            "authorized_failure_keys": sorted(
                f"{item['owner']}:{item['result_key']}"
                for item in repair_authority["failure_set"]
            ),
            "allowed_write_set": actual_write_set,
            "runtime_refresh_operation_id": expected_operation_id,
            "repair_bundle_path": str(bundle_path),
            "repair_bundle_sha256": sha256_file(bundle_path),
            "generation_set_sha256": generations["generation_set_sha256"],
            "generation_set_path": str(generation_path),
            "generation_set_file_sha256": sha256_file(generation_path),
            "runtime_policy_sha256": handoff["runtime_policy_sha256"],
            "predecessor_sequence": predecessor_sequence,
            "semantic_attempt_budget_consumed": repair_authority[
                "semantic_attempt_budget_consumed"
            ],
            "production_state_sha256": sha256_file(
                run / "workflow/production-state.json"
            ),
            "compile_manifest_sha256": sha256_file(
                run / "workflow/compile-manifest.json"
            ),
            "allow_generation_advance": True,
        }

    def bind_content_repair_promotion(
        self,
        *,
        run_dir: Path,
        expected_operation_id: str,
        workspace_root: Path,
        generation_set_path: Path,
        inventory_path: Path,
        semantic_dependencies_path: Path,
        disposition_path: Path | None = None,
        predecessor_contract_gap_brief_path: Path | None = None,
        failure_authority_path: Path | None = None,
        repair_bundle_path: Path | None = None,
        actual_write_set: list[str] | None = None,
        preflight_authorization: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        run = run_dir.resolve()
        active_path = run / "workflow/runtime-refresh-active.json"
        journal = read_json(active_path)
        _require_fingerprint(journal, "journal_sha256", "Compile Runtime refresh journal")
        handoff = journal.get("content_repair_handoff")
        if journal.get("operation_id") != expected_operation_id or not isinstance(handoff, dict):
            raise ContractError("content repair promotion lacks its runtime handoff")
        _require_fingerprint(handoff, "handoff_sha256", "content repair handoff")
        if journal.get("state") == "superseded_by_content_repair":
            self.validate_retained_content_repair_closure(
                run_dir=run, journal=journal
            )
            return handoff
        generation_set = read_json(generation_set_path.resolve())
        bindings = {
            "workspace_root": str(workspace_root.resolve()),
            "generation_set_path": str(generation_set_path.resolve()),
            "generation_set_sha256": generation_set["generation_set_sha256"],
            "generation_set_file_sha256": sha256_file(generation_set_path.resolve()),
            "inventory_path": str(inventory_path.resolve()),
            "semantic_dependencies_path": str(semantic_dependencies_path.resolve()),
        }
        if handoff.get("state") == "promotion_ready":
            supplied_failure_authority = (
                failure_authority_path or predecessor_contract_gap_brief_path
            )
            if handoff.get("promotion") == bindings:
                refresh = handoff.get("promotion_refresh")
                if refresh is None:
                    return handoff
                supplied_disposition = (
                    read_json(disposition_path.resolve())
                    if disposition_path is not None
                    else None
                )
                recorded_authority_path = refresh.get(
                    "failure_authority_path",
                    refresh.get("predecessor_contract_gap_brief_path"),
                )
                if (
                    (
                        supplied_disposition.get("disposition_sha256")
                        if supplied_disposition is not None
                        else None
                    )
                    == refresh.get("disposition_sha256")
                    and supplied_failure_authority is not None
                    and str(supplied_failure_authority.resolve())
                    == recorded_authority_path
                    and repair_bundle_path is not None
                    and str(repair_bundle_path.resolve())
                    == refresh.get("repair_bundle_path")
                    and sha256_file(repair_bundle_path.resolve())
                    == refresh.get("repair_bundle_sha256")
                ):
                    return handoff
                raise ContractError(
                    "content repair continuation replay identity changed",
                    data={
                        "first_failing_gate": "content_repair_continuation_replay",
                        "error_code": "runtime_refresh_continuation_replay_identity_changed",
                    },
                )
            if supplied_failure_authority is None:
                raise ContractError(
                    "content repair continuation requires its failure authority",
                    data={
                        "first_failing_gate": "content_repair_continuation_predecessor",
                        "error_code": "runtime_refresh_continuation_authority_required",
                    },
                )
            authorization = preflight_authorization or self.preflight_repair_continuation(
                run_dir=run,
                expected_operation_id=expected_operation_id,
                repair_bundle_path=(
                    repair_bundle_path
                    if repair_bundle_path is not None
                    else Path(handoff["repair_bundle_path"])
                ),
                disposition_path=disposition_path,
                failure_authority_path=supplied_failure_authority,
                successor_workspace_root=workspace_root,
                actual_write_set=actual_write_set,
            )
            if authorization.get("allow_generation_advance") is not True:
                raise ContractError("content repair continuation authorization is invalid")
            old_promotion = handoff["promotion"]
            if workspace_root.resolve() == Path(old_promotion["workspace_root"]):
                raise ContractError(
                    "content repair promotion refresh requires a fresh workspace",
                    data={
                        "first_failing_gate": "content_repair_promotion_refresh_successor",
                        "error_code": "runtime_refresh_promotion_refresh_workspace_reused",
                    },
                )
            _require_fingerprint(
                generation_set,
                "generation_set_sha256",
                "content repair successor Artifact Generation set",
            )
            if (workspace_root.resolve() / "precompile-text-seal.json").exists():
                raise ContractError(
                    "content repair promotion refresh successor already has a Seal",
                    data={
                        "first_failing_gate": "content_repair_promotion_refresh_successor",
                        "error_code": "runtime_refresh_promotion_refresh_successor_sealed",
                    },
                )
            retained_promotions = handoff.get("retained_prior_promotions", [])
            if not isinstance(retained_promotions, list):
                raise ContractError("content repair retained promotion history is invalid")
            handoff["retained_prior_promotions"] = [
                *deepcopy(retained_promotions),
                deepcopy(old_promotion),
            ]
            if handoff.get("promotion_refresh") is not None:
                retained_refreshes = handoff.get("retained_promotion_refreshes", [])
                if not isinstance(retained_refreshes, list):
                    raise ContractError("content repair retained disposition history is invalid")
                handoff["retained_promotion_refreshes"] = [
                    *deepcopy(retained_refreshes),
                    deepcopy(handoff["promotion_refresh"]),
                ]
            handoff["promotion_refresh"] = authorization
            current_bundle = repair_bundle_path.resolve()
            handoff["repair_bundle_path"] = str(current_bundle)
            handoff["repair_bundle_sha256"] = sha256_file(current_bundle)
        handoff["state"] = "promotion_ready"
        handoff["promotion"] = bindings
        handoff["handoff_sha256"] = _fingerprint(handoff, "handoff_sha256")
        journal["content_repair_handoff"] = handoff
        journal["journal_sha256"] = _fingerprint(journal, "journal_sha256")
        write_json_atomic(active_path, journal)
        return handoff

    def supersede_for_content_repair(
        self, *, workspace_root: Path, fault_point: str | None = None
    ) -> dict[str, Any] | None:
        if fault_point is not None and fault_point not in CONTENT_REPAIR_HANDOFF_FAULT_POINTS:
            raise ContractError(f"unsupported content repair handoff fault point: {fault_point}")
        workspace = workspace_root.resolve()
        run = next(
            (candidate for candidate in workspace.parents if (candidate / "workflow/run.json").is_file()),
            None,
        )
        if run is None:
            return None
        active_path = run / "workflow/runtime-refresh-active.json"
        if not active_path.is_file():
            return None
        journal = read_json(active_path)
        _require_fingerprint(journal, "journal_sha256", "Compile Runtime refresh journal")
        handoff = journal.get("content_repair_handoff")
        if not isinstance(handoff, dict) or handoff.get("promotion", {}).get("workspace_root") != str(workspace):
            return None
        if journal.get("state") == "superseded_by_content_repair":
            self.validate_retained_content_repair_closure(
                run_dir=run, journal=journal
            )
            return handoff
        _require_fingerprint(handoff, "handoff_sha256", "content repair handoff")
        if journal.get("state") != "precompile_refresh_required" or handoff.get("state") != "promotion_ready":
            raise ContractError("content repair runtime handoff is not ready for supersession")
        precompile = PrecompileQualityProvider(self.project_root).assess_current_seal(workspace_root=workspace)
        if precompile.get("classification") != "precompile_seal_reused":
            raise ContractError("content repair supersession requires a current passing Seal")
        if fault_point == "after_seal_before_runtime_supersession":
            raise RuntimeRefreshFault(fault_point)
        generation_path = Path(handoff["promotion"]["generation_set_path"])
        if sha256_file(generation_path) != handoff["promotion"].get(
            "generation_set_file_sha256"
        ):
            raise ContractError(
                "content repair Artifact Generation file binding is stale",
                data={
                    "first_failing_gate": "content_repair_generation_file_binding",
                    "error_code": "runtime_refresh_handoff_generation_file_drift",
                },
            )
        generations = read_json(generation_path)
        if generations.get("generation_set_sha256") != _fingerprint(
            generations, "generation_set_sha256"
        ):
            raise ContractError(
                "content repair Artifact Generation fingerprint is stale",
                data={
                    "first_failing_gate": "content_repair_generation_fingerprint",
                    "error_code": "runtime_refresh_handoff_generation_fingerprint_stale",
                },
            )
        if generations["generation_set_sha256"] != handoff["promotion"].get(
            "generation_set_sha256"
        ):
            raise ContractError(
                "content repair Artifact Generation handoff binding is stale",
                data={
                    "first_failing_gate": "content_repair_generation_handoff_binding",
                    "error_code": "runtime_refresh_handoff_generation_identity_changed",
                },
            )
        if generations.get("artifacts") != precompile.get("artifact_generations"):
            raise ContractError(
                "content repair Artifact Generations differ from the current Seal",
                data={
                    "first_failing_gate": "content_repair_seal_generation_binding",
                    "error_code": "runtime_refresh_handoff_seal_generation_mismatch",
                },
            )
        production = ContentProduction(VideoWorkflowKernel(run.parent))
        authority = production.require_current_diagnostic_compile_authority(
            run, content_repair_handoff_operation_id=journal["operation_id"]
        )
        compile_manifest = read_json(run / "workflow/compile-manifest.json")
        entries_by_id = {item["logical_id"]: item for item in compile_manifest["entries"]}
        entries = []
        for generation in generations["artifacts"]:
            current = entries_by_id.get(generation["logical_id"])
            if current is None or (current["generation"], current["sha256"]) != (generation["generation"], generation["sha256"]):
                raise ContractError("content repair successor generations are not current")
            source = require_contained_path(
                run / current["source_path"],
                run,
                purpose="content repair successor Final Compile source",
                error_type=ContractError,
                allow_missing=True,
            )
            entries.append(
                {
                    **{
                        key: current[key]
                        for key in (
                            "logical_id",
                            "generation",
                            "sha256",
                            "staging_path",
                        )
                    },
                    "source_path": str(source),
                }
            )
        policy_path = Path(authority["runtime_policy_path"])
        policy = read_json(policy_path)
        report = read_json(run / "review/latex/diagnostic-compile-report.json")
        manifest = {
            "schema_name": "final-compile-manifest",
            "schema_version": "1.0.0",
            "activation_status": "target_only",
            "mode": "final",
            "precompile_text_seal_sha256": precompile["seal_sha256"],
            "entries": entries,
            "approved_runtime_inputs": self._approved_runtime_inputs(report, policy),
            "runtime_policy": {"path": str(policy_path.resolve()), "sha256": sha256_file(policy_path)},
        }
        manifest["manifest_sha256"] = _fingerprint(manifest, "manifest_sha256")
        self.quality.validate("final-compile-manifest", manifest)
        output_path = run / "待删除/runtime-refresh" / journal["operation_id"] / "content-repair/final-compile-manifest.json"
        if output_path.exists() and output_path.read_bytes() != canonical_json_bytes(manifest):
            raise ContractError("content repair successor Final Compile Manifest conflicts")
        write_json_atomic(output_path, manifest)
        handoff["state"] = "superseded"
        handoff["seal_sha256"] = precompile["seal_sha256"]
        handoff["successor_final_compile_manifest_path"] = str(output_path.resolve())
        handoff["successor_final_compile_manifest_sha256"] = sha256_file(output_path)
        handoff["successor_diagnostic_report_sha256"] = sha256_file(run / "review/latex/diagnostic-compile-report.json")
        handoff["handoff_sha256"] = _fingerprint(handoff, "handoff_sha256")
        journal["state"] = "superseded_by_content_repair"
        journal["content_repair_handoff"] = handoff
        journal["successor_final_compile_manifest_path"] = str(output_path.resolve())
        journal["successor_final_compile_manifest_sha256"] = sha256_file(output_path)
        journal["journal_sha256"] = _fingerprint(journal, "journal_sha256")
        write_json_atomic(active_path, journal)
        return handoff

    @staticmethod
    def _reject_terminal(message: str, gate: str, code: str) -> None:
        raise ContractError(
            message,
            data={"first_failing_gate": gate, "error_code": code},
        )

    def validate_retained_content_repair_closure(
        self, *, run_dir: Path, journal: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Authenticate one immutable completed Runtime-to-content closure."""
        run = run_dir.resolve()
        retained = journal or read_json(
            run / "workflow/runtime-refresh-active.json"
        )
        try:
            _require_fingerprint(
                retained, "journal_sha256", "Compile Runtime refresh journal"
            )
        except ContractError:
            self._reject_terminal(
                "terminal Compile Runtime journal fingerprint is stale",
                "content_repair_terminal_journal",
                "runtime_refresh_terminal_journal_fingerprint_stale",
            )
        if retained.get("state") != "superseded_by_content_repair":
            self._reject_terminal(
                "Compile Runtime content repair is not terminal",
                "content_repair_terminal_state",
                "runtime_refresh_terminal_state_invalid",
            )
        handoff = retained.get("content_repair_handoff")
        if not isinstance(handoff, dict):
            self._reject_terminal(
                "terminal Compile Runtime closure lacks its handoff",
                "content_repair_terminal_handoff",
                "runtime_refresh_terminal_handoff_missing",
            )
        try:
            _require_fingerprint(handoff, "handoff_sha256", "content repair handoff")
        except ContractError:
            self._reject_terminal(
                "terminal content repair handoff fingerprint is stale",
                "content_repair_terminal_handoff",
                "runtime_refresh_terminal_handoff_fingerprint_stale",
            )
        if (
            handoff.get("state") != "superseded"
            or handoff.get("runtime_refresh_operation_id")
            != retained.get("operation_id")
            or handoff.get("runtime_policy_sha256")
            != retained.get("canonical_runtime_policy_sha256")
        ):
            self._reject_terminal(
                "terminal content repair handoff identity is stale",
                "content_repair_terminal_handoff",
                "runtime_refresh_terminal_handoff_identity_stale",
            )
        for path_field, sha_field in (
            ("repair_bundle_path", "repair_bundle_sha256"),
            (
                "predecessor_final_compile_manifest_path",
                "predecessor_final_compile_manifest_sha256",
            ),
        ):
            path = Path(str(handoff.get(path_field, "")))
            if not path.is_file() or sha256_file(path) != handoff.get(sha_field):
                self._reject_terminal(
                    "terminal content repair input binding is stale",
                    "content_repair_terminal_handoff",
                    "runtime_refresh_terminal_handoff_input_drift",
                )

        promotion = handoff.get("promotion")
        if not isinstance(promotion, dict):
            self._reject_terminal(
                "terminal content repair promotion is missing",
                "content_repair_terminal_promotion",
                "runtime_refresh_terminal_promotion_missing",
            )
        workspace = Path(str(promotion.get("workspace_root", "")))
        generation_path = Path(str(promotion.get("generation_set_path", "")))
        promotion_inventory_path = Path(str(promotion.get("inventory_path", "")))
        promotion_dependencies_path = Path(
            str(promotion.get("semantic_dependencies_path", ""))
        )
        report_path = workspace / "precompile-quality-report.json"
        seal_path = workspace / "precompile-text-seal.json"
        inventory_path = workspace / "reader-facing-text-inventory.json"
        dependencies_path = workspace / "semantic-dependencies.json"
        required_paths = (
            workspace,
            generation_path,
            promotion_inventory_path,
            promotion_dependencies_path,
            report_path,
            seal_path,
            inventory_path,
            dependencies_path,
        )
        if not workspace.is_dir() or any(
            not path.is_file() for path in required_paths[1:]
        ):
            self._reject_terminal(
                "terminal content repair promotion evidence is unavailable",
                "content_repair_terminal_promotion",
                "runtime_refresh_terminal_promotion_evidence_missing",
            )
        if sha256_file(generation_path) != promotion.get(
            "generation_set_file_sha256"
        ):
            self._reject_terminal(
                "terminal Artifact Generation file binding is stale",
                "content_repair_terminal_generation_binding",
                "runtime_refresh_terminal_generation_file_drift",
            )
        generations = read_json(generation_path)
        report = read_json(report_path)
        seal = read_json(seal_path)
        inventory = read_json(inventory_path)
        dependencies = read_json(dependencies_path)
        try:
            self.quality.validate("precompile-artifact-generation-set", generations)
            self.quality.validate("precompile-quality-report", report)
            self.quality.validate("precompile-text-seal", seal)
            self.quality.validate("reader-facing-text-inventory", inventory)
            self.quality.validate("precompile-semantic-dependencies", dependencies)
            _require_fingerprint(
                generations,
                "generation_set_sha256",
                "retained Artifact Generation set",
            )
            _require_fingerprint(report, "report_sha256", "retained PRE report")
            _require_fingerprint(seal, "seal_sha256", "retained PRE Seal")
            _require_fingerprint(
                inventory, "inventory_sha256", "retained reader inventory"
            )
            _require_fingerprint(
                dependencies,
                "dependencies_sha256",
                "retained semantic dependencies",
            )
        except ContractError:
            self._reject_terminal(
                "terminal content repair promotion evidence is invalid",
                "content_repair_terminal_promotion",
                "runtime_refresh_terminal_promotion_evidence_invalid",
            )
        bound_generation_path = (
            workspace
            / "seal-bindings"
            / seal["seal_sha256"]
            / "artifact-generations.json"
        )
        bound_inventory_path = (
            workspace
            / "seal-bindings"
            / seal["seal_sha256"]
            / "reader-facing-text-inventory.json"
        )
        if (
            not bound_generation_path.is_file()
            or not bound_inventory_path.is_file()
            or read_json(bound_generation_path).get("generation_set_sha256")
            != generations.get("generation_set_sha256")
            or read_json(bound_inventory_path).get("inventory_sha256")
            != inventory.get("inventory_sha256")
            or read_json(promotion_inventory_path).get("inventory_sha256")
            != inventory.get("inventory_sha256")
            or read_json(promotion_dependencies_path).get("dependencies_sha256")
            != dependencies.get("dependencies_sha256")
        ):
            self._reject_terminal(
                "terminal PRE retained inputs are stale",
                "content_repair_terminal_precompile_binding",
                "runtime_refresh_terminal_precompile_input_drift",
            )
        if (
            generations.get("generation_set_sha256")
            != promotion.get("generation_set_sha256")
            or report.get("overall_decision") != "pass"
            or report.get("contract_gaps")
            or report.get("generation_set_sha256")
            != generations.get("generation_set_sha256")
            or report.get("inventory_sha256") != inventory.get("inventory_sha256")
            or report.get("semantic_dependencies_sha256")
            != dependencies.get("dependencies_sha256")
            or seal.get("seal_sha256") != handoff.get("seal_sha256")
            or seal.get("precompile_quality_report_sha256")
            != report.get("report_sha256")
            or seal.get("generation_set_sha256")
            != generations.get("generation_set_sha256")
            or seal.get("inventory_sha256") != inventory.get("inventory_sha256")
            or seal.get("reader_text_set_sha256")
            != inventory.get("reader_text_set_sha256")
            or seal.get("semantic_dependencies_sha256")
            != dependencies.get("dependencies_sha256")
        ):
            self._reject_terminal(
                "terminal PRE report and Seal bindings are stale",
                "content_repair_terminal_precompile_binding",
                "runtime_refresh_terminal_precompile_binding_stale",
            )

        manifest_path = Path(
            str(handoff.get("successor_final_compile_manifest_path", ""))
        )
        if (
            not manifest_path.is_file()
            or sha256_file(manifest_path)
            != handoff.get("successor_final_compile_manifest_sha256")
        ):
            self._reject_terminal(
                "terminal successor Final Compile Manifest binding is stale",
                "content_repair_terminal_manifest_binding",
                "runtime_refresh_terminal_manifest_file_drift",
            )
        manifest = read_json(manifest_path)
        try:
            self.quality.validate("final-compile-manifest", manifest)
            _require_fingerprint(
                manifest, "manifest_sha256", "retained Final Compile Manifest"
            )
        except ContractError:
            self._reject_terminal(
                "terminal successor Final Compile Manifest is invalid",
                "content_repair_terminal_manifest_binding",
                "runtime_refresh_terminal_manifest_invalid",
            )
        manifest_bindings = {
            (item["logical_id"], item["generation"], item["sha256"])
            for item in manifest["entries"]
        }
        generation_bindings = {
            (item["logical_id"], item["generation"], item["sha256"])
            for item in generations["artifacts"]
        }
        if (
            manifest_bindings != generation_bindings
            or manifest.get("precompile_text_seal_sha256")
            != seal.get("seal_sha256")
            or manifest.get("runtime_policy", {}).get("sha256")
            != handoff.get("runtime_policy_sha256")
            or retained.get("successor_final_compile_manifest_path")
            != str(manifest_path.resolve())
            or retained.get("successor_final_compile_manifest_sha256")
            != handoff.get("successor_final_compile_manifest_sha256")
        ):
            self._reject_terminal(
                "terminal successor Final Compile Manifest authority is stale",
                "content_repair_terminal_manifest_binding",
                "runtime_refresh_terminal_manifest_binding_stale",
            )
        return {
            "classification": "runtime_content_repair_closure_retained",
            "operation_id": retained["operation_id"],
            "handoff_sha256": handoff["handoff_sha256"],
            "seal_sha256": seal["seal_sha256"],
            "manifest_sha256": manifest["manifest_sha256"],
        }

    @staticmethod
    def _approved_runtime_inputs(
        report: dict[str, Any], policy: dict[str, Any]
    ) -> list[dict[str, str]]:
        _require_fingerprint(policy, "policy_sha256", "Compile Runtime Policy")
        if report.get("runtime_policy_sha256") != policy["policy_sha256"]:
            raise ContractError(
                "Diagnostic Compile Report lacks the current Runtime Policy binding"
            )
        inventory = read_json(Path(policy["package_inventory"]["path"]))
        if (
            sha256_file(Path(policy["package_inventory"]["path"]))
            != policy["package_inventory"]["sha256"]
        ):
            raise ContractError("Compile Runtime package inventory is stale")
        registered_runtime = {
            str(Path(item["path"]).resolve()).casefold(): item["sha256"]
            for item in inventory["files"]
        }
        registered_fonts = {
            str(Path(item["path"]).resolve()).casefold(): item["sha256"]
            for item in policy["system_fonts"]
        }
        approved: dict[str, dict[str, str]] = {}
        for item in report["dependency_closure"]["inputs"]:
            if item["classification"] not in {
                "registered_runtime_dependency",
                "registered_system_font",
            }:
                continue
            path = Path(item["path"]).resolve()
            identity = str(path).casefold()
            registered = (
                registered_runtime
                if item["classification"] == "registered_runtime_dependency"
                else registered_fonts
            )
            authenticated_sha256 = registered.get(identity)
            if (
                authenticated_sha256 is None
                or not path.is_file()
                or sha256_file(path) != authenticated_sha256
            ):
                raise ContractError(
                    "diagnostic runtime input drifted before Final Compile binding"
                )
            candidate = {
                "path": str(path),
                "sha256": authenticated_sha256,
                "classification": item["classification"],
            }
            if identity in approved and approved[identity] != candidate:
                raise ContractError("diagnostic runtime input has conflicting identities")
            approved[identity] = candidate
        return [approved[key] for key in sorted(approved)]

    def _preflight_predecessor_manifest(
        self,
        path: Path,
        *,
        expected_runtime_policy_sha256: str,
    ) -> dict[str, Any]:
        resolved = path.resolve()
        if not resolved.is_file():
            raise ContractError("current Final Compile Manifest is unavailable")
        manifest = read_json(resolved)
        self.quality.validate("final-compile-manifest", manifest)
        _require_fingerprint(manifest, "manifest_sha256", "Final Compile Manifest")
        if (
            manifest.get("runtime_policy", {}).get("sha256")
            != expected_runtime_policy_sha256
        ):
            raise ContractError(
                "Final Compile Manifest lacks authorized predecessor Runtime Policy"
            )
        return manifest

    def _validate_committed(
        self,
        journal: dict[str, Any],
        production: ContentProduction,
        run: Path,
        *,
        allow_runtime_input_drift: bool = False,
    ) -> None:
        authority = production.require_current_diagnostic_compile_authority(run)
        policy_path = run / "workflow/compile-runtime-policy.json"
        report_path = Path(journal["successor_diagnostic_report_path"])
        manifest_path = Path(journal["successor_final_compile_manifest_path"])
        required_files = (policy_path, report_path, manifest_path)
        if any(not path.is_file() for path in required_files):
            raise ContractError("committed Compile Runtime refresh output is unavailable")
        policy = read_json(policy_path)
        production.kernel.contracts.validate("compile-runtime-policy", policy)
        _require_fingerprint(policy, "policy_sha256", "Compile Runtime Policy")
        inventory_path = Path(policy["package_inventory"]["path"])
        if (
            not inventory_path.is_file()
            or sha256_file(inventory_path)
            != policy["package_inventory"]["sha256"]
        ):
            raise ContractError("committed Compile Runtime inventory is stale")
        inventory = read_json(inventory_path)
        if not allow_runtime_input_drift:
            for item in inventory.get("files", []):
                path = Path(item["path"])
                if not path.is_file() or sha256_file(path) != item["sha256"]:
                    raise ContractError("committed Compile Runtime input is stale")
        if (
            authority["runtime_policy_sha256"]
            != journal["canonical_runtime_policy_sha256"]
            or sha256_file(policy_path) != journal["canonical_runtime_policy_sha256"]
            or sha256_file(report_path)
            != journal["successor_diagnostic_report_sha256"]
            or sha256_file(manifest_path)
            != journal["successor_final_compile_manifest_sha256"]
        ):
            raise ContractError("committed Compile Runtime refresh authority drifted")
        manifest = read_json(manifest_path)
        self.quality.validate("final-compile-manifest", manifest)
        _require_fingerprint(manifest, "manifest_sha256", "Final Compile Manifest")
        precompile = self._validate_precompile(
            Path(journal["precompile_workspace_root"])
        )
        if (
            precompile.get("classification") != "precompile_seal_reused"
            or precompile.get("seal_sha256")
            != manifest.get("precompile_text_seal_sha256")
            or manifest.get("runtime_policy", {}).get("sha256")
            != journal["canonical_runtime_policy_sha256"]
        ):
            raise ContractError("committed Compile Runtime refresh binding is stale")
        manifest_bindings = {
            (item["logical_id"], item["generation"], item["sha256"])
            for item in manifest["entries"]
        }
        precompile_bindings = {
            (item["logical_id"], item["generation"], item["sha256"])
            for item in precompile["artifact_generations"]
        }
        if manifest_bindings != precompile_bindings:
            raise ContractError("committed Compile Runtime content binding is stale")

    @staticmethod
    def _runtime_inputs(report: dict[str, Any]) -> list[Path]:
        paths = {
            Path(item["path"]).resolve()
            for item in report["dependency_closure"]["inputs"]
            if item["classification"] == "registered_runtime_dependency"
        }
        if any(not path.is_file() for path in paths):
            raise ContractError("recorded Compile Runtime input is unavailable")
        return sorted(paths, key=lambda path: str(path).casefold())

    @staticmethod
    def _restore_predecessor_production(journal: dict[str, Any]) -> None:
        """Recover a prepared operation from any partial canonical compile write."""
        for item in journal["predecessor_evidence"].values():
            archive = Path(item["archive_path"])
            target = Path(item["source_path"])
            if sha256_file(archive) != item["sha256"]:
                raise ContractError("Compile Runtime predecessor archive drifted")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(archive, target)
            if sha256_file(target) != item["sha256"]:
                raise ContractError("Compile Runtime predecessor recovery failed")

    @staticmethod
    def _drifted_inventory_entries(policy: dict[str, Any]) -> list[dict[str, str]]:
        inventory = read_json(Path(policy["package_inventory"]["path"]))
        drifted = []
        for item in inventory.get("files", []):
            path = Path(item.get("path", "")).resolve()
            actual = sha256_file(path) if path.is_file() else None
            if actual != item.get("sha256"):
                drifted.append(
                    {
                        "path": str(path),
                        "expected_sha256": str(item.get("sha256")),
                        "actual_sha256": actual or "missing",
                    }
                )
        return drifted

    def _validate_precompile(self, root: Path | None) -> dict[str, Any]:
        if root is None:
            return {
                "classification": "precompile_refresh_required",
                "suggested_workspace_root": None,
            }
        return PrecompileQualityProvider(self.project_root).assess_current_seal(
            workspace_root=root
        )

    @staticmethod
    def _result(journal: dict[str, Any]) -> dict[str, Any]:
        classification = (
            "compile_runtime_refresh_complete"
            if journal["state"] == "committed"
            else (
                "compile_runtime_refresh_superseded_by_content_repair"
                if journal["state"] == "superseded_by_content_repair"
                else "precompile_refresh_required"
            )
        )
        return {
            "classification": classification,
            "operation_id": journal["operation_id"],
            "state": journal["state"],
            "runtime_inventory_path": journal["runtime_inventory_path"],
            "runtime_policy_path": journal.get(
                "canonical_runtime_policy_path",
                journal["successor_runtime_policy_path"],
            ),
            "drifted_inputs": journal["drifted_inputs"],
            "precompile": journal.get("precompile"),
            "successor_final_compile_manifest_path": journal.get(
                "successor_final_compile_manifest_path"
            ),
        }
