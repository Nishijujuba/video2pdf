from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .delivery_quality import DeliveryQualityRegistry
from .errors import ContractError, PrecompileFault, TextEquivalenceRejected
from .utils import canonical_json_bytes, read_json, write_json_atomic


PRECOMPILE_OWNERS = (
    "source-faithfulness-reviewer",
    "writing-quality-reviewer",
    "pyramid-reviewer",
)
PREPARE_FAULT_POINTS = {"after_first_skeleton_write"}
PATCH_COMMIT_FAULT_POINTS = {"after_patch_write"}
MATERIALIZE_FAULT_POINTS = {"after_report_write"}
PRECOMPILE_PROVIDER_ID = "precompile-quality-provider"
PRECOMPILE_PROVIDER_VERSION = "1.0.0"


def _fingerprint_without(value: dict[str, Any], field: str) -> str:
    material = {key: item for key, item in value.items() if key != field}
    return hashlib.sha256(canonical_json_bytes(material)).hexdigest()


def _require_fingerprint(value: dict[str, Any], field: str, label: str) -> None:
    if value.get(field) != _fingerprint_without(value, field):
        raise ContractError(f"{label} {field} is stale or invalid")


def _task_id(owner: str, generation_set_sha256: str, inventory_sha256: str) -> str:
    return hashlib.sha256(
        f"{owner}\0{generation_set_sha256}\0{inventory_sha256}".encode("utf-8")
    ).hexdigest()[:32]


def _reviewer_root(root: Path, owner: str) -> Path:
    return root / "reviewers" / owner


def _skeleton_path(root: Path, owner: str) -> Path:
    return _reviewer_root(root, owner) / "input" / "review-skeleton.json"


def _patch_path(root: Path, owner: str) -> Path:
    return _reviewer_root(root, owner) / "output" / "judgment-patch.json"


def _commit_path(root: Path, owner: str) -> Path:
    return _reviewer_root(root, owner) / "commit" / "patch-commit.json"


