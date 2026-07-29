from __future__ import annotations

import json
import unittest
from pathlib import Path

import scripts.project_test_discovery as discovery_module
from scripts.project_test_discovery import (
    DiscoveryError,
    discover_tests,
    write_discovery_manifest,
)
from scripts.project_test_results import canonical_json_bytes
from scripts.project_test_registry import load_registry

from tests.project_test_runner.test_registry import (
    fixture_run_dir,
    registry_document,
    write,
)


PASSING_TEST = """\
import unittest

class ExampleTests(unittest.TestCase):
    def test_beta(self):
        pass

    def test_alpha(self):
        pass
"""


class DiscoveryTests(unittest.TestCase):
    def test_discovery_reuses_the_canonical_result_encoder(self) -> None:
        self.assertIs(discovery_module.canonical_json_bytes, canonical_json_bytes)

    def make_repo(self) -> tuple[Path, Path]:
        root = fixture_run_dir("discovery")
        (root / ".git").mkdir()
        for directory in (
            "tests/unit",
            ".agents/skills/demo/scripts",
            ".claude/skills/demo/scripts",
            "scripts",
        ):
            (root / directory).mkdir(parents=True)
        write(root / "tests/unit/test_alpha.py", PASSING_TEST)
        write(
            root / ".agents/skills/demo/scripts/test_skill.py",
            "import unittest\n"
            "class SkillTests(unittest.TestCase):\n"
            "    def test_skill(self): pass\n",
        )
        write(
            root / ".claude/skills/demo/scripts/test_skill.py",
            "raise RuntimeError('mirror must never execute')\n",
        )
        write(
            root / "scripts/test_tool.py",
            "import unittest\n"
            "class ToolTests(unittest.TestCase):\n"
            "    def test_tool(self): pass\n",
        )
        registry_path = root / "config/test-suites.v1.json"
        write(registry_path, json.dumps(registry_document()))
        return root, registry_path

    def test_dynamic_inventory_groups_modules_and_complete_test_ids(self) -> None:
        root, registry_path = self.make_repo()
        manifest = discover_tests(
            load_registry(root, registry_path),
            selected_suite_ids=["unit", "skills"],
            commit="abc123",
        )
        self.assertEqual(manifest["total_count"], 3)
        self.assertEqual(manifest["duplicate_test_ids"], [])
        self.assertEqual(
            [module["source_path"] for module in manifest["modules"]],
            [
                ".agents/skills/demo/scripts/test_skill.py",
                "tests/unit/test_alpha.py",
            ],
        )
        ids = [
            test_id
            for module in manifest["modules"]
            for test_id in module["test_ids"]
        ]
        self.assertEqual(
            sorted(ids),
            [
                "test_alpha.ExampleTests.test_alpha",
                "test_alpha.ExampleTests.test_beta",
                "test_skill.SkillTests.test_skill",
            ],
        )
        self.assertTrue(
            all(len(module["module_key"]) == 12 for module in manifest["modules"])
        )
        self.assertEqual(
            manifest["suite_ids"], ["skills", "unit"]
        )
        self.assertEqual(
            len(manifest["test_id_set_sha256"]), 64
        )

    def test_loader_errors_and_failed_tests_fail_closed(self) -> None:
        root, registry_path = self.make_repo()
        write(
            root / "tests/unit/test_alpha.py",
            "raise RuntimeError('broken import')\n",
        )
        with self.assertRaisesRegex(DiscoveryError, "unittest loader error"):
            discover_tests(load_registry(root, registry_path), commit="abc123")

    def test_duplicate_testcase_ids_fail_closed(self) -> None:
        root, registry_path = self.make_repo()
        duplicate_id_test = """\
import unittest
class DuplicateIdTests(unittest.TestCase):
    def id(self):
        return "shared.test.identity"
    def test_one(self):
        pass
"""
        write(root / "tests/unit/test_alpha.py", duplicate_id_test)
        write(root / "tests/unit/test_second.py", duplicate_id_test)
        with self.assertRaisesRegex(DiscoveryError, "duplicate TestCase.id"):
            discover_tests(load_registry(root, registry_path), commit="abc123")

    def test_manifest_fingerprints_and_order_are_stable(self) -> None:
        root, registry_path = self.make_repo()
        registry = load_registry(root, registry_path)
        first = discover_tests(registry, commit="abc123")
        second = discover_tests(registry, commit="abc123")
        self.assertEqual(first, second)
        self.assertEqual(first["registry_sha256"], registry.fingerprint)
        self.assertEqual(first["schema_version"], 1)
        self.assertEqual(
            first["discovery_arguments"],
            {"suite_ids": ["scripts", "skills", "unit"]},
        )

    def test_manifest_is_exclusive_create_and_never_overwritten(self) -> None:
        root, registry_path = self.make_repo()
        manifest = discover_tests(
            load_registry(root, registry_path), commit="abc123"
        )
        destination = root / "external/discovery.json"
        write_discovery_manifest(destination, manifest)
        original = destination.read_bytes()
        with self.assertRaisesRegex(DiscoveryError, "already exists"):
            write_discovery_manifest(destination, {"forged": True})
        self.assertEqual(destination.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
