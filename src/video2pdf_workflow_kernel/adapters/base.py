from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Literal, Protocol, runtime_checkable

from ..errors import KernelError


AUTHENTICATION_CLASSIFICATIONS = frozenset(
    {
        "not_applicable",
        "anonymous",
        "cookie_accepted",
        "cookie_missing",
        "cookie_unreadable",
        "cookie_rejected",
        "cookie_expired",
    }
)


class PlatformAdapterError(KernelError):
    """A machine-classified, secret-free platform provider failure."""

    def __init__(
        self,
        message: str,
        *,
        classification: str,
        exit_code: int,
        blocker_kind: str | None = None,
        retryable: bool = False,
        data: dict[str, Any] | None = None,
    ) -> None:
        self.classification = classification
        self.exit_code = exit_code
        self.blocker_kind = blocker_kind
        self.retryable = retryable
        safe_data = dict(data or {})
        safe_data.setdefault("retryable", retryable)
        if blocker_kind is not None:
            safe_data.setdefault("blocker_kind", blocker_kind)
        super().__init__(message, data=safe_data)


@dataclass(frozen=True, repr=False)
class SecretArgument:
    value: str
    evidence_value: str = "<localized-cookie-file>"


@dataclass(frozen=True, repr=False)
class _RedactionSnapshot:
    values: tuple[str, ...] = field(repr=False)


@dataclass(frozen=True)
class CommandSpec:
    operation: str
    argv: tuple[str | SecretArgument, ...]
    cwd: Path
    allowed_output_root: Path
    timeout_seconds: int

    def execution_argv(self) -> tuple[str, ...]:
        return tuple(
            argument.value if isinstance(argument, SecretArgument) else argument
            for argument in self.argv
        )

    def evidence_argv(
        self, *, redaction_snapshot: _RedactionSnapshot | None = None
    ) -> tuple[str, ...]:
        snapshot = redaction_snapshot or _capture_redaction_snapshot(self)
        structural = tuple(
            argument.evidence_value
            if isinstance(argument, SecretArgument)
            else argument
            for argument in self.argv
        )
        return tuple(
            redact_provider_text(
                argument.encode("utf-8"),
                self,
                redaction_snapshot=snapshot,
            ).decode("utf-8")
            for argument in structural
        )


@dataclass(frozen=True)
class RecordedProviderEvidence:
    recording_id: str
    manifest_sha256: str
    canonical_platform: Literal["bilibili", "youtube"]
    adapter_id: str
    adapter_contract_version: str
    command_count: int
    command_sequence_sha256: str


@dataclass(frozen=True)
class CommandEvidence:
    operation: str
    argv: tuple[str, ...]
    argv_sha256: str
    returncode: int
    stdout_sha256: str
    stderr_sha256: str
    recorded_provider: RecordedProviderEvidence | None = None
    recorded_stdout_sha256: str | None = None
    recorded_stderr_sha256: str | None = None


def command_evidence_sequence_sha256(
    evidence_values: tuple[CommandEvidence, ...] | list[CommandEvidence],
) -> str:
    """Bind the ordered, redacted command transcript without fixture paths."""

    payload = [
        {
            "operation": evidence.operation,
            "argv": list(evidence.argv),
            "argv_sha256": evidence.argv_sha256,
            "returncode": evidence.returncode,
            "stdout_sha256": (
                evidence.recorded_stdout_sha256 or evidence.stdout_sha256
            ),
            "stderr_sha256": (
                evidence.recorded_stderr_sha256 or evidence.stderr_sha256
            ),
        }
        for evidence in evidence_values
    ]
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, repr=False)
class CommandResult:
    returncode: int
    stdout: bytes = field(repr=False)
    stderr: bytes = field(repr=False)
    evidence: CommandEvidence


class CommandRunner(Protocol):
    def run(self, command: CommandSpec) -> CommandResult: ...


@dataclass(frozen=True)
class PlatformProbeRequest:
    source_url: str
    localized_cookie_file: Path
    staging_root: Path
    explicit_item_selector: str | None = None


@dataclass(frozen=True)
class SubtitleTrack:
    track_id: str
    provider_language: str
    normalized_language: str
    origin: Literal["manual", "automatic"]
    formats: tuple[str, ...]


@dataclass(frozen=True)
class PlatformProbe:
    adapter_id: str
    canonical_platform: Literal["bilibili", "youtube"]
    canonical_item_id: str
    canonical_url: str
    original_title: str
    duration_seconds: float
    platform_revision: dict[str, str | int | None]
    subtitle_tracks: tuple[SubtitleTrack, ...]
    media_formats: tuple[dict[str, Any], ...]
    normalized_metadata_path: Path
    authentication_classification: str
    command_evidence: tuple[CommandEvidence, ...]


