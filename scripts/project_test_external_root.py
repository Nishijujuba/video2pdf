from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Collection
from urllib.parse import urlsplit
import uuid


PROJECT_MARKER_SCHEMA_NAME = "external-test-project"
PROJECT_MARKER_SCHEMA_VERSION = "1.0.0"
PROJECT_KEY = "video2pdf"
PROJECT_REPOSITORY = "Nishijujuba/video2pdf"
PROJECT_REMOTE_IDENTITY = "github.com/Nishijujuba/video2pdf"
PROJECT_MARKER_NAME = "project.json"

_MARKER_FIELDS = frozenset(
    {
        "schema_name",
        "schema_version",
        "project_key",
        "repository",
        "remote_identity",
    }
)
_SUITE_KEY_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_TIMESTAMP_PATTERN = re.compile(r"\d{8}_\d{6}\Z")
_SHORT_RUN_ID_PATTERN = re.compile(r"[0-9a-f]{8}\Z")
_SCP_GITHUB_REMOTE = re.compile(
    r"git@github\.com:(?P<owner>[^/:\s]+)/(?P<repository>[^/\s]+?)(?:\.git)?/?\Z",
    re.IGNORECASE,
)
_REPARSE_POINT_ATTRIBUTE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class ExternalRootError(ValueError):
    """The configured external test boundary is unsafe or has invalid ownership."""


def _is_disallowed_windows_namespace(value: str) -> bool:
    normalized = value.replace("/", "\\")
    return normalized.startswith(("\\\\", "\\?\\", "\\.\\"))


def _absolute_lexical_path(value: str | os.PathLike[str]) -> Path:
    raw_value = os.fspath(value)
    if not raw_value:
        raise ExternalRootError("External Test Root must not be empty")
    if _is_disallowed_windows_namespace(raw_value):
        raise ExternalRootError(
            "External Test Root must be a local path; UNC and device paths are rejected"
        )
    candidate = Path(raw_value)
    if not candidate.is_absolute():
        raise ExternalRootError("External Test Root must be an absolute path")
    return Path(os.path.abspath(candidate))


def _is_reparse_point(path: Path) -> bool:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return False
    attributes = getattr(metadata, "st_file_attributes", 0)
    return path.is_symlink() or bool(attributes & _REPARSE_POINT_ATTRIBUTE)


def _assert_no_reparse_points(path: Path) -> None:
    for component in reversed((path, *path.parents)):
        if not component.exists() and not component.is_symlink():
            continue
        if _is_reparse_point(component):
            raise ExternalRootError(
                f"External Test Root path contains a reparse point: {component}"
            )


def _normalized_common_path(base: Path, candidate: Path) -> str:
    try:
        return os.path.normcase(os.path.commonpath((str(base), str(candidate))))
    except ValueError as error:
        raise ExternalRootError(
            f"path is outside the owned External Test Root: {candidate}"
        ) from error


def assert_path_within(
    base: str | os.PathLike[str],
    candidate: str | os.PathLike[str],
) -> Path:
    """Return a lexical absolute candidate only when it is contained by base."""

    absolute_base = _absolute_lexical_path(base)
    absolute_candidate = _absolute_lexical_path(candidate)
    common = _normalized_common_path(absolute_base, absolute_candidate)
    if common != os.path.normcase(str(absolute_base)):
        raise ExternalRootError(
            f"path is outside the owned External Test Root: {absolute_candidate}"
        )
    return absolute_candidate


def assert_safe_write_path(
    base: str | os.PathLike[str],
    candidate: str | os.PathLike[str],
) -> Path:
    """Recheck containment and every existing ancestor before a write."""

    absolute_base = _absolute_lexical_path(base)
    absolute_candidate = assert_path_within(absolute_base, candidate)
    _assert_no_reparse_points(absolute_base)
    _assert_no_reparse_points(absolute_candidate)
    return absolute_candidate


def validate_external_test_root(
    test_root: str | os.PathLike[str],
) -> Path:
    """Validate a user-created, local, absolute, ordinary External Test Root."""

    root = _absolute_lexical_path(test_root)
    _assert_no_reparse_points(root)
    if not root.exists() or not root.is_dir():
        raise ExternalRootError(
            "External Test Root must be an existing ordinary directory"
        )
    _assert_no_reparse_points(root)
    return root


