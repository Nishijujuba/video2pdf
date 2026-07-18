from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from .base import PlatformAcquireRequest, PlatformAdapterError, PlatformProbeRequest
from .yt_dlp import YtDlpPlatformAdapter


_YOUTUBE_ITEM_PATTERN = re.compile(r"^[0-9A-Za-z_-]{11}$")


def _youtube_url_item_id(source_url: str) -> str:
    if source_url != source_url.strip():
        raise ValueError("YouTube URL has surrounding whitespace")
    parsed = urlsplit(source_url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("YouTube URL has an invalid port") from exc
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
    ):
        raise ValueError("YouTube URL has an unsupported origin")
    host = (parsed.hostname or "").lower()
    if host == "youtu.be":
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) != 1:
            raise ValueError("YouTube short URL has an invalid item path")
        item_id = parts[0]
    elif host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        if parsed.path == "/watch":
            values = [
                value
                for key, value in parse_qsl(parsed.query, keep_blank_values=True)
                if key == "v"
            ]
            if len(values) != 1:
                raise ValueError("YouTube watch URL has an ambiguous item identity")
            item_id = values[0]
        else:
            parts = [part for part in parsed.path.split("/") if part]
            if len(parts) != 2 or parts[0] not in {"embed", "shorts"}:
                raise ValueError("YouTube URL has an unsupported item path")
            item_id = parts[1]
    else:
        raise ValueError("YouTube URL has an unsupported origin")
    if _YOUTUBE_ITEM_PATTERN.fullmatch(item_id) is None:
        raise ValueError("YouTube URL has an invalid item identity")
    return item_id


class YouTubePlatformAdapter(YtDlpPlatformAdapter):
    adapter_id = "youtube-yt-dlp.v1"
    canonical_platform = "youtube"
    download_resource_class = "youtube_download"
    _platform_yt_dlp_flags = ("--js-runtimes", "node")

    def _canonical_item_id(
        self, metadata: dict[str, Any], request: PlatformProbeRequest
    ) -> str:
        item_id = metadata.get("id")
        if (
            not isinstance(item_id, str)
            or _YOUTUBE_ITEM_PATTERN.fullmatch(item_id) is None
        ):
            raise PlatformAdapterError(
                "YouTube metadata has an invalid canonical video identity",
                classification="source_provider_output_invalid",
                exit_code=70,
                data={"adapter_id": self.adapter_id},
            )
        try:
            request_item_id = _youtube_url_item_id(request.source_url)
            webpage_url = metadata.get("webpage_url")
            metadata_url_item_id = _youtube_url_item_id(
                webpage_url if isinstance(webpage_url, str) else ""
            )
        except ValueError as exc:
            raise PlatformAdapterError(
                "YouTube metadata has an invalid canonical webpage URL",
                classification="source_provider_output_invalid",
                exit_code=70,
                data={"adapter_id": self.adapter_id},
            ) from exc
        if request_item_id != item_id or metadata_url_item_id != item_id:
            raise PlatformAdapterError(
                "YouTube metadata identity conflicts with its source locator",
                classification="source_provider_output_invalid",
                exit_code=70,
                data={"adapter_id": self.adapter_id},
            )
        return item_id

    def _canonical_url(
        self, metadata: dict[str, Any], request: PlatformProbeRequest
    ) -> str:
        return f"https://www.youtube.com/watch?v={self._canonical_item_id(metadata, request)}"

    def _validated_acquisition_url(self, request: PlatformAcquireRequest) -> str:
        item_id = request.probe.canonical_item_id
        expected_url = f"https://www.youtube.com/watch?v={item_id}"
        if request.probe.canonical_url != expected_url:
            raise PlatformAdapterError(
                "YouTube acquisition probe URL conflicts with its canonical item identity",
                classification="contract_invalid",
                exit_code=20,
            )
        try:
            request_item_id = _youtube_url_item_id(request.source_url)
        except ValueError as exc:
            raise PlatformAdapterError(
                "YouTube acquisition request has an invalid source locator",
                classification="contract_invalid",
                exit_code=20,
            ) from exc
        if request_item_id != item_id:
            raise PlatformAdapterError(
                "YouTube acquisition request conflicts with the admitted Source Probe",
                classification="contract_invalid",
                exit_code=20,
            )
        return request.probe.canonical_url
