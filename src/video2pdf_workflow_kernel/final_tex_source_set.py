from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

from .errors import CompileDependencyGap
from .source_candidates import KERNEL_VERSION
from .utils import canonical_json_bytes, path_fold, sha256_bytes, sha256_file


class FinalEditableTexSourceSetProjector:
    """Own recorder-derived editable TeX closure projection and failure policy."""

    def project(
        self,
        *,
        compile_entries: list[dict[str, Any]],
        recorder_cwd: Path,
        recorder_inputs: list[Path],
        registered_runtime_input_paths: set[str],
        project_root: Path,
        entrypoint_staging_paths: list[str],
        final_pdf: dict[str, Any],
        generation_set_sha256: str,
        compile_manifest_sha256: str,
        recorder_path: str,
        recorder_sha256: str,
    ) -> dict[str, Any]:
        if len(entrypoint_staging_paths) != 1:
            raise CompileDependencyGap(
                "Final Editable TeX Source Set entrypoint is missing or ambiguous",
                data={
                    "first_failing_gate": "final_editable_tex_source_set_entrypoint",
                    "error_code": (
                        "final_tex_entrypoint_missing"
                        if not entrypoint_staging_paths
                        else "final_tex_entrypoint_ambiguous"
                    ),
                },
            )
        root = recorder_cwd.resolve()
        observed = {
            path_fold(str((path if path.is_absolute() else root / path).resolve()))
            for path in recorder_inputs
            if path_fold(
                str((path if path.is_absolute() else root / path).resolve())
            )
            not in registered_runtime_input_paths
        }
        entrypoint = PurePosixPath(
            entrypoint_staging_paths[0].replace("\\", "/")
        ).as_posix()
        members: list[dict[str, Any]] = []
        represented: set[str] = set()
        for entry in compile_entries:
            staging = PurePosixPath(
                str(entry.get("staging_path", "")).replace("\\", "/")
            )
            staged = (root / Path(*staging.parts)).resolve()
            if (
                staging.suffix.casefold() != ".tex"
                or path_fold(str(staged)) not in observed
            ):
                continue
            source = Path(str(entry.get("source_path", ""))).resolve()
            if (
                not source.is_file()
                or sha256_file(source) != entry.get("sha256")
                or not isinstance(entry.get("generation"), int)
                or entry["generation"] < 1
            ):
                raise CompileDependencyGap(
                    "Final Editable TeX Source Set member identity is stale",
                    data={
                        "first_failing_gate": "final_editable_tex_source_set_identity",
                        "error_code": "final_tex_source_identity_stale",
                    },
                )
            members.append(
                {
                    "logical_id": entry["logical_id"],
                    "generation": entry["generation"],
                    "path": str(source),
                    "sha256": entry["sha256"],
                    "size": source.stat().st_size,
                    "role": (
                        "tex_entrypoint"
                        if staging.as_posix() == entrypoint
                        else "included_tex_source"
                    ),
                }
            )
            represented.add(path_fold(str(staged)))
        consumed = {item for item in observed if Path(item).suffix.casefold() == ".tex"}
        if consumed != represented:
            raise CompileDependencyGap(
                "Final Editable TeX Source Set contradicts compile dependency evidence",
                data={
                    "first_failing_gate": "final_editable_tex_source_set_membership",
                    "error_code": "final_tex_dependency_evidence_mismatch",
                },
            )
        members.sort(
            key=lambda item: (
                item["role"] != "tex_entrypoint",
                path_fold(item["path"]),
            )
        )
        evidence = [
            {key: value for key, value in member.items() if key != "role"}
            for member in members
        ]
        artifact = {
            "schema_name": "final-editable-tex-source-set",
            "schema_version": "1.0.0",
            "kernel_version": KERNEL_VERSION,
            "project_root": str(project_root.resolve()),
            "generation_set_sha256": generation_set_sha256,
            "final_pdf": final_pdf,
            "compile_evidence": {
                "compile_manifest_sha256": compile_manifest_sha256,
                "recorder_path": recorder_path,
                "recorder_sha256": recorder_sha256,
                "dependency_closure_complete": True,
                "final_pdf": final_pdf,
                "tex_entrypoint_logical_id": next(
                    member["logical_id"]
                    for member in members
                    if member["role"] == "tex_entrypoint"
                ),
                "consumed_project_tex_sources": evidence,
            },
            "members": members,
        }
        artifact["source_set_sha256"] = sha256_bytes(canonical_json_bytes(artifact))
        return artifact
