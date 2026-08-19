from __future__ import annotations

from copy import deepcopy


BATCH_AUTHORITY = {
    "authority_path": "D:/control/active-batch-cutover.json",
    "authority_sha256": "a" * 64,
    "exit_evidence_sha256": "b" * 64,
    "generation": 1,
    "publication_commit": "c" * 40,
    "global_gate_binding": {
        "authority_path": "D:/control/active-global-gate.json",
        "authority_sha256": "d" * 64,
        "generation": 1,
    },
    "platform_authority_bindings": {},
    "current": True,
}

BATCH_AUTHORITY_BINDING = {
    key: BATCH_AUTHORITY[key]
    for key in (
        "authority_path",
        "authority_sha256",
        "exit_evidence_sha256",
        "generation",
        "publication_commit",
    )
}


class CurrentBatchAuthorityPublisher:
    def __init__(self, *, global_gate_binding=None):
        self.global_gate_binding = global_gate_binding

    def require_current(self, *, control_store_root):
        del control_store_root
        authority = deepcopy(BATCH_AUTHORITY)
        if self.global_gate_binding is not None:
            authority["global_gate_binding"] = deepcopy(self.global_gate_binding)
        return authority
