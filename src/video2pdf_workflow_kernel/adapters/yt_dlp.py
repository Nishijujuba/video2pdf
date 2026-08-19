from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import shutil
import stat
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

from ..utils import sha256_file
from .base import (
    CommandEvidence,
    CommandResult,
    CommandRunner,
    CommandSpec,
    PlatformAcquireRequest,
    PlatformAcquisition,
    PlatformAdapterError,
    PlatformProbe,
    PlatformProbeRequest,
    SecretArgument,
    StagedArtifact,
    SubtitleTrack,
)


@dataclass(frozen=True)
class YtDlpRuntime:
    python_executable: Path
    ffmpeg_dir: Path
    ffprobe_executable: Path | None = None
    socket_timeout_seconds: int = 30
    retries: int = 3
    command_timeout_seconds: int = 1800

    @property
    def ffprobe(self) -> Path:
        if self.ffprobe_executable is not None:
            return self.ffprobe_executable
        executable = "ffprobe.exe" if self.ffmpeg_dir.suffix == "" else "ffprobe"
        return self.ffmpeg_dir / executable


def _safe_language(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    normalized = re.sub(r"[^a-z0-9-]+", "-", normalized)
    normalized = re.sub(r"-+", "-", normalized).strip("-")
    if normalized:
        return normalized
    return f"und-{hashlib.sha256(value.encode('utf-8')).hexdigest()[:8]}"


def _track_id(origin: str, provider_language: str) -> str:
    digest = hashlib.sha256(
        f"{origin}\0{provider_language}".encode("utf-8")
    ).hexdigest()[:8]
    return f"{origin}:{_safe_language(provider_language)}:{digest}"


def _is_reparse_point(path: Path) -> bool:
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _regular_contained_file(path: Path, root: Path, *, purpose: str) -> Path:
    resolved_root = root.resolve(strict=True)
    resolved = path.resolve(strict=True)
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise PlatformAdapterError(
            f"{purpose} output escapes provider staging",
            classification="source_provider_output_invalid",
            exit_code=70,
            data={"purpose": purpose},
        ) from exc
    if not path.is_file() or path.is_symlink() or _is_reparse_point(path):
        raise PlatformAdapterError(
            f"{purpose} output is not a regular file",
            classification="source_provider_output_invalid",
            exit_code=70,
            data={"purpose": purpose},
        )
    return resolved


def _copy_canonical(source: Path, target: Path, *, root: Path, purpose: str) -> Path:
    _regular_contained_file(source, root, purpose=purpose)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if not target.is_file() or sha256_file(target) != sha256_file(source):
            raise PlatformAdapterError(
                f"canonical {purpose} output already exists with different content",
                classification="source_artifact_drift",
                exit_code=40,
                data={"purpose": purpose},
            )
        return target
    shutil.copy2(source, target)
    return target


def _artifact(logical_id: str, path: Path, media_type: str) -> StagedArtifact:
    return StagedArtifact(
        logical_id=logical_id,
        path=path,
        media_type=media_type,
        sha256=sha256_file(path),
        size_bytes=path.stat().st_size,
    )


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    if path.exists():
        if path.read_text(encoding="utf-8") != payload:
            raise PlatformAdapterError(
                "normalized provider metadata changed within one staging attempt",
                classification="source_artifact_drift",
                exit_code=40,
            )
        return
    path.write_text(payload, encoding="utf-8")


def _parse_json(result: CommandResult, *, operation: str) -> dict[str, Any]:
    try:
        value = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PlatformAdapterError(
            f"{operation} returned invalid JSON",
            classification="source_provider_output_invalid",
            exit_code=70,
            data={"operation": operation},
        ) from exc
    if not isinstance(value, dict):
        raise PlatformAdapterError(
            f"{operation} returned a non-object JSON value",
            classification="source_provider_output_invalid",
            exit_code=70,
            data={"operation": operation},
        )
    return value


_AUTH_EXPIRED_MARKERS = ("cookie expired", "cookies have expired", "cookie 已过期", "cookie过期")
_AUTH_REJECTED_MARKERS = (
    "sign in to confirm",
    "login required",
    "authentication required",
    "cookie rejected",
    "please log in",
    "请先登录",
    "账号未登录",
)
_NETWORK_MARKERS = (
    "timed out",
    "timeout",
    "temporary failure in name resolution",
    "name or service not known",
    "connection reset",
    "connection refused",
    "ssl eof",
    "tls",
)
_RUNTIME_MARKERS = (
    "javascript runtime",
    "js runtime",
    "ffmpeg not found",
    "ffprobe not found",
    "no such file or directory",
)
_UNAVAILABLE_MARKERS = (
    "private video",
    "video unavailable",
    "has been removed",
    "not available in your country",
    "members-only",
)


def _raise_provider_failure(
    result: CommandResult, *, adapter_id: str, operation: str
) -> None:
    stderr = result.stderr.decode("utf-8", errors="replace")
    lowered = stderr.lower()
    safe_data: dict[str, Any] = {
        "adapter_id": adapter_id,
        "operation": operation,
        "provider_exit_code": result.returncode,
        "command_argv_sha256": result.evidence.argv_sha256,
        "sanitized_stderr_tail": stderr[-2000:],
    }
    if any(marker in lowered for marker in _AUTH_EXPIRED_MARKERS):
        safe_data["authentication_classification"] = "cookie_expired"
        raise PlatformAdapterError(
            "platform cookie expired",
            classification="source_authentication_required",
            exit_code=30,
            blocker_kind="user_input",
            data=safe_data,
        )
    if any(marker in lowered for marker in _AUTH_REJECTED_MARKERS):
        safe_data["authentication_classification"] = "cookie_rejected"
        raise PlatformAdapterError(
            "platform cookie was rejected",
            classification="source_authentication_required",
            exit_code=30,
            blocker_kind="user_input",
            data=safe_data,
        )
    if any(marker in lowered for marker in _UNAVAILABLE_MARKERS):
        raise PlatformAdapterError(
            "platform source is unavailable",
            classification="source_unavailable",
            exit_code=30,
            blocker_kind="source_terminal",
            data=safe_data,
        )
    if any(marker in lowered for marker in _RUNTIME_MARKERS):
        raise PlatformAdapterError(
            "platform runtime dependency is unavailable",
            classification="source_runtime_dependency_unavailable",
            exit_code=70,
            data=safe_data,
        )
    if any(marker in lowered for marker in _NETWORK_MARKERS):
        raise PlatformAdapterError(
            "platform network request failed",
            classification="source_network_unavailable",
            exit_code=70,
            retryable=True,
            data=safe_data,
        )
    raise PlatformAdapterError(
        "platform provider command failed",
        classification="source_provider_command_failed",
        exit_code=70,
        data=safe_data,
    )


def _require_success(
    result: CommandResult, *, adapter_id: str, operation: str
) -> CommandResult:
    if result.returncode != 0:
        _raise_provider_failure(result, adapter_id=adapter_id, operation=operation)
    return result


def _validate_cookie(path: Path, *, adapter_id: str) -> None:
    if not path.is_file():
        raise PlatformAdapterError(
            "localized platform cookie is missing",
            classification="source_authentication_required",
            exit_code=30,
            blocker_kind="user_input",
            data={
                "adapter_id": adapter_id,
                "authentication_classification": "cookie_missing",
            },
        )
    try:
        text = path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeError) as exc:
        raise PlatformAdapterError(
            "localized platform cookie is unreadable",
            classification="source_authentication_required",
            exit_code=30,
            blocker_kind="user_input",
            data={
                "adapter_id": adapter_id,
                "authentication_classification": "cookie_unreadable",
            },
        ) from None
    if not text.startswith("# Netscape HTTP Cookie File"):
        raise PlatformAdapterError(
            "localized platform cookie has an unsupported format",
            classification="source_authentication_required",
            exit_code=30,
            blocker_kind="user_input",
            data={
                "adapter_id": adapter_id,
                "authentication_classification": "cookie_unreadable",
            },
        )


