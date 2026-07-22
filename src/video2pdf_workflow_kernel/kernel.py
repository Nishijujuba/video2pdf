from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
from typing import Any, Callable, Mapping
from urllib.parse import parse_qsl, urlsplit

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
    PathBudgetError,
)
from .models import (
    BootstrapProbeResult,
    DeterministicLocatorRequest,
    ProductionBootstrapResult,
    ProductionInitializationResult,
    ReconcileResult,
    TraceResult,
)
from .scaffold import (
    create_scaffold,
    load_scaffold,
    max_reserved_path_units,
    output_component_budget,
    output_name,
    validate_path_budget,
)
from .utils import (
    canonical_json_bytes,
    read_json,
    require_contained_path,
    sha256_bytes,
    sha256_file,
    write_json_atomic,
)


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
_BILIBILI_ITEM_ID = re.compile(r"^BV[0-9A-Za-z]{10}$")
_YOUTUBE_ITEM_ID = re.compile(r"^[0-9A-Za-z_-]{11}$")


def _deterministic_production_locator(
    platform: str, request: DeterministicLocatorRequest
) -> tuple[str, str, str]:
    if not isinstance(request, DeterministicLocatorRequest):
        raise ContractError(
            "deterministic production Bootstrap requires a locator request"
        )
    title = request.original_title
    if (
        not isinstance(title, str)
        or not title
        or title.strip() != title
        or len(title) > 2000
        or any(ord(character) < 32 for character in title)
    ):
        raise ContractError("deterministic production title is invalid")
    raw_url = request.source_url
    if (
        not isinstance(raw_url, str)
        or not raw_url
        or raw_url.strip() != raw_url
    ):
        raise ContractError("deterministic production locator is invalid")
    parsed = urlsplit(raw_url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ContractError("deterministic production locator port is invalid") from exc
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.fragment
    ):
        raise ContractError("deterministic production locator is noncanonical")
    host = (parsed.hostname or "").casefold()
    query = parse_qsl(parsed.query, keep_blank_values=True)
    if platform == "bilibili":
        if host not in {"bilibili.com", "www.bilibili.com"}:
            raise ContractError("deterministic Bilibili locator host is invalid")
        parts = [part for part in parsed.path.split("/") if part]
        if (
            len(parts) != 2
            or parts[0] != "video"
            or _BILIBILI_ITEM_ID.fullmatch(parts[1]) is None
        ):
            raise ContractError("deterministic Bilibili locator path is invalid")
        selector = request.explicit_item_selector
        if (
            not isinstance(selector, str)
            or re.fullmatch(r"p[1-9][0-9]*", selector) is None
        ):
            raise ContractError(
                "deterministic Bilibili locator requires an explicit part"
            )
        part = int(selector[1:])
        if query and query != [("p", str(part))]:
            raise ContractError("deterministic Bilibili locator query is ambiguous")
        item_id = f"{parts[1]}:p{part}"
        canonical_url = f"https://www.bilibili.com/video/{parts[1]}/"
    elif platform == "youtube":
        if request.explicit_item_selector is not None:
            raise ContractError("deterministic YouTube locator has an item selector")
        if host not in {"youtube.com", "www.youtube.com"} or parsed.path != "/watch":
            raise ContractError("deterministic YouTube locator path is invalid")
        if len(query) != 1 or query[0][0] != "v":
            raise ContractError("deterministic YouTube locator query is ambiguous")
        item_id = query[0][1]
        if _YOUTUBE_ITEM_ID.fullmatch(item_id) is None:
            raise ContractError("deterministic YouTube item identity is invalid")
        canonical_url = f"https://www.youtube.com/watch?v={item_id}"
    else:
        raise ContractError("deterministic production platform is unsupported")
    if raw_url != canonical_url:
        raise ContractError("deterministic production locator is noncanonical")
    return item_id, canonical_url, title


