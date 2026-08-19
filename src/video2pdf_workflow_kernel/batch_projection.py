from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from .errors import (
    ContractError,
    ControlStoreUnavailable,
    InitializationFault,
    KernelConflict,
    KernelError,
)
from .kernel import FAULT_POINTS as KERNEL_INITIALIZATION_FAULT_POINTS
from .utils import (
    canonical_json_bytes,
    read_json,
    sha256_bytes,
    sha256_file,
    write_json_atomic,
)

_BILIBILI_ITEM_PATTERN = re.compile(r"^(BV[0-9A-Za-z]{10}):p([1-9][0-9]*)$")
_YOUTUBE_VIDEO_ID = re.compile(r"^[0-9A-Za-z_-]{11}$")
_SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_CHECKPOINT_ORDER = (
    "run_initialized",
    "source_candidates_ready",
    "source_acquisition_decision_ready",
    "source_ready",
)
_AFTER_FIRST_TASK_CLAIM_BEFORE_MAPPING_COMMIT = (
    "after_first_task_claim_before_mapping_commit"
)
BATCH_RUN_FAULT_POINTS = frozenset(
    {
        *KERNEL_INITIALIZATION_FAULT_POINTS,
        _AFTER_FIRST_TASK_CLAIM_BEFORE_MAPPING_COMMIT,
    }
)


def is_guarded_delivered(projection: dict) -> bool:
    """THE single batch item success helper (pinned).

    An item succeeds only when its Run reached the ``delivered`` delivery
    stage with a passing Delivery Guard fingerprint bound in the projection.
    PDF existence, process exit codes, and cached status are never consulted.
    """
    outcome = projection.get("delivery_outcome") or {}
    source_authority = projection.get("source_authority") or {}
    outcome_guard = outcome.get("guard_report_sha256")
    return (
        outcome.get("guarded_delivered") is True
        and outcome.get("delivery_stage") == "delivered"
        and bool(outcome_guard)
        and source_authority.get("guard_report_sha256") == outcome_guard
        and bool(source_authority.get("run_record_sha256"))
    )


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_iso_with_tz(value: str, *, purpose: str) -> None:
    if not isinstance(value, str) or not value:
        raise ContractError(f"{purpose} must be an ISO 8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ContractError(f"{purpose} must be ISO 8601: {value}") from exc
    if parsed.tzinfo is None:
        raise ContractError(f"{purpose} must include a timezone offset")


def _task_start_compact(value: str) -> str:
    return datetime.fromisoformat(value).strftime("%Y%m%d_%H%M%S")


def _normalize_title(title: str) -> str:
    """Normalize a batch title per the project whitelist rule."""
    value = "".join(ch if (ch.isalnum() or ch in " _") else "_" for ch in title)
    value = re.sub(r" +", " ", value)
    value = re.sub(r"_+", "_", value)
    value = value.strip(" ._")
    value = value[:120].rstrip(" ._")
    return value or "batch"


def _flat_playlist(platform: str, source_url: str) -> list[dict[str, Any]] | None:
    """Best-effort flat yt-dlp enumeration; never downloads media.

    Returns None when the provider cannot be reached so planning still works
    from deterministic URL derivation alone.  The batch never uses this as a
    cookie scanner or breaker authority.
    """
    del platform  # both supported platforms use the same flat listing
    try:
        argv = [
            sys.executable or "python",
            "-m",
            "yt_dlp",
            "--no-cache-dir",
            "--flat-playlist",
            "--dump-single-json",
            "--",
            source_url,
        ]
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            check=False,
        )
        if completed.returncode != 0:
            return None
        value = json.loads(completed.stdout)
        entries = value.get("entries")
        if isinstance(entries, list) and entries:
            return [entry for entry in entries if isinstance(entry, dict)]
        return None
    except (OSError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired):
        return None


def _bilibili_url_locator(source_url: str) -> tuple[str, int | None]:
    from .adapters.bilibili import _bilibili_url_locator as derive

    return derive(source_url)


def _youtube_video_id(source_url: str) -> str:
    parsed = urlsplit(source_url)
    if (parsed.hostname or "").casefold() not in {"youtube.com", "www.youtube.com"}:
        raise ValueError("YouTube batch item URL has an unsupported host")
    if parsed.path != "/watch":
        raise ValueError("YouTube batch item URL is not a canonical watch URL")
    query = parse_qsl(parsed.query, keep_blank_values=True)
    if len(query) != 1 or query[0][0] != "v":
        raise ValueError("YouTube batch item URL is ambiguous")
    video_id = query[0][1]
    if _YOUTUBE_VIDEO_ID.fullmatch(video_id) is None:
        raise ValueError("YouTube batch item URL has an invalid video identity")
    return video_id