def normalize_github_remote_identity(remote: str) -> str:
    """Normalize an unambiguous GitHub remote to github.com/owner/repository."""

    if not isinstance(remote, str) or not remote.strip():
        raise ExternalRootError("GitHub remote identity must be a non-empty string")
    value = remote.strip()
    scp_match = _SCP_GITHUB_REMOTE.fullmatch(value)
    if scp_match:
        owner = scp_match.group("owner")
        repository = scp_match.group("repository")
    else:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in {"https", "ssh", "git"}:
            raise ExternalRootError("unsupported GitHub remote scheme")
        if parsed.hostname is None or parsed.hostname.casefold() != "github.com":
            raise ExternalRootError("remote identity must use github.com")
        if parsed.password is not None:
            raise ExternalRootError("credential-bearing GitHub remotes are rejected")
        expected_username = "git" if parsed.scheme.lower() in {"ssh", "git"} else None
        if parsed.username != expected_username:
            raise ExternalRootError("unexpected GitHub remote username")
        if parsed.query or parsed.fragment:
            raise ExternalRootError("GitHub remote query and fragment are rejected")
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) != 2:
            raise ExternalRootError(
                "GitHub remote must contain exactly owner and repository"
            )
        owner, repository = parts
        if repository.lower().endswith(".git"):
            repository = repository[:-4]
    if not owner or not repository or any(
        character.isspace() for character in owner + repository
    ):
        raise ExternalRootError("invalid GitHub owner or repository")
    normalized = f"github.com/{owner.casefold()}/{repository.casefold()}"
    if normalized == PROJECT_REMOTE_IDENTITY.casefold():
        return PROJECT_REMOTE_IDENTITY
    return normalized


def _project_marker() -> dict[str, object]:
    return {
        "schema_name": PROJECT_MARKER_SCHEMA_NAME,
        "schema_version": PROJECT_MARKER_SCHEMA_VERSION,
        "project_key": PROJECT_KEY,
        "repository": PROJECT_REPOSITORY,
        "remote_identity": PROJECT_REMOTE_IDENTITY,
    }


def _expected_marker(remote_identity: str) -> dict[str, object]:
    normalized_remote = normalize_github_remote_identity(remote_identity)
    if normalized_remote != PROJECT_REMOTE_IDENTITY:
        raise ExternalRootError(
            "GitHub remote identity does not match Nishijujuba/video2pdf"
        )
    return _project_marker()


