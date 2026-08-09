from __future__ import annotations

from contextlib import contextmanager
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time
from typing import Any, Callable, Iterator, TypeVar

from .contracts import ContractRegistry
from .control_store import ControlStore
from .errors import (
    ArtifactDrift,
    CliUsageError,
    ContractError,
    DeliveryLifecycleFault,
    KernelConflict,
)
from .guarded_delivery import (
    validate_acceptance_report,
    validate_delivery_guard_report,
)
from .utils import (
    canonical_json_bytes,
    normalized_physical_path,
    read_json,
    require_safe_path_segment,
    sha256_file,
    write_json_atomic,
)


_T = TypeVar("_T")


LEGAL_TRANSITIONS = frozenset(
    {
        ("generating", "ready_for_delivery"),
        ("generating", "blocked"),
        ("ready_for_delivery", "accepted"),
        ("ready_for_delivery", "generating"),
        ("ready_for_delivery", "blocked"),
        ("accepted", "delivered"),
        ("accepted", "generating"),
        ("accepted", "blocked"),
    }
)
FAULT_POINTS = frozenset(
    {
        "after_intent_prepared",
        "after_video_target_write",
        "after_task_index_write",
        "after_run_record_commit",
        "after_control_store_commit",
    }
)


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _reject(message: str, gate: str, code: str) -> None:
    raise ContractError(
        message,
        data={"first_failing_gate": gate, "error_code": code},
    )


@contextmanager
def _exclusive_delivery_lifecycle_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            deadline = time.monotonic() + 30.0
            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise KernelConflict(
                            "Delivery lifecycle Run lock timed out",
                            data={
                                "first_failing_gate": "lifecycle_fencing",
                                "error_code": "delivery_run_lock_timeout",
                            },
                        )
                    time.sleep(0.05)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _run_locked(method: Callable[..., _T]) -> Callable[..., _T]:
    """Serialize delivery writers across the shared index and one Run."""

    @wraps(method)
    def guarded(self: "DeliveryLifecycleProvider", *args: Any, **kwargs: Any) -> _T:
        run_dir = Path(kwargs["run_dir"]).resolve()
        self._load_run(run_dir)
        with _exclusive_delivery_lifecycle_lock(
            run_dir.parent
            / ".workflow-control"
            / "initial-delivery-task-index.lock"
        ):
            with _exclusive_delivery_lifecycle_lock(
                run_dir / "workflow" / ".delivery-lifecycle.lock"
            ):
                kwargs["run_dir"] = run_dir
                return method(self, *args, **kwargs)

    return guarded


