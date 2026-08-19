from __future__ import annotations

import json
import os
import sys


task = json.load(sys.stdin)
profile_id = task["language_profile"]["profile_id"]
text = task["input_text"].casefold()
rule_id = task["target_rule"]["rule_id"]

decision = "pass"
violation_id = None
exception_id = None
if any(
    marker in text
    for marker in ("preserved to analyze", "为了分析", "以分析")
):
    decision = "pass_with_exception"
    exception_id = f"{rule_id}.attributed_source_quotation"
elif any(
    marker in text
    for marker in (
        "reads reliability",
        "reads the system's reliability",
        "读取了系统的可靠性",
        "读取可靠性",
    )
):
    decision = "fail"
    violation_id = f"{rule_id}.predicate_object_mismatch"
elif (
    any(marker in text for marker in ("statistical significance", "统计显著性"))
    and any(marker in text for marker in ("every patient", "每位患者"))
    and any(marker in text for marker in ("proves", "证明"))
):
    decision = "fail"
    violation_id = f"{rule_id}.semantic_domain_drift"
elif (
    any(marker in text for marker in ("training loss", "训练损失"))
    and any(
        marker in text
        for marker in (
            "production accuracy",
            "accurate in production",
            "生产准确性",
            "生产环境中保持准确",
            "上线后持续准确",
        )
    )
    and not any(
        marker in text
        for marker in ("requires separate", "需要独立", "需要上线评测")
    )
):
    decision = "fail"
    violation_id = f"{rule_id}.lifecycle_stage_confusion"
elif (
    any(marker in text for marker in ("thirty percent", "30%", "百分之三十"))
    and not any(marker in text for marker in (" from ", "从"))
):
    decision = "fail"
    violation_id = f"{rule_id}.modifier_dimension_missing"
elif (
    any(marker in text for marker in ("small classroom trial", "小规模课堂试验"))
    and any(
        marker in text
        for marker in ("every school must", "所有学校都必须", "must adopt")
    )
    and not any(
        marker in text
        for marker in ("does not establish", "不足以", "尚不足")
    )
):
    decision = "fail"
    violation_id = f"{rule_id}.modal_strength_unsupported"

if (
    os.environ.get("DELIVERY_QUALITY_VARIANCE_FIXTURE") == "1"
    and profile_id == "en"
    and "improves system reliability" in text
    and task["attempt_number"] == 3
):
    decision = "fail"
    violation_id = (
        f"{task['target_rule']['rule_id']}.predicate_object_mismatch"
    )

json.dump(
    {
        "schema_name": "delivery-quality-reviewer-result",
        "schema_version": "1.0.0",
        "provider": {
            "name": "deterministic-reviewer-fixture",
            "model_revision": "reviewer-fixture-v1",
            "sampling": "deterministic-fixture",
        },
        "assessment": {
            "decision": decision,
            "violation_id": violation_id,
            "exception_id": exception_id,
            "evidence_locator": task["evidence_locator"],
            "rationale": "Independent fixture Reviewer classified the supplied task.",
        },
    },
    sys.stdout,
    ensure_ascii=False,
)
