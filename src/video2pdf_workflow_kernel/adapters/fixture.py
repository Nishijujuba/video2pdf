from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
import shutil
from typing import Any

from ..contracts import ContractRegistry
from ..errors import CapabilityForbidden, ContractError
from ..utils import sha256_file


class FixturePlatformAdapter:
    """Test-only adapter with an intentionally deprived capability surface."""

    adapter_id = "fixture"
    test_only = True
    capabilities = ("offline_probe", "verified_import")
    forbidden_capabilities = frozenset(
        {
            "network_download",
            "cookie_access",
            "downloader",
            "whisper",
            "semantic_provider",
            "latex",
            "acceptance",
            "batch",
            "delivery",
            "subprocess",
        }
    )

    def __init__(self, fixture_root: Path, contracts: ContractRegistry) -> None:
        self.fixture_root = fixture_root.resolve()
        self.contracts = contracts
        manifest_path = self.fixture_root / "fixture.json"
        if not manifest_path.is_file():
            raise ContractError(f"fixture manifest does not exist: {manifest_path}")
        self.manifest_path = manifest_path
        self.manifest: dict[str, Any] = json.loads(
            manifest_path.read_text(encoding="utf-8")
        )
        contracts.validate("fixture-package", self.manifest)
        if tuple(self.manifest["capabilities"]) != self.capabilities:
            raise ContractError("fixture capabilities differ from the deprived adapter contract")
        self._verify_fixture_artifacts()

    def require_capability(self, capability: str) -> None:
        if capability not in self.capabilities:
            raise CapabilityForbidden(
                f"Fixture Platform Adapter cannot invoke production capability: {capability}",
                data={
                    "adapter_id": self.adapter_id,
                    "requested_capability": capability,
                    "allowed_capabilities": list(self.capabilities),
                },
            )

    def probe(self) -> dict[str, Any]:
        self.require_capability("offline_probe")
        return {
            "adapter_id": self.adapter_id,
            "canonical_item_id": self.manifest["canonical_item_id"],
            "original_title": self.manifest["original_title"],
            "duration_seconds": self.manifest["duration_seconds"],
            "capabilities": list(self.capabilities),
        }

    def verified_import(self, staged_run_dir: Path) -> list[dict[str, Any]]:
        self.require_capability("verified_import")
        imported: list[dict[str, Any]] = []
        for artifact in self.manifest["artifacts"]:
            source_relative = PurePosixPath(artifact["path"])
            source = self._contained_fixture_path(artifact["path"])
            if source_relative.parts[0] == "metadata":
                target_relative = PurePosixPath("source/metadata") / PurePosixPath(
                    *source_relative.parts[1:]
                )
            else:
                target_relative = PurePosixPath("source") / source_relative
            target = staged_run_dir.joinpath(*target_relative.parts)
            source_root = (staged_run_dir / "source").resolve()
            try:
                target.resolve(strict=False).relative_to(source_root)
            except ValueError as exc:
                raise ContractError(
                    f"fixture import target escapes source root: {target_relative}"
                ) from exc
            if not target.parent.is_dir():
                raise ContractError(
                    f"Kernel scaffold did not create managed import directory: {target.parent}"
                )
            shutil.copy2(source, target)
            imported.append(
                {
                    "logical_id": artifact["logical_id"],
                    "path": target_relative.as_posix(),
                    "media_type": artifact["media_type"],
                    "sha256": sha256_file(target),
                    "size_bytes": target.stat().st_size,
                }
            )
        return imported

    def _verify_fixture_artifacts(self) -> None:
        for artifact in self.manifest["artifacts"]:
            relative = PurePosixPath(artifact["path"])
            path = self._contained_fixture_path(artifact["path"])
            if not path.is_file():
                raise ContractError(f"fixture artifact is missing: {path}")
            actual = sha256_file(path)
            if actual != artifact["sha256"]:
                raise ContractError(
                    f"immutable fixture artifact drift: {relative}: expected "
                    f"{artifact['sha256']}, got {actual}"
                )

    def _contained_fixture_path(self, value: str) -> Path:
        if "\\" in value:
            raise ContractError(f"fixture artifact path uses a backslash: {value!r}")
        relative = PurePosixPath(value)
        if (
            relative.is_absolute()
            or relative.as_posix() != value
            or any(part in {".", ".."} for part in relative.parts)
        ):
            raise ContractError(f"fixture artifact path is noncanonical: {value!r}")
        candidate = self.fixture_root.joinpath(*relative.parts).resolve(strict=False)
        try:
            candidate.relative_to(self.fixture_root)
        except ValueError as exc:
            raise ContractError(
                f"fixture artifact path escapes package: {value!r}"
            ) from exc
        return candidate
