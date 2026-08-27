from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any

from jsonschema import Draft202012Validator, ValidationError

from .acceptance_v2 import AcceptanceV2Provider
from .contracts import ContractRegistry
from .control_store import ControlStore
from .errors import (
    AcceptanceV2Rejected,
    ContractError,
    ControlStoreUnavailable,
    KernelConflict,
    PlatformKernelFault,
)
from .evidence import (
    EvidenceSupportError,
    exit_evidence_revalidation_enabled,
    git_output,
    sha256_git_archive,
    sha256_git_blob,
)
from .global_gate import GlobalGatePublisher
from .kernel import VideoWorkflowKernel
from .guarded_delivery import (
    require_current_kernel_guarded_decision,
    validate_acceptance_report,
    validate_delivery_guard_report,
)
from .utils import (
    canonical_json_bytes,
    read_json,
    require_safe_path_segment,
    sha256_file,
    write_json_atomic,
)


PLATFORM_KERNEL_DB = "platform-kernel-control.sqlite3"
PLATFORM_AUTHORITY_DIR = "platform-authorities"
SUPPORTED_PLATFORMS = frozenset({"bilibili", "youtube"})
PLATFORM_SPECS = {
    "bilibili": {
        "display_name": "Bilibili",
        "error_prefix": "bilibili",
        "expected_slice": {
            "number": 12,
            "name": "bilibili-platform-kernel-cutover",
        },
        "expected_activation_scope": {
            "kind": "platform_kernel_cutover",
            "runtime_authority_change": True,
            "components_activated": ["bilibili_platform_kernel"],
            "platform": "bilibili",
            "global_gate_authority": "unchanged",
            "qualification_contract_sha256": (
                "927022a0bcf5f626f4b9275928dce9de201775523ab1bf4c0c9b6803f0012461"
            ),
        },
        "atomic_members": frozenset(
            {
                "bilibili_adapter",
                "kernel_run_authority",
                "task_ownership",
                "delivery_contracts",
                "delivery_lifecycle",
                "acceptance_v2_binding",
                "delivery_guard_binding",
                "hooks",
                "bilibili_skill",
                "project_instructions",
                "validators",
                "tests",
                "activation_documentation",
                "guarded_delivery_evidence",
            }
        ),
        "platform_statuses": {
            "bilibili": "active_kernel",
            "youtube": "active_legacy",
        },
        "qualification_test_module": (
            "tests.video_workflow.test_issue13_exit_evidence"
        ),
        "collection_schema_name": "issue13-exit-evidence-collection",
    },
    "youtube": {
        "display_name": "YouTube",
        "error_prefix": "youtube",
        "expected_slice": {
            "number": 13,
            "name": "youtube-platform-kernel-cutover",
        },
        "expected_activation_scope": {
            "kind": "platform_kernel_cutover",
            "runtime_authority_change": True,
            "components_activated": ["youtube_platform_kernel"],
            "platform": "youtube",
            "global_gate_authority": "unchanged",
            "qualification_contract_sha256": (
                "ee5d74bbb55b1854d495af62be1a3d784021b636f58dce50f55039eef8a2df31"
            ),
        },
        "atomic_members": frozenset(
            {
                "youtube_adapter",
                "kernel_run_authority",
                "task_ownership",
                "delivery_contracts",
                "delivery_lifecycle",
                "acceptance_v2_binding",
                "delivery_guard_binding",
                "hooks",
                "youtube_skill",
                "project_instructions",
                "validators",
                "tests",
                "activation_documentation",
                "guarded_delivery_evidence",
            }
        ),
        "platform_statuses": {
            "bilibili": "active_kernel",
            "youtube": "active_kernel",
        },
        "qualification_test_module": (
            "tests.video_workflow.test_issue14_exit_evidence"
        ),
        "collection_schema_name": "issue14-exit-evidence-collection",
    },
}
ACTIVATION_FAULT_POINTS = frozenset(
    {"after_intent", "after_authority_write", "after_control_commit"}
)
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _platform_spec(platform: str) -> dict[str, Any]:
    """Validate a platform name and return its Platform Kernel cutover spec."""
    spec = PLATFORM_SPECS.get(platform)
    if spec is None:
        raise ContractError(
            "Only the Bilibili or YouTube Platform Kernel Cutover is active"
        )
    return spec


def _require_formal_exit_evidence(evidence_path: Path, platform: str) -> None:
    """Bind platform authority to the repository's post-publication validator."""

    spec = _platform_spec(platform)
    validator_path = PROJECT_ROOT / "scripts" / "validate_slice_exit_evidence.py"
    try:
        spec_loader = importlib.util.spec_from_file_location(
            f"video2pdf_{spec['error_prefix']}_exit_evidence_validator",
            validator_path,
        )
        if spec_loader is None or spec_loader.loader is None:
            raise RuntimeError("validator module cannot be loaded")
        module = importlib.util.module_from_spec(spec_loader)
        spec_loader.loader.exec_module(module)
        module.validate_manifest(
            evidence_path.resolve(), schema_only=False, pre_publication=False
        )
    except Exception as exc:
        raise ContractError(
            f"{spec['display_name']} Platform Kernel requires current "
            "post-publication Exit Evidence",
            data={
                "first_failing_gate": "exit_evidence_lineage",
                "error_code": f"{spec['error_prefix']}_exit_evidence_lineage_invalid",
            },
        ) from exc


def _fingerprint(value: dict[str, Any], field: str) -> str:
    payload = {key: item for key, item in value.items() if key != field}
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _evidence_publication_commit(
    implementation_commit: str, relative_manifest: str
) -> str | None:
    """Locate the evidence publication commit: the direct child of the
    implementation commit that published the identical manifest blob."""
    try:
        head_blob = git_output(
            PROJECT_ROOT, "rev-parse", f"HEAD:{relative_manifest}"
        )
        for candidate in git_output(
            PROJECT_ROOT, "log", "--format=%H", "HEAD", "--", relative_manifest
        ).splitlines():
            parents = git_output(
                PROJECT_ROOT, "rev-list", "--parents", "-n", "1", candidate
            ).split()
            if len(parents) != 2 or parents[1] != implementation_commit:
                continue
            try:
                candidate_blob = git_output(
                    PROJECT_ROOT, "rev-parse", f"{candidate}:{relative_manifest}"
                )
            except EvidenceSupportError:
                continue
            if candidate_blob == head_blob:
                return candidate
    except EvidenceSupportError:
        return None
    return None


def _evidence_path(
    binding: Any,
    *,
    label: str,
    allow_absolute: bool,
    anchor_commits: tuple[str, ...] = (),
) -> Path:
    if not isinstance(binding, dict):
        raise ContractError(f"Bilibili cutover {label} binding is absent")
    raw_path = binding.get("path")
    expected_sha = binding.get("sha256")
    if not isinstance(raw_path, str) or not isinstance(expected_sha, str):
        raise ContractError(f"Bilibili cutover {label} binding is invalid")
    candidate = Path(raw_path)
    if candidate.is_absolute():
        if not allow_absolute:
            raise ContractError(f"Bilibili cutover {label} path must be project-relative")
        path = candidate.resolve()
    else:
        path = (PROJECT_ROOT / candidate).resolve()
    if not path.is_relative_to(PROJECT_ROOT) or not path.is_file():
        raise ContractError(f"Bilibili cutover {label} escapes trusted evidence storage")
    if sha256_file(path) != expected_sha:
        # Delivery projections legitimately evolve after a delivered cutover
        # (session archival, task-index ownership updates).  The evidence
        # identity stays anchored to the immutable publication history: the
        # blob at the manifest implementation_commit — or, for files first
        # committed by the publication itself, at the publication commit —
        # is the canonical content.
        anchored = False
        relative = path.relative_to(PROJECT_ROOT).as_posix()
        for commit in anchor_commits:
            try:
                if sha256_git_blob(PROJECT_ROOT, commit, relative) == expected_sha:
                    anchored = True
                    break
            except EvidenceSupportError:
                continue
        if not anchored:
            raise ContractError(f"Bilibili cutover {label} fingerprint is stale")
    return path


def _validate_guarded_delivery(
    value: dict[str, Any], platform: str, publication_commit: str | None = None
) -> None:
    spec = _platform_spec(platform)
    prefix = spec["error_prefix"]
    display_name = spec["display_name"]
    guarded = value.get("guarded_delivery_evidence")
    expected_roles = {
        "run_record",
        "source_manifest",
        "acceptance_report_v2",
        "delivery_guard_report",
        "video_delivery_target",
        "session_delivery_target",
        "delivery_task_index",
        "global_gate_authority",
        "final_pdf",
    }
    if not isinstance(guarded, dict):
        raise ContractError(
            f"{display_name} cutover lacks collected guarded-delivery evidence",
            data={
                "first_failing_gate": "guarded_delivery_evidence",
                "error_code": "guarded_delivery_evidence_missing",
            },
        )
    artifacts = guarded.get("artifacts")
    if not isinstance(artifacts, list):
        artifacts = []
    manifest_artifacts = {
        item.get("role"): item for item in artifacts if isinstance(item, dict)
    }
    if (
        guarded.get("canonical_platform") != platform
        or guarded.get("delivery_stage") != "delivered"
        or set(manifest_artifacts) != expected_roles
    ):
        raise ContractError(
            f"{display_name} guarded-delivery evidence is incomplete",
            data={
                "first_failing_gate": "guarded_delivery_evidence",
                "error_code": "guarded_delivery_evidence_invalid",
            },
        )
    try:
        collection_path = _evidence_path(
            guarded.get("collection"),
            label="guarded-delivery collection",
            allow_absolute=False,
        )
        collection = read_json(collection_path)
        if (
            collection.get("schema_name") != spec["collection_schema_name"]
            or collection.get("run_id") != guarded.get("run_id")
            or collection.get("canonical_platform") != platform
            or collection.get("delivery_stage") != "delivered"
        ):
            raise ContractError(
                f"{display_name} guarded-delivery collection identity is invalid"
            )
        collected_artifacts = collection.get("artifacts")
        if not isinstance(collected_artifacts, dict) or set(collected_artifacts) != expected_roles:
            raise ContractError(
                f"{display_name} guarded-delivery collection artifact set is invalid"
            )
        resolved_artifacts: dict[str, Path] = {}
        guarded_implementation_commit = value.get("implementation_commit")
        anchor_commits: tuple[str, ...] = ()
        if isinstance(guarded_implementation_commit, str):
            anchors = [guarded_implementation_commit]
            if publication_commit and publication_commit != guarded_implementation_commit:
                anchors.append(publication_commit)
            anchor_commits = tuple(anchors)
        for role in expected_roles:
            manifest_path = _evidence_path(
                manifest_artifacts[role],
                label=role,
                allow_absolute=False,
                anchor_commits=anchor_commits,
            )
            collected_path = _evidence_path(
                collected_artifacts[role],
                label=role,
                allow_absolute=True,
                anchor_commits=anchor_commits,
            )
            if (
                manifest_path != collected_path
                or manifest_artifacts[role]["sha256"]
                != collected_artifacts[role]["sha256"]
            ):
                raise ContractError(
                    f"{display_name} guarded-delivery {role} differs from its collection"
                )
            resolved_artifacts[role] = collected_path
        run_id = guarded.get("run_id")
        validate_acceptance_report(
            project_root=PROJECT_ROOT,
            report_path=resolved_artifacts["acceptance_report_v2"],
            run_id=str(run_id),
        )
        validate_delivery_guard_report(
            report_path=resolved_artifacts["delivery_guard_report"]
        )
        manifest_qualification = guarded.get("qualification_run")
        collected_qualification = collection.get("qualification_run")
        if not isinstance(manifest_qualification, dict) or not isinstance(
            collected_qualification, dict
        ):
            raise ContractError(f"{display_name} qualification evidence is absent")
        if manifest_qualification.get("run_id") != collected_qualification.get("run_id"):
            raise ContractError(f"{display_name} qualification Run identity differs")
        for manifest_key, collected_key in (
            ("command_record", "command_record"),
            ("terminal_status", "terminal_status"),
            ("exit_code", "exit_code_artifact"),
        ):
            manifest_path = _evidence_path(
                manifest_qualification.get(manifest_key),
                label=f"qualification {manifest_key}",
                allow_absolute=False,
            )
            collected_path = _evidence_path(
                collected_qualification.get(collected_key),
                label=f"qualification {manifest_key}",
                allow_absolute=True,
            )
            if manifest_path != collected_path:
                raise ContractError(
                    f"{display_name} qualification binding differs from collection"
                )
        command = read_json(
            _evidence_path(
                collected_qualification["command_record"],
                label="qualification command",
                allow_absolute=True,
            )
        )
        status = read_json(
            _evidence_path(
                collected_qualification["terminal_status"],
                label="qualification status",
                allow_absolute=True,
            )
        )
        exit_code = int(
            _evidence_path(
                collected_qualification["exit_code_artifact"],
                label="qualification exit code",
                allow_absolute=True,
            ).read_text(encoding="utf-8").strip()
        )
        expected_qualification_argv = [
            sys.executable,
            "-X",
            "utf8",
            "-B",
            "-m",
            "unittest",
            "-v",
            spec["qualification_test_module"],
        ]
        qualification_argv = command.get("argv")
        qualification_command_matches = (
            isinstance(qualification_argv, list)
            and len(qualification_argv) == len(expected_qualification_argv)
            and isinstance(qualification_argv[0], str)
            and Path(qualification_argv[0]).resolve()
            == Path(expected_qualification_argv[0]).resolve()
            and qualification_argv[1:] == expected_qualification_argv[1:]
        )
        if (
            command.get("run_id") != manifest_qualification.get("run_id")
            or not qualification_command_matches
            or command.get("cwd") != str(PROJECT_ROOT.resolve())
            or command.get("accepted_exit_codes") != [0]
            or status.get("run_id") != manifest_qualification.get("run_id")
            or status.get("state") != "succeeded"
            or status.get("exit_code") != 0
            or exit_code != 0
            or status.get("security", {}).get("acceptance_evidence_eligible") is not True
            or collected_qualification.get("acceptance_evidence_eligible") is not True
        ):
            raise ContractError(
                f"{display_name} qualification Run is not succeeded evidence"
            )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError, KeyError) as exc:
        raise ContractError(
            f"{display_name} guarded-delivery evidence cannot be decoded"
        ) from exc


