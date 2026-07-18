from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any
import uuid

from .contracts import ContractRegistry, _validate_project_relative_path
from .errors import (
    ArtifactDrift,
    ContractError,
    ControlStoreUnavailable,
    KernelConflict,
    ResourceAdmissionBlocked,
    TaskFault,
)
from .models import (
    TaskClaimResult,
    TaskCompletionResult,
    TaskPreparationResult,
    TaskPromotionResult,
)
from .resource_admission import (
    RESOURCE_CLAIM_FAULT_POINTS,
    RESOURCE_RECLAIM_FAULT_POINTS,
)
from .prompts import generate_source_acquisition_prompt
from .utils import (
    canonical_json_bytes,
    read_json,
    require_contained_path,
    sha256_file,
    write_json_atomic,
)


PREPARATION_FAULT_POINTS = frozenset(
    {"after_task_root_published"}
)
CLAIM_FAULT_POINTS = frozenset(
    {"after_claim_committed", "after_attempt_record_written"}
) | RESOURCE_CLAIM_FAULT_POINTS
RECLAIM_FAULT_POINTS = frozenset(
    {"after_reclaim_committed", "after_reclaim_attempt_record_written"}
) | RESOURCE_RECLAIM_FAULT_POINTS
COMPLETION_FAULT_POINTS = frozenset(
    {
        "after_completion_prepared",
        "after_completion_record_written",
        "after_completion_state_commit",
    }
)
PROMOTION_FAULT_POINTS = frozenset(
    {
        "after_promotion_intent_prepared",
        "after_promotion_journal_written",
        "after_promotion_journal_bound",
        "after_prior_outputs_preserved",
        "after_output_published",
        "after_outputs_state_commit",
        "after_run_record_commit_marker",
        "after_record_state_commit",
        "before_promotion_intent_commit",
        "after_promotion_intent_commit",
    }
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _inject(selected: str | None, current: str) -> None:
    if selected == current:
        raise TaskFault(current)


def _is_link_or_reparse(path: Path) -> bool:
    try:
        result = path.lstat()
    except OSError:
        return False
    return path.is_symlink() or bool(
        getattr(result, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _write_bytes_atomic(path: Path, data: bytes) -> str:
    temp = path.with_name(f".{path.name}.kernel-new")
    with temp.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)
    return hashlib.sha256(data).hexdigest()


class TaskExecution:
    """Deep Slice 2 task, fencing, Completion Gate, and promotion interface."""

    def __init__(self, kernel: Any) -> None:
        self.kernel = kernel
        self.project_root: Path = kernel.project_root
        self.contracts: ContractRegistry = kernel.contracts

    @staticmethod
    def _validate_production_task_identity_fields(
        *, task_stage: str, logical_task_key: str
    ) -> None:
        if task_stage not in {
            "provider_acquisition",
            "semantic_judgment",
            "whisper_transcription",
        }:
            raise ContractError(
                f"unsupported production Source Task stage: {task_stage}"
            )
        if not logical_task_key or any(
            char not in "abcdefghijklmnopqrstuvwxyz0123456789-"
            for char in logical_task_key
        ):
            raise ContractError("logical Task key must use lowercase kebab-case")

    @classmethod
    def _production_source_task_id(
        cls,
        record: dict[str, Any],
        *,
        task_stage: str,
        logical_task_key: str,
    ) -> str:
        """Derive the epoch-stable Source Task identity.

        Mutable freshness bindings intentionally remain in the Envelope.  This
        basis lets the provider prepare the semantic Decision Skeleton before
        the provider promotion advances the Run revision.
        """
        cls._validate_production_task_identity_fields(
            task_stage=task_stage,
            logical_task_key=logical_task_key,
        )
        identity = canonical_json_bytes(
            {
                "run_id": record["run_id"],
                "source_epoch": record["source_epoch"],
                "task_stage": task_stage,
                "logical_task_key": logical_task_key,
            }
        )
        return hashlib.sha256(identity).hexdigest()[:32]

    def derive_production_source_task_id(
        self,
        run_dir: Path,
        *,
        task_stage: str,
        logical_task_key: str,
    ) -> str:
        run_dir = run_dir.resolve()
        self.kernel._verify_current_source(run_dir)
        record, _, _ = self._run_record(run_dir)
        if record.get("schema_version") != "3.0.0":
            raise ContractError("production Source Tasks require Run Record v3")
        return self._production_source_task_id(
            record,
            task_stage=task_stage,
            logical_task_key=logical_task_key,
        )

    @staticmethod
    def _snapshot_directory_sha(relative: str) -> str:
        return hashlib.sha256(
            b"video-workflow-protected-directory-v1\0" + relative.encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _safe_run_path(run_dir: Path, relative: str, *, prefix: str | None = None) -> Path:
        _validate_project_relative_path(relative, prefix=prefix)
        root = run_dir.resolve()
        candidate = run_dir.joinpath(*PurePosixPath(relative).parts)
        try:
            candidate.resolve(strict=False).relative_to(root)
        except ValueError as exc:
            raise ContractError(f"task path escapes its Run: {relative!r}") from exc
        current = run_dir
        for part in PurePosixPath(relative).parts[:-1]:
            current = current / part
            if current.exists() and _is_link_or_reparse(current):
                raise ContractError(f"task path traverses a link or reparse point: {relative!r}")
        return candidate

    def _run_record(self, run_dir: Path) -> tuple[dict[str, Any], Path, str]:
        run_dir = run_dir.resolve()
        path = run_dir / "workflow/run.json"
        if _is_link_or_reparse(path) or not path.is_file():
            raise ArtifactDrift(
                "Task authority Run Record is absent or linked",
                data={"drifted_paths": ["workflow/run.json"]},
            )
        record = read_json(path)
        self.contracts.validate_run_record(record)
        if Path(record["output_path"]).resolve() != run_dir:
            raise KernelConflict("Task authority Run Record path binding disagrees")
        return record, path, sha256_file(path)

    def _protected_run_snapshot(
        self, run_dir: Path, *, task_root_path: str
    ) -> list[dict[str, str]]:
        root = run_dir.resolve()
        dynamic_kernel_namespaces = (
            "workflow/tasks",
            # Provider workers stage media here. Candidate Inventory evidence
            # authenticates the exact file set at Completion Gate.
            "work/source-acquisition/candidates",
        )
        snapshot: list[dict[str, str]] = []
        pending = [root]
        while pending:
            directory = pending.pop()
            for entry in os.scandir(directory):
                path = Path(entry.path)
                relative = path.relative_to(root).as_posix()
                info = entry.stat(follow_symlinks=False)
                if entry.is_symlink() or (
                    getattr(info, "st_file_attributes", 0)
                    & stat.FILE_ATTRIBUTE_REPARSE_POINT
                ):
                    raise ContractError(
                        f"protected Run boundary contains a link or reparse point: {relative}"
                    )
                if any(
                    relative == prefix or relative.startswith(f"{prefix}/")
                    for prefix in dynamic_kernel_namespaces
                ):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    snapshot.append(
                        {
                            "path": relative,
                            "sha256": self._snapshot_directory_sha(relative),
                        }
                    )
                    pending.append(path)
                elif entry.is_file(follow_symlinks=False):
                    snapshot.append({"path": relative, "sha256": sha256_file(path)})
                else:
                    raise ContractError(
                        f"protected Run boundary contains an unsupported entry: {relative}"
                    )
        return sorted(snapshot, key=lambda item: item["path"])

    def _verify_protected_run_snapshot(
        self,
        run_dir: Path,
        envelope: dict[str, Any],
        *,
        allowed_replacements: dict[str, tuple[str | None, str]] | None = None,
    ) -> None:
        actual = self._protected_run_snapshot(
            run_dir, task_root_path=envelope["task_root_path"]
        )
        expected = {
            item["path"]: item["sha256"]
            for item in envelope["protected_run_snapshot"]
        }
        observed = {item["path"]: item["sha256"] for item in actual}
        for path, (prior_sha, new_sha) in (allowed_replacements or {}).items():
            expected_prior = expected.get(path)
            actual_sha = observed.get(path)
            if expected_prior == prior_sha and actual_sha in {prior_sha, new_sha}:
                if actual_sha is None:
                    expected.pop(path, None)
                else:
                    expected[path] = actual_sha
        if observed != expected:
            protected_outputs = set(envelope["write_set"])
            output_violations = sorted(
                path
                for path in protected_outputs
                if observed.get(path) != expected.get(path)
            )
            if output_violations:
                raise ArtifactDrift(
                    "Worker wrote a canonical output before promotion",
                    data={"drifted_paths": output_violations},
                )
            raise ContractError(
                "Run-wide worker write boundary differs from the Task Envelope snapshot",
                data={
                    "unexpected_paths": sorted(set(observed) - set(expected)),
                    "missing_paths": sorted(set(expected) - set(observed)),
                    "changed_paths": sorted(
                        path
                        for path in set(expected) & set(observed)
                        if expected[path] != observed[path]
                    ),
                },
            )

    def _verify_task_root_inventory(
        self,
        run_dir: Path,
        *,
        run_id: str,
        skip_task_id: str | None = None,
        skip_attempt_id: str | None = None,
    ) -> None:
        if (skip_task_id is None) != (skip_attempt_id is None):
            raise ContractError(
                "Task namespace verification requires one complete skip binding"
            )
        store = self.kernel._preflight_control_store()
        namespace = run_dir / "workflow/tasks"
        if _is_link_or_reparse(namespace) or not namespace.is_dir():
            raise ArtifactDrift("Task namespace is absent or linked")
        durable_task_ids = store.task_ids_for_authority(run_id)
        observed_task_ids: set[str] = set()
        with os.scandir(namespace) as entries:
            for entry in entries:
                info = entry.stat(follow_symlinks=False)
                if (
                    entry.is_symlink()
                    or not entry.is_dir(follow_symlinks=False)
                    or getattr(info, "st_file_attributes", 0)
                    & stat.FILE_ATTRIBUTE_REPARSE_POINT
                ):
                    raise ContractError(
                        f"Task namespace contains an invalid entry: {entry.name}"
                    )
                observed_task_ids.add(entry.name)
        missing_task_ids = durable_task_ids - observed_task_ids
        if missing_task_ids:
            raise ContractError(
                "Task namespace differs from durable Claim authority",
                data={
                    "unexpected_task_roots": [],
                    "missing_task_roots": sorted(missing_task_ids),
                },
            )
        for prepared_task_id in sorted(observed_task_ids - durable_task_ids):
            self._verify_unclaimed_prepared_task(
                run_dir,
                namespace / prepared_task_id,
                run_id=run_id,
                task_id=prepared_task_id,
            )
        for durable_task_id in sorted(durable_task_ids):
            task_dir = namespace / durable_task_id
            self._verify_one_task_root(
                task_dir,
                run_id=run_id,
                task_id=durable_task_id,
                current_attempt_id=(
                    skip_attempt_id
                    if durable_task_id == skip_task_id
                    else None
                ),
            )

    def _verify_unclaimed_prepared_task(
        self,
        run_dir: Path,
        task_dir: Path,
        *,
        run_id: str,
        task_id: str,
    ) -> None:
        """Authenticate a reproducible pre-Claim root against the current Run."""
        if _is_link_or_reparse(task_dir) or not task_dir.is_dir():
            raise ArtifactDrift("prepared Task root is absent or linked")
        actual_files: set[str] = set()
        actual_dirs: set[str] = set()
        with os.scandir(task_dir) as entries:
            for entry in entries:
                info = entry.stat(follow_symlinks=False)
                if entry.is_symlink() or (
                    getattr(info, "st_file_attributes", 0)
                    & stat.FILE_ATTRIBUTE_REPARSE_POINT
                ):
                    raise ArtifactDrift("prepared Task root contains a linked entry")
                if entry.is_file(follow_symlinks=False):
                    actual_files.add(entry.name)
                elif entry.is_dir(follow_symlinks=False):
                    actual_dirs.add(entry.name)
                else:
                    raise ContractError(
                        "prepared Task root contains an unsupported entry"
                    )
        if actual_files not in ({"task.json"}, {"task.json", "prompt.md"}) or actual_dirs:
            raise ContractError(
                "unclaimed Task root differs from its exact prepared inventory"
            )
        envelope_path = task_dir / "task.json"
        try:
            envelope = read_json(envelope_path)
            self.contracts.validate("subagent-task-envelope", envelope)
        except (OSError, UnicodeError, ValueError, ContractError) as exc:
            raise ArtifactDrift("prepared Task Envelope is invalid") from exc
        if (
            envelope.get("task_id") != task_id
            or envelope.get("task_root_path") != f"workflow/tasks/{task_id}"
            or envelope.get("authority_binding", {}).get("run_id") != run_id
        ):
            raise ArtifactDrift("prepared Task identity binding drifted")
        expected_root_files = {"task.json"}
        if envelope.get("generated_prompt") is not None:
            expected_root_files.add("prompt.md")
        if actual_files != expected_root_files:
            raise ContractError(
                "unclaimed Task root differs from its declared Prompt inventory"
            )
        record, _, _ = self._run_record(run_dir)
        expected, prompt = self._rebuild_envelope(run_dir, record, envelope)
        if expected["task_id"] != task_id:
            raise ArtifactDrift("prepared Task deterministic identity drifted")
        self._verify_task_files(run_dir, expected, prompt)

    def _verify_envelope_recorded_prompt(
        self,
        task_dir: Path,
        envelope: dict[str, Any],
        *,
        task_id: str,
    ) -> None:
        """Verify a durable Prompt from its immutable Task Envelope authority."""
        generated_prompt = envelope["generated_prompt"]
        if generated_prompt is None:
            if (task_dir / "prompt.md").exists():
                raise ArtifactDrift("prompt-free Task contains an undeclared Prompt")
            return
        expected_path = f"workflow/tasks/{task_id}/prompt.md"
        prompt_path = task_dir / "prompt.md"
        # Contract validation closes and versions both provenance records.  The
        # durable Claim authenticates the full Envelope fingerprint, including
        # those exact historical fields; replay must never reinterpret them
        # through whichever Prompt generator happens to be installed now.
        if (
            generated_prompt["path"] != expected_path
            or _is_link_or_reparse(prompt_path)
            or not prompt_path.is_file()
            or sha256_file(prompt_path) != generated_prompt["sha256"]
        ):
            raise ArtifactDrift(
                "Task Envelope-recorded Generated Prompt authority drifted"
            )

    def _verify_one_task_root(
        self,
        task_dir: Path,
        *,
        run_id: str,
        task_id: str,
        current_attempt_id: str | None,
    ) -> None:
        if _is_link_or_reparse(task_dir) or not task_dir.is_dir():
            raise ArtifactDrift("durable Task root is absent or linked")
        envelope_path = task_dir / "task.json"
        if _is_link_or_reparse(envelope_path) or not envelope_path.is_file():
            raise ArtifactDrift("durable Task Envelope is absent or linked")
        try:
            envelope = read_json(envelope_path)
            self.contracts.validate("subagent-task-envelope", envelope)
        except (OSError, UnicodeError, ValueError, ContractError) as exc:
            raise ArtifactDrift("durable Task Envelope is invalid") from exc
        expected_root_files = {"task.json"}
        if envelope.get("generated_prompt") is not None:
            expected_root_files.add("prompt.md")
        expected_root_dirs = {"attempts"}
        actual_root_files: set[str] = set()
        actual_root_dirs: set[str] = set()
        with os.scandir(task_dir) as entries:
            for entry in entries:
                info = entry.stat(follow_symlinks=False)
                if entry.is_symlink() or (
                    getattr(info, "st_file_attributes", 0)
                    & stat.FILE_ATTRIBUTE_REPARSE_POINT
                ):
                    raise ContractError(
                        f"Task root contains a link or reparse point: {entry.name}"
                    )
                if entry.is_dir(follow_symlinks=False):
                    actual_root_dirs.add(entry.name)
                elif entry.is_file(follow_symlinks=False):
                    actual_root_files.add(entry.name)
                else:
                    raise ContractError(
                        f"Task root contains an unsupported entry: {entry.name}"
                    )
        if (
            actual_root_files != expected_root_files
            or actual_root_dirs != expected_root_dirs
        ):
            raise ContractError("Task root inventory contains undeclared entries")
        store = self.kernel._preflight_control_store()
        claim = store.task_claim_for_task(task_id)
        if (
            claim is None
            or claim["authority_id"] != run_id
            or claim["envelope_sha256"] != sha256_file(envelope_path)
            or envelope["task_id"] != task_id
            or envelope["task_root_path"] != f"workflow/tasks/{task_id}"
            or envelope["authority_binding"]["run_id"] != run_id
        ):
            raise ArtifactDrift(
                "Task namespace Envelope or Generated Prompt authority drifted"
            )
        self._verify_envelope_recorded_prompt(
            task_dir,
            envelope,
            task_id=task_id,
        )
        attempts_root = task_dir / "attempts"
        if _is_link_or_reparse(attempts_root) or not attempts_root.is_dir():
            raise ArtifactDrift("durable Task Attempts namespace is absent or linked")
        durable_attempts = store.task_attempts_for_task(task_id)
        known_attempts = {str(row["attempt_id"]) for row in durable_attempts}
        if current_attempt_id is not None and current_attempt_id not in known_attempts:
            raise ControlStoreUnavailable("current Task Attempt lacks durable authority")
        observed_attempts: set[str] = set()
        with os.scandir(attempts_root) as entries:
            for entry in entries:
                info = entry.stat(follow_symlinks=False)
                if (
                    entry.is_symlink()
                    or not entry.is_dir(follow_symlinks=False)
                    or getattr(info, "st_file_attributes", 0)
                    & stat.FILE_ATTRIBUTE_REPARSE_POINT
                ):
                    raise ContractError(
                        "Task attempts namespace contains an invalid entry: "
                        f"{entry.name}"
                    )
                observed_attempts.add(entry.name)
        if observed_attempts != known_attempts:
            raise ContractError(
                "Task attempts namespace differs from durable Attempt authority"
            )
        for attempt in durable_attempts:
            attempt_id = str(attempt["attempt_id"])
            if attempt_id == current_attempt_id:
                continue
            self._verify_durable_attempt_boundary(
                attempts_root / attempt_id,
                envelope=envelope,
                attempt=attempt,
                current_claim=claim,
            )

    def _verify_attempt_record(
        self,
        attempt_dir: Path,
        *,
        envelope: dict[str, Any],
        attempt: Any,
        current_claim: Any,
    ) -> dict[str, Any]:
        path = attempt_dir / "attempt.json"
        require_contained_path(
            path,
            attempt_dir,
            purpose="Task Attempt record",
            error_type=ArtifactDrift,
            leaf_kind="file",
            require_single_link=True,
        )
        try:
            record = read_json(path)
            self.contracts.validate("task-attempt", record)
        except (OSError, UnicodeError, ValueError, ContractError) as exc:
            raise ArtifactDrift("Task Attempt record is invalid") from exc
        store = self.kernel._preflight_control_store()
        authority = store.task_attempt_authority(str(attempt["attempt_id"]))
        if authority is None:
            raise ControlStoreUnavailable(
                "Task Attempt lacks immutable record authority"
            )
        try:
            authority_record = json.loads(str(authority["attempt_record_json"]))
        except json.JSONDecodeError as exc:
            raise ControlStoreUnavailable(
                "Task Attempt record authority is invalid"
            ) from exc
        canonical = canonical_json_bytes(authority_record)
        if (
            canonical.decode("utf-8") != authority["attempt_record_json"]
            or hashlib.sha256(canonical).hexdigest()
            != authority["attempt_record_sha256"]
            or path.read_bytes() != canonical
            or record != authority_record
        ):
            raise ArtifactDrift(
                "Task Attempt record differs from immutable durable authority"
            )
        expected = {
            "task_id": envelope["task_id"],
            "attempt_id": str(attempt["attempt_id"]),
            "claim_generation": int(attempt["claim_generation"]),
            "task_envelope_sha256": str(current_claim["envelope_sha256"]),
            "attempt_path": str(attempt["attempt_path"]),
            "state": "claimed",
        }
        if str(current_claim["attempt_id"]) == str(attempt["attempt_id"]):
            expected.update(
                {
                    "coordinator_session_id": str(
                        current_claim["coordinator_session_id"]
                    ),
                    "worker_id": str(current_claim["worker_id"]),
                    "claimed_at": str(current_claim["updated_at"]),
                }
            )
        if any(record.get(key) != value for key, value in expected.items()):
            raise ArtifactDrift("Task Attempt record disagrees with durable authority")
        return record

    @staticmethod
    def _attempt_entries(attempt_dir: Path) -> tuple[set[str], set[str]]:
        if _is_link_or_reparse(attempt_dir) or not attempt_dir.is_dir():
            raise ContractError("Task Attempt boundary is absent or linked")
        actual_dirs: set[str] = set()
        actual_files: set[str] = set()
        pending = [(attempt_dir, "")]
        while pending:
            directory, prefix = pending.pop()
            with os.scandir(directory) as entries:
                for entry in entries:
                    relative = f"{prefix}/{entry.name}".lstrip("/")
                    info = entry.stat(follow_symlinks=False)
                    if entry.is_symlink() or (
                        getattr(info, "st_file_attributes", 0)
                        & stat.FILE_ATTRIBUTE_REPARSE_POINT
                    ):
                        raise ContractError(
                            "Task Attempt contains a link or reparse point: "
                            f"{relative}"
                        )
                    if entry.is_dir(follow_symlinks=False):
                        actual_dirs.add(relative)
                        pending.append((Path(entry.path), relative))
                    elif entry.is_file(follow_symlinks=False):
                        # Windows DirEntry.stat() may report st_nlink=0 from its
                        # directory-enumeration cache. lstat() performs the
                        # authoritative file query used by require_contained_path.
                        if os.lstat(entry.path).st_nlink != 1:
                            raise ContractError(
                                "Task Attempt file is not an independent regular file: "
                                f"{relative}"
                            )
                        actual_files.add(relative)
                    else:
                        raise ContractError(
                            "Task Attempt contains an unsupported entry: "
                            f"{relative}"
                        )
        return actual_dirs, actual_files

    @staticmethod
    def _provider_candidate_canonical_root(
        envelope: dict[str, Any],
    ) -> PurePosixPath | None:
        if (
            envelope.get("schema_version") != "3.0.0"
            or envelope.get("task_stage") != "provider_acquisition"
        ):
            return None
        return PurePosixPath(
            "work/source-acquisition/candidates"
        ) / f"e{envelope['source_epoch']}"

    def _provider_candidate_specs(
        self,
        envelope: dict[str, Any],
        attempt_dir: Path,
        *,
        require_inventory: bool,
    ) -> list[dict[str, Any]]:
        canonical_root = self._provider_candidate_canonical_root(envelope)
        if canonical_root is None:
            return []
        inventory_output = next(
            output
            for output in envelope["required_outputs"]
            if output["logical_id"] == "source_candidate_inventory"
        )
        inventory_path = self._safe_run_path(
            attempt_dir, inventory_output["attempt_relative_path"]
        )
        if not inventory_path.exists():
            if require_inventory:
                raise ContractError(
                    "provider Task Attempt lacks its Candidate Inventory"
                )
            return []
        require_contained_path(
            inventory_path,
            attempt_dir,
            purpose="provider Task Attempt Candidate Inventory",
            error_type=ContractError,
            leaf_kind="file",
            require_single_link=True,
        )
        try:
            inventory = read_json(inventory_path)
            self.contracts.validate("source-candidate-inventory", inventory)
        except (OSError, UnicodeError, ValueError, ContractError) as exc:
            raise ContractError(
                "provider Task Attempt Candidate Inventory is invalid"
            ) from exc
        if (
            inventory["mode"] != "fresh_download"
            or inventory["run_id"] != envelope["authority_binding"]["run_id"]
            or inventory["source_epoch"] != envelope["source_epoch"]
        ):
            raise ContractError(
                "provider Candidate Inventory differs from its Task ownership"
            )
        result: list[dict[str, Any]] = []
        seen_attempt_paths: set[str] = set()
        for candidate in inventory["candidates"]:
            canonical = PurePosixPath(candidate["staged_path"])
            try:
                relative = canonical.relative_to(canonical_root)
            except ValueError as exc:
                raise ContractError(
                    "provider Candidate path differs from its Source Epoch root"
                ) from exc
            if not relative.parts:
                raise ContractError("provider Candidate path lacks a file name")
            attempt_path = (PurePosixPath("o/candidates") / relative).as_posix()
            if attempt_path in seen_attempt_paths:
                raise ContractError("provider Candidate Attempt paths collide")
            seen_attempt_paths.add(attempt_path)
            result.append(
                {
                    "logical_id": f"source_candidate_{candidate['candidate_id']}",
                    "attempt_path": attempt_path,
                    "canonical_path": canonical.as_posix(),
                    "sha256": candidate["sha256"],
                    "size_bytes": candidate["size_bytes"],
                }
            )
        return result

    def _declared_attempt_inventory(
        self,
        envelope: dict[str, Any],
        *,
        completion_expected: bool,
        promotion_journal_expected: bool,
        attempt_dir: Path | None = None,
        provider_inventory_required: bool = False,
    ) -> tuple[set[str], set[str]]:
        directories: set[str] = set()
        files = {"attempt.json"}
        outputs = [
            {"attempt_path": output["attempt_relative_path"]}
            for output in envelope["required_outputs"]
        ]
        if self._provider_candidate_canonical_root(envelope) is not None:
            if attempt_dir is None:
                if provider_inventory_required:
                    raise ContractError(
                        "provider Attempt inventory expansion lacks its boundary"
                    )
            else:
                outputs.extend(
                    self._provider_candidate_specs(
                        envelope,
                        attempt_dir,
                        require_inventory=provider_inventory_required,
                    )
                )
        for output in outputs:
            relative = PurePosixPath(output["attempt_path"])
            files.add(relative.as_posix())
            parent = relative.parent
            while str(parent) != ".":
                directories.add(parent.as_posix())
                parent = parent.parent
        if completion_expected:
            files.add("completion.json")
        if promotion_journal_expected:
            files.add("p.json")
        return directories, files

    @staticmethod
    def _validate_srt_bytes(payload: bytes) -> None:
        """Validate the canonical UTF-8/LF SubRip byte contract."""
        if (
            not payload
            or b"\r" in payload
            or not payload.endswith(b"\n")
            or payload.endswith(b"\n\n")
        ):
            raise ContractError(
                "Whisper transcript must be non-empty canonical UTF-8/LF SRT"
            )
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ContractError("Whisper transcript must be valid UTF-8") from exc
        blocks = text.rstrip("\n").split("\n\n")
        timestamp = re.compile(
            r"^(\d{2}):(\d{2}):(\d{2}),(\d{3}) --> "
            r"(\d{2}):(\d{2}):(\d{2}),(\d{3})$"
        )

        def milliseconds(parts: tuple[str, ...]) -> int:
            hours, minutes, seconds, millis = (int(value) for value in parts)
            if minutes > 59 or seconds > 59:
                raise ContractError("Whisper transcript timestamp is invalid")
            return (((hours * 60) + minutes) * 60 + seconds) * 1000 + millis

        prior_end = -1
        for expected_index, block in enumerate(blocks, start=1):
            lines = block.split("\n")
            if (
                len(lines) < 3
                or lines[0] != str(expected_index)
                or any(not line.strip() for line in lines[2:])
            ):
                raise ContractError("Whisper transcript SRT block is invalid")
            match = timestamp.fullmatch(lines[1])
            if match is None:
                raise ContractError("Whisper transcript timestamp is invalid")
            values = match.groups()
            start = milliseconds(values[:4])
            end = milliseconds(values[4:])
            if start < prior_end or end <= start:
                raise ContractError(
                    "Whisper transcript cues overlap or have an empty range"
                )
            prior_end = end

    def _verify_durable_attempt_boundary(
        self,
        attempt_dir: Path,
        *,
        envelope: dict[str, Any],
        attempt: Any,
        current_claim: Any,
    ) -> None:
        actual_dirs, actual_files = self._attempt_entries(attempt_dir)
        state = str(attempt["state"])
        completion_json = attempt["completion_record_json"]
        promotion_journal_sha = attempt["promotion_journal_sha256"]
        expected_dirs, expected_files = self._declared_attempt_inventory(
            envelope,
            completion_expected=completion_json is not None,
            promotion_journal_expected=promotion_journal_sha is not None,
            attempt_dir=attempt_dir,
            provider_inventory_required=(
                state in {"VALIDATED_WAITING_FOR_PROMOTION", "COMMITTED_COMPLETE"}
            ),
        )
        allowed_dirs = set(expected_dirs)
        allowed_files = set(expected_files)
        if state in {"CLAIMED", "ABANDONED", "FAILED"}:
            for output in envelope["required_outputs"]:
                relative = PurePosixPath(output["attempt_relative_path"])
                allowed_files.add(
                    (relative.parent / f".{relative.name}.kernel-new").as_posix()
                )
        provider_attempt_prefix = "o/candidates"
        unexpected_dirs = actual_dirs - allowed_dirs
        unexpected_files = actual_files - allowed_files
        if state in {"CLAIMED", "ABANDONED", "FAILED"} and (
            self._provider_candidate_canonical_root(envelope) is not None
        ):
            unexpected_dirs = {
                path
                for path in unexpected_dirs
                if path != provider_attempt_prefix
                and not path.startswith(f"{provider_attempt_prefix}/")
            }
            unexpected_files = {
                path
                for path in unexpected_files
                if not path.startswith(f"{provider_attempt_prefix}/")
            }
        if (
            "attempt.json" not in actual_files
            or unexpected_dirs
            or unexpected_files
        ):
            raise ContractError(
                "durable Task Attempt contains content outside its closed staging boundary"
            )
        self._verify_attempt_record(
            attempt_dir,
            envelope=envelope,
            attempt=attempt,
            current_claim=current_claim,
        )
        if state in {"VALIDATED_WAITING_FOR_PROMOTION", "COMMITTED_COMPLETE"}:
            exact_dirs, exact_files = self._declared_attempt_inventory(
                envelope,
                completion_expected=True,
                promotion_journal_expected=state == "COMMITTED_COMPLETE",
                attempt_dir=attempt_dir,
                provider_inventory_required=True,
            )
            if actual_dirs != exact_dirs or actual_files != exact_files:
                raise ContractError(
                    "validated Task Attempt lacks its exact durable evidence inventory"
                )
        elif state not in {"CLAIMED", "ABANDONED", "STALE", "FAILED"}:
            raise ControlStoreUnavailable("durable Task Attempt state is unsupported")
        outputs: dict[str, bytes] = {}
        for output_spec in envelope["required_outputs"]:
            relative = output_spec["attempt_relative_path"]
            if relative not in actual_files:
                continue
            path = attempt_dir / relative
            require_contained_path(
                path,
                attempt_dir,
                purpose="durable Task Attempt output",
                error_type=ArtifactDrift,
                leaf_kind="file",
                require_single_link=True,
            )
            payload = path.read_bytes()
            if output_spec["schema_name"] == "source-transcription-srt":
                self._validate_srt_bytes(payload)
            else:
                try:
                    output = read_json(path)
                    self.contracts.validate(output_spec["schema_name"], output)
                except (OSError, UnicodeError, ValueError, ContractError) as exc:
                    raise ArtifactDrift(
                        "staged Task Attempt output is invalid"
                    ) from exc
                if envelope.get("schema_version") != "3.0.0" and (
                    output["task_id"] != envelope["task_id"]
                    or output["attempt_id"] != attempt["attempt_id"]
                    or output["task_envelope_sha256"]
                    != current_claim["envelope_sha256"]
                    or output["source_manifest_sha256"]
                    != envelope["input_artifacts"][0]["sha256"]
                ):
                    raise ArtifactDrift(
                        "staged Task Attempt output authority drifted"
                    )
            outputs[output_spec["logical_id"]] = payload
        provider_candidates = self._provider_candidate_specs(
            envelope,
            attempt_dir,
            require_inventory=completion_json is not None,
        )
        if completion_json is not None:
            for candidate in provider_candidates:
                path = self._safe_run_path(
                    attempt_dir, candidate["attempt_path"]
                )
                require_contained_path(
                    path,
                    attempt_dir,
                    purpose="durable provider Candidate output",
                    error_type=ArtifactDrift,
                    leaf_kind="file",
                    require_single_link=True,
                )
                payload = path.read_bytes()
                if (
                    len(payload) != candidate["size_bytes"]
                    or hashlib.sha256(payload).hexdigest()
                    != candidate["sha256"]
                ):
                    raise ArtifactDrift(
                        "durable provider Candidate output fingerprint drifted"
                    )
                outputs[candidate["logical_id"]] = payload
        if completion_json is None:
            if "completion.json" in actual_files:
                raise ArtifactDrift("Task Completion evidence lacks durable authority")
        else:
            if (
                not outputs
                or "completion.json" not in actual_files
            ):
                raise ArtifactDrift(
                    "durable Task Completion lost its staged output or record"
                )
            try:
                completion = json.loads(str(completion_json))
                self.contracts.validate("task-completion-record", completion)
            except (TypeError, ValueError, ContractError) as exc:
                raise ArtifactDrift("durable Task Completion authority is invalid") from exc
            completion_path = attempt_dir / "completion.json"
            try:
                require_contained_path(
                    completion_path,
                    attempt_dir,
                    purpose="durable Task Completion evidence",
                    error_type=ArtifactDrift,
                    leaf_kind="file",
                    require_single_link=True,
                )
                completion_file = read_json(completion_path)
            except (OSError, UnicodeError, ValueError) as exc:
                raise ArtifactDrift("durable Task Completion evidence is invalid") from exc
            if (
                canonical_json_bytes(completion).decode("utf-8")
                != str(completion_json)
                or _is_link_or_reparse(completion_path)
                or completion_file != completion
                or sha256_file(completion_path) != attempt["completion_sha256"]
                or completion["task_id"] != envelope["task_id"]
                or completion["attempt_id"] != attempt["attempt_id"]
                or int(completion["claim_generation"])
                != int(attempt["claim_generation"])
                or completion["task_envelope_sha256"]
                != current_claim["envelope_sha256"]
            ):
                raise ArtifactDrift("durable Task Completion evidence drifted")
            expected_inputs = [
                {
                    "logical_id": item["logical_id"],
                    "generation": item["generation"],
                    "sha256": item["sha256"],
                }
                for item in envelope["input_artifacts"]
            ]
            expected_outputs = [
                {
                    "logical_id": item["logical_id"],
                    "attempt_path": item["attempt_relative_path"],
                    "canonical_path": item["canonical_path"],
                    "sha256": hashlib.sha256(outputs[item["logical_id"]]).hexdigest(),
                }
                for item in envelope["required_outputs"]
            ]
            expected_outputs.extend(
                {
                    "logical_id": item["logical_id"],
                    "attempt_path": item["attempt_path"],
                    "canonical_path": item["canonical_path"],
                    "sha256": item["sha256"],
                }
                for item in provider_candidates
            )
            if (
                completion["validated_inputs"] != expected_inputs
                or completion["outputs"] != expected_outputs
            ):
                raise ArtifactDrift("durable Task Completion bindings drifted")
        journal_path = attempt_dir / "p.json"
        if promotion_journal_sha is None:
            if journal_path.exists():
                raise ArtifactDrift("Task promotion journal lacks durable authority")
            return
        store = self.kernel._preflight_control_store()
        intent = store.task_promotion_for_attempt(
            str(envelope["task_id"]), str(attempt["attempt_id"])
        )
        if intent is None:
            raise ControlStoreUnavailable(
                "Task promotion journal lacks durable intent authority"
            )
        try:
            require_contained_path(
                journal_path,
                attempt_dir,
                purpose="durable Task promotion journal",
                error_type=ArtifactDrift,
                leaf_kind="file",
                require_single_link=True,
            )
            outputs = json.loads(str(intent["outputs_json"]))
            journal = read_json(journal_path)
        except (OSError, UnicodeError, ValueError) as exc:
            raise ArtifactDrift("durable Task promotion journal is invalid") from exc
        if (
            _is_link_or_reparse(journal_path)
            or not journal_path.is_file()
            or sha256_file(journal_path) != promotion_journal_sha
            or journal != self._journal(intent, outputs)
        ):
            raise ArtifactDrift("durable Task promotion journal drifted")

    def _build_envelope(
        self,
        run_dir: Path,
        record: dict[str, Any],
        *,
        logical_task_key: str,
        prepared_at: str,
        required_resources: tuple[str, ...] | None,
        batch_id: str | None,
    ) -> tuple[dict[str, Any], bytes]:
        if not logical_task_key or any(
            char not in "abcdefghijklmnopqrstuvwxyz0123456789-"
            for char in logical_task_key
        ):
            raise ContractError("logical Task key must use lowercase kebab-case")
        try:
            parsed = datetime.fromisoformat(prepared_at)
        except ValueError as exc:
            raise ContractError("Task prepared_at must be ISO 8601") from exc
        if parsed.tzinfo is None:
            raise ContractError("Task prepared_at must include a timezone offset")
        source = record["artifact_generations"]["source_manifest"]
        source_manifest = read_json(run_dir / source["path"])
        self.contracts.validate("source-manifest", source_manifest)
        if sha256_file(run_dir / source["path"]) != source["sha256"]:
            raise ArtifactDrift(
                "Task input Source Manifest differs from its Artifact Generation",
                data={"drifted_paths": [source["path"]]},
            )
        prior = record["artifact_generations"].get("source_acquisition_decision")
        identity_fields = {
                "authority_kind": "kernel_run",
                "run_id": record["run_id"],
                "expected_coordination_revision": record["coordination_revision"],
                "role": "source_acquisition",
                "logical_task_key": logical_task_key,
                "source_manifest_generation": source["generation"],
                "source_manifest_sha256": source["sha256"],
                "prior_decision_generation": None if prior is None else prior["generation"],
                "prior_decision_sha256": None if prior is None else prior["sha256"],
            }
        if required_resources is not None:
            if (
                not required_resources
                or tuple(sorted(required_resources)) != required_resources
                or len(required_resources) != len(set(required_resources))
            ):
                raise ContractError(
                    "Resource Request must be non-empty, unique, and stably sorted"
                )
            identity_fields["resource_request"] = list(required_resources)
            identity_fields["fairness_group_id"] = batch_id or record["run_id"]
            identity_fields["batch_id"] = batch_id
        identity = canonical_json_bytes(identity_fields)
        task_id = hashlib.sha256(identity).hexdigest()[:32]
        task_root = f"workflow/tasks/{task_id}"
        prompt, provenance = generate_source_acquisition_prompt(self.project_root)
        allowed_read_paths = sorted(
            {source["path"], *(item["path"] for item in source_manifest["artifacts"])}
        )
        envelope = {
            "schema_name": "subagent-task-envelope",
            "schema_version": "2.0.0" if required_resources is not None else "1.0.0",
            "kernel_version": "2.0.0",
            "task_id": task_id,
            "logical_task_key": logical_task_key,
            "role": "source_acquisition",
            "authority_binding": {
                "kind": "kernel_run",
                "run_id": record["run_id"],
                "expected_coordination_revision": record["coordination_revision"],
                "target_checkpoint": "source_acquisition_decision_ready",
            },
            "prepared_at": prepared_at,
            "task_root_path": task_root,
            "input_artifacts": [
                {
                    "logical_id": "source_manifest",
                    "path": source["path"],
                    "generation": source["generation"],
                    "sha256": source["sha256"],
                }
            ],
            "allowed_read_paths": allowed_read_paths,
            "protected_run_snapshot": self._protected_run_snapshot(
                run_dir, task_root_path=task_root
            ),
            "write_set": ["workflow/source-acquisition-judgment-patch.json"],
            "required_outputs": [
                {
                    "logical_id": "source_acquisition_decision",
                    "attempt_relative_path": "o/p.json",
                    "canonical_path": "workflow/source-acquisition-judgment-patch.json",
                    "schema_name": "source-acquisition-judgment-patch",
                    "expected_prior_generation": None if prior is None else prior["generation"],
                    "expected_prior_sha256": None if prior is None else prior["sha256"],
                }
            ],
            "generated_prompt": {
                "path": f"{task_root}/prompt.md",
                **provenance,
            },
            "bounded_semantic_fields": [
                "selected_subtitle_track",
                "whisper_fallback.choice",
                "whisper_fallback.rationale",
                "known_gaps",
            ],
        }
        if required_resources is not None:
            envelope["resource_request"] = list(required_resources)
            envelope["fairness_group_id"] = batch_id or record["run_id"]
            if batch_id is not None:
                envelope["batch_id"] = batch_id
        self.contracts.validate("subagent-task-envelope", envelope)
        return envelope, prompt

    def _build_production_source_envelope(
        self,
        run_dir: Path,
        record: dict[str, Any],
        *,
        task_stage: str,
        logical_task_key: str,
        prepared_at: str,
        whisper_audio_candidate: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], bytes | None]:
        if record.get("schema_version") != "3.0.0":
            raise ContractError("production Source Tasks require Run Record v3")
        self._validate_production_task_identity_fields(
            task_stage=task_stage,
            logical_task_key=logical_task_key,
        )
        try:
            parsed = datetime.fromisoformat(prepared_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ContractError("Task prepared_at must be ISO 8601") from exc
        if parsed.tzinfo is None:
            raise ContractError("Task prepared_at must include a timezone offset")
        task_id = self._production_source_task_id(
            record,
            task_stage=task_stage,
            logical_task_key=logical_task_key,
        )
        task_root = f"workflow/tasks/{task_id}"
        generations = record["artifact_generations"]
        platform = record["canonical_platform"]

        def generation_binding(logical_id: str) -> dict[str, Any]:
            generation = generations.get(logical_id)
            if generation is None:
                raise ArtifactDrift(
                    f"production Source Task lacks {logical_id} authority"
                )
            path = self._safe_run_path(run_dir, generation["path"])
            if (
                _is_link_or_reparse(path)
                or not path.is_file()
                or sha256_file(path) != generation["sha256"]
            ):
                raise ArtifactDrift(
                    f"production Source Task {logical_id} generation drifted",
                    data={"drifted_paths": [generation["path"]]},
                )
            return {
                "logical_id": logical_id,
                "path": generation["path"],
                "generation": generation["generation"],
                "sha256": generation["sha256"],
            }

        prompt: bytes | None = None
        generated_prompt: dict[str, Any] | None = None
        bounded_fields: list[str] = []
        bound_whisper_candidate: dict[str, Any] | None = None
        if task_stage == "provider_acquisition":
            if record["source_state"] not in {"pending", "stale"}:
                raise KernelConflict(
                    "provider Source Task requires an open acquisition state"
                )
            if whisper_audio_candidate is not None:
                raise ContractError("provider Source Task cannot bind Whisper input")
            inputs = [generation_binding("bootstrap_record")]
            reads = [inputs[0]["path"]]
            writes = [
                "work/source-acquisition/candidate-inventory.json",
                "work/source-acquisition/decision.skeleton.json",
                (
                    "work/source-acquisition/candidates/"
                    f"e{record['source_epoch']}"
                ),
            ]
            output_specs = [
                (
                    "source_candidate_inventory",
                    "o/candidate-inventory.json",
                    writes[0],
                    "source-candidate-inventory",
                    "1.0.0",
                ),
                (
                    "source_acquisition_decision_skeleton",
                    "o/decision.skeleton.json",
                    writes[1],
                    "source-acquisition-decision-skeleton",
                    "1.0.0",
                ),
            ]
            target_checkpoint = "source_candidates_ready"
            resources = [f"{platform}_download"]
        elif task_stage == "semantic_judgment":
            if record["source_acquisition_mode"] != "fresh_download":
                raise KernelConflict("Verified Import has no semantic Source Task")
            if record["source_state"] != "candidates_ready":
                raise KernelConflict(
                    "semantic Source Task requires current candidate evidence"
                )
            if whisper_audio_candidate is not None:
                raise ContractError("semantic Source Task cannot bind Whisper input")
            inputs = [
                generation_binding("source_candidate_inventory"),
                generation_binding("source_acquisition_decision_skeleton"),
            ]
            skeleton = read_json(run_dir / inputs[1]["path"])
            self.contracts.validate(
                "source-acquisition-decision-skeleton", skeleton
            )
            if (
                skeleton["task_id"] != task_id
                or skeleton["run_id"] != record["run_id"]
                or skeleton["source_epoch"] != record["source_epoch"]
                or skeleton["candidate_inventory"]["generation"]
                != inputs[0]["generation"]
                or skeleton["candidate_inventory"]["sha256"]
                != inputs[0]["sha256"]
            ):
                raise ArtifactDrift(
                    "semantic Source Task Decision Skeleton binding drifted"
                )
            reads = [item["path"] for item in inputs]
            writes = ["workflow/source-acquisition-judgment-patch.json"]
            output_specs = [
                (
                    "source_acquisition_decision",
                    "o/p.json",
                    writes[0],
                    "source-acquisition-judgment-patch",
                    "2.0.0",
                )
            ]
            prompt, provenance = generate_source_acquisition_prompt(
                self.project_root,
                role_version="2.0.0",
                platform=platform,
                platform_version="1.0.0",
            )
            generated_prompt = {
                "path": f"{task_root}/prompt.md",
                **provenance,
            }
            bounded_fields = [
                "selected_subtitle_candidate_id",
                "subtitle_selection_rationale",
                "whisper_fallback.choice",
                "whisper_fallback.rationale",
                "known_gaps",
            ]
            target_checkpoint = "source_acquisition_decision_ready"
            resources = ["codex_semantic"]
        else:
            if record["source_acquisition_mode"] != "fresh_download":
                raise KernelConflict("Verified Import has no Whisper Source Task")
            if record["source_state"] != "decision_ready":
                raise KernelConflict(
                    "Whisper Source Task requires a current semantic Decision"
                )
            if whisper_audio_candidate is None:
                raise ContractError("Whisper Source Task requires an audio candidate")
            inputs = [
                generation_binding("source_candidate_inventory"),
                generation_binding("source_acquisition_decision_skeleton"),
                generation_binding("source_acquisition_decision"),
            ]
            inventory = read_json(run_dir / inputs[0]["path"])
            skeleton = read_json(run_dir / inputs[1]["path"])
            decision = read_json(run_dir / inputs[2]["path"])
            self.contracts.validate("source-candidate-inventory", inventory)
            self.contracts.validate(
                "source-acquisition-decision-skeleton", skeleton
            )
            self.contracts.validate(
                "source-acquisition-judgment-patch", decision
            )
            expected_candidate = next(
                (
                    candidate
                    for candidate in inventory["candidates"]
                    if candidate["candidate_id"]
                    == whisper_audio_candidate.get("candidate_id")
                ),
                None,
            )
            if (
                decision["judgment"]["whisper_fallback"]["choice"]
                != "use_whisper"
                or skeleton["allowed_judgment"]["whisper_audio_candidate_id"]
                != whisper_audio_candidate.get("candidate_id")
                or expected_candidate is None
                or expected_candidate["staged_path"]
                != whisper_audio_candidate.get("staged_path")
                or expected_candidate["sha256"]
                != whisper_audio_candidate.get("sha256")
            ):
                raise ContractError(
                    "Whisper Source Task audio candidate is outside its Decision authority"
                )
            audio_path = self._safe_run_path(
                run_dir,
                str(whisper_audio_candidate["staged_path"]),
                prefix="work",
            )
            if (
                _is_link_or_reparse(audio_path)
                or not audio_path.is_file()
                or sha256_file(audio_path) != whisper_audio_candidate["sha256"]
            ):
                raise ArtifactDrift("Whisper Source Task audio candidate drifted")
            reads = [
                *(item["path"] for item in inputs),
                str(whisper_audio_candidate["staged_path"]),
            ]
            writes = ["work/source-acquisition/transcription.srt"]
            output_specs = [
                (
                    "source_transcription",
                    "o/transcription.srt",
                    writes[0],
                    "source-transcription-srt",
                    "1.0.0",
                )
            ]
            target_checkpoint = "source_acquisition_decision_ready"
            resources = ["whisper"]
            bound_whisper_candidate = dict(whisper_audio_candidate)

        required_outputs = []
        for (
            logical_id,
            attempt_relative_path,
            canonical_path,
            schema_name,
            schema_version,
        ) in output_specs:
            prior = generations.get(logical_id)
            current_epoch_prior = (
                prior
                if prior is not None
                and int(prior.get("source_epoch", -1)) == record["source_epoch"]
                else None
            )
            required_outputs.append(
                {
                    "logical_id": logical_id,
                    "attempt_relative_path": attempt_relative_path,
                    "canonical_path": canonical_path,
                    "schema_name": schema_name,
                    "schema_version": schema_version,
                    "expected_prior_generation": (
                        None
                        if current_epoch_prior is None
                        else current_epoch_prior["generation"]
                    ),
                    "expected_prior_sha256": (
                        None
                        if current_epoch_prior is None
                        else current_epoch_prior["sha256"]
                    ),
                }
            )
        envelope = {
            "schema_name": "subagent-task-envelope",
            "schema_version": "3.0.0",
            "kernel_version": "2.0.0",
            "task_id": task_id,
            "logical_task_key": logical_task_key,
            "role": "source_acquisition",
            "task_stage": task_stage,
            "platform": platform,
            "source_epoch": record["source_epoch"],
            "authority_binding": {
                "kind": "kernel_run",
                "run_id": record["run_id"],
                "expected_coordination_revision": record[
                    "coordination_revision"
                ],
                "target_checkpoint": target_checkpoint,
            },
            "prepared_at": prepared_at,
            "task_root_path": task_root,
            "input_artifacts": inputs,
            "allowed_read_paths": reads,
            "protected_run_snapshot": self._protected_run_snapshot(
                run_dir, task_root_path=task_root
            ),
            "write_set": writes,
            "required_outputs": required_outputs,
            "generated_prompt": generated_prompt,
            "bounded_semantic_fields": bounded_fields,
            "whisper_audio_candidate": bound_whisper_candidate,
            "resource_request": resources,
            "fairness_group_id": record["run_id"],
        }
        self.contracts.validate("subagent-task-envelope", envelope)
        return envelope, prompt

    def _rebuild_envelope(
        self,
        run_dir: Path,
        record: dict[str, Any],
        envelope: dict[str, Any],
    ) -> tuple[dict[str, Any], bytes | None]:
        if envelope.get("schema_version") == "3.0.0":
            return self._build_production_source_envelope(
                run_dir,
                record,
                task_stage=str(envelope["task_stage"]),
                logical_task_key=str(envelope["logical_task_key"]),
                prepared_at=str(envelope["prepared_at"]),
                whisper_audio_candidate=envelope.get("whisper_audio_candidate"),
            )
        return self._build_envelope(
            run_dir,
            record,
            logical_task_key=str(envelope["logical_task_key"]),
            prepared_at=str(envelope["prepared_at"]),
            required_resources=(
                tuple(envelope["resource_request"])
                if envelope.get("schema_version") == "2.0.0"
                else None
            ),
            batch_id=envelope.get("batch_id"),
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
    ) -> TaskPreparationResult:
        if fault_point is not None and fault_point not in PREPARATION_FAULT_POINTS:
            raise ContractError(f"unknown Task preparation fault point: {fault_point}")
        run_dir = run_dir.resolve()
        self.kernel.reconcile_run(run_dir)
        store = self.kernel._preflight_control_store()
        record, _, _ = self._run_record(run_dir)
        if store.active_task_promotion(record["run_id"]) is not None:
            raise KernelConflict("Task preparation is blocked by a non-terminal promotion")
        (run_dir / "待删除/task-preparations").mkdir(parents=True, exist_ok=True)
        (run_dir / "待删除/task-attempts").mkdir(parents=True, exist_ok=True)
        envelope, prompt = self._build_production_source_envelope(
            run_dir,
            record,
            task_stage=task_stage,
            logical_task_key=logical_task_key,
            prepared_at=prepared_at,
            whisper_audio_candidate=whisper_audio_candidate,
        )
        task_id = envelope["task_id"]
        task_dir = self._safe_run_path(
            run_dir, envelope["task_root_path"], prefix="workflow"
        )
        if task_dir.exists():
            self._verify_task_files(run_dir, envelope, prompt)
        else:
            staging = run_dir / "待删除/task-preparations" / uuid.uuid4().hex
            staging.mkdir(parents=True, exist_ok=False)
            if prompt is not None:
                _write_bytes_atomic(staging / "prompt.md", prompt)
            write_json_atomic(staging / "task.json", envelope)
            try:
                os.replace(staging, task_dir)
            except FileExistsError:
                self._verify_task_files(run_dir, envelope, prompt)
            _inject(fault_point, "after_task_root_published")
        return TaskPreparationResult(
            run_id=record["run_id"],
            run_dir=run_dir,
            task_id=task_id,
            task_dir=task_dir,
            envelope_path=task_dir / "task.json",
            prompt_path=task_dir / "prompt.md",
        )

    def prepare_source_acquisition_task(
        self,
        run_dir: Path,
        *,
        logical_task_key: str,
        prepared_at: str,
        required_resources: tuple[str, ...] | None = ("codex_semantic",),
        batch_id: str | None = None,
        fault_point: str | None = None,
    ) -> TaskPreparationResult:
        if fault_point is not None and fault_point not in PREPARATION_FAULT_POINTS:
            raise ContractError(f"unknown Task preparation fault point: {fault_point}")
        run_dir = run_dir.resolve()
        # Task creation is a Run resume boundary.  Reconcile every registered
        # Source and Task authority before deriving or writing another Task
        # root so durable Task evidence drift cannot be hidden by later work.
        self.kernel.reconcile_run(run_dir)
        store = self.kernel._preflight_control_store()
        record, _, _ = self._run_record(run_dir)
        if store.active_task_promotion(record["run_id"]) is not None:
            raise KernelConflict("Task preparation is blocked by a non-terminal promotion")
        (run_dir / "待删除/task-preparations").mkdir(parents=True, exist_ok=True)
        (run_dir / "待删除/task-attempts").mkdir(parents=True, exist_ok=True)
        envelope, prompt = self._build_envelope(
            run_dir,
            record,
            logical_task_key=logical_task_key,
            prepared_at=prepared_at,
            required_resources=required_resources,
            batch_id=batch_id,
        )
        task_id = envelope["task_id"]
        task_dir = self._safe_run_path(
            run_dir, envelope["task_root_path"], prefix="workflow"
        )
        envelope_path = task_dir / "task.json"
        prompt_path = task_dir / "prompt.md"
        if task_dir.exists():
            self._verify_task_files(run_dir, envelope, prompt)
        else:
            staging = (
                run_dir
                / "待删除/task-preparations"
                / uuid.uuid4().hex
            )
            staging.mkdir(parents=True, exist_ok=False)
            _write_bytes_atomic(staging / "prompt.md", prompt)
            write_json_atomic(staging / "task.json", envelope)
            try:
                os.replace(staging, task_dir)
            except FileExistsError:
                self._verify_task_files(run_dir, envelope, prompt)
            _inject(fault_point, "after_task_root_published")
        return TaskPreparationResult(
            run_id=record["run_id"],
            run_dir=run_dir,
            task_id=task_id,
            task_dir=task_dir,
            envelope_path=envelope_path,
            prompt_path=prompt_path,
        )

    def _verify_task_files(
        self,
        run_dir: Path,
        expected_envelope: dict[str, Any],
        expected_prompt: bytes | None,
    ) -> None:
        task_dir = self._safe_run_path(
            run_dir, expected_envelope["task_root_path"], prefix="workflow"
        )
        if _is_link_or_reparse(task_dir) or not task_dir.is_dir():
            raise ArtifactDrift("Task directory is absent or linked")
        envelope_path = task_dir / "task.json"
        prompt_path = task_dir / "prompt.md"
        if _is_link_or_reparse(envelope_path) or not envelope_path.is_file():
            raise ArtifactDrift("Task Envelope is absent or linked")
        if expected_prompt is None:
            if prompt_path.exists():
                raise ArtifactDrift("prompt-free Task contains an undeclared Prompt")
        elif _is_link_or_reparse(prompt_path) or not prompt_path.is_file():
            raise ArtifactDrift("Generated Task Prompt is absent or linked")
        actual_envelope = read_json(envelope_path)
        self.contracts.validate("subagent-task-envelope", actual_envelope)
        if actual_envelope != expected_envelope or (
            expected_prompt is not None and prompt_path.read_bytes() != expected_prompt
        ):
            raise ArtifactDrift("Task Envelope or Generated Task Prompt drifted")
        if expected_prompt is not None and (
            sha256_file(prompt_path)
            != actual_envelope["generated_prompt"]["sha256"]
        ):
            raise ArtifactDrift("Generated Task Prompt fingerprint drifted")

    def _load_current_task(
        self,
        run_dir: Path,
        task_id: str,
        *,
        use_persisted_protected_snapshot: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any], Path, Path]:
        _validate_project_relative_path(f"workflow/tasks/{task_id}", prefix="workflow")
        record, run_path, _ = self._run_record(run_dir)
        task_dir = self._safe_run_path(
            run_dir, f"workflow/tasks/{task_id}", prefix="workflow"
        )
        envelope_path = task_dir / "task.json"
        if _is_link_or_reparse(envelope_path) or not envelope_path.is_file():
            raise ArtifactDrift("Task Envelope is absent or linked")
        envelope = read_json(envelope_path)
        self.contracts.validate("subagent-task-envelope", envelope)
        if envelope["task_id"] != task_id:
            raise KernelConflict("Task Envelope identity disagrees with its path")
        expected, prompt = self._rebuild_envelope(run_dir, record, envelope)
        if use_persisted_protected_snapshot:
            # Once a Claim durably binds the Envelope fingerprint, the prepared
            # snapshot is the immutable write-boundary authority. Rebuilding it
            # from the live Run here would turn an undeclared worker write into
            # an apparent Envelope drift before the boundary comparison can
            # identify the actual violation.
            expected["protected_run_snapshot"] = envelope["protected_run_snapshot"]
        self._verify_task_files(run_dir, expected, prompt)
        return envelope, record, task_dir, run_path

    def _create_attempt_record(
        self,
        run_dir: Path,
        envelope: dict[str, Any],
        claim: Any,
        *,
        fault_point: str | None,
        after_write_fault_point: str,
    ) -> TaskClaimResult:
        task_id = envelope["task_id"]
        attempt_id = str(claim["attempt_id"])
        attempt_rel = f"workflow/tasks/{task_id}/attempts/{attempt_id}"
        if claim["attempt_path"] != attempt_rel:
            raise ControlStoreUnavailable("Task Attempt path binding in Control Store is invalid")
        attempt_dir = self._safe_run_path(run_dir, attempt_rel, prefix="workflow")
        expected_attempt_record = {
            "schema_name": "task-attempt",
            "schema_version": "1.0.0",
            "kernel_version": "2.0.0",
            "task_id": task_id,
            "attempt_id": attempt_id,
            "claim_generation": int(claim["claim_generation"]),
            "task_envelope_sha256": str(claim["envelope_sha256"]),
            "attempt_path": attempt_rel,
            "coordinator_session_id": str(claim["coordinator_session_id"]),
            "worker_id": str(claim["worker_id"]),
            "claimed_at": str(claim["updated_at"]),
            "state": "claimed",
        }
        authority = self.kernel._preflight_control_store().task_attempt_authority(
            attempt_id
        )
        if authority is None:
            raise ControlStoreUnavailable(
                "Task Attempt lacks immutable record authority"
            )
        try:
            attempt_record = json.loads(str(authority["attempt_record_json"]))
        except json.JSONDecodeError as exc:
            raise ControlStoreUnavailable(
                "Task Attempt record authority is invalid"
            ) from exc
        canonical = canonical_json_bytes(attempt_record)
        if (
            attempt_record != expected_attempt_record
            or canonical.decode("utf-8") != authority["attempt_record_json"]
            or hashlib.sha256(canonical).hexdigest()
            != authority["attempt_record_sha256"]
        ):
            raise ControlStoreUnavailable(
                "Task Attempt record authority disagrees with its Claim"
            )
        self.contracts.validate("task-attempt", attempt_record)
        if attempt_dir.exists():
            path = attempt_dir / "attempt.json"
            if (
                not path.is_file()
                or _is_link_or_reparse(path)
                or path.read_bytes() != canonical
            ):
                raise ArtifactDrift("Task Attempt record drifted")
        else:
            staging = (
                run_dir / "待删除/task-attempts" / uuid.uuid4().hex
            )
            staging.mkdir(parents=True, exist_ok=False)
            write_json_atomic(staging / "attempt.json", attempt_record)
            attempt_dir.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging, attempt_dir)
        _inject(fault_point, after_write_fault_point)
        resource_admission = None
        if envelope.get("schema_version") in {"2.0.0", "3.0.0"}:
            from .resource_admission import ResourceAdmission

            resource_admission = ResourceAdmission(self.kernel).status(
                task_id, attempt_id
            )
        return TaskClaimResult(
            run_id=envelope["authority_binding"]["run_id"],
            run_dir=run_dir,
            task_id=task_id,
            attempt_id=attempt_id,
            claim_generation=int(claim["claim_generation"]),
            attempt_dir=attempt_dir,
            resource_admission=resource_admission,
        )

    def claim_task(
        self,
        run_dir: Path,
        task_id: str,
        *,
        coordinator_session_id: str,
        worker_id: str,
        fault_point: str | None = None,
    ) -> TaskClaimResult:
        if fault_point is not None and fault_point not in CLAIM_FAULT_POINTS:
            raise ContractError(f"unknown Task Claim fault point: {fault_point}")
        run_dir = run_dir.resolve()
        self.kernel._verify_current_source(run_dir)
        envelope, record, _, _ = self._load_current_task(run_dir, task_id)
        generation = 1
        attempt_id = hashlib.sha256(
            f"task-attempt\0{task_id}\0{generation}".encode("utf-8")
        ).hexdigest()[:24]
        attempt_rel = f"workflow/tasks/{task_id}/attempts/{attempt_id}"
        if envelope.get("schema_version") in {"2.0.0", "3.0.0"}:
            from .resource_admission import ResourceAdmission

            claim = ResourceAdmission(self.kernel).claim_task(
                authority_id=record["run_id"],
                task_id=task_id,
                envelope_sha256=sha256_file(
                    run_dir / envelope["task_root_path"] / "task.json"
                ),
                write_set=tuple(envelope["write_set"]),
                attempt_path=attempt_rel,
                coordinator_session_id=coordinator_session_id,
                worker_id=worker_id,
                claimed_at=_utc_now(),
                required_resources=tuple(envelope["resource_request"]),
                fairness_group_id=str(envelope["fairness_group_id"]),
                batch_id=envelope.get("batch_id"),
                fault_point=(
                    fault_point
                    if fault_point in RESOURCE_CLAIM_FAULT_POINTS
                    else None
                ),
            )
        else:
            claim = self.kernel._preflight_control_store().claim_task(
                authority_id=record["run_id"],
                task_id=task_id,
                envelope_sha256=sha256_file(
                    run_dir / envelope["task_root_path"] / "task.json"
                ),
                write_set=tuple(envelope["write_set"]),
                attempt_path=attempt_rel,
                coordinator_session_id=coordinator_session_id,
                worker_id=worker_id,
                claimed_at=_utc_now(),
            )
        _inject(fault_point, "after_claim_committed")
        return self._create_attempt_record(
            run_dir,
            envelope,
            claim,
            fault_point=fault_point,
            after_write_fault_point="after_attempt_record_written",
        )

    def reclaim_task(
        self,
        run_dir: Path,
        *,
        task_id: str,
        expected_attempt_id: str,
        expected_claim_generation: int,
        coordinator_session_id: str,
        worker_id: str,
        reason: str,
        fault_point: str | None = None,
    ) -> TaskClaimResult:
        if fault_point is not None and fault_point not in RECLAIM_FAULT_POINTS:
            raise ContractError(f"unknown Task reclaim fault point: {fault_point}")
        run_dir = run_dir.resolve()
        self.kernel._verify_current_source(run_dir)
        envelope, record, _, _ = self._load_current_task(run_dir, task_id)
        generation = expected_claim_generation + 1
        attempt_id = hashlib.sha256(
            f"task-attempt\0{task_id}\0{generation}".encode("utf-8")
        ).hexdigest()[:24]
        attempt_rel = f"workflow/tasks/{task_id}/attempts/{attempt_id}"
        if envelope.get("schema_version") in {"2.0.0", "3.0.0"}:
            from .resource_admission import ResourceAdmission

            claim = ResourceAdmission(self.kernel).reclaim_task(
                authority_id=record["run_id"],
                task_id=task_id,
                expected_attempt_id=expected_attempt_id,
                expected_claim_generation=expected_claim_generation,
                attempt_path=attempt_rel,
                coordinator_session_id=coordinator_session_id,
                worker_id=worker_id,
                reason=reason,
                reclaimed_at=_utc_now(),
                required_resources=tuple(envelope["resource_request"]),
                fairness_group_id=str(envelope["fairness_group_id"]),
                batch_id=envelope.get("batch_id"),
                fault_point=(
                    fault_point
                    if fault_point in RESOURCE_RECLAIM_FAULT_POINTS
                    else None
                ),
            )
        else:
            claim = self.kernel._preflight_control_store().reclaim_task(
                authority_id=record["run_id"],
                task_id=task_id,
                expected_attempt_id=expected_attempt_id,
                expected_claim_generation=expected_claim_generation,
                attempt_path=attempt_rel,
                coordinator_session_id=coordinator_session_id,
                worker_id=worker_id,
                reason=reason,
                reclaimed_at=_utc_now(),
            )
        _inject(fault_point, "after_reclaim_committed")
        return self._create_attempt_record(
            run_dir,
            envelope,
            claim,
            fault_point=fault_point,
            after_write_fault_point="after_reclaim_attempt_record_written",
        )

    def _attempt_inventory(
        self,
        attempt_dir: Path,
        *,
        envelope: dict[str, Any],
        completion_allowed: bool,
        promotion_journal_expected: bool = False,
    ) -> None:
        expected_dirs, expected_files = self._declared_attempt_inventory(
            envelope,
            completion_expected=(
                completion_allowed and (attempt_dir / "completion.json").exists()
            ),
            promotion_journal_expected=promotion_journal_expected,
            attempt_dir=attempt_dir,
            provider_inventory_required=True,
        )
        actual_dirs, actual_files = self._attempt_entries(attempt_dir)
        if actual_dirs != expected_dirs or actual_files != expected_files:
            raise ContractError(
                "Task Attempt inventory differs from the exact declared outputs",
                data={
                    "unexpected_directories": sorted(actual_dirs - expected_dirs),
                    "missing_directories": sorted(expected_dirs - actual_dirs),
                    "unexpected_files": sorted(actual_files - expected_files),
                    "missing_files": sorted(expected_files - actual_files),
                },
            )

    def _canonical_task_output_path(self, run_dir: Path, relative: str) -> Path:
        prefix = PurePosixPath(relative).parts[0]
        if prefix not in {"work", "workflow"}:
            raise ContractError(
                "Task canonical output must be owned by work or workflow"
            )
        return self._safe_run_path(run_dir, relative, prefix=prefix)

    def _validate_candidate_staging_inventory(
        self,
        run_dir: Path,
        record: dict[str, Any],
        inventory: dict[str, Any],
        skeleton: dict[str, Any],
        *,
        inventory_sha256: str,
        inventory_generation: int | None = None,
        provider_attempt_dir: Path | None = None,
    ) -> None:
        if (
            inventory["run_id"] != record["run_id"]
            or inventory["source_epoch"] != record["source_epoch"]
            or inventory["mode"] != record["source_acquisition_mode"]
            or inventory["canonical_platform"] != record["canonical_platform"]
            or inventory["canonical_item_id"] != record["canonical_item_id"]
            or inventory["source_identity"] != record["source_identity"]
        ):
            raise ContractError("Candidate Inventory differs from Run authority")
        next_generation = inventory_generation
        if next_generation is None:
            next_generation = 1
            prior = record["artifact_generations"].get(
                "source_candidate_inventory"
            )
            if prior is not None:
                next_generation = int(prior["generation"]) + 1
        if (
            skeleton["run_id"] != record["run_id"]
            or skeleton["source_epoch"] != record["source_epoch"]
            or skeleton["acquisition_id"] != inventory["acquisition_id"]
            or skeleton["source_identity"] != record["source_identity"]
            or skeleton["candidate_inventory"]
            != {
                "path": "work/source-acquisition/candidate-inventory.json",
                "generation": next_generation,
                "sha256": inventory_sha256,
            }
            or skeleton["policy_binding"]["policy_id"]
            != inventory["policy_binding"]["policy_id"]
            or skeleton["policy_binding"]["version"]
            != inventory["policy_binding"]["version"]
            or skeleton["policy_binding"]["sha256"]
            != inventory["policy_binding"]["sha256"]
        ):
            raise ContractError(
                "Decision Skeleton differs from Candidate Inventory authority"
            )
        from .source_acquisition import (
            SubtitleCandidate,
            build_allowed_source_judgment,
        )
        from .source_candidates import SourceCandidatePolicy

        policy = SourceCandidatePolicy(
            content_classification=inventory["policy_binding"][
                "content_classification"
            ],
            subtitle_language_priority=tuple(
                inventory["policy_binding"]["subtitle_language_priority"]
            ),
            whisper_allowed=inventory["policy_binding"]["whisper_allowed"],
            policy_id=inventory["policy_binding"]["policy_id"],
            version=inventory["policy_binding"]["version"],
        )
        if policy.binding() != inventory["policy_binding"]:
            raise ContractError("Candidate Inventory policy fingerprint is invalid")
        video_candidates = [
            candidate
            for candidate in inventory["candidates"]
            if candidate["role"] == "video"
        ]
        cover_candidates = [
            candidate
            for candidate in inventory["candidates"]
            if candidate["role"] == "cover"
        ]
        if len(video_candidates) != 1 or len(cover_candidates) != 1:
            raise ContractError(
                "Candidate Inventory must declare one canonical video and cover"
            )
        video_candidate = video_candidates[0]
        whisper_candidate_id = (
            video_candidate["candidate_id"]
            if policy.whisper_allowed
            and "audio" in video_candidate["technical_probe"]["stream_types"]
            else None
        )
        expected_allowed = build_allowed_source_judgment(
            [
                SubtitleCandidate(
                    candidate_id=candidate["candidate_id"],
                    language=candidate["language"],
                    subtitle_kind=candidate["subtitle_kind"],
                    technically_usable=(
                        candidate["technical_probe"]["status"] == "pass"
                    ),
                )
                for candidate in inventory["candidates"]
                if candidate["role"] == "subtitle"
            ],
            english_primary=(
                policy.content_classification == "language_learning"
            ),
            whisper_allowed=policy.whisper_allowed,
            whisper_audio_candidate_id=whisper_candidate_id,
        )
        if skeleton["allowed_judgment"] != expected_allowed:
            raise ContractError(
                "Decision Skeleton choices differ from Candidate Inventory policy"
            )

        canonical_root = PurePosixPath(
            "work/source-acquisition/candidates"
        ) / f"e{inventory['source_epoch']}"
        candidate_root = (
            self._safe_run_path(
                run_dir, canonical_root.as_posix(), prefix="work"
            )
            if provider_attempt_dir is None
            else self._safe_run_path(
                provider_attempt_dir, "o/candidates"
            )
        )
        require_contained_path(
            candidate_root,
            run_dir if provider_attempt_dir is None else provider_attempt_dir,
            purpose="Candidate staging root",
            error_type=ArtifactDrift,
            leaf_kind="directory",
        )
        expected = {
            candidate["staged_path"]: candidate
            for candidate in inventory["candidates"]
        }
        candidate_locations: dict[str, Path] = {}
        for relative in expected:
            try:
                suffix = PurePosixPath(relative).relative_to(canonical_root)
            except ValueError as exc:
                raise ContractError(
                    "Candidate Inventory contains a path outside its epoch root"
                ) from exc
            if not suffix.parts:
                raise ContractError("Candidate Inventory path lacks a file name")
            candidate_locations[relative] = self._safe_run_path(
                candidate_root, suffix.as_posix()
            )
        observed: set[str] = set()
        observed_directories: set[str] = set()
        pending = [candidate_root]
        while pending:
            directory = pending.pop()
            with os.scandir(directory) as entries:
                for entry in entries:
                    path = Path(entry.path)
                    suffix = path.relative_to(candidate_root).as_posix()
                    relative = (
                        canonical_root / PurePosixPath(suffix)
                    ).as_posix()
                    info = entry.stat(follow_symlinks=False)
                    if entry.is_symlink() or (
                        getattr(info, "st_file_attributes", 0)
                        & stat.FILE_ATTRIBUTE_REPARSE_POINT
                    ):
                        raise ArtifactDrift(
                            "Candidate staging contains a linked entry",
                            data={"drifted_paths": [relative]},
                        )
                    if entry.is_dir(follow_symlinks=False):
                        observed_directories.add(relative)
                        pending.append(path)
                    elif entry.is_file(follow_symlinks=False):
                        observed.add(relative)
                    else:
                        raise ContractError(
                            "Candidate staging contains an unsupported entry"
                        )
        if observed != set(expected):
            raise ArtifactDrift(
                "Candidate Inventory does not authenticate the exact staging set",
                data={
                    "unexpected_paths": sorted(observed - set(expected)),
                    "missing_paths": sorted(set(expected) - observed),
                },
            )
        expected_directories: set[str] = set()
        for relative in expected:
            parent = PurePosixPath(relative).parent
            while parent != canonical_root:
                expected_directories.add(parent.as_posix())
                parent = parent.parent
        if observed_directories != expected_directories:
            raise ArtifactDrift(
                "Candidate Inventory does not authenticate the exact staging directories",
                data={
                    "unexpected_paths": sorted(
                        observed_directories - expected_directories
                    ),
                    "missing_paths": sorted(
                        expected_directories - observed_directories
                    ),
                },
            )
        for relative, candidate in expected.items():
            path = candidate_locations[relative]
            require_contained_path(
                path,
                candidate_root,
                purpose="Candidate staging file",
                error_type=ArtifactDrift,
                leaf_kind="file",
                require_single_link=True,
            )
            if (
                path.stat().st_size != candidate["size_bytes"]
                or sha256_file(path) != candidate["sha256"]
            ):
                raise ArtifactDrift(
                    "Candidate staging fingerprint differs from Inventory",
                    data={"drifted_paths": [relative]},
                )

    def _validate_production_staged_outputs(
        self,
        run_dir: Path,
        record: dict[str, Any],
        envelope: dict[str, Any],
        claim: Any,
        attempt_id: str,
        parsed_outputs: dict[str, Any],
        output_payloads: dict[str, bytes],
        provider_inventory_generation: int | None = None,
        provider_attempt_dir: Path | None = None,
    ) -> None:
        stage = envelope["task_stage"]
        if stage == "provider_acquisition":
            inventory = parsed_outputs["source_candidate_inventory"]
            skeleton = parsed_outputs["source_acquisition_decision_skeleton"]
            canonical_root = self._safe_run_path(
                run_dir,
                (
                    "work/source-acquisition/candidates/"
                    f"e{envelope['source_epoch']}"
                ),
                prefix="work",
            )
            intent = self.kernel._preflight_control_store().task_promotion_for_attempt(
                envelope["task_id"], attempt_id
            )
            if os.path.lexists(canonical_root) and (
                intent is None
                or intent["run_id"] != record["run_id"]
                or int(intent["claim_generation"])
                != int(claim["claim_generation"])
                or intent["state"] == "ABORTED"
            ):
                raise ArtifactDrift(
                    "provider canonical Candidate root exists without matching Promotion authority",
                    data={
                        "drifted_paths": [
                            canonical_root.relative_to(run_dir).as_posix()
                        ]
                    },
                )
            if provider_attempt_dir is None:
                raise ContractError(
                    "provider Completion Gate lacks its Attempt Candidate boundary"
                )
            self._validate_candidate_staging_inventory(
                run_dir,
                record,
                inventory,
                skeleton,
                inventory_sha256=hashlib.sha256(
                    output_payloads["source_candidate_inventory"]
                ).hexdigest(),
                inventory_generation=provider_inventory_generation,
                provider_attempt_dir=provider_attempt_dir,
            )
            return
        inventory_generation = record["artifact_generations"].get(
            "source_candidate_inventory"
        )
        skeleton_generation = record["artifact_generations"].get(
            "source_acquisition_decision_skeleton"
        )
        if inventory_generation is None or skeleton_generation is None:
            raise ArtifactDrift(
                "production Source Task lacks current candidate authority"
            )
        inventory = read_json(run_dir / inventory_generation["path"])
        skeleton = read_json(run_dir / skeleton_generation["path"])
        self.contracts.validate("source-candidate-inventory", inventory)
        self.contracts.validate(
            "source-acquisition-decision-skeleton", skeleton
        )
        self._validate_candidate_staging_inventory(
            run_dir,
            record,
            inventory,
            skeleton,
            inventory_sha256=inventory_generation["sha256"],
            inventory_generation=inventory_generation["generation"],
        )
        if stage == "semantic_judgment":
            from .source_acquisition import validate_source_judgment

            decision = parsed_outputs["source_acquisition_decision"]
            skeleton_binding = next(
                item
                for item in envelope["input_artifacts"]
                if item["logical_id"]
                == "source_acquisition_decision_skeleton"
            )
            if (
                decision["task_id"] != envelope["task_id"]
                or decision["attempt_id"] != attempt_id
                or decision["task_envelope_sha256"] != claim["envelope_sha256"]
                or decision["skeleton_sha256"] != skeleton_binding["sha256"]
            ):
                raise ContractError(
                    "Source Judgment Patch protected bindings are invalid"
                )
            validate_source_judgment(
                skeleton["allowed_judgment"], decision["judgment"]
            )
            return
        audio = envelope["whisper_audio_candidate"]
        audio_path = self._safe_run_path(
            run_dir, audio["staged_path"], prefix="work"
        )
        if (
            _is_link_or_reparse(audio_path)
            or not audio_path.is_file()
            or sha256_file(audio_path) != audio["sha256"]
        ):
            raise ArtifactDrift(
                "Whisper audio candidate drifted before Completion Gate",
                data={"drifted_paths": [audio["staged_path"]]},
            )
        self._validate_srt_bytes(output_payloads["source_transcription"])

    def _completion_gate(
        self,
        run_dir: Path,
        *,
        task_id: str,
        attempt_id: str,
        claim_generation: int,
        validation_time: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], Path, dict[str, Any]]:
        self.kernel._verify_current_source(run_dir)
        envelope, record, task_dir, run_path = self._load_current_task(
            run_dir,
            task_id,
            use_persisted_protected_snapshot=True,
        )
        store = self.kernel._preflight_control_store()
        claim = store.task_claim_for_attempt(task_id, attempt_id)
        if (
            claim is None
            or claim["state"] != "ACTIVE"
            or claim["attempt_id"] != attempt_id
            or int(claim["claim_generation"]) != claim_generation
            or claim["authority_id"] != record["run_id"]
            or claim["envelope_sha256"] != sha256_file(task_dir / "task.json")
        ):
            raise KernelConflict("Task Completion Gate fencing token is stale")
        if envelope.get("schema_version") in {"2.0.0", "3.0.0"}:
            resource = store.resource_status(task_id, attempt_id)
            if (
                resource is None
                or int(resource["claim_generation"]) != claim_generation
                or resource["state"] != "ADMITTED"
                or resource["lease_state"] not in {"released", "resolved"}
                or resource["launch_authorization_state"] != "COMPLETED"
            ):
                raise ResourceAdmissionBlocked(
                    "Task Completion requires a confirmed Resource launch authority",
                    data={
                        "task_id": task_id,
                        "attempt_id": attempt_id,
                        "claim_generation": claim_generation,
                    },
                )
        if claim["attempt_state"] not in {
            "CLAIMED",
            "VALIDATED_WAITING_FOR_PROMOTION",
        }:
            raise KernelConflict("Task Attempt is not eligible for completion")
        if record["coordination_revision"] != envelope["authority_binding"]["expected_coordination_revision"]:
            raise KernelConflict("Task authority revision is stale")
        for declared_input in envelope["input_artifacts"]:
            generation = record["artifact_generations"].get(
                declared_input["logical_id"]
            )
            path = self._safe_run_path(run_dir, declared_input["path"])
            if (
                generation is None
                or generation["path"] != declared_input["path"]
                or generation["generation"] != declared_input["generation"]
                or generation["sha256"] != declared_input["sha256"]
                or _is_link_or_reparse(path)
                or not path.is_file()
                or sha256_file(path) != generation["sha256"]
            ):
                raise ArtifactDrift(
                    "Task input Artifact Generation is stale",
                    data={"drifted_paths": [declared_input["path"]]},
                )
        attempt_dir = self._safe_run_path(
            run_dir,
            f"workflow/tasks/{task_id}/attempts/{attempt_id}",
            prefix="workflow",
        )
        completion_path = attempt_dir / "completion.json"
        prepared_completion_json = claim["completion_record_json"]
        if (
            claim["attempt_state"] == "CLAIMED"
            and completion_path.exists()
            and prepared_completion_json is None
        ):
            raise ArtifactDrift("Worker prewrote Kernel-owned Completion evidence")
        if (
            claim["attempt_state"] == "VALIDATED_WAITING_FOR_PROMOTION"
            and prepared_completion_json is None
        ):
            raise ControlStoreUnavailable(
                "validated Task Attempt lacks prepared Completion authority"
            )
        self._attempt_inventory(
            attempt_dir,
            envelope=envelope,
            completion_allowed=completion_path.exists(),
        )
        self._verify_task_root_inventory(
            run_dir,
            run_id=record["run_id"],
            skip_task_id=task_id,
            skip_attempt_id=attempt_id,
        )
        self._verify_attempt_record(
            attempt_dir,
            envelope=envelope,
            attempt=claim,
            current_claim=claim,
        )
        self._verify_protected_run_snapshot(run_dir, envelope)
        output_payloads: dict[str, bytes] = {}
        parsed_outputs: dict[str, Any] = {}
        for output_spec in envelope["required_outputs"]:
            staged_path = self._safe_run_path(
                attempt_dir, output_spec["attempt_relative_path"]
            )
            require_contained_path(
                staged_path,
                attempt_dir,
                purpose=f"required Task output {output_spec['logical_id']}",
                error_type=ContractError,
                leaf_kind="file",
                require_single_link=True,
            )
            payload = staged_path.read_bytes()
            output_payloads[output_spec["logical_id"]] = payload
            if output_spec["schema_name"] == "source-transcription-srt":
                self._validate_srt_bytes(payload)
            else:
                try:
                    value = read_json(staged_path)
                    self.contracts.validate(output_spec["schema_name"], value)
                except (OSError, UnicodeError, ValueError, ContractError) as exc:
                    raise ContractError(
                        f"required Task output is invalid: {output_spec['logical_id']}"
                    ) from exc
                parsed_outputs[output_spec["logical_id"]] = value
            canonical = self._canonical_task_output_path(
                run_dir, output_spec["canonical_path"]
            )
            prior_sha = output_spec["expected_prior_sha256"]
            if prior_sha is None:
                if canonical.exists():
                    raise ArtifactDrift(
                        "Worker wrote the canonical output before promotion",
                        data={"drifted_paths": [output_spec["canonical_path"]]},
                    )
            elif (
                _is_link_or_reparse(canonical)
                or not canonical.is_file()
                or sha256_file(canonical) != prior_sha
            ):
                raise ArtifactDrift(
                    "canonical prior Artifact Generation drifted",
                    data={"drifted_paths": [output_spec["canonical_path"]]},
                )

        if envelope.get("schema_version") == "3.0.0":
            self._validate_production_staged_outputs(
                run_dir,
                record,
                envelope,
                claim,
                attempt_id,
                parsed_outputs,
                output_payloads,
                provider_attempt_dir=attempt_dir,
            )
        else:
            source = record["artifact_generations"]["source_manifest"]
            patch = parsed_outputs["source_acquisition_decision"]
            if (
                patch["task_id"] != task_id
                or patch["attempt_id"] != attempt_id
                or patch["task_envelope_sha256"] != claim["envelope_sha256"]
                or patch["source_manifest_sha256"] != source["sha256"]
            ):
                raise ContractError("Judgment Patch protected bindings are invalid")
            source_manifest = read_json(run_dir / source["path"])
            subtitle_ids = {
                item["logical_id"]
                for item in source_manifest["artifacts"]
                if item["path"].startswith("source/subtitles/")
            }
            if patch["judgment"]["selected_subtitle_track"] not in subtitle_ids:
                raise ContractError(
                    "Judgment Patch selected an undeclared subtitle track"
                )
        if prepared_completion_json is None:
            if validation_time is None:
                raise ControlStoreUnavailable(
                    "new Task Completion requires a trusted validation event time"
                )
            validated_at = validation_time
        else:
            try:
                prepared_completion = json.loads(str(prepared_completion_json))
            except json.JSONDecodeError as exc:
                raise ControlStoreUnavailable(
                    "prepared Task Completion JSON is invalid"
                ) from exc
            self.contracts.validate("task-completion-record", prepared_completion)
            if (
                canonical_json_bytes(prepared_completion).decode("utf-8")
                != prepared_completion_json
                or hashlib.sha256(
                    str(prepared_completion_json).encode("utf-8")
                ).hexdigest()
                != claim["completion_sha256"]
            ):
                raise ControlStoreUnavailable(
                    "prepared Task Completion authority is not canonical"
                )
            validated_at = prepared_completion["validated_at"]
        provider_candidate_outputs = self._provider_candidate_specs(
            envelope,
            attempt_dir,
            require_inventory=True,
        )
        completion_outputs = [
            {
                "logical_id": item["logical_id"],
                "attempt_path": item["attempt_relative_path"],
                "canonical_path": item["canonical_path"],
                "sha256": hashlib.sha256(
                    output_payloads[item["logical_id"]]
                ).hexdigest(),
            }
            for item in envelope["required_outputs"]
        ]
        completion_outputs.extend(
            {
                "logical_id": item["logical_id"],
                "attempt_path": item["attempt_path"],
                "canonical_path": item["canonical_path"],
                "sha256": item["sha256"],
            }
            for item in provider_candidate_outputs
        )
        completion = {
            "schema_name": "task-completion-record",
            "schema_version": "1.0.0",
            "kernel_version": "2.0.0",
            "task_id": task_id,
            "attempt_id": attempt_id,
            "claim_generation": claim_generation,
            "task_envelope_sha256": claim["envelope_sha256"],
            "validated_authority_revision": record["coordination_revision"],
            "validated_run_record_sha256": sha256_file(run_path),
            "validated_inputs": [
                {
                    "logical_id": item["logical_id"],
                    "generation": item["generation"],
                    "sha256": item["sha256"],
                }
                for item in envelope["input_artifacts"]
            ],
            "outputs": completion_outputs,
            "gate_status": "pass",
            "validated_at": validated_at,
        }
        self.contracts.validate("task-completion-record", completion)
        if prepared_completion_json is not None and prepared_completion != completion:
            raise ArtifactDrift("prepared Task Completion bindings drifted")
        if completion_path.exists():
            if (
                _is_link_or_reparse(completion_path)
                or not completion_path.is_file()
                or completion_path.read_bytes() != canonical_json_bytes(completion)
                or prepared_completion_json is None
                or claim["completion_sha256"] is None
                or sha256_file(completion_path) != claim["completion_sha256"]
            ):
                raise ArtifactDrift("Task Completion evidence drifted")
        return envelope, record, completion, attempt_dir, parsed_outputs

    def complete_task(
        self,
        run_dir: Path,
        *,
        task_id: str,
        attempt_id: str,
        claim_generation: int,
        fault_point: str | None = None,
    ) -> TaskCompletionResult:
        if fault_point is not None and fault_point not in COMPLETION_FAULT_POINTS:
            raise ContractError(f"unknown Task Completion fault point: {fault_point}")
        run_dir = run_dir.resolve()
        envelope, _, completion, attempt_dir, _ = self._completion_gate(
            run_dir,
            task_id=task_id,
            attempt_id=attempt_id,
            claim_generation=claim_generation,
            validation_time=_utc_now(),
        )
        completion_path = attempt_dir / "completion.json"
        store = self.kernel._preflight_control_store()
        prepared = store.prepare_task_completion(
            task_id=task_id,
            attempt_id=attempt_id,
            claim_generation=claim_generation,
            completion_record=completion,
        )
        durable_completion_sha = str(prepared["completion_sha256"])
        _inject(fault_point, "after_completion_prepared")
        if completion_path.exists():
            if (
                _is_link_or_reparse(completion_path)
                or not completion_path.is_file()
                or completion_path.read_bytes() != canonical_json_bytes(completion)
                or sha256_file(completion_path) != durable_completion_sha
            ):
                raise ArtifactDrift("Task Completion evidence drifted")
            completion_sha = sha256_file(completion_path)
        else:
            completion_sha = write_json_atomic(completion_path, completion)
            if completion_sha != durable_completion_sha:
                raise KernelConflict(
                    "written Task Completion evidence differs from durable authority"
                )
        _inject(fault_point, "after_completion_record_written")
        store.mark_task_validated(
            task_id=task_id,
            attempt_id=attempt_id,
            claim_generation=claim_generation,
            completion_sha256=completion_sha,
        )
        _inject(fault_point, "after_completion_state_commit")
        return TaskCompletionResult(
            run_id=envelope["authority_binding"]["run_id"],
            run_dir=run_dir,
            task_id=task_id,
            attempt_id=attempt_id,
            claim_generation=claim_generation,
            completion_path=completion_path,
        )

    def _replacement_run_record(
        self,
        record: dict[str, Any],
        envelope: dict[str, Any],
        *,
        attempt_id: str,
        intent_id: str,
        output_sha256: dict[str, str],
        committed_at: str,
    ) -> dict[str, Any]:
        if envelope.get("schema_version") == "3.0.0":
            return self._replacement_production_run_record(
                record,
                envelope,
                attempt_id=attempt_id,
                intent_id=intent_id,
                output_sha256=output_sha256,
                committed_at=committed_at,
            )
        replacement = copy.deepcopy(record)
        previous = replacement["artifact_generations"].get(
            "source_acquisition_decision"
        )
        generation = 1 if previous is None else int(previous["generation"]) + 1
        source_generation = replacement["artifact_generations"]["source_manifest"][
            "generation"
        ]
        replacement["schema_version"] = "2.0.0"
        replacement["coordination_revision"] = record["coordination_revision"] + 1
        replacement["last_mutation_intent_id"] = intent_id
        replacement["artifact_generations"]["source_acquisition_decision"] = {
            "path": "workflow/source-acquisition-judgment-patch.json",
            "generation": generation,
            "sha256": output_sha256["source_acquisition_decision"],
            "producer": f"task:{envelope['task_id']}/{attempt_id}",
            "committed_at": committed_at,
        }
        replacement["checkpoint_dependencies"] = {
            "source_acquisition_decision_ready": ["source_ready"]
        }
        replacement["checkpoints"]["source_acquisition_decision_ready"] = {
            "status": "current",
            "artifact_generations": {
                "source_manifest": source_generation,
                "source_acquisition_decision": generation,
            },
            "evidence_sha256": output_sha256["source_acquisition_decision"],
        }
        self.contracts.validate_run_record(replacement)
        return replacement

    def _replacement_production_run_record(
        self,
        record: dict[str, Any],
        envelope: dict[str, Any],
        *,
        attempt_id: str,
        intent_id: str,
        output_sha256: dict[str, str],
        committed_at: str,
    ) -> dict[str, Any]:
        replacement = copy.deepcopy(record)
        replacement["coordination_revision"] = record["coordination_revision"] + 1
        replacement["last_mutation_intent_id"] = intent_id
        produced_bindings: list[dict[str, Any]] = []
        for output in envelope["required_outputs"]:
            logical_id = output["logical_id"]
            previous = replacement["artifact_generations"].get(logical_id)
            generation = 1 if previous is None else int(previous["generation"]) + 1
            digest = output_sha256[logical_id]
            replacement["artifact_generations"][logical_id] = {
                "path": output["canonical_path"],
                "generation": generation,
                "sha256": digest,
                "producer": f"task:{envelope['task_id']}/{attempt_id}",
                "committed_at": committed_at,
                "source_epoch": record["source_epoch"],
            }
            produced_bindings.append(
                {
                    "logical_id": logical_id,
                    "generation": generation,
                    "sha256": digest,
                }
            )

        stage = envelope["task_stage"]
        if stage == "provider_acquisition":
            checkpoint_name = "source_candidates_ready"
            replacement["source_state"] = "candidates_ready"
            prerequisite_name = "run_initialized"
            bindings = produced_bindings
        else:
            checkpoint_name = "source_acquisition_decision_ready"
            replacement["source_state"] = "decision_ready"
            prerequisite_name = "source_candidates_ready"
            binding_ids = [
                "source_candidate_inventory",
                "source_acquisition_decision_skeleton",
                "source_acquisition_decision",
            ]
            if stage == "whisper_transcription":
                binding_ids.append("source_transcription")
            bindings = []
            for logical_id in binding_ids:
                generation = replacement["artifact_generations"][logical_id]
                bindings.append(
                    {
                        "logical_id": logical_id,
                        "generation": generation["generation"],
                        "sha256": generation["sha256"],
                    }
                )
        prerequisite = replacement["checkpoints"].get(prerequisite_name)
        if prerequisite is None or prerequisite["status"] != "current":
            raise ArtifactDrift(
                f"production Task prerequisite is stale: {prerequisite_name}"
            )
        checkpoint_evidence = hashlib.sha256(
            canonical_json_bytes(
                [
                    {
                        "logical_id": binding["logical_id"],
                        "sha256": binding["sha256"],
                    }
                    for binding in bindings
                ]
            )
        ).hexdigest()
        replacement["checkpoints"][checkpoint_name] = {
            "status": "current",
            "artifact_bindings": bindings,
            "prerequisite_bindings": [
                {
                    "checkpoint": prerequisite_name,
                    "evidence_sha256": prerequisite["evidence_sha256"],
                }
            ],
            "evidence_sha256": checkpoint_evidence,
            "completed_at": committed_at,
        }
        replacement["source_blocker"] = None
        replacement["source_version"] = None
        replacement["phase"] = "source_acquisition"
        self.contracts.validate_run_record(replacement)
        return replacement

    def _promotion_outputs(
        self,
        envelope: dict[str, Any],
        completion: dict[str, Any],
        *,
        claim_generation: int,
        attempt_dir: Path,
    ) -> list[dict[str, Any]]:
        expected_outputs: list[dict[str, Any]] = [
            {
                "logical_id": output["logical_id"],
                "attempt_path": output["attempt_relative_path"],
                "canonical_path": output["canonical_path"],
                "prior_sha256": output["expected_prior_sha256"],
                "preservation_name": (
                    f"{output['logical_id']}"
                    f"{PurePosixPath(output['canonical_path']).suffix}"
                    if envelope.get("schema_version") == "3.0.0"
                    else "decision.json"
                ),
            }
            for output in envelope["required_outputs"]
        ]
        for candidate in self._provider_candidate_specs(
            envelope,
            attempt_dir,
            require_inventory=True,
        ):
            candidate_suffix = PurePosixPath(candidate["attempt_path"]).relative_to(
                "o/candidates"
            )
            expected_outputs.append(
                {
                    "logical_id": candidate["logical_id"],
                    "attempt_path": candidate["attempt_path"],
                    "canonical_path": candidate["canonical_path"],
                    "prior_sha256": None,
                    "preservation_name": (
                        PurePosixPath("candidates") / candidate_suffix
                    ).as_posix(),
                }
            )
        if len(completion["outputs"]) != len(expected_outputs):
            raise ContractError("Task Completion output count drifted")
        promoted: list[dict[str, Any]] = []
        for output, expected in zip(
            completion["outputs"], expected_outputs, strict=True
        ):
            if (
                output["logical_id"] != expected["logical_id"]
                or output["canonical_path"] != expected["canonical_path"]
                or output["attempt_path"] != expected["attempt_path"]
            ):
                raise ContractError("Task Completion output binding drifted")
            promoted.append(
                {
                    **output,
                    "prior_sha256": expected["prior_sha256"],
                    "preservation_path": (
                        f"待删除/task-promotions/{envelope['task_id']}/"
                        f"g{claim_generation:08d}/previous/"
                        f"{expected['preservation_name']}"
                    ),
                }
            )
        return promoted

    def _journal(
        self, intent: Any, outputs: list[dict[str, Any]]
    ) -> dict[str, Any]:
        journal = {
            "schema_name": "task-promotion-journal",
            "schema_version": "1.0.0",
            "kernel_version": "2.0.0",
            "intent_id": str(intent["intent_id"]),
            "run_id": str(intent["run_id"]),
            "task_id": str(intent["task_id"]),
            "attempt_id": str(intent["attempt_id"]),
            "claim_generation": int(intent["claim_generation"]),
            "expected_run_revision": int(intent["expected_run_revision"]),
            "prior_run_record_sha256": str(intent["old_run_record_sha256"]),
            "replacement_run_record_sha256": str(intent["replacement_run_record_sha256"]),
            "coordination_record_path": "workflow/run.json",
            "outputs": outputs,
            "state": "PREPARED",
        }
        self.contracts.validate("task-promotion-journal", journal)
        return journal

    def _authenticate_promotion_evidence(
        self,
        run_dir: Path,
        intent: Any,
        outputs: list[dict[str, Any]],
        *,
        verify_run_boundary: bool,
        allow_published_outputs: bool = False,
        allow_replacement_run_record: bool = False,
        allow_unbound_journal: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any], Path, dict[str, bytes]]:
        store = self.kernel._preflight_control_store()
        claim = store.task_claim_for_attempt(
            str(intent["task_id"]), str(intent["attempt_id"])
        )
        if claim is None or claim["authority_id"] != intent["run_id"]:
            raise ControlStoreUnavailable(
                "Task promotion evidence lacks its Claim authority"
            )
        expected_claim_state = "TERMINAL" if intent["state"] == "COMMITTED" else "ACTIVE"
        expected_attempt_state = (
            "COMMITTED_COMPLETE"
            if intent["state"] == "COMMITTED"
            else "VALIDATED_WAITING_FOR_PROMOTION"
        )
        if (
            claim["state"] != expected_claim_state
            or claim["attempt_state"] != expected_attempt_state
            or int(claim["claim_generation"]) != int(intent["claim_generation"])
        ):
            raise KernelConflict("Task promotion Claim/Attempt lifecycle binding drifted")
        self._verify_task_root_inventory(
            run_dir,
            run_id=str(intent["run_id"]),
            skip_task_id=str(intent["task_id"]),
            skip_attempt_id=str(intent["attempt_id"]),
        )
        task_root = f"workflow/tasks/{intent['task_id']}"
        task_dir = self._safe_run_path(run_dir, task_root, prefix="workflow")
        envelope_path = task_dir / "task.json"
        if (
            _is_link_or_reparse(envelope_path)
            or not envelope_path.is_file()
        ):
            raise ArtifactDrift("Task promotion Envelope or Prompt is absent or linked")
        envelope = read_json(envelope_path)
        self.contracts.validate("subagent-task-envelope", envelope)
        identity_version = store.task_promotion_identity_version(
            str(intent["intent_id"])
        )
        if (
            envelope["task_id"] != intent["task_id"]
            or envelope["authority_binding"]["run_id"] != intent["run_id"]
            or sha256_file(envelope_path) != claim["envelope_sha256"]
        ):
            raise ArtifactDrift(
                "Task promotion immutable Envelope or Generated Prompt drifted"
            )
        if identity_version not in {"evidence-v2", "legacy-v1"}:
            raise ControlStoreUnavailable(
                "Task promotion identity version is absent or unsupported"
            )
        self._verify_envelope_recorded_prompt(
            task_dir,
            envelope,
            task_id=str(intent["task_id"]),
        )
        attempt_dir = self._safe_run_path(
            run_dir,
            f"{task_root}/attempts/{intent['attempt_id']}",
            prefix="workflow",
        )
        self._attempt_inventory(
            attempt_dir,
            envelope=envelope,
            completion_allowed=True,
            promotion_journal_expected=(
                intent["journal_sha256"] is not None
                or (
                    allow_unbound_journal
                    and intent["state"] == "PREPARED"
                    and (attempt_dir / "p.json").exists()
                )
            ),
        )
        self._verify_attempt_record(
            attempt_dir,
            envelope=envelope,
            attempt=claim,
            current_claim=claim,
        )
        completion_path = attempt_dir / "completion.json"
        require_contained_path(
            completion_path,
            attempt_dir,
            purpose="Task promotion Completion evidence",
            error_type=ArtifactDrift,
            leaf_kind="file",
            require_single_link=True,
        )
        completion = read_json(completion_path)
        self.contracts.validate("task-completion-record", completion)
        if (
            canonical_json_bytes(completion).decode("utf-8")
            != claim["completion_record_json"]
            or sha256_file(completion_path) != claim["completion_sha256"]
            or completion["task_envelope_sha256"] != claim["envelope_sha256"]
            or completion["task_id"] != intent["task_id"]
            or completion["attempt_id"] != intent["attempt_id"]
            or int(completion["claim_generation"]) != int(intent["claim_generation"])
        ):
            raise ArtifactDrift("Task promotion Completion evidence drifted")
        expected_outputs = self._promotion_outputs(
            envelope,
            completion,
            claim_generation=int(intent["claim_generation"]),
            attempt_dir=attempt_dir,
        )
        expected_write_set = {
            output["canonical_path"]
            for output in envelope["required_outputs"]
        }
        candidate_root = self._provider_candidate_canonical_root(envelope)
        if candidate_root is not None:
            expected_write_set.add(candidate_root.as_posix())
        if outputs != expected_outputs or set(envelope["write_set"]) != expected_write_set:
            raise ControlStoreUnavailable(
                "Task promotion outputs are outside immutable Completion/write-set authority"
            )
        if verify_run_boundary:
            allowed = None
            if allow_published_outputs:
                allowed = {
                    output["canonical_path"]: (
                        output["prior_sha256"],
                        output["sha256"],
                    )
                    for output in outputs
                }
                protected = {
                    item["path"]: item["sha256"]
                    for item in envelope["protected_run_snapshot"]
                }
                for output in outputs:
                    preservation = PurePosixPath(output["preservation_path"])
                    allowed[output["preservation_path"]] = (
                        protected.get(output["preservation_path"]),
                        output["prior_sha256"],
                    )
                    parent = preservation.parent
                    while str(parent) not in {".", "待删除"}:
                        relative = parent.as_posix()
                        allowed[relative] = (
                            protected.get(relative),
                            self._snapshot_directory_sha(relative),
                        )
                        parent = parent.parent
            if allow_replacement_run_record:
                allowed = dict(allowed or {})
                allowed["workflow/run.json"] = (
                    str(intent["old_run_record_sha256"]),
                    str(intent["replacement_run_record_sha256"]),
                )
            self._verify_protected_run_snapshot(
                run_dir, envelope, allowed_replacements=allowed
            )
        candidates: dict[str, bytes] = {}
        parsed_outputs: dict[str, Any] = {}
        required_output_specs = {
            item["logical_id"]: item
            for item in envelope["required_outputs"]
        }
        provider_candidate_specs = {
            item["logical_id"]: item
            for item in self._provider_candidate_specs(
                envelope,
                attempt_dir,
                require_inventory=True,
            )
        }
        for output in outputs:
            source = self._safe_run_path(attempt_dir, output["attempt_path"])
            require_contained_path(
                source,
                attempt_dir,
                purpose="validated Task Attempt output",
                error_type=ArtifactDrift,
                leaf_kind="file",
                require_single_link=True,
            )
            candidate = source.read_bytes()
            if hashlib.sha256(candidate).hexdigest() != output["sha256"]:
                raise ArtifactDrift(
                    "validated Task Attempt output fingerprint drifted"
                )
            candidates[output["logical_id"]] = candidate
            output_spec = required_output_specs.get(output["logical_id"])
            if output_spec is None:
                provider_spec = provider_candidate_specs.get(
                    output["logical_id"]
                )
                if (
                    provider_spec is None
                    or len(candidate) != provider_spec["size_bytes"]
                    or output["canonical_path"]
                    != provider_spec["canonical_path"]
                ):
                    raise ArtifactDrift(
                        "validated provider Candidate output binding drifted"
                    )
                continue
            if output_spec["schema_name"] == "source-transcription-srt":
                self._validate_srt_bytes(candidate)
            else:
                try:
                    value = json.loads(candidate.decode("utf-8"))
                    self.contracts.validate(output_spec["schema_name"], value)
                except (UnicodeDecodeError, json.JSONDecodeError, ContractError) as exc:
                    raise ArtifactDrift(
                        "validated Task Attempt output content drifted"
                    ) from exc
                parsed_outputs[output["logical_id"]] = value
        if envelope.get("schema_version") == "3.0.0":
            record, _, _ = self._run_record(run_dir)
            provider_generation = None
            if envelope["task_stage"] == "provider_acquisition":
                current = record["artifact_generations"].get(
                    "source_candidate_inventory"
                )
                candidate_sha = hashlib.sha256(
                    candidates["source_candidate_inventory"]
                ).hexdigest()
                if current is not None and current["sha256"] == candidate_sha:
                    provider_generation = current["generation"]
            self._validate_production_staged_outputs(
                run_dir,
                record,
                envelope,
                claim,
                str(intent["attempt_id"]),
                parsed_outputs,
                candidates,
                provider_inventory_generation=provider_generation,
                provider_attempt_dir=attempt_dir,
            )
        return envelope, completion, attempt_dir, candidates

    def _preserve_and_publish_output(
        self,
        run_dir: Path,
        attempt_dir: Path,
        output: dict[str, Any],
        *,
        publish: bool,
        candidate_bytes: bytes,
    ) -> None:
        source = self._safe_run_path(attempt_dir, output["attempt_path"])
        require_contained_path(
            source,
            attempt_dir,
            purpose="validated Task Attempt output",
            error_type=ArtifactDrift,
            leaf_kind="file",
            require_single_link=True,
        )
        if (
            hashlib.sha256(candidate_bytes).hexdigest() != output["sha256"]
            or source.read_bytes() != candidate_bytes
        ):
            raise ArtifactDrift("validated Task Attempt output fingerprint drifted")
        canonical = self._canonical_task_output_path(
            run_dir, output["canonical_path"]
        )
        prior_sha = output["prior_sha256"]
        if canonical.exists():
            require_contained_path(
                canonical,
                run_dir,
                purpose="canonical promotion target",
                error_type=ArtifactDrift,
                leaf_kind="file",
                require_single_link=True,
            )
            actual = sha256_file(canonical)
            if actual == output["sha256"]:
                self._verify_prior_generation_preservation(
                    run_dir,
                    output,
                    recover_from_canonical=False,
                )
                return
            if prior_sha is None or actual != prior_sha:
                raise ArtifactDrift("canonical promotion target differs from old and new generations")
            self._verify_prior_generation_preservation(
                run_dir,
                output,
                recover_from_canonical=True,
            )
        elif prior_sha is not None:
            raise ArtifactDrift("canonical prior Artifact Generation disappeared")
        if publish:
            canonical.parent.mkdir(parents=True, exist_ok=True)
            canonical = self._canonical_task_output_path(
                run_dir, output["canonical_path"]
            )
            _write_bytes_atomic(canonical, candidate_bytes)
            require_contained_path(
                canonical,
                run_dir,
                purpose="promoted canonical output",
                error_type=ArtifactDrift,
                leaf_kind="file",
                require_single_link=True,
            )
            if sha256_file(canonical) != output["sha256"]:
                raise ArtifactDrift("promoted canonical output failed fingerprint verification")

    def _verify_prior_generation_preservation(
        self,
        run_dir: Path,
        output: dict[str, Any],
        *,
        recover_from_canonical: bool,
    ) -> None:
        """Verify one registered prior generation, with bounded PREPARED recovery."""
        prior_sha = output["prior_sha256"]
        if prior_sha is None:
            return
        preservation_relative = str(output["preservation_path"])
        preservation = self._safe_run_path(
            run_dir,
            preservation_relative,
            prefix="待删除",
        )
        drift = {
            "drifted_paths": [preservation_relative],
            "expected_sha256": str(prior_sha),
        }
        if _is_link_or_reparse(preservation):
            raise ArtifactDrift(
                "preserved prior Artifact Generation is linked or reparse-backed",
                data=drift,
            )
        if preservation.is_file():
            if sha256_file(preservation) != prior_sha:
                raise ArtifactDrift(
                    "preserved prior Artifact Generation fingerprint drifted",
                    data=drift,
                )
            return
        if preservation.exists():
            raise ArtifactDrift(
                "preserved prior Artifact Generation is not an ordinary file",
                data=drift,
            )
        if not recover_from_canonical:
            raise ArtifactDrift(
                "preserved prior Artifact Generation is missing",
                data=drift,
            )

        canonical_relative = str(output["canonical_path"])
        canonical = self._canonical_task_output_path(run_dir, canonical_relative)
        if _is_link_or_reparse(canonical) or not canonical.is_file():
            raise ArtifactDrift(
                "preserved prior Artifact Generation cannot be rebuilt from canonical authority",
                data={**drift, "drifted_paths": [canonical_relative, preservation_relative]},
            )
        prior_bytes = canonical.read_bytes()
        if hashlib.sha256(prior_bytes).hexdigest() != prior_sha:
            raise ArtifactDrift(
                "preserved prior Artifact Generation cannot be rebuilt after publication",
                data={**drift, "drifted_paths": [canonical_relative, preservation_relative]},
            )
        try:
            preservation.parent.mkdir(parents=True, exist_ok=True)
            preservation = self._safe_run_path(
                run_dir,
                preservation_relative,
                prefix="待删除",
            )
            _write_bytes_atomic(preservation, prior_bytes)
        except OSError as exc:
            raise ArtifactDrift(
                "preserved prior Artifact Generation recovery failed",
                data=drift,
            ) from exc
        self._verify_prior_generation_preservation(
            run_dir,
            output,
            recover_from_canonical=False,
        )

    def _verify_committed_prior_generations(self, run_dir: Path, run_id: str) -> None:
        """Verify history without replaying obsolete protected Run snapshots."""
        store = self.kernel._preflight_control_store()
        verified_intents: set[str] = set()
        for task_id in sorted(store.task_ids_for_authority(run_id)):
            for attempt in store.task_attempts_for_task(task_id):
                if attempt["promotion_state"] != "COMMITTED":
                    continue
                intent = store.task_promotion_for_attempt(
                    task_id,
                    str(attempt["attempt_id"]),
                )
                if (
                    intent is None
                    or intent["state"] != "COMMITTED"
                    or intent["run_id"] != run_id
                ):
                    raise ControlStoreUnavailable(
                        "committed Task promotion history is incomplete"
                    )
                intent_id = str(intent["intent_id"])
                if intent_id in verified_intents:
                    continue
                verified_intents.add(intent_id)
                try:
                    outputs = json.loads(str(intent["outputs_json"]))
                except json.JSONDecodeError as exc:
                    raise ControlStoreUnavailable(
                        "committed Task promotion output authority is invalid"
                    ) from exc
                for output in outputs:
                    self._verify_prior_generation_preservation(
                        run_dir,
                        output,
                        recover_from_canonical=False,
                    )

    def promote_task(
        self,
        run_dir: Path,
        *,
        task_id: str,
        attempt_id: str,
        claim_generation: int,
        fault_point: str | None = None,
    ) -> TaskPromotionResult:
        if fault_point is not None and fault_point not in PROMOTION_FAULT_POINTS:
            raise ContractError(f"unknown Task promotion fault point: {fault_point}")
        run_dir = run_dir.resolve()
        store = self.kernel._preflight_control_store()
        caller_record, run_path, caller_run_sha = self._run_record(run_dir)
        binding = store.binding_for_run(caller_record["run_id"])
        if (
            binding is None
            or Path(binding["output_path"]).resolve() != run_dir
            or store.current_run_record_sha(caller_record["run_id"]) != caller_run_sha
        ):
            raise KernelConflict("Task promotion caller Run authority is invalid")
        existing = store.task_promotion_for_attempt(task_id, attempt_id)
        if existing is not None:
            if existing["run_id"] != caller_record["run_id"]:
                raise KernelConflict(
                    "Task promotion replay authority differs from caller Run"
                )
            if int(existing["claim_generation"]) != claim_generation:
                raise KernelConflict("Task promotion fencing token is stale")
            if existing["state"] != "COMMITTED":
                self.reconcile_promotion(run_dir, existing)
                existing = store.task_promotion_by_id(existing["intent_id"])
            if existing is not None and existing["state"] == "COMMITTED":
                # A committed replay is a Run resume boundary.  Reconciliation
                # authenticates the current Source and transactionally stales
                # every dependent checkpoint before any idempotent success can
                # be returned for drifted input.
                self.kernel.reconcile_run(run_dir)
                outputs = json.loads(str(existing["outputs_json"]))
                self._authenticate_promotion_evidence(
                    run_dir,
                    existing,
                    outputs,
                    verify_run_boundary=False,
                )
                for output in outputs:
                    self._verify_prior_generation_preservation(
                        run_dir,
                        output,
                        recover_from_canonical=False,
                    )
                self.verify_committed_task_state(run_dir)
                return TaskPromotionResult(
                    run_id=str(existing["run_id"]), run_dir=run_dir,
                    task_id=task_id, attempt_id=attempt_id,
                    claim_generation=claim_generation, intent_id=str(existing["intent_id"]),
                )
        envelope, record, completion, attempt_dir, _ = self._completion_gate(
            run_dir,
            task_id=task_id,
            attempt_id=attempt_id,
            claim_generation=claim_generation,
        )
        completion_path = attempt_dir / "completion.json"
        if not completion_path.is_file() or read_json(completion_path) != completion:
            raise KernelConflict("Task must pass Completion Gate before promotion")
        claim = store.task_claim_for_attempt(task_id, attempt_id)
        if claim is None or claim["attempt_state"] != "VALIDATED_WAITING_FOR_PROMOTION":
            raise KernelConflict("Task Attempt is not waiting for promotion")
        old_run_sha = sha256_file(run_path)
        output_sha256 = {
            output["logical_id"]: output["sha256"]
            for output in completion["outputs"]
        }
        outputs = self._promotion_outputs(
            envelope,
            completion,
            claim_generation=claim_generation,
            attempt_dir=attempt_dir,
        )
        outputs_json = canonical_json_bytes(outputs).decode("utf-8")
        intent_id = store.derive_task_promotion_intent_id(
            run_id=record["run_id"],
            task_id=task_id,
            attempt_id=attempt_id,
            claim_generation=claim_generation,
            expected_run_revision=record["coordination_revision"],
            old_run_record_sha256=old_run_sha,
            envelope_sha256=str(claim["envelope_sha256"]),
            completion_sha256=str(claim["completion_sha256"]),
            outputs_json=outputs_json,
        )
        replacement = self._replacement_run_record(
            record,
            envelope,
            attempt_id=attempt_id,
            intent_id=intent_id,
            output_sha256=output_sha256,
            committed_at=_utc_now(),
        )
        intent = store.prepare_task_promotion(
            run_id=record["run_id"], task_id=task_id, attempt_id=attempt_id,
            claim_generation=claim_generation,
            expected_run_revision=record["coordination_revision"],
            old_run_record_sha256=old_run_sha, intent_id=intent_id,
            replacement_run_record=replacement, outputs=outputs,
        )
        _inject(fault_point, "after_promotion_intent_prepared")
        try:
            _, post_record, post_completion, _, _ = self._completion_gate(
                run_dir,
                task_id=task_id,
                attempt_id=attempt_id,
                claim_generation=claim_generation,
            )
            if post_record != record or post_completion != completion:
                raise ArtifactDrift(
                    "post-slot Completion Gate authority differs from pre-slot validation"
                )
            _, _, attempt_dir, candidates = self._authenticate_promotion_evidence(
                run_dir,
                intent,
                outputs,
                verify_run_boundary=True,
            )
        except (ArtifactDrift, ContractError, ControlStoreUnavailable, KernelConflict):
            store.abort_task_promotion(intent_id)
            raise
        journal = self._journal(intent, outputs)
        journal_path = attempt_dir / "p.json"
        journal_sha = write_json_atomic(journal_path, journal)
        _inject(fault_point, "after_promotion_journal_written")
        store.bind_task_promotion_journal(intent_id, journal_sha)
        _inject(fault_point, "after_promotion_journal_bound")
        for output in outputs:
            self._preserve_and_publish_output(
                run_dir,
                attempt_dir,
                output,
                publish=False,
                candidate_bytes=candidates[output["logical_id"]],
            )
        _inject(fault_point, "after_prior_outputs_preserved")
        for output in outputs:
            self._preserve_and_publish_output(
                run_dir,
                attempt_dir,
                output,
                publish=True,
                candidate_bytes=candidates[output["logical_id"]],
            )
            _inject(fault_point, "after_output_published")
        store.transition_task_promotion(
            intent_id, expected_state="PREPARED", new_state="FILES_PUBLISHED"
        )
        _inject(fault_point, "after_outputs_state_commit")
        replacement_sha = write_json_atomic(run_path, replacement)
        if replacement_sha != intent["replacement_run_record_sha256"]:
            raise ControlStoreUnavailable("replacement Run Record hash disagrees with intent")
        _inject(fault_point, "after_run_record_commit_marker")
        store.transition_task_promotion(
            intent_id, expected_state="FILES_PUBLISHED", new_state="RECORD_COMMITTED"
        )
        _inject(fault_point, "after_record_state_commit")
        _inject(fault_point, "before_promotion_intent_commit")
        store.commit_task_promotion(intent_id)
        _inject(fault_point, "after_promotion_intent_commit")
        self.verify_committed_task_state(run_dir)
        return TaskPromotionResult(
            run_id=record["run_id"], run_dir=run_dir, task_id=task_id,
            attempt_id=attempt_id, claim_generation=claim_generation,
            intent_id=intent_id,
        )

    def reconcile_promotion(self, run_dir: Path, intent: Any | None = None) -> bool:
        run_dir = run_dir.resolve()
        store = self.kernel._preflight_control_store()
        record, run_path, actual_run_sha = self._run_record(run_dir)
        run_id = record["run_id"]
        intent = intent or store.active_task_promotion(run_id)
        if intent is None:
            return False
        if intent["run_id"] != run_id:
            raise KernelConflict("Task promotion intent differs from caller Run authority")
        replacement_json = str(intent["replacement_run_record_json"])
        try:
            replacement = json.loads(replacement_json)
            outputs = json.loads(str(intent["outputs_json"]))
        except json.JSONDecodeError as exc:
            raise ControlStoreUnavailable("Task promotion intent JSON is invalid") from exc
        self.contracts.validate_run_record(replacement)
        if (
            hashlib.sha256(replacement_json.encode("utf-8")).hexdigest()
            != intent["replacement_run_record_sha256"]
            or replacement.get("last_mutation_intent_id") != intent["intent_id"]
            or replacement.get("run_id") != run_id
        ):
            raise ControlStoreUnavailable("Task promotion replacement authority is invalid")
        state = str(intent["state"])
        try:
            old_sha = str(intent["old_run_record_sha256"])
            replacement_sha = str(intent["replacement_run_record_sha256"])
            if state == "PREPARED" and actual_run_sha != old_sha:
                raise ControlStoreUnavailable(
                    "PREPARED promotion has a changed coordination marker"
                )
            if state == "FILES_PUBLISHED" and actual_run_sha not in {
                old_sha,
                replacement_sha,
            }:
                raise ControlStoreUnavailable(
                    "FILES_PUBLISHED promotion has a contradictory coordination marker"
                )
            if state == "RECORD_COMMITTED" and actual_run_sha != replacement_sha:
                raise ControlStoreUnavailable(
                    "RECORD_COMMITTED promotion lost its replacement marker"
                )
            self.kernel._verify_current_source(
                run_dir,
                expected_run_record_sha256=actual_run_sha,
            )
            _, _, attempt_dir, candidates = self._authenticate_promotion_evidence(
                run_dir,
                intent,
                outputs,
                verify_run_boundary=True,
                allow_published_outputs=True,
                allow_replacement_run_record=actual_run_sha == replacement_sha,
                allow_unbound_journal=True,
            )
            for output in outputs:
                self._verify_prior_generation_preservation(
                    run_dir,
                    output,
                    recover_from_canonical=state == "PREPARED",
                )
        except (ArtifactDrift, ContractError, ControlStoreUnavailable, KernelConflict):
            if state == "PREPARED":
                published = False
                for output in outputs:
                    canonical = self._canonical_task_output_path(
                        run_dir, output["canonical_path"]
                    )
                    if canonical.is_file() and sha256_file(canonical) == output["sha256"]:
                        published = True
                if not published:
                    store.abort_task_promotion(intent["intent_id"])
            raise
        journal = self._journal(intent, outputs)
        journal_path = attempt_dir / "p.json"
        if journal_path.exists():
            require_contained_path(
                journal_path,
                attempt_dir,
                purpose="Task promotion journal",
                error_type=ArtifactDrift,
                leaf_kind="file",
                require_single_link=True,
            )
            if read_json(journal_path) != journal:
                raise ArtifactDrift("Task promotion journal drifted")
            journal_sha = sha256_file(journal_path)
        else:
            if intent["state"] != "PREPARED" or actual_run_sha != intent["old_run_record_sha256"]:
                raise ControlStoreUnavailable("Task promotion journal is missing after publication")
            journal_sha = write_json_atomic(journal_path, journal)
        store.bind_task_promotion_journal(intent["intent_id"], journal_sha)
        if state == "PREPARED":
            for output in outputs:
                self._preserve_and_publish_output(
                    run_dir,
                    attempt_dir,
                    output,
                    publish=True,
                    candidate_bytes=candidates[output["logical_id"]],
                )
            store.transition_task_promotion(
                intent["intent_id"], expected_state="PREPARED", new_state="FILES_PUBLISHED"
            )
            state = "FILES_PUBLISHED"
        if state == "FILES_PUBLISHED":
            for output in outputs:
                canonical = self._canonical_task_output_path(
                    run_dir, output["canonical_path"]
                )
                require_contained_path(
                    canonical,
                    run_dir,
                    purpose="FILES_PUBLISHED promotion output",
                    error_type=ArtifactDrift,
                    leaf_kind="file",
                    require_single_link=True,
                )
                if sha256_file(canonical) != output["sha256"]:
                    raise ArtifactDrift("FILES_PUBLISHED promotion output drifted")
            actual_run_sha = sha256_file(run_path)
            if actual_run_sha == intent["old_run_record_sha256"]:
                replacement_sha = write_json_atomic(run_path, replacement)
                if replacement_sha != intent["replacement_run_record_sha256"]:
                    raise ControlStoreUnavailable("reconciled Run Record hash disagrees")
            elif actual_run_sha != intent["replacement_run_record_sha256"]:
                raise ControlStoreUnavailable("promotion coordination marker is contradictory")
            store.transition_task_promotion(
                intent["intent_id"], expected_state="FILES_PUBLISHED",
                new_state="RECORD_COMMITTED",
            )
            state = "RECORD_COMMITTED"
        if state == "RECORD_COMMITTED":
            if sha256_file(run_path) != intent["replacement_run_record_sha256"]:
                raise ControlStoreUnavailable("RECORD_COMMITTED Run Record fingerprint drifted")
            for output in outputs:
                canonical = self._canonical_task_output_path(
                    run_dir, output["canonical_path"]
                )
                require_contained_path(
                    canonical,
                    run_dir,
                    purpose="RECORD_COMMITTED promotion output",
                    error_type=ArtifactDrift,
                    leaf_kind="file",
                    require_single_link=True,
                )
                if sha256_file(canonical) != output["sha256"]:
                    raise ArtifactDrift("RECORD_COMMITTED promotion output drifted")
            store.commit_task_promotion(intent["intent_id"])
        self.verify_committed_task_state(run_dir)
        return True

    def verify_committed_task_state(self, run_dir: Path) -> None:
        record, run_path, run_sha = self._run_record(run_dir)
        store = self.kernel._preflight_control_store()
        durable_task_ids = store.task_ids_for_authority(str(record["run_id"]))
        task_namespace = run_dir / "workflow/tasks"
        if (
            record["schema_version"] == "1.0.0"
            and not durable_task_ids
            and not os.path.lexists(task_namespace)
        ):
            return
        if record["schema_version"] == "3.0.0" and not durable_task_ids:
            if os.path.lexists(task_namespace):
                if _is_link_or_reparse(task_namespace) or not task_namespace.is_dir():
                    raise ArtifactDrift("production Task namespace is invalid")
                if any(os.scandir(task_namespace)):
                    raise ArtifactDrift(
                        "production Task namespace contains unauthorised roots"
                    )
            return
        # Reconciliation passes no skip binding: every durable Attempt,
        # including an unclaimed prepared root and every current in-progress
        # or committed Attempt, must be present and authentic.  Execution
        # gates use a narrowly bound skip only while separately validating
        # their current Attempt.
        self._verify_task_root_inventory(
            run_dir,
            run_id=str(record["run_id"]),
        )
        if record["schema_version"] == "1.0.0":
            return
        intent = store.task_promotion_by_id(record["last_mutation_intent_id"])
        if record["schema_version"] != "3.0.0" and (
            intent is None
            or intent["state"] != "COMMITTED"
            or intent["run_id"] != record["run_id"]
            or intent["replacement_run_record_sha256"] != run_sha
        ):
            raise ArtifactDrift(
                "task-capable Run Record lacks a committed promotion authority",
                data={"drifted_paths": ["workflow/run.json"]},
            )
        if record["schema_version"] == "3.0.0":
            current_logical_ids = {
                binding["logical_id"]
                for checkpoint in record["checkpoints"].values()
                if checkpoint["status"] == "current"
                for binding in checkpoint["artifact_bindings"]
            }
            for logical_id in current_logical_ids:
                generation = record["artifact_generations"][logical_id]
                path = self._safe_run_path(run_dir, generation["path"])
                if (
                    _is_link_or_reparse(path)
                    or not path.is_file()
                    or sha256_file(path) != generation["sha256"]
                ):
                    raise ArtifactDrift(
                        "promoted production Task Artifact Generation drifted",
                        data={"drifted_paths": [generation["path"]]},
                    )
                if str(generation["producer"]).startswith("task:"):
                    producer = str(generation["producer"]).removeprefix("task:")
                    try:
                        producer_task_id, producer_attempt_id = producer.split(
                            "/", 1
                        )
                    except ValueError as exc:
                        raise ArtifactDrift(
                            "production Artifact has an invalid Task producer"
                        ) from exc
                    producer_intent = store.task_promotion_for_attempt(
                        producer_task_id, producer_attempt_id
                    )
                    if (
                        producer_intent is None
                        or producer_intent["state"] != "COMMITTED"
                        or producer_intent["run_id"] != record["run_id"]
                    ):
                        raise ArtifactDrift(
                            "production Artifact lacks committed Task authority"
                        )
                    try:
                        producer_outputs = json.loads(
                            str(producer_intent["outputs_json"])
                        )
                    except json.JSONDecodeError as exc:
                        raise ControlStoreUnavailable(
                            "production Task output authority is invalid"
                        ) from exc
                    if not any(
                        output["logical_id"] == logical_id
                        and output["canonical_path"] == generation["path"]
                        and output["sha256"] == generation["sha256"]
                        for output in producer_outputs
                    ):
                        raise ArtifactDrift(
                            "production Artifact differs from committed Task output"
                        )
            if {
                "source_candidate_inventory",
                "source_acquisition_decision_skeleton",
            }.issubset(current_logical_ids):
                inventory_generation = record["artifact_generations"][
                    "source_candidate_inventory"
                ]
                skeleton_generation = record["artifact_generations"][
                    "source_acquisition_decision_skeleton"
                ]
                inventory = read_json(run_dir / inventory_generation["path"])
                skeleton = read_json(run_dir / skeleton_generation["path"])
                self.contracts.validate("source-candidate-inventory", inventory)
                self.contracts.validate(
                    "source-acquisition-decision-skeleton", skeleton
                )
                self._validate_candidate_staging_inventory(
                    run_dir,
                    record,
                    inventory,
                    skeleton,
                    inventory_sha256=inventory_generation["sha256"],
                    inventory_generation=inventory_generation["generation"],
                )
            if "source_transcription" in current_logical_ids:
                transcript = record["artifact_generations"]["source_transcription"]
                self._validate_srt_bytes((run_dir / transcript["path"]).read_bytes())
            self._verify_committed_prior_generations(
                run_dir,
                str(record["run_id"]),
            )
            return
        decision = record["artifact_generations"]["source_acquisition_decision"]
        path = self._safe_run_path(run_dir, decision["path"], prefix="workflow")
        if (
            _is_link_or_reparse(path)
            or not path.is_file()
            or sha256_file(path) != decision["sha256"]
        ):
            raise ArtifactDrift(
                "promoted task Artifact Generation drifted",
                data={"drifted_paths": [decision["path"]]},
            )
        self._verify_committed_prior_generations(
            run_dir,
            str(record["run_id"]),
        )
