from __future__ import annotations

import hashlib
import json
import sys


SLICE_BASE_COMMIT = "2e80c5d1d83bb40cf7ef47a9cc01685728cd615c"
SLICE_NUMBER = 12
SLICE_NAME = "bilibili-platform-kernel-cutover"
EVIDENCE_PREFIX = "evidence/slice-12/"

ATOMIC_MEMBERS = (
    "bilibili_adapter",
    "kernel_run_authority",
    "task_ownership",
    "delivery_contracts",
    "delivery_lifecycle",
    "acceptance_v2_binding",
    "delivery_guard_binding",
    "hooks",
    "bilibili_skill",
    "project_instructions",
    "validators",
    "tests",
    "activation_documentation",
    "guarded_delivery_evidence",
)
ATOMIC_MEMBER_STATUS = {member: "active" for member in ATOMIC_MEMBERS}
PLATFORM_STATUSES = {"bilibili": "active_kernel", "youtube": "active_legacy"}

RESULT_SPECS = (
    ("bilibili_kernel_cutover_pass", "positive", "test_bilibili_kernel_cutover_pass", None, None),
    ("youtube_legacy_preserved", "positive", "test_youtube_legacy_preserved", None, None),
    ("youtube_authority_change_rejected", "negative", "test_youtube_authority_change_rejected", "platform_statuses", "youtube_platform_authority_changed"),
)
QUALIFICATION_TEST_TARGETS = tuple(
    f"tests.video_workflow.test_issue13_exit_evidence.Issue13ExitEvidenceTests.{target}"
    for _, _, target, _, _ in RESULT_SPECS
)
COMMANDS = (
    (
        "issue13-platform-cutover-tests",
        (sys.executable, "-X", "utf8", "-B", "-m", "unittest", "-v", *QUALIFICATION_TEST_TARGETS),
        0,
    ),
    (
        "issue13-exit-evidence-tests",
        (sys.executable, "-X", "utf8", "-B", "-m", "unittest", "-v", "tests.video_workflow.test_issue13_exit_evidence"),
        0,
    ),
)
RESULT_BINDINGS = [
    {
        "result_id": result_id,
        "result_kind": result_kind,
        "command_id": "issue13-platform-cutover-tests",
        "test_target": target,
        **({"expected_first_failing_gate": gate, "expected_error_code": code} if result_kind == "negative" else {}),
    }
    for (result_id, result_kind, _short_target, gate, code), target in zip(RESULT_SPECS, QUALIFICATION_TEST_TARGETS, strict=True)
]
RESULTS = {
    kind: [item["result_id"] for item in RESULT_BINDINGS if item["result_kind"] == kind]
    for kind in ("positive", "negative")
}
RESULTS["recovery"] = ["reconcile_interrupted_cutover"]
RESULT_BINDINGS.append(
    {
        "result_id": "reconcile_interrupted_cutover",
        "result_kind": "recovery",
        "command_id": "issue13-exit-evidence-tests",
        "test_target": "tests.video_workflow.test_issue13_exit_evidence",
    }
)
EXPECTED_CHECKPOINTS = [
    {"name": "bilibili_platform_kernel", "status": "current"},
    {"name": "youtube_platform_kernel", "status": "preserved"},
    {"name": "active_global_gate", "status": "preserved"},
]
FIXTURE_SPECS = (
    ("exit_evidence_manifest_contract", "schemas/exit-evidence-manifest.v2.schema.json"),
    ("slice12_positive_fixture", "tests/video_workflow/fixtures/exit_evidence/slice12.valid.json"),
    ("slice12_single_contradiction_fixture", "tests/video_workflow/fixtures/exit_evidence/slice12.youtube-kernel.invalid.json"),
)

QUALIFICATION_CONTRACT_SHA256 = hashlib.sha256(
    (json.dumps(RESULT_BINDINGS, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
).hexdigest()
ACTIVATION_SCOPE = {
    "kind": "platform_kernel_cutover",
    "runtime_authority_change": True,
    "components_activated": ["bilibili_platform_kernel"],
    "platform": "bilibili",
    "global_gate_authority": "unchanged",
    "qualification_contract_sha256": QUALIFICATION_CONTRACT_SHA256,
}
