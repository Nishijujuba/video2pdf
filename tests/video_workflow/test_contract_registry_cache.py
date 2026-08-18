from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import sys
import unittest
import uuid
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from tests.video_workflow._test_run import module_test_root
from video2pdf_workflow_kernel import contracts
from video2pdf_workflow_kernel.contracts import ContractRegistry
from video2pdf_workflow_kernel.errors import ContractError


class ContractRegistryPreparedCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        contracts._clear_prepared_registry_cache_for_tests()
        self.schema_count = len(
            json.loads(
                (
                    PROJECT_ROOT
                    / "schemas/video-workflow/registry.v1.json"
                ).read_text(encoding="utf-8")
            )["contracts"]
        )
        self.assertEqual(self.schema_count, 61)

    def tearDown(self) -> None:
        contracts._clear_prepared_registry_cache_for_tests()

    def make_project(self, label: str = "registry-cache") -> Path:
        root = (
            module_test_root(PROJECT_ROOT)
            / f"{label}-{uuid.uuid4().hex[:10]}"
        )
        root.mkdir(parents=True, exist_ok=False)
        shutil.copytree(
            PROJECT_ROOT / "schemas/video-workflow",
            root / "schemas/video-workflow",
        )
        shutil.copytree(
            PROJECT_ROOT / "requirements",
            root / "requirements",
        )
        shutil.copytree(
            PROJECT_ROOT / "tests/video_workflow/fixtures/contracts",
            root / "tests/video_workflow/fixtures/contracts",
        )
        return root

    def check_schema_calls(self):
        original = contracts.Draft202012Validator.check_schema
        return mock.patch.object(
            contracts.Draft202012Validator,
            "check_schema",
            side_effect=lambda schema: original(schema),
        )

    def prepare(
        self, root: Path, registry_path: Path | None = None
    ) -> ContractRegistry:
        registry = ContractRegistry(root, registry_path)
        instance = json.loads(
            (
                root
                / "tests/video_workflow/fixtures/contracts/workflow-result.valid.json"
            ).read_text(encoding="utf-8")
        )
        registry.validate("workflow-result", instance)
        return registry

    def test_same_root_reuses_prepared_schemas_without_sharing_instances(
        self,
    ) -> None:
        root = self.make_project()
        with self.check_schema_calls() as check_schema:
            first = self.prepare(root)
            self.assertEqual(check_schema.call_count, self.schema_count)

            second = self.prepare(root)
            self.assertEqual(check_schema.call_count, self.schema_count)

        self.assertIsNot(first.schemas, second.schemas)
        self.assertIsNot(first._registry, second._registry)
        identity = next(iter(first.schemas))
        self.assertIsNot(first.schemas[identity], second.schemas[identity])

    def test_registry_authority_mutation_is_not_hidden_by_cache(self) -> None:
        root = self.make_project()
        self.prepare(root)
        canonical_path = root / contracts.REGISTRY_RELATIVE_PATH
        alternate_path = root / "alternate-registry.json"
        alternate_path.write_bytes(canonical_path.read_bytes())
        mutated = json.loads(alternate_path.read_text(encoding="utf-8"))
        mutated["kernel_version"] = "changed"
        alternate_path.write_text(json.dumps(mutated), encoding="utf-8")

        with self.assertRaisesRegex(
            ContractError,
            "alternate registry authority metadata differs",
        ):
            ContractRegistry(root, alternate_path)

    def test_schema_change_rebuilds_and_invalid_schema_is_never_cached(
        self,
    ) -> None:
        root = self.make_project()
        self.prepare(root)
        schema_path = root / "schemas/video-workflow/v1/common.v1.schema.json"
        original = json.loads(schema_path.read_text(encoding="utf-8"))
        invalid = json.loads(json.dumps(original))
        invalid["type"] = 7
        schema_path.write_text(json.dumps(invalid), encoding="utf-8")

        with self.check_schema_calls() as check_schema:
            with self.assertRaisesRegex(
                ContractError, "invalid Draft 2020-12"
            ):
                self.prepare(root)
            first_failure_count = check_schema.call_count
            self.assertGreater(first_failure_count, 0)
            with self.assertRaisesRegex(
                ContractError, "invalid Draft 2020-12"
            ):
                self.prepare(root)
            self.assertEqual(
                check_schema.call_count,
                first_failure_count * 2,
            )

            repaired = json.loads(json.dumps(original))
            repaired["description"] = "cache invalidation proof"
            schema_path.write_text(json.dumps(repaired), encoding="utf-8")
            self.prepare(root)
            self.assertEqual(
                check_schema.call_count,
                first_failure_count * 2 + self.schema_count,
            )

    def test_same_instance_check_detects_runtime_input_drift(self) -> None:
        root = self.make_project()
        registry = self.prepare(root)
        (root / contracts.RUNTIME_INPUT).write_text(
            "jsonschema==0.0.0\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ContractError, "runtime input"):
            registry.check()

    def test_same_instance_check_rebuilds_after_schema_drift(self) -> None:
        root = self.make_project()
        registry = self.prepare(root)
        schema_path = root / "schemas/video-workflow/v1/common.v1.schema.json"
        changed = json.loads(schema_path.read_text(encoding="utf-8"))
        changed["description"] = "same-instance schema drift"
        schema_path.write_text(json.dumps(changed), encoding="utf-8")

        with self.check_schema_calls() as check_schema:
            registry.check()

        self.assertEqual(check_schema.call_count, self.schema_count)
        self.assertEqual(
            registry.schemas[("common-definitions", "1.0.0")]["description"],
            "same-instance schema drift",
        )

    def test_same_instance_check_rejects_registry_authority_drift(self) -> None:
        root = self.make_project()
        registry = self.prepare(root)
        registry_path = root / contracts.REGISTRY_RELATIVE_PATH
        changed = json.loads(registry_path.read_text(encoding="utf-8"))
        changed["kernel_version"] = "changed"
        registry_path.write_text(json.dumps(changed), encoding="utf-8")

        with self.assertRaisesRegex(
            ContractError,
            "alternate registry authority metadata differs",
        ):
            registry.check()

    def test_same_instance_check_rejects_new_schema_inventory_drift(self) -> None:
        root = self.make_project()
        registry = self.prepare(root)
        source = root / "schemas/video-workflow/v1/common.v1.schema.json"
        (source.parent / "same-instance-unregistered.schema.json").write_bytes(
            source.read_bytes()
        )

        with self.assertRaisesRegex(ContractError, "completeness mismatch"):
            registry.check()

    def test_runtime_lock_change_during_prepare_is_not_cached(self) -> None:
        root = self.make_project()
        lock_path = root / contracts.RUNTIME_LOCK
        original_loads = contracts.tomllib.loads
        mutated = False

        def mutate_after_parse(raw: str):
            nonlocal mutated
            result = original_loads(raw)
            if not mutated and "lock-version" in raw:
                mutated = True
                lock_path.write_text(
                    raw.replace('name = "jsonschema"', 'name = "jsonschemb"', 1),
                    encoding="utf-8",
                )
            return result

        with mock.patch.object(
            contracts.tomllib,
            "loads",
            side_effect=mutate_after_parse,
        ):
            with self.assertRaisesRegex(
                ContractError,
                "changed during registry preparation",
            ):
                self.prepare(root)

        self.assertEqual(len(contracts._PREPARED_REGISTRY_CACHE), 0)

    def test_new_unregistered_schema_fails_completeness(self) -> None:
        root = self.make_project()
        self.prepare(root)
        source = root / "schemas/video-workflow/v1/common.v1.schema.json"
        (source.parent / "unregistered.schema.json").write_bytes(
            source.read_bytes()
        )

        with self.assertRaisesRegex(ContractError, "completeness mismatch"):
            self.prepare(root)

    def test_alternate_registry_path_and_project_root_are_cache_isolated(
        self,
    ) -> None:
        first_root = self.make_project("registry-cache-first")
        second_root = self.make_project("registry-cache-second")
        alternate = first_root / "alternate-registry.json"
        alternate.write_bytes(
            (first_root / contracts.REGISTRY_RELATIVE_PATH).read_bytes()
        )

        with self.check_schema_calls() as check_schema:
            self.prepare(first_root)
            self.prepare(first_root, alternate)
            self.prepare(second_root)
        self.assertEqual(check_schema.call_count, self.schema_count * 3)

    def test_concurrent_construction_builds_one_prepared_snapshot(self) -> None:
        root = self.make_project()

        def load() -> None:
            self.prepare(root)

        with self.check_schema_calls() as check_schema:
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(lambda _: load(), range(8)))
        self.assertEqual(check_schema.call_count, self.schema_count)

    def test_clear_hook_and_bounded_lru_force_rebuilds(self) -> None:
        root = self.make_project("registry-cache-clear")
        with self.check_schema_calls() as check_schema:
            self.prepare(root)
            contracts._clear_prepared_registry_cache_for_tests()
            self.prepare(root)
        self.assertEqual(check_schema.call_count, self.schema_count * 2)

        contracts._clear_prepared_registry_cache_for_tests()
        roots = [
            self.make_project(f"registry-cache-lru-{index}")
            for index in range(9)
        ]
        with self.check_schema_calls() as check_schema:
            for candidate in roots:
                self.prepare(candidate)
            self.prepare(roots[0])
        self.assertEqual(check_schema.call_count, self.schema_count * 10)

    def test_validate_does_not_cache_instance_decisions(self) -> None:
        root = self.make_project()
        registry = ContractRegistry(root)
        valid = json.loads(
            (
                root
                / "tests/video_workflow/fixtures/contracts/workflow-result.valid.json"
            ).read_text(encoding="utf-8")
        )
        registry.validate("workflow-result", valid)
        valid["schema_version"] = "missing"
        with self.assertRaisesRegex(ContractError, "unknown workflow-result"):
            registry.validate("workflow-result", valid)

    def test_prepared_snapshot_retains_only_reusable_schema_bytes(self) -> None:
        root = self.make_project()
        self.prepare(root)

        snapshot = next(iter(contracts._PREPARED_REGISTRY_CACHE.values()))

        self.assertEqual(tuple(snapshot.__dataclass_fields__), ("schemas",))


if __name__ == "__main__":
    unittest.main()
