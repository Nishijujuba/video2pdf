from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest import mock
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
TEMP_PARENT = (
    PROJECT_ROOT / "tests/project_test_runner/fixtures/external_root"
).resolve()
sys.path.insert(0, str(SCRIPTS_ROOT))

import project_test_external_root as external_root


EXPECTED_REMOTE = "github.com/Nishijujuba/video2pdf"


@contextmanager
def temporary_root():
    yield tempfile.mkdtemp(dir=TEMP_PARENT)


class ExternalRootValidationTests(unittest.TestCase):
    def test_accepts_an_existing_absolute_ordinary_directory(self) -> None:
        with temporary_root() as temporary_directory:
            root = Path(temporary_directory).resolve()

            self.assertEqual(external_root.validate_external_test_root(root), root)

    def test_rejects_relative_unc_and_device_paths(self) -> None:
        rejected = (
            Path("relative") / "tests",
            r"\\server\share\tests",
            r"\\?\D:\tests",
            r"\\.\D:\tests",
        )

        for candidate in rejected:
            with self.subTest(candidate=str(candidate)):
                with self.assertRaises(external_root.ExternalRootError):
                    external_root.validate_external_test_root(candidate)

    def test_rejects_an_existing_non_directory(self) -> None:
        with temporary_root() as temporary_directory:
            ordinary_file = Path(temporary_directory) / "root.txt"
            ordinary_file.write_text("not a directory", encoding="utf-8")

            with self.assertRaisesRegex(
                external_root.ExternalRootError,
                "existing ordinary directory",
            ):
                external_root.validate_external_test_root(ordinary_file)

    @unittest.skipUnless(os.name == "nt", "Windows reparse behavior")
    def test_rejects_a_symlink_at_any_ancestor(self) -> None:
        with temporary_root() as temporary_directory:
            fixture_root = Path(temporary_directory)
            target = fixture_root / "ordinary"
            target.mkdir()
            link = fixture_root / "linked"
            try:
                link.symlink_to(target, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"directory symlink unavailable: {error}")

            with self.assertRaisesRegex(
                external_root.ExternalRootError,
                "reparse",
            ):
                external_root.validate_external_test_root(link)

    @unittest.skipUnless(os.name == "nt", "Windows junction behavior")
    def test_rejects_a_junction_at_any_ancestor(self) -> None:
        with temporary_root() as temporary_directory:
            fixture_root = Path(temporary_directory)
            target = fixture_root / "ordinary"
            target.mkdir()
            junction = fixture_root / "junction"
            created = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(junction), str(target)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            if created.returncode != 0:
                self.skipTest(f"junction unavailable: {created.stderr}")

            with self.assertRaisesRegex(
                external_root.ExternalRootError,
                "reparse",
            ):
                external_root.validate_external_test_root(junction)

    def test_rejects_a_reparse_point_reported_by_file_attributes(self) -> None:
        with temporary_root() as temporary_directory:
            root = Path(temporary_directory).resolve()
            with (
                mock.patch.object(
                    external_root.os,
                    "lstat",
                    return_value=SimpleNamespace(st_file_attributes=0x400),
                ),
                mock.patch.object(Path, "is_symlink", return_value=False),
            ):
                self.assertTrue(external_root._is_reparse_point(root))


class RemoteIdentityTests(unittest.TestCase):
    def test_normalizes_supported_github_remote_forms(self) -> None:
        remotes = (
            "https://github.com/Nishijujuba/video2pdf.git",
            "ssh://git@github.com/Nishijujuba/video2pdf.git",
            "git@github.com:Nishijujuba/video2pdf.git",
            "https://GITHUB.COM/nishijujuba/VIDEO2PDF/",
        )

        for remote in remotes:
            with self.subTest(remote=remote):
                self.assertEqual(
                    external_root.normalize_github_remote_identity(remote),
                    EXPECTED_REMOTE,
                )

    def test_rejects_non_github_or_ambiguous_remote_forms(self) -> None:
        remotes = (
            "https://example.com/Nishijujuba/video2pdf.git",
            "https://github.com/Nishijujuba/video2pdf/extra",
            "https://user:secret@github.com/Nishijujuba/video2pdf.git",
            "https://github.com/Nishijujuba/video2pdf.git?token=secret",
            "github.com/Nishijujuba/video2pdf",
        )

        for remote in remotes:
            with self.subTest(remote=remote):
                with self.assertRaises(external_root.ExternalRootError):
                    external_root.normalize_github_remote_identity(remote)


