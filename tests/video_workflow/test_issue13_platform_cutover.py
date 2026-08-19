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
from tests.video_workflow._issue43_git_authority import (
    build_current_global_gate_authority,
)
from scripts import issue13_exit_evidence_contract as issue13_contract
from video2pdf_workflow_kernel.global_gate import GlobalGatePublisher
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


def _run_formal_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            "-B",
            str(PROJECT_ROOT / "scripts/video_workflow.py"),
            *arguments,
        ],
        cwd=PROJECT_ROOT,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


class Issue13PlatformCutoverTests(unittest.TestCase):
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
            "activated_at": "2026-08-08T00:00:00Z",
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

    def _write_valid_cutover_manifest(
        self,
        *,
        status_overrides: dict[str, str] | None = None,
        components_activated: list[str] | None = None,
        control_store_root: Path | None = None,
        global_gate_binding: dict[str, object] | None = None,
    ) -> tuple[Path, Path]:
        if control_store_root is None:
            case_root = new_case_dir(self.id(), label="issue13-platform-cutover")
            control_store_root = case_root / "control-store"
            control_store_root.mkdir(parents=True)
        else:
            control_store_root = control_store_root.resolve()
            control_store_root.mkdir(parents=True, exist_ok=True)
            case_root = control_store_root
        if global_gate_binding is None:
            global_gate_binding = self._write_stub_global_gate(control_store_root)
        exit_evidence = case_root / "exit-evidence-manifest.json"
        manifest = {
                    "$schema": (
                        "https://video2pdf.local/schemas/"
                        "exit-evidence-manifest.v2.schema.json"
                    ),
                    "schema_version": 2,
                    "kind": "video-workflow-exit-evidence",
                    "fingerprint_algorithm": "sha256-raw-v1",
                    "slice": {
                        "number": 12,
                        "name": "bilibili-platform-kernel-cutover",
                    },
                    "slice_base_commit": (
                        "1111111111111111111111111111111111111111"
                    ),
                    "implementation_commit": (
                        "2222222222222222222222222222222222222222"
                    ),
                    "evidence_paths": [
                        "evidence/bilibili-platform-kernel/"
                        "exit-evidence-manifest.json",
                        "evidence/bilibili-platform-kernel/logs/"
                        "qualification.log",
                    ],
                    "generated_at": "2026-08-09T00:00:00Z",
                    "activation_scope": {
                        "kind": "platform_kernel_cutover",
                        "runtime_authority_change": True,
                        "components_activated": ["bilibili_platform_kernel"],
                        "activated_platform": "bilibili",
                        "legacy_track_authority": (
                            "existing_directories_preserved"
                        ),
                        "platform_kernel_authority": (
                            "bilibili_active_kernel"
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
                        "youtube": "active_legacy",
                    },
                    "global_gate_binding": global_gate_binding,
                    "mirror_checks": [
                        {
                            "source_path": (
                                ".agents/skills/bilibili-render-pdf/SKILL.md"
                            ),
                            "mirror_path": (
                                ".claude/skills/bilibili-render-pdf/SKILL.md"
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
                            "test_id": "issue13-platform-cutover-tests",
                            "command": [
                                "python",
                                "-m",
                                "unittest",
                                "tests.video_workflow."
                                "test_issue13_platform_cutover",
                            ],
                            "expected_exit_code": 0,
                            "actual_exit_code": 0,
                            "log": {
                                "role": "command_log",
                                "path": (
                                    "evidence/bilibili-platform-kernel/"
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
                            "test_id": "issue13-workflow-policy-check",
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
                                    "evidence/bilibili-platform-kernel/"
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
                            "name": "bilibili_platform_kernel_authority",
                            "status": "current",
                        },
                        {
                            "name": "youtube_legacy_authority",
                            "status": "preserved",
                        },
                    ],
                    "fixtures": [
                        {
                            "role": "bilibili_cutover_manifest",
                            "path": (
                                "tests/video_workflow/fixtures/"
                                "exit_evidence_manifest.v2.issue13.valid.json"
                            ),
                            "sha256": (
                                "88888888888888888888888888888888"
                                "88888888888888888888888888888888"
                            ),
                        }
                    ],
                    "results": {
                        "positive": ["bilibili_kernel_activation_pass"],
                        "negative": ["youtube_activation_scope_rejected"],
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
            manifest["slice_base_commit"] = issue13_contract.SLICE_BASE_COMMIT
            manifest["activation_scope"] = deepcopy(issue13_contract.ACTIVATION_SCOPE)
            manifest["atomic_members"] = list(issue13_contract.ATOMIC_MEMBERS)
            manifest["atomic_member_status"] = deepcopy(
                issue13_contract.ATOMIC_MEMBER_STATUS
            )
            manifest["result_bindings"] = deepcopy(issue13_contract.RESULT_BINDINGS)
            manifest.pop("global_gate_binding")
            manifest.pop("mirror_checks")
            manifest.pop("policy_status")
            manifest["atomic_member_status"].update(status_overrides)
        else:
            manifest["slice_base_commit"] = issue13_contract.SLICE_BASE_COMMIT
            manifest["activation_scope"] = deepcopy(issue13_contract.ACTIVATION_SCOPE)
            manifest["atomic_members"] = list(issue13_contract.ATOMIC_MEMBERS)
            manifest["atomic_member_status"] = deepcopy(
                issue13_contract.ATOMIC_MEMBER_STATUS
            )
            manifest["result_bindings"] = deepcopy(issue13_contract.RESULT_BINDINGS)
            manifest.pop("global_gate_binding")
            manifest.pop("mirror_checks")
            manifest.pop("policy_status")
        if components_activated is not None:
            manifest["activation_scope"]["components_activated"] = (
                components_activated
            )
        guarded_root = case_root / "guarded-delivery"
        guarded_root.mkdir(parents=True)

        def write_bound(name: str, payload: bytes) -> tuple[Path, dict[str, str]]:
            path = guarded_root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            return path, {
                "path": path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix(),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }

        run_id = "13131313131313131313131313131313"
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
        qualification_id = "13131313-1313-4313-8313-131313131313"
        command_path, command_binding = write_bound(
            "command.json",
            json.dumps(
                {
                    "run_id": qualification_id,
                    "argv": list(issue13_contract.COMMANDS[1][1]),
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
            "schema_name": "issue13-exit-evidence-collection",
            "schema_version": "1.0.0",
            "run_id": run_id,
            "canonical_platform": "bilibili",
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
            "canonical_platform": "bilibili",
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
                    "canonical_platform": "bilibili",
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
                        "ownership": {"session_id": "candidate-session"},
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
            "candidate_session_id": "candidate-session",
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
                    "bilibili",
                    run_id,
                    "s" * 64,
                    "candidate-session",
                    global_gate_binding["authority_sha256"],
                    implementation_commit,
                    "p" * 64,
                    json.dumps(candidate, sort_keys=True, separators=(",", ":")),
                    "PROVISIONAL",
                ),
            )
        return control_store_root, exit_evidence

    def _write_schema_valid_slice12_manifest(self) -> tuple[Path, Path]:
        control_store_root, exit_evidence = self._write_valid_cutover_manifest()
        manifest = json.loads(exit_evidence.read_text(encoding="utf-8"))
        schema = json.loads(
            (
                PROJECT_ROOT / "schemas" / "exit-evidence-manifest.v2.schema.json"
            ).read_text(encoding="utf-8")
        )
        Draft202012Validator(schema).validate(manifest)
        return control_store_root, exit_evidence

    def _write_schema_valid_forged_implementation_lineage(
        self,
    ) -> tuple[Path, Path]:
        control_store_root, exit_evidence = (
            self._write_schema_valid_slice12_manifest()
        )
        manifest = json.loads(exit_evidence.read_text(encoding="utf-8"))
        manifest["implementation_commit"] = "f" * 40
        manifest["artifact_fingerprints"][0]["sha256"] = "e" * 64
        schema = json.loads(
            (
                PROJECT_ROOT / "schemas" / "exit-evidence-manifest.v2.schema.json"
            ).read_text(encoding="utf-8")
        )
        Draft202012Validator(schema).validate(manifest)
        exit_evidence.write_text(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        return control_store_root, exit_evidence

    def assert_platform_authority_uncommitted(
        self, control_store_root: Path
    ) -> None:
        self.assertFalse(
            (
                control_store_root
                / "platform-authorities"
                / "bilibili.json"
            ).exists()
        )
        database = control_store_root / "platform-kernel-control.sqlite3"
        if not database.is_file():
            return
        with sqlite3.connect(database) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            authority_count = (
                connection.execute(
                    "SELECT COUNT(*) FROM platform_cutover_authority"
                ).fetchone()[0]
                if "platform_cutover_authority" in tables
                else 0
            )
            committed_intent_count = (
                connection.execute(
                    "SELECT COUNT(*) FROM platform_cutover_intents "
                    "WHERE state='COMMITTED'"
                ).fetchone()[0]
                if "platform_cutover_intents" in tables
                else 0
            )
        self.assertEqual(0, authority_count)
        self.assertEqual(0, committed_intent_count)

    def test_bilibili_activation_publishes_single_platform_authority(self) -> None:
        control_store_root, exit_evidence = self._write_valid_cutover_manifest()

        completed = _run_cli(
            "platform-kernel-activate",
            "--platform",
            "bilibili",
            "--control-store-root",
            str(control_store_root),
            "--exit-evidence",
            str(exit_evidence),
            "--activated-at",
            "2026-08-09T00:00:00Z",
        )
        envelope = json.loads(completed.stdout)

        self.assertEqual(
            {
                "classification": "platform_kernel_activated",
                "platform_statuses": {
                    "bilibili": "active_kernel",
                    "youtube": "active_legacy",
                },
            },
            {
                "classification": envelope["classification"],
                "platform_statuses": envelope.get("data", {}).get(
                    "platform_statuses"
                ),
            },
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
                    "bilibili",
                    "--control-store-root",
                    str(control_store_root),
                    "--exit-evidence",
                    str(exit_evidence),
                    "--activated-at",
                    "2026-08-09T00:00:00Z",
                )
                self.assertNotEqual(0, completed.returncode, completed.stdout)
                self.assertFalse(
                    (control_store_root / "platform-authorities" / "bilibili.json").exists()
                )

    def test_platform_activation_accepts_schema_valid_slice12_manifest(self) -> None:
        control_store_root, exit_evidence = (
            self._write_schema_valid_slice12_manifest()
        )

        completed = _run_cli(
            "platform-kernel-activate",
            "--platform",
            "bilibili",
            "--control-store-root",
            str(control_store_root),
            "--exit-evidence",
            str(exit_evidence),
            "--activated-at",
            "2026-08-09T00:00:00Z",
        )
        envelope = json.loads(completed.stdout)

        self.assertEqual(
            {
                "returncode": 0,
                "classification": "platform_kernel_activated",
                "platform_statuses": {
                    "bilibili": "active_kernel",
                    "youtube": "active_legacy",
                },
                "first_failing_gate": None,
                "error_code": None,
                "message": None,
            },
            {
                "returncode": completed.returncode,
                "classification": envelope["classification"],
                "platform_statuses": envelope.get("data", {}).get(
                    "platform_statuses"
                ),
                "first_failing_gate": envelope.get("data", {}).get(
                    "first_failing_gate"
                ),
                "error_code": envelope.get("data", {}).get("error_code"),
                "message": envelope.get("data", {}).get("message"),
            },
        )

    def test_activation_rejects_schema_valid_forged_implementation_lineage(
        self,
    ) -> None:
        control_store_root, exit_evidence = (
            self._write_schema_valid_forged_implementation_lineage()
        )

        completed = _run_formal_cli(
            "platform-kernel-activate",
            "--platform",
            "bilibili",
            "--control-store-root",
            str(control_store_root),
            "--exit-evidence",
            str(exit_evidence),
            "--activated-at",
            "2026-08-09T00:00:00Z",
        )

        self.assertNotEqual(0, completed.returncode, completed.stdout)
        self.assert_platform_authority_uncommitted(control_store_root)

    def test_activation_rejects_locally_valid_unpublished_exit_evidence(self) -> None:
        control_store_root, exit_evidence = self._write_schema_valid_slice12_manifest()

        completed = _run_formal_cli(
            "platform-kernel-activate",
            "--platform",
            "bilibili",
            "--control-store-root",
            str(control_store_root),
            "--exit-evidence",
            str(exit_evidence),
            "--activated-at",
            "2026-08-09T00:00:00Z",
        )

        self.assertNotEqual(0, completed.returncode, completed.stdout)
        self.assert_platform_authority_uncommitted(control_store_root)

    def test_reconcile_rejects_prepared_intent_with_unpublished_evidence_lineage(
        self,
    ) -> None:
        control_store_root, exit_evidence = (
            self._write_schema_valid_slice12_manifest()
        )
        interrupted = _run_cli(
            "platform-kernel-activate",
            "--platform",
            "bilibili",
            "--control-store-root",
            str(control_store_root),
            "--exit-evidence",
            str(exit_evidence),
            "--activated-at",
            "2026-08-09T00:00:00Z",
            "--fault-point",
            "after_intent",
        )
        self.assertNotEqual(0, interrupted.returncode, interrupted.stdout)
        self.assert_platform_authority_uncommitted(control_store_root)
        with sqlite3.connect(
            control_store_root / "platform-kernel-control.sqlite3"
        ) as database:
            prepared = database.execute(
                "SELECT COUNT(*) FROM platform_cutover_intents WHERE state='PREPARED'"
            ).fetchone()[0]
        self.assertEqual(1, prepared)

        reconciled = _run_formal_cli(
            "platform-kernel-reconcile",
            "--platform",
            "bilibili",
            "--control-store-root",
            str(control_store_root),
        )

        self.assertNotEqual(0, reconciled.returncode, reconciled.stdout)
        self.assert_platform_authority_uncommitted(control_store_root)

    def test_require_current_rejects_locally_activated_unpublished_evidence(
        self,
    ) -> None:
        control_store_root, exit_evidence = self._write_schema_valid_slice12_manifest()
        activated = _run_cli(
            "platform-kernel-activate",
            "--platform",
            "bilibili",
            "--control-store-root",
            str(control_store_root),
            "--exit-evidence",
            str(exit_evidence),
            "--activated-at",
            "2026-08-09T00:00:00Z",
        )
        self.assertEqual(0, activated.returncode, activated.stdout)

        checked = _run_formal_cli(
            "workflow-policy-check",
            "--control-store-root",
            str(control_store_root),
        )

        self.assertNotEqual(0, checked.returncode, checked.stdout)

    def test_failed_atomic_member_preserves_bilibili_legacy_authority(
        self,
    ) -> None:
        control_store_root, exit_evidence = self._write_valid_cutover_manifest(
            status_overrides={"validators": "failed"}
        )

        completed = _run_cli(
            "platform-kernel-activate",
            "--platform",
            "bilibili",
            "--control-store-root",
            str(control_store_root),
            "--exit-evidence",
            str(exit_evidence),
            "--activated-at",
            "2026-08-09T00:00:00Z",
        )
        envelope = json.loads(completed.stdout)

        self.assertNotEqual(0, completed.returncode)
        self.assertEqual(
            {
                "first_failing_gate": "atomic_member_status",
                "error_code": "bilibili_cutover_atomic_member_failed",
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
                / "bilibili.json"
            ).exists()
        )

    def test_bilibili_activation_rejects_youtube_scope_expansion(
        self,
    ) -> None:
        control_store_root, exit_evidence = self._write_valid_cutover_manifest(
            components_activated=[
                "bilibili_platform_kernel",
                "youtube_platform_kernel",
            ]
        )

        completed = _run_cli(
            "platform-kernel-activate",
            "--platform",
            "bilibili",
            "--control-store-root",
            str(control_store_root),
            "--exit-evidence",
            str(exit_evidence),
            "--activated-at",
            "2026-08-09T00:00:00Z",
        )
        envelope = json.loads(completed.stdout)

        self.assertNotEqual(0, completed.returncode)
        self.assertEqual(
            {
                "first_failing_gate": "activation_scope",
                "error_code": "bilibili_activation_scope_invalid",
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
                / "bilibili.json"
            ).exists()
        )

    def test_exact_bilibili_activation_retry_is_idempotent(self) -> None:
        control_store_root, exit_evidence = self._write_valid_cutover_manifest()
        arguments = (
            "platform-kernel-activate",
            "--platform",
            "bilibili",
            "--control-store-root",
            str(control_store_root),
            "--exit-evidence",
            str(exit_evidence),
            "--activated-at",
            "2026-08-09T00:00:00Z",
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
                    "platform": "bilibili",
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

    def test_interrupted_bilibili_activation_reconciles_and_exact_retry_is_idempotent(
        self,
    ) -> None:
        control_store_root, exit_evidence = self._write_valid_cutover_manifest()
        activation_arguments = (
            "platform-kernel-activate",
            "--platform",
            "bilibili",
            "--control-store-root",
            str(control_store_root),
            "--exit-evidence",
            str(exit_evidence),
            "--activated-at",
            "2026-08-09T00:00:00Z",
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
            "bilibili",
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
                    "platform": "bilibili",
                    "authority_status": "current",
                    "generation": 1,
                },
                "retry_returncode": 0,
                "retry_data": {
                    "platform": "bilibili",
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

    def test_workflow_policy_check_reports_bilibili_kernel_and_youtube_legacy(
        self,
    ) -> None:
        root = new_case_dir(self.id(), label="issue13-combined-policy")
        repository, global_evidence = build_current_global_gate_authority(root)
        global_authority = GlobalGatePublisher(project_root=repository).activate(
            control_store_root=root,
            exit_evidence=global_evidence,
            activated_at="2026-08-09T00:00:00Z",
        )
        _, platform_evidence = self._write_valid_cutover_manifest(
            control_store_root=root,
            global_gate_binding={
                "activation_status": "active_global_gate",
                "authority_path": global_authority["authority_path"],
                "authority_sha256": global_authority["authority_sha256"],
                "generation": global_authority["generation"],
            },
        )
        self.assertEqual(global_authority["generation"], 1)
        activated = _run_cli(
            "platform-kernel-activate",
            "--platform",
            "bilibili",
            "--control-store-root",
            str(root),
            "--exit-evidence",
            str(platform_evidence),
            "--activated-at",
            "2026-08-09T00:01:00Z",
        )
        self.assertEqual(0, activated.returncode, activated.stdout)

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
                    "youtube": "active_legacy",
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

    def test_bilibili_activation_rejects_stale_global_gate_binding(self) -> None:
        root = new_case_dir(self.id(), label="issue13-global-binding")
        repository, global_evidence = build_current_global_gate_authority(root)
        GlobalGatePublisher(project_root=repository).activate(
            control_store_root=root,
            exit_evidence=global_evidence,
            activated_at="2026-08-09T00:00:00Z",
        )
        _, platform_evidence = self._write_valid_cutover_manifest()
        (root / "active_global_gate.json").write_bytes(b"{}\n")

        completed = _run_cli(
            "platform-kernel-activate",
            "--platform",
            "bilibili",
            "--control-store-root",
            str(root),
            "--exit-evidence",
            str(platform_evidence),
            "--activated-at",
            "2026-08-09T00:01:00Z",
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
        self.assertFalse((root / "platform-authorities" / "bilibili.json").exists())


if __name__ == "__main__":
    unittest.main()