class VideoWorkflowKernel:
    """Deep Kernel interface; CLI and workflow adapters delegate here."""

    def __init__(
        self,
        workspace_root: Path,
        *,
        resource_provider_verifiers: Mapping[str, Callable[..., str]] | None = None,
        local_process_inspector: Callable[..., str | None] | None = None,
        _control_store_recovery_token: str | None = None,
    ) -> None:
        provider_verifiers = dict(resource_provider_verifiers or {})
        if any(
            not isinstance(provider, str)
            or not provider.strip()
            or not callable(verifier)
            for provider, verifier in provider_verifiers.items()
        ):
            raise ContractError(
                "Resource provider verifier registry is invalid"
            )
        if local_process_inspector is not None and not callable(
            local_process_inspector
        ):
            raise ContractError("local process inspector must be callable")
        self._resource_provider_verifiers = provider_verifiers
        self._local_process_inspector = local_process_inspector
        self.project_root = Path(__file__).resolve().parents[2]
        self.workspace_root = workspace_root.resolve()
        self.contracts = ContractRegistry(self.project_root)
        self.contracts.check()
        self.scaffold = load_scaffold(self.project_root, self.contracts)
        if ControlStore.identity_evidence_exists(self.workspace_root):
            self.control_store: ControlStore | None = ControlStore(
                self.workspace_root,
                self.contracts,
                recovery_operation_token=_control_store_recovery_token,
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

    def bootstrap_production_source(
        self,
        *,
        adapter: Any,
        request: Any,
        runner: Any,
        task_start: str,
        request_id: str,
        provider_kind: str,
        requested_source_acquisition_mode: str = "fresh_download",
        resource_admission: dict[str, Any] | None = None,
    ) -> ProductionBootstrapResult:
        """Persist a deterministic locator or an offline recorded Bootstrap probe."""

        from .adapters import PlatformAdapter, PlatformAdapterError
        from .source_acquisition import derive_source_identity, record_source_blocker

        if not isinstance(adapter, PlatformAdapter):
            raise ContractError("production Source Adapter does not implement PlatformAdapter")
        if provider_kind not in {"deterministic_locator", "recorded_fixture"}:
            raise ContractError("fresh production Bootstrap provider kind is unsupported")
        if requested_source_acquisition_mode != "fresh_download":
            raise ContractError("production platform probe requires fresh_download mode")
        try:
            parsed_start = datetime.fromisoformat(task_start)
        except ValueError as exc:
            raise ContractError(f"task_start must be ISO 8601: {task_start}") from exc
        if parsed_start.tzinfo is None:
            raise ContractError("task_start must include a timezone offset")
        if not request_id:
            raise ContractError("production Bootstrap request identity is required")
        if resource_admission is not None:
            raise ContractError(
                "production Bootstrap cannot claim a Run-bound Resource Lease"
            )
        locator = (
            _deterministic_production_locator(adapter.canonical_platform, request)
            if provider_kind == "deterministic_locator"
            else None
        )
        if self.control_store is None:
            self.control_store = ControlStore.initialize(
                self.workspace_root, self.contracts
            )
        self.control_store.check()
        canonical_platform = adapter.canonical_platform
        if provider_kind == "deterministic_locator":
            assert locator is not None
            canonical_item_id, canonical_url, original_title = locator
            availability = {
                "status": "pending",
                "duration_seconds": None,
                "chapter_count": None,
                "subtitle_languages": [],
                "media_format_classes": [],
            }
            locator_evidence = {
                "canonical_platform": canonical_platform,
                "canonical_item_id": canonical_item_id,
                "canonical_url": canonical_url,
                "original_title": original_title,
                "explicit_item_selector": request.explicit_item_selector,
            }
            command_argv_redacted: list[str] = []
            authentication_classification = "not_applicable"
            normalized_result_sha256 = sha256_bytes(
                canonical_json_bytes(locator_evidence)
            )
        else:
            try:
                probe = adapter.probe(request, runner=runner)
            except PlatformAdapterError as error:
                if error.blocker_kind == "user_input":
                    error.data["source_blocker"] = record_source_blocker(
                        self, adapter.canonical_platform, error
                    )
                raise
            if (
                probe.adapter_id != adapter.adapter_id
                or probe.canonical_platform != canonical_platform
            ):
                raise KernelConflict(
                    "production Source Adapter changed its platform identity"
                )
            canonical_item_id = probe.canonical_item_id
            canonical_url = probe.canonical_url
            original_title = probe.original_title
            format_classes: set[str] = set()
            for media_format in probe.media_formats:
                video = str(media_format.get("vcodec", "none")) != "none"
                audio = str(media_format.get("acodec", "none")) != "none"
                if video and audio:
                    format_classes.add("combined")
                elif video:
                    format_classes.add("video_only")
                elif audio:
                    format_classes.add("audio_only")
            if not format_classes:
                raise ContractError("production Bootstrap found no usable media formats")
            if not probe.command_evidence:
                raise ContractError("production Bootstrap lacks provider command evidence")
            availability = {
                "duration_seconds": probe.duration_seconds,
                "chapter_count": int(
                    probe.platform_revision.get("chapter_count") or 0
                ),
                "subtitle_languages": sorted(
                    {track.normalized_language for track in probe.subtitle_tracks}
                ),
                "media_format_classes": sorted(format_classes),
            }
            command_argv_redacted = list(probe.command_evidence[-1].argv)
            authentication_classification = probe.authentication_classification
            normalized_result_sha256 = sha256_file(
                probe.normalized_metadata_path
            )
        source_identity = derive_source_identity(
            canonical_platform, canonical_item_id
        )
        run_id = hashlib.sha256(
            "\0".join(
                (
                    canonical_platform,
                    canonical_item_id,
                    task_start,
                    request_id,
                )
            ).encode("utf-8")
        ).hexdigest()[:32]
        record = {
            "schema_name": "bootstrap-record",
            "schema_version": "2.0.0",
            "kernel_version": "2.0.0",
            "run_id": run_id,
            "request_id": request_id,
            "task_start": task_start,
            "requested_source_acquisition_mode": requested_source_acquisition_mode,
            "adapter": {
                "id": canonical_platform,
                "contract_version": "1.0.0",
                "canonical_platform": canonical_platform,
            },
            "source_request": {
                "kind": "fresh_download",
                "canonical_locator": canonical_url,
            },
            "canonical_platform": canonical_platform,
            "canonical_item_id": canonical_item_id,
            "source_identity_scheme": "canonical-platform-item-v1",
            "source_identity": source_identity,
            "original_title": original_title,
            "availability": availability,
            "probe_execution": {
                "provider_kind": provider_kind,
                "command_argv_redacted": command_argv_redacted,
                "authentication_classification": authentication_classification,
                "normalized_result_sha256": normalized_result_sha256,
                "resource_admission": None,
            },
            "status": "probe_complete",
        }
        self.contracts.validate("bootstrap-record", record)
        record_dir = self.bootstrap_root / run_id
        record_dir.mkdir(parents=True, exist_ok=True)
        record_path = record_dir / "probe.json"
        if record_path.exists():
            if read_json(record_path) != record:
                raise KernelConflict("production Bootstrap identity changed on replay")
        else:
            write_json_atomic(record_path, record)
        return ProductionBootstrapResult(
            run_id=run_id,
            request_id=request_id,
            record_path=record_path,
            original_title=original_title,
            task_start=task_start,
            canonical_platform=canonical_platform,
            canonical_item_id=canonical_item_id,
            source_identity=source_identity,
        )

    def bootstrap_verified_import(
        self,
        *,
        verified: Any,
        task_start: str,
        request_id: str,
    ) -> ProductionBootstrapResult:
        """Persist the receiving Run identity for an explicit verified import."""

        from .verified_import import VerifiedSourcePackage

        if not isinstance(verified, VerifiedSourcePackage):
            raise ContractError("Verified Import package authority is invalid")
        try:
            parsed_start = datetime.fromisoformat(task_start)
        except ValueError as exc:
            raise ContractError(f"task_start must be ISO 8601: {task_start}") from exc
        if parsed_start.tzinfo is None:
            raise ContractError("task_start must include a timezone offset")
        if not isinstance(request_id, str) or not request_id:
            raise ContractError("Verified Import request identity is required")
        manifest = verified.manifest
        canonical_platform = manifest["canonical_platform"]
        canonical_item_id = manifest["canonical_item_id"]
        source_identity = manifest["source_identity"]
        run_id = hashlib.sha256(
            "\0".join(
                (
                    canonical_platform,
                    canonical_item_id,
                    task_start,
                    request_id,
                )
            ).encode("utf-8")
        ).hexdigest()[:32]
        if run_id == verified.prior_run_id:
            raise KernelConflict(
                "Verified Import requires a distinct receiving Run identity"
            )
        media_format_classes: set[str] = set()
        for artifact in manifest["artifacts"]:
            if artifact["role"] not in {"video", "audio"}:
                continue
            streams = set(artifact["technical_probe"]["stream_types"])
            if {"video", "audio"}.issubset(streams):
                media_format_classes.add("combined")
            elif "video" in streams:
                media_format_classes.add("video_only")
            elif "audio" in streams:
                media_format_classes.add("audio_only")
        if not media_format_classes:
            raise ContractError("Verified Import has no usable media format class")
        record = {
            "schema_name": "bootstrap-record",
            "schema_version": "2.0.0",
            "kernel_version": "2.0.0",
            "run_id": run_id,
            "request_id": request_id,
            "task_start": task_start,
            "requested_source_acquisition_mode": "verified_import",
            "adapter": {
                "id": canonical_platform,
                "contract_version": "1.0.0",
                "canonical_platform": canonical_platform,
            },
            "source_request": {
                "kind": "verified_import",
                "prior_output_path": str(verified.prior_run_dir),
                "prior_run_id": verified.prior_run_id,
                "prior_source_manifest_sha256": verified.manifest_sha256,
            },
            "canonical_platform": canonical_platform,
            "canonical_item_id": canonical_item_id,
            "source_identity_scheme": "canonical-platform-item-v1",
            "source_identity": source_identity,
            "original_title": manifest["original_title"],
            "availability": {
                "duration_seconds": manifest["technical_validation"][
                    "duration_seconds"
                ],
                "chapter_count": 0,
                "subtitle_languages": manifest["technical_validation"][
                    "subtitle_languages"
                ],
                "media_format_classes": sorted(media_format_classes),
            },
            "probe_execution": {
                "provider_kind": "verified_import",
                "command_argv_redacted": list(verified.validation_command.argv),
                "authentication_classification": "not_applicable",
                "normalized_result_sha256": sha256_bytes(
                    canonical_json_bytes(verified.import_binding["validation"])
                ),
                "resource_admission": None,
            },
            "status": "probe_complete",
        }
        self.contracts.validate("bootstrap-record", record)
        if self.control_store is None:
            self.control_store = ControlStore.initialize(
                self.workspace_root, self.contracts
            )
        self.control_store.check()
        record_dir = self.bootstrap_root / run_id
        record_dir.mkdir(parents=True, exist_ok=True)
        record_path = record_dir / "probe.json"
        if record_path.exists():
            if read_json(record_path) != record:
                raise KernelConflict("Verified Import Bootstrap identity changed on replay")
        else:
            write_json_atomic(record_path, record)
        return ProductionBootstrapResult(
            run_id=run_id,
            request_id=request_id,
            record_path=record_path,
            original_title=manifest["original_title"],
            task_start=task_start,
            canonical_platform=canonical_platform,
            canonical_item_id=canonical_item_id,
            source_identity=source_identity,
        )

    def initialize_production_source(
        self,
        probe: ProductionBootstrapResult,
        *,
        fault_point: str | None = None,
    ) -> ProductionInitializationResult:
        """Commit a production Run at source_acquisition/pending without a Manifest."""

        if fault_point is not None and fault_point not in FAULT_POINTS:
            raise ContractError(f"unknown initialization fault point: {fault_point}")
        store = self._preflight_control_store()
        bootstrap = read_json(probe.record_path)
        self.contracts.validate("bootstrap-record", bootstrap)
        expected_caller = {
            "run_id": probe.run_id,
            "request_id": probe.request_id,
            "original_title": probe.original_title,
            "task_start": probe.task_start,
            "canonical_platform": probe.canonical_platform,
            "canonical_item_id": probe.canonical_item_id,
            "source_identity": probe.source_identity,
        }
        actual = {
            **{key: bootstrap[key] for key in ("run_id", "request_id", "original_title", "task_start", "canonical_item_id", "source_identity")},
            "canonical_platform": bootstrap["adapter"]["canonical_platform"],
        }
        if expected_caller != actual:
            raise KernelConflict("production Bootstrap caller binding drifted")
        existing = store.binding_for_run(probe.run_id)
        if existing is not None:
            intent = store.intent_for_run(probe.run_id)
            if intent is None:
                raise KernelConflict("production Run binding lacks initialization intent")
            if intent["state"] != "COMMITTED":
                self.reconcile_initialization(probe.run_id)
            run_dir = Path(existing["output_path"])
            self._verify_production_run_identity(run_dir)
            return ProductionInitializationResult(probe.run_id, run_dir)
        output_path = self._resolve_production_output_path(probe)
        validate_path_budget(output_path, self.scaffold)
        intent_id = hashlib.sha256(
            f"initialize-production\0{probe.run_id}\0{output_path}".encode("utf-8")
        ).hexdigest()[:32]
        staging_path = self.initialization_root / probe.run_id / "candidate"
        store.prepare_initialization(
            run_id=probe.run_id,
            output_path=output_path,
            intent_id=intent_id,
            staging_path=staging_path,
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
        bootstrap_path = staging_path / "待删除/bootstrap/probe.json"
        bootstrap_path.write_bytes(probe.record_path.read_bytes())
        bootstrap_sha = sha256_file(bootstrap_path)
        self._inject(fault_point, "after_bootstrap_evidence_staged")
        artifact_plan = self._production_artifact_plan(probe.run_id)
        self.contracts.validate("artifact-plan", artifact_plan)
        write_json_atomic(staging_path / "workflow/artifact-plan.json", artifact_plan)
        run_record = self._production_run_record(
            probe=probe,
            output_path=output_path,
            intent_id=intent_id,
            bootstrap_sha=bootstrap_sha,
            source_acquisition_mode=bootstrap[
                "requested_source_acquisition_mode"
            ],
        )
        self.contracts.validate_run_record(run_record)
        prepared_path = staging_path / "待删除/bootstrap/prepared-run.json"
        expected_run_sha = write_json_atomic(prepared_path, run_record)
        store.bind_publication_expectations(
            intent_id,
            expected_run_record_sha256=expected_run_sha,
            canonical_platform=probe.canonical_platform,
            canonical_item_id=probe.canonical_item_id,
            source_identity=probe.source_identity,
            source_manifest_sha256=None,
        )
        self._inject(fault_point, "after_contracts_written")
        if output_path.exists():
            raise KernelConflict("production output path appeared during initialization")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging_path, output_path)
        self._inject(fault_point, "after_output_dir_publish")
        store.transition_intent(
            intent_id, expected_state="PREPARED", new_state="PUBLISHED"
        )
        run_sha = write_json_atomic(output_path / "workflow/run.json", run_record)
        self._inject(fault_point, "after_run_record_commit_marker")
        store.transition_intent(
            intent_id,
            expected_state="PUBLISHED",
            new_state="RECORD_COMMITTED",
            run_record_sha256=run_sha,
        )
        self._inject(fault_point, "before_intent_commit")
        store.transition_intent(
            intent_id,
            expected_state="RECORD_COMMITTED",
            new_state="COMMITTED",
            run_record_sha256=run_sha,
        )
        self._inject(fault_point, "after_intent_commit")
        self._verify_current_source(output_path)
        return ProductionInitializationResult(probe.run_id, output_path)

    def import_verified_source(
        self,
        *,
        prior_run_dir: Path,
        task_start: str,
        request_id: str,
    ) -> Any:
        """Validate, copy, and publish one explicit v2 Verified Source Import."""

        from .source_candidates import (
            SourceProviderBinding,
            ToolVersion,
            materialize_verified_import_candidates,
        )
        from .verified_import import inspect_current_source_package

        prior_run_dir = Path(os.path.abspath(prior_run_dir))
        prior_kernel = VideoWorkflowKernel(prior_run_dir.parent)
        validation_command_argv = (
            "scripts/video_workflow.py",
            "source-import",
            "--workspace-root",
            str(self.workspace_root.resolve()),
            "--prior-run-dir",
            str(prior_run_dir),
            "--task-start",
            task_start,
            "--request-id",
            request_id,
        )
        verified = inspect_current_source_package(
            prior_run_dir,
            contracts=self.contracts,
            validation_command_argv=validation_command_argv,
        )
        prior_kernel.reconcile_run(prior_run_dir)
        reconciled = inspect_current_source_package(
            prior_run_dir,
            contracts=self.contracts,
            validation_command_argv=validation_command_argv,
        )
        if (
            reconciled.manifest_sha256 != verified.manifest_sha256
            or reconciled.import_binding != verified.import_binding
        ):
            raise ArtifactDrift(
                "Verified Import prior Source changed during current-state validation"
            )
        verified = reconciled
        probe = self.bootstrap_verified_import(
            verified=verified,
            task_start=task_start,
            request_id=request_id,
        )
        initialized = self.initialize_production_source(probe)
        record = read_json(initialized.run_dir / "workflow/run.json")
        if record["source_state"] == "ready":
            return self.finalize_production_source(
                initialized.run_dir,
                published_at=task_start,
            )
        if (
            record["source_state"] != "pending"
            or record["source_acquisition_mode"] != "verified_import"
            or record["requested_source_acquisition_mode"] != "verified_import"
        ):
            raise KernelConflict(
                "Verified Import receiving Run has incompatible Source state"
            )
        acquisition_id = hashlib.sha256(
            canonical_json_bytes(
                {
                    "operation": "verified-import-candidates-v1",
                    "run_id": record["run_id"],
                    "source_epoch": record["source_epoch"],
                    "prior_source_manifest_sha256": verified.manifest_sha256,
                }
            )
        ).hexdigest()[:32]
        materialize_verified_import_candidates(
            initialized.run_dir,
            run_id=record["run_id"],
            source_epoch=record["source_epoch"],
            acquisition_id=acquisition_id,
            prior_run_dir=verified.prior_run_dir,
            prior_manifest=verified.manifest,
            provider=SourceProviderBinding(
                kind="verified_import",
                recording_sha256=None,
                tool_versions=(ToolVersion("video2pdf-workflow-kernel", "2.0.0"),),
            ),
            policy=verified.policy,
            import_binding=verified.import_binding,
            validation_command=verified.validation_command,
            contracts=self.contracts,
        )
        return self.finalize_production_source(
            initialized.run_dir,
            published_at=task_start,
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
        self.contracts.validate_run_record(run_record)
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
            self.contracts.validate_run_record(run_record)
            run_record_sha = sha256_file(prepared_path)
        else:
            run_record = read_json(run_path)
            self.contracts.validate_run_record(run_record)
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

    def prepare_source_acquisition_task(
        self,
        run_dir: Path,
        *,
        logical_task_key: str,
        prepared_at: str,
        required_resources: tuple[str, ...] | None = ("codex_semantic",),
        batch_id: str | None = None,
        fault_point: str | None = None,
    ) -> Any:
        from .task_execution import TaskExecution

        return TaskExecution(self).prepare_source_acquisition_task(
            run_dir,
            logical_task_key=logical_task_key,
            prepared_at=prepared_at,
            required_resources=required_resources,
            batch_id=batch_id,
            fault_point=fault_point,
        )

    def prepare_production_source_task(
        self,
        run_dir: Path,
        *,
        task_stage: str,
        logical_task_key: str,
        prepared_at: str,
        whisper_audio_candidate: dict[str, Any] | None = None,
        fault_point: str | None = None,
    ) -> Any:
        from .task_execution import TaskExecution

        return TaskExecution(self).prepare_production_source_task(
            run_dir,
            task_stage=task_stage,
            logical_task_key=logical_task_key,
            prepared_at=prepared_at,
            whisper_audio_candidate=whisper_audio_candidate,
            fault_point=fault_point,
        )

    def derive_production_source_task_id(
        self,
        run_dir: Path,
        *,
        task_stage: str,
        logical_task_key: str,
    ) -> str:
        from .task_execution import TaskExecution

        return TaskExecution(self).derive_production_source_task_id(
            run_dir,
            task_stage=task_stage,
            logical_task_key=logical_task_key,
        )

    def claim_task(
        self,
        run_dir: Path,
        task_id: str,
        *,
        coordinator_session_id: str,
        worker_id: str,
        fault_point: str | None = None,
    ) -> Any:
        from .task_execution import TaskExecution

        return TaskExecution(self).claim_task(
            run_dir,
            task_id,
            coordinator_session_id=coordinator_session_id,
            worker_id=worker_id,
            fault_point=fault_point,
        )

    def reclaim_task(self, run_dir: Path, **kwargs: Any) -> Any:
        from .task_execution import TaskExecution

        return TaskExecution(self).reclaim_task(run_dir, **kwargs)

    def resource_status(self, task_id: str, attempt_id: str) -> Any:
        from .resource_admission import ResourceAdmission

        return ResourceAdmission(self).status(task_id, attempt_id)

    def backup_control_store(
        self,
        backup_dir: Path,
        *,
        backup_id: str,
        coordinator_session_id: str,
        created_at: str,
    ) -> dict[str, Any]:
        from .control_store_recovery import ControlStoreRecovery

        store = self._preflight_control_store()
        return ControlStoreRecovery(
            self.workspace_root,
            project_root=self.project_root,
        ).create_backup(
            store,
            backup_dir,
            backup_id=backup_id,
            coordinator_session_id=coordinator_session_id,
            created_at=created_at,
        )

    def resource_scheduler_status(self) -> dict[str, Any]:
        from .resource_admission import ResourceAdmission

        return ResourceAdmission(self).scheduler_status()

    def resource_capacity_status(self) -> dict[str, Any]:
        from .resource_admission import ResourceAdmission

        return ResourceAdmission(self).capacity_status()

    def activate_resource_configuration(
        self, configuration: dict[str, Any]
    ) -> dict[str, Any]:
        from .resource_admission import ResourceAdmission

        return ResourceAdmission(self).activate_configuration(configuration)

    def set_resource_circuit_breaker(
        self,
        resource_class: str,
        *,
        state: str,
        reason: str,
        platform: str | None = None,
    ) -> dict[str, Any]:
        from .resource_admission import ResourceAdmission

        return ResourceAdmission(self).set_circuit_breaker(
            resource_class,
            state=state,
            reason=reason,
            platform=platform,
        )

    def resource_circuit_breaker_status(self) -> list[dict[str, Any]]:
        from .resource_admission import ResourceAdmission

        return ResourceAdmission(self).circuit_breaker_status()

    def resource_reconcile(
        self,
        *,
        current_coordinator_session_id: str,
        lost_coordinator_session_ids: tuple[str, ...],
    ) -> dict[str, Any]:
        from .resource_recovery import ResourceRecovery

        return ResourceRecovery(
            self,
            provider_verifiers=self._resource_provider_verifiers,
            local_process_inspector=self._local_process_inspector,
        ).reconcile(
            current_coordinator_session_id=current_coordinator_session_id,
            lost_coordinator_session_ids=lost_coordinator_session_ids,
        )

    def resource_resolve(
        self,
        lease_id: str,
        attempt_id: str,
        expected_claim_generation: int,
        *,
        resolution_evidence: dict[str, Any],
    ) -> Any:
        from .resource_recovery import ResourceRecovery

        return ResourceRecovery(
            self,
            provider_verifiers=self._resource_provider_verifiers,
            local_process_inspector=self._local_process_inspector,
        ).resolve(
            lease_id,
            attempt_id,
            expected_claim_generation,
            resolution_evidence=resolution_evidence,
        )

    def task_claim_status(self, task_id: str) -> dict[str, Any] | None:
        store = self._preflight_control_store()
        row = store.task_claim_for_task(task_id)
        if row is None:
            return None
        return {
            "task_id": str(row["task_id"]),
            "attempt_id": str(row["attempt_id"]),
            "claim_generation": int(row["claim_generation"]),
            "state": str(row["state"]).lower(),
        }

    def release_resource_lease(
        self,
        attempt_id: str,
        claim_generation: int,
        launch_token: str,
        *,
        terminal_evidence: dict[str, Any],
    ) -> Any:
        from .resource_admission import ResourceAdmission

        return ResourceAdmission(self).release_resource_lease(
            attempt_id,
            claim_generation,
            launch_token,
            terminal_evidence=terminal_evidence,
        )

    def launch_admitted_task(
        self,
        attempt_id: str,
        claim_generation: int,
        required_resources: tuple[str, ...],
        launcher: Any,
        *,
        fault_point: str | None = None,
    ) -> Any:
        from .resource_admission import ResourceAdmission

        return ResourceAdmission(self).launch_admitted_task(
            attempt_id,
            claim_generation,
            required_resources,
            launcher,
            fault_point=fault_point,
        )

    def complete_task(self, run_dir: Path, **kwargs: Any) -> Any:
        from .task_execution import TaskExecution

        return TaskExecution(self).complete_task(run_dir, **kwargs)

    def promote_task(self, run_dir: Path, **kwargs: Any) -> Any:
        from .task_execution import TaskExecution

        return TaskExecution(self).promote_task(run_dir, **kwargs)

    def source_reopen(
        self,
        run_dir: Path,
        *,
        reason: str,
        fault_point: str | None = None,
    ) -> Any:
        from .source_acquisition import (
            SourceReopenControlAuthority,
            SourceReopenSaga,
        )

        run_dir = run_dir.resolve()
        self.reconcile_run(run_dir)
        validated_record = self.require_current_validated_source_package(run_dir)
        authority = SourceReopenControlAuthority(self._preflight_control_store())
        return SourceReopenSaga(
            run_dir,
            contracts=self.contracts,
            authority=authority,
        ).reopen(
            reason=reason,
            validated_record=validated_record,
            fault_point=fault_point,
        )

    def resolve_source_user_input(
        self,
        run_dir: Path,
        *,
        authentication_classification: str,
        credential_evidence: Mapping[str, Any],
        credential_evidence_sha256: str,
        fault_point: str | None = None,
    ) -> dict[str, Any]:
        """Resolve a Run v3 Source blocker after credential revalidation."""

        from .source_acquisition import resolve_source_user_input

        run_dir = run_dir.resolve()
        self.reconcile_run(run_dir)
        return resolve_source_user_input(
            self,
            run_dir,
            authentication_classification=authentication_classification,
            credential_evidence=credential_evidence,
            credential_evidence_sha256=credential_evidence_sha256,
            fault_point=fault_point,
        )

    def source_publication_intent_id(self, run_dir: Path) -> str:
        """Return the deterministic publication identity before package staging."""

        from .source_publication import (
            SourcePublicationControlAuthority,
            SourcePublicationSaga,
        )

        run_dir = run_dir.resolve()
        self.reconcile_run(run_dir)
        authority = SourcePublicationControlAuthority(
            self._preflight_control_store()
        )
        return SourcePublicationSaga(
            run_dir,
            contracts=self.contracts,
            authority=authority,
        ).publication_intent_id()

    def finalize_production_source(
        self,
        run_dir: Path,
        *,
        published_at: str,
        whisper_transcript: Any | None = None,
        fault_point: str | None = None,
    ) -> Any:
        """Materialize and atomically publish the current production Source Package."""

        from .source_package import materialize_source_package
        from .source_publication import (
            SourcePublicationControlAuthority,
            SourcePublicationResult,
            SourcePublicationSaga,
        )

        run_dir = run_dir.resolve()
        store = self._preflight_control_store()
        initial_record = read_json(run_dir / "workflow/run.json")
        self.contracts.validate_run_record(initial_record)
        active_publication = store.active_source_publication(
            initial_record["run_id"]
        )
        resuming_candidate_materialization = (
            active_publication is not None
            and active_publication["state"] == "PREPARED"
            and active_publication["journal_sha256"] is None
        )
        if not resuming_candidate_materialization:
            self.reconcile_run(run_dir)
        record = read_json(run_dir / "workflow/run.json")
        self.contracts.validate_run_record(record)
        if record.get("schema_version") != "3.0.0":
            raise ContractError("production Source finalization requires Run Record v3")
        if record["source_state"] == "ready":
            self._verify_current_source(run_dir)
            manifest = read_json(run_dir / "source/manifest.json")
            journal_path = (
                run_dir
                / "work/source-acquisition/source-publication-journal.json"
            )
            try:
                journal = read_json(journal_path)
                self.contracts.validate("source-publication-journal", journal)
                journal_sha256 = sha256_file(journal_path)
            except (ContractError, OSError, ValueError) as exc:
                raise ArtifactDrift(
                    "ready Source publication journal is invalid",
                    data={
                        "run_dir": str(run_dir),
                        "drifted_paths": [
                            "work/source-acquisition/source-publication-journal.json"
                        ],
                    },
                ) from exc
            publication = store.source_publication_by_id(journal["intent_id"])
            if (
                publication is None
                or publication["state"] != "COMMITTED"
                or publication["run_id"] != record["run_id"]
                or int(publication["source_epoch"]) != record["source_epoch"]
                or publication["journal_sha256"] != journal_sha256
                or publication["replacement_run_record_sha256"]
                != journal["replacement_run_record_sha256"]
                or publication["source_manifest_sha256"]
                != record["artifact_generations"]["source_manifest"]["sha256"]
                or publication["source_manifest_sha256"]
                != journal["replacement_source_manifest_sha256"]
                or publication["source_identity"] != manifest["source_identity"]
                or publication["source_identity"] != journal["source_identity"]
                or publication["source_version"] != manifest["source_version"]
                or publication["source_version"] != journal["source_version"]
            ):
                raise ArtifactDrift(
                    "ready Source lacks its committed publication authority",
                    data={
                        "run_dir": str(run_dir),
                        "drifted_paths": [
                            "work/source-acquisition/source-publication-journal.json"
                        ],
                    },
                )
            return SourcePublicationResult(
                intent_id=str(journal["intent_id"]),
                run_dir=run_dir,
                manifest_path=run_dir / "source/manifest.json",
                manifest_sha256=record["artifact_generations"]["source_manifest"][
                    "sha256"
                ],
                source_identity=manifest["source_identity"],
                source_version=manifest["source_version"],
            )
        authority = SourcePublicationControlAuthority(
            store
        )
        saga = SourcePublicationSaga(
            run_dir,
            contracts=self.contracts,
            authority=authority,
        )
        intent_id = saga.publication_intent_id()
        materialization_published_at = saga.materialization_published_at(
            published_at
        )
        candidate_root = (
            f"work/source-acquisition/publications/{intent_id}/candidate/source"
        )
        fresh = record["source_acquisition_mode"] == "fresh_download"
        package = materialize_source_package(
            run_dir,
            destination_source_root=candidate_root,
            published_at=materialization_published_at,
            decision_skeleton_path=(
                "work/source-acquisition/decision.skeleton.json" if fresh else None
            ),
            judgment_patch_path=(
                "workflow/source-acquisition-judgment-patch.json" if fresh else None
            ),
            whisper_transcript=whisper_transcript,
            contracts=self.contracts,
            prepare_publication=saga.prepare,
        )
        result = saga.publish(package, fault_point=fault_point)
        self._verify_current_source(run_dir)
        return result

    def reconcile_run(
        self, run_dir: Path, *, fault_point: str | None = None
    ) -> ReconcileResult:
        run_dir = run_dir.resolve()
        record_path = run_dir / "workflow/run.json"
        record = read_json(record_path)
        self.contracts.validate_run_record(record)
        return self.reconcile_authority(
            "kernel_run",
            record["run_id"],
            expected_run_dir=run_dir,
            fault_point=fault_point,
        )

    def reconcile_authority(
        self,
        kind: str,
        authority_id: str,
        *,
        expected_run_dir: Path | None = None,
        fault_point: str | None = None,
    ) -> ReconcileResult:
        """Public closed authority dispatcher; Slice 2 registers kernel_run only."""
        if kind != "kernel_run":
            raise ContractError(f"unknown reconciliation authority kind: {kind!r}")
        store = self._preflight_control_store()
        binding = store.binding_for_run(authority_id)
        if binding is None:
            raise KernelConflict(f"kernel_run authority does not exist: {authority_id}")
        run_dir = Path(binding["output_path"]).resolve()
        if expected_run_dir is not None and expected_run_dir.resolve() != run_dir:
            raise KernelConflict("Run wrapper path disagrees with authority dispatcher")
        initialization = store.intent_for_run(authority_id)
        if (
            initialization is not None
            and str(initialization["state"])
            in {"PREPARED", "PUBLISHED", "RECORD_COMMITTED"}
        ):
            self.reconcile_initialization(authority_id)
        return self._reconcile_kernel_run(run_dir, fault_point=fault_point)

    def _reconcile_kernel_run(
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
        self.contracts.validate_run_record(record)
        binding = store.binding_for_run(record["run_id"])
        if binding is None or Path(binding["output_path"]).resolve() != run_dir:
            raise KernelConflict("Run Record and Control Store binding disagree")
        if (
            store.prepared_run_state_mutation(record["run_id"]) is not None
            and store.active_task_promotion(record["run_id"]) is not None
        ):
            raise ControlStoreUnavailable(
                "Run has two non-terminal coordination-record mutation authorities"
            )
        active_publication = (
            store.active_source_publication(record["run_id"])
            if hasattr(store, "active_source_publication")
            else None
        )
        if active_publication is not None:
            from .source_publication import (
                SourcePublicationControlAuthority,
                SourcePublicationSaga,
            )

            SourcePublicationSaga(
                run_dir,
                contracts=self.contracts,
                authority=SourcePublicationControlAuthority(store),
            ).reconcile()
            record = read_json(record_path)
            self.contracts.validate_run_record(record)
        reopen_root = run_dir / "待删除" / "source-reopens"
        if reopen_root.is_dir():
            from .source_acquisition import (
                SourceReopenControlAuthority,
                SourceReopenSaga,
            )

            if any(reopen_root.glob("*/reopen.json")):
                SourceReopenSaga(
                    run_dir,
                    contracts=self.contracts,
                    authority=SourceReopenControlAuthority(store),
                ).reconcile()
        self._resume_prepared_run_state_mutation(store, record["run_id"], record_path)
        from .task_execution import TaskExecution

        task_execution = TaskExecution(self)
        promotion_recovered = task_execution.reconcile_promotion(run_dir)
        record = read_json(record_path)
        self.contracts.validate_run_record(record)
        if record["run_id"] != binding["run_id"]:
            raise KernelConflict("Run Record and Control Store binding disagree")
        authority_sha = store.current_run_record_sha(record["run_id"])
        actual_sha = sha256_file(record_path)
        if authority_sha is None or actual_sha != authority_sha:
            raise ArtifactDrift(
                "Run Record differs from its committed authority predecessor",
                data={
                    "run_dir": str(run_dir),
                    "drifted_paths": ["workflow/run.json"],
                },
            )
        try:
            self._verify_current_source(run_dir)
        except ArtifactDrift:
            if record["checkpoints"]["source_ready"]["status"] == "stale":
                raise
            old_sha = sha256_file(record_path)
            replacement = json.loads(json.dumps(record))
            replacement["coordination_revision"] = record["coordination_revision"] + 1
            if replacement.get("schema_version") == "3.0.0":
                replacement["source_state"] = "stale"
                replacement["source_version"] = None
                replacement["source_blocker"] = None
                replacement["phase"] = "source_acquisition"
                for checkpoint_name, checkpoint in replacement[
                    "checkpoints"
                ].items():
                    if checkpoint_name != "run_initialized":
                        checkpoint["status"] = "stale"
            else:
                for checkpoint in replacement["checkpoints"].values():
                    checkpoint["status"] = "stale"
            if replacement.get("schema_version") in {"2.0.0", "3.0.0"}:
                replacement["last_mutation_intent_id"] = (
                    store.derive_run_state_mutation_id(
                        run_id=record["run_id"],
                        expected_run_revision=record["coordination_revision"],
                        old_run_record_sha256=old_sha,
                    )
                )
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
        task_execution.verify_committed_task_state(run_dir)
        return ReconcileResult(
            record["run_id"],
            run_dir,
            (
                "new_state_complete"
                if promotion_recovered or active_publication is not None
                else "current_state_verified"
            ),
        )

    def _resume_prepared_run_state_mutation(
        self, store: ControlStore, run_id: str, record_path: Path
    ) -> None:
        mutation = store.prepared_run_state_mutation(run_id)
        if mutation is None:
            return
        replacement = json.loads(mutation["replacement_run_record_json"])
        self.contracts.validate_run_record(replacement)
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
        if (
            replacement.get("schema_version") == "3.0.0"
            and replacement.get("source_state") == "pending"
            and "source_credential_resolution_evidence"
            in replacement.get("artifact_generations", {})
        ):
            self._verify_source_credential_resolution_evidence(
                record_path.parent.parent,
                replacement,
                require_current_breaker=True,
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
        name = self._workspace_output_name(
            original_title=probe.original_title,
            timestamp=timestamp,
            adapter_id="fixture",
            item_id=probe.canonical_item_id,
        )
        candidate = self.workspace_root / name
        store = self._require_control_store()
        owner = store.binding_for_path(candidate)
        if owner is None and not candidate.exists():
            return candidate
        if owner is not None and owner["run_id"] == probe.run_id:
            return candidate
        collision_suffix = f"_r{probe.run_id[:8]}"
        collision_name = self._workspace_output_name(
            original_title=probe.original_title,
            timestamp=timestamp,
            adapter_id="fixture",
            item_id=probe.canonical_item_id,
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

    def _resolve_production_output_path(
        self, probe: ProductionBootstrapResult
    ) -> Path:
        parsed = datetime.fromisoformat(probe.task_start)
        timestamp = parsed.strftime("%Y%m%d_%H%M%S")
        name = self._workspace_output_name(
            original_title=probe.original_title,
            timestamp=timestamp,
            adapter_id=probe.canonical_platform,
            item_id=probe.canonical_item_id,
        )
        candidate = self.workspace_root / name
        store = self._require_control_store()
        owner = store.binding_for_path(candidate)
        if owner is None and not candidate.exists():
            return candidate
        if owner is not None and owner["run_id"] == probe.run_id:
            return candidate
        suffix = f"_r{probe.run_id[:8]}"
        collision_name = self._workspace_output_name(
            original_title=probe.original_title,
            timestamp=timestamp,
            adapter_id=probe.canonical_platform,
            item_id=probe.canonical_item_id,
            collision_suffix=suffix,
        )
        collision = self.workspace_root / collision_name
        owner = store.binding_for_path(collision)
        if owner is not None and owner["run_id"] == probe.run_id:
            return collision
        if owner is not None or collision.exists():
            raise KernelConflict("production collision-safe output path is occupied")
        return collision

    def _workspace_output_name(
        self,
        *,
        original_title: str,
        timestamp: str,
        adapter_id: str,
        item_id: str,
        collision_suffix: str = "",
    ) -> str:
        dynamic_budget = output_component_budget(self.workspace_root, self.scaffold)
        try:
            return output_name(
                original_title=original_title,
                timestamp=timestamp,
                adapter_id=adapter_id,
                item_id=item_id,
                max_units=dynamic_budget,
                collision_suffix=collision_suffix,
            )
        except PathBudgetError:
            # Preserve the canonical candidate so validate_path_budget can emit
            # the exact absolute-path evidence when even the identity suffix
            # cannot fit inside the workspace-specific component budget.
            return output_name(
                original_title=original_title,
                timestamp=timestamp,
                adapter_id=adapter_id,
                item_id=item_id,
                max_units=self.scaffold["max_output_component_utf16_units"],
                collision_suffix=collision_suffix,
            )

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
    def _production_artifact_plan(run_id: str) -> dict[str, Any]:
        bindings = (
            ("run_record", "workflow/run.json", "run-record", "3.0.0", "kernel:init-run", (), "run_initialized", "always"),
            ("artifact_plan", "workflow/artifact-plan.json", "artifact-plan", "2.0.0", "kernel:init-run", (), "run_initialized", "always"),
            ("bootstrap_record", "待删除/bootstrap/probe.json", "bootstrap-record", "2.0.0", "kernel:bootstrap", (), "run_initialized", "always"),
            ("scaffold_contract", "workflow/scaffold-contract.json", "scaffold-contract", "1.0.0", "kernel:init-run", (), "run_initialized", "always"),
            ("scaffold_ledger", "workflow/scaffold-ledger.json", "scaffold-ledger", "1.0.0", "kernel:init-run", (), "run_initialized", "always"),
            ("source_candidate_inventory", "work/source-acquisition/candidate-inventory.json", "source-candidate-inventory", "1.0.0", "kernel:source-candidates", (("bootstrap_record", "always"),), "source_candidates_ready", "always"),
            ("source_acquisition_decision_skeleton", "work/source-acquisition/decision.skeleton.json", "source-acquisition-decision-skeleton", "1.0.0", "kernel:source-prepare", (("source_candidate_inventory", "always"),), "source_candidates_ready", "fresh_download"),
            ("source_acquisition_decision", "workflow/source-acquisition-judgment-patch.json", "source-acquisition-judgment-patch", "2.0.0", "task:source-acquisition", (("source_candidate_inventory", "always"), ("source_acquisition_decision_skeleton", "fresh_download")), "source_acquisition_decision_ready", "fresh_download"),
            ("source_transcription", "work/source-acquisition/transcription.srt", "source-transcription-srt", "1.0.0", "task:whisper-transcription", (("source_candidate_inventory", "always"), ("source_acquisition_decision", "fresh_download")), "source_acquisition_decision_ready", "whisper_requested"),
            # Publication journal authority lives in the Control Store. Binding
            # it into the Run-owned plan would create a circular fingerprint:
            # the journal authenticates the replacement Run Record itself.
            ("source_manifest", "source/manifest.json", "source-manifest", "2.0.0", "kernel:source-finalize", (("source_candidate_inventory", "always"), ("source_acquisition_decision", "fresh_download"), ("source_transcription", "whisper_requested")), "source_ready", "always"),
        )
        return {
            "schema_name": "artifact-plan",
            "schema_version": "2.0.0",
            "kernel_version": "2.0.0",
            "run_id": run_id,
            "artifacts": [
                {
                    "logical_id": logical_id,
                    "path": path,
                    "schema_name": schema_name,
                    "schema_version": schema_version,
                    "generator": generator,
                    "dependencies": [
                        {"logical_id": dependency, "when": when}
                        for dependency, when in dependencies
                    ],
                    "earliest_checkpoint": earliest,
                    "condition": condition,
                }
                for (
                    logical_id,
                    path,
                    schema_name,
                    schema_version,
                    generator,
                    dependencies,
                    earliest,
                    condition,
                ) in bindings
            ],
        }

    @staticmethod
    def _production_run_record(
        *,
        probe: ProductionBootstrapResult,
        output_path: Path,
        intent_id: str,
        bootstrap_sha: str,
        source_acquisition_mode: str,
    ) -> dict[str, Any]:
        from .utils import normalize_title

        initialized_checkpoint = {
            "status": "current",
            "artifact_bindings": [
                {
                    "logical_id": "bootstrap_record",
                    "generation": 1,
                    "sha256": bootstrap_sha,
                }
            ],
            "prerequisite_bindings": [],
            "evidence_sha256": bootstrap_sha,
            "completed_at": probe.task_start,
        }
        return {
            "schema_name": "run-record",
            "schema_version": "3.0.0",
            "kernel_version": "2.0.0",
            "scaffold_version": "1.0.0",
            "run_id": probe.run_id,
            "request_id": probe.request_id,
            "platform_adapter": probe.canonical_platform,
            "adapter_contract_version": "1.0.0",
            "canonical_platform": probe.canonical_platform,
            "canonical_item_id": probe.canonical_item_id,
            "source_identity_scheme": "canonical-platform-item-v1",
            "source_identity": probe.source_identity,
            "source_version_scheme": "source-content-v1",
            "source_version": None,
            "original_title": probe.original_title,
            "normalized_title": normalize_title(probe.original_title),
            "task_start": probe.task_start,
            "output_path": str(output_path.resolve()),
            "deliverable_version": 1,
            "version_basis": "source_only",
            "requested_source_acquisition_mode": source_acquisition_mode,
            "source_acquisition_mode": source_acquisition_mode,
            "source_epoch": 1,
            "source_state": "pending",
            "source_blocker": None,
            "phase": "source_acquisition",
            "initialization_intent_id": intent_id,
            "coordination_revision": 1,
            "last_mutation_intent_id": None,
            "artifact_plan": "workflow/artifact-plan.json",
            "artifact_generations": {
                "bootstrap_record": {
                    "path": "待删除/bootstrap/probe.json",
                    "generation": 1,
                    "sha256": bootstrap_sha,
                    "producer": "kernel:bootstrap",
                    "committed_at": probe.task_start,
                    "source_epoch": 0,
                }
            },
            "checkpoint_dependencies": {
                "run_initialized": [],
                "source_candidates_ready": ["run_initialized"],
                "source_acquisition_decision_ready": ["source_candidates_ready"],
                "source_ready": [
                    "source_acquisition_decision_ready"
                    if source_acquisition_mode == "fresh_download"
                    else "source_candidates_ready"
                ],
            },
            "checkpoints": {"run_initialized": initialized_checkpoint},
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

    def _verify_production_run_identity(self, run_dir: Path) -> None:
        record = read_json(run_dir / "workflow/run.json")
        self.contracts.validate_run_record(record)
        if record.get("schema_version") != "3.0.0":
            raise ContractError("production Run identity requires Run Record v3")
        self._verify_production_source_state(
            run_dir,
            record,
            expected_run_record_sha256=None,
        )

    def require_current_validated_source_package(
        self,
        run_dir: Path,
    ) -> dict[str, Any]:
        """Return a Source Package only after full Run, authority, and file validation."""

        run_dir = Path(run_dir).resolve()
        require_contained_path(
            run_dir / "workflow/run.json",
            run_dir,
            purpose="Validated Source Run Record",
            error_type=ArtifactDrift,
            leaf_kind="file",
            require_single_link=True,
        )
        self._verify_current_source(run_dir)
        record = read_json(run_dir / "workflow/run.json")
        self.contracts.validate_run_record(record)
        source_ready = record.get("checkpoints", {}).get("source_ready", {})
        if (
            record.get("source_state") != "ready"
            or re.fullmatch(r"[0-9a-f]{64}", str(record.get("source_version", "")))
            is None
            or source_ready.get("status") != "current"
        ):
            raise ArtifactDrift("Validated Source Package is not current")
        return record

    def production_plan(
        self,
        run_dir: Path,
        *,
        supersede_task_id: str | None = None,
        expected_claim_generation: int | None = None,
    ) -> dict[str, Any]:
        from .content_production import ContentProduction

        return ContentProduction(self).plan(
            run_dir,
            supersede_task_id=supersede_task_id,
            expected_claim_generation=expected_claim_generation,
        )

    def production_advance(
        self,
        run_dir: Path,
        task_id: str,
        attempt_id: str,
        *,
        compile_runtime_policy: dict[str, Any] | None = None,
        fault_point: str | None = None,
    ) -> dict[str, Any]:
        from .content_production import ContentProduction

        return ContentProduction(self).advance(
            run_dir,
            task_id,
            attempt_id,
            compile_runtime_policy=compile_runtime_policy,
            fault_point=fault_point,
        )

    def _verify_current_source(
        self,
        run_dir: Path,
        *,
        expected_run_record_sha256: str | None = None,
    ) -> None:
        record_path = run_dir / "workflow/run.json"
        record = read_json(record_path)
        self.contracts.validate_run_record(record)
        if record.get("schema_version") == "3.0.0":
            self._verify_production_source_state(
                run_dir,
                record,
                expected_run_record_sha256=expected_run_record_sha256,
            )
            return
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
            expected_current_sha=(
                expected_run_record_sha256
                if expected_run_record_sha256 is not None
                else store.current_run_record_sha(record["run_id"])
            ),
        )
        manifest_path = run_dir / "source/manifest.json"
        expected_manifest_sha = record["artifact_generations"]["source_manifest"]["sha256"]
        if manifest_path.is_symlink() or not manifest_path.is_file():
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
            drift.extend(self._source_inventory_drift(run_dir, manifest))
            for artifact in manifest["artifacts"]:
                path = run_dir.joinpath(*artifact["path"].split("/"))
                if (
                    path.is_symlink()
                    or not path.is_file()
                    or sha256_file(path) != artifact["sha256"]
                ):
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

    def _verify_source_credential_resolution_evidence(
        self,
        run_dir: Path,
        record: Mapping[str, Any],
        *,
        require_current_breaker: bool,
    ) -> None:
        generation = record.get("artifact_generations", {}).get(
            "source_credential_resolution_evidence"
        )
        if generation is None:
            return
        relative = str(generation["path"])
        evidence_path = run_dir.joinpath(*PurePosixPath(relative).parts)
        try:
            require_contained_path(
                evidence_path,
                run_dir,
                purpose="bound Source credential resolution evidence",
                error_type=ArtifactDrift,
                leaf_kind="file",
                require_single_link=True,
            )
            if sha256_file(evidence_path) != generation["sha256"]:
                raise ArtifactDrift(
                    "bound Source credential resolution evidence fingerprint drifted"
                )
            evidence = read_json(evidence_path)
            self.contracts.validate(
                "source-credential-resolution-evidence", evidence
            )
        except (OSError, UnicodeError, ValueError, ContractError) as exc:
            raise ArtifactDrift(
                "bound Source credential resolution evidence is invalid",
                data={"drifted_paths": [relative]},
            ) from exc
        if (
            evidence["run_id"] != record["run_id"]
            or evidence["canonical_platform"] != record["canonical_platform"]
            or int(generation["source_epoch"]) != int(evidence["source_epoch"]) + 1
            or int(generation["source_epoch"]) > int(record["source_epoch"])
        ):
            raise ArtifactDrift(
                "bound Source credential resolution evidence identity drifted",
                data={"drifted_paths": [relative]},
            )
        if require_current_breaker:
            breakers = [
                item
                for item in self.resource_circuit_breaker_status()
                if item.get("breaker_key") == evidence["breaker_key"]
            ]
            if (
                len(breakers) != 1
                or breakers[0].get("state") != "closed"
                or breakers[0].get("updated_seq")
                != evidence["breaker_updated_seq"]
                or evidence["resource_class"]
                != f"{record['canonical_platform']}_download"
            ):
                raise ArtifactDrift(
                    "Source credential resolution recovery lost its closed breaker authority",
                    data={"drifted_paths": [relative]},
                )

    def _verify_production_source_state(
        self,
        run_dir: Path,
        record: dict[str, Any],
        *,
        expected_run_record_sha256: str | None,
    ) -> None:
        self._verify_source_credential_resolution_evidence(
            run_dir,
            record,
            require_current_breaker=False,
        )
        record_path = run_dir / "workflow/run.json"
        run_record_sha = sha256_file(record_path)
        store = self._require_control_store()
        intent = store.intent_for_run(record["run_id"])
        if intent is None:
            raise ArtifactDrift("production Run has no initialization authority")
        drift = self._identity_binding_drift(
            run_dir,
            intent,
            record,
            run_record_sha,
            expected_current_sha=(
                expected_run_record_sha256
                if expected_run_record_sha256 is not None
                else store.current_run_record_sha(record["run_id"])
            ),
        )
        generations = record["artifact_generations"]
        current_bindings: set[str] = set()
        for checkpoint in record["checkpoints"].values():
            if checkpoint["status"] != "current":
                continue
            current_bindings.update(
                binding["logical_id"] for binding in checkpoint["artifact_bindings"]
            )
        for logical_id in current_bindings:
            generation = generations[logical_id]
            path = run_dir.joinpath(*PurePosixPath(generation["path"]).parts)
            try:
                require_contained_path(
                    path,
                    run_dir,
                    purpose=f"current {logical_id} Artifact",
                    error_type=ArtifactDrift,
                    leaf_kind="file",
                    require_single_link=True,
                )
            except ArtifactDrift:
                drift.append(generation["path"])
                continue
            if sha256_file(path) != generation["sha256"]:
                drift.append(generation["path"])
        state = record["source_state"]
        manifest_path = run_dir / "source/manifest.json"
        if state == "ready":
            source_generation = generations.get("source_manifest")
            try:
                manifest = read_json(manifest_path)
                self.contracts.validate("source-manifest", manifest)
            except (ContractError, OSError, ValueError):
                manifest = None
                drift.append("source/manifest.json")
            if manifest is not None:
                if (
                    source_generation is None
                    or sha256_file(manifest_path) != source_generation["sha256"]
                    or manifest["run_id"] != record["run_id"]
                    or manifest["source_identity"] != record["source_identity"]
                    or manifest["source_version"] != record["source_version"]
                ):
                    drift.append("source/manifest.json")
                drift.extend(self._source_inventory_drift(run_dir, manifest))
                for artifact in manifest["artifacts"]:
                    path = run_dir.joinpath(*PurePosixPath(artifact["path"]).parts)
                    try:
                        require_contained_path(
                            path,
                            run_dir / "source",
                            purpose="published Source artifact",
                            error_type=ArtifactDrift,
                            leaf_kind="file",
                            require_single_link=True,
                        )
                    except ArtifactDrift:
                        drift.append(artifact["path"])
                        continue
                    if sha256_file(path) != artifact["sha256"]:
                        drift.append(artifact["path"])
        elif state == "stale":
            if manifest_path.exists():
                drift.append("source/manifest.json")
        if drift:
            raise ArtifactDrift(
                "production Source state differs from committed authority",
                data={"run_dir": str(run_dir), "drifted_paths": sorted(set(drift))},
            )

    def _source_inventory_drift(
        self, run_dir: Path, manifest: dict[str, Any]
    ) -> list[str]:
        source_root = run_dir / "source"
        expected_directories = {
            value
            for value in self.scaffold["managed_directories"]
            if value == "source" or value.startswith("source/")
        }
        expected_files = {
            "source/manifest.json",
            *(artifact["path"] for artifact in manifest["artifacts"]),
        }
        drift: set[str] = set()
        if self._is_symlink_or_reparse_point(source_root) or not source_root.is_dir():
            return ["source"]

        actual_directories = {"source"}
        actual_files: set[str] = set()
        pending = [(source_root, "source")]
        while pending:
            directory, relative_directory = pending.pop()
            try:
                entries = list(os.scandir(directory))
            except OSError:
                drift.add(relative_directory)
                continue
            for entry in entries:
                relative = f"{relative_directory}/{entry.name}"
                try:
                    stat_result = entry.stat(follow_symlinks=False)
                except OSError:
                    drift.add(relative)
                    continue
                if entry.is_symlink() or (
                    getattr(stat_result, "st_file_attributes", 0) & 0x400
                ):
                    drift.add(relative)
                elif entry.is_dir(follow_symlinks=False):
                    actual_directories.add(relative)
                    pending.append((Path(entry.path), relative))
                elif entry.is_file(follow_symlinks=False):
                    actual_files.add(relative)
                else:
                    drift.add(relative)

        drift.update(actual_directories ^ expected_directories)
        drift.update(actual_files ^ expected_files)
        resolved_source_root = source_root.resolve()
        for value in expected_files:
            relative = PurePosixPath(value)
            candidate = run_dir.joinpath(*relative.parts)
            try:
                candidate.resolve(strict=False).relative_to(resolved_source_root)
            except ValueError:
                drift.add(value)
        return sorted(drift)

    @staticmethod
    def _is_symlink_or_reparse_point(path: Path) -> bool:
        try:
            stat_result = path.lstat()
        except OSError:
            return False
        return path.is_symlink() or bool(
            getattr(stat_result, "st_file_attributes", 0) & 0x400
        )

    def _identity_binding_drift(
        self,
        run_dir: Path,
        intent: Any,
        record: dict[str, Any],
        run_record_sha: str,
        expected_current_sha: str | None = None,
    ) -> list[str]:
        if record.get("schema_version") == "3.0.0":
            return self._production_identity_binding_drift(
                run_dir,
                intent,
                record,
                run_record_sha,
                expected_current_sha=expected_current_sha,
            )
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

    def _production_identity_binding_drift(
        self,
        run_dir: Path,
        intent: Any,
        record: dict[str, Any],
        run_record_sha: str,
        *,
        expected_current_sha: str | None,
    ) -> list[str]:
        drift: list[str] = []
        required_intent = (
            "expected_run_record_sha256",
            "canonical_platform",
            "canonical_item_id",
            "source_identity",
        )
        if any(intent[field] is None for field in required_intent):
            return ["control-store/initialization-intent"]
        expected_sha = expected_current_sha or intent["expected_run_record_sha256"]
        if expected_sha != run_record_sha:
            drift.append("workflow/run.json")
        if (
            record["run_id"] != intent["run_id"]
            or record["initialization_intent_id"] != intent["intent_id"]
            or Path(record["output_path"]).resolve()
            != Path(intent["output_path"]).resolve()
            or record["canonical_platform"] != intent["canonical_platform"]
            or record["canonical_item_id"] != intent["canonical_item_id"]
            or record["source_identity"] != intent["source_identity"]
        ):
            drift.append("workflow/run.json")
        bootstrap_generation = record["artifact_generations"].get("bootstrap_record")
        bootstrap_path = run_dir / "待删除/bootstrap/probe.json"
        try:
            bootstrap = read_json(bootstrap_path)
            self.contracts.validate("bootstrap-record", bootstrap)
        except (ContractError, OSError, ValueError):
            bootstrap = None
            drift.append("待删除/bootstrap/probe.json")
        if bootstrap is not None and (
            bootstrap_generation is None
            or sha256_file(bootstrap_path) != bootstrap_generation["sha256"]
            or bootstrap["run_id"] != record["run_id"]
            or bootstrap["adapter"]["canonical_platform"]
            != record["canonical_platform"]
            or bootstrap["canonical_item_id"] != record["canonical_item_id"]
            or bootstrap["source_identity"] != record["source_identity"]
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