class ProjectOwnershipTests(unittest.TestCase):
    def marker(self) -> dict[str, object]:
        return {
            "schema_name": external_root.PROJECT_MARKER_SCHEMA_NAME,
            "schema_version": external_root.PROJECT_MARKER_SCHEMA_VERSION,
            "project_key": external_root.PROJECT_KEY,
            "repository": external_root.PROJECT_REPOSITORY,
            "remote_identity": EXPECTED_REMOTE,
        }

    def test_initializes_project_directory_and_marker_exclusively(self) -> None:
        with temporary_root() as temporary_directory:
            test_root = Path(temporary_directory).resolve()

            project_root = external_root.ensure_project_root(
                test_root,
                "git@github.com:Nishijujuba/video2pdf.git",
            )

            self.assertEqual(project_root, test_root / external_root.PROJECT_KEY)
            self.assertEqual(
                json.loads((project_root / "project.json").read_text("utf-8")),
                self.marker(),
            )
            marker_bytes = (project_root / "project.json").read_bytes()
            marker_mtime = (project_root / "project.json").stat().st_mtime_ns

            external_root.ensure_project_root(
                test_root,
                "https://github.com/Nishijujuba/video2pdf",
            )

            self.assertEqual((project_root / "project.json").read_bytes(), marker_bytes)
            self.assertEqual(
                (project_root / "project.json").stat().st_mtime_ns,
                marker_mtime,
            )

    def test_existing_project_directory_with_missing_marker_fails_closed(self) -> None:
        with temporary_root() as temporary_directory:
            test_root = Path(temporary_directory).resolve()
            (test_root / external_root.PROJECT_KEY).mkdir()

            with self.assertRaisesRegex(
                external_root.ExternalRootError,
                "missing",
            ):
                external_root.ensure_project_root(
                    test_root,
                    "https://github.com/Nishijujuba/video2pdf",
                )

    def test_invalid_mismatched_and_unknown_marker_fields_fail_closed(self) -> None:
        variants = (
            {},
            {**self.marker(), "project_key": "another-project"},
            {**self.marker(), "remote_identity": "github.com/other/repository"},
            {**self.marker(), "unknown": True},
        )

        for marker in variants:
            with self.subTest(marker=marker):
                with temporary_root() as temporary_directory:
                    test_root = Path(temporary_directory).resolve()
                    project_root = test_root / external_root.PROJECT_KEY
                    project_root.mkdir()
                    (project_root / "project.json").write_text(
                        json.dumps(marker),
                        encoding="utf-8",
                    )

                    with self.assertRaises(external_root.ExternalRootError):
                        external_root.ensure_project_root(
                            test_root,
                            "https://github.com/Nishijujuba/video2pdf",
                        )

    def test_malformed_marker_fails_closed(self) -> None:
        with temporary_root() as temporary_directory:
            test_root = Path(temporary_directory).resolve()
            project_root = test_root / external_root.PROJECT_KEY
            project_root.mkdir()
            (project_root / "project.json").write_text("{", encoding="utf-8")

            with self.assertRaisesRegex(
                external_root.ExternalRootError,
                "invalid",
            ):
                external_root.ensure_project_root(
                    test_root,
                    "https://github.com/Nishijujuba/video2pdf",
                )

    def test_marker_symlink_is_rejected_before_reading(self) -> None:
        with temporary_root() as temporary_directory:
            test_root = Path(temporary_directory).resolve()
            project_root = test_root / external_root.PROJECT_KEY
            project_root.mkdir()
            outside_marker = test_root / "outside-project.json"
            outside_marker.write_text(
                json.dumps(self.marker()),
                encoding="utf-8",
            )
            try:
                (project_root / "project.json").symlink_to(outside_marker)
            except OSError as error:
                self.skipTest(f"file symlink unavailable: {error}")

            with self.assertRaisesRegex(
                external_root.ExternalRootError,
                "reparse",
            ):
                external_root.ensure_project_root(
                    test_root,
                    "https://github.com/Nishijujuba/video2pdf",
                )

    def test_marker_is_rechecked_immediately_before_initial_write(self) -> None:
        with temporary_root() as temporary_directory:
            test_root = Path(temporary_directory).resolve()
            original_check = external_root.assert_safe_write_path

            def change_before_marker_write(base: Path, candidate: Path) -> Path:
                if Path(candidate).name == "project.json":
                    raise external_root.ExternalRootError("simulated reparse race")
                return original_check(base, candidate)

            with mock.patch.object(
                external_root,
                "assert_safe_write_path",
                side_effect=change_before_marker_write,
            ):
                with self.assertRaisesRegex(
                    external_root.ExternalRootError,
                    "simulated reparse race",
                ):
                    external_root.ensure_project_root(
                        test_root,
                        "https://github.com/Nishijujuba/video2pdf",
                    )

            self.assertTrue((test_root / external_root.PROJECT_KEY).is_dir())
            self.assertFalse(
                (test_root / external_root.PROJECT_KEY / "project.json").exists()
            )


