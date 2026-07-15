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
COMPLETION_FAULT_POINTS = frozenset(
    {"after_completion_record_written", "after_completion_state_commit"}
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
                / task_id
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
        self, run_dir: Path, task_id: str
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
        self._verify_task_files(run_dir, expected, prompt)
        return envelope, record, task_dir, run_path

    def _create_attempt_record(
        self,
        run_dir: Path,
        envelope: dict[str, Any],
        claim: Any,
        *,
        fault_point: str | None,
    ) -> TaskClaimResult:
        task_id = envelope["task_id"]
        attempt_id = str(claim["attempt_id"])
        attempt_rel = f"workflow/tasks/{task_id}/attempts/{attempt_id}"
        if claim["attempt_path"] != attempt_rel:
            raise ControlStoreUnavailable("Task Attempt path binding in Control Store is invalid")
        attempt_dir = self._safe_run_path(run_dir, attempt_rel, prefix="workflow")
        attempt_record = {
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
        self.contracts.validate("task-attempt", attempt_record)
        if attempt_dir.exists():
            path = attempt_dir / "attempt.json"
            if not path.is_file() or _is_link_or_reparse(path) or read_json(path) != attempt_record:
                raise ArtifactDrift("Task Attempt record drifted")
        else:
            staging = (
                run_dir / "待删除/task-attempts" / task_id / uuid.uuid4().hex
            )
            staging.mkdir(parents=True, exist_ok=False)
            write_json_atomic(staging / "attempt.json", attempt_record)
            attempt_dir.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging, attempt_dir)
        _inject(fault_point, "after_attempt_record_written")
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
            run_dir, envelope, claim, fault_point=fault_point
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
        if fault_point is not None and fault_point not in CLAIM_FAULT_POINTS:
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
        _inject(fault_point, "after_claim_committed")
        return self._create_attempt_record(
            run_dir, envelope, claim, fault_point=fault_point
        )

    def _attempt_inventory(
        self, attempt_dir: Path, *, completion_allowed: bool
    ) -> None:
        if _is_link_or_reparse(attempt_dir) or not attempt_dir.is_dir():
            raise ContractError("Task Attempt boundary is absent or linked")
        expected_dirs = {"o"}
        expected_files = {"attempt.json", "o/p.json"}
        if completion_allowed and (attempt_dir / "completion.json").exists():
            expected_files.add("completion.json")
        actual_dirs: set[str] = set()
        actual_files: set[str] = set()
        pending = [(attempt_dir, "")]
        while pending:
            directory, prefix = pending.pop()
            for entry in os.scandir(directory):
                relative = f"{prefix}/{entry.name}".lstrip("/")
                info = entry.stat(follow_symlinks=False)
                if entry.is_symlink() or (
                    getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
                ):
                    raise ContractError(f"Task Attempt contains a link or reparse point: {relative}")
                if entry.is_dir(follow_symlinks=False):
                    actual_dirs.add(relative)
                    pending.append((Path(entry.path), relative))
                elif entry.is_file(follow_symlinks=False):
                    actual_files.add(relative)
                else:
                    raise ContractError(f"Task Attempt contains an unsupported entry: {relative}")
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
        completion_allowed: bool,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], Path, dict[str, Any]]:
        self.kernel._verify_current_source(run_dir)
        envelope, record, task_dir, run_path = self._load_current_task(run_dir, task_id)
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
        self._attempt_inventory(attempt_dir, completion_allowed=completion_allowed)
        attempt_record = read_json(attempt_dir / "attempt.json")
        self.contracts.validate("task-attempt", attempt_record)
        expected_attempt = {
            "task_id": task_id,
            "attempt_id": attempt_id,
            "claim_generation": claim_generation,
            "task_envelope_sha256": claim["envelope_sha256"],
            "attempt_path": claim["attempt_path"],
            "coordinator_session_id": claim["coordinator_session_id"],
            "worker_id": claim["worker_id"],
            "claimed_at": claim["updated_at"],
            "state": "claimed",
        }
        for key, value in expected_attempt.items():
            if attempt_record[key] != value:
                raise ArtifactDrift("Task Attempt record disagrees with its Claim")
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
            "validated_at": envelope["prepared_at"],
        }
        self.contracts.validate("task-completion-record", completion)
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
            completion_allowed=True,
        )
        completion_path = attempt_dir / "completion.json"
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

    @staticmethod
    def _intent_id(
        *,
        run_id: str,
        task_id: str,
        attempt_id: str,
        claim_generation: int,
        expected_revision: int,
        old_run_sha: str,
        output_sha: str,
    ) -> str:
        return hashlib.sha256(
            "\0".join(
                (
                    "task_artifact_promotion", run_id, task_id, attempt_id,
                    str(claim_generation), str(expected_revision), old_run_sha, output_sha,
                )
            ).encode("utf-8")
        ).hexdigest()

    def _replacement_run_record(
        self,
        record: dict[str, Any],
        envelope: dict[str, Any],
        *,
        attempt_id: str,
        intent_id: str,
        output_sha: str,
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
            "committed_at": envelope["prepared_at"],
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

    def _preserve_and_publish_output(
        self,
        run_dir: Path,
        attempt_dir: Path,
        output: dict[str, Any],
        *,
        publish: bool,
    ) -> None:
        source = self._safe_run_path(attempt_dir, output["attempt_path"])
        if _is_link_or_reparse(source) or not source.is_file():
            raise ArtifactDrift("validated Task Attempt output is absent or linked")
        if sha256_file(source) != output["sha256"]:
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
            _write_bytes_atomic(canonical, source.read_bytes())
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
        existing = store.task_promotion_for_attempt(task_id, attempt_id)
        if existing is not None:
            if int(existing["claim_generation"]) != claim_generation:
                raise KernelConflict("Task promotion fencing token is stale")
            if existing["state"] != "COMMITTED":
                self.reconcile_promotion(run_dir, existing)
                existing = store.task_promotion_by_id(existing["intent_id"])
            if existing is not None and existing["state"] == "COMMITTED":
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
            completion_allowed=True,
        )
        completion_path = attempt_dir / "completion.json"
        if not completion_path.is_file() or read_json(completion_path) != completion:
            raise KernelConflict("Task must pass Completion Gate before promotion")
        claim = store.task_claim_for_attempt(task_id, attempt_id)
        if claim is None or claim["attempt_state"] != "VALIDATED_WAITING_FOR_PROMOTION":
            raise KernelConflict("Task Attempt is not waiting for promotion")
        run_path = run_dir / "workflow/run.json"
        old_run_sha = sha256_file(run_path)
        output_sha = completion["outputs"][0]["sha256"]
        intent_id = self._intent_id(
            run_id=record["run_id"], task_id=task_id, attempt_id=attempt_id,
            claim_generation=claim_generation,
            expected_revision=record["coordination_revision"],
            old_run_sha=old_run_sha, output_sha=output_sha,
        )
        replacement = self._replacement_run_record(
            record, envelope, attempt_id=attempt_id,
            intent_id=intent_id, output_sha=output_sha,
        )
        outputs = self._promotion_outputs(
            envelope, completion, claim_generation=claim_generation
        )
        intent = store.prepare_task_promotion(
            run_id=record["run_id"], task_id=task_id, attempt_id=attempt_id,
            claim_generation=claim_generation,
            expected_run_revision=record["coordination_revision"],
            old_run_record_sha256=old_run_sha, intent_id=intent_id,
            replacement_run_record=replacement, outputs=outputs,
        )
        _inject(fault_point, "after_promotion_intent_prepared")
        journal = self._journal(intent, outputs)
        journal_path = attempt_dir / "p.json"
        journal_sha = write_json_atomic(journal_path, journal)
        _inject(fault_point, "after_promotion_journal_written")
        store.bind_task_promotion_journal(intent_id, journal_sha)
        _inject(fault_point, "after_promotion_journal_bound")
        for output in outputs:
            self._preserve_and_publish_output(
                run_dir, attempt_dir, output, publish=False
            )
        _inject(fault_point, "after_prior_outputs_preserved")
        for output in outputs:
            self._preserve_and_publish_output(
                run_dir, attempt_dir, output, publish=True
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
        journal = self._journal(intent, outputs)
        attempt_dir = self._safe_run_path(
            run_dir,
            f"workflow/tasks/{intent['task_id']}/attempts/{intent['attempt_id']}",
            prefix="workflow",
        )
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
        state = str(intent["state"])
        if state == "PREPARED":
            if actual_run_sha != intent["old_run_record_sha256"]:
                raise ControlStoreUnavailable("PREPARED promotion has a changed coordination marker")
            for output in outputs:
                self._preserve_and_publish_output(
                    run_dir, attempt_dir, output, publish=True
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
