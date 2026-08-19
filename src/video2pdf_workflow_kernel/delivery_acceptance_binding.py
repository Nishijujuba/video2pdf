from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
from typing import Any

from .acceptance_v2 import AcceptanceV2Provider, CONTROL_DB_NAME
from .control_store import ControlStore
from .delivery_lifecycle import (
    DeliveryLifecycleProvider,
    _run_locked,
    _sha256_json,
)
from .errors import ArtifactDrift, CliUsageError, ContractError, KernelConflict
from .utils import (
    canonical_json_bytes,
    normalized_physical_path,
    read_json,
    require_safe_path_segment,
    sha256_file,
    write_json_atomic,
)


class DeliveryAcceptanceBindingProvider(DeliveryLifecycleProvider):
    """Bind one provider-current Acceptance decision as a ready successor."""

    @staticmethod
    def _read_canonical_report(
        report_path: Path,
        *,
        error_code: str = "delivery_acceptance_report_malformed",
    ) -> dict[str, Any]:
        try:
            report = read_json(report_path)
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as exc:
            raise ContractError(
                "Delivery Acceptance canonical report is malformed",
                data={
                    "first_failing_gate": "acceptance_provider_authority",
                    "error_code": error_code,
                },
            ) from exc
        if not isinstance(report, dict):
            raise ContractError(
                "Delivery Acceptance canonical report is malformed",
                data={
                    "first_failing_gate": "acceptance_provider_authority",
                    "error_code": error_code,
                },
            )
        return report

    @classmethod
    def _require_report_snapshot(
        cls,
        *,
        report_path: Path,
        expected_file_sha256: str,
        expected_provider_sha256: str,
    ) -> None:
        if (
            not report_path.is_file()
            or sha256_file(report_path) != expected_file_sha256
        ):
            raise ContractError(
                "Acceptance Report v2 changed during Delivery Acceptance binding",
                data={
                    "first_failing_gate": "acceptance_provider_authority",
                    "error_code": "delivery_acceptance_report_drifted",
                },
            )
        report = cls._read_canonical_report(
            report_path, error_code="delivery_acceptance_report_drifted"
        )
        if report.get("report_sha256") != expected_provider_sha256:
            raise ContractError(
                "Acceptance Report v2 provider identity changed during binding",
                data={
                    "first_failing_gate": "acceptance_provider_authority",
                    "error_code": "delivery_acceptance_report_drifted",
                },
            )

    @classmethod
    def _commit_run_record_with_report_fence(
        cls,
        store: ControlStore,
        *,
        intent_id: str,
        run_path: Path,
        replacement: dict[str, Any],
        report_path: Path,
        report_file_sha256: str,
        provider_report_sha256: str,
    ) -> None:
        acceptance_control_path = report_path.parent / CONTROL_DB_NAME
        with sqlite3.connect(
            acceptance_control_path, timeout=5.0, isolation_level=None
        ) as acceptance_control, sqlite3.connect(
            store.path, timeout=5.0, isolation_level=None
        ) as connection:
            acceptance_control.execute("BEGIN IMMEDIATE")
            connection.execute("BEGIN IMMEDIATE")
            state = connection.execute(
                "SELECT state FROM delivery_lifecycle_intents WHERE intent_id=?",
                (intent_id,),
            ).fetchone()
            if state is None or state[0] != "FILES_PUBLISHED":
                raise KernelConflict(
                    "Delivery Acceptance writer lost its Run commit fence",
                    data={
                        "first_failing_gate": "lifecycle_fencing",
                        "error_code": "delivery_writer_fence_lost",
                    },
                )
            cls._require_report_snapshot(
                report_path=report_path,
                expected_file_sha256=report_file_sha256,
                expected_provider_sha256=provider_report_sha256,
            )
            write_json_atomic(run_path, replacement)
            changed = connection.execute(
                "UPDATE delivery_lifecycle_intents SET state='RECORD_COMMITTED' "
                "WHERE intent_id=? AND state='FILES_PUBLISHED'",
                (intent_id,),
            ).rowcount
            if changed != 1:
                raise KernelConflict(
                    "Delivery Acceptance Run commit lost its state fence",
                    data={
                        "first_failing_gate": "lifecycle_fencing",
                        "error_code": "delivery_writer_fence_lost",
                    },
                )
            connection.execute("COMMIT")
            acceptance_control.execute("COMMIT")

    @staticmethod
    def _abort_published_successor(
        store: ControlStore,
        *,
        intent_id: str,
        journal_path: Path,
        preservation_states: list[dict[str, Any]],
    ) -> None:
        DeliveryAcceptanceBindingProvider._restore_projection_states(
            journal_path, preservation_states
        )
        with sqlite3.connect(store.path, isolation_level=None) as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                "UPDATE delivery_lifecycle_intents SET state='ABORTED' "
                "WHERE intent_id=? AND state='FILES_PUBLISHED'",
                (intent_id,),
            ).rowcount
            if changed != 1:
                raise KernelConflict(
                    "Delivery Acceptance rollback lost its intent fence",
                    data={
                        "first_failing_gate": "lifecycle_fencing",
                        "error_code": "delivery_writer_fence_lost",
                    },
                )
            connection.execute(
                "UPDATE projection_publication_slots SET state='RELEASED' "
                "WHERE intent_id=? AND state='HELD'",
                (intent_id,),
            )
            connection.execute("COMMIT")

    @staticmethod
    def _identity(
        *,
        run_id: str,
        session_id: str,
        expected_run_revision: int,
        expected_ownership_generation: int,
        report_path: Path,
        report_file_sha256: str,
        report_sha256: str,
        bound_at: str,
    ) -> dict[str, Any]:
        return {
            "operation": "delivery_acceptance_bind",
            "run_id": run_id,
            "session_id": session_id,
            "expected_run_revision": expected_run_revision,
            "expected_ownership_generation": expected_ownership_generation,
            "acceptance_report_path": str(report_path.resolve()),
            "acceptance_report_file_sha256": report_file_sha256,
            "acceptance_report_sha256": report_sha256,
            "bound_at": bound_at,
        }

    @staticmethod
    def _committed_replay(
        *,
        store: ControlStore,
        intent_id: str,
        intent_identity: str,
        run_path: Path,
        run_record: dict[str, Any],
        report_path: Path,
        report_file_sha256: str,
    ) -> dict[str, Any] | None:
        with sqlite3.connect(
            f"file:{store.path.as_posix()}?mode=ro", uri=True
        ) as connection:
            connection.row_factory = sqlite3.Row
            intent = connection.execute(
                "SELECT * FROM delivery_lifecycle_intents WHERE intent_id=?",
                (intent_id,),
            ).fetchone()
            if intent is None:
                return None
            slots = connection.execute(
                "SELECT * FROM projection_publication_slots WHERE intent_id=? "
                "ORDER BY normalized_path",
                (intent_id,),
            ).fetchall()
        try:
            replacement = json.loads(intent["replacement_run_record_json"])
        except (json.JSONDecodeError, TypeError) as exc:
            raise KernelConflict(
                "Delivery Acceptance replay authority is malformed",
                data={
                    "first_failing_gate": "lifecycle_fencing",
                    "error_code": "delivery_acceptance_replay_authority_invalid",
                },
            ) from exc
        if intent["state"] == "ABORTED":
            if not (
                intent["intent_identity"] == intent_identity
                and intent["operation"] == "transition"
                and intent["prior_stage"] == "ready_for_delivery"
                and intent["target_stage"] == "ready_for_delivery"
                and intent["replacement_run_record_sha256"]
                == _sha256_json(replacement)
                and len(slots) == 4
                and all(row["state"] == "RELEASED" for row in slots)
            ):
                raise KernelConflict(
                    "Delivery Acceptance aborted replay authority is invalid",
                    data={
                        "first_failing_gate": "lifecycle_fencing",
                        "error_code": "delivery_acceptance_replay_authority_invalid",
                    },
                )
            return None
        if not (
            intent["intent_identity"] == intent_identity
            and intent["state"] == "COMMITTED"
            and intent["operation"] == "transition"
            and intent["prior_stage"] == "ready_for_delivery"
            and intent["target_stage"] == "ready_for_delivery"
            and intent["replacement_run_record_sha256"] == sha256_file(run_path)
            and replacement == run_record
            and store.current_run_record_sha(run_record["run_id"])
            == sha256_file(run_path)
            and len(slots) == 4
            and all(row["state"] == "RELEASED" for row in slots)
        ):
            raise KernelConflict(
                "Delivery Acceptance replay conflicts with committed authority",
                data={
                    "first_failing_gate": "lifecycle_fencing",
                    "error_code": "delivery_acceptance_bind_conflict",
                },
            )
        video_binding = run_record["delivery"]["projections"]["video_target"]
        raw_video_path = Path(video_binding["path"])
        video_path = (
            run_path.parents[1] / raw_video_path
            if not raw_video_path.is_absolute()
            else raw_video_path
        ).resolve()
        video = read_json(video_path)
        if video.get("artifacts", {}).get("acceptance_report") != {
            "path": str(report_path.resolve()),
            "sha256": report_file_sha256,
        }:
            raise KernelConflict(
                "Delivery Acceptance replay target differs from its report",
                data={
                    "first_failing_gate": "acceptance_binding",
                    "error_code": "delivery_acceptance_replay_report_conflict",
                },
            )
        session_path = Path(
            run_record["delivery"]["projections"]["session_target"]["path"]
        ).resolve()
        task_index_path = Path(
            run_record["delivery"]["projections"]["task_index"]["path"]
        ).resolve()
        current_paths = (video_path, session_path, task_index_path, run_path)
        proposed_by_path = {
            row["normalized_path"]: row["proposed_sha256"] for row in slots
        }
        if (
            set(proposed_by_path)
            != {normalized_physical_path(path) for path in current_paths}
            or any(
                not path.is_file()
                or proposed_by_path[normalized_physical_path(path)]
                != sha256_file(path)
                for path in current_paths
            )
        ):
            raise KernelConflict(
                "Delivery Acceptance replay projections are stale",
                data={
                    "first_failing_gate": "projection_currentness",
                    "error_code": "delivery_acceptance_replay_projection_stale",
                },
            )
        return {
            "run_id": run_record["run_id"],
            "intent_id": intent_id,
            "stage": "ready_for_delivery",
            "run_revision": run_record["coordination_revision"],
            "ownership_generation": run_record["delivery"]["ownership"][
                "generation"
            ],
            "run_record_path": str(run_path),
            "acceptance_report": {
                "path": str(report_path.resolve()),
                "sha256": report_file_sha256,
            },
            "idempotent": True,
        }

    @staticmethod
    def _require_provider_current_report(
        *,
        repository_root: Path,
        run_record: dict[str, Any],
        report_path: Path,
        expected_run_revision: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        canonical_path = (
            Path(run_record["output_path"])
            / "review"
            / "acceptance"
            / "acceptance_report.json"
        ).resolve()
        if report_path.resolve() != canonical_path or not canonical_path.is_file():
            raise ContractError(
                "Delivery Acceptance requires the canonical Acceptance Report v2",
                data={
                    "first_failing_gate": "acceptance_provider_authority",
                    "error_code": "delivery_acceptance_report_path_invalid",
                },
            )
        report = DeliveryAcceptanceBindingProvider._read_canonical_report(
            canonical_path
        )
        run_binding = report.get("run_binding")
        if not isinstance(run_binding, dict) or (
            run_binding.get("run_id") != run_record["run_id"]
            or run_binding.get("coordination_revision") != expected_run_revision
        ):
            raise ContractError(
                "Acceptance Report v2 is bound to another Run authority",
                data={
                    "first_failing_gate": "acceptance_provider_authority",
                    "error_code": "delivery_acceptance_report_run_binding_stale",
                },
            )
        eligibility = AcceptanceV2Provider(repository_root).guard_eligibility(
            workspace_root=canonical_path.parent
        )
        if not (
            eligibility.get("eligible") is True
            and eligibility.get("delivery_authority") is True
            and eligibility.get("report_sha256") == report.get("report_sha256")
        ):
            raise ContractError(
                "Acceptance Report v2 lacks provider-current delivery authority",
                data={
                    "first_failing_gate": "acceptance_provider_authority",
                    "error_code": "delivery_acceptance_report_provider_stale",
                },
            )
        return report, eligibility

    @_run_locked
    def bind(
        self,
        *,
        run_dir: Path,
        session_id: str,
        acceptance_report: Path,
        expected_run_revision: int,
        expected_ownership_generation: int,
        bound_at: str,
    ) -> dict[str, Any]:
        require_safe_path_segment(
            session_id,
            purpose="Delivery Acceptance session identity",
            error_type=CliUsageError,
        )
        run_path, run_record = self._load_run(run_dir)
        report_path = acceptance_report.resolve()
        canonical_report_path = (
            Path(run_record["output_path"])
            / "review"
            / "acceptance"
            / "acceptance_report.json"
        ).resolve()
        if report_path != canonical_report_path or not canonical_report_path.is_file():
            raise ContractError(
                "Delivery Acceptance requires the canonical Acceptance Report v2",
                data={
                    "first_failing_gate": "acceptance_provider_authority",
                    "error_code": "delivery_acceptance_report_path_invalid",
                },
            )
        report = self._read_canonical_report(canonical_report_path)
        report_path = canonical_report_path
        report_file_sha256 = sha256_file(report_path)
        provider_report_sha256 = report.get("report_sha256")
        if not isinstance(provider_report_sha256, str) or not provider_report_sha256:
            raise ContractError(
                "Delivery Acceptance canonical report lacks provider identity",
                data={
                    "first_failing_gate": "acceptance_provider_authority",
                    "error_code": "delivery_acceptance_report_malformed",
                },
            )
        identity = self._identity(
            run_id=run_record["run_id"],
            session_id=session_id,
            expected_run_revision=expected_run_revision,
            expected_ownership_generation=expected_ownership_generation,
            report_path=report_path,
            report_file_sha256=report_file_sha256,
            report_sha256=provider_report_sha256,
            bound_at=bound_at,
        )
        intent_id = _sha256_json(identity)
        intent_identity = _sha256_json(identity)
        store = ControlStore(run_dir.parent, self.contracts)
        replay = self._committed_replay(
            store=store,
            intent_id=intent_id,
            intent_identity=intent_identity,
            run_path=run_path,
            run_record=run_record,
            report_path=report_path,
            report_file_sha256=report_file_sha256,
        )
        if replay is not None:
            return replay

        delivery = run_record["delivery"]
        ownership = delivery["ownership"]
        if (
            delivery["stage"] != "ready_for_delivery"
            or ownership["session_id"] != session_id
            or run_record["coordination_revision"] != expected_run_revision
            or ownership["generation"] != expected_ownership_generation
        ):
            raise KernelConflict(
                "Delivery Acceptance CAS identity is stale",
                data={
                    "first_failing_gate": "lifecycle_fencing",
                    "error_code": "delivery_acceptance_bind_fence_lost",
                },
            )

        report, eligibility = self._require_provider_current_report(
            repository_root=self.repository_root,
            run_record=run_record,
            report_path=report_path,
            expected_run_revision=expected_run_revision,
        )
        projections = delivery["projections"]
        video_path = self._validate_binding(
            projections["video_target"],
            label="video target",
            run_dir=run_dir,
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
        project_root = self._project_root(run_dir)
        if any(
            not path.is_relative_to(project_root)
            for path in (session_path, task_index_path)
        ):
            raise ContractError(
                "Delivery Acceptance projection escapes the project root",
                data={
                    "first_failing_gate": "path_boundary",
                    "error_code": "delivery_projection_path_escape",
                },
            )
        artifacts = old_video.get("artifacts")
        if not isinstance(artifacts, dict) or (
            artifacts.get("acceptance_report") is not None
            or artifacts.get("delivery_guard_report") is not None
        ):
            raise KernelConflict(
                "Delivery Acceptance target decision slots are already occupied",
                data={
                    "first_failing_gate": "acceptance_binding",
                    "error_code": "delivery_acceptance_slot_conflict",
                },
            )

        run_revision = expected_run_revision + 1
        acceptance_binding = {
            "path": str(report_path),
            "sha256": report_file_sha256,
        }
        video = json.loads(json.dumps(old_video))
        video.update(
            {
                "projection_revision": old_video["projection_revision"] + 1,
                "run_revision": run_revision,
                "lifecycle_intent_id": intent_id,
            }
        )
        video["artifacts"]["acceptance_report"] = acceptance_binding
        video_sha256 = _sha256_json(video)

        session = json.loads(json.dumps(old_session))
        session.update(
            {
                "projection_revision": old_session["projection_revision"] + 1,
                "run_revision": run_revision,
                "lifecycle_intent_id": intent_id,
                "video_target": {
                    "path": str(video_path),
                    "projection_revision": video["projection_revision"],
                    "sha256": video_sha256,
                },
            }
        )
        session_sha256 = _sha256_json(session)

        old_index = read_json(task_index_path)
        index = json.loads(json.dumps(old_index))
        index["projection_revision"] = old_index["projection_revision"] + 1
        matching = [
            item
            for item in index["entries"]
            if item["run_id"] == run_record["run_id"]
        ]
        if len(matching) != 1:
            raise ContractError(
                "Delivery task index Run identity is invalid",
                data={
                    "first_failing_gate": "task_index",
                    "error_code": "delivery_task_index_identity_invalid",
                },
            )
        matching[0].update(
            {
                "run_revision": run_revision,
                "lifecycle_intent_id": intent_id,
                "video_target": {
                    "path": str(video_path),
                    "projection_revision": video["projection_revision"],
                    "sha256": video_sha256,
                },
                "session_target": {
                    "path": str(session_path),
                    "projection_revision": session["projection_revision"],
                    "sha256": session_sha256,
                },
            }
        )
        index["entries"] = sorted(index["entries"], key=lambda item: item["run_id"])
        index_sha256 = _sha256_json(index)

        replacement = json.loads(json.dumps(run_record))
        replacement.update(
            {
                "coordination_revision": run_revision,
                "last_mutation_intent_id": intent_id,
            }
        )
        replacement["delivery"]["projections"].update(
            {
                "video_target": {
                    "path": "review/acceptance/delivery_target.json",
                    "projection_revision": video["projection_revision"],
                    "sha256": video_sha256,
                },
                "session_target": {
                    "path": str(session_path),
                    "projection_revision": session["projection_revision"],
                    "sha256": session_sha256,
                },
                "task_index": {
                    "path": str(task_index_path),
                    "projection_revision": index["projection_revision"],
                    "sha256": index_sha256,
                },
            }
        )
        self.contracts.validate("kernel-delivery-target", video)
        self.contracts.validate("kernel-session-delivery-target", session)
        self.contracts.validate("kernel-delivery-task-index", index)
        self.contracts.validate("run-record", replacement)
        replacement_sha256 = _sha256_json(replacement)

        preservation_root = (
            run_dir / "待删除" / "delivery-lifecycle" / intent_id / "prior"
        )
        preservation_root.mkdir(parents=True, exist_ok=True)
        preservation_states = self._preserve_projection_states(
            preservation_root,
            (video_path, session_path, task_index_path),
        )
        shutil.copy2(run_path, preservation_root / f"03-{run_path.name}")
        journal_path = preservation_root.parent / "intent.json"
        journal = {
            "intent_id": intent_id,
            "identity": identity,
            "run_path": str(run_path),
            "prior_run_sha256": sha256_file(run_path),
            "replacement_run_sha256": replacement_sha256,
            "preservations": preservation_states,
            "projections": [
                {"path": str(video_path), "value": video, "sha256": video_sha256},
                {
                    "path": str(session_path),
                    "value": session,
                    "sha256": session_sha256,
                },
                {
                    "path": str(task_index_path),
                    "value": index,
                    "sha256": index_sha256,
                },
            ],
            "acceptance_report": {
                **acceptance_binding,
                "provider_report_sha256": report["report_sha256"],
                "provider_eligibility": {
                    "eligible": eligibility["eligible"],
                    "delivery_authority": eligibility["delivery_authority"],
                },
            },
        }
        write_json_atomic(journal_path, journal)

        proposed = {
            str(video_path): video_sha256,
            str(session_path): session_sha256,
            str(task_index_path): index_sha256,
            str(run_path): replacement_sha256,
        }
        predecessors = {
            str(path): sha256_file(path)
            for path in (video_path, session_path, task_index_path, run_path)
        }
        acceptance_control_path = report_path.parent / CONTROL_DB_NAME
        if not acceptance_control_path.is_file():
            raise ContractError(
                "Acceptance Report v2 control authority is missing",
                data={
                    "first_failing_gate": "acceptance_provider_authority",
                    "error_code": "delivery_acceptance_report_provider_stale",
                },
            )
        with sqlite3.connect(
            acceptance_control_path, timeout=5.0, isolation_level=None
        ) as acceptance_control, sqlite3.connect(
            store.path, timeout=5.0, isolation_level=None
        ) as connection:
            acceptance_control.execute("BEGIN IMMEDIATE")
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE")
            self._require_report_snapshot(
                report_path=report_path,
                expected_file_sha256=report_file_sha256,
                expected_provider_sha256=provider_report_sha256,
            )
            self._require_run_promotion_authority(
                store,
                connection,
                run_record=run_record,
                prior_run_sha256=predecessors[str(run_path)],
            )
            if any(sha256_file(Path(path)) != digest for path, digest in predecessors.items()):
                connection.execute("ROLLBACK")
                raise ArtifactDrift(
                    "Delivery Acceptance projection changed before slot acquisition",
                    data={
                        "first_failing_gate": "projection_fencing",
                        "error_code": "delivery_acceptance_projection_fence_lost",
                    },
                )
            existing = connection.execute(
                "SELECT state,intent_identity FROM delivery_lifecycle_intents "
                "WHERE intent_id=?",
                (intent_id,),
            ).fetchone()
            if existing is None:
                connection.execute(
                    "INSERT INTO delivery_lifecycle_intents("
                    "intent_id,run_id,session_id,expected_run_revision,"
                    "expected_ownership_generation,prior_stage,target_stage,operation,"
                    "prior_run_record_sha256,replacement_run_record_sha256,"
                    "replacement_run_record_json,state,intent_identity) "
                    "VALUES(?,?,?,?,?,?,?,'transition',?,?,?,'PREPARED',?)",
                    (
                        intent_id,
                        run_record["run_id"],
                        session_id,
                        expected_run_revision,
                        expected_ownership_generation,
                        "ready_for_delivery",
                        "ready_for_delivery",
                        predecessors[str(run_path)],
                        replacement_sha256,
                        canonical_json_bytes(replacement).decode("utf-8"),
                        intent_identity,
                    ),
                )
                for path in sorted(
                    (video_path, session_path, task_index_path, run_path),
                    key=normalized_physical_path,
                ):
                    normalized = normalized_physical_path(path)
                    slot_id = hashlib.sha256(
                        (intent_id + "\0" + normalized).encode("utf-8")
                    ).hexdigest()
                    connection.execute(
                        "INSERT INTO projection_publication_slots("
                        "slot_id,intent_id,normalized_path,expected_state,expected_sha256,"
                        "proposed_state,proposed_sha256,state,slot_identity) "
                        "VALUES(?,?,?,'present',?,'present',?,'HELD',?)",
                        (
                            slot_id,
                            intent_id,
                            normalized,
                            predecessors[str(path)],
                            proposed[str(path)],
                            slot_id,
                        ),
                    )
            else:
                if not (
                    existing["state"] == "ABORTED"
                    and existing["intent_identity"] == intent_identity
                ):
                    raise KernelConflict(
                        "Delivery Acceptance retry conflicts with intent authority",
                        data={
                            "first_failing_gate": "lifecycle_fencing",
                            "error_code": "delivery_acceptance_bind_conflict",
                        },
                    )
                retry_slots = connection.execute(
                    "SELECT normalized_path,expected_sha256,proposed_sha256,state "
                    "FROM projection_publication_slots WHERE intent_id=?",
                    (intent_id,),
                ).fetchall()
                expected_retry_slots = {
                    normalized_physical_path(path): (
                        predecessors[str(path)],
                        proposed[str(path)],
                    )
                    for path in (video_path, session_path, task_index_path, run_path)
                }
                if (
                    len(retry_slots) != 4
                    or {
                        row["normalized_path"]: (
                            row["expected_sha256"],
                            row["proposed_sha256"],
                        )
                        for row in retry_slots
                    }
                    != expected_retry_slots
                    or any(row["state"] != "RELEASED" for row in retry_slots)
                ):
                    raise KernelConflict(
                        "Delivery Acceptance retry slot authority is invalid",
                        data={
                            "first_failing_gate": "lifecycle_fencing",
                            "error_code": "delivery_acceptance_replay_authority_invalid",
                        },
                    )
                changed = connection.execute(
                    "UPDATE delivery_lifecycle_intents SET state='PREPARED' "
                    "WHERE intent_id=? AND state='ABORTED'",
                    (intent_id,),
                ).rowcount
                slots_changed = connection.execute(
                    "UPDATE projection_publication_slots SET state='HELD' "
                    "WHERE intent_id=? AND state='RELEASED'",
                    (intent_id,),
                ).rowcount
                if changed != 1 or slots_changed != 4:
                    raise KernelConflict(
                        "Delivery Acceptance retry lost its aborted intent fence",
                        data={
                            "first_failing_gate": "lifecycle_fencing",
                            "error_code": "delivery_writer_fence_lost",
                        },
                    )
            connection.execute("COMMIT")
            acceptance_control.execute("COMMIT")

        publications = (
            (video_path, video),
            (session_path, session),
            (task_index_path, index),
        )
        for path, value in sorted(
            publications, key=lambda item: normalized_physical_path(item[0])
        ):
            write_json_atomic(path, value)
        self._advance_intent_state(
            store,
            intent_id=intent_id,
            expected_state="PREPARED",
            new_state="FILES_PUBLISHED",
        )
        try:
            self._commit_run_record_with_report_fence(
                store,
                intent_id=intent_id,
                run_path=run_path,
                replacement=replacement,
                report_path=report_path,
                report_file_sha256=report_file_sha256,
                provider_report_sha256=provider_report_sha256,
            )
        except ContractError:
            self._abort_published_successor(
                store,
                intent_id=intent_id,
                journal_path=journal_path,
                preservation_states=preservation_states,
            )
            raise
        self._advance_intent_state(
            store,
            intent_id=intent_id,
            expected_state="RECORD_COMMITTED",
            new_state="COMMITTED",
            release_slots=True,
        )
        return {
            "run_id": run_record["run_id"],
            "intent_id": intent_id,
            "stage": "ready_for_delivery",
            "run_revision": run_revision,
            "ownership_generation": expected_ownership_generation,
            "run_record_path": str(run_path),
            "acceptance_report": acceptance_binding,
            "acceptance_report_sha256": report["report_sha256"],
            "idempotent": False,
        }
