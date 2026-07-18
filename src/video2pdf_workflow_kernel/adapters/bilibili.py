from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from .base import PlatformAcquireRequest, PlatformAdapterError, PlatformProbeRequest
from .yt_dlp import YtDlpPlatformAdapter


_BILIBILI_BVID_PATTERN = re.compile(r"^BV[0-9A-Za-z]{10}$")
_BILIBILI_PROVIDER_ID_PATTERN = re.compile(
    r"^(BV[0-9A-Za-z]{10})(?:_p([1-9][0-9]*))?$"
)
_BILIBILI_ITEM_PATTERN = re.compile(
    r"^(BV[0-9A-Za-z]{10}):p([1-9][0-9]*)$"
)
_BILIBILI_PATH_PATTERN = re.compile(r"^/video/(BV[0-9A-Za-z]{10})/?$")


def _bilibili_url_locator(source_url: str) -> tuple[str, int | None]:
    if source_url != source_url.strip():
        raise ValueError("Bilibili URL has surrounding whitespace")
    parsed = urlsplit(source_url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Bilibili URL has an invalid port") from exc
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or (parsed.hostname or "").lower()
        not in {"bilibili.com", "www.bilibili.com", "m.bilibili.com"}
    ):
        raise ValueError("Bilibili URL has an unsupported origin")
    matched = _BILIBILI_PATH_PATTERN.fullmatch(parsed.path)
    if matched is None:
        raise ValueError("Bilibili URL has an unsupported item path")
    part_values = [
        value
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key == "p"
    ]
    if len(part_values) > 1:
        raise ValueError("Bilibili URL has an ambiguous part selection")
    if not part_values:
        return matched.group(1), None
    part = part_values[0]
    if not part.isdigit() or int(part) < 1:
        raise ValueError("Bilibili URL has an invalid part selection")
    return matched.group(1), int(part)


def _bilibili_explicit_part(selector: str | None) -> int | None:
    if selector is None:
        return None
    part = selector.lower().removeprefix("p")
    if not part.isdigit() or int(part) < 1:
        raise ValueError("Bilibili part selection is invalid")
    return int(part)


def _metadata_part(value: Any, *, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"Bilibili metadata {field} is invalid")
    normalized = str(value)
    if not normalized.isdigit() or int(normalized) < 1:
        raise ValueError(f"Bilibili metadata {field} is invalid")
    return int(normalized)


