from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .adapters import FixturePlatformAdapter
from .artifact_plan import ARTIFACT_PLAN_BINDINGS
from .contracts import ContractRegistry
from .control_store import ControlStore
from .errors import (
    ArtifactDrift,
    ContractError,
    ControlStoreUnavailable,
    InitializationFault,
    KernelConflict,
)
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
RUN_STATE_MUTATION_FAULT_POINTS = frozenset(
    {
        "after_run_state_mutation_prepared",
        "after_stale_run_record_write",
        "after_run_state_mutation_commit",
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
        if ControlStore.identity_evidence_exists(self.workspace_root):
            self.control_store: ControlStore | None = ControlStore(
                self.workspace_root, self.contracts
            )
            self.control_store.check()
        else:
            self.control_store = None
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
        if self.control_store is None:
            self.control_store = ControlStore.initialize(
                self.workspace_root, self.contracts
            )
            self.control_store.check()
        else:
            self.control_store.check()
        adapter = FixturePlatformAdapter(fixture, self.contracts)
        record = self._derive_bootstrap_record(
            adapter=adapter,
            task_start=task_start,
            request_id=request_id,
            title_override=title_override,
        )
        run_id = record["run_id"]
        original_title = record["original_title"]
        fixture_sha = record["fixture_manifest_sha256"]
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
            canonical_item_id=record["canonical_item_id"],
            fixture_manifest_sha256=fixture_sha,
        )

    def _derive_bootstrap_record(
        self,
        *,
        adapter: FixturePlatformAdapter,
        task_start: str,
        request_id: str,
        title_override: str | None = None,
    ) -> dict[str, Any]:
        metadata = adapter.probe()
        try:
            parsed_start = datetime.fromisoformat(task_start)
        except ValueError as exc:
            raise ContractError(f"task_start must be ISO 8601: {task_start}") from exc
        if parsed_start.tzinfo is None:
            raise ContractError("task_start must include a timezone offset")
        original_title = metadata["original_title"]
        if title_override is not None and title_override != original_title:
            raise KernelConflict(
                "title override disagrees with canonical fixture metadata",
                data={"canonical_title": original_title},
            )
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
        return {
            "schema_name": "bootstrap-record",
            "schema_version": "1.0.0",
            "kernel_version": "2.0.0",
            "run_id": run_id,
            "request_id": request_id,
            "adapter_id": adapter.adapter_id,
            "canonical_item_id": metadata["canonical_item_id"],
            "original_title": original_title,
            "task_start": task_start,
            "fixture_uri": f"fixture://{adapter.fixture_root.as_posix()}",
            "fixture_manifest_sha256": fixture_sha,
            "status": "probe_complete",
        }

    def trace_source_ready(
        self,
        *,
        fixture: Path,
        task_start: str,
        request_id: str,
        title_override: str | None = None,
        fault_point: str | None = None,
    ) -> TraceResult:
        if self.control_store is not None:
            self.control_store.check()
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
        store = self._preflight_control_store()
        if fault_point is not None and fault_point not in FAULT_POINTS:
            raise ContractError(f"unknown initialization fault point: {fault_point}")
        adapter = FixturePlatformAdapter(fixture, self.contracts)
        loaded_probe = read_json(probe.record_path)
        self.contracts.validate("bootstrap-record", loaded_probe)
        expected_probe = self._derive_bootstrap_record(
            adapter=adapter,
            task_start=loaded_probe["task_start"],
            request_id=loaded_probe["request_id"],
        )
        if loaded_probe != expected_probe:
            raise KernelConflict(
                "Bootstrap evidence disagrees with canonical fixture identity"
            )
        caller_binding = {
            "run_id": probe.run_id,
            "request_id": probe.request_id,
            "original_title": probe.original_title,
            "task_start": probe.task_start,
            "canonical_item_id": probe.canonical_item_id,
            "fixture_manifest_sha256": probe.fixture_manifest_sha256,
        }
        evidence_binding = {name: loaded_probe[name] for name in caller_binding}
        if caller_binding != evidence_binding:
            raise KernelConflict("caller Bootstrap identity disagrees with validated evidence")
        existing = store.binding_for_run(probe.run_id)
        if existing:
            intent = store.intent_for_run(probe.run_id)
            if (
                intent is None
                or intent["intent_id"] != existing["initialization_intent_id"]
                or Path(intent["output_path"]).resolve()
                != Path(existing["output_path"]).resolve()
            ):
                raise KernelConflict("Control Store binding and initialization intent disagree")
            run_dir = Path(existing["output_path"])
            state = str(intent["state"])
            if state in {"PREPARED", "PUBLISHED", "RECORD_COMMITTED"}:
                reconciled = self.reconcile_initialization(probe.run_id)
                if reconciled.outcome == "new_state_complete":
                    return TraceResult(
                        run_id=probe.run_id,
                        run_dir=run_dir,
                        classification="already_source_ready",
                        max_path_utf16_units=max_reserved_path_units(run_dir, self.scaffold),
                        adapter_capabilities=adapter.capabilities,
                    )
            elif state == "COMMITTED":
                run_path = run_dir / "workflow/run.json"
                if not run_dir.is_dir() or not run_path.is_file():
                    raise KernelConflict(
                        "committed initialization lost its canonical Run Record"
                    )
                if intent["run_record_sha256"] != sha256_file(run_path):
                    raise KernelConflict(
                        "committed initialization Run Record fingerprint disagrees"
                    )
                self._verify_current_source(run_dir)
                return TraceResult(
                    run_id=probe.run_id,
                    run_dir=run_dir,
                    classification="already_source_ready",
                    max_path_utf16_units=max_reserved_path_units(run_dir, self.scaffold),
                    adapter_capabilities=adapter.capabilities,
                )
            else:
                raise KernelConflict("active binding has an invalid initialization state")

        output_path = self._resolve_output_path(probe)
        maximum_units = validate_path_budget(output_path, self.scaffold)
        intent_id = hashlib.sha256(
            f"initialize\0{probe.run_id}\0{output_path}".encode("utf-8")
        ).hexdigest()[:32]
        staging_path = self.initialization_root / probe.run_id / "candidate"
        state = store.prepare_initialization(
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
        self.contracts.validate("scaffold-contract", self.scaffold)
        write_json_atomic(
            staging_path / "workflow/scaffold-contract.json", self.scaffold
        )
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
        expected_run_record_sha = write_json_atomic(
            staging_path / "待删除/bootstrap/prepared-run.json", run_record
        )
        store.bind_publication_expectations(
            intent_id,
            expected_run_record_sha256=expected_run_record_sha,
            canonical_platform="fixture",
            canonical_item_id=probe.canonical_item_id,
            source_identity=probe.fixture_manifest_sha256,
            source_manifest_sha256=source_manifest_sha,
        )
        self._inject(fault_point, "after_contracts_written")

        if output_path.exists():
            raise KernelConflict("output path appeared during initialization")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging_path, output_path)
        self._inject(fault_point, "after_output_dir_publish")
        store.transition_intent(
            intent_id, expected_state="PREPARED", new_state="PUBLISHED"
        )

        run_record_sha = write_json_atomic(output_path / "workflow/run.json", run_record)
        self._inject(fault_point, "after_run_record_commit_marker")
        store.transition_intent(
            intent_id,
            expected_state="PUBLISHED",
            new_state="RECORD_COMMITTED",
            run_record_sha256=run_record_sha,
        )
        self._inject(fault_point, "before_intent_commit")
        store.transition_intent(
            intent_id,
            expected_state="RECORD_COMMITTED",
            new_state="COMMITTED",
            run_record_sha256=run_record_sha,
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
        store = self._preflight_control_store()
        intent = store.intent_for_run(run_id)
        if intent is None:
            raise KernelConflict(f"initialization intent does not exist for run {run_id}")
        output_path = Path(intent["output_path"])
        staging_path = Path(intent["staging_path"])
        state = str(intent["state"])
        if state == "ABORTED":
            return ReconcileResult(run_id, output_path, "old_state_complete")
        if not output_path.exists():
            if state in {"PUBLISHED", "RECORD_COMMITTED", "COMMITTED"}:
                raise KernelConflict(
                    f"{state} initialization lost its canonical output; recovery is blocked"
                )
            if staging_path.exists():
                destination = staging_path.parent / f"aborted-{intent['intent_id']}"
                if destination.exists():
                    destination = staging_path.parent / (
                        f"aborted-{intent['intent_id']}-{hashlib.sha256(str(staging_path).encode()).hexdigest()[:8]}"
                    )
                os.replace(staging_path, destination)
            store.abort_initialization(run_id)
            return ReconcileResult(run_id, output_path, "old_state_complete")

        prepared_path = output_path / "待删除/bootstrap/prepared-run.json"
        run_path = output_path / "workflow/run.json"
        if not run_path.is_file():
            if state in {"RECORD_COMMITTED", "COMMITTED"}:
                raise KernelConflict(
                    f"{state} initialization lost its canonical Run Record"
                )
            if not prepared_path.is_file():
                raise KernelConflict("published output lacks its prepared Run Record")
            run_record = read_json(prepared_path)
            self.contracts.validate("run-record", run_record)
            run_record_sha = sha256_file(prepared_path)
        else:
            run_record = read_json(run_path)
            self.contracts.validate("run-record", run_record)
            run_record_sha = sha256_file(run_path)
        recovery_drift = self._identity_binding_drift(
            output_path, intent, run_record, run_record_sha
        )
        if recovery_drift:
            raise KernelConflict(
                "initialization recovery evidence disagrees with immutable intent",
                data={"drifted_bindings": recovery_drift},
            )
        if state == "PREPARED":
            store.transition_intent(
                intent["intent_id"], expected_state="PREPARED", new_state="PUBLISHED"
            )
            state = "PUBLISHED"
        if not run_path.is_file():
            canonical_sha = write_json_atomic(run_path, run_record)
            if canonical_sha != run_record_sha:
                raise KernelConflict("canonical Run Record differs from prepared evidence")
        if state == "PUBLISHED":
            store.transition_intent(
                intent["intent_id"],
                expected_state="PUBLISHED",
                new_state="RECORD_COMMITTED",
                run_record_sha256=run_record_sha,
            )
            state = "RECORD_COMMITTED"
        elif state in {"RECORD_COMMITTED", "COMMITTED"}:
            if intent["run_record_sha256"] != run_record_sha:
                raise KernelConflict(
                    "initialization intent Run Record fingerprint disagrees"
                )
        self._verify_current_source(output_path)
        if state == "RECORD_COMMITTED":
            store.transition_intent(
                intent["intent_id"],
                expected_state="RECORD_COMMITTED",
                new_state="COMMITTED",
                run_record_sha256=run_record_sha,
            )
        return ReconcileResult(run_id, output_path, "new_state_complete")

    def reconcile_run(
        self, run_dir: Path, *, fault_point: str | None = None
    ) -> ReconcileResult:
        if (
            fault_point is not None
            and fault_point not in RUN_STATE_MUTATION_FAULT_POINTS
        ):
            raise ContractError(f"unknown run-state mutation fault point: {fault_point}")
        store = self._preflight_control_store()
        run_dir = run_dir.resolve()
        record_path = run_dir / "workflow/run.json"
        record = read_json(record_path)
        self.contracts.validate("run-record", record)
        binding = store.binding_for_run(record["run_id"])
        if binding is None or Path(binding["output_path"]).resolve() != run_dir:
            raise KernelConflict("Run Record and Control Store binding disagree")
        self._resume_prepared_run_state_mutation(store, record["run_id"], record_path)
        record = read_json(record_path)
        self.contracts.validate("run-record", record)
        if record["run_id"] != binding["run_id"]:
            raise KernelConflict("Run Record and Control Store binding disagree")
        try:
            self._verify_current_source(run_dir)
        except ArtifactDrift:
            if record["checkpoints"]["source_ready"]["status"] == "stale":
                raise
            old_sha = sha256_file(record_path)
            replacement = json.loads(json.dumps(record))
            replacement["coordination_revision"] = record["coordination_revision"] + 1
            replacement["checkpoints"]["source_ready"]["status"] = "stale"
            mutation = store.prepare_run_state_mutation(
                run_id=record["run_id"],
                expected_run_revision=record["coordination_revision"],
                old_run_record_sha256=old_sha,
                replacement_run_record=replacement,
            )
            self._inject(fault_point, "after_run_state_mutation_prepared")
            if sha256_file(record_path) != mutation["old_run_record_sha256"]:
                raise KernelConflict(
                    "Run Record changed after source-drift mutation preparation"
                )
            replacement_sha = write_json_atomic(record_path, replacement)
            if replacement_sha != mutation["replacement_run_record_sha256"]:
                raise KernelConflict("source-drift replacement fingerprint changed")
            self._inject(fault_point, "after_stale_run_record_write")
            store.commit_run_state_mutation(mutation["mutation_id"])
            self._inject(fault_point, "after_run_state_mutation_commit")
            raise
        return ReconcileResult(record["run_id"], run_dir, "new_state_complete")

    def _resume_prepared_run_state_mutation(
        self, store: ControlStore, run_id: str, record_path: Path
    ) -> None:
        mutation = store.prepared_run_state_mutation(run_id)
        if mutation is None:
            return
        replacement = json.loads(mutation["replacement_run_record_json"])
        self.contracts.validate("run-record", replacement)
        replacement_sha = hashlib.sha256(
            (mutation["replacement_run_record_json"]).encode("utf-8")
        ).hexdigest()
        if (
            replacement_sha != mutation["replacement_run_record_sha256"]
            or replacement["run_id"] != run_id
            or replacement["coordination_revision"]
            != mutation["expected_run_revision"] + 1
        ):
            raise ControlStoreUnavailable(
                "prepared run-state mutation replacement evidence is invalid"
            )
        actual_sha = sha256_file(record_path)
        if actual_sha == mutation["old_run_record_sha256"]:
            actual_sha = write_json_atomic(record_path, replacement)
        if actual_sha != mutation["replacement_run_record_sha256"]:
            raise KernelConflict(
                "prepared run-state mutation cannot reconcile an unknown Run Record"
            )
        store.commit_run_state_mutation(mutation["mutation_id"])

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
        store = self._require_control_store()
        owner = store.binding_for_path(candidate)
        if owner is None and not candidate.exists():
            return candidate
        if owner is not None and owner["run_id"] == probe.run_id:
            return candidate
        collision_suffix = f"_r{probe.run_id[:8]}"
        collision_name = output_name(
            original_title=probe.original_title,
            timestamp=timestamp,
            adapter_id="fixture",
            item_id=probe.canonical_item_id,
            max_units=self.scaffold["max_output_component_utf16_units"],
            collision_suffix=collision_suffix,
        )
        collision = self.workspace_root / collision_name
        owner = store.binding_for_path(collision)
        if owner is not None and owner["run_id"] == probe.run_id:
            return collision
        if owner is not None or collision.exists():
            raise KernelConflict(
                "same-second collision-safe output path is already occupied",
                data={"candidate_output_path": str(collision)},
            )
        return collision

    def _require_control_store(self) -> ControlStore:
        if self.control_store is None:
            raise ControlStoreUnavailable(
                "Control Store is absent; Bootstrap must initialize it explicitly"
            )
        return self.control_store

    def _preflight_control_store(self) -> ControlStore:
        store = self._require_control_store()
        store.check()
        return store

    @staticmethod
    def _artifact_plan(run_id: str) -> dict[str, Any]:
        return {
            "schema_name": "artifact-plan",
            "schema_version": "1.0.0",
            "kernel_version": "2.0.0",
            "run_id": run_id,
            "artifacts": [
                {
                    "logical_id": binding.logical_id,
                    "path": binding.path,
                    "schema_name": binding.schema_name,
                    "generator": binding.generator,
                    "earliest_checkpoint": binding.earliest_checkpoint,
                }
                for binding in ARTIFACT_PLAN_BINDINGS
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
            "canonical_platform": "fixture",
            "canonical_item_id": probe.canonical_item_id,
            "source_identity": probe.fixture_manifest_sha256,
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
        run_record_sha = sha256_file(record_path)
        store = self._require_control_store()
        intent = store.intent_for_run(record["run_id"])
        if intent is None:
            raise ArtifactDrift(
                "Run Record has no immutable initialization intent",
                data={
                    "run_dir": str(run_dir),
                    "drifted_paths": ["workflow/run.json"],
                },
            )
        drift = self._identity_binding_drift(
            run_dir,
            intent,
            record,
            run_record_sha,
            expected_current_sha=store.current_run_record_sha(record["run_id"]),
        )
        manifest_path = run_dir / "source/manifest.json"
        expected_manifest_sha = record["artifact_generations"]["source_manifest"]["sha256"]
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

    def _identity_binding_drift(
        self,
        run_dir: Path,
        intent: Any,
        record: dict[str, Any],
        run_record_sha: str,
        expected_current_sha: str | None = None,
    ) -> list[str]:
        drift: list[str] = []
        expected_fields = (
            "expected_run_record_sha256",
            "canonical_platform",
            "canonical_item_id",
            "source_identity",
            "source_manifest_sha256",
        )
        if any(intent[field] is None for field in expected_fields):
            drift.append("control-store/initialization-intent")
            return drift
        expected_sha = expected_current_sha or intent["expected_run_record_sha256"]
        if expected_sha != run_record_sha:
            drift.append("workflow/run.json")
        if (
            expected_current_sha is None
            and intent["run_record_sha256"] is not None
            and intent["run_record_sha256"] != run_record_sha
        ):
            drift.append("workflow/run.json")
        if (
            record["run_id"] != intent["run_id"]
            or record["initialization_intent_id"] != intent["intent_id"]
            or Path(record["output_path"]).resolve() != Path(intent["output_path"]).resolve()
            or record["canonical_platform"] != intent["canonical_platform"]
            or record["canonical_item_id"] != intent["canonical_item_id"]
            or record["source_identity"] != intent["source_identity"]
        ):
            drift.append("workflow/run.json")

        manifest_path = run_dir / "source/manifest.json"
        try:
            manifest = read_json(manifest_path)
            self.contracts.validate("source-manifest", manifest)
            manifest_sha = sha256_file(manifest_path)
        except (ContractError, OSError, ValueError):
            manifest = None
            manifest_sha = None
            drift.append("source/manifest.json")
        if manifest is not None and (
            manifest_sha != intent["source_manifest_sha256"]
            or record["artifact_generations"]["source_manifest"]["sha256"]
            != manifest_sha
            or manifest["run_id"] != intent["run_id"]
            or manifest["adapter_id"] != intent["canonical_platform"]
            or manifest["canonical_item_id"] != intent["canonical_item_id"]
            or manifest["fixture_manifest_sha256"] != intent["source_identity"]
        ):
            drift.append("source/manifest.json")

        bootstrap_path = run_dir / "待删除/bootstrap/probe.json"
        try:
            bootstrap = read_json(bootstrap_path)
            self.contracts.validate("bootstrap-record", bootstrap)
        except (ContractError, OSError, ValueError):
            bootstrap = None
            drift.append("待删除/bootstrap/probe.json")
        if bootstrap is not None and (
            bootstrap["run_id"] != intent["run_id"]
            or bootstrap["adapter_id"] != intent["canonical_platform"]
            or bootstrap["canonical_item_id"] != intent["canonical_item_id"]
            or bootstrap["fixture_manifest_sha256"] != intent["source_identity"]
            or bootstrap["request_id"] != record["request_id"]
            or bootstrap["original_title"] != record["original_title"]
            or bootstrap["task_start"] != record["task_start"]
        ):
            drift.append("待删除/bootstrap/probe.json")
        return sorted(set(drift))

    @staticmethod
    def _inject(selected: str | None, current: str) -> None:
        if selected == current:
            raise InitializationFault(current)
