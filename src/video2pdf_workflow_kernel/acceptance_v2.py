from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
from pathlib import Path
import sqlite3
from typing import Any

from .errors import AcceptanceV2Fault, AcceptanceV2Rejected, ContractError, KernelError
from .contracts import ContractRegistry
from .control_store import ControlStore
from .delivery_quality import DeliveryQualityRegistry
from .utils import canonical_json_bytes, read_json, sha256_file, write_json_atomic
from .global_gate import (
    GlobalGatePublisher,
    OPTIONAL_ACCEPTANCE_QUALITY_INPUTS,
    REQUIRED_ACCEPTANCE_QUALITY_INPUTS,
)


DIMENSION_CRITERIA = {
    "visual_quality": (
        "figure_visual_integrity",
        "table_layout_integrity",
        "credibility_disclosure_rendered_placement",
    ),
}
PREPARE_FAULT_POINTS = {"after_prepare_control_commit"}
PATCH_FAULT_POINTS = {"after_patch_publish", "after_patch_control_commit"}
MATERIALIZE_FAULT_POINTS = {"after_report_publish", "after_report_control_commit"}
ATTEMPT_LIMIT = 3
CONTROL_DB_NAME = "acceptance-control.sqlite3"
FINAL_AUTHORITY_DB_NAME = "acceptance-v2-final-authority.sqlite3"
JUDGMENT_DEPENDENCIES = {
    "source-faithfulness-reviewer": frozenset({"main_tex"}),
    "writing-quality-reviewer": frozenset({"main_tex"}),
    "pyramid-reviewer": frozenset({"main_tex"}),
    "visual_quality": frozenset({"final_pdf", "main_tex"}),
}


def _fingerprint_without(value: dict[str, Any], field: str) -> str:
    material = {key: item for key, item in value.items() if key != field}
    return hashlib.sha256(canonical_json_bytes(material)).hexdigest()


def _id(*parts: object, length: int = 32) -> str:
    return hashlib.sha256("\0".join(str(part) for part in parts).encode()).hexdigest()[:length]


def _artifact_set_sha(artifacts: list[dict[str, Any]], pages: list[dict[str, Any]]) -> str:
    return hashlib.sha256(
        canonical_json_bytes({
            "artifacts": [
                {"logical_id": item["logical_id"], "sha256": item["sha256"]}
                for item in artifacts
            ],
            "rendered_pages": [
                {"page": item["page"], "sha256": item["sha256"]}
                for item in pages
            ],
        })
    ).hexdigest()


def _final_authority_generations(binding: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"logical_id": "kernel_run_record", "path": binding["run"]["run_record_path"], "sha256": binding["run"]["run_record_sha256"]},
        *[
            {"logical_id": f"artifact:{item['logical_id']}", "path": item["path"], "sha256": item["sha256"]}
            for item in binding["artifacts"]
        ],
        *[
            {"logical_id": f"quality:{logical_id}", "path": item["path"], "sha256": item["sha256"]}
            for logical_id, item in sorted(binding["quality_inputs"].items())
        ],
        *[
            {"logical_id": f"rendered_page:{item['page']}", "path": item["path"], "sha256": item["sha256"]}
            for item in binding["rendered_pages"]
        ],
    ]


def _report_bundle_sha(report_sha256: str, attempt_record_sha256: str, ledger_sha256: str) -> str:
    return hashlib.sha256(canonical_json_bytes({
        "report_sha256": report_sha256,
        "attempt_record_sha256": attempt_record_sha256,
        "ledger_sha256": ledger_sha256,
    })).hexdigest()


def _reject(message: str, gate: str, code: str, **data: Any) -> None:
    raise AcceptanceV2Rejected(
        message,
        data={"first_failing_gate": gate, "error_code": code, **data},
    )


