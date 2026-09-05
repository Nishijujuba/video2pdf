from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any

from .content_production import ContentProduction
from .delivery_quality import DeliveryQualityRegistry
from .errors import ContractError, RuntimeRefreshFault
from .kernel import VideoWorkflowKernel
from .precompile_quality import PrecompileQualityProvider
from .utils import canonical_json_bytes, read_json, sha256_file, write_json_atomic


RUNTIME_REFRESH_FAULT_POINTS = {
    "before_production_state_publish",
    "after_diagnostic_publish",
}


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
        predecessor_manifest = self._preflight_predecessor_manifest(
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
        inventory = read_json(Path(policy["package_inventory"]["path"]))
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
            recorded_sha256 = item.get("sha256")
            if (
                identity not in registered
                or registered[identity] != recorded_sha256
                or not path.is_file()
                or sha256_file(path) != recorded_sha256
            ):
                raise ContractError(
                    "diagnostic runtime input drifted before Final Compile binding"
                )
            candidate = {
                "path": str(path),
                "sha256": recorded_sha256,
                "classification": item["classification"],
            }
            if identity in approved and approved[identity] != candidate:
                raise ContractError("diagnostic runtime input has conflicting identities")
            approved[identity] = candidate
        successor_manifest["approved_runtime_inputs"] = [
            approved[key] for key in sorted(approved)
        ]
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
            else "precompile_refresh_required"
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
