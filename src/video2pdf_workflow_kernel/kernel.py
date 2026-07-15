from __future__ import annotations

from datetime import datetime
import hashlib
import os
from pathlib import Path
from typing import Any

from .adapters import FixturePlatformAdapter
from .contracts import ContractRegistry
from .control_store import ControlStore
from .errors import ArtifactDrift, ContractError, InitializationFault, KernelConflict
from .models import BootstrapProbeResult, ReconcileResult, TraceResult
from .scaffold import (
    create_scaffold,
    load_scaffold,
    max_reserved_path_units,
    output_name,
    validate_path_budget,
)
from .utils import read_json, sha256_file, write_json_atomic


FAULT_POINTS = frozenset(
    {
        "after_intent_prepared",
        "after_scaffold_staged",
        "after_bootstrap_evidence_staged",
        "after_contracts_written",
        "after_output_dir_publish",
        "after_run_record_commit_marker",
        "before_intent_commit",
        "after_intent_commit",
    }
)


class VideoWorkflowKernel:
    """Deep Slice 1 interface; CLI and future adapters delegate here."""

    def __init__(self, workspace_root: Path) -> None:
        self.project_root = Path(__file__).resolve().parents[2]
        self.workspace_root = workspace_root.resolve()
        self.contracts = ContractRegistry(self.project_root)
        self.contracts.check()
        self.scaffold = load_scaffold(self.project_root, self.contracts)
        self.control_store = ControlStore(self.workspace_root)
        self.control_store.check()
        self.bootstrap_root = (
            self.workspace_root.parent / "待删除" / "pipeline-bootstrap"
        )
        self.initialization_root = (
            self.workspace_root.parent / "待删除" / "kernel-initialization"
        )

    def bootstrap_probe(
        self,
        *,
        fixture: Path,
        task_start: str,
        request_id: str,
        title_override: str | None = None,
    ) -> BootstrapProbeResult:
        adapter = FixturePlatformAdapter(fixture, self.contracts)
        metadata = adapter.probe()
        try:
            parsed_start = datetime.fromisoformat(task_start)
        except ValueError as exc:
            raise ContractError(f"task_start must be ISO 8601: {task_start}") from exc
        if parsed_start.tzinfo is None:
            raise ContractError("task_start must include a timezone offset")
        original_title = title_override or metadata["original_title"]
        identity = "\0".join(
            (
                adapter.adapter_id,
                metadata["canonical_item_id"],
                task_start,
                request_id,
            )
        )
        run_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
        fixture_sha = sha256_file(adapter.manifest_path)
        record = {
            "schema_name": "bootstrap-record",
            "schema_version": "1.0.0",
            "kernel_version": "2.0.0",
            "run_id": run_id,
            "request_id": request_id,
            "adapter_id": adapter.adapter_id,
            "canonical_item_id": metadata["canonical_item_id"],
            "original_title": original_title,
            "task_start": task_start,
            "fixture_uri": f"fixture://{fixture.resolve().as_posix()}",
            "fixture_manifest_sha256": fixture_sha,
            "status": "probe_complete",
        }
        self.contracts.validate("bootstrap-record", record)
        record_dir = self.bootstrap_root / run_id
        record_dir.mkdir(parents=True, exist_ok=True)
        record_path = record_dir / "probe.json"
        if record_path.exists():
            if read_json(record_path) != record:
                raise KernelConflict("bootstrap identity was reused with different evidence")
        else:
            write_json_atomic(record_path, record)
        return BootstrapProbeResult(
            run_id=run_id,
            request_id=request_id,
            record_path=record_path,
            original_title=original_title,
            task_start=task_start,
            canonical_item_id=metadata["canonical_item_id"],
            fixture_manifest_sha256=fixture_sha,
        )

    def trace_source_ready(
        self,
        *,
        fixture: Path,
        task_start: str,
        request_id: str,
        title_override: str | None = None,
        fault_point: str | None = None,
    ) -> TraceResult:
        probe = self.bootstrap_probe(
            fixture=fixture,
            task_start=task_start,
            request_id=request_id,
            title_override=title_override,
        )
        return self.initialize_verified_import(
            probe=probe, fixture=fixture, fault_point=fault_point
        )

    def initialize_verified_import(
        self,
        *,
        probe: BootstrapProbeResult,
        fixture: Path,
        fault_point: str | None = None,
    ) -> TraceResult:
        if fault_point is not None and fault_point not in FAULT_POINTS:
            raise ContractError(f"unknown initialization fault point: {fault_point}")
        adapter = FixturePlatformAdapter(fixture, self.contracts)
        if sha256_file(adapter.manifest_path) != probe.fixture_manifest_sha256:
            raise ContractError("fixture changed after Bootstrap Probe")

        existing = self.control_store.binding_for_run(probe.run_id)
        if existing:
            run_dir = Path(existing["output_path"])
            if run_dir.is_dir() and (run_dir / "workflow/run.json").is_file():
                self._verify_current_source(run_dir)
                return TraceResult(
                    run_id=probe.run_id,
                    run_dir=run_dir,
                    classification="already_source_ready",
                    max_path_utf16_units=max_reserved_path_units(run_dir, self.scaffold),
                    adapter_capabilities=adapter.capabilities,
                )

        output_path = self._resolve_output_path(probe)
        maximum_units = validate_path_budget(output_path, self.scaffold)
        intent_id = hashlib.sha256(
            f"initialize\0{probe.run_id}\0{output_path}".encode("utf-8")
        ).hexdigest()[:32]
        staging_path = self.initialization_root / probe.run_id / "candidate"
        state = self.control_store.prepare_initialization(
            run_id=probe.run_id,
            output_path=output_path,
            intent_id=intent_id,
            staging_path=staging_path,
        )
        if state == "COMMITTED":
            self._verify_current_source(output_path)
            return TraceResult(
                run_id=probe.run_id,
                run_dir=output_path,
                classification="already_source_ready",
                max_path_utf16_units=maximum_units,
                adapter_capabilities=adapter.capabilities,
            )
        self._inject(fault_point, "after_intent_prepared")

        staging_path.parent.mkdir(parents=True, exist_ok=True)
        ledger = create_scaffold(staging_path, self.scaffold, probe.run_id)
        self.contracts.validate("scaffold-ledger", ledger)
        write_json_atomic(staging_path / "workflow/scaffold-ledger.json", ledger)
        self._inject(fault_point, "after_scaffold_staged")

        imported = adapter.verified_import(staging_path)
        source_manifest = {
            "schema_name": "source-manifest",
            "schema_version": "1.0.0",
            "kernel_version": "2.0.0",
            "run_id": probe.run_id,
            "mode": "verified_import",
            "adapter_id": adapter.adapter_id,
            "canonical_item_id": probe.canonical_item_id,
            "fixture_manifest_sha256": probe.fixture_manifest_sha256,
            "artifacts": imported,
        }
        self.contracts.validate("source-manifest", source_manifest)
        source_manifest_sha = write_json_atomic(
            staging_path / "source/manifest.json", source_manifest
        )
        (staging_path / "待删除/bootstrap/probe.json").write_bytes(
            probe.record_path.read_bytes()
        )
        self._inject(fault_point, "after_bootstrap_evidence_staged")
        artifact_plan = self._artifact_plan(probe.run_id)
        self.contracts.validate("artifact-plan", artifact_plan)
        write_json_atomic(staging_path / "workflow/artifact-plan.json", artifact_plan)
        run_record = self._run_record(
            probe=probe,
            output_path=output_path,
            intent_id=intent_id,
            source_manifest_sha=source_manifest_sha,
        )
        self.contracts.validate("run-record", run_record)
        write_json_atomic(
            staging_path / "待删除/bootstrap/prepared-run.json", run_record
        )
        self._inject(fault_point, "after_contracts_written")

        if output_path.exists():
            raise KernelConflict("output path appeared during initialization")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging_path, output_path)
        self._inject(fault_point, "after_output_dir_publish")
        self.control_store.set_intent_state(intent_id, "PUBLISHED")

        run_record_sha = write_json_atomic(output_path / "workflow/run.json", run_record)
        self._inject(fault_point, "after_run_record_commit_marker")
        self.control_store.set_intent_state(
            intent_id, "RECORD_COMMITTED", run_record_sha256=run_record_sha
        )
        self._inject(fault_point, "before_intent_commit")
        self.control_store.set_intent_state(
            intent_id, "COMMITTED", run_record_sha256=run_record_sha
        )
        self._inject(fault_point, "after_intent_commit")
        self._verify_current_source(output_path)
        return TraceResult(
            run_id=probe.run_id,
            run_dir=output_path,
            classification="source_ready",
            max_path_utf16_units=maximum_units,
            adapter_capabilities=adapter.capabilities,
        )

    def reconcile_initialization(self, run_id: str) -> ReconcileResult:
        intent = self.control_store.intent_for_run(run_id)
        if intent is None:
            raise KernelConflict(f"initialization intent does not exist for run {run_id}")
        output_path = Path(intent["output_path"])
        staging_path = Path(intent["staging_path"])
        state = str(intent["state"])
        if state == "ABORTED":
            return ReconcileResult(run_id, output_path, "old_state_complete")
        if not output_path.exists():
            if staging_path.exists():
                destination = staging_path.parent / f"aborted-{intent['intent_id']}"
                if destination.exists():
                    destination = staging_path.parent / (
                        f"aborted-{intent['intent_id']}-{hashlib.sha256(str(staging_path).encode()).hexdigest()[:8]}"
                    )
                os.replace(staging_path, destination)
            self.control_store.abort_initialization(run_id)
            return ReconcileResult(run_id, output_path, "old_state_complete")

        prepared_path = output_path / "待删除/bootstrap/prepared-run.json"
        run_path = output_path / "workflow/run.json"
        if not run_path.is_file():
            if not prepared_path.is_file():
                raise KernelConflict("published output lacks its prepared Run Record")
            run_record = read_json(prepared_path)
            self.contracts.validate("run-record", run_record)
            run_record_sha = write_json_atomic(run_path, run_record)
            self.control_store.set_intent_state(
                intent["intent_id"],
                "RECORD_COMMITTED",
                run_record_sha256=run_record_sha,
            )
        else:
            run_record = read_json(run_path)
            self.contracts.validate("run-record", run_record)
            run_record_sha = sha256_file(run_path)
        self._verify_current_source(output_path)
        self.control_store.set_intent_state(
            intent["intent_id"], "COMMITTED", run_record_sha256=run_record_sha
        )
        return ReconcileResult(run_id, output_path, "new_state_complete")

    def reconcile_run(self, run_dir: Path) -> ReconcileResult:
        run_dir = run_dir.resolve()
        record_path = run_dir / "workflow/run.json"
        record = read_json(record_path)
        self.contracts.validate("run-record", record)
        binding = self.control_store.binding_for_run(record["run_id"])
        if binding is None or Path(binding["output_path"]).resolve() != run_dir:
            raise KernelConflict("Run Record and Control Store binding disagree")
        try:
            self._verify_current_source(run_dir)
        except ArtifactDrift:
            record["checkpoints"]["source_ready"]["status"] = "stale"
            write_json_atomic(record_path, record)
            raise
        return ReconcileResult(record["run_id"], run_dir, "new_state_complete")

    def _resolve_output_path(self, probe: BootstrapProbeResult) -> Path:
        parsed = datetime.fromisoformat(probe.task_start)
        timestamp = parsed.strftime("%Y%m%d_%H%M%S")
        name = output_name(
            original_title=probe.original_title,
            timestamp=timestamp,
            adapter_id="fixture",
            item_id=probe.canonical_item_id,
            max_units=self.scaffold["max_output_component_utf16_units"],
        )
        candidate = self.workspace_root / name
        owner = self.control_store.binding_for_path(candidate)
        if owner is None and not candidate.exists():
            return candidate
        if owner is not None and owner["run_id"] == probe.run_id:
            return candidate
        collision_suffix = f"_r{probe.run_id[:8]}"
        from .utils import truncate_utf16, utf16_units

        collision_name = (
            truncate_utf16(
                name,
                self.scaffold["max_output_component_utf16_units"]
                - utf16_units(collision_suffix),
            )
            + collision_suffix
        )
        collision = self.workspace_root / collision_name
        owner = self.control_store.binding_for_path(collision)
        if owner is not None and owner["run_id"] == probe.run_id:
            return collision
        if owner is not None or collision.exists():
            raise KernelConflict(
                "same-second collision-safe output path is already occupied",
                data={"candidate_output_path": str(collision)},
            )
        return collision

    @staticmethod
    def _artifact_plan(run_id: str) -> dict[str, Any]:
        artifacts = [
            ("run_record", "workflow/run.json", "run-record", "run_initialized"),
            ("artifact_plan", "workflow/artifact-plan.json", "artifact-plan", "run_initialized"),
            ("scaffold_ledger", "workflow/scaffold-ledger.json", "scaffold-ledger", "run_initialized"),
            ("source_manifest", "source/manifest.json", "source-manifest", "source_ready"),
        ]
        return {
            "schema_name": "artifact-plan",
            "schema_version": "1.0.0",
            "kernel_version": "2.0.0",
            "run_id": run_id,
            "artifacts": [
                {
                    "logical_id": logical_id,
                    "path": path,
                    "schema_name": schema_name,
                    "generator": "kernel:init-run" if checkpoint == "run_initialized" else "kernel:verified-import",
                    "earliest_checkpoint": checkpoint,
                }
                for logical_id, path, schema_name, checkpoint in artifacts
            ],
        }

    @staticmethod
    def _run_record(
        *,
        probe: BootstrapProbeResult,
        output_path: Path,
        intent_id: str,
        source_manifest_sha: str,
    ) -> dict[str, Any]:
        from .utils import normalize_title

        return {
            "schema_name": "run-record",
            "schema_version": "1.0.0",
            "kernel_version": "2.0.0",
            "scaffold_version": "1.0.0",
            "run_id": probe.run_id,
            "request_id": probe.request_id,
            "platform_adapter": "fixture",
            "canonical_item_id": probe.canonical_item_id,
            "original_title": probe.original_title,
            "normalized_title": normalize_title(probe.original_title),
            "task_start": probe.task_start,
            "output_path": str(output_path.resolve()),
            "deliverable_version": 1,
            "version_basis": "source_only",
            "source_acquisition_mode": "verified_import",
            "phase": "source_ready",
            "initialization_intent_id": intent_id,
            "coordination_revision": 1,
            "artifact_plan": "workflow/artifact-plan.json",
            "artifact_generations": {
                "source_manifest": {
                    "path": "source/manifest.json",
                    "generation": 1,
                    "sha256": source_manifest_sha,
                    "producer": "kernel:verified-import",
                }
            },
            "checkpoints": {
                "source_ready": {
                    "status": "current",
                    "artifact_generations": {"source_manifest": 1},
                    "evidence_sha256": source_manifest_sha,
                }
            },
        }

    def _verify_current_source(self, run_dir: Path) -> None:
        record_path = run_dir / "workflow/run.json"
        record = read_json(record_path)
        self.contracts.validate("run-record", record)
        manifest_path = run_dir / "source/manifest.json"
        expected_manifest_sha = record["artifact_generations"]["source_manifest"]["sha256"]
        drift: list[str] = []
        if not manifest_path.is_file():
            drift.append("source/manifest.json")
            manifest = None
        else:
            actual_manifest_sha = sha256_file(manifest_path)
            if actual_manifest_sha != expected_manifest_sha:
                drift.append("source/manifest.json")
            try:
                manifest = read_json(manifest_path)
                self.contracts.validate("source-manifest", manifest)
            except (ContractError, ValueError):
                manifest = None
                drift.append("source/manifest.json")
        if manifest is not None:
            for artifact in manifest["artifacts"]:
                path = run_dir.joinpath(*artifact["path"].split("/"))
                if not path.is_file() or sha256_file(path) != artifact["sha256"]:
                    drift.append(artifact["path"])
        if drift:
            raise ArtifactDrift(
                "imported source differs from its committed generation",
                data={"run_dir": str(run_dir), "drifted_paths": sorted(set(drift))},
            )
        if record["checkpoints"]["source_ready"]["status"] != "current":
            raise ArtifactDrift(
                "source_ready checkpoint is stale",
                data={"run_dir": str(run_dir), "drifted_paths": []},
            )

    @staticmethod
    def _inject(selected: str | None, current: str) -> None:
        if selected == current:
            raise InitializationFault(current)
