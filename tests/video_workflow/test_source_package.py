from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
import uuid


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

TEST_ROOT = PROJECT_ROOT / "workspace/待删除/kernel-test-runs/source-package"

from video2pdf_workflow_kernel.errors import ContractError
from video2pdf_workflow_kernel.contracts import ContractRegistry
from video2pdf_workflow_kernel.source_acquisition import derive_source_identity
from video2pdf_workflow_kernel.utils import canonical_json_bytes, sha256_bytes


def create_directory_link(link: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except OSError:
        if os.name != "nt":
            raise unittest.SkipTest("directory symlinks are unavailable")
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise unittest.SkipTest("directory junctions are unavailable")


class RecordingContracts:
    def __init__(self, delegate: ContractRegistry | None = None) -> None:
        self.calls: list[str] = []
        self.delegate = delegate

    def validate(self, schema_name: str, instance: object) -> None:
        self.calls.append(schema_name)
        if self.delegate is not None:
            self.delegate.validate(schema_name, instance)


def new_run(label: str) -> Path:
    root = TEST_ROOT / f"{label}-{uuid.uuid4().hex}"
    root.mkdir(parents=True, exist_ok=False)
    return root


def write_bytes(root: Path, relative: str, value: bytes) -> dict[str, object]:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)
    return {
        "staged_path": relative,
        "sha256": sha256_bytes(value),
        "size_bytes": len(value),
    }


def write_json(root: Path, relative: str, value: dict) -> str:
    data = canonical_json_bytes(value)
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return sha256_bytes(data)


def candidate_id(label: str) -> str:
    return sha256_bytes(label.encode("utf-8"))


def build_inventory(
    root: Path,
    *,
    mode: str = "fresh_download",
    import_binding: dict | None = None,
) -> dict:
    origin = "platform_download" if mode == "fresh_download" else "verified_import"
    prefix = (
        "work/source-acquisition/candidates/e1"
        if mode == "fresh_download"
        else "work/source-acquisition/candidates"
    )
    staged = {
        "metadata": write_bytes(
            root,
            f"{prefix}/metadata/platform.json",
            canonical_json_bytes({"id": "yt-test-001", "title": "Source Package"}),
        ),
        "cover": write_bytes(root, f"{prefix}/cover/cover.jpg", b"jpeg-source"),
        "video": write_bytes(root, f"{prefix}/media/video.mp4", b"video-source"),
        "subtitle": write_bytes(
            root,
            f"{prefix}/subtitles/subtitle.en.manual.srt",
            b"1\n00:00:00,000 --> 00:00:01,000\nHello\n",
        ),
    }
    candidates = [
        {
            "candidate_id": candidate_id("metadata"),
            "role": "metadata",
            **staged["metadata"],
            "media_type": "application/json",
            "origin": origin,
            "language": None,
            "subtitle_kind": None,
            "technical_probe": {
                "status": "pass",
                "duration_seconds": None,
                "stream_types": ["metadata"],
                "codec_names": ["json"],
            },
        },
        {
            "candidate_id": candidate_id("cover"),
            "role": "cover",
            **staged["cover"],
            "media_type": "image/jpeg",
            "origin": origin,
            "language": None,
            "subtitle_kind": None,
            "technical_probe": {
                "status": "pass",
                "duration_seconds": None,
                "stream_types": ["image"],
                "codec_names": ["jpeg"],
            },
        },
        {
            "candidate_id": candidate_id("video"),
            "role": "video",
            **staged["video"],
            "media_type": "video/mp4",
            "origin": origin,
            "language": None,
            "subtitle_kind": None,
            "technical_probe": {
                "status": "pass",
                "duration_seconds": 120.0,
                "stream_types": ["video", "audio"],
                "codec_names": ["h264", "aac"],
            },
        },
        {
            "candidate_id": candidate_id("subtitle-en-manual"),
            "role": "subtitle",
            **staged["subtitle"],
            "media_type": "application/x-subrip",
            "origin": origin,
            "language": "en",
            "subtitle_kind": "manual",
            "technical_probe": {
                "status": "pass",
                "duration_seconds": 120.0,
                "stream_types": ["subtitle"],
                "codec_names": ["subrip"],
            },
        },
    ]
    platform = "youtube"
    identity = derive_source_identity(platform, "yt-test-001")
    return {
        "schema_name": "source-candidate-inventory",
        "schema_version": "1.0.0",
        "kernel_version": "2.0.0",
        "run_id": "0" * 32,
        "acquisition_id": "a" * 32,
        "source_epoch": 1,
        "mode": mode,
        "adapter": {"id": platform, "contract_version": "1.0.0"},
        "canonical_platform": platform,
        "canonical_item_id": "yt-test-001",
        "source_identity_scheme": "canonical-platform-item-v1",
        "source_identity": identity,
        "provider": {
            "kind": "recorded_fixture" if mode == "fresh_download" else "verified_import",
            "recording_sha256": "b" * 64 if mode == "fresh_download" else None,
            "tool_versions": [{"name": "source-test", "version": "1.0.0"}],
        },
        "authentication_classification": (
            "cookie_accepted" if mode == "fresh_download" else "not_applicable"
        ),
        "policy_binding": {
            "policy_id": "source-acquisition-policy",
            "version": "1.0.0",
            "sha256": "c" * 64,
            "content_classification": "language_learning",
            "subtitle_language_priority": ["en", "zh-Hans"],
            "whisper_allowed": True,
        },
        "source_metadata": {
            "original_title": "Source Package",
            "duration_seconds": 120.0,
        },
        "commands": [
            {
                "command_id": "acquire",
                "purpose": (
                    "download"
                    if mode == "fresh_download"
                    else "verified_import_validation"
                ),
                "command_argv_redacted": ["source-test", "--recorded"],
                "exit_classification": "success",
                "sanitized_log_sha256": "d" * 64,
            }
        ],
        "candidates": candidates,
        "import_binding": import_binding,
        "status": "candidates_ready",
    }


