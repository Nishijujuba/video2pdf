from __future__ import annotations

import ast
from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEST_ROOT = PROJECT_ROOT / "tests" / "video_workflow"
EXCEPTIONS_PATH = PROJECT_ROOT / "config" / "test-local-write-exceptions.v1.json"


@dataclass(frozen=True)
class PathFact:
    rooted_in_project: bool
    parts: tuple[str | None, ...]

    @property
    def relative(self) -> str | None:
        if not self.rooted_in_project or any(part is None for part in self.parts):
            return None
        return PurePosixPath(*(part for part in self.parts if part)).as_posix()

    @property
    def known_prefix(self) -> str | None:
        if not self.rooted_in_project:
            return None
        known: list[str] = []
        for part in self.parts:
            if part is None:
                break
            if part:
                known.append(part)
        return PurePosixPath(*known).as_posix() if known else None


_PATH_MUTATORS = {
    "mkdir",
    "touch",
    "write_bytes",
    "write_text",
    "unlink",
    "rmdir",
}
_TWO_PATH_MUTATORS = {
    "copy",
    "copy2",
    "copyfile",
    "copytree",
    "move",
    "rename",
    "replace",
}
_FUNCTION_PATH_MUTATORS = {
    "makedirs",
    "mkdir",
    "remove",
    "removedirs",
    "rmdir",
    "rmtree",
    "unlink",
}
_TEMPORARY_CREATORS = {
    "mkdtemp",
    "mkstemp",
    "NamedTemporaryFile",
    "TemporaryDirectory",
}