class BilibiliPlatformAdapter(YtDlpPlatformAdapter):
    adapter_id = "bilibili-yt-dlp.v1"
    canonical_platform = "bilibili"
    download_resource_class = "bilibili_download"

    def _provider_probe_url(self, request: PlatformProbeRequest) -> str:
        try:
            item_id, url_part = _bilibili_url_locator(request.source_url)
            explicit_part = _bilibili_explicit_part(
                request.explicit_item_selector
            )
        except ValueError as exc:
            raise PlatformAdapterError(
                "Bilibili probe request has an invalid source locator or part selection",
                classification="contract_invalid",
                exit_code=20,
            ) from exc
        if explicit_part is None:
            return request.source_url
        if url_part is not None and url_part != explicit_part:
            raise PlatformAdapterError(
                "Bilibili probe URL conflicts with its explicit part selection",
                classification="contract_invalid",
                exit_code=20,
            )
        return f"https://www.bilibili.com/video/{item_id}/?p={explicit_part}"

    def _validate_item_selection(
        self, metadata: dict[str, Any], request: PlatformProbeRequest
    ) -> None:
        entries = metadata.get("entries")
        count = metadata.get("n_entries")
        is_multi_item = (
            metadata.get("_type") == "playlist"
            or isinstance(entries, list) and len(entries) > 1
            or isinstance(count, int) and count > 1
        )
        if is_multi_item and request.explicit_item_selector is None:
            raise PlatformAdapterError(
                "Bilibili multi-part source requires an explicit part selection",
                classification="source_item_selection_required",
                exit_code=30,
                blocker_kind="user_input",
                data={"adapter_id": self.adapter_id},
            )

    def _canonical_item_id(
        self, metadata: dict[str, Any], request: PlatformProbeRequest
    ) -> str:
        raw_bvid = metadata.get("bvid")
        raw_provider_id = metadata.get("id")
        provider_id_match = (
            _BILIBILI_PROVIDER_ID_PATTERN.fullmatch(raw_provider_id)
            if isinstance(raw_provider_id, str)
            else None
        )
        if raw_bvid is None:
            bvid = provider_id_match.group(1) if provider_id_match else ""
        elif isinstance(raw_bvid, str) and _BILIBILI_BVID_PATTERN.fullmatch(
            raw_bvid
        ):
            bvid = raw_bvid
        else:
            bvid = ""
        if not bvid or (
            raw_provider_id is not None
            and (
                provider_id_match is None
                or provider_id_match.group(1) != bvid
            )
        ):
            raise PlatformAdapterError(
                "Bilibili metadata has an invalid or conflicting BV identity",
                classification="source_provider_output_invalid",
                exit_code=70,
                data={"adapter_id": self.adapter_id},
            )
        try:
            request_bvid, request_url_part = _bilibili_url_locator(
                request.source_url
            )
            explicit_part = _bilibili_explicit_part(
                request.explicit_item_selector
            )
        except ValueError as exc:
            raise PlatformAdapterError(
                "Bilibili probe request has an invalid source locator or part selection",
                classification="contract_invalid",
                exit_code=20,
            ) from exc
        if request_bvid != bvid:
            raise PlatformAdapterError(
                "Bilibili metadata identity conflicts with the requested item",
                classification="source_provider_output_invalid",
                exit_code=70,
                data={"adapter_id": self.adapter_id},
            )
        try:
            evidence_parts = tuple(
                value
                for value in (
                    _metadata_part(metadata.get("page"), field="page"),
                    _metadata_part(
                        metadata.get("playlist_index"),
                        field="playlist_index",
                    ),
                    (
                        int(provider_id_match.group(2))
                        if provider_id_match and provider_id_match.group(2)
                        else None
                    ),
                )
                if value is not None
            )
        except ValueError as exc:
            raise PlatformAdapterError(
                "Bilibili metadata has an invalid part identity",
                classification="source_provider_output_invalid",
                exit_code=70,
                data={"adapter_id": self.adapter_id},
            ) from exc
        selected_part = (
            explicit_part
            or request_url_part
            or (evidence_parts[0] if evidence_parts else 1)
        )
        if any(value != selected_part for value in evidence_parts):
            raise PlatformAdapterError(
                "Bilibili metadata part conflicts with the requested selection",
                classification="source_provider_output_invalid",
                exit_code=70,
                data={"adapter_id": self.adapter_id},
            )
        webpage_url = metadata.get("webpage_url")
        try:
            metadata_bvid, metadata_url_part = _bilibili_url_locator(
                webpage_url if isinstance(webpage_url, str) else ""
            )
        except ValueError as exc:
            raise PlatformAdapterError(
                "Bilibili metadata has an invalid canonical webpage URL",
                classification="source_provider_output_invalid",
                exit_code=70,
                data={"adapter_id": self.adapter_id},
            ) from exc
        if metadata_bvid != bvid or (
            metadata_url_part is not None
            and metadata_url_part != selected_part
        ):
            raise PlatformAdapterError(
                "Bilibili metadata webpage URL conflicts with its item identity",
                classification="source_provider_output_invalid",
                exit_code=70,
                data={"adapter_id": self.adapter_id},
            )
        return f"{bvid}:p{selected_part}"

    def _canonical_url(
        self, metadata: dict[str, Any], request: PlatformProbeRequest
    ) -> str:
        matched = _BILIBILI_ITEM_PATTERN.fullmatch(
            self._canonical_item_id(metadata, request)
        )
        if matched is None:
            raise PlatformAdapterError(
                "Bilibili canonical item identity is invalid",
                classification="source_provider_output_invalid",
                exit_code=70,
                data={"adapter_id": self.adapter_id},
            )
        return (
            f"https://www.bilibili.com/video/{matched.group(1)}/"
            f"?p={int(matched.group(2))}"
        )

    def _validated_acquisition_url(self, request: PlatformAcquireRequest) -> str:
        matched = _BILIBILI_ITEM_PATTERN.fullmatch(request.probe.canonical_item_id)
        if matched is None:
            raise PlatformAdapterError(
                "Bilibili acquisition probe has an invalid canonical item identity",
                classification="contract_invalid",
                exit_code=20,
            )
        item_id = matched.group(1)
        part = int(matched.group(2))
        expected_url = f"https://www.bilibili.com/video/{item_id}/?p={part}"
        if request.probe.canonical_url != expected_url:
            raise PlatformAdapterError(
                "Bilibili acquisition probe URL conflicts with its canonical item identity",
                classification="contract_invalid",
                exit_code=20,
            )
        try:
            request_item_id, request_part = _bilibili_url_locator(request.source_url)
        except ValueError as exc:
            raise PlatformAdapterError(
                "Bilibili acquisition request has an invalid source locator",
                classification="contract_invalid",
                exit_code=20,
            ) from exc
        if request_item_id != item_id or (
            request_part is not None and request_part != part
        ):
            raise PlatformAdapterError(
                "Bilibili acquisition request conflicts with the admitted Source Probe",
                classification="contract_invalid",
                exit_code=20,
            )
        return request.probe.canonical_url

    def _subtitle_arguments(
        self, origin: str, languages: tuple[str, ...], source_url: str
    ) -> tuple[str, ...]:
        arguments = super()._subtitle_arguments(origin, languages, source_url)
        if origin != "automatic":
            return arguments
        return (arguments[0], "--write-subs", *arguments[1:])