def persist_fresh_controls(
    root: Path, inventory: dict, *, mechanical_injection: bool = False
) -> tuple[str, str, str]:
    inventory_path = "work/source-acquisition/candidate-inventory.json"
    inventory_sha = write_json(root, inventory_path, inventory)
    skeleton = {
        "schema_name": "source-acquisition-decision-skeleton",
        "schema_version": "1.0.0",
        "kernel_version": "2.0.0",
        "run_id": inventory["run_id"],
        "source_epoch": inventory["source_epoch"],
        "acquisition_id": inventory["acquisition_id"],
        "task_id": "1" * 32,
        "source_identity": inventory["source_identity"],
        "candidate_inventory": {
            "path": inventory_path,
            "generation": 1,
            "sha256": inventory_sha,
        },
        "policy_binding": {
            key: inventory["policy_binding"][key]
            for key in ("policy_id", "version", "sha256")
        },
        "allowed_judgment": {
            "subtitle_candidate_ids": [candidate_id("subtitle-en-manual")],
            "whisper_choices": ["not_required", "use_whisper", "unavailable"],
            "whisper_audio_candidate_id": candidate_id("video"),
            "known_gap_codes": [
                "missing_subtitles",
                "partial_subtitles",
                "subtitle_quality",
            ],
        },
        "required_judgment_fields": [
            "selected_subtitle_candidate_id",
            "subtitle_selection_rationale",
            "whisper_fallback.choice",
            "whisper_fallback.rationale",
            "known_gaps",
        ],
        "target_checkpoint": "source_acquisition_decision_ready",
        "status": "prepared",
    }
    skeleton_path = "work/source-acquisition/decision.skeleton.json"
    skeleton_sha = write_json(root, skeleton_path, skeleton)
    judgment = {
        "selected_subtitle_candidate_id": candidate_id("subtitle-en-manual"),
        "subtitle_selection_rationale": "The English manual track is usable.",
        "whisper_fallback": {
            "choice": "not_required",
            "rationale": "A preferred manual subtitle exists.",
        },
        "known_gaps": [],
    }
    if mechanical_injection:
        judgment["source_version"] = "f" * 64
    patch = {
        "schema_name": "source-acquisition-judgment-patch",
        "schema_version": "2.0.0",
        "kernel_version": "2.0.0",
        "task_id": skeleton["task_id"],
        "attempt_id": "2" * 24,
        "task_envelope_sha256": "e" * 64,
        "skeleton_sha256": skeleton_sha,
        "judgment": judgment,
    }
    patch_path = "workflow/source-acquisition-judgment-patch.json"
    write_json(root, patch_path, patch)
    return inventory_path, skeleton_path, patch_path


