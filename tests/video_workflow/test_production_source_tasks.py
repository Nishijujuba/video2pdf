from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import unittest
import uuid


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class ProductionSourceTaskTests(unittest.TestCase):
    def _initialized_youtube_run(self):
        from video2pdf_workflow_kernel.adapters import (
            PlatformProbeRequest,
            RecordedCommandRunner,
            YouTubePlatformAdapter,
            YtDlpRuntime,
        )
        from video2pdf_workflow_kernel.kernel import VideoWorkflowKernel

        root = PROJECT_ROOT / "待删除" / "st" / uuid.uuid4().hex[:8]
        workspace = root / "workspace"
        staging = root / "provider-staging"
        cookie = root / "credentials/cookies.txt"
        staging.mkdir(parents=True)
        cookie.parent.mkdir(parents=True)
        cookie.write_text(
            "# Netscape HTTP Cookie File\n"
            ".example.test\tTRUE\t/\tTRUE\t2147483647\tSID\trecorded\n",
            encoding="utf-8",
        )
        adapter = YouTubePlatformAdapter(
            YtDlpRuntime(
                python_executable=Path("python"),
                ffmpeg_dir=Path("ffmpeg-bin"),
                ffprobe_executable=Path("ffprobe"),
            )
        )
        runner = RecordedCommandRunner(
            PROJECT_ROOT
            / "tests/video_workflow/fixtures/providers/youtube/fresh-download"
        )
        kernel = VideoWorkflowKernel(
            workspace,
            resource_provider_verifiers={
                "source-task-test": lambda **_identity: (
                    "provider-proof://source-task-test/succeeded"
                )
            },
        )
        probe = kernel.bootstrap_production_source(
            adapter=adapter,
            request=PlatformProbeRequest(
                source_url="https://www.youtube.com/watch?v=yt-test-001",
                localized_cookie_file=cookie,
                staging_root=staging,
            ),
            runner=runner,
            task_start="2026-07-18T12:00:00+08:00",
            request_id=f"source-task-{uuid.uuid4().hex[:8]}",
            provider_kind="recorded_fixture",
        )
        return kernel, kernel.initialize_production_source(probe).run_dir

    @staticmethod
    def _release_admitted_launch(kernel, claim, resource: str) -> None:
        launch_tokens: list[str] = []

        def launch(token: str) -> str:
            launch_tokens.append(token)
            return "started"

        kernel.launch_admitted_task(
            claim.attempt_id,
            claim.claim_generation,
            (resource,),
            launch,
        )
        kernel.release_resource_lease(
            claim.attempt_id,
            claim.claim_generation,
            launch_tokens[0],
            terminal_evidence={
                "evidence_class": "provider_terminal_result",
                "provider": "source-task-test",
                "terminal_result_id": f"terminal-{claim.attempt_id}",
                "declared_outcome": "succeeded",
                "observed_at": "2026-07-18T04:10:00Z",
            },
        )

    @staticmethod
    def _credential_resolution_evidence(
        record: dict,
        breaker: dict,
        *,
        probe_command_argv_redacted: list[str] | None = None,
    ) -> dict:
        return {
            "$schema": (
                "https://video2pdf.local/schemas/video-workflow/v1/"
                "source-credential-resolution-evidence.v1.schema.json"
            ),
            "schema_name": "source-credential-resolution-evidence",
            "schema_version": "1.0.0",
            "kernel_version": "2.0.0",
            "run_id": record["run_id"],
            "source_epoch": record["source_epoch"],
            "canonical_platform": record["canonical_platform"],
            "resource_class": record["source_blocker"]["resource_class"],
            "breaker_key": breaker["breaker_key"],
            "breaker_updated_seq": breaker["updated_seq"],
            "blocker_evidence_sha256": record["source_blocker"][
                "evidence_sha256"
            ],
            "authentication_classification": "cookie_accepted",
            "credential_reference": "<localized-cookie-file>",
            "probe_kind": "provider_authentication_probe",
            "probe_command_argv_redacted": (
                probe_command_argv_redacted
                if probe_command_argv_redacted is not None
                else [
                    "<python-executable>",
                    "-m",
                    "yt_dlp",
                    "--cookies",
                    "<localized-cookie-file>",
                    "--simulate",
                    "<canonical-source-url>",
                ]
            ),
            "provider_exit_code": 0,
            "credential_probe_outcome": "accepted",
            "provider_result_sha256": hashlib.sha256(
                b"credential-probe-result"
            ).hexdigest(),
            "sanitized_log_sha256": hashlib.sha256(
                b"credential-probe-sanitized-log"
            ).hexdigest(),
            "verified_at": "2026-07-18T05:00:00Z",
        }

    def _prepared_credential_resolution(self):
        from video2pdf_workflow_kernel.adapters import PlatformAdapterError
        from video2pdf_workflow_kernel.source_acquisition import (
            SourceUserInputResolutionFault,
            persist_source_blocker,
        )
        from video2pdf_workflow_kernel.utils import canonical_json_bytes, read_json

        kernel, run_dir = self._initialized_youtube_run()
        persist_source_blocker(
            kernel,
            run_dir,
            "youtube",
            PlatformAdapterError(
                "platform cookie was rejected",
                classification="source_authentication_required",
                exit_code=30,
                blocker_kind="user_input",
                data={"authentication_classification": "cookie_rejected"},
            ),
        )
        blocked = read_json(run_dir / "workflow/run.json")
        closed_breaker = kernel.set_resource_circuit_breaker(
            "youtube_download",
            state="closed",
            reason="localized cookie credential was refreshed and probed",
            platform="youtube",
        )
        evidence = self._credential_resolution_evidence(blocked, closed_breaker)
        evidence_sha = hashlib.sha256(canonical_json_bytes(evidence)).hexdigest()
        with self.assertRaises(SourceUserInputResolutionFault):
            kernel.resolve_source_user_input(
                run_dir,
                authentication_classification="cookie_accepted",
                credential_evidence=evidence,
                credential_evidence_sha256=evidence_sha,
                fault_point="after_resolution_mutation_prepared",
            )
        mutation = kernel.control_store.prepared_run_state_mutation(
            blocked["run_id"]
        )
        self.assertIsNotNone(mutation)
        replacement = json.loads(mutation["replacement_run_record_json"])
        generation = replacement["artifact_generations"][
            "source_credential_resolution_evidence"
        ]
        return kernel, run_dir, blocked, replacement, generation

    def _stage_provider_outputs(
        self,
        kernel,
        run_dir: Path,
        claim,
        *,
        content_marker: str = "",
        include_english_subtitle: bool = False,
    ) -> dict:
        from video2pdf_workflow_kernel.source_candidates import (
            SourceCandidatePolicy,
        )
        from video2pdf_workflow_kernel.utils import read_json, write_json_atomic

        record = read_json(run_dir / "workflow/run.json")
        candidate_root = claim.attempt_dir / "o/candidates"
        payloads = {
            "metadata/platform.json": (
                json.dumps(
                    {"title": "Recorded Source", "marker": content_marker},
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
                + b"\n"
            ),
            "cover/cover.jpg": (
                b"recorded-jpeg-bytes" + content_marker.encode("utf-8")
            ),
            "media/video.mp4": (
                b"recorded-mp4-with-audio" + content_marker.encode("utf-8")
            ),
        }
        if include_english_subtitle:
            payloads["subtitles/subtitle.en.srt"] = (
                "1\n00:00:00,000 --> 00:00:01,500\n"
                f"Recorded speech {content_marker}.\n"
            ).encode("utf-8")
        for relative, payload in payloads.items():
            path = candidate_root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)

        def candidate(
            relative: str,
            *,
            role: str,
            media_type: str,
            stream_types: list[str],
            codec_names: list[str],
            language: str | None = None,
            subtitle_kind: str | None = None,
        ) -> dict:
            payload = payloads[relative]
            digest = hashlib.sha256(payload).hexdigest()
            return {
                "candidate_id": hashlib.sha256(
                    f"candidate:{relative}:{digest}".encode("utf-8")
                ).hexdigest(),
                "role": role,
                "staged_path": (
                    "work/source-acquisition/candidates/"
                    f"e{record['source_epoch']}/{relative}"
                ),
                "media_type": media_type,
                "sha256": digest,
                "size_bytes": len(payload),
                "origin": "platform_download",
                "language": language,
                "subtitle_kind": subtitle_kind,
                "technical_probe": {
                    "status": "pass",
                    "duration_seconds": 30 if role == "video" else None,
                    "stream_types": stream_types,
                    "codec_names": codec_names,
                },
            }

        candidates = [
            candidate(
                "metadata/platform.json",
                role="metadata",
                media_type="application/json",
                stream_types=["metadata"],
                codec_names=["json"],
            ),
            candidate(
                "cover/cover.jpg",
                role="cover",
                media_type="image/jpeg",
                stream_types=["image"],
                codec_names=["jpeg"],
            ),
            candidate(
                "media/video.mp4",
                role="video",
                media_type="video/mp4",
                stream_types=["video", "audio"],
                codec_names=["h264", "aac"],
            ),
        ]
        if include_english_subtitle:
            candidates.append(
                candidate(
                    "subtitles/subtitle.en.srt",
                    role="subtitle",
                    media_type="application/x-subrip",
                    stream_types=["subtitle"],
                    codec_names=["subrip"],
                    language="en",
                    subtitle_kind="manual",
                )
            )
        acquisition_id = hashlib.sha256(
            (
                f"acquisition:{record['run_id']}:"
                f"{record['source_epoch']}:{content_marker}"
            ).encode("utf-8")
        ).hexdigest()[:32]
        policy_binding = SourceCandidatePolicy(
            content_classification="language_learning",
            subtitle_language_priority=("en", "zh-Hans"),
            whisper_allowed=True,
        ).binding()
        inventory = {
            "schema_name": "source-candidate-inventory",
            "schema_version": "1.0.0",
            "kernel_version": "2.0.0",
            "run_id": record["run_id"],
            "acquisition_id": acquisition_id,
            "source_epoch": record["source_epoch"],
            "mode": "fresh_download",
            "adapter": {"id": "youtube", "contract_version": "1.0.0"},
            "canonical_platform": "youtube",
            "canonical_item_id": record["canonical_item_id"],
            "source_identity_scheme": "canonical-platform-item-v1",
            "source_identity": record["source_identity"],
            "provider": {
                "kind": "recorded_fixture",
                "recording_sha256": hashlib.sha256(b"recording").hexdigest(),
                "tool_versions": [
                    {"name": "recorded-youtube", "version": "1.0.0"},
                    {"name": "ffprobe", "version": "7.1"},
                ],
            },
            "authentication_classification": "cookie_accepted",
            "policy_binding": policy_binding,
            "source_metadata": {
                "original_title": record["original_title"],
                "duration_seconds": 30,
            },
            "commands": [
                {
                    "command_id": "download",
                    "purpose": "download",
                    "command_argv_redacted": [
                        "recorded-youtube",
                        "--cookies",
                        "<localized-cookie-file>",
                        "--download",
                        "https://www.youtube.com/watch?v=yt-test-001",
                    ],
                    "exit_classification": "success",
                    "sanitized_log_sha256": hashlib.sha256(b"log").hexdigest(),
                }
            ],
            "candidates": candidates,
            "import_binding": None,
            "status": "candidates_ready",
        }
        output_root = claim.attempt_dir / "o"
        output_root.mkdir(parents=True, exist_ok=True)
        inventory_sha = write_json_atomic(
            output_root / "candidate-inventory.json", inventory
        )
        semantic_task_id = kernel.derive_production_source_task_id(
            run_dir,
            task_stage="semantic_judgment",
            logical_task_key="source-semantic-epoch-1",
        )
        video = next(item for item in candidates if item["role"] == "video")
        subtitle_ids = [
            item["candidate_id"]
            for item in candidates
            if item["role"] == "subtitle"
        ]
        prior_inventory = record["artifact_generations"].get(
            "source_candidate_inventory"
        )
        inventory_generation = (
            1
            if prior_inventory is None
            else int(prior_inventory["generation"]) + 1
        )
        skeleton = {
            "schema_name": "source-acquisition-decision-skeleton",
            "schema_version": "1.0.0",
            "kernel_version": "2.0.0",
            "run_id": record["run_id"],
            "source_epoch": record["source_epoch"],
            "acquisition_id": acquisition_id,
            "task_id": semantic_task_id,
            "source_identity": record["source_identity"],
            "candidate_inventory": {
                "path": "work/source-acquisition/candidate-inventory.json",
                "generation": inventory_generation,
                "sha256": inventory_sha,
            },
            "policy_binding": {
                "policy_id": "source-acquisition-policy",
                "version": "1.0.0",
                "sha256": policy_binding["sha256"],
            },
            "allowed_judgment": {
                "subtitle_candidate_ids": subtitle_ids,
                "whisper_choices": ["not_required", "use_whisper", "unavailable"],
                "whisper_audio_candidate_id": video["candidate_id"],
                "known_gap_codes": [
                    "metadata_incomplete",
                    "missing_audio",
                    "missing_cover",
                    "missing_subtitles",
                    "other",
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
        write_json_atomic(output_root / "decision.skeleton.json", skeleton)
        return video

    def test_provider_task_is_v3_platform_admitted_and_prompt_free(self) -> None:
        from video2pdf_workflow_kernel.utils import read_json

        kernel, run_dir = self._initialized_youtube_run()
        prepared = kernel.prepare_production_source_task(
            run_dir,
            task_stage="provider_acquisition",
            logical_task_key="source-provider-epoch-1",
            prepared_at="2026-07-18T04:01:00Z",
        )

        envelope = read_json(prepared.envelope_path)
        self.assertEqual(envelope["schema_version"], "3.0.0")
        self.assertEqual(envelope["task_stage"], "provider_acquisition")
        self.assertEqual(envelope["platform"], "youtube")
        self.assertEqual(envelope["resource_request"], ["youtube_download"])
        self.assertIsNone(envelope["generated_prompt"])
        self.assertFalse(prepared.prompt_path.exists())
        self.assertEqual(
            [item["logical_id"] for item in envelope["required_outputs"]],
            [
                "source_candidate_inventory",
                "source_acquisition_decision_skeleton",
            ],
        )

    def test_provider_task_claim_uses_platform_resource_admission(self) -> None:
        kernel, run_dir = self._initialized_youtube_run()
        prepared = kernel.prepare_production_source_task(
            run_dir,
            task_stage="provider_acquisition",
            logical_task_key="source-provider-epoch-1",
            prepared_at="2026-07-18T04:01:00Z",
        )

        claim = kernel.claim_task(
            run_dir,
            prepared.task_id,
            coordinator_session_id="issue-7-coordinator",
            worker_id="issue-7-provider",
        )

        self.assertIsNotNone(claim.resource_admission)
        self.assertEqual(
            claim.resource_admission.required_resources,
            ("youtube_download",),
        )
        self.assertEqual(claim.resource_admission.queue_state, "admitted")

    def test_production_task_id_is_stable_within_epoch_and_changes_on_reopen(self) -> None:
        from video2pdf_workflow_kernel.task_execution import TaskExecution
        from video2pdf_workflow_kernel.utils import read_json

        kernel, run_dir = self._initialized_youtube_run()
        first = kernel.derive_production_source_task_id(
            run_dir,
            task_stage="semantic_judgment",
            logical_task_key="source-semantic-epoch-1",
        )
        replay = kernel.derive_production_source_task_id(
            run_dir,
            task_stage="semantic_judgment",
            logical_task_key="source-semantic-epoch-1",
        )
        record = read_json(run_dir / "workflow/run.json")
        reopened = dict(record)
        reopened["source_epoch"] = record["source_epoch"] + 1

        self.assertEqual(first, replay)
        self.assertNotEqual(
            first,
            TaskExecution._production_source_task_id(
                reopened,
                task_stage="semantic_judgment",
                logical_task_key="source-semantic-epoch-1",
            ),
        )

    def test_cookie_rejected_run_resolves_after_platform_breaker_closes(self) -> None:
        from video2pdf_workflow_kernel.adapters import PlatformAdapterError
        from video2pdf_workflow_kernel.errors import ContractError, KernelConflict
        from video2pdf_workflow_kernel.source_acquisition import (
            persist_source_blocker,
        )
        from video2pdf_workflow_kernel.utils import (
            canonical_json_bytes,
            read_json,
            sha256_file,
        )

        kernel, run_dir = self._initialized_youtube_run()
        with self.assertRaisesRegex(KernelConflict, "not blocked"):
            kernel.resolve_source_user_input(
                run_dir,
                authentication_classification="cookie_accepted",
                credential_evidence={},
                credential_evidence_sha256="a" * 64,
            )
        first_task_id = kernel.derive_production_source_task_id(
            run_dir,
            task_stage="provider_acquisition",
            logical_task_key="source-provider",
        )
        persist_source_blocker(
            kernel,
            run_dir,
            "youtube",
            PlatformAdapterError(
                "platform cookie was rejected",
                classification="source_authentication_required",
                exit_code=30,
                blocker_kind="user_input",
                data={"authentication_classification": "cookie_rejected"},
            ),
        )
        blocked = read_json(run_dir / "workflow/run.json")

        with self.assertRaisesRegex(KernelConflict, "breaker is still open"):
            kernel.resolve_source_user_input(
                run_dir,
                authentication_classification="cookie_accepted",
                credential_evidence={},
                credential_evidence_sha256=hashlib.sha256(
                    b"safe-cookie-validation-evidence"
                ).hexdigest(),
            )

        closed_breaker = kernel.set_resource_circuit_breaker(
            "youtube_download",
            state="closed",
            reason="localized cookie credential was refreshed and probed",
            platform="youtube",
        )
        evidence = self._credential_resolution_evidence(blocked, closed_breaker)
        evidence_sha = hashlib.sha256(canonical_json_bytes(evidence)).hexdigest()
        with self.assertRaisesRegex(ContractError, "SHA-256"):
            kernel.resolve_source_user_input(
                run_dir,
                authentication_classification="cookie_accepted",
                credential_evidence=evidence,
                credential_evidence_sha256="raw-cookie-material-is-forbidden",
            )
        leaked = self._credential_resolution_evidence(
            blocked,
            closed_breaker,
            probe_command_argv_redacted=[
                "<python-executable>",
                "-m",
                "yt_dlp",
                "--cookies",
                "C:/Users/operator/private-cookies.txt",
                "--simulate",
                "<canonical-source-url>",
            ],
        )
        with self.assertRaisesRegex(ContractError, "secret-free"):
            kernel.resolve_source_user_input(
                run_dir,
                authentication_classification="cookie_accepted",
                credential_evidence=leaked,
                credential_evidence_sha256=hashlib.sha256(
                    canonical_json_bytes(leaked)
                ).hexdigest(),
            )
        with self.assertRaisesRegex(ContractError, "fingerprint differs"):
            kernel.resolve_source_user_input(
                run_dir,
                authentication_classification="cookie_accepted",
                credential_evidence=evidence,
                credential_evidence_sha256="a" * 64,
            )
        stale_breaker_evidence = json.loads(json.dumps(evidence))
        stale_breaker_evidence["breaker_updated_seq"] -= 1
        with self.assertRaisesRegex(ContractError, "current blocked Run"):
            kernel.resolve_source_user_input(
                run_dir,
                authentication_classification="cookie_accepted",
                credential_evidence=stale_breaker_evidence,
                credential_evidence_sha256=hashlib.sha256(
                    canonical_json_bytes(stale_breaker_evidence)
                ).hexdigest(),
            )
        resolved = kernel.resolve_source_user_input(
            run_dir,
            authentication_classification="cookie_accepted",
            credential_evidence=evidence,
            credential_evidence_sha256=evidence_sha,
        )
        current = read_json(run_dir / "workflow/run.json")
        next_task = kernel.prepare_production_source_task(
            run_dir,
            task_stage="provider_acquisition",
            logical_task_key="source-provider",
            prepared_at="2026-07-18T05:01:00Z",
        )

        self.assertEqual(resolved["classification"], "source_user_input_resolved")
        self.assertEqual(resolved["source_epoch"], blocked["source_epoch"] + 1)
        self.assertEqual(current["source_state"], "pending")
        self.assertIsNone(current["source_blocker"])
        self.assertIsNone(current["source_version"])
        self.assertEqual(
            current["coordination_revision"],
            blocked["coordination_revision"] + 1,
        )
        self.assertNotEqual(next_task.task_id, first_task_id)
        self.assertEqual(resolved["credential_evidence_sha256"], evidence_sha)
        evidence_path = run_dir / resolved["credential_evidence_path"]
        self.assertEqual(read_json(evidence_path), evidence)
        self.assertEqual(sha256_file(evidence_path), evidence_sha)
        self.assertEqual(
            kernel.control_store.current_run_record_sha(current["run_id"]),
            sha256_file(run_dir / "workflow/run.json"),
        )

    def test_credential_resolution_reconcile_requires_bound_evidence(self) -> None:
        from video2pdf_workflow_kernel.utils import read_json, sha256_file

        kernel, run_dir, blocked, replacement, generation = (
            self._prepared_credential_resolution()
        )

        result = kernel.reconcile_run(run_dir)
        current = read_json(run_dir / "workflow/run.json")
        evidence_path = run_dir / generation["path"]

        self.assertEqual(result.outcome, "current_state_verified")
        self.assertEqual(current, replacement)
        self.assertEqual(current["source_state"], "pending")
        self.assertEqual(current["source_epoch"], blocked["source_epoch"] + 1)
        self.assertEqual(sha256_file(evidence_path), generation["sha256"])
        self.assertEqual(
            kernel.control_store.current_run_record_sha(current["run_id"]),
            sha256_file(run_dir / "workflow/run.json"),
        )

    def test_credential_resolution_recovery_rejects_missing_or_drifted_evidence(
        self,
    ) -> None:
        from video2pdf_workflow_kernel.errors import ArtifactDrift

        for failure in ("missing", "drifted"):
            with self.subTest(failure=failure):
                kernel, run_dir, blocked, _replacement, generation = (
                    self._prepared_credential_resolution()
                )
                evidence_path = run_dir / generation["path"]
                if failure == "missing":
                    preserved = evidence_path.with_name("missing-evidence.json")
                    evidence_path.replace(preserved)
                else:
                    evidence_path.write_bytes(
                        evidence_path.read_bytes() + b"drift"
                    )

                with self.assertRaises(ArtifactDrift):
                    kernel.reconcile_run(run_dir)

                mutation = kernel.control_store.prepared_run_state_mutation(
                    blocked["run_id"]
                )
                self.assertIsNotNone(mutation)
                self.assertEqual(mutation["state"], "PREPARED")

    def test_ready_source_reopens_and_republishes_changed_candidate_content(self) -> None:
        from video2pdf_workflow_kernel.kernel import VideoWorkflowKernel
        from video2pdf_workflow_kernel.source_acquisition import SourceReopenFault
        from video2pdf_workflow_kernel.utils import (
            read_json,
            sha256_file,
            write_json_atomic,
        )

        kernel, run_dir = self._initialized_youtube_run()

        def acquire(marker: str, published_at: str) -> tuple[dict, str]:
            provider = kernel.prepare_production_source_task(
                run_dir,
                task_stage="provider_acquisition",
                logical_task_key="source-provider",
                prepared_at=published_at,
            )
            provider_claim = kernel.claim_task(
                run_dir,
                provider.task_id,
                coordinator_session_id="issue-7-reopen-coordinator",
                worker_id=f"issue-7-provider-{marker}",
            )
            self._stage_provider_outputs(
                kernel,
                run_dir,
                provider_claim,
                content_marker=marker,
                include_english_subtitle=True,
            )
            self._release_admitted_launch(
                kernel,
                provider_claim,
                "youtube_download",
            )
            kernel.complete_task(
                run_dir,
                task_id=provider.task_id,
                attempt_id=provider_claim.attempt_id,
                claim_generation=provider_claim.claim_generation,
            )
            kernel.promote_task(
                run_dir,
                task_id=provider.task_id,
                attempt_id=provider_claim.attempt_id,
                claim_generation=provider_claim.claim_generation,
            )

            candidates_ready = read_json(run_dir / "workflow/run.json")
            inventory = read_json(
                run_dir / "work/source-acquisition/candidate-inventory.json"
            )
            subtitle = next(
                item for item in inventory["candidates"] if item["role"] == "subtitle"
            )
            semantic = kernel.prepare_production_source_task(
                run_dir,
                task_stage="semantic_judgment",
                logical_task_key="source-semantic-epoch-1",
                prepared_at=published_at,
            )
            semantic_claim = kernel.claim_task(
                run_dir,
                semantic.task_id,
                coordinator_session_id="issue-7-reopen-coordinator",
                worker_id=f"issue-7-semantic-{marker}",
            )
            output = semantic_claim.attempt_dir / "o/p.json"
            output.parent.mkdir(parents=True, exist_ok=True)
            write_json_atomic(
                output,
                {
                    "schema_name": "source-acquisition-judgment-patch",
                    "schema_version": "2.0.0",
                    "kernel_version": "2.0.0",
                    "task_id": semantic.task_id,
                    "attempt_id": semantic_claim.attempt_id,
                    "task_envelope_sha256": sha256_file(semantic.envelope_path),
                    "skeleton_sha256": candidates_ready[
                        "artifact_generations"
                    ]["source_acquisition_decision_skeleton"]["sha256"],
                    "judgment": {
                        "selected_subtitle_candidate_id": subtitle["candidate_id"],
                        "subtitle_selection_rationale": (
                            "The current English manual subtitle passed technical validation."
                        ),
                        "whisper_fallback": {
                            "choice": "not_required",
                            "rationale": (
                                "The preferred English manual subtitle is current."
                            ),
                        },
                        "known_gaps": [],
                    },
                },
            )
            self._release_admitted_launch(
                kernel,
                semantic_claim,
                "codex_semantic",
            )
            kernel.complete_task(
                run_dir,
                task_id=semantic.task_id,
                attempt_id=semantic_claim.attempt_id,
                claim_generation=semantic_claim.claim_generation,
            )
            kernel.promote_task(
                run_dir,
                task_id=semantic.task_id,
                attempt_id=semantic_claim.attempt_id,
                claim_generation=semantic_claim.claim_generation,
            )
            kernel.finalize_production_source(
                run_dir,
                published_at=published_at,
            )
            return read_json(run_dir / "workflow/run.json"), provider.task_id

        first_ready, first_provider_task_id = acquire(
            "first-generation",
            "2026-07-18T05:10:00Z",
        )
        first_inventory_sha = first_ready["artifact_generations"][
            "source_candidate_inventory"
        ]["sha256"]

        with self.assertRaisesRegex(SourceReopenFault, "after_reopen_prepared"):
            kernel.source_reopen(
                run_dir,
                reason="replace changed production candidate evidence",
                fault_point="after_reopen_prepared",
            )
        journal_path = next(
            (run_dir / "待删除/source-reopens").glob("*/reopen.json")
        )
        prepared = read_json(journal_path)
        preservations = {
            item["logical_id"]: item for item in prepared["preservations"]
        }
        self.assertEqual(
            set(preservations),
            {
                "source_package",
                "source_candidates",
                "source_candidate_inventory",
                "source_acquisition_decision_skeleton",
                "source_acquisition_decision",
            },
        )
        candidate_binding = preservations["source_candidates"]
        self.assertEqual(
            candidate_binding["expected_sha256"],
            first_inventory_sha,
        )
        partial = preservations["source_candidate_inventory"]
        partial_current = run_dir.joinpath(*Path(partial["current_path"]).parts)
        partial_preserved = run_dir.joinpath(
            *Path(partial["preservation_path"]).parts
        )
        partial_preserved.parent.mkdir(parents=True, exist_ok=True)
        os.replace(partial_current, partial_preserved)

        kernel = VideoWorkflowKernel(
            kernel.workspace_root,
            resource_provider_verifiers={
                "source-task-test": lambda **_identity: (
                    "provider-proof://source-task-test/succeeded"
                )
            },
        )
        kernel.reconcile_run(run_dir)
        reopened = read_json(run_dir / "workflow/run.json")
        committed = read_json(journal_path)

        self.assertEqual(committed["state"], "COMMITTED")
        self.assertEqual(reopened["source_epoch"], first_ready["source_epoch"] + 1)
        self.assertEqual(reopened["source_state"], "stale")
        for item in committed["preservations"]:
            preserved = run_dir.joinpath(*Path(item["preservation_path"]).parts)
            self.assertTrue(preserved.exists())
        second_ready, second_provider_task_id = acquire(
            "second-generation",
            "2026-07-18T05:20:00Z",
        )

        self.assertNotEqual(second_provider_task_id, first_provider_task_id)
        self.assertEqual(second_ready["source_state"], "ready")
        self.assertEqual(second_ready["source_epoch"], 2)
        self.assertNotEqual(
            second_ready["artifact_generations"]["source_candidate_inventory"][
                "sha256"
            ],
            first_inventory_sha,
        )
        self.assertNotEqual(second_ready["source_version"], first_ready["source_version"])
        self.assertEqual(
            kernel.control_store.current_run_record_sha(second_ready["run_id"]),
            sha256_file(run_dir / "workflow/run.json"),
        )

    def test_source_blocker_resolution_cli_closes_the_user_input_transition(self) -> None:
        from contextlib import redirect_stdout
        import io

        from video2pdf_workflow_kernel.adapters import PlatformAdapterError
        from video2pdf_workflow_kernel.cli import main
        from video2pdf_workflow_kernel.source_acquisition import (
            persist_source_blocker,
        )
        from video2pdf_workflow_kernel.utils import (
            canonical_json_bytes,
            read_json,
            write_json_atomic,
        )

        kernel, run_dir = self._initialized_youtube_run()
        persist_source_blocker(
            kernel,
            run_dir,
            "youtube",
            PlatformAdapterError(
                "platform cookie expired",
                classification="source_authentication_required",
                exit_code=30,
                blocker_kind="user_input",
                data={"authentication_classification": "cookie_expired"},
            ),
        )
        closed_breaker = kernel.set_resource_circuit_breaker(
            "youtube_download",
            state="closed",
            reason="replacement credential passed the provider probe",
            platform="youtube",
        )
        blocked = read_json(run_dir / "workflow/run.json")
        evidence = self._credential_resolution_evidence(blocked, closed_breaker)
        evidence_path = run_dir / "待删除/test-input/credential-evidence.json"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_sha = write_json_atomic(evidence_path, evidence)
        self.assertEqual(
            evidence_sha,
            hashlib.sha256(canonical_json_bytes(evidence)).hexdigest(),
        )
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exit_code = main(
                [
                    "source-blocker-resolve",
                    "--run-dir",
                    str(run_dir),
                    "--authentication-classification",
                    "cookie_accepted",
                    "--credential-evidence",
                    str(evidence_path),
                    "--credential-evidence-sha256",
                    evidence_sha,
                ]
            )
        result = json.loads(stdout.getvalue())
        record = read_json(run_dir / "workflow/run.json")

        self.assertEqual(exit_code, 0)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["classification"], "source_user_input_resolved")
        self.assertEqual(result["data"]["credential_evidence_sha256"], evidence_sha)
        self.assertEqual(
            read_json(run_dir / result["data"]["credential_evidence_path"]),
            evidence,
        )
        self.assertEqual(record["source_state"], "pending")
        self.assertEqual(record["source_epoch"], 2)

    def test_three_stage_source_tasks_complete_through_resource_admission(self) -> None:
        from video2pdf_workflow_kernel.utils import (
            read_json,
            sha256_file,
            write_json_atomic,
        )

        kernel, run_dir = self._initialized_youtube_run()
        provider = kernel.prepare_production_source_task(
            run_dir,
            task_stage="provider_acquisition",
            logical_task_key="source-provider-epoch-1",
            prepared_at="2026-07-18T04:01:00Z",
        )
        provider_claim = kernel.claim_task(
            run_dir,
            provider.task_id,
            coordinator_session_id="issue-7-coordinator",
            worker_id="issue-7-provider",
        )
        video = self._stage_provider_outputs(
            kernel, run_dir, provider_claim
        )
        self._release_admitted_launch(
            kernel, provider_claim, "youtube_download"
        )
        kernel.complete_task(
            run_dir,
            task_id=provider.task_id,
            attempt_id=provider_claim.attempt_id,
            claim_generation=provider_claim.claim_generation,
        )
        kernel.promote_task(
            run_dir,
            task_id=provider.task_id,
            attempt_id=provider_claim.attempt_id,
            claim_generation=provider_claim.claim_generation,
        )

        after_provider = read_json(run_dir / "workflow/run.json")
        self.assertEqual(after_provider["source_state"], "candidates_ready")
        self.assertEqual(
            {
                "source_candidate_inventory",
                "source_acquisition_decision_skeleton",
            },
            {
                item["logical_id"]
                for item in after_provider["checkpoints"][
                    "source_candidates_ready"
                ]["artifact_bindings"]
            },
        )

        semantic = kernel.prepare_production_source_task(
            run_dir,
            task_stage="semantic_judgment",
            logical_task_key="source-semantic-epoch-1",
            prepared_at="2026-07-18T04:11:00Z",
        )
        semantic_envelope = read_json(semantic.envelope_path)
        self.assertEqual(semantic.task_id, semantic_envelope["task_id"])
        self.assertEqual(
            semantic_envelope["resource_request"], ["codex_semantic"]
        )
        self.assertTrue(semantic.prompt_path.is_file())
        self.assertEqual(
            semantic_envelope["generated_prompt"]["role_template"]["version"],
            "2.0.0",
        )
        semantic_claim = kernel.claim_task(
            run_dir,
            semantic.task_id,
            coordinator_session_id="issue-7-coordinator",
            worker_id="issue-7-semantic",
        )
        semantic_output = semantic_claim.attempt_dir / "o/p.json"
        semantic_output.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(
            semantic_output,
            {
                "schema_name": "source-acquisition-judgment-patch",
                "schema_version": "2.0.0",
                "kernel_version": "2.0.0",
                "task_id": semantic.task_id,
                "attempt_id": semantic_claim.attempt_id,
                "task_envelope_sha256": sha256_file(semantic.envelope_path),
                "skeleton_sha256": after_provider["artifact_generations"][
                    "source_acquisition_decision_skeleton"
                ]["sha256"],
                "judgment": {
                    "selected_subtitle_candidate_id": None,
                    "subtitle_selection_rationale": (
                        "No technically usable English subtitle is available."
                    ),
                    "whisper_fallback": {
                        "choice": "use_whisper",
                        "rationale": (
                            "The admitted video candidate contains an audio stream."
                        ),
                    },
                    "known_gaps": [],
                },
            },
        )
        self._release_admitted_launch(kernel, semantic_claim, "codex_semantic")
        kernel.complete_task(
            run_dir,
            task_id=semantic.task_id,
            attempt_id=semantic_claim.attempt_id,
            claim_generation=semantic_claim.claim_generation,
        )
        kernel.promote_task(
            run_dir,
            task_id=semantic.task_id,
            attempt_id=semantic_claim.attempt_id,
            claim_generation=semantic_claim.claim_generation,
        )

        whisper_candidate = {
            "candidate_id": video["candidate_id"],
            "staged_path": video["staged_path"],
            "sha256": video["sha256"],
        }
        whisper = kernel.prepare_production_source_task(
            run_dir,
            task_stage="whisper_transcription",
            logical_task_key="source-whisper-epoch-1",
            prepared_at="2026-07-18T04:21:00Z",
            whisper_audio_candidate=whisper_candidate,
        )
        whisper_envelope = read_json(whisper.envelope_path)
        self.assertIsNone(whisper_envelope["generated_prompt"])
        self.assertEqual(whisper_envelope["resource_request"], ["whisper"])
        self.assertEqual(
            whisper_envelope["authority_binding"]["target_checkpoint"],
            "source_acquisition_decision_ready",
        )
        whisper_claim = kernel.claim_task(
            run_dir,
            whisper.task_id,
            coordinator_session_id="issue-7-coordinator",
            worker_id="issue-7-whisper",
        )
        transcript = whisper_claim.attempt_dir / "o/transcription.srt"
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_bytes(
            b"1\n00:00:00,000 --> 00:00:01,500\nRecorded speech.\n"
        )
        self._release_admitted_launch(kernel, whisper_claim, "whisper")
        kernel.complete_task(
            run_dir,
            task_id=whisper.task_id,
            attempt_id=whisper_claim.attempt_id,
            claim_generation=whisper_claim.claim_generation,
        )
        kernel.promote_task(
            run_dir,
            task_id=whisper.task_id,
            attempt_id=whisper_claim.attempt_id,
            claim_generation=whisper_claim.claim_generation,
        )

        final_record = read_json(run_dir / "workflow/run.json")
        self.assertEqual(final_record["source_state"], "decision_ready")
        self.assertEqual(final_record["coordination_revision"], 4)
        self.assertIn("source_transcription", final_record["artifact_generations"])
        self.assertEqual(
            transcript.read_bytes(),
            (run_dir / "work/source-acquisition/transcription.srt").read_bytes(),
        )
        kernel.reconcile_run(run_dir)
        replay = kernel.promote_task(
            run_dir,
            task_id=whisper.task_id,
            attempt_id=whisper_claim.attempt_id,
            claim_generation=whisper_claim.claim_generation,
        )
        self.assertEqual(replay.task_id, whisper.task_id)

    def test_whisper_output_uses_a_strict_utf8_lf_srt_byte_contract(self) -> None:
        from video2pdf_workflow_kernel.errors import ContractError
        from video2pdf_workflow_kernel.task_execution import TaskExecution

        TaskExecution._validate_srt_bytes(
            b"1\n00:00:00,000 --> 00:00:01,500\nValid cue.\n"
        )
        invalid_payloads = (
            b"1\r\n00:00:00,000 --> 00:00:01,500\r\nCRLF cue.\r\n",
            b"1\n00:00:01,500 --> 00:00:00,000\nReversed cue.\n",
            b"2\n00:00:00,000 --> 00:00:01,500\nWrong index.\n",
            b"\xff\n",
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload), self.assertRaises(ContractError):
                TaskExecution._validate_srt_bytes(payload)

    def test_provider_inventory_authenticates_the_exact_candidate_staging_set(self) -> None:
        from video2pdf_workflow_kernel.errors import ContractError

        kernel, run_dir = self._initialized_youtube_run()
        prepared = kernel.prepare_production_source_task(
            run_dir,
            task_stage="provider_acquisition",
            logical_task_key="source-provider-exact-staging",
            prepared_at="2026-07-18T04:01:00Z",
        )
        claim = kernel.claim_task(
            run_dir,
            prepared.task_id,
            coordinator_session_id="issue-7-coordinator",
            worker_id="issue-7-provider",
        )
        self._stage_provider_outputs(kernel, run_dir, claim)
        undeclared = claim.attempt_dir / "o/candidates/subtitles/undeclared.srt"
        undeclared.parent.mkdir(parents=True, exist_ok=True)
        undeclared.write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nUndeclared.\n",
            encoding="utf-8",
        )
        self._release_admitted_launch(kernel, claim, "youtube_download")

        with self.assertRaises(ContractError):
            kernel.complete_task(
                run_dir,
                task_id=prepared.task_id,
                attempt_id=claim.attempt_id,
                claim_generation=claim.claim_generation,
            )

    def test_v3_completion_requires_confirmed_resource_launch_authority(self) -> None:
        from video2pdf_workflow_kernel.errors import ResourceAdmissionBlocked

        kernel, run_dir = self._initialized_youtube_run()
        prepared = kernel.prepare_production_source_task(
            run_dir,
            task_stage="provider_acquisition",
            logical_task_key="source-provider-unlaunched",
            prepared_at="2026-07-18T04:01:00Z",
        )
        claim = kernel.claim_task(
            run_dir,
            prepared.task_id,
            coordinator_session_id="issue-7-coordinator",
            worker_id="issue-7-provider",
        )
        self._stage_provider_outputs(kernel, run_dir, claim)

        with self.assertRaises(ResourceAdmissionBlocked):
            kernel.complete_task(
                run_dir,
                task_id=prepared.task_id,
                attempt_id=claim.attempt_id,
                claim_generation=claim.claim_generation,
            )


if __name__ == "__main__":
    unittest.main()
