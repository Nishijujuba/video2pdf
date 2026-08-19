from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

from .adapters import FixturePlatformAdapter, PlatformAdapterError
from .contracts import ContractRegistry
from .delivery_quality import DeliveryQualityRegistry
from .control_store import ControlStore
from .control_store_recovery import ControlStoreRecovery
from .errors import (
    CliUsageError,
    ControlStoreUnavailable,
    InitializationFault,
    KernelConflict,
    KernelError,
)
from .kernel import FAULT_POINTS, VideoWorkflowKernel
from .models import BootstrapProbeResult, ProductionBootstrapResult
from .source_live_smoke import SOURCE_ACQUIRE_FAULT_POINTS, run_source_live_smoke
from .task_execution import (
    CLAIM_FAULT_POINTS,
    COMPLETION_FAULT_POINTS,
    PREPARATION_FAULT_POINTS,
    PROMOTION_FAULT_POINTS,
    RECLAIM_FAULT_POINTS,
)
from .content_production import PRODUCTION_FAULT_POINTS
from .guarded_compile import GuardedCompileProvider
from .final_compile import GuardedFinalCompileProvider
from .final_delivery_evidence import (
    FINAL_EVIDENCE_FAULT_POINTS,
    FinalDeliveryEvidenceProvider,
)
from .precompile_quality import (
    MATERIALIZE_FAULT_POINTS,
    PATCH_COMMIT_FAULT_POINTS,
    PREPARE_FAULT_POINTS,
    PrecompileQualityProvider,
)
from .rendered_text_reconciliation import RenderedTextReconciliationProvider
from .acceptance_v2 import (
    MATERIALIZE_FAULT_POINTS as ACCEPTANCE_MATERIALIZE_FAULT_POINTS,
    PATCH_FAULT_POINTS as ACCEPTANCE_PATCH_FAULT_POINTS,
    PREPARE_FAULT_POINTS as ACCEPTANCE_PREPARE_FAULT_POINTS,
    AcceptanceV2Provider,
)
from .batch_projection import BATCH_RUN_FAULT_POINTS, BatchProjectionProvider
from .batch_authority import (
    ACTIVATION_FAULT_POINTS as BATCH_ACTIVATION_FAULT_POINTS,
    BatchCutoverPublisher,
)
from .global_gate import ACTIVATION_FAULT_POINTS, GlobalGatePublisher, LegacyAcceptanceProvider
from .platform_kernel import (
    ACTIVATION_FAULT_POINTS as PLATFORM_ACTIVATION_FAULT_POINTS,
    BilibiliPlatformCutoverPublisher,
)
from .production_bootstrap import (
    bootstrap_bilibili_production_probe,
    bootstrap_youtube_production_probe,
)
from .source_acquire import (
    acquire_bilibili_source_for_run,
    reconcile_bilibili_source_acquire,
)
from .delivery_lifecycle import DeliveryLifecycleProvider, FAULT_POINTS as DELIVERY_FAULT_POINTS
from .delivery_acceptance_binding import DeliveryAcceptanceBindingProvider
from .utils import read_json


class MachineArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CliUsageError(message)