def _load_marker(marker_path: Path) -> dict[str, Any]:
    try:
        value = json.loads(marker_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ExternalRootError(
            f"existing project directory has missing {PROJECT_MARKER_NAME}"
        ) from error
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ExternalRootError("project ownership marker is invalid") from error
    if not isinstance(value, dict):
        raise ExternalRootError("project ownership marker must be a JSON object")
    if set(value) != _MARKER_FIELDS:
        raise ExternalRootError(
            "project ownership marker has missing or unknown fields"
        )
    if not all(
        isinstance(value[field], str)
        for field in (
            "schema_name",
            "schema_version",
            "project_key",
            "repository",
            "remote_identity",
        )
    ):
        raise ExternalRootError("project ownership marker field types are invalid")
    return value


def _validate_project_marker(
    project_root: Path,
    expected_marker: dict[str, object],
) -> None:
    marker_path = assert_path_within(
        project_root,
        project_root / PROJECT_MARKER_NAME,
    )
    if not marker_path.exists() and not marker_path.is_symlink():
        raise ExternalRootError(
            f"existing project directory has missing {PROJECT_MARKER_NAME}"
        )
    _assert_no_reparse_points(marker_path)
    if not marker_path.is_file():
        raise ExternalRootError(
            "project ownership marker must be an existing ordinary file"
        )
    marker = _load_marker(marker_path)
    _assert_no_reparse_points(marker_path)
    if marker != expected_marker:
        raise ExternalRootError("project ownership marker does not match this project")


def _write_marker_exclusively(
    test_root: Path,
    project_root: Path,
    marker: dict[str, object],
) -> None:
    marker_path = project_root / PROJECT_MARKER_NAME
    assert_safe_write_path(test_root, marker_path)
    try:
        with marker_path.open("x", encoding="utf-8", newline="\n") as marker_file:
            json.dump(
                marker,
                marker_file,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            marker_file.write("\n")
            marker_file.flush()
            os.fsync(marker_file.fileno())
    except FileExistsError as error:
        raise ExternalRootError(
            "project ownership marker already exists during exclusive initialization"
        ) from error
    except OSError as error:
        raise ExternalRootError("could not initialize project ownership marker") from error


def ensure_project_root(
    test_root: str | os.PathLike[str],
    remote_identity: str,
) -> Path:
    """Validate ownership or exclusively initialize video2pdf/project.json."""

    root = validate_external_test_root(test_root)
    expected_marker = _expected_marker(remote_identity)
    project_root = assert_path_within(root, root / PROJECT_KEY)

    if project_root.exists() or project_root.is_symlink():
        _assert_no_reparse_points(project_root)
        if not project_root.is_dir():
            raise ExternalRootError("existing project path is not an ordinary directory")
        _validate_project_marker(project_root, expected_marker)
        _assert_no_reparse_points(project_root)
        return project_root

    assert_safe_write_path(root, project_root)
    try:
        project_root.mkdir(exist_ok=False)
    except FileExistsError as error:
        raise ExternalRootError(
            "project directory appeared during exclusive initialization"
        ) from error
    except OSError as error:
        raise ExternalRootError("could not initialize project directory") from error

    _assert_no_reparse_points(project_root)
    _write_marker_exclusively(root, project_root, expected_marker)
    _assert_no_reparse_points(project_root)
    return project_root


def _validate_owned_project_root(project_root: Path) -> None:
    if not project_root.exists() or not project_root.is_dir():
        raise ExternalRootError("owned project root must be an existing directory")
    _assert_no_reparse_points(project_root)
    _validate_project_marker(project_root, _project_marker())


def create_unique_run_directory(
    project_root: str | os.PathLike[str],
    suite_key: str,
    *,
    registered_suite_keys: Collection[str] | None = None,
    timestamp: str | None = None,
    short_run_id: str | None = None,
) -> Path:
    """Create suite/timestamp_id exactly once after containment safety checks."""

    owned_root = _absolute_lexical_path(project_root)
    _validate_owned_project_root(owned_root)
    if not isinstance(suite_key, str) or not _SUITE_KEY_PATTERN.fullmatch(suite_key):
        raise ExternalRootError("suite_key must be a short lowercase filesystem key")
    if suite_key != "all":
        if registered_suite_keys is None:
            raise ExternalRootError(
                "registered_suite_keys is required for a non-all suite"
            )
        if suite_key not in registered_suite_keys:
            raise ExternalRootError(f"suite_key is not registered: {suite_key}")

    run_timestamp = timestamp or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_id = short_run_id or uuid.uuid4().hex[:8]
    if not _TIMESTAMP_PATTERN.fullmatch(run_timestamp):
        raise ExternalRootError("run timestamp must use yyyyMMdd_HHmmss")
    if not _SHORT_RUN_ID_PATTERN.fullmatch(run_id):
        raise ExternalRootError("short_run_id must contain eight lowercase characters")

    suite_root = assert_path_within(owned_root, owned_root / suite_key)
    run_root = assert_path_within(
        owned_root,
        suite_root / f"{run_timestamp}_{run_id}",
    )

    if suite_root.exists() or suite_root.is_symlink():
        _assert_no_reparse_points(suite_root)
        if not suite_root.is_dir():
            raise ExternalRootError("suite path is not an ordinary directory")
    else:
        assert_safe_write_path(owned_root, suite_root)
        try:
            suite_root.mkdir(exist_ok=False)
        except FileExistsError:
            _assert_no_reparse_points(suite_root)
            if not suite_root.is_dir():
                raise ExternalRootError("suite path is not an ordinary directory")
        except OSError as error:
            raise ExternalRootError("could not initialize suite directory") from error

    assert_safe_write_path(owned_root, run_root)
    try:
        run_root.mkdir(exist_ok=False)
    except FileExistsError as error:
        raise ExternalRootError(f"run directory already exists: {run_root}") from error
    except OSError as error:
        raise ExternalRootError("could not create unique run directory") from error
    _assert_no_reparse_points(run_root)
    assert_path_within(owned_root, run_root)
    return run_root
