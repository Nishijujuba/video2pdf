from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from .contracts import ContractRegistry
from .content_production import ContentProduction
from .delivery_quality import DeliveryQualityRegistry
from .errors import ContractError
from .kernel import VideoWorkflowKernel
from .latex_generated_text import extract_tcolorbox_titles
from .precompile_quality import PrecompileQualityProvider
from .utils import (
    canonical_json_bytes,
    read_json,
    require_contained_path,
    sha256_file,
    write_json_atomic,
)


class PrecompileRepairPromotionProvider:
    """Publish or resume one Precompile repair through Kernel Production."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.contracts = ContractRegistry(self.project_root)
        self.delivery_quality = DeliveryQualityRegistry(self.project_root)

    def promote(
        self,
        *,
        run_dir: Path,
        repair_bundle_path: Path,
        predecessor_workspace_root: Path,
        workspace_root: Path,
        inventory_path: Path,
        semantic_dependencies_path: Path,
        repair_attempt_number: int,
        prepared_at: str,
    ) -> dict[str, Any]:
        try:
            return self._promote(
                run_dir=run_dir,
                repair_bundle_path=repair_bundle_path,
                predecessor_workspace_root=predecessor_workspace_root,
                workspace_root=workspace_root,
                inventory_path=inventory_path,
                semantic_dependencies_path=semantic_dependencies_path,
                repair_attempt_number=repair_attempt_number,
                prepared_at=prepared_at,
            )
        except ContractError as error:
            if error.data.get("first_failing_gate") and error.data.get("error_code"):
                raise
            raise ContractError(
                str(error),
                data={
                    "first_failing_gate": "precompile_repair_contract_validation",
                    "error_code": "precompile_repair_contract_invalid",
                },
            ) from error

    def _promote(
        self,
        *,
        run_dir: Path,
        repair_bundle_path: Path,
        predecessor_workspace_root: Path,
        workspace_root: Path,
        inventory_path: Path,
        semantic_dependencies_path: Path,
        repair_attempt_number: int,
        prepared_at: str,
    ) -> dict[str, Any]:
        run_dir = run_dir.resolve()
        bundle_path = require_contained_path(
            repair_bundle_path.resolve(),
            run_dir,
            purpose="Precompile repair promotion bundle",
            error_type=ContractError,
            leaf_kind="file",
            require_single_link=True,
        )
        bundle = read_json(bundle_path)
        if (
            bundle.get("schema_name") != "production-repair-replay-bundle"
            or bundle.get("schema_version") != "1.0.0"
        ):
            raise ContractError("Precompile repair promotion bundle identity is invalid")

        state_path = require_contained_path(
            run_dir / "workflow" / "production-state.json",
            run_dir,
            purpose="Kernel Production State",
            error_type=ContractError,
            leaf_kind="file",
            require_single_link=True,
        )
        state = read_json(state_path)
        self.contracts.validate("production-state", state)
        if bundle.get("run_id") != state.get("run_id"):
            raise ContractError("Precompile repair promotion bundle belongs to another Run")

        for group in ("input_snapshot", "derived_payload"):
            entries = bundle.get(group)
            if not isinstance(entries, list):
                raise ContractError(f"Precompile repair promotion bundle {group} is missing")
            for entry in entries:
                if not isinstance(entry, dict):
                    raise ContractError("Precompile repair promotion bundle entry is invalid")
                path = require_contained_path(
                    run_dir / str(entry.get("path", "")),
                    run_dir,
                    purpose="Precompile repair promotion bundle artifact",
                    error_type=ContractError,
                    leaf_kind="file",
                    require_single_link=True,
                )
                if sha256_file(path) != entry.get("sha256"):
                    raise ContractError(
                        f"Precompile repair promotion bundle artifact drifted: {entry.get('path')}"
                    )

        initial_claims = bundle.get("initial_claims")
        task_order = bundle.get("task_order")
        if not isinstance(initial_claims, dict) or not isinstance(task_order, list):
            raise ContractError("Precompile repair promotion claim plan is incomplete")
        if len(task_order) != len(set(task_order)):
            raise ContractError("Precompile repair promotion task order is ambiguous")
        expected_task_order = self._required_replay_task_order(state)
        if task_order != expected_task_order:
            raise ContractError(
                "Precompile repair promotion requires the complete ordered Production task closure",
                data={
                    "first_failing_gate": "precompile_repair_task_order_closure",
                    "error_code": "precompile_repair_task_order_incomplete",
                    "expected_task_count": len(expected_task_order),
                    "actual_task_count": len(task_order),
                },
            )

        resumed_task_count = self._resume_production_repair(
            run_dir=run_dir,
            bundle_path=bundle_path,
            bundle=bundle,
            initial_claims=initial_claims,
            task_order=task_order,
        )
        state = read_json(state_path)
        self.contracts.validate("production-state", state)
        if state.get("checkpoints", {}).get("draft_compile_ready") != "current":
            raise ContractError(
                "Precompile repair promotion replacements are committed without a current diagnostic compile"
            )
        ContentProduction(
            VideoWorkflowKernel(run_dir.parent)
        ).require_current_diagnostic_compile_authority(run_dir)

        predecessor_root = require_contained_path(
            predecessor_workspace_root.resolve(),
            run_dir,
            purpose="Precompile repair predecessor workspace",
            error_type=ContractError,
            leaf_kind="directory",
        )
        predecessor_path = require_contained_path(
            predecessor_root / "artifact-generations.json",
            predecessor_root,
            purpose="Precompile repair predecessor Artifact Generation set",
            error_type=ContractError,
            leaf_kind="file",
            require_single_link=True,
        )
        predecessor = read_json(predecessor_path)
        self.delivery_quality.validate(
            "precompile-artifact-generation-set", predecessor
        )
        compile_manifest_path = run_dir / "workflow" / "compile-manifest.json"
        compile_manifest = read_json(compile_manifest_path)
        self.contracts.validate("compile-manifest", compile_manifest)
        successor = self._derive_successor_generations(
            run_id=state["run_id"],
            compile_manifest=compile_manifest,
            predecessor=predecessor,
            production_state_sha256=sha256_file(state_path),
        )
        self.delivery_quality.validate(
            "precompile-artifact-generation-set", successor
        )
        operation_id = hashlib.sha256(
            canonical_json_bytes(
                {
                    "successor_inventory_derivation_version": "2",
                    "bundle_sha256": sha256_file(bundle_path),
                    "predecessor_generation_set_sha256": predecessor[
                        "generation_set_sha256"
                    ],
                    "production_state_sha256": sha256_file(state_path),
                    "compile_manifest_sha256": sha256_file(
                        compile_manifest_path
                    ),
                }
            )
        ).hexdigest()[:24]
        output_root = (
            run_dir
            / "review"
            / "precompile"
            / "production-repair-promotions"
            / operation_id
        )
        output_root.mkdir(parents=True, exist_ok=True)
        successor_path = output_root / "artifact-generations.json"
        if successor_path.exists():
            if successor_path.read_bytes() != canonical_json_bytes(successor):
                raise ContractError(
                    "Precompile repair successor Artifact Generation set is already published immutably"
                )
        else:
            write_json_atomic(successor_path, successor)

        candidate_inventory_path = require_contained_path(
            inventory_path.resolve(),
            run_dir,
            purpose="Precompile repair successor inventory source",
            error_type=ContractError,
            leaf_kind="file",
            require_single_link=True,
        )
        candidate_inventory = read_json(candidate_inventory_path)
        self.delivery_quality.validate(
            "reader-facing-text-inventory", candidate_inventory
        )
        successor_inventory = self._derive_successor_inventory(
            run_dir=run_dir,
            compile_manifest=compile_manifest,
            generations=successor,
            candidate=candidate_inventory,
            operation_id=operation_id,
        )
        self.delivery_quality.validate(
            "reader-facing-text-inventory", successor_inventory
        )
        successor_inventory_path = output_root / "reader-facing-text-inventory.json"
        if successor_inventory_path.exists():
            if successor_inventory_path.read_bytes() != canonical_json_bytes(
                successor_inventory
            ):
                raise ContractError(
                    "Precompile repair successor inventory is already published immutably"
                )
        else:
            write_json_atomic(successor_inventory_path, successor_inventory)

        successor_workspace = require_contained_path(
            workspace_root.resolve(),
            run_dir,
            purpose="Precompile repair successor workspace",
            error_type=ContractError,
            leaf_kind="directory",
            allow_missing=True,
        )
        semantic_dependencies = require_contained_path(
            semantic_dependencies_path.resolve(),
            run_dir,
            purpose="Precompile repair semantic dependencies",
            error_type=ContractError,
            leaf_kind="file",
            require_single_link=True,
        )
        candidate_dependencies = read_json(semantic_dependencies)
        self.delivery_quality.validate(
            "precompile-semantic-dependencies", candidate_dependencies
        )
        successor_dependencies = self._derive_successor_dependencies(
            run_dir=run_dir,
            candidate=candidate_dependencies,
        )
        self.delivery_quality.validate(
            "precompile-semantic-dependencies", successor_dependencies
        )
        successor_dependencies_path = output_root / "semantic-dependencies.json"
        if successor_dependencies_path.exists():
            if successor_dependencies_path.read_bytes() != canonical_json_bytes(
                successor_dependencies
            ):
                raise ContractError(
                    "Precompile repair semantic dependencies are already published immutably"
                )
        else:
            write_json_atomic(successor_dependencies_path, successor_dependencies)
        prepared = PrecompileQualityProvider(self.project_root).prepare_repair(
            predecessor_workspace_root=predecessor_root,
            workspace_root=successor_workspace,
            inventory_path=successor_inventory_path,
            artifact_generations_path=successor_path,
            semantic_dependencies_path=successor_dependencies_path,
            repair_attempt_number=repair_attempt_number,
            prepared_at=prepared_at,
            kernel_production_run_dir=run_dir,
        )

        return {
            "classification": (
                "precompile_repair_promoted"
                if resumed_task_count
                else "precompile_repair_already_promoted"
            ),
            "run_id": state["run_id"],
            "repair_bundle_path": str(bundle_path),
            "production_state_path": str(state_path),
            "production_state_sha256": sha256_file(state_path),
            "promoted_task_count": len(task_order),
            "resumed_task_count": resumed_task_count,
            "draft_compile_ready": "current",
            "predecessor_generation_set_sha256": predecessor[
                "generation_set_sha256"
            ],
            "successor_generation_set_path": str(successor_path),
            "successor_generation_set_sha256": successor[
                "generation_set_sha256"
            ],
            "successor_inventory_path": str(successor_inventory_path),
            "successor_inventory_sha256": successor_inventory[
                "inventory_sha256"
            ],
            "successor_semantic_dependencies_path": str(
                successor_dependencies_path
            ),
            "successor_semantic_dependencies_sha256": successor_dependencies[
                "dependencies_sha256"
            ],
            "repair_attempt_path": prepared["repair_attempt_path"],
            "reviewer_skeleton_paths": prepared["skeleton_paths"],
        }

    @staticmethod
    def _required_replay_task_order(state: dict[str, Any]) -> list[str]:
        sections = state["sections"]
        section_ids = list(sections)
        task_order = ["outline", "pyramid-outline"]
        task_order.extend(
            f"writer-{section_id.replace('_', '-')}"
            for section_id in section_ids
        )
        for section_id in section_ids:
            for slot in sections[section_id]["figure_slots"]:
                task_order.append(
                    f"figure-{slot['wave']}-{slot['slot_id'].replace('_', '-')}"
                )
        task_order.extend(
            f"pyramid-section-{section_id.replace('_', '-')}"
            for section_id in section_ids
        )
        task_order.append("pyramid-main")
        return task_order

    @staticmethod
    def _reject(message: str, gate: str, code: str, **data: Any) -> None:
        raise ContractError(
            message,
            data={
                "first_failing_gate": gate,
                "error_code": code,
                **data,
            },
        )

    def _resume_production_repair(
        self,
        *,
        run_dir: Path,
        bundle_path: Path,
        bundle: dict[str, Any],
        initial_claims: dict[str, Any],
        task_order: list[Any],
    ) -> int:
        state_path = run_dir / "workflow" / "production-state.json"
        self._restore_initial_artifacts(
            run_dir=run_dir,
            bundle=bundle,
            state=read_json(state_path),
        )
        kernel = VideoWorkflowKernel(run_dir.parent)
        resumed = 0
        for raw_logical_key in task_order:
            if not isinstance(raw_logical_key, str) or not raw_logical_key:
                self._reject(
                    "Precompile repair promotion task identity is invalid",
                    "repair_bundle_identity",
                    "precompile_repair_task_identity_invalid",
                )
            logical_key = raw_logical_key
            state = read_json(state_path)
            initial = initial_claims.get(logical_key)
            current = state.get("claims", {}).get(logical_key)
            if not isinstance(initial, dict) or not isinstance(current, dict):
                self._reject(
                    f"Precompile repair promotion claim disappeared: {logical_key}",
                    "production_repair_claim",
                    "precompile_repair_claim_missing",
                    logical_task_key=logical_key,
                )
            if current.get("task_id") != initial.get("task_id"):
                self._reject(
                    f"Precompile repair promotion task identity changed: {logical_key}",
                    "production_repair_claim",
                    "precompile_repair_task_identity_changed",
                    logical_task_key=logical_key,
                )
            initial_generation = initial.get("claim_generation")
            if not isinstance(initial_generation, int):
                self._reject(
                    f"Precompile repair promotion initial generation is invalid: {logical_key}",
                    "repair_bundle_identity",
                    "precompile_repair_initial_generation_invalid",
                    logical_task_key=logical_key,
                )
            current_generation = current.get("claim_generation")
            if current_generation == initial_generation:
                if current.get("status") != "committed":
                    self._reject(
                        f"Precompile repair predecessor claim is not committed: {logical_key}",
                        "production_repair_resume",
                        "precompile_repair_predecessor_not_committed",
                        logical_task_key=logical_key,
                    )
                plan = kernel.production_plan(
                    run_dir,
                    supersede_task_id=current["task_id"],
                    expected_claim_generation=initial_generation,
                )
                runnable = plan.get("runnable_tasks", [])
                if (
                    len(runnable) != 1
                    or runnable[0].get("logical_task_key") != logical_key
                ):
                    self._reject(
                        f"Production supersede returned the wrong replacement: {logical_key}",
                        "production_repair_supersede",
                        "precompile_repair_supersede_mismatch",
                        logical_task_key=logical_key,
                    )
                envelope = runnable[0]
            elif current_generation == initial_generation + 1:
                envelope_path = require_contained_path(
                    run_dir
                    / "workflow"
                    / "tasks"
                    / current["task_id"]
                    / "envelope.json",
                    run_dir,
                    purpose="Precompile repair replacement Task Envelope",
                    error_type=ContractError,
                    leaf_kind="file",
                    require_single_link=True,
                )
                envelope = read_json(envelope_path)
                if envelope.get("logical_task_key") != logical_key:
                    self._reject(
                        f"Precompile repair replacement Envelope drifted: {logical_key}",
                        "production_repair_resume",
                        "precompile_repair_envelope_stale",
                        logical_task_key=logical_key,
                    )
            else:
                self._reject(
                    f"Precompile repair claim generation is outside its replay fence: {logical_key}",
                    "production_repair_resume",
                    "precompile_repair_generation_fence_invalid",
                    logical_task_key=logical_key,
                )

            self.contracts.validate("production-task-envelope", envelope)
            attempt_id = self._materialize_replacement_attempt(
                run_dir=run_dir,
                bundle_root=bundle_path.parent,
                bundle=bundle,
                envelope=envelope,
            )
            if current_generation == initial_generation + 1 and current.get(
                "status"
            ) == "committed":
                receipt = state.get("receipts", {}).get(logical_key)
                if (
                    not isinstance(receipt, dict)
                    or receipt.get("attempt_id") != attempt_id
                    or receipt.get("claim_generation") != current_generation
                ):
                    self._reject(
                        f"Precompile repair committed receipt is stale: {logical_key}",
                        "production_repair_resume",
                        "precompile_repair_receipt_stale",
                        logical_task_key=logical_key,
                    )
                continue
            if current.get("status") not in {"committed", "active", "available"}:
                self._reject(
                    f"Precompile repair replacement state is unsupported: {logical_key}",
                    "production_repair_resume",
                    "precompile_repair_claim_state_invalid",
                    logical_task_key=logical_key,
                )
            runtime_policy = None
            if logical_key == "pyramid-main":
                runtime_policy_path = require_contained_path(
                    run_dir / "workflow" / "compile-runtime-policy.json",
                    run_dir,
                    purpose="Precompile repair closing Compile Runtime Policy",
                    error_type=ContractError,
                    leaf_kind="file",
                    require_single_link=True,
                )
                bundled_policy = self._bound_payload_bytes(
                    run_dir=run_dir,
                    bundle_root=bundle_path.parent,
                    bundle=bundle,
                    relative_path="payload/compile-runtime-policy.json",
                )
                if hashlib.sha256(bundled_policy).hexdigest() != sha256_file(
                    runtime_policy_path
                ):
                    self._reject(
                        "Precompile repair Compile Runtime Policy drifted",
                        "repair_bundle_integrity",
                        "precompile_repair_runtime_policy_drifted",
                    )
                runtime_policy = json.loads(bundled_policy)
            kernel.production_advance(
                run_dir,
                envelope["task_id"],
                attempt_id,
                compile_runtime_policy=runtime_policy,
            )
            resumed += 1

        final_plan = kernel.production_plan(run_dir)
        if final_plan.get("classification") != "production_complete":
            self._reject(
                "Precompile repair replay did not close the Production graph",
                "production_repair_completion",
                "precompile_repair_production_incomplete",
            )
        return resumed

    def _restore_initial_artifacts(
        self,
        *,
        run_dir: Path,
        bundle: dict[str, Any],
        state: dict[str, Any],
    ) -> None:
        restoration = bundle.get("restoration", [])
        if not isinstance(restoration, list):
            self._reject(
                "Precompile repair restoration map is invalid",
                "repair_bundle_identity",
                "precompile_repair_restoration_invalid",
            )
        for item in restoration:
            if not isinstance(item, dict):
                self._reject(
                    "Precompile repair restoration entry is invalid",
                    "repair_bundle_identity",
                    "precompile_repair_restoration_invalid",
                )
            logical_id = item.get("logical_id")
            artifact = state.get("artifacts", {}).get(logical_id)
            if not isinstance(artifact, dict) or artifact.get("sha256") != item.get(
                "sha256"
            ):
                continue
            if item.get("target_path") != artifact.get("path"):
                self._reject(
                    f"Precompile repair restoration target is not authoritative: {logical_id}",
                    "production_baseline_restoration",
                    "precompile_repair_restoration_target_invalid",
                    logical_id=logical_id,
                )
            source = require_contained_path(
                run_dir / str(item.get("source_path", "")),
                run_dir,
                purpose="Precompile repair retained authority source",
                error_type=ContractError,
                leaf_kind="file",
                require_single_link=True,
            )
            target = require_contained_path(
                run_dir / str(item.get("target_path", "")),
                run_dir,
                purpose="Precompile repair restored Production artifact",
                error_type=ContractError,
                leaf_kind="file",
                allow_missing=True,
            )
            expected_sha = str(item.get("sha256", ""))
            if sha256_file(source) != expected_sha:
                self._reject(
                    f"Precompile repair retained authority source drifted: {logical_id}",
                    "repair_bundle_integrity",
                    "precompile_repair_restoration_source_stale",
                    logical_id=logical_id,
                )
            if target.is_file() and sha256_file(target) == expected_sha:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.precompile-repair-restore")
            if temporary.exists():
                if sha256_file(temporary) != expected_sha:
                    self._reject(
                        f"Precompile repair restoration temporary conflicts: {logical_id}",
                        "production_baseline_restoration",
                        "precompile_repair_restoration_conflict",
                        logical_id=logical_id,
                    )
            else:
                temporary.write_bytes(source.read_bytes())
            temporary.replace(target)

    def _materialize_replacement_attempt(
        self,
        *,
        run_dir: Path,
        bundle_root: Path,
        bundle: dict[str, Any],
        envelope: dict[str, Any],
    ) -> str:
        envelope_path = run_dir / "workflow" / "tasks" / envelope["task_id"] / "envelope.json"
        envelope_sha256 = sha256_file(envelope_path)
        attempt_id = hashlib.sha256(
            (
                "production-repair-replay\0"
                + envelope["task_id"]
                + "\0"
                + str(envelope["claim_generation"])
                + "\0"
                + envelope_sha256
            ).encode("utf-8")
        ).hexdigest()[:24]
        payload = bundle_root / "payload"
        role = envelope["role"]
        outputs: dict[str, bytes]
        if role == "outline":
            outputs = {"outline.json": self._bound_payload_bytes(run_dir=run_dir, bundle_root=bundle_root, bundle=bundle, relative_path="payload/outline.json")}
            self.contracts.validate("outline-contract", read_json(payload / "outline.json"))
        elif role == "writer":
            section_id = envelope["section_id"]
            outputs = {
                f"{section_id}.tex": self._bound_payload_bytes(run_dir=run_dir, bundle_root=bundle_root, bundle=bundle, relative_path=f"payload/writers/{section_id}.tex"),
                "writer-result.json": self._bound_payload_bytes(run_dir=run_dir, bundle_root=bundle_root, bundle=bundle, relative_path=f"payload/writers/{section_id}.result.json"),
            }
            self.contracts.validate(
                "writer-result",
                read_json(payload / "writers" / f"{section_id}.result.json"),
            )
        elif role == "figure":
            slot_id = envelope["slot_id"]
            outputs = {
                f"{slot_id}.png": self._bound_payload_bytes(run_dir=run_dir, bundle_root=bundle_root, bundle=bundle, relative_path=f"payload/figures/{slot_id}.png"),
                "figure-manifest.json": self._bound_payload_bytes(run_dir=run_dir, bundle_root=bundle_root, bundle=bundle, relative_path=f"payload/figures/{slot_id}.manifest.json"),
                f"{slot_id}.tex": self._bound_payload_bytes(run_dir=run_dir, bundle_root=bundle_root, bundle=bundle, relative_path=f"payload/figures/{slot_id}.tex"),
            }
            self.contracts.validate(
                "figure-manifest",
                read_json(payload / "figures" / f"{slot_id}.manifest.json"),
            )
        elif role.startswith("pyramid_"):
            logical_key = envelope["logical_task_key"]
            binding_bytes = self._bound_payload_bytes(
                run_dir=run_dir,
                bundle_root=bundle_root,
                bundle=bundle,
                relative_path=f"payload/pyramid/{logical_key}.json",
            )
            binding = json.loads(binding_bytes)
            self.contracts.validate("pyramid-evaluation-binding", binding)
            if (
                binding.get("target") != envelope["pyramid_target"]
                or binding.get("evaluation_context")
                != envelope["evaluation_context"]
                or binding.get("status") != "pass"
            ):
                self._reject(
                    f"Precompile repair Pyramid evaluation is stale: {logical_key}",
                    "repair_bundle_payload",
                    "precompile_repair_pyramid_evaluation_stale",
                    logical_task_key=logical_key,
                )
            outputs = {"pyramid-report.json": binding_bytes}
        else:
            self._reject(
                f"Precompile repair role is unsupported: {role}",
                "repair_bundle_payload",
                "precompile_repair_role_unsupported",
            )
        if set(outputs) != set(envelope["required_outputs"]):
            self._reject(
                f"Precompile repair output set is incomplete: {envelope['logical_task_key']}",
                "repair_bundle_payload",
                "precompile_repair_output_set_incomplete",
                logical_task_key=envelope["logical_task_key"],
            )
        attempt_root = (
            run_dir
            / "workflow"
            / "tasks"
            / envelope["task_id"]
            / "attempts"
            / attempt_id
        )
        attempt_root.mkdir(parents=True, exist_ok=True)
        output_records = []
        for name in envelope["required_outputs"]:
            target = attempt_root / name
            value = outputs[name]
            if target.exists() and target.read_bytes() != value:
                self._reject(
                    f"Precompile repair Attempt output changed: {name}",
                    "production_repair_attempt",
                    "precompile_repair_attempt_output_conflict",
                    logical_task_key=envelope["logical_task_key"],
                )
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(f".{target.name}.precompile-repair-attempt")
                if temporary.exists() and temporary.read_bytes() != value:
                    self._reject(
                        f"Precompile repair Attempt temporary changed: {name}",
                        "production_repair_attempt",
                        "precompile_repair_attempt_output_conflict",
                        logical_task_key=envelope["logical_task_key"],
                    )
                if not temporary.exists():
                    temporary.write_bytes(value)
                temporary.replace(target)
            output_records.append({"path": name, "sha256": sha256_file(target)})
        attempt = {
            "schema_name": "production-task-attempt",
            "schema_version": "1.0.0",
            "kernel_version": "2.0.0",
            "task_id": envelope["task_id"],
            "attempt_id": attempt_id,
            "claim_generation": envelope["claim_generation"],
            "claim_token": envelope["claim_token"],
            "envelope_sha256": envelope_sha256,
            "outputs": output_records,
        }
        self.contracts.validate("production-task-attempt", attempt)
        attempt_path = attempt_root / "attempt.json"
        if attempt_path.exists():
            if read_json(attempt_path) != attempt:
                self._reject(
                    "Precompile repair Attempt identity changed",
                    "production_repair_attempt",
                    "precompile_repair_attempt_conflict",
                    logical_task_key=envelope["logical_task_key"],
                )
        else:
            write_json_atomic(attempt_path, attempt)
        return attempt_id

    def _bound_payload_bytes(
        self,
        *,
        run_dir: Path,
        bundle_root: Path,
        bundle: dict[str, Any],
        relative_path: str,
    ) -> bytes:
        source = require_contained_path(
            bundle_root / relative_path,
            run_dir,
            purpose="Precompile repair bundle payload",
            error_type=ContractError,
            leaf_kind="file",
            require_single_link=True,
        )
        declared = {
            str(item.get("path")): str(item.get("sha256"))
            for item in bundle.get("derived_payload", [])
            if isinstance(item, dict)
        }
        run_relative = source.relative_to(run_dir).as_posix()
        expected = declared.get(run_relative)
        if expected is None or sha256_file(source) != expected:
            self._reject(
                f"Precompile repair bundle does not bind consumed payload: {run_relative}",
                "repair_bundle_integrity",
                "precompile_repair_bundle_payload_unbound",
                payload_path=run_relative,
            )
        return source.read_bytes()

    @staticmethod
    def _derive_successor_generations(
        *,
        run_id: str,
        compile_manifest: dict[str, Any],
        predecessor: dict[str, Any],
        production_state_sha256: str,
    ) -> dict[str, Any]:
        entries = compile_manifest.get("entries")
        if not isinstance(entries, list) or not entries:
            raise ContractError("current Compile Manifest has no generation entries")
        artifacts = [
            {
                "logical_id": entry["logical_id"],
                "generation": entry["generation"],
                "sha256": entry["sha256"],
            }
            for entry in entries
        ]
        if {item["logical_id"] for item in artifacts} != {
            item["logical_id"] for item in predecessor["artifacts"]
        }:
            raise ContractError(
                "current Compile Manifest and predecessor Precompile generation scope differ"
            )
        predecessor_by_id = {
            item["logical_id"]: item for item in predecessor["artifacts"]
        }
        if any(
            item["generation"] < predecessor_by_id[item["logical_id"]]["generation"]
            for item in artifacts
        ):
            raise ContractError(
                "current Production generation regressed below the Precompile predecessor"
            )
        successor = {
            "schema_name": "precompile-artifact-generation-set",
            "schema_version": "1.0.0",
            "generation_set_id": (
                f"{run_id[:8]}-production-repair-"
                f"{production_state_sha256[:12]}"
            ),
            "artifacts": sorted(artifacts, key=lambda item: item["logical_id"]),
            "producer_ids": sorted({entry["producer"] for entry in entries}),
        }
        successor["generation_set_sha256"] = hashlib.sha256(
            canonical_json_bytes(successor)
        ).hexdigest()
        return successor

    @staticmethod
    def _derive_successor_inventory(
        *,
        run_dir: Path,
        compile_manifest: dict[str, Any],
        generations: dict[str, Any],
        candidate: dict[str, Any],
        operation_id: str,
    ) -> dict[str, Any]:
        if (
            candidate.get("schema_name") != "reader-facing-text-inventory"
            or candidate.get("schema_version") != "1.0.0"
        ):
            raise ContractError("Precompile repair successor inventory identity is invalid")
        generation_by_id = {
            item["logical_id"]: item for item in generations["artifacts"]
        }
        manifest_by_id = {
            item["logical_id"]: item for item in compile_manifest["entries"]
        }
        items: list[dict[str, Any]] = []
        for source_item in candidate.get("items", []):
            item = {
                key: value
                for key, value in source_item.items()
                if key != "item_sha256"
            }
            logical_id = item.get("source_artifact_logical_id")
            generation = generation_by_id.get(logical_id)
            manifest_entry = manifest_by_id.get(logical_id)
            if generation is None or manifest_entry is None:
                raise ContractError(
                    f"Precompile repair inventory source is absent from current Compile Manifest: {logical_id}"
                )
            item["source_generation"] = generation["generation"]
            item["source_sha256"] = generation["sha256"]
            if item.get("representation") == "structured_text":
                source_path = require_contained_path(
                    run_dir / manifest_entry["source_path"],
                    run_dir,
                    purpose="Precompile repair structured-text source",
                    error_type=ContractError,
                    leaf_kind="file",
                    require_single_link=True,
                )
                declared_text = source_path.read_text(encoding="utf-8")
                item["declared_text"] = declared_text
                item["text_sha256"] = hashlib.sha256(
                    declared_text.encode("utf-8")
                ).hexdigest()
            elif item.get("representation") == "declared_generated_text":
                source_path = require_contained_path(
                    run_dir / manifest_entry["source_path"],
                    run_dir,
                    purpose="Precompile repair generated-text source",
                    error_type=ContractError,
                    leaf_kind="file",
                    require_single_link=True,
                )
                declared_text = (
                    PrecompileRepairPromotionProvider._tcolorbox_titles(
                        source_path=source_path,
                        locator=item.get("locator"),
                        item_id=item.get("item_id"),
                    )
                )
                item["declared_text"] = declared_text
                item["text_sha256"] = hashlib.sha256(
                    declared_text.encode("utf-8")
                ).hexdigest()
            item["item_sha256"] = hashlib.sha256(
                canonical_json_bytes(item)
            ).hexdigest()
            items.append(item)
        if not items:
            raise ContractError("Precompile repair successor inventory is empty")
        inventory = {
            key: value
            for key, value in candidate.items()
            if key
            not in {
                "inventory_sha256",
                "inventory_id",
                "generation_set_sha256",
                "items",
                "reader_text_set_sha256",
            }
        }
        inventory["inventory_id"] = f"production-repair-{operation_id}"
        inventory["generation_set_sha256"] = generations[
            "generation_set_sha256"
        ]
        inventory["items"] = items
        inventory["reader_text_set_sha256"] = hashlib.sha256(
            canonical_json_bytes(
                [
                    {
                        "item_id": item["item_id"],
                        "kind": item["kind"],
                        "representation": item["representation"],
                        "text_sha256": item["text_sha256"],
                    }
                    for item in items
                ]
            )
        ).hexdigest()
        inventory["inventory_sha256"] = hashlib.sha256(
            canonical_json_bytes(inventory)
        ).hexdigest()
        return inventory

    @staticmethod
    def _tcolorbox_titles(
        *, source_path: Path, locator: object, item_id: object
    ) -> str:
        if (
            not isinstance(locator, str)
            or not locator.startswith("latex-generated:")
            or not locator.endswith("/newtcolorbox-title")
            or source_path.suffix.casefold() != ".sty"
        ):
            raise ContractError(
                f"Precompile repair generated-text source is unsupported: {item_id}"
            )
        titles = list(
            extract_tcolorbox_titles(
                source_path.read_text(encoding="utf-8")
            ).values()
        )
        if not titles or len(titles) != len(set(titles)):
            raise ContractError(
                f"Precompile repair generated-text titles are invalid: {item_id}"
            )
        return "\n".join(titles)

    @staticmethod
    def _derive_successor_dependencies(
        *,
        run_dir: Path,
        candidate: dict[str, Any],
    ) -> dict[str, Any]:
        if (
            candidate.get("schema_name") != "precompile-semantic-dependencies"
            or candidate.get("schema_version") != "1.0.0"
        ):
            raise ContractError("Precompile repair semantic dependency identity is invalid")
        dependencies = candidate.get("dependencies")
        if not isinstance(dependencies, list):
            raise ContractError("Precompile repair semantic dependencies are missing")
        successor = deepcopy(candidate)
        successor.pop("dependencies_sha256", None)
        for dependency in successor["dependencies"]:
            projection = dependency.get("projection")
            evidence = projection.get("evidence") if isinstance(projection, dict) else None
            if not isinstance(evidence, list) or not evidence:
                raise ContractError(
                    "Precompile repair semantic projection evidence is missing"
                )
            for item in evidence:
                path = require_contained_path(
                    run_dir / str(item.get("path", "")),
                    run_dir,
                    purpose="Precompile repair semantic dependency evidence",
                    error_type=ContractError,
                    leaf_kind="file",
                    require_single_link=True,
                )
                item["sha256"] = sha256_file(path)
            dependency["projection_sha256"] = hashlib.sha256(
                canonical_json_bytes(projection)
            ).hexdigest()
        successor["dependencies_sha256"] = hashlib.sha256(
            canonical_json_bytes(successor)
        ).hexdigest()
        return successor