class RunDirectoryTests(unittest.TestCase):
    def create_project_root(self, test_root: Path) -> Path:
        return external_root.ensure_project_root(
            test_root,
            "https://github.com/Nishijujuba/video2pdf",
        )

    def test_creates_a_unique_run_directory_without_reuse(self) -> None:
        with temporary_root() as temporary_directory:
            test_root = Path(temporary_directory).resolve()
            project_root = self.create_project_root(test_root)

            run_root = external_root.create_unique_run_directory(
                project_root,
                "video-workflow",
                registered_suite_keys={"video-workflow"},
                timestamp="20260723_120000",
                short_run_id="abc12345",
            )
            self.assertEqual(
                run_root,
                project_root / "video-workflow" / "20260723_120000_abc12345",
            )

            with self.assertRaisesRegex(
                external_root.ExternalRootError,
                "already exists",
            ):
                external_root.create_unique_run_directory(
                    project_root,
                    "video-workflow",
                    registered_suite_keys={"video-workflow"},
                    timestamp="20260723_120000",
                    short_run_id="abc12345",
                )

    def test_default_run_identity_uses_utc_and_an_eight_hex_id(self) -> None:
        with temporary_root() as temporary_directory:
            test_root = Path(temporary_directory).resolve()
            project_root = self.create_project_root(test_root)
            fixed_time = datetime(2026, 7, 23, 4, 5, 6, tzinfo=timezone.utc)

            with (
                mock.patch.object(external_root, "datetime") as datetime_mock,
                mock.patch.object(external_root.uuid, "uuid4") as uuid_mock,
            ):
                datetime_mock.now.return_value = fixed_time
                uuid_mock.return_value.hex = "abcdef0123456789"
                run_root = external_root.create_unique_run_directory(
                    project_root,
                    "all",
                )

            datetime_mock.now.assert_called_once_with(timezone.utc)
            self.assertEqual(run_root.name, "20260723_040506_abcdef01")

    def test_rejects_suite_keys_that_escape_project_containment(self) -> None:
        with temporary_root() as temporary_directory:
            test_root = Path(temporary_directory).resolve()
            project_root = self.create_project_root(test_root)

            for suite_key in ("../escape", r"..\escape", "nested/suite", ""):
                with self.subTest(suite_key=suite_key):
                    with self.assertRaises(external_root.ExternalRootError):
                        external_root.create_unique_run_directory(
                            project_root,
                            suite_key,
                            registered_suite_keys={"video-workflow"},
                            timestamp="20260723_120000",
                            short_run_id="abc12345",
                        )

    def test_rejects_a_well_formed_but_unregistered_suite_key(self) -> None:
        with temporary_root() as temporary_directory:
            test_root = Path(temporary_directory).resolve()
            project_root = self.create_project_root(test_root)

            with self.assertRaisesRegex(
                external_root.ExternalRootError,
                "not registered",
            ):
                external_root.create_unique_run_directory(
                    project_root,
                    "bogus-suite",
                    registered_suite_keys={"video-workflow"},
                    timestamp="20260723_120000",
                    short_run_id="abc12345",
                )

    def test_rechecks_safety_immediately_before_run_directory_write(self) -> None:
        with temporary_root() as temporary_directory:
            test_root = Path(temporary_directory).resolve()
            project_root = self.create_project_root(test_root)
            original_check = external_root.assert_safe_write_path

            def change_before_run_mkdir(base: Path, candidate: Path) -> Path:
                if Path(candidate).name == "20260723_120000_abc12345":
                    raise external_root.ExternalRootError("simulated junction race")
                return original_check(base, candidate)

            with mock.patch.object(
                external_root,
                "assert_safe_write_path",
                side_effect=change_before_run_mkdir,
            ):
                with self.assertRaisesRegex(
                    external_root.ExternalRootError,
                    "simulated junction race",
                ):
                    external_root.create_unique_run_directory(
                        project_root,
                        "video-workflow",
                        registered_suite_keys={"video-workflow"},
                        timestamp="20260723_120000",
                        short_run_id="abc12345",
                    )

            self.assertTrue((project_root / "video-workflow").is_dir())
            self.assertFalse(
                (
                    project_root
                    / "video-workflow"
                    / "20260723_120000_abc12345"
                ).exists()
            )


if __name__ == "__main__":
    unittest.main()
