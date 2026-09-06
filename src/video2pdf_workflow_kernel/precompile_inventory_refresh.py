from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any
import uuid

from .content_production import ContentProduction
from .delivery_quality import DeliveryQualityRegistry
from .errors import ContractError
from .kernel import VideoWorkflowKernel
from .precompile_quality import PrecompileQualityProvider
from .precompile_repair_promotion import PrecompileRepairPromotionProvider
from .utils import canonical_json_bytes, read_json, sha256_file, write_json_atomic


_GENERATED_TITLE_FAILURE = re.compile(
    r"^generated style title occurrence is absent or ambiguous: ([A-Za-z0-9._-]+)$"
)


def _fingerprint_without(value: dict[str, Any], field: str) -> str:
    return hashlib.sha256(
        canonical_json_bytes({key: item for key, item in value.items() if key != field})
    ).hexdigest()


def _require_fingerprint(
    value: dict[str, Any], field: str, label: str, gate: str, code: str
) -> None:
    if value.get(field) != _fingerprint_without(value, field):
        raise ContractError(
            f"{label} {field} is stale or invalid",
            data={"first_failing_gate": gate, "error_code": code},
        )


def _reject(message: str, gate: str, code: str, **data: Any) -> None:
    raise ContractError(
        message,
        data={"first_failing_gate": gate, "error_code": code, **data},
    )


def _option(argv: list[Any], name: str) -> str | None:
    positions = [index for index, value in enumerate(argv) if value == name]
    if len(positions) != 1 or positions[0] + 1 >= len(argv):
        return None
    value = argv[positions[0] + 1]
    return value if isinstance(value, str) and value else None


def _immutable_json(path: Path, value: dict[str, Any], message: str) -> None:
    payload = canonical_json_bytes(value)
    if path.exists():
        if path.read_bytes() != payload:
            _reject(
                message,
                "precompile_inventory_refresh_publication",
                "precompile_inventory_refresh_publication_conflict",
            )
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, value)


def _immutable_bytes(path: Path, payload: bytes, message: str) -> None:
    if path.exists():
        if path.read_bytes() != payload:
            _reject(
                message,
                "precompile_inventory_refresh_custody",
                "precompile_inventory_refresh_custody_conflict",
            )
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.refresh-new")
    with temporary.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


