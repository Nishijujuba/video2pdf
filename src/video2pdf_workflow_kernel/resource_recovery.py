from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Callable, Mapping

from .errors import ContractError, ControlStoreUnavailable, KernelConflict
from .models import ResourceAdmissionState
from .resource_admission import ResourceAdmission
from .utils import canonical_json_bytes


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ResourceRecovery:
    """Evidence-bearing lifecycle boundary for uncertain physical Resource Leases."""

    def __init__(
        self,
        kernel: Any,
        *,
        provider_verifiers: Mapping[str, Callable[..., str]],
        local_process_inspector: Callable[..., str | None] | None,
    ) -> None:
        self.kernel = kernel
        self.provider_verifiers = dict(provider_verifiers)
        self.local_process_inspector = local_process_inspector

    @staticmethod
    def _validate_observed_at(value: Any) -> None:
        try:
            observed_at = datetime.fromisoformat(str(value))
        except ValueError as exc:
            raise ContractError(
                "Resource resolution observed_at must be ISO 8601"
            ) from exc
        if observed_at.tzinfo is None:
            raise ContractError(
                "Resource resolution observed_at requires a timezone"
            )

    def reconcile(
        self,
        *,
        current_coordinator_session_id: str,
        lost_coordinator_session_ids: tuple[str, ...],
    ) -> dict[str, Any]:
        if not current_coordinator_session_id.strip():
            raise ContractError(
                "Resource reconcile requires a current coordinator session identity"
            )
        if any(not item.strip() for item in lost_coordinator_session_ids):
            raise ContractError(
                "Resource reconcile lost coordinator identities must be non-empty"
            )
        if tuple(sorted(set(lost_coordinator_session_ids))) != lost_coordinator_session_ids:
            raise ContractError(
                "Resource reconcile lost coordinator identities must be unique and stably sorted"
            )
        if current_coordinator_session_id in lost_coordinator_session_ids:
            raise ContractError(
                "current coordinator session cannot be declared lost"
            )
        store = self.kernel._preflight_control_store()
        audit_payloads = {
            str(row["lease_id"]): canonical_json_bytes(
                {
                    "current_coordinator_session_id": current_coordinator_session_id,
                    "lost_coordinator_session_id": str(
                        row["coordinator_session_id"]
                    ),
                    "prior_worker_id": str(row["worker_id"]),
                    "attempt_id": str(row["attempt_id"]),
                    "claim_generation": int(row["claim_generation"]),
                }
            ).decode("utf-8")
            for row in store.resource_leases_for_coordinator_sessions(
                lost_coordinator_session_ids
            )
        }
        transitioned = store.reconcile_resource_leases_for_lost_sessions(
            current_coordinator_session_id=current_coordinator_session_id,
            lost_coordinator_session_ids=lost_coordinator_session_ids,
            audit_payloads=audit_payloads,
            reconciled_at=_utc_now(),
        )
        unknown = [
            str(row["lease_id"])
            for row in store.unknown_resource_leases()
        ]
        return {
            "classification": "resource_reconciled",
            "transitioned_lease_ids": transitioned,
            "unknown_lease_ids": unknown,
            "capacity_released": False,
        }

    def _evidence_record(
        self,
        *,
        lease_id: str,
        attempt_id: str,
        expected_claim_generation: int,
        evidence: dict[str, Any],
    ) -> tuple[str, str]:
        store = self.kernel._preflight_control_store()
        row = store.resource_status_by_attempt(attempt_id)
        if row is None or row["lease_id"] is None:
            raise ControlStoreUnavailable(
                "Resource resolution has no persisted Lease identity"
            )
        if str(row["lease_id"]) != lease_id or int(
            row["claim_generation"]
        ) != expected_claim_generation:
            raise KernelConflict(
                "Resource resolution identity or Lease generation disagrees"
            )
        evidence_class = evidence.get("evidence_class")
        if evidence_class == "explicit_human_resolution":
            required = {
                "evidence_class",
                "declared_outcome",
                "reason",
                "observed_termination_basis",
                "coordinator_identity",
                "observed_at",
            }
            if set(evidence) != required:
                raise ContractError(
                    "explicit human Resource resolution has an invalid field set"
                )
            evidence_body = {
                "reason": evidence["reason"],
                "observed_termination_basis": evidence[
                    "observed_termination_basis"
                ],
                "coordinator_identity": evidence["coordinator_identity"],
            }
        elif evidence_class == "provider_terminal_result":
            required = {
                "evidence_class",
                "provider",
                "terminal_result_id",
                "declared_outcome",
                "observed_at",
            }
            if set(evidence) != required:
                raise ContractError(
                    "provider Resource resolution has an invalid field set"
                )
            provider = evidence["provider"]
            terminal_result_id = evidence["terminal_result_id"]
            if (
                not isinstance(provider, str)
                or not provider.strip()
                or not isinstance(terminal_result_id, str)
                or not terminal_result_id.strip()
            ):
                raise ContractError(
                    "provider Resource resolution identity is invalid"
                )
            verifier = self.provider_verifiers.get(provider)
            if verifier is None:
                raise ContractError(
                    "provider Resource resolution has no trusted verifier"
                )
            try:
                proof_reference = verifier(
                    provider=provider,
                    terminal_result_id=terminal_result_id,
                    lease_id=lease_id,
                    attempt_id=attempt_id,
                    launch_token=str(row["launch_token"]),
                    declared_outcome=evidence["declared_outcome"],
                )
            except Exception as exc:
                raise ContractError(
                    "provider Resource resolution verifier failed closed"
                ) from exc
            if not isinstance(proof_reference, str) or not proof_reference.strip():
                raise ContractError(
                    "provider Resource resolution verifier returned no persistent proof"
                )
            evidence_body = {
                "provider": provider,
                "terminal_result_id": terminal_result_id,
                "verification_proof_reference": proof_reference,
            }
        elif evidence_class == "local_process_terminated":
            required = {
                "evidence_class",
                "declared_outcome",
                "pid",
                "process_creation_identity",
                "launch_token",
                "observed_at",
            }
            if set(evidence) != required:
                raise ContractError(
                    "local process Resource resolution has an invalid field set"
                )
            launch_identity_json = row["launch_execution_identity_json"]
            if launch_identity_json is None:
                raise KernelConflict(
                    "Resource Lease lacks launch-time process identity evidence"
                )
            try:
                launch_identity = json.loads(str(launch_identity_json))
            except ValueError as exc:
                raise ControlStoreUnavailable(
                    "Resource Lease launch-time process identity is invalid"
                ) from exc
            if (
                launch_identity.get("pid") != evidence["pid"]
                or launch_identity.get("process_creation_identity")
                != evidence["process_creation_identity"]
                or launch_identity.get("launch_token") != evidence["launch_token"]
            ):
                raise KernelConflict(
                    "local process resolution disagrees with launch-time identity"
                )
            if self.local_process_inspector is None:
                raise ContractError(
                    "local process Resource resolution has no trusted inspector"
                )
            try:
                proof_reference = self.local_process_inspector(
                    pid=int(launch_identity["pid"]),
                    process_creation_identity=str(
                        launch_identity["process_creation_identity"]
                    ),
                    launch_token=str(launch_identity["launch_token"]),
                    lease_id=lease_id,
                    attempt_id=attempt_id,
                )
            except Exception as exc:
                raise ContractError(
                    "local process Resource resolution inspector failed closed"
                ) from exc
            if proof_reference is None:
                raise KernelConflict(
                    "trusted local process inspector did not prove the matching process absent"
                )
            if not isinstance(proof_reference, str) or not proof_reference.strip():
                raise ContractError(
                    "local process inspector returned no persistent proof"
                )
            evidence_body = {
                "pid": evidence["pid"],
                "process_creation_identity": evidence[
                    "process_creation_identity"
                ],
                "launch_token": evidence["launch_token"],
                "inspection_proof_reference": proof_reference,
            }
        else:
            raise ContractError("Resource resolution evidence class is unsupported")
        self._validate_observed_at(evidence["observed_at"])
        record = {
            "schema_name": "resource-lease-resolution-evidence",
            "schema_version": "1.0.0",
            "kernel_version": "2.0.0",
            "lease_id": lease_id,
            "attempt_id": attempt_id,
            "claim_generation": expected_claim_generation,
            "evidence_class": evidence_class,
            "declared_outcome": evidence["declared_outcome"],
            "observed_at": evidence["observed_at"],
            "evidence": evidence_body,
        }
        self.kernel.contracts.validate(
            "resource-lease-resolution-evidence", record
        )
        canonical = canonical_json_bytes(record).decode("utf-8")
        return canonical, hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _resolution_request_from_record(
        record: dict[str, object],
    ) -> dict[str, object]:
        evidence_class = str(record["evidence_class"])
        evidence = record["evidence"]
        if not isinstance(evidence, dict):
            raise ControlStoreUnavailable(
                "persisted Resource resolution evidence body is invalid"
            )
        request: dict[str, object] = {
            "evidence_class": evidence_class,
            "declared_outcome": record["declared_outcome"],
            "observed_at": record["observed_at"],
        }
        if evidence_class == "provider_terminal_result":
            request.update(
                {
                    "provider": evidence["provider"],
                    "terminal_result_id": evidence["terminal_result_id"],
                }
            )
        elif evidence_class == "local_process_terminated":
            request.update(
                {
                    "pid": evidence["pid"],
                    "process_creation_identity": evidence[
                        "process_creation_identity"
                    ],
                    "launch_token": evidence["launch_token"],
                }
            )
        elif evidence_class == "explicit_human_resolution":
            request.update(
                {
                    "reason": evidence["reason"],
                    "observed_termination_basis": evidence[
                        "observed_termination_basis"
                    ],
                    "coordinator_identity": evidence["coordinator_identity"],
                }
            )
        else:
            raise ControlStoreUnavailable(
                "persisted Resource resolution evidence class is unsupported"
            )
        return request

    def _persisted_resolution_replay(
        self,
        *,
        store: Any,
        row: Any,
        lease_id: str,
        attempt_id: str,
        expected_claim_generation: int,
        evidence: dict[str, Any],
    ) -> tuple[str, str] | None:
        if row is None or row["lease_id"] is None:
            raise ControlStoreUnavailable(
                "Resource resolution has no persisted Lease identity"
            )
        if str(row["lease_id"]) != lease_id or int(
            row["claim_generation"]
        ) != expected_claim_generation:
            raise KernelConflict(
                "Resource resolution identity or Lease generation disagrees"
            )
        if str(row["lease_state"]) != "resolved":
            return None
        terminal_json = row["terminal_evidence_json"]
        terminal_sha256 = row["terminal_evidence_sha256"]
        record = store._validate_resource_terminal_evidence(
            row,
            terminal_json,
            terminal_sha256,
            allowed_evidence_classes={
                "provider_terminal_result",
                "local_process_terminated",
                "explicit_human_resolution",
            },
        )
        persisted_request = self._resolution_request_from_record(record)
        try:
            replay_matches = canonical_json_bytes(evidence) == canonical_json_bytes(
                persisted_request
            )
        except (TypeError, ValueError) as exc:
            raise ContractError(
                "Resource resolution evidence must be JSON serializable"
            ) from exc
        if not replay_matches:
            raise KernelConflict(
                "Resource resolution replay conflicts with terminal evidence"
            )
        return str(terminal_json), str(terminal_sha256)

    def resolve(
        self,
        lease_id: str,
        attempt_id: str,
        expected_claim_generation: int,
        *,
        resolution_evidence: dict[str, Any],
    ) -> ResourceAdmissionState:
        store = self.kernel._preflight_control_store()
        persisted_replay = self._persisted_resolution_replay(
            store=store,
            row=store.resource_status_by_attempt(attempt_id),
            lease_id=lease_id,
            attempt_id=attempt_id,
            expected_claim_generation=expected_claim_generation,
            evidence=resolution_evidence,
        )
        if persisted_replay is None:
            evidence_json, evidence_sha256 = self._evidence_record(
                lease_id=lease_id,
                attempt_id=attempt_id,
                expected_claim_generation=expected_claim_generation,
                evidence=resolution_evidence,
            )
        else:
            evidence_json, evidence_sha256 = persisted_replay
        admission = ResourceAdmission(self.kernel)
        row = store.resolve_unknown_resource_lease(
            lease_id=lease_id,
            attempt_id=attempt_id,
            expected_claim_generation=expected_claim_generation,
            resolution_evidence_json=evidence_json,
            resolution_evidence_sha256=evidence_sha256,
            resolved_at=_utc_now(),
            resource_scheduler=admission._schedule,
        )
        return admission._state(row)
