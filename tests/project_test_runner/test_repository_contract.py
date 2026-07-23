from __future__ import annotations

import ast
import json
from pathlib import Path, PurePosixPath
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEST_ROOT = PROJECT_ROOT / "tests" / "video_workflow"
EXCEPTIONS_PATH = PROJECT_ROOT / "config" / "test-local-write-exceptions.v1.json"


def project_relative_path(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name) and node.id == "PROJECT_ROOT":
        return ""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.replace("\\", "/")
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = project_relative_path(node.left)
        right = project_relative_path(node.right)
        if left is None or right is None:
            return None
        return (PurePosixPath(left) / right).as_posix()
    return None


class RepositoryGeneratedPathContractTests(unittest.TestCase):
    def test_video_workflow_tests_do_not_construct_unregistered_repo_local_roots(
        self,
    ) -> None:
        exceptions = json.loads(EXCEPTIONS_PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            set(exceptions),
            {"schema_name", "schema_version", "exceptions"},
        )
        allowed = {
            (item["test_id"], item["path_prefix"]): item["reason"]
            for item in exceptions["exceptions"]
        }
        self.assertTrue(
            all(test_id and prefix and reason for (test_id, prefix), reason in allowed.items())
        )

        violations: list[str] = []
        used_exceptions: set[tuple[str, str]] = set()
        for source in sorted(TEST_ROOT.glob("test_*.py")):
            source_text = source.read_text(encoding="utf-8")
            tree = ast.parse(source_text, filename=str(source))
            parents = {
                child: parent
                for parent in ast.walk(tree)
                for child in ast.iter_child_nodes(parent)
            }
            for node in ast.walk(tree):
                if not isinstance(node, ast.BinOp) or not isinstance(
                    node.op, ast.Div
                ):
                    continue
                if isinstance(parents.get(node), ast.BinOp):
                    continue
                expression = ast.get_source_segment(source_text, node) or ""
                if "PROJECT_ROOT" not in expression:
                    continue
                if (
                    "待删除" not in expression
                    and "workspace/待删除" not in expression
                ):
                    continue
                owner: ast.AST | None = node
                method = None
                class_node = None
                while owner in parents:
                    owner = parents[owner]
                    if isinstance(
                        owner, (ast.FunctionDef, ast.AsyncFunctionDef)
                    ) and method is None:
                        method = owner
                    if isinstance(owner, ast.ClassDef):
                        class_node = owner
                        break
                test_id = (
                    f"{source.stem}.{class_node.name}.{method.name}"
                    if class_node is not None
                    and method is not None
                    and method.name.startswith("test_")
                    else ""
                )
                relative = project_relative_path(node)
                matching_exceptions = {
                    (registered_id, prefix)
                    for registered_id, prefix in allowed
                    if registered_id == test_id
                    and (
                        (relative is not None and relative.startswith(prefix))
                        or all(
                            component in expression
                            for component in prefix.split("/")
                        )
                    )
                }
                if matching_exceptions:
                    used_exceptions.update(matching_exceptions)
                    continue
                violations.append(
                    f"{source.relative_to(PROJECT_ROOT)}:{node.lineno}"
                )

        self.assertEqual(
            violations,
            [],
            "direct repo-local generated roots require migration through "
            "tests.video_workflow._test_run; path-boundary exceptions require "
            "an exact TestCase.id(), narrow prefix, and reason: "
            + ", ".join(violations),
        )
        self.assertEqual(
            set(allowed),
            used_exceptions,
            "every local-write exception must match one current exact test path",
        )


if __name__ == "__main__":
    unittest.main()