class PrecompileInventoryRefreshProvider:
    """Refresh one derived inventory after a retained downstream compile failure."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.delivery_quality = DeliveryQualityRegistry(self.project_root)

    def refresh(
        self,
        *,
        run_dir: Path,
        predecessor_workspace_root: Path,
        workspace_root: Path,
        compile_manifest_path: Path,
        failed_command_run_dir: Path,
        approval_reference: str,
        prepared_at: str,
    ) -> dict[str, Any]:
        run = run_dir.resolve()
        predecessor = predecessor_workspace_root.resolve()
        successor = workspace_root.resolve()
        manifest_path = compile_manifest_path.resolve()
        failed_root = failed_command_run_dir.resolve()
        gate = "precompile_inventory_refresh_admission"
        if not approval_reference.strip() or not prepared_at:
            _reject(
                "inventory refresh requires an approved repair reference and preparation time",
                gate,
                "precompile_inventory_refresh_approval_missing",
            )
        for label, path in (
            ("predecessor workspace", predecessor),
            ("successor workspace", successor),
            ("Compile Manifest", manifest_path),
        ):
            if not path.is_relative_to(run):
                _reject(
                    f"{label} is outside the Run",
                    gate,
                    "precompile_inventory_refresh_path_outside_run",
                )
        if not failed_root.is_relative_to(self.project_root):
            _reject(
                "failed command evidence is outside the project",
                gate,
                "precompile_inventory_refresh_path_outside_project",
            )
        if successor == predecessor or predecessor in successor.parents:
            _reject(
                "inventory refresh successor conflicts with its predecessor",
                gate,
                "precompile_inventory_refresh_successor_invalid",
            )
        run_record_path = run / "workflow/run.json"
        run_record = read_json(run_record_path)
        kernel = VideoWorkflowKernel(run.parent)
        try:
            kernel.contracts.validate("run-record", run_record)
        except ContractError:
            _reject(
                "inventory refresh Run authority is invalid",
                gate,
                "precompile_inventory_refresh_run_invalid",
            )
        if Path(str(run_record.get("output_path", ""))).resolve() != run:
            _reject(
                "inventory refresh Run authority is invalid",
                gate,
                "precompile_inventory_refresh_run_invalid",
            )

        quality = PrecompileQualityProvider(self.project_root)
        seal_assessment = quality.assess_current_seal(workspace_root=predecessor)
        if seal_assessment.get("classification") != "precompile_seal_reused":
            _reject(
                "inventory refresh requires a current passing sealed predecessor",
                "precompile_inventory_refresh_predecessor",
                "precompile_inventory_refresh_predecessor_stale",
            )
        seal = read_json(predecessor / "precompile-text-seal.json")
        generations_path = predecessor / "artifact-generations.json"
        inventory_path = predecessor / "reader-facing-text-inventory.json"
        dependencies_path = predecessor / "semantic-dependencies.json"
        attempt_path = predecessor / "repair-attempt.json"
        generations = read_json(generations_path)
        inventory = read_json(inventory_path)
        dependencies = read_json(dependencies_path)
        predecessor_attempt = read_json(attempt_path)
        _require_fingerprint(
            predecessor_attempt,
            "attempt_sha256",
            "predecessor repair Attempt",
            "precompile_inventory_refresh_predecessor",
            "precompile_inventory_refresh_predecessor_attempt_stale",
        )
        if (
            predecessor_attempt.get("repaired_generation_set_sha256")
            != generations.get("generation_set_sha256")
            or predecessor_attempt.get("repaired_inventory_sha256")
            != inventory.get("inventory_sha256")
            or not isinstance(predecessor_attempt.get("repair_sequence"), int)
            or predecessor_attempt["repair_sequence"] < 1
            or not isinstance(predecessor_attempt.get("semantic_attempt_number"), int)
            or predecessor_attempt["semantic_attempt_number"] < 0
        ):
            _reject(
                "predecessor repair history is stale",
                "precompile_inventory_refresh_predecessor",
                "precompile_inventory_refresh_predecessor_history_stale",
            )

        try:
            ContentProduction(kernel).require_current_diagnostic_compile_authority(run)
        except ContractError:
            _reject(
                "inventory refresh requires complete current Production authority",
                "precompile_inventory_refresh_current_production",
                "precompile_inventory_refresh_current_production_incomplete",
            )
        current_compile_manifest = read_json(
            run / "workflow/compile-manifest.json"
        )
        current_production_bindings = {
            (item.get("logical_id"), item.get("generation"), item.get("sha256"))
            for item in current_compile_manifest.get("entries", [])
        }
        predecessor_bindings = {
            (item.get("logical_id"), item.get("generation"), item.get("sha256"))
            for item in generations.get("artifacts", [])
        }
        if current_production_bindings != predecessor_bindings:
            _reject(
                "predecessor generations do not bind current Production",
                "precompile_inventory_refresh_current_production",
                "precompile_inventory_refresh_current_production_mismatch",
            )

        operation_id = hashlib.sha256(
            canonical_json_bytes(
                {
                    "run_id": run_record["run_id"],
                    "predecessor_seal_sha256": seal["seal_sha256"],
                    "approval_reference": approval_reference,
                }
            )
        ).hexdigest()[:24]
        authority_root = run / "review/precompile/inventory-refresh"
        claim_path = authority_root / "claims" / f"{seal['seal_sha256']}.json"
        requested_claim = {
            "schema_name": "precompile-inventory-refresh-claim",
            "schema_version": "1.0.0",
            "operation_id": operation_id,
            "run_id": run_record["run_id"],
            "predecessor_workspace_root": str(predecessor),
            "predecessor_seal_sha256": seal["seal_sha256"],
            "successor_workspace_root": str(successor),
            "compile_manifest_source_path": str(manifest_path),
            "failed_command_source_root": str(failed_root),
            "approval_reference": approval_reference,
            "prepared_at": prepared_at,
        }
        requested_claim["claim_sha256"] = _fingerprint_without(
            requested_claim, "claim_sha256"
        )
        if claim_path.is_file():
            claim = read_json(claim_path)
            _require_fingerprint(
                claim,
                "claim_sha256",
                "inventory refresh claim",
                "precompile_inventory_refresh_successor_claim",
                "precompile_inventory_refresh_claim_stale",
            )
            if claim != requested_claim:
                _reject(
                    "this predecessor already owns a different inventory refresh successor",
                    "precompile_inventory_refresh_successor_claim",
                    "precompile_inventory_refresh_competing_successor",
                )
            refresh_path = successor / "precompile-inventory-refresh.json"
            if refresh_path.is_file():
                refresh = read_json(refresh_path)
                _require_fingerprint(
                    refresh,
                    "refresh_sha256",
                    "Precompile Inventory Refresh",
                    "precompile_inventory_refresh_replay",
                    "precompile_inventory_refresh_replay_stale",
                )
                self.delivery_quality.validate("precompile-inventory-refresh", refresh)
                self._validate_replay(refresh=refresh, successor=successor)
                return self._result(refresh, successor, replayed=True)

        manifest = read_json(manifest_path)
        self.delivery_quality.validate("final-compile-manifest", manifest)
        _require_fingerprint(
            manifest,
            "manifest_sha256",
            "Final Compile Manifest",
            "precompile_inventory_refresh_manifest",
            "precompile_inventory_refresh_manifest_stale",
        )
        generation_by_id = {
            item["logical_id"]: item for item in generations.get("artifacts", [])
        }
        manifest_by_id = {
            item.get("logical_id"): item for item in manifest.get("entries", [])
        }
        if (
            manifest.get("precompile_text_seal_sha256") != seal.get("seal_sha256")
            or set(manifest_by_id) != set(generation_by_id)
            or any(
                entry.get("generation") != generation_by_id[logical_id].get("generation")
                or entry.get("sha256") != generation_by_id[logical_id].get("sha256")
                for logical_id, entry in manifest_by_id.items()
            )
        ):
            _reject(
                "Final Compile Manifest does not bind the predecessor Production set",
                "precompile_inventory_refresh_manifest",
                "precompile_inventory_refresh_manifest_binding_mismatch",
            )
        for entry in manifest["entries"]:
            source = Path(entry["source_path"]).resolve()
            if (
                not source.is_relative_to(run)
                or not source.is_file()
                or sha256_file(source) != entry["sha256"]
            ):
                _reject(
                    "Final Compile Manifest Production source is stale",
                    "precompile_inventory_refresh_production",
                    "precompile_inventory_refresh_production_stale",
                    logical_id=entry["logical_id"],
                )

        failure = self._validate_failure(
            failed_root=failed_root,
            run=run,
            predecessor=predecessor,
            manifest_path=manifest_path,
            inventory=inventory,
        )
        successor_inventory = PrecompileRepairPromotionProvider._derive_successor_inventory(
            run_dir=run,
            compile_manifest=manifest,
            generations=generations,
            candidate=inventory,
            operation_id=operation_id,
        )
        prior_by_id = {item["item_id"]: item for item in inventory["items"]}
        successor_by_id = {
            item["item_id"]: item for item in successor_inventory["items"]
        }
        added_ids = set(successor_by_id) - set(prior_by_id)
        removed_ids = set(prior_by_id) - set(successor_by_id)
        non_generated_ids = {
            item_id
            for item_id, item in prior_by_id.items()
            if item.get("representation") != "declared_generated_text"
        }
        if (
            added_ids
            or any(
                prior_by_id[item_id].get("representation")
                != "declared_generated_text"
                for item_id in removed_ids
            )
            or any(
                successor_by_id.get(item_id) != prior_by_id[item_id]
                for item_id in non_generated_ids
            )
        ):
            _reject(
                "inventory refresh changed a non-generated declaration or added membership",
                "precompile_inventory_refresh_derivation",
                "precompile_inventory_refresh_membership_changed",
            )
        changed_ids = sorted(
            item_id
            for item_id in prior_by_id
            if item_id not in successor_by_id
            or prior_by_id[item_id] != successor_by_id[item_id]
        )
        if (
            not changed_ids
            or failure["generated_item_id"] not in changed_ids
            or any(
                prior_by_id[item_id].get("representation")
                != "declared_generated_text"
                for item_id in changed_ids
            )
        ):
            _reject(
                "inventory refresh did not isolate changes to generated declarations",
                "precompile_inventory_refresh_derivation",
                "precompile_inventory_refresh_derivation_scope_invalid",
            )

        custody_root = authority_root / "operations" / operation_id / "failure-evidence"
        custody_sources = {
            "status.json": failed_root / "status.json",
            "command.json": failed_root / "command.json",
            "stdout.log": failed_root / "stdout.log",
            "exit-code.txt": failed_root / "exit-code.txt",
            "adapter-stderr.log": Path(failure["adapter_stderr_path"]),
            "compile-manifest.json": manifest_path,
        }
        for name, source in custody_sources.items():
            _immutable_bytes(
                custody_root / name,
                source.read_bytes(),
                f"inventory refresh custody artifact changed: {name}",
            )
        custody_bindings = {
            name: {"path": str((custody_root / name).resolve()), "sha256": sha256_file(custody_root / name)}
            for name in sorted(custody_sources)
        }
        _immutable_json(
            claim_path,
            requested_claim,
            "inventory refresh Run claim changed",
        )
        prepared = quality.prepare(
            workspace_root=successor,
            inventory_path=self._stage_derived_inventory(
                authority_root, operation_id, successor_inventory
            ),
            artifact_generations_path=generations_path,
            semantic_dependencies_path=dependencies_path,
            prepared_at=prepared_at,
        )
        task_ids = [read_json(Path(path))["task_id"] for path in prepared["skeleton_paths"]]
        technical_authority = {
            "kind": "downstream_final_compile_failure",
            "operation_id": operation_id,
            "generated_item_id": failure["generated_item_id"],
            "custody_root": str(custody_root.resolve()),
            "bindings": custody_bindings,
            "approval_reference": approval_reference,
        }
        attempt = {
            "schema_name": "precompile-repair-attempt",
            "schema_version": "1.0.0",
            "activation_status": "target_only",
            "repair_attempt_number": max(
                1, predecessor_attempt["semantic_attempt_number"]
            ),
            "prepared_at": prepared_at,
            "predecessor_failure_authority": technical_authority,
            "predecessor_generation_set_sha256": generations[
                "generation_set_sha256"
            ],
            "repaired_generation_set_sha256": generations[
                "generation_set_sha256"
            ],
            "repaired_inventory_sha256": successor_inventory["inventory_sha256"],
            "advanced_logical_ids": [],
            "advanced_semantic_input_ids": ["reader_facing_text_inventory"],
            "repair_routing": {},
            "failure_set": [],
            "disposition": None,
            "repair_bundle": None,
            "repair_sequence": predecessor_attempt["repair_sequence"] + 1,
            "promotion_input_bindings": {
                "predecessor_workspace_root": str(predecessor),
                "predecessor_seal_sha256": seal["seal_sha256"],
                "predecessor_inventory_sha256": inventory["inventory_sha256"],
                "predecessor_repair_attempt_sha256": predecessor_attempt[
                    "attempt_sha256"
                ],
            },
            "semantic_attempt_budget_consumed": False,
            "semantic_attempt_number": predecessor_attempt[
                "semantic_attempt_number"
            ],
            "allowed_write_set": [],
            "fresh_reviewer_task_ids": task_ids,
        }
        attempt["attempt_sha256"] = _fingerprint_without(attempt, "attempt_sha256")
        _immutable_json(
            successor / "repair-attempt.json",
            attempt,
            "inventory refresh repair Attempt changed",
        )
        refresh = {
            "schema_name": "precompile-inventory-refresh",
            "schema_version": "1.0.0",
            "activation_status": "target_only",
            "operation_id": operation_id,
            "run_id": run_record["run_id"],
            "prepared_at": prepared_at,
            "approval_reference": approval_reference,
            "predecessor": {
                "workspace_root": str(predecessor),
                "seal_sha256": seal["seal_sha256"],
                "inventory_sha256": inventory["inventory_sha256"],
                "generation_set_sha256": generations["generation_set_sha256"],
                "semantic_dependencies_sha256": dependencies[
                    "dependencies_sha256"
                ],
                "repair_attempt_sha256": predecessor_attempt["attempt_sha256"],
            },
            "successor": {
                "workspace_root": str(successor),
                "inventory_sha256": successor_inventory["inventory_sha256"],
                "generation_set_sha256": generations["generation_set_sha256"],
                "semantic_dependencies_sha256": dependencies[
                    "dependencies_sha256"
                ],
                "repair_attempt_sha256": attempt["attempt_sha256"],
            },
            "downstream_failure_authority": technical_authority,
            "changed_generated_item_ids": changed_ids,
            "semantic_attempt_number": predecessor_attempt[
                "semantic_attempt_number"
            ],
            "semantic_attempt_budget_consumed": False,
            "repair_sequence": predecessor_attempt["repair_sequence"] + 1,
            "fresh_reviewer_task_ids": task_ids,
        }
        refresh["refresh_sha256"] = _fingerprint_without(refresh, "refresh_sha256")
        self.delivery_quality.validate("precompile-inventory-refresh", refresh)
        _immutable_json(
            successor / "precompile-inventory-refresh.json",
            refresh,
            "Precompile Inventory Refresh changed",
        )
        return self._result(refresh, successor, replayed=False)

    def _validate_replay(
        self, *, refresh: dict[str, Any], successor: Path
    ) -> None:
        gate = "precompile_inventory_refresh_replay"
        attempt_path = successor / "repair-attempt.json"
        inventory_path = successor / "reader-facing-text-inventory.json"
        generations_path = successor / "artifact-generations.json"
        dependencies_path = successor / "semantic-dependencies.json"
        if any(
            not path.is_file()
            for path in (
                attempt_path,
                inventory_path,
                generations_path,
                dependencies_path,
            )
        ):
            _reject(
                "inventory refresh replay workspace is incomplete",
                gate,
                "precompile_inventory_refresh_replay_incomplete",
            )
        attempt = read_json(attempt_path)
        _require_fingerprint(
            attempt,
            "attempt_sha256",
            "inventory refresh repair Attempt",
            gate,
            "precompile_inventory_refresh_replay_attempt_stale",
        )
        inventory = read_json(inventory_path)
        generations = read_json(generations_path)
        dependencies = read_json(dependencies_path)
        quality = PrecompileQualityProvider(self.project_root)
        try:
            self.delivery_quality.validate(
                "precompile-artifact-generation-set", generations
            )
            quality._validate_generation_set(generations)
        except (ContractError, KeyError, TypeError):
            _reject(
                "inventory refresh replay Artifact Generations are stale",
                gate,
                "precompile_inventory_refresh_replay_generations_stale",
            )
        try:
            self.delivery_quality.validate(
                "precompile-semantic-dependencies", dependencies
            )
            quality._validate_dependencies(dependencies)
        except (ContractError, KeyError, TypeError):
            _reject(
                "inventory refresh replay semantic dependencies are stale",
                gate,
                "precompile_inventory_refresh_replay_dependencies_stale",
            )
        try:
            self.delivery_quality.validate("reader-facing-text-inventory", inventory)
            writing_projection, _, _ = quality._writing_projection()
            quality._validate_inventory(
                inventory, generations, writing_projection
            )
        except (ContractError, KeyError, TypeError):
            _reject(
                "inventory refresh replay Reader-Facing Text Inventory is stale",
                gate,
                "precompile_inventory_refresh_replay_inventory_stale",
            )
        successor_binding = refresh["successor"]
        if (
            attempt["attempt_sha256"]
            != successor_binding["repair_attempt_sha256"]
            or inventory.get("inventory_sha256")
            != successor_binding["inventory_sha256"]
            or generations.get("generation_set_sha256")
            != successor_binding["generation_set_sha256"]
            or dependencies.get("dependencies_sha256")
            != successor_binding["semantic_dependencies_sha256"]
        ):
            _reject(
                "inventory refresh replay bindings are stale",
                gate,
                "precompile_inventory_refresh_replay_binding_stale",
            )
        task_ids = []
        for owner in (
            "source-faithfulness-reviewer",
            "writing-quality-reviewer",
            "pyramid-reviewer",
        ):
            skeleton_path = (
                successor / "reviewers" / owner / "input/review-skeleton.json"
            )
            if not skeleton_path.is_file():
                _reject(
                    "inventory refresh replay Reviewer tasks are incomplete",
                    gate,
                    "precompile_inventory_refresh_replay_reviewers_incomplete",
                )
            skeleton = read_json(skeleton_path)
            try:
                self.delivery_quality.validate(
                    "precompile-review-skeleton", skeleton
                )
                _require_fingerprint(
                    skeleton,
                    "skeleton_sha256",
                    f"{owner} Reviewer Skeleton",
                    gate,
                    "precompile_inventory_refresh_replay_reviewers_stale",
                )
                if skeleton.get("owner") != owner:
                    raise ContractError("Reviewer Skeleton owner is stale")
                quality._validate_skeleton_current(successor, skeleton, owner)
            except (ContractError, KeyError, TypeError):
                _reject(
                    "inventory refresh replay Reviewer bindings are stale",
                    gate,
                    "precompile_inventory_refresh_replay_reviewers_stale",
                )
            task_ids.append(skeleton.get("task_id"))
        if task_ids != refresh["fresh_reviewer_task_ids"]:
            _reject(
                "inventory refresh replay Reviewer bindings are stale",
                gate,
                "precompile_inventory_refresh_replay_reviewers_stale",
            )
        for binding in refresh["downstream_failure_authority"]["bindings"].values():
            path = Path(binding["path"])
            if not path.is_file() or sha256_file(path) != binding["sha256"]:
                _reject(
                    "inventory refresh failure custody is stale",
                    gate,
                    "precompile_inventory_refresh_replay_custody_stale",
                )

    @staticmethod
    def _stage_derived_inventory(
        authority_root: Path, operation_id: str, inventory: dict[str, Any]
    ) -> Path:
        path = authority_root / "operations" / operation_id / "derived-inventory.json"
        _immutable_json(path, inventory, "derived inventory changed")
        return path

    @staticmethod
    def _result(
        refresh: dict[str, Any], successor: Path, *, replayed: bool
    ) -> dict[str, Any]:
        return {
            "classification": (
                "precompile_inventory_refresh_replayed"
                if replayed
                else "precompile_inventory_refresh_prepared"
            ),
            "operation_id": refresh["operation_id"],
            "refresh_sha256": refresh["refresh_sha256"],
            "refresh_path": str(successor / "precompile-inventory-refresh.json"),
            "successor_workspace_root": str(successor),
            "inventory_sha256": refresh["successor"]["inventory_sha256"],
            "repair_sequence": refresh["repair_sequence"],
            "semantic_attempt_number": refresh["semantic_attempt_number"],
            "fresh_reviewer_task_ids": refresh["fresh_reviewer_task_ids"],
        }

    @staticmethod
    def _validate_failure(
        *,
        failed_root: Path,
        run: Path,
        predecessor: Path,
        manifest_path: Path,
        inventory: dict[str, Any],
    ) -> dict[str, Any]:
        gate = "precompile_inventory_refresh_failure_binding"
        required = {
            name: failed_root / name
            for name in ("status.json", "command.json", "stdout.log", "exit-code.txt")
        }
        if any(not path.is_file() for path in required.values()):
            _reject(
                "retained Final Compile command evidence is incomplete",
                gate,
                "precompile_inventory_refresh_failure_evidence_missing",
            )
        status = read_json(required["status.json"])
        command = read_json(required["command.json"])
        try:
            exit_code = int(required["exit-code.txt"].read_text(encoding="utf-8").strip())
            result = json.loads(required["stdout.log"].read_text(encoding="utf-8"))
        except (ValueError, json.JSONDecodeError) as exc:
            _reject(
                "retained Final Compile command evidence is unreadable",
                gate,
                "precompile_inventory_refresh_failure_evidence_invalid",
                detail=type(exc).__name__,
            )
        argv = command.get("argv")
        if (
            not isinstance(argv, list)
            or "delivery-quality-final-compile" not in argv
            or _option(argv, "--input-track") != "kernel"
            or _option(argv, "--precompile-workspace-root") is None
            or Path(_option(argv, "--precompile-workspace-root") or "").resolve()
            != predecessor
            or _option(argv, "--compile-manifest") is None
            or Path(_option(argv, "--compile-manifest") or "").resolve()
            != manifest_path
            or _option(argv, "--workspace-root") is None
            or not Path(_option(argv, "--workspace-root") or "").resolve().is_relative_to(run)
        ):
            _reject(
                "retained Final Compile command binds different inputs",
                gate,
                "precompile_inventory_refresh_failure_command_mismatch",
            )
        if (
            status.get("run_id") != command.get("run_id")
            or status.get("state") != "failed"
            or status.get("exit_code") != 40
            or exit_code != 40
            or status.get("security", {}).get("acceptance_evidence_eligible") is not True
            or 40 in command.get("accepted_exit_codes", [])
            or result.get("command") != "delivery-quality-final-compile"
            or result.get("status") != "error"
            or result.get("classification") != "compile_dependency_gap"
            or result.get("data", {}).get("first_failing_gate")
            != "final_compile_adapter_execution"
            or result.get("data", {}).get("error_code")
            != "final_compile_adapter_failed"
        ):
            _reject(
                "retained Final Compile failure authority is invalid",
                gate,
                "precompile_inventory_refresh_failure_status_invalid",
            )
        final_workspace = Path(_option(argv, "--workspace-root") or "").resolve()
        adapter_stderr = Path(
            str(result.get("data", {}).get("stderr_path", ""))
        ).resolve()
        if adapter_stderr.parent != final_workspace or not adapter_stderr.is_file():
            _reject(
                "retained Final Compile adapter diagnostic is stale",
                gate,
                "precompile_inventory_refresh_failure_diagnostic_stale",
            )
        lines = [
            line.strip()
            for line in adapter_stderr.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        matches = [match for line in lines if (match := _GENERATED_TITLE_FAILURE.fullmatch(line))]
        if len(matches) != 1:
            _reject(
                "retained Final Compile failure does not identify one generated declaration",
                gate,
                "precompile_inventory_refresh_failure_diagnostic_invalid",
            )
        item_id = matches[0].group(1)
        matching_items = [item for item in inventory["items"] if item.get("item_id") == item_id]
        if (
            len(matching_items) != 1
            or matching_items[0].get("representation") != "declared_generated_text"
        ):
            _reject(
                "retained Final Compile failure does not bind a governed generated item",
                gate,
                "precompile_inventory_refresh_failure_item_invalid",
            )
        return {
            "generated_item_id": item_id,
            "adapter_stderr_path": str(adapter_stderr),
        }