def _parser() -> argparse.ArgumentParser:
    parser = MachineArgumentParser(prog="video_workflow.py")
    commands = parser.add_subparsers(dest="command", required=True)

    contracts = commands.add_parser("contracts-check")
    contracts.add_argument("--registry", type=Path)

    delivery_quality_contracts = commands.add_parser(
        "delivery-quality-contracts-check"
    )
    delivery_quality_contracts.add_argument("--registry", type=Path)
    delivery_quality_contracts.add_argument("--mechanical-fixture")

    delivery_quality_conformance = commands.add_parser(
        "delivery-quality-conformance"
    )
    delivery_quality_conformance.add_argument(
        "--reviewer-adapter", required=True, type=Path
    )
    delivery_quality_conformance.add_argument("--output", required=True, type=Path)
    delivery_quality_conformance.add_argument(
        "--implementation-commit", required=True
    )
    delivery_quality_conformance.add_argument(
        "--track", choices=("kernel", "legacy"), default="kernel"
    )
    delivery_quality_conformance.add_argument(
        "--adapter-id", default="reviewer-subprocess-adapter"
    )

    precompile_prepare = commands.add_parser(
        "delivery-quality-precompile-prepare"
    )
    precompile_prepare.add_argument("--workspace-root", required=True, type=Path)
    precompile_prepare.add_argument("--inventory", required=True, type=Path)
    precompile_prepare.add_argument(
        "--artifact-generations", required=True, type=Path
    )
    precompile_prepare.add_argument(
        "--semantic-dependencies", required=True, type=Path
    )
    precompile_prepare.add_argument("--prepared-at", required=True)
    precompile_prepare.add_argument(
        "--fault-point", choices=sorted(PREPARE_FAULT_POINTS)
    )

    precompile_repair_prepare = commands.add_parser(
        "delivery-quality-precompile-repair-prepare"
    )
    precompile_repair_prepare.add_argument(
        "--predecessor-workspace-root", required=True, type=Path
    )
    precompile_repair_prepare.add_argument(
        "--workspace-root", required=True, type=Path
    )
    precompile_repair_prepare.add_argument(
        "--inventory", required=True, type=Path
    )
    precompile_repair_prepare.add_argument(
        "--artifact-generations", required=True, type=Path
    )
    precompile_repair_prepare.add_argument(
        "--semantic-dependencies", required=True, type=Path
    )
    precompile_repair_prepare.add_argument(
        "--repair-attempt-number", required=True, type=int
    )
    precompile_repair_prepare.add_argument("--prepared-at", required=True)

    precompile_patch_commit = commands.add_parser(
        "delivery-quality-precompile-patch-commit"
    )
    precompile_patch_commit.add_argument(
        "--workspace-root", required=True, type=Path
    )
    precompile_patch_commit.add_argument(
        "--owner", required=True
    )
    precompile_patch_commit.add_argument("--patch", required=True, type=Path)
    precompile_patch_commit.add_argument("--committed-at", required=True)
    precompile_patch_commit.add_argument(
        "--fault-point", choices=sorted(PATCH_COMMIT_FAULT_POINTS)
    )

    precompile_materialize = commands.add_parser(
        "delivery-quality-precompile-materialize"
    )
    precompile_materialize.add_argument(
        "--workspace-root", required=True, type=Path
    )
    precompile_materialize.add_argument("--provider-id", required=True)
    precompile_materialize.add_argument("--provider-version", required=True)
    precompile_materialize.add_argument("--materialized-at", required=True)
    precompile_materialize.add_argument(
        "--fault-point", choices=sorted(MATERIALIZE_FAULT_POINTS)
    )

    precompile_seal = commands.add_parser("delivery-quality-seal")
    precompile_seal.add_argument("--workspace-root", required=True, type=Path)
    precompile_seal.add_argument("--sealed-at", required=True)

    text_equivalence = commands.add_parser(
        "delivery-quality-text-equivalence"
    )
    text_equivalence.add_argument("--workspace-root", required=True, type=Path)
    text_equivalence.add_argument(
        "--successor-inventory", required=True, type=Path
    )
    text_equivalence.add_argument(
        "--successor-artifact-generations", required=True, type=Path
    )
    text_equivalence.add_argument(
        "--mutation-class", required=True
    )
    text_equivalence.add_argument("--proved-at", required=True)

    rendered_text_reconcile = commands.add_parser(
        "delivery-quality-rendered-text-reconcile"
    )
    rendered_text_reconcile.add_argument(
        "--precompile-workspace-root", required=True, type=Path
    )
    rendered_text_reconcile.add_argument("--compile-manifest", required=True, type=Path)
    rendered_text_reconcile.add_argument("--compile-report", required=True, type=Path)
    rendered_text_reconcile.add_argument("--final-artifact-seal", required=True, type=Path)
    rendered_text_reconcile.add_argument("--final-pdf", required=True, type=Path)
    rendered_text_reconcile.add_argument("--render-evidence-manifest", required=True, type=Path)
    rendered_text_reconcile.add_argument("--rendered-text-inventory", required=True, type=Path)
    rendered_text_reconcile.add_argument("--text-origin-manifest", required=True, type=Path)
    rendered_text_reconcile.add_argument("--output", required=True, type=Path)
    rendered_text_reconcile.add_argument("--reconciled-at", required=True)

    final_compile = commands.add_parser("delivery-quality-final-compile")
    final_compile.add_argument("--precompile-workspace-root", required=True, type=Path)
    final_compile.add_argument("--compile-manifest", required=True, type=Path)
    final_compile.add_argument("--text-origin-plan", required=True, type=Path)
    final_compile.add_argument("--compiler-adapter", required=True, type=Path)
    final_compile.add_argument("--runtime-policy", required=True, type=Path)
    final_compile.add_argument("--workspace-root", required=True, type=Path)
    final_compile.add_argument("--compiled-at", required=True)

    final_evidence = commands.add_parser("delivery-final-evidence-prepare")
    final_evidence.add_argument("--run-dir", required=True, type=Path)
    final_evidence.add_argument("--final-pdf", required=True, type=Path)
    final_evidence.add_argument("--main-tex", required=True, type=Path)
    final_evidence.add_argument("--final-compile-report", required=True, type=Path)
    final_evidence.add_argument("--final-compile-manifest", required=True, type=Path)
    final_evidence.add_argument("--precompile-quality-report", required=True, type=Path)
    final_evidence.add_argument("--precompile-text-seal", required=True, type=Path)
    final_evidence.add_argument("--final-artifact-seal", required=True, type=Path)
    final_evidence.add_argument("--rendered-text-reconciliation", required=True, type=Path)
    final_evidence.add_argument("--render-evidence-manifest", required=True, type=Path)
    final_evidence.add_argument("--rendered-text-inventory", required=True, type=Path)
    final_evidence.add_argument("--text-origin-manifest", required=True, type=Path)
    final_evidence.add_argument("--global-gate-authority", required=True, type=Path)
    final_evidence.add_argument("--allowed-manifest", required=True, type=Path)
    final_evidence.add_argument("--prepared-at", required=True)
    final_evidence.add_argument(
        "--fault-point", choices=sorted(FINAL_EVIDENCE_FAULT_POINTS)
    )

    acceptance_authority = commands.add_parser("acceptance-final-authority-publish")
    acceptance_authority.add_argument("--input-binding", required=True, type=Path)

    acceptance_prepare = commands.add_parser("acceptance-prepare")
    acceptance_prepare.add_argument("--workspace-root", required=True, type=Path)
    acceptance_prepare.add_argument("--input-binding", required=True, type=Path)
    acceptance_prepare.add_argument("--attempt-number", required=True, type=int)
    acceptance_prepare.add_argument("--prepared-at", required=True)
    acceptance_prepare.add_argument("--coordinator-session", required=True)
    acceptance_prepare.add_argument("--fault-point", choices=sorted(ACCEPTANCE_PREPARE_FAULT_POINTS))

    acceptance_patch = commands.add_parser("acceptance-patch-commit")
    acceptance_patch.add_argument("--workspace-root", required=True, type=Path)
    acceptance_patch.add_argument("--dimension", required=True, choices=("visual_quality",))
    acceptance_patch.add_argument("--patch", required=True, type=Path)
    acceptance_patch.add_argument("--committed-at", required=True)
    acceptance_patch.add_argument("--fault-point", choices=sorted(ACCEPTANCE_PATCH_FAULT_POINTS))

    acceptance_materialize = commands.add_parser("acceptance-materialize")
    acceptance_materialize.add_argument("--workspace-root", required=True, type=Path)
    acceptance_materialize.add_argument("--provider-id", required=True)
    acceptance_materialize.add_argument("--provider-version", required=True)
    acceptance_materialize.add_argument("--materialized-at", required=True)
    acceptance_materialize.add_argument("--fault-point", choices=sorted(ACCEPTANCE_MATERIALIZE_FAULT_POINTS))

    acceptance_repair = commands.add_parser("acceptance-repair-prepare")
    acceptance_repair.add_argument("--workspace-root", required=True, type=Path)
    acceptance_repair.add_argument("--input-binding", required=True, type=Path)
    acceptance_repair.add_argument("--prepared-at", required=True)
    acceptance_repair.add_argument("--coordinator-session", required=True)

    acceptance_reconcile = commands.add_parser("acceptance-reconcile")
    acceptance_reconcile.add_argument("--workspace-root", required=True, type=Path)

    acceptance_guard = commands.add_parser("acceptance-guard-eligibility")
    acceptance_guard.add_argument("--workspace-root", required=True, type=Path)

    legacy_adopt = commands.add_parser("legacy-acceptance-adopt")
    legacy_adopt.add_argument("--video-output-dir", required=True, type=Path)
    legacy_adopt.add_argument("--final-pdf", required=True, type=Path)
    legacy_adopt.add_argument("--main-tex", required=True, type=Path)
    legacy_adopt.add_argument("--allowed-artifacts-manifest", required=True, type=Path)
    legacy_adopt.add_argument("--compile-report", required=True, type=Path)
    legacy_adopt.add_argument("--criteria", required=True, type=Path)
    legacy_adopt.add_argument("--dimension-map", required=True, type=Path)
    legacy_adopt.add_argument("--rendered-pages-manifest", required=True, type=Path)
    legacy_adopt.add_argument("--quality-inputs-manifest", required=True, type=Path)
    legacy_adopt.add_argument("--control-store-root", required=True, type=Path)
    legacy_adopt.add_argument("--adopted-at", required=True)
    legacy_adopt.add_argument("--output", type=Path)

    global_gate_activate = commands.add_parser("global-gate-activate")
    global_gate_activate.add_argument("--control-store-root", required=True, type=Path)
    global_gate_activate.add_argument("--exit-evidence", required=True, type=Path)
    global_gate_activate.add_argument("--activated-at", required=True)
    global_gate_activate.add_argument("--fault-point", choices=sorted(ACTIVATION_FAULT_POINTS))

    global_gate_reconcile = commands.add_parser("global-gate-reconcile")
    global_gate_reconcile.add_argument("--control-store-root", required=True, type=Path)

    batch_activate = commands.add_parser("batch-activate")
    batch_activate.add_argument("--control-store-root", required=True, type=Path)
    batch_activate.add_argument("--exit-evidence", required=True, type=Path)
    batch_activate.add_argument("--activated-at", required=True)
    batch_activate.add_argument(
        "--fault-point", choices=sorted(BATCH_ACTIVATION_FAULT_POINTS)
    )

    batch_reconcile = commands.add_parser("batch-reconcile")
    batch_reconcile.add_argument("--control-store-root", required=True, type=Path)

    batch_authority_check = commands.add_parser("batch-authority-check")
    batch_authority_check.add_argument(
        "--control-store-root", required=True, type=Path
    )

    workflow_policy_check = commands.add_parser("workflow-policy-check")
    workflow_policy_check.add_argument("--control-store-root", required=True, type=Path)

    platform_kernel_prepare = commands.add_parser("platform-kernel-prepare")
    platform_kernel_prepare.add_argument("--platform", required=True)
    platform_kernel_prepare.add_argument(
        "--control-store-root", required=True, type=Path
    )
    platform_kernel_prepare.add_argument("--implementation-commit", required=True)
    platform_kernel_prepare.add_argument(
        "--candidate-probe", required=True, type=Path
    )
    platform_kernel_prepare.add_argument("--candidate-session-id", required=True)
    platform_kernel_prepare.add_argument("--prepared-at", required=True)

    platform_kernel_candidate_activate = commands.add_parser(
        "platform-kernel-candidate-activate"
    )
    platform_kernel_candidate_activate.add_argument("--platform", required=True)
    platform_kernel_candidate_activate.add_argument(
        "--control-store-root", required=True, type=Path
    )
    platform_kernel_candidate_activate.add_argument(
        "--candidate-run-dir", required=True, type=Path
    )
    platform_kernel_candidate_activate.add_argument("--activated-at", required=True)

    platform_kernel_activate = commands.add_parser("platform-kernel-activate")
    platform_kernel_activate.add_argument("--platform", required=True)
    platform_kernel_activate.add_argument(
        "--control-store-root", required=True, type=Path
    )
    platform_kernel_activate.add_argument(
        "--exit-evidence", required=True, type=Path
    )
    platform_kernel_activate.add_argument("--activated-at", required=True)
    platform_kernel_activate.add_argument(
        "--fault-point", choices=sorted(PLATFORM_ACTIVATION_FAULT_POINTS)
    )

    platform_kernel_reconcile = commands.add_parser("platform-kernel-reconcile")
    platform_kernel_reconcile.add_argument("--platform", required=True)
    platform_kernel_reconcile.add_argument(
        "--control-store-root", required=True, type=Path
    )

    youtube_platform_authority_refresh = commands.add_parser(
        "youtube-platform-authority-refresh"
    )
    youtube_platform_authority_refresh.add_argument(
        "--control-store-root", required=True, type=Path
    )
    youtube_platform_authority_refresh.add_argument(
        "--exit-evidence", required=True, type=Path
    )
    youtube_platform_authority_refresh.add_argument(
        "--expected-generation", required=True, type=int
    )
    youtube_platform_authority_refresh.add_argument("--refreshed-at", required=True)
    youtube_platform_authority_refresh.add_argument(
        "--fault-point", choices=sorted(PLATFORM_ACTIVATION_FAULT_POINTS)
    )

    delivery_transition = commands.add_parser("delivery-transition")
    delivery_transition.add_argument("--run-dir", required=True, type=Path)
    delivery_transition.add_argument("--from-stage", required=True)
    delivery_transition.add_argument("--to-stage", required=True)
    delivery_transition.add_argument("--session-id", required=True)
    delivery_transition.add_argument("--expected-run-revision", required=True, type=int)
    delivery_transition.add_argument(
        "--expected-ownership-generation", required=True, type=int
    )
    delivery_transition.add_argument("--evidence", required=True, type=Path)
    delivery_transition.add_argument("--transitioned-at", required=True)
    delivery_transition.add_argument(
        "--fault-point", choices=sorted(DELIVERY_FAULT_POINTS)
    )

    delivery_acceptance_bind = commands.add_parser("delivery-acceptance-bind")
    delivery_acceptance_bind.add_argument("--run-dir", required=True, type=Path)
    delivery_acceptance_bind.add_argument("--session-id", required=True)
    delivery_acceptance_bind.add_argument(
        "--acceptance-report", required=True, type=Path
    )
    delivery_acceptance_bind.add_argument(
        "--expected-run-revision", required=True, type=int
    )
    delivery_acceptance_bind.add_argument(
        "--expected-ownership-generation", required=True, type=int
    )
    delivery_acceptance_bind.add_argument("--bound-at", required=True)

    delivery_handoff = commands.add_parser("delivery-handoff")
    delivery_handoff.add_argument("--run-dir", required=True, type=Path)
    delivery_handoff.add_argument("--from-session-id", required=True)
    delivery_handoff.add_argument("--to-session-id", required=True)
    delivery_handoff.add_argument("--expected-run-revision", required=True, type=int)
    delivery_handoff.add_argument(
        "--expected-ownership-generation", required=True, type=int
    )
    delivery_handoff.add_argument("--handed-off-at", required=True)
    delivery_handoff.add_argument(
        "--fault-point", choices=sorted(DELIVERY_FAULT_POINTS)
    )

    delivery_archive = commands.add_parser("delivery-archive")
    delivery_archive.add_argument("--run-dir", required=True, type=Path)
    delivery_archive.add_argument("--session-id", required=True)
    delivery_archive.add_argument("--expected-run-revision", required=True, type=int)
    delivery_archive.add_argument(
        "--expected-ownership-generation", required=True, type=int
    )
    delivery_archive.add_argument("--archived-at", required=True)
    delivery_archive.add_argument(
        "--fault-point", choices=sorted(DELIVERY_FAULT_POINTS)
    )

    delivery_reconcile = commands.add_parser("delivery-reconcile")
    delivery_reconcile.add_argument("--run-dir", required=True, type=Path)

    store = commands.add_parser("control-store-check")
    store.add_argument("--workspace-root", required=True, type=Path)

    store_backup = commands.add_parser("control-store-backup")
    store_backup.add_argument("--workspace-root", required=True, type=Path)
    store_backup.add_argument("--backup-dir", required=True, type=Path)
    store_backup.add_argument("--backup-id", required=True)
    store_backup.add_argument("--coordinator-session-id", required=True)
    store_backup.add_argument("--created-at", required=True)

    store_restore = commands.add_parser("control-store-restore")
    store_restore.add_argument("--workspace-root", required=True, type=Path)
    store_restore.add_argument("--backup-dir", required=True, type=Path)
    store_restore.add_argument("--backup-id", required=True)
    store_restore.add_argument("--coordinator-session-id", required=True)
    store_restore.add_argument("--restored-at", required=True)

    store_restore_resume = commands.add_parser("control-store-restore-resume")
    store_restore_resume.add_argument(
        "--workspace-root", required=True, type=Path
    )
    store_restore_resume.add_argument("--operation-id", required=True)
    store_restore_resume.add_argument("--resumed-at", required=True)

    store_recovery_status = commands.add_parser("control-store-recovery-status")
    store_recovery_status.add_argument("--workspace-root", required=True, type=Path)

    probe = commands.add_parser("bootstrap-probe")
    _add_bootstrap_probe_inputs(probe)

    init = commands.add_parser("init-run")
    init.add_argument("--workspace-root", required=True, type=Path)
    init.add_argument("--probe", required=True, type=Path)
    init.add_argument("--fixture", type=Path)
    init.add_argument("--control-store-root", type=Path)
    init.add_argument("--session-id")
    init.add_argument("--fault-point", choices=sorted(FAULT_POINTS))

    cutover_candidate_init = commands.add_parser("init-cutover-candidate")
    cutover_candidate_init.add_argument("--workspace-root", required=True, type=Path)
    cutover_candidate_init.add_argument(
        "--control-store-root", required=True, type=Path
    )
    cutover_candidate_init.add_argument("--probe", required=True, type=Path)
    cutover_candidate_init.add_argument("--session-id", required=True)
    cutover_candidate_init.add_argument(
        "--fault-point", choices=sorted(FAULT_POINTS | {"after_candidate_begin"})
    )

    cutover_candidate_reconcile = commands.add_parser(
        "platform-kernel-candidate-reconcile"
    )
    cutover_candidate_reconcile.add_argument(
        "--platform", required=True, choices=("bilibili", "youtube")
    )
    cutover_candidate_reconcile.add_argument(
        "--control-store-root", required=True, type=Path
    )
    cutover_candidate_reconcile.add_argument(
        "--workspace-root", required=True, type=Path
    )
    cutover_candidate_reconcile.add_argument(
        "--candidate-probe", required=True, type=Path
    )
    cutover_candidate_reconcile.add_argument(
        "--candidate-session-id", required=True
    )

    cutover_candidate_rebind = commands.add_parser(
        "platform-kernel-candidate-rebind"
    )
    cutover_candidate_rebind.add_argument(
        "--platform", required=True, choices=("bilibili", "youtube")
    )
    cutover_candidate_rebind.add_argument(
        "--control-store-root", required=True, type=Path
    )
    cutover_candidate_rebind.add_argument(
        "--candidate-run-dir", required=True, type=Path
    )
    cutover_candidate_rebind.add_argument("--implementation-commit", required=True)
    cutover_candidate_rebind.add_argument("--rebound-at", required=True)

    source_import = commands.add_parser("source-import")
    source_import.add_argument("--workspace-root", required=True, type=Path)
    source_import.add_argument("--probe", type=Path)
    source_import.add_argument("--fixture", type=Path)
    source_import.add_argument("--prior-run-dir", type=Path)
    source_import.add_argument("--task-start")
    source_import.add_argument("--request-id")
    source_import.add_argument("--fault-point", choices=sorted(FAULT_POINTS))

    source_acquire = commands.add_parser("source-acquire")
    source_acquire.add_argument("--run-dir", required=True, type=Path)
    source_acquire.add_argument("--cookie-file", required=True, type=Path)
    source_acquire.add_argument("--provider-recording", type=Path)
    source_acquire.add_argument("--whisper-transcript", type=Path)
    source_acquire.add_argument(
        "--fault-point", choices=sorted(SOURCE_ACQUIRE_FAULT_POINTS)
    )

    source_acquire_reconcile = commands.add_parser("source-acquire-reconcile")
    source_acquire_reconcile.add_argument("--run-dir", required=True, type=Path)

    source_blocker_resolve = commands.add_parser("source-blocker-resolve")
    source_blocker_resolve.add_argument("--run-dir", required=True, type=Path)
    source_blocker_resolve.add_argument(
        "--authentication-classification",
        required=True,
        choices=("cookie_accepted",),
    )
    source_blocker_resolve.add_argument(
        "--credential-evidence",
        required=True,
        type=Path,
    )
    source_blocker_resolve.add_argument(
        "--credential-evidence-sha256",
        required=True,
    )

    trace = commands.add_parser("trace-source-ready")
    _add_trace_inputs(trace)
    trace.add_argument("--fault-point", choices=sorted(FAULT_POINTS))

    reconcile = commands.add_parser("reconcile-run")
    reconcile.add_argument("--run-dir", type=Path)
    reconcile.add_argument("--workspace-root", type=Path)
    reconcile.add_argument("--run-id")

    authority = commands.add_parser("reconcile-authority")
    authority.add_argument("--workspace-root", required=True, type=Path)
    authority.add_argument("--kind", required=True)
    authority.add_argument("--id", required=True)

    task_prepare = commands.add_parser("task-prepare")
    task_prepare.add_argument("--run-dir", required=True, type=Path)
    task_prepare.add_argument("--logical-task-key", required=True)
    task_prepare.add_argument("--prepared-at")
    task_prepare.add_argument("--fault-point", choices=sorted(PREPARATION_FAULT_POINTS))

    task_claim = commands.add_parser("task-claim")
    task_claim.add_argument("--run-dir", required=True, type=Path)
    task_claim.add_argument("--task-id", required=True)
    task_claim.add_argument("--coordinator-session-id", required=True)
    task_claim.add_argument("--worker-id", required=True)
    task_claim.add_argument("--fault-point", choices=sorted(CLAIM_FAULT_POINTS))

    task_reclaim = commands.add_parser("task-reclaim")
    task_reclaim.add_argument("--run-dir", required=True, type=Path)
    task_reclaim.add_argument("--task-id", required=True)
    task_reclaim.add_argument("--expected-attempt-id", required=True)
    task_reclaim.add_argument("--expected-claim-generation", required=True, type=int)
    task_reclaim.add_argument("--coordinator-session-id", required=True)
    task_reclaim.add_argument("--worker-id", required=True)
    task_reclaim.add_argument("--reason", required=True)
    task_reclaim.add_argument("--fault-point", choices=sorted(RECLAIM_FAULT_POINTS))

    task_complete = commands.add_parser("task-complete")
    task_complete.add_argument("--run-dir", required=True, type=Path)
    task_complete.add_argument("--task-id", required=True)
    task_complete.add_argument("--attempt-id", required=True)
    task_complete.add_argument("--claim-generation", required=True, type=int)
    task_complete.add_argument("--fault-point", choices=sorted(COMPLETION_FAULT_POINTS))

    task_promote = commands.add_parser("task-promote")
    task_promote.add_argument("--run-dir", required=True, type=Path)
    task_promote.add_argument("--task-id", required=True)
    task_promote.add_argument("--attempt-id", required=True)
    task_promote.add_argument("--claim-generation", required=True, type=int)
    task_promote.add_argument("--fault-point", choices=sorted(PROMOTION_FAULT_POINTS))

    resource_status = commands.add_parser("resource-status")
    resource_status.add_argument("--workspace-root", required=True, type=Path)
    resource_status.add_argument("--task-id", required=True)
    resource_status.add_argument("--attempt-id", required=True)

    resource_scheduler_status = commands.add_parser("resource-scheduler-status")
    resource_scheduler_status.add_argument(
        "--workspace-root", required=True, type=Path
    )

    resource_capacity_status = commands.add_parser("resource-capacity-status")
    resource_capacity_status.add_argument(
        "--workspace-root", required=True, type=Path
    )

    resource_config_activate = commands.add_parser("resource-config-activate")
    resource_config_activate.add_argument(
        "--workspace-root", required=True, type=Path
    )
    resource_config_activate.add_argument(
        "--configuration", required=True, type=Path
    )

    resource_breaker_set = commands.add_parser("resource-breaker-set")
    resource_breaker_set.add_argument("--workspace-root", required=True, type=Path)
    resource_breaker_set.add_argument("--resource-class", required=True)
    resource_breaker_set.add_argument(
        "--state", required=True, choices=("open", "closed")
    )
    resource_breaker_set.add_argument("--reason", required=True)
    resource_breaker_set.add_argument(
        "--platform", choices=("bilibili", "youtube")
    )

    resource_breaker_status = commands.add_parser("resource-breaker-status")
    resource_breaker_status.add_argument(
        "--workspace-root", required=True, type=Path
    )

    resource_reconcile = commands.add_parser("resource-reconcile")
    resource_reconcile.add_argument("--workspace-root", required=True, type=Path)
    resource_reconcile.add_argument(
        "--current-coordinator-session-id", required=True
    )
    resource_reconcile.add_argument(
        "--lost-coordinator-session-id", action="append", default=[]
    )

    resource_resolve = commands.add_parser("resource-resolve")
    resource_resolve.add_argument("--workspace-root", required=True, type=Path)
    resource_resolve.add_argument("--lease-id", required=True)
    resource_resolve.add_argument("--attempt-id", required=True)
    resource_resolve.add_argument(
        "--expected-claim-generation", required=True, type=int
    )
    resource_resolve.add_argument(
        "--resolution-evidence", required=True, type=Path
    )

    capability = commands.add_parser("adapter-capability-check")
    capability.add_argument("--fixture", required=True, type=Path)
    capability.add_argument("--capability", required=True)

    source_live_smoke = commands.add_parser("source-live-smoke")
    source_live_smoke.add_argument("--spec", required=True, type=Path)
    source_live_smoke.add_argument("--credential-profile", required=True)
    source_live_smoke.add_argument("--work-root", required=True, type=Path)

    production_plan = commands.add_parser("production-plan")
    production_plan.add_argument("--run-dir", required=True, type=Path)
    production_plan.add_argument("--supersede-task-id")
    production_plan.add_argument("--expected-claim-generation", type=int)

    production_advance = commands.add_parser("production-advance")
    production_advance.add_argument("--run-dir", required=True, type=Path)
    production_advance.add_argument("--task-id", required=True)
    production_advance.add_argument("--attempt-id", required=True)
    production_advance.add_argument("--compile-runtime-policy", type=Path)
    production_advance.add_argument(
        "--fault-point", choices=sorted(PRODUCTION_FAULT_POINTS)
    )

    guarded_compile = commands.add_parser("guarded-compile")
    guarded_compile.add_argument("--run-dir", required=True, type=Path)
    guarded_compile.add_argument("--manifest", required=True, type=Path)
    guarded_compile.add_argument("--runtime-policy", required=True, type=Path)

    batch_plan = commands.add_parser("batch-plan")
    batch_plan.add_argument("--workspace-root", type=Path)
    batch_plan.add_argument("--control-store-root", required=True, type=Path)
    batch_plan.add_argument("--platform", required=True, choices=("bilibili", "youtube"))
    batch_plan.add_argument("--source-url")
    batch_plan.add_argument("--task-start", required=True)
    batch_plan.add_argument("--request-id", required=True)
    batch_plan.add_argument("--selection")
    batch_plan.add_argument("--url-set")

    batch_run = commands.add_parser("batch-run")
    batch_run.add_argument("--batch-id", required=True)
    batch_run.add_argument("--control-store-root", required=True, type=Path)
    batch_run.add_argument("--session-id", required=True)
    batch_run.add_argument("--run-task-start")
    batch_run.add_argument(
        "--fault-point",
        choices=sorted(BATCH_RUN_FAULT_POINTS),
    )

    batch_recover = commands.add_parser("batch-recover")
    batch_recover.add_argument("--batch-id", required=True)
    batch_recover.add_argument("--control-store-root", required=True, type=Path)

    batch_rebuild = commands.add_parser("batch-rebuild-projections")
    batch_rebuild.add_argument("--batch-id", required=True)
    batch_rebuild.add_argument("--control-store-root", required=True, type=Path)

    batch_status = commands.add_parser("batch-status")
    batch_status.add_argument("--batch-id", required=True)
    batch_status.add_argument("--control-store-root", required=True, type=Path)
    return parser