def _require_shape(value: Any, required: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not required <= set(value):
        _reject(f"{label} has missing fields", "contract_shape", "acceptance_contract_invalid")
    return value


@dataclass(frozen=True)
class AcceptanceInputDomain:
    """Track-independent Acceptance input authority used by every provider phase."""

    track: str
    fingerprint: str
    video_root: Path
    artifacts: list[dict[str, Any]]
    quality_inputs: dict[str, dict[str, Any]]
    pages: list[dict[str, Any]]
    global_gate_authority: dict[str, Any]
    run: dict[str, Any] | None
    legacy_authority: dict[str, Any] | None
    producer_ids: frozenset[str]
    repairer_ids: frozenset[str]
    changed_generations: frozenset[str]
    predecessor_generation_set_sha256: str | None

    @classmethod
    def from_binding(cls, binding: dict[str, Any]) -> "AcceptanceInputDomain":
        track = binding.get("input_track")
        if track == "legacy" and binding.get("schema_name") == "legacy-acceptance-input-set":
            pages_container = binding.get("rendered_pages")
            pages = pages_container.get("pages") if isinstance(pages_container, dict) else None
            required = ("input_set_sha256", "video_output_dir", "artifacts", "quality_inputs", "global_gate_authority")
            if any(key not in binding for key in required) or not isinstance(pages, list):
                _reject("Legacy Acceptance domain is incomplete", "contract_shape", "legacy_acceptance_input_contract_invalid")
            return cls(
                track="legacy",
                fingerprint=binding["input_set_sha256"],
                video_root=Path(binding["video_output_dir"]).resolve(),
                artifacts=binding["artifacts"],
                quality_inputs=binding["quality_inputs"],
                pages=pages,
                global_gate_authority=binding["global_gate_authority"],
                run=None,
                legacy_authority={
                    "input_set_id": binding["input_set_id"],
                    "video_output_dir": binding["video_output_dir"],
                    "provider": binding["provider"],
                    "adopted_at": binding["adopted_at"],
                },
                producer_ids=frozenset(),
                repairer_ids=frozenset(),
                changed_generations=frozenset(),
                predecessor_generation_set_sha256=None,
            )
        if track == "kernel" and binding.get("schema_name") == "acceptance-v2-input-binding":
            run = binding.get("run")
            required = ("binding_sha256", "artifacts", "quality_inputs", "rendered_pages", "global_gate_authority")
            if any(key not in binding for key in required) or not isinstance(run, dict):
                _reject("Kernel Acceptance domain is incomplete", "contract_shape", "acceptance_input_contract_invalid")
            return cls(
                track="kernel",
                fingerprint=binding["binding_sha256"],
                video_root=Path(run.get("video_root", "")).resolve(),
                artifacts=binding["artifacts"],
                quality_inputs=binding["quality_inputs"],
                pages=binding["rendered_pages"],
                global_gate_authority=binding["global_gate_authority"],
                run=run,
                legacy_authority=None,
                producer_ids=frozenset(run.get("producer_ids", [])),
                repairer_ids=frozenset(run.get("repairer_ids", [])),
                changed_generations=frozenset(run.get("changed_generation_ids", [])),
                predecessor_generation_set_sha256=run.get("predecessor_generation_set_sha256"),
            )
        _reject("Acceptance input identity is unsupported", "input_identity", "acceptance_input_identity_unsupported")


class AcceptanceV2Provider:
    """Target-only Final Acceptance transaction and materialization boundary.

    This module intentionally has no dependency on the active Legacy Delivery Guard.
    The Global Gate Cutover owns activation and Legacy input adaptation.
    """

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.registry = DeliveryQualityRegistry(self.project_root)

    def _evaluation_rules(self) -> dict[str, list[dict[str, Any]]]:
        projections = read_json(self.project_root / "delivery-quality/v1/role-projections.v1.json")
        result = {}
        for projection in projections["projections"]:
            if projection["projection_id"] in {"writing-quality-evaluation", "visual-quality-evaluation"}:
                result[projection["projection_id"]] = projection["rules"]
        if set(result) != {"writing-quality-evaluation", "visual-quality-evaluation"}:
            _reject("evaluation projections are incomplete", "policy_binding", "acceptance_projection_incomplete")
        return result

    @staticmethod
    def _connect_control(root: Path) -> sqlite3.Connection:
        connection = sqlite3.connect(root / CONTROL_DB_NAME, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS execution_authority (singleton INTEGER PRIMARY KEY CHECK(singleton=1), execution_id TEXT NOT NULL, execution_revision INTEGER NOT NULL, state TEXT NOT NULL, binding_sha256 TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS reviewer_claims (task_id TEXT PRIMARY KEY, execution_id TEXT NOT NULL, claim_generation INTEGER NOT NULL, fencing_token TEXT NOT NULL, skeleton_sha256 TEXT NOT NULL, state TEXT NOT NULL)"
        )
        claim_columns = {row[1] for row in connection.execute("PRAGMA table_info(reviewer_claims)")}
        if "skeleton_sha256" not in claim_columns:
            connection.execute("ALTER TABLE reviewer_claims ADD COLUMN skeleton_sha256 TEXT")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS publication_intents (intent_id TEXT PRIMARY KEY, execution_id TEXT NOT NULL, expected_revision INTEGER NOT NULL, kind TEXT NOT NULL, state TEXT NOT NULL, artifact_sha256 TEXT NOT NULL)"
        )
        return connection

    @staticmethod
    def _connect_final_authority(control_store_root: Path) -> sqlite3.Connection:
        connection = sqlite3.connect(control_store_root / FINAL_AUTHORITY_DB_NAME, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS final_quality_authority (run_id TEXT PRIMARY KEY, acceptance_revision INTEGER NOT NULL, run_record_sha256 TEXT NOT NULL, authority_path TEXT NOT NULL, authority_sha256 TEXT NOT NULL)"
        )
        return connection

    def publish_final_authority(self, *, input_binding_path: Path) -> dict[str, Any]:
        binding = read_json(input_binding_path.resolve())
        self._validate_binding(binding, verify_files=True, require_published_final_authority=False)
        run = binding["run"]
        checkpoint = run["final_checkpoint"]
        control_store_root = Path(run["control_store_root"]).resolve()
        values = (
            run["run_id"], run["acceptance_revision"], run["run_record_sha256"],
            str(Path(checkpoint["authority_path"]).resolve()), checkpoint["authority_sha256"],
        )
        with self._connect_final_authority(control_store_root) as control:
            control.execute("BEGIN IMMEDIATE")
            current = control.execute("SELECT * FROM final_quality_authority WHERE run_id=?", (run["run_id"],)).fetchone()
            if current is not None and current["acceptance_revision"] > run["acceptance_revision"]:
                control.execute("ROLLBACK")
                _reject("Final Quality authority revision is stale", "run_final_quality_authority", "acceptance_final_authority_revision_stale")
            if current is not None and current["acceptance_revision"] == run["acceptance_revision"]:
                if tuple(current[key] for key in ("run_id", "acceptance_revision", "run_record_sha256", "authority_path", "authority_sha256")) != values:
                    control.execute("ROLLBACK")
                    _reject("Final Quality authority revision conflicts", "run_final_quality_authority", "acceptance_final_authority_conflict")
                control.execute("COMMIT")
                return {"run_id": run["run_id"], "acceptance_revision": run["acceptance_revision"], "authority_sha256": checkpoint["authority_sha256"], "idempotent": True, "activation_status": "active_global_gate"}
            control.execute(
                "INSERT OR REPLACE INTO final_quality_authority(run_id,acceptance_revision,run_record_sha256,authority_path,authority_sha256) VALUES(?,?,?,?,?)",
                values,
            )
            control.execute("COMMIT")
        return {"run_id": run["run_id"], "acceptance_revision": run["acceptance_revision"], "authority_sha256": checkpoint["authority_sha256"], "idempotent": False, "activation_status": "active_global_gate"}

    def prepare(
        self,
        *,
        workspace_root: Path,
        input_binding_path: Path,
        attempt_number: int,
        prepared_at: str,
        fault_point: str | None = None,
    ) -> dict[str, Any]:
        root = workspace_root.resolve()
        binding = read_json(input_binding_path.resolve())
        self._validate_binding(binding, verify_files=True)
        domain = AcceptanceInputDomain.from_binding(binding)
        expected_root = (domain.video_root / "review" / "acceptance").resolve()
        if root != expected_root:
            _reject("Acceptance workspace is not the canonical video authority", "workspace_authority", "acceptance_workspace_authority_invalid")
        if attempt_number != 1:
            _reject(
                "initial acceptance preparation requires attempt 1",
                "repair_budget",
                "acceptance_attempt_sequence_invalid",
            )
        ledger = {
            "attempt_limit": ATTEMPT_LIMIT,
            "semantic_attempts": [],
            "contract_gap_cycles": [],
        }
        ledger["ledger_sha256"] = _fingerprint_without(ledger, "ledger_sha256")
        return self._prepare_execution(
            root=root,
            binding=binding,
            attempt_number=attempt_number,
            prepared_at=prepared_at,
            ledger=ledger,
            fault_point=fault_point,
        )

    def _prepare_execution(
        self,
        *,
        root: Path,
        binding: dict[str, Any],
        attempt_number: int,
        prepared_at: str,
        ledger: dict[str, Any],
        fault_point: str | None = None,
    ) -> dict[str, Any]:
        root.mkdir(parents=True, exist_ok=True)
        self.registry.validate("acceptance-v2-repair-ledger", ledger)
        domain = AcceptanceInputDomain.from_binding(binding)
        binding_sha = domain.fingerprint
        execution_id = _id(binding_sha, attempt_number, prepared_at)
        execution_root = root / "executions" / execution_id
        evaluation_rules = self._evaluation_rules()
        dimensions: dict[str, Any] = {}
        dimension_rules = {"visual_quality": tuple(item["rule_id"] for item in evaluation_rules["visual-quality-evaluation"])}
        pages = domain.pages
        for dimension, criteria in dimension_rules.items():
            task_id = _id(execution_id, dimension)
            dimensions[dimension] = {
                "task_id": task_id,
                "attempt_id": _id(task_id, 1),
                "claim_generation": 1,
                "fencing_token": _id(execution_id, task_id, 1, length=64),
                "criterion_ids": list(criteria),
                "allowed_read_set": (["main_tex"] if dimension == "text_quality" else [
                    "final_pdf", *[f"rendered_page:{page['page']}" for page in pages],
                ]),
                "peer_results_visible": False,
            }
        skeleton = {
            "schema_name": "acceptance-v2-review-skeleton",
            "schema_version": "1.0.0",
            "activation_status": "active_global_gate",
            "execution_id": execution_id,
            "input_binding_sha256": binding_sha,
            "attempt_number": attempt_number,
            "dimensions": dimensions,
            "required_visual_pages": [page["page"] for page in pages],
            "aggregation_policy": "failure-dominant-add-only-v1",
            "policy_bindings": {
                "catalog_sha256": sha256_file(self.project_root / "delivery-quality/v1/rule-catalog.v1.json"),
                "role_projections_sha256": sha256_file(self.project_root / "delivery-quality/v1/role-projections.v1.json"),
            },
        }
        skeleton["skeleton_sha256"] = _fingerprint_without(skeleton, "skeleton_sha256")
        execution = {
            "schema_name": "acceptance-v2-execution-context",
            "schema_version": "1.0.0",
            "activation_status": "active_global_gate",
            "execution_id": execution_id,
            "execution_revision": 1,
            "state": "reviewing",
            "prepared_at": prepared_at,
            "attempt_number": attempt_number,
            "input_binding_sha256": binding_sha,
            "skeleton_sha256": skeleton["skeleton_sha256"],
            "committed_patches": {},
            "report_publication": None,
        }
        execution["execution_sha256"] = _fingerprint_without(execution, "execution_sha256")
        if domain.track == "kernel":
            self.registry.validate("acceptance-v2-input-binding", binding)
        self.registry.validate("acceptance-v2-review-skeleton", skeleton)
        self.registry.validate("acceptance-v2-execution-context", execution)
        execution_root.mkdir(parents=True, exist_ok=True)
        write_json_atomic(execution_root / "input-binding.json", binding)
        write_json_atomic(execution_root / "acceptance_report.skeleton.json", skeleton)
        write_json_atomic(execution_root / "execution.json", execution)
        for dimension, task in dimensions.items():
            task_value = {
                "schema_name": "acceptance-v2-task-envelope",
                "schema_version": "1.0.0",
                "activation_status": "active_global_gate",
                "task_authority": {"kind": "acceptance_execution", "execution_id": execution_id},
                "dimension": dimension,
                **task,
                "skeleton_sha256": skeleton["skeleton_sha256"],
            }
            task_root = execution_root / "tasks" / task["task_id"]
            task_root.mkdir(parents=True, exist_ok=True)
            write_json_atomic(task_root / "task.json", task_value)
        with self._connect_control(root) as control:
            control.execute("BEGIN IMMEDIATE")
            active = control.execute("SELECT * FROM execution_authority WHERE singleton=1").fetchone()
            if active is not None and active["state"] not in {"invalidated", "terminal"} and active["execution_id"] != execution_id:
                control.execute("ROLLBACK")
                _reject("a non-terminal Acceptance Execution already owns this workspace", "execution_uniqueness", "acceptance_execution_already_active")
            if active is None or active["state"] in {"invalidated", "terminal"}:
                control.execute(
                    "INSERT OR REPLACE INTO execution_authority(singleton,execution_id,execution_revision,state,binding_sha256) VALUES(1,?,?,?,?)",
                    (execution_id, 1, "reviewing", binding_sha),
                )
                for task in dimensions.values():
                    control.execute(
                        "INSERT INTO reviewer_claims(task_id,execution_id,claim_generation,fencing_token,skeleton_sha256,state) VALUES(?,?,?,?,?,?)",
                        (task["task_id"], execution_id, task["claim_generation"], task["fencing_token"], skeleton["skeleton_sha256"], "ACTIVE"),
                    )
            elif active["execution_revision"] != 1 or active["binding_sha256"] != binding_sha:
                control.execute("ROLLBACK")
                _reject("prepared Acceptance authority is contradictory", "execution_uniqueness", "acceptance_execution_prepare_contradictory")
            control.execute("COMMIT")
        if fault_point == "after_prepare_control_commit":
            raise AcceptanceV2Fault(fault_point)
        write_json_atomic(root / "input-binding.json", binding)
        write_json_atomic(root / "acceptance_report.skeleton.json", skeleton)
        write_json_atomic(root / "execution.json", execution)
        write_json_atomic(root / "repair-ledger.json", ledger)
        write_json_atomic(root / "current.json", {"execution_id": execution_id, "execution_root": str(execution_root)})
        return {
            "workspace_root": str(root),
            "execution_id": execution_id,
            "execution_root": str(execution_root),
            "skeleton_path": str(root / "acceptance_report.skeleton.json"),
            "activation_status": "active_global_gate",
            "dimension_count": len(dimensions),
        }

    def commit_patch(
        self,
        *,
        workspace_root: Path,
        dimension: str,
        patch_path: Path,
        committed_at: str,
        fault_point: str | None = None,
    ) -> dict[str, Any]:
        root, execution_root, execution, skeleton, binding = self._load_current(workspace_root)
        if dimension not in DIMENSION_CRITERIA:
            _reject("unknown acceptance dimension", "patch_identity", "acceptance_dimension_unknown")
        patch = read_json(patch_path.resolve())
        self.registry.validate("acceptance-v2-judgment-patch", patch)
        self._validate_patch(patch, dimension, skeleton, binding)
        pending = []
        if (execution_root / "intents").exists():
            for path in (execution_root / "intents").glob("*.json"):
                intent = read_json(path)
                if intent.get("state") == "PREPARED":
                    pending.append(intent)
        if any(
            intent.get("intent_kind") == "acceptance_patch_publication"
            and intent.get("dimension") == dimension
            and intent.get("patch_sha256") != patch["patch_sha256"]
            for intent in pending
        ):
            _reject("a competing Patch publication already owns this dimension", "patch_fencing", "acceptance_patch_conflict")
        if pending:
            _reject("an earlier Acceptance publication requires reconciliation", "publication_recovery", "acceptance_reconcile_required")
        if dimension in execution["committed_patches"]:
            existing = execution["committed_patches"][dimension]
            if existing["patch_sha256"] == patch["patch_sha256"]:
                self._require_committed_patch_authority(root, execution_root, execution, dimension, existing)
                committed_path = Path(existing["path"])
                if (
                    not committed_path.is_file()
                    or sha256_file(committed_path) != existing["file_sha256"]
                    or read_json(committed_path) != patch
                ):
                    _reject("committed Patch bytes drifted", "patch_freshness", "acceptance_patch_stale")
                return {"dimension": dimension, "patch_sha256": patch["patch_sha256"], "idempotent": True}
            _reject("dimension already has a different committed Patch", "patch_fencing", "acceptance_patch_conflict")
        with self._connect_control(root) as control:
            authority = control.execute("SELECT * FROM execution_authority WHERE singleton=1").fetchone()
            claim = control.execute("SELECT * FROM reviewer_claims WHERE task_id=?", (patch["task_id"],)).fetchone()
            if (
                authority is None
                or authority["execution_id"] != execution["execution_id"]
                or authority["execution_revision"] != execution["execution_revision"]
                or claim is None
                or claim["state"] != "ACTIVE"
                or claim["claim_generation"] != patch["claim_generation"]
                or claim["fencing_token"] != patch["fencing_token"]
            ):
                _reject("Reviewer Claim fencing authority is stale", "patch_fencing", "acceptance_patch_fencing_stale")
        intent_id = _id(execution["execution_id"], dimension, patch["patch_sha256"])
        committed_path = execution_root / "committed" / dimension / intent_id / "judgment-patch.json"
        intent_path = execution_root / "intents" / f"patch-{dimension}-{intent_id}.json"
        committed_path.parent.mkdir(parents=True, exist_ok=True)
        intent_path.parent.mkdir(parents=True, exist_ok=True)
        intent = {
            "intent_kind": "acceptance_patch_publication",
            "intent_id": intent_id,
            "state": "PREPARED",
            "dimension": dimension,
            "expected_execution_revision": execution["execution_revision"],
            "patch_sha256": patch["patch_sha256"],
            "canonical_path": str(committed_path),
            "committed_at": committed_at,
        }
        with self._connect_control(root) as control:
            control.execute(
                "INSERT OR IGNORE INTO publication_intents(intent_id,execution_id,expected_revision,kind,state,artifact_sha256) VALUES(?,?,?,?,?,?)",
                (intent["intent_id"], execution["execution_id"], execution["execution_revision"], intent["intent_kind"], "PREPARED", intent["patch_sha256"]),
            )
        write_json_atomic(intent_path, intent)
        write_json_atomic(committed_path, patch)
        if fault_point == "after_patch_publish":
            raise AcceptanceV2Fault(fault_point)
        self._finish_patch_intent(root, execution_root, execution, intent, intent_path, fault_after_control=fault_point == "after_patch_control_commit")
        return {"dimension": dimension, "patch_sha256": patch["patch_sha256"], "intent_id": intent["intent_id"], "idempotent": False}

    def materialize(
        self,
        *,
        workspace_root: Path,
        provider_id: str,
        provider_version: str,
        materialized_at: str,
        fault_point: str | None = None,
    ) -> dict[str, Any]:
        if provider_id != "acceptance-v2-provider" or provider_version != "1.0.0":
            _reject("unregistered Acceptance v2 provider identity", "provider_identity", "acceptance_provider_invalid")
        root, execution_root, execution, skeleton, binding = self._load_current(workspace_root)
        domain = AcceptanceInputDomain.from_binding(binding)
        self._validate_binding(binding, verify_files=True)
        if execution["state"] == "materialized" and execution.get("report_publication"):
            immutable_path = Path(execution["report_publication"]["path"])
            report = read_json(immutable_path)
            root_report = read_json(root / "acceptance_report.json")
            intent_path = execution_root / "intents" / f"report-{execution['report_publication']['intent_id']}.json"
            intent = read_json(intent_path) if intent_path.is_file() else {}
            with self._connect_control(root) as control:
                authority = control.execute("SELECT * FROM execution_authority WHERE singleton=1").fetchone()
                stored_intent = control.execute("SELECT * FROM publication_intents WHERE intent_id=?", (execution["report_publication"]["intent_id"],)).fetchone()
            if (
                report.get("report_sha256") != _fingerprint_without(report, "report_sha256")
                or execution["report_publication"]["report_sha256"] != report.get("report_sha256")
                or root_report != report
                or not self._report_bundle_valid(immutable_path.parent, intent)
                or authority is None
                or authority["execution_id"] != execution["execution_id"]
                or authority["execution_revision"] != execution["execution_revision"]
                or authority["state"] != "terminal"
                or stored_intent is None
                or stored_intent["state"] != "COMMITTED"
                or stored_intent["artifact_sha256"] != intent.get("bundle_sha256")
            ):
                _reject("committed Acceptance Report bytes drifted", "report_freshness", "acceptance_report_stale")
            return {
                "report_path": str(root / "acceptance_report.json"),
                "report_sha256": report["report_sha256"],
                "overall_status": report["overall_status"],
                "routing_state": report["routing_state"],
                "semantic_attempts_used": report["repair_budget"]["semantic_attempts_used"],
                "activation_status": "active_global_gate",
                "idempotent": True,
            }
        if execution["state"] != "reviewing" or execution.get("report_publication") is not None:
            _reject("Acceptance execution is not open for materialization", "report_fencing", "acceptance_execution_terminal")
        if execution["input_binding_sha256"] != domain.fingerprint:
            _reject("execution input binding is stale", "input_freshness", "acceptance_input_stale")
        pending = [path for path in (execution_root / "intents").glob("*.json") if read_json(path).get("state") == "PREPARED"] if (execution_root / "intents").exists() else []
        if pending:
            _reject("acceptance publication intent is non-terminal", "publication_recovery", "acceptance_reconcile_required")
        if set(execution["committed_patches"]) != set(DIMENSION_CRITERIA):
            _reject("the Visual Quality Patch is required", "patch_completeness", "acceptance_patch_incomplete")
        patches = {
            dimension: read_json(Path(record["path"]))
            for dimension, record in execution["committed_patches"].items()
            if self._require_committed_patch_authority(root, execution_root, execution, dimension, record)
        }
        for dimension, patch in patches.items():
            self._validate_patch(patch, dimension, skeleton, binding)
            if sha256_file(Path(execution["committed_patches"][dimension]["path"])) != execution["committed_patches"][dimension]["file_sha256"]:
                _reject("committed Patch bytes drifted", "patch_freshness", "acceptance_patch_stale")
        evaluation_rules = self._evaluation_rules()
        catalog = read_json(self.project_root / "delivery-quality/v1/rule-catalog.v1.json")
        registered_violations = {
            rule["rule_id"]: {item["violation_id"] for item in rule["violations"]}
            for rule in catalog["rules"]
        }
        registered_exceptions = {
            rule["rule_id"]: {item["exception_id"] for item in rule.get("exceptions", [])}
            for rule in catalog["rules"]
        }
        precompile_report = read_json(Path(domain.quality_inputs["precompile_quality_report"]["path"]))
        precompile_seal = read_json(Path(domain.quality_inputs["precompile_text_seal"]["path"]))
        precompile_reused = precompile_seal.get("decision_origin") == "reused_after_text_equivalence"
        changed_generations = set(domain.changed_generations)
        equivalence_waivers = (
            {"source-faithfulness-reviewer", "writing-quality-reviewer", "pyramid-reviewer"}
            if precompile_reused else set()
        )
        dependency_snapshot = [
            {
                "judgment": judgment,
                "generation_ids": sorted(dependencies),
                "equivalence_waived": judgment in equivalence_waivers,
            }
            for judgment, dependencies in JUDGMENT_DEPENDENCIES.items()
        ]
        invalidated_judgments = [
            judgment for judgment, dependencies in JUDGMENT_DEPENDENCIES.items()
            if changed_generations & dependencies and judgment not in equivalence_waivers
        ] if execution["attempt_number"] > 1 else []
        retained_judgments = [
            judgment for judgment in JUDGMENT_DEPENDENCIES if judgment not in invalidated_judgments
        ] if execution["attempt_number"] > 1 else []
        precompile_by_rule = {
            item["rule_id"]: item for item in precompile_report["normalized_rule_results"]
        }
        expected_precompile = {
            item["rule_id"]: item for item in evaluation_rules["writing-quality-evaluation"]
        }
        if set(precompile_by_rule) != set(expected_precompile):
            _reject("Precompile rule provenance is incomplete", "quality_input_validity", "acceptance_precompile_rule_coverage_invalid")
        results = []
        precompile_gaps = []
        for rule_id, rule in expected_precompile.items():
            upstream = precompile_by_rule[rule_id]
            if upstream["rule_semantic_sha256"] != rule["rule_semantic_sha256"]:
                _reject("Precompile rule provenance is stale", "quality_input_validity", "acceptance_precompile_rule_fingerprint_stale")
            if (
                not upstream.get("evidence")
                or not set(upstream.get("violations", [])) <= registered_violations[rule_id]
                or not set(upstream.get("exceptions", [])) <= registered_exceptions[rule_id]
            ):
                precompile_gaps.append({
                    "gap_id": _id("precompile-rule-contract", rule_id),
                    "observation": "unregistered or incomplete precompile rule applicability evidence",
                    "evidence_location": f"precompile-rule:{rule_id}",
                })
            results.append({
                **upstream,
                "decision_phase": "precompile",
                "source_report_sha256": precompile_report["report_sha256"],
                "artifact_generation_sha256": precompile_seal["generation_set_sha256"],
            })
        visual_patch = patches["visual_quality"]
        visual_rule_by_id = {item["rule_id"]: item for item in evaluation_rules["visual-quality-evaluation"]}
        gaps = [*precompile_gaps, *[gap for patch in patches.values() for gap in patch["contract_gaps"]]]
        for result in visual_patch["criterion_results"]:
            rule = visual_rule_by_id[result["criterion_id"]]
            violation_id = result.get("violation_id")
            if result["decision"] == "fail" and violation_id not in registered_violations[rule["rule_id"]]:
                gaps.append({"gap_id": _id("unknown-violation", rule["rule_id"], violation_id), "observation": "unregistered Visual Quality violation", "evidence_location": result["evidence"][0].get("location", "unknown")})
            results.append(
                {
                    "rule_id": rule["rule_id"],
                    "rule_semantic_sha256": rule["rule_semantic_sha256"],
                    "decision_phase": "postcompile",
                    "primary_semantic_decision_owner": "visual-quality-reviewer",
                    "source_report_sha256": visual_patch["patch_sha256"],
                    "artifact_generation_sha256": domain.fingerprint,
                    "decision": result["decision"],
                    "evidence": result["evidence"],
                    "violations": [violation_id] if result["decision"] == "fail" and violation_id in registered_violations[rule["rule_id"]] else [],
                    "exceptions": [],
                }
            )
        result_by_id = {item["rule_id"]: item for item in results}
        cross_findings = []
        for finding in visual_patch["cross_phase_findings"]:
            target = result_by_id.get(finding.get("rule_id"))
            violation_id = finding.get("violation_id")
            if target is None or target["decision_phase"] != "precompile" or finding.get("effect") != "add_failure_only" or not finding.get("evidence") or violation_id not in registered_violations.get(finding.get("rule_id"), set()):
                gaps.append({"gap_id": _id("visual_quality", finding), "observation": "invalid Cross-Phase Finding", "evidence_location": finding.get("location", "unknown")})
                continue
            target["decision"] = "fail"
            target.setdefault("cross_phase_finding_ids", []).append(finding["finding_id"])
            target["violations"] = sorted(set(target["violations"]) | {violation_id})
            cross_findings.append(finding)
        ledger = read_json(root / "repair-ledger.json")
        if ledger.get("ledger_sha256") != _fingerprint_without(ledger, "ledger_sha256"):
            _reject("repair ledger fingerprint is stale", "repair_admission", "acceptance_repair_ledger_stale")
        if gaps:
            overall = "blocked_contract_gap"
            routing = "human_disposition_required"
            if execution["execution_id"] not in ledger["contract_gap_cycles"]:
                ledger["contract_gap_cycles"].append(execution["execution_id"])
        elif any(result["decision"] == "fail" for result in results):
            overall = "fail"
            if not any(item["execution_id"] == execution["execution_id"] for item in ledger["semantic_attempts"]):
                ledger["semantic_attempts"].append({
                    "attempt": execution["attempt_number"],
                    "execution_id": execution["execution_id"],
                    "input_binding_sha256": domain.fingerprint,
                    "artifact_generation_sha256": precompile_seal["generation_set_sha256"],
                    "artifact_set_sha256": _artifact_set_sha(domain.artifacts, domain.pages),
                    "source_failure_set_sha256": hashlib.sha256(canonical_json_bytes([item for item in results if item["decision"] == "fail"])).hexdigest(),
                    "decision": "fail",
                    "routing_state": "repair_required",
                })
            routing = "manual_repair_required" if len(ledger["semantic_attempts"]) >= ATTEMPT_LIMIT else "repair_required"
        else:
            overall = "pass"
            routing = "ready_for_delivery"
        ledger["ledger_sha256"] = _fingerprint_without(ledger, "ledger_sha256")
        report = {
            "schema_name": "acceptance-report-v2",
            "schema_version": "2.0.0",
            "activation_status": "active_global_gate",
            "global_gate_authority": binding["global_gate_authority"],
            "execution_id": execution["execution_id"],
            "execution_revision": execution["execution_revision"] + 1,
            "materialized_at": materialized_at,
            "provider": {"provider_id": provider_id, "provider_version": provider_version},
            "input_track": domain.track,
            "input_binding_sha256": domain.fingerprint,
            "skeleton_sha256": skeleton["skeleton_sha256"],
            "patches": {dimension: {"patch_sha256": patch["patch_sha256"], "task_id": patch["task_id"], "attempt_id": patch["attempt_id"]} for dimension, patch in patches.items()},
            "criterion_results": results,
            "precompile_owner_reports": precompile_report["owner_reports"],
            "visual_scan_evidence": visual_patch["visual_scan_evidence"],
            "cross_phase_findings": cross_findings,
            "contract_gaps": gaps,
            "overall_status": overall,
            "routing_state": routing,
            "repair_budget": {"attempt_limit": ATTEMPT_LIMIT, "semantic_attempts_used": len(ledger["semantic_attempts"])},
            "repair_ledger_sha256": ledger["ledger_sha256"],
            "semantic_reinterpretation_performed": False,
        }
        if domain.run is not None:
            report["run_binding"] = domain.run
        else:
            report["legacy_binding"] = domain.legacy_authority
        attempt_record = {
            "schema_name": "acceptance-v2-attempt-record",
            "schema_version": "1.0.0",
            "execution_id": execution["execution_id"],
            "attempt_number": execution["attempt_number"],
            "input_track": domain.track,
            "input_binding_sha256": domain.fingerprint,
            "predecessor_generation_set_sha256": domain.predecessor_generation_set_sha256,
            "artifact_generation_sha256": precompile_seal["generation_set_sha256"],
            "artifact_set_sha256": _artifact_set_sha(domain.artifacts, domain.pages),
            "changed_generations": sorted(changed_generations),
            "dependency_snapshot": dependency_snapshot,
            "invalidated_judgments": invalidated_judgments,
            "retained_judgments": retained_judgments,
            "required_reruns": invalidated_judgments,
            "completed_reruns": invalidated_judgments,
            "failure_set_sha256": hashlib.sha256(canonical_json_bytes([item for item in results if item["decision"] == "fail"])).hexdigest(),
            "overall_status": overall,
            "routing_state": routing,
        }
        if domain.run is not None:
            attempt_record.update({
                "run_id": domain.run["run_id"],
                "run_revision": domain.run["coordination_revision"],
                "acceptance_revision": domain.run["acceptance_revision"],
            })
        else:
            attempt_record["legacy_input_set_id"] = domain.legacy_authority["input_set_id"]
        attempt_record["attempt_record_sha256"] = _fingerprint_without(attempt_record, "attempt_record_sha256")
        self.registry.validate("acceptance-v2-attempt-record", attempt_record)
        self.registry.validate("acceptance-v2-repair-ledger", ledger)
        report["attempt_record_sha256"] = attempt_record["attempt_record_sha256"]
        report["report_sha256"] = _fingerprint_without(report, "report_sha256")
        self.registry.validate("acceptance-report-v2", report)
        report_identity = _id(execution["execution_id"], report["report_sha256"])
        immutable_report_path = execution_root / "reports" / report_identity / "acceptance_report.json"
        staged_report_path = execution_root / "staged-reports" / report_identity / "acceptance_report.json"
        report_path = root / "acceptance_report.json"
        intent_path = execution_root / "intents" / f"report-{report_identity}.json"
        intent = {
            "intent_kind": "acceptance_report_publication",
            "intent_id": _id(execution["execution_id"], report["report_sha256"]),
            "state": "PREPARED",
            "expected_execution_revision": execution["execution_revision"],
            "report_sha256": report["report_sha256"],
            "attempt_record_sha256": attempt_record["attempt_record_sha256"],
            "ledger_sha256": ledger["ledger_sha256"],
            "bundle_sha256": _report_bundle_sha(report["report_sha256"], attempt_record["attempt_record_sha256"], ledger["ledger_sha256"]),
            "canonical_path": str(immutable_report_path),
            "staged_path": str(staged_report_path),
            "materialized_at": materialized_at,
        }
        with self._connect_control(root) as control:
            authority = control.execute("SELECT * FROM execution_authority WHERE singleton=1").fetchone()
            active_claims = control.execute("SELECT COUNT(*) FROM reviewer_claims WHERE execution_id=? AND state='ACTIVE'", (execution["execution_id"],)).fetchone()[0]
            if authority is None or authority["execution_revision"] != execution["execution_revision"] or active_claims:
                _reject("report publication lacks terminal Reviewer Claims or current revision", "report_fencing", "acceptance_report_fencing_stale")
            control.execute(
                "INSERT INTO publication_intents(intent_id,execution_id,expected_revision,kind,state,artifact_sha256) VALUES(?,?,?,?,?,?)",
                (intent["intent_id"], execution["execution_id"], execution["execution_revision"], intent["intent_kind"], "PREPARED", intent["bundle_sha256"]),
            )
        write_json_atomic(intent_path, intent)
        staged_report_path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(staged_report_path, report)
        write_json_atomic(staged_report_path.parent / "attempt-record.json", attempt_record)
        write_json_atomic(staged_report_path.parent / "repair-ledger.json", ledger)
        if fault_point == "after_report_publish":
            raise AcceptanceV2Fault(fault_point)
        self._finish_report_intent(root, execution_root, execution, intent, intent_path, fault_after_control=fault_point == "after_report_control_commit")
        return {
            "report_path": str(report_path),
            "report_sha256": report["report_sha256"],
            "overall_status": overall,
            "routing_state": routing,
            "semantic_attempts_used": len(ledger["semantic_attempts"]),
            "activation_status": "active_global_gate",
            "idempotent": False,
        }

    def prepare_repair(self, *, workspace_root: Path, input_binding_path: Path, prepared_at: str) -> dict[str, Any]:
        root = workspace_root.resolve()
        report = read_json(root / "acceptance_report.json")
        if report.get("overall_status") != "fail" or report.get("routing_state") != "repair_required":
            _reject("repair requires a complete repairable semantic failure", "repair_admission", "acceptance_repair_not_admissible")
        ledger = read_json(root / "repair-ledger.json")
        self.registry.validate("acceptance-v2-repair-ledger", ledger)
        if ledger.get("ledger_sha256") != _fingerprint_without(ledger, "ledger_sha256"):
            _reject("repair ledger fingerprint is stale", "repair_admission", "acceptance_repair_ledger_stale")
        next_attempt = len(ledger["semantic_attempts"]) + 1
        if next_attempt > ATTEMPT_LIMIT:
            _reject("semantic repair budget is exhausted", "repair_budget", "acceptance_repair_budget_exhausted")
        prior_current = read_json(root / "current.json")
        prior_execution = read_json(Path(prior_current["execution_root"]) / "execution.json")
        prior_report_path = Path(prior_execution["report_publication"]["path"])
        prior_attempt_path = prior_report_path.parent / "attempt-record.json"
        prior_entry = next(
            (item for item in ledger["semantic_attempts"] if item["execution_id"] == prior_execution["execution_id"]),
            None,
        )
        if prior_entry is None or not prior_report_path.is_file() or not prior_attempt_path.is_file():
            _reject("repair history is incomplete", "repair_admission", "acceptance_repair_history_incomplete")
        prior_entry.update({
            "report_path": str(prior_report_path),
            "report_sha256": report["report_sha256"],
            "attempt_record_path": str(prior_attempt_path),
            "attempt_record_sha256": read_json(prior_attempt_path)["attempt_record_sha256"],
        })
        ledger["ledger_sha256"] = _fingerprint_without(ledger, "ledger_sha256")
        prior_binding = read_json(root / "input-binding.json")
        binding = read_json(input_binding_path.resolve())
        self._validate_binding(binding, verify_files=True)
        prior_domain = AcceptanceInputDomain.from_binding(prior_binding)
        domain = AcceptanceInputDomain.from_binding(binding)
        if domain.track != "kernel" or prior_domain.track != "kernel":
            _reject("Legacy repair admission requires a freshly adopted input set and new execution", "repair_lineage", "legacy_repair_admission_unsupported")
        if domain.fingerprint == prior_domain.fingerprint:
            _reject("semantic repair requires a new Artifact Generation binding", "repair_generation", "acceptance_repair_generation_unchanged")
        prior_seal = read_json(Path(prior_binding["quality_inputs"]["precompile_text_seal"]["path"]))
        current_seal = read_json(Path(binding["quality_inputs"]["precompile_text_seal"]["path"]))
        prior_run = prior_binding["run"]
        current_run = binding["run"]
        if (
            current_run["run_id"] != prior_run["run_id"]
            or Path(current_run["video_root"]).resolve() != Path(prior_run["video_root"]).resolve()
            or current_run["coordination_revision"] < prior_run["coordination_revision"]
            or current_run["acceptance_revision"] <= prior_run["acceptance_revision"]
            or current_run["predecessor_generation_set_sha256"] != prior_seal["generation_set_sha256"]
        ):
            _reject("repair binding is outside the current Kernel Run lineage", "repair_lineage", "acceptance_repair_lineage_invalid")
        if (
            current_seal["generation_set_sha256"] == prior_seal["generation_set_sha256"]
            or _artifact_set_sha(domain.artifacts, domain.pages) == _artifact_set_sha(prior_domain.artifacts, prior_domain.pages)
        ):
            _reject("semantic repair requires a new Artifact Generation binding", "repair_generation", "acceptance_repair_generation_unchanged")
        actual_changed_generation_ids = {
            logical_id
            for logical_id in {item["logical_id"] for item in prior_binding["artifacts"]} | {item["logical_id"] for item in binding["artifacts"]}
            if next((item for item in prior_binding["artifacts"] if item["logical_id"] == logical_id), None)
            != next((item for item in binding["artifacts"] if item["logical_id"] == logical_id), None)
        }
        if set(current_run["changed_generation_ids"]) != actual_changed_generation_ids:
            _reject("declared changed generations do not match predecessor bytes", "repair_generation", "acceptance_changed_generation_ids_mismatch")
        with self._connect_control(root) as control:
            control.execute("UPDATE execution_authority SET state='invalidated' WHERE singleton=1")
        return self._prepare_execution(root=root, binding=binding, attempt_number=next_attempt, prepared_at=prepared_at, ledger=ledger)

    def reconcile(self, *, workspace_root: Path) -> dict[str, Any]:
        root, execution_root, execution, _, _ = self._load_current(workspace_root)
        actions = []
        intents_root = execution_root / "intents"
        if intents_root.exists():
            for intent_path in sorted(intents_root.glob("*.json")):
                intent = read_json(intent_path)
                if intent.get("state") != "PREPARED":
                    continue
                self._validate_intent_paths(execution_root, execution, intent, intent_path)
                canonical = Path(intent["canonical_path"])
                if intent["intent_kind"] == "acceptance_patch_publication":
                    if not canonical.is_file():
                        _reject("prepared publication has no canonical bytes", "publication_recovery", "acceptance_publication_missing")
                    patch = read_json(canonical)
                    if patch.get("patch_sha256") != intent["patch_sha256"] or patch.get("patch_sha256") != _fingerprint_without(patch, "patch_sha256"):
                        _reject("prepared Patch publication is contradictory", "publication_recovery", "acceptance_publication_contradictory")
                    self._finish_patch_intent(root, execution_root, execution, intent, intent_path)
                    execution = read_json(root / "execution.json")
                    actions.append(f"committed_patch:{intent['dimension']}")
                else:
                    staged = Path(intent.get("staged_path", intent["canonical_path"]))
                    if not staged.is_file() or not self._report_bundle_valid(staged.parent, intent):
                        _reject("prepared publication has no staged bytes", "publication_recovery", "acceptance_publication_missing")
                    self._finish_report_intent(root, execution_root, execution, intent, intent_path)
                    execution = read_json(root / "execution.json")
                    actions.append("committed_report")
        return {"execution_id": execution["execution_id"], "actions": actions, "committed_dimensions": sorted(execution["committed_patches"]), "report_published": execution["report_publication"] is not None}

    @staticmethod
    def _validate_intent_paths(execution_root: Path, execution: dict[str, Any], intent: dict[str, Any], intent_path: Path) -> None:
        if intent.get("expected_execution_revision") != execution["execution_revision"]:
            _reject("publication intent revision is stale", "publication_recovery", "acceptance_publication_intent_stale")
        if intent.get("intent_kind") == "acceptance_patch_publication":
            expected_id = _id(execution["execution_id"], intent.get("dimension"), intent.get("patch_sha256"))
            expected_canonical = execution_root / "committed" / str(intent.get("dimension")) / expected_id / "judgment-patch.json"
            expected_intent_path = execution_root / "intents" / f"patch-{intent.get('dimension')}-{expected_id}.json"
            valid = (
                intent.get("intent_id") == expected_id
                and Path(intent.get("canonical_path", "")).resolve() == expected_canonical.resolve()
                and intent_path.resolve() == expected_intent_path.resolve()
            )
        elif intent.get("intent_kind") == "acceptance_report_publication":
            expected_id = _id(execution["execution_id"], intent.get("report_sha256"))
            expected_canonical = execution_root / "reports" / expected_id / "acceptance_report.json"
            expected_staged = execution_root / "staged-reports" / expected_id / "acceptance_report.json"
            expected_intent_path = execution_root / "intents" / f"report-{expected_id}.json"
            valid = (
                intent.get("intent_id") == expected_id
                and Path(intent.get("canonical_path", "")).resolve() == expected_canonical.resolve()
                and Path(intent.get("staged_path", "")).resolve() == expected_staged.resolve()
                and intent_path.resolve() == expected_intent_path.resolve()
                and intent.get("bundle_sha256") == _report_bundle_sha(
                    intent.get("report_sha256", ""), intent.get("attempt_record_sha256", ""), intent.get("ledger_sha256", "")
                )
            )
        else:
            valid = False
        if not valid:
            _reject("publication intent paths or identity are contradictory", "publication_recovery", "acceptance_publication_intent_contradictory")

    def guard_eligibility(self, *, workspace_root: Path) -> dict[str, Any]:
        root, execution_root, execution, _, binding = self._load_current(workspace_root)
        domain = AcceptanceInputDomain.from_binding(binding)
        self._validate_binding(binding, verify_files=True)
        report_path = root / "acceptance_report.json"
        checks = {
            "report_exists": report_path.is_file(),
            "execution_report_committed": execution["report_publication"] is not None,
            "visual_patch_committed": set(execution["committed_patches"]) == set(DIMENSION_CRITERIA),
            "visual_patch_authority_current": all(
                self._committed_patch_authority_current(root, execution_root, execution, dimension, record)
                for dimension, record in execution["committed_patches"].items()
            ),
            "no_pending_intents": not any(read_json(path).get("state") == "PREPARED" for path in (execution_root / "intents").glob("*.json")),
        }
        report = read_json(report_path) if report_path.is_file() else {}
        ledger = read_json(root / "repair-ledger.json")
        try:
            self.registry.validate("acceptance-report-v2", report)
            report_schema_valid = True
        except ContractError:
            report_schema_valid = False
        immutable_report_path = Path(execution["report_publication"]["path"]) if execution["report_publication"] else Path()
        immutable_report = read_json(immutable_report_path) if execution["report_publication"] and immutable_report_path.is_file() else {}
        attempt_record_path = immutable_report_path.parent / "attempt-record.json" if execution["report_publication"] else Path()
        attempt_record = read_json(attempt_record_path) if attempt_record_path.is_file() else {}
        immutable_ledger_path = immutable_report_path.parent / "repair-ledger.json" if execution["report_publication"] else Path()
        immutable_ledger = read_json(immutable_ledger_path) if immutable_ledger_path.is_file() else {}
        try:
            self.registry.validate("acceptance-v2-attempt-record", attempt_record)
            self.registry.validate("acceptance-v2-repair-ledger", immutable_ledger)
            companion_schemas_valid = True
        except ContractError:
            companion_schemas_valid = False
        with self._connect_control(root) as control:
            authority = control.execute("SELECT * FROM execution_authority WHERE singleton=1").fetchone()
            pending_control_intents = control.execute(
                "SELECT COUNT(*) FROM publication_intents WHERE execution_id=? AND state='PREPARED'",
                (execution["execution_id"],),
            ).fetchone()[0]
            report_intent = (
                control.execute(
                    "SELECT * FROM publication_intents WHERE intent_id=?",
                    (execution["report_publication"]["intent_id"],),
                ).fetchone()
                if execution["report_publication"]
                else None
            )
        checks.update({
            "report_schema_valid": report_schema_valid,
            "companion_schemas_valid": companion_schemas_valid,
            "report_fingerprint_current": report.get("report_sha256") == _fingerprint_without(report, "report_sha256") if report else False,
            "report_publication_current": bool(
                report
                and execution["report_publication"]
                and execution["report_publication"]["report_sha256"] == report.get("report_sha256")
                and immutable_report == report
                and immutable_report_path.is_relative_to(execution_root / "reports")
                and immutable_report_path.parent.parent == execution_root / "reports"
            ),
            "report_execution_current": bool(
                report
                and report.get("execution_id") == execution["execution_id"]
                and report.get("execution_revision") == execution["execution_revision"]
                and report.get("skeleton_sha256") == execution["skeleton_sha256"]
                and report.get("input_track") == domain.track
                and (
                    report.get("run_binding") == domain.run
                    if domain.run is not None
                    else report.get("legacy_binding") == domain.legacy_authority and "run_binding" not in report
                )
            ),
            "control_authority_terminal": bool(
                authority
                and authority["execution_id"] == execution["execution_id"]
                and authority["execution_revision"] == execution["execution_revision"]
                and authority["state"] == "terminal"
                and authority["binding_sha256"] == domain.fingerprint
            ),
            "control_no_pending_intents": pending_control_intents == 0,
            "control_report_intent_committed": bool(
                report_intent
                and report_intent["state"] == "COMMITTED"
                and report_intent["execution_id"] == execution["execution_id"]
                and report_intent["kind"] == "acceptance_report_publication"
                and report_intent["expected_revision"] + 1 == execution["execution_revision"]
                and report_intent["artifact_sha256"] == _report_bundle_sha(
                    report.get("report_sha256", ""),
                    attempt_record.get("attempt_record_sha256", ""),
                    ledger.get("ledger_sha256", ""),
                )
            ),
            "repair_ledger_current": bool(
                report
                and report.get("repair_budget", {}).get("semantic_attempts_used") == len(ledger["semantic_attempts"])
                and ledger.get("ledger_sha256") == _fingerprint_without(ledger, "ledger_sha256")
                and report.get("repair_ledger_sha256") == ledger.get("ledger_sha256")
                and immutable_ledger == ledger
            ),
            "attempt_record_current": bool(
                attempt_record
                and attempt_record.get("attempt_record_sha256") == _fingerprint_without(attempt_record, "attempt_record_sha256")
                and report.get("attempt_record_sha256") == attempt_record.get("attempt_record_sha256")
                and attempt_record.get("execution_id") == execution["execution_id"]
                and attempt_record.get("input_binding_sha256") == domain.fingerprint
            ),
            "historical_attempts_current": self._historical_attempts_current(root, ledger, execution),
            "input_binding_current": report.get("input_binding_sha256") == domain.fingerprint,
            "decision_pass": report.get("overall_status") == "pass",
            "routing_ready": report.get("routing_state") == "ready_for_delivery",
        })
        current_global_gate = GlobalGatePublisher().require_current(control_store_root=Path(domain.global_gate_authority["control_store_root"]))
        checks["global_gate_authority_current"] = bool(
            report.get("activation_status") == "active_global_gate"
            and report.get("global_gate_authority") == domain.global_gate_authority == current_global_gate
        )
        return {"activation_status": "active_global_gate", "delivery_authority": all(checks.values()), "eligible": all(checks.values()), "mechanical_checks": checks, "report_sha256": report.get("report_sha256")}

    def _historical_attempts_current(self, root: Path, ledger: dict[str, Any], current_execution: dict[str, Any]) -> bool:
        executions_root = (root / "executions").resolve()
        for entry in ledger.get("semantic_attempts", []):
            if entry.get("execution_id") == current_execution.get("execution_id"):
                continue
            required = {"report_path", "report_sha256", "attempt_record_path", "attempt_record_sha256"}
            if not required <= set(entry):
                return False
            report_path = Path(entry["report_path"]).resolve()
            attempt_path = Path(entry["attempt_record_path"]).resolve()
            if not report_path.is_relative_to(executions_root) or not attempt_path.is_relative_to(executions_root):
                return False
            if not report_path.is_file() or not attempt_path.is_file() or report_path.parent != attempt_path.parent:
                return False
            report = read_json(report_path)
            attempt = read_json(attempt_path)
            historical_ledger_path = report_path.parent / "repair-ledger.json"
            historical_ledger = read_json(historical_ledger_path) if historical_ledger_path.is_file() else {}
            try:
                self.registry.validate("acceptance-report-v2", report)
                self.registry.validate("acceptance-v2-attempt-record", attempt)
                self.registry.validate("acceptance-v2-repair-ledger", historical_ledger)
            except ContractError:
                return False
            if (
                not {"schema_name", "schema_version", "input_track", "execution_id", "attempt_number", "input_binding_sha256", "artifact_set_sha256", "overall_status", "routing_state", "attempt_record_sha256"} <= set(attempt)
                or set(attempt) - {"schema_name", "schema_version", "input_track", "execution_id", "attempt_number", "run_id", "run_revision", "acceptance_revision", "legacy_input_set_id", "input_binding_sha256", "predecessor_generation_set_sha256", "artifact_generation_sha256", "artifact_set_sha256", "changed_generations", "dependency_snapshot", "invalidated_judgments", "retained_judgments", "required_reruns", "completed_reruns", "failure_set_sha256", "overall_status", "routing_state", "attempt_record_sha256"}
                or report.get("report_sha256") != entry["report_sha256"]
                or report.get("report_sha256") != _fingerprint_without(report, "report_sha256")
                or attempt.get("attempt_record_sha256") != entry["attempt_record_sha256"]
                or attempt.get("attempt_record_sha256") != _fingerprint_without(attempt, "attempt_record_sha256")
                or report.get("execution_id") != entry.get("execution_id")
                or attempt.get("execution_id") != entry.get("execution_id")
                or report.get("attempt_record_sha256") != attempt.get("attempt_record_sha256")
                or report.get("repair_ledger_sha256") != historical_ledger.get("ledger_sha256")
                or historical_ledger.get("ledger_sha256") != _fingerprint_without(historical_ledger, "ledger_sha256")
                or attempt.get("attempt_number") != entry.get("attempt")
                or attempt.get("input_binding_sha256") != entry.get("input_binding_sha256")
                or attempt.get("artifact_set_sha256") != entry.get("artifact_set_sha256")
                or attempt.get("overall_status") != entry.get("decision")
                or report.get("overall_status") != entry.get("decision")
            ):
                return False
        return True

    def _load_current(self, workspace_root: Path) -> tuple[Path, Path, dict[str, Any], dict[str, Any], dict[str, Any]]:
        root = workspace_root.resolve()
        current = read_json(root / "current.json")
        execution_root = Path(current["execution_root"]).resolve()
        executions_root = (root / "executions").resolve()
        if (
            not execution_root.is_relative_to(executions_root)
            or execution_root.parent != executions_root
            or execution_root.name != current["execution_id"]
        ):
            _reject("execution pointer escapes workspace or disagrees with its identity", "execution_identity", "acceptance_execution_path_invalid")
        execution = read_json(root / "execution.json")
        skeleton = read_json(root / "acceptance_report.skeleton.json")
        binding = read_json(root / "input-binding.json")
        immutable_execution = read_json(execution_root / "execution.json")
        immutable_skeleton = read_json(execution_root / "acceptance_report.skeleton.json")
        immutable_binding = read_json(execution_root / "input-binding.json")
        if execution != immutable_execution or skeleton != immutable_skeleton or binding != immutable_binding:
            _reject("Acceptance root projections drifted from execution-owned evidence", "execution_identity", "acceptance_execution_projection_stale")
        self.registry.validate("acceptance-v2-execution-context", execution)
        self.registry.validate("acceptance-v2-review-skeleton", skeleton)
        if binding.get("input_track") != "legacy":
            self.registry.validate("acceptance-v2-input-binding", binding)
        self._validate_binding(binding, verify_files=False)
        domain = AcceptanceInputDomain.from_binding(binding)
        if execution["execution_id"] != current["execution_id"] or skeleton["execution_id"] != execution["execution_id"] or execution.get("skeleton_sha256") != skeleton.get("skeleton_sha256") or execution.get("input_binding_sha256") != domain.fingerprint:
            _reject("acceptance execution identities disagree", "execution_identity", "acceptance_execution_identity_invalid")
        if execution.get("execution_sha256") != _fingerprint_without(execution, "execution_sha256"):
            _reject("acceptance execution fingerprint is stale", "execution_identity", "acceptance_execution_stale")
        if skeleton.get("skeleton_sha256") != _fingerprint_without(skeleton, "skeleton_sha256"):
            _reject("Acceptance Skeleton fingerprint is stale", "execution_identity", "acceptance_skeleton_stale")
        if skeleton.get("input_binding_sha256") != domain.fingerprint:
            _reject("Acceptance Skeleton input binding is stale", "execution_identity", "acceptance_skeleton_binding_stale")
        expected_policy = {
            "catalog_sha256": sha256_file(self.project_root / "delivery-quality/v1/rule-catalog.v1.json"),
            "role_projections_sha256": sha256_file(self.project_root / "delivery-quality/v1/role-projections.v1.json"),
        }
        if skeleton.get("policy_bindings") != expected_policy or skeleton.get("required_visual_pages") != [item["page"] for item in domain.pages]:
            _reject("Acceptance Skeleton policy or page bindings are stale", "execution_identity", "acceptance_skeleton_policy_stale")
        visual_rules = next(
            item["rules"] for item in read_json(self.project_root / "delivery-quality/v1/role-projections.v1.json")["projections"]
            if item["projection_id"] == "visual-quality-evaluation"
        )
        task_id = _id(execution["execution_id"], "visual_quality")
        expected_dimension = {
            "task_id": task_id,
            "attempt_id": _id(task_id, 1),
            "claim_generation": 1,
            "fencing_token": _id(execution["execution_id"], task_id, 1, length=64),
            "criterion_ids": [item["rule_id"] for item in visual_rules],
            "allowed_read_set": ["final_pdf", *[f"rendered_page:{item['page']}" for item in domain.pages]],
            "peer_results_visible": False,
        }
        task_envelope = read_json(execution_root / "tasks" / task_id / "task.json")
        expected_envelope = {
            "schema_name": "acceptance-v2-task-envelope", "schema_version": "1.0.0", "activation_status": "active_global_gate",
            "task_authority": {"kind": "acceptance_execution", "execution_id": execution["execution_id"]},
            "dimension": "visual_quality", **expected_dimension, "skeleton_sha256": skeleton["skeleton_sha256"],
        }
        with self._connect_control(root) as control:
            claim = control.execute("SELECT * FROM reviewer_claims WHERE task_id=?", (task_id,)).fetchone()
        if (
            skeleton.get("dimensions") != {"visual_quality": expected_dimension}
            or task_envelope != expected_envelope
            or claim is None
            or claim["execution_id"] != execution["execution_id"]
            or claim["claim_generation"] != 1
            or claim["fencing_token"] != expected_dimension["fencing_token"]
            or claim["skeleton_sha256"] != skeleton["skeleton_sha256"]
        ):
            _reject("Acceptance dimension authority is stale", "execution_identity", "acceptance_dimension_authority_stale")
        return root, execution_root, execution, skeleton, binding

    def _validate_binding(self, binding: dict[str, Any], *, verify_files: bool, require_published_final_authority: bool = True) -> None:
        if binding.get("schema_name") == "legacy-acceptance-input-set" and binding.get("input_track") == "legacy":
            if "run" in binding:
                _reject("A Legacy Acceptance Input Set cannot contain a synthetic Run binding", "input_identity", "legacy_synthetic_run_rejected")
            if binding.get("contract_gaps"):
                _reject("A Legacy Acceptance Input Set with Contract Gaps cannot enter review", "contract_gap", "legacy_contract_gap_blocked")
            required = {"schema_name", "schema_version", "activation_status", "input_track", "input_set_id", "video_output_dir", "artifacts", "quality_inputs_manifest", "quality_inputs", "allowed_artifacts_manifest", "compile_provenance", "acceptance_criteria", "acceptance_dimension_map", "rendered_pages", "provider", "invocation", "adopted_at", "global_gate_authority", "input_set_sha256"}
            _require_shape(binding, required, "Legacy Acceptance Input Set")
            if set(binding) != required or binding.get("schema_version") != "1.0.0" or binding.get("activation_status") != "active_global_gate":
                _reject("unsupported Legacy Acceptance Input Set", "contract_shape", "legacy_acceptance_input_contract_invalid")
            if binding["input_set_sha256"] != _fingerprint_without(binding, "input_set_sha256"):
                _reject("Legacy Acceptance Input Set fingerprint is stale", "input_freshness", "legacy_acceptance_input_stale")
            root = Path(binding["video_output_dir"]).resolve()
            for item in [*binding["artifacts"], *binding["rendered_pages"]["pages"]]:
                path = Path(item.get("path", "")).resolve()
                if not path.is_relative_to(root):
                    _reject("Legacy Acceptance input path escapes its authority", "input_path_boundary", "acceptance_input_path_escape")
                if verify_files and (not path.is_file() or sha256_file(path) != item.get("sha256")):
                    _reject("Legacy Acceptance input is stale", "input_freshness", "acceptance_input_stale", path=str(path))
            for key in ("allowed_artifacts_manifest", "compile_provenance", "acceptance_dimension_map", "quality_inputs_manifest"):
                item = binding[key]
                path = Path(item.get("path", "")).resolve()
                if not path.is_relative_to(root) or (verify_files and (not path.is_file() or sha256_file(path) != item.get("sha256"))):
                    _reject("Legacy Acceptance authority binding is stale", "input_freshness", "acceptance_input_stale", path=str(path))
            if frozenset(binding["quality_inputs"]) not in {
                REQUIRED_ACCEPTANCE_QUALITY_INPUTS,
                REQUIRED_ACCEPTANCE_QUALITY_INPUTS | OPTIONAL_ACCEPTANCE_QUALITY_INPUTS,
            }:
                _reject("Legacy Acceptance quality inputs are incomplete", "quality_input_membership", "legacy_quality_input_incomplete")
            manifest = read_json(Path(binding["quality_inputs_manifest"]["path"]))
            if manifest.get("quality_inputs") != binding["quality_inputs"]:
                _reject("Legacy quality input manifest disagrees with adopted authority", "quality_input_contract", "legacy_quality_input_contract_invalid")
            if verify_files:
                for logical_id, item in binding["quality_inputs"].items():
                    path = Path(item.get("path", "")).resolve()
                    if not path.is_relative_to(root):
                        _reject("Legacy quality input path escapes its authority", "input_path_boundary", "acceptance_input_path_escape", path=str(path))
                    if not path.is_file() or sha256_file(path) != item.get("sha256"):
                        _reject("Legacy quality input is stale", "quality_input_freshness", "legacy_quality_input_stale", logical_id=logical_id)
            criteria = binding["acceptance_criteria"]
            criteria_path = Path(criteria.get("path", "")).resolve()
            if not criteria_path.is_relative_to(self.project_root) or (verify_files and (not criteria_path.is_file() or sha256_file(criteria_path) != criteria.get("sha256"))):
                _reject("Legacy Acceptance criteria binding is stale", "policy_binding", "acceptance_policy_stale")
            if binding.get("provider", {}).get("provider_id") != "legacy-acceptance-adoption-provider":
                _reject("Legacy Acceptance provider identity is unsupported", "input_identity", "legacy_provider_unsupported")
            current_gate = GlobalGatePublisher().require_current(control_store_root=Path(binding["global_gate_authority"].get("control_store_root", "")))
            if current_gate != binding["global_gate_authority"]:
                _reject("Legacy Acceptance Global Gate binding is stale", "global_gate_authority", "global_gate_authority_stale")
            return
        _require_shape(binding, {"schema_name", "schema_version", "activation_status", "input_track", "binding_id", "run", "quality_inputs", "artifacts", "rendered_pages", "binding_sha256"}, "Acceptance input binding")
        if binding["schema_name"] != "acceptance-v2-input-binding" or binding["schema_version"] != "1.0.0" or binding["activation_status"] != "target_only":
            _reject("unsupported Acceptance input binding", "contract_shape", "acceptance_input_contract_invalid")
        if binding["input_track"] != "kernel":
            _reject("unsupported Acceptance input track", "contract_shape", "acceptance_input_track_invalid")
        if binding["binding_sha256"] != _fingerprint_without(binding, "binding_sha256"):
            _reject("Acceptance input binding fingerprint is invalid", "input_freshness", "acceptance_input_stale")
        run = binding["run"]
        video_root = Path(run["video_root"]).resolve()
        run_record_path = Path(run.get("run_record_path", "")).resolve()
        control_store_root = Path(run.get("control_store_root", "")).resolve()
        current_global_gate = GlobalGatePublisher().require_current(control_store_root=control_store_root)
        if binding.get("global_gate_authority") != current_global_gate:
            _reject("Kernel Acceptance Global Gate binding is stale", "global_gate_authority", "global_gate_authority_stale")
        if run_record_path != video_root / "workflow" / "run.json" or not control_store_root.is_dir():
            _reject("Kernel Run authority path is invalid", "run_lifecycle", "acceptance_run_authority_invalid")
        if not run_record_path.is_file() or sha256_file(run_record_path) != run.get("run_record_sha256"):
            _reject("Kernel Run Record is absent or stale", "run_lifecycle", "acceptance_run_record_stale")
        run_record = read_json(run_record_path)
        try:
            ContractRegistry(self.project_root).validate_run_record(run_record)
            current_run_sha = ControlStore(control_store_root, ContractRegistry(self.project_root)).current_run_record_sha(run["run_id"])
        except KernelError as exc:
            _reject("Kernel Run authority is invalid", "run_lifecycle", "acceptance_run_authority_invalid", detail=str(exc))
        checkpoint = run_record.get("checkpoints", {}).get("source_ready")
        recorded_producers = sorted({
            item["producer"] for item in run_record.get("artifact_generations", {}).values()
            if isinstance(item, dict) and isinstance(item.get("producer"), str)
        })
        if (
            run_record.get("run_id") != run.get("run_id")
            or run_record.get("coordination_revision") != run.get("coordination_revision")
            or Path(run_record.get("output_path", "")).resolve() != video_root
            or current_run_sha != run.get("run_record_sha256")
            or not isinstance(checkpoint, dict)
            or checkpoint.get("status") != "current"
            or run.get("checkpoint") != {"name": "source_ready", "status": "current", "evidence_sha256": checkpoint.get("evidence_sha256")}
            or run.get("producer_ids") != recorded_producers
            or not set(run.get("repairer_ids", [])) <= set(recorded_producers)
        ):
            _reject("Kernel Run Record or Control Store authority is not current", "run_lifecycle", "acceptance_run_lifecycle_invalid")
        final_checkpoint = run.get("final_checkpoint", {})
        final_authority_path = Path(final_checkpoint.get("authority_path", "")).resolve()
        expected_final_authority_path = video_root / "workflow" / f"final-quality-ready.{run.get('acceptance_revision')}.json"
        if (
            final_checkpoint.get("name") != "final_quality_ready"
            or final_checkpoint.get("status") != "current"
            or final_authority_path != expected_final_authority_path
            or not final_authority_path.is_file()
            or sha256_file(final_authority_path) != final_checkpoint.get("authority_sha256")
        ):
            _reject("Final Quality checkpoint authority is absent or stale", "run_final_quality_authority", "acceptance_final_authority_stale")
        final_authority = read_json(final_authority_path)
        expected_final_authority = {
            "schema_name": "acceptance-v2-final-quality-authority",
            "schema_version": "1.0.0",
            "activation_status": "target_only",
            "run_id": run["run_id"],
            "run_record_sha256": run["run_record_sha256"],
            "acceptance_revision": run["acceptance_revision"],
            "checkpoint": {"name": "final_quality_ready", "status": "current"},
            "artifact_generations": _final_authority_generations(binding),
        }
        expected_final_authority["authority_sha256"] = _fingerprint_without(expected_final_authority, "authority_sha256")
        if final_authority != expected_final_authority:
            _reject("Final Quality checkpoint does not exactly bind current Artifact Generations", "run_final_quality_authority", "acceptance_final_authority_binding_invalid")
        if require_published_final_authority:
            with self._connect_final_authority(control_store_root) as authority_store:
                published = authority_store.execute("SELECT * FROM final_quality_authority WHERE run_id=?", (run["run_id"],)).fetchone()
            if (
                published is None
                or published["acceptance_revision"] != run["acceptance_revision"]
                or published["run_record_sha256"] != run["run_record_sha256"]
                or Path(published["authority_path"]).resolve() != final_authority_path
                or published["authority_sha256"] != final_checkpoint["authority_sha256"]
            ):
                _reject("Final Quality checkpoint is not the published Control Store authority", "run_final_quality_authority", "acceptance_final_authority_unpublished")
        pages = [item.get("page") for item in binding["rendered_pages"]]
        if pages != list(range(1, len(pages) + 1)) or not pages:
            _reject("rendered pages must exactly cover 1..page_count", "input_page_coverage", "acceptance_input_page_coverage")
        logical_ids = [item.get("logical_id") for item in binding["artifacts"]]
        if len(logical_ids) != len(set(logical_ids)) or not {"final_pdf", "main_tex"} <= set(logical_ids):
            _reject("Acceptance artifacts are incomplete or duplicated", "input_membership", "acceptance_input_membership_invalid")
        changed_generation_ids = run.get("changed_generation_ids")
        predecessor = run.get("predecessor_generation_set_sha256")
        if (
            not isinstance(changed_generation_ids, list)
            or len(changed_generation_ids) != len(set(changed_generation_ids))
            or not set(changed_generation_ids) <= set(logical_ids)
            or (predecessor is None and changed_generation_ids)
            or (predecessor is not None and not changed_generation_ids)
        ):
            _reject("Kernel Run changed-generation declaration is invalid", "repair_lineage", "acceptance_changed_generation_ids_invalid")
        required_quality = {
            "precompile_quality_report": ("precompile-quality-report", "overall_decision", "pass"),
            "precompile_text_seal": ("precompile-text-seal", None, None),
            "rendered_text_reconciliation": ("rendered-text-reconciliation-report", "overall_decision", "pass"),
            "final_artifact_seal": ("final-artifact-seal", None, None),
            "final_compile_manifest": ("final-compile-manifest", None, None),
            "render_evidence_manifest": ("render-evidence-manifest", None, None),
            "rendered_text_object_inventory": ("rendered-text-object-inventory", None, None),
            "text_origin_manifest": ("text-origin-manifest", None, None),
        }
        optional_quality = {"text_equivalence_report": ("text-equivalence-report", "overall_decision", "equivalent")}
        if frozenset(binding["quality_inputs"]) not in {frozenset(required_quality), frozenset(required_quality) | frozenset(optional_quality)}:
            _reject("Acceptance quality inputs are incomplete", "quality_input_membership", "acceptance_quality_input_incomplete")
        if verify_files:
            for item in [*binding["artifacts"], *binding["rendered_pages"]]:
                path = Path(item["path"]).resolve()
                if not path.is_relative_to(video_root):
                    _reject("Acceptance input path escapes the authorized video root", "input_path_boundary", "acceptance_input_path_escape", path=str(path))
                if not path.is_file() or sha256_file(path) != item["sha256"]:
                    _reject("Acceptance input artifact is stale", "input_freshness", "acceptance_input_stale", path=str(path))
            final_pdf_sha = next(item["sha256"] for item in binding["artifacts"] if item["logical_id"] == "final_pdf")
            quality_values: dict[str, dict[str, Any]] = {}
            for logical_id, (schema_name, decision_field, required_decision) in {**required_quality, **{key: value for key, value in optional_quality.items() if key in binding["quality_inputs"]}}.items():
                item = binding["quality_inputs"][logical_id]
                path = Path(item.get("path", "")).resolve()
                if not path.is_relative_to(video_root):
                    _reject("Acceptance quality path escapes the authorized video root", "input_path_boundary", "acceptance_input_path_escape", path=str(path))
                if not path.is_file() or sha256_file(path) != item.get("sha256"):
                    _reject("Acceptance quality evidence is stale", "input_freshness", "acceptance_input_stale", path=str(path))
                value = read_json(path)
                self.registry.validate(schema_name, value)
                quality_values[logical_id] = value
                if value.get("schema_name") != schema_name:
                    _reject("Acceptance quality evidence identity is invalid", "quality_input_validity", "acceptance_quality_input_invalid")
                if decision_field and value.get(decision_field) != required_decision:
                    _reject("Acceptance requires passing prerequisite quality evidence", "quality_input_validity", "acceptance_quality_prerequisite_failed")
            precompile_report = quality_values["precompile_quality_report"]
            precompile_seal = quality_values["precompile_text_seal"]
            final_seal = quality_values["final_artifact_seal"]
            reconciliation = quality_values["rendered_text_reconciliation"]
            compile_manifest = quality_values["final_compile_manifest"]
            render_manifest = quality_values["render_evidence_manifest"]
            rendered_inventory = quality_values["rendered_text_object_inventory"]
            origin_manifest = quality_values["text_origin_manifest"]
            equivalence = quality_values.get("text_equivalence_report")
            expected_owners = {
                "source-faithfulness-reviewer",
                "writing-quality-reviewer",
                "pyramid-reviewer",
            }
            owner_reports = precompile_report["owner_reports"]
            owner_fields = {
                "owner", "task_id", "skeleton_sha256", "patch_sha256",
                "commit_sha256", "reviewer", "result_count", "decision",
            }
            if (
                {item.get("owner") for item in owner_reports} != expected_owners
                or any(not owner_fields <= set(item) for item in owner_reports)
                or any(item.get("decision") != "pass" for item in owner_reports)
                or any(not isinstance(item.get("reviewer"), dict) or not item["reviewer"].get("reviewer_id") for item in owner_reports)
                or any(not isinstance(item.get("result_count"), int) or item["result_count"] < 1 for item in owner_reports)
                or any(not all(isinstance(item.get(field), str) and len(item[field]) == 64 for field in ("skeleton_sha256", "patch_sha256", "commit_sha256")) for item in owner_reports)
            ):
                _reject("Precompile semantic-owner provenance is incomplete", "quality_input_validity", "acceptance_precompile_owner_provenance_invalid")
            if (
                precompile_report["catalog_sha256"] != sha256_file(self.project_root / "delivery-quality/v1/rule-catalog.v1.json")
                or precompile_report["role_projections_sha256"] != sha256_file(self.project_root / "delivery-quality/v1/role-projections.v1.json")
            ):
                _reject("Precompile policy bindings are stale", "quality_input_validity", "acceptance_precompile_policy_stale")
            expected_precompile_provider = {
                "provider_id": "precompile-quality-provider",
                "provider_version": "1.0.0",
                "provider_sha256": sha256_file(self.project_root / "src/video2pdf_workflow_kernel/precompile_quality.py"),
            }
            if precompile_report.get("provider") != expected_precompile_provider or precompile_seal.get("provider") != expected_precompile_provider:
                _reject("Precompile provider provenance is unregistered", "quality_input_validity", "acceptance_precompile_provider_invalid")
            if precompile_report["report_sha256"] != _fingerprint_without(precompile_report, "report_sha256"):
                _reject("Precompile Quality Report fingerprint is invalid", "quality_input_validity", "acceptance_precompile_report_stale")
            seal_lineage_valid = (
                precompile_seal["seal_sha256"] == _fingerprint_without(precompile_seal, "seal_sha256")
                and precompile_seal["precompile_quality_report_sha256"] == precompile_report["report_sha256"]
            )
            if precompile_seal.get("decision_origin") == "fresh_evaluation":
                seal_lineage_valid = seal_lineage_valid and equivalence is None and precompile_seal["generation_set_sha256"] == precompile_report["generation_set_sha256"] and precompile_seal.get("predecessor_seal_sha256") is None and precompile_seal.get("text_equivalence_report_sha256") is None
            elif precompile_seal.get("decision_origin") == "reused_after_text_equivalence":
                seal_lineage_valid = seal_lineage_valid and bool(
                    equivalence
                    and equivalence.get("report_sha256") == _fingerprint_without(equivalence, "report_sha256")
                    and precompile_seal.get("text_equivalence_report_sha256") == equivalence.get("report_sha256")
                    and precompile_seal.get("predecessor_seal_sha256") == equivalence.get("prior_seal_sha256")
                    and precompile_report["generation_set_sha256"] == equivalence.get("prior_generation_set_sha256")
                    and precompile_seal["generation_set_sha256"] == equivalence.get("successor_generation_set_sha256")
                    and precompile_seal["inventory_sha256"] == equivalence.get("successor_inventory_sha256")
                )
            else:
                seal_lineage_valid = False
            if not seal_lineage_valid:
                _reject("Precompile Text Seal lineage is invalid", "quality_input_validity", "acceptance_precompile_seal_stale")
            if final_seal["seal_sha256"] != _fingerprint_without(final_seal, "seal_sha256") or final_seal["precompile_text_seal_sha256"] != precompile_seal["seal_sha256"] or final_seal["generation_set_sha256"] != precompile_seal["generation_set_sha256"] or final_seal["final_pdf"]["sha256"] != final_pdf_sha:
                _reject("Final Artifact Seal lineage is invalid", "quality_input_validity", "acceptance_final_artifact_seal_stale")
            expected_compile_provider = {
                "provider_id": "guarded-final-compile-provider",
                "provider_sha256": sha256_file(self.project_root / "src/video2pdf_workflow_kernel/final_compile.py"),
            }
            if final_seal.get("compile_provider") != expected_compile_provider or compile_manifest.get("manifest_sha256") != final_seal.get("compile_manifest_sha256") or compile_manifest.get("precompile_text_seal_sha256") != precompile_seal["seal_sha256"]:
                _reject("Final compile provenance is invalid", "quality_input_validity", "acceptance_final_compile_provenance_invalid")
            if compile_manifest.get("manifest_sha256") != _fingerprint_without(compile_manifest, "manifest_sha256"):
                _reject("Final Compile Manifest fingerprint is invalid", "quality_input_validity", "acceptance_final_compile_manifest_stale")
            if reconciliation["report_sha256"] != _fingerprint_without(reconciliation, "report_sha256") or reconciliation["precompile_text_seal_sha256"] != precompile_seal["seal_sha256"] or reconciliation["final_artifact_seal_sha256"] != final_seal["seal_sha256"] or reconciliation["final_pdf_sha256"] != final_pdf_sha:
                _reject("Rendered Text Reconciliation lineage is invalid", "quality_input_validity", "acceptance_reconciliation_stale")
            if reconciliation.get("provider") != {"provider_id": "rendered-text-reconciliation-provider", "provider_version": "1.0.0"}:
                _reject("Reconciliation provider provenance is unregistered", "quality_input_validity", "acceptance_reconciliation_provider_invalid")
            bound_pages = [
                {"page": item["page"], "path": str(Path(item["path"]).resolve()), "sha256": item["sha256"]}
                for item in binding["rendered_pages"]
            ]
            manifested_pages = [
                {"page": item["page"], "path": str(Path(item["path"]).resolve()), "sha256": item["sha256"]}
                for item in render_manifest.get("pages", [])
            ]
            if render_manifest.get("page_count") != len(bound_pages) or manifested_pages != bound_pages:
                _reject("Rendered page binding differs from compiler-produced evidence", "quality_input_validity", "acceptance_rendered_pages_stale")
            if (
                render_manifest.get("manifest_sha256") != _fingerprint_without(render_manifest, "manifest_sha256")
                or rendered_inventory.get("inventory_sha256") != _fingerprint_without(rendered_inventory, "inventory_sha256")
                or origin_manifest.get("manifest_sha256") != _fingerprint_without(origin_manifest, "manifest_sha256")
                or
                render_manifest.get("manifest_sha256") != reconciliation.get("render_evidence_manifest_sha256")
                or rendered_inventory.get("inventory_sha256") != reconciliation.get("rendered_text_inventory_sha256")
                or origin_manifest.get("manifest_sha256") != reconciliation.get("text_origin_manifest_sha256")
                or render_manifest.get("final_pdf_sha256") != final_pdf_sha
                or rendered_inventory.get("final_pdf_sha256") != final_pdf_sha
                or origin_manifest.get("precompile_text_seal_sha256") != precompile_seal["seal_sha256"]
                or origin_manifest.get("final_artifact_seal_sha256") != final_seal["seal_sha256"]
                or origin_manifest.get("rendered_text_inventory_sha256") != rendered_inventory.get("inventory_sha256")
                or origin_manifest.get("compiler_provider") != expected_compile_provider
            ):
                _reject("Rendered evidence provenance is incomplete", "quality_input_validity", "acceptance_rendered_evidence_invalid")

    def _validate_patch(self, patch: dict[str, Any], dimension: str, skeleton: dict[str, Any], binding: dict[str, Any]) -> None:
        domain = AcceptanceInputDomain.from_binding(binding)
        _require_shape(patch, {"schema_name", "schema_version", "dimension", "task_id", "attempt_id", "claim_generation", "fencing_token", "skeleton_sha256", "reviewer", "actual_read_set", "criterion_results", "visual_scan_evidence", "cross_phase_findings", "contract_gaps", "patch_sha256"}, "Acceptance Judgment Patch")
        if patch["patch_sha256"] != _fingerprint_without(patch, "patch_sha256"):
            _reject("Patch fingerprint is stale", "patch_identity", "acceptance_patch_fingerprint_invalid")
        task = skeleton["dimensions"][dimension]
        if patch["dimension"] != dimension or patch["task_id"] != task["task_id"] or patch["attempt_id"] != task["attempt_id"] or patch["skeleton_sha256"] != skeleton["skeleton_sha256"]:
            _reject("Patch task authority is stale", "patch_identity", "acceptance_patch_authority_invalid")
        if patch["reviewer"].get("independent") is not True:
            _reject("Acceptance reviewer is not independent", "reviewer_independence", "acceptance_reviewer_not_independent")
        reviewer_id = patch["reviewer"].get("reviewer_id")
        disallowed_reviewers = set(domain.producer_ids) | set(domain.repairer_ids)
        if reviewer_id in disallowed_reviewers:
            _reject("Acceptance reviewer overlaps an artifact producer or repairer", "reviewer_independence", "acceptance_reviewer_identity_overlap")
        criterion_ids = [item.get("criterion_id") for item in patch["criterion_results"]]
        if criterion_ids != task["criterion_ids"] or len(criterion_ids) != len(set(criterion_ids)):
            _reject("Patch criterion coverage is incomplete", "criterion_coverage", "acceptance_criterion_coverage")
        for result in patch["criterion_results"]:
            if result.get("decision") not in {"pass", "fail"} or not result.get("evidence"):
                _reject("criterion result is incomplete", "criterion_coverage", "acceptance_criterion_result_invalid")
        if dimension == "visual_quality":
            visual = patch.get("visual_scan_evidence") or {}
            page_results = visual.get("pages_checked", [])
            pages = [item.get("page") for item in page_results]
            if pages != skeleton["required_visual_pages"] or len(pages) != len(set(pages)):
                _reject("Visual Patch lacks exact individual page coverage", "visual_page_coverage", "acceptance_visual_page_coverage")
            if any(item.get("decision") not in {"pass", "fail"} or not item.get("evidence") for item in page_results):
                _reject("Visual Patch lacks page-specific decisions or evidence", "visual_page_coverage", "acceptance_visual_page_evidence_incomplete")
            if any(item["decision"] == "fail" for item in page_results) and not any(item["decision"] == "fail" for item in patch["criterion_results"]):
                _reject("Visual page failures are absent from criterion decisions", "criterion_coverage", "acceptance_visual_failure_unmapped")
            bound_pages = {item["page"]: item for item in domain.pages}
            if any(
                Path(item.get("path", "")).resolve() != Path(bound_pages[item["page"]]["path"]).resolve()
                or item.get("sha256") != bound_pages[item["page"]]["sha256"]
                for item in page_results
            ):
                _reject("Visual page evidence is not bound to the authorized input", "visual_page_coverage", "acceptance_visual_page_binding_stale")
        final_pdf = next(item for item in domain.artifacts if item["logical_id"] == "final_pdf")
        expected_reads = [
            {"logical_id": "final_pdf", "path": str(Path(final_pdf["path"]).resolve()), "sha256": final_pdf["sha256"]},
            *[
                {"logical_id": f"rendered_page:{item['page']}", "path": str(Path(item["path"]).resolve()), "sha256": item["sha256"]}
                for item in domain.pages
            ],
        ]
        actual_reads = [
            {**item, "path": str(Path(item.get("path", "")).resolve())}
            for item in patch["actual_read_set"]
        ]
        if actual_reads != expected_reads:
            _reject("Patch read set does not exactly cover its dimension boundary", "allowed_read_set", "acceptance_read_set_incomplete")

    def _committed_patch_authority_current(self, root: Path, execution_root: Path, execution: dict[str, Any], dimension: str, record: dict[str, Any]) -> bool:
        patch_sha = record.get("patch_sha256")
        expected_id = _id(execution["execution_id"], dimension, patch_sha)
        canonical = execution_root / "committed" / dimension / expected_id / "judgment-patch.json"
        intent_path = execution_root / "intents" / f"patch-{dimension}-{expected_id}.json"
        if (
            record.get("intent_id") != expected_id
            or Path(record.get("path", "")).resolve() != canonical.resolve()
            or record.get("generation") != 1
            or not canonical.is_file()
            or not intent_path.is_file()
            or sha256_file(canonical) != record.get("file_sha256")
        ):
            return False
        patch = read_json(canonical)
        intent = read_json(intent_path)
        if (
            patch.get("patch_sha256") != patch_sha
            or patch_sha != _fingerprint_without(patch, "patch_sha256")
            or intent.get("state") != "COMMITTED"
            or intent.get("intent_kind") != "acceptance_patch_publication"
            or intent.get("intent_id") != expected_id
            or intent.get("dimension") != dimension
            or intent.get("patch_sha256") != patch_sha
            or Path(intent.get("canonical_path", "")).resolve() != canonical.resolve()
        ):
            return False
        with self._connect_control(root) as control:
            stored = control.execute("SELECT * FROM publication_intents WHERE intent_id=?", (expected_id,)).fetchone()
        return bool(
            stored
            and stored["execution_id"] == execution["execution_id"]
            and stored["expected_revision"] == intent.get("expected_execution_revision")
            and stored["kind"] == intent["intent_kind"]
            and stored["state"] == "COMMITTED"
            and stored["artifact_sha256"] == patch_sha
        )

    def _require_committed_patch_authority(self, root: Path, execution_root: Path, execution: dict[str, Any], dimension: str, record: dict[str, Any]) -> bool:
        if not self._committed_patch_authority_current(root, execution_root, execution, dimension, record):
            _reject("committed Patch authority is stale", "patch_freshness", "acceptance_patch_authority_stale")
        return True

    def _finish_patch_intent(self, root: Path, execution_root: Path, execution: dict[str, Any], intent: dict[str, Any], intent_path: Path, *, fault_after_control: bool = False) -> None:
        self._validate_intent_paths(execution_root, execution, intent, intent_path)
        expected = intent["expected_execution_revision"]
        if execution["execution_revision"] not in {expected, expected + 1}:
            _reject("Patch publication lost its execution revision fence", "patch_fencing", "acceptance_patch_revision_stale")
        canonical = Path(intent["canonical_path"])
        replacement = copy.deepcopy(execution)
        replacement["committed_patches"][intent["dimension"]] = {
            "patch_sha256": intent["patch_sha256"],
            "file_sha256": sha256_file(canonical),
            "path": str(canonical),
            "intent_id": intent["intent_id"],
            "generation": 1,
        }
        replacement["execution_revision"] = expected + 1
        replacement["execution_sha256"] = _fingerprint_without(replacement, "execution_sha256")
        lost_fence = False
        with self._connect_control(root) as control:
            control.execute("BEGIN IMMEDIATE")
            authority = control.execute("SELECT * FROM execution_authority WHERE singleton=1").fetchone()
            stored_intent = control.execute("SELECT * FROM publication_intents WHERE intent_id=?", (intent["intent_id"],)).fetchone()
            staged_patch = read_json(canonical) if canonical.is_file() else {}
            bytes_match = (
                staged_patch.get("patch_sha256") == intent["patch_sha256"]
                and staged_patch.get("patch_sha256") == _fingerprint_without(staged_patch, "patch_sha256")
            )
            stored_matches = bool(
                stored_intent
                and stored_intent["execution_id"] == execution["execution_id"]
                and stored_intent["expected_revision"] == expected
                and stored_intent["kind"] == intent["intent_kind"]
                and stored_intent["artifact_sha256"] == intent["patch_sha256"]
            )
            if authority and authority["execution_id"] == execution["execution_id"] and authority["execution_revision"] == expected and stored_matches and stored_intent["state"] == "PREPARED" and bytes_match:
                control.execute("UPDATE execution_authority SET execution_revision=? WHERE singleton=1", (expected + 1,))
                committed_patch = read_json(canonical)
                control.execute("UPDATE reviewer_claims SET state='TERMINAL' WHERE task_id=? AND state='ACTIVE'", (committed_patch["task_id"],))
                control.execute("UPDATE publication_intents SET state='COMMITTED' WHERE intent_id=?", (intent["intent_id"],))
            elif not (authority and authority["execution_id"] == execution["execution_id"] and authority["execution_revision"] == expected + 1 and stored_intent and stored_intent["state"] == "COMMITTED"):
                if stored_intent and stored_intent["state"] == "PREPARED":
                    control.execute("UPDATE publication_intents SET state='ABORTED' WHERE intent_id=?", (intent["intent_id"],))
                lost_fence = True
            control.execute("COMMIT")
        if lost_fence:
            intent["state"] = "ABORTED"
            write_json_atomic(intent_path, intent)
            _reject("Patch publication lost its Control Store revision fence", "patch_fencing", "acceptance_patch_revision_stale")
        if fault_after_control:
            raise AcceptanceV2Fault("after_patch_control_commit")
        write_json_atomic(execution_root / "execution.json", replacement)
        write_json_atomic(root / "execution.json", replacement)
        intent["state"] = "COMMITTED"
        write_json_atomic(intent_path, intent)

    def _report_bundle_valid(self, bundle_root: Path, intent: dict[str, Any]) -> bool:
        report_path = bundle_root / "acceptance_report.json"
        attempt_path = bundle_root / "attempt-record.json"
        ledger_path = bundle_root / "repair-ledger.json"
        if not report_path.is_file() or not attempt_path.is_file() or not ledger_path.is_file():
            return False
        report = read_json(report_path)
        attempt = read_json(attempt_path)
        ledger = read_json(ledger_path)
        try:
            self.registry.validate("acceptance-report-v2", report)
            self.registry.validate("acceptance-v2-attempt-record", attempt)
            self.registry.validate("acceptance-v2-repair-ledger", ledger)
        except ContractError:
            return False
        required_attempt = {"schema_name", "schema_version", "input_track", "execution_id", "attempt_number", "input_binding_sha256", "artifact_set_sha256", "overall_status", "routing_state", "attempt_record_sha256"}
        if not required_attempt <= set(attempt):
            return False
        report_sha = report.get("report_sha256")
        attempt_sha = attempt.get("attempt_record_sha256")
        ledger_sha = ledger.get("ledger_sha256")
        return bool(
            report_sha == intent.get("report_sha256")
            and report_sha == _fingerprint_without(report, "report_sha256")
            and attempt_sha == intent.get("attempt_record_sha256")
            and attempt_sha == _fingerprint_without(attempt, "attempt_record_sha256")
            and ledger_sha == intent.get("ledger_sha256")
            and ledger_sha == _fingerprint_without(ledger, "ledger_sha256")
            and report.get("attempt_record_sha256") == attempt_sha
            and report.get("repair_ledger_sha256") == ledger_sha
            and report.get("execution_id") == attempt.get("execution_id")
            and intent.get("bundle_sha256") == _report_bundle_sha(report_sha, attempt_sha, ledger_sha)
        )

    def _finish_report_intent(self, root: Path, execution_root: Path, execution: dict[str, Any], intent: dict[str, Any], intent_path: Path, *, fault_after_control: bool = False) -> None:
        self._validate_intent_paths(execution_root, execution, intent, intent_path)
        expected = intent["expected_execution_revision"]
        if execution["execution_revision"] not in {expected, expected + 1}:
            _reject("report publication lost its execution revision fence", "report_fencing", "acceptance_report_revision_stale")
        replacement = copy.deepcopy(execution)
        replacement["report_publication"] = {"intent_id": intent["intent_id"], "report_sha256": intent["report_sha256"], "path": intent["canonical_path"]}
        replacement["execution_revision"] = expected + 1
        replacement["state"] = "materialized"
        replacement["execution_sha256"] = _fingerprint_without(replacement, "execution_sha256")
        lost_fence = False
        with self._connect_control(root) as control:
            control.execute("BEGIN IMMEDIATE")
            authority = control.execute("SELECT * FROM execution_authority WHERE singleton=1").fetchone()
            stored_intent = control.execute("SELECT * FROM publication_intents WHERE intent_id=?", (intent["intent_id"],)).fetchone()
            report_path = Path(intent.get("staged_path", intent["canonical_path"]))
            bytes_match = self._report_bundle_valid(report_path.parent, intent)
            stored_matches = bool(
                stored_intent
                and stored_intent["execution_id"] == execution["execution_id"]
                and stored_intent["expected_revision"] == expected
                and stored_intent["kind"] == intent["intent_kind"]
                and stored_intent["artifact_sha256"] == intent["bundle_sha256"]
            )
            if authority and authority["execution_id"] == execution["execution_id"] and authority["execution_revision"] == expected and stored_matches and stored_intent["state"] == "PREPARED" and bytes_match:
                control.execute("UPDATE execution_authority SET execution_revision=?, state='terminal' WHERE singleton=1", (expected + 1,))
                control.execute("UPDATE publication_intents SET state='COMMITTED' WHERE intent_id=?", (intent["intent_id"],))
            elif not (authority and authority["execution_id"] == execution["execution_id"] and authority["execution_revision"] == expected + 1 and authority["state"] == "terminal" and stored_intent and stored_intent["state"] == "COMMITTED"):
                if stored_intent and stored_intent["state"] == "PREPARED":
                    control.execute("UPDATE publication_intents SET state='ABORTED' WHERE intent_id=?", (intent["intent_id"],))
                lost_fence = True
            control.execute("COMMIT")
        if lost_fence:
            intent["state"] = "ABORTED"
            write_json_atomic(intent_path, intent)
            _reject("report publication lost its Control Store revision fence", "report_fencing", "acceptance_report_revision_stale")
        if fault_after_control:
            raise AcceptanceV2Fault("after_report_control_commit")
        staged_root = Path(intent.get("staged_path", intent["canonical_path"])).parent
        published_root = Path(intent["canonical_path"]).parent
        published_root.mkdir(parents=True, exist_ok=True)
        write_json_atomic(Path(intent["canonical_path"]), read_json(staged_root / "acceptance_report.json"))
        write_json_atomic(published_root / "attempt-record.json", read_json(staged_root / "attempt-record.json"))
        write_json_atomic(published_root / "repair-ledger.json", read_json(staged_root / "repair-ledger.json"))
        write_json_atomic(execution_root / "execution.json", replacement)
        write_json_atomic(root / "execution.json", replacement)
        write_json_atomic(root / "acceptance_report.json", read_json(Path(intent["canonical_path"])))
        staged_ledger_path = published_root / "repair-ledger.json"
        if staged_ledger_path.is_file():
            write_json_atomic(root / "repair-ledger.json", read_json(staged_ledger_path))
        intent["state"] = "COMMITTED"
        write_json_atomic(intent_path, intent)
