from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from pathlib import Path
import shutil
from typing import Any
from contextlib import contextmanager

from .errors import ArtifactDrift, ContractError, KernelConflict, ProductionFault, SupersededProductionAttempt
from .guarded_compile import GuardedCompileProvider
from .utils import read_json, require_contained_path, sha256_file, write_json_atomic


def _digest(*values: str, length: int = 32) -> str:
    return hashlib.sha256("\0".join(values).encode("utf-8")).hexdigest()[:length]


def _is_lower_hex(value: str, length: int) -> bool:
    return len(value) == length and all(character in "0123456789abcdef" for character in value)


PRODUCTION_FAULT_POINTS = frozenset(
    {
        "after_promotion_prepared",
        "after_first_output",
        "after_promotion_committed",
        "before_receipt_committed",
        "after_state_committed",
    }
)


def _inject(selected: str | None, current: str) -> None:
    if selected == current:
        raise ProductionFault(current)


class ContentProduction:
    """Deep Module implementing the section-scoped production graph."""

    def __init__(self, kernel: Any) -> None:
        self.kernel = kernel

    @staticmethod
    def _state_path(run_dir: Path) -> Path:
        return run_dir / "workflow/production-state.json"

    @staticmethod
    @contextmanager
    def _exclusive_run_lock(run_dir: Path):
        lock_path = run_dir / "待删除/production-advance.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX)
            try:
                yield
            finally:
                os.lseek(descriptor, 0, os.SEEK_SET)
                if os.name == "nt":
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    @staticmethod
    def _verify_artifacts(run_dir: Path, state: dict[str, Any]) -> None:
        for logical_id, artifact in state["artifacts"].items():
            path = run_dir / artifact["path"]
            try:
                require_contained_path(
                    path,
                    run_dir,
                    purpose=f"Production artifact {logical_id}",
                    error_type=ArtifactDrift,
                    leaf_kind="file",
                    require_single_link=True,
                )
            except ArtifactDrift:
                raise
            if (
                path.stat().st_size != artifact["size"]
                or sha256_file(path) != artifact["sha256"]
            ):
                raise ArtifactDrift(
                    f"Production artifact drifted: {logical_id}",
                    data={"logical_id": logical_id, "path": artifact["path"]},
                )

    def _load_or_create_state(self, run_dir: Path) -> dict[str, Any]:
        run_dir = run_dir.resolve()
        record = self.kernel.require_current_validated_source_package(run_dir)
        source = record["artifact_generations"]["source_manifest"]
        current_source_binding = {
            "logical_id": "source_manifest",
            "generation": source["generation"],
            "sha256": source["sha256"],
        }
        path = self._state_path(run_dir)
        if path.exists():
            state = read_json(path)
            self.kernel.contracts.validate("production-state", state)
            if state.get("run_id") != record["run_id"]:
                raise KernelConflict("Production State belongs to another Run")
            if state.get("source_binding") != current_source_binding:
                raise KernelConflict("Production State binds a stale Source Manifest generation")
            self._verify_artifacts(run_dir, state)
            return state
        state = {
            "schema_name": "production-state",
            "schema_version": "2.0.0",
            "kernel_version": "2.0.0",
            "run_id": record["run_id"],
            "source_binding": current_source_binding,
            "artifacts": {},
            "completed_tasks": [],
            "completed_roles": [],
            "claims": {},
            "receipts": {},
            "checkpoints": {"source_ready": "current", "draft_compile_ready": "pending"},
            "sections": {},
            "promotion_sequence": [],
        }
        self.kernel.contracts.validate("production-state", state)
        write_json_atomic(path, state)
        return state

    @staticmethod
    def _artifact(state: dict[str, Any], logical_id: str) -> dict[str, Any]:
        try:
            return state["artifacts"][logical_id]
        except KeyError as exc:
            raise KernelConflict(f"required Production artifact is missing: {logical_id}") from exc

    @staticmethod
    def _record_artifact(
        state: dict[str, Any], logical_id: str, path: str, source: Path, producer: str
    ) -> dict[str, Any]:
        prior = state["artifacts"].get(logical_id)
        generation = 1 if prior is None else prior["generation"] + 1
        value = {
            "path": path,
            "generation": generation,
            "sha256": sha256_file(source),
            "size": source.stat().st_size,
            "producer": producer,
        }
        state["artifacts"][logical_id] = value
        return value

    @staticmethod
    def _figure_logical_task_key(slot: dict[str, Any]) -> str:
        wave = "incremental" if slot["wave"] == "incremental" else "required"
        return f"figure-{wave}-{slot['slot_id'].replace('_', '-')}"

    @classmethod
    def _figure_task_bindings(
        cls, state: dict[str, Any]
    ) -> dict[str, tuple[str, dict[str, Any]]]:
        return {
            cls._figure_logical_task_key(slot): (section_id, slot)
            for section_id, section in state["sections"].items()
            for slot in section["figure_slots"]
        }

    def _envelope(
        self, run_dir: Path, state: dict[str, Any], logical_key: str,
        role: str, *, section_id: str | None = None, slot_id: str | None = None,
    ) -> dict[str, Any]:
        if role == "outline":
            write_set, outputs = ["work/outline/outline.json"], ["outline.json"]
        elif role == "pyramid_outline":
            write_set, outputs = ["review/pyramid/outline.json"], ["pyramid-report.json"]
        elif role == "writer":
            assert section_id
            write_set = [f"work/writers/{section_id}.tex", f"work/writers/{section_id}.result.json"]
            outputs = [f"{section_id}.tex", "writer-result.json"]
        elif role == "figure":
            assert section_id and slot_id
            manifest_path = "work/figures/figure-manifest.json" if slot_id == "figure_01" else f"work/figures/{slot_id}.manifest.json"
            write_set = [f"figures/{slot_id}.png", manifest_path, f"work/figures/{slot_id}.tex"]
            outputs = [f"{slot_id}.png", "figure-manifest.json", f"{slot_id}.tex"]
        elif role == "pyramid_section":
            assert section_id
            write_set, outputs = [f"review/pyramid/{section_id}.json"], ["pyramid-report.json"]
        else:
            write_set, outputs = ["review/pyramid/main.json"], ["pyramid-report.json"]
        input_generations = [state["source_binding"]]
        dependency_ids: tuple[str, ...] = {
            "outline": (),
            "pyramid_outline": ("outline_contract",),
            "writer": ("outline_contract", "pyramid_outline_report"),
            "figure": ("outline_contract", "pyramid_outline_report"),
            "pyramid_section": (f"integrated_{section_id}",),
            "pyramid_main": ("integrated_main", "integration_manifest"),
        }[role]
        if role == "figure" and slot_id and "_incremental_" in slot_id:
            dependency_ids += (f"writer_result_{section_id}",)
        if role == "pyramid_main":
            dependency_ids += tuple(f"pyramid_{item}_report" for item in state["sections"])
        for logical_id in dependency_ids:
            artifact = self._artifact(state, logical_id)
            input_generations.append(
                {
                    "logical_id": logical_id,
                    "generation": artifact["generation"],
                    "sha256": artifact["sha256"],
                }
            )
        task_id = _digest(state["run_id"], logical_key)
        claim = state["claims"].get(logical_key)
        if claim is None:
            claim = {
                "task_id": task_id,
                "claim_generation": 1,
                "claim_token": _digest(state["run_id"], logical_key, "1", "claim"),
                "status": "available",
            }
            state["claims"][logical_key] = claim
        envelope: dict[str, Any] = {
            "schema_name": "production-task-envelope",
            "schema_version": "2.0.0",
            "kernel_version": "2.0.0",
            "run_id": state["run_id"],
            "task_id": task_id,
            "logical_task_key": logical_key,
            "role": role,
            "claim_generation": claim["claim_generation"],
            "claim_token": claim["claim_token"],
            "input_generations": input_generations,
            "write_set": write_set,
            "required_outputs": outputs,
            "attempt_root": f"workflow/tasks/{task_id}/attempts",
        }
        if section_id:
            envelope["section_id"] = section_id
        if slot_id:
            envelope["slot_id"] = slot_id
        target_by_role = {
            "pyramid_outline": "outline_contract",
            "pyramid_section": f"integrated_{section_id}",
            "pyramid_main": "integrated_main",
        }
        if role in target_by_role:
            logical_id = target_by_role[role]
            target = self._artifact(state, logical_id)
            envelope["pyramid_target"] = {
                "logical_id": logical_id,
                "path": target["path"],
                "generation": target["generation"],
                "sha256": target["sha256"],
            }
            envelope["evaluation_context"] = {
                "pyramid_standard": "pyramid-principle-v1",
                "checkpoint": role,
                "audience": "reader-facing Chinese teaching PDF",
            }
        self.kernel.contracts.validate("production-task-envelope", envelope)
        task_dir = run_dir / "workflow/tasks" / envelope["task_id"]
        task_dir.mkdir(parents=True, exist_ok=True)
        path = task_dir / "envelope.json"
        if not path.exists() or read_json(path) != envelope:
            write_json_atomic(path, envelope)
        return envelope

    def _plan_locked(
        self,
        run_dir: Path,
        state: dict[str, Any],
        *,
        supersede_task_id: str | None = None,
        expected_claim_generation: int | None = None,
        persist: bool = True,
    ) -> dict[str, Any]:
        completed = set(state["completed_tasks"])
        specs: list[tuple[str, str, str | None, str | None]] = []
        if "outline" not in completed:
            specs = [("outline", "outline", None, None)]
        elif "pyramid-outline" not in completed:
            specs = [("pyramid-outline", "pyramid_outline", None, None)]
        else:
            for section_id, section in state["sections"].items():
                if section["status"] == "blocked":
                    continue
                writer_key = f"writer-{section_id.replace('_', '-')}"
                if writer_key not in completed:
                    specs.append((writer_key, "writer", section_id, None))
                for slot in section["figure_slots"]:
                    if slot["wave"] == "incremental" and writer_key not in completed:
                        continue
                    key = self._figure_logical_task_key(slot)
                    if key not in completed:
                        specs.append((key, "figure", section_id, slot["slot_id"]))
                pyramid_key = f"pyramid-section-{section_id.replace('_', '-')}"
                if f"integrated_{section_id}" in state["artifacts"] and pyramid_key not in completed:
                    specs.append((pyramid_key, "pyramid_section", section_id, None))
            pyramid_keys = {f"pyramid-section-{item.replace('_', '-')}" for item in state["sections"]}
            if state["sections"] and pyramid_keys.issubset(completed) and "pyramid-main" not in completed:
                specs = [("pyramid-main", "pyramid_main", None, None)]
        specs.sort(key=lambda item: item[0])
        if supersede_task_id is not None:
            logical_key = next((key for key in state["claims"] if _digest(state["run_id"], key) == supersede_task_id), None)
            if logical_key is None:
                raise KernelConflict("Production task is not currently reclaimable")
            claim = state["claims"][logical_key]
            if expected_claim_generation != claim["claim_generation"]:
                raise KernelConflict("Production claim generation changed before reclaim")
            generation = claim["claim_generation"] + 1
            state["claims"][logical_key] = {
                "task_id": supersede_task_id,
                "claim_generation": generation,
                "claim_token": _digest(state["run_id"], logical_key, str(generation), "claim"),
                "status": "available",
            }
            state["receipts"].pop(logical_key, None)
            if logical_key in completed:
                state["completed_tasks"].remove(logical_key)
            self._invalidate_for_supersede(state, logical_key)
            specs = []
            if logical_key == "outline":
                role = "outline"
            elif logical_key == "pyramid-outline":
                role = "pyramid_outline"
            elif logical_key == "pyramid-main":
                role = "pyramid_main"
            elif logical_key.startswith("pyramid-section-"):
                role = "pyramid_section"
            elif logical_key.startswith("writer-"):
                role = "writer"
            else:
                role = "figure"
            section_id = logical_key.split("writer-", 1)[1].replace("-", "_", 1) if role == "writer" else None
            if role == "pyramid_section":
                section_id = "section_" + logical_key.rsplit("-", 1)[-1]
            slot_id = None
            if role == "figure":
                binding = self._figure_task_bindings(state).get(logical_key)
                if binding is None:
                    raise KernelConflict("Production Figure task has no current Slot binding")
                section_id, slot = binding
                slot_id = slot["slot_id"]
            specs.append((logical_key, role, section_id, slot_id))
        tasks = [
            self._envelope(run_dir, state, key, role, section_id=section, slot_id=slot)
            for key, role, section, slot in specs
        ]
        self._assert_disjoint_write_sets(tasks)
        self.kernel.contracts.validate("production-state", state)
        if persist:
            write_json_atomic(self._state_path(run_dir), state)
        blocked_sections = [
            {
                "section_id": section_id,
                "blocked_evidence": section["blocked_evidence"],
            }
            for section_id, section in state["sections"].items()
            if section["status"] == "blocked"
        ]
        classification = "production_tasks_runnable" if specs else "production_complete"
        if blocked_sections and not specs:
            classification = "production_blocked"
        return {
            "classification": classification,
            "run_id": state["run_id"],
            "runnable_tasks": tasks,
            "checkpoints": state["checkpoints"],
            "blocked_sections": blocked_sections,
        }

    @classmethod
    def _invalidate_for_supersede(cls, state: dict[str, Any], logical_key: str) -> None:
        if not logical_key.startswith(("writer-section-", "figure-")):
            return
        section_id: str | None = None
        superseded_figure_slot: dict[str, Any] | None = None
        if logical_key.startswith("writer-section-"):
            section_id = "section_" + logical_key.rsplit("-", 1)[-1]
        else:
            binding = cls._figure_task_bindings(state).get(logical_key)
            if binding is not None:
                section_id, superseded_figure_slot = binding
        if section_id is None:
            return
        dependent_tasks = {
            f"pyramid-section-{section_id.replace('_', '-')}",
            "pyramid-main",
        }
        incremental_slots = [
            slot
            for slot in state["sections"][section_id]["figure_slots"]
            if slot["wave"] == "incremental"
        ]
        if logical_key.startswith("writer-section-"):
            dependent_tasks.update(
                f"figure-incremental-{slot['slot_id'].replace('_', '-')}"
                for slot in incremental_slots
            )
        state["completed_tasks"] = [key for key in state["completed_tasks"] if key not in dependent_tasks]
        for key in dependent_tasks:
            state["receipts"].pop(key, None)
            claim = state["claims"].get(key)
            if claim is not None and key.startswith("figure-incremental-"):
                generation = claim["claim_generation"] + 1
                state["claims"][key] = {
                    "task_id": claim["task_id"],
                    "claim_generation": generation,
                    "claim_token": _digest(state["run_id"], key, str(generation), "claim"),
                    "status": "available",
                }
        state["sections"][section_id]["status"] = "active"
        state["sections"][section_id]["blocked_evidence"] = []
        if logical_key.startswith("writer-section-") and incremental_slots:
            state["sections"][section_id]["incremental_wave_status"] = "admitted"
        stale_artifacts = {
            f"integrated_{section_id}", f"pyramid_{section_id}_report",
            "integrated_main", "integration_manifest", "local_class", "local_style",
            "bibliography", "pyramid_main_report", "compile_manifest",
            "diagnostic_compile_report", "diagnostic_pdf",
        }
        if logical_key.startswith("writer-section-"):
            stale_artifacts.update({f"writer_{section_id}", f"writer_result_{section_id}"})
            for slot in incremental_slots:
                stale_artifacts.update(
                    {
                        f"figure_asset_{slot['slot_id']}",
                        f"figure_manifest_{slot['slot_id']}",
                        f"figure_contribution_{slot['slot_id']}",
                    }
                )
        elif superseded_figure_slot is not None:
            slot_id = superseded_figure_slot["slot_id"]
            stale_artifacts.update(
                {
                    f"figure_asset_{slot_id}",
                    f"figure_manifest_{slot_id}",
                    f"figure_contribution_{slot_id}",
                }
            )
        for artifact in stale_artifacts:
            state["artifacts"].pop(artifact, None)
        state["checkpoints"]["draft_compile_ready"] = "pending"

    def plan(
        self,
        run_dir: Path,
        *,
        supersede_task_id: str | None = None,
        expected_claim_generation: int | None = None,
    ) -> dict[str, Any]:
        run_dir = run_dir.resolve()
        with self._exclusive_run_lock(run_dir):
            state = self._load_or_create_state(run_dir)
            return self._plan_locked(
                run_dir,
                state,
                supersede_task_id=supersede_task_id,
                expected_claim_generation=expected_claim_generation,
            )

    @staticmethod
    def _normal_path(value: str) -> tuple[str, ...]:
        if not isinstance(value, str) or not value or ".." in Path(value).parts:
            raise ContractError("write set path is not canonical")
        return tuple(part.casefold() for part in value.replace("\\", "/").split("/") if part)

    @classmethod
    def _assert_disjoint_write_sets(cls, tasks: list[dict[str, Any]]) -> None:
        owned: list[tuple[tuple[str, ...], str]] = []
        for task in tasks:
            for raw in task["write_set"]:
                path = cls._normal_path(raw)
                for other, owner in owned:
                    if path[:len(other)] == other or other[:len(path)] == path:
                        raise ContractError(f"overlapping production write sets: {owner} and {task['logical_task_key']}")
                owned.append((path, task["logical_task_key"]))

    def _validate_outline(self, value: dict[str, Any]) -> None:
        self.kernel.contracts.validate("outline-contract", value)
        if value.get("schema_name") != "outline-contract" or value.get("schema_version") not in {"1.0.0", "2.0.0"}:
            raise ContractError("Outline Contract identity is invalid")
        sections = value.get("sections")
        if not isinstance(sections, list) or not sections:
            raise ContractError("Outline Contract requires sections")
        section_ids = [item.get("section_id") for item in sections]
        if len(section_ids) != len(set(section_ids)):
            raise ContractError("Outline Contract section identity must be unique")
        slots = value.get("required_figure_slots")
        slot_ids = [item.get("slot_id") for item in slots or []]
        markers = [item.get("placement_marker") for item in slots or []]
        if not slots or len(slot_ids) != len(set(slot_ids)) or len(markers) != len(set(markers)):
            raise ContractError("Outline Contract figure identity must be unique")
        if any(item.get("section_id") not in section_ids for item in slots):
            raise ContractError("Outline Contract figure slot owns an unknown section")
        if any(item.get("placement_marker") != f"% FIGURE_SLOT:{item.get('slot_id')}" for item in slots):
            raise ContractError("Outline Contract figure marker identity is invalid")
        if not isinstance(value.get("terminology"), list) or not value["terminology"]:
            raise ContractError("Outline Contract must freeze terminology")
        support = value.get("compile_support")
        required = {
            "document_class", "class_content", "style_name", "style_content",
            "bibliography_name", "bibliography_content",
        }
        if not isinstance(support, dict) or not required.issubset(support):
            raise ContractError("Outline Contract compile support is incomplete")

    def _validate_pyramid(
        self, envelope: dict[str, Any], value: dict[str, Any]
    ) -> None:
        self.kernel.contracts.validate("pyramid-evaluation-binding", value)
        if value.get("schema_name") != "pyramid-evaluation-binding" or value.get("status") != "pass":
            raise ContractError("Pyramid Evaluation did not pass")
        if value.get("target") != envelope.get("pyramid_target"):
            raise ContractError("Pyramid Evaluation Target is stale")
        if value.get("evaluation_context") != envelope.get("evaluation_context"):
            raise ContractError("Pyramid evaluation context is stale")

    @staticmethod
    def _figure_contribution(manifest: dict[str, Any]) -> bytes:
        caption = manifest["caption"]
        source = manifest["source"]
        return (
            "\\begin{figure}\n"
            "\\centering\n"
            f"\\includegraphics{{figures/{manifest['slot_id']}}}\n"
            f"\\caption{{{caption}}}\n"
            f"\\par\\small Source ({source['kind']}): {source['value']}\n"
            "\\end{figure}\n"
        ).encode("utf-8")

    def _validate_attempt(
        self, run_dir: Path, envelope: dict[str, Any], attempt_id: str
    ) -> dict[str, Path]:
        if not _is_lower_hex(attempt_id, 24):
            raise ContractError("Production Task Attempt identity is invalid")
        task_dir = run_dir / "workflow/tasks" / envelope["task_id"]
        attempt_dir = task_dir / "attempts" / attempt_id
        record_path = attempt_dir / "attempt.json"
        require_contained_path(
            attempt_dir,
            task_dir,
            purpose="Production Task Attempt directory",
            error_type=ContractError,
            leaf_kind="directory",
        )
        require_contained_path(
            record_path,
            attempt_dir,
            purpose="Production Task Attempt record",
            error_type=ContractError,
            leaf_kind="file",
            require_single_link=True,
        )
        record = read_json(record_path)
        self.kernel.contracts.validate("production-task-attempt", record)
        if record.get("claim_generation") != envelope["claim_generation"] or record.get("claim_token") != envelope["claim_token"]:
            raise SupersededProductionAttempt("Production Task Attempt binding is invalid because its claim was superseded")
        if (
            record.get("schema_name") != "production-task-attempt"
            or record.get("task_id") != envelope["task_id"]
            or record.get("attempt_id") != attempt_id
        ):
            raise ContractError("Production Task Attempt binding is invalid")
        if record.get("envelope_sha256") != sha256_file(task_dir / "envelope.json"):
            raise ContractError("Production Task Attempt envelope binding is stale")
        declared = {item["path"]: item["sha256"] for item in record.get("outputs", [])}
        if set(declared) != set(envelope["required_outputs"]):
            raise ContractError("Production Task Attempt output set is incomplete")
        actual_files = {
            path.relative_to(attempt_dir).as_posix()
            for path in attempt_dir.rglob("*")
            if path.is_file() and path.name != "attempt.json"
        }
        if actual_files != set(declared):
            raise ContractError("Production Task Attempt contains undeclared files")
        outputs: dict[str, Path] = {}
        for relative, expected in declared.items():
            path = attempt_dir / relative
            require_contained_path(
                path,
                attempt_dir,
                purpose="Production Task Attempt output",
                error_type=ContractError,
                leaf_kind="file",
                require_single_link=True,
            )
            if sha256_file(path) != expected:
                raise ContractError("Production Task Attempt output fingerprint is stale")
            outputs[relative] = path
        return outputs

    @staticmethod
    def _publish(source: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.production-new")
        shutil.copyfile(source, temporary)
        temporary.replace(target)

    def _promote_outputs(
        self,
        run_dir: Path,
        task_id: str,
        attempt_id: str,
        mappings: list[tuple[Path, str]],
        *,
        fault_point: str | None,
    ) -> None:
        if fault_point is not None and fault_point not in PRODUCTION_FAULT_POINTS:
            raise ContractError("unknown Content Production fault point")
        intent_id = _digest(task_id, attempt_id, "production-promotion")
        intent_root = run_dir / "待删除/production-promotions" / intent_id
        journal_path = intent_root / "journal.json"
        if journal_path.exists():
            journal = read_json(journal_path)
            self.kernel.contracts.validate("production-promotion-journal", journal)
        else:
            staged_root = intent_root / "new"
            staged_root.mkdir(parents=True, exist_ok=False)
            entries = []
            for index, (source, relative) in enumerate(mappings):
                staged = staged_root / f"{index:04d}"
                shutil.copyfile(source, staged)
                entries.append(
                    {
                        "source_sha256": sha256_file(source),
                        "staged_path": staged.relative_to(run_dir).as_posix(),
                        "target_path": relative,
                    }
                )
            journal = {
                "schema_name": "production-promotion-journal",
                "schema_version": "1.0.0",
                "kernel_version": "2.0.0",
                "intent_id": intent_id,
                "task_id": task_id,
                "attempt_id": attempt_id,
                "state": "prepared",
                "entries": entries,
            }
            self.kernel.contracts.validate("production-promotion-journal", journal)
            write_json_atomic(journal_path, journal)
        if journal["task_id"] != task_id or journal["attempt_id"] != attempt_id:
            raise KernelConflict("Content Production promotion identity changed")
        _inject(fault_point, "after_promotion_prepared")
        for index, entry in enumerate(journal["entries"]):
            staged = run_dir / entry["staged_path"]
            target = run_dir / entry["target_path"]
            if not staged.is_file() or sha256_file(staged) != entry["source_sha256"]:
                raise KernelConflict("Content Production staged promotion drifted")
            if not target.is_file() or sha256_file(target) != entry["source_sha256"]:
                self._publish(staged, target)
            if index == 0:
                _inject(fault_point, "after_first_output")
        if journal["state"] != "committed":
            journal["state"] = "committed"
            self.kernel.contracts.validate("production-promotion-journal", journal)
            write_json_atomic(journal_path, journal)
        _inject(fault_point, "after_promotion_committed")

    @staticmethod
    def _section_ready(state: dict[str, Any], section_id: str) -> bool:
        section = state["sections"][section_id]
        writer_key = f"writer-{section_id.replace('_', '-')}"
        if writer_key not in state["completed_tasks"]:
            return False
        for slot in section["figure_slots"]:
            prefix = "incremental" if slot["wave"] == "incremental" else "required"
            if f"figure-{prefix}-{slot['slot_id'].replace('_', '-')}" not in state["completed_tasks"]:
                return False
        return True

    @staticmethod
    def _admit_candidates(
        state: dict[str, Any], section_id: str, candidates: list[dict[str, Any]]
    ) -> None:
        section = state["sections"][section_id]
        if not candidates:
            if section["incremental_wave_status"] == "not_requested":
                section["incremental_wave_status"] = "no_candidates"
            return
        if section["incremental_wave_status"] != "not_requested":
            raise ContractError(f"incremental figure wave budget exhausted for {section_id}")
        ordered = sorted(candidates, key=lambda item: item["candidate_id"])
        seen: set[tuple[str, str]] = set()
        for candidate in ordered:
            if candidate["section_id"] != section_id:
                raise ContractError("Writer Result candidate crosses section ownership")
            identity = (candidate["candidate_id"], candidate["placement_marker"])
            if identity in seen:
                raise ContractError("Writer Result contains a duplicate figure candidate")
            seen.add(identity)
        required_overflow = [
            candidate for candidate in ordered[1:] if candidate["priority"] == "required"
        ]
        if required_overflow:
            section["status"] = "blocked"
            section["blocked_evidence"] = [
                {
                    "candidate_id": candidate["candidate_id"],
                    "priority": "required",
                    "reason": "per-section incremental figure budget exhausted",
                }
                for candidate in required_overflow
            ]
            raise ContractError(f"required incremental figure budget exceeded for {section_id}")
        admitted = ordered[0]
        slot_id = f"figure_{section_id.split('_')[-1]}_incremental_01"
        expected_marker = f"% FIGURE_SLOT:{slot_id}"
        if admitted["placement_marker"] != expected_marker:
            raise ContractError("Writer Result candidate placement marker is not kernel-stable")
        section["figure_slots"].append(
            {
                "slot_id": slot_id,
                "section_id": section_id,
                "teaching_purpose": admitted["teaching_purpose"],
                "placement_marker": expected_marker,
                "wave": "incremental",
                "candidate_id": admitted["candidate_id"],
            }
        )
        section["incremental_wave_status"] = "admitted"
        section["incremental_wave_count"] = 1
        for candidate in ordered[1:]:
            section["rejected_candidates"].append(
                {"candidate_id": candidate["candidate_id"], "reason": "per-section budget exhausted"}
            )

    def _integrate_section(self, run_dir: Path, state: dict[str, Any], section_id: str) -> None:
        logical_id = f"integrated_{section_id}"
        if logical_id in state["artifacts"]:
            return
        section = state["sections"][section_id]
        writer = (run_dir / f"work/writers/{section_id}.tex").read_text(encoding="utf-8")
        for slot in section["figure_slots"]:
            slot_id = slot["slot_id"]
            snippet = (run_dir / f"work/figures/{slot_id}.tex").read_text(encoding="utf-8")
            marker = slot["placement_marker"]
            if writer.count(marker) != 1:
                raise ContractError(f"Writer section must contain exactly one {slot_id} slot marker")
            writer = writer.replace(marker, snippet.rstrip())
        target = run_dir / f"work/integration/{section_id}.tex"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(writer, encoding="utf-8")
        self._record_artifact(
            state, logical_id, f"work/integration/{section_id}.tex",
            target, "kernel:section-integration",
        )
        state["sections"][section_id]["status"] = "integrated"

    def _integrate_main(self, run_dir: Path, state: dict[str, Any]) -> None:
        if "integrated_main" in state["artifacts"]:
            return
        outline = read_json(run_dir / "work/outline/outline.json")
        support = outline["compile_support"]
        support_specs = (
            (
                "local_class", f"work/integration/{support['document_class']}.cls",
                support["class_content"],
            ),
            (
                "local_style", f"work/integration/{support['style_name']}.sty",
                support["style_content"],
            ),
            (
                "bibliography", f"work/integration/{support['bibliography_name']}",
                support["bibliography_content"],
            ),
        )
        for logical_id, relative, content in support_specs:
            path = run_dir / relative
            path.write_text(content, encoding="utf-8")
            self._record_artifact(state, logical_id, relative, path, "kernel:main-integration")
        main = run_dir / "work/integration/main.tex"
        main.write_text(
            f"\\documentclass{{{support['document_class']}}}\n"
            f"\\usepackage{{{support['style_name']}}}\n"
            "\\usepackage{graphicx}\n\\usepackage{fontspec}\n\\setmainfont{Arial}\n"
            "\\begin{document}\n"
            + "".join(f"\\input{{{section_id}.tex}}\n" for section_id in state["sections"])
            + f"\\bibliography{{{Path(support['bibliography_name']).stem}}}\n"
            +
            "\\end{document}\n",
            encoding="utf-8",
        )
        main_generation = self._record_artifact(
            state, "integrated_main", "work/integration/main.tex",
            main, "kernel:main-integration",
        )
        manifest = {
            "schema_name": "integration-manifest",
            "schema_version": "2.0.0",
            "kernel_version": "2.0.0",
            "run_id": state["run_id"],
            "main": {"logical_id": "integrated_main", **main_generation},
            "sections": [
                {"logical_id": f"integrated_{section_id}", **self._artifact(state, f"integrated_{section_id}")}
                for section_id in state["sections"]
            ],
            "figures": [
                {"logical_id": logical_id, **self._artifact(state, logical_id)}
                for section in state["sections"].values()
                for slot in section["figure_slots"]
                for logical_id in (
                    f"figure_asset_{slot['slot_id']}",
                    f"figure_manifest_{slot['slot_id']}",
                    f"figure_contribution_{slot['slot_id']}",
                )
            ],
            "terminology": outline["terminology"],
            "source_binding": state["source_binding"],
        }
        self.kernel.contracts.validate("integration-manifest", manifest)
        manifest_path = run_dir / "workflow/integration-manifest.json"
        write_json_atomic(manifest_path, manifest)
        self._record_artifact(
            state, "integration_manifest", "workflow/integration-manifest.json",
            manifest_path, "kernel:main-integration",
        )

    def _compile(
        self, run_dir: Path, state: dict[str, Any], runtime_policy: dict[str, Any]
    ) -> dict[str, Any]:
        policy_path = run_dir / "workflow/compile-runtime-policy.json"
        self.kernel.contracts.validate("compile-runtime-policy", runtime_policy)
        write_json_atomic(policy_path, runtime_policy)
        entries: list[dict[str, Any]] = []
        specs: list[tuple[str, str | None, str, str]] = [
            ("integrated_main", "main.tex", "entry_tex", "application/x-tex"),
            *[(f"integrated_{section_id}", f"{section_id}.tex", "section_tex", "application/x-tex") for section_id in state["sections"]],
            *[(f"figure_asset_{slot['slot_id']}", f"figures/{slot['slot_id']}.png", "figure", "image/png") for section in state["sections"].values() for slot in section["figure_slots"]],
            ("local_class", None, "local_class", "application/x-tex"),
            ("local_style", None, "local_style", "application/x-tex"),
            ("bibliography", None, "bibliography", "application/x-bibtex"),
        ]
        for logical_id, staging_path, role, media_type in specs:
            artifact = self._artifact(state, logical_id)
            if staging_path is None:
                staging_path = Path(artifact["path"]).name
            entries.append(
                {
                    "logical_id": logical_id,
                    "generation": artifact["generation"],
                    "sha256": artifact["sha256"],
                    "size": artifact["size"],
                    "producer": artifact["producer"],
                    "source_path": artifact["path"],
                    "staging_path": staging_path,
                    "role": role,
                    "media_type": media_type,
                    "required": True,
                }
            )
        integration = self._artifact(state, "integration_manifest")
        manifest = {
            "schema_name": "compile-manifest",
            "schema_version": "1.0.0",
            "kernel_version": "2.0.0",
            "run_id": state["run_id"],
            "mode": "diagnostic",
            "delivery_authority": False,
            "integration_manifest_generation": {
                "generation": integration["generation"],
                "sha256": integration["sha256"],
            },
            "runtime_policy_sha256": runtime_policy["policy_sha256"],
            "dependency_discovery_policy_version": runtime_policy[
                "dependency_discovery_policy_version"
            ],
            "entries": entries,
        }
        self.kernel.contracts.validate("compile-manifest", manifest)
        manifest_path = run_dir / "workflow/compile-manifest.json"
        write_json_atomic(manifest_path, manifest)
        self._record_artifact(
            state, "compile_manifest", "workflow/compile-manifest.json",
            manifest_path, "kernel:compile-plan",
        )
        result = GuardedCompileProvider(run_dir).compile(manifest_path, runtime_policy)
        self.kernel.contracts.validate("diagnostic-compile-report", result["report"])
        self._record_artifact(
            state, "diagnostic_compile_report",
            "review/latex/diagnostic-compile-report.json",
            result["report_path"], "provider:guarded-compile",
        )
        self._record_artifact(
            state,
            "diagnostic_pdf",
            "待删除/diagnostic-compile/main.pdf",
            result["pdf_path"],
            "provider:guarded-compile",
        )
        state["checkpoints"]["draft_compile_ready"] = "current"
        return {
            "classification": "diagnostic_compile_ready",
            "compile_report_path": str(result["report_path"]),
            "diagnostic_pdf_path": str(result["pdf_path"]),
        }

    def advance(
        self,
        run_dir: Path,
        task_id: str,
        attempt_id: str,
        *,
        compile_runtime_policy: dict[str, Any] | None = None,
        fault_point: str | None = None,
    ) -> dict[str, Any]:
        run_dir = run_dir.resolve()
        with self._exclusive_run_lock(run_dir):
            return self._advance_locked(
                run_dir,
                task_id,
                attempt_id,
                compile_runtime_policy=compile_runtime_policy,
                fault_point=fault_point,
            )

    def _advance_locked(
        self,
        run_dir: Path,
        task_id: str,
        attempt_id: str,
        *,
        compile_runtime_policy: dict[str, Any] | None = None,
        fault_point: str | None = None,
    ) -> dict[str, Any]:
        state = self._load_or_create_state(run_dir)
        for receipt in state["receipts"].values():
            if receipt["task_id"] != task_id:
                continue
            if receipt["attempt_id"] == attempt_id:
                return receipt["result"]
            raise KernelConflict("Production task was committed by another fenced Attempt")
        runnable = self._plan_locked(run_dir, state)["runnable_tasks"]
        envelope = next((item for item in runnable if item["task_id"] == task_id), None)
        if envelope is None:
            raise KernelConflict("Production task is not currently runnable")
        role = envelope["role"]
        logical_key = envelope["logical_task_key"]
        section_id = envelope.get("section_id")
        slot_id = envelope.get("slot_id")
        if role == "pyramid_main" and compile_runtime_policy is None:
            raise ContractError("Main Pyramid completion requires a Compile Runtime Policy")
        outputs = self._validate_attempt(run_dir, envelope, attempt_id)
        prepared_sections: dict[str, Any] | None = None
        if role == "writer":
            result = json.loads(outputs["writer-result.json"].read_text(encoding="utf-8"))
            self.kernel.contracts.validate("writer-result", result)
            if result.get("section_id") != section_id:
                raise ContractError("Writer Result section binding is invalid")
            candidate_state = deepcopy(state)
            try:
                self._admit_candidates(
                    candidate_state, section_id, result.get("new_figure_candidates", [])
                )
            except ContractError:
                if candidate_state["sections"][section_id]["status"] == "blocked":
                    state["sections"] = candidate_state["sections"]
                    self.kernel.contracts.validate("production-state", state)
                    write_json_atomic(self._state_path(run_dir), state)
                raise
            prepared_sections = candidate_state["sections"]
        claim = state["claims"][logical_key]
        if (
            claim["claim_generation"] != envelope["claim_generation"]
            or claim["claim_token"] != envelope["claim_token"]
        ):
            raise KernelConflict("Production task Attempt uses a superseded claim")
        active_attempt = claim.get("active_attempt_id")
        if active_attempt is not None and active_attempt != attempt_id:
            raise KernelConflict("Production task is owned by another fenced Attempt")
        if active_attempt is None:
            claim["active_attempt_id"] = attempt_id
            claim["status"] = "active"
            self.kernel.contracts.validate("production-state", state)
            write_json_atomic(self._state_path(run_dir), state)
        if role == "outline":
            value = json.loads(outputs["outline.json"].read_text(encoding="utf-8"))
            self._validate_outline(value)
            target = run_dir / "work/outline/outline.json"
            self._promote_outputs(
                run_dir, task_id, attempt_id,
                [(outputs["outline.json"], "work/outline/outline.json")],
                fault_point=fault_point,
            )
            self._record_artifact(
                state, "outline_contract", "work/outline/outline.json",
                target, f"task:{task_id}:{attempt_id}",
            )
            state["sections"] = {
                section["section_id"]: {
                    "title": section["title"],
                    "figure_slots": [
                        {**slot, "wave": "required"}
                        for slot in value["required_figure_slots"]
                        if slot["section_id"] == section["section_id"]
                    ],
                    "incremental_wave_status": "not_requested",
                    "incremental_wave_count": 0,
                    "rejected_candidates": [],
                    "blocked_evidence": [],
                    "status": "active",
                }
                for section in value["sections"]
            }
        elif role.startswith("pyramid_"):
            value = json.loads(outputs["pyramid-report.json"].read_text(encoding="utf-8"))
            self._validate_pyramid(envelope, value)
            relative = envelope["write_set"][0]
            target = run_dir / relative
            self._promote_outputs(
                run_dir, task_id, attempt_id,
                [(outputs["pyramid-report.json"], relative)],
                fault_point=fault_point,
            )
            pyramid_logical_id = (
                f"pyramid_{section_id}_report" if role == "pyramid_section" else f"{role}_report"
            )
            self._record_artifact(
                state, pyramid_logical_id, relative, target,
                f"provider:{task_id}:{attempt_id}",
            )
        elif role == "writer":
            assert prepared_sections is not None
            state["sections"] = prepared_sections
            writer_specs = (
                (f"{section_id}.tex", f"writer_{section_id}"),
                ("writer-result.json", f"writer_result_{section_id}"),
            )
            self._promote_outputs(
                run_dir,
                task_id,
                attempt_id,
                [(outputs[name], f"work/writers/{section_id}.result.json" if name == "writer-result.json" else f"work/writers/{name}") for name, _ in writer_specs],
                fault_point=fault_point,
            )
            for name, logical_id in writer_specs:
                relative = f"work/writers/{section_id}.result.json" if name == "writer-result.json" else f"work/writers/{name}"
                target = run_dir / relative
                self._record_artifact(
                    state, logical_id, relative, target,
                    f"task:{task_id}:{attempt_id}",
                )
        elif role == "figure":
            manifest = json.loads(outputs["figure-manifest.json"].read_text(encoding="utf-8"))
            self.kernel.contracts.validate("figure-manifest", manifest)
            if manifest.get("slot_id") != slot_id or manifest.get("section_id") != section_id:
                raise ContractError("Figure Manifest slot binding is invalid")
            if manifest.get("asset_sha256") != sha256_file(outputs[f"{slot_id}.png"]):
                raise ContractError("Figure Manifest asset fingerprint is stale")
            if not manifest.get("caption") or not isinstance(manifest.get("source"), dict):
                raise ContractError("Figure Manifest caption or source is missing")
            contribution = outputs[f"{slot_id}.tex"].read_bytes()
            if manifest.get("slot_contribution_sha256") != hashlib.sha256(
                contribution
            ).hexdigest():
                raise ContractError("Figure Manifest contribution fingerprint is stale")
            if contribution != self._figure_contribution(manifest):
                raise ContractError("Figure contribution differs from its Manifest")
            mappings = (
                (f"{slot_id}.png", f"figures/{slot_id}.png", f"figure_asset_{slot_id}"),
                (
                    "figure-manifest.json", envelope["write_set"][1],
                    f"figure_manifest_{slot_id}",
                ),
                (f"{slot_id}.tex", f"work/figures/{slot_id}.tex", f"figure_contribution_{slot_id}"),
            )
            self._promote_outputs(
                run_dir,
                task_id,
                attempt_id,
                [(outputs[name], relative) for name, relative, _ in mappings],
                fault_point=fault_point,
            )
            for name, relative, logical_id in mappings:
                target = run_dir / relative
                self._record_artifact(
                    state, logical_id, relative, target,
                    f"task:{task_id}:{attempt_id}",
                )
            if any(
                slot["slot_id"] == slot_id and slot["wave"] == "incremental"
                for slot in state["sections"][section_id]["figure_slots"]
            ):
                state["sections"][section_id]["incremental_wave_status"] = "complete"
        else:
            raise ContractError(f"unsupported Production role: {role}")

        state["completed_tasks"].append(logical_key)
        if role not in state["completed_roles"]:
            state["completed_roles"].append(role)
        state["promotion_sequence"].append(logical_key)
        if section_id and role in {"writer", "figure"} and self._section_ready(state, section_id):
            self._integrate_section(run_dir, state, section_id)
        if role == "pyramid_section" and all(
            f"pyramid-section-{item.replace('_', '-')}" in state["completed_tasks"] or item == section_id
            for item in state["sections"]
        ):
            self._integrate_main(run_dir, state)
        result = {"classification": "production_advanced", "completed_role": role}
        result["promotion_sequence"] = len(state["promotion_sequence"])
        if role == "pyramid_main":
            assert compile_runtime_policy is not None
            result = self._compile(run_dir, state, compile_runtime_policy)
        claim["status"] = "committed"
        next_plan = self._plan_locked(run_dir, state, persist=False)
        result["next_classification"] = next_plan["classification"]
        result["runnable_tasks"] = next_plan["runnable_tasks"]
        result["checkpoints"] = next_plan["checkpoints"]
        _inject(fault_point, "before_receipt_committed")
        state["receipts"][logical_key] = {
            "task_id": task_id,
            "attempt_id": attempt_id,
            "claim_generation": envelope["claim_generation"],
            "result": result,
        }
        self.kernel.contracts.validate("production-state", state)
        write_json_atomic(self._state_path(run_dir), state)
        _inject(fault_point, "after_state_committed")
        return result