class DeliveryLifecycleProvider:
    """Commits delivery stage and ownership through one fenced projection saga."""

    def __init__(self, repository_root: Path) -> None:
        self.repository_root = repository_root.resolve()
        self.contracts = ContractRegistry(self.repository_root)

    @staticmethod
    def _project_root(run_dir: Path) -> Path:
        workspace_root = run_dir.parent
        if workspace_root.name != "workspace":
            _reject(
                "Kernel delivery Run must be a direct child of workspace",
                "path_boundary",
                "delivery_run_layout_invalid",
            )
        return workspace_root.parent

    def _load_run(self, run_dir: Path) -> tuple[Path, dict[str, Any]]:
        root = run_dir.resolve()
        run_path = root / "workflow" / "run.json"
        if not run_path.is_file():
            _reject(
                "Delivery lifecycle Run Record is unavailable",
                "run_record",
                "delivery_run_record_absent",
            )
        value = read_json(run_path)
        if value.get("schema_version") != "4.0.0":
            _reject(
                "Delivery lifecycle requires Run Record v4",
                "run_record",
                "run_migration_required",
            )
        self.contracts.validate("run-record", value)
        if (
            value.get("canonical_platform") != "bilibili"
            or Path(value.get("output_path", "")).resolve() != root
        ):
            _reject(
                "Delivery lifecycle Run identity is invalid",
                "run_record",
                "delivery_run_identity_invalid",
            )
        return run_path, value

    @staticmethod
    def _validate_binding(
        binding: dict[str, Any], *, label: str, run_dir: Path | None = None
    ) -> Path:
        raw_path = Path(binding["path"])
        path = (
            (run_dir / raw_path).resolve()
            if run_dir is not None and not raw_path.is_absolute()
            else raw_path.resolve()
        )
        if not path.is_file() or sha256_file(path) != binding["sha256"]:
            raise ArtifactDrift(
                f"{label} projection is absent or stale",
                data={
                    "first_failing_gate": "projection_currentness",
                    "error_code": "delivery_projection_stale",
                },
            )
        return path

    @staticmethod
    def _validate_task_index_binding(
        binding: dict[str, Any],
        *,
        run_record: dict[str, Any],
        video_target: dict[str, Any],
        session_target: dict[str, Any],
    ) -> Path:
        path = Path(binding["path"]).resolve()
        if not path.is_file():
            raise ArtifactDrift(
                "task index projection is absent or stale",
                data={
                    "first_failing_gate": "projection_currentness",
                    "error_code": "delivery_projection_stale",
                },
            )
        if sha256_file(path) == binding["sha256"]:
            return path
        value = read_json(path)
        entries = [
            item
            for item in value.get("entries", [])
            if item.get("run_id") == run_record["run_id"]
        ]
        delivery = run_record["delivery"]
        ownership = delivery["ownership"]
        projections = delivery["projections"]
        snapshot_revision = video_target.get("run_revision")
        entry_video = entries[0].get("video_target") if len(entries) == 1 else None
        entry_session = entries[0].get("session_target") if len(entries) == 1 else None
        video_binding = projections["video_target"]
        video_binding_path = Path(video_binding["path"])
        expected_video_path = (
            Path(video_target["video_output_dir"]) / video_binding_path
            if not video_binding_path.is_absolute()
            else video_binding_path
        ).resolve()
        if len(entries) != 1 or any(
            (
                not isinstance(snapshot_revision, int),
                snapshot_revision > run_record["coordination_revision"],
                entries[0].get("run_revision") != snapshot_revision,
                session_target.get("run_revision") != snapshot_revision,
                entries[0].get("stage") != delivery["stage"],
                video_target.get("stage") != delivery["stage"],
                session_target.get("stage") != delivery["stage"],
                entries[0].get("session_id") != ownership["session_id"],
                entries[0].get("ownership_generation")
                != ownership["generation"],
                video_target.get("ownership") != ownership,
                session_target.get("session_id") != ownership["session_id"],
                session_target.get("ownership_generation")
                != ownership["generation"],
                entries[0].get("lifecycle_intent_id")
                != video_target.get("lifecycle_intent_id"),
                session_target.get("lifecycle_intent_id")
                != video_target.get("lifecycle_intent_id"),
                not isinstance(entry_video, dict),
                entry_video is not None
                and (
                    Path(entry_video.get("path", "")).resolve()
                    != expected_video_path
                    or entry_video.get("projection_revision")
                    != video_binding["projection_revision"]
                    or entry_video.get("sha256") != video_binding["sha256"]
                ),
                entry_session != projections["session_target"],
            )
        ):
            raise ArtifactDrift(
                "task index projection is absent or stale",
                data={
                    "first_failing_gate": "projection_currentness",
                    "error_code": "delivery_projection_stale",
                },
            )
        return path

    def _validated_evidence(
        self,
        evidence_path: Path,
        *,
        run_id: str,
        from_stage: str,
        to_stage: str,
        expected_run_revision: int,
    ) -> dict[str, Any]:
        if not evidence_path.is_file():
            _reject(
                "Delivery transition evidence is unavailable",
                "transition_evidence",
                "delivery_transition_evidence_absent",
            )
        value = read_json(evidence_path)
        if (
            value.get("schema_name") != "delivery-transition-evidence"
            or value.get("schema_version") != "1.0.0"
            or value.get("run_id") != run_id
            or value.get("from_stage") != from_stage
            or value.get("to_stage") != to_stage
        ):
            _reject(
                "Delivery transition evidence identity is invalid",
                "transition_evidence",
                "delivery_transition_evidence_invalid",
            )
        artifacts = value.get("artifacts")
        if not isinstance(artifacts, dict):
            _reject(
                "Delivery transition artifact bindings are invalid",
                "transition_evidence",
                "delivery_transition_evidence_invalid",
            )
        for role, binding in artifacts.items():
            if not isinstance(binding, dict) or set(binding) != {"path", "sha256"}:
                _reject(
                    f"Delivery transition {role} binding is invalid",
                    "transition_evidence",
                    "delivery_transition_evidence_invalid",
                )
            path = Path(binding["path"]).resolve()
            if not path.is_file() or sha256_file(path) != binding["sha256"]:
                raise ArtifactDrift(
                    f"Delivery transition {role} binding is stale",
                    data={
                        "first_failing_gate": "transition_evidence",
                        "error_code": "delivery_transition_evidence_stale",
                    },
                )
        if to_stage == "accepted":
            acceptance_binding = artifacts.get("acceptance_report")
            if not isinstance(acceptance_binding, dict):
                _reject(
                    "Acceptance transition lacks Acceptance Report v2",
                    "acceptance_decision",
                    "acceptance_report_v2_absent",
                )
            try:
                validate_acceptance_report(
                    project_root=self.repository_root,
                    report_path=Path(acceptance_binding["path"]),
                    run_id=run_id,
                    coordination_revision=expected_run_revision,
                )
            except ContractError:
                _reject(
                    "Acceptance transition requires a current passing Acceptance Report v2",
                    "acceptance_decision",
                    "acceptance_report_v2_not_pass",
                )
        if to_stage == "delivered":
            guard_binding = artifacts.get("delivery_guard_report")
            if not isinstance(guard_binding, dict):
                _reject(
                    "Delivery transition lacks a Delivery Guard Report",
                    "delivery_guard_decision",
                    "delivery_guard_report_absent",
                )
            try:
                validate_delivery_guard_report(
                    report_path=Path(guard_binding["path"])
                )
            except ContractError:
                _reject(
                    "Delivery transition requires a passing Delivery Guard Report",
                    "delivery_guard_decision",
                    "delivery_guard_report_not_pass",
                )
        gate = value.get("global_gate_authority")
        if not isinstance(gate, dict) or set(gate) != {"path", "generation", "sha256"}:
            _reject(
                "Delivery transition Global Gate binding is invalid",
                "global_gate_authority",
                "delivery_global_gate_binding_invalid",
            )
        gate_path = Path(gate["path"]).resolve()
        if not gate_path.is_file() or sha256_file(gate_path) != gate["sha256"]:
            raise ArtifactDrift(
                "Delivery transition Global Gate authority is stale",
                data={
                    "first_failing_gate": "global_gate_authority",
                    "error_code": "delivery_global_gate_binding_stale",
                },
            )
        return value

    @staticmethod
    def _binding(path: Path, revision: int) -> dict[str, Any]:
        return {
            "path": str(path.resolve()),
            "projection_revision": revision,
            "sha256": sha256_file(path),
        }

    @staticmethod
    def _preserve_projection_states(
        preservation_root: Path, paths: tuple[Path, ...]
    ) -> list[dict[str, Any]]:
        states: list[dict[str, Any]] = []
        for position, path in enumerate(paths):
            target = path.resolve()
            if target.exists() and not target.is_file():
                raise KernelConflict(
                    "Delivery projection preservation target is not a file"
                )
            if target.is_file():
                backup = preservation_root / f"{position:02d}-{target.name}"
                shutil.copy2(target, backup)
                states.append(
                    {
                        "path": str(target),
                        "state": "present",
                        "sha256": sha256_file(target),
                        "backup_path": str(backup),
                    }
                )
            else:
                states.append(
                    {
                        "path": str(target),
                        "state": "absent",
                        "sha256": None,
                        "backup_path": None,
                    }
                )
        return states

    @staticmethod
    def _restore_projection_states(
        journal_path: Path, states: list[dict[str, Any]]
    ) -> None:
        rolled_back_root = journal_path.parent / "rolled-back"
        for position, state in enumerate(states):
            target = Path(state["path"])
            if state["state"] == "present":
                backup = Path(state["backup_path"])
                if (
                    not backup.is_file()
                    or sha256_file(backup) != state["sha256"]
                ):
                    raise KernelConflict(
                        "Delivery reconciliation preservation set is incomplete",
                        data={
                            "first_failing_gate": "delivery_reconcile",
                            "error_code": "delivery_reconcile_preservation_incomplete",
                        },
                    )
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup, target)
                continue
            if state["state"] != "absent":
                raise KernelConflict(
                    "Delivery reconciliation preservation state is invalid"
                )
            if target.exists():
                if not target.is_file():
                    raise KernelConflict(
                        "Delivery reconciliation cannot preserve a non-file projection"
                    )
                rolled_back_root.mkdir(parents=True, exist_ok=True)
                destination = rolled_back_root / f"{position:02d}-{target.name}"
                if destination.exists():
                    raise KernelConflict(
                        "Delivery reconciliation rollback destination is occupied"
                    )
                target.replace(destination)

    @staticmethod
    def _require_run_promotion_authority(
        store: ControlStore,
        connection: sqlite3.Connection,
        *,
        run_record: dict[str, Any],
        prior_run_sha256: str,
    ) -> None:
        run_id = run_record["run_id"]
        if store._current_run_record_sha(connection, run_id) != prior_run_sha256:
            raise ArtifactDrift(
                "Delivery lifecycle Run predecessor is stale",
                data={
                    "first_failing_gate": "run_predecessor",
                    "error_code": "delivery_run_predecessor_stale",
                },
            )
        if store._next_run_revision(connection, run_id) != run_record[
            "coordination_revision"
        ]:
            raise KernelConflict(
                "Delivery lifecycle expected revision is outside the committed Run chain"
            )
        store._assert_run_promotion_slot(connection, run_id)

    @staticmethod
    def _advance_intent_state(
        store: ControlStore,
        *,
        intent_id: str,
        expected_state: str,
        new_state: str,
        release_slots: bool = False,
    ) -> None:
        with sqlite3.connect(store.path, isolation_level=None) as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                "UPDATE delivery_lifecycle_intents SET state=? "
                "WHERE intent_id=? AND state=?",
                (new_state, intent_id, expected_state),
            ).rowcount
            if changed != 1:
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    "Delivery lifecycle writer lost its intent-state fence",
                    data={
                        "first_failing_gate": "lifecycle_fencing",
                        "error_code": "delivery_writer_fence_lost",
                    },
                )
            if release_slots:
                connection.execute(
                    "UPDATE projection_publication_slots SET state='RELEASED' "
                    "WHERE intent_id=? AND state='HELD'",
                    (intent_id,),
                )
            connection.execute("COMMIT")

    @staticmethod
    def _commit_run_record(
        store: ControlStore,
        *,
        intent_id: str,
        run_path: Path,
        replacement: dict[str, Any],
    ) -> None:
        with sqlite3.connect(store.path, isolation_level=None) as connection:
            connection.execute("BEGIN IMMEDIATE")
            state = connection.execute(
                "SELECT state FROM delivery_lifecycle_intents WHERE intent_id=?",
                (intent_id,),
            ).fetchone()
            if state is None or state[0] != "FILES_PUBLISHED":
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    "Delivery lifecycle writer lost its Run commit fence",
                    data={
                        "first_failing_gate": "lifecycle_fencing",
                        "error_code": "delivery_writer_fence_lost",
                    },
                )
            write_json_atomic(run_path, replacement)
            changed = connection.execute(
                "UPDATE delivery_lifecycle_intents SET state='RECORD_COMMITTED' "
                "WHERE intent_id=? AND state='FILES_PUBLISHED'",
                (intent_id,),
            ).rowcount
            if changed != 1:
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    "Delivery lifecycle Run commit lost its state fence"
                )
            connection.execute("COMMIT")

    @_run_locked
    def transition(
        self,
        *,
        run_dir: Path,
        from_stage: str,
        to_stage: str,
        session_id: str,
        expected_run_revision: int,
        expected_ownership_generation: int,
        evidence_path: Path,
        transitioned_at: str,
        fault_point: str | None = None,
    ) -> dict[str, Any]:
        del transitioned_at  # The evidence bytes and intent identity carry time authority.
        require_safe_path_segment(
            session_id,
            purpose="Delivery lifecycle session identity",
            error_type=CliUsageError,
        )
        run_path, run_record = self._load_run(run_dir)
        delivery = run_record["delivery"]
        ownership = delivery["ownership"]
        if (from_stage, to_stage) not in LEGAL_TRANSITIONS:
            _reject(
                "Delivery lifecycle transition is illegal",
                "lifecycle_transition",
                "delivery_transition_illegal",
            )
        if (
            delivery["stage"] != from_stage
            or ownership["session_id"] != session_id
            or run_record["coordination_revision"] != expected_run_revision
            or ownership["generation"] != expected_ownership_generation
        ):
            raise KernelConflict(
                "Delivery lifecycle CAS identity is stale",
                data={
                    "first_failing_gate": "lifecycle_fencing",
                    "error_code": "delivery_lifecycle_fence_lost",
                },
            )
        evidence = self._validated_evidence(
            evidence_path.resolve(),
            run_id=run_record["run_id"],
            from_stage=from_stage,
            to_stage=to_stage,
            expected_run_revision=expected_run_revision,
        )
        projections = delivery["projections"]
        video_path = self._validate_binding(
            projections["video_target"],
            label="video target",
            run_dir=run_dir.resolve(),
        )
        session_path = self._validate_binding(
            projections["session_target"], label="session target"
        )
        video_target = read_json(video_path)
        session_target = read_json(session_path)
        task_index_path = self._validate_task_index_binding(
            projections["task_index"],
            run_record=run_record,
            video_target=video_target,
            session_target=session_target,
        )
        project_root = self._project_root(run_dir.resolve())
        for path in (session_path, task_index_path):
            if not path.is_relative_to(project_root):
                _reject(
                    "Delivery projection escapes the project root",
                    "path_boundary",
                    "delivery_projection_path_escape",
                )

        evidence_sha = sha256_file(evidence_path.resolve())
        identity = {
            "operation": "transition",
            "run_id": run_record["run_id"],
            "expected_run_revision": expected_run_revision,
            "expected_ownership_generation": expected_ownership_generation,
            "from_stage": from_stage,
            "to_stage": to_stage,
            "session_id": session_id,
            "evidence_sha256": evidence_sha,
        }
        intent_id = _sha256_json(identity)
        run_revision = expected_run_revision + 1
        old_video = video_target
        old_session = session_target
        task_index_predecessor_sha = sha256_file(task_index_path)
        old_index = read_json(task_index_path)
        artifacts = dict(old_video["artifacts"])
        for role in ("final_pdf", "main_tex", "final_compile_report"):
            if role in evidence["artifacts"]:
                artifacts[role] = evidence["artifacts"][role]
        if "acceptance_report" in evidence["artifacts"]:
            artifacts["acceptance_report"] = evidence["artifacts"]["acceptance_report"]
        if "delivery_guard_report" in evidence["artifacts"]:
            artifacts["delivery_guard_report"] = evidence["artifacts"]["delivery_guard_report"]
        video = {
            **old_video,
            "projection_revision": old_video["projection_revision"] + 1,
            "run_revision": run_revision,
            "lifecycle_intent_id": intent_id,
            "stage": to_stage,
            "artifacts": artifacts,
            "global_gate_authority": evidence["global_gate_authority"],
        }
        video_sha = _sha256_json(video)
        session = {
            **old_session,
            "projection_revision": old_session["projection_revision"] + 1,
            "run_revision": run_revision,
            "lifecycle_intent_id": intent_id,
            "stage": to_stage,
            "video_target": {
                "path": str(video_path),
                "projection_revision": video["projection_revision"],
                "sha256": video_sha,
            },
        }
        session_sha = _sha256_json(session)
        index = dict(old_index)
        index["projection_revision"] = old_index["projection_revision"] + 1
        entries = [dict(item) for item in old_index["entries"]]
        matching = [item for item in entries if item["run_id"] == run_record["run_id"]]
        if len(matching) != 1:
            _reject(
                "Delivery task index Run identity is invalid",
                "task_index",
                "delivery_task_index_identity_invalid",
            )
        entry = matching[0]
        entry.update(
            {
                "run_revision": run_revision,
                "lifecycle_intent_id": intent_id,
                "stage": to_stage,
                "session_id": session_id,
                "ownership_generation": expected_ownership_generation,
                "video_target": {
                    "path": str(video_path),
                    "projection_revision": video["projection_revision"],
                    "sha256": video_sha,
                },
                "session_target": {
                    "path": str(session_path),
                    "projection_revision": session["projection_revision"],
                    "sha256": session_sha,
                },
            }
        )
        index["entries"] = sorted(entries, key=lambda item: item["run_id"])
        index_sha = _sha256_json(index)
        replacement = json.loads(json.dumps(run_record))
        replacement["coordination_revision"] = run_revision
        replacement["last_mutation_intent_id"] = intent_id
        replacement["delivery"]["stage"] = to_stage
        replacement["delivery"]["projections"].update(
            {
                "video_target": {
                    "path": "review/acceptance/delivery_target.json",
                    "projection_revision": video["projection_revision"],
                    "sha256": video_sha,
                },
                "session_target": {
                    "path": str(session_path),
                    "projection_revision": session["projection_revision"],
                    "sha256": session_sha,
                },
                "task_index": {
                    "path": str(task_index_path),
                    "projection_revision": index["projection_revision"],
                    "sha256": index_sha,
                },
            }
        )
        self.contracts.validate("run-record", replacement)
        self.contracts.validate("kernel-delivery-target", video)
        self.contracts.validate("kernel-session-delivery-target", session)
        self.contracts.validate("kernel-delivery-task-index", index)

        store = ControlStore(run_dir.resolve().parent, self.contracts)
        if store.binding_for_run(run_record["run_id"]) is None:
            raise KernelConflict("Delivery lifecycle Run lacks Control Store binding")
        replacement_sha = _sha256_json(replacement)
        preservation_root = (
            run_dir.resolve() / "待删除" / "delivery-lifecycle" / intent_id / "prior"
        )
        preservation_root.mkdir(parents=True, exist_ok=True)
        prior_paths = (
            video_path,
            session_path,
            task_index_path,
        )
        preservation_states = self._preserve_projection_states(
            preservation_root, prior_paths
        )
        shutil.copy2(run_path, preservation_root / f"03-{run_path.name}")
        journal_path = preservation_root.parent / "intent.json"
        journal = {
            "intent_id": intent_id,
            "identity": identity,
            "run_path": str(run_path),
            "prior_run_sha256": sha256_file(run_path),
            "replacement_run_sha256": replacement_sha,
            "preservations": preservation_states,
            "projections": [
                {"path": str(video_path), "value": video, "sha256": video_sha},
                {"path": str(session_path), "value": session, "sha256": session_sha},
                {"path": str(task_index_path), "value": index, "sha256": index_sha},
            ],
        }
        write_json_atomic(journal_path, journal)
        connection = sqlite3.connect(store.path, timeout=5.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE")
            self._require_run_promotion_authority(
                store,
                connection,
                run_record=run_record,
                prior_run_sha256=journal["prior_run_sha256"],
            )
            if sha256_file(task_index_path) != task_index_predecessor_sha:
                connection.execute("ROLLBACK")
                raise ArtifactDrift(
                    "Delivery task index changed before its publication slot was acquired",
                    data={
                        "first_failing_gate": "projection_fencing",
                        "error_code": "delivery_task_index_fence_lost",
                    },
                )
            existing = connection.execute(
                "SELECT * FROM delivery_lifecycle_intents WHERE intent_id=?",
                (intent_id,),
            ).fetchone()
            intent_values = (
                run_record["run_id"],
                session_id,
                expected_run_revision,
                expected_ownership_generation,
                from_stage,
                to_stage,
                "transition",
                journal["prior_run_sha256"],
                replacement_sha,
                canonical_json_bytes(replacement).decode("utf-8"),
                _sha256_json(identity),
            )
            if existing is None:
                connection.execute(
                    "INSERT INTO delivery_lifecycle_intents("
                    "intent_id,run_id,session_id,expected_run_revision,"
                    "expected_ownership_generation,prior_stage,target_stage,operation,"
                    "prior_run_record_sha256,replacement_run_record_sha256,"
                    "replacement_run_record_json,state,intent_identity) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?, 'PREPARED',?)",
                    (intent_id, *intent_values),
                )
            elif (
                existing["state"] == "ABORTED"
                and existing["intent_identity"] == _sha256_json(identity)
            ):
                connection.execute(
                    "UPDATE delivery_lifecycle_intents SET state='PREPARED',"
                    "replacement_run_record_sha256=?,replacement_run_record_json=? "
                    "WHERE intent_id=?",
                    (replacement_sha, canonical_json_bytes(replacement).decode("utf-8"), intent_id),
                )
            else:
                connection.execute("ROLLBACK")
                raise KernelConflict("Delivery lifecycle intent already exists")
            proposed = {str(video_path): video_sha, str(session_path): session_sha, str(task_index_path): index_sha}
            for path in sorted(
                (video_path, session_path, task_index_path),
                key=normalized_physical_path,
            ):
                normalized = normalized_physical_path(path)
                slot_identity = hashlib.sha256(
                    (intent_id + "\0" + normalized).encode("utf-8")
                ).hexdigest()
                connection.execute(
                    "INSERT INTO projection_publication_slots("
                    "slot_id,intent_id,normalized_path,expected_state,expected_sha256,"
                    "proposed_state,proposed_sha256,state,slot_identity) "
                    "VALUES(?,?,?,'present',?,'present',?,'HELD',?) "
                    "ON CONFLICT(slot_id) DO UPDATE SET "
                    "expected_state='present',expected_sha256=excluded.expected_sha256,"
                    "proposed_state='present',proposed_sha256=excluded.proposed_sha256,"
                    "state='HELD'",
                    (
                        slot_identity,
                        intent_id,
                        normalized,
                        sha256_file(path),
                        proposed[str(path)],
                        slot_identity,
                    ),
                )
            connection.execute("COMMIT")
        finally:
            connection.close()

        if fault_point == "after_intent_prepared":
            raise DeliveryLifecycleFault(fault_point)

        write_json_atomic(video_path, video)
        if fault_point == "after_video_target_write":
            raise DeliveryLifecycleFault(fault_point)
        write_json_atomic(session_path, session)
        write_json_atomic(task_index_path, index)
        self._advance_intent_state(
            store,
            intent_id=intent_id,
            expected_state="PREPARED",
            new_state="FILES_PUBLISHED",
        )
        if fault_point == "after_task_index_write":
            raise DeliveryLifecycleFault(fault_point)
        self._commit_run_record(
            store,
            intent_id=intent_id,
            run_path=run_path,
            replacement=replacement,
        )
        if fault_point == "after_run_record_commit":
            raise DeliveryLifecycleFault(fault_point)
        self._advance_intent_state(
            store,
            intent_id=intent_id,
            expected_state="RECORD_COMMITTED",
            new_state="COMMITTED",
            release_slots=True,
        )
        if fault_point == "after_control_store_commit":
            raise DeliveryLifecycleFault(fault_point)
        return {
            "run_id": run_record["run_id"],
            "intent_id": intent_id,
            "stage": to_stage,
            "run_revision": run_revision,
            "ownership_generation": expected_ownership_generation,
            "run_record_path": str(run_path),
        }

    @_run_locked
    def reconcile(self, *, run_dir: Path) -> dict[str, Any]:
        run_path, run_record = self._load_run(run_dir)
        store = ControlStore(run_dir.resolve().parent, self.contracts)
        connection = sqlite3.connect(store.path, timeout=5.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("BEGIN IMMEDIATE")
            intents = connection.execute(
                "SELECT * FROM delivery_lifecycle_intents "
                "WHERE run_id=? AND state IN ('PREPARED','FILES_PUBLISHED','RECORD_COMMITTED')",
                (run_record["run_id"],),
            ).fetchall()
            if not intents:
                connection.execute("COMMIT")
                return {
                    "run_id": run_record["run_id"],
                    "outcome": "no_op",
                    "stage": run_record["delivery"]["stage"],
                    "run_revision": run_record["coordination_revision"],
                    "run_record_path": str(run_path),
                }
            if len(intents) != 1:
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    "Delivery reconciliation found ambiguous pending intents",
                    data={
                        "first_failing_gate": "delivery_reconcile",
                        "error_code": "delivery_reconcile_ambiguous",
                    },
                )
            intent = intents[0]
            intent_id = intent["intent_id"]
            journal_path = (
                run_dir.resolve()
                / "待删除"
                / "delivery-lifecycle"
                / intent_id
                / "intent.json"
            )
            if not journal_path.is_file():
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    "Delivery reconciliation journal is absent",
                    data={
                        "first_failing_gate": "delivery_reconcile",
                        "error_code": "delivery_reconcile_journal_absent",
                    },
                )
            journal = read_json(journal_path)
            current_run_sha = sha256_file(run_path)
            if current_run_sha == journal["prior_run_sha256"]:
                if intent["state"] not in {"PREPARED", "FILES_PUBLISHED"}:
                    connection.execute("ROLLBACK")
                    raise KernelConflict(
                        "Delivery reconciliation found a committed-state Run predecessor",
                        data={
                            "first_failing_gate": "delivery_reconcile",
                            "error_code": "delivery_reconcile_state_conflict",
                        },
                    )
                preservations = journal.get("preservations")
                if not isinstance(preservations, list) or not preservations:
                    connection.execute("ROLLBACK")
                    raise KernelConflict(
                        "Delivery reconciliation preservation set is incomplete",
                        data={
                            "first_failing_gate": "delivery_reconcile",
                            "error_code": "delivery_reconcile_preservation_incomplete",
                        },
                    )
                self._restore_projection_states(journal_path, preservations)
                session_move = journal.get("session_move")
                if isinstance(session_move, dict):
                    moved = Path(session_move["destination"])
                    source = Path(session_move["source"])
                    if moved.is_file() and not source.exists():
                        source.parent.mkdir(parents=True, exist_ok=True)
                        moved.replace(source)
                changed = connection.execute(
                    "UPDATE delivery_lifecycle_intents SET state='ABORTED' "
                    "WHERE intent_id=? AND state=?",
                    (intent_id, intent["state"]),
                ).rowcount
                if changed != 1:
                    connection.execute("ROLLBACK")
                    raise KernelConflict(
                        "Delivery reconciliation lost its abort fence",
                        data={
                            "first_failing_gate": "lifecycle_fencing",
                            "error_code": "delivery_reconcile_fence_lost",
                        },
                    )
                connection.execute(
                    "UPDATE projection_publication_slots SET state='RELEASED' WHERE intent_id=?",
                    (intent_id,),
                )
                connection.execute("COMMIT")
                restored = read_json(run_path)
                return {
                    "run_id": restored["run_id"],
                    "intent_id": intent_id,
                    "outcome": "rolled_back",
                    "recovery_outcome": "rolled_back",
                    "stage": restored["delivery"]["stage"],
                    "run_revision": restored["coordination_revision"],
                    "run_record_path": str(run_path),
                }
            if current_run_sha == journal["replacement_run_sha256"]:
                for projection in journal["projections"]:
                    path = Path(projection["path"])
                    path.parent.mkdir(parents=True, exist_ok=True)
                    write_json_atomic(path, projection["value"])
                session_move = journal.get("session_move")
                if isinstance(session_move, dict):
                    source = Path(session_move["source"])
                    destination = Path(session_move["destination"])
                    if source.is_file() and not destination.exists():
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        source.replace(destination)
                    elif source.exists() and destination.exists():
                        connection.execute("ROLLBACK")
                        raise KernelConflict(
                            "Delivery archive session move is ambiguous",
                            data={
                                "first_failing_gate": "delivery_reconcile",
                                "error_code": "delivery_reconcile_session_move_conflict",
                            },
                        )
                changed = connection.execute(
                    "UPDATE delivery_lifecycle_intents SET state='COMMITTED' "
                    "WHERE intent_id=? AND state=?",
                    (intent_id, intent["state"]),
                ).rowcount
                if changed != 1:
                    connection.execute("ROLLBACK")
                    raise KernelConflict(
                        "Delivery reconciliation lost its commit fence",
                        data={
                            "first_failing_gate": "lifecycle_fencing",
                            "error_code": "delivery_reconcile_fence_lost",
                        },
                    )
                connection.execute(
                    "UPDATE projection_publication_slots SET state='RELEASED' WHERE intent_id=?",
                    (intent_id,),
                )
                connection.execute("COMMIT")
                return {
                    "run_id": run_record["run_id"],
                    "intent_id": intent_id,
                    "outcome": "completed",
                    "stage": run_record["delivery"]["stage"],
                    "run_revision": run_record["coordination_revision"],
                    "run_record_path": str(run_path),
                }
            connection.execute("ROLLBACK")
            raise KernelConflict(
                "Delivery reconciliation Run Record matches neither revision",
                data={
                    "first_failing_gate": "delivery_reconcile",
                    "error_code": "delivery_reconcile_run_conflict",
                },
            )
        finally:
            connection.close()

    @_run_locked
    def handoff(
        self,
        *,
        run_dir: Path,
        from_session_id: str,
        to_session_id: str,
        expected_run_revision: int,
        expected_ownership_generation: int,
        handed_off_at: str,
        fault_point: str | None = None,
    ) -> dict[str, Any]:
        require_safe_path_segment(
            from_session_id,
            purpose="Delivery handoff source session identity",
            error_type=CliUsageError,
        )
        require_safe_path_segment(
            to_session_id,
            purpose="Delivery handoff successor session identity",
            error_type=CliUsageError,
        )
        del handed_off_at
        if from_session_id == to_session_id:
            _reject(
                "Delivery handoff requires a distinct successor session",
                "ownership_handoff",
                "delivery_handoff_same_session",
            )
        run_path, run_record = self._load_run(run_dir)
        delivery = run_record["delivery"]
        ownership = delivery["ownership"]
        if (
            ownership["session_id"] != from_session_id
            or ownership["generation"] != expected_ownership_generation
            or run_record["coordination_revision"] != expected_run_revision
        ):
            raise KernelConflict(
                "Delivery handoff CAS identity is stale",
                data={
                    "first_failing_gate": "lifecycle_fencing",
                    "error_code": "delivery_handoff_fence_lost",
                },
            )
        projections = delivery["projections"]
        video_path = self._validate_binding(
            projections["video_target"],
            label="video target",
            run_dir=run_dir.resolve(),
        )
        old_session_path = self._validate_binding(
            projections["session_target"], label="session target"
        )
        old_video = read_json(video_path)
        old_session = read_json(old_session_path)
        task_index_path = self._validate_task_index_binding(
            projections["task_index"],
            run_record=run_record,
            video_target=old_video,
            session_target=old_session,
        )
        project_root = self._project_root(run_dir.resolve())
        new_session_path = (
            project_root
            / ".codex"
            / "delivery-targets"
            / "sessions"
            / to_session_id
            / "current.json"
        )
        if new_session_path.exists():
            raise KernelConflict(
                "Delivery successor session projection already exists",
                data={
                    "first_failing_gate": "projection_slot",
                    "error_code": "delivery_successor_session_occupied",
                },
            )
        run_revision = expected_run_revision + 1
        ownership_generation = expected_ownership_generation + 1
        identity = {
            "operation": "handoff",
            "run_id": run_record["run_id"],
            "expected_run_revision": expected_run_revision,
            "expected_ownership_generation": expected_ownership_generation,
            "from_session_id": from_session_id,
            "to_session_id": to_session_id,
        }
        intent_id = _sha256_json(identity)
        task_index_predecessor_sha = sha256_file(task_index_path)
        old_index = read_json(task_index_path)
        video = {
            **old_video,
            "projection_revision": old_video["projection_revision"] + 1,
            "run_revision": run_revision,
            "lifecycle_intent_id": intent_id,
            "ownership": {
                "session_id": to_session_id,
                "generation": ownership_generation,
            },
        }
        video_sha = _sha256_json(video)
        superseded_session = {
            **old_session,
            "projection_revision": old_session["projection_revision"] + 1,
            "run_revision": run_revision,
            "lifecycle_intent_id": intent_id,
            "owner_status": "superseded",
            "video_target": {
                "path": str(video_path),
                "projection_revision": video["projection_revision"],
                "sha256": video_sha,
            },
        }
        new_session = {
            **old_session,
            "projection_revision": 1,
            "projection_path": str(new_session_path.resolve()),
            "session_id": to_session_id,
            "run_revision": run_revision,
            "lifecycle_intent_id": intent_id,
            "ownership_generation": ownership_generation,
            "owner_status": "active",
            "video_target": {
                "path": str(video_path),
                "projection_revision": video["projection_revision"],
                "sha256": video_sha,
            },
        }
        superseded_sha = _sha256_json(superseded_session)
        new_session_sha = _sha256_json(new_session)
        index = dict(old_index)
        index["projection_revision"] = old_index["projection_revision"] + 1
        entries = [dict(item) for item in old_index["entries"]]
        matching = [item for item in entries if item["run_id"] == run_record["run_id"]]
        if len(matching) != 1:
            _reject(
                "Delivery task index Run identity is invalid",
                "task_index",
                "delivery_task_index_identity_invalid",
            )
        matching[0].update(
            {
                "run_revision": run_revision,
                "lifecycle_intent_id": intent_id,
                "session_id": to_session_id,
                "ownership_generation": ownership_generation,
                "video_target": {
                    "path": str(video_path),
                    "projection_revision": video["projection_revision"],
                    "sha256": video_sha,
                },
                "session_target": {
                    "path": str(new_session_path.resolve()),
                    "projection_revision": 1,
                    "sha256": new_session_sha,
                },
            }
        )
        index["entries"] = sorted(entries, key=lambda item: item["run_id"])
        index_sha = _sha256_json(index)
        replacement = json.loads(json.dumps(run_record))
        replacement["coordination_revision"] = run_revision
        replacement["last_mutation_intent_id"] = intent_id
        replacement["delivery"]["ownership"] = {
            "session_id": to_session_id,
            "generation": ownership_generation,
        }
        replacement["delivery"]["projections"].update(
            {
                "video_target": {
                    "path": "review/acceptance/delivery_target.json",
                    "projection_revision": video["projection_revision"],
                    "sha256": video_sha,
                },
                "session_target": {
                    "path": str(new_session_path.resolve()),
                    "projection_revision": 1,
                    "sha256": new_session_sha,
                },
                "task_index": {
                    "path": str(task_index_path),
                    "projection_revision": index["projection_revision"],
                    "sha256": index_sha,
                },
            }
        )
        self.contracts.validate("kernel-delivery-target", video)
        self.contracts.validate("kernel-session-delivery-target", superseded_session)
        self.contracts.validate("kernel-session-delivery-target", new_session)
        self.contracts.validate("kernel-delivery-task-index", index)
        self.contracts.validate("run-record", replacement)
        store = ControlStore(run_dir.resolve().parent, self.contracts)
        replacement_sha = _sha256_json(replacement)
        prior_run_sha = sha256_file(run_path)
        preservation_root = (
            run_dir.resolve() / "待删除" / "delivery-lifecycle" / intent_id / "prior"
        )
        preservation_root.mkdir(parents=True, exist_ok=True)
        preservation_states = self._preserve_projection_states(
            preservation_root,
            (video_path, old_session_path, new_session_path, task_index_path),
        )
        shutil.copy2(run_path, preservation_root / f"04-{run_path.name}")
        journal_path = preservation_root.parent / "intent.json"
        write_json_atomic(
            journal_path,
            {
                "intent_id": intent_id,
                "identity": identity,
                "run_path": str(run_path),
                "prior_run_sha256": prior_run_sha,
                "replacement_run_sha256": replacement_sha,
                "preservations": preservation_states,
                "projections": [
                    {"path": str(video_path), "value": video, "sha256": video_sha},
                    {
                        "path": str(old_session_path),
                        "value": superseded_session,
                        "sha256": superseded_sha,
                    },
                    {
                        "path": str(new_session_path),
                        "value": new_session,
                        "sha256": new_session_sha,
                    },
                    {"path": str(task_index_path), "value": index, "sha256": index_sha},
                ],
            },
        )
        connection = sqlite3.connect(store.path, timeout=5.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE")
            self._require_run_promotion_authority(
                store,
                connection,
                run_record=run_record,
                prior_run_sha256=prior_run_sha,
            )
            if sha256_file(task_index_path) != task_index_predecessor_sha:
                connection.execute("ROLLBACK")
                raise ArtifactDrift(
                    "Delivery task index changed before its publication slot was acquired",
                    data={
                        "first_failing_gate": "projection_fencing",
                        "error_code": "delivery_task_index_fence_lost",
                    },
                )
            existing = connection.execute(
                "SELECT * FROM delivery_lifecycle_intents WHERE intent_id=?",
                (intent_id,),
            ).fetchone()
            if existing is None:
                connection.execute(
                    "INSERT INTO delivery_lifecycle_intents("
                    "intent_id,run_id,session_id,expected_run_revision,"
                    "expected_ownership_generation,prior_stage,target_stage,operation,"
                    "prior_run_record_sha256,replacement_run_record_sha256,"
                    "replacement_run_record_json,state,intent_identity) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?, 'PREPARED',?)",
                    (
                        intent_id,
                        run_record["run_id"],
                        to_session_id,
                        expected_run_revision,
                        expected_ownership_generation,
                        delivery["stage"],
                        delivery["stage"],
                        "handoff",
                        prior_run_sha,
                        replacement_sha,
                        canonical_json_bytes(replacement).decode("utf-8"),
                        _sha256_json(identity),
                    ),
                )
            elif (
                existing["state"] == "ABORTED"
                and existing["intent_identity"] == _sha256_json(identity)
            ):
                connection.execute(
                    "UPDATE delivery_lifecycle_intents SET state='PREPARED',"
                    "replacement_run_record_sha256=?,replacement_run_record_json=? "
                    "WHERE intent_id=?",
                    (
                        replacement_sha,
                        canonical_json_bytes(replacement).decode("utf-8"),
                        intent_id,
                    ),
                )
            else:
                connection.execute("ROLLBACK")
                raise KernelConflict("Delivery handoff intent already exists")
            slot_specs = (
                (video_path, "present", sha256_file(video_path), video_sha),
                (
                    old_session_path,
                    "present",
                    sha256_file(old_session_path),
                    superseded_sha,
                ),
                (new_session_path, "absent", None, new_session_sha),
                (
                    task_index_path,
                    "present",
                    task_index_predecessor_sha,
                    index_sha,
                ),
            )
            for target, expected_state, expected_sha, proposed_sha in sorted(
                slot_specs, key=lambda item: normalized_physical_path(item[0])
            ):
                slot_identity = hashlib.sha256(
                    (intent_id + "\0" + normalized_physical_path(target)).encode("utf-8")
                ).hexdigest()
                connection.execute(
                    "INSERT INTO projection_publication_slots("
                    "slot_id,intent_id,normalized_path,expected_state,expected_sha256,"
                    "proposed_state,proposed_sha256,state,slot_identity) "
                    "VALUES(?,?,?,?,?,'present',?,'HELD',?) "
                    "ON CONFLICT(slot_id) DO UPDATE SET "
                    "expected_state=excluded.expected_state,"
                    "expected_sha256=excluded.expected_sha256,"
                    "proposed_state='present',"
                    "proposed_sha256=excluded.proposed_sha256,state='HELD'",
                    (
                        slot_identity,
                        intent_id,
                        normalized_physical_path(target),
                        expected_state,
                        expected_sha,
                        proposed_sha,
                        slot_identity,
                    ),
                )
            connection.execute("COMMIT")
        finally:
            connection.close()
        if fault_point == "after_intent_prepared":
            raise DeliveryLifecycleFault(fault_point)
        write_json_atomic(video_path, video)
        if fault_point == "after_video_target_write":
            raise DeliveryLifecycleFault(fault_point)
        write_json_atomic(old_session_path, superseded_session)
        new_session_path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(new_session_path, new_session)
        write_json_atomic(task_index_path, index)
        self._advance_intent_state(
            store,
            intent_id=intent_id,
            expected_state="PREPARED",
            new_state="FILES_PUBLISHED",
        )
        if fault_point == "after_task_index_write":
            raise DeliveryLifecycleFault(fault_point)
        self._commit_run_record(
            store,
            intent_id=intent_id,
            run_path=run_path,
            replacement=replacement,
        )
        if fault_point == "after_run_record_commit":
            raise DeliveryLifecycleFault(fault_point)
        self._advance_intent_state(
            store,
            intent_id=intent_id,
            expected_state="RECORD_COMMITTED",
            new_state="COMMITTED",
            release_slots=True,
        )
        if fault_point == "after_control_store_commit":
            raise DeliveryLifecycleFault(fault_point)
        return {
            "run_id": run_record["run_id"],
            "intent_id": intent_id,
            "stage": delivery["stage"],
            "run_revision": run_revision,
            "ownership_generation": ownership_generation,
            "run_record_path": str(run_path),
        }

    @_run_locked
    def archive(
        self,
        *,
        run_dir: Path,
        session_id: str,
        expected_run_revision: int,
        expected_ownership_generation: int,
        archived_at: str,
        fault_point: str | None = None,
    ) -> dict[str, Any]:
        require_safe_path_segment(
            session_id,
            purpose="Delivery archive session identity",
            error_type=CliUsageError,
        )
        run_path, run_record = self._load_run(run_dir)
        delivery = run_record["delivery"]
        ownership = delivery["ownership"]
        if delivery["stage"] != "delivered":
            _reject(
                "Delivery archive requires delivered stage",
                "lifecycle_transition",
                "delivery_archive_stage_invalid",
            )
        if (
            ownership["session_id"] != session_id
            or ownership["generation"] != expected_ownership_generation
            or run_record["coordination_revision"] != expected_run_revision
        ):
            raise KernelConflict(
                "Delivery archive CAS identity is stale",
                data={
                    "first_failing_gate": "lifecycle_fencing",
                    "error_code": "delivery_archive_fence_lost",
                },
            )
        projections = delivery["projections"]
        video_path = self._validate_binding(
            projections["video_target"],
            label="video target",
            run_dir=run_dir.resolve(),
        )
        session_path = self._validate_binding(
            projections["session_target"], label="session target"
        )
        old_video = read_json(video_path)
        old_session = read_json(session_path)
        task_index_path = self._validate_task_index_binding(
            projections["task_index"],
            run_record=run_record,
            video_target=old_video,
            session_target=old_session,
        )
        project_root = self._project_root(run_dir.resolve())
        identity = {
            "operation": "archive",
            "run_id": run_record["run_id"],
            "expected_run_revision": expected_run_revision,
            "expected_ownership_generation": expected_ownership_generation,
            "session_id": session_id,
        }
        intent_id = _sha256_json(identity)
        archive_path = (
            project_root
            / ".codex"
            / "delivery-targets"
            / "archive"
            / session_id
            / f"{intent_id}.json"
        )
        if archive_path.exists():
            raise KernelConflict(
                "Delivery archive destination is occupied",
                data={
                    "first_failing_gate": "projection_slot",
                    "error_code": "delivery_archive_destination_occupied",
                },
            )
        run_revision = expected_run_revision + 1
        task_index_predecessor_sha = sha256_file(task_index_path)
        old_index = read_json(task_index_path)
        guard_binding = old_video["artifacts"].get("delivery_guard_report")
        if not isinstance(guard_binding, dict):
            _reject(
                "Delivered target lacks a Delivery Guard Report binding",
                "delivery_guard_report",
                "delivery_guard_report_absent",
            )
        video = {
            **old_video,
            "projection_revision": old_video["projection_revision"] + 1,
            "run_revision": run_revision,
            "lifecycle_intent_id": intent_id,
        }
        video_sha = _sha256_json(video)
        archive = {
            "schema_name": "kernel-delivery-target-archive",
            "schema_version": "1.0.0",
            "projection_kind": "archive",
            "projection_revision": 1,
            "run_id": run_record["run_id"],
            "run_revision": run_revision,
            "lifecycle_intent_id": intent_id,
            "stage": "delivered",
            "session_id": session_id,
            "ownership_generation": expected_ownership_generation,
            "archived_from": {
                "path": str(session_path),
                "projection_revision": old_session["projection_revision"],
                "sha256": sha256_file(session_path),
            },
            "video_target": {
                "path": str(video_path),
                "projection_revision": video["projection_revision"],
                "sha256": video_sha,
            },
            "delivery_guard_report": guard_binding,
            "archived_at": archived_at,
        }
        archive_sha = _sha256_json(archive)
        index = dict(old_index)
        index["projection_revision"] = old_index["projection_revision"] + 1
        entries = [dict(item) for item in old_index["entries"]]
        matching = [item for item in entries if item["run_id"] == run_record["run_id"]]
        if len(matching) != 1:
            _reject(
                "Delivery task index Run identity is invalid",
                "task_index",
                "delivery_task_index_identity_invalid",
            )
        matching[0].update(
            {
                "run_revision": run_revision,
                "lifecycle_intent_id": intent_id,
                "video_target": {
                    "path": str(video_path),
                    "projection_revision": video["projection_revision"],
                    "sha256": video_sha,
                },
                "session_target": None,
                "archive": {
                    "path": str(archive_path.resolve()),
                    "projection_revision": 1,
                    "sha256": archive_sha,
                },
            }
        )
        index["entries"] = sorted(entries, key=lambda item: item["run_id"])
        index_sha = _sha256_json(index)
        replacement = json.loads(json.dumps(run_record))
        replacement["coordination_revision"] = run_revision
        replacement["last_mutation_intent_id"] = intent_id
        replacement["delivery"]["projections"].update(
            {
                "video_target": {
                    "path": "review/acceptance/delivery_target.json",
                    "projection_revision": video["projection_revision"],
                    "sha256": video_sha,
                },
                "session_target": None,
                "task_index": {
                    "path": str(task_index_path),
                    "projection_revision": index["projection_revision"],
                    "sha256": index_sha,
                },
                "archive": {
                    "path": str(archive_path.resolve()),
                    "projection_revision": 1,
                    "sha256": archive_sha,
                },
            }
        )
        self.contracts.validate("kernel-delivery-target", video)
        self.contracts.validate("kernel-delivery-target-archive", archive)
        self.contracts.validate("kernel-delivery-task-index", index)
        self.contracts.validate("run-record", replacement)
        store = ControlStore(run_dir.resolve().parent, self.contracts)
        replacement_sha = _sha256_json(replacement)
        prior_run_sha = sha256_file(run_path)
        preservation_root = (
            run_dir.resolve() / "待删除" / "delivery-lifecycle" / intent_id / "prior"
        )
        preservation_root.mkdir(parents=True, exist_ok=True)
        preservation_states = self._preserve_projection_states(
            preservation_root,
            (archive_path, video_path, session_path, task_index_path),
        )
        shutil.copy2(run_path, preservation_root / f"04-{run_path.name}")
        moved_session_path = preservation_root.parent / "archived-session-current.json"
        write_json_atomic(
            preservation_root.parent / "intent.json",
            {
                "intent_id": intent_id,
                "identity": identity,
                "run_path": str(run_path),
                "prior_run_sha256": prior_run_sha,
                "replacement_run_sha256": replacement_sha,
                "preservations": preservation_states,
                "session_move": {
                    "source": str(session_path),
                    "destination": str(moved_session_path),
                },
                "projections": [
                    {"path": str(archive_path), "value": archive, "sha256": archive_sha},
                    {"path": str(video_path), "value": video, "sha256": video_sha},
                    {"path": str(task_index_path), "value": index, "sha256": index_sha},
                ],
            },
        )
        connection = sqlite3.connect(store.path, timeout=5.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE")
            self._require_run_promotion_authority(
                store,
                connection,
                run_record=run_record,
                prior_run_sha256=prior_run_sha,
            )
            if sha256_file(task_index_path) != task_index_predecessor_sha:
                connection.execute("ROLLBACK")
                raise ArtifactDrift(
                    "Delivery task index changed before its publication slot was acquired",
                    data={
                        "first_failing_gate": "projection_fencing",
                        "error_code": "delivery_task_index_fence_lost",
                    },
                )
            existing = connection.execute(
                "SELECT * FROM delivery_lifecycle_intents WHERE intent_id=?",
                (intent_id,),
            ).fetchone()
            if existing is None:
                connection.execute(
                    "INSERT INTO delivery_lifecycle_intents("
                    "intent_id,run_id,session_id,expected_run_revision,"
                    "expected_ownership_generation,prior_stage,target_stage,operation,"
                    "prior_run_record_sha256,replacement_run_record_sha256,"
                    "replacement_run_record_json,state,intent_identity) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?, 'PREPARED',?)",
                    (
                        intent_id,
                        run_record["run_id"],
                        session_id,
                        expected_run_revision,
                        expected_ownership_generation,
                        "delivered",
                        "delivered",
                        "archive",
                        prior_run_sha,
                        replacement_sha,
                        canonical_json_bytes(replacement).decode("utf-8"),
                        _sha256_json(identity),
                    ),
                )
            elif (
                existing["state"] == "ABORTED"
                and existing["intent_identity"] == _sha256_json(identity)
            ):
                connection.execute(
                    "UPDATE delivery_lifecycle_intents SET state='PREPARED',"
                    "replacement_run_record_sha256=?,replacement_run_record_json=? "
                    "WHERE intent_id=?",
                    (
                        replacement_sha,
                        canonical_json_bytes(replacement).decode("utf-8"),
                        intent_id,
                    ),
                )
            else:
                connection.execute("ROLLBACK")
                raise KernelConflict("Delivery archive intent already exists")
            slot_specs = (
                (archive_path, "absent", None, "present", archive_sha),
                (video_path, "present", sha256_file(video_path), "present", video_sha),
                (session_path, "present", sha256_file(session_path), "absent", None),
                (
                    task_index_path,
                    "present",
                    task_index_predecessor_sha,
                    "present",
                    index_sha,
                ),
            )
            for path, expected_state, expected_sha, proposed_state, proposed_sha in sorted(
                slot_specs, key=lambda item: normalized_physical_path(item[0])
            ):
                normalized = normalized_physical_path(path)
                slot_identity = hashlib.sha256(
                    (intent_id + "\0" + normalized).encode("utf-8")
                ).hexdigest()
                connection.execute(
                    "INSERT INTO projection_publication_slots("
                    "slot_id,intent_id,normalized_path,expected_state,expected_sha256,"
                    "proposed_state,proposed_sha256,state,slot_identity) "
                    "VALUES(?,?,?,?,?,?,?,'HELD',?) "
                    "ON CONFLICT(slot_id) DO UPDATE SET "
                    "expected_state=excluded.expected_state,"
                    "expected_sha256=excluded.expected_sha256,"
                    "proposed_state=excluded.proposed_state,"
                    "proposed_sha256=excluded.proposed_sha256,state='HELD'",
                    (
                        slot_identity,
                        intent_id,
                        normalized,
                        expected_state,
                        expected_sha,
                        proposed_state,
                        proposed_sha,
                        slot_identity,
                    ),
                )
            connection.execute("COMMIT")
        finally:
            connection.close()
        if fault_point == "after_intent_prepared":
            raise DeliveryLifecycleFault(fault_point)
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(archive_path, archive)
        write_json_atomic(video_path, video)
        if fault_point == "after_video_target_write":
            raise DeliveryLifecycleFault(fault_point)
        write_json_atomic(task_index_path, index)
        if fault_point == "after_task_index_write":
            raise DeliveryLifecycleFault(fault_point)
        session_path.replace(moved_session_path)
        self._advance_intent_state(
            store,
            intent_id=intent_id,
            expected_state="PREPARED",
            new_state="FILES_PUBLISHED",
        )
        self._commit_run_record(
            store,
            intent_id=intent_id,
            run_path=run_path,
            replacement=replacement,
        )
        if fault_point == "after_run_record_commit":
            raise DeliveryLifecycleFault(fault_point)
        self._advance_intent_state(
            store,
            intent_id=intent_id,
            expected_state="RECORD_COMMITTED",
            new_state="COMMITTED",
            release_slots=True,
        )
        if fault_point == "after_control_store_commit":
            raise DeliveryLifecycleFault(fault_point)
        return {
            "run_id": run_record["run_id"],
            "intent_id": intent_id,
            "stage": "delivered",
            "run_revision": run_revision,
            "ownership_generation": expected_ownership_generation,
            "archive_path": str(archive_path),
            "run_record_path": str(run_path),
        }


__all__ = ["DeliveryLifecycleProvider", "FAULT_POINTS", "LEGAL_TRANSITIONS"]
