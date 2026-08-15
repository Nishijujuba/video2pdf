from __future__ import annotations

import hashlib
import json
import sys


SLICE_BASE_COMMIT = "f20b9caff0aa0af1a83f89877ab0c7c0292308d6"
SLICE_NUMBER = 13
SLICE_NAME = "youtube-platform-kernel-cutover"
EVIDENCE_PREFIX = "evidence/slice-13/"

ATOMIC_MEMBERS = (
    "youtube_adapter",
    "kernel_run_authority",
    "task_ownership",
    "delivery_contracts",
    "delivery_lifecycle",
    "acceptance_v2_binding",
    "delivery_guard_binding",
    "hooks",
    "youtube_skill",
    "project_instructions",
    "validators",
    "tests",
    "activation_documentation",
    "guarded_delivery_evidence",
)
ATOMIC_MEMBER_STATUS = {member: "active" for member in ATOMIC_MEMBERS}
PLATFORM_STATUSES = {"bilibili": "active_kernel", "youtube": "active_kernel"}

RESULT_SPECS = (
    ("youtube_kernel_cutover_pass", "positive", "test_youtube_kernel_cutover_pass", None, None),
    ("bilibili_kernel_preserved", "positive", "test_bilibili_kernel_preserved", None, None),
    (
        "bilibili_authority_change_rejected",
        "negative",
        "test_bilibili_authority_change_rejected",
        "platform_statuses",
        "bilibili_platform_authority_changed",
    ),
)
QUALIFICATION_TEST_TARGETS = tuple(
    f"tests.video_workflow.test_issue14_exit_evidence.Issue14ExitEvidenceTests.{target}"
    for _, _, target, _, _ in RESULT_SPECS
)
COMMANDS = (
    (
        "issue14-platform-cutover-tests",
        (sys.executable, "-X", "utf8", "-B", "-m", "unittest", "-v", *QUALIFICATION_TEST_TARGETS),
        0,
    ),
    (
        "issue14-exit-evidence-tests",
        (sys.executable, "-X", "utf8", "-B", "-m", "unittest", "-v", "tests.video_workflow.test_issue14_exit_evidence"),
        0,
    ),
)

# Semantic command identity: the interpreter (argv[0]) is machine-local
# execution-environment evidence, not semantic identity. The semantic identity
# of a closed qualification command is argv[1:] -- the Python flags, module,
# verbosity, and test targets. Validation of persisted command records matches
# these roles so a repository can be relocated or re-interpreted without the
# interpreter's absolute path becoming part of the identity contract.
QUALIFICATION_INTERPRETER_ROLE = "qualification_python"
QUALIFICATION_CWD_ROLE = "project_root"
SEMANTIC_ARGUMENTS = tuple(
    tuple(command_argv[1:]) for _command_id, command_argv, _exit in COMMANDS
)
RESULT_BINDINGS = [
    {
        "result_id": result_id,
        "result_kind": result_kind,
        "command_id": "issue14-platform-cutover-tests",
        "test_target": target,
        **({"expected_first_failing_gate": gate, "expected_error_code": code} if result_kind == "negative" else {}),
    }
    for (result_id, result_kind, _short_target, gate, code), target in zip(RESULT_SPECS, QUALIFICATION_TEST_TARGETS, strict=True)
]
RESULTS = {
    kind: [item["result_id"] for item in RESULT_BINDINGS if item["result_kind"] == kind]
    for kind in ("positive", "negative")
}
RESULTS["recovery"] = ["reconcile_interrupted_youtube_cutover"]
RESULT_BINDINGS.append(
    {
        "result_id": "reconcile_interrupted_youtube_cutover",
        "result_kind": "recovery",
        "command_id": "issue14-exit-evidence-tests",
        "test_target": "tests.video_workflow.test_issue14_exit_evidence",
    }
)
EXPECTED_CHECKPOINTS = [
    {"name": "bilibili_platform_kernel", "status": "preserved"},
    {"name": "youtube_platform_kernel", "status": "current"},
    {"name": "active_global_gate", "status": "preserved"},
]
FIXTURE_SPECS = (
    ("exit_evidence_manifest_contract", "schemas/exit-evidence-manifest.v2.schema.json"),
    ("slice13_positive_fixture", "tests/video_workflow/fixtures/exit_evidence/slice13.valid.json"),
    ("slice13_single_contradiction_fixture", "tests/video_workflow/fixtures/exit_evidence/slice13.bilibili-kernel.invalid.json"),
)

QUALIFICATION_CONTRACT_SHA256 = hashlib.sha256(
    (json.dumps(RESULT_BINDINGS, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
).hexdigest()
ACTIVATION_SCOPE = {
    "kind": "platform_kernel_cutover",
    "runtime_authority_change": True,
    "components_activated": ["youtube_platform_kernel"],
    "platform": "youtube",
    "global_gate_authority": "unchanged",
    "qualification_contract_sha256": QUALIFICATION_CONTRACT_SHA256,
}