def _target_key(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        owner = _target_key(node.value)
        if owner is not None:
            return f"{owner}.{node.attr}"
    return None


class _LocalWriteAnalyzer(ast.NodeVisitor):
    def __init__(
        self,
        source_text: str,
        source: Path,
        allowed: dict[tuple[str, str], str],
    ) -> None:
        self.source_text = source_text
        self.source = source
        self.allowed = allowed
        self.used: set[tuple[str, str]] = set()
        self.violations: list[str] = []
        self.environment: dict[str, PathFact] = {
            "PROJECT_ROOT": PathFact(True, ()),
        }
        self.test_id = ""
        self.helpers: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
        self.active_helpers: set[str] = set()

    def _literal_parts(self, node: ast.AST) -> tuple[str | None, ...]:
        fact = self._fact(node)
        if fact is not None and not fact.rooted_in_project:
            return fact.parts
        return (None,)

    def _fact(self, node: ast.AST) -> PathFact | None:
        key = _target_key(node)
        if key is not None and key in self.environment:
            return self.environment[key]
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return PathFact(
                False,
                tuple(
                    part
                    for part in node.value.replace("\\", "/").split("/")
                    if part and part != "."
                ),
            )
        if isinstance(node, ast.JoinedStr):
            parts: list[str] = []
            for value in node.values:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    parts.append(value.value)
                    continue
                value_node = value.value if isinstance(value, ast.FormattedValue) else value
                value_fact = self._fact(value_node)
                if (
                    value_fact is None
                    or value_fact.rooted_in_project
                    or len(value_fact.parts) != 1
                    or value_fact.parts[0] is None
                ):
                    return PathFact(False, (None,))
                parts.append(value_fact.parts[0])
            return self._fact(ast.Constant(value="".join(parts)))
        if isinstance(node, ast.Subscript):
            base = self._fact(node.value)
            if (
                base is not None
                and not base.rooted_in_project
                and len(base.parts) == 1
                and base.parts[0] is not None
                and isinstance(node.slice, ast.Slice)
            ):
                lower = (
                    node.slice.lower.value
                    if isinstance(node.slice.lower, ast.Constant)
                    and isinstance(node.slice.lower.value, int)
                    else None
                )
                upper = (
                    node.slice.upper.value
                    if isinstance(node.slice.upper, ast.Constant)
                    and isinstance(node.slice.upper.value, int)
                    else None
                )
                if node.slice.step is None:
                    return PathFact(False, (base.parts[0][lower:upper],))
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            left = self._fact(node.left)
            if left is None:
                return None
            return PathFact(
                left.rooted_in_project,
                left.parts + self._literal_parts(node.right),
            )
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            base = self._fact(node.func.value)
            if base is not None and node.func.attr == "joinpath":
                parts = tuple(
                    part
                    for argument in node.args
                    for part in self._literal_parts(argument)
                )
                return PathFact(base.rooted_in_project, base.parts + parts)
            if base is not None and node.func.attr in {"absolute", "resolve"}:
                return base
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"Path", "PurePath", "PurePosixPath", "PureWindowsPath"}
            and node.args
        ):
            return self._fact(node.args[0])
        if isinstance(node, ast.Attribute):
            base = self._fact(node.value)
            if base is not None and node.attr == "parent":
                return PathFact(base.rooted_in_project, base.parts[:-1])
        return None

    def _record(self, node: ast.Call, fact: PathFact | None) -> None:
        if fact is None or not fact.rooted_in_project:
            return
        relative = fact.relative
        known_prefix = fact.known_prefix
        matches = {
            key
            for key in self.allowed
            if key[0] == self.test_id
            and (
                (
                    relative is not None
                    and (
                        relative == key[1]
                        or relative.startswith(f"{key[1]}/")
                    )
                )
                or (
                    relative is None
                    and known_prefix is not None
                    and (
                        known_prefix == key[1]
                        or known_prefix.startswith(f"{key[1]}/")
                    )
                )
            )
        }
        if matches:
            self.used.update(matches)
            return
        expression = ast.get_source_segment(self.source_text, node) or "<call>"
        self.violations.append(
            f"{self.source.as_posix()}:{node.lineno}:{self.test_id}:{expression}"
        )

    def visit_Assign(self, node: ast.Assign) -> None:
        fact = self._fact(node.value)
        if fact is not None:
            for target in node.targets:
                key = _target_key(target)
                if key is not None:
                    self.environment[key] = fact
        self.visit(node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            fact = self._fact(node.value)
            key = _target_key(node.target)
            if fact is not None and key is not None:
                self.environment[key] = fact
            self.visit(node.value)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if not node.name.startswith("test_") and not self.test_id:
            self.helpers[node.name] = node
            return
        previous_environment = self.environment
        previous_test_id = self.test_id
        self.environment = dict(previous_environment)
        if node.name.startswith("test_"):
            class_name = getattr(node, "_contract_class_name", None)
            self.test_id = (
                f"{self.source.stem}.{class_name}.{node.name}"
                if class_name is not None
                else f"{self.source.stem}.{node.name}"
            )
        for statement in node.body:
            self.visit(statement)
        self.environment = previous_environment
        self.test_id = previous_test_id

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for statement in node.body:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                setattr(statement, "_contract_class_name", node.name)
            self.visit(statement)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute):
            method = node.func.attr
            if method in _PATH_MUTATORS:
                self._record(node, self._fact(node.func.value))
            elif method == "open":
                mode_node = (
                    node.args[0]
                    if node.args
                    else next(
                        (
                            keyword.value
                            for keyword in node.keywords
                            if keyword.arg == "mode"
                        ),
                        None,
                    )
                )
                mode = (
                    mode_node.value
                    if isinstance(mode_node, ast.Constant)
                    and isinstance(mode_node.value, str)
                    else "r"
                    if mode_node is None
                    else None
                )
                if mode is None or any(flag in mode for flag in "wax+"):
                    self._record(node, self._fact(node.func.value))
            elif method in {"rename", "replace"} and node.args:
                self._record(node, self._fact(node.func.value))
                self._record(node, self._fact(node.args[0]))
            elif method in _TWO_PATH_MUTATORS and len(node.args) >= 2:
                self._record(node, self._fact(node.args[1]))
            elif method in _FUNCTION_PATH_MUTATORS and node.args:
                self._record(node, self._fact(node.args[0]))
            elif method in _TEMPORARY_CREATORS:
                directory = next(
                    (
                        keyword.value
                        for keyword in node.keywords
                        if keyword.arg == "dir"
                    ),
                    None,
                )
                self._record(node, self._fact(directory) if directory else None)
        elif isinstance(node.func, ast.Name):
            helper = self.helpers.get(node.func.id)
            if helper is not None and node.func.id not in self.active_helpers:
                previous_environment = self.environment
                self.environment = dict(previous_environment)
                for parameter, argument in zip(helper.args.args, node.args):
                    fact = self._fact(argument)
                    if fact is not None:
                        self.environment[parameter.arg] = fact
                self.active_helpers.add(node.func.id)
                for statement in helper.body:
                    self.visit(statement)
                self.active_helpers.remove(node.func.id)
                self.environment = previous_environment
            if node.func.id in _TWO_PATH_MUTATORS and len(node.args) >= 2:
                self._record(node, self._fact(node.args[1]))
            elif node.func.id == "open" and node.args:
                mode_node = (
                    node.args[1]
                    if len(node.args) >= 2
                    else next(
                        (
                            keyword.value
                            for keyword in node.keywords
                            if keyword.arg == "mode"
                        ),
                        None,
                    )
                )
                mode = (
                    mode_node.value
                    if isinstance(mode_node, ast.Constant)
                    and isinstance(mode_node.value, str)
                    else "r"
                    if mode_node is None
                    else None
                )
                if mode is None or any(flag in mode for flag in "wax+"):
                    self._record(node, self._fact(node.args[0]))
            elif node.func.id in _TEMPORARY_CREATORS:
                directory = next(
                    (
                        keyword.value
                        for keyword in node.keywords
                        if keyword.arg == "dir"
                    ),
                    None,
                )
                self._record(node, self._fact(directory) if directory else None)
            elif node.func.id.startswith(("write", "create", "copy", "move")):
                for argument in node.args:
                    self._record(node, self._fact(argument))
        self.generic_visit(node)


