from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from .adapters import FixturePlatformAdapter
from .contracts import ContractRegistry
from .control_store import ControlStore
from .errors import CliUsageError, KernelError
from .kernel import FAULT_POINTS, VideoWorkflowKernel
from .models import BootstrapProbeResult
from .task_execution import (
    CLAIM_FAULT_POINTS,
    COMPLETION_FAULT_POINTS,
    PROMOTION_FAULT_POINTS,
)
from .utils import read_json


class MachineArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CliUsageError(message)


def _parser() -> argparse.ArgumentParser:
    parser = MachineArgumentParser(prog="video_workflow.py")
    commands = parser.add_subparsers(dest="command", required=True)

    contracts = commands.add_parser("contracts-check")
    contracts.add_argument("--registry", type=Path)

    store = commands.add_parser("control-store-check")
    store.add_argument("--workspace-root", required=True, type=Path)

    probe = commands.add_parser("bootstrap-probe")
    _add_trace_inputs(probe)

    init = commands.add_parser("init-run")
    init.add_argument("--workspace-root", required=True, type=Path)
    init.add_argument("--probe", required=True, type=Path)
    init.add_argument("--fixture", required=True, type=Path)
    init.add_argument("--fault-point", choices=sorted(FAULT_POINTS))

    source_import = commands.add_parser("source-import")
    source_import.add_argument("--workspace-root", required=True, type=Path)
    source_import.add_argument("--probe", required=True, type=Path)
    source_import.add_argument("--fixture", required=True, type=Path)
    source_import.add_argument("--fault-point", choices=sorted(FAULT_POINTS))

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
    task_reclaim.add_argument("--fault-point", choices=sorted(CLAIM_FAULT_POINTS))

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

    capability = commands.add_parser("adapter-capability-check")
    capability.add_argument("--fixture", required=True, type=Path)
    capability.add_argument("--capability", required=True)
    return parser


def _add_trace_inputs(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workspace-root", required=True, type=Path)
    parser.add_argument("--fixture", required=True, type=Path)
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


def _execute(args: argparse.Namespace, project_root: Path) -> dict:
    command = args.command
    if command == "contracts-check":
        registry = ContractRegistry(project_root, args.registry)
        return _ok(command, "contracts_valid", registry.check(), str(registry.registry_path))
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
    if command == "bootstrap-probe":
        kernel = VideoWorkflowKernel(args.workspace_root)
        result = kernel.bootstrap_probe(
            fixture=args.fixture,
            task_start=args.task_start,
            request_id=args.request_id,
            title_override=args.title_override,
        )
        return _ok(
            command,
            "probe_complete",
            {"run_id": result.run_id, "probe_record": str(result.record_path)},
            str(result.record_path),
        )
    if command in {"init-run", "source-import"}:
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