class SourcePackageTests(unittest.TestCase):
    def test_materializer_rejects_descendant_junction_before_writing_outside_run(
        self,
    ) -> None:
        from video2pdf_workflow_kernel.source_package import materialize_source_package

        root = new_run("destination-descendant-junction")
        inventory = build_inventory(root)
        inventory_path, skeleton_path, patch_path = persist_fresh_controls(
            root, inventory
        )
        destination = (
            "work/source-acquisition/publications/"
            + "9" * 32
            + "/candidate/source"
        )
        destination_root = root / destination
        destination_root.mkdir(parents=True)
        outside = TEST_ROOT / "outside" / uuid.uuid4().hex
        create_directory_link(destination_root / "metadata", outside)

        with self.assertRaisesRegex(ContractError, "link|reparse|boundary"):
            materialize_source_package(
                root,
                inventory_path=inventory_path,
                decision_skeleton_path=skeleton_path,
                judgment_patch_path=patch_path,
                destination_source_root=destination,
                published_at="2026-07-18T12:00:00+08:00",
                contracts=RecordingContracts(),
            )

        self.assertFalse((outside / "platform.json").exists())

    def test_materializer_rejects_linked_candidate_root_even_when_target_is_inside_run(
        self,
    ) -> None:
        from video2pdf_workflow_kernel.source_package import materialize_source_package

        root = new_run("linked-candidate-root")
        inventory = build_inventory(root)
        inventory_path, skeleton_path, patch_path = persist_fresh_controls(
            root, inventory
        )
        destination = (
            "work/source-acquisition/publications/"
            + "a" * 32
            + "/candidate/source"
        )
        destination_root = root / destination
        destination_root.parent.mkdir(parents=True)
        linked_target = root / "work/source-acquisition/linked-candidate-target"
        create_directory_link(destination_root, linked_target)

        with self.assertRaisesRegex(ContractError, "link|reparse"):
            materialize_source_package(
                root,
                inventory_path=inventory_path,
                decision_skeleton_path=skeleton_path,
                judgment_patch_path=patch_path,
                destination_source_root=destination,
                published_at="2026-07-18T12:00:00+08:00",
                contracts=RecordingContracts(),
            )

        self.assertEqual(tuple(linked_target.iterdir()), ())

    def test_materializer_rejects_a_hardlinked_candidate_file(self) -> None:
        from video2pdf_workflow_kernel.source_package import materialize_source_package

        root = new_run("hardlinked-candidate-file")
        inventory = build_inventory(root)
        inventory_path, skeleton_path, patch_path = persist_fresh_controls(
            root, inventory
        )
        candidate = root / inventory["candidates"][0]["staged_path"]
        mirror_root = (
            PROJECT_ROOT
            / "待删除/source-package-hardlinks"
            / uuid.uuid4().hex
        )
        mirror_root.mkdir(parents=True, exist_ok=False)
        os.link(candidate, mirror_root / candidate.name)

        destination = (
            "work/source-acquisition/publications/"
            + "b" * 32
            + "/candidate/source"
        )
        with self.assertRaisesRegex(ContractError, "independent regular file"):
            materialize_source_package(
                root,
                inventory_path=inventory_path,
                decision_skeleton_path=skeleton_path,
                judgment_patch_path=patch_path,
                destination_source_root=destination,
                published_at="2026-07-18T12:00:00+08:00",
                contracts=RecordingContracts(),
            )

        self.assertFalse((root / destination).exists())

    def test_fresh_materializer_rejects_task_local_skeleton_path(self) -> None:
        from video2pdf_workflow_kernel.source_package import materialize_source_package

        root = new_run("legacy-skeleton-path")
        inventory = build_inventory(root)
        inventory_path, skeleton_path, patch_path = persist_fresh_controls(
            root, inventory
        )
        legacy_path = (
            "workflow/tasks/11111111111111111111111111111111/decision.skeleton.json"
        )
        write_bytes(root, legacy_path, (root / skeleton_path).read_bytes())

        with self.assertRaisesRegex(ContractError, "fixed authority"):
            materialize_source_package(
                root,
                inventory_path=inventory_path,
                decision_skeleton_path=legacy_path,
                judgment_patch_path=patch_path,
                destination_source_root=(
                    "work/source-acquisition/publications/"
                    + "8" * 32
                    + "/candidate/source"
                ),
                published_at="2026-07-18T12:00:00+08:00",
                contracts=RecordingContracts(),
            )

    def test_fresh_materializer_binds_controls_and_canonicalizes_every_artifact(
        self,
    ) -> None:
        from video2pdf_workflow_kernel.source_package import materialize_source_package

        root = new_run("fresh")
        inventory = build_inventory(root)
        inventory_path, skeleton_path, patch_path = persist_fresh_controls(
            root, inventory
        )
        contracts = RecordingContracts(ContractRegistry(PROJECT_ROOT))

        result = materialize_source_package(
            root,
            inventory_path=inventory_path,
            decision_skeleton_path=skeleton_path,
            judgment_patch_path=patch_path,
            destination_source_root=(
                "work/source-acquisition/publications/" + "3" * 32 + "/candidate/source"
            ),
            published_at="2026-07-18T12:00:00+08:00",
            contracts=contracts,
        )

        manifest = result.manifest
        self.assertEqual(
            contracts.calls,
            [
                "source-candidate-inventory",
                "source-acquisition-decision-skeleton",
                "source-acquisition-judgment-patch",
                "source-manifest",
            ],
        )
        self.assertEqual(manifest["mode"], "fresh_download")
        self.assertEqual(manifest["source_identity"], inventory["source_identity"])
        self.assertEqual(
            manifest["provenance"]["candidate_inventory_sha256"],
            sha256_bytes((root / inventory_path).read_bytes()),
        )
        self.assertEqual(
            manifest["provenance"]["decision_skeleton_sha256"],
            sha256_bytes((root / skeleton_path).read_bytes()),
        )
        self.assertEqual(
            manifest["selection"],
            {
                "selected_subtitle_artifact_id": "subtitle_en_manual",
                "whisper_status": "not_required",
            },
        )
        by_id = {item["logical_id"]: item for item in manifest["artifacts"]}
        self.assertEqual(
            set(by_id), {"metadata", "cover", "video", "subtitle_en_manual"}
        )
        self.assertEqual(by_id["metadata"]["path"], "source/metadata/platform.json")
        self.assertEqual(by_id["cover"]["path"], "source/cover/cover.jpg")
        self.assertEqual(by_id["video"]["path"], "source/media/video.mp4")
        self.assertEqual(
            by_id["subtitle_en_manual"]["path"],
            "source/subtitles/subtitle.en.manual.srt",
        )
        self.assertEqual(
            manifest["source_version_basis"]["artifacts"],
            sorted(
                manifest["source_version_basis"]["artifacts"],
                key=lambda item: item["logical_id"],
            ),
        )
        self.assertEqual(result.manifest_sha256, sha256_bytes(result.manifest_bytes))
        self.assertEqual(result.manifest_path.read_bytes(), result.manifest_bytes)

    def test_verified_import_skips_agent_and_preserves_content_version(self) -> None:
        from video2pdf_workflow_kernel.source_package import materialize_source_package

        fresh_root = new_run("fresh-version")
        fresh_inventory = build_inventory(fresh_root)
        inventory_path, skeleton_path, patch_path = persist_fresh_controls(
            fresh_root, fresh_inventory
        )
        fresh = materialize_source_package(
            fresh_root,
            inventory_path=inventory_path,
            decision_skeleton_path=skeleton_path,
            judgment_patch_path=patch_path,
            destination_source_root="work/source-acquisition/publications/" + "4" * 32 + "/candidate/source",
            published_at="2026-07-18T12:00:00+08:00",
            contracts=RecordingContracts(ContractRegistry(PROJECT_ROOT)),
        )

        imported_root = new_run("verified-import")
        validation = {
            name: {"status": "pass", "evidence_sha256": character * 64}
            for name, character in zip(
                (
                    "canonical_identity",
                    "schema_compatibility",
                    "artifact_fingerprints",
                    "subtitle_policy",
                    "technical_properties",
                    "source_quality",
                    "original_only",
                ),
                "1234567",
                strict=True,
            )
        }
        import_binding = {
            "prior_run_id": fresh.manifest["run_id"],
            "prior_source_manifest_sha256": fresh.manifest_sha256,
            "prior_source_identity": fresh.manifest["source_identity"],
            "prior_source_version": fresh.manifest["source_version"],
            "validation": validation,
        }
        imported_inventory = build_inventory(
            imported_root, mode="verified_import", import_binding=import_binding
        )
        import_inventory_path = "work/source-acquisition/candidate-inventory.json"
        write_json(imported_root, import_inventory_path, imported_inventory)
        contracts = RecordingContracts(ContractRegistry(PROJECT_ROOT))

        imported = materialize_source_package(
            imported_root,
            inventory_path=import_inventory_path,
            destination_source_root="work/source-acquisition/publications/" + "5" * 32 + "/candidate/source",
            published_at="2026-07-18T12:01:00+08:00",
            contracts=contracts,
        )

        self.assertEqual(
            contracts.calls, ["source-candidate-inventory", "source-manifest"]
        )
        self.assertEqual(imported.manifest["mode"], "verified_import")
        self.assertEqual(
            imported.manifest["source_identity"], fresh.manifest["source_identity"]
        )
        self.assertEqual(
            imported.manifest["source_version"], fresh.manifest["source_version"]
        )
        self.assertTrue(
            all(
                item["origin"] == "verified_import"
                for item in imported.manifest["artifacts"]
            )
        )
        self.assertNotIn("judgment_patch", imported.manifest["provenance"])

    def test_materializer_rejects_path_escape_and_hash_drift_before_writes(self) -> None:
        from video2pdf_workflow_kernel.source_package import materialize_source_package

        for mutation, expected in (
            ("escape", "staged path"),
            ("hash", "fingerprint drift"),
        ):
            with self.subTest(mutation=mutation):
                root = new_run(mutation)
                inventory = build_inventory(root)
                if mutation == "escape":
                    inventory["candidates"][0]["staged_path"] = (
                        "work/source-acquisition/../outside.json"
                    )
                else:
                    inventory["candidates"][0]["sha256"] = "0" * 64
                inventory_path, skeleton_path, patch_path = persist_fresh_controls(
                    root, inventory
                )
                destination = (
                    "work/source-acquisition/publications/"
                    + "6" * 32
                    + "/candidate/source"
                )
                with self.assertRaisesRegex(ContractError, expected):
                    materialize_source_package(
                        root,
                        inventory_path=inventory_path,
                        decision_skeleton_path=skeleton_path,
                        judgment_patch_path=patch_path,
                        destination_source_root=destination,
                        published_at="2026-07-18T12:00:00+08:00",
                        contracts=RecordingContracts(),
                    )
                self.assertFalse((root / destination).exists())

    def test_materializer_rejects_binding_drift_and_agent_mechanical_fields(
        self,
    ) -> None:
        from video2pdf_workflow_kernel.source_package import materialize_source_package

        for mutation, expected in (
            ("inventory-binding", "Inventory fingerprint"),
            ("mechanical", "mechanical"),
        ):
            with self.subTest(mutation=mutation):
                root = new_run(mutation)
                inventory = build_inventory(root)
                inventory_path, skeleton_path, patch_path = persist_fresh_controls(
                    root, inventory, mechanical_injection=mutation == "mechanical"
                )
                if mutation == "inventory-binding":
                    skeleton = json.loads(
                        (root / skeleton_path).read_text(encoding="utf-8")
                    )
                    skeleton["candidate_inventory"]["sha256"] = "0" * 64
                    skeleton_sha = write_json(root, skeleton_path, skeleton)
                    patch = json.loads(
                        (root / patch_path).read_text(encoding="utf-8")
                    )
                    patch["skeleton_sha256"] = skeleton_sha
                    write_json(root, patch_path, patch)
                destination = (
                    "work/source-acquisition/publications/"
                    + "7" * 32
                    + "/candidate/source"
                )
                with self.assertRaisesRegex(ContractError, expected):
                    materialize_source_package(
                        root,
                        inventory_path=inventory_path,
                        decision_skeleton_path=skeleton_path,
                        judgment_patch_path=patch_path,
                        destination_source_root=destination,
                        published_at="2026-07-18T12:00:00+08:00",
                        contracts=RecordingContracts(),
                    )
                self.assertFalse((root / destination).exists())


if __name__ == "__main__":
    unittest.main()