def _safe_formats(metadata: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    allowed = (
        "format_id",
        "ext",
        "width",
        "height",
        "fps",
        "vcodec",
        "acodec",
        "protocol",
        "filesize",
        "filesize_approx",
        "format_note",
    )
    formats: list[dict[str, Any]] = []
    for item in metadata.get("formats") or []:
        if not isinstance(item, dict):
            continue
        projected = {key: item[key] for key in allowed if item.get(key) is not None}
        if projected:
            formats.append(projected)
    return tuple(formats)


def _subtitle_tracks(metadata: dict[str, Any]) -> tuple[SubtitleTrack, ...]:
    tracks: list[SubtitleTrack] = []
    for origin, key in (("manual", "subtitles"), ("automatic", "automatic_captions")):
        inventory = metadata.get(key) or {}
        if not isinstance(inventory, dict):
            continue
        for language, candidates in inventory.items():
            if not isinstance(language, str) or not isinstance(candidates, list):
                continue
            formats = tuple(
                sorted(
                    {
                        str(candidate["ext"])
                        for candidate in candidates
                        if isinstance(candidate, dict) and candidate.get("ext")
                    }
                )
            )
            tracks.append(
                SubtitleTrack(
                    track_id=_track_id(origin, language),
                    provider_language=language,
                    normalized_language=_safe_language(language),
                    origin=origin,  # type: ignore[arg-type]
                    formats=formats,
                )
            )
    return tuple(
        sorted(
            tracks,
            key=lambda item: (
                0 if item.origin == "manual" else 1,
                item.normalized_language,
                item.provider_language,
            ),
        )
    )


def _subtitle_output_candidates(
    output_root: Path, provider_language: str
) -> tuple[Path, ...]:
    """Resolve native and translated yt-dlp subtitle filename conventions."""

    exact_name = f"candidate.{provider_language}.srt"
    translated_suffix = f".{provider_language}.srt"
    return tuple(
        sorted(
            path
            for path in output_root.iterdir()
            if path.name == exact_name
            or (
                path.name.startswith("candidate.")
                and path.name.endswith(translated_suffix)
                and path.name != exact_name
            )
        )
    )


class YtDlpPlatformAdapter:
    adapter_contract_version = "1.0.0"
    adapter_id = "yt-dlp"
    canonical_platform = "unknown"
    download_resource_class = "unknown_download"
    _platform_yt_dlp_flags: tuple[str, ...] = ()

    def __init__(self, runtime: YtDlpRuntime) -> None:
        self.runtime = runtime

    def _base_argv(self, cookie_file: Path) -> tuple[str | SecretArgument, ...]:
        return (
            str(self.runtime.python_executable),
            "-X",
            "utf8",
            "-B",
            "-m",
            "yt_dlp",
            "--ignore-config",
            "--no-cache-dir",
            "--no-part",
            "--no-playlist",
            "--no-progress",
            "--socket-timeout",
            str(self.runtime.socket_timeout_seconds),
            "--retries",
            str(self.runtime.retries),
            "--fragment-retries",
            str(self.runtime.retries),
            "--ffmpeg-location",
            str(self.runtime.ffmpeg_dir),
            "--cookies",
            SecretArgument(str(cookie_file)),
            *self._platform_yt_dlp_flags,
        )

    def _command(
        self,
        operation: str,
        cookie_file: Path,
        cwd: Path,
        arguments: Iterable[str],
    ) -> CommandSpec:
        cwd.mkdir(parents=True, exist_ok=True)
        return CommandSpec(
            operation=operation,
            argv=(*self._base_argv(cookie_file), *tuple(arguments)),
            cwd=cwd,
            allowed_output_root=cwd,
            timeout_seconds=self.runtime.command_timeout_seconds,
        )

    def _canonical_item_id(
        self, metadata: dict[str, Any], request: PlatformProbeRequest
    ) -> str:
        raise NotImplementedError

    def _canonical_url(
        self, metadata: dict[str, Any], request: PlatformProbeRequest
    ) -> str:
        raw = str(metadata.get("webpage_url") or request.source_url)
        parsed = urlsplit(raw)
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))

    def _validate_item_selection(
        self, metadata: dict[str, Any], request: PlatformProbeRequest
    ) -> None:
        return None

    def _provider_probe_url(self, request: PlatformProbeRequest) -> str:
        return request.source_url

    def _validated_acquisition_url(self, request: PlatformAcquireRequest) -> str:
        raise NotImplementedError

    def probe(
        self, request: PlatformProbeRequest, *, runner: CommandRunner
    ) -> PlatformProbe:
        assert_adapter_binding = getattr(runner, "assert_adapter_binding", None)
        if assert_adapter_binding is not None:
            assert_adapter_binding(
                canonical_platform=self.canonical_platform,
                adapter_id=self.adapter_id,
                adapter_contract_version=self.adapter_contract_version,
            )
        _validate_cookie(request.localized_cookie_file, adapter_id=self.adapter_id)
        provider_probe_url = self._provider_probe_url(request)
        staging_root = request.staging_root.resolve(strict=False)
        staging_root.mkdir(parents=True, exist_ok=True)
        probe_root = staging_root / "provider" / "probe"
        metadata_root = staging_root / "provider" / "metadata"
        evidence: list[CommandEvidence] = []

        subtitle_result = runner.run(
            self._command(
                "subtitle_list",
                request.localized_cookie_file,
                probe_root,
                ("--skip-download", "--list-subs", "--", provider_probe_url),
            )
        )
        evidence.append(subtitle_result.evidence)
        _require_success(
            subtitle_result, adapter_id=self.adapter_id, operation="subtitle_list"
        )

        metadata_result = runner.run(
            self._command(
                "metadata_probe",
                request.localized_cookie_file,
                metadata_root,
                (
                    "--skip-download",
                    "--dump-single-json",
                    "--",
                    provider_probe_url,
                ),
            )
        )
        evidence.append(metadata_result.evidence)
        _require_success(
            metadata_result, adapter_id=self.adapter_id, operation="metadata_probe"
        )
        metadata = _parse_json(metadata_result, operation="metadata_probe")
        self._validate_item_selection(metadata, request)
        canonical_item_id = self._canonical_item_id(metadata, request)
        canonical_url = self._canonical_url(metadata, request)
        tracks = _subtitle_tracks(metadata)
        formats = _safe_formats(metadata)
        title = metadata.get("title")
        if not isinstance(title, str) or not title.strip():
            raise PlatformAdapterError(
                "platform metadata omitted the original title",
                classification="source_provider_output_invalid",
                exit_code=70,
                data={"adapter_id": self.adapter_id},
            )
        try:
            duration = float(metadata["duration"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PlatformAdapterError(
                "platform metadata omitted a valid duration",
                classification="source_provider_output_invalid",
                exit_code=70,
                data={"adapter_id": self.adapter_id},
            ) from exc
        platform_revision = {
            key: metadata.get(key)
            for key in ("upload_date", "timestamp", "release_timestamp")
            if metadata.get(key) is not None
        }
        normalized_path = staging_root / "canonical" / "metadata" / "platform.json"
        _write_json(
            normalized_path,
            {
                "adapter_id": self.adapter_id,
                "canonical_platform": self.canonical_platform,
                "canonical_item_id": canonical_item_id,
                "canonical_url": canonical_url,
                "original_title": title,
                "duration_seconds": duration,
                "platform_revision": platform_revision,
                "subtitle_tracks": [
                    {
                        "track_id": track.track_id,
                        "provider_language": track.provider_language,
                        "normalized_language": track.normalized_language,
                        "origin": track.origin,
                        "formats": list(track.formats),
                    }
                    for track in tracks
                ],
                "formats": list(formats),
            },
        )
        return PlatformProbe(
            adapter_id=self.adapter_id,
            canonical_platform=self.canonical_platform,  # type: ignore[arg-type]
            canonical_item_id=canonical_item_id,
            canonical_url=canonical_url,
            original_title=title,
            duration_seconds=duration,
            platform_revision=platform_revision,
            subtitle_tracks=tracks,
            media_formats=formats,
            normalized_metadata_path=normalized_path,
            authentication_classification="cookie_accepted",
            command_evidence=tuple(evidence),
        )

    def _subtitle_arguments(
        self, origin: str, languages: tuple[str, ...], source_url: str
    ) -> tuple[str, ...]:
        write_flags = (
            ("--write-subs",)
            if origin == "manual"
            else ("--write-auto-subs",)
        )
        return (
            "--skip-download",
            *write_flags,
            "--sub-langs",
            ",".join(languages),
            "--sub-format",
            "srt/vtt/best",
            "--convert-subs",
            "srt",
            "--paths",
            "home:.",
            "--output",
            "candidate.%(language)s.%(ext)s",
            "--",
            source_url,
        )

    def acquire(
        self, request: PlatformAcquireRequest, *, runner: CommandRunner
    ) -> PlatformAcquisition:
        assert_adapter_binding = getattr(runner, "assert_adapter_binding", None)
        if assert_adapter_binding is not None:
            assert_adapter_binding(
                canonical_platform=self.canonical_platform,
                adapter_id=self.adapter_id,
                adapter_contract_version=self.adapter_contract_version,
            )
        if (
            request.probe.adapter_id != self.adapter_id
            or request.probe.canonical_platform != self.canonical_platform
        ):
            raise PlatformAdapterError(
                "acquisition probe belongs to a different Platform Adapter",
                classification="contract_invalid",
                exit_code=20,
            )
        acquisition_url = self._validated_acquisition_url(request)
        available = {track.track_id: track for track in request.probe.subtitle_tracks}
        if (
            len(request.eligible_track_ids) != len(set(request.eligible_track_ids))
            or any(track_id not in available for track_id in request.eligible_track_ids)
        ):
            raise PlatformAdapterError(
                "acquisition names an unknown or duplicate subtitle track",
                classification="contract_invalid",
                exit_code=20,
            )
        _validate_cookie(request.localized_cookie_file, adapter_id=self.adapter_id)
        staging_root = request.staging_root.resolve(strict=False)
        canonical_root = staging_root / "canonical"
        evidence: list[CommandEvidence] = []
        subtitle_artifacts: list[StagedArtifact] = []

        selected_tracks = tuple(available[value] for value in request.eligible_track_ids)
        for origin in ("manual", "automatic"):
            tracks = tuple(track for track in selected_tracks if track.origin == origin)
            if not tracks:
                continue
            output_root = staging_root / "provider" / "subtitles" / origin
            languages = tuple(track.provider_language for track in tracks)
            result = runner.run(
                self._command(
                    f"subtitle_{origin}",
                    request.localized_cookie_file,
                    output_root,
                    self._subtitle_arguments(origin, languages, acquisition_url),
                )
            )
            evidence.append(result.evidence)
            _require_success(
                result,
                adapter_id=self.adapter_id,
                operation=f"subtitle_{origin}",
            )
            for track in tracks:
                candidates = _subtitle_output_candidates(
                    output_root, track.provider_language
                )
                if len(candidates) != 1:
                    raise PlatformAdapterError(
                        "subtitle provider output is missing or ambiguous",
                        classification="source_provider_output_invalid",
                        exit_code=70,
                        data={"track_id": track.track_id},
                    )
                target = canonical_root / "subtitles" / (
                    f"subtitle.{track.normalized_language}.{track.origin}.srt"
                )
                _copy_canonical(
                    candidates[0], target, root=output_root, purpose="subtitle"
                )
                subtitle_artifacts.append(
                    _artifact(track.track_id, target, "application/x-subrip")
                )

        cover_root = staging_root / "provider" / "cover"
        cover_result = runner.run(
            self._command(
                "thumbnail_download",
                request.localized_cookie_file,
                cover_root,
                (
                    "--skip-download",
                    "--write-thumbnail",
                    "--convert-thumbnails",
                    "jpg",
                    "--paths",
                    "home:.",
                    "--output",
                    "cover.%(ext)s",
                    "--",
                    acquisition_url,
                ),
            )
        )
        evidence.append(cover_result.evidence)
        _require_success(
            cover_result, adapter_id=self.adapter_id, operation="thumbnail_download"
        )
        covers = sorted(cover_root.glob("cover.jpg"))
        if len(covers) != 1:
            raise PlatformAdapterError(
                "thumbnail provider output is missing or ambiguous",
                classification="source_provider_output_invalid",
                exit_code=70,
            )
        canonical_cover = _copy_canonical(
            covers[0],
            canonical_root / "cover" / "cover.jpg",
            root=cover_root,
            purpose="cover",
        )

        media_root = staging_root / "provider" / "media"
        height = request.max_video_height
        if height < 144 or height > 4320:
            raise PlatformAdapterError(
                "maximum video height is outside the supported range",
                classification="contract_invalid",
                exit_code=20,
            )
        media_result = runner.run(
            self._command(
                "media_download",
                request.localized_cookie_file,
                media_root,
                (
                    "--format",
                    f"bestvideo[height<={height}]+bestaudio/best[height<={height}]/best",
                    "--merge-output-format",
                    "mp4",
                    "--paths",
                    "home:.",
                    "--output",
                    "video.%(ext)s",
                    "--",
                    acquisition_url,
                ),
            )
        )
        evidence.append(media_result.evidence)
        _require_success(
            media_result, adapter_id=self.adapter_id, operation="media_download"
        )
        media_candidates = sorted(
            path
            for extension in ("mp4", "mkv", "webm")
            for path in media_root.glob(f"video.{extension}")
        )
        if len(media_candidates) != 1:
            raise PlatformAdapterError(
                "media provider output is missing or ambiguous",
                classification="source_provider_output_invalid",
                exit_code=70,
            )
        extension = media_candidates[0].suffix.lower()
        canonical_video = _copy_canonical(
            media_candidates[0],
            canonical_root / "media" / f"video{extension}",
            root=media_root,
            purpose="media",
        )

        probe_spec = CommandSpec(
            operation="media_probe",
            argv=(
                str(self.runtime.ffprobe),
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                "-show_chapters",
                canonical_video.name,
            ),
            cwd=canonical_video.parent,
            allowed_output_root=canonical_video.parent,
            timeout_seconds=120,
        )
        probe_result = runner.run(probe_spec)
        evidence.append(probe_result.evidence)
        _require_success(
            probe_result, adapter_id=self.adapter_id, operation="media_probe"
        )
        raw_probe = _parse_json(probe_result, operation="media_probe")
        normalized_probe = {
            "format": {
                key: raw_probe.get("format", {}).get(key)
                for key in ("format_name", "duration", "size", "bit_rate")
                if isinstance(raw_probe.get("format"), dict)
                and raw_probe["format"].get(key) is not None
            },
            "streams": [
                {
                    key: stream[key]
                    for key in (
                        "index",
                        "codec_type",
                        "codec_name",
                        "width",
                        "height",
                        "sample_rate",
                        "channels",
                        "duration",
                    )
                    if stream.get(key) is not None
                }
                for stream in raw_probe.get("streams", [])
                if isinstance(stream, dict)
            ],
        }
        if not any(
            stream.get("codec_type") == "video"
            for stream in normalized_probe["streams"]
        ) or not any(
            stream.get("codec_type") == "audio"
            for stream in normalized_probe["streams"]
        ):
            raise PlatformAdapterError(
                "media probe did not find both video and audio streams",
                classification="source_provider_output_invalid",
                exit_code=70,
            )
        media_probe_path = canonical_root / "metadata" / "media-probe.json"
        _write_json(media_probe_path, normalized_probe)

        media_type = {
            ".mp4": "video/mp4",
            ".mkv": "video/x-matroska",
            ".webm": "video/webm",
        }[extension]
        return PlatformAcquisition(
            probe=request.probe,
            subtitle_candidates=tuple(subtitle_artifacts),
            video=_artifact("video", canonical_video, media_type),
            cover=_artifact("cover", canonical_cover, "image/jpeg"),
            media_probe=_artifact(
                "media_probe", media_probe_path, "application/json"
            ),
            command_evidence=tuple(evidence),
        )