@dataclass(frozen=True)
class PlatformAcquireRequest:
    source_url: str
    localized_cookie_file: Path
    staging_root: Path
    probe: PlatformProbe
    eligible_track_ids: tuple[str, ...]
    max_video_height: int = 1080


@dataclass(frozen=True)
class StagedArtifact:
    logical_id: str
    path: Path
    media_type: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class PlatformAcquisition:
    probe: PlatformProbe
    subtitle_candidates: tuple[StagedArtifact, ...]
    video: StagedArtifact
    cover: StagedArtifact
    media_probe: StagedArtifact
    command_evidence: tuple[CommandEvidence, ...]


@runtime_checkable
class PlatformAdapter(Protocol):
    adapter_id: str
    canonical_platform: str
    download_resource_class: str

    def probe(
        self, request: PlatformProbeRequest, *, runner: CommandRunner
    ) -> PlatformProbe: ...

    def acquire(
        self, request: PlatformAcquireRequest, *, runner: CommandRunner
    ) -> PlatformAcquisition: ...


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _redaction_values(command: CommandSpec) -> tuple[str, ...]:
    values: set[str] = set()
    for argument in command.argv:
        if not isinstance(argument, SecretArgument):
            continue
        value = argument.value
        values.update({value, value.replace("\\", "/"), value.replace("/", "\\")})
        path = Path(value)
        if not path.is_file():
            continue
        try:
            cookie_text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in cookie_text.splitlines():
            if not line or line.startswith("#"):
                continue
            fields = line.split("\t")
            if len(fields) >= 7 and len(fields[-1]) >= 4:
                values.add(fields[-1])
    return tuple(sorted(values, key=len, reverse=True))


def _capture_redaction_snapshot(command: CommandSpec) -> _RedactionSnapshot:
    return _RedactionSnapshot(values=_redaction_values(command))


_HEADER_PATTERN = re.compile(
    r"(?im)^(\s*(?:cookie|set-cookie|authorization)\s*:).*$"
)
_QUERY_SECRET_PATTERN = re.compile(
    r"(?i)([?&](?:auth|authorization|cookie|csrf|po_token|session|signature|token|visitor_data)=)[^&\s\"']+"
)


def redact_provider_text(
    value: bytes,
    command: CommandSpec,
    *,
    redaction_snapshot: _RedactionSnapshot | None = None,
) -> bytes:
    snapshot = redaction_snapshot or _capture_redaction_snapshot(command)
    text = value.decode("utf-8", errors="replace")
    for secret in snapshot.values:
        text = text.replace(secret, "<redacted>")
    text = _HEADER_PATTERN.sub(r"\1 <redacted>", text)
    text = _QUERY_SECRET_PATTERN.sub(r"\1<redacted>", text)
    return text.encode("utf-8")


class SubprocessCommandRunner:
    """Execute one fixed command and expose only redacted process output."""

    def run(self, command: CommandSpec) -> CommandResult:
        cwd = command.cwd.resolve(strict=False)
        allowed = command.allowed_output_root.resolve(strict=False)
        try:
            cwd.relative_to(allowed)
        except ValueError as exc:
            raise PlatformAdapterError(
                "provider command cwd escapes its allowed output root",
                classification="contract_invalid",
                exit_code=20,
                data={"operation": command.operation},
            ) from exc
        cwd.mkdir(parents=True, exist_ok=True)
        environment = os.environ.copy()
        environment["PYTHONUTF8"] = "1"
        environment["PYTHONIOENCODING"] = "utf-8"
        redaction_snapshot = _capture_redaction_snapshot(command)
        try:
            completed = subprocess.run(
                command.execution_argv(),
                cwd=cwd,
                env=environment,
                capture_output=True,
                check=False,
                timeout=command.timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PlatformAdapterError(
                "provider command could not complete",
                classification="source_provider_command_failed",
                exit_code=70,
                retryable=isinstance(exc, subprocess.TimeoutExpired),
                data={"operation": command.operation},
            ) from None
        stdout = redact_provider_text(
            completed.stdout,
            command,
            redaction_snapshot=redaction_snapshot,
        )
        stderr = redact_provider_text(
            completed.stderr,
            command,
            redaction_snapshot=redaction_snapshot,
        )
        evidence_argv = command.evidence_argv(
            redaction_snapshot=redaction_snapshot
        )
        evidence = CommandEvidence(
            operation=command.operation,
            argv=evidence_argv,
            argv_sha256=_sha256("\0".join(evidence_argv).encode("utf-8")),
            returncode=completed.returncode,
            stdout_sha256=_sha256(stdout),
            stderr_sha256=_sha256(stderr),
        )
        return CommandResult(
            returncode=completed.returncode,
            stdout=stdout,
            stderr=stderr,
            evidence=evidence,
        )
