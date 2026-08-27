from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
import uuid
from typing import Any, Literal

from .adapters import (
    BilibiliPlatformAdapter,
    PlatformProbeRequest,
    RecordedCommandRunner,
    SubprocessCommandRunner,
    YouTubePlatformAdapter,
    YtDlpRuntime,
)
from .errors import ContractError, KernelConflict
from .models import DeterministicLocatorRequest
from .utils import canonical_json_bytes, require_contained_path


def _is_reparse_point(path: Path) -> bool:
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _credential_identity(
    *,
    source_url: str,
    explicit_item_selector: str | None,
    task_start: str,
    request_id: str,
) -> str:
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "explicit_item_selector": explicit_item_selector,
                "request_id": request_id,
                "source_url": source_url,
                "task_start": task_start,
            }
        )
    ).hexdigest()[:24]


def _localize_cookie(
    source: Path,
    *,
    disposable_root: Path,
    credential_identity: str,
) -> Path:
    try:
        if (
            not source.is_file()
            or source.is_symlink()
            or _is_reparse_point(source)
        ):
            raise ContractError("production Bootstrap cookie is not a regular file")
        payload = source.read_bytes()
    except ContractError:
        raise
    except (OSError, ValueError) as exc:
        raise ContractError("production Bootstrap cookie is unreadable") from exc
    if not payload:
        raise ContractError("production Bootstrap cookie is empty")

    credential_root = (
        disposable_root / "bootstrap" / "credentials" / credential_identity
    )
    require_contained_path(
        credential_root,
        disposable_root.parent,
        purpose="production Bootstrap credential directory",
        error_type=ContractError,
        allow_missing=True,
    )
    credential_root.mkdir(parents=True, exist_ok=True)
    require_contained_path(
        credential_root,
        disposable_root.parent,
        purpose="production Bootstrap credential directory",
        error_type=ContractError,
        leaf_kind="directory",
    )
    target = credential_root / "cookies.txt"
    require_contained_path(
        target,
        disposable_root.parent,
        purpose="production Bootstrap localized cookie",
        error_type=ContractError,
        allow_missing=True,
    )
    if target.exists():
        try:
            if (
                not target.is_file()
                or target.is_symlink()
                or _is_reparse_point(target)
                or target.read_bytes() != payload
            ):
                raise KernelConflict(
                    "production Bootstrap credential identity changed on replay"
                )
        except KernelConflict:
            raise
        except OSError as exc:
            raise ContractError(
                "production Bootstrap localized cookie is unreadable"
            ) from exc
        return target

    temporary = credential_root / f".cookies.{uuid.uuid4().hex}.kernel-new"
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except OSError as exc:
        raise ContractError("production Bootstrap cookie localization failed") from exc
    return require_contained_path(
        target,
        disposable_root.parent,
        purpose="production Bootstrap localized cookie",
        error_type=ContractError,
        leaf_kind="file",
        require_single_link=True,
    )


def bootstrap_bilibili_production_probe(
    *,
    kernel: Any,
    workspace_root: Path,
    source_url: str | None,
    cookie_file: Path | None,
    original_title: str | None,
    task_start: str,
    request_id: str,
    explicit_item_selector: str | None,
    provider_recording: Path | None,
    provider_mode: Literal["deterministic", "recorded", "live"] | None = None,
) -> Any:
    return _bootstrap_platform_production_probe(
        platform="bilibili",
        kernel=kernel,
        workspace_root=workspace_root,
        source_url=source_url,
        cookie_file=cookie_file,
        original_title=original_title,
        task_start=task_start,
        request_id=request_id,
        explicit_item_selector=explicit_item_selector,
        provider_recording=provider_recording,
        provider_mode=provider_mode,
    )


def bootstrap_youtube_production_probe(
    *,
    kernel: Any,
    workspace_root: Path,
    source_url: str | None,
    cookie_file: Path | None,
    original_title: str | None,
    task_start: str,
    request_id: str,
    explicit_item_selector: str | None,
    provider_recording: Path | None,
    provider_mode: Literal["deterministic", "recorded", "live"] | None = None,
) -> Any:
    return _bootstrap_platform_production_probe(
        platform="youtube",
        kernel=kernel,
        workspace_root=workspace_root,
        source_url=source_url,
        cookie_file=cookie_file,
        original_title=original_title,
        task_start=task_start,
        request_id=request_id,
        explicit_item_selector=explicit_item_selector,
        provider_recording=provider_recording,
        provider_mode=provider_mode,
    )


def _bootstrap_platform_production_probe(
    *,
    platform: str,
    kernel: Any,
    workspace_root: Path,
    source_url: str | None,
    cookie_file: Path | None,
    original_title: str | None,
    task_start: str,
    request_id: str,
    explicit_item_selector: str | None,
    provider_recording: Path | None,
    provider_mode: Literal["deterministic", "recorded", "live"] | None,
) -> Any:
    if not source_url:
        raise ContractError("production Bootstrap requires --source-url")

    runtime = YtDlpRuntime(
        python_executable=Path("python"),
        ffmpeg_dir=Path("ffmpeg-bin"),
        ffprobe_executable=Path("ffprobe"),
    )
    if platform == "youtube":
        adapter = YouTubePlatformAdapter(runtime)
    else:
        adapter = BilibiliPlatformAdapter(runtime)
    selected_mode = provider_mode or (
        "recorded" if provider_recording is not None else "deterministic"
    )
    if selected_mode == "deterministic":
        if provider_recording is not None:
            raise ContractError(
                "deterministic Bootstrap cannot accept a provider recording"
            )
        if cookie_file is not None:
            raise ContractError(
                "deterministic Bootstrap cannot accept --cookie-file"
            )
        if original_title is None or not original_title.strip():
            raise ContractError(
                "deterministic Bootstrap requires --original-title"
            )
        request = DeterministicLocatorRequest(
            source_url=source_url,
            original_title=original_title,
            explicit_item_selector=explicit_item_selector,
        )
        return kernel.bootstrap_production_source(
            adapter=adapter,
            request=request,
            runner=None,
            task_start=task_start,
            request_id=request_id,
            provider_kind="deterministic_locator",
        )

    if selected_mode == "recorded" and provider_recording is None:
        raise ContractError("recorded Bootstrap requires a provider recording")
    if selected_mode == "live" and provider_recording is not None:
        raise ContractError("live Bootstrap cannot accept a provider recording")
    if cookie_file is None:
        raise ContractError("provider Bootstrap requires a credential reference")
    if original_title is not None:
        raise ContractError(
            "provider Bootstrap derives title from provider metadata"
        )

    disposable_root = workspace_root.resolve().parent / "待删除"
    identity = _credential_identity(
        source_url=source_url,
        explicit_item_selector=explicit_item_selector,
        task_start=task_start,
        request_id=request_id,
    )
    localized_cookie = _localize_cookie(
        cookie_file,
        disposable_root=disposable_root,
        credential_identity=identity,
    )
    staging_root = disposable_root / "bootstrap" / "provider-attempts" / identity

    runner = (
        SubprocessCommandRunner()
        if selected_mode == "live"
        else RecordedCommandRunner(provider_recording)
    )
    request = PlatformProbeRequest(
        source_url=source_url,
        localized_cookie_file=localized_cookie,
        staging_root=staging_root,
        explicit_item_selector=explicit_item_selector,
    )
    return kernel.bootstrap_production_source(
        adapter=adapter,
        request=request,
        runner=runner,
        task_start=task_start,
        request_id=request_id,
        provider_kind=(
            "live_provider" if selected_mode == "live" else "recorded_fixture"
        ),
    )