def _validate_evidence(
    value: Any, platform: str, evidence_path: Path | None = None
) -> dict[str, Any]:
    spec = _platform_spec(platform)
    prefix = spec["error_prefix"]
    display_name = spec["display_name"]
    expected_slice = spec["expected_slice"]
    expected_activation_scope = spec["expected_activation_scope"]
    atomic_members = spec["atomic_members"]
    expected_statuses = spec["platform_statuses"]
    if not isinstance(value, dict):
        raise ContractError(f"{display_name} cutover Exit Evidence must be an object")
    if (
        value.get("kind") != "video-workflow-exit-evidence"
        or value.get("schema_version") != 2
        or value.get("slice") != expected_slice
        or value.get("overall_decision") != "pass"
        or value.get("platform_statuses") != expected_statuses
    ):
        raise ContractError(f"{display_name} cutover Exit Evidence identity is invalid")
    scope = value.get("activation_scope")
    if not isinstance(scope, dict):
        raise ContractError(f"{display_name} cutover activation scope is invalid")
    comparable_scope = {
        key: scope.get(key) for key in expected_activation_scope
    }
    if comparable_scope != expected_activation_scope:
        raise ContractError(
            f"{display_name} cutover activation scope is invalid",
            data={
                "first_failing_gate": "activation_scope",
                "error_code": f"{prefix}_activation_scope_invalid",
            },
        )
    if set(value.get("atomic_members", [])) != atomic_members:
        raise ContractError(f"{display_name} cutover atomic member set is incomplete")
    statuses = value.get("atomic_member_status")
    if (
        not isinstance(statuses, dict)
        or set(statuses) != atomic_members
        or any(statuses[member] != "active" for member in atomic_members)
    ):
        raise ContractError(
            f"{display_name} cutover atomic member is inactive",
            data={
                "first_failing_gate": "atomic_member_status",
                "error_code": f"{prefix}_cutover_atomic_member_failed",
            },
        )
    publication_commit = None
    evidence_implementation_commit = value.get("implementation_commit")
    if isinstance(evidence_implementation_commit, str) and evidence_path is not None:
        try:
            relative_manifest = evidence_path.resolve().relative_to(
                PROJECT_ROOT
            ).as_posix()
        except ValueError:
            relative_manifest = None
        if relative_manifest:
            publication_commit = _evidence_publication_commit(
                evidence_implementation_commit, relative_manifest
            )
    _validate_guarded_delivery(value, platform, publication_commit)
    implementation_commit = value.get("implementation_commit")
    fingerprints = value.get("artifact_fingerprints")
    try:
        if not isinstance(implementation_commit, str):
            raise EvidenceSupportError("implementation commit is absent")
        git_output(
            PROJECT_ROOT,
            "cat-file",
            "-e",
            f"{implementation_commit}^{{commit}}",
        )
        if not isinstance(fingerprints, list) or not fingerprints:
            raise EvidenceSupportError("implementation fingerprints are absent")
        for item in fingerprints:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("path"), str)
                or not isinstance(item.get("sha256"), str)
                or sha256_git_archive(
                    PROJECT_ROOT, implementation_commit, item["path"]
                )
                != item["sha256"]
            ):
                raise EvidenceSupportError(
                    "implementation fingerprint differs from its committed archive"
                )
    except EvidenceSupportError as exc:
        raise ContractError(
            f"{display_name} cutover implementation lineage is invalid",
            data={
                "first_failing_gate": "implementation_artifacts",
                "error_code": f"{prefix}_implementation_lineage_invalid",
            },
        ) from exc
    schema_path = (
        PROJECT_ROOT
        / "schemas"
        / "exit-evidence-manifest.v2.schema.json"
    )
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(value)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise ContractError(
            f"{display_name} cutover Exit Evidence is schema-invalid",
            data={
                "first_failing_gate": "exit_evidence_contract",
                "error_code": f"{prefix}_exit_evidence_schema_invalid",
            },
        ) from exc
    return value


