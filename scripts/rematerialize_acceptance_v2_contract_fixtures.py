from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "schemas/delivery-quality/registry.v1.json"
SCENARIOS = {
    "acceptance-v2-input-binding": ("activation_status", "active"),
    "acceptance-v2-review-skeleton": ("aggregation_policy", "unsupported"),
    "acceptance-v2-judgment-patch": ("dimension", "text"),
    "acceptance-v2-task-envelope": ("input_access", "read_write"),
    "acceptance-v2-execution-context": ("state", "delivered"),
    "acceptance-report-v2": ("overall_status", "unknown"),
    "acceptance-v2-attempt-record": ("overall_status", "unknown"),
    "acceptance-v2-repair-ledger": ("attempt_limit", 4),
}


def encoded(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def main() -> int:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    entries = {item["schema_name"]: item for item in registry["contracts"]}
    for schema_name, (field, contradiction) in SCENARIOS.items():
        entry = entries[schema_name]
        positive_path = ROOT / entry["positive_example"]
        positive = json.loads(positive_path.read_text(encoding="utf-8"))
        if schema_name == "acceptance-v2-input-binding":
            run = positive["run"]
            run.setdefault("changed_generation_ids", [])
            run.setdefault("acceptance_revision", 1)
            run.setdefault("run_record_path", "C:/video/workflow/run.json")
            run.setdefault("run_record_sha256", "9" * 64)
            run.setdefault("control_store_root", "C:/")
            run["checkpoint"] = {"name": "source_ready", "status": "current", "evidence_sha256": "a" * 64}
            run["final_checkpoint"] = {"name": "final_quality_ready", "status": "current", "authority_path": "C:/video/workflow/final-quality-ready.1.json", "authority_sha256": "b" * 64}
        elif schema_name == "acceptance-report-v2":
            run = positive["run_binding"]
            run.setdefault("changed_generation_ids", [])
            run.setdefault("acceptance_revision", 1)
            run.setdefault("run_record_path", "C:/video/workflow/run.json")
            run.setdefault("run_record_sha256", "9" * 64)
            run.setdefault("control_store_root", "C:/")
            run["checkpoint"] = {"name": "source_ready", "status": "current", "evidence_sha256": "a" * 64}
            run["final_checkpoint"] = {"name": "final_quality_ready", "status": "current", "authority_path": "C:/video/workflow/final-quality-ready.1.json", "authority_sha256": "b" * 64}
            positive.setdefault("repair_ledger_sha256", "6" * 64)
        elif schema_name == "acceptance-v2-task-envelope":
            patch_schema = {
                "logical_id": "judgment_patch_schema",
                "path": "C:/policy/acceptance-v2-judgment-patch.v1.schema.json",
                "sha256": "b" * 64,
            }
            positive["authorized_read_set"] = [
                item
                for item in positive["authorized_read_set"]
                if item["logical_id"] != "judgment_patch_schema"
            ]
            positive["authorized_read_set"].insert(5, patch_schema)
            positive["required_output"] = {
                "logical_id": "judgment_patch",
                "path": positive["required_output"]["path"],
                "contract": {
                    "schema": {
                        "name": "acceptance-v2-judgment-patch",
                        "version": "1.0.0",
                        "path": patch_schema["path"],
                        "sha256": patch_schema["sha256"],
                    },
                    "field_ownership": {
                        "provider_bound": [
                            "schema_name", "schema_version", "dimension",
                            "task_id", "attempt_id", "claim_generation",
                            "fencing_token", "skeleton_sha256", "actual_read_set",
                        ],
                        "reviewer_judgment": [
                            "reviewer", "criterion_results", "visual_scan_evidence",
                            "cross_phase_findings", "contract_gaps",
                        ],
                        "mechanically_derived": ["patch_sha256"],
                        "optional": [],
                        "forbidden": "all_fields_not_declared_by_schema",
                    },
                    "serialization": {
                        "encoding": "utf-8", "ensure_ascii": False,
                        "sort_keys": True, "item_separator": ",",
                        "key_separator": ":", "trailing_newline": "required",
                    },
                    "fingerprint": {
                        "owner": "acceptance_v2_provider",
                        "field": "patch_sha256", "algorithm": "sha256",
                        "input": "canonical_json_without_patch_sha256",
                    },
                },
            }
        positive_bytes = encoded(positive)
        positive_path.write_bytes(positive_bytes)
        entry["canonical_sha256"] = hashlib.sha256(positive_bytes).hexdigest()
        negative = copy.deepcopy(positive)
        negative[field] = contradiction
        (ROOT / entry["negative_example"]).write_bytes(encoded(negative))
    precompile = entries["precompile-quality-report"]
    precompile_path = ROOT / precompile["positive_example"]
    precompile["canonical_sha256"] = hashlib.sha256(precompile_path.read_bytes()).hexdigest()
    REGISTRY.write_bytes((json.dumps(registry, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