class BatchProjectionProvider:
    """Deep module: deterministic batch planning, guarded single-video Runs,
    and read-only Batch Item Projections over authoritative Run state.

    The provider never writes per-video phase, checkpoints, generations,
    quality decisions, repair budgets, or delivery lifecycle state.  It only
    creates Runs through the Kernel's own guarded initialization and stores
    batch-owned records and projections in the Control Store.
    """

    def __init__(self, *, batch_authority_publisher: Any | None = None) -> None:
        if batch_authority_publisher is None:
            from .batch_authority import BatchCutoverPublisher

            batch_authority_publisher = BatchCutoverPublisher()
        self.batch_authority_publisher = batch_authority_publisher

    @staticmethod
    def _batch_authority_binding(authority: dict[str, Any]) -> dict[str, Any]:
        required = (
            "authority_path",
            "authority_sha256",
            "exit_evidence_sha256",
            "generation",
            "publication_commit",
        )
        if not isinstance(authority, dict) or any(
            key not in authority for key in required
        ):
            raise ContractError("current Batch authority has an incomplete binding")
        return {key: authority[key] for key in required}

    # ------------------------------------------------------------------
    # plan
    # ------------------------------------------------------------------
    def plan(
        self,
        workspace_root: Path,
        contracts: Any,
        *,
        platform: str,
        source_url: str | None,
        task_start: str,
        request_id: str,
        control_store_root: Path,
        selection: list[Any] | None = None,
        url_set: str | None = None,
    ) -> dict[str, Any]:
        if platform not in {"bilibili", "youtube"}:
            raise ContractError(f"batch platform is unsupported: {platform}")
        _validate_iso_with_tz(task_start, purpose="task_start")
        if not isinstance(request_id, str) or not request_id:
            raise ContractError("batch request identity is required")
        if (source_url is None) == (url_set is None):
            raise ContractError(
                "batch plan requires exactly one of source_url or url_set"
            )
        workspace = Path(workspace_root).resolve()
        control_root = Path(control_store_root).resolve()

        authority_before = self.batch_authority_publisher.require_current(
            control_store_root=Path(control_store_root)
        )
        binding = self._batch_authority_binding(authority_before)

        items = self._enumerate_items(platform, source_url, url_set)
        authority_after = self.batch_authority_publisher.require_current(
            control_store_root=Path(control_store_root)
        )
        if self._batch_authority_binding(authority_after) != binding:
            raise KernelConflict("Batch authority changed during Batch planning")
        selected_indexes = self._resolve_selection(items, selection)
        if not selected_indexes:
            raise ContractError("batch selection produced no selected items")
        selected_items = [
            item for item in items if item["item_index"] in selected_indexes
        ]
        item_order: list[dict[str, Any]] = []
        for item in items:
            item_order.append(
                {
                    "item_index": item["item_index"],
                    "part_id": item["part_id"],
                    "canonical_item_id": item["canonical_item_id"],
                    "canonical_url": item["canonical_url"],
                    "title": item["title"],
                    "selected": item["item_index"] in selected_indexes,
                }
            )
        source_order_identity = [
            {
                "item_index": item["item_index"],
                "canonical_item_id": item["canonical_item_id"],
                "selected": item["selected"],
            }
            for item in item_order
        ]
        batch_source_identity = sha256_bytes(
            canonical_json_bytes(source_order_identity)
        )
        identity_source_url = (
            source_url
            if source_url is not None
            else str(items[0]["canonical_url"])
        )
        original_title = str(selected_items[0]["title"])
        batch_id = hashlib.sha256(
            "\0".join(
                (
                    "batch",
                    platform,
                    batch_source_identity,
                    task_start,
                    request_id,
                )
            ).encode("utf-8")
        ).hexdigest()[:32]
        compact = _task_start_compact(task_start)
        batch_dir = (
            workspace / f"{_normalize_title(original_title)}_{compact}" / "batch-control"
        )
        control_dir = (
            control_root / ".workflow-control" / "batches" / batch_id
        )
        now = _utc_now_iso()
        record = {
            "schema_name": "batch-record",
            "schema_version": "1.0.0",
            "kernel_version": "2.0.0",
            "batch_id": batch_id,
            "batch_identity": {
                "kind": self._batch_kind(platform, url_set, len(items)),
                "canonical_platform": platform,
                "batch_source_identity": batch_source_identity,
                "source_url": identity_source_url,
                "original_title": original_title,
                "task_start": task_start,
                "request_id": request_id,
            },
            "output_root": str(workspace),
            "batch_dir": str(batch_dir),
            "control_dir": str(control_dir),
            "batch_stage": "planned",
            "batch_authority_binding": binding,
            "run_task_start": None,
            "item_order": item_order,
            "run_mappings": [],
            "projections": [],
            "created_at": now,
            "updated_at": now,
        }
        self._validate_planned_record(contracts, record)
        store = self._open_store(control_root, contracts)
        try:
            stored_id, outcome = store.create_batch_record(record, record["batch_identity"])
        except KernelConflict:
            # A deterministic replay may differ only in plan timestamps; the
            # existing planned record is authoritative.  Reuse it so repeated
            # plan calls stay idempotent and never create a second record.
            existing = store.get_batch_record(batch_id)
            if existing is None:
                raise
            self._validate_loaded_record(contracts, existing)
            expected_replay = dict(record)
            authoritative_replay = dict(existing)
            for volatile_key in ("created_at", "updated_at"):
                expected_replay.pop(volatile_key, None)
                authoritative_replay.pop(volatile_key, None)
            if authoritative_replay != expected_replay:
                raise KernelConflict(
                    "batch plan replay differs from the authoritative Batch Record",
                    data={
                        "batch_id": batch_id,
                        "first_failing_gate": "batch_record_replay_identity",
                        "error_code": "batch_record_replay_conflict",
                    },
                )
            authoritative_record_path = (
                Path(existing["batch_dir"]) / "batch-record.json"
            )
            if not authoritative_record_path.is_file():
                self._persist_record_file(existing)
            return {
                "batch_id": batch_id,
                "batch_dir": str(existing["batch_dir"]),
                "batch_record_path": str(authoritative_record_path),
                "item_order": existing["item_order"],
                "created_or_replayed": "REPLAY",
            }
        if stored_id != batch_id:
            raise KernelConflict(
                "batch record identity changed on replay",
                data={"expected": batch_id, "actual": stored_id},
            )
        batch_dir.mkdir(parents=True, exist_ok=True)
        (batch_dir.parent / "待删除").mkdir(parents=True, exist_ok=True)
        record_path = batch_dir / "batch-record.json"
        write_json_atomic(record_path, record)
        return {
            "batch_id": batch_id,
            "batch_dir": str(batch_dir),
            "batch_record_path": str(record_path),
            "item_order": item_order,
            "created_or_replayed": outcome,
        }

    def _enumerate_items(
        self,
        platform: str,
        source_url: str | None,
        url_set: str | None,
    ) -> list[dict[str, Any]]:
        if url_set is not None:
            urls = [value.strip() for value in url_set.split(",") if value.strip()]
            if not urls:
                raise ContractError("batch url_set is empty")
            items: list[dict[str, Any]] = []
            for position, url in enumerate(urls, start=1):
                item = self._derive_platform_item(platform, url, position)
                item["item_index"] = position
                items.append(item)
            return items
        assert source_url is not None
        entries = _flat_playlist(platform, source_url)
        if not entries:
            raise ContractError(
                f"batch source enumeration failed: {source_url}"
            )
        items = []
        for position, entry in enumerate(entries, start=1):
            raw_url = str(
                entry.get("webpage_url") or entry.get("url") or source_url
            )
            raw_title = entry.get("title")
            title = str(raw_title) if isinstance(raw_title, str) and raw_title.strip() else None
            item = self._derive_platform_item(platform, raw_url, position, title=title)
            item["item_index"] = position
            items.append(item)
        return items

    def _derive_platform_item(
        self,
        platform: str,
        url: str,
        position: int,
        *,
        title: str | None = None,
    ) -> dict[str, Any]:
        if platform == "bilibili":
            try:
                bvid, part = _bilibili_url_locator(url)
            except ValueError as exc:
                raise ContractError(
                    f"Bilibili batch item URL is invalid: {url}"
                ) from exc
            selected = part or 1
            canonical_item_id = f"{bvid}:p{selected}"
            canonical_url = f"https://www.bilibili.com/video/{bvid}/?p={selected}"
            item_title = title or f"{bvid} P{selected}"
            return {
                "part_id": f"p{selected}",
                "canonical_item_id": canonical_item_id,
                "canonical_url": canonical_url,
                "title": item_title,
            }
        if platform == "youtube":
            try:
                video_id = _youtube_video_id(url)
            except ValueError as exc:
                raise ContractError(
                    f"YouTube batch item URL is invalid: {url}"
                ) from exc
            item_title = title or f"{video_id}"
            return {
                "part_id": None,
                "canonical_item_id": video_id,
                "canonical_url": f"https://www.youtube.com/watch?v={video_id}",
                "title": item_title,
            }
        raise ContractError(f"batch platform is unsupported: {platform}")

    @staticmethod
    def _batch_kind(platform: str, url_set: str | None, item_count: int) -> str:
        del item_count
        if url_set is not None:
            return "url_set"
        if platform == "bilibili":
            return "bilibili_multipart"
        return "url_set"

    def _resolve_selection(
        self, items: list[dict[str, Any]], selection: list[Any] | None
    ) -> list[int]:
        if selection is None:
            return [int(item["item_index"]) for item in items]
        requested = list(selection)
        by_index = {int(item["item_index"]): item for item in items}
        by_part_id = {
            item["part_id"]: item for item in items if item["part_id"] is not None
        }
        selected: set[int] = set()
        for value in requested:
            matched = None
            if isinstance(value, int) and value in by_index:
                matched = by_index[value]
            elif isinstance(value, str):
                if value.isdigit() and int(value) in by_index:
                    matched = by_index[int(value)]
                elif value in by_part_id:
                    matched = by_part_id[value]
            if matched is None:
                raise ContractError(
                    f"batch selection references an unknown item: {value}"
                )
            selected.add(int(matched["item_index"]))
        return sorted(selected)

    # ------------------------------------------------------------------
    # run
    # ------------------------------------------------------------------
    def run(
        self,
        workspace_root: Path,
        contracts: Any,
        *,
        batch_id: str,
        control_store_root: Path,
        session_id: str,
        run_task_start: str,
        fault_point: str | None = None,
    ) -> dict[str, Any]:
        _validate_iso_with_tz(run_task_start, purpose="run_task_start")
        if (
            not isinstance(session_id, str)
            or not session_id
            or _SESSION_ID_PATTERN.fullmatch(session_id) is None
        ):
            raise ContractError(
                "batch-run requires a valid Kernel delivery session identity"
            )
        current_authority = self.batch_authority_publisher.require_current(
            control_store_root=Path(control_store_root)
        )
        current_binding = self._batch_authority_binding(current_authority)
        del workspace_root
        control_root = Path(control_store_root).resolve()
        store = self._open_store(control_root, contracts)
        record = store.get_batch_record(batch_id)
        if record is None:
            raise KernelConflict("batch record not found", data={"batch_id": batch_id})
        self._validate_loaded_record(contracts, record)
        workspace = Path(record["output_root"]).resolve()
        if record.get("batch_authority_binding") != current_binding:
            raise KernelConflict(
                "Batch Record authority binding is missing or stale",
                data={
                    "batch_id": batch_id,
                    "first_failing_gate": "batch_authority_binding",
                    "error_code": "batch_authority_binding_stale",
                },
            )
        global_gate_binding = current_authority.get("global_gate_binding")
        if not isinstance(global_gate_binding, dict) or not global_gate_binding:
            raise ContractError("current Batch authority lacks its Global Gate binding")
        stage = str(record["batch_stage"])
        if stage not in {"planned", "running"}:
            raise KernelConflict(
                f"batch stage {stage} does not permit run creation",
                data={"batch_id": batch_id},
            )
        store.bind_batch_run_task_start(batch_id, run_task_start)
        record = store.get_batch_record(batch_id)
        if record is None:
            raise KernelConflict("batch record disappeared after run start binding")
        self._validate_loaded_record(contracts, record)
        platform = str(record["batch_identity"]["canonical_platform"])
        mapped_indexes = {
            int(mapping["item_index"]) for mapping in record.get("run_mappings", [])
        }
        pending = [
            item
            for item in record["item_order"]
            if item["selected"] and int(item["item_index"]) not in mapped_indexes
        ]
        kernel = self._kernel(workspace, contracts)
        items: list[dict[str, Any]] = []
        mappings_to_commit: list[dict[str, Any]] = []
        for item in pending:
            item_index = int(item["item_index"])
            request_id = f"{batch_id}:{item_index}"
            item_session_id = self._item_session_id(session_id, item_index)
            run_id = self._derive_run_id(
                platform, str(item["canonical_item_id"]), run_task_start, request_id
            )
            source_url, selector, original_title = self._item_bootstrap_binding(
                platform,
                str(item["canonical_item_id"]),
                str(item["canonical_url"]),
                str(item["title"]),
            )
            probe = self._bootstrap_probe(
                kernel,
                platform,
                source_url,
                selector,
                original_title,
                run_task_start,
                request_id,
            )
            if probe.run_id != run_id:
                raise KernelConflict(
                    "deterministic batch run_id disagrees with Kernel Bootstrap",
                    data={
                        "item_index": item_index,
                        "expected": run_id,
                        "actual": probe.run_id,
                    },
                )
            initialized = kernel.initialize_production_source(
                probe,
                session_id=item_session_id,
                global_gate_binding=global_gate_binding,
                fault_point=(
                    None
                    if fault_point == _AFTER_FIRST_TASK_CLAIM_BEFORE_MAPPING_COMMIT
                    else fault_point
                ),
            )
            run_dir = initialized.run_dir
            admission = self._submit_first_admitted_task(
                kernel,
                run_dir,
                batch_id,
                run_task_start,
                coordinator_session_id=item_session_id,
            )
            if (
                fault_point == _AFTER_FIRST_TASK_CLAIM_BEFORE_MAPPING_COMMIT
                and not mappings_to_commit
            ):
                raise InitializationFault(
                    _AFTER_FIRST_TASK_CLAIM_BEFORE_MAPPING_COMMIT
                )
            mappings_to_commit.append(
                {
                    "item_index": item_index,
                    "run_id": run_id,
                    "request_id": request_id,
                }
            )
            items.append(
                {
                    "item_index": item_index,
                    "part_id": item["part_id"],
                    "run_id": run_id,
                    "run_dir": str(run_dir),
                    "stage": admission.queue_state,
                }
            )
        if mappings_to_commit:
            store.commit_batch_run_mappings(batch_id, mappings_to_commit)
        updated_record = store.get_batch_record(batch_id)
        if updated_record is not None:
            self._persist_record_file(updated_record)
        return {"batch_id": batch_id, "items": items}

    @staticmethod
    def _item_session_id(session_id: str, item_index: int) -> str:
        suffix = f"-{item_index}"
        base = session_id[: 128 - len(suffix)]
        return f"{base}{suffix}"

    def _item_bootstrap_binding(
        self,
        platform: str,
        canonical_item_id: str,
        canonical_url: str,
        title: str,
    ) -> tuple[str, str | None, str]:
        del canonical_url
        if platform == "bilibili":
            matched = _BILIBILI_ITEM_PATTERN.fullmatch(canonical_item_id)
            if matched is None:
                raise ContractError(
                    "Bilibili batch item has no canonical part identity",
                    data={"canonical_item_id": canonical_item_id},
                )
            bvid = matched.group(1)
            part = matched.group(2)
            return f"https://www.bilibili.com/video/{bvid}/", f"p{part}", title
        if platform == "youtube":
            if _YOUTUBE_VIDEO_ID.fullmatch(canonical_item_id) is None:
                raise ContractError(
                    "YouTube batch item has no canonical video identity",
                    data={"canonical_item_id": canonical_item_id},
                )
            return (
                f"https://www.youtube.com/watch?v={canonical_item_id}",
                None,
                title,
            )
        raise ContractError(f"batch platform is unsupported: {platform}")

    @staticmethod
    def _derive_run_id(
        platform: str, canonical_item_id: str, task_start: str, request_id: str
    ) -> str:
        """Exact Kernel deterministic run_id formula (kernel.py:406-415)."""
        return hashlib.sha256(
            "\0".join(
                (platform, canonical_item_id, task_start, request_id)
            ).encode("utf-8")
        ).hexdigest()[:32]

    @staticmethod
    def _bootstrap_probe(
        kernel: Any,
        platform: str,
        source_url: str,
        selector: str | None,
        original_title: str,
        task_start: str,
        request_id: str,
    ) -> Any:
        from .adapters import BilibiliPlatformAdapter, YouTubePlatformAdapter, YtDlpRuntime
        from .models import DeterministicLocatorRequest

        runtime = YtDlpRuntime(
            python_executable=Path("python"),
            ffmpeg_dir=Path("ffmpeg-bin"),
            ffprobe_executable=Path("ffprobe"),
        )
        adapter = (
            YouTubePlatformAdapter(runtime)
            if platform == "youtube"
            else BilibiliPlatformAdapter(runtime)
        )
        request = DeterministicLocatorRequest(
            source_url=source_url,
            original_title=original_title,
            explicit_item_selector=selector,
        )
        return kernel.bootstrap_production_source(
            adapter=adapter,
            request=request,
            runner=None,
            task_start=task_start,
            request_id=request_id,
            provider_kind="deterministic_locator",
        )

    @staticmethod
    def _submit_first_admitted_task(
        kernel: Any,
        run_dir: Path,
        batch_id: str,
        run_task_start: str,
        *,
        coordinator_session_id: str,
    ) -> Any:
        """Prepare and claim the Run's first provider-acquisition Task."""
        run_dir = Path(run_dir)
        record = read_json(run_dir / "workflow/run.json")
        prepared = kernel.prepare_production_source_task(
            run_dir,
            task_stage="provider_acquisition",
            logical_task_key=f"source-provider-epoch-{record['source_epoch']}",
            prepared_at=run_task_start,
            batch_id=batch_id,
        )
        existing = kernel.control_store.task_claim_for_task(prepared.task_id)
        if existing is not None:
            if (
                str(existing["authority_id"]) != str(record["run_id"])
                or str(existing["envelope_sha256"])
                != sha256_file(prepared.envelope_path)
            ):
                raise KernelConflict(
                    "existing Batch Task Claim differs from the current Task Envelope"
                )
            admission = kernel.resource_status(
                prepared.task_id, str(existing["attempt_id"])
            )
            if (
                admission.batch_id != batch_id
                or admission.fairness_group_id != batch_id
            ):
                raise KernelConflict(
                    "existing Batch Task Claim has conflicting Resource Admission authority"
                )
            return admission
        claim = kernel.claim_task(
            run_dir,
            prepared.task_id,
            coordinator_session_id=coordinator_session_id,
            worker_id=f"batch-provider-{record['run_id']}",
        )
        if claim.resource_admission is None:
            raise KernelConflict("Batch Task Claim lacks Resource Admission authority")
        return claim.resource_admission

    # ------------------------------------------------------------------
    # recover
    # ------------------------------------------------------------------
    def recover(
        self,
        workspace_root: Path,
        contracts: Any,
        *,
        batch_id: str,
        control_store_root: Path,
    ) -> dict[str, Any]:
        control_root = Path(control_store_root).resolve()
        current_authority = self.batch_authority_publisher.require_current(
            control_store_root=control_root
        )
        current_binding = self._batch_authority_binding(current_authority)
        del workspace_root
        store = self._open_store(control_root, contracts)
        record = store.get_batch_record(batch_id)
        if record is None:
            raise KernelConflict("batch record not found", data={"batch_id": batch_id})
        self._validate_loaded_record(contracts, record)
        if record.get("batch_authority_binding") != current_binding:
            raise KernelConflict(
                "Batch Record authority binding is missing or stale",
                data={
                    "batch_id": batch_id,
                    "first_failing_gate": "batch_authority_binding",
                    "error_code": "batch_authority_binding_stale",
                },
            )
        workspace = Path(record["output_root"]).resolve()
        reconciled: list[dict[str, Any]] = []
        mappings = list(record.get("run_mappings", []))
        missing_item_indexes: list[int] = []
        run_task_start = record.get("run_task_start")
        run_store = None
        kernel = None
        if mappings or isinstance(run_task_start, str):
            run_store = self._open_existing_store(workspace, contracts)
            from .kernel import VideoWorkflowKernel

            kernel = VideoWorkflowKernel(workspace)
            kernel.control_store = run_store
        if not mappings and isinstance(run_task_start, str):
            expected: list[dict[str, Any]] = []
            present: list[dict[str, Any]] = []
            platform = str(record["batch_identity"]["canonical_platform"])
            for item in record["item_order"]:
                if not item["selected"]:
                    continue
                item_index = int(item["item_index"])
                request_id = f"{batch_id}:{item_index}"
                run_id = self._derive_run_id(
                    platform,
                    str(item["canonical_item_id"]),
                    run_task_start,
                    request_id,
                )
                mapping = {
                    "item_index": item_index,
                    "run_id": run_id,
                    "request_id": request_id,
                }
                expected.append(mapping)
                if (
                    run_store.binding_for_run(run_id) is not None
                    or run_store.intent_for_run(run_id) is not None
                ):
                    present.append(mapping)
                else:
                    missing_item_indexes.append(item_index)
            if expected and len(present) == len(expected):
                store.commit_batch_run_mappings(batch_id, expected)
                mappings = expected
            else:
                mappings = present
        for mapping in mappings:
            if run_store is None or kernel is None:
                raise KernelConflict("batch Run authority is unavailable")
            run_id = str(mapping["run_id"])
            result = self._reconcile_one(kernel, run_store, run_id)
            reconciled.append(
                {
                    "item_index": int(mapping["item_index"]),
                    "run_id": run_id,
                    "run_dir": str(result.run_dir),
                    "outcome": result.outcome,
                }
            )
        projections = self.rebuild_projections(
            workspace,
            contracts,
            batch_id=batch_id,
            control_store_root=control_root,
        )
        return {
            "batch_id": batch_id,
            "reconciled": reconciled,
            "projections": projections,
            "missing_item_indexes": missing_item_indexes,
        }

    def _reconcile_one(self, kernel: Any, store: Any, run_id: str) -> Any:
        intent = store.intent_for_run(run_id)
        if intent is not None and str(intent["state"]) in {
            "PREPARED",
            "PUBLISHED",
            "RECORD_COMMITTED",
            "ABORTED",
        }:
            return kernel.reconcile_initialization(run_id)
        binding = store.binding_for_run(run_id)
        if binding is None:
            raise KernelConflict(
                f"batch run mapping has no Kernel authority: {run_id}"
            )
        run_dir = Path(binding["output_path"])
        return kernel.reconcile_run(run_dir)

    # ------------------------------------------------------------------
    # rebuild_projections
    # ------------------------------------------------------------------
    def rebuild_projections(
        self,
        workspace_root: Path,
        contracts: Any,
        *,
        batch_id: str,
        control_store_root: Path | None = None,
    ) -> list[dict[str, Any]]:
        workspace = Path(workspace_root).resolve()
        control_root = (
            workspace
            if control_store_root is None
            else Path(control_store_root).resolve()
        )
        store = self._open_store(control_root, contracts)
        record = store.get_batch_record(batch_id)
        if record is None:
            raise KernelConflict("batch record not found", data={"batch_id": batch_id})
        self._validate_loaded_record(contracts, record)
        mapped_by_index = {
            int(mapping["item_index"]): mapping
            for mapping in record.get("run_mappings", [])
        }
        if not mapped_by_index:
            return []
        run_store = self._open_existing_store(Path(record["output_root"]), contracts)
        projections: list[dict[str, Any]] = []
        for item in record["item_order"]:
            if not item["selected"]:
                continue
            item_index = int(item["item_index"])
            mapping = mapped_by_index.get(item_index)
            if mapping is None:
                continue
            run_id = str(mapping["run_id"])
            binding = run_store.binding_for_run(run_id)
            if binding is None:
                raise KernelConflict(
                    f"batch run mapping has no Control Store binding: {run_id}"
                )
            run_dir = Path(binding["output_path"])
            run_path = run_dir / "workflow" / "run.json"
            if not run_path.is_file():
                raise KernelConflict(
                    "batch run record is unavailable",
                    data={"run_id": run_id, "run_dir": str(run_dir)},
                )
            try:
                run_record = read_json(run_path)
            except (OSError, UnicodeError, ValueError):
                raise KernelConflict(
                    "batch run record is not valid JSON",
                    data={"run_id": run_id, "run_dir": str(run_dir)},
                ) from None
            contracts.validate_run_record(run_record)
            projection = self._derive_projection(
                contracts,
                batch_id,
                item_index,
                run_id,
                run_record,
                run_dir,
                run_path,
            )
            existing = store.get_item_projection(batch_id, item_index)
            if existing is not None:
                # Rebuilds are idempotent over unchanged Run state: keep the
                # prior projection timestamps so the content fingerprint is
                # stable and the store replays the same revision.
                projection["projected_at"] = existing["projected_at"]
                projection["source_authority"]["accepted_at_projection"] = (
                    existing["source_authority"]["accepted_at_projection"]
                )
            store.put_item_projection(batch_id, item_index, run_id, projection)
            stored = store.get_item_projection(batch_id, item_index)
            if stored is None:
                raise KernelConflict(
                    "stored batch item projection is unavailable",
                    data={"batch_id": batch_id, "item_index": item_index},
                )
            contracts.validate("batch-item-projection", stored)
            projections.append(stored)
        if projections:
            self._roll_up_stage(store, record, projections)
        updated_record = store.get_batch_record(batch_id)
        if updated_record is not None:
            self._persist_record_file(updated_record)
        return projections

    def _derive_projection(
        self,
        contracts: Any,
        batch_id: str,
        item_index: int,
        run_id: str,
        run_record: dict[str, Any],
        run_dir: Path,
        run_path: Path,
    ) -> dict[str, Any]:
        delivery = run_record.get("delivery") or {}
        delivery_stage = str(delivery.get("stage") or "generating")
        ownership = delivery.get("ownership") or {}
        blocker = self._blocker_string(run_record.get("source_blocker"))
        guard_sha, guard_valid = self._guard_report(contracts, run_record, run_dir)
        guarded_delivered = delivery_stage == "delivered" and guard_valid
        now = _utc_now_iso()
        return {
            "schema_name": "batch-item-projection",
            "schema_version": "1.0.0",
            "kernel_version": "2.0.0",
            "batch_id": batch_id,
            "item_index": item_index,
            "run_id": run_id,
            "run_state": {
                "phase": str(run_record.get("phase") or "source_acquisition"),
                "source_state": str(run_record.get("source_state") or "pending"),
                "source_blocker": blocker,
                "coordination_revision": int(
                    run_record.get("coordination_revision") or 1
                ),
                "output_path": str(run_record["output_path"]),
                "delivery": {
                    "stage": delivery_stage,
                    "ownership": {
                        "session_id": str(ownership.get("session_id") or ""),
                        "generation": int(ownership.get("generation") or 1),
                    },
                },
            },
            "checkpoint": self._current_checkpoint(run_record.get("checkpoints") or {}),
            "blocker": blocker,
            "delivery_outcome": {
                "delivery_stage": delivery_stage,
                "guarded_delivered": guarded_delivered,
                "acceptance_report_sha256": self._acceptance_report_sha(run_dir),
                "guard_report_sha256": guard_sha,
                "delivered_at": (
                    self._delivered_at(run_record)
                    if delivery_stage == "delivered"
                    else None
                ),
            },
            "projection_revision": 1,
            "projected_at": now,
            "source_authority": {
                "run_record_sha256": sha256_file(run_path),
                "guard_report_sha256": guard_sha,
                "accepted_at_projection": now,
            },
        }

    @staticmethod
    def _blocker_string(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return value if value else None
        if isinstance(value, dict):
            reason = value.get("reason")
            if isinstance(reason, str) and reason:
                return reason
            return json.dumps(value, sort_keys=True, separators=(",", ":"))
        return str(value)

    @staticmethod
    def _current_checkpoint(checkpoints: dict[str, Any]) -> dict[str, str]:
        current: dict[str, str] | None = None
        for name in _CHECKPOINT_ORDER:
            entry = checkpoints.get(name)
            if isinstance(entry, dict) and entry.get("status") == "current":
                current = {"name": name, "status": "current"}
        if current is not None:
            return current
        for name in _CHECKPOINT_ORDER:
            entry = checkpoints.get(name)
            if isinstance(entry, dict) and entry.get("status") == "stale":
                return {"name": name, "status": "stale"}
        for name, entry in checkpoints.items():
            if isinstance(entry, dict) and isinstance(entry.get("status"), str):
                return {"name": name, "status": entry["status"]}
        return {"name": "run_initialized", "status": "stale"}

    @staticmethod
    def _guard_report(
        contracts: Any, run_record: dict[str, Any], run_dir: Path
    ) -> tuple[str | None, bool]:
        from .guarded_delivery import require_current_kernel_delivered_decision

        try:
            authority = require_current_kernel_delivered_decision(
                project_root=contracts.project_root,
                run_dir=run_dir,
            )
            if authority["run_id"] != run_record["run_id"]:
                return None, False
            return authority["delivery_guard_report"]["sha256"], True
        except (
            KernelError,
            KeyError,
            OSError,
            UnicodeError,
            ValueError,
            TypeError,
        ):
            return None, False

    @staticmethod
    def _acceptance_report_sha(run_dir: Path) -> str | None:
        acceptance_path = run_dir / "review" / "acceptance" / "acceptance_report.json"
        if not acceptance_path.is_file():
            return None
        return sha256_file(acceptance_path)

    @staticmethod
    def _delivered_at(run_record: dict[str, Any]) -> str | None:
        timestamps: list[str] = []
        for entry in (run_record.get("checkpoints") or {}).values():
            if not isinstance(entry, dict):
                continue
            completed = entry.get("completed_at")
            if isinstance(completed, str) and completed:
                timestamps.append(completed)
        if not timestamps:
            return None
        return max(timestamps)

    @staticmethod
    def _roll_up_stage(
        store: Any, record: dict[str, Any], projections: list[dict[str, Any]]
    ) -> None:
        by_index = {int(p["item_index"]): p for p in projections}
        selected = [item for item in record["item_order"] if item["selected"]]
        if not selected:
            return
        all_delivered = True
        any_blocked = False
        for item in selected:
            projection = by_index.get(int(item["item_index"]))
            if projection is None or not is_guarded_delivered(projection):
                all_delivered = False
            if projection is None:
                continue
            outcome = projection.get("delivery_outcome") or {}
            if outcome.get("delivery_stage") == "blocked":
                any_blocked = True
            if isinstance(projection.get("blocker"), str) and projection["blocker"]:
                any_blocked = True
        if all_delivered:
            target = "completed"
        elif any_blocked:
            target = "blocked"
        else:
            target = "running"
        current = store.get_batch_record(record["batch_id"])
        if current is None:
            raise KernelConflict("batch record disappeared during stage roll-up")
        if str(current["batch_stage"]) != target:
            store.update_batch_stage(record["batch_id"], str(current["batch_stage"]), target)

    # ------------------------------------------------------------------
    # status
    # ------------------------------------------------------------------
    def status(
        self,
        workspace_root: Path,
        contracts: Any,
        *,
        batch_id: str,
        control_store_root: Path | None = None,
    ) -> dict[str, Any]:
        workspace = Path(workspace_root).resolve()
        control_root = (
            workspace
            if control_store_root is None
            else Path(control_store_root).resolve()
        )
        store = self._open_store(control_root, contracts)
        record = store.get_batch_record(batch_id)
        if record is None:
            raise KernelConflict("batch record not found", data={"batch_id": batch_id})
        self._validate_loaded_record(contracts, record)
        mappings = list(record.get("run_mappings", []))
        run_store = (
            None
            if not mappings
            else self._open_existing_store(Path(record["output_root"]), contracts)
        )
        items: list[dict[str, Any]] = []
        for item in record["item_order"]:
            if not item["selected"]:
                continue
            item_index = int(item["item_index"])
            mapping = next(
                (
                    candidate
                    for candidate in mappings
                    if int(candidate["item_index"]) == item_index
                ),
                None,
            )
            entry: dict[str, Any] = {
                "item_index": item_index,
                "title": str(item["title"]),
                "run_id": str(mapping["run_id"]) if mapping is not None else None,
                "delivery_stage": None,
                "guarded_delivered": False,
                "blocker": None,
            }
            if mapping is not None:
                if run_store is None:
                    raise KernelConflict("batch Run authority is unavailable")
                run_id = str(mapping["run_id"])
                binding = run_store.binding_for_run(run_id)
                run_path = (
                    None
                    if binding is None
                    else Path(binding["output_path"]) / "workflow" / "run.json"
                )
                if run_path is not None and run_path.is_file():
                    run_record = read_json(run_path)
                    contracts.validate_run_record(run_record)
                    projection = self._derive_projection(
                        contracts,
                        batch_id,
                        item_index,
                        run_id,
                        run_record,
                        Path(binding["output_path"]),
                        run_path,
                    )
                    contracts.validate("batch-item-projection", projection)
                    outcome = projection.get("delivery_outcome") or {}
                    entry["delivery_stage"] = outcome.get("delivery_stage")
                    entry["guarded_delivered"] = is_guarded_delivered(projection)
                    entry["blocker"] = projection.get("blocker")
            items.append(entry)
        return {
            "batch_id": batch_id,
            "batch_stage": str(record["batch_stage"]),
            "items": items,
        }

    # ------------------------------------------------------------------
    # shared helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _validate_planned_record(contracts: Any, record: dict[str, Any]) -> None:
        contracts.validate("batch-record", record)

    @classmethod
    def _validate_loaded_record(cls, contracts: Any, record: dict[str, Any]) -> None:
        cls._validate_planned_record(contracts, record)

    @staticmethod
    def _open_store(workspace_root: Path, contracts: Any) -> Any:
        from .control_store import ControlStore

        workspace = Path(workspace_root).resolve()
        if ControlStore.identity_evidence_exists(workspace):
            store = ControlStore(workspace, contracts)
            store.check()
            return store
        return ControlStore.initialize(workspace, contracts)

    @staticmethod
    def _open_existing_store(workspace_root: Path, contracts: Any) -> Any:
        from .control_store import ControlStore

        workspace = Path(workspace_root).resolve()
        if not ControlStore.identity_evidence_exists(workspace):
            raise ControlStoreUnavailable(
                f"Run Control Store is unavailable: {workspace}"
            )
        store = ControlStore(workspace, contracts)
        store.check()
        return store

    @staticmethod
    def _kernel(workspace_root: Path, contracts: Any) -> Any:
        from .control_store import ControlStore
        from .kernel import VideoWorkflowKernel

        workspace = Path(workspace_root).resolve()
        kernel = VideoWorkflowKernel(workspace)
        if ControlStore.identity_evidence_exists(workspace):
            kernel.control_store = ControlStore(workspace, contracts)
            kernel.control_store.check()
        else:
            kernel.control_store = ControlStore.initialize(workspace, contracts)
        return kernel

    @staticmethod
    def _persist_record_file(record: dict[str, Any]) -> None:
        batch_dir = Path(record["batch_dir"])
        batch_dir.mkdir(parents=True, exist_ok=True)
        write_json_atomic(batch_dir / "batch-record.json", record)
