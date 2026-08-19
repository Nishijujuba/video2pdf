from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
from typing import Any

from ..contracts import ContractRegistry
from ..errors import ContractError
from ..utils import sha256_file
from .base import (
    CommandEvidence,
    CommandResult,
    CommandSpec,
    PlatformAdapterError,
    RecordedProviderEvidence,
    command_evidence_sequence_sha256,
    redact_provider_text,
)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _is_reparse_point(path: Path) -> bool:
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


class RecordedCommandRunner:
    """Replay a closed sequence of provider calls without launching a process."""

    def __init__(self, recording_root: Path) -> None:
        self.recording_root = recording_root.resolve()
        manifest_path = self.recording_root / "recording.json"
        if not manifest_path.is_file():
            raise PlatformAdapterError(
                "recorded provider manifest is missing",
                classification="contract_invalid",
                exit_code=20,
            )
        self._manifest_path = manifest_path
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise PlatformAdapterError(
                "recorded provider manifest is invalid",
                classification="contract_invalid",
                exit_code=20,
            ) from exc
        try:
            ContractRegistry(Path(__file__).resolve().parents[3]).validate(
                "recorded-provider-package", manifest
            )
        except ContractError as exc:
            raise PlatformAdapterError(
                "recorded provider manifest is contract-invalid",
                classification="contract_invalid",
                exit_code=20,
            ) from exc
        commands = manifest["commands"]
        manifest_sha256 = sha256_file(manifest_path)
        expected_evidence: list[CommandEvidence] = []
        output_targets: set[str] = set()
        for command in commands:
            stdout = self._fixture_file(
                command["stdout"]["path"], purpose="stdout"
            )
            stderr = self._fixture_file(
                command["stderr"]["path"], purpose="stderr"
            )
            if sha256_file(stdout) != command["stdout"]["sha256"]:
                raise PlatformAdapterError(
                    "recorded stdout fixture drifted",
                    classification="source_artifact_drift",
                    exit_code=40,
                )
            if sha256_file(stderr) != command["stderr"]["sha256"]:
                raise PlatformAdapterError(
                    "recorded stderr fixture drifted",
                    classification="source_artifact_drift",
                    exit_code=40,
                )
            for output in command["outputs"]:
                target = output["attempt_relative_path"]
                if target in output_targets:
                    raise PlatformAdapterError(
                        "recorded provider repeats an output target",
                        classification="contract_invalid",
                        exit_code=20,
                    )
                output_targets.add(target)
                source = self._fixture_file(output["fixture"], purpose="output")
                if sha256_file(source) != output["sha256"]:
                    raise PlatformAdapterError(
                        "recorded provider output fixture drifted",
                        classification="source_artifact_drift",
                        exit_code=40,
                    )
            argv = tuple(command["argv"])
            expected_evidence.append(
                CommandEvidence(
                    operation=command["operation"],
                    argv=argv,
                    argv_sha256=_sha256("\0".join(argv).encode("utf-8")),
                    returncode=command["returncode"],
                    stdout_sha256=command["stdout"]["sha256"],
                    stderr_sha256=command["stderr"]["sha256"],
                    recorded_stdout_sha256=command["stdout"]["sha256"],
                    recorded_stderr_sha256=command["stderr"]["sha256"],
                )
            )
        adapter = manifest["adapter"]
        self._recording_evidence = RecordedProviderEvidence(
            recording_id=manifest["recording_id"],
            manifest_sha256=manifest_sha256,
            canonical_platform=manifest["canonical_platform"],
            adapter_id=adapter["id"],
            adapter_contract_version=adapter["contract_version"],
            command_count=len(commands),
            command_sequence_sha256=command_evidence_sequence_sha256(
                expected_evidence
            ),
        )
        self._commands: tuple[dict[str, Any], ...] = tuple(commands)
        self._cursor = 0
        self.evidence: list[CommandEvidence] = []

    @property
    def recording_evidence(self) -> RecordedProviderEvidence:
        return self._recording_evidence

    def _assert_manifest_current(self) -> None:
        if (
            not self._manifest_path.is_file()
            or sha256_file(self._manifest_path)
            != self.recording_evidence.manifest_sha256
        ):
            raise PlatformAdapterError(
                "recorded provider manifest drifted after validation",
                classification="source_artifact_drift",
                exit_code=40,
            )

    def assert_adapter_binding(
        self,
        *,
        canonical_platform: str,
        adapter_id: str,
        adapter_contract_version: str,
    ) -> None:
        binding = self.recording_evidence
        if binding.canonical_platform != canonical_platform:
            raise PlatformAdapterError(
                "recorded provider platform differs from the selected Adapter",
                classification="contract_invalid",
                exit_code=20,
            )
        if (
            binding.adapter_id != adapter_id
            or binding.adapter_contract_version != adapter_contract_version
        ):
            raise PlatformAdapterError(
                "recorded provider Adapter contract differs from the selected Adapter",
                classification="contract_invalid",
                exit_code=20,
            )

    def _fixture_file(self, relative_value: str, *, purpose: str) -> Path:
        if "\\" in relative_value:
            raise PlatformAdapterError(
                f"recorded {purpose} path is noncanonical",
                classification="contract_invalid",
                exit_code=20,
            )
        relative = PurePosixPath(relative_value)
        if (
            relative.is_absolute()
            or relative.as_posix() != relative_value
            or any(part in {"", ".", ".."} for part in relative.parts)
        ):
            raise PlatformAdapterError(
                f"recorded {purpose} path is noncanonical",
                classification="contract_invalid",
                exit_code=20,
            )
        expected_root = {
            "stdout": "stdout",
            "stderr": "stderr",
            "output": "outputs",
        }.get(purpose)
        if expected_root is not None and relative.parts[0] != expected_root:
            raise PlatformAdapterError(
                f"recorded {purpose} path is outside its canonical directory",
                classification="contract_invalid",
                exit_code=20,
            )
        path = self.recording_root.joinpath(*relative.parts)
        resolved = path.resolve(strict=True)
        try:
            resolved.relative_to(self.recording_root)
        except ValueError as exc:
            raise PlatformAdapterError(
                f"recorded {purpose} path escapes its fixture",
                classification="contract_invalid",
                exit_code=20,
            ) from exc
        if not path.is_file() or path.is_symlink() or _is_reparse_point(path):
            raise PlatformAdapterError(
                f"recorded {purpose} is not a regular file",
                classification="contract_invalid",
                exit_code=20,
            )
        return resolved

    def _target_file(self, root: Path, relative_value: str) -> Path:
        if "\\" in relative_value:
            raise PlatformAdapterError(
                "recorded output path is noncanonical",
                classification="contract_invalid",
                exit_code=20,
            )
        relative = PurePosixPath(relative_value)
        if (
            relative.is_absolute()
            or relative.as_posix() != relative_value
            or any(part in {"", ".", ".."} for part in relative.parts)
        ):
            raise PlatformAdapterError(
                "recorded output path is noncanonical",
                classification="contract_invalid",
                exit_code=20,
            )
        resolved_root = root.resolve(strict=False)
        target = root.joinpath(*relative.parts)
        try:
            target.resolve(strict=False).relative_to(resolved_root)
        except ValueError as exc:
            raise PlatformAdapterError(
                "recorded output path escapes provider staging",
                classification="contract_invalid",
                exit_code=20,
            ) from exc
        return target

    def run(self, command: CommandSpec) -> CommandResult:
        self._assert_manifest_current()
        if self._cursor >= len(self._commands):
            raise PlatformAdapterError(
                "recorded provider received an unexpected extra command",
                classification="source_recording_mismatch",
                exit_code=20,
                data={"operation": command.operation},
            )
        expected = self._commands[self._cursor]
        expected_operation = expected.get("operation")
        expected_argv = expected.get("argv")
        actual_argv = command.evidence_argv()
        if expected_operation != command.operation or expected_argv != list(actual_argv):
            raise PlatformAdapterError(
                "recorded provider command is out of order or has drifted",
                classification="source_recording_mismatch",
                exit_code=20,
                data={
                    "expected_operation": expected_operation,
                    "actual_operation": command.operation,
                },
            )
        self._cursor += 1

        stdout_binding = expected["stdout"]
        stderr_binding = expected["stderr"]
        stdout_path = self._fixture_file(stdout_binding["path"], purpose="stdout")
        stderr_path = self._fixture_file(stderr_binding["path"], purpose="stderr")
        stdout_raw = stdout_path.read_bytes()
        stderr_raw = stderr_path.read_bytes()
        if _sha256(stdout_raw) != stdout_binding["sha256"]:
            raise PlatformAdapterError(
                "recorded stdout fixture drifted",
                classification="source_artifact_drift",
                exit_code=40,
            )
        if _sha256(stderr_raw) != stderr_binding["sha256"]:
            raise PlatformAdapterError(
                "recorded stderr fixture drifted",
                classification="source_artifact_drift",
                exit_code=40,
            )
        stdout = redact_provider_text(stdout_raw, command)
        stderr = redact_provider_text(stderr_raw, command)
        outputs = expected.get("outputs", [])
        if not isinstance(outputs, list):
            raise PlatformAdapterError(
                "recorded provider outputs are invalid",
                classification="contract_invalid",
                exit_code=20,
            )
        for output in outputs:
            if not isinstance(output, dict):
                raise PlatformAdapterError(
                    "recorded provider output entry is invalid",
                    classification="contract_invalid",
                    exit_code=20,
                )
            source = self._fixture_file(str(output["fixture"]), purpose="output")
            expected_sha256 = str(output["sha256"])
            if sha256_file(source) != expected_sha256:
                raise PlatformAdapterError(
                    "recorded provider output fixture drifted",
                    classification="source_artifact_drift",
                    exit_code=40,
                )
            target = self._target_file(
                command.allowed_output_root, str(output["attempt_relative_path"])
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if not target.is_file() or sha256_file(target) != expected_sha256:
                    raise PlatformAdapterError(
                        "recorded provider target already contains different content",
                        classification="source_artifact_drift",
                        exit_code=40,
                    )
            else:
                shutil.copy2(source, target)

        returncode = expected.get("returncode")
        if not isinstance(returncode, int):
            raise PlatformAdapterError(
                "recorded provider return code is invalid",
                classification="contract_invalid",
                exit_code=20,
            )
        evidence = CommandEvidence(
            operation=command.operation,
            argv=actual_argv,
            argv_sha256=_sha256("\0".join(actual_argv).encode("utf-8")),
            returncode=returncode,
            stdout_sha256=_sha256(stdout),
            stderr_sha256=_sha256(stderr),
            recorded_provider=self.recording_evidence,
            recorded_stdout_sha256=stdout_binding["sha256"],
            recorded_stderr_sha256=stderr_binding["sha256"],
        )
        self.evidence.append(evidence)
        return CommandResult(
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            evidence=evidence,
        )

    def assert_consumed(self) -> None:
        self._assert_manifest_current()
        remaining = len(self._commands) - self._cursor
        if remaining:
            raise PlatformAdapterError(
                f"recorded provider has {remaining} unconsumed command(s)",
                classification="source_recording_mismatch",
                exit_code=20,
                data={"remaining_commands": remaining},
            )
        if (
            command_evidence_sequence_sha256(self.evidence)
            != self.recording_evidence.command_sequence_sha256
        ):
            raise PlatformAdapterError(
                "recorded provider command evidence differs from its manifest",
                classification="source_recording_mismatch",
                exit_code=20,
            )
