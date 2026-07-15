from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
from typing import Any
import uuid

from .contracts import ContractRegistry, _validate_project_relative_path
from .errors import (
    ArtifactDrift,
    ContractError,
    ControlStoreUnavailable,
    KernelConflict,
    TaskFault,
)
from .models import (
    TaskClaimResult,
    TaskCompletionResult,
    TaskPreparationResult,
    TaskPromotionResult,
)
from .prompts import generate_source_acquisition_prompt
from .utils import canonical_json_bytes, read_json, sha256_file, write_json_atomic


CLAIM_FAULT_POINTS = frozenset(
    {"after_claim_committed", "after_attempt_record_written"}
)
RECLAIM_FAULT_POINTS = frozenset(
    {"after_reclaim_committed", "after_reclaim_attempt_record_written"}
)
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
        dynamic_kernel_namespaces = ("workflow/tasks",)
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
        task_id: str,
        current_attempt_id: str,
    ) -> None:
        store = self.kernel._preflight_control_store()
        namespace = run_dir / "workflow/tasks"
        durable_task_ids = store.task_ids_for_authority(run_id)
        observed_task_ids: set[str] = set()
        for entry in os.scandir(namespace):
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
        if observed_task_ids != durable_task_ids:
            raise ContractError(
                "Task namespace differs from durable Claim authority",
                data={
                    "unexpected_task_roots": sorted(
                        observed_task_ids - durable_task_ids
                    ),
                    "missing_task_roots": sorted(
                        durable_task_ids - observed_task_ids
                    ),
                },
            )
        for durable_task_id in sorted(durable_task_ids):
            task_dir = namespace / durable_task_id
            self._verify_one_task_root(
                task_dir,
                run_id=run_id,
                task_id=durable_task_id,
                current_attempt_id=(
                    current_attempt_id if durable_task_id == task_id else None
                ),
            )

    def _verify_one_task_root(
        self,
        task_dir: Path,
        *,
        run_id: str,
        task_id: str,
        current_attempt_id: str | None,
    ) -> None:
        expected_root_files = {"task.json", "prompt.md"}
        expected_root_dirs = {"attempts"}
        actual_root_files: set[str] = set()
        actual_root_dirs: set[str] = set()
        for entry in os.scandir(task_dir):
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
        envelope_path = task_dir / "task.json"
        prompt_path = task_dir / "prompt.md"
        envelope = read_json(envelope_path)
        self.contracts.validate("subagent-task-envelope", envelope)
        prompt, provenance = generate_source_acquisition_prompt(self.project_root)
        store = self.kernel._preflight_control_store()
        claim = store.task_claim_for_task(task_id)
        if (
            claim is None
            or claim["authority_id"] != run_id
            or claim["envelope_sha256"] != sha256_file(envelope_path)
            or envelope["task_id"] != task_id
            or envelope["task_root_path"] != f"workflow/tasks/{task_id}"
            or envelope["authority_binding"]["run_id"] != run_id
            or (
                current_attempt_id is None
                and (
                    envelope["generated_prompt"]
                    != {
                        "path": f"workflow/tasks/{task_id}/prompt.md",
                        **provenance,
                    }
                    or prompt_path.read_bytes() != prompt
                )
            )
        ):
            raise ArtifactDrift(
                "Task namespace Envelope or Generated Prompt authority drifted"
            )
        attempts_root = task_dir / "attempts"
        durable_attempts = store.task_attempts_for_task(task_id)
        known_attempts = {str(row["attempt_id"]) for row in durable_attempts}
        if current_attempt_id is not None and current_attempt_id not in known_attempts:
            raise ControlStoreUnavailable("current Task Attempt lacks durable authority")
        observed_attempts: set[str] = set()
        for entry in os.scandir(attempts_root):
            info = entry.stat(follow_symlinks=False)
            if (
                entry.is_symlink()
                or not entry.is_dir(follow_symlinks=False)
                or getattr(info, "st_file_attributes", 0)
                & stat.FILE_ATTRIBUTE_REPARSE_POINT
            ):
                raise ContractError(
                    f"Task attempts namespace contains an invalid entry: {entry.name}"
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
        if _is_link_or_reparse(path) or not path.is_file():
            raise ArtifactDrift("Task Attempt record is absent or linked")
        record = read_json(path)
        self.contracts.validate("task-attempt", record)
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
            for entry in os.scandir(directory):
                relative = f"{prefix}/{entry.name}".lstrip("/")
                info = entry.stat(follow_symlinks=False)
                if entry.is_symlink() or (
                    getattr(info, "st_file_attributes", 0)
                    & stat.FILE_ATTRIBUTE_REPARSE_POINT
                ):
                    raise ContractError(
                        f"Task Attempt contains a link or reparse point: {relative}"
                    )
                if entry.is_dir(follow_symlinks=False):
                    actual_dirs.add(relative)
                    pending.append((Path(entry.path), relative))
                elif entry.is_file(follow_symlinks=False):
                    actual_files.add(relative)
                else:
                    raise ContractError(
                        f"Task Attempt contains an unsupported entry: {relative}"
                    )
        return actual_dirs, actual_files

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
        allowed_dirs = {"o"}
        allowed_files = {"attempt.json", "o/p.json"}
        if state in {"CLAIMED", "ABANDONED", "FAILED"}:
            allowed_files.add("o/.p.json.kernel-new")
        completion_json = attempt["completion_record_json"]
        promotion_journal_sha = attempt["promotion_journal_sha256"]
        if completion_json is not None:
            allowed_files.add("completion.json")
        if promotion_journal_sha is not None:
            allowed_files.add("p.json")
        if (
            "attempt.json" not in actual_files
            or not actual_dirs.issubset(allowed_dirs)
            or not actual_files.issubset(allowed_files)
            or ("o/p.json" in actual_files and "o" not in actual_dirs)
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
            expected_files = {
                "attempt.json",
                "o/p.json",
                "completion.json",
            }
            if state == "COMMITTED_COMPLETE":
                expected_files.add("p.json")
            if actual_dirs != {"o"} or actual_files != expected_files:
                raise ContractError(
                    "validated Task Attempt lacks its exact durable evidence inventory"
                )
        elif state not in {"CLAIMED", "ABANDONED", "STALE", "FAILED"}:
            raise ControlStoreUnavailable("durable Task Attempt state is unsupported")
        if "o/p.json" in actual_files:
            output = read_json(attempt_dir / "o/p.json")
            output_spec = envelope["required_outputs"][0]
            self.contracts.validate(output_spec["schema_name"], output)
            if (
                output["task_id"] != envelope["task_id"]
                or output["attempt_id"] != attempt["attempt_id"]
                or output["task_envelope_sha256"]
                != current_claim["envelope_sha256"]
            ):
                raise ArtifactDrift("staged Task Attempt output authority drifted")
        if completion_json is None:
            if "completion.json" in actual_files:
                raise ArtifactDrift("Task Completion evidence lacks durable authority")
            return
        completion = json.loads(str(completion_json))
        self.contracts.validate("task-completion-record", completion)
        completion_path = attempt_dir / "completion.json"
        if completion_path.is_file():
            if (
                read_json(completion_path) != completion
                or sha256_file(completion_path) != attempt["completion_sha256"]
            ):
                raise ArtifactDrift("durable Task Completion evidence drifted")
        elif state in {"VALIDATED_WAITING_FOR_PROMOTION", "COMMITTED_COMPLETE"}:
            raise ArtifactDrift("durable Task Completion evidence is absent")
        journal_path = attempt_dir / "p.json"
        if promotion_journal_sha is None:
            if journal_path.exists():
                raise ArtifactDrift("Task promotion journal lacks durable authority")
        elif (
            _is_link_or_reparse(journal_path)
            or not journal_path.is_file()
            or sha256_file(journal_path) != promotion_journal_sha
        ):
            raise ArtifactDrift("durable Task promotion journal drifted")

    def _build_envelope(
        self,
        run_dir: Path,
        record: dict[str, Any],
        *,
        logical_task_key: str,
        prepared_at: str,
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
        identity = canonical_json_bytes(
            {
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
        )
        task_id = hashlib.sha256(identity).hexdigest()[:32]
        task_root = f"workflow/tasks/{task_id}"
        prompt, provenance = generate_source_acquisition_prompt(self.project_root)
        allowed_read_paths = sorted(
            {source["path"], *(item["path"] for item in source_manifest["artifacts"])}
        )
        envelope = {
            "schema_name": "subagent-task-envelope",
            "schema_version": "1.0.0",
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
        self.contracts.validate("subagent-task-envelope", envelope)
        return envelope, prompt

    def prepare_source_acquisition_task(
        self,
        run_dir: Path,
        *,
        logical_task_key: str,
        prepared_at: str,
    ) -> TaskPreparationResult:
        run_dir = run_dir.resolve()
        self.kernel._verify_current_source(run_dir)
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
        return TaskPreparationResult(
            run_id=record["run_id"],
            run_dir=run_dir,
            task_id=task_id,
            task_dir=task_dir,
            envelope_path=envelope_path,
            prompt_path=prompt_path,
        )

    def _verify_task_files(
        self, run_dir: Path, expected_envelope: dict[str, Any], expected_prompt: bytes
    ) -> None:
        task_dir = self._safe_run_path(
            run_dir, expected_envelope["task_root_path"], prefix="workflow"
        )
        if _is_link_or_reparse(task_dir) or not task_dir.is_dir():
            raise ArtifactDrift("Task directory is absent or linked")
        envelope_path = task_dir / "task.json"
        prompt_path = task_dir / "prompt.md"
        if (
            _is_link_or_reparse(envelope_path)
            or _is_link_or_reparse(prompt_path)
            or not envelope_path.is_file()
            or not prompt_path.is_file()
        ):
            raise ArtifactDrift("Task Envelope or Generated Task Prompt is absent or linked")
        actual_envelope = read_json(envelope_path)
        self.contracts.validate("subagent-task-envelope", actual_envelope)
        if actual_envelope != expected_envelope or prompt_path.read_bytes() != expected_prompt:
            raise ArtifactDrift("Task Envelope or Generated Task Prompt drifted")
        if sha256_file(prompt_path) != actual_envelope["generated_prompt"]["sha256"]:
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
        expected, prompt = self._build_envelope(
            run_dir,
            record,
            logical_task_key=envelope["logical_task_key"],
            prepared_at=envelope["prepared_at"],
        )
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
        return TaskClaimResult(
            run_id=envelope["authority_binding"]["run_id"],
            run_dir=run_dir,
            task_id=task_id,
            attempt_id=attempt_id,
            claim_generation=int(claim["claim_generation"]),
            attempt_dir=attempt_dir,
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
        store = self.kernel._preflight_control_store()
        claim = store.claim_task(
            authority_id=record["run_id"],
            task_id=task_id,
            envelope_sha256=sha256_file(run_dir / envelope["task_root_path"] / "task.json"),
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
        completion_allowed: bool,
        promotion_journal_expected: bool = False,
    ) -> None:
        expected_dirs = {"o"}
        expected_files = {"attempt.json", "o/p.json"}
        if completion_allowed and (attempt_dir / "completion.json").exists():
            expected_files.add("completion.json")
        if promotion_journal_expected:
            expected_files.add("p.json")
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
        if claim["attempt_state"] not in {
            "CLAIMED",
            "VALIDATED_WAITING_FOR_PROMOTION",
        }:
            raise KernelConflict("Task Attempt is not eligible for completion")
        if record["coordination_revision"] != envelope["authority_binding"]["expected_coordination_revision"]:
            raise KernelConflict("Task authority revision is stale")
        source = record["artifact_generations"].get("source_manifest")
        declared_source = envelope["input_artifacts"][0]
        if (
            source is None
            or source["generation"] != declared_source["generation"]
            or source["sha256"] != declared_source["sha256"]
            or sha256_file(run_dir / source["path"]) != source["sha256"]
        ):
            raise ArtifactDrift(
                "Task input Artifact Generation is stale",
                data={"drifted_paths": ["source/manifest.json"]},
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
            attempt_dir, completion_allowed=completion_path.exists()
        )
        self._verify_task_root_inventory(
            run_dir,
            run_id=record["run_id"],
            task_id=task_id,
            current_attempt_id=attempt_id,
        )
        self._verify_attempt_record(
            attempt_dir,
            envelope=envelope,
            attempt=claim,
            current_claim=claim,
        )
        self._verify_protected_run_snapshot(run_dir, envelope)
        output_spec = envelope["required_outputs"][0]
        patch_path = attempt_dir / output_spec["attempt_relative_path"]
        if _is_link_or_reparse(patch_path) or not patch_path.is_file():
            raise ContractError("required Judgment Patch is absent or linked")
        patch = read_json(patch_path)
        self.contracts.validate(output_spec["schema_name"], patch)
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
            raise ContractError("Judgment Patch selected an undeclared subtitle track")
        canonical = self._safe_run_path(
            run_dir, output_spec["canonical_path"], prefix="workflow"
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
                    "logical_id": "source_manifest",
                    "generation": source["generation"],
                    "sha256": source["sha256"],
                }
            ],
            "outputs": [
                {
                    "logical_id": output_spec["logical_id"],
                    "attempt_path": output_spec["attempt_relative_path"],
                    "canonical_path": output_spec["canonical_path"],
                    "sha256": sha256_file(patch_path),
                }
            ],
            "gate_status": "pass",
            "validated_at": validated_at,
        }
        self.contracts.validate("task-completion-record", completion)
        if prepared_completion_json is not None and prepared_completion != completion:
            raise ArtifactDrift("prepared Task Completion bindings drifted")
        if completion_path.exists():
            if _is_link_or_reparse(completion_path) or read_json(completion_path) != completion:
                raise ArtifactDrift("Task Completion evidence drifted")
        return envelope, record, completion, attempt_dir, patch

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
        self.kernel._preflight_control_store().prepare_task_completion(
            task_id=task_id,
            attempt_id=attempt_id,
            claim_generation=claim_generation,
            completion_record=completion,
        )
        _inject(fault_point, "after_completion_prepared")
        if completion_path.exists():
            if _is_link_or_reparse(completion_path) or read_json(completion_path) != completion:
                raise ArtifactDrift("Task Completion evidence drifted")
            completion_sha = sha256_file(completion_path)
        else:
            completion_sha = write_json_atomic(completion_path, completion)
        _inject(fault_point, "after_completion_record_written")
        self.kernel._preflight_control_store().mark_task_validated(
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
        output_sha: str,
        committed_at: str,
    ) -> dict[str, Any]:
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
            "sha256": output_sha,
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
            "evidence_sha256": output_sha,
        }
        self.contracts.validate_run_record(replacement)
        return replacement

    def _promotion_outputs(
        self,
        envelope: dict[str, Any],
        completion: dict[str, Any],
        *,
        claim_generation: int,
    ) -> list[dict[str, Any]]:
        output = completion["outputs"][0]
        expected = envelope["required_outputs"][0]
        if (
            output["logical_id"] != expected["logical_id"]
            or output["canonical_path"] != expected["canonical_path"]
            or output["attempt_path"] != expected["attempt_relative_path"]
        ):
            raise ContractError("Task Completion output binding drifted")
        return [
            {
                **output,
                "prior_sha256": expected["expected_prior_sha256"],
                "preservation_path": (
                    f"待删除/task-promotions/{envelope['task_id']}/"
                    f"g{claim_generation:08d}/previous/"
                    "decision.json"
                ),
            }
        ]

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
            task_id=str(intent["task_id"]),
            current_attempt_id=str(intent["attempt_id"]),
        )
        task_root = f"workflow/tasks/{intent['task_id']}"
        task_dir = self._safe_run_path(run_dir, task_root, prefix="workflow")
        envelope_path = task_dir / "task.json"
        prompt_path = task_dir / "prompt.md"
        if (
            _is_link_or_reparse(envelope_path)
            or _is_link_or_reparse(prompt_path)
            or not envelope_path.is_file()
            or not prompt_path.is_file()
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
        if identity_version == "evidence-v2":
            prompt_bytes, provenance = generate_source_acquisition_prompt(
                self.project_root
            )
            expected_prompt = {"path": f"{task_root}/prompt.md", **provenance}
            if (
                envelope["generated_prompt"] != expected_prompt
                or prompt_path.read_bytes() != prompt_bytes
            ):
                raise ArtifactDrift(
                    "Task promotion current Prompt authority drifted"
                )
        elif identity_version == "legacy-v1":
            if (
                envelope["generated_prompt"]["path"] != f"{task_root}/prompt.md"
                or sha256_file(prompt_path)
                != envelope["generated_prompt"]["sha256"]
            ):
                raise ArtifactDrift(
                    "legacy Task promotion historical Prompt authority drifted"
                )
        else:
            raise ControlStoreUnavailable(
                "Task promotion identity version is absent or unsupported"
            )
        attempt_dir = self._safe_run_path(
            run_dir,
            f"{task_root}/attempts/{intent['attempt_id']}",
            prefix="workflow",
        )
        self._attempt_inventory(
            attempt_dir,
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
        if _is_link_or_reparse(completion_path) or not completion_path.is_file():
            raise ArtifactDrift("Task promotion Completion evidence is absent or linked")
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
        )
        if outputs != expected_outputs or {
            output["canonical_path"] for output in outputs
        } != set(envelope["write_set"]):
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
        for output in outputs:
            source = self._safe_run_path(attempt_dir, output["attempt_path"])
            if _is_link_or_reparse(source) or not source.is_file():
                raise ArtifactDrift("validated Task Attempt output is absent or linked")
            candidate = source.read_bytes()
            if hashlib.sha256(candidate).hexdigest() != output["sha256"]:
                raise ArtifactDrift(
                    "validated Task Attempt output fingerprint drifted"
                )
            candidates[output["logical_id"]] = candidate
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
        if _is_link_or_reparse(source) or not source.is_file():
            raise ArtifactDrift("validated Task Attempt output is absent or linked")
        if (
            hashlib.sha256(candidate_bytes).hexdigest() != output["sha256"]
            or source.read_bytes() != candidate_bytes
        ):
            raise ArtifactDrift("validated Task Attempt output fingerprint drifted")
        canonical = self._safe_run_path(
            run_dir, output["canonical_path"], prefix="workflow"
        )
        preservation = self._safe_run_path(
            run_dir, output["preservation_path"], prefix="待删除"
        )
        prior_sha = output["prior_sha256"]
        if canonical.exists():
            if _is_link_or_reparse(canonical) or not canonical.is_file():
                raise ArtifactDrift("canonical promotion target is linked or not a file")
            actual = sha256_file(canonical)
            if actual == output["sha256"]:
                return
            if prior_sha is None or actual != prior_sha:
                raise ArtifactDrift("canonical promotion target differs from old and new generations")
            preservation.parent.mkdir(parents=True, exist_ok=True)
            if preservation.exists():
                if _is_link_or_reparse(preservation) or sha256_file(preservation) != prior_sha:
                    raise ArtifactDrift("preserved prior Artifact Generation drifted")
            else:
                _write_bytes_atomic(preservation, canonical.read_bytes())
        elif prior_sha is not None:
            raise ArtifactDrift("canonical prior Artifact Generation disappeared")
        if publish:
            _write_bytes_atomic(canonical, candidate_bytes)
            if sha256_file(canonical) != output["sha256"]:
                raise ArtifactDrift("promoted canonical output failed fingerprint verification")

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
                outputs = json.loads(str(existing["outputs_json"]))
                self._authenticate_promotion_evidence(
                    run_dir,
                    existing,
                    outputs,
                    verify_run_boundary=False,
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
        output_sha = completion["outputs"][0]["sha256"]
        outputs = self._promotion_outputs(
            envelope, completion, claim_generation=claim_generation
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
            output_sha=output_sha,
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
        except (ArtifactDrift, ContractError, ControlStoreUnavailable, KernelConflict):
            if state == "PREPARED":
                published = False
                for output in outputs:
                    canonical = self._safe_run_path(
                        run_dir, output["canonical_path"], prefix="workflow"
                    )
                    if canonical.is_file() and sha256_file(canonical) == output["sha256"]:
                        published = True
                if not published:
                    store.abort_task_promotion(intent["intent_id"])
            raise
        journal = self._journal(intent, outputs)
        journal_path = attempt_dir / "p.json"
        if journal_path.exists():
            if _is_link_or_reparse(journal_path) or read_json(journal_path) != journal:
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
                canonical = self._safe_run_path(
                    run_dir, output["canonical_path"], prefix="workflow"
                )
                if not canonical.is_file() or sha256_file(canonical) != output["sha256"]:
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
                canonical = self._safe_run_path(
                    run_dir, output["canonical_path"], prefix="workflow"
                )
                if not canonical.is_file() or sha256_file(canonical) != output["sha256"]:
                    raise ArtifactDrift("RECORD_COMMITTED promotion output drifted")
            store.commit_task_promotion(intent["intent_id"])
        self.verify_committed_task_state(run_dir)
        return True

    def verify_committed_task_state(self, run_dir: Path) -> None:
        record, run_path, run_sha = self._run_record(run_dir)
        if record["schema_version"] == "1.0.0":
            return
        intent = self.kernel._preflight_control_store().task_promotion_by_id(
            record["last_mutation_intent_id"]
        )
        if (
            intent is None
            or intent["state"] != "COMMITTED"
            or intent["run_id"] != record["run_id"]
            or intent["replacement_run_record_sha256"] != run_sha
        ):
            raise ArtifactDrift(
                "task-capable Run Record lacks a committed promotion authority",
                data={"drifted_paths": ["workflow/run.json"]},
            )
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
