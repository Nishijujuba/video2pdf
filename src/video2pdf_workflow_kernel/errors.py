from __future__ import annotations


class KernelError(RuntimeError):
    """Base class for classified, fail-closed Kernel errors."""

    classification = "kernel_error"
    exit_code = 70

    def __init__(self, message: str, *, data: dict | None = None) -> None:
        super().__init__(message)
        self.data = data or {}


class ContractError(KernelError):
    classification = "contract_invalid"
    exit_code = 20


class UnknownContractVersion(ContractError):
    classification = "unknown_contract_version"


class UnresolvedSchemaReference(ContractError):
    classification = "unresolved_schema_reference"


class KernelConflict(KernelError):
    classification = "identity_or_path_conflict"
    exit_code = 30


class PathBudgetError(KernelError):
    classification = "path_budget_exceeded"
    exit_code = 30


class CapabilityForbidden(KernelError):
    classification = "capability_forbidden"
    exit_code = 30


class ArtifactDrift(KernelError):
    classification = "artifact_drift"
    exit_code = 40


class ControlStoreUnavailable(KernelError):
    classification = "control_store_unavailable"
    exit_code = 50


class InitializationFault(KernelError):
    classification = "injected_initialization_fault"
    exit_code = 60

    def __init__(self, fault_point: str) -> None:
        super().__init__(
            f"injected initialization fault at {fault_point}",
            data={"fault_point": fault_point},
        )
        self.fault_point = fault_point


class CliUsageError(KernelError):
    classification = "usage_error"
    exit_code = 2
