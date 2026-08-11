from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
from typing import Any, Iterator
import uuid

from .acceptance_v2 import (
    AcceptanceV2Provider,
    FINAL_AUTHORITY_DB_NAME,
    final_authority_generations,
    fingerprint_contract_without,
)
from .contracts import ContractRegistry
from .control_store import ControlStore
from .errors import ArtifactDrift, ContractError, FinalEvidenceFault
from .kernel import VideoWorkflowKernel
from .utils import (
    canonical_json_bytes,
    read_json,
    require_contained_path,
    sha256_file,
    write_json_atomic,
)


QUALITY_ARGUMENTS = {
    "precompile_quality_report": "precompile-quality-report",
    "precompile_text_seal": "precompile-text-seal",
    "rendered_text_reconciliation": "rendered-text-reconciliation-report",
    "final_artifact_seal": "final-artifact-seal",
    "final_compile_manifest": "final-compile-manifest",
    "render_evidence_manifest": "render-evidence-manifest",
    "rendered_text_object_inventory": "rendered-text-object-inventory",
    "text_origin_manifest": "text-origin-manifest",
}
FINAL_EVIDENCE_FAULT_POINTS = frozenset({"after_binding_write"})


class FinalDeliveryEvidenceProvider:
    """Publish Run-bound Acceptance inputs from already governed evidence."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.registry = ContractRegistry(self.project_root)
        self.acceptance = AcceptanceV2Provider(self.project_root)

    @staticmethod
    @contextmanager
    def _run_lock(run_dir: Path) -> Iterator[None]:
        lock_path = run_dir / "待删除" / "delivery-final-evidence-prepare.lock"
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
    def _fingerprinted(path: Path, root: Path, purpose: str) -> dict[str, str]:
        current = require_contained_path(
            path.resolve(),
            root,
            purpose=purpose,
            error_type=ContractError,
            leaf_kind="file",
            require_single_link=True,
        )
        return {"path": str(current), "sha256": sha256_file(current)}

    @staticmethod
    def _resolve_manifest_path(value: str, manifest_path: Path) -> Path:
        path = Path(value)
        return path.resolve() if path.is_absolute() else (manifest_path.parent / path).resolve()

    @staticmethod
    def _validate_time(value: str) -> None:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise ContractError("Final Evidence prepared_at is invalid") from exc
        if parsed.tzinfo is None:
            raise ContractError("Final Evidence prepared_at must include a timezone")

    @staticmethod
    def _connect_intents(control_store_root: Path) -> sqlite3.Connection:
        connection = sqlite3.connect(
            control_store_root / FINAL_AUTHORITY_DB_NAME,
            timeout=30,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS final_evidence_prepare_intents ("
            "run_id TEXT NOT NULL, acceptance_revision INTEGER NOT NULL, "
            "state TEXT NOT NULL, binding_path TEXT NOT NULL, binding_sha256 TEXT NOT NULL, "
            "authority_path TEXT NOT NULL, authority_sha256 TEXT NOT NULL, prepared_at TEXT NOT NULL, "
            "PRIMARY KEY(run_id, acceptance_revision))"
        )
        return connection

    @staticmethod
    def _next_revision(control: sqlite3.Connection, run_id: str) -> int:
        control.execute(
            "CREATE TABLE IF NOT EXISTS final_quality_authority (run_id TEXT PRIMARY KEY, "
            "acceptance_revision INTEGER NOT NULL, run_record_sha256 TEXT NOT NULL, "
            "authority_path TEXT NOT NULL, authority_sha256 TEXT NOT NULL)"
        )
        published = control.execute(
            "SELECT acceptance_revision FROM final_quality_authority WHERE run_id=?",
            (run_id,),
        ).fetchone()
        prepared = control.execute(
            "SELECT MAX(acceptance_revision) AS revision FROM final_evidence_prepare_intents "
            "WHERE run_id=? AND state='COMMITTED'",
            (run_id,),
        ).fetchone()
        revisions = [0]
        if published is not None:
            revisions.append(int(published["acceptance_revision"]))
        if prepared is not None and prepared["revision"] is not None:
            revisions.append(int(prepared["revision"]))
        return max(revisions) + 1

    @staticmethod
    def _move_failed_publication(run_dir: Path, path: Path) -> None:
        if not path.exists():
            return
        destination = (
            run_dir
            / "待删除"
            / "failed-final-evidence-publications"
            / f"{uuid.uuid4().hex}-{path.name}"
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(destination))

    def prepare(
        self,
        *,
        run_dir: Path,
        final_pdf: Path,
        main_tex: Path,
        final_compile_report: Path,
        final_compile_manifest: Path,
        precompile_quality_report: Path,
        precompile_text_seal: Path,
        final_artifact_seal: Path,
        rendered_text_reconciliation: Path,
        render_evidence_manifest: Path,
        rendered_text_inventory: Path,
        text_origin_manifest: Path,
        global_gate_authority: Path,
        allowed_manifest: Path,
        prepared_at: str,
        fault_point: str | None = None,
    ) -> dict[str, Any]:
        root = run_dir.resolve()
        self._validate_time(prepared_at)
        if fault_point is not None and fault_point not in FINAL_EVIDENCE_FAULT_POINTS:
            raise ContractError("unknown Final Evidence fault point")
        require_contained_path(
            root,
            root,
            purpose="Final Evidence Run directory",
            error_type=ContractError,
            leaf_kind="directory",
        )
        with self._run_lock(root):
            return self._prepare_locked(
                root=root,
                final_pdf=final_pdf,
                main_tex=main_tex,
                final_compile_report=final_compile_report,
                final_compile_manifest=final_compile_manifest,
                precompile_quality_report=precompile_quality_report,
                precompile_text_seal=precompile_text_seal,
                final_artifact_seal=final_artifact_seal,
                rendered_text_reconciliation=rendered_text_reconciliation,
                render_evidence_manifest=render_evidence_manifest,
                rendered_text_inventory=rendered_text_inventory,
                text_origin_manifest=text_origin_manifest,
                global_gate_authority=global_gate_authority,
                allowed_manifest=allowed_manifest,
                prepared_at=prepared_at,
                fault_point=fault_point,
            )

    def _prepare_locked(
        self,
        *,
        root: Path,
        prepared_at: str,
        fault_point: str | None,
        **paths: Path,
    ) -> dict[str, Any]:
        run_path = root / "workflow" / "run.json"
        run_binding = self._fingerprinted(run_path, root, "Kernel Run Record")
        run = read_json(run_path)
        self.registry.validate_run_record(run)
        if (
            run.get("schema_version") != "4.0.0"
            or Path(run.get("output_path", "")).resolve() != root
            or run.get("source_state") != "ready"
            or run.get("checkpoints", {}).get("source_ready", {}).get("status") != "current"
        ):
            raise ArtifactDrift("Final Evidence requires a current Run v4 source authority")

        plan = VideoWorkflowKernel(root.parent).production_plan(root)
        if plan.get("classification") != "production_complete":
            raise ArtifactDrift("Final Evidence requires production_complete")
        state_path = root / "workflow" / "production-state.json"
        state = read_json(state_path)
        self.registry.validate("production-state", state)
        if state.get("checkpoints", {}).get("draft_compile_ready") != "current":
            raise ArtifactDrift("Final Evidence production compile evidence is not current")

        supplied_main = self._fingerprinted(paths["main_tex"], root, "Final main TeX")
        current_main = state.get("artifacts", {}).get("integrated_main")
        if (
            not isinstance(current_main, dict)
            or Path(current_main.get("path", "")) != Path(paths["main_tex"].resolve().relative_to(root))
            or current_main.get("sha256") != supplied_main["sha256"]
        ):
            raise ArtifactDrift("Final main TeX differs from current production authority")

        quality_path_arguments = {
            "precompile_quality_report": paths["precompile_quality_report"],
            "precompile_text_seal": paths["precompile_text_seal"],
            "rendered_text_reconciliation": paths["rendered_text_reconciliation"],
            "final_artifact_seal": paths["final_artifact_seal"],
            "final_compile_manifest": paths["final_compile_manifest"],
            "render_evidence_manifest": paths["render_evidence_manifest"],
            "rendered_text_object_inventory": paths["rendered_text_inventory"],
            "text_origin_manifest": paths["text_origin_manifest"],
        }
        quality_inputs: dict[str, dict[str, str]] = {}
        quality_values: dict[str, dict[str, Any]] = {}
        for logical_id, schema_name in QUALITY_ARGUMENTS.items():
            item = self._fingerprinted(
                quality_path_arguments[logical_id], root, f"Final quality input {logical_id}"
            )
            value = read_json(Path(item["path"]))
            self.acceptance.registry.validate(schema_name, value)
            quality_inputs[logical_id] = item
            quality_values[logical_id] = value

        final_pdf_binding = self._fingerprinted(paths["final_pdf"], root, "Final PDF")
        compile_report_binding = self._fingerprinted(
            paths["final_compile_report"], root, "Final Compile Report"
        )
        compile_report = read_json(Path(compile_report_binding["path"]))
        self.acceptance.registry.validate("final-compile-report", compile_report)
        final_seal = quality_values["final_artifact_seal"]
        compile_manifest = quality_values["final_compile_manifest"]
        render_manifest = quality_values["render_evidence_manifest"]
        rendered_inventory_value = quality_values["rendered_text_object_inventory"]
        origin_manifest_value = quality_values["text_origin_manifest"]
        reconciliation = quality_values["rendered_text_reconciliation"]
        if (
            compile_report.get("mode") != "final"
            or compile_report.get("status") != "pass"
            or compile_report.get("dependency_closure", {}).get("complete") is not True
            or compile_report.get("pdf", {}).get("sha256") != final_pdf_binding["sha256"]
            or compile_report.get("final_artifact_seal_sha256") != final_seal.get("seal_sha256")
            or compile_report.get("compile_manifest_sha256") != compile_manifest.get("manifest_sha256")
            or compile_report.get("render_evidence_manifest_sha256") != render_manifest.get("manifest_sha256")
            or compile_report.get("rendered_text_inventory_sha256") != rendered_inventory_value.get("inventory_sha256")
            or compile_report.get("text_origin_manifest_sha256") != origin_manifest_value.get("manifest_sha256")
            or reconciliation.get("final_pdf_sha256") != final_pdf_binding["sha256"]
        ):
            raise ArtifactDrift("Final Compile evidence lineage is not current")
        sealed_pdf = self._resolve_manifest_path(
            final_seal.get("final_pdf", {}).get("path", ""), Path(quality_inputs["final_artifact_seal"]["path"])
        )
        if sealed_pdf != Path(final_pdf_binding["path"]):
            raise ArtifactDrift("Final Artifact Seal binds another PDF")

        render_manifest_path = Path(quality_inputs["render_evidence_manifest"]["path"])
        rendered_pages: list[dict[str, Any]] = []
        for expected_page, page in enumerate(render_manifest.get("pages", []), start=1):
            if page.get("page") != expected_page:
                raise ContractError("Render Evidence pages must exactly cover 1..page_count")
            page_path = self._resolve_manifest_path(page.get("path", ""), render_manifest_path)
            page_binding = self._fingerprinted(page_path, root, f"Rendered page {expected_page}")
            if page_binding["sha256"] != page.get("sha256"):
                raise ArtifactDrift("Rendered page evidence drifted")
            rendered_pages.append({"page": expected_page, **page_binding})
        if render_manifest.get("page_count") != len(rendered_pages) or not rendered_pages:
            raise ContractError("Render Evidence page coverage is incomplete")

        allowed_binding = self._fingerprinted(paths["allowed_manifest"], root, "Allowed Artifacts Manifest")
        expected_allowed_path = root / "review" / "acceptance" / "allowed_artifacts_manifest.json"
        if Path(allowed_binding["path"]) != expected_allowed_path:
            raise ContractError("Allowed Artifacts Manifest path is not canonical")
        allowed = read_json(expected_allowed_path)
        allowed_items = allowed.get("final_artifacts", [])
        if not isinstance(allowed_items, list) or any(
            not isinstance(item, dict) for item in allowed_items
        ):
            raise ContractError("Allowed Artifacts Manifest entries are invalid")
        roles = [item.get("role") for item in allowed_items]
        if roles.count("delivery_glossary") > 1:
            raise ContractError(
                "Allowed Artifacts Manifest duplicates the governed Delivery Glossary",
                data={
                    "first_failing_gate": "delivery_glossary_lineage",
                    "error_code": "final_evidence_delivery_glossary_lineage_invalid",
                },
            )
        if (
            len(roles) != len(set(roles))
            or set(roles) not in ({"pdf", "tex"}, {"pdf", "tex", "delivery_glossary"})
        ):
            raise ContractError("Allowed Artifacts Manifest roles are invalid or duplicated")
        allowed_by_role = {
            item["role"]: (root / item.get("path", "")).resolve()
            for item in allowed_items
        }
        if {key: allowed_by_role[key] for key in ("pdf", "tex")} != {
            "pdf": Path(final_pdf_binding["path"]),
            "tex": Path(supplied_main["path"]),
        }:
            raise ContractError("Allowed Artifacts Manifest does not exactly bind final artifacts")
        reader_inventory_path = (
            Path(quality_inputs["precompile_quality_report"]["path"]).parent
            / "reader-facing-text-inventory.json"
        )
        reader_inventory_binding = self._fingerprinted(
            reader_inventory_path, root, "Reader-Facing Text Inventory"
        )
        reader_inventory = read_json(Path(reader_inventory_binding["path"]))
        self.acceptance.registry.validate("reader-facing-text-inventory", reader_inventory)
        precompile_report = quality_values["precompile_quality_report"]
        precompile_seal = quality_values["precompile_text_seal"]
        if not (
            reader_inventory.get("inventory_sha256")
            == precompile_report.get("inventory_sha256")
            == precompile_seal.get("inventory_sha256")
            and reader_inventory.get("reader_text_set_sha256")
            == precompile_report.get("reader_text_set_sha256")
            == precompile_seal.get("reader_text_set_sha256")
        ):
            raise ContractError(
                "Reader-Facing Text Inventory differs from governed quality lineage",
                data={
                    "first_failing_gate": "delivery_glossary_lineage",
                    "error_code": "final_evidence_delivery_glossary_lineage_invalid",
                },
            )
        governed_glossaries = (
            reader_inventory.get("delivery_glossary"),
            precompile_report.get("delivery_glossary"),
            precompile_seal.get("delivery_glossary"),
        )
        manifest_has_glossary = "delivery_glossary" in allowed_by_role
        glossary_binding = None
        if not manifest_has_glossary:
            if any(item is not None for item in governed_glossaries):
                raise ContractError(
                    "Governed Delivery Glossary is absent from Allowed Artifacts Manifest",
                    data={
                        "first_failing_gate": "delivery_glossary_lineage",
                        "error_code": "final_evidence_delivery_glossary_lineage_invalid",
                    },
                )
        else:
            governed_glossary = governed_glossaries[0]
            if (
                not isinstance(governed_glossary, dict)
                or any(item != governed_glossary for item in governed_glossaries[1:])
                or not isinstance(governed_glossary.get("glossary_id"), str)
                or not governed_glossary["glossary_id"]
                or not isinstance(governed_glossary.get("path"), str)
                or not governed_glossary["path"]
                or not isinstance(governed_glossary.get("sha256"), str)
                or len(governed_glossary["sha256"]) != 64
                or any(character not in "0123456789abcdef" for character in governed_glossary["sha256"])
            ):
                raise ContractError(
                    "Allowed Delivery Glossary differs from governed quality lineage",
                    data={
                        "first_failing_gate": "delivery_glossary_lineage",
                        "error_code": "final_evidence_delivery_glossary_lineage_invalid",
                    },
                )
            glossary_binding = self._fingerprinted(
                allowed_by_role["delivery_glossary"], root, "Delivery Glossary"
            )
            governed_path = (root / governed_glossary["path"]).resolve()
            if (
                governed_path != Path(glossary_binding["path"])
                or governed_glossary["sha256"] != glossary_binding["sha256"]
            ):
                raise ContractError(
                    "Allowed Delivery Glossary path or hash differs from governed quality lineage",
                    data={
                        "first_failing_gate": "delivery_glossary_lineage",
                        "error_code": "final_evidence_delivery_glossary_lineage_invalid",
                    },
                )

        gate_path = paths["global_gate_authority"].resolve()
        gate_file_sha = sha256_file(gate_path) if gate_path.is_file() else None
        gate_value = read_json(gate_path) if gate_path.is_file() else {}
        control_store_root = gate_path.parent
        expected_kernel_control_root = root.parent.resolve()
        if control_store_root != expected_kernel_control_root:
            raise ContractError(
                "Kernel Control Store and Global Gate authority must share one exact root"
            )
        current_gate = self.acceptance.require_current_global_gate(
            control_store_root=control_store_root
        )
        if (
            Path(current_gate.get("path", "")).resolve() != gate_path
            or current_gate.get("file_sha256") != gate_file_sha
            or gate_value.get("authority_sha256") != current_gate.get("authority_sha256")
        ):
            raise ArtifactDrift("Global Gate authority is not current")

        store = ControlStore(control_store_root, self.registry)
        if store.current_run_record_sha(run["run_id"]) != run_binding["sha256"]:
            raise ArtifactDrift("Control Store does not bind the current Run Record")

        binding_path = root / "review" / "acceptance" / "input-binding.json"
        if binding_path.is_file():
            existing = read_json(binding_path)
            existing_valid = True
            try:
                self.acceptance.validate_input_binding(
                    existing,
                    verify_files=True,
                    require_published_final_authority=False,
                )
            except Exception:
                existing_valid = False
            existing_run = existing.get("run", {}) if isinstance(existing, dict) else {}
            existing_checkpoint = existing_run.get("final_checkpoint", {})
            existing_revision = existing_run.get("acceptance_revision")
            committed_intent = None
            if isinstance(existing_revision, int):
                with self._connect_intents(control_store_root) as replay_control:
                    committed_intent = replay_control.execute(
                        "SELECT * FROM final_evidence_prepare_intents "
                        "WHERE run_id=? AND acceptance_revision=?",
                        (run["run_id"], existing_revision),
                    ).fetchone()
            intent_exact = bool(
                committed_intent is not None
                and committed_intent["state"] == "COMMITTED"
                and committed_intent["binding_path"] == str(binding_path)
                and committed_intent["binding_sha256"] == existing.get("binding_sha256")
                and Path(committed_intent["authority_path"]).resolve()
                == Path(existing_checkpoint.get("authority_path", "")).resolve()
                and committed_intent["authority_sha256"]
                == existing_checkpoint.get("authority_sha256")
            )
            if existing_valid and intent_exact:
                expected_paths = {
                    *(item["path"] for item in existing["artifacts"]),
                    *(item["path"] for item in existing["quality_inputs"].values()),
                }
                supplied_paths = {
                    final_pdf_binding["path"], supplied_main["path"],
                    compile_report_binding["path"], allowed_binding["path"],
                    *(item["path"] for item in quality_inputs.values()),
                }
                if glossary_binding is not None:
                    supplied_paths.add(glossary_binding["path"])
                if supplied_paths == expected_paths:
                    return self._result(existing, binding_path, idempotent=True)
                raise ArtifactDrift("Prepared Final Evidence conflicts with current inputs")
            self._move_failed_publication(root, binding_path)
            if not intent_exact:
                authority_value = existing_checkpoint.get("authority_path")
                if isinstance(authority_value, str):
                    authority_candidate = Path(authority_value).resolve()
                    if authority_candidate.is_relative_to(root):
                        self._move_failed_publication(root, authority_candidate)

        with self._connect_intents(control_store_root) as intents:
            intents.execute("BEGIN IMMEDIATE")
            revision = self._next_revision(intents, run["run_id"])
            authority_path = root / "workflow" / f"final-quality-ready.{revision}.json"
            artifacts = [
                {"logical_id": "final_pdf", **final_pdf_binding},
                {"logical_id": "main_tex", **supplied_main},
                {"logical_id": "final_compile_report", **compile_report_binding},
                {"logical_id": "allowed_artifacts_manifest", **allowed_binding},
            ]
            if glossary_binding is not None:
                artifacts.append(
                    {"logical_id": "delivery_glossary", **glossary_binding}
                )
            binding: dict[str, Any] = {
                "schema_name": "acceptance-v2-input-binding",
                "schema_version": "1.0.0",
                "activation_status": "target_only",
                "input_track": "kernel",
                "binding_id": hashlib.sha256(
                    f"{run['run_id']}\0{revision}\0{final_pdf_binding['sha256']}".encode()
                ).hexdigest(),
                "global_gate_authority": current_gate,
                "run": {
                    "run_id": run["run_id"],
                    "coordination_revision": run["coordination_revision"],
                    "acceptance_revision": revision,
                    "video_root": str(root),
                    "checkpoint": {
                        "name": "source_ready",
                        "status": "current",
                        "evidence_sha256": run["checkpoints"]["source_ready"]["evidence_sha256"],
                    },
                    "final_checkpoint": {
                        "name": "final_quality_ready",
                        "status": "current",
                        "authority_path": str(authority_path),
                        "authority_sha256": "0" * 64,
                    },
                    "run_record_path": run_binding["path"],
                    "run_record_sha256": run_binding["sha256"],
                    "control_store_root": str(control_store_root),
                    "producer_ids": sorted(
                        {
                            item["producer"]
                            for item in run.get("artifact_generations", {}).values()
                            if isinstance(item, dict) and isinstance(item.get("producer"), str)
                        }
                    ),
                    "repairer_ids": [],
                    "predecessor_generation_set_sha256": None,
                    "changed_generation_ids": [],
                },
                "quality_inputs": quality_inputs,
                "artifacts": artifacts,
                "rendered_pages": rendered_pages,
            }
            authority = {
                "schema_name": "acceptance-v2-final-quality-authority",
                "schema_version": "1.0.0",
                "activation_status": "target_only",
                "run_id": run["run_id"],
                "run_record_sha256": run_binding["sha256"],
                "acceptance_revision": revision,
                "checkpoint": {"name": "final_quality_ready", "status": "current"},
                "artifact_generations": final_authority_generations(binding),
            }
            authority["authority_sha256"] = fingerprint_contract_without(
                authority, "authority_sha256"
            )
            binding["run"]["final_checkpoint"]["authority_sha256"] = hashlib.sha256(
                canonical_json_bytes(authority)
            ).hexdigest()
            binding["binding_sha256"] = fingerprint_contract_without(
                binding, "binding_sha256"
            )
            self.acceptance.registry.validate("acceptance-v2-input-binding", binding)
            intents.execute(
                "INSERT INTO final_evidence_prepare_intents VALUES(?,?,?,?,?,?,?,?)",
                (
                    run["run_id"], revision, "PREPARED", str(binding_path), binding["binding_sha256"],
                    str(authority_path), binding["run"]["final_checkpoint"]["authority_sha256"], prepared_at,
                ),
            )
            published_authority = False
            try:
                authority_path.parent.mkdir(parents=True, exist_ok=True)
                binding_path.parent.mkdir(parents=True, exist_ok=True)
                write_json_atomic(authority_path, authority)
                published_authority = True
                self.acceptance.validate_input_binding(
                    binding,
                    verify_files=True,
                    require_published_final_authority=False,
                )
                write_json_atomic(binding_path, binding)
                if fault_point == "after_binding_write":
                    raise FinalEvidenceFault(fault_point)
                intents.execute(
                    "UPDATE final_evidence_prepare_intents SET state='COMMITTED' "
                    "WHERE run_id=? AND acceptance_revision=? AND state='PREPARED'",
                    (run["run_id"], revision),
                )
                intents.execute("COMMIT")
            except BaseException as error:
                intents.execute("ROLLBACK")
                if (
                    isinstance(error, FinalEvidenceFault)
                    and error.fault_point == "after_binding_write"
                ):
                    raise
                if published_authority:
                    self._move_failed_publication(root, authority_path)
                self._move_failed_publication(root, binding_path)
                raise
        return self._result(binding, binding_path, idempotent=False)

    @staticmethod
    def _result(binding: dict[str, Any], binding_path: Path, *, idempotent: bool) -> dict[str, Any]:
        final_pdf = next(
            item["path"] for item in binding["artifacts"] if item["logical_id"] == "final_pdf"
        )
        return {
            "run_id": binding["run"]["run_id"],
            "acceptance_revision": binding["run"]["acceptance_revision"],
            "input_binding_path": str(binding_path),
            "input_binding_sha256": binding["binding_sha256"],
            "final_quality_authority_path": binding["run"]["final_checkpoint"]["authority_path"],
            "final_quality_authority_sha256": binding["run"]["final_checkpoint"]["authority_sha256"],
            "final_pdf_path": final_pdf,
            "idempotent": idempotent,
        }
