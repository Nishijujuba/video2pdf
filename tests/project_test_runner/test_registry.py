from __future__ import annotations

import json
import unittest
from pathlib import Path

from scripts.project_test_registry import RegistryError, load_registry
from tests.project_test_runner import _fixture_root
from tests.project_test_runner._fixture_root import new_fixture_dir

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def write(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def fixture_run_dir(prefix: str) -> Path:
    return new_fixture_dir(prefix)


def registry_document() -> dict:
    return {
        "schema_name": "video2pdf.project-test-suites",
        "schema_version": 1,
        "project": {
            "project_key": "video2pdf",
            "repository": "Nishijujuba/video2pdf",
        },
        "suites": [
            {
                "suite_id": "unit",
                "suite_key": "unit",
                "roots": [{"path": "tests/unit", "pattern": "test_*.py"}],
            },
            {
                "suite_id": "skills",
                "suite_key": "skills",
                "roots": [
                    {"path": ".agents/skills", "pattern": "**/test_*.py"}
                ],
            },
            {
                "suite_id": "scripts",
                "suite_key": "scripts",
                "roots": [{"path": "scripts", "pattern": "test_*.py"}],
            },
        ],
        "mirrors": [
            {
                "authority_path": ".agents/skills",
                "mirror_path": ".claude/skills",
            }
        ],
    }


class RegistryTests(unittest.TestCase):
    def test_generated_fixture_root_obeys_runner_identity_boundary(self) -> None:
        fallback = _fixture_root.fixture_root_from_environment({}, PROJECT_ROOT)
        self.assertEqual(
            fallback,
            PROJECT_ROOT
            / "待删除"
            / "kernel-test-runs"
            / "project-test-runner",
        )

        run_dir = PROJECT_ROOT.resolve()
        environment = {
            _fixture_root.RUN_DIR_ENV: str(run_dir),
            _fixture_root.SUITE_ID_ENV: "project-test-runner",
            _fixture_root.MODULE_KEY_ENV: "0123456789ab",
        }
        with self.assertRaisesRegex(
            _fixture_root.FixtureRootError,
            "ownership",
        ):
            _fixture_root.fixture_root_from_environment(
                environment, PROJECT_ROOT
            )
        self.assertEqual(
            _fixture_root.committed_fixture_root(),
            PROJECT_ROOT / "tests" / "project_test_runner" / "fixtures",
        )
        with self.assertRaises(TypeError):
            _fixture_root.FROZEN_RUN_ENV[_fixture_root.RUN_DIR_ENV] = "changed"
        for invalid_environment in (
            {_fixture_root.RUN_DIR_ENV: str(run_dir)},
            {
                **environment,
                _fixture_root.SUITE_ID_ENV: "video-workflow",
            },
            {
                **environment,
                _fixture_root.MODULE_KEY_ENV: "../escape",
            },
        ):
            with self.subTest(invalid_environment=invalid_environment):
                with self.assertRaises(_fixture_root.FixtureRootError):
                    _fixture_root.fixture_root_from_environment(
                        invalid_environment, PROJECT_ROOT
                    )

    def make_repo(self) -> tuple[Path, Path, dict]:
        root = fixture_run_dir("registry")
        for directory in (
            "tests/unit",
            ".agents/skills/demo/scripts",
            ".claude/skills/demo/scripts",
            "scripts",
        ):
            (root / directory).mkdir(parents=True)
        write(root / "tests/unit/test_alpha.py")
        write(root / ".agents/skills/demo/scripts/test_skill.py")
        write(root / ".claude/skills/demo/scripts/test_skill.py")
        write(root / "scripts/test_tool.py")
        path = root / "config/test-suites.v1.json"
        document = registry_document()
        write(path, json.dumps(document))
        return root, path, document

    def assert_invalid(self, mutate, message: str) -> None:
        root, path, document = self.make_repo()
        mutate(document)
        write(path, json.dumps(document))
        with self.assertRaisesRegex(RegistryError, message):
            load_registry(root, path)

    def test_valid_registry_is_strict_and_selects_suites(self) -> None:
        root, path, _ = self.make_repo()
        registry = load_registry(root, path)
        self.assertEqual(
            [suite.suite_id for suite in registry.select_suites(None)],
            ["scripts", "skills", "unit"],
        )
        self.assertEqual(
            [suite.suite_id for suite in registry.select_suites(["unit"])],
            ["unit"],
        )
        with self.assertRaisesRegex(RegistryError, "unknown suite"):
            registry.select_suites(["missing"])
        self.assertEqual(
            load_registry(root, Path("config/test-suites.v1.json")).fingerprint,
            registry.fingerprint,
        )

    def test_unknown_fields_fail_at_every_registry_level(self) -> None:
        cases = [
            (lambda d: d.__setitem__("extra", True), "unknown field"),
            (
                lambda d: d["project"].__setitem__("extra", True),
                "unknown field",
            ),
            (
                lambda d: d["suites"][0].__setitem__("extra", True),
                "unknown field",
            ),
            (
                lambda d: d["suites"][0]["roots"][0].__setitem__(
                    "extra", True
                ),
                "unknown field",
            ),
            (
                lambda d: d["mirrors"][0].__setitem__("extra", True),
                "unknown field",
            ),
        ]
        for mutate, message in cases:
            with self.subTest(mutate=mutate):
                self.assert_invalid(mutate, message)

    def test_duplicate_ids_keys_and_roots_fail_closed(self) -> None:
        self.assert_invalid(
            lambda d: d["suites"][1].__setitem__("suite_id", "unit"),
            "duplicate suite_id",
        )
        self.assert_invalid(
            lambda d: d["suites"][1].__setitem__("suite_key", "unit"),
            "duplicate suite_key",
        )
        self.assert_invalid(
            lambda d: d["suites"][1]["roots"][0].__setitem__(
                "path", "tests/unit"
            ),
            "duplicate authority root",
        )
        self.assert_invalid(
            lambda d: d["suites"][0].__setitem__("suite_key", "../escape"),
            "filesystem-safe identifier",
        )

    def test_each_authority_root_must_match_tests(self) -> None:
        root, path, document = self.make_repo()
        (root / "tests/unit/test_alpha.py").rename(
            root / "tests/unit/alpha.py"
        )
        write(path, json.dumps(document))
        with self.assertRaisesRegex(
            RegistryError, "authority root matches no test files"
        ):
            load_registry(root, path)

    def test_overlapping_roots_missing_paths_and_unsafe_paths_fail(self) -> None:
        root, path, document = self.make_repo()
        (root / "tests/unit/sub").mkdir()
        document["suites"].append(
            {
                "suite_id": "nested",
                "suite_key": "nested",
                "roots": [
                    {"path": "tests/unit/sub", "pattern": "test_*.py"}
                ],
            }
        )
        write(path, json.dumps(document))
        with self.assertRaisesRegex(RegistryError, "overlapping authority roots"):
            load_registry(root, path)

        self.assert_invalid(
            lambda d: d["suites"][0]["roots"][0].__setitem__(
                "path", "tests/missing"
            ),
            "does not exist",
        )
        self.assert_invalid(
            lambda d: d["suites"][0]["roots"][0].__setitem__(
                "path", "../outside"
            ),
            "repository-relative",
        )

    def test_invalid_mirror_declarations_fail_closed(self) -> None:
        self.assert_invalid(
            lambda d: d["mirrors"][0].__setitem__(
                "authority_path", ".claude/skills"
            ),
            "mirror authority must identify an authority root",
        )
        self.assert_invalid(
            lambda d: d["mirrors"][0].__setitem__(
                "mirror_path", ".agents/skills"
            ),
            "mirror path overlaps an authority root",
        )
        self.assert_invalid(
            lambda d: d["mirrors"][0].__setitem__(
                "mirror_path", ".claude/missing"
            ),
            "does not exist",
        )

    def test_unregistered_authoritative_tests_fail_and_mirrors_are_excluded(
        self,
    ) -> None:
        root, path, _ = self.make_repo()
        write(root / "tests/unregistered/test_lost.py")
        with self.assertRaisesRegex(
            RegistryError, "unregistered authoritative test file"
        ):
            load_registry(root, path)

        # The mirror has a test that is intentionally outside the executable set.
        write(root / ".claude/skills/demo/scripts/test_mirror_only.py")
        (root / "tests/unregistered/test_lost.py").rename(
            root / "tests/unregistered/lost.py"
        )
        registry = load_registry(root, path)
        candidates = registry.registered_test_files()
        self.assertNotIn(
            ".claude/skills/demo/scripts/test_mirror_only.py", candidates
        )

    def test_repository_registry_covers_every_authoritative_test_file(
        self,
    ) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        registry = load_registry(
            repo_root, repo_root / "config/test-suites.v1.json"
        )
        self.assertIn(
            "tests/project_test_runner/test_registry.py",
            registry.registered_test_files(),
        )


if __name__ == "__main__":
    unittest.main()