def _add_trace_inputs(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workspace-root", required=True, type=Path)
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--task-start", required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--title-override")


def _add_bootstrap_probe_inputs(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workspace-root", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--fixture", type=Path)
    mode.add_argument("--platform", choices=("bilibili", "youtube"))
    parser.add_argument("--source-url")
    parser.add_argument("--cookie-file", type=Path)
    parser.add_argument("--original-title")
    parser.add_argument("--explicit-item-selector")
    parser.add_argument("--provider-recording", type=Path)
    parser.add_argument("--task-start", required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--title-override")


def _probe_from_path(path: Path, contracts: ContractRegistry) -> BootstrapProbeResult:
    value = read_json(path)
    contracts.validate("bootstrap-record", value)
    return BootstrapProbeResult(
        run_id=value["run_id"],
        request_id=value["request_id"],
        record_path=path.resolve(),
        original_title=value["original_title"],
        task_start=value["task_start"],
        canonical_item_id=value["canonical_item_id"],
        fixture_manifest_sha256=value["fixture_manifest_sha256"],
    )


def _production_probe_from_path(
    path: Path, contracts: ContractRegistry
) -> ProductionBootstrapResult:
    value = read_json(path)
    contracts.validate("bootstrap-record", value)
    adapter = value["adapter"]
    return ProductionBootstrapResult(
        run_id=value["run_id"],
        request_id=value["request_id"],
        record_path=path.resolve(),
        original_title=value["original_title"],
        task_start=value["task_start"],
        canonical_platform=adapter["canonical_platform"],
        canonical_item_id=value["canonical_item_id"],
        source_identity=value["source_identity"],
    )


def _parse_batch_selection(raw: str | None) -> list | None:
    if raw is None or not raw.strip():
        return None
    values: list = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            raise CliUsageError("batch-plan --selection contains an empty entry")
        if token.isdigit():
            values.append(int(token))
        else:
            values.append(token)
    return values


def _load_batch_record(
    *,
    batch_id: str,
    control_store_root: Path,
    contracts: ContractRegistry,
) -> tuple[Path, dict[str, Any]]:
    control_root = Path(control_store_root).resolve()
    if not ControlStore.identity_evidence_exists(control_root):
        raise KernelConflict("batch record not found", data={"batch_id": batch_id})
    store = ControlStore(control_root, contracts)
    record = store.get_batch_record(batch_id)
    if record is None:
        raise KernelConflict("batch record not found", data={"batch_id": batch_id})
    contracts.validate("batch-record", record)
    return control_root, record


def _ok(command: str, classification: str, data: dict[str, Any], evidence_path: str | None = None) -> dict:
    return {
        "schema_name": "workflow-result",
        "schema_version": "1.0.0",
        "kernel_version": "2.0.0",
        "command": command,
        "status": "ok",
        "classification": classification,
        "evidence_path": evidence_path,
        "data": data,
    }


def _error(command: str, error: KernelError) -> dict:
    data = {"message": str(error), **error.data}
    return {
        "schema_name": "workflow-result",
        "schema_version": "1.0.0",
        "kernel_version": "2.0.0",
        "command": command,
        "status": "error",
        "classification": error.classification,
        "evidence_path": data.get("evidence_path"),
        "data": data,
    }


def _platform_cutover_presence(db_path: Path) -> set[str]:
    """Return platforms with any committed platform-kernel control state."""
    import sqlite3

    platforms: set[str] = set()
    try:
        connection = sqlite3.connect(
            db_path, timeout=0.05, isolation_level=None
        )
        try:
            for table in (
                "platform_cutover_authority",
                "platform_cutover_candidates",
                "platform_cutover_intents",
            ):
                try:
                    rows = connection.execute(
                        f"SELECT DISTINCT platform FROM {table}"
                    ).fetchall()
                except sqlite3.DatabaseError:
                    continue
                platforms.update(row[0] for row in rows)
        finally:
            connection.close()
    except (OSError, sqlite3.DatabaseError):
        return platforms
    return platforms


def _resource_status_data(status: Any) -> dict[str, Any]:
    return {
        "queue_id": status.queue_id,
        "task_id": status.task_id,
        "attempt_id": status.attempt_id,
        "claim_generation": status.claim_generation,
        "queue_state": status.queue_state,
        "required_resources": list(status.required_resources),
        "configuration_id": status.configuration_id,
        "configuration_version": status.configuration_version,
        "configuration_sha256": status.configuration_sha256,
        "lease_id": status.lease_id,
        "lease_state": status.lease_state,
        "bypass_count": status.bypass_count,
        "reservation_state": status.reservation_state,
        "reservation_seq": status.reservation_seq,
        "launch_authorization_state": status.launch_authorization_state,
        "launch_required_resources": (
            None
            if status.launch_required_resources is None
            else list(status.launch_required_resources)
        ),
        "launch_eligible": status.launch_eligible,
    }


def _execute(args: argparse.Namespace, project_root: Path) -> dict:
    command = args.command
    if command == "legacy-acceptance-adopt":
        result = LegacyAcceptanceProvider(project_root).adopt(
            video_output_dir=args.video_output_dir, final_pdf=args.final_pdf,
            main_tex=args.main_tex, allowed_artifacts_manifest=args.allowed_artifacts_manifest,
            compile_report=args.compile_report, criteria=args.criteria,
            dimension_map=args.dimension_map, rendered_pages_manifest=args.rendered_pages_manifest,
            quality_inputs_manifest=args.quality_inputs_manifest,
            control_store_root=args.control_store_root, adopted_at=args.adopted_at, output=args.output,
        )
        return _ok(command, "legacy_acceptance_adopted", result, result["input_set_path"])
    if command == "global-gate-activate":
        result = GlobalGatePublisher().activate(
            control_store_root=args.control_store_root,
            exit_evidence=args.exit_evidence,
            activated_at=args.activated_at,
            fault_point=args.fault_point,
        )
        return _ok(command, "global_gate_activated", result, result["authority_path"])
    if command == "global-gate-reconcile":
        result = GlobalGatePublisher().reconcile(control_store_root=args.control_store_root)
        return _ok(command, "global_gate_reconciled", result, result["authority_path"])
    if command == "batch-activate":
        result = BatchCutoverPublisher().activate(
            control_store_root=args.control_store_root,
            exit_evidence=args.exit_evidence,
            activated_at=args.activated_at,
            fault_point=args.fault_point,
        )
        return _ok(
            command,
            "batch_authority_activated",
            result,
            result["authority_path"],
        )
    if command == "batch-reconcile":
        result = BatchCutoverPublisher().reconcile(
            control_store_root=args.control_store_root
        )
        return _ok(
            command,
            "batch_authority_reconciled",
            result,
            result["authority_path"],
        )
    if command == "batch-authority-check":
        result = BatchCutoverPublisher().require_current(
            control_store_root=args.control_store_root
        )
        return _ok(
            command,
            "batch_authority_current",
            result,
            result["authority_path"],
        )
    if command == "workflow-policy-check":
        result = GlobalGatePublisher().check_policy(control_store_root=args.control_store_root)
        platform_db = args.control_store_root.resolve() / "platform-kernel-control.sqlite3"
        platform_statuses = {"bilibili": "active_legacy", "youtube": "active_legacy"}
        if platform_db.is_file():
            publisher = BilibiliPlatformCutoverPublisher()
            with_control_presence = _platform_cutover_presence(platform_db)
            for platform in ("bilibili", "youtube"):
                try:
                    platform_policy = publisher.check_policy(
                        platform=platform,
                        control_store_root=args.control_store_root,
                    )
                    platform_statuses.update(platform_policy["platform_statuses"])
                except KernelError:
                    if platform in with_control_presence:
                        raise
                    # No committed authority, candidate, or intent for this
                    # platform: keep its legacy fallback status.
                    continue
        result["platform_statuses"] = platform_statuses
        return _ok(command, "workflow_policy_current", result, result["global_gate_authority"]["path"])
    if command == "platform-kernel-prepare":
        result = BilibiliPlatformCutoverPublisher().prepare_candidate(
            platform=args.platform,
            control_store_root=args.control_store_root,
            implementation_commit=args.implementation_commit,
            candidate_probe=args.candidate_probe,
            candidate_session_id=args.candidate_session_id,
            prepared_at=args.prepared_at,
        )
        return _ok(
            command,
            "platform_kernel_candidate_prepared",
            result,
        )
    if command == "platform-kernel-candidate-activate":
        result = BilibiliPlatformCutoverPublisher().activate_candidate(
            platform=args.platform,
            control_store_root=args.control_store_root,
            candidate_run_dir=args.candidate_run_dir,
            activated_at=args.activated_at,
        )
        return _ok(command, "platform_kernel_candidate_activated", result)
    if command == "platform-kernel-activate":
        result = BilibiliPlatformCutoverPublisher().activate(
            platform=args.platform,
            control_store_root=args.control_store_root,
            exit_evidence=args.exit_evidence,
            activated_at=args.activated_at,
            fault_point=args.fault_point,
        )
        return _ok(
            command,
            "platform_kernel_activated",
            result,
            result["authority_path"],
        )
    if command == "platform-kernel-reconcile":
        result = BilibiliPlatformCutoverPublisher().reconcile(
            platform=args.platform,
            control_store_root=args.control_store_root,
        )
        return _ok(
            command,
            "platform_kernel_reconciled",
            result,
            result["authority_path"],
        )
    if command == "youtube-platform-authority-refresh":
        result = BilibiliPlatformCutoverPublisher().refresh_authority(
            platform="youtube",
            control_store_root=args.control_store_root,
            exit_evidence=args.exit_evidence,
            expected_generation=args.expected_generation,
            refreshed_at=args.refreshed_at,
            fault_point=args.fault_point,
        )
        return _ok(
            command,
            "platform_kernel_authority_refreshed",
            result,
            result["authority_path"],
        )
    if command == "delivery-transition":
        result = DeliveryLifecycleProvider(project_root).transition(
            run_dir=args.run_dir,
            from_stage=args.from_stage,
            to_stage=args.to_stage,
            session_id=args.session_id,
            expected_run_revision=args.expected_run_revision,
            expected_ownership_generation=args.expected_ownership_generation,
            evidence_path=args.evidence,
            transitioned_at=args.transitioned_at,
            fault_point=args.fault_point,
        )
        return _ok(
            command,
            "delivery_lifecycle_transitioned",
            result,
            result["run_record_path"],
        )
    if command == "delivery-acceptance-bind":
        result = DeliveryAcceptanceBindingProvider(project_root).bind(
            run_dir=args.run_dir,
            session_id=args.session_id,
            acceptance_report=args.acceptance_report,
            expected_run_revision=args.expected_run_revision,
            expected_ownership_generation=args.expected_ownership_generation,
            bound_at=args.bound_at,
        )
        return _ok(
            command,
            "delivery_acceptance_bound",
            result,
            result["run_record_path"],
        )
    if command == "delivery-handoff":
        result = DeliveryLifecycleProvider(project_root).handoff(
            run_dir=args.run_dir,
            from_session_id=args.from_session_id,
            to_session_id=args.to_session_id,
            expected_run_revision=args.expected_run_revision,
            expected_ownership_generation=args.expected_ownership_generation,
            handed_off_at=args.handed_off_at,
            fault_point=args.fault_point,
        )
        return _ok(
            command,
            "delivery_ownership_handed_off",
            result,
            result["run_record_path"],
        )
    if command == "delivery-archive":
        result = DeliveryLifecycleProvider(project_root).archive(
            run_dir=args.run_dir,
            session_id=args.session_id,
            expected_run_revision=args.expected_run_revision,
            expected_ownership_generation=args.expected_ownership_generation,
            archived_at=args.archived_at,
            fault_point=args.fault_point,
        )
        return _ok(
            command,
            "delivery_target_archived",
            result,
            result["archive_path"],
        )
    if command == "delivery-reconcile":
        result = DeliveryLifecycleProvider(project_root).reconcile(
            run_dir=args.run_dir,
        )
        return _ok(
            command,
            "delivery_lifecycle_reconciled",
            result,
            result["run_record_path"],
        )
    if command == "acceptance-final-authority-publish":
        result = AcceptanceV2Provider(project_root).publish_final_authority(input_binding_path=args.input_binding)
        return _ok(command, "acceptance_v2_final_authority_published", result)
    if command == "delivery-final-evidence-prepare":
        result = FinalDeliveryEvidenceProvider(project_root).prepare(
            run_dir=args.run_dir,
            final_pdf=args.final_pdf,
            main_tex=args.main_tex,
            final_compile_report=args.final_compile_report,
            final_compile_manifest=args.final_compile_manifest,
            precompile_quality_report=args.precompile_quality_report,
            precompile_text_seal=args.precompile_text_seal,
            final_artifact_seal=args.final_artifact_seal,
            rendered_text_reconciliation=args.rendered_text_reconciliation,
            render_evidence_manifest=args.render_evidence_manifest,
            rendered_text_inventory=args.rendered_text_inventory,
            text_origin_manifest=args.text_origin_manifest,
            global_gate_authority=args.global_gate_authority,
            allowed_manifest=args.allowed_manifest,
            prepared_at=args.prepared_at,
            fault_point=args.fault_point,
        )
        return _ok(
            command,
            "delivery_final_evidence_prepared",
            result,
            result["input_binding_path"],
        )
    if command == "acceptance-prepare":
        result = AcceptanceV2Provider(project_root).prepare(
            workspace_root=args.workspace_root,
            input_binding_path=args.input_binding,
            attempt_number=args.attempt_number,
            prepared_at=args.prepared_at,
            coordinator_session=args.coordinator_session,
            fault_point=args.fault_point,
        )
        return _ok(command, "acceptance_v2_prepared", result, result["skeleton_path"])
    if command == "acceptance-patch-commit":
        result = AcceptanceV2Provider(project_root).commit_patch(
            workspace_root=args.workspace_root,
            dimension=args.dimension,
            patch_path=args.patch,
            committed_at=args.committed_at,
            fault_point=args.fault_point,
        )
        return _ok(command, "acceptance_v2_patch_committed", result)
    if command == "acceptance-materialize":
        result = AcceptanceV2Provider(project_root).materialize(
            workspace_root=args.workspace_root,
            provider_id=args.provider_id,
            provider_version=args.provider_version,
            materialized_at=args.materialized_at,
            fault_point=args.fault_point,
        )
        return _ok(command, "acceptance_v2_materialized", result, result["report_path"])
    if command == "acceptance-repair-prepare":
        result = AcceptanceV2Provider(project_root).prepare_repair(
            workspace_root=args.workspace_root,
            input_binding_path=args.input_binding,
            prepared_at=args.prepared_at,
            coordinator_session=args.coordinator_session,
        )
        return _ok(command, "acceptance_v2_repair_prepared", result, result["skeleton_path"])
    if command == "acceptance-reconcile":
        result = AcceptanceV2Provider(project_root).reconcile(
            workspace_root=args.workspace_root,
        )
        return _ok(command, "acceptance_v2_reconciled", result)
    if command == "acceptance-guard-eligibility":
        result = AcceptanceV2Provider(project_root).guard_eligibility(
            workspace_root=args.workspace_root,
        )
        classification = "acceptance_v2_guard_eligible" if result["eligible"] else "acceptance_v2_guard_blocked"
        return _ok(command, classification, result, str(args.workspace_root.resolve() / "acceptance_report.json"))
    if command == "delivery-quality-final-compile":
        result = GuardedFinalCompileProvider(project_root).compile(
            precompile_workspace_root=args.precompile_workspace_root,
            compile_manifest_path=args.compile_manifest,
            text_origin_plan_path=args.text_origin_plan,
            compiler_adapter_path=args.compiler_adapter,
            runtime_policy_path=args.runtime_policy,
            workspace_root=args.workspace_root,
            compiled_at=args.compiled_at,
        )
        return _ok(
            command,
            "guarded_final_compile_complete",
            result,
            result["final_compile_report_path"],
        )
    if command == "delivery-quality-rendered-text-reconcile":
        result = RenderedTextReconciliationProvider(project_root).reconcile(
            precompile_workspace_root=args.precompile_workspace_root,
            compile_manifest_path=args.compile_manifest,
            compile_report_path=args.compile_report,
            final_artifact_seal_path=args.final_artifact_seal,
            final_pdf_path=args.final_pdf,
            render_evidence_manifest_path=args.render_evidence_manifest,
            rendered_text_inventory_path=args.rendered_text_inventory,
            text_origin_manifest_path=args.text_origin_manifest,
            output_path=args.output,
            reconciled_at=args.reconciled_at,
        )
        return _ok(
            command,
            "rendered_text_reconciliation_passed",
            result,
            result["report_path"],
        )
    if command == "delivery-quality-precompile-prepare":
        result = PrecompileQualityProvider(project_root).prepare(
            workspace_root=args.workspace_root,
            inventory_path=args.inventory,
            artifact_generations_path=args.artifact_generations,
            semantic_dependencies_path=args.semantic_dependencies,
            prepared_at=args.prepared_at,
            fault_point=args.fault_point,
        )
        return _ok(
            command,
            "precompile_review_tasks_prepared",
            result,
            str(args.workspace_root.resolve() / "reviewers"),
        )
    if command == "delivery-quality-precompile-repair-prepare":
        result = PrecompileQualityProvider(project_root).prepare_repair(
            predecessor_workspace_root=args.predecessor_workspace_root,
            workspace_root=args.workspace_root,
            inventory_path=args.inventory,
            artifact_generations_path=args.artifact_generations,
            semantic_dependencies_path=args.semantic_dependencies,
            repair_attempt_number=args.repair_attempt_number,
            prepared_at=args.prepared_at,
        )
        return _ok(
            command,
            "precompile_repair_attempt_prepared",
            result,
            result["repair_attempt_path"],
        )
    if command == "delivery-quality-precompile-patch-commit":
        result = PrecompileQualityProvider(project_root).commit_patch(
            workspace_root=args.workspace_root,
            owner=args.owner,
            patch_path=args.patch,
            committed_at=args.committed_at,
            fault_point=args.fault_point,
        )
        return _ok(
            command,
            "precompile_judgment_patch_committed",
            result,
            str(
                args.workspace_root.resolve()
                / "reviewers"
                / args.owner
                / "commit"
                / "patch-commit.json"
            ),
        )
    if command == "delivery-quality-precompile-materialize":
        result = PrecompileQualityProvider(project_root).materialize(
            workspace_root=args.workspace_root,
            provider_id=args.provider_id,
            provider_version=args.provider_version,
            materialized_at=args.materialized_at,
            fault_point=args.fault_point,
        )
        classification = (
            "precompile_quality_report_passed"
            if result["overall_decision"] == "pass"
            else "precompile_quality_report_failed"
        )
        return _ok(
            command,
            classification,
            result,
            result["report_path"],
        )
    if command == "delivery-quality-seal":
        result = PrecompileQualityProvider(project_root).seal(
            workspace_root=args.workspace_root,
            sealed_at=args.sealed_at,
        )
        classification = (
            "precompile_text_successor_seal_created"
            if result["decision_origin"] == "reused_after_text_equivalence"
            else "precompile_text_seal_created"
        )
        return _ok(
            command,
            classification,
            result,
            result["seal_path"],
        )
    if command == "delivery-quality-text-equivalence":
        result = PrecompileQualityProvider(project_root).prove_text_equivalence(
            workspace_root=args.workspace_root,
            successor_inventory_path=args.successor_inventory,
            successor_artifact_generations_path=(
                args.successor_artifact_generations
            ),
            mutation_class=args.mutation_class,
            proved_at=args.proved_at,
        )
        return _ok(
            command,
            "text_equivalence_proved",
            result,
            result["report_path"],
        )
    if command == "guarded-compile":
        run_dir = args.run_dir.resolve()
        manifest_path = args.manifest.resolve()
        policy = read_json(args.runtime_policy.resolve())
        contracts = ContractRegistry(project_root)
        contracts.validate("compile-runtime-policy", policy)
        contracts.validate("compile-manifest", read_json(manifest_path))
        VideoWorkflowKernel(run_dir.parent).require_current_validated_source_package(
            run_dir
        )
        result = GuardedCompileProvider(run_dir).compile(manifest_path, policy)
        return _ok(
            command,
            "diagnostic_compile_ready",
            result["report"],
            str(result["report_path"]),
        )
    if command == "production-plan":
        run_dir = args.run_dir.resolve()
        result = VideoWorkflowKernel(run_dir.parent).production_plan(
            run_dir,
            supersede_task_id=args.supersede_task_id,
            expected_claim_generation=args.expected_claim_generation,
        )
        return _ok(
            command,
            str(result["classification"]),
            result,
            str(run_dir / "workflow/production-state.json"),
        )
    if command == "production-advance":
        run_dir = args.run_dir.resolve()
        policy = (
            None
            if args.compile_runtime_policy is None
            else read_json(args.compile_runtime_policy.resolve())
        )
        result = VideoWorkflowKernel(run_dir.parent).production_advance(
            run_dir,
            args.task_id,
            args.attempt_id,
            compile_runtime_policy=policy,
            fault_point=args.fault_point,
        )
        return _ok(
            command,
            str(result["classification"]),
            result,
            result.get("compile_report_path")
            or str(run_dir / "workflow/production-state.json"),
        )
    if command == "control-store-restore":
        result = ControlStoreRecovery(
            args.workspace_root,
            project_root=project_root,
        ).restore_selected(
            args.backup_dir,
            backup_id=args.backup_id,
            coordinator_session_id=args.coordinator_session_id,
            restored_at=args.restored_at,
        )
        if result["classification"] != "control_store_restore_complete":
            evidence_path = result.get("orphan_report_path") or result["report_path"]
            raise ControlStoreUnavailable(
                "Control Store restore completed with unresolved global authority",
                data={**result, "evidence_path": str(evidence_path)},
            )
        return _ok(
            command,
            str(result["classification"]),
            result,
            str(result["report_path"]),
        )
    if command == "control-store-restore-resume":
        result = ControlStoreRecovery(
            args.workspace_root,
            project_root=project_root,
        ).resume_restore(
            operation_id=args.operation_id,
            resumed_at=args.resumed_at,
        )
        if result["classification"] != "control_store_restore_complete":
            evidence_path = result.get("orphan_report_path") or result["report_path"]
            raise ControlStoreUnavailable(
                "Control Store restore resume completed with unresolved global authority",
                data={**result, "evidence_path": str(evidence_path)},
            )
        return _ok(
            command,
            str(result["classification"]),
            result,
            str(result["report_path"]),
        )
    if command == "control-store-recovery-status":
        result = ControlStoreRecovery(
            args.workspace_root,
            project_root=project_root,
        ).diagnostic_status()
        return _ok(
            command,
            str(result["classification"]),
            result,
            result.get("recovery_report_path") or result.get("sentinel_path"),
        )
    if command == "contracts-check":
        registry = ContractRegistry(project_root, args.registry)
        return _ok(command, "contracts_valid", registry.check(), str(registry.registry_path))
    if command == "delivery-quality-contracts-check":
        registry = DeliveryQualityRegistry(project_root, args.registry)
        return _ok(
            command,
            "delivery_quality_contracts_valid",
            registry.check(args.mechanical_fixture),
            str(registry.registry_path),
        )
    if command == "delivery-quality-conformance":
        registry = DeliveryQualityRegistry(project_root)
        output_path = None if str(args.output) == "-" else args.output
        report = registry.conformance(
            reviewer_adapter_path=args.reviewer_adapter,
            output_path=output_path,
            implementation_commit=args.implementation_commit,
            track=args.track,
            adapter_id=args.adapter_id,
        )
        return _ok(
            command,
            "delivery_quality_conformance_passed",
            {
                "report_id": report["report_id"],
                "semantic_case_count": len(report["semantic_results"]),
                "semantic_attempt_count": sum(
                    len(item["attempts"]) for item in report["semantic_results"]
                ),
                "mechanical_fixture_count": len(report["mechanical_results"]),
                "overall_decision": report["overall_decision"],
                "authority": report["authority"],
                "activation_status": report["implementation"][
                    "activation_status"
                ],
                **({"report": report} if output_path is None else {}),
            },
            str(output_path.resolve()) if output_path is not None else None,
        )
    if command == "control-store-check":
        contracts = ContractRegistry(project_root)
        contracts.check()
        health = ControlStore(args.workspace_root, contracts).check()
        return _ok(
            command,
            "control_store_healthy",
            {
                "path": str(health.path),
                "schema_version": health.schema_version,
                "pragmas": health.pragmas,
                "quick_check": health.quick_check,
                "lock_contention_checked": health.lock_contention_checked,
                "atomic_replace_checked": health.atomic_replace_checked,
            },
            str(health.path),
        )
    if command == "control-store-backup":
        kernel = VideoWorkflowKernel(args.workspace_root)
        result = kernel.backup_control_store(
            args.backup_dir,
            backup_id=args.backup_id,
            coordinator_session_id=args.coordinator_session_id,
            created_at=args.created_at,
        )
        return _ok(
            command,
            str(result["classification"]),
            result,
            str(result["manifest_path"]),
        )
    if command == "bootstrap-probe":
        kernel = VideoWorkflowKernel(args.workspace_root)
        if args.fixture is not None:
            if any(
                value is not None
                for value in (
                    args.source_url,
                    args.cookie_file,
                    args.original_title,
                    args.explicit_item_selector,
                    args.provider_recording,
                )
            ):
                raise CliUsageError(
                    "fixture Bootstrap cannot accept production source arguments"
                )
            result = kernel.bootstrap_probe(
                fixture=args.fixture,
                task_start=args.task_start,
                request_id=args.request_id,
                title_override=args.title_override,
            )
            data = {"run_id": result.run_id, "probe_record": str(result.record_path)}
        else:
            if args.title_override is not None:
                raise CliUsageError(
                    "production Bootstrap cannot accept --title-override"
                )
            probe_kwargs = {
                "kernel": kernel,
                "workspace_root": args.workspace_root,
                "source_url": args.source_url,
                "cookie_file": args.cookie_file,
                "original_title": args.original_title,
                "task_start": args.task_start,
                "request_id": args.request_id,
                "explicit_item_selector": args.explicit_item_selector,
                "provider_recording": args.provider_recording,
            }
            if args.platform == "youtube":
                result = bootstrap_youtube_production_probe(**probe_kwargs)
            else:
                result = bootstrap_bilibili_production_probe(**probe_kwargs)
            data = {
                "run_id": result.run_id,
                "probe_record": str(result.record_path),
                "canonical_item_id": result.canonical_item_id,
                "source_identity": result.source_identity,
                "original_title": result.original_title,
            }
        return _ok(
            command,
            "probe_complete",
            data,
            str(result.record_path),
        )
    if command == "source-import" and args.prior_run_dir is not None:
        if (
            args.probe is not None
            or args.fixture is not None
            or args.fault_point is not None
            or args.task_start is None
            or args.request_id is None
        ):
            raise CliUsageError(
                "production source-import requires --prior-run-dir, --task-start, "
                "and --request-id without fixture arguments"
            )
        kernel = VideoWorkflowKernel(args.workspace_root)
        result = kernel.import_verified_source(
            prior_run_dir=args.prior_run_dir,
            task_start=args.task_start,
            request_id=args.request_id,
        )
        record = read_json(result.run_dir / "workflow/run.json")
        manifest = read_json(result.manifest_path)
        return _ok(
            command,
            "verified_source_imported",
            {
                "run_id": record["run_id"],
                "run_dir": str(result.run_dir),
                "source_identity": result.source_identity,
                "source_version": result.source_version,
                "source_manifest_sha256": result.manifest_sha256,
                "prior_run_id": manifest["provenance"]["prior_run_id"],
                "prior_source_manifest_sha256": manifest["provenance"][
                    "prior_source_manifest_sha256"
                ],
                "checkpoint": "source_ready",
                "checkpoint_status": record["checkpoints"]["source_ready"][
                    "status"
                ],
            },
            str(result.manifest_path),
        )
    if command == "source-acquire":
        try:
            result = acquire_bilibili_source_for_run(
                run_dir=args.run_dir,
                cookie_file=args.cookie_file,
                provider_recording=args.provider_recording,
                whisper_transcript=args.whisper_transcript,
                fault_point=args.fault_point,
            )
        except PlatformAdapterError as error:
            blocker = error.data.get("source_blocker")
            if error.blocker_kind != "user_input" or not isinstance(blocker, dict):
                raise
            run_dir = args.run_dir.resolve()
            return _ok(
                command,
                "user_input_required",
                {
                    "run_dir": str(run_dir),
                    "source_blocker": blocker,
                    "authentication_classification": error.data.get(
                        "authentication_classification"
                    ),
                },
                str(run_dir / "workflow" / "run.json"),
            )
        return _ok(
            command,
            (
                "source_already_ready"
                if result.get("idempotent") is True
                else "source_acquired"
            ),
            result,
            str(result["source_manifest"]),
        )
    if command == "source-acquire-reconcile":
        result = reconcile_bilibili_source_acquire(run_dir=args.run_dir)
        return _ok(
            command,
            "source_acquire_reconciled",
            result,
            str(args.run_dir.resolve() / "workflow" / "run.json"),
        )
    if command == "init-cutover-candidate":
        kernel = VideoWorkflowKernel(args.workspace_root)
        kernel.control_store = ControlStore.initialize(
            args.workspace_root, kernel.contracts
        )
        probe = _production_probe_from_path(args.probe, kernel.contracts)
        if probe.canonical_platform not in {"bilibili", "youtube"}:
            raise CliUsageError(
                "cutover candidate init is active only for Bilibili or YouTube"
            )
        publisher = BilibiliPlatformCutoverPublisher()
        candidate = publisher.begin_candidate_initialization(
            platform=probe.canonical_platform,
            control_store_root=args.control_store_root,
            candidate_probe=args.probe,
            candidate_session_id=args.session_id,
            workspace_root=args.workspace_root,
        )
        if args.fault_point == "after_candidate_begin":
            raise InitializationFault("after_candidate_begin")
        result = kernel.initialize_production_source(
            probe,
            session_id=args.session_id,
            global_gate_binding=candidate["global_gate_binding"],
            fault_point=args.fault_point,
        )
        publisher.record_candidate_initialized(
            platform=probe.canonical_platform,
            control_store_root=args.control_store_root,
            candidate_run_dir=result.run_dir,
        )
        return _ok(
            command,
            "cutover_candidate_initialized",
            {
                "run_id": result.run_id,
                "run_dir": str(result.run_dir),
                "platform": probe.canonical_platform,
                "stage": "generating",
                "session_id": args.session_id,
            },
            str(result.run_dir / "workflow/run.json"),
        )
    if command == "platform-kernel-candidate-reconcile":
        publisher = BilibiliPlatformCutoverPublisher()
        candidate = publisher.require_prepared_candidate(
            platform=args.platform,
            control_store_root=args.control_store_root,
            candidate_probe=args.candidate_probe,
            candidate_session_id=args.candidate_session_id,
        )
        if candidate.get("state") != "INITIALIZING":
            raise KernelConflict(
                "Bilibili cutover candidate has no interrupted initialization",
                data={
                    "first_failing_gate": "platform_kernel_candidate",
                    "error_code": "bilibili_candidate_initialization_not_reconcilable",
                },
            )
        if Path(str(candidate.get("workspace_root", ""))).resolve() != args.workspace_root.resolve():
            raise KernelConflict(
                "Bilibili cutover candidate workspace binding changed",
                data={
                    "first_failing_gate": "platform_kernel_candidate",
                    "error_code": "bilibili_cutover_candidate_binding_mismatch",
                },
            )
        kernel = VideoWorkflowKernel(args.workspace_root)
        kernel.control_store = ControlStore.initialize(
            args.workspace_root, kernel.contracts
        )
        intent = kernel.control_store.intent_for_run(candidate["candidate_run_id"])
        binding = kernel.control_store.binding_for_run(candidate["candidate_run_id"])
        if intent is None and binding is None:
            candidate_outputs = []
            for marker in args.workspace_root.rglob("*.json"):
                if marker.name not in {"run.json", "prepared-run.json"}:
                    continue
                try:
                    if read_json(marker).get("run_id") == candidate["candidate_run_id"]:
                        candidate_outputs.append(marker)
                except (OSError, json.JSONDecodeError, TypeError, ValueError):
                    candidate_outputs.append(marker)
            if candidate_outputs:
                raise KernelConflict(
                    "Bilibili candidate has unbound initialization output",
                    data={
                        "first_failing_gate": "platform_kernel_candidate",
                        "error_code": "bilibili_candidate_initialization_state_ambiguous",
                    },
                )
            publisher.rollback_unstarted_candidate_initialization(
                platform=args.platform,
                control_store_root=args.control_store_root,
                candidate_run_id=candidate["candidate_run_id"],
                workspace_root=args.workspace_root,
            )
            return _ok(
                command,
                "candidate_initialization_rolled_back",
                {
                    "run_id": candidate["candidate_run_id"],
                    "platform": args.platform,
                    "state": "PREPARED",
                },
                None,
            )
        if intent is None or binding is None:
            raise KernelConflict(
                "Bilibili candidate initialization state is contradictory",
                data={
                    "first_failing_gate": "platform_kernel_candidate",
                    "error_code": "bilibili_candidate_initialization_state_ambiguous",
                },
            )
        result = kernel.reconcile_initialization(candidate["candidate_run_id"])
        if result.outcome == "old_state_complete":
            reconciled_intent = kernel.control_store.intent_for_run(
                candidate["candidate_run_id"]
            )
            reconciled_binding = kernel.control_store.binding_for_run(
                candidate["candidate_run_id"]
            )
            if (
                reconciled_intent is None
                or reconciled_intent["state"] != "ABORTED"
                or reconciled_binding is not None
                or result.run_dir.exists()
            ):
                raise KernelConflict(
                    "Bilibili candidate aborted initialization is contradictory",
                    data={
                        "first_failing_gate": "platform_kernel_candidate",
                        "error_code": "bilibili_candidate_initialization_state_ambiguous",
                    },
                )
            publisher.rollback_unstarted_candidate_initialization(
                platform=args.platform,
                control_store_root=args.control_store_root,
                candidate_run_id=candidate["candidate_run_id"],
                workspace_root=args.workspace_root,
            )
            return _ok(
                command,
                "candidate_initialization_rolled_back",
                {
                    "run_id": candidate["candidate_run_id"],
                    "platform": args.platform,
                    "state": "PREPARED",
                    "kernel_initialization_state": "ABORTED",
                },
                None,
            )
        publisher.record_candidate_initialized(
            platform=args.platform,
            control_store_root=args.control_store_root,
            candidate_run_dir=result.run_dir,
        )
        return _ok(
            command,
            "candidate_initialization_reconciled",
            {
                "run_id": result.run_id,
                "run_dir": str(result.run_dir),
                "platform": args.platform,
                "reconcile_classification": result.outcome,
            },
            str(result.run_dir / "workflow/run.json"),
        )
    if command == "platform-kernel-candidate-rebind":
        result = BilibiliPlatformCutoverPublisher().rebind_candidate_implementation(
            platform=args.platform,
            control_store_root=args.control_store_root,
            candidate_run_dir=args.candidate_run_dir,
            implementation_commit=args.implementation_commit,
            rebound_at=args.rebound_at,
        )
        return _ok(
            command,
            "platform_kernel_candidate_rebound",
            result,
            result["run_record_path"],
        )
    if command == "init-run" and args.fixture is None:
        if args.control_store_root is None or not args.session_id:
            raise CliUsageError(
                "active Kernel init-run requires --control-store-root and --session-id"
            )
        kernel = VideoWorkflowKernel(args.workspace_root)
        kernel.control_store = ControlStore.initialize(
            args.workspace_root, kernel.contracts
        )
        probe = _production_probe_from_path(args.probe, kernel.contracts)
        if probe.canonical_platform not in {"bilibili", "youtube"}:
            raise CliUsageError(
                "production init-run is active only for Bilibili or YouTube"
            )
        platform = BilibiliPlatformCutoverPublisher().require_current(
            platform=probe.canonical_platform,
            control_store_root=args.control_store_root,
        )
        platform_authority = read_json(Path(platform["authority_path"]))
        result = kernel.initialize_production_source(
            probe,
            session_id=args.session_id,
            global_gate_binding=platform_authority["global_gate_binding"],
            fault_point=args.fault_point,
        )
        return _ok(
            command,
            "run_initialized",
            {
                "run_id": result.run_id,
                "run_dir": str(result.run_dir),
                "platform": probe.canonical_platform,
                "stage": "generating",
                "session_id": args.session_id,
            },
            str(result.run_dir / "workflow/run.json"),
        )
    if command in {"init-run", "source-import"}:
        if command == "init-run" and (
            args.control_store_root is not None or args.session_id is not None
        ):
            raise CliUsageError(
                "fixture init-run cannot combine --fixture with Kernel authority arguments"
            )
        if command == "source-import" and (
            args.probe is None
            or args.fixture is None
            or args.task_start is not None
            or args.request_id is not None
        ):
            raise CliUsageError(
                "fixture source-import requires --probe and --fixture only"
            )
        kernel = VideoWorkflowKernel(args.workspace_root)
        result = kernel.initialize_verified_import(
            probe=_probe_from_path(args.probe, kernel.contracts),
            fixture=args.fixture,
            fault_point=args.fault_point,
        )
        return _trace_envelope(command, result)
    if command == "trace-source-ready":
        kernel = VideoWorkflowKernel(args.workspace_root)
        result = kernel.trace_source_ready(
            fixture=args.fixture,
            task_start=args.task_start,
            request_id=args.request_id,
            title_override=args.title_override,
            fault_point=args.fault_point,
        )
        return _trace_envelope(command, result)
    if command == "source-blocker-resolve":
        run_dir = args.run_dir.resolve()
        kernel = VideoWorkflowKernel(run_dir.parent)
        result = kernel.resolve_source_user_input(
            run_dir,
            authentication_classification=args.authentication_classification,
            credential_evidence=read_json(args.credential_evidence.resolve()),
            credential_evidence_sha256=args.credential_evidence_sha256,
        )
        return _ok(
            command,
            str(result["classification"]),
            result,
            str(run_dir / "workflow/run.json"),
        )
    if command == "reconcile-run":
        if args.run_id is not None:
            if args.workspace_root is None or args.run_dir is not None:
                raise CliUsageError(
                    "initialization reconciliation requires --workspace-root and --run-id"
                )
            kernel = VideoWorkflowKernel(args.workspace_root)
            result = kernel.reconcile_initialization(args.run_id)
            return _ok(
                command,
                "initialization_reconciled",
                {
                    "run_id": result.run_id,
                    "run_dir": str(result.run_dir),
                    "outcome": result.outcome,
                },
                str(result.run_dir / "workflow/run.json")
                if result.outcome == "new_state_complete"
                else None,
            )
        if args.run_dir is None or args.workspace_root is not None:
            raise CliUsageError(
                "run reconciliation requires --run-dir, or use --workspace-root with --run-id"
            )
        run_dir = args.run_dir.resolve()
        workspace_root = run_dir.parent
        kernel = VideoWorkflowKernel(workspace_root)
        result = kernel.reconcile_run(run_dir)
        return _ok(
            command,
            "source_ready_current",
            {
                "run_id": result.run_id,
                "run_dir": str(result.run_dir),
                "outcome": result.outcome,
            },
            str(result.run_dir / "workflow/run.json"),
        )
    if command == "reconcile-authority":
        kernel = VideoWorkflowKernel(args.workspace_root)
        result = kernel.reconcile_authority(args.kind, args.id)
        return _ok(
            command,
            "authority_reconciled",
            {
                "kind": args.kind,
                "authority_id": result.run_id,
                "run_dir": str(result.run_dir),
                "outcome": result.outcome,
            },
            str(result.run_dir / "workflow/run.json"),
        )
    if command == "task-prepare":
        run_dir = args.run_dir.resolve()
        kernel = VideoWorkflowKernel(run_dir.parent)
        record = read_json(run_dir / "workflow/run.json")
        kernel.contracts.validate_run_record(record)
        result = kernel.prepare_source_acquisition_task(
            run_dir,
            logical_task_key=args.logical_task_key,
            prepared_at=args.prepared_at or record["task_start"],
            fault_point=args.fault_point,
        )
        return _ok(
            command,
            result.classification,
            {
                "run_id": result.run_id,
                "task_id": result.task_id,
                "task_dir": str(result.task_dir),
                "prompt_path": str(result.prompt_path),
            },
            str(result.envelope_path),
        )
    if command in {"task-claim", "task-reclaim"}:
        run_dir = args.run_dir.resolve()
        kernel = VideoWorkflowKernel(run_dir.parent)
        if command == "task-claim":
            result = kernel.claim_task(
                run_dir,
                args.task_id,
                coordinator_session_id=args.coordinator_session_id,
                worker_id=args.worker_id,
                fault_point=args.fault_point,
            )
        else:
            result = kernel.reclaim_task(
                run_dir,
                task_id=args.task_id,
                expected_attempt_id=args.expected_attempt_id,
                expected_claim_generation=args.expected_claim_generation,
                coordinator_session_id=args.coordinator_session_id,
                worker_id=args.worker_id,
                reason=args.reason,
                fault_point=args.fault_point,
            )
        return _ok(
            command,
            result.classification,
            {
                "run_id": result.run_id,
                "task_id": result.task_id,
                "attempt_id": result.attempt_id,
                "claim_generation": result.claim_generation,
                "attempt_dir": str(result.attempt_dir),
                "resource_admission": (
                    None
                    if result.resource_admission is None
                    else {
                        "queue_id": result.resource_admission.queue_id,
                        "queue_state": result.resource_admission.queue_state,
                        "required_resources": list(
                            result.resource_admission.required_resources
                        ),
                        "configuration_id": result.resource_admission.configuration_id,
                        "configuration_version": result.resource_admission.configuration_version,
                        "configuration_sha256": result.resource_admission.configuration_sha256,
                        "lease_id": result.resource_admission.lease_id,
                        "lease_state": result.resource_admission.lease_state,
                        "bypass_count": result.resource_admission.bypass_count,
                        "reservation_state": result.resource_admission.reservation_state,
                        "reservation_seq": result.resource_admission.reservation_seq,
                        "launch_authorization_state": result.resource_admission.launch_authorization_state,
                        "launch_required_resources": result.resource_admission.launch_required_resources,
                        "launch_eligible": result.resource_admission.launch_eligible,
                    }
                ),
            },
            str(result.attempt_dir / "attempt.json"),
        )
    if command == "task-complete":
        run_dir = args.run_dir.resolve()
        kernel = VideoWorkflowKernel(run_dir.parent)
        result = kernel.complete_task(
            run_dir,
            task_id=args.task_id,
            attempt_id=args.attempt_id,
            claim_generation=args.claim_generation,
            fault_point=args.fault_point,
        )
        return _ok(
            command,
            result.classification,
            {
                "run_id": result.run_id,
                "task_id": result.task_id,
                "attempt_id": result.attempt_id,
                "claim_generation": result.claim_generation,
            },
            str(result.completion_path),
        )
    if command == "resource-status":
        kernel = VideoWorkflowKernel(args.workspace_root)
        status = kernel.resource_status(args.task_id, args.attempt_id)
        return _ok(
            command,
            "resource_admission_status",
            _resource_status_data(status),
            str(args.workspace_root / ".workflow-control/control.sqlite3"),
        )
    if command == "resource-scheduler-status":
        kernel = VideoWorkflowKernel(args.workspace_root)
        return _ok(
            command,
            "resource_scheduler_status",
            kernel.resource_scheduler_status(),
            str(args.workspace_root / ".workflow-control/control.sqlite3"),
        )
    if command == "resource-capacity-status":
        kernel = VideoWorkflowKernel(args.workspace_root)
        return _ok(
            command,
            "resource_capacity_status",
            kernel.resource_capacity_status(),
            str(args.workspace_root / ".workflow-control/control.sqlite3"),
        )
    if command == "resource-config-activate":
        kernel = VideoWorkflowKernel(args.workspace_root)
        return _ok(
            command,
            "resource_configuration_activated",
            kernel.activate_resource_configuration(
                read_json(args.configuration.resolve())
            ),
            str(args.workspace_root / ".workflow-control/control.sqlite3"),
        )
    if command == "resource-breaker-set":
        kernel = VideoWorkflowKernel(args.workspace_root)
        return _ok(
            command,
            "resource_circuit_breaker_updated",
            kernel.set_resource_circuit_breaker(
                args.resource_class,
                state=args.state,
                reason=args.reason,
                platform=args.platform,
            ),
            str(args.workspace_root / ".workflow-control/control.sqlite3"),
        )
    if command == "resource-breaker-status":
        kernel = VideoWorkflowKernel(args.workspace_root)
        return _ok(
            command,
            "resource_circuit_breaker_status",
            {"breakers": kernel.resource_circuit_breaker_status()},
            str(args.workspace_root / ".workflow-control/control.sqlite3"),
        )
    if command == "resource-reconcile":
        kernel = VideoWorkflowKernel(args.workspace_root)
        result = kernel.resource_reconcile(
            current_coordinator_session_id=args.current_coordinator_session_id,
            lost_coordinator_session_ids=tuple(
                args.lost_coordinator_session_id
            ),
        )
        return _ok(
            command,
            str(result["classification"]),
            result,
            str(args.workspace_root / ".workflow-control/control.sqlite3"),
        )
    if command == "resource-resolve":
        kernel = VideoWorkflowKernel(args.workspace_root)
        status = kernel.resource_resolve(
            args.lease_id,
            args.attempt_id,
            args.expected_claim_generation,
            resolution_evidence=read_json(args.resolution_evidence.resolve()),
        )
        return _ok(
            command,
            "resource_lease_resolved",
            _resource_status_data(status),
            str(args.workspace_root / ".workflow-control/control.sqlite3"),
        )
    if command == "task-promote":
        run_dir = args.run_dir.resolve()
        kernel = VideoWorkflowKernel(run_dir.parent)
        result = kernel.promote_task(
            run_dir,
            task_id=args.task_id,
            attempt_id=args.attempt_id,
            claim_generation=args.claim_generation,
            fault_point=args.fault_point,
        )
        return _ok(
            command,
            result.classification,
            {
                "run_id": result.run_id,
                "task_id": result.task_id,
                "attempt_id": result.attempt_id,
                "claim_generation": result.claim_generation,
                "intent_id": result.intent_id,
            },
            str(run_dir / "workflow/run.json"),
        )
    if command == "adapter-capability-check":
        contracts = ContractRegistry(project_root)
        contracts.check()
        adapter = FixturePlatformAdapter(args.fixture, contracts)
        adapter.require_capability(args.capability)
        return _ok(
            command,
            "capability_available",
            {"capability": args.capability},
        )
    if command == "batch-plan":
        contracts = ContractRegistry(project_root)
        if (args.source_url is None) == (args.url_set is None):
            raise CliUsageError(
                "batch-plan requires exactly one of --source-url or --url-set"
            )
        result = BatchProjectionProvider().plan(
            workspace_root=args.workspace_root or project_root / "workspace",
            contracts=contracts,
            platform=args.platform,
            source_url=args.source_url,
            task_start=args.task_start,
            request_id=args.request_id,
            control_store_root=args.control_store_root,
            selection=_parse_batch_selection(args.selection),
            url_set=args.url_set,
        )
        return _ok(
            command,
            "batch_planned",
            {
                "batch_id": result["batch_id"],
                "batch_dir": result["batch_dir"],
                "item_order": result["item_order"],
                "created_or_replayed": result["created_or_replayed"],
            },
            result["batch_record_path"],
        )
    if command == "batch-run":
        contracts = ContractRegistry(project_root)
        control_root, record = _load_batch_record(
            batch_id=args.batch_id,
            control_store_root=args.control_store_root,
            contracts=contracts,
        )
        output_root = Path(record["output_root"]).resolve()
        run_task_start = args.run_task_start or record.get("run_task_start")
        if run_task_start is None:
            run_task_start = datetime.now(timezone.utc).isoformat()
        result = BatchProjectionProvider().run(
            workspace_root=output_root,
            contracts=contracts,
            batch_id=args.batch_id,
            control_store_root=control_root,
            session_id=args.session_id,
            run_task_start=run_task_start,
            fault_point=args.fault_point,
        )
        return _ok(
            command,
            "batch_run_submitted",
            result,
            str(Path(record["batch_dir"]) / "batch-record.json"),
        )
    if command == "batch-recover":
        contracts = ContractRegistry(project_root)
        control_root, record = _load_batch_record(
            batch_id=args.batch_id,
            control_store_root=args.control_store_root,
            contracts=contracts,
        )
        result = BatchProjectionProvider().recover(
            workspace_root=Path(record["output_root"]).resolve(),
            contracts=contracts,
            batch_id=args.batch_id,
            control_store_root=control_root,
        )
        return _ok(
            command,
            "batch_recovered",
            result,
            str(Path(record["batch_dir"]) / "batch-record.json"),
        )
    if command == "batch-rebuild-projections":
        contracts = ContractRegistry(project_root)
        control_root, record = _load_batch_record(
            batch_id=args.batch_id,
            control_store_root=args.control_store_root,
            contracts=contracts,
        )
        result = BatchProjectionProvider().rebuild_projections(
            workspace_root=Path(record["output_root"]).resolve(),
            contracts=contracts,
            batch_id=args.batch_id,
            control_store_root=control_root,
        )
        return _ok(
            command,
            "batch_projections_rebuilt",
            {"batch_id": args.batch_id, "projections": result},
        )
    if command == "batch-status":
        contracts = ContractRegistry(project_root)
        control_root, record = _load_batch_record(
            batch_id=args.batch_id,
            control_store_root=args.control_store_root,
            contracts=contracts,
        )
        result = BatchProjectionProvider().status(
            workspace_root=Path(record["output_root"]).resolve(),
            contracts=contracts,
            batch_id=args.batch_id,
            control_store_root=control_root,
        )
        return _ok(
            command,
            "batch_status_reported",
            result,
        )
    raise CliUsageError(f"unsupported command: {command}")


def _trace_envelope(command: str, result: Any) -> dict:
    return _ok(
        command,
        result.classification,
        {
            "run_id": result.run_id,
            "run_dir": str(result.run_dir),
            "checkpoint": "source_ready",
            "checkpoint_status": "current",
            "max_path_utf16_units": result.max_path_utf16_units,
            "adapter_capabilities": list(result.adapter_capabilities),
        },
        str(result.run_dir / "workflow/run.json"),
    )


def main(argv: list[str] | None = None) -> int:
    command = "unknown"
    try:
        args = _parser().parse_args(argv)
        command = args.command
        project_root = Path(__file__).resolve().parents[2]
        if command == "source-live-smoke":
            try:
                report = run_source_live_smoke(
                    spec_path=args.spec,
                    credential_profile=args.credential_profile,
                    work_root=args.work_root,
                    project_root=project_root,
                )
            except KernelError as exc:
                print(
                    f"ERROR: source live smoke failed ({exc.classification})",
                    file=sys.stderr,
                )
                return exc.exit_code
            except Exception:
                print("ERROR: source live smoke failed (kernel_error)", file=sys.stderr)
                return 70
            sys.stdout.write(
                json.dumps(
                    report,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            )
            return 0
        envelope = _execute(args, project_root)
        exit_code = 0
    except KernelError as exc:
        envelope = _error(command, exc)
        exit_code = exc.exit_code
    except Exception as exc:  # parser/top-level fail-closed envelope
        error = KernelError(f"unexpected kernel failure: {type(exc).__name__}: {exc}")
        envelope = _error(command, error)
        exit_code = error.exit_code

    try:
        project_root = Path(__file__).resolve().parents[2]
        canonical = ContractRegistry(project_root)
        canonical.validate("workflow-result", envelope)
    except Exception:
        exit_code = 70
        envelope = {
            "schema_name": "workflow-result",
            "schema_version": "1.0.0",
            "kernel_version": "2.0.0",
            "command": command,
            "status": "error",
            "classification": "result_envelope_failure",
            "evidence_path": None,
            "data": {"message": "Kernel could not validate its result envelope"},
        }
    sys.stdout.write(json.dumps(envelope, ensure_ascii=False, sort_keys=True) + "\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