class BilibiliPlatformCutoverPublisher:
    """Owns the independent authority transfer for Platform Kernel Runs."""

    @staticmethod
    def _platform_spec(platform: str) -> dict[str, Any]:
        """Validate a platform name and return its cutover spec."""
        return _platform_spec(platform)

    def _fallback_platform_statuses(
        self, *, platform: str, control_store_root: Path
    ) -> dict[str, str]:
        """Return the pre-cutover fallback statuses for one platform.

        The queried platform is reported ``active_legacy`` while the other
        platform keeps its CURRENT committed authority status when one exists.
        """
        self._platform_spec(platform)
        statuses = {"bilibili": "active_legacy", "youtube": "active_legacy"}
        other = next(name for name in SUPPORTED_PLATFORMS if name != platform)
        root = control_store_root.resolve()
        if (root / PLATFORM_KERNEL_DB).is_file():
            try:
                with self._connect(root) as connection:
                    row = connection.execute(
                        "SELECT 1 FROM platform_cutover_authority WHERE platform=?",
                        (other,),
                    ).fetchone()
                if row is not None:
                    statuses[other] = "active_kernel"
            except ControlStoreUnavailable:
                pass
        return statuses

    def _connect(self, root: Path) -> sqlite3.Connection:
        if not root.is_dir():
            raise ControlStoreUnavailable(
                "Platform cutover control-store root is unavailable"
            )
        try:
            connection = sqlite3.connect(
                root / PLATFORM_KERNEL_DB,
                timeout=0.05,
                isolation_level=None,
            )
            connection.row_factory = sqlite3.Row
            connection.execute(
                "CREATE TABLE IF NOT EXISTS platform_cutover_authority ("
                "platform TEXT PRIMARY KEY, generation INTEGER NOT NULL, "
                "evidence_sha256 TEXT NOT NULL, authority_sha256 TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS platform_cutover_intents ("
                "intent_id TEXT PRIMARY KEY, platform TEXT NOT NULL UNIQUE, "
                "evidence_sha256 TEXT NOT NULL, authority_json TEXT NOT NULL, "
                "candidate_snapshot_sha256 TEXT, "
                "state TEXT NOT NULL CHECK(state IN ('PREPARED','COMMITTED')))"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS platform_authority_refresh_intents ("
                "intent_id TEXT PRIMARY KEY, platform TEXT NOT NULL, "
                "expected_generation INTEGER NOT NULL, "
                "evidence_sha256 TEXT NOT NULL, authority_json TEXT NOT NULL, "
                "state TEXT NOT NULL CHECK(state IN ('PREPARED','COMMITTED')))"
            )
            intent_columns = {
                str(column["name"])
                for column in connection.execute(
                    "PRAGMA table_info(platform_cutover_intents)"
                ).fetchall()
            }
            if "candidate_snapshot_sha256" not in intent_columns:
                connection.execute(
                    "ALTER TABLE platform_cutover_intents "
                    "ADD COLUMN candidate_snapshot_sha256 TEXT"
                )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS platform_cutover_candidates ("
                "platform TEXT PRIMARY KEY, candidate_run_id TEXT NOT NULL, "
                "source_identity TEXT NOT NULL, session_id TEXT NOT NULL, "
                "global_gate_sha256 TEXT NOT NULL, implementation_commit TEXT NOT NULL, "
                "probe_sha256 TEXT NOT NULL, candidate_json TEXT NOT NULL, "
                "state TEXT NOT NULL CHECK(state IN "
                "('PREPARED','INITIALIZING','INITIALIZED','PROVISIONAL','CONFIRMED')))"
            )
            candidate_table = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' "
                "AND name='platform_cutover_candidates'"
            ).fetchone()
            if candidate_table is not None and "'INITIALIZING'" not in str(
                candidate_table["sql"]
            ):
                connection.execute("BEGIN EXCLUSIVE")
                try:
                    connection.execute(
                        "ALTER TABLE platform_cutover_candidates "
                        "RENAME TO platform_cutover_candidates_v1"
                    )
                    connection.execute(
                        "CREATE TABLE platform_cutover_candidates ("
                        "platform TEXT PRIMARY KEY, candidate_run_id TEXT NOT NULL, "
                        "source_identity TEXT NOT NULL, session_id TEXT NOT NULL, "
                        "global_gate_sha256 TEXT NOT NULL, implementation_commit TEXT NOT NULL, "
                        "probe_sha256 TEXT NOT NULL, candidate_json TEXT NOT NULL, "
                        "state TEXT NOT NULL CHECK(state IN "
                        "('PREPARED','INITIALIZING','INITIALIZED','PROVISIONAL','CONFIRMED')))"
                    )
                    connection.execute(
                        "INSERT INTO platform_cutover_candidates SELECT * "
                        "FROM platform_cutover_candidates_v1"
                    )
                    connection.execute("DROP TABLE platform_cutover_candidates_v1")
                    connection.execute("COMMIT")
                except Exception:
                    connection.execute("ROLLBACK")
                    raise
            return connection
        except (OSError, sqlite3.DatabaseError) as exc:
            raise ControlStoreUnavailable(
                "Platform cutover control store is unavailable"
            ) from exc

    @staticmethod
    def _candidate_snapshot(row: sqlite3.Row) -> dict[str, Any]:
        try:
            candidate = json.loads(row["candidate_json"])
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise KernelConflict(
                "Bilibili candidate snapshot cannot be decoded",
                data={
                    "first_failing_gate": "platform_kernel_candidate",
                    "error_code": "bilibili_candidate_snapshot_invalid",
                },
            ) from exc
        if (
            not isinstance(candidate, dict)
            or candidate.get("state") != row["state"]
            or candidate.get("candidate_run_id") != row["candidate_run_id"]
            or candidate.get("source_identity") != row["source_identity"]
            or candidate.get("candidate_session_id") != row["session_id"]
            or candidate.get("implementation_commit") != row["implementation_commit"]
            or candidate.get("probe_sha256") != row["probe_sha256"]
            or not isinstance(candidate.get("global_gate_binding"), dict)
            or candidate["global_gate_binding"].get("authority_sha256")
            != row["global_gate_sha256"]
        ):
            raise KernelConflict(
                "Bilibili candidate SQL and JSON states differ",
                data={
                    "first_failing_gate": "platform_kernel_candidate",
                    "error_code": "bilibili_candidate_state_inconsistent",
                },
            )
        return candidate

    @classmethod
    def _confirmation_snapshot_fingerprint(
        cls, row: sqlite3.Row, evidence: dict[str, Any]
    ) -> str:
        candidate = cls._candidate_snapshot(row)
        guarded = evidence.get("guarded_delivery_evidence")
        artifacts = guarded.get("artifacts") if isinstance(guarded, dict) else None
        if not isinstance(artifacts, list) or len(artifacts) != 9:
            raise KernelConflict(
                "Bilibili candidate confirmation snapshot is incomplete"
            )
        current_artifacts = []
        for item in sorted(artifacts, key=lambda value: str(value.get("role", ""))):
            if not isinstance(item, dict):
                raise KernelConflict(
                    "Bilibili candidate confirmation snapshot is invalid"
                )
            raw_path = Path(str(item.get("path", "")))
            path = (
                raw_path.resolve()
                if raw_path.is_absolute()
                else (PROJECT_ROOT / raw_path).resolve()
            )
            if not path.is_file():
                raise KernelConflict(
                    "Bilibili candidate confirmation artifact is unavailable"
                )
            current_artifacts.append(
                {
                    "role": item.get("role"),
                    "path": str(path),
                    "sha256": sha256_file(path),
                }
            )
        return hashlib.sha256(
            canonical_json_bytes(
                {
                    "candidate": candidate,
                    "run_id": guarded.get("run_id"),
                    "artifacts": current_artifacts,
                }
            )
        ).hexdigest()

    def prepare_candidate(
        self,
        *,
        platform: str,
        control_store_root: Path,
        implementation_commit: str,
        candidate_probe: Path,
        candidate_session_id: str,
        prepared_at: str,
    ) -> dict[str, Any]:
        """Durably bind the single pre-confirmation Run without activating it."""

        spec = self._platform_spec(platform)
        display_name = spec["display_name"]
        session_id = require_safe_path_segment(
            candidate_session_id,
            purpose="cutover candidate session_id",
            error_type=ContractError,
        )
        probe_path = candidate_probe.resolve()
        if not probe_path.is_file():
            raise ContractError(f"{display_name} cutover candidate probe is unavailable")
        probe = read_json(probe_path)
        adapter = probe.get("adapter")
        if (
            probe.get("schema_name") != "bootstrap-record"
            or probe.get("schema_version") != "2.0.0"
            or probe.get("status") != "probe_complete"
            or not isinstance(adapter, dict)
            or adapter.get("canonical_platform") != platform
            or probe.get("canonical_platform") != platform
            or not isinstance(probe.get("run_id"), str)
            or len(probe["run_id"]) != 32
            or not isinstance(probe.get("source_identity"), str)
            or len(probe["source_identity"]) != 64
        ):
            raise ContractError("Bilibili cutover candidate probe identity is invalid")
        try:
            git_output(
                PROJECT_ROOT,
                "cat-file",
                "-e",
                f"{implementation_commit}^{{commit}}",
            )
            if git_output(PROJECT_ROOT, "rev-parse", "HEAD") != implementation_commit:
                raise EvidenceSupportError(
                    "cutover candidate implementation commit is not current HEAD"
                )
        except EvidenceSupportError as exc:
            raise ContractError(
                "Bilibili cutover candidate implementation lineage is invalid",
                data={
                    "first_failing_gate": "implementation_artifacts",
                    "error_code": "bilibili_candidate_implementation_invalid",
                },
            ) from exc

        root = control_store_root.resolve()
        global_gate = GlobalGatePublisher().require_current(control_store_root=root)
        candidate = {
            "schema_name": "platform-kernel-cutover-candidate",
            "schema_version": "1.0.0",
            "platform": platform,
            "candidate_run_id": probe["run_id"],
            "source_identity": probe["source_identity"],
            "candidate_session_id": session_id,
            "global_gate_binding": {
                "activation_status": "active_global_gate",
                "authority_path": global_gate["path"],
                "authority_sha256": global_gate["file_sha256"],
                "generation": global_gate["generation"],
            },
            "implementation_commit": implementation_commit,
            "probe_path": str(probe_path),
            "probe_sha256": sha256_file(probe_path),
            "prepared_at": prepared_at,
            "state": "PREPARED",
        }
        encoded = json.dumps(candidate, sort_keys=True, separators=(",", ":"))
        reprepared = False
        candidate_run_dir: str | None = None
        with self._connect(root) as connection:
            connection.execute("BEGIN IMMEDIATE")
            active = connection.execute(
                "SELECT 1 FROM platform_cutover_authority WHERE platform=?",
                (platform,),
            ).fetchone()
            if active is not None:
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    "Bilibili Platform Kernel is already confirmed",
                    data={
                        "first_failing_gate": "platform_kernel_authority",
                        "error_code": "bilibili_platform_authority_already_confirmed",
                    },
                )
            existing = connection.execute(
                "SELECT * FROM platform_cutover_candidates "
                "WHERE platform=?",
                (platform,),
            ).fetchone()
            if existing is not None:
                existing_candidate = self._candidate_snapshot(existing)
                if existing["state"] == "INITIALIZED":
                    rebound_candidate = self._require_initialized_reprepare(
                        root=root,
                        connection=connection,
                        row=existing,
                        requested_candidate=candidate,
                        platform=platform,
                    )
                    candidate_run_dir = rebound_candidate["candidate_run_dir"]
                    if (
                        existing["implementation_commit"] == implementation_commit
                        and existing_candidate.get("prepared_at") == prepared_at
                    ):
                        connection.execute("COMMIT")
                        idempotent = True
                    elif existing["implementation_commit"] == implementation_commit:
                        connection.execute("ROLLBACK")
                        raise KernelConflict(
                            "Bilibili initialized candidate reprepare is not an exact retry"
                        )
                    else:
                        rebound_candidate["implementation_commit"] = implementation_commit
                        rebound_candidate["prepared_at"] = prepared_at
                        rebound_encoded = json.dumps(
                            rebound_candidate, sort_keys=True, separators=(",", ":")
                        )
                        updated = connection.execute(
                            "UPDATE platform_cutover_candidates SET "
                            "implementation_commit=?,candidate_json=? "
                            "WHERE platform=? AND state='INITIALIZED' "
                            "AND implementation_commit=? AND candidate_json=?",
                            (
                                implementation_commit,
                                rebound_encoded,
                                platform,
                                existing["implementation_commit"],
                                existing["candidate_json"],
                            ),
                        )
                        if updated.rowcount != 1:
                            connection.execute("ROLLBACK")
                            raise KernelConflict(
                                "Bilibili cutover candidate changed during preparation"
                            )
                        connection.execute("COMMIT")
                        idempotent = False
                        reprepared = True
                elif existing["state"] != "PREPARED":
                    connection.execute("ROLLBACK")
                    raise KernelConflict(
                        "A different Bilibili cutover candidate is already prepared"
                    )
                elif existing["candidate_json"] == encoded:
                    connection.execute("COMMIT")
                    idempotent = True
                elif existing["state"] == "PREPARED":
                    rebound_candidate = dict(existing_candidate)
                    rebound_candidate["implementation_commit"] = implementation_commit
                    rebound_candidate["prepared_at"] = prepared_at
                    if (
                        existing["implementation_commit"] == implementation_commit
                        or "workspace_root" in existing_candidate
                        or "candidate_run_dir" in existing_candidate
                        or rebound_candidate != candidate
                    ):
                        connection.execute("ROLLBACK")
                        raise KernelConflict(
                            "A different Bilibili cutover candidate is already prepared"
                        )
                    updated = connection.execute(
                        "UPDATE platform_cutover_candidates SET "
                        "implementation_commit=?,candidate_json=? "
                        "WHERE platform=? AND state='PREPARED' "
                        "AND implementation_commit=? AND candidate_json=?",
                        (
                            implementation_commit,
                            encoded,
                            platform,
                            existing["implementation_commit"],
                            existing["candidate_json"],
                        ),
                    )
                    if updated.rowcount != 1:
                        connection.execute("ROLLBACK")
                        raise KernelConflict(
                            "Bilibili cutover candidate changed during preparation"
                        )
                    connection.execute("COMMIT")
                    idempotent = False
                    reprepared = True
            else:
                connection.execute(
                    "INSERT INTO platform_cutover_candidates("
                    "platform,candidate_run_id,source_identity,session_id,"
                    "global_gate_sha256,implementation_commit,probe_sha256,"
                    "candidate_json,state) VALUES(?,?,?,?,?,?,?,?, 'PREPARED')",
                    (
                        platform,
                        candidate["candidate_run_id"],
                        candidate["source_identity"],
                        session_id,
                        candidate["global_gate_binding"]["authority_sha256"],
                        implementation_commit,
                        candidate["probe_sha256"],
                        encoded,
                    ),
                )
                connection.execute("COMMIT")
                idempotent = False
        return {
            "authority_status": "prepared_candidate",
            "candidate_run_id": candidate["candidate_run_id"],
            "candidate_session_id": session_id,
            "source_identity": candidate["source_identity"],
            "global_gate_binding": candidate["global_gate_binding"],
            "implementation_commit": implementation_commit,
            "platform_statuses": self._fallback_platform_statuses(
                platform=platform, control_store_root=root
            ),
            "idempotent": idempotent,
            "reprepared": reprepared,
            **(
                {"candidate_run_dir": candidate_run_dir}
                if candidate_run_dir is not None
                else {}
            ),
        }

    def _require_initialized_reprepare(
        self,
        *,
        root: Path,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        requested_candidate: dict[str, Any],
        platform: str,
    ) -> dict[str, Any]:
        """Prove that an initialized candidate still has only initialization authority."""

        spec = self._platform_spec(platform)
        display_name = spec["display_name"]
        candidate = self._candidate_snapshot(row)
        comparable = dict(candidate)
        comparable.pop("workspace_root", None)
        comparable.pop("candidate_run_dir", None)
        comparable["state"] = "PREPARED"
        comparable["implementation_commit"] = requested_candidate[
            "implementation_commit"
        ]
        comparable["prepared_at"] = requested_candidate["prepared_at"]
        if comparable != requested_candidate:
            raise KernelConflict(
                f"{display_name} initialized candidate identity differs from preparation",
                data={
                    "first_failing_gate": "platform_kernel_candidate",
                    "error_code": "bilibili_initialized_reprepare_identity_mismatch",
                },
            )
        pending_platform_intent = connection.execute(
            "SELECT 1 FROM platform_cutover_intents WHERE platform=? AND state='PREPARED'",
            (platform,),
        ).fetchone()
        if pending_platform_intent is not None:
            raise KernelConflict(
                f"{display_name} initialized candidate has a pending platform intent",
                data={
                    "first_failing_gate": "platform_kernel_authority",
                    "error_code": "bilibili_initialized_reprepare_intent_pending",
                },
            )

        run_dir, run, video = self._current_candidate_run(
            root=root, row=row, expected_stage="generating", platform=platform
        )
        workspace = Path(str(candidate.get("workspace_root", ""))).resolve()
        if run_dir.parent != workspace:
            raise KernelConflict(
                f"{display_name} initialized candidate Run path differs from preparation",
                data={
                    "first_failing_gate": "path_boundary",
                    "error_code": "bilibili_initialized_reprepare_run_path_invalid",
                },
            )
        run_path = run_dir / "workflow" / "run.json"
        projections = run["delivery"]["projections"]
        project_root = run_dir.parents[1]
        session_path = self._projection_path(
            projections.get("session_target"),
            base=run_dir,
            allowed_root=project_root,
            label="session target",
        )
        index_path = self._projection_path(
            projections.get("task_index"),
            base=run_dir,
            allowed_root=project_root,
            label="task index",
        )
        session = read_json(session_path)
        index = read_json(index_path)
        matching_entries = [
            item for item in index.get("entries", []) if item.get("run_id") == run["run_id"]
        ]
        entry = matching_entries[0] if len(matching_entries) == 1 else {}
        if run.get("source_state") == "decision_ready":
            self._require_decision_ready_reprepare(
                run_dir=run_dir,
                run=run,
                video=video,
                session=session,
                index=index,
                entry=entry,
                run_connection_path=workspace / ".workflow-control" / "control.sqlite3",
                session_id=row["session_id"],
            )
            return candidate
        if run.get("source_state") == "ready":
            self._require_source_ready_reprepare(
                run_dir=run_dir,
                run=run,
                video=video,
                session=session,
                index=index,
                entry=entry,
                session_id=row["session_id"],
                prior_implementation_commit=row["implementation_commit"],
                requested_implementation_commit=requested_candidate[
                    "implementation_commit"
                ],
            )
            return candidate
        if (
            run.get("coordination_revision") != 1
            or run.get("source_epoch") != 1
            or run.get("source_state") != "pending"
            or run.get("source_blocker") is not None
            or run.get("source_version") is not None
            or run.get("phase") != "source_acquisition"
            or run.get("last_mutation_intent_id") is not None
            or set(run.get("artifact_generations", {})) != {"bootstrap_record"}
            or set(run.get("checkpoints", {})) != {"run_initialized"}
            or run["checkpoints"]["run_initialized"].get("status") != "current"
            or (run_dir / "source" / "manifest.json").exists()
            or any(value is not None for value in video.get("artifacts", {}).values())
            or video.get("projection_revision") != 1
            or video.get("run_revision") != 1
            or run["delivery"].get("ownership")
            != {"session_id": row["session_id"], "generation": 1}
            or projections.get("archive") is not None
            or projections["video_target"].get("projection_revision") != 1
            or projections["session_target"].get("projection_revision") != 1
            or session.get("projection_revision") != 1
            or session.get("run_revision") != 1
            or session.get("owner_status") != "active"
            or session.get("ownership_generation") != 1
            or entry.get("run_revision") != 1
            or entry.get("ownership_generation") != 1
            or entry.get("session_id") != row["session_id"]
            or entry.get("archive") is not None
            or entry.get("video_target", {}).get("projection_revision") != 1
            or entry.get("session_target", {}).get("projection_revision") != 1
            or projections["task_index"].get("projection_revision")
            != index.get("projection_revision")
        ):
            raise KernelConflict(
                "Bilibili initialized candidate has progressed beyond initialization",
                data={
                    "first_failing_gate": "platform_kernel_candidate",
                    "error_code": "bilibili_initialized_reprepare_progressed",
                },
            )
        control_database = workspace / ".workflow-control" / "control.sqlite3"
        if not control_database.is_file():
            raise KernelConflict(
                "Bilibili initialized candidate Control Store is unavailable"
            )
        with sqlite3.connect(control_database) as run_connection:
            run_connection.row_factory = sqlite3.Row
            initialization = run_connection.execute(
                "SELECT * FROM initialization_intents WHERE run_id=? AND state='COMMITTED'",
                (run["run_id"],),
            ).fetchone()
            committed_progress = sum(
                run_connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE run_id=? AND state='COMMITTED'",
                    (run["run_id"],),
                ).fetchone()[0]
                for table in (
                    "run_state_mutation_intents",
                    "task_promotion_intents",
                    "source_publication_intents",
                    "delivery_lifecycle_intents",
                )
            )
        if (
            initialization is None
            or initialization["output_path"] != str(run_dir)
            or initialization["run_record_sha256"] != sha256_file(run_path)
            or committed_progress != 0
        ):
            raise KernelConflict(
                "Bilibili initialized candidate Run is not current initialization authority",
                data={
                    "first_failing_gate": "platform_kernel_candidate",
                    "error_code": "bilibili_initialized_reprepare_run_not_current",
                },
            )
        return candidate

    @staticmethod
    def _require_source_ready_reprepare(
        *,
        run_dir: Path,
        run: dict[str, Any],
        video: dict[str, Any],
        session: dict[str, Any],
        index: dict[str, Any],
        entry: dict[str, Any],
        session_id: str,
        prior_implementation_commit: str,
        requested_implementation_commit: str,
    ) -> None:
        """Accept current source authority before diagnostic compilation or delivery publication."""

        if prior_implementation_commit != requested_implementation_commit:
            try:
                requested_lineage = git_output(
                    PROJECT_ROOT,
                    "rev-list",
                    "--parents",
                    "-n",
                    "1",
                    requested_implementation_commit,
                ).split()
                if prior_implementation_commit not in requested_lineage[1:]:
                    raise EvidenceSupportError(
                        "prior implementation commit is not a direct parent"
                    )
            except EvidenceSupportError as exc:
                raise KernelConflict(
                    "Bilibili source-ready candidate implementation is not a direct child",
                    data={
                        "first_failing_gate": "implementation_artifacts",
                        "error_code": "bilibili_candidate_implementation_invalid",
                    },
                ) from exc

        artifact_ids = {
            "bootstrap_record",
            "source_candidate_inventory",
            "source_acquisition_decision_skeleton",
            "source_acquisition_decision",
            "source_transcription",
            "source_manifest",
        }
        checkpoint_ids = {
            "run_initialized",
            "source_candidates_ready",
            "source_acquisition_decision_ready",
            "source_ready",
        }
        projections = run["delivery"]["projections"]
        delivery_artifacts = video.get("artifacts")
        if (
            run.get("coordination_revision") != 5
            or run.get("source_epoch") != 1
            or run.get("source_blocker") is not None
            or run.get("phase") != "source_ready"
            or set(run.get("artifact_generations", {})) != artifact_ids
            or set(run.get("checkpoints", {})) != checkpoint_ids
            or any(
                checkpoint.get("status") != "current"
                for checkpoint in run["checkpoints"].values()
            )
            or not isinstance(delivery_artifacts, dict)
            or any(value is not None for value in delivery_artifacts.values())
            or run["delivery"].get("ownership")
            != {"session_id": session_id, "generation": 1}
            or projections.get("archive") is not None
            or projections["video_target"].get("projection_revision") != 1
            or projections["session_target"].get("projection_revision") != 1
            or video.get("projection_revision") != 1
            or video.get("run_revision") != 1
            or session.get("projection_revision") != 1
            or session.get("run_revision") != 1
            or session.get("owner_status") != "active"
            or session.get("ownership_generation") != 1
            or entry.get("run_revision") != 1
            or entry.get("ownership_generation") != 1
            or entry.get("session_id") != session_id
            or entry.get("archive") is not None
            or entry.get("video_target", {}).get("projection_revision") != 1
            or entry.get("session_target", {}).get("projection_revision") != 1
            or projections["task_index"].get("projection_revision")
            != index.get("projection_revision")
            or (run_dir / "review" / "acceptance" / "acceptance_report.json").exists()
            or (
                run_dir / "review" / "acceptance" / "delivery_guard_report.json"
            ).exists()
        ):
            raise KernelConflict(
                "Bilibili initialized candidate has progressed beyond source-ready",
                data={
                    "first_failing_gate": "platform_kernel_candidate",
                    "error_code": "bilibili_initialized_reprepare_progressed",
                },
            )

        control_database = run_dir.parent / ".workflow-control" / "control.sqlite3"
        try:
            current = VideoWorkflowKernel(run_dir.parent).require_current_validated_source_package(
                run_dir
            )
        except Exception as exc:
            raise KernelConflict(
                "Bilibili source-ready candidate authority is not current",
                data={
                    "first_failing_gate": "platform_kernel_candidate",
                    "error_code": "bilibili_initialized_reprepare_source_not_current",
                },
            ) from exc
        if current != run or not control_database.is_file():
            raise KernelConflict(
                "Bilibili source-ready candidate authority changed during validation",
                data={
                    "first_failing_gate": "platform_kernel_candidate",
                    "error_code": "bilibili_initialized_reprepare_source_not_current",
                },
            )
        with sqlite3.connect(control_database) as run_connection:
            run_connection.row_factory = sqlite3.Row
            delivery_intents = run_connection.execute(
                "SELECT COUNT(*) FROM delivery_lifecycle_intents WHERE run_id=?",
                (run["run_id"],),
            ).fetchone()[0]
            mutation_intents = run_connection.execute(
                "SELECT COUNT(*) FROM run_state_mutation_intents WHERE run_id=?",
                (run["run_id"],),
            ).fetchone()[0]
        if delivery_intents or mutation_intents:
            raise KernelConflict(
                "Bilibili source-ready candidate has later lifecycle authority",
                data={
                    "first_failing_gate": "platform_kernel_candidate",
                    "error_code": "bilibili_initialized_reprepare_progressed",
                },
            )

    @staticmethod
    def _require_decision_ready_reprepare(
        *,
        run_dir: Path,
        run: dict[str, Any],
        video: dict[str, Any],
        session: dict[str, Any],
        index: dict[str, Any],
        entry: dict[str, Any],
        run_connection_path: Path,
        session_id: str,
    ) -> None:
        """Accept only the current pre-publication Source decision authority."""

        try:
            ContractRegistry(PROJECT_ROOT).validate_run_record(run)
        except (ContractError, OSError) as exc:
            raise KernelConflict(
                "Bilibili decision-ready candidate Run is contract-invalid"
            ) from exc
        artifact_ids = {
            "bootstrap_record",
            "source_candidate_inventory",
            "source_acquisition_decision_skeleton",
            "source_acquisition_decision",
        }
        checkpoint_ids = {
            "run_initialized",
            "source_candidates_ready",
            "source_acquisition_decision_ready",
        }
        delivery_artifacts = video.get("artifacts")
        projections = run["delivery"]["projections"]
        if (
            run.get("coordination_revision") != 3
            or run.get("source_epoch") != 1
            or run.get("source_blocker") is not None
            or run.get("source_version") is not None
            or run.get("phase") != "source_acquisition"
            or set(run.get("artifact_generations", {})) != artifact_ids
            or set(run.get("checkpoints", {})) != checkpoint_ids
            or any(
                checkpoint.get("status") != "current"
                for checkpoint in run["checkpoints"].values()
            )
            or (run_dir / "source" / "manifest.json").exists()
            or not isinstance(delivery_artifacts, dict)
            or any(value is not None for value in delivery_artifacts.values())
            or run["delivery"].get("ownership")
            != {"session_id": session_id, "generation": 1}
            or projections.get("archive") is not None
            or projections["video_target"].get("projection_revision") != 1
            or projections["session_target"].get("projection_revision") != 1
            or video.get("projection_revision") != 1
            or video.get("run_revision") != 1
            or session.get("projection_revision") != 1
            or session.get("run_revision") != 1
            or session.get("owner_status") != "active"
            or session.get("ownership_generation") != 1
            or entry.get("run_revision") != 1
            or entry.get("ownership_generation") != 1
            or entry.get("session_id") != session_id
            or entry.get("archive") is not None
            or entry.get("video_target", {}).get("projection_revision") != 1
            or entry.get("session_target", {}).get("projection_revision") != 1
            or projections["task_index"].get("projection_revision")
            != index.get("projection_revision")
            or (run_dir / "review" / "acceptance" / "acceptance_report.json").exists()
            or (
                run_dir / "review" / "acceptance" / "delivery_guard_report.json"
            ).exists()
        ):
            raise KernelConflict(
                "Bilibili initialized candidate has progressed beyond the allowed "
                "decision-ready authority",
                data={
                    "first_failing_gate": "platform_kernel_candidate",
                    "error_code": "bilibili_initialized_reprepare_progressed",
                },
            )

        generations = run["artifact_generations"]
        for logical_id, generation in generations.items():
            path = run_dir / Path(*str(generation.get("path", "")).split("/"))
            if (
                generation.get("generation") != 1
                or not path.is_file()
                or generation.get("sha256") != sha256_file(path)
            ):
                raise KernelConflict(
                    "Bilibili decision-ready candidate artifact authority drifted",
                    data={
                        "first_failing_gate": "platform_kernel_candidate",
                        "error_code": "bilibili_initialized_reprepare_artifact_drift",
                    },
                )
        expected_checkpoint_bindings = {
            "run_initialized": {"bootstrap_record"},
            "source_candidates_ready": {
                "source_candidate_inventory",
                "source_acquisition_decision_skeleton",
            },
            "source_acquisition_decision_ready": {
                "source_candidate_inventory",
                "source_acquisition_decision_skeleton",
                "source_acquisition_decision",
            },
        }
        for checkpoint_id, logical_ids in expected_checkpoint_bindings.items():
            bindings = run["checkpoints"][checkpoint_id].get("artifact_bindings", [])
            if (
                {binding.get("logical_id") for binding in bindings} != logical_ids
                or any(
                    binding.get("generation")
                    != generations[binding["logical_id"]]["generation"]
                    or binding.get("sha256")
                    != generations[binding["logical_id"]]["sha256"]
                    for binding in bindings
                )
            ):
                raise KernelConflict(
                    "Bilibili decision-ready candidate checkpoint authority drifted"
                )

        if not run_connection_path.is_file():
            raise KernelConflict(
                "Bilibili initialized candidate Control Store is unavailable"
            )
        with sqlite3.connect(run_connection_path) as run_connection:
            run_connection.row_factory = sqlite3.Row
            run_id = run["run_id"]
            initialization = run_connection.execute(
                "SELECT * FROM initialization_intents WHERE run_id=? AND state='COMMITTED'",
                (run_id,),
            ).fetchall()
            intent_counts = {
                table: run_connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE run_id=?", (run_id,)
                ).fetchone()[0]
                for table in (
                    "run_state_mutation_intents",
                    "source_publication_intents",
                    "delivery_lifecycle_intents",
                )
            }
            promotions = run_connection.execute(
                "SELECT * FROM task_promotion_intents WHERE run_id=? "
                "ORDER BY expected_run_revision",
                (run_id,),
            ).fetchall()
            claims = run_connection.execute(
                "SELECT c.*,a.state AS attempt_state FROM task_claims c "
                "JOIN task_attempts a ON a.attempt_id=c.attempt_id "
                "WHERE c.authority_id=? ORDER BY c.task_id",
                (run_id,),
            ).fetchall()
            task_envelopes = {}
            for path in sorted((run_dir / "workflow" / "tasks").glob("*/task.json")):
                envelope = read_json(path)
                task_envelopes[envelope.get("task_id")] = envelope

            claims_by_stage = {
                task_envelopes.get(claim["task_id"], {}).get("task_stage"): claim
                for claim in claims
            }
            provider = claims_by_stage.get("provider_acquisition")
            semantic = claims_by_stage.get("semantic_judgment")
            whisper = claims_by_stage.get("whisper_transcription")
            promotion_by_task = {row["task_id"]: row for row in promotions}
            allowed_promoted = (provider, semantic)
            if (
                len(initialization) != 1
                or initialization[0]["output_path"] != str(run_dir)
                or any(intent_counts.values())
                or len(task_envelopes) != 3
                or len(claims) != 3
                or len(promotions) != 2
                or any(row["state"] != "COMMITTED" for row in promotions)
                or [row["expected_run_revision"] for row in promotions] != [1, 2]
                or promotions[0]["old_run_record_sha256"]
                != (
                    initialization[0]["run_record_sha256"]
                    or initialization[0]["expected_run_record_sha256"]
                )
                or promotions[0]["replacement_run_record_sha256"]
                != promotions[1]["old_run_record_sha256"]
                or promotions[1]["replacement_run_record_sha256"]
                != sha256_file(run_dir / "workflow" / "run.json")
                or provider is None
                or semantic is None
                or whisper is None
                or any(
                    claim["state"] != "TERMINAL"
                    or claim["attempt_state"] != "COMMITTED_COMPLETE"
                    or claim["task_id"] not in promotion_by_task
                    for claim in allowed_promoted
                )
                or whisper["state"] != "ACTIVE"
                or whisper["attempt_state"] != "CLAIMED"
                or whisper["task_id"] in promotion_by_task
                or run.get("last_mutation_intent_id")
                != promotion_by_task[semantic["task_id"]]["intent_id"]
            ):
                raise KernelConflict(
                    "Bilibili decision-ready candidate task authority is unexpected",
                    data={
                        "first_failing_gate": "platform_kernel_candidate",
                        "error_code": "bilibili_initialized_reprepare_task_authority_invalid",
                    },
                )

            producers = {
                logical_id: generation.get("producer")
                for logical_id, generation in generations.items()
            }
            provider_prefix = f"task:{provider['task_id']}/{provider['attempt_id']}"
            semantic_prefix = f"task:{semantic['task_id']}/{semantic['attempt_id']}"
            whisper_attempts = run_connection.execute(
                "SELECT a.*,ca.completion_record_json FROM task_attempts a "
                "LEFT JOIN task_completion_authorities ca "
                "ON ca.attempt_id=a.attempt_id "
                "WHERE a.task_id=? ORDER BY a.claim_generation",
                (whisper["task_id"],),
            ).fetchall()
            whisper_leases = run_connection.execute(
                "SELECT * FROM resource_leases WHERE task_id=? "
                "ORDER BY claim_generation",
                (whisper["task_id"],),
            ).fetchall()
            leases_by_attempt = {row["attempt_id"]: row for row in whisper_leases}
            latest_generation = (
                int(whisper_attempts[-1]["claim_generation"])
                if whisper_attempts
                else 0
            )
            generations_are_contiguous = [
                int(row["claim_generation"]) for row in whisper_attempts
            ] == list(range(1, latest_generation + 1))
            current_attempts = [
                row for row in whisper_attempts if row["state"] == "CLAIMED"
            ]
            prior_attempts = whisper_attempts[:-1]
            proof_root = (
                run_dir.parent.parent
                / "待删除"
                / "source-acquire"
                / run_id
                / "terminal-proofs"
            )

            def prior_attempt_is_closed(attempt: sqlite3.Row) -> bool:
                lease = leases_by_attempt.get(attempt["attempt_id"])
                if (
                    attempt["state"] not in {"ABANDONED", "FAILED"}
                    or attempt["completion_sha256"] is not None
                    or attempt["completion_record_json"] is not None
                    or lease is None
                    or lease["state"] not in {"released", "resolved"}
                    or not isinstance(lease["terminal_evidence_json"], str)
                ):
                    return False
                try:
                    terminal = json.loads(lease["terminal_evidence_json"])
                    evidence = terminal["evidence"]
                    terminal_result_id = require_safe_path_segment(
                        str(evidence["terminal_result_id"]),
                        purpose="source terminal result id",
                        error_type=ContractError,
                    )
                    proof_path = proof_root / f"{terminal_result_id}.json"
                    proof = read_json(proof_path)
                    proof_reference = (
                        f"{proof_path.resolve().relative_to(PROJECT_ROOT).as_posix()}"
                        f"#sha256={sha256_file(proof_path)}"
                    )
                except (
                    KeyError,
                    OSError,
                    ValueError,
                    json.JSONDecodeError,
                    ContractError,
                ):
                    return False
                outcome = terminal.get("declared_outcome")
                proof_fields = {
                    "schema_name",
                    "schema_version",
                    "provider",
                    "terminal_result_id",
                    "task_id",
                    "attempt_id",
                    "claim_generation",
                    "launch_token",
                    "stage",
                    "declared_outcome",
                    "artifacts",
                    "observed_at",
                }
                proof_artifacts = proof.get("artifacts")
                if outcome == "failed":
                    proof_artifacts_are_valid = proof_artifacts == {}
                else:
                    transcript_path = (
                        run_dir
                        / Path(*str(attempt["attempt_path"]).split("/"))
                        / "o"
                        / "transcription.srt"
                    )
                    proof_artifacts_are_valid = (
                        isinstance(proof_artifacts, dict)
                        and set(proof_artifacts) == {"source_transcription"}
                        and transcript_path.is_file()
                        and proof_artifacts["source_transcription"]
                        == sha256_file(transcript_path)
                    )
                return (
                    terminal.get("schema_name")
                    == "resource-lease-resolution-evidence"
                    and terminal.get("schema_version") == "1.0.0"
                    and terminal.get("kernel_version") == "2.0.0"
                    and terminal.get("evidence_class")
                    == "provider_terminal_result"
                    and terminal.get("lease_id") == lease["lease_id"]
                    and terminal.get("attempt_id") == attempt["attempt_id"]
                    and terminal.get("claim_generation")
                    == attempt["claim_generation"]
                    and outcome in {"failed", "succeeded"}
                    and hashlib.sha256(
                        canonical_json_bytes(terminal)
                    ).hexdigest()
                    == lease["terminal_evidence_sha256"]
                    and evidence.get("provider") == "source-acquire"
                    and evidence.get("terminal_result_id") == terminal_result_id
                    and evidence.get("verification_proof_reference")
                    == proof_reference
                    and set(proof) == proof_fields
                    and proof.get("schema_name") == "source-provider-terminal-proof"
                    and proof.get("schema_version") == "1.0.0"
                    and proof.get("provider") == "source-acquire"
                    and proof.get("terminal_result_id") == terminal_result_id
                    and proof.get("task_id") == whisper["task_id"]
                    and proof.get("attempt_id") == attempt["attempt_id"]
                    and proof.get("claim_generation")
                    == attempt["claim_generation"]
                    and proof.get("launch_token") == lease["launch_token"]
                    and proof.get("stage") == "whisper"
                    and proof.get("declared_outcome") == outcome
                    and proof.get("observed_at") == terminal.get("observed_at")
                    and proof_artifacts_are_valid
                )

            current_attempt_is_open = (
                len(current_attempts) == 1
                and whisper_attempts
                and current_attempts[0]["attempt_id"]
                == whisper_attempts[-1]["attempt_id"]
                and current_attempts[0]["attempt_id"] == whisper["attempt_id"]
                and int(current_attempts[0]["claim_generation"])
                == latest_generation
                == int(whisper["claim_generation"])
                and current_attempts[0]["completion_sha256"] is None
                and current_attempts[0]["completion_record_json"] is None
            )
            if (
                producers["source_candidate_inventory"] != provider_prefix
                or producers["source_acquisition_decision_skeleton"]
                != provider_prefix
                or producers["source_acquisition_decision"] != semantic_prefix
                or latest_generation < 2
                or not generations_are_contiguous
                or len(whisper_leases) != len(whisper_attempts)
                or not current_attempt_is_open
                or not all(prior_attempt_is_closed(row) for row in prior_attempts)
            ):
                raise KernelConflict(
                    "Bilibili decision-ready candidate retry authority is invalid",
                    data={
                        "first_failing_gate": "platform_kernel_candidate",
                        "error_code": "bilibili_initialized_reprepare_retry_invalid",
                    },
                )

    def require_prepared_candidate(
        self,
        *,
        platform: str,
        control_store_root: Path,
        candidate_probe: Path,
        candidate_session_id: str,
    ) -> dict[str, Any]:
        spec = self._platform_spec(platform)
        display_name = spec["display_name"]
        root = control_store_root.resolve()
        with self._connect(root) as connection:
            row = connection.execute(
                "SELECT * FROM platform_cutover_candidates WHERE platform=?",
                (platform,),
            ).fetchone()
        if row is None or row["state"] not in {"PREPARED", "INITIALIZING", "INITIALIZED"}:
            raise KernelConflict(
                "Bilibili cutover candidate is absent or already confirmed",
                data={
                    "first_failing_gate": "platform_kernel_candidate",
                    "error_code": "bilibili_cutover_candidate_unavailable",
                },
            )
        probe_path = candidate_probe.resolve()
        if not probe_path.is_file():
            raise KernelConflict(
                "Bilibili cutover candidate probe is unavailable",
                data={
                    "first_failing_gate": "platform_kernel_candidate",
                    "error_code": "bilibili_cutover_candidate_binding_mismatch",
                },
            )
        probe = read_json(probe_path)
        candidate = self._candidate_snapshot(row)
        global_gate = candidate["global_gate_binding"]
        global_gate_path = Path(global_gate["authority_path"]).resolve()
        current_global_gate = GlobalGatePublisher().require_current(
            control_store_root=root
        )
        if (
            row["candidate_run_id"] != probe.get("run_id")
            or row["source_identity"] != probe.get("source_identity")
            or row["session_id"] != candidate_session_id
            or row["probe_sha256"] != sha256_file(probe_path)
            or row["implementation_commit"]
            != git_output(PROJECT_ROOT, "rev-parse", "HEAD")
            or not global_gate_path.is_relative_to(root)
            or not global_gate_path.is_file()
            or row["global_gate_sha256"] != sha256_file(global_gate_path)
            or global_gate["authority_path"] != current_global_gate["path"]
            or global_gate["authority_sha256"]
            != current_global_gate["file_sha256"]
            or global_gate["generation"] != current_global_gate["generation"]
        ):
            raise KernelConflict(
                "Bilibili cutover candidate binding differs from its prepared authority",
                data={
                    "first_failing_gate": "platform_kernel_candidate",
                    "error_code": "bilibili_cutover_candidate_binding_mismatch",
                },
            )
        return candidate

    def begin_candidate_initialization(
        self,
        *,
        platform: str,
        control_store_root: Path,
        candidate_probe: Path,
        candidate_session_id: str,
        workspace_root: Path,
    ) -> dict[str, Any]:
        candidate = self.require_prepared_candidate(
            platform=platform,
            control_store_root=control_store_root,
            candidate_probe=candidate_probe,
            candidate_session_id=candidate_session_id,
        )
        root = control_store_root.resolve()
        workspace = workspace_root.resolve()
        candidate["workspace_root"] = str(workspace)
        candidate["state"] = "INITIALIZING"
        encoded = json.dumps(candidate, sort_keys=True, separators=(",", ":"))
        with self._connect(root) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM platform_cutover_candidates WHERE platform=?",
                (platform,),
            ).fetchone()
            if row is None:
                connection.execute("ROLLBACK")
                raise KernelConflict("Bilibili cutover candidate disappeared")
            current = self._candidate_snapshot(row)
            if row["state"] != "PREPARED":
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    "Bilibili cutover candidate initialization is already owned",
                    data={
                        "first_failing_gate": "platform_kernel_candidate",
                        "error_code": "bilibili_candidate_initialization_in_progress",
                    },
                )
            if current["candidate_run_id"] != candidate["candidate_run_id"]:
                connection.execute("ROLLBACK")
                raise KernelConflict("Bilibili cutover candidate identity changed")
            connection.execute(
                "UPDATE platform_cutover_candidates "
                "SET candidate_json=?,state='INITIALIZING' "
                "WHERE platform=? AND state='PREPARED'",
                (encoded, platform),
            )
            connection.execute("COMMIT")
        return candidate

    def record_candidate_initialized(
        self, *, platform: str, control_store_root: Path, candidate_run_dir: Path
    ) -> None:
        self._platform_spec(platform)
        root = control_store_root.resolve()
        run_dir = candidate_run_dir.resolve()
        run_path = run_dir / "workflow" / "run.json"
        if not run_path.is_file():
            raise KernelConflict("Bilibili cutover candidate Run Record is unavailable")
        run = read_json(run_path)
        with self._connect(root) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM platform_cutover_candidates WHERE platform=?",
                (platform,),
            ).fetchone()
            if (
                row is None
                or row["state"] not in {"INITIALIZING", "INITIALIZED"}
                or run.get("schema_version") != "4.0.0"
                or run.get("canonical_platform") != platform
                or run.get("run_id") != row["candidate_run_id"]
                or Path(str(run.get("output_path", ""))).resolve() != run_dir
            ):
                connection.execute("ROLLBACK")
                raise KernelConflict("Bilibili cutover candidate lost its durable fence")
            candidate = self._candidate_snapshot(row)
            prior_run_dir = candidate.get("candidate_run_dir")
            if prior_run_dir is not None and Path(prior_run_dir).resolve() != run_dir:
                connection.execute("ROLLBACK")
                raise KernelConflict("Bilibili cutover candidate Run binding changed")
            candidate["candidate_run_dir"] = str(run_dir)
            candidate["state"] = "INITIALIZED"
            connection.execute(
                "UPDATE platform_cutover_candidates "
                "SET candidate_json=?,state='INITIALIZED' WHERE platform=? "
                "AND state IN ('INITIALIZING','INITIALIZED')",
                (
                    json.dumps(candidate, sort_keys=True, separators=(",", ":")),
                    platform,
                ),
            )
            connection.execute("COMMIT")

    def rollback_unstarted_candidate_initialization(
        self,
        *,
        platform: str,
        control_store_root: Path,
        candidate_run_id: str,
        workspace_root: Path,
    ) -> None:
        self._platform_spec(platform)
        root = control_store_root.resolve()
        workspace = workspace_root.resolve()
        with self._connect(root) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM platform_cutover_candidates WHERE platform=?",
                (platform,),
            ).fetchone()
            if row is None or row["state"] != "INITIALIZING":
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    "Bilibili candidate initialization rollback lost its fence"
                )
            candidate = self._candidate_snapshot(row)
            if (
                row["candidate_run_id"] != candidate_run_id
                or Path(str(candidate.get("workspace_root", ""))).resolve()
                != workspace
            ):
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    "Bilibili candidate initialization rollback binding changed"
                )
            candidate.pop("workspace_root", None)
            candidate.pop("candidate_run_dir", None)
            candidate["state"] = "PREPARED"
            changed = connection.execute(
                "UPDATE platform_cutover_candidates SET candidate_json=?,state='PREPARED' "
                "WHERE platform=? AND candidate_run_id=? AND state='INITIALIZING'",
                (
                    json.dumps(candidate, sort_keys=True, separators=(",", ":")),
                    platform,
                    candidate_run_id,
                ),
            ).rowcount
            if changed != 1:
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    "Bilibili candidate initialization rollback lost its CAS fence"
                )
            connection.execute("COMMIT")

    @staticmethod
    def _projection_path(
        binding: Any, *, base: Path, allowed_root: Path, label: str
    ) -> Path:
        if not isinstance(binding, dict):
            raise KernelConflict(f"Bilibili candidate {label} binding is absent")
        raw = binding.get("path")
        expected = binding.get("sha256")
        if not isinstance(raw, str) or not isinstance(expected, str):
            raise KernelConflict(f"Bilibili candidate {label} binding is invalid")
        candidate = Path(raw)
        path = (base / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
        if (
            not path.is_relative_to(allowed_root.resolve())
            or not path.is_file()
            or sha256_file(path) != expected
        ):
            if not path.is_relative_to(allowed_root.resolve()):
                raise KernelConflict(
                    f"Bilibili candidate {label} escapes its projection root",
                    data={
                        "first_failing_gate": "path_boundary",
                        "error_code": "bilibili_candidate_projection_escape",
                    },
                )
            raise KernelConflict(f"Bilibili candidate {label} binding is stale")
        return path

    def _current_candidate_run(
        self,
        *,
        root: Path,
        row: sqlite3.Row,
        expected_stage: str,
        platform: str,
    ) -> tuple[Path, dict[str, Any], dict[str, Any]]:
        candidate = self._candidate_snapshot(row)
        declared = candidate.get("candidate_run_dir")
        if not isinstance(declared, str):
            raise KernelConflict("Bilibili candidate Run binding is absent")
        run_dir = Path(declared).resolve()
        run_path = run_dir / "workflow" / "run.json"
        if not run_path.is_file():
            raise KernelConflict("Bilibili candidate Run Record is unavailable")
        run = read_json(run_path)
        delivery = run.get("delivery")
        if (
            run.get("schema_version") != "4.0.0"
            or run.get("canonical_platform") != platform
            or run.get("run_id") != row["candidate_run_id"]
            or Path(str(run.get("output_path", ""))).resolve() != run_dir
            or not isinstance(delivery, dict)
            or delivery.get("stage") != expected_stage
        ):
            raise KernelConflict(
                "Bilibili candidate Run is not at the required delivery stage",
                data={
                    "first_failing_gate": "platform_kernel_candidate",
                    "error_code": "bilibili_candidate_not_ready_for_activation",
                },
            )
        if run.get("source_identity") != row["source_identity"]:
            raise KernelConflict(
                "Bilibili candidate source identity differs from preparation",
                data={
                    "first_failing_gate": "platform_kernel_candidate",
                    "error_code": "bilibili_candidate_source_binding_mismatch",
                },
            )
        ownership = delivery.get("ownership")
        if (
            not isinstance(ownership, dict)
            or ownership.get("session_id") != row["session_id"]
        ):
            raise KernelConflict(
                "Bilibili candidate session differs from preparation",
                data={
                    "first_failing_gate": "platform_kernel_candidate",
                    "error_code": "bilibili_candidate_session_binding_mismatch",
                },
            )
        projections = delivery.get("projections")
        if not isinstance(projections, dict):
            raise KernelConflict("Bilibili candidate delivery projections are absent")
        video_path = self._projection_path(
            projections.get("video_target"),
            base=run_dir,
            allowed_root=run_dir,
            label="video target",
        )
        project_root = run_dir.parents[1]
        session_path = self._projection_path(
            projections.get("session_target"),
            base=run_dir,
            allowed_root=project_root,
            label="session target",
        )
        index_path = self._projection_path(
            projections.get("task_index"),
            base=run_dir,
            allowed_root=project_root,
            label="task index",
        )
        video = read_json(video_path)
        session = read_json(session_path)
        index = read_json(index_path)
        run_id = run["run_id"]
        matching = [
            item for item in index.get("entries", []) if item.get("run_id") == run_id
        ]
        if (
            video.get("run_id") != run_id
            or video.get("stage") != expected_stage
            or session.get("run_id") != run_id
            or session.get("stage") != expected_stage
            or len(matching) != 1
            or matching[0].get("stage") != expected_stage
        ):
            raise KernelConflict("Bilibili candidate delivery projections are not current")
        gate = video.get("global_gate_authority")
        current_gate = GlobalGatePublisher().require_current(control_store_root=root)
        if (
            not isinstance(gate, dict)
            or gate.get("path") != current_gate["path"]
            or gate.get("generation") != current_gate["generation"]
            or gate.get("sha256") != current_gate["file_sha256"]
        ):
            raise KernelConflict("Bilibili candidate Global Gate binding is stale")
        return run_dir, run, video

    def activate_candidate(
        self,
        *,
        platform: str,
        control_store_root: Path,
        candidate_run_dir: Path,
        activated_at: str,
    ) -> dict[str, Any]:
        self._platform_spec(platform)
        root = control_store_root.resolve()
        with self._connect(root) as connection:
            row = connection.execute(
                "SELECT * FROM platform_cutover_candidates WHERE platform=?",
                (platform,),
            ).fetchone()
        if row is None or row["state"] not in {"INITIALIZED", "PROVISIONAL"}:
            raise KernelConflict(
                "Bilibili cutover candidate is not initialized",
                data={
                    "first_failing_gate": "platform_kernel_candidate",
                    "error_code": "bilibili_cutover_candidate_unavailable",
                },
            )
        if row["implementation_commit"] != git_output(
            PROJECT_ROOT, "rev-parse", "HEAD"
        ):
            raise KernelConflict(
                "Bilibili candidate implementation is no longer current",
                data={
                    "first_failing_gate": "implementation_artifacts",
                    "error_code": "bilibili_candidate_implementation_stale",
                },
            )
        self._candidate_snapshot(row)
        run_dir, run, video = self._current_candidate_run(
            root=root, row=row, expected_stage="ready_for_delivery", platform=platform
        )
        if run_dir != candidate_run_dir.resolve():
            raise KernelConflict("Bilibili candidate activation targets another Run")
        artifacts = video.get("artifacts")
        acceptance_binding = (
            artifacts.get("acceptance_report") if isinstance(artifacts, dict) else None
        )
        guard_binding = (
            artifacts.get("delivery_guard_report") if isinstance(artifacts, dict) else None
        )
        acceptance_path = run_dir / "review" / "acceptance" / "acceptance_report.json"
        if (
            not isinstance(acceptance_binding, dict)
            or guard_binding is not None
            or Path(str(acceptance_binding.get("path", ""))).resolve()
            != acceptance_path.resolve()
            or not acceptance_path.is_file()
            or acceptance_binding.get("sha256") != sha256_file(acceptance_path)
        ):
            raise KernelConflict(
                "Bilibili candidate guarded decision is not bound to the ready target",
                data={
                    "first_failing_gate": "platform_kernel_candidate",
                    "error_code": "bilibili_candidate_guarded_decision_unbound",
                },
            )
        try:
            report = validate_acceptance_report(
                project_root=PROJECT_ROOT,
                report_path=acceptance_path,
                run_id=run["run_id"],
                coordination_revision=read_json(acceptance_path)["run_binding"][
                    "coordination_revision"
                ],
            )
            eligibility = AcceptanceV2Provider(PROJECT_ROOT).guard_eligibility(
                workspace_root=acceptance_path.parent
            )
            if (
                eligibility.get("eligible") is not True
                or eligibility.get("delivery_authority") is not True
                or eligibility.get("report_sha256") != report.get("report_sha256")
            ):
                raise ContractError(
                    "Acceptance Report v2 lacks committed provider authority"
                )
        except (ContractError, KernelConflict, OSError, KeyError, TypeError, ValueError) as exc:
            raise KernelConflict(
                "Bilibili candidate lacks a passing Acceptance decision",
                data={
                    "first_failing_gate": "platform_kernel_candidate",
                    "error_code": "bilibili_candidate_guarded_decision_invalid",
                },
            ) from exc
        candidate = json.loads(row["candidate_json"])
        candidate.update(
            {
                "state": "PROVISIONAL",
                "provisional_activated_at": activated_at,
                "acceptance_report_sha256": sha256_file(acceptance_path),
            }
        )
        encoded = json.dumps(candidate, sort_keys=True, separators=(",", ":"))
        with self._connect(root) as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT * FROM platform_cutover_candidates "
                "WHERE platform=?",
                (platform,),
            ).fetchone()
            if current is None or current["state"] not in {"INITIALIZED", "PROVISIONAL"}:
                connection.execute("ROLLBACK")
                raise KernelConflict("Bilibili candidate activation lost its fence")
            self._candidate_snapshot(current)
            if current["state"] == "PROVISIONAL" and current["candidate_json"] != encoded:
                connection.execute("ROLLBACK")
                raise KernelConflict("Bilibili candidate provisional authority conflicts")
            connection.execute(
                "UPDATE platform_cutover_candidates "
                "SET candidate_json=?,state='PROVISIONAL' WHERE platform=?",
                (encoded, platform),
            )
            connection.execute("COMMIT")
        return {
            "platform": platform,
            "cutover_state": "PROVISIONAL",
            "candidate_run_id": run["run_id"],
            "candidate_run_dir": str(run_dir),
            "platform_statuses": self._fallback_platform_statuses(
                platform=platform, control_store_root=root
            ),
        }

    def rebind_candidate_implementation(
        self,
        *,
        platform: str,
        control_store_root: Path,
        candidate_run_dir: Path,
        implementation_commit: str,
        rebound_at: str,
    ) -> dict[str, Any]:
        """Rebind the sole PROVISIONAL candidate to a direct-child commit.

        This is a narrow recovery authority for the single Issue #13 Run.  It
        proves the exact rev6->rev7->rev8 committed Delivery Lifecycle chain at
        stage ``accepted``, rewrites only the candidate implementation binding,
        and grants no ``delivered``, ``CONFIRMED``, or ordinary init-run
        authority.
        """

        self._platform_spec(platform)
        root = control_store_root.resolve()
        new_commit = implementation_commit.strip() if isinstance(
            implementation_commit, str
        ) else ""
        try:
            git_output(
                PROJECT_ROOT,
                "cat-file",
                "-e",
                f"{new_commit}^{{commit}}",
            )
            current_head = git_output(PROJECT_ROOT, "rev-parse", "HEAD")
        except EvidenceSupportError as exc:
            raise KernelConflict(
                "Bilibili candidate rebind implementation lineage is invalid",
                data={
                    "first_failing_gate": "implementation_artifacts",
                    "error_code": "bilibili_candidate_implementation_invalid",
                },
            ) from exc
        with self._connect(root) as connection:
            row = connection.execute(
                "SELECT * FROM platform_cutover_candidates WHERE platform=?",
                (platform,),
            ).fetchone()
            authority = connection.execute(
                "SELECT 1 FROM platform_cutover_authority WHERE platform=?",
                (platform,),
            ).fetchone()
            pending_intent = connection.execute(
                "SELECT 1 FROM platform_cutover_intents "
                "WHERE platform=? AND state='PREPARED'",
                (platform,),
            ).fetchone()
        if authority is not None:
            raise KernelConflict(
                "Bilibili Platform Kernel is already confirmed",
                data={
                    "first_failing_gate": "platform_kernel_authority",
                    "error_code": "bilibili_platform_authority_already_confirmed",
                },
            )
        if pending_intent is not None:
            raise KernelConflict(
                "Bilibili PROVISIONAL rebind requires no pending platform intent",
                data={
                    "first_failing_gate": "platform_kernel_authority",
                    "error_code": "bilibili_candidate_rebind_platform_intent_pending",
                },
            )
        if row is None:
            raise KernelConflict(
                "Bilibili cutover candidate is absent",
                data={
                    "first_failing_gate": "platform_kernel_candidate",
                    "error_code": "bilibili_cutover_candidate_unavailable",
                },
            )
        if row["state"] != "PROVISIONAL":
            raise KernelConflict(
                "Bilibili cutover candidate is not provisional",
                data={
                    "first_failing_gate": "platform_kernel_candidate",
                    "error_code": "bilibili_cutover_candidate_unavailable",
                },
            )
        if new_commit != current_head:
            raise KernelConflict(
                "Bilibili candidate rebind implementation is not current HEAD",
                data={
                    "first_failing_gate": "implementation_artifacts",
                    "error_code": "bilibili_candidate_implementation_invalid",
                },
            )
        candidate = self._candidate_snapshot(row)
        old_commit = row["implementation_commit"]
        if old_commit != new_commit:
            try:
                requested_lineage = git_output(
                    PROJECT_ROOT,
                    "rev-list",
                    "--parents",
                    "-n",
                    "1",
                    new_commit,
                ).split()
                if (
                    len(requested_lineage) != 2
                    or requested_lineage[1] != old_commit
                ):
                    raise EvidenceSupportError(
                        "candidate implementation is not the single direct parent"
                    )
            except EvidenceSupportError as exc:
                raise KernelConflict(
                    "Bilibili candidate rebind implementation is not a direct child",
                    data={
                        "first_failing_gate": "implementation_artifacts",
                        "error_code": "bilibili_candidate_implementation_invalid",
                    },
                ) from exc
        run_dir, run, _video = self._current_candidate_run(
            root=root, row=row, expected_stage="accepted", platform=platform
        )
        if run_dir != candidate_run_dir.resolve():
            raise KernelConflict(
                "Bilibili candidate rebind targets another Run",
                data={
                    "first_failing_gate": "platform_kernel_candidate",
                    "error_code": "bilibili_candidate_rebind_run_mismatch",
                },
            )
        if (
            run.get("delivery", {}).get("ownership", {}).get("generation") != 1
        ):
            raise KernelConflict(
                "Bilibili candidate rebind is not the exact owned accepted Run",
                data={
                    "first_failing_gate": "platform_kernel_candidate",
                    "error_code": "bilibili_candidate_rebind_run_mismatch",
                },
            )
        self._require_exact_accepted_chain(run_dir=run_dir, run=run)
        control_database = run_dir.parent / ".workflow-control" / "control.sqlite3"
        try:
            with sqlite3.connect(control_database) as run_connection:
                run_connection.row_factory = sqlite3.Row
                pending_lifecycle = run_connection.execute(
                    "SELECT 1 FROM delivery_lifecycle_intents WHERE run_id=? "
                    "AND state IN ('PREPARED','ABORTED') LIMIT 1",
                    (run["run_id"],),
                ).fetchone()
                held_slots = run_connection.execute(
                    "SELECT 1 FROM projection_publication_slots WHERE intent_id IN "
                    "(SELECT intent_id FROM delivery_lifecycle_intents WHERE run_id=?) "
                    "AND state IN ('HELD','PREPARED') LIMIT 1",
                    (run["run_id"],),
                ).fetchone()
        except (sqlite3.DatabaseError, OSError) as exc:
            raise KernelConflict(
                "Bilibili candidate rebind lifecycle authority is unavailable",
                data={
                    "first_failing_gate": "platform_kernel_candidate",
                    "error_code": "bilibili_candidate_rebind_lifecycle_intent_pending",
                },
            ) from exc
        if pending_lifecycle is not None or held_slots is not None:
            raise KernelConflict(
                "Bilibili candidate rebind has pending lifecycle authority",
                data={
                    "first_failing_gate": "platform_kernel_candidate",
                    "error_code": "bilibili_candidate_rebind_lifecycle_intent_pending",
                },
            )
        run_path = run_dir / "workflow" / "run.json"
        current_sha = ControlStore(
            run_dir.parent, ContractRegistry(PROJECT_ROOT)
        ).current_run_record_sha(run["run_id"])
        if current_sha != sha256_file(run_path):
            raise KernelConflict(
                "Bilibili candidate rebind Run record is not current",
                data={
                    "first_failing_gate": "platform_kernel_candidate",
                    "error_code": "bilibili_candidate_rebind_run_record_stale",
                },
            )
        try:
            eligibility = AcceptanceV2Provider(PROJECT_ROOT).guard_eligibility(
                workspace_root=run_dir / "review" / "acceptance"
            )
        except (
            AcceptanceV2Rejected,
            ContractError,
            KernelConflict,
            OSError,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            raise KernelConflict(
                "Bilibili candidate rebind lacks current Acceptance authority",
                data={
                    "first_failing_gate": "platform_kernel_candidate",
                    "error_code": (
                        "bilibili_candidate_rebind_acceptance_authority_invalid"
                    ),
                },
            ) from exc
        if (
            eligibility.get("eligible") is not True
            or eligibility.get("delivery_authority") is not True
        ):
            raise KernelConflict(
                "Bilibili candidate rebind lacks a passing Acceptance decision",
                data={
                    "first_failing_gate": "platform_kernel_candidate",
                    "error_code": (
                        "bilibili_candidate_rebind_acceptance_authority_invalid"
                    ),
                },
            )
        rebound = dict(candidate)
        rebound["implementation_commit"] = new_commit
        if old_commit != new_commit:
            rebound["rebound_from_commit"] = old_commit
        rebound["rebound_at"] = rebound_at
        encoded = json.dumps(rebound, sort_keys=True, separators=(",", ":"))
        if old_commit == new_commit:
            # Already rebound to this commit: an exact retry is idempotent and
            # any other rebound metadata conflicts with committed authority.
            if "rebound_from_commit" not in candidate:
                raise KernelConflict(
                    "Bilibili candidate rebind conflicts with committed authority",
                    data={
                        "first_failing_gate": "platform_kernel_candidate",
                        "error_code": "bilibili_candidate_rebind_conflict",
                    },
                )
            with self._connect(root) as connection:
                connection.execute("BEGIN IMMEDIATE")
                current = connection.execute(
                    "SELECT * FROM platform_cutover_candidates WHERE platform=?",
                    (platform,),
                ).fetchone()
                if (
                    current is None
                    or current["state"] != "PROVISIONAL"
                    or current["implementation_commit"] != new_commit
                    or current["candidate_json"] != encoded
                ):
                    connection.execute("ROLLBACK")
                    raise KernelConflict(
                        "Bilibili candidate rebind lost its committed fence",
                        data={
                            "first_failing_gate": "platform_kernel_candidate",
                            "error_code": "bilibili_candidate_rebind_conflict",
                        },
                    )
                connection.execute("COMMIT")
            return {
                "platform": platform,
                "cutover_state": "PROVISIONAL",
                "candidate_run_id": run["run_id"],
                "candidate_run_dir": str(run_dir),
                "implementation_commit": new_commit,
                "rebound_from_commit": old_commit,
                "rebound_at": rebound_at,
                "platform_statuses": self._fallback_platform_statuses(
                    platform=platform, control_store_root=root
                ),
                "idempotent": True,
                "run_record_path": str(run_path),
            }
        with self._connect(root) as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT * FROM platform_cutover_candidates WHERE platform=?",
                (platform,),
            ).fetchone()
            if (
                current is None
                or current["state"] != "PROVISIONAL"
                or current["implementation_commit"] != old_commit
                or current["candidate_json"] != row["candidate_json"]
            ):
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    "Bilibili candidate rebind lost its committed fence",
                    data={
                        "first_failing_gate": "platform_kernel_candidate",
                        "error_code": "bilibili_candidate_rebind_conflict",
                    },
                )
            if current["implementation_commit"] == new_commit:
                if current["candidate_json"] == encoded:
                    connection.execute("COMMIT")
                    return {
                        "platform": platform,
                        "cutover_state": "PROVISIONAL",
                        "candidate_run_id": run["run_id"],
                        "candidate_run_dir": str(run_dir),
                        "implementation_commit": new_commit,
                        "rebound_from_commit": old_commit,
                        "rebound_at": rebound_at,
                        "platform_statuses": self._fallback_platform_statuses(
                            platform=platform, control_store_root=root
                        ),
                        "idempotent": True,
                        "run_record_path": str(run_path),
                    }
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    "Bilibili candidate rebind conflicts with committed authority",
                    data={
                        "first_failing_gate": "platform_kernel_candidate",
                        "error_code": "bilibili_candidate_rebind_conflict",
                    },
                )
            changed = connection.execute(
                "UPDATE platform_cutover_candidates SET implementation_commit=?,"
                "candidate_json=? "
                "WHERE platform=? AND state='PROVISIONAL' AND candidate_run_id=? "
                "AND source_identity=? AND session_id=? AND global_gate_sha256=? "
                "AND probe_sha256=? AND implementation_commit=? AND candidate_json=?",
                (
                    new_commit,
                    encoded,
                    platform,
                    row["candidate_run_id"],
                    row["source_identity"],
                    row["session_id"],
                    row["global_gate_sha256"],
                    row["probe_sha256"],
                    old_commit,
                    row["candidate_json"],
                ),
            ).rowcount
            if changed != 1:
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    "Bilibili candidate rebind lost its CAS fence",
                    data={
                        "first_failing_gate": "platform_kernel_candidate",
                        "error_code": "bilibili_candidate_rebind_conflict",
                    },
                )
            connection.execute("COMMIT")
        return {
            "platform": platform,
            "cutover_state": "PROVISIONAL",
            "candidate_run_id": run["run_id"],
            "candidate_run_dir": str(run_dir),
            "implementation_commit": new_commit,
            "rebound_from_commit": old_commit,
            "rebound_at": rebound_at,
            "platform_statuses": self._fallback_platform_statuses(
                platform=platform, control_store_root=root
            ),
            "idempotent": False,
            "run_record_path": str(run_path),
        }

    def _require_exact_accepted_chain(
        self, *, run_dir: Path, run: dict[str, Any]
    ) -> None:
        """Prove the exact rev6->rev7->rev8 committed Delivery Lifecycle chain."""

        rev = run["coordination_revision"]
        run_id = run["run_id"]
        control_database = run_dir.parent / ".workflow-control" / "control.sqlite3"
        try:
            with sqlite3.connect(control_database) as connection:
                connection.row_factory = sqlite3.Row
                committed = connection.execute(
                    "SELECT * FROM delivery_lifecycle_intents "
                    "WHERE run_id=? AND state='COMMITTED' "
                    "ORDER BY expected_run_revision",
                    (run_id,),
                ).fetchall()
            bind_intents = [
                item
                for item in committed
                if item["expected_run_revision"] == rev - 2
                and item["target_stage"] == "ready_for_delivery"
            ]
            accepted_intents = [
                item
                for item in committed
                if item["expected_run_revision"] == rev - 1
                and item["target_stage"] == "accepted"
            ]
            if len(bind_intents) != 1 or len(accepted_intents) != 1:
                raise EvidenceSupportError(
                    "accepted chain is not exactly two committed intents"
                )
            bind_intent = bind_intents[0]
            accepted_intent = accepted_intents[0]
            rev7 = json.loads(bind_intent["replacement_run_record_json"])
            rev8 = json.loads(accepted_intent["replacement_run_record_json"])
            rev7_sha = hashlib.sha256(canonical_json_bytes(rev7)).hexdigest()
            rev8_sha = hashlib.sha256(canonical_json_bytes(rev8)).hexdigest()
            run_sha = hashlib.sha256(canonical_json_bytes(run)).hexdigest()
            run_path = run_dir / "workflow" / "run.json"
            bind_journal = read_json(
                run_dir
                / "待删除"
                / "delivery-lifecycle"
                / bind_intent["intent_id"]
                / "intent.json"
            )
            accepted_journal = read_json(
                run_dir
                / "待删除"
                / "delivery-lifecycle"
                / accepted_intent["intent_id"]
                / "intent.json"
            )
            if (
                rev7.get("run_id") != run_id
                or rev7.get("coordination_revision") != rev - 1
                or rev7.get("delivery", {}).get("stage") != "ready_for_delivery"
                or rev7_sha != bind_intent["replacement_run_record_sha256"]
                or accepted_intent["prior_run_record_sha256"]
                != bind_intent["replacement_run_record_sha256"]
                or rev8.get("run_id") != run_id
                or rev8.get("coordination_revision") != rev
                or rev8.get("delivery", {}).get("stage") != "accepted"
                or rev8 != run
                or rev8_sha != accepted_intent["replacement_run_record_sha256"]
                or run_sha != sha256_file(run_path)
                or run.get("last_mutation_intent_id")
                != accepted_intent["intent_id"]
                or bind_journal.get("prior_run_sha256")
                != bind_intent["prior_run_record_sha256"]
                or accepted_journal.get("prior_run_sha256")
                != accepted_intent["prior_run_record_sha256"]
            ):
                raise EvidenceSupportError(
                    "accepted chain record authority drifted"
                )
        except (
            EvidenceSupportError,
            OSError,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            sqlite3.DatabaseError,
        ) as exc:
            raise KernelConflict(
                "Bilibili PROVISIONAL/accepted chain is not the exact "
                "rev6-rev7-rev8 lineage",
                data={
                    "first_failing_gate": "platform_kernel_candidate",
                    "error_code": "bilibili_candidate_rebind_chain_invalid",
                },
            ) from exc

    def authorize_delivery_transition(
        self,
        *,
        platform: str,
        control_store_root: Path,
        run_dir: Path,
        run_id: str,
        to_stage: str,
    ) -> None:
        if to_stage not in {"accepted", "delivered"}:
            return
        self._platform_spec(platform)
        root = control_store_root.resolve()
        if not (root / PLATFORM_KERNEL_DB).is_file():
            raise KernelConflict(
                "Bilibili delivery transition lacks Platform Kernel authority",
                data={
                    "first_failing_gate": "platform_kernel_authority",
                    "error_code": "bilibili_platform_authority_stale",
                },
            )
        with self._connect(root) as connection:
            active = connection.execute(
                "SELECT 1 FROM platform_cutover_authority WHERE platform=?",
                (platform,),
            ).fetchone()
            candidate = connection.execute(
                "SELECT * FROM platform_cutover_candidates WHERE platform=?",
                (platform,),
            ).fetchone()
        if active is not None:
            self.require_current(platform=platform, control_store_root=root)
            return
        if candidate is not None and candidate["state"] == "PROVISIONAL":
            value = self._candidate_snapshot(candidate)
            if (
                candidate["candidate_run_id"] == run_id
                and Path(str(value.get("candidate_run_dir", ""))).resolve()
                == run_dir.resolve()
            ):
                if to_stage == "delivered":
                    try:
                        guarded = require_current_kernel_guarded_decision(
                            project_root=run_dir.resolve().parents[1],
                            run_dir=run_dir.resolve(),
                        )
                    except (ContractError, OSError, KeyError, TypeError, ValueError) as exc:
                        raise KernelConflict(
                            "Bilibili candidate guarded delivery authority is stale",
                            data={
                                "first_failing_gate": "platform_kernel_candidate",
                                "error_code": "bilibili_candidate_guarded_decision_stale",
                            },
                        ) from exc
                    if (
                        guarded.get("run_id") != run_id
                        or guarded.get("acceptance_report", {}).get("sha256")
                        != value.get("acceptance_report_sha256")
                    ):
                        raise KernelConflict(
                            "Bilibili candidate guarded delivery authority is stale",
                            data={
                                "first_failing_gate": "platform_kernel_candidate",
                                "error_code": "bilibili_candidate_guarded_decision_stale",
                            },
                        )
                return
        raise KernelConflict(
            "Bilibili delivery transition lacks confirmed or provisional authority",
            data={
                "first_failing_gate": "platform_kernel_candidate",
                "error_code": "bilibili_candidate_delivery_not_authorized",
            },
        )

    def reject_candidate_handoff(
        self,
        *,
        platform: str,
        control_store_root: Path,
        run_id: str,
    ) -> None:
        self._platform_spec(platform)
        root = control_store_root.resolve()
        if not (root / PLATFORM_KERNEL_DB).is_file():
            return
        with self._connect(root) as connection:
            candidate = connection.execute(
                "SELECT * FROM platform_cutover_candidates WHERE platform=?",
                (platform,),
            ).fetchone()
        if candidate is None:
            return
        self._candidate_snapshot(candidate)
        if (
            candidate["candidate_run_id"] == run_id
            and candidate["state"] != "CONFIRMED"
        ):
            raise KernelConflict(
                "Bilibili cutover candidate cannot be handed off before confirmation",
                data={
                    "first_failing_gate": "platform_kernel_candidate",
                    "error_code": "bilibili_candidate_handoff_forbidden",
                },
            )

    def _require_confirmable_candidate(
        self,
        *,
        root: Path,
        evidence: dict[str, Any],
        candidate_row: sqlite3.Row | None = None,
        platform: str,
    ) -> sqlite3.Row:
        spec = self._platform_spec(platform)
        display_name = spec["display_name"]
        row = candidate_row
        if row is None:
            with self._connect(root) as connection:
                row = connection.execute(
                    "SELECT * FROM platform_cutover_candidates WHERE platform=?",
                    (platform,),
                ).fetchone()
        guarded = evidence.get("guarded_delivery_evidence")
        guarded_run_id = guarded.get("run_id") if isinstance(guarded, dict) else None
        if row is None or row["state"] not in {"PROVISIONAL", "CONFIRMED"}:
            raise KernelConflict(
                f"{display_name} activation requires one provisional candidate",
                data={
                    "first_failing_gate": "platform_kernel_candidate",
                    "error_code": "bilibili_provisional_candidate_absent",
                },
            )
        if evidence.get("implementation_commit") != row["implementation_commit"]:
            raise KernelConflict(
                f"{display_name} activation evidence differs from the candidate "
                "implementation",
                data={
                    "first_failing_gate": "implementation_artifacts",
                    "error_code": "bilibili_candidate_implementation_evidence_mismatch",
                },
            )
        run_dir, run, video = self._current_candidate_run(
            root=root, row=row, expected_stage="delivered", platform=platform
        )
        if guarded_run_id != row["candidate_run_id"] or run["run_id"] != guarded_run_id:
            raise KernelConflict(
                f"{display_name} guarded delivery differs from the delivered candidate",
                data={
                    "first_failing_gate": "guarded_delivery_candidate_binding",
                    "error_code": (
                        "bilibili_guarded_run_differs_from_delivered_candidate"
                    ),
                },
            )
        manifest_artifacts = {
            item.get("role"): item
            for item in guarded.get("artifacts", [])
            if isinstance(item, dict)
        }
        source = run.get("artifact_generations", {}).get("source_manifest")
        projections = run["delivery"]["projections"]
        expected_paths = {
            "run_record": run_dir / "workflow" / "run.json",
            "source_manifest": run_dir / "source" / "manifest.json",
            "acceptance_report_v2": Path(
                str(video.get("artifacts", {}).get("acceptance_report", {}).get("path", ""))
            ),
            "delivery_guard_report": Path(
                str(video.get("artifacts", {}).get("delivery_guard_report", {}).get("path", ""))
            ),
            "video_delivery_target": self._projection_path(
                projections["video_target"],
                base=run_dir,
                allowed_root=run_dir,
                label="video target",
            ),
            "session_delivery_target": self._projection_path(
                projections["session_target"],
                base=run_dir,
                allowed_root=run_dir.parents[1],
                label="session target",
            ),
            "delivery_task_index": self._projection_path(
                projections["task_index"],
                base=run_dir,
                allowed_root=run_dir.parents[1],
                label="task index",
            ),
            "global_gate_authority": Path(
                str(video.get("global_gate_authority", {}).get("path", ""))
            ),
            "final_pdf": Path(
                str(video.get("artifacts", {}).get("final_pdf", {}).get("path", ""))
            ),
        }
        if (
            not isinstance(source, dict)
            or not expected_paths["source_manifest"].is_file()
            or source.get("sha256")
            != sha256_file(expected_paths["source_manifest"])
        ):
            raise KernelConflict(
                f"{display_name} candidate source manifest authority is absent or stale",
                data={
                    "first_failing_gate": "guarded_delivery_candidate_binding",
                    "error_code": "bilibili_candidate_source_manifest_stale",
                },
            )
        for role, expected_path in expected_paths.items():
            binding = manifest_artifacts.get(role)
            resolved = expected_path.resolve()
            if (
                not isinstance(binding, dict)
                or not resolved.is_file()
                or (
                    Path(str(binding.get("path", ""))).resolve()
                    if Path(str(binding.get("path", ""))).is_absolute()
                    else (PROJECT_ROOT / Path(str(binding.get("path", "")))).resolve()
                )
                != resolved
                or binding.get("sha256") != sha256_file(resolved)
            ):
                raise KernelConflict(
                    f"{display_name} guarded delivery role differs from candidate: {role}",
                    data={
                        "first_failing_gate": "guarded_delivery_candidate_binding",
                        "error_code": "bilibili_guarded_candidate_role_mismatch",
                        "role": role,
                    },
                )
        return row

    def activate(
        self,
        *,
        platform: str,
        control_store_root: Path,
        exit_evidence: Path,
        activated_at: str,
        fault_point: str | None = None,
    ) -> dict[str, Any]:
        spec = self._platform_spec(platform)
        display_name = spec["display_name"]
        evidence_path = exit_evidence.resolve()
        if not evidence_path.is_file():
            raise ContractError(f"{display_name} cutover Exit Evidence is unavailable")
        evidence = _validate_evidence(read_json(evidence_path), platform, evidence_path)
        _require_formal_exit_evidence(evidence_path, platform)
        evidence_sha256 = sha256_file(evidence_path)
        root = control_store_root.resolve()
        global_gate = GlobalGatePublisher().require_current(
            control_store_root=root
        )
        confirmable_candidate = self._require_confirmable_candidate(
            root=root, evidence=evidence, platform=platform
        )
        candidate_snapshot_sha256 = self._confirmation_snapshot_fingerprint(
            confirmable_candidate, evidence
        )
        binding = {
            "activation_status": "active_global_gate",
            "authority_path": global_gate["path"],
            "authority_sha256": global_gate["file_sha256"],
            "generation": global_gate["generation"],
        }
        authority_path = root / PLATFORM_AUTHORITY_DIR / f"{platform}.json"
        authority_path.parent.mkdir(parents=True, exist_ok=True)
        authority = {
            "schema_name": "platform-kernel-authority",
            "schema_version": "1.0.0",
            "platform": platform,
            "generation": 1,
            "authority_status": "active_kernel",
            "new_run_authority": "video_workflow_kernel_v2",
            "existing_run_authority": "legacy_preserved",
            "acceptance_authority": "active_global_gate",
            "global_gate_binding": binding,
            "exit_evidence_path": str(evidence_path),
            "exit_evidence_sha256": evidence_sha256,
            "activated_at": activated_at,
        }
        authority["authority_sha256"] = _fingerprint(
            authority, "authority_sha256"
        )
        intent_id = hashlib.sha256(
            (platform + "\0" + evidence_sha256).encode("utf-8")
        ).hexdigest()
        with self._connect(root) as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT * FROM platform_cutover_authority WHERE platform=?",
                (platform,),
            ).fetchone()
            if current is not None:
                if current["evidence_sha256"] != evidence_sha256:
                    connection.execute("ROLLBACK")
                    raise KernelConflict(
                        f"A different {display_name} Platform Kernel authority already exists"
                    )
                if (
                    not authority_path.is_file()
                    or sha256_file(authority_path) != current["authority_sha256"]
                ):
                    connection.execute("ROLLBACK")
                    raise KernelConflict(
                        f"Committed {display_name} Platform Kernel authority is stale"
                    )
                connection.execute("COMMIT")
                return {
                    "platform": platform,
                    "generation": int(current["generation"]),
                    "authority_path": str(authority_path),
                    "authority_sha256": current["authority_sha256"],
                    "platform_statuses": spec["platform_statuses"],
                    "cutover_state": "CONFIRMED",
                    "idempotent": True,
                }
            pending = connection.execute(
                "SELECT * FROM platform_cutover_intents WHERE state='PREPARED'"
            ).fetchall()
            if pending:
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    f"An interrupted {display_name} Platform Kernel publication "
                    "requires reconciliation"
                )
            connection.execute(
                "INSERT INTO platform_cutover_intents("
                "intent_id,platform,evidence_sha256,authority_json,"
                "candidate_snapshot_sha256,state) "
                "VALUES(?,?,?,?,?, 'PREPARED')",
                (
                    intent_id,
                    platform,
                    evidence_sha256,
                    __import__("json").dumps(
                        authority, sort_keys=True, separators=(",", ":")
                    ),
                    candidate_snapshot_sha256,
                ),
            )
            connection.execute("COMMIT")
        if fault_point == "after_intent":
            raise PlatformKernelFault(fault_point)
        write_json_atomic(authority_path, authority)
        if fault_point == "after_authority_write":
            raise PlatformKernelFault(fault_point)
        file_sha256 = sha256_file(authority_path)
        with self._connect(root) as connection:
            connection.execute("BEGIN IMMEDIATE")
            candidate = connection.execute(
                "SELECT * FROM platform_cutover_candidates "
                "WHERE platform=?",
                (platform,),
            ).fetchone()
            intent = connection.execute(
                "SELECT * FROM platform_cutover_intents WHERE intent_id=? "
                "AND state='PREPARED'",
                (intent_id,),
            ).fetchone()
            if candidate is None or (
                candidate["state"] != "PROVISIONAL"
                or candidate["candidate_run_id"]
                != evidence["guarded_delivery_evidence"]["run_id"]
                or intent is None
                or intent["candidate_snapshot_sha256"]
                != self._confirmation_snapshot_fingerprint(candidate, evidence)
            ):
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    "Bilibili activation evidence differs from its prepared candidate"
                )
            connection.execute(
                "INSERT INTO platform_cutover_authority("
                "platform,generation,evidence_sha256,authority_sha256) "
                "VALUES(?,?,?,?)",
                (platform, 1, evidence_sha256, file_sha256),
            )
            connection.execute(
                "UPDATE platform_cutover_intents SET state='COMMITTED' "
                "WHERE intent_id=?",
                (intent_id,),
            )
            confirmed_candidate = json.loads(candidate["candidate_json"])
            confirmed_candidate["state"] = "CONFIRMED"
            connection.execute(
                "UPDATE platform_cutover_candidates "
                "SET state='CONFIRMED',candidate_json=? "
                "WHERE platform=? AND state='PROVISIONAL'",
                (
                    json.dumps(
                        confirmed_candidate, sort_keys=True, separators=(",", ":")
                    ),
                    platform,
                ),
            )
            connection.execute("COMMIT")
        if fault_point == "after_control_commit":
            raise PlatformKernelFault(fault_point)
        return {
            "platform": platform,
            "generation": 1,
            "authority_path": str(authority_path),
            "authority_sha256": file_sha256,
            "platform_statuses": spec["platform_statuses"],
            "cutover_state": "CONFIRMED",
            "idempotent": False,
        }

    def reconcile(
        self, *, platform: str, control_store_root: Path
    ) -> dict[str, Any]:
        spec = self._platform_spec(platform)
        display_name = spec["display_name"]
        root = control_store_root.resolve()
        authority_path = root / PLATFORM_AUTHORITY_DIR / f"{platform}.json"
        with self._connect(root) as connection:
            refresh_intents = connection.execute(
                "SELECT * FROM platform_authority_refresh_intents "
                "WHERE platform=? AND state='PREPARED'",
                (platform,),
            ).fetchall()
            committed_refresh = connection.execute(
                "SELECT * FROM platform_authority_refresh_intents "
                "WHERE platform=? AND state='COMMITTED' "
                "ORDER BY expected_generation DESC LIMIT 1",
                (platform,),
            ).fetchone()
            current_authority = connection.execute(
                "SELECT * FROM platform_cutover_authority WHERE platform=?",
                (platform,),
            ).fetchone()
        if len(refresh_intents) > 1:
            raise KernelConflict(
                f"{display_name} authority refresh reconciliation is ambiguous"
            )
        if refresh_intents:
            return self._reconcile_authority_refresh(
                platform=platform,
                control_store_root=root,
                intent_id=refresh_intents[0]["intent_id"],
            )
        if (
            committed_refresh is not None
            and current_authority is not None
            and int(current_authority["generation"])
            == int(committed_refresh["expected_generation"]) + 1
            and current_authority["evidence_sha256"]
            == committed_refresh["evidence_sha256"]
            and authority_path.is_file()
            and sha256_file(authority_path) == current_authority["authority_sha256"]
            and read_json(authority_path)
            == json.loads(committed_refresh["authority_json"])
        ):
            current = self.require_current(
                platform=platform, control_store_root=root
            )
            return {
                "platform": platform,
                "generation": current["generation"],
                "authority_path": current["authority_path"],
                "authority_sha256": current["authority_sha256"],
                "authority_status": "current",
                "cutover_state": "CONFIRMED",
                "current": True,
                "reconciled": True,
            }
        with self._connect(root) as connection:
            connection.execute("BEGIN IMMEDIATE")
            pending = connection.execute(
                "SELECT * FROM platform_cutover_intents WHERE state='PREPARED'"
            ).fetchall()
            if len(pending) != 1:
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    f"{display_name} Platform Kernel reconciliation requires one "
                    "prepared intent"
                )
            intent = pending[0]
            authority = __import__("json").loads(intent["authority_json"])
            evidence_path = Path(str(authority.get("exit_evidence_path", ""))).resolve()
            if (
                not evidence_path.is_relative_to(PROJECT_ROOT)
                or not evidence_path.is_file()
                or sha256_file(evidence_path) != intent["evidence_sha256"]
            ):
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    f"Interrupted {display_name} Platform Kernel Exit Evidence drifted"
                )
            evidence = _validate_evidence(read_json(evidence_path), platform, evidence_path)
            _require_formal_exit_evidence(evidence_path, platform)
            global_gate_binding = authority.get("global_gate_binding")
            if not isinstance(global_gate_binding, dict):
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    "Interrupted Bilibili Platform Kernel Global Gate binding is absent"
                )
            global_gate_path = Path(
                str(global_gate_binding.get("authority_path", ""))
            ).resolve()
            if (
                not global_gate_path.is_relative_to(root)
                or not global_gate_path.is_file()
                or sha256_file(global_gate_path)
                != global_gate_binding.get("authority_sha256")
            ):
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    "Interrupted Bilibili Platform Kernel Global Gate drifted"
                )
            if authority_path.is_file() and read_json(authority_path) != authority:
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    "Interrupted Bilibili Platform Kernel authority bytes conflict"
                )
            if not authority_path.is_file():
                authority_path.parent.mkdir(parents=True, exist_ok=True)
                write_json_atomic(authority_path, authority)
            file_sha256 = sha256_file(authority_path)
            candidate = connection.execute(
                "SELECT * FROM platform_cutover_candidates WHERE platform=?",
                (platform,),
            ).fetchone()
            if candidate is None:
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    "Bilibili provisional candidate is absent during reconciliation",
                    data={
                        "first_failing_gate": "platform_kernel_candidate",
                        "error_code": "bilibili_provisional_candidate_absent",
                    },
                )
            if (
                candidate["state"] != "PROVISIONAL"
                or candidate["candidate_run_id"]
                != evidence["guarded_delivery_evidence"]["run_id"]
            ):
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    "Interrupted activation candidate snapshot drifted",
                    data={
                        "first_failing_gate": "platform_kernel_candidate",
                        "error_code": "bilibili_candidate_confirmation_snapshot_drift",
                    },
                )
            current_snapshot_sha256 = self._confirmation_snapshot_fingerprint(
                candidate, evidence
            )
            prepared_snapshot_sha256 = intent["candidate_snapshot_sha256"]
            if prepared_snapshot_sha256 is None:
                self._require_confirmable_candidate(
                    root=root, evidence=evidence, candidate_row=candidate, platform=platform
                )
                changed = connection.execute(
                    "UPDATE platform_cutover_intents "
                    "SET candidate_snapshot_sha256=? "
                    "WHERE intent_id=? AND state='PREPARED' "
                    "AND candidate_snapshot_sha256 IS NULL",
                    (current_snapshot_sha256, intent["intent_id"]),
                ).rowcount
                if changed != 1:
                    connection.execute("ROLLBACK")
                    raise KernelConflict(
                        "Interrupted activation snapshot backfill lost its CAS fence"
                    )
                prepared_snapshot_sha256 = current_snapshot_sha256
            if prepared_snapshot_sha256 != current_snapshot_sha256:
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    "Interrupted activation candidate snapshot drifted",
                    data={
                        "first_failing_gate": "platform_kernel_candidate",
                        "error_code": "bilibili_candidate_confirmation_snapshot_drift",
                    },
                )
            current = connection.execute(
                "SELECT * FROM platform_cutover_authority WHERE platform=?",
                (platform,),
            ).fetchone()
            if current is None:
                connection.execute(
                    "INSERT INTO platform_cutover_authority("
                    "platform,generation,evidence_sha256,authority_sha256) "
                    "VALUES(?,?,?,?)",
                    (platform, 1, intent["evidence_sha256"], file_sha256),
                )
            elif (
                current["evidence_sha256"] != intent["evidence_sha256"]
                or current["authority_sha256"] != file_sha256
            ):
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    "Interrupted Bilibili Platform Kernel publication lost its fence"
                )
            connection.execute(
                "UPDATE platform_cutover_intents SET state='COMMITTED' "
                "WHERE intent_id=?",
                (intent["intent_id"],),
            )
            confirmed_candidate = self._candidate_snapshot(candidate)
            self._current_candidate_run(
                root=root, row=candidate, expected_stage="delivered", platform=platform
            )
            confirmed_candidate["state"] = "CONFIRMED"
            connection.execute(
                "UPDATE platform_cutover_candidates "
                "SET state='CONFIRMED',candidate_json=? WHERE platform=?",
                (
                    json.dumps(
                        confirmed_candidate,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    platform,
                ),
            )
            connection.execute("COMMIT")
        return {
            "platform": platform,
            "generation": 1,
            "authority_path": str(authority_path),
            "authority_sha256": file_sha256,
            "authority_status": "current",
            "cutover_state": "CONFIRMED",
            "current": True,
            "reconciled": True,
        }

    def _reconcile_authority_refresh(
        self,
        *,
        platform: str,
        control_store_root: Path,
        intent_id: str,
    ) -> dict[str, Any]:
        spec = self._platform_spec(platform)
        display_name = spec["display_name"]
        root = control_store_root.resolve()
        authority_path = root / PLATFORM_AUTHORITY_DIR / f"{platform}.json"
        with self._connect(root) as connection:
            connection.execute("BEGIN IMMEDIATE")
            intent = connection.execute(
                "SELECT * FROM platform_authority_refresh_intents "
                "WHERE intent_id=? AND platform=? AND state='PREPARED'",
                (intent_id, platform),
            ).fetchone()
            current = connection.execute(
                "SELECT * FROM platform_cutover_authority WHERE platform=?",
                (platform,),
            ).fetchone()
            candidate = connection.execute(
                "SELECT state,candidate_run_id FROM platform_cutover_candidates "
                "WHERE platform=?",
                (platform,),
            ).fetchone()
            if (
                intent is None
                or current is None
                or candidate is None
                or candidate["state"] != "CONFIRMED"
                or int(current["generation"]) != int(intent["expected_generation"])
            ):
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    f"{display_name} authority refresh lost its reconciliation fence"
                )
            authority = json.loads(intent["authority_json"])
            evidence_path = Path(str(authority.get("exit_evidence_path", ""))).resolve()
            if (
                not evidence_path.is_relative_to(PROJECT_ROOT)
                or not evidence_path.is_file()
                or sha256_file(evidence_path) != intent["evidence_sha256"]
            ):
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    f"Interrupted {display_name} authority refresh evidence drifted"
                )
            evidence = _validate_evidence(
                read_json(evidence_path), platform, evidence_path
            )
            if (
                candidate["candidate_run_id"]
                != evidence["guarded_delivery_evidence"]["run_id"]
            ):
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    f"Interrupted {display_name} authority refresh candidate drifted"
                )
            _require_formal_exit_evidence(evidence_path, platform)
            global_gate = GlobalGatePublisher().require_current(control_store_root=root)
            binding = authority.get("global_gate_binding")
            if binding != {
                "activation_status": "active_global_gate",
                "authority_path": global_gate["path"],
                "authority_sha256": global_gate["file_sha256"],
                "generation": global_gate["generation"],
            }:
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    f"Interrupted {display_name} authority refresh Global Gate drifted"
                )
            if authority_path.is_file():
                existing = read_json(authority_path)
                if (
                    existing != authority
                    and sha256_file(authority_path) != current["authority_sha256"]
                ):
                    connection.execute("ROLLBACK")
                    raise KernelConflict(
                        f"Interrupted {display_name} authority refresh bytes conflict"
                    )
            if not authority_path.is_file() or read_json(authority_path) != authority:
                authority_path.parent.mkdir(parents=True, exist_ok=True)
                write_json_atomic(authority_path, authority)
            file_sha256 = sha256_file(authority_path)
            generation = int(intent["expected_generation"]) + 1
            changed = connection.execute(
                "UPDATE platform_cutover_authority SET generation=?,"
                "evidence_sha256=?,authority_sha256=? "
                "WHERE platform=? AND generation=? AND authority_sha256=?",
                (
                    generation,
                    intent["evidence_sha256"],
                    file_sha256,
                    platform,
                    intent["expected_generation"],
                    current["authority_sha256"],
                ),
            ).rowcount
            if changed != 1:
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    f"{display_name} authority refresh lost its generation fence"
                )
            connection.execute(
                "UPDATE platform_authority_refresh_intents SET state='COMMITTED' "
                "WHERE intent_id=? AND state='PREPARED'",
                (intent_id,),
            )
            connection.execute("COMMIT")
        return {
            "platform": platform,
            "generation": generation,
            "authority_path": str(authority_path),
            "authority_sha256": file_sha256,
            "authority_status": "current",
            "cutover_state": "CONFIRMED",
            "current": True,
            "reconciled": True,
        }

    def refresh_authority(
        self,
        *,
        platform: str,
        control_store_root: Path,
        exit_evidence: Path,
        expected_generation: int,
        refreshed_at: str,
        fault_point: str | None = None,
    ) -> dict[str, Any]:
        """Advance a confirmed platform authority to newer published evidence."""

        spec = self._platform_spec(platform)
        display_name = spec["display_name"]
        root = control_store_root.resolve()
        evidence_path = exit_evidence.resolve()
        if not evidence_path.is_file():
            raise ContractError(
                f"{display_name} authority refresh Exit Evidence is unavailable"
            )
        evidence = _validate_evidence(read_json(evidence_path), platform, evidence_path)
        _require_formal_exit_evidence(evidence_path, platform)
        evidence_sha256 = sha256_file(evidence_path)
        global_gate = GlobalGatePublisher().require_current(control_store_root=root)
        authority_path = root / PLATFORM_AUTHORITY_DIR / f"{platform}.json"

        with self._connect(root) as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT * FROM platform_cutover_authority WHERE platform=?",
                (platform,),
            ).fetchone()
            candidate = connection.execute(
                "SELECT state,candidate_run_id FROM platform_cutover_candidates "
                "WHERE platform=?",
                (platform,),
            ).fetchone()
            pending = connection.execute(
                "SELECT COUNT(*) FROM platform_authority_refresh_intents "
                "WHERE platform=? AND state='PREPARED'",
                (platform,),
            ).fetchone()[0]
            committed = connection.execute(
                "SELECT authority_json FROM platform_authority_refresh_intents "
                "WHERE platform=? AND expected_generation=? "
                "AND evidence_sha256=? AND state='COMMITTED'",
                (platform, expected_generation, evidence_sha256),
            ).fetchone()
            if (
                current is None
                or candidate is None
                or candidate["state"] != "CONFIRMED"
                or candidate["candidate_run_id"]
                != evidence["guarded_delivery_evidence"]["run_id"]
            ):
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    f"{display_name} authority refresh requires a current confirmed authority"
                )
            if pending:
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    f"Interrupted {display_name} authority refresh requires reconciliation"
                )
            if (
                not authority_path.is_file()
                or sha256_file(authority_path) != current["authority_sha256"]
            ):
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    f"{display_name} authority refresh requires current authority bytes"
                )
            current_authority = read_json(authority_path)
            if (
                current_authority.get("platform") != platform
                or current_authority.get("generation") != current["generation"]
                or current_authority.get("authority_sha256")
                != _fingerprint(current_authority, "authority_sha256")
            ):
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    f"{display_name} authority refresh found conflicting authority bytes"
                )
            if (
                committed is not None
                and int(current["generation"]) == expected_generation + 1
                and current["evidence_sha256"] == evidence_sha256
                and read_json(authority_path) == json.loads(committed["authority_json"])
            ):
                connection.execute("COMMIT")
                return {
                    "platform": platform,
                    "generation": int(current["generation"]),
                    "authority_path": str(authority_path),
                    "authority_sha256": current["authority_sha256"],
                    "platform_statuses": spec["platform_statuses"],
                    "cutover_state": "CONFIRMED",
                    "idempotent": True,
                }
            if int(current["generation"]) != expected_generation:
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    f"{display_name} authority refresh expected generation is stale",
                    data={
                        "first_failing_gate": "platform_kernel_authority",
                        "error_code": (
                            f"{spec['error_prefix']}_platform_authority_refresh_fenced"
                        ),
                    },
                )
            if current["evidence_sha256"] == evidence_sha256:
                connection.execute("COMMIT")
                return {
                    "platform": platform,
                    "generation": int(current["generation"]),
                    "authority_path": str(authority_path),
                    "authority_sha256": current["authority_sha256"],
                    "platform_statuses": spec["platform_statuses"],
                    "cutover_state": "CONFIRMED",
                    "idempotent": True,
                }
            generation = expected_generation + 1
            authority = dict(current_authority)
            authority.update(
                {
                    "generation": generation,
                    "global_gate_binding": {
                        "activation_status": "active_global_gate",
                        "authority_path": global_gate["path"],
                        "authority_sha256": global_gate["file_sha256"],
                        "generation": global_gate["generation"],
                    },
                    "exit_evidence_path": str(evidence_path),
                    "exit_evidence_sha256": evidence_sha256,
                    "refreshed_at": refreshed_at,
                }
            )
            authority["authority_sha256"] = _fingerprint(
                authority, "authority_sha256"
            )
            intent_id = hashlib.sha256(
                (platform + "\0" + str(generation) + "\0" + evidence_sha256).encode(
                    "utf-8"
                )
            ).hexdigest()
            connection.execute(
                "INSERT INTO platform_authority_refresh_intents("
                "intent_id,platform,expected_generation,evidence_sha256,"
                "authority_json,state) VALUES(?,?,?,?,?,'PREPARED')",
                (
                    intent_id,
                    platform,
                    int(current["generation"]),
                    evidence_sha256,
                    json.dumps(authority, sort_keys=True, separators=(",", ":")),
                ),
            )
            connection.execute("COMMIT")

        if fault_point == "after_intent":
            raise PlatformKernelFault(fault_point)
        _require_formal_exit_evidence(evidence_path, platform)
        write_json_atomic(authority_path, authority)
        if fault_point == "after_authority_write":
            raise PlatformKernelFault(fault_point)
        file_sha256 = sha256_file(authority_path)
        with self._connect(root) as connection:
            connection.execute("BEGIN IMMEDIATE")
            intent = connection.execute(
                "SELECT * FROM platform_authority_refresh_intents "
                "WHERE intent_id=? AND platform=? AND state='PREPARED'",
                (intent_id, platform),
            ).fetchone()
            candidate = connection.execute(
                "SELECT state,candidate_run_id FROM platform_cutover_candidates "
                "WHERE platform=?",
                (platform,),
            ).fetchone()
            current_global_gate = GlobalGatePublisher().require_current(
                control_store_root=root
            )
            if (
                intent is None
                or candidate is None
                or candidate["state"] != "CONFIRMED"
                or candidate["candidate_run_id"]
                != evidence["guarded_delivery_evidence"]["run_id"]
                or sha256_file(evidence_path) != evidence_sha256
                or authority["global_gate_binding"]
                != {
                    "activation_status": "active_global_gate",
                    "authority_path": current_global_gate["path"],
                    "authority_sha256": current_global_gate["file_sha256"],
                    "generation": current_global_gate["generation"],
                }
            ):
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    f"{display_name} authority refresh inputs drifted before commit"
                )
            _require_formal_exit_evidence(evidence_path, platform)
            changed = connection.execute(
                "UPDATE platform_cutover_authority SET generation=?,"
                "evidence_sha256=?,authority_sha256=? "
                "WHERE platform=? AND generation=? AND authority_sha256=?",
                (
                    generation,
                    evidence_sha256,
                    file_sha256,
                    platform,
                    generation - 1,
                    current["authority_sha256"],
                ),
            ).rowcount
            if changed != 1:
                connection.execute("ROLLBACK")
                raise KernelConflict(
                    f"{display_name} authority refresh lost its generation fence"
                )
            connection.execute(
                "UPDATE platform_authority_refresh_intents SET state='COMMITTED' "
                "WHERE intent_id=? AND state='PREPARED'",
                (intent_id,),
            )
            connection.execute("COMMIT")
        if fault_point == "after_control_commit":
            raise PlatformKernelFault(fault_point)
        return {
            "platform": platform,
            "generation": generation,
            "authority_path": str(authority_path),
            "authority_sha256": file_sha256,
            "platform_statuses": evidence["platform_statuses"],
            "cutover_state": "CONFIRMED",
            "idempotent": False,
        }

    def require_current(
        self, *, platform: str, control_store_root: Path
    ) -> dict[str, Any]:
        spec = self._platform_spec(platform)
        display_name = spec["display_name"]
        prefix = spec["error_prefix"]
        root = control_store_root.resolve()
        authority_path = root / PLATFORM_AUTHORITY_DIR / f"{platform}.json"
        with self._connect(root) as connection:
            current = connection.execute(
                "SELECT * FROM platform_cutover_authority WHERE platform=?",
                (platform,),
            ).fetchone()
            candidate = connection.execute(
                "SELECT state FROM platform_cutover_candidates WHERE platform=?",
                (platform,),
            ).fetchone()
            pending = connection.execute(
                "SELECT COUNT(*) FROM platform_cutover_intents WHERE state='PREPARED'"
            ).fetchone()[0]
            pending_refresh = connection.execute(
                "SELECT COUNT(*) FROM platform_authority_refresh_intents "
                "WHERE platform=? AND state='PREPARED'",
                (platform,),
            ).fetchone()[0]
        if current is None and candidate is not None and candidate["state"] != "CONFIRMED":
            raise KernelConflict(
                f"{display_name} Platform Kernel candidate awaits confirmation",
                data={
                    "first_failing_gate": "platform_kernel_authority",
                    "error_code": f"{prefix}_platform_authority_pending_confirmation",
                },
            )
        if (
            current is None
            or pending
            or pending_refresh
            or not authority_path.is_file()
            or sha256_file(authority_path) != current["authority_sha256"]
        ):
            raise KernelConflict(
                f"{display_name} Platform Kernel authority is absent, stale, or incomplete",
                data={
                    "first_failing_gate": "platform_kernel_authority",
                    "error_code": f"{prefix}_platform_authority_stale",
                },
            )
        authority = read_json(authority_path)
        evidence_path = Path(authority.get("exit_evidence_path", ""))
        if (
            authority.get("platform") != platform
            or authority.get("authority_status") != "active_kernel"
            or authority.get("generation") != current["generation"]
            or authority.get("exit_evidence_sha256") != current["evidence_sha256"]
            or authority.get("authority_sha256")
            != _fingerprint(authority, "authority_sha256")
        ):
            raise KernelConflict(
                f"{display_name} Platform Kernel authority content conflicts with control state",
                data={
                    "first_failing_gate": "platform_kernel_authority",
                    "error_code": f"{prefix}_platform_authority_conflict",
                },
            )
        try:
            revalidation_enabled = exit_evidence_revalidation_enabled(root)
        except EvidenceSupportError as exc:
            raise KernelConflict(
                str(exc),
                data={
                    "first_failing_gate": "workflow_policy_config",
                    "error_code": "workflow_policy_config_invalid",
                },
            ) from exc
        if revalidation_enabled:
            if (
                not evidence_path.is_file()
                or sha256_file(evidence_path) != current["evidence_sha256"]
            ):
                raise KernelConflict(
                    f"{display_name} Platform Kernel Exit Evidence is stale",
                    data={
                        "first_failing_gate": "platform_kernel_authority",
                        "error_code": f"{prefix}_platform_exit_evidence_stale",
                    },
                )
            evidence = _validate_evidence(
                read_json(evidence_path), platform, evidence_path
            )
            _require_formal_exit_evidence(evidence_path, platform)
            platform_statuses = evidence["platform_statuses"]
        else:
            platform_statuses = {platform: authority["authority_status"]}
        return {
            "platform": platform,
            "generation": int(current["generation"]),
            "authority_path": str(authority_path),
            "authority_sha256": current["authority_sha256"],
            "exit_evidence_sha256": current["evidence_sha256"],
            "platform_statuses": platform_statuses,
            "evidence_freshness_check": (
                "enabled" if revalidation_enabled else "disabled"
            ),
            "exit_evidence_revalidated": revalidation_enabled,
            "current": True,
        }

    def check_policy(
        self, *, platform: str, control_store_root: Path
    ) -> dict[str, Any]:
        current = self.require_current(
            platform=platform, control_store_root=control_store_root
        )
        return {
            "current": True,
            "platform_statuses": current["platform_statuses"],
            "platform_kernel_authority": current,
        }


# The publisher machinery serves every supported platform; the class keeps its
# original name for CLI and test compatibility.
PlatformCutoverPublisher = BilibiliPlatformCutoverPublisher

__all__ = [
    "ACTIVATION_FAULT_POINTS",
    "BilibiliPlatformCutoverPublisher",
    "PlatformCutoverPublisher",
]