class PrecompileQualityProvider:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.registry = DeliveryQualityRegistry(self.project_root)

    def prepare(
        self,
        *,
        workspace_root: Path,
        inventory_path: Path,
        artifact_generations_path: Path,
        semantic_dependencies_path: Path,
        prepared_at: str,
        fault_point: str | None = None,
    ) -> dict[str, Any]:
        self.registry.check()
        inventory = read_json(inventory_path.resolve())
        generations = read_json(artifact_generations_path.resolve())
        dependencies = read_json(semantic_dependencies_path.resolve())
        self._validate_generation_set(generations)
        self._validate_dependencies(dependencies)
        self.registry.validate("precompile-artifact-generation-set", generations)
        self.registry.validate("precompile-semantic-dependencies", dependencies)
        writing_projection, catalog_sha256, projection_sha256 = (
            self._writing_projection()
        )
        required_results = self._validate_inventory(
            inventory,
            generations,
            writing_projection,
        )
        self.registry.validate("reader-facing-text-inventory", inventory)

        dependency_by_owner = {
            item["owner"]: item for item in dependencies["dependencies"]
        }
        skeletons: list[tuple[str, dict[str, Any]]] = []
        for owner in PRECOMPILE_OWNERS:
            projection = self._owner_projection(
                owner,
                writing_projection,
                catalog_sha256,
                projection_sha256,
                dependencies,
            )
            if owner == "writing-quality-reviewer":
                owner_required_results = required_results
            else:
                dependency = dependency_by_owner[owner]
                owner_required_results = [
                    {
                        "scope_id": scope_id,
                        "result_key": f"{owner}:{scope_id}",
                    }
                    for scope_id in dependency["required_scope_ids"]
                ]
            skeleton = {
                "schema_name": "precompile-review-skeleton",
                "schema_version": "1.0.0",
                "task_id": _task_id(
                    owner,
                    generations["generation_set_sha256"],
                    inventory["inventory_sha256"],
                ),
                "owner": owner,
                "prepared_at": prepared_at,
                "activation_status": "target_only",
                "peer_results_visible": False,
                "generation_set_sha256": generations["generation_set_sha256"],
                "inventory_sha256": inventory["inventory_sha256"],
                "language_profile_id": inventory["language_profile_id"],
                "delivery_glossary": inventory.get("delivery_glossary"),
                "projection": projection,
                "required_results": owner_required_results,
                "allowed_decisions": ["pass", "fail", "contract_gap"],
                "required_patch_fields": [
                    "task_id",
                    "skeleton_sha256",
                    "reviewer",
                    "results",
                    "contract_gaps",
                ],
            }
            skeleton["skeleton_sha256"] = hashlib.sha256(
                canonical_json_bytes(skeleton)
            ).hexdigest()
            self.registry.validate("precompile-review-skeleton", skeleton)
            skeletons.append((owner, skeleton))

        root = workspace_root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        _write_immutable_or_same(root / "artifact-generations.json", generations)
        _write_immutable_or_same(
            root / "reader-facing-text-inventory.json", inventory
        )
        _write_immutable_or_same(
            root / "semantic-dependencies.json", dependencies
        )
        paths = []
        for index, (owner, skeleton) in enumerate(skeletons):
            path = _skeleton_path(root, owner)
            _write_immutable_or_same(path, skeleton)
            paths.append(str(path))
            if index == 0 and fault_point == "after_first_skeleton_write":
                raise PrecompileFault(fault_point)
        return {
            "workspace_root": str(root),
            "owner_count": len(skeletons),
            "generation_set_sha256": generations["generation_set_sha256"],
            "inventory_sha256": inventory["inventory_sha256"],
            "skeleton_paths": paths,
            "activation_status": "target_only",
        }

    def prepare_repair(
        self,
        *,
        predecessor_workspace_root: Path,
        workspace_root: Path,
        inventory_path: Path,
        artifact_generations_path: Path,
        semantic_dependencies_path: Path,
        repair_attempt_number: int,
        prepared_at: str,
    ) -> dict[str, Any]:
        if repair_attempt_number not in {1, 2, 3}:
            raise ContractError("repair attempt number must be within 1..3")
        predecessor_root = predecessor_workspace_root.resolve()
        report = read_json(predecessor_root / "precompile-quality-report.json")
        _require_fingerprint(report, "report_sha256", "Precompile Quality Report")
        if (
            report.get("overall_decision") != "fail"
            or not report.get("failure_set")
            or report.get("contract_gaps")
            or report.get("semantic_attempt_budget_consumed") is not True
        ):
            raise ContractError(
                "repair preparation requires a materialized semantic failure"
            )
        routing = report.get("repair_routing")
        if not isinstance(routing, dict):
            raise ContractError("failed report lacks deterministic repair routing")
        routed_keys = {
            key
            for group_name in (
                "parallel_repair_tasks",
                "integration_repair_tasks",
            )
            for task in routing.get(group_name, [])
            for key in task.get("failure_keys", [])
        }
        expected_keys = {
            f"{item['owner']}:{item['result_key']}"
            for item in report["failure_set"]
        }
        if routed_keys != expected_keys:
            raise ContractError("deterministic repair routing is incomplete")

        predecessor_generations = read_json(
            predecessor_root / "artifact-generations.json"
        )
        repaired_generations = read_json(artifact_generations_path.resolve())
        self._validate_generation_set(predecessor_generations)
        self._validate_generation_set(repaired_generations)
        predecessor_by_id = {
            item["logical_id"]: item
            for item in predecessor_generations["artifacts"]
        }
        repaired_by_id = {
            item["logical_id"]: item for item in repaired_generations["artifacts"]
        }
        if set(predecessor_by_id) != set(repaired_by_id):
            raise ContractError(
                "repair must preserve the Artifact Generation logical set"
            )
        advanced = []
        for logical_id in sorted(predecessor_by_id):
            prior = predecessor_by_id[logical_id]
            current = repaired_by_id[logical_id]
            if current["generation"] < prior["generation"]:
                raise ContractError("repair cannot regress an Artifact Generation")
            if current["generation"] == prior["generation"]:
                if current["sha256"] != prior["sha256"]:
                    raise ContractError(
                        "repair changed bytes without advancing the generation"
                    )
                continue
            if current["sha256"] == prior["sha256"]:
                raise ContractError(
                    "repair advanced a generation without changing its bytes"
                )
            advanced.append(logical_id)
        if not advanced:
            raise ContractError("repair must advance at least one Artifact Generation")

        prepared = self.prepare(
            workspace_root=workspace_root,
            inventory_path=inventory_path,
            artifact_generations_path=artifact_generations_path,
            semantic_dependencies_path=semantic_dependencies_path,
            prepared_at=prepared_at,
        )
        ledger = {
            "schema_name": "precompile-repair-attempt",
            "schema_version": "1.0.0",
            "activation_status": "target_only",
            "repair_attempt_number": repair_attempt_number,
            "prepared_at": prepared_at,
            "predecessor_report_sha256": report["report_sha256"],
            "predecessor_generation_set_sha256": predecessor_generations[
                "generation_set_sha256"
            ],
            "repaired_generation_set_sha256": repaired_generations[
                "generation_set_sha256"
            ],
            "repaired_inventory_sha256": prepared["inventory_sha256"],
            "advanced_logical_ids": advanced,
            "repair_routing": routing,
            "fresh_reviewer_task_ids": [
                read_json(Path(path))["task_id"]
                for path in prepared["skeleton_paths"]
            ],
        }
        ledger["attempt_sha256"] = hashlib.sha256(
            canonical_json_bytes(ledger)
        ).hexdigest()
        ledger_path = workspace_root.resolve() / "repair-attempt.json"
        _write_immutable_or_same(ledger_path, ledger)
        return {
            **prepared,
            "repair_attempt_number": repair_attempt_number,
            "repair_attempt_sha256": ledger["attempt_sha256"],
            "repair_attempt_path": str(ledger_path),
            "advanced_logical_ids": advanced,
        }

    def commit_patch(
        self,
        *,
        workspace_root: Path,
        owner: str,
        patch_path: Path,
        committed_at: str,
        fault_point: str | None = None,
    ) -> dict[str, Any]:
        if owner not in PRECOMPILE_OWNERS:
            raise ContractError("unknown precompile Reviewer owner")
        root = workspace_root.resolve()
        skeleton_path = _skeleton_path(root, owner)
        if not skeleton_path.is_file():
            raise ContractError("fixed Reviewer Skeleton is missing")
        skeleton = read_json(skeleton_path)
        _require_fingerprint(skeleton, "skeleton_sha256", "Reviewer Skeleton")
        self._validate_skeleton_current(root, skeleton, owner)
        patch = read_json(patch_path.resolve())
        self.registry.validate("precompile-review-skeleton", skeleton)
        self.registry.validate("precompile-judgment-patch", patch)
        self._validate_patch(patch, skeleton, owner)
        generations = read_json(root / "artifact-generations.json")
        reviewer_id = patch["reviewer"]["reviewer_id"]
        if reviewer_id in generations["producer_ids"]:
            raise ContractError(
                "Reviewer identity overlaps an Artifact Generation producer"
            )
        for peer_owner in PRECOMPILE_OWNERS:
            if peer_owner == owner:
                continue
            peer_path = _patch_path(root, peer_owner)
            if peer_path.is_file():
                peer = read_json(peer_path)
                if peer.get("reviewer", {}).get("reviewer_id") == reviewer_id:
                    raise ContractError(
                        "Reviewer identities must be distinct across isolated tasks"
                    )
        destination = _patch_path(root, owner)
        commit_path = _commit_path(root, owner)
        if destination.exists():
            if destination.read_bytes() != canonical_json_bytes(patch):
                raise ContractError("Reviewer Patch is already committed immutably")
            if commit_path.exists():
                commit = read_json(commit_path)
                _require_fingerprint(commit, "commit_sha256", "Patch commit")
                if (
                    commit.get("state") != "committed"
                    or commit.get("patch_sha256") != patch["patch_sha256"]
                    or commit.get("skeleton_sha256")
                    != patch["skeleton_sha256"]
                ):
                    raise ContractError("existing Patch commit binding drifted")
                return {
                    "task_id": patch["task_id"],
                    "owner": owner,
                    "patch_sha256": patch["patch_sha256"],
                    "commit_sha256": commit["commit_sha256"],
                    "idempotent": True,
                    "recovered_partial_commit": False,
                    "activation_status": "target_only",
                }
            recovered_partial_commit = True
        else:
            recovered_partial_commit = False
            destination.parent.mkdir(parents=True, exist_ok=True)
            write_json_atomic(destination, patch)
            if fault_point == "after_patch_write":
                raise PrecompileFault(fault_point)
        commit = {
            "schema_name": "precompile-patch-commit",
            "schema_version": "1.0.0",
            "task_id": patch["task_id"],
            "owner": owner,
            "skeleton_sha256": patch["skeleton_sha256"],
            "patch_sha256": patch["patch_sha256"],
            "generation_set_sha256": patch["generation_set_sha256"],
            "committed_at": committed_at,
            "state": "committed",
        }
        commit["commit_sha256"] = hashlib.sha256(
            canonical_json_bytes(commit)
        ).hexdigest()
        commit_path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(commit_path, commit)
        return {
            "task_id": patch["task_id"],
            "owner": owner,
            "patch_sha256": patch["patch_sha256"],
            "commit_sha256": commit["commit_sha256"],
            "idempotent": False,
            "recovered_partial_commit": recovered_partial_commit,
            "activation_status": "target_only",
        }

    def materialize(
        self,
        *,
        workspace_root: Path,
        provider_id: str,
        provider_version: str,
        materialized_at: str,
        fault_point: str | None = None,
    ) -> dict[str, Any]:
        if (
            provider_id != PRECOMPILE_PROVIDER_ID
            or provider_version != PRECOMPILE_PROVIDER_VERSION
        ):
            raise ContractError("unregistered Precompile Quality provider identity")
        self.registry.check()
        root = workspace_root.resolve()
        generations = read_json(root / "artifact-generations.json")
        inventory = read_json(root / "reader-facing-text-inventory.json")
        dependencies = read_json(root / "semantic-dependencies.json")
        self._validate_generation_set(generations)
        self._validate_dependencies(dependencies)
        writing_projection, catalog_sha256, role_projections_sha256 = (
            self._writing_projection()
        )
        self._validate_inventory(inventory, generations, writing_projection)
        owner_reports = []
        failures = []
        contract_gaps = []
        reviewer_ids: set[str] = set()
        for owner in PRECOMPILE_OWNERS:
            skeleton = read_json(_skeleton_path(root, owner))
            patch = read_json(_patch_path(root, owner))
            commit = read_json(_commit_path(root, owner))
            _require_fingerprint(skeleton, "skeleton_sha256", "Reviewer Skeleton")
            self._validate_skeleton_current(root, skeleton, owner)
            self._validate_patch(patch, skeleton, owner)
            reviewer_id = patch["reviewer"]["reviewer_id"]
            if reviewer_id in generations["producer_ids"]:
                raise ContractError(
                    "Reviewer identity overlaps an Artifact Generation producer"
                )
            if reviewer_id in reviewer_ids:
                raise ContractError(
                    "Reviewer identities must be distinct across isolated tasks"
                )
            reviewer_ids.add(reviewer_id)
            _require_fingerprint(commit, "commit_sha256", "Patch commit")
            if (
                commit.get("state") != "committed"
                or commit.get("patch_sha256") != patch["patch_sha256"]
                or commit.get("generation_set_sha256")
                != generations["generation_set_sha256"]
            ):
                raise ContractError("Reviewer Patch commit binding is stale")
            owner_failures = [
                {
                    "owner": owner,
                    "result_key": item["result_key"],
                    "violation_id": item.get("violation_id"),
                    "evidence_locator": item["evidence_locator"],
                    "repair_write_set": item["repair_write_set"],
                }
                for item in patch["results"]
                if item["decision"] == "fail"
            ]
            failures.extend(owner_failures)
            contract_gaps.extend(
                {"owner": owner, **item} for item in patch["contract_gaps"]
            )
            owner_reports.append(
                {
                    "owner": owner,
                    "task_id": patch["task_id"],
                    "skeleton_sha256": patch["skeleton_sha256"],
                    "patch_sha256": patch["patch_sha256"],
                    "commit_sha256": commit["commit_sha256"],
                    "reviewer": patch["reviewer"],
                    "result_count": len(patch["results"]),
                    "decision": "fail" if owner_failures else "pass",
                }
            )
        if contract_gaps:
            brief = {
                "schema_name": "precompile-contract-gap-brief",
                "schema_version": "1.0.0",
                "generation_set_sha256": generations["generation_set_sha256"],
                "inventory_sha256": inventory["inventory_sha256"],
                "contract_gaps": contract_gaps,
                "semantic_attempt_budget_consumed": False,
                "routing": "human_policy_disposition_required",
            }
            brief["brief_sha256"] = hashlib.sha256(
                canonical_json_bytes(brief)
            ).hexdigest()
            path = root / "precompile-contract-gap-brief.json"
            write_json_atomic(path, brief)
            raise ContractError(
                "Delivery Quality Contract Gap blocks precompile materialization",
                data={
                    "contract_gap_count": len(contract_gaps),
                    "semantic_attempt_budget_consumed": False,
                    "evidence_path": str(path),
                },
            )
        provider_sha256 = hashlib.sha256(
            Path(__file__).read_bytes()
        ).hexdigest()
        report = {
            "schema_name": "precompile-quality-report",
            "schema_version": "1.0.0",
            "report_id": hashlib.sha256(
                (
                    generations["generation_set_sha256"]
                    + inventory["inventory_sha256"]
                    + "".join(item["patch_sha256"] for item in owner_reports)
                ).encode("utf-8")
            ).hexdigest()[:32],
            "activation_status": "target_only",
            "materialized_at": materialized_at,
            "generation_set_sha256": generations["generation_set_sha256"],
            "inventory_sha256": inventory["inventory_sha256"],
            "reader_text_set_sha256": inventory["reader_text_set_sha256"],
            "language_profile_id": inventory["language_profile_id"],
            "delivery_glossary": inventory.get("delivery_glossary"),
            "catalog_sha256": catalog_sha256,
            "role_projections_sha256": role_projections_sha256,
            "semantic_dependencies_sha256": dependencies["dependencies_sha256"],
            "provider": {
                "provider_id": provider_id,
                "provider_version": provider_version,
                "provider_sha256": provider_sha256,
            },
            "owner_reports": owner_reports,
            "failure_set": failures,
            "repair_routing": _route_failures(failures),
            "contract_gaps": [],
            "semantic_attempt_budget_consumed": bool(failures),
            "overall_decision": "fail" if failures else "pass",
        }
        report["report_sha256"] = hashlib.sha256(
            canonical_json_bytes(report)
        ).hexdigest()
        self.registry.validate("precompile-quality-report", report)
        path = root / "precompile-quality-report.json"
        _write_immutable_or_same(path, report)
        if fault_point == "after_report_write":
            raise PrecompileFault(fault_point)
        return {
            "report_id": report["report_id"],
            "report_sha256": report["report_sha256"],
            "overall_decision": report["overall_decision"],
            "failure_count": len(failures),
            "report_path": str(path),
            "activation_status": "target_only",
        }

    def seal(
        self,
        *,
        workspace_root: Path,
        sealed_at: str,
    ) -> dict[str, Any]:
        self.registry.check()
        root = workspace_root.resolve()
        report = read_json(root / "precompile-quality-report.json")
        _require_fingerprint(report, "report_sha256", "Precompile Quality Report")
        if report.get("overall_decision") != "pass" or report.get("contract_gaps"):
            raise ContractError(
                "passing Precompile Quality Report without Contract Gaps is required"
            )
        equivalence_path = root / "text-equivalence-report.json"
        equivalence = (
            read_json(equivalence_path) if equivalence_path.is_file() else None
        )
        if equivalence is None:
            generations = read_json(root / "artifact-generations.json")
            inventory = read_json(root / "reader-facing-text-inventory.json")
            decision_origin = "fresh_evaluation"
            predecessor_sha256 = None
            equivalence_sha256 = None
        else:
            _require_fingerprint(
                equivalence, "report_sha256", "Text Equivalence Report"
            )
            if equivalence.get("overall_decision") != "equivalent":
                raise ContractError("passing Text Equivalence Report is required")
            prior = read_json(root / "precompile-text-seal.json")
            _require_fingerprint(prior, "seal_sha256", "predecessor Seal")
            if equivalence.get("prior_seal_sha256") != prior["seal_sha256"]:
                raise ContractError("Text Equivalence Report predecessor is stale")
            generations = read_json(root / "successor/artifact-generations.json")
            inventory = read_json(
                root / "successor/reader-facing-text-inventory.json"
            )
            if (
                equivalence.get("successor_generation_set_sha256")
                != generations.get("generation_set_sha256")
                or equivalence.get("successor_inventory_sha256")
                != inventory.get("inventory_sha256")
            ):
                raise ContractError(
                    "Text Equivalence Report successor bindings are stale"
                )
            decision_origin = "reused_after_text_equivalence"
            predecessor_sha256 = prior["seal_sha256"]
            equivalence_sha256 = equivalence["report_sha256"]
        dependencies = read_json(root / "semantic-dependencies.json")
        self._validate_generation_set(generations)
        self._validate_dependencies(dependencies)
        writing_projection, catalog_sha256, projections_sha256 = (
            self._writing_projection()
        )
        self._validate_inventory(inventory, generations, writing_projection)
        provider = report.get("provider")
        current_provider_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        report_by_owner = {
            item.get("owner"): item for item in report.get("owner_reports", [])
        }
        if set(report_by_owner) != set(PRECOMPILE_OWNERS):
            raise ContractError("Precompile Quality Report owner set is stale")
        for owner in PRECOMPILE_OWNERS:
            skeleton = read_json(_skeleton_path(root, owner))
            patch = read_json(_patch_path(root, owner))
            commit = read_json(_commit_path(root, owner))
            _require_fingerprint(skeleton, "skeleton_sha256", "Reviewer Skeleton")
            self._validate_skeleton_current(root, skeleton, owner)
            self._validate_patch(patch, skeleton, owner)
            _require_fingerprint(commit, "commit_sha256", "Patch commit")
            owner_report = report_by_owner[owner]
            if (
                owner_report.get("skeleton_sha256")
                != skeleton["skeleton_sha256"]
                or owner_report.get("patch_sha256") != patch["patch_sha256"]
                or owner_report.get("commit_sha256") != commit["commit_sha256"]
                or commit.get("state") != "committed"
            ):
                raise ContractError(
                    "Precompile Quality Report Reviewer inputs are stale"
                )
        if (
            (
                equivalence is None
                and (
                    report["generation_set_sha256"]
                    != generations["generation_set_sha256"]
                    or report["inventory_sha256"] != inventory["inventory_sha256"]
                )
            )
            or report["semantic_dependencies_sha256"]
            != dependencies["dependencies_sha256"]
            or report.get("catalog_sha256") != catalog_sha256
            or report.get("role_projections_sha256") != projections_sha256
            or report.get("language_profile_id")
            != inventory.get("language_profile_id")
            or report.get("delivery_glossary")
            != inventory.get("delivery_glossary")
            or provider
            != {
                "provider_id": PRECOMPILE_PROVIDER_ID,
                "provider_version": PRECOMPILE_PROVIDER_VERSION,
                "provider_sha256": current_provider_sha256,
            }
        ):
            raise ContractError("Precompile Quality Report is stale")
        seal = {
            "schema_name": "precompile-text-seal",
            "schema_version": "1.0.0",
            "seal_id": hashlib.sha256(
                (
                    report["report_sha256"]
                    + generations["generation_set_sha256"]
                ).encode("utf-8")
            ).hexdigest()[:32],
            "activation_status": "target_only",
            "sealed_at": sealed_at,
            "decision_origin": decision_origin,
            "generation_set_sha256": generations["generation_set_sha256"],
            "catalog_sha256": report["catalog_sha256"],
            "role_projections_sha256": report["role_projections_sha256"],
            "language_profile_id": report["language_profile_id"],
            "delivery_glossary": report.get("delivery_glossary"),
            "semantic_dependencies_sha256": report[
                "semantic_dependencies_sha256"
            ],
            "inventory_sha256": inventory["inventory_sha256"],
            "reader_text_set_sha256": inventory["reader_text_set_sha256"],
            "precompile_quality_report_sha256": report["report_sha256"],
            "provider": report["provider"],
            "predecessor_seal_sha256": predecessor_sha256,
            "text_equivalence_report_sha256": equivalence_sha256,
        }
        seal["seal_sha256"] = hashlib.sha256(
            canonical_json_bytes(seal)
        ).hexdigest()
        self.registry.validate("precompile-text-seal", seal)
        path = root / "precompile-text-seal.json"
        archive_root = root / "seals"
        archive_root.mkdir(parents=True, exist_ok=True)
        if path.exists():
            current = read_json(path)
            _require_fingerprint(current, "seal_sha256", "current Seal")
            current_archive = archive_root / f"{current['seal_sha256']}.json"
            if (
                current_archive.exists()
                and current_archive.read_bytes() != canonical_json_bytes(current)
            ):
                raise ContractError("archived Precompile Text Seal drifted")
            write_json_atomic(current_archive, current)
        write_json_atomic(path, seal)
        write_json_atomic(archive_root / f"{seal['seal_sha256']}.json", seal)
        binding_root = root / "seal-bindings" / seal["seal_sha256"]
        _write_immutable_or_same(
            binding_root / "artifact-generations.json", generations
        )
        _write_immutable_or_same(
            binding_root / "reader-facing-text-inventory.json", inventory
        )
        return {
            "seal_id": seal["seal_id"],
            "seal_sha256": seal["seal_sha256"],
            "decision_origin": seal["decision_origin"],
            "seal_path": str(path),
            "activation_status": "target_only",
        }

    def prove_text_equivalence(
        self,
        *,
        workspace_root: Path,
        successor_inventory_path: Path,
        successor_artifact_generations_path: Path,
        mutation_class: str,
        proved_at: str,
    ) -> dict[str, Any]:
        if mutation_class != "presentation_only":
            raise ContractError(
                "only a classified presentation_only mutation can reuse judgment"
            )
        self.registry.check()
        root = workspace_root.resolve()
        prior_seal = read_json(root / "precompile-text-seal.json")
        _require_fingerprint(prior_seal, "seal_sha256", "predecessor Seal")
        prior_binding_root = root / "seal-bindings" / prior_seal["seal_sha256"]
        prior_inventory = read_json(
            prior_binding_root / "reader-facing-text-inventory.json"
        )
        prior_generations = read_json(
            prior_binding_root / "artifact-generations.json"
        )
        if (
            prior_inventory.get("inventory_sha256")
            != prior_seal.get("inventory_sha256")
            or prior_generations.get("generation_set_sha256")
            != prior_seal.get("generation_set_sha256")
        ):
            raise ContractError("predecessor Seal binding snapshot is stale")
        successor_inventory = read_json(successor_inventory_path.resolve())
        successor_generations = read_json(
            successor_artifact_generations_path.resolve()
        )
        self._validate_generation_set(successor_generations)
        writing_projection, catalog_sha256, role_projections_sha256 = (
            self._writing_projection()
        )
        self._validate_inventory(
            successor_inventory,
            successor_generations,
            writing_projection,
        )
        prior_items = {
            item["item_id"]: item for item in prior_inventory["items"]
        }
        successor_items = {
            item["item_id"]: item for item in successor_inventory["items"]
        }
        shared_ids = sorted(set(prior_items) & set(successor_items))
        mapping = []
        for item_id in shared_ids:
            prior_item = prior_items[item_id]
            successor_item = successor_items[item_id]
            equivalent = all(
                prior_item.get(field) == successor_item.get(field)
                for field in (
                    "kind",
                    "semantic_region",
                    "language_profile_id",
                    "representation",
                    "text_sha256",
                    "applicable_rule_ids",
                )
            )
            mapping.append(
                {
                    "item_id": item_id,
                    "prior_locator": prior_item["locator"],
                    "successor_locator": successor_item["locator"],
                    "prior_text_sha256": prior_item["text_sha256"],
                    "successor_text_sha256": successor_item["text_sha256"],
                    "equivalent": equivalent,
                }
            )
        checks = {
            "stable_item_identity_bijection": (
                set(prior_items) == set(successor_items)
                and len(mapping) == len(prior_items)
                and all(item["equivalent"] for item in mapping)
            ),
            "reader_text_set_unchanged": (
                prior_inventory["reader_text_set_sha256"]
                == successor_inventory["reader_text_set_sha256"]
            ),
            "declared_surface_unchanged": (
                prior_inventory["declared_surface"]
                == successor_inventory["declared_surface"]
            ),
            "catalog_unchanged": prior_seal["catalog_sha256"] == catalog_sha256,
            "projections_unchanged": (
                prior_seal["role_projections_sha256"]
                == role_projections_sha256
            ),
            "language_profile_unchanged": (
                prior_inventory["language_profile_id"]
                == successor_inventory["language_profile_id"]
            ),
            "delivery_glossary_unchanged": (
                prior_inventory.get("delivery_glossary")
                == successor_inventory.get("delivery_glossary")
            ),
            "semantic_dependencies_unchanged": (
                prior_seal["semantic_dependencies_sha256"]
                == read_json(root / "semantic-dependencies.json")[
                    "dependencies_sha256"
                ]
            ),
            "generation_advanced": (
                prior_generations["generation_set_sha256"]
                != successor_generations["generation_set_sha256"]
            ),
        }
        report = {
            "schema_name": "text-equivalence-report",
            "schema_version": "1.0.0",
            "activation_status": "target_only",
            "proved_at": proved_at,
            "mutation_class": mutation_class,
            "prior_seal_sha256": prior_seal["seal_sha256"],
            "prior_inventory_sha256": prior_inventory["inventory_sha256"],
            "successor_inventory_sha256": successor_inventory[
                "inventory_sha256"
            ],
            "prior_generation_set_sha256": prior_generations[
                "generation_set_sha256"
            ],
            "successor_generation_set_sha256": successor_generations[
                "generation_set_sha256"
            ],
            "item_mapping": mapping,
            "checks": checks,
            "contract_gaps": [],
            "overall_decision": (
                "equivalent" if all(checks.values()) else "different"
            ),
        }
        report["report_sha256"] = hashlib.sha256(
            canonical_json_bytes(report)
        ).hexdigest()
        self.registry.validate("text-equivalence-report", report)
        write_json_atomic(root / "text-equivalence-report.json", report)
        if report["overall_decision"] != "equivalent":
            raise TextEquivalenceRejected(
                "reader-facing text or a semantic dependency changed",
                data={
                    "report_sha256": report["report_sha256"],
                    "failed_checks": [
                        key for key, passed in checks.items() if not passed
                    ],
                    "evidence_path": str(root / "text-equivalence-report.json"),
                },
            )
        successor_root = root / "successor"
        successor_root.mkdir(parents=True, exist_ok=True)
        write_json_atomic(
            successor_root / "artifact-generations.json",
            successor_generations,
        )
        write_json_atomic(
            successor_root / "reader-facing-text-inventory.json",
            successor_inventory,
        )
        return {
            "report_sha256": report["report_sha256"],
            "prior_seal_sha256": prior_seal["seal_sha256"],
            "successor_generation_set_sha256": successor_generations[
                "generation_set_sha256"
            ],
            "item_count": len(mapping),
            "report_path": str(root / "text-equivalence-report.json"),
            "activation_status": "target_only",
        }

    def _validate_patch(
        self,
        patch: Any,
        skeleton: dict[str, Any],
        owner: str,
    ) -> None:
        if not isinstance(patch, dict):
            raise ContractError("Reviewer Patch must be an object")
        if (
            patch.get("schema_name") != "precompile-judgment-patch"
            or patch.get("schema_version") != "1.0.0"
        ):
            raise ContractError("unsupported Reviewer Patch contract")
        _require_fingerprint(patch, "patch_sha256", "Reviewer Patch")
        for field in (
            "task_id",
            "owner",
            "skeleton_sha256",
            "generation_set_sha256",
        ):
            expected = skeleton[field] if field != "owner" else owner
            if patch.get(field) != expected:
                raise ContractError(f"Reviewer Patch {field} is unauthorized or stale")
        reviewer = patch.get("reviewer")
        if (
            not isinstance(reviewer, dict)
            or not reviewer.get("reviewer_id")
            or not _is_sha256(reviewer.get("runtime_sha256"))
            or reviewer.get("independent_from_generation_producers") is not True
        ):
            raise ContractError("Reviewer independence provenance is incomplete")
        results = patch.get("results")
        if not isinstance(results, list):
            raise ContractError("Reviewer Patch results must be a list")
        expected_keys = {
            item["result_key"] for item in skeleton["required_results"]
        }
        actual_keys = [item.get("result_key") for item in results]
        if (
            set(actual_keys) != expected_keys
            or len(actual_keys) != len(expected_keys)
        ):
            raise ContractError(
                "Reviewer Patch does not cover the fixed Skeleton result set"
            )
        for item in results:
            if item.get("decision") not in {"pass", "fail"}:
                raise ContractError("Reviewer result decision is invalid")
            if not item.get("evidence_locator") or not isinstance(
                item.get("repair_write_set"), list
            ):
                raise ContractError("Reviewer result evidence or repair boundary is missing")
            if item["decision"] == "fail" and not item.get("violation_id"):
                raise ContractError("failed Reviewer result lacks violation identity")
            if item["decision"] == "pass" and item.get("violation_id") is not None:
                raise ContractError("passing Reviewer result cannot carry a violation")
        gaps = patch.get("contract_gaps")
        if not isinstance(gaps, list):
            raise ContractError("Reviewer Patch Contract Gaps must be a list")
        for gap in gaps:
            if not all(
                gap.get(field)
                for field in ("gap_id", "observation", "evidence_locator")
            ):
                raise ContractError("Reviewer Contract Gap is incomplete")

    def _validate_generation_set(self, value: Any) -> None:
        if not isinstance(value, dict):
            raise ContractError("Artifact Generation set must be an object")
        if (
            value.get("schema_name") != "precompile-artifact-generation-set"
            or value.get("schema_version") != "1.0.0"
        ):
            raise ContractError("unsupported Artifact Generation set contract")
        _require_fingerprint(
            value, "generation_set_sha256", "Artifact Generation set"
        )
        artifacts = value.get("artifacts")
        if not isinstance(artifacts, list) or not artifacts:
            raise ContractError("Artifact Generation set must be non-empty")
        producer_ids = value.get("producer_ids")
        if (
            not isinstance(producer_ids, list)
            or not producer_ids
            or any(not isinstance(item, str) or not item for item in producer_ids)
            or len(producer_ids) != len(set(producer_ids))
        ):
            raise ContractError(
                "Artifact Generation producer identities must be non-empty and unique"
            )
        logical_ids = [item.get("logical_id") for item in artifacts]
        if len(logical_ids) != len(set(logical_ids)):
            raise ContractError("Artifact Generation logical identities must be unique")
        for item in artifacts:
            if (
                not isinstance(item.get("generation"), int)
                or item["generation"] < 1
                or not _is_sha256(item.get("sha256"))
            ):
                raise ContractError("Artifact Generation binding is invalid")

    def _validate_dependencies(self, value: Any) -> None:
        if not isinstance(value, dict):
            raise ContractError("semantic dependencies must be an object")
        if (
            value.get("schema_name") != "precompile-semantic-dependencies"
            or value.get("schema_version") != "1.0.0"
        ):
            raise ContractError("unsupported semantic dependencies contract")
        _require_fingerprint(value, "dependencies_sha256", "semantic dependencies")
        dependencies = value.get("dependencies")
        if not isinstance(dependencies, list):
            raise ContractError("semantic dependencies must contain a list")
        owners = [item.get("owner") for item in dependencies]
        expected = set(PRECOMPILE_OWNERS) - {"writing-quality-reviewer"}
        if set(owners) != expected or len(owners) != len(expected):
            raise ContractError(
                "semantic dependencies must bind Source-Faithfulness and Pyramid once"
            )
        for item in dependencies:
            if (
                not item.get("projection_id")
                or not _is_sha256(item.get("projection_sha256"))
                or not item.get("provider_id")
                or not _is_sha256(item.get("provider_sha256"))
                or not item.get("required_scope_ids")
            ):
                raise ContractError("semantic dependency binding is incomplete")

    def _writing_projection(self) -> tuple[dict[str, Any], str, str]:
        catalog_path = self.project_root / "delivery-quality/v1/rule-catalog.v1.json"
        projections_path = (
            self.project_root / "delivery-quality/v1/role-projections.v1.json"
        )
        projections = read_json(projections_path)
        writing = next(
            item
            for item in projections["projections"]
            if item["projection_id"] == "writing-quality-evaluation"
        )
        return (
            writing,
            hashlib.sha256(catalog_path.read_bytes()).hexdigest(),
            hashlib.sha256(projections_path.read_bytes()).hexdigest(),
        )

    def _validate_inventory(
        self,
        inventory: Any,
        generations: dict[str, Any],
        writing_projection: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if not isinstance(inventory, dict):
            raise ContractError("Reader-Facing Text Inventory must be an object")
        if (
            inventory.get("schema_name") != "reader-facing-text-inventory"
            or inventory.get("schema_version") != "1.0.0"
        ):
            raise ContractError("unsupported Reader-Facing Text Inventory contract")
        _require_fingerprint(inventory, "inventory_sha256", "inventory")
        if inventory.get("generation_set_sha256") != generations.get(
            "generation_set_sha256"
        ):
            raise ContractError("inventory and Artifact Generations are stale")
        items = inventory.get("items")
        surface = inventory.get("declared_surface")
        coverage = inventory.get("coverage_ledger")
        if not isinstance(items, list) or not items:
            raise ContractError("Reader-Facing Text Inventory must be non-empty")
        if not isinstance(surface, list) or not isinstance(coverage, list):
            raise ContractError("inventory surface coverage is missing")
        region_ids = [item.get("region_id") for item in surface]
        item_ids = [item.get("item_id") for item in items]
        covered_ids = [
            item.get("region_id")
            for item in coverage
            if item.get("status") == "covered"
        ]
        if (
            len(region_ids) != len(set(region_ids))
            or len(item_ids) != len(set(item_ids))
            or set(region_ids) != set(item_ids)
            or set(covered_ids) != set(region_ids)
            or len(coverage) != len(region_ids)
        ):
            raise ContractError(
                "Reader-Facing Text Inventory lacks bijective declared-surface coverage"
            )
        generation_by_id = {
            item["logical_id"]: item for item in generations["artifacts"]
        }
        profiles = read_json(
            self.project_root / "delivery-quality/v1/language-profiles.v1.json"
        )
        registered_profiles = {
            item["profile_id"] for item in profiles["profiles"]
        }
        if inventory.get("language_profile_id") not in registered_profiles:
            raise ContractError("inventory cites an unregistered Language Profile")
        projected_rules = {
            item["rule_id"]: item for item in writing_projection["rules"]
        }
        glossary = inventory.get("delivery_glossary")
        if glossary is not None and (
            not isinstance(glossary, dict)
            or not glossary.get("glossary_id")
            or not _is_sha256(glossary.get("sha256"))
        ):
            raise ContractError("Delivery Glossary identity is invalid")
        required_results: list[dict[str, Any]] = []
        text_set_material = []
        for item in items:
            _require_fingerprint(item, "item_sha256", f"item {item.get('item_id')}")
            representation = item.get("representation")
            if representation == "missing_raster_text" or (
                representation == "authoritative_raster_text"
                and not _is_sha256(item.get("text_sha256"))
            ):
                raise ContractError(
                    "raster reader-facing text lacks authoritative representation"
                )
            if representation not in {
                "structured_text",
                "authoritative_raster_text",
                "declared_generated_text",
            }:
                raise ContractError("unsupported reader-facing text representation")
            if not _is_sha256(item.get("text_sha256")):
                raise ContractError("reader-facing text fingerprint is invalid")
            source = generation_by_id.get(item.get("source_artifact_logical_id"))
            if (
                source is None
                or source["generation"] != item.get("source_generation")
                or source["sha256"] != item.get("source_sha256")
            ):
                raise ContractError("inventory item has stale source generation")
            if item.get("language_profile_id") != inventory.get(
                "language_profile_id"
            ):
                raise ContractError("inventory item language profile drifted")
            rule_ids = item.get("applicable_rule_ids")
            if not isinstance(rule_ids, list) or len(rule_ids) != len(set(rule_ids)):
                raise ContractError("applicable rule identities must be unique")
            unknown = set(rule_ids) - set(projected_rules)
            if unknown:
                raise ContractError("inventory cites unknown Writing Quality rule")
            if "no_meta_writing_content" not in rule_ids:
                raise ContractError(
                    "every reader-facing item requires no-meta Writing Quality coverage"
                )
            if (
                "delivery_glossary_term_strategy" in rule_ids
                and glossary is None
            ):
                raise ContractError(
                    "Delivery Glossary rule is applicable without a Glossary binding"
                )
            for rule_id in rule_ids:
                required_results.append(
                    {
                        "rule_id": rule_id,
                        "rule_semantic_sha256": projected_rules[rule_id][
                            "rule_semantic_sha256"
                        ],
                        "item_id": item["item_id"],
                        "item_sha256": item["item_sha256"],
                        "result_key": f"{rule_id}:{item['item_id']}",
                    }
                )
            text_set_material.append(
                {
                    "item_id": item["item_id"],
                    "kind": item["kind"],
                    "representation": item["representation"],
                    "text_sha256": item["text_sha256"],
                }
            )
        if inventory.get("reader_text_set_sha256") != hashlib.sha256(
            canonical_json_bytes(text_set_material)
        ).hexdigest():
            raise ContractError("Reader-Facing Text Inventory text-set identity drifted")
        extractors = inventory.get("extractors")
        if not isinstance(extractors, list) or not extractors:
            raise ContractError("Reader-Facing Text Inventory extractor set is missing")
        extractor_ids = [item.get("extractor_id") for item in extractors]
        if (
            any(not item for item in extractor_ids)
            or len(extractor_ids) != len(set(extractor_ids))
            or any(not _is_sha256(item.get("extractor_sha256")) for item in extractors)
        ):
            raise ContractError("Reader-Facing Text Inventory extractor identity is invalid")
        return sorted(required_results, key=lambda item: item["result_key"])

    def _validate_skeleton_current(
        self,
        root: Path,
        skeleton: dict[str, Any],
        owner: str,
    ) -> None:
        generations = read_json(root / "artifact-generations.json")
        inventory = read_json(root / "reader-facing-text-inventory.json")
        dependencies = read_json(root / "semantic-dependencies.json")
        self._validate_generation_set(generations)
        self._validate_dependencies(dependencies)
        writing_projection, catalog_sha256, projections_sha256 = (
            self._writing_projection()
        )
        self._validate_inventory(inventory, generations, writing_projection)
        if (
            skeleton.get("generation_set_sha256")
            != generations["generation_set_sha256"]
            or skeleton.get("inventory_sha256") != inventory["inventory_sha256"]
            or skeleton.get("language_profile_id")
            != inventory["language_profile_id"]
            or skeleton.get("delivery_glossary")
            != inventory.get("delivery_glossary")
        ):
            raise ContractError("Reviewer Skeleton has stale artifact bindings")
        projection = skeleton.get("projection")
        if not isinstance(projection, dict):
            raise ContractError("Reviewer Skeleton projection is stale")
        expected = self._owner_projection(
            owner,
            writing_projection,
            catalog_sha256,
            projections_sha256,
            dependencies,
        )
        if projection != expected:
            raise ContractError(
                "Reviewer Skeleton has stale policy, projection, or provider identity"
            )

    @staticmethod
    def _owner_projection(
        owner: str,
        writing_projection: dict[str, Any],
        catalog_sha256: str,
        projections_sha256: str,
        dependencies: dict[str, Any],
    ) -> dict[str, Any]:
        if owner == "writing-quality-reviewer":
            return {
                "projection_id": writing_projection["projection_id"],
                "projection_sha256": projections_sha256,
                "catalog_sha256": catalog_sha256,
            }
        dependency = next(
            item
            for item in dependencies["dependencies"]
            if item["owner"] == owner
        )
        return {
            "projection_id": dependency["projection_id"],
            "projection_sha256": dependency["projection_sha256"],
            "provider_id": dependency["provider_id"],
            "provider_sha256": dependency["provider_sha256"],
        }


def _is_sha256(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    return all(char in "0123456789abcdef" for char in value)


def _write_immutable_or_same(path: Path, value: Any) -> None:
    expected = canonical_json_bytes(value)
    if path.exists():
        if path.read_bytes() != expected:
            raise ContractError(f"immutable precompile artifact drifted: {path.name}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, value)


def _route_failures(failures: list[dict[str, Any]]) -> dict[str, Any]:
    if not failures:
        return {
            "parallel_repair_tasks": [],
            "integration_repair_tasks": [],
        }
    adjacency = {index: set() for index in range(len(failures))}
    write_sets = [set(item["repair_write_set"]) for item in failures]
    for left in range(len(failures)):
        for right in range(left + 1, len(failures)):
            if write_sets[left] & write_sets[right]:
                adjacency[left].add(right)
                adjacency[right].add(left)
    visited: set[int] = set()
    parallel = []
    integrated = []
    for start in range(len(failures)):
        if start in visited:
            continue
        stack = [start]
        component = []
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            component.append(current)
            stack.extend(adjacency[current] - visited)
        selected = [failures[index] for index in sorted(component)]
        task = {
            "failure_keys": [
                f"{item['owner']}:{item['result_key']}" for item in selected
            ],
            "write_set": sorted(
                {path for index in component for path in write_sets[index]}
            ),
        }
        if len(component) > 1:
            task["task_kind"] = "integration_repair"
            integrated.append(task)
        else:
            task["task_kind"] = "content_repair"
            parallel.append(task)
    return {
        "parallel_repair_tasks": parallel,
        "integration_repair_tasks": integrated,
    }
