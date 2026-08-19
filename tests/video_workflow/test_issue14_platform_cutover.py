from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sqlite3
import sys
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tests.video_workflow._test_run import new_case_dir
from tests.video_workflow.test_issue13_delivery_lifecycle import (
    _acceptance_report,
    _guard_report,
)
from scripts import issue14_exit_evidence_contract as issue14_contract
from scripts import issue43_exit_evidence_contract as global_gate_contract
from video2pdf_workflow_kernel import cli as kernel_cli
from video2pdf_workflow_kernel.errors import KernelError
import video2pdf_workflow_kernel.platform_kernel as platform_kernel_module


def _run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    command = arguments[0] if arguments else "unknown"
    with patch.object(
        platform_kernel_module, "_require_formal_exit_evidence", return_value=None
    ):
        try:
            parsed = kernel_cli._parser().parse_args(list(arguments))
            envelope = kernel_cli._execute(parsed, PROJECT_ROOT)
            returncode = 0
        except KernelError as exc:
            envelope = kernel_cli._error(command, exc)
            returncode = exc.exit_code
    return subprocess.CompletedProcess(
        list(arguments),
        returncode,
        json.dumps(envelope, ensure_ascii=False, sort_keys=True) + "\n",
        "",
    )


class Issue14PlatformCutoverTests(unittest.TestCase):
    @staticmethod
    def _write_stub_global_gate(control_store_root: Path) -> dict[str, object]:
        evidence = control_store_root / "global-gate-exit-evidence.json"
        evidence.write_text("{}\n", encoding="utf-8")
        evidence_sha = hashlib.sha256(evidence.read_bytes()).hexdigest()
        authority_path = control_store_root / "active_global_gate.json"
        authority = {
            "schema_name": "global-gate-authority",
            "schema_version": "1.0.0",
            "generation": 1,
            "active_global_gate": "acceptance_report_v2",
            "acceptance_report_schema_version": "2.0.0",
            "legacy_acceptance_authority": "legacy_acceptance_input_set_v1",
            "platform_kernel_authority": "unchanged",
            "exit_evidence_path": str(evidence),
            "exit_evidence_sha256": evidence_sha,
            "activated_at": "2026-08-12T00:00:00Z",
        }
        authority["authority_sha256"] = hashlib.sha256(
            (
                json.dumps(
                    authority,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("utf-8")
        ).hexdigest()
        authority_path.write_text(
            json.dumps(authority, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        file_sha = hashlib.sha256(authority_path.read_bytes()).hexdigest()
        with sqlite3.connect(control_store_root / "global-gate-control.sqlite3") as db:
            db.execute(
                "CREATE TABLE gate_authority (singleton INTEGER PRIMARY KEY, "
                "generation INTEGER, evidence_sha256 TEXT, authority_sha256 TEXT)"
            )
            db.execute(
                "CREATE TABLE gate_intents (intent_id TEXT PRIMARY KEY, state TEXT)"
            )
            db.execute(
                "INSERT INTO gate_authority VALUES(1,1,?,?)",
                (evidence_sha, file_sha),
            )
        return {
            "activation_status": "active_global_gate",
            "authority_path": str(authority_path),
            "authority_sha256": file_sha,
            "generation": 1,
        }

    def test_current_slice13_refresh_evidence_is_postpublication_valid(self) -> None:
        evidence = PROJECT_ROOT / "evidence/slice-13/exit-evidence-manifest.json"
        completed = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                "-B",
                "scripts/validate_slice_exit_evidence.py",
                str(evidence),
            ],
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)

    def _write_valid_cutover_manifest(
        self,
        *,
        status_overrides: dict[str, str] | None = None,
        components_activated: list[str] | None = None,
        control_store_root: Path | None = None,
        global_gate_binding: dict[str, object] | None = None,
    ) -> tuple[Path, Path]:
        if control_store_root is None:
            case_root = new_case_dir(self.id(), label="issue14-platform-cutover")
            control_store_root = case_root / "control-store"
            control_store_root.mkdir(parents=True)
        else:
            control_store_root = control_store_root.resolve()
            control_store_root.mkdir(parents=True, exist_ok=True)
            case_root = control_store_root
        if global_gate_binding is None:
            global_gate_binding = self._write_stub_global_gate(control_store_root)
        youtube_root = case_root / "youtube-cutover"
        youtube_root.mkdir(parents=True, exist_ok=True)
        exit_evidence = youtube_root / "exit-evidence-manifest.json"
        manifest = {
                    "$schema": (
                        "https://video2pdf.local/schemas/"
                        "exit-evidence-manifest.v2.schema.json"
                    ),
                    "schema_version": 2,
                    "kind": "video-workflow-exit-evidence",
                    "fingerprint_algorithm": "sha256-raw-v1",
                    "slice": {
                        "number": 13,
                        "name": "youtube-platform-kernel-cutover",
                    },
                    "slice_base_commit": (
                        "1111111111111111111111111111111111111111"
                    ),
                    "implementation_commit": (
                        "2222222222222222222222222222222222222222"
                    ),
                    "evidence_paths": [
                        "evidence/youtube-platform-kernel/"
                        "exit-evidence-manifest.json",
                        "evidence/youtube-platform-kernel/logs/"
                        "qualification.log",
                    ],
                    "generated_at": "2026-08-12T00:00:00Z",
                    "activation_scope": {
                        "kind": "platform_kernel_cutover",
                        "runtime_authority_change": True,
                        "components_activated": ["youtube_platform_kernel"],
                        "activated_platform": "youtube",
                        "legacy_track_authority": (
                            "existing_directories_preserved"
                        ),
                        "platform_kernel_authority": (
                            "youtube_active_kernel"
                        ),
                        "qualification_contract_sha256": (
                            "33333333333333333333333333333333"
                            "33333333333333333333333333333333"
                        ),
                    },
                    "atomic_members": [
                        "platform_adapter",
                        "kernel_lifecycle",
                        "delivery_lifecycle",
                        "schemas",
                        "prompts",
                        "configuration",
                        "providers",
                        "validators",
                        "delivery_guard",
                        "hooks",
                        "skills",
                        "project_instructions",
                        "mirrors",
                        "tests",
                    ],
                    "atomic_member_status": {
                        "platform_adapter": "active",
                        "kernel_lifecycle": "active",
                        "delivery_lifecycle": "active",
                        "schemas": "active",
                        "prompts": "active",
                        "configuration": "active",
                        "providers": "active",
                        "validators": "active",
                        "delivery_guard": "active",
                        "hooks": "active",
                        "skills": "active",
                        "project_instructions": "active",
                        "mirrors": "active",
                        "tests": "active",
                    },
                    "policy_status": "active_kernel",
                    "platform_statuses": {
                        "bilibili": "active_kernel",
                        "youtube": "active_kernel",
                    },
                    "global_gate_binding": global_gate_binding,
                    "mirror_checks": [
                        {
                            "source_path": (
                                ".agents/skills/youtube-render-pdf/SKILL.md"
                            ),
                            "mirror_path": (
                                ".claude/skills/youtube-render-pdf/SKILL.md"
                            ),
                            "source_sha256": (
                                "55555555555555555555555555555555"
                                "55555555555555555555555555555555"
                            ),
                            "mirror_sha256": (
                                "55555555555555555555555555555555"
                                "55555555555555555555555555555555"
                            ),
                            "status": "equal",
                        }
                    ],
                    "commands": [
                        {
                            "test_id": "issue14-platform-cutover-tests",
                            "command": [
                                "python",
                                "-m",
                                "unittest",
                                "tests.video_workflow."
                                "test_issue14_platform_cutover",
                            ],
                            "expected_exit_code": 0,
                            "actual_exit_code": 0,
                            "log": {
                                "role": "command_log",
                                "path": (
                                    "evidence/youtube-platform-kernel/"
                                    "logs/qualification.log"
                                ),
                                "sha256": (
                                    "66666666666666666666666666666666"
                                    "66666666666666666666666666666666"
                                ),
                            },
                            "conforms": True,
                        },
                        {
                            "test_id": "issue14-workflow-policy-check",
                            "command": [
                                "python",
                                "scripts/video_workflow.py",
                                "workflow-policy-check",
                            ],
                            "expected_exit_code": 0,
                            "actual_exit_code": 0,
                            "log": {
                                "role": "command_log",
                                "path": (
                                    "evidence/youtube-platform-kernel/"
                                    "logs/policy.log"
                                ),
                                "sha256": (
                                    "77777777777777777777777777777777"
                                    "77777777777777777777777777777777"
                                ),
                            },
                            "conforms": True,
                        },
                    ],
                    "expected_checkpoints": [
                        {
                            "name": "bilibili_platform_kernel",
                            "status": "preserved",
                        },
                        {
                            "name": "youtube_platform_kernel",
                            "status": "current",
                        },
                    ],
                    "fixtures": [
                        {
                            "role": "youtube_cutover_manifest",
                            "path": (
                                "tests/video_workflow/fixtures/"
                                "exit_evidence_manifest.v2.issue14.valid.json"
                            ),
                            "sha256": (
                                "88888888888888888888888888888888"
                                "88888888888888888888888888888888"
                            ),
                        }
                    ],
                    "results": {
                        "positive": ["youtube_kernel_activation_pass"],
                        "negative": ["bilibili_activation_scope_rejected"],
                        "recovery": ["activation_reconcile_pass"],
                    },
                    "artifact_fingerprints": [
                        {
                            "role": "implementation_artifact",
                            "path": (
                                "src/video2pdf_workflow_kernel/"
                                "platform_kernel.py"
                            ),
                            "sha256": (
                                "99999999999999999999999999999999"
                                "99999999999999999999999999999999"
                            ),
                        }
                    ],
                    "unresolved_exceptions": [],
                    "overall_decision": "pass",
                }
        implementation_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=True,
        ).stdout.strip()
        implementation_path = "src/video2pdf_workflow_kernel/kernel.py"
        implementation_blob = subprocess.run(
            ["git", "show", f"{implementation_commit}:{implementation_path}"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            check=True,
        ).stdout
        manifest["implementation_commit"] = implementation_commit
        manifest["artifact_fingerprints"] = [
            {
                "role": "implementation_artifact",
                "path": implementation_path,
                "sha256": hashlib.sha256(implementation_blob).hexdigest(),
            }
        ]
        if status_overrides is not None:
            manifest["slice_base_commit"] = issue14_contract.SLICE_BASE_COMMIT
            manifest["activation_scope"] = deepcopy(issue14_contract.ACTIVATION_SCOPE)
            manifest["atomic_members"] = list(issue14_contract.ATOMIC_MEMBERS)
            manifest["atomic_member_status"] = deepcopy(
                issue14_contract.ATOMIC_MEMBER_STATUS
            )
            manifest["result_bindings"] = deepcopy(issue14_contract.RESULT_BINDINGS)
            manifest.pop("global_gate_binding")
            manifest.pop("mirror_checks")
            manifest.pop("policy_status")
            manifest["atomic_member_status"].update(status_overrides)
        else:
            manifest["slice_base_commit"] = issue14_contract.SLICE_BASE_COMMIT
            manifest["activation_scope"] = deepcopy(issue14_contract.ACTIVATION_SCOPE)
            manifest["atomic_members"] = list(issue14_contract.ATOMIC_MEMBERS)
            manifest["atomic_member_status"] = deepcopy(
                issue14_contract.ATOMIC_MEMBER_STATUS
            )
            manifest["result_bindings"] = deepcopy(issue14_contract.RESULT_BINDINGS)
            manifest.pop("global_gate_binding")
            manifest.pop("mirror_checks")
            manifest.pop("policy_status")
        if components_activated is not None:
            manifest["activation_scope"]["components_activated"] = (
                components_activated
            )
        guarded_root = youtube_root / "guarded-delivery"
        guarded_root.mkdir(parents=True)

        def write_bound(name: str, payload: bytes) -> tuple[Path, dict[str, str]]:
            path = guarded_root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            return path, {
                "path": path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix(),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }

        run_id = "14141414141414141414141414141414"
        session_id = "candidate-session-youtube"
        semantic_payloads = {
            "acceptance_report_v2": _acceptance_report(run_id, 3, "pass"),
            "delivery_guard_report": _guard_report("pass"),
        }
        role_files: dict[str, tuple[Path, dict[str, str]]] = {}
        for role in (
            "run_record",
            "source_manifest",
            "acceptance_report_v2",
            "delivery_guard_report",
            "video_delivery_target",
            "session_delivery_target",
            "delivery_task_index",
        ):
            payload = semantic_payloads.get(role, {})
            role_files[role] = write_bound(
                f"{role}.json",
                json.dumps(payload, sort_keys=True).encode("utf-8") + b"\n",
            )
        role_files["global_gate_authority"] = (
            Path(global_gate_binding["authority_path"]),
            {
                "path": Path(global_gate_binding["authority_path"])
                .resolve()
                .relative_to(PROJECT_ROOT.resolve())
                .as_posix(),
                "sha256": global_gate_binding["authority_sha256"],
            },
        )
        role_files["final_pdf"] = write_bound("final.pdf", b"%PDF-1.7\n")
        qualification_id = "14141414-1414-4414-8414-141414141414"
        qualification_argv = list(issue14_contract.COMMANDS[1][1])
        qualification_argv[0] = Path(qualification_argv[0]).as_posix()
        command_path, command_binding = write_bound(
            "command.json",
            json.dumps(
                {
                    "run_id": qualification_id,
                    "argv": qualification_argv,
                    "cwd": str(PROJECT_ROOT.resolve()),
                    "accepted_exit_codes": [0],
                },
                sort_keys=True,
            ).encode("utf-8")
            + b"\n",
        )
        status_path, status_binding = write_bound(
            "status.json",
            json.dumps(
                {
                    "run_id": qualification_id,
                    "state": "succeeded",
                    "exit_code": 0,
                    "security": {"acceptance_evidence_eligible": True},
                },
                sort_keys=True,
            ).encode("utf-8")
            + b"\n",
        )
        exit_path, exit_binding = write_bound("exit-code.txt", b"0\n")
        collection = {
            "schema_name": "issue14-exit-evidence-collection",
            "schema_version": "1.0.0",
            "run_id": run_id,
            "canonical_platform": "youtube",
            "delivery_stage": "delivered",
            "artifacts": {
                role: {
                    "path": str(path.resolve()),
                    "sha256": binding["sha256"],
                }
                for role, (path, binding) in role_files.items()
            },
            "qualification_run": {
                "run_id": qualification_id,
                "state": "succeeded",
                "exit_code": 0,
                "acceptance_evidence_eligible": True,
                "command_record": {
                    "path": str(command_path.resolve()),
                    "sha256": command_binding["sha256"],
                },
                "terminal_status": {
                    "path": str(status_path.resolve()),
                    "sha256": status_binding["sha256"],
                },
                "exit_code_artifact": {
                    "path": str(exit_path.resolve()),
                    "sha256": exit_binding["sha256"],
                },
            },
        }
        collection_path, collection_binding = write_bound(
            "collection.json",
            json.dumps(collection, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
            + b"\n",
        )
        manifest["guarded_delivery_evidence"] = {
            "collection": {
                "role": "guarded_delivery_collection",
                **collection_binding,
            },
            "run_id": collection["run_id"],
            "canonical_platform": "youtube",
            "delivery_stage": "delivered",
            "artifacts": [
                {"role": role, **binding}
                for role, (_path, binding) in role_files.items()
            ],
            "qualification_run": {
                "run_id": qualification_id,
                "command_record": {
                    "role": "persisted_command_record",
                    **command_binding,
                },
                "terminal_status": {
                    "role": "persisted_terminal_status",
                    **status_binding,
                },
                "exit_code": {
                    "role": "persisted_exit_code",
                    **exit_binding,
                },
            },
        }
        exit_evidence.write_text(
            json.dumps(
                manifest,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        candidate_root = guarded_root / "candidate-run"
        source_path = candidate_root / "source" / "manifest.json"
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text(
            json.dumps(
                {"run_id": run_id, "source_identity": "s" * 64},
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        video_path = candidate_root / "review" / "acceptance" / "delivery_target.json"
        session_path = candidate_root / "delivery-session-target.json"
        index_path = candidate_root / "delivery-task-index.json"
        video = {
            "run_id": run_id,
            "stage": "delivered",
            "artifacts": {
                "acceptance_report": {
                    "path": str(role_files["acceptance_report_v2"][0].resolve()),
                    "sha256": role_files["acceptance_report_v2"][1]["sha256"],
                },
                "delivery_guard_report": {
                    "path": str(role_files["delivery_guard_report"][0].resolve()),
                    "sha256": role_files["delivery_guard_report"][1]["sha256"],
                },
                "final_pdf": {
                    "path": str(role_files["final_pdf"][0].resolve()),
                    "sha256": role_files["final_pdf"][1]["sha256"],
                },
            },
            "global_gate_authority": {
                "path": global_gate_binding["authority_path"],
                "generation": global_gate_binding["generation"],
                "sha256": global_gate_binding["authority_sha256"],
            },
        }
        session = {"run_id": run_id, "stage": "delivered"}
        index = {"entries": [{"run_id": run_id, "stage": "delivered"}]}
        for path, value in (
            (video_path, video),
            (session_path, session),
            (index_path, index),
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
        run_path = candidate_root / "workflow" / "run.json"
        run_path.parent.mkdir(parents=True, exist_ok=True)
        run_path.write_text(
            json.dumps(
                {
                    "schema_version": "4.0.0",
                    "canonical_platform": "youtube",
                    "run_id": run_id,
                    "output_path": str(candidate_root.resolve()),
                    "source_identity": "s" * 64,
                    "artifact_generations": {
                        "source_manifest": {
                            "sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
                        },
                    },
                    "delivery": {
                        "stage": "delivered",
                        "ownership": {"session_id": session_id, "generation": 1},
                        "projections": {
                            "video_target": {
                                "path": str(video_path.resolve()),
                                "sha256": hashlib.sha256(video_path.read_bytes()).hexdigest(),
                            },
                            "session_target": {
                                "path": str(session_path.resolve()),
                                "sha256": hashlib.sha256(session_path.read_bytes()).hexdigest(),
                            },
                            "task_index": {
                                "path": str(index_path.resolve()),
                                "sha256": hashlib.sha256(index_path.read_bytes()).hexdigest(),
                            },
                        },
                    },
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        # Rematerialize every node reachable from the delivered candidate. This
        # preserves one contradiction per negative scenario after the snapshot
        # and guarded-delivery validation gates were strengthened.
        def current_binding(path: Path) -> dict[str, str]:
            return {
                "path": path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }

        role_files.update(
            {
                "run_record": (run_path, current_binding(run_path)),
                "source_manifest": (source_path, current_binding(source_path)),
                "video_delivery_target": (video_path, current_binding(video_path)),
                "session_delivery_target": (session_path, current_binding(session_path)),
                "delivery_task_index": (index_path, current_binding(index_path)),
            }
        )
        collection["artifacts"] = {
            role: {"path": str(path.resolve()), "sha256": binding["sha256"]}
            for role, (path, binding) in role_files.items()
        }
        collection_path.write_text(
            json.dumps(collection, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        collection_binding = current_binding(collection_path)
        manifest["guarded_delivery_evidence"]["collection"].update(
            collection_binding
        )
        manifest["guarded_delivery_evidence"]["artifacts"] = [
            {"role": role, **binding}
            for role, (_path, binding) in role_files.items()
        ]
        exit_evidence.write_text(
            json.dumps(
                manifest,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        # Fixture graph: the SQL columns are authority inputs and candidate_json
        # is their frozen snapshot. Activation is the first gate; each negative
        # scenario mutates only its declared downstream evidence contradiction.
        candidate = {
            "candidate_run_id": run_id,
            "candidate_run_dir": str(candidate_root.resolve()),
            "source_identity": "s" * 64,
            "candidate_session_id": session_id,
            "global_gate_binding": {
                "authority_sha256": global_gate_binding["authority_sha256"],
            },
            "implementation_commit": implementation_commit,
            "probe_sha256": "p" * 64,
            "state": "PROVISIONAL",
        }
        with sqlite3.connect(
            control_store_root / "platform-kernel-control.sqlite3"
        ) as platform_db:
            platform_db.execute(
                "CREATE TABLE IF NOT EXISTS platform_cutover_candidates ("
                "platform TEXT PRIMARY KEY, candidate_run_id TEXT NOT NULL, "
                "source_identity TEXT NOT NULL, session_id TEXT NOT NULL, "
                "global_gate_sha256 TEXT NOT NULL, implementation_commit TEXT NOT NULL, "
                "probe_sha256 TEXT NOT NULL, candidate_json TEXT NOT NULL, "
                "state TEXT NOT NULL CHECK(state IN "
                "('PREPARED','INITIALIZED','PROVISIONAL','CONFIRMED')))"
            )
            platform_db.execute(
                "INSERT INTO platform_cutover_candidates VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    "youtube",
                    run_id,
                    "s" * 64,
                    session_id,
                    global_gate_binding["authority_sha256"],
                    implementation_commit,
                    "p" * 64,
                    json.dumps(candidate, sort_keys=True, separators=(",", ":")),
                    "PROVISIONAL",
                ),
            )
        return control_store_root, exit_evidence

    @staticmethod
    def _write_refreshed_cutover_manifest(exit_evidence: Path) -> Path:
        refreshed_evidence = exit_evidence.with_name("refreshed-exit-evidence.json")
        refreshed_manifest = json.loads(exit_evidence.read_text(encoding="utf-8"))
        refreshed_manifest["generated_at"] = "2026-08-19T12:00:00Z"
        refreshed_evidence.write_text(
            json.dumps(
                refreshed_manifest,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        return refreshed_evidence

    @staticmethod
    def _write_published_global_gate(
        control_store_root: Path,
    ) -> dict[str, object]:
        """Seed a current Global Gate authority from the committed Slice 11 evidence.

        The Global Gate requires current post-publication Exit Evidence. This
        builder preserves the committed qualification result while rematerializing
        its live mirror checks against the current repository root.
        """
        control_store_root = control_store_root.resolve()
        evidence_path = control_store_root / "global-gate-exit-evidence.json"
        evidence = json.loads(
            (
                PROJECT_ROOT / "evidence/global-gate/exit-evidence-manifest.json"
            ).read_text(encoding="utf-8")
        )
        evidence["mirror_checks"] = [
            {
                "source_path": source,
                "mirror_path": mirror,
                "source_sha256": hashlib.sha256(
                    (PROJECT_ROOT / source).read_bytes()
                ).hexdigest(),
                "mirror_sha256": hashlib.sha256(
                    (PROJECT_ROOT / mirror).read_bytes()
                ).hexdigest(),
                "status": (
                    "equal"
                    if (PROJECT_ROOT / source).read_bytes()
                    == (PROJECT_ROOT / mirror).read_bytes()
                    else "stale"
                ),
            }
            for source, mirror in global_gate_contract.MIRROR_SPECS
        ]
        evidence_path.write_text(
            json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n",
            encoding="utf-8",
        )
        evidence_sha = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
        authority_path = control_store_root / "active_global_gate.json"
        authority = {
            "schema_name": "global-gate-authority",
            "schema_version": "1.0.0",
            "generation": 1,
            "active_global_gate": "acceptance_report_v2",
            "acceptance_report_schema_version": "2.0.0",
            "legacy_acceptance_authority": "legacy_acceptance_input_set_v1",
            "platform_kernel_authority": "unchanged",
            "exit_evidence_path": str(evidence_path),
            "exit_evidence_sha256": evidence_sha,
            "activated_at": "2026-08-12T00:00:00Z",
        }
        authority["authority_sha256"] = hashlib.sha256(
            (
                json.dumps(
                    authority,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("utf-8")
        ).hexdigest()
        authority_path.write_text(
            json.dumps(authority, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        file_sha = hashlib.sha256(authority_path.read_bytes()).hexdigest()
        with sqlite3.connect(control_store_root / "global-gate-control.sqlite3") as db:
            db.execute(
                "CREATE TABLE gate_authority (singleton INTEGER PRIMARY KEY, "
                "generation INTEGER, evidence_sha256 TEXT, authority_sha256 TEXT)"
            )
            db.execute(
                "CREATE TABLE gate_intents (intent_id TEXT PRIMARY KEY, state TEXT)"
            )
            db.execute(
                "INSERT INTO gate_authority VALUES(1,1,?,?)",
                (evidence_sha, file_sha),
            )
        return {
            "activation_status": "active_global_gate",
            "authority_path": str(authority_path),
            "authority_sha256": file_sha,
            "generation": 1,
        }

    def test_youtube_activation_publishes_single_platform_authority(self) -> None:
        control_store_root, exit_evidence = self._write_valid_cutover_manifest()

        completed = _run_cli(
            "platform-kernel-activate",
            "--platform",
            "youtube",
            "--control-store-root",
            str(control_store_root),
            "--exit-evidence",
            str(exit_evidence),
            "--activated-at",
            "2026-08-12T00:00:00Z",
        )
        envelope = json.loads(completed.stdout)

        self.assertEqual(
            {
                "classification": "platform_kernel_activated",
                "platform_statuses": {
                    "bilibili": "active_kernel",
                    "youtube": "active_kernel",
                },
            },
            {
                "classification": envelope["classification"],
                "platform_statuses": envelope.get("data", {}).get(
                    "platform_statuses"
                ),
            },
        )
        self.assertTrue(
            (control_store_root / "platform-authorities" / "youtube.json").exists()
        )

    def test_activation_rejects_content_bound_failed_acceptance_or_guard(self) -> None:
        mutations = {
            "acceptance_report_v2": ("overall_status", "fail"),
            "delivery_guard_report": ("status", "blocked"),
        }
        for role, (field, failed_value) in mutations.items():
            with self.subTest(role=role):
                control_store_root, exit_evidence = self._write_valid_cutover_manifest()
                manifest = json.loads(exit_evidence.read_text(encoding="utf-8"))
                artifact_binding = next(
                    item
                    for item in manifest["guarded_delivery_evidence"]["artifacts"]
                    if item["role"] == role
                )
                artifact_path = (PROJECT_ROOT / artifact_binding["path"]).resolve()
                artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
                artifact[field] = failed_value
                artifact_path.write_text(
                    json.dumps(artifact, sort_keys=True) + "\n", encoding="utf-8"
                )
                artifact_sha = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
                artifact_binding["sha256"] = artifact_sha

                collection_binding = manifest["guarded_delivery_evidence"]["collection"]
                collection_path = (PROJECT_ROOT / collection_binding["path"]).resolve()
                collection = json.loads(collection_path.read_text(encoding="utf-8"))
                collection["artifacts"][role]["sha256"] = artifact_sha
                collection_path.write_text(
                    json.dumps(collection, sort_keys=True, separators=(",", ":")) + "\n",
                    encoding="utf-8",
                )
                collection_binding["sha256"] = hashlib.sha256(
                    collection_path.read_bytes()
                ).hexdigest()
                exit_evidence.write_text(
                    json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
                    encoding="utf-8",
                )

                completed = _run_cli(
                    "platform-kernel-activate",
                    "--platform",
                    "youtube",
                    "--control-store-root",
                    str(control_store_root),
                    "--exit-evidence",
                    str(exit_evidence),
                    "--activated-at",
                    "2026-08-12T00:00:00Z",
                )
                self.assertNotEqual(0, completed.returncode, completed.stdout)
                self.assertFalse(
                    (control_store_root / "platform-authorities" / "youtube.json").exists()
                )

    def test_failed_atomic_member_preserves_youtube_legacy_authority(
        self,
    ) -> None:
        control_store_root, exit_evidence = self._write_valid_cutover_manifest(
            status_overrides={"validators": "failed"}
        )

        completed = _run_cli(
            "platform-kernel-activate",
            "--platform",
            "youtube",
            "--control-store-root",
            str(control_store_root),
            "--exit-evidence",
            str(exit_evidence),
            "--activated-at",
            "2026-08-12T00:00:00Z",
        )
        envelope = json.loads(completed.stdout)

        self.assertNotEqual(0, completed.returncode)
        self.assertEqual(
            {
                "first_failing_gate": "atomic_member_status",
                "error_code": "youtube_cutover_atomic_member_failed",
            },
            {
                "first_failing_gate": envelope.get("data", {}).get(
                    "first_failing_gate"
                ),
                "error_code": envelope.get("data", {}).get("error_code"),
            },
        )
        self.assertFalse(
            (
                control_store_root
                / "platform-authorities"
                / "youtube.json"
            ).exists()
        )

    def test_youtube_activation_rejects_bilibili_scope_expansion(
        self,
    ) -> None:
        control_store_root, exit_evidence = self._write_valid_cutover_manifest(
            components_activated=[
                "youtube_platform_kernel",
                "bilibili_platform_kernel",
            ]
        )

        completed = _run_cli(
            "platform-kernel-activate",
            "--platform",
            "youtube",
            "--control-store-root",
            str(control_store_root),
            "--exit-evidence",
            str(exit_evidence),
            "--activated-at",
            "2026-08-12T00:00:00Z",
        )
        envelope = json.loads(completed.stdout)

        self.assertNotEqual(0, completed.returncode)
        self.assertEqual(
            {
                "first_failing_gate": "activation_scope",
                "error_code": "youtube_activation_scope_invalid",
            },
            {
                "first_failing_gate": envelope.get("data", {}).get(
                    "first_failing_gate"
                ),
                "error_code": envelope.get("data", {}).get("error_code"),
            },
        )
        self.assertFalse(
            (
                control_store_root
                / "platform-authorities"
                / "youtube.json"
            ).exists()
        )

    def test_exact_youtube_activation_retry_is_idempotent(self) -> None:
        control_store_root, exit_evidence = self._write_valid_cutover_manifest()
        arguments = (
            "platform-kernel-activate",
            "--platform",
            "youtube",
            "--control-store-root",
            str(control_store_root),
            "--exit-evidence",
            str(exit_evidence),
            "--activated-at",
            "2026-08-12T00:00:00Z",
        )
        first = _run_cli(*arguments)
        if first.returncode != 0:
            raise AssertionError(first.stdout + first.stderr)

        second = _run_cli(*arguments)
        envelope = json.loads(second.stdout)

        self.assertEqual(
            {
                "returncode": 0,
                "data": {
                    "platform": "youtube",
                    "generation": 1,
                    "idempotent": True,
                },
            },
            {
                "returncode": second.returncode,
                "data": {
                    "platform": envelope.get("data", {}).get("platform"),
                    "generation": envelope.get("data", {}).get("generation"),
                    "idempotent": envelope.get("data", {}).get("idempotent"),
                },
            },
        )

    def test_confirmed_youtube_authority_refreshes_to_current_published_evidence(
        self,
    ) -> None:
        control_store_root, exit_evidence = self._write_valid_cutover_manifest()
        activated = _run_cli(
            "platform-kernel-activate",
            "--platform",
            "youtube",
            "--control-store-root",
            str(control_store_root),
            "--exit-evidence",
            str(exit_evidence),
            "--activated-at",
            "2026-08-12T00:00:00Z",
        )
        self.assertEqual(0, activated.returncode, activated.stdout)

        refreshed_evidence = self._write_refreshed_cutover_manifest(exit_evidence)

        refreshed = _run_cli(
            "youtube-platform-authority-refresh",
            "--control-store-root",
            str(control_store_root),
            "--exit-evidence",
            str(refreshed_evidence),
            "--expected-generation",
            "1",
            "--refreshed-at",
            "2026-08-19T12:01:00Z",
        )
        envelope = json.loads(refreshed.stdout)
        replay = _run_cli(
            "youtube-platform-authority-refresh",
            "--control-store-root",
            str(control_store_root),
            "--exit-evidence",
            str(refreshed_evidence),
            "--expected-generation",
            "1",
            "--refreshed-at",
            "2026-08-19T12:01:00Z",
        )
        replay_envelope = json.loads(replay.stdout)

        self.assertEqual(
            {
                "refresh_returncode": 0,
                "classification": "platform_kernel_authority_refreshed",
                "generation": 2,
                "idempotent": False,
                "replay_returncode": 0,
                "replay_generation": 2,
                "replay_idempotent": True,
            },
            {
                "refresh_returncode": refreshed.returncode,
                "classification": envelope.get("classification"),
                "generation": envelope.get("data", {}).get("generation"),
                "idempotent": envelope.get("data", {}).get("idempotent"),
                "replay_returncode": replay.returncode,
                "replay_generation": replay_envelope.get("data", {}).get(
                    "generation"
                ),
                "replay_idempotent": replay_envelope.get("data", {}).get(
                    "idempotent"
                ),
            },
        )

    def test_interrupted_youtube_authority_refresh_reconciles(self) -> None:
        control_store_root, exit_evidence = self._write_valid_cutover_manifest()
        activated = _run_cli(
            "platform-kernel-activate",
            "--platform",
            "youtube",
            "--control-store-root",
            str(control_store_root),
            "--exit-evidence",
            str(exit_evidence),
            "--activated-at",
            "2026-08-12T00:00:00Z",
        )
        self.assertEqual(0, activated.returncode, activated.stdout)
        refreshed_evidence = self._write_refreshed_cutover_manifest(exit_evidence)
        refresh_arguments = (
            "youtube-platform-authority-refresh",
            "--control-store-root",
            str(control_store_root),
            "--exit-evidence",
            str(refreshed_evidence),
            "--expected-generation",
            "1",
            "--refreshed-at",
            "2026-08-19T12:01:00Z",
        )

        interrupted = _run_cli(
            *refresh_arguments,
            "--fault-point",
            "after_authority_write",
        )
        reconciled = _run_cli(
            "platform-kernel-reconcile",
            "--platform",
            "youtube",
            "--control-store-root",
            str(control_store_root),
        )
        reconciliation = json.loads(reconciled.stdout)
        replayed = _run_cli(*refresh_arguments)
        replay = json.loads(replayed.stdout)

        self.assertNotEqual(0, interrupted.returncode)
        self.assertEqual(
            {
                "reconcile_returncode": 0,
                "classification": "platform_kernel_reconciled",
                "generation": 2,
                "authority_status": "current",
                "replay_returncode": 0,
                "replay_idempotent": True,
            },
            {
                "reconcile_returncode": reconciled.returncode,
                "classification": reconciliation.get("classification"),
                "generation": reconciliation.get("data", {}).get("generation"),
                "authority_status": reconciliation.get("data", {}).get(
                    "authority_status"
                ),
                "replay_returncode": replayed.returncode,
                "replay_idempotent": replay.get("data", {}).get("idempotent"),
            },
        )

    def test_youtube_authority_refresh_reconcile_observes_control_commit(self) -> None:
        control_store_root, exit_evidence = self._write_valid_cutover_manifest()
        activated = _run_cli(
            "platform-kernel-activate",
            "--platform",
            "youtube",
            "--control-store-root",
            str(control_store_root),
            "--exit-evidence",
            str(exit_evidence),
            "--activated-at",
            "2026-08-12T00:00:00Z",
        )
        self.assertEqual(0, activated.returncode, activated.stdout)
        refreshed_evidence = self._write_refreshed_cutover_manifest(exit_evidence)

        interrupted = _run_cli(
            "youtube-platform-authority-refresh",
            "--control-store-root",
            str(control_store_root),
            "--exit-evidence",
            str(refreshed_evidence),
            "--expected-generation",
            "1",
            "--refreshed-at",
            "2026-08-19T12:01:00Z",
            "--fault-point",
            "after_control_commit",
        )
        reconciled = _run_cli(
            "platform-kernel-reconcile",
            "--platform",
            "youtube",
            "--control-store-root",
            str(control_store_root),
        )
        envelope = json.loads(reconciled.stdout)

        self.assertNotEqual(0, interrupted.returncode)
        self.assertEqual(
            {
                "returncode": 0,
                "classification": "platform_kernel_reconciled",
                "generation": 2,
                "authority_status": "current",
            },
            {
                "returncode": reconciled.returncode,
                "classification": envelope.get("classification"),
                "generation": envelope.get("data", {}).get("generation"),
                "authority_status": envelope.get("data", {}).get(
                    "authority_status"
                ),
            },
        )

    def test_youtube_authority_refresh_fences_wrong_expected_generation(self) -> None:
        control_store_root, exit_evidence = self._write_valid_cutover_manifest()
        activated = _run_cli(
            "platform-kernel-activate",
            "--platform",
            "youtube",
            "--control-store-root",
            str(control_store_root),
            "--exit-evidence",
            str(exit_evidence),
            "--activated-at",
            "2026-08-12T00:00:00Z",
        )
        self.assertEqual(0, activated.returncode, activated.stdout)
        # scenario_class=single_contradiction
        # target_invariant=expected_generation_equals_committed_generation
        # mutation_seam=cli_expected_generation
        # rematerialized_nodes=[refreshed_exit_evidence]
        # intentionally_stale_nodes=[expected_generation]
        # expected_first_gate=platform_kernel_authority
        # expected_error_code=youtube_platform_authority_refresh_fenced
        refreshed_evidence = self._write_refreshed_cutover_manifest(exit_evidence)

        completed = _run_cli(
            "youtube-platform-authority-refresh",
            "--control-store-root",
            str(control_store_root),
            "--exit-evidence",
            str(refreshed_evidence),
            "--expected-generation",
            "2",
            "--refreshed-at",
            "2026-08-19T12:01:00Z",
        )
        envelope = json.loads(completed.stdout)
        authority = json.loads(
            (
                control_store_root / "platform-authorities" / "youtube.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(
            {
                "returncode": 30,
                "first_failing_gate": "platform_kernel_authority",
                "error_code": "youtube_platform_authority_refresh_fenced",
                "generation": 1,
            },
            {
                "returncode": completed.returncode,
                "first_failing_gate": envelope.get("data", {}).get(
                    "first_failing_gate"
                ),
                "error_code": envelope.get("data", {}).get("error_code"),
                "generation": authority.get("generation"),
            },
        )

    def test_interrupted_youtube_activation_reconciles_and_exact_retry_is_idempotent(
        self,
    ) -> None:
        control_store_root, exit_evidence = self._write_valid_cutover_manifest()
        activation_arguments = (
            "platform-kernel-activate",
            "--platform",
            "youtube",
            "--control-store-root",
            str(control_store_root),
            "--exit-evidence",
            str(exit_evidence),
            "--activated-at",
            "2026-08-12T00:00:00Z",
        )

        interrupted = _run_cli(
            *activation_arguments,
            "--fault-point",
            "after_authority_write",
        )
        fault = json.loads(interrupted.stdout)
        self.assertNotEqual(0, interrupted.returncode)
        self.assertEqual(
            "injected_platform_kernel_fault",
            fault["classification"],
        )

        reconciled = _run_cli(
            "platform-kernel-reconcile",
            "--platform",
            "youtube",
            "--control-store-root",
            str(control_store_root),
        )
        reconciliation = json.loads(reconciled.stdout)
        retried = _run_cli(*activation_arguments)
        retry = json.loads(retried.stdout)

        self.assertEqual(
            {
                "reconcile_returncode": 0,
                "reconcile_classification": "platform_kernel_reconciled",
                "reconcile_data": {
                    "platform": "youtube",
                    "authority_status": "current",
                    "generation": 1,
                },
                "retry_returncode": 0,
                "retry_data": {
                    "platform": "youtube",
                    "generation": 1,
                    "idempotent": True,
                },
            },
            {
                "reconcile_returncode": reconciled.returncode,
                "reconcile_classification": reconciliation[
                    "classification"
                ],
                "reconcile_data": {
                    "platform": reconciliation.get("data", {}).get(
                        "platform"
                    ),
                    "authority_status": reconciliation.get("data", {}).get(
                        "authority_status"
                    ),
                    "generation": reconciliation.get("data", {}).get(
                        "generation"
                    ),
                },
                "retry_returncode": retried.returncode,
                "retry_data": {
                    "platform": retry.get("data", {}).get("platform"),
                    "generation": retry.get("data", {}).get("generation"),
                    "idempotent": retry.get("data", {}).get("idempotent"),
                },
            },
        )

    def test_workflow_policy_check_reports_both_kernel(self) -> None:
        from tests.video_workflow.test_issue13_platform_cutover import (
            Issue13PlatformCutoverTests,
        )

        root = new_case_dir(self.id(), label="issue14-combined-policy")
        gate_binding = self._write_published_global_gate(root)
        self.assertEqual(1, gate_binding["generation"])
        bilibili_helper = Issue13PlatformCutoverTests(
            methodName="test_exact_bilibili_activation_retry_is_idempotent"
        )
        _bilibili_root, bilibili_evidence = bilibili_helper._write_valid_cutover_manifest(
            control_store_root=root,
            global_gate_binding=gate_binding,
        )
        activated_b = _run_cli(
            "platform-kernel-activate",
            "--platform",
            "bilibili",
            "--control-store-root",
            str(root),
            "--exit-evidence",
            str(bilibili_evidence),
            "--activated-at",
            "2026-08-12T00:01:00Z",
        )
        self.assertEqual(0, activated_b.returncode, activated_b.stdout)

        _youtube_root, youtube_evidence = self._write_valid_cutover_manifest(
            control_store_root=root,
            global_gate_binding=gate_binding,
        )
        activated_y = _run_cli(
            "platform-kernel-activate",
            "--platform",
            "youtube",
            "--control-store-root",
            str(root),
            "--exit-evidence",
            str(youtube_evidence),
            "--activated-at",
            "2026-08-12T00:02:00Z",
        )
        self.assertEqual(0, activated_y.returncode, activated_y.stdout)

        completed = _run_cli(
            "workflow-policy-check",
            "--control-store-root",
            str(root),
        )
        envelope = json.loads(completed.stdout)

        self.assertEqual(
            {
                "returncode": 0,
                "classification": "workflow_policy_current",
                "platform_statuses": {
                    "bilibili": "active_kernel",
                    "youtube": "active_kernel",
                },
            },
            {
                "returncode": completed.returncode,
                "classification": envelope["classification"],
                "platform_statuses": envelope.get("data", {}).get(
                    "platform_statuses"
                ),
            },
        )

    def test_youtube_activation_rejects_stale_global_gate_binding(self) -> None:
        root = new_case_dir(self.id(), label="issue14-global-binding")
        self._write_published_global_gate(root)
        _, platform_evidence = self._write_valid_cutover_manifest()
        (root / "active_global_gate.json").write_bytes(b"{}\n")

        completed = _run_cli(
            "platform-kernel-activate",
            "--platform",
            "youtube",
            "--control-store-root",
            str(root),
            "--exit-evidence",
            str(platform_evidence),
            "--activated-at",
            "2026-08-12T00:01:00Z",
        )
        envelope = json.loads(completed.stdout)

        self.assertNotEqual(0, completed.returncode)
        self.assertEqual(
            {
                "first_failing_gate": "global_gate_authority",
                "error_code": "global_gate_authority_stale",
            },
            {
                "first_failing_gate": envelope.get("data", {}).get(
                    "first_failing_gate"
                ),
                "error_code": envelope.get("data", {}).get("error_code"),
            },
        )
        self.assertFalse((root / "platform-authorities" / "youtube.json").exists())

    def _kernelize_youtube_target(self) -> tuple[Path, Path, Path]:
        project_root = new_case_dir(self.id(), label="issue14-kernel-guard")
        session_id = "session-issue14-guard"
        video_root = project_root / "workspace" / "YouTube Kernel Run_20260812_090000"
        review_dir = video_root / "review" / "acceptance"
        run_id = "14141414141414141414141414141414"
        intent_id = hashlib.sha256(
            f"kernel-guard:{self.id()}".encode("utf-8")
        ).hexdigest()

        final_pdf = video_root / "final.pdf"
        final_pdf.parent.mkdir(parents=True, exist_ok=True)
        final_pdf.write_bytes(b"%PDF-1.7\n% youtube kernel guard fixture\n")
        acceptance_report = review_dir / "acceptance_report.json"
        guard_report = review_dir / "delivery_guard_report.json"
        review_dir.mkdir(parents=True, exist_ok=True)
        acceptance_report.write_text(
            json.dumps(_acceptance_report(run_id, 2, "pass"), sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        guard_report.write_text(
            json.dumps(_guard_report("pass"), sort_keys=True) + "\n",
            encoding="utf-8",
        )
        global_gate = project_root / "active_global_gate.json"
        global_gate.write_text("{}\n", encoding="utf-8")

        video_target = review_dir / "delivery_target.json"
        video_target.write_text(
            json.dumps(
                {
                    "schema_name": "kernel-delivery-target",
                    "schema_version": "1.0.0",
                    "projection_kind": "video_target",
                    "projection_revision": 2,
                    "run_id": run_id,
                    "run_revision": 2,
                    "lifecycle_intent_id": intent_id,
                    "video_output_dir": str(video_root.resolve()),
                    "stage": "accepted",
                    "ownership": {"session_id": session_id, "generation": 1},
                    "artifacts": {
                        "final_pdf": {
                            "path": str(final_pdf.resolve()),
                            "sha256": hashlib.sha256(final_pdf.read_bytes()).hexdigest(),
                        },
                        "main_tex": None,
                        "final_compile_report": None,
                        "acceptance_report": {
                            "path": str(acceptance_report.resolve()),
                            "sha256": hashlib.sha256(acceptance_report.read_bytes()).hexdigest(),
                        },
                        "delivery_guard_report": {
                            "path": str(guard_report.resolve()),
                            "sha256": hashlib.sha256(guard_report.read_bytes()).hexdigest(),
                        },
                    },
                    "global_gate_authority": {
                        "path": str(global_gate.resolve()),
                        "generation": 1,
                        "sha256": hashlib.sha256(global_gate.read_bytes()).hexdigest(),
                    },
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        current_target = (
            project_root
            / ".codex"
            / "delivery-targets"
            / "sessions"
            / session_id
            / "current.json"
        )
        current_target.parent.mkdir(parents=True, exist_ok=True)
        current_target.write_text(
            json.dumps(
                {
                    "schema_name": "kernel-session-delivery-target",
                    "schema_version": "1.0.0",
                    "projection_kind": "session_target",
                    "projection_revision": 2,
                    "projection_path": str(current_target.resolve()),
                    "session_id": session_id,
                    "run_id": run_id,
                    "run_revision": 2,
                    "lifecycle_intent_id": intent_id,
                    "stage": "accepted",
                    "ownership_generation": 1,
                    "owner_status": "active",
                    "video_output_dir": str(video_root.resolve()),
                    "video_target": {
                        "path": str(video_target.resolve()),
                        "projection_revision": 2,
                        "sha256": hashlib.sha256(video_target.read_bytes()).hexdigest(),
                    },
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        task_index = (
            project_root / ".codex" / "delivery-targets" / "task-index.json"
        )
        task_index.write_text(
            json.dumps(
                {
                    "schema_name": "kernel-delivery-task-index",
                    "schema_version": "1.0.0",
                    "projection_kind": "task_index",
                    "projection_revision": 2,
                    "entries": [
                        {
                            "run_id": run_id,
                            "canonical_platform": "youtube",
                            "video_output_dir": str(video_root.resolve()),
                            "run_revision": 2,
                            "lifecycle_intent_id": intent_id,
                            "stage": "accepted",
                            "session_id": session_id,
                            "ownership_generation": 1,
                            "video_target": {
                                "path": str(video_target.resolve()),
                                "projection_revision": 2,
                                "sha256": hashlib.sha256(video_target.read_bytes()).hexdigest(),
                            },
                            "session_target": {
                                "path": str(current_target.resolve()),
                                "projection_revision": 2,
                                "sha256": hashlib.sha256(current_target.read_bytes()).hexdigest(),
                            },
                            "archive": None,
                        }
                    ],
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        run_path = video_root / "workflow" / "run.json"
        run_path.parent.mkdir(parents=True, exist_ok=True)
        run_path.write_text(
            json.dumps(
                {
                    "schema_name": "run-record",
                    "schema_version": "4.0.0",
                    "run_id": run_id,
                    "canonical_platform": "youtube",
                    "output_path": str(video_root.resolve()),
                    "source_identity": "s" * 64,
                    "coordination_revision": 2,
                    "last_mutation_intent_id": intent_id,
                    "delivery": {
                        "stage": "accepted",
                        "ownership": {"session_id": session_id, "generation": 1},
                        "projections": {
                            "video_target": {
                                "path": "review/acceptance/delivery_target.json",
                                "projection_revision": 2,
                                "sha256": hashlib.sha256(video_target.read_bytes()).hexdigest(),
                            },
                            "session_target": {
                                "path": str(current_target.resolve()),
                                "projection_revision": 2,
                                "sha256": hashlib.sha256(current_target.read_bytes()).hexdigest(),
                            },
                            "task_index": {
                                "path": str(task_index.resolve()),
                                "projection_revision": 2,
                                "sha256": hashlib.sha256(task_index.read_bytes()).hexdigest(),
                            },
                            "archive": None,
                        },
                    },
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        return project_root, current_target, task_index

    @staticmethod
    def tree_fingerprints(root: Path) -> dict[str, str]:
        return {
            path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*")
            if path.is_file()
        }

    def test_activated_scope_rejects_legacy_guard_mutation_commands(self) -> None:
        project_root, current_target, task_index = self._kernelize_youtube_target()
        guard = (
            PROJECT_ROOT
            / ".agents"
            / "skills"
            / "final-delivery-acceptance"
            / "scripts"
            / "delivery_guard.py"
        )
        commands = {
            "task-claim": [
                "--session-id", "session-issue14-guard", "--video-output-dir", str(project_root / "workspace" / "YouTube Kernel Run_20260812_090000"),
                "--target-file", str(current_target), "--stage", "accepted",
            ],
            "task-update": [
                "--session-id", "session-issue14-guard", "--video-output-dir", str(project_root / "workspace" / "YouTube Kernel Run_20260812_090000"),
                "--stage", "accepted", "--owner-status", "active",
            ],
            "clear-target": [
                "--session-id", "session-issue14-guard", "--video-output-dir", str(project_root / "workspace" / "YouTube Kernel Run_20260812_090000"),
            ],
            "task-handoff": [
                "--from-session-id", "session-issue14-guard", "--to-session-id", "session-b",
                "--video-output-dir", str(project_root / "workspace" / "YouTube Kernel Run_20260812_090000"), "--target-file", str(current_target),
                "--stage", "accepted", "--previous-owner-status", "superseded",
            ],
        }
        for command, arguments in commands.items():
            with self.subTest(command=command):
                before = self.tree_fingerprints(project_root)
                completed = subprocess.run(
                    [
                        sys.executable, "-X", "utf8", "-B", str(guard), command,
                        "--project-root", str(project_root),
                        "--current-target", str(current_target),
                        "--task-index", str(task_index), *arguments,
                    ],
                    cwd=project_root,
                    text=True,
                    encoding="utf-8",
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(2, completed.returncode, completed.stdout + completed.stderr)
                self.assertIn("Kernel delivery authority is read-only", completed.stderr)
                self.assertEqual(before, self.tree_fingerprints(project_root))

    def test_init_run_rejects_unsupported_platform(self) -> None:
        from types import SimpleNamespace

        case_root = new_case_dir(self.id(), label="issue14-unsupported-platform")
        workspace = case_root / "workspace"
        workspace.mkdir()
        probe = SimpleNamespace(canonical_platform="vimeo")
        with patch.object(
            kernel_cli, "_production_probe_from_path", return_value=probe
        ):
            completed = _run_cli(
                "init-run",
                "--workspace-root",
                str(workspace),
                "--probe",
                str(case_root / "probe.json"),
                "--control-store-root",
                str(case_root / "control-store"),
                "--session-id",
                "session-issue14-vimeo",
            )
        envelope = json.loads(completed.stdout)
        self.assertEqual(2, completed.returncode, completed.stdout)
        self.assertEqual("usage_error", envelope["classification"])
        self.assertIn(
            "active only for Bilibili or YouTube",
            envelope["data"]["message"],
        )

    def test_platform_authority_change_rejected_slice12_fixture_still_rejected(
        self,
    ) -> None:
        # The committed slice-12 negative fixture must remain REJECTED after
        # the Issue #14 cutover: the slice-12 platform-statuses authority is
        # preserved because Bilibili must not change during the YouTube cutover.
        from tests.video_workflow.test_issue13_exit_evidence import (
            Issue13ExitEvidenceTests,
        )
        from scripts import validate_slice_exit_evidence as validator

        scenario = json.loads(
            (
                PROJECT_ROOT
                / "tests/video_workflow/fixtures/exit_evidence/slice12.youtube-kernel.invalid.json"
            ).read_text(encoding="utf-8")
        )
        invalid = Issue13ExitEvidenceTests(
            methodName="test_youtube_authority_change_rejected"
        ).manifest()
        invalid.update(deepcopy(scenario["mutation"]))
        with self.assertRaises(validator.EvidenceError) as caught:
            validator.validate_issue13_cutover(invalid)
        self.assertEqual(
            scenario["expected_first_failing_gate"],
            caught.exception.first_failing_gate,
        )
        self.assertEqual(
            scenario["expected_error_code"],
            caught.exception.error_code,
        )


if __name__ == "__main__":
    unittest.main()