def analyze_source(
    source_text: str,
    source: Path,
    allowed: dict[tuple[str, str], str],
) -> tuple[list[str], set[tuple[str, str]]]:
    tree = ast.parse(source_text, filename=str(source))
    analyzer = _LocalWriteAnalyzer(source_text, source, allowed)
    for statement in tree.body:
        analyzer.visit(statement)
    return analyzer.violations, analyzer.used


class RepositoryGeneratedPathContractTests(unittest.TestCase):
    def test_analysis_rejects_joinpath_aliases_and_dynamic_repo_writes(self) -> None:
        source = """
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[2]

def test_escape(fragment):
    alias = PROJECT_ROOT
    local = alias.joinpath("scratch", fragment)
    local.mkdir(parents=True)
"""
        violations, _ = analyze_source(
            source,
            Path("tests/video_workflow/test_escape.py"),
            {},
        )
        self.assertEqual(
            [violation.split(":", 2)[1] for violation in violations],
            ["8"],
        )

    def test_analysis_rejects_other_repo_local_write_calls(self) -> None:
        source = """
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[2]

def test_escape():
    destination = PROJECT_ROOT / "scratch" / "result.json"
    destination.write_text("{}")
"""
        violations, _ = analyze_source(
            source,
            Path("tests/video_workflow/test_escape.py"),
            {},
        )
        self.assertEqual(len(violations), 1)

    def test_analysis_rejects_os_tempfile_and_builtin_open_writes(self) -> None:
        source = """
import os
import tempfile
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[2]

def test_escape():
    destination = PROJECT_ROOT.joinpath("scratch", "result.json")
    os.makedirs(destination.parent)
    tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "scratch")
    open(destination, "w")
"""
        violations, _ = analyze_source(
            source,
            Path("tests/video_workflow/test_escape.py"),
            {},
        )
        self.assertEqual(len(violations), 3)

    def test_analysis_ignores_read_only_fixtures_schemas_and_evidence(self) -> None:
        source = """
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = PROJECT_ROOT.joinpath("tests", "video_workflow", "fixtures", "case.json")
SCHEMA = PROJECT_ROOT / "schemas" / "case.schema.json"
EVIDENCE = PROJECT_ROOT / "evidence" / "slice-01" / "manifest.json"

def test_read_only():
    FIXTURE.read_text()
    SCHEMA.open("rb")
    EVIDENCE.is_file()
"""
        violations, _ = analyze_source(
            source,
            Path("tests/video_workflow/test_read_only.py"),
            {},
        )
        self.assertEqual(violations, [])

    def test_video_workflow_tests_do_not_construct_unregistered_repo_local_roots(
        self,
    ) -> None:
        exceptions = json.loads(EXCEPTIONS_PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            set(exceptions),
            {"schema_name", "schema_version", "exceptions"},
        )
        self.assertEqual(
            exceptions["schema_name"],
            "video2pdf.test-local-write-exceptions",
        )
        self.assertEqual(exceptions["schema_version"], 1)
        self.assertTrue(isinstance(exceptions["exceptions"], list))
        for item in exceptions["exceptions"]:
            self.assertEqual(
                set(item),
                {"test_id", "path_prefix", "reason"},
            )
        allowed = {
            (item["test_id"], item["path_prefix"]): item["reason"]
            for item in exceptions["exceptions"]
        }
        self.assertEqual(len(allowed), len(exceptions["exceptions"]))
        self.assertTrue(
            all(test_id and prefix and reason for (test_id, prefix), reason in allowed.items())
        )
        for test_id, prefix in allowed:
            self.assertRegex(
                test_id,
                r"^test_[A-Za-z0-9_]+\.[A-Za-z0-9_]+\.[A-Za-z0-9_]+$",
            )
            self.assertEqual(prefix, PurePosixPath(prefix).as_posix())
            self.assertFalse(PurePosixPath(prefix).is_absolute())
            self.assertNotIn("*", prefix)
            self.assertNotIn("..", PurePosixPath(prefix).parts)
            self.assertGreaterEqual(len(PurePosixPath(prefix).parts), 2)

        violations: list[str] = []
        used_exceptions: set[tuple[str, str]] = set()
        for source in sorted(TEST_ROOT.glob("test_*.py")):
            source_text = source.read_text(encoding="utf-8")
            source_violations, source_used = analyze_source(
                source_text,
                source.relative_to(PROJECT_ROOT),
                allowed,
            )
            violations.extend(source_violations)
            used_exceptions.update(source_used)

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
